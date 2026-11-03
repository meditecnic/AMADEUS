"""Write probe contracts: real product paths over offline HTTP, record-based scores."""

from __future__ import annotations

import argparse

import asyncio


import json


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

import memory_supersede_probe as probe




from app.services.memory_v11.recall import shared_bigram_count

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

@pytest.fixture(scope='module')
def fake_run(tmp_path_factory):
    directory = tmp_path_factory.mktemp('supersede') / 'run'
    env = dict(os.environ, PYTHON_DOTENV_DISABLED='1')
    env.pop('AMADEUS_DB_PATH', None)
    with subprocess.Popen([sys.executable, str(probe.SCRIPT), '--run-dir', str(directory)],
                          env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE) as proc:
        try:
            _, stderr = proc.communicate(timeout=180)
        except subprocess.TimeoutExpired:
            proc.kill()
            raise
    assert proc.returncode == 0, stderr.decode()
    return directory, probe.b5.read_json(directory / 'results.json'), proc.pid

def test_fake_all_cases_isolated_offline(fake_run):
    directory, rows, runner_pid = fake_run
    assert len(rows) == 12
    assert Counter(r['mode'] for r in rows) == {'v11': 12}
    assert {r['status'] for r in rows} == {'completed'}
    # Workers run one after another, so Windows may give a later worker the PID
    # of one that already exited. Isolation means no case ran in the runner
    # process and each case had its own data directory.
    assert runner_pid not in {r['pid'] for r in rows}
    assert len({r['data_dir'] for r in rows}) == len(rows)
    assert {r['embedding']['reason'] for r in rows} == {'fake-768'}
    assert 'pass' in {r['score']['status'] for r in rows}
    assert 'needs_manual' in {r['score']['status'] for r in rows}
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
        assert db.execute('SELECT COUNT(*) FROM stable_fact_version_embeddings WHERE dimensions=768 AND length(vector)=3072').fetchone()[0] >= 21
        assert db.execute('SELECT COUNT(*) FROM stable_fact_versions WHERE fact_id=?', (row['t1']['id'],)).fetchone()[0] == 2

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
    directory = tmp_path / 'executions' / 'v11-case-1'
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
    capture = probe.Capture({'transport': 'real', 'cap_cny': '0.1'}, {'id': 'v11-case-1'}, {}, directory)
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
        assert capture.rows[0]['error_type'] == 'Refused'
        assert capture.rows[0]['error_detail'] == 'output_truncated'
        assert 'json_parse_failed' not in capture.rows[0]
    else:
        assert capture.rows[0]['json_parse_failed'] == parse_failed

@pytest.fixture
def probe_paid_mock(monkeypatch):
    from app.services.memory import memory_service

    monkeypatch.setattr(memory_service, 'embedder', memory_service.embedder)
    original = httpx.AsyncClient
    waits = []

    async def sleep(seconds):
        waits.append(seconds)

    def install(handler):
        def client(*args, **kwargs):
            kwargs.setdefault('transport', httpx.MockTransport(handler))
            return original(*args, **kwargs)
        monkeypatch.setattr(httpx, 'AsyncClient', client)

    monkeypatch.setattr(probe.b5.asyncio, 'sleep', sleep)
    return install, waits

@pytest.mark.asyncio
@pytest.mark.parametrize('status,count', [(429, 2), (503, 2), (400, 1)])
async def test_probe_retries_only_429_and_server_errors(tmp_path, probe_paid_mock, status, count):
    install, waits = probe_paid_mock
    seen = []

    def provider(request):
        seen.append(request)
        return httpx.Response(status)

    install(provider)
    directory = tmp_path / 'executions' / 'probe'
    directory.mkdir(parents=True)
    capture = probe.Capture({'transport': 'real', 'cap_cny': '1'}, {'id': 'probe'}, {}, directory)
    with probe.b5.fake_network_guard(), pytest.raises((probe.b5.Refused, httpx.HTTPStatusError)):
        await capture.handle(httpx.Request('POST', 'https://api.deepseek.com/chat/completions', json={'messages': []}))
    assert len(seen) == count and waits == ([5] if count == 2 else [])
    rows = probe.b5.read_json(directory / 'requests.json')
    assert len(rows) == count and all(r['error_type'] == str(status) for r in rows)


def test_fixture_sha256_matches_archive_blob():
    import hashlib

    path = Path(__file__).parent / 'fixtures/memory_supersede_probe_v1.json'
    raw = path.read_bytes().replace(b'\r\n', b'\n')  # Git's CRLF checkout conversion only.
    assert hashlib.sha256(raw).hexdigest() == '9cac054046ddd7a7166deecaded1fe410f502d0cf82562b036ba71b9aa682aa9'


@pytest.mark.parametrize('failures', [0, 1, 3])
def test_probe_transport_retry_results_budget_and_continuation(tmp_path, monkeypatch, probe_paid_mock, failures):
    install, waits = probe_paid_mock
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'offline-placeholder')
    monkeypatch.setattr(probe, 'load_embedder', lambda plan: (probe.FakeEmbedder(), {'loaded': False, 'reason': 'fake-768'}))
    plan = probe.make_plan(argparse.Namespace(transport='fake', base_url=probe.b5.BUILTIN_DEEPSEEK_BASE_URL,
                                             cap_cny='1', model_dir=str(tmp_path / 'missing'), repeats=1))
    plan['transport'] = 'real'
    plan['executions'] = plan['executions'][:5]
    active, sent = [], []
    cases = {c['id']: c for c in probe.load_fixture()['cases']}

    def provider(request):
        execution = active[-1]
        payload = json.loads(request.content)
        sent.append(payload)
        execution['attempts'] += 1
        if len(active) <= failures or (failures == 0 and len(sent) == 1):
            raise httpx.ReadTimeout('sk-' + 'x' * 30 + ' Bearer hidden-token')
        execution['successes'] += 1
        completion = probe.fake_completion(cases[execution['entry']['case_id']], execution['entry']['mode'],
                                           execution['successes'], payload)
        return httpx.Response(200, json={'choices': [{'finish_reason': 'stop', 'message': {
            'content': json.dumps(completion, ensure_ascii=False)}}],
            'usage': {'prompt_tokens': 100, 'completion_tokens': 10}})

    install(provider)

    def execute(plan, entry, directory):
        active.append(dict(entry=entry, attempts=0, successes=0))
        monkeypatch.setenv('AMADEUS_DATA_DIR', str(directory / 'data'))
        result = asyncio.run(probe.product_execution(plan, entry, directory))
        probe.b5.write_json(directory / 'result.json', result)
        return result

    monkeypatch.setattr(probe, 'execute_subprocess', execute)
    directory = tmp_path / 'run'
    with probe.b5.fake_network_guard():
        rows = probe.run_suite(directory, plan)
    assert [r['status'] for r in rows] == (['completed'] * 5 if failures == 0 else
        ['error'] * failures + (['completed'] * 4 if failures == 1 else []))
    first = rows[0]
    assert first['error_type'] == 'ReadTimeout' and not first['stop_reason']
    assert 'sk-' not in first['error_detail'] and 'hidden-token' not in first['error_detail']
    requests = probe.b5.read_json(directory / 'executions' / first['id'] / 'requests.json')
    assert len(requests) == (3 if failures == 0 else 2)
    assert requests[0]['error_type'] == 'ReadTimeout'
    assert requests[0]['request'] == requests[1]['request']
    budget = probe.b5.Budget(directory / 'budget.json', '1')
    charges = [a for a in budget.state['attempts'] if a['execution_id'] == first['id']]
    expected = sum((Decimal(a['reserve_cny']) for a in charges if a['status'] == 'reserved'), Decimal(0))
    assert expected > 0 and Decimal(first['retained_reserve_cny']) == expected
    assert Decimal(first['cost_cny']) == expected + Decimal(first['settled_cny'])
    assert waits == [5] * max(1, failures)
    assert len(active) == (3 if failures == 3 else 5)


@pytest.mark.parametrize('category,changed,new_value,expected', [
    ('keep', False, False, 'pass'), ('keep', True, True, 'wrongly_replaced'),
    ('supersede', True, True, 'pass'), ('supersede', False, True, 'duplicate'),
    ('supersede', False, False, 'missed'), ('stop', True, False, 'needs_manual'),
    ('stop', False, False, 'missed')])
def test_v11_classification_tracks_ids_and_versions(category, changed, new_value, expected):
    case = dict(category=category, old_aliases=['旧值'], new_aliases=['新值'])
    old = dict(id='fact', version=1, active=True, text='旧值')
    after = [dict(old, active=not changed)]
    if new_value:
        after.append(dict(id='new', version=1, active=True, text='新值'))
    assert probe.classify(case, 'v11', [old], after, old)['status'] == expected
    assert probe.classify(case, 'v11', [], after, None)['status'] == 'setup_failed'


@pytest.mark.parametrize('count', [1, 2, 3])
def test_probe_aggregate_manual_counts_and_no_comparison_gate(tmp_path, fake_run, count):
    source, rows, _ = fake_run
    paths = []
    for i in range(count):
        path = tmp_path / str(i)
        probe.b5.write_json(path/'results.json', rows)
        paths.append(path)
    before = {p: p.read_bytes() for d in paths for p in d.rglob('*') if p.is_file()}
    pending = next(row for row in rows if row['score']['status'] == 'needs_manual')
    manual = tmp_path/'manual.json'
    probe.b5.write_json(manual, [dict(run_dir=p.name, execution_id=pending['id'], judgment='pass') for p in paths])
    result = probe.aggregate(paths, tmp_path/'out', manual)
    assert set(result['rates']) == {'v11'} and 'investigate' not in result
    assert result['manual_overrides'] == count
    assert result['rates']['v11']['stop']['mean'] == .5
    assert all(p.read_bytes() == data for p, data in before.items())
    with pytest.raises(probe.b5.Refused, match='one_to_three'):
        probe.aggregate(paths*4, tmp_path/'refused')
