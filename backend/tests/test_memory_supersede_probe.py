"""Write probe contracts: real product paths over offline HTTP, record-based scores."""
from __future__ import annotations

import argparse
import asyncio
import copy
import json
import math
import os
import re
import sqlite3
import subprocess
import sys
from collections import Counter
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import memory_supersede_probe as probe  # noqa: E402
from app.db import get_db, init_db, reset_initialization_cache  # noqa: E402
from app.services.memory import build_extraction_instruction  # noqa: E402
from app.services.memory_v11.jobs import _V11_MEMORY_SYSTEM  # noqa: E402
from app.services.memory_v11.recall import shared_bigram_count  # noqa: E402


def test_fixture_integrity():
    fixture = probe.load_fixture()
    assert fixture['version'] == 'memory-supersede-probe-v1'
    assert Counter(c['category'] for c in fixture['cases']) == {'supersede': 6, 'keep': 4, 'stop': 2}
    assert len({c['id'] for c in fixture['cases']}) == 12
    low = 0
    for case in fixture['cases']:
        assert len(case['utterances']) == 2
        assert all(re.search('[\u4e00-\u9fff]', s) for s in case['utterances'])
        assert len(set(case['old_aliases'])) >= 2
        assert len(set(case['new_aliases'])) >= 2 or case['category'] == 'stop'
        assert all(len(alias) >= 2 for alias in case['old_aliases'] + case['new_aliases'])
        assert probe.matches(case['old_text'], case['old_aliases'])
        if case['category'] != 'stop':
            assert probe.matches(case['new_text'], case['new_aliases'])
        count = shared_bigram_count(need=case['utterances'][1], display=case['utterances'][0])
        assert case['shared_bigrams'] == count
        low += case['category'] == 'supersede' and count <= 1
    assert low >= 2
    assert len(fixture['background_facts']) == 20
    assert len({r['fact_key'] for r in fixture['background_facts']}) == 20
    for row in fixture['background_facts']:
        assert re.fullmatch('[a-z]+(?:_[a-z]+)+', row['fact_key'])
        assert all(alias not in row['text'] for c in fixture['cases'] for alias in c['old_aliases'] + c['new_aliases'])


@pytest.mark.asyncio
async def test_fake_embedding_is_normalized_deterministic_and_storable(tmp_path, monkeypatch):
    monkeypatch.setenv('AMADEUS_DATA_DIR', str(tmp_path))
    reset_initialization_cache()
    await init_db()
    embedder = probe.FakeEmbedder()
    first = await embedder.encode_passage('相同文本')
    assert first == await embedder.encode_passage('相同文本')
    assert first != await embedder.encode_passage('另外文本')
    assert len(first) == 384
    assert math.sqrt(sum(x * x for x in first)) == pytest.approx(1)
    await probe.seed_backgrounds('v11', probe.load_fixture(), embedder)
    db = await get_db(probe.SCOPE['worldline'], 'memory')
    try:
        count = await (await db.execute('SELECT COUNT(*) FROM stable_fact_version_embeddings WHERE dimensions=384 AND length(vector)=1536')).fetchone()
        assert count[0] == 20
    finally:
        await db.close()


CLASSIFICATIONS = [
    ('supersede', 'pass'), ('supersede', 'duplicate'), ('supersede', 'missed'),
    ('supersede', 'needs_manual'), ('supersede', 'setup_failed'),
    ('keep', 'pass'), ('keep', 'wrongly_replaced'), ('keep', 'setup_failed'),
    ('stop', 'needs_manual'), ('stop', 'duplicate'), ('stop', 'missed'), ('stop', 'setup_failed'),
]


@pytest.mark.parametrize('mode', ['v11', 'legacy'])
@pytest.mark.parametrize('category,expected', CLASSIFICATIONS)
@pytest.mark.asyncio
async def test_classification_from_database(tmp_path, monkeypatch, mode, category, expected):
    monkeypatch.setenv('AMADEUS_DATA_DIR', str(tmp_path))
    reset_initialization_cache()
    await init_db()
    case = {'category': category, 'old_aliases': ['旧值'], 'new_aliases': ['新值']}
    db = await get_db(probe.SCOPE['worldline'], 'memory')
    owner, identity = probe.SCOPE['session_id'], probe.SCOPE['identity_mode']
    try:
        if mode == 'v11':
            from app.services.memory_v11.repository import create_stable_fact
            saved = await create_stable_fact(**probe.SCOPE, display_text='旧值', semantic_json={'value': 'old'})
            fid = saved['fact_id']
        else:
            await db.execute("""INSERT INTO core_facts(session_id,identity_mode,fact_key,fact_value,
                confidence,importance,source_message_ids,created_at) VALUES(?,?,'old_key','旧值',0.96,0.8,'[]','2026')""", (owner, identity))
            await db.commit()
        before = await probe.snapshot(mode)
        t1 = before[0] if expected != 'setup_failed' else None
        changed = expected in {'pass', 'needs_manual'} and category == 'supersede' or expected == 'wrongly_replaced' or category == 'stop' and expected == 'needs_manual'
        if changed:
            text = '新值' if expected == 'pass' else '不相关的文字'
            if mode == 'v11':
                await db.execute('UPDATE stable_fact_versions SET invalid_at=? WHERE fact_id=?', ('2026-11-01', fid))
                await db.execute("""INSERT INTO stable_fact_versions(fact_id,version_no,display_text,
                    semantic_json,semantic_fingerprint,confidence,change_kind,previous_version,valid_from)
                    VALUES(?,2,?,'{}','new',0.96,'supersede',1,'2026-11-01')""", (fid, text))
                await db.execute('UPDATE stable_facts SET active_version=2 WHERE fact_id=?', (fid,))
            else:
                await db.execute('UPDATE core_facts SET is_current=0')
                await db.execute("""INSERT INTO core_facts(session_id,identity_mode,fact_key,fact_value,
                    confidence,importance,source_message_ids,created_at) VALUES(?,?,'old_key',?,0.96,0.8,'[]','2026')""", (owner, identity, text))
            await db.commit()
        if expected == 'duplicate':
            if mode == 'v11':
                await create_stable_fact(**probe.SCOPE, display_text='新值', semantic_json={'value': 'new'})
            else:
                await db.execute("""INSERT INTO core_facts(session_id,identity_mode,fact_key,fact_value,
                    confidence,importance,source_message_ids,created_at) VALUES(?,?,'new_key','新值',0.96,0.8,'[]','2026')""", (owner, identity))
                await db.commit()
        after = await probe.snapshot(mode)
        result = probe.classify(case, mode, before, after, t1)
        assert result['status'] == expected
        if expected == 'duplicate':
            assert result['t1_changed'] is False
            assert [r['text'] for r in result['new_records']] == ['新值']
    finally:
        await db.close()


def test_classification_tracks_ids_and_versions_not_mentions():
    case = {'category': 'supersede', 'new_aliases': ['新值']}
    t1 = {'id': 't1', 'active': True, 'version': 1, 'text': '旧值'}
    unrelated = {'id': 'background', 'active': True, 'version': 1, 'text': '新值'}
    # A preexisting mention cannot turn an unchanged T1 into success.
    assert probe.classify(case, 'v11', [t1, unrelated], [t1, unrelated], t1)['status'] == 'missed'
    retired = {**t1, 'active': False}
    assert probe.classify({'category': 'stop'}, 'v11', [t1], [retired], t1)['status'] == 'needs_manual'
    # Legacy row content alone is not the replacement signal.
    legacy = {'id': 1, 'key': 'old_key', 'text': '旧值', 'active': True}
    assert probe.classify(case, 'legacy', [legacy], [{**legacy, 'text': '新值'}], legacy)['status'] == 'missed'


@pytest.fixture(scope='module')
def fake_run(tmp_path_factory):
    directory = tmp_path_factory.mktemp('supersede') / 'run'
    env = dict(os.environ, PYTHON_DOTENV_DISABLED='1')
    env.pop('AMADEUS_DB_PATH', None)
    proc = subprocess.run([sys.executable, str(probe.SCRIPT), '--run-dir', str(directory)],
                          env=env, capture_output=True, timeout=180)
    assert proc.returncode == 0, proc.stderr.decode()
    return directory, probe.b5.read_json(directory / 'results.json')


def test_fake_all_cases_both_modes_isolated_offline(fake_run):
    directory, rows = fake_run
    assert len(rows) == 24
    assert Counter(r['mode'] for r in rows) == {'v11': 12, 'legacy': 12}
    assert {r['status'] for r in rows} == {'completed'}
    assert len({r['pid'] for r in rows}) == len(rows)
    assert len({r['data_dir'] for r in rows}) == len(rows)
    assert {r['embedding'] for r in rows} == {'fake-384'}
    assert 'pass' in {r['score']['status'] for r in rows}
    assert 'duplicate' in {r['score']['status'] for r in rows}
    assert all(r['request_count'] == 2 and r['cost_cny'] == '0' for r in rows)
    assert all(len(r['rounds']) == 2 and r['t1'] for r in rows)
    summary = probe.b5.read_json(directory / 'summary.json')
    for data in summary['modes'].values():
        assert data['request_count'] == 24
        assert data['json_parse_failures'] == data['job_failures'] == 0
        assert data['categories']['stop']['needs_manual'] == 2
    # Queries against the persisted store establish that data/embeddings were
    # really written, not merely returned by the HTTP fake.
    row = next(r for r in rows if r['id'] == 'v11-cat_name-1')
    db_path = Path(row['data_dir']) / 'worldlines/sg/memory.sqlite3'
    with sqlite3.connect(db_path) as db:
        assert db.execute('SELECT COUNT(*) FROM stable_fact_version_embeddings').fetchone()[0] >= 21
        assert db.execute('SELECT COUNT(*) FROM stable_fact_versions WHERE fact_id=?', (row['t1']['id'],)).fetchone()[0] == 2


def test_requests_match_product_and_second_conversation_is_new(fake_run):
    directory, rows = fake_run
    cases = {c['id']: c for c in probe.load_fixture()['cases']}
    saw_supersede = False
    for row in rows:
        case = cases[row['case_id']]
        captures = probe.b5.read_json(directory / 'executions' / row['id'] / 'requests.json')
        assert row['rounds'][0]['conversation_id'] != row['rounds'][1]['conversation_id']
        assert len(captures) == 2
        for round_no, (turn, capture) in enumerate(zip(row['rounds'], captures), 1):
            assert capture['conversation_id'] == turn['conversation_id']
            assert capture['round'] == round_no
            payload = capture['request']
            assert payload['messages'][0] == {'role': 'system', 'content': _V11_MEMORY_SYSTEM if row['mode'] == 'v11' else build_extraction_instruction('self')}
            assert payload['temperature'] == 0.1
            assert payload['response_format'] == {'type': 'json_object'}
            assert payload['max_tokens'] == 2000
            assert set(payload) == {'messages', 'temperature', 'response_format', 'model', 'max_tokens'}
            content = json.loads(payload['messages'][1]['content'])
            assert 'old_aliases' not in content and 'new_aliases' not in content
            if row['mode'] == 'v11':
                assert content['user_text'] == case['utterances'][round_no - 1]
                assert content['source_message_id'] == turn['source_message_ids'][0]
                assert turn['job']['state'] == 'completed'
                response = json.loads(capture['response']['choices'][0]['message']['content'])
                for obs in response.get('observations', []):
                    if obs['relation'] == 'supersedes':
                        candidate = content['candidates'][obs['candidate_index'] - 1]
                        assert probe.matches(candidate['display_text'], case['old_aliases'])
                        assert row['score']['t1_changed']
                        saw_supersede = True
            else:
                assert content['source_message_ids'] == turn['source_message_ids']
                assert [m['content'] for m in content['messages']] == [case['utterances'][round_no - 1], 'ふーん、そうなんだ。']
                assert 'candidates' not in content
    assert saw_supersede


@pytest.mark.parametrize('env,cap,url,error', [
    ({}, '5', probe.b5.BUILTIN_DEEPSEEK_BASE_URL, 'real_http_not_authorized'),
    ({'AMADEUS_B5_REAL_HTTP': 'authorized'}, '5', probe.b5.BUILTIN_DEEPSEEK_BASE_URL, 'real_credential_missing'),
    ({'AMADEUS_B5_REAL_HTTP': 'authorized', 'DEEPSEEK_API_KEY': 'offline-placeholder'}, None, probe.b5.BUILTIN_DEEPSEEK_BASE_URL, 'cap_required'),
    ({'AMADEUS_B5_REAL_HTTP': 'authorized', 'DEEPSEEK_API_KEY': 'offline-placeholder'}, '5', 'https://example.invalid', 'unofficial_base_url'),
])
def test_real_gate_rejects_before_creating_run(tmp_path, monkeypatch, env, cap, url, error, capsys):
    monkeypatch.delenv('AMADEUS_B5_REAL_HTTP', raising=False)
    monkeypatch.delenv('DEEPSEEK_API_KEY', raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    target = tmp_path / 'run'
    args = ['--transport', 'real', '--run-dir', str(target), '--base-url', url]
    if cap:
        args += ['--cap-cny', cap]
    assert probe.main(args) == 2
    assert error in capsys.readouterr().err
    assert not target.exists()


def test_real_requires_local_e5(tmp_path):
    with pytest.raises(probe.b5.Refused, match='local_e5_unavailable'):
        probe.load_embedder({'transport': 'real', 'e5_snapshot': str(tmp_path / 'absent')})


@pytest.mark.asyncio
async def test_capture_budget_stops_without_http(tmp_path, monkeypatch):
    directory = tmp_path / 'executions' / 'v11-case-1'
    directory.mkdir(parents=True)
    monkeypatch.setattr(httpx.AsyncClient, 'post', lambda *a, **k: pytest.fail('budget allowed HTTP'))
    capture = probe.Capture({'transport': 'real', 'cap_cny': '0.001'}, {'id': 'v11-case-1'}, {}, directory)
    with pytest.raises(probe.b5.BudgetExceeded):
        await capture.handle(httpx.Request('POST', 'https://api.deepseek.com/chat/completions', json={'messages': []}))
    assert capture.stop_reason == 'not_run_budget'
    assert probe.b5.read_json(tmp_path / 'budget.json')['attempts'] == []


@pytest.mark.asyncio
@pytest.mark.parametrize('content,parse_failed', [('{}', False), (' \n```json\n{}\n```\n', False), ('not JSON', True), ('[]', True)])
@pytest.mark.parametrize('finish_reason', ['stop', 'length'])
async def test_capture_adds_only_output_cap_and_reserves_2000_tokens(tmp_path, monkeypatch, content, parse_failed, finish_reason):
    directory = tmp_path / 'executions' / 'legacy-case-1'
    directory.mkdir(parents=True)
    original = {'model': 'deepseek-flash', 'messages': [
        {'role': 'system', 'content': '产品提示词\n逐字保留。'},
        {'role': 'user', 'content': '最近早上改喝拿铁了。'}], 'temperature': 0.1,
        'response_format': {'type': 'json_object'}}
    usage = {'prompt_tokens': 100, 'completion_tokens': 10}
    async def post(self, url, **kwargs):
        assert kwargs['json'] == {**original, 'max_tokens': 2000}
        assert 'content-length' not in kwargs['headers']
        return httpx.Response(200, json={'choices': [{'finish_reason': finish_reason, 'message': {'content': content}}], 'usage': usage})
    monkeypatch.setattr(httpx.AsyncClient, 'post', post)
    capture = probe.Capture({'transport': 'real', 'cap_cny': '0.1'}, {'id': 'legacy-case-1'}, {}, directory)
    request = httpx.Request('POST', 'https://api.deepseek.com/chat/completions', json=original)
    if finish_reason == 'length':
        with pytest.raises(probe.b5.Refused, match='output_truncated'):
            await capture.handle(request)
    else:
        await capture.handle(request)
    budget = probe.b5.read_json(tmp_path / 'budget.json')
    assert budget['attempts'][0]['status'] == 'settled'
    assert budget['attempts'][0]['charge_cny'] == str(probe.b5.usage_cost(usage))
    payload = capture.rows[0]['request']
    assert payload == {**original, 'max_tokens': 2000}
    expected_reserve = (Decimal(probe.b5.estimate_input_tokens(payload)) * 2 + Decimal(2000) * 8) / 1_000_000
    assert Decimal(budget['attempts'][0]['reserve_cny']) == expected_reserve
    if finish_reason == 'length':
        assert capture.error_code == capture.rows[0]['error_code'] == 'output_truncated'
        assert 'json_parse_failed' not in capture.rows[0]
    else:
        assert capture.rows[0]['json_parse_failed'] == parse_failed


@pytest.mark.asyncio
async def test_fake_network_guard():
    import socket
    with probe.b5.fake_network_guard():
        with pytest.raises(probe.b5.Refused, match='network_forbidden'):
            socket.getaddrinfo('example.invalid', 443)
        async with httpx.AsyncClient() as client:
            with pytest.raises(probe.b5.Refused, match='network_forbidden'):
                await client.get('https://api.deepseek.com')


def result_row(mode, category, status, index=1):
    return {'id': f'{mode}-{category}-{index}', 'mode': mode, 'category': category,
            'status': 'completed', 'score': {'status': status}, 'rounds': [], 'request_count': 2,
            'usage': {'prompt_tokens': 100, 'completion_tokens': 10}, 'cost_cny': '0'}


def test_aggregate_cli_manual_and_ten_percentage_point_flag(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('offline aggregate invoked runner/gate')
    monkeypatch.setattr(probe, 'run_suite', forbidden)
    monkeypatch.setattr(probe.b5, 'check_real_gate', forbidden)
    run_dirs = []
    for i, v11_passes in enumerate((8, 9, 7), 1):
        run_dir = tmp_path / f'run-{i}'
        run_dirs.append(run_dir)
        rows = []
        for mode in ('legacy', 'v11'):
            for n in range(10):
                rows.append(result_row(mode, 'supersede', 'pass' if mode == 'legacy' or n < v11_passes else 'duplicate', n))
            rows.extend([result_row(mode, 'keep', 'pass'), result_row(mode, 'keep', 'setup_failed', 2),
                         result_row(mode, 'stop', 'needs_manual')])
        probe.b5.write_json(run_dir / 'results.json', rows)
    originals = {p: p.read_bytes() for d in run_dirs for p in d.glob('*.json')}
    output = tmp_path / 'aggregate'
    assert probe.main(['--aggregate', *map(str, run_dirs), '--output', str(output), '--transport', 'real']) == 0
    aggregate = probe.b5.read_json(output / 'aggregate.json')
    assert aggregate['rates']['v11']['supersede'] == {'per_run': [0.8, 0.9, 0.7], 'mean': pytest.approx(0.8), 'min': 0.7, 'max': 0.9}
    assert aggregate['investigate'] == ['supersede']
    assert aggregate['rates']['v11']['keep']['mean'] == 1
    assert aggregate['runs'][0]['modes']['v11']['categories']['stop']['needs_manual'] == 1
    assert '要查：supersede' in (output / 'aggregate.md').read_text(encoding='utf-8')
    manual = tmp_path / 'manual.json'
    probe.b5.write_json(manual, [
        {'run_dir': 'run-1', 'execution_id': 'v11-stop-1', 'judgment': 'pass'},
        {'run_dir': 'run-1', 'execution_id': 'legacy-stop-1', 'judgment': 'fail'}])
    assert probe.main(['--aggregate', *map(str, run_dirs), '--output', str(output), '--manual', str(manual)]) == 0
    revised = probe.b5.read_json(output / 'aggregate.json')
    assert revised['manual_overrides'] == 2
    assert revised['rates']['v11']['stop']['per_run'] == [1, 0, 0]
    assert revised['runs'][0]['modes']['legacy']['categories']['stop']['fail'] == 1
    assert revised['runs'][0]['modes']['legacy']['categories']['stop']['needs_manual'] == 0
    assert '人工改判：2 条' in (output / 'aggregate.md').read_text(encoding='utf-8')
    for path, content in originals.items():
        assert path.read_bytes() == content


@pytest.mark.parametrize('legacy_passes,v11_passes', [(10, 9), (4, 3)])
def test_aggregate_exact_ten_points_is_not_flagged(tmp_path, legacy_passes, v11_passes):
    run = tmp_path / 'run'
    rows = [result_row(mode, 'keep', 'pass' if n < (legacy_passes if mode == 'legacy' else v11_passes) else 'wrongly_replaced', n)
            for mode in ('legacy', 'v11') for n in range(10)]
    probe.b5.write_json(run / 'results.json', rows)
    assert probe.aggregate([run], tmp_path / 'out')['investigate'] == []


def test_plan_repeats_are_independent_entries():
    args = argparse.Namespace(transport='fake', base_url=probe.b5.BUILTIN_DEEPSEEK_BASE_URL,
                              cap_cny=None, e5_snapshot='', repeats=3)
    plan = probe.make_plan(args)
    assert len(plan['executions']) == len({e['id'] for e in plan['executions']}) == 72
    assert Counter(e['repeat'] for e in plan['executions']) == {1: 24, 2: 24, 3: 24}
    assert plan['fixture_sha256'] == probe.b5.sha256(probe.FIXTURE)


@pytest.mark.parametrize('mode,fault', [('legacy', 'drop'), ('legacy', 'parse'), ('v11', 'schema')])
@pytest.mark.asyncio
async def test_product_failure_and_drop_telemetry(tmp_path, monkeypatch, mode, fault):
    from app.services.memory import memory_service
    import app.services.memory as memory_module

    monkeypatch.setenv('AMADEUS_DATA_DIR', str(tmp_path / 'data'))
    monkeypatch.setattr(memory_service, 'embedder', memory_service.embedder)
    directory = tmp_path / 'executions' / 'case'
    directory.mkdir(parents=True)
    plan = {'fixture_sha256': probe.b5.sha256(probe.FIXTURE), 'transport': 'fake',
            'base_url': probe.b5.BUILTIN_DEEPSEEK_BASE_URL, 'model': 'deepseek-flash', 'cap_cny': None}
    entry = {'id': f'{mode}-cat_name-1', 'case_id': 'cat_name', 'mode': mode, 'category': 'supersede'}
    if fault == 'drop':
        def response(case, mode, round_no, payload):
            return {'facts': [{'horizon': 'durable', 'fact_key': 'private_content_label',
                    'fact_value': '待丢弃的原文', 'confidence': 0.2, 'importance': 0.8,
                    'source_message_ids': [1]}]}
        monkeypatch.setattr(probe, 'fake_completion', response)
    elif fault == 'parse':
        def parse_failed(*args):
            raise ValueError('synthetic schema failure')
        monkeypatch.setattr(memory_module, 'parse_extraction_payload', parse_failed)
    else:
        monkeypatch.setattr(probe, 'fake_completion', lambda *args: {'observations': 'invalid_container'})
    with probe.b5.fake_network_guard():
        row = await probe.product_execution(plan, entry, directory)
    assert row['score']['status'] == 'setup_failed'
    summary = probe.summarize([row])['modes'][mode]
    if fault == 'drop':
        assert all(turn['dropped_reasons'] == ['conf'] for turn in row['rounds'])
        assert 'private_content_label' not in json.dumps([r['dropped_reasons'] for r in row['rounds']])
    elif fault == 'parse':
        assert summary['json_parse_failures'] == 2
        assert all(turn['extraction_failed'] for turn in row['rounds'])
    else:
        assert summary['job_failures'] == 2
        assert all(turn['job']['last_error_code'] == 'validation_failed' for turn in row['rounds'])


@pytest.mark.parametrize('mode', ['legacy', 'v11'])
@pytest.mark.parametrize('truncated_round', [1, 2])
@pytest.mark.asyncio
async def test_output_truncated_never_parsed_or_classified(tmp_path, monkeypatch, mode, truncated_round):
    from app.services.memory import memory_service

    monkeypatch.setenv('AMADEUS_DATA_DIR', str(tmp_path / 'data'))
    monkeypatch.setattr(memory_service, 'embedder', memory_service.embedder)
    directory = tmp_path / 'executions' / f'{mode}-cat_name-1'
    directory.mkdir(parents=True)
    plan = {'fixture_sha256': probe.b5.sha256(probe.FIXTURE), 'transport': 'fake',
            'base_url': probe.b5.BUILTIN_DEEPSEEK_BASE_URL, 'model': 'deepseek-flash', 'cap_cny': None}
    entry = {'id': directory.name, 'case_id': 'cat_name', 'mode': mode, 'category': 'supersede'}
    original_response, original_loads = httpx.Response, json.loads
    blocked = []
    request_count = 0

    def response(status, **kwargs):
        nonlocal request_count
        request_count += 1
        if request_count == truncated_round:
            choice = kwargs['json']['choices'][0]
            choice['finish_reason'] = 'length'
            # Even valid JSON accompanied by length must never establish facts.
            blocked.append(choice['message']['content'])
        return original_response(status, **kwargs)

    def loads(value, *args, **kwargs):
        if isinstance(value, str) and value in blocked:
            pytest.fail('truncated completion was parsed')
        return original_loads(value, *args, **kwargs)

    monkeypatch.setattr(probe.httpx, 'Response', response)
    monkeypatch.setattr(probe.json, 'loads', loads)
    monkeypatch.setattr(probe, 'classify', lambda *args: pytest.fail('truncated execution was classified'))
    with probe.b5.fake_network_guard():
        row = await probe.product_execution(plan, entry, directory)
    assert row['status'] == 'error'
    assert row['error_code'] == 'output_truncated'
    assert row['score'] is None
    assert row['stop_reason'] is None
    assert row['request_count'] == len(row['rounds']) == truncated_round
    assert len(row['rounds'][-1]['records']) == 20 + truncated_round - 1
    if truncated_round == 2:
        assert row['rounds'][0]['records'] == row['rounds'][1]['records']
    captures = probe.b5.read_json(directory / 'requests.json')
    assert captures[-1]['error_code'] == 'output_truncated'
    assert 'relations' not in captures[-1] and 'json_parse_failed' not in captures[-1]
    data = probe.summarize([row])['modes'][mode]
    assert data['output_truncated'] == 1
    assert data['statuses'] == {'error': 1}
    assert data['json_parse_failures'] == 0
    assert data['categories']['supersede']['eligible'] == 0
    # Offline aggregation keeps the error visible and excludes it from scores.
    probe.b5.write_json(tmp_path / 'results.json', [row, result_row(mode, 'supersede', 'pass')])
    output = tmp_path / 'aggregate'
    aggregate = probe.aggregate([tmp_path], output)
    assert aggregate['rates'][mode]['supersede']['mean'] == 1
    assert aggregate['runs'][0]['modes'][mode]['output_truncated'] == 1
    assert 'output_truncated=1' in (output / 'aggregate.md').read_text(encoding='utf-8')


def test_help_discloses_output_cap(capsys):
    with pytest.raises(SystemExit) as exc:
        probe.main(['--help'])
    assert exc.value.code == 0
    help_text = ' '.join(capsys.readouterr().out.split())
    assert 'max_tokens=2000' in help_text
    assert 'messages and all other fields are unchanged' in help_text
    assert 'normal memory write replies are far below this limit' in help_text
