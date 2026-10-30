"""All model responses and paid-transport tests are local fakes."""
from __future__ import annotations

import asyncio
import copy
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import urllib.request

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import persona_eval_run as pe  # noqa: E402


@pytest.fixture
def plan():
    return pe.make_plan()


def subset(plan, *ids):
    result = copy.deepcopy(plan)
    result['question_set']['cases'] = [c for c in result['question_set']['cases'] if c['id'] in ids]
    result['executions'] = [{'id': c['id']} for c in result['question_set']['cases']]
    return result


def test_fixture_preserves_19_originals_and_coverage(plan):
    qset = plan['question_set']
    pe.validate_questions(qset)
    assert len(qset['cases']) == 40
    originals = pe.read_json(pe.REGRESSIONS)['cases']
    imported = {c['source_id']: c for c in qset['cases'] if c.get('source_id')}
    assert len(imported) == len(originals) == 19
    for original in originals:
        assert {k: imported[original['id']][k] for k in original} == original
    assert {(c['identity_mode'], c['worldline']) for c in qset['cases']} == {
        ('self', 'beta'), ('self', 'steins_gate'), ('okabe', 'beta'), ('okabe', 'steins_gate')}
    drift = [c for c in qset['cases'] if 'dialogue_id' in c]
    assert len(drift) == 2
    for case in drift:
        lines = pe.inputs(case, qset)
        assert len(lines) == 60 and len(set(lines)) == 58
        assert lines[0] == lines[29] == lines[59]
    assert any(c.get('resident_facts') and '美式咖啡' in str(c['resident_facts']) for c in qset['cases'])


@pytest.mark.parametrize('damage', ['original', 'missing', 'duplicate', 'drift', 'mode', 'rubric'])
def test_invalid_question_set_refused(plan, damage):
    qset = plan['question_set']
    if damage == 'original': qset['cases'][0]['input'] += ' changed'
    elif damage == 'missing': qset['cases'].pop(0)
    elif damage == 'duplicate': qset['cases'].append(copy.deepcopy(qset['cases'][0]))
    elif damage == 'drift': qset['dialogues'][qset['cases'][-1]['dialogue_id']].pop()
    elif damage == 'mode': qset['cases'][-1]['identity_mode'] = 'other'
    else: qset['cases'][-1]['required_traits'] = []
    with pytest.raises(pe.Refused): pe.validate_questions(qset)


@pytest.mark.parametrize(('body', 'raw', 'failed'), [
    ('你好，我今天也很开心。', '[EMO:happy] 你好，我今天也很开心。', 'japanese'),
    ('今日はいい天気ね。我们可以一起散步。', '[EMO:neutral] 今日はいい天気ね。我们可以一起散步。', 'japanese'),
    ('こんにちは。', 'こんにちは。', 'single_legal_emo'),
    ('こんにちは。', '[EMO:neutral][EMO:happy] こんにちは。', 'single_legal_emo'),
    ('こんにちは。', '[EMO:bogus] こんにちは。', 'single_legal_emo'),
    ('こんにちは。', 'こんにちは。[EMO:neutral]', 'single_legal_emo'),
    ('岡部、今日は休みなさい。', '[EMO:neutral] 岡部、今日は休みなさい。', 'self_address'),
    ('ちゃんと寝なさい、凶真。', '[EMO:neutral] ちゃんと寝なさい、凶真。', 'self_address'),
    ('美式咖啡が好きだったわね。', '[EMO:neutral] 美式咖啡が好きだったわね。', 'fact_original_terms'),
])
def test_mechanical_failures(body, raw, failed):
    case = {'identity_mode': 'self', 'resident_facts': [{'text': '用户喜欢喝美式咖啡', 'original_terms': ['美式咖啡']}]}
    score = pe.mechanical_score(case, body, raw, {'neutral', 'happy'})
    assert (not score[failed]) if failed in {'japanese', 'single_legal_emo'} else score[failed]


def test_names_and_third_person_allowed():
    body = 'AmadeusとDNAの話ね。岡部は別の人よ。あなたは岡部ではないわ。'
    assert pe.mechanical_score({'identity_mode': 'self'}, body, '[EMO:neutral] '+body, {'neutral'}) == {
        'japanese': True, 'single_legal_emo': True, 'self_address': [], 'fact_original_terms': []}
    assert not pe.mechanical_score({'identity_mode': 'okabe'}, '岡部、休んで。', '[EMO:neutral] 岡部、休んで。', {'neutral'})['self_address']


def synthetic_executor(plan, entry, directory):
    assert pe.read_json(directory.parents[1] / 'plan.json') == plan
    case = next(c for c in plan['question_set']['cases'] if c['id'] == entry['id'])
    turns = [{'turn': n, 'input': text, 'body': 'よく考えてみましょう。', 'emotion': 'neutral',
              'frontend_emo': '[EMO:neutral]', 'attempts': 1, 'rewrite_count': 0,
              'mechanical': {'japanese': True, 'single_legal_emo': True, 'self_address': [], 'fact_original_terms': []}}
             for n, text in enumerate(pe.inputs(case, plan['question_set']), 1)]
    return {**entry, 'status': 'completed', 'turns': turns}


def prepare_run(path, plan):
    pe.run_suite(path, plan, synthetic_executor)
    return pe.judge_pack(path)


def filled(template, judge_id, family):
    doc = copy.deepcopy(template)
    doc.update(judge_id=judge_id, judge_family=family)
    for case in doc['cases']:
        for value in [case, *case['probes']]:
            value.update(score=4, reason='Meets the persona with minor style issues.')
            for check in value['checks']:
                check.update(verdict='pass', reason='Supported by the conversation.')
    return doc


def test_pack_merge_and_compare(tmp_path, plan):
    plan = subset(plan, 'self-sg-cn-coffee', 'drift-self-sg')
    old, new = tmp_path/'old', tmp_path/'new'
    template = prepare_run(old, plan)
    pack = (old/'judge-pack.md').read_text()
    assert '自然な日本語' in pack and 'self-register' in pack and 'sg-overlay' in pack
    assert 'okabe-register:' not in pack and 'beta-overlay:' not in pack
    assert '用户喜欢喝美式咖啡' in pack and 'Turn 60' in pack
    one, two = filled(template, 'a', 'provider-a'), filled(template, 'b', 'provider-b')
    files = [tmp_path/'a.json', tmp_path/'b.json']
    for path, doc in zip(files, (one, two), strict=True): pe.write_json(path, doc)
    summary = pe.merge_judgements(old, files)
    assert summary['judge_metrics'] == {'pass': 2}
    assert summary['mechanical']['turns'] == 61 and summary['mechanical']['japanese_rate'] == 1
    assert summary['needs_u'] == []
    assert summary['drift']['drift-self-sg'][1]['judgement']['score'] == 4
    two['cases'][0]['checks'][0]['verdict'] = 'fail'
    two['cases'][1]['probes'][1]['score'] = 2
    pe.write_json(files[1], two)
    summary = pe.merge_judgements(old, files)
    assert summary['judge_metrics'] == {'needs_u': 2}
    assert set(summary['needs_u']) == {c['id'] for c in template['cases']}
    assert summary['judges']['drift-self-sg']['probes']['30']['score'] is None
    assert '要 U 看' in (old/'persona-summary.md').read_text()
    with pytest.raises(pe.Refused, match='at_least_two'): pe.merge_judgements(old, files[:1])
    with pytest.raises(FileNotFoundError): pe.merge_judgements(old, [files[0], tmp_path/'absent.json'])
    prepare_run(new, plan)
    assert pe.compare_runs(old, new)['counts'] == {'changed': 2}
    assert pe.main(['--compare', str(new), str(new)]) == 0
    assert pe.read_json(new/'persona-comparison.json')['counts'] == {'unchanged': 2}


@pytest.mark.parametrize('damage', ['unsure', 'missing_case', 'missing_check', 'reason', 'score', 'same_family', 'wrong_run'])
def test_incomplete_judgements_never_pass(tmp_path, plan, damage):
    template = prepare_run(tmp_path/'run', subset(plan, 'self-sg-greeting'))
    one, two = filled(template, 'a', 'provider-a'), filled(template, 'b', 'provider-b')
    if damage == 'unsure': two['cases'][0]['checks'][0]['verdict'] = 'unsure'
    elif damage == 'missing_case': two['cases'] = []
    elif damage == 'missing_check': two['cases'][0]['checks'].pop()
    elif damage == 'reason': two['cases'][0]['checks'][0]['reason'] = ''
    elif damage == 'score': two['cases'][0]['score'] = 6
    elif damage == 'same_family': two['judge_family'] = 'Provider-A'
    else: two['run_id'] = 'other'
    paths = [tmp_path/'a.json', tmp_path/'b.json']
    for path, doc in zip(paths, [one, two], strict=True): pe.write_json(path, doc)
    if damage in {'same_family', 'wrong_run'}:
        with pytest.raises(pe.Refused): pe.merge_judgements(tmp_path/'run', paths)
    else:
        assert pe.merge_judgements(tmp_path/'run', paths)['judge_metrics'] == {'needs_u': 1}


def test_agreed_fail_and_three_judges(tmp_path, plan):
    template = prepare_run(tmp_path/'run', subset(plan, 'self-sg-greeting'))
    paths = []
    for n in range(3):
        doc = filled(template, str(n), f'provider-{n}')
        doc['cases'][0]['checks'][0]['verdict'] = 'fail'
        path = tmp_path/f'{n}.json'
        pe.write_json(path, doc)
        paths.append(path)
    assert pe.merge_judgements(tmp_path/'run', paths)['judge_metrics'] == {'fail': 1}


def test_compare_counts_added_removed_changed(tmp_path, plan):
    old, new = tmp_path/'old', tmp_path/'new'
    prepare_run(old, subset(plan, 'self-sg-greeting', 'self-beta-greeting'))
    prepare_run(new, subset(plan, 'self-sg-greeting', 'okabe-sg-greeting'))
    target = new/'executions/self-sg-greeting/result.json'
    row = pe.read_json(target)
    row['turns'][0]['attempts'] = 2
    pe.write_json(target, row)
    assert pe.compare_runs(old, new)['counts'] == {'added': 1, 'removed': 1, 'changed': 1}


def test_resume_does_not_repeat(tmp_path, plan):
    plan = subset(plan, 'self-sg-greeting')
    pe.run_suite(tmp_path/'run', plan, synthetic_executor)
    assert pe.run_suite(tmp_path/'run', plan, lambda *_: pytest.fail('reissued'))['statuses'] == {'completed': 1}
    with pytest.raises(pe.Refused, match='frozen_plan_changed'):
        pe.run_suite(tmp_path/'run', {**plan, 'model': 'changed'}, synthetic_executor)
    interrupted = tmp_path/'interrupted'
    pe.write_json(interrupted/'plan.json', plan)
    pe.write_json(interrupted/'executions/self-sg-greeting/started.json', plan['executions'][0])
    assert pe.run_suite(interrupted, plan, lambda *_: pytest.fail('reissued'))['statuses'] == {'interrupted': 1}


@pytest.mark.parametrize('missing', ['cap', 'authorization', 'key', 'url'])
def test_real_gate_before_http_or_plan(tmp_path, monkeypatch, missing):
    monkeypatch.setenv('AMADEUS_B5_REAL_HTTP', 'authorized')
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'test-only-credential')
    monkeypatch.setattr(httpx.AsyncClient, 'send', lambda *_a, **_k: pytest.fail('HTTP attempted'))
    args = ['--run-dir', str(tmp_path/'run'), '--transport', 'real']
    if missing != 'cap': args += ['--cap-cny', '1']
    if missing == 'authorization': monkeypatch.delenv('AMADEUS_B5_REAL_HTTP')
    if missing == 'key': monkeypatch.delenv('DEEPSEEK_API_KEY')
    if missing == 'url': args += ['--base-url', 'https://unofficial.invalid']
    assert pe.main(args) == 2
    assert not (tmp_path/'run').exists()


@pytest.mark.parametrize('method', ['httpx', 'urllib', 'socket', 'subprocess', 'system'])
def test_fake_refuses_network_and_child_http(method):
    with pe.fake_guard():
        if method == 'httpx':
            with pytest.raises(pe.Refused): httpx.get('https://api.deepseek.com')
        elif method == 'urllib':
            with pytest.raises(pe.Refused): urllib.request.urlopen('https://api.deepseek.com')
        elif method == 'socket':
            with socket.socket() as client:
                with pytest.raises(pe.Refused): client.connect(('127.0.0.1', 18001))
        elif method == 'subprocess':
            with pytest.raises(pe.Refused, match='subprocess_forbidden'):
                subprocess.run([sys.executable, '-c', "import urllib.request; urllib.request.urlopen('https://api.deepseek.com')"])
        else:
            with pytest.raises(pe.Refused, match='subprocess_forbidden'): os.system('curl https://api.deepseek.com')


@pytest.mark.parametrize('override', [None, 'other', 'db_override'])
def test_worker_isolation_before_app(tmp_path, monkeypatch, override):
    worker = tmp_path/'executions/probe'
    if override is None: monkeypatch.delenv('AMADEUS_DATA_DIR', raising=False)
    elif override == 'other': monkeypatch.setenv('AMADEUS_DATA_DIR', str(tmp_path/'other'))
    else:
        monkeypatch.setenv('AMADEUS_DATA_DIR', str(worker/'data'))
        monkeypatch.setenv('AMADEUS_DB_PATH', str(tmp_path/'user.db'))
    monkeypatch.setattr(pe, 'product_case', lambda *_: pytest.fail('app reached'))
    assert pe.main(['--worker', str(worker)]) == 2
    assert not (tmp_path/'user.db').exists()


@pytest.mark.parametrize('case_id', ['self-sg-cn-coffee', 'drift-self-sg', 'drift-okabe-beta'])
def test_product_path_in_fresh_worker(tmp_path, plan, case_id):
    plan = subset(plan, case_id)
    summary = pe.run_suite(tmp_path/'run', plan)
    assert summary['statuses'] == {'completed': 1}
    row = pe.read_json(tmp_path/'run'/'executions'/case_id/'result.json')
    case = plan['question_set']['cases'][0]
    assert len(row['turns']) == (60 if 'dialogue_id' in case else 1)
    assert summary['mechanical']['japanese_rate'] == summary['mechanical']['emo_rate'] == 1
    request = row['turns'][0]['requests'][0]['request']
    assert '用户喜欢喝美式咖啡' in request['messages'][0]['content'] and 'CORE FACTS' in request['messages'][0]['content']
    assert 'required_traits' not in json.dumps(request)
    assert row['turns'][0]['requests'][0]['usage_kind'] == 'synthetic_placeholder'
    assert (tmp_path/'run'/'executions'/case_id/'data').is_dir()
    if len(row['turns']) == 60:
        messages = row['turns'][-1]['requests'][0]['request']['messages']
        assert any(m.get('content') == row['turns'][-2]['input'] for m in messages)
        assert sum(m.get('role') == 'user' for m in messages) >= 60
        assert row['turns'][-1]['input'] == row['turns'][0]['input']
        assert messages[0]['content'].count('IDENTITY AND SECURITY\n') == 1


def sse(payload, text):
    usage = {'prompt_tokens': 10, 'completion_tokens': 10, 'prompt_cache_hit_tokens': 0}
    return ('data: '+json.dumps({'choices':[{'delta':{'content':text}}]},ensure_ascii=False)
            +'\n\ndata: '+json.dumps({'choices':[], 'usage':usage})+'\n\ndata: [DONE]\n\n')


@pytest.mark.asyncio
async def test_language_rewrite_count_and_emo_source(tmp_path, plan, monkeypatch):
    plan = subset(plan, 'self-sg-greeting')
    directory = tmp_path/'executions/self-sg-greeting'
    directory.mkdir(parents=True)
    def fake(payload, _kind):
        return sse(payload, '今日はゆっくり休んで。' if pe.b5.request_kind(payload, 'main') == 'japanese_rewrite' else
                   '[EMO:neutral] 你好，我今天很忙，但是可以帮你整理一下计划。')
    monkeypatch.setattr(pe.b5, 'fake_response', fake)
    with pe.fake_guard(): row = await pe.product_case(plan, plan['executions'][0], directory)
    turn = row['turns'][0]
    assert turn['rewrite_count'] == 1 and turn['attempts'] == 1
    assert turn['body'] == '今日はゆっくり休んで。'
    assert turn['mechanical']['japanese'] and turn['mechanical']['single_legal_emo']
    assert turn['requests'][-1]['raw_output'] == '今日はゆっくり休んで。'


@pytest.mark.asyncio
async def test_paid_capture_budget_without_real_network(tmp_path, plan, monkeypatch):
    plan.update(transport='real', cap_cny='1')
    monkeypatch.setenv('AMADEUS_B5_REAL_HTTP', 'authorized')
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'test-only-credential')
    directory = tmp_path/'executions/probe'
    directory.mkdir(parents=True)
    capture = pe.Capture(plan, directory, 1)
    request = httpx.Request('POST', plan['base_url']+'/chat/completions', json={'messages':[], 'model':plan['model']})
    async def local_post(_client, url, *, headers, json):
        assert pe.read_json(tmp_path/'budget.json')['attempts'][0]['status'] == 'reserved'
        assert url == plan['base_url']+'/chat/completions'
        return httpx.Response(200, text=sse(json, '[EMO:neutral] こんにちは。'))
    monkeypatch.setattr(httpx.AsyncClient, 'post', local_post)
    with pe.fake_guard(): await capture.handle(request)
    state = pe.read_json(tmp_path/'budget.json')
    assert state['attempts'][0]['status'] == 'settled'
    assert state['attempts'][0]['execution_id'] == 'probe'
    assert 'test-only-credential' not in (directory/'turn-01-requests.json').read_text()
    async def no_usage(*_args, **_kwargs): return httpx.Response(200, text='data: [DONE]\n\n')
    capture = pe.Capture(plan, directory, 2)
    monkeypatch.setattr(httpx.AsyncClient, 'post', no_usage)
    with pe.fake_guard(), pytest.raises(pe.b5.AccountingUncertain): await capture.handle(request)
    count = len(pe.read_json(tmp_path/'budget.json')['attempts'])
    with pe.fake_guard(), pytest.raises(pe.b5.AccountingUncertain): await capture.handle(request)
    assert len(pe.read_json(tmp_path/'budget.json')['attempts']) == count


def test_worker_guard_covers_async_http_and_children(tmp_path, monkeypatch):
    directory = tmp_path/'executions/probe'
    pe.write_json(tmp_path/'plan.json', {'transport':'fake', 'base_url':pe.b5.BUILTIN_DEEPSEEK_BASE_URL, 'cap_cny':None})
    pe.write_json(directory/'started.json', {'id':'probe'})
    monkeypatch.setenv('AMADEUS_DATA_DIR', str(directory/'data'))
    async def probe(*_args):
        await asyncio.sleep(0)
        async with httpx.AsyncClient() as client:
            with pytest.raises(pe.Refused): await client.get('https://api.deepseek.com')
        with pytest.raises(pe.Refused):
            subprocess.run([sys.executable, '-c', "import urllib.request; urllib.request.urlopen('https://api.deepseek.com')"])
        return {'id':'probe', 'status':'completed'}
    monkeypatch.setattr(pe, 'product_case', probe)
    assert pe.main(['--worker', str(directory)]) == 0


@pytest.mark.asyncio
async def test_product_retry_attempts_are_counted(tmp_path, plan, monkeypatch):
    plan = subset(plan, 'self-sg-greeting')
    directory = tmp_path/'executions/self-sg-greeting'
    directory.mkdir(parents=True)
    replies = iter(['[EMO:neutral] I am an AI assistant.',
                    '[EMO:neutral] 今日はゆっくり休んで。'])
    monkeypatch.setattr(pe.b5, 'fake_response', lambda payload, _kind: sse(payload, next(replies)))
    with pe.fake_guard(): row = await pe.product_case(plan, plan['executions'][0], directory)
    assert row['turns'][0]['attempts'] == 2
    assert row['turns'][0]['rewrite_count'] == 0
    assert row['turns'][0]['body'] == '今日はゆっくり休んで。'


def test_paid_missing_budget_and_cap_stop_are_not_reissued(tmp_path, plan, monkeypatch):
    plan = subset(plan, 'self-sg-greeting', 'self-beta-greeting')
    plan.update(transport='real', cap_cny='1')
    monkeypatch.setenv('AMADEUS_B5_REAL_HTTP', 'authorized')
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'test-only-credential')
    def exhausted(*_args): raise pe.b5.BudgetExceeded('not_run_budget')
    summary = pe.run_suite(tmp_path/'run', plan, exhausted)
    assert summary['statuses'] == {'not_run_budget': 1, 'not_run_after_stop': 1}
    assert pe.run_suite(tmp_path/'run', plan, lambda *_: pytest.fail('reissued'))['statuses'] == summary['statuses']
    (tmp_path/'run'/'budget.json').unlink()
    with pytest.raises(pe.Refused, match='budget_missing_existing_run'):
        pe.run_suite(tmp_path/'run', plan, lambda *_: pytest.fail('reissued'))
