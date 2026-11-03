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

import memory_supersede_probe as probe

from app.db import get_db, init_db, reset_initialization_cache

from app.services.memory import build_extraction_instruction

from app.services.memory_v11.jobs import _V11_MEMORY_SYSTEM

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
        assert db.execute('SELECT COUNT(*) FROM stable_fact_version_embeddings').fetchone()[0] >= 21
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
