from contextlib import closing
import asyncio
import json
from pathlib import Path
import sqlite3

import httpx
import pytest

from app import db
from app.services import model_usage as accounting
from app.services.deepseek import DeepSeekService
from app.services.provider_adapters import GeminiAdapter, OpenAICompatibleAdapter, OpenAIResponsesAdapter


@pytest.fixture
def usage_store(tmp_path, monkeypatch):
    monkeypatch.setenv('AMADEUS_DATA_DIR', str(tmp_path))
    monkeypatch.delenv('AMADEUS_DB_PATH', raising=False)
    db.reset_initialization_cache()
    path = db._resolve_path('steins_gate', 'control')
    db._initialize_sync(path, db.CONTROL_SCHEMA, 'control')
    token = accounting._context.set((None, None, 'background', 'unspecified'))
    yield Path(path)
    accounting._context.reset(token)
    db.reset_initialization_cache()


def rows(path):
    with closing(sqlite3.connect(path)) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(row) for row in conn.execute('SELECT * FROM model_usage ORDER BY id')]


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['compatible', 'responses', 'gemini', 'deepseek'])
@pytest.mark.parametrize('method', ['stream', 'json', 'translation', 'compression'])
async def test_every_paid_path_records_exact_provider_usage(kind, method, usage_store, monkeypatch):
    requests = []
    def handler(request):
        requests.append(json.loads(request.content))
        if kind == 'gemini':
            metadata = {'usageMetadata': {'promptTokenCount': 100, 'candidatesTokenCount': 16,
                                         'thoughtsTokenCount': 4, 'cachedContentTokenCount': 25}}
            payload = {'candidates': [{'content': {'parts': [{'text': '{"ok":true}'}]}}], **metadata}
        elif kind == 'responses':
            metadata = {'usage': {'input_tokens': 100, 'output_tokens': 20,
                                  'input_tokens_details': {'cached_tokens': 25}}}
            payload = {'output_text': '{"ok":true}', **metadata}
        else:
            metadata = {'usage': {'prompt_tokens': 100, 'completion_tokens': 20,
                                  'prompt_tokens_details': {'cached_tokens': 25}}}
            if kind == 'deepseek':
                metadata['usage']['prompt_cache_hit_tokens'] = 25
            payload = {'choices': [{'message': {'content': '{"ok":true}'}}], **metadata}
        if method == 'stream':
            if kind == 'responses':
                events = [{'type': 'response.output_text.delta', 'delta': 'hello'},
                          {'type': 'response.completed', 'response': metadata}]
            elif kind == 'gemini':
                events = [payload, metadata]  # repeated cumulative count must not double
            else:
                events = [{'choices': [{'delta': {'content': 'hello'}}]}, {'choices': [], **metadata}]
            return httpx.Response(200, text=''.join('data: '+json.dumps(e)+'\n\n' for e in events)+'data: [DONE]\n\n')
        return httpx.Response(200, json=payload)
    transport = httpx.MockTransport(handler)
    builder = lambda history, *args: history
    if kind == 'compatible':
        adapter = OpenAICompatibleAdapter(provider_id='custom', base_url='https://example.invalid',
                                         default_model='test-model', message_builder=builder, transport=transport)
    elif kind == 'responses':
        adapter = OpenAIResponsesAdapter(message_builder=builder, transport=transport)
    elif kind == 'gemini':
        adapter = GeminiAdapter(message_builder=builder, transport=transport)
    else:
        original = httpx.AsyncClient
        monkeypatch.setattr(httpx, 'AsyncClient', lambda **kwargs: original(transport=transport, **kwargs))
        adapter = DeepSeekService()
    accounting.set_chat_context('beta', 'conversation-1')
    if method == 'stream':
        result = [chunk async for chunk in adapter.get_chat_stream([], api_key='test-credential', model='test-model')]
        assert result
    elif method == 'json':
        with accounting.usage_scope(source='memory'):
            assert await adapter.complete_json([], api_key='test-credential', model='test-model') == {'ok': True}
    elif method == 'translation':
        with accounting.usage_scope(source='translation'):
            assert await adapter.translate_to_zh('hello', api_key='test-credential', model='test-model')
    else:
        with accounting.usage_scope(source='compression'):
            assert await adapter.compress_history([], api_key='test-credential', model='test-model')
    record, = rows(usage_store)
    assert (record['input_tokens'], record['output_tokens'], record['cached_tokens'], record['reported']) == (100, 20, 25, 1)
    assert record['worldline'] == 'beta' and record['conversation_id'] == 'conversation-1'
    assert record['category'] == ('chat' if method == 'stream' else 'background')
    assert record['source'] == {'stream': 'chat', 'json': 'memory', 'translation': 'translation', 'compression': 'compression'}[method]
    assert record['model_id'] == 'test-model'
    if kind == 'compatible':
        assert 'stream_options' not in requests[0]
    assert 'test-credential' not in json.dumps(record)


@pytest.mark.asyncio
@pytest.mark.parametrize('provider', ['custom', 'custom:abcdef', 'glm', 'openai'])
async def test_missing_usage_is_null_and_only_official_allowlist_opts_in(provider, usage_store):
    def handler(request):
        payload = json.loads(request.content)
        assert ('stream_options' in payload) == (provider == 'openai')
        return httpx.Response(200, text='data: {"choices":[{"delta":{"content":"ok"}}]}\n\ndata: [DONE]\n\n')
    adapter = OpenAICompatibleAdapter(provider_id=provider, base_url='https://example.invalid',
                                     default_model='model', message_builder=lambda *args: [], transport=httpx.MockTransport(handler))
    assert [c async for c in adapter.get_chat_stream([], api_key='test-credential')][0]['content'] == 'ok'
    record, = rows(usage_store)
    assert record['reported'] == 0
    assert record['input_tokens'] is record['output_tokens'] is record['cached_tokens'] is None


@pytest.mark.asyncio
async def test_accounting_write_failure_keeps_reply_and_logs_no_content(usage_store, monkeypatch, caplog):
    def fail(*args, **kwargs):
        raise sqlite3.OperationalError('secret-url-and-content')
    monkeypatch.setattr(accounting.sqlite3, 'connect', fail)
    adapter = OpenAIResponsesAdapter(message_builder=lambda *args: [], transport=httpx.MockTransport(
        lambda req: httpx.Response(200, text='data: {"type":"response.output_text.delta","delta":"reply"}\n\n')))
    assert [c async for c in adapter.get_chat_stream([], api_key='test-credential')] == [{'content': 'reply', 'tool_calls': None}]
    assert 'model_usage_write_failed' in caplog.text
    assert 'secret-url-and-content' not in caplog.text


@pytest.mark.asyncio
async def test_request_cancellation_is_unreported_and_contexts_are_isolated(usage_store):
    async def call(wl, cid):
        accounting.set_chat_context(wl, cid)
        with accounting.usage_scope(source='memory'):
            async with accounting.usage_call('provider', 'model') as usage:
                usage.started = True
                await asyncio.sleep(0)
    await asyncio.gather(call('beta', 'b'), call('sg', 's'))
    assert {(r['worldline'], r['conversation_id'], r['source']) for r in rows(usage_store)} == {('beta', 'b', 'memory'), ('steins_gate', 's', 'memory')}
    with pytest.raises(asyncio.CancelledError):
        async with accounting.usage_call('provider', 'model') as usage:
            usage.started = True
            raise asyncio.CancelledError
    # shielded save may finish after cancellation; a thread barrier is unnecessary
    # here because the cancellation was raised inside the body, before finalization.
    assert len(rows(usage_store)) == 3


def test_v12_upgrade_is_backed_up_before_usage_ddl(usage_store, monkeypatch):
    from app.services import backups
    with closing(sqlite3.connect(usage_store)) as conn:
        conn.execute('DROP TABLE model_usage')
        conn.execute("UPDATE schema_meta SET value='12' WHERE key='schema_version'")
        conn.commit()
    monkeypatch.setattr(db, '_initialize_vec0', lambda path: None)
    db._run_migration_locked()
    manifest, = backups.list_backups()
    assert manifest['reason'] == 'pre-upgrade'
    backup = usage_store.parent/'backups'/manifest['id']/'control.sqlite3'
    with closing(sqlite3.connect(backup)) as conn:
        assert conn.execute("SELECT value FROM schema_meta WHERE key='schema_version'").fetchone()[0] == '12'
        assert not conn.execute("SELECT name FROM sqlite_master WHERE name='model_usage'").fetchone()
    with closing(sqlite3.connect(usage_store)) as conn:
        assert conn.execute("SELECT value FROM schema_meta WHERE key='schema_version'").fetchone()[0] == '13'
        assert conn.execute('SELECT COUNT(*) FROM model_usage').fetchone()[0] == 0
    db._run_migration_locked()
    assert len(backups.list_backups()) == 1


def test_usage_migration_rolls_back_on_ddl_failure():
    with closing(sqlite3.connect(':memory:')) as conn:
        conn.executescript("CREATE TABLE schema_meta(key TEXT PRIMARY KEY,value TEXT); INSERT INTO schema_meta VALUES('schema_version','12');")
        conn.set_authorizer(lambda action, *args: sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_CREATE_INDEX else sqlite3.SQLITE_OK)
        with pytest.raises(sqlite3.DatabaseError):
            db._migrate_v12_to_v13(conn, 'control')
        conn.set_authorizer(None)
        assert conn.execute("SELECT value FROM schema_meta").fetchone()[0] == '12'
        assert not conn.execute("SELECT name FROM sqlite_master WHERE name='model_usage'").fetchone()


def test_usage_currency_nulls_session_and_local_month(usage_store, monkeypatch):
    from datetime import datetime, timezone
    from app.services import usage_report as report
    prices = {'usd': dict(input=2, output=10, cached_input=0.5, currency='USD'),
              'cny': dict(input=7, output=14, cached_input=1, currency='CNY')}
    monkeypatch.setattr(report.model_catalog, 'pricing_for', lambda p, m: prices.get(m))
    values = [
        ('2026-10-31T16:30:00+00:00', 'c1', 'usd', 1000000, 100000, 200000, 1),
        ('2026-11-30T16:30:00+00:00', 'c1', 'usd', 100, 100, 0, 1),
        ('2026-11-02T00:00:00+00:00', 'c2', 'cny', 1000000, 1000000, 0, 1),
        ('2026-11-02T01:00:00+00:00', 'c1', 'unknown', None, None, None, 0),
        ('2026-11-02T01:00:00+00:00', 'c1', 'usd', None, None, None, 0)]
    with closing(sqlite3.connect(usage_store)) as conn:
        conn.executemany('''INSERT INTO model_usage
            (time_utc,conversation_id,model_id,input_tokens,output_tokens,cached_tokens,reported,
             worldline,category,source,provider_id) VALUES(?,?,?,?,?,?,?,'beta','chat','chat','p')''', values)
        conn.commit()
    result = report.usage_report('month', None, 480, now=datetime(2026, 11, 15, tzinfo=timezone.utc))
    assert result['estimate'] is True
    assert result['totals_by_currency'] == {'CNY': 21.0, 'USD': 2.7}
    assert result['unreported_calls'] == 2
    assert result['unpriced_models'] == [{'provider_id': 'p', 'model_id': 'unknown'}]
    by_model = {g['model_id']: g for g in result['groups']}
    assert by_model['usd']['calls'] == 2 and by_model['usd']['input_tokens'] == 1000000
    assert by_model['unknown']['estimated_cost'] is None and by_model['unknown']['input_tokens'] is None
    assert sum(g['calls'] for g in report.usage_report('session', 'c1', 0)['groups']) == 4
    assert sum(g['calls'] for g in report.usage_report('all', None, 0)['groups']) == 5


def test_catalog_official_prices_and_long_context(usage_store):
    from app.services.usage_report import usage_report, model_catalog
    for model in ['deepseek-flash', 'deepseek-v4-flash', 'deepseek-v4-pro']:
        price = model_catalog.pricing_for('deepseek', model)
        assert price['source'] == 'https://api-docs.deepseek.com/quick_start/pricing/'
        assert price['as_of'] == '2026-11-06'
    assert model_catalog.pricing_for('glm', 'glm-5.2') is None
    price = model_catalog.pricing_for('openai', 'gpt-5.6-luna')
    assert model_catalog.public_model('openai', 'gpt-5.6-luna')['pricing'] == price
    price['input'] = 999
    assert model_catalog.pricing_for('openai', 'gpt-5.6-luna')['input'] == 0.2
    with closing(sqlite3.connect(usage_store)) as conn:
        conn.execute('''INSERT INTO model_usage(time_utc,category,source,provider_id,model_id,input_tokens,output_tokens,reported)
            VALUES('2026-11-06T00:00:00+00:00','chat','chat','openai','gpt-5.6-luna',270000,100,1)''')
        conn.commit()
    group, = usage_report('all', None, 0)['groups']
    assert group['estimated_cost'] is None and group['unestimated_calls'] == 1


@pytest.mark.parametrize('patch', [{'input': -1}, {'output': float('nan')}, {'currency': 'usd'}, {'as_of': 'bad'}, {'source': 'http://invalid'}, {'max_input_tokens': 0}])
def test_invalid_catalog_pricing_rejected(patch):
    from app.services.model_catalog import ModelCatalog
    price = dict(input=1, output=2, cached_input=0.1, currency='USD', as_of='2026-11-06', source='https://official.example/pricing')
    price.update(patch)
    with pytest.raises(ValueError):
        ModelCatalog._parse_pricing(price)


def test_usage_api_validation_and_empty_totals(app_client):
    response = app_client.get('/api/data/usage?range=all')
    assert response.status_code == 200
    assert response.json() == dict(estimate=True, range='all', tz=0, groups=[], totals_by_currency={}, unreported_calls=0, unpriced_models=[])
    assert app_client.get('/api/data/usage?range=session').json() == {'detail': {'code': 'session_id_required'}}
    for query in ['range=invalid', 'tz=841', 'tz=abc']:
        assert app_client.get('/api/data/usage?'+query).status_code == 422
