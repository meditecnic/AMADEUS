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
        'japanese': True, 'chinese_only_chars': [], 'single_legal_emo': True, 'self_address': [], 'fact_original_terms': []}
    assert not pe.mechanical_score({'identity_mode': 'okabe'}, '岡部、休んで。', '[EMO:neutral] 岡部、休んで。', {'neutral'})['self_address']


def synthetic_executor(plan, entry, directory):
    assert pe.read_json(directory.parents[1] / 'plan.json') == plan
    case = next(c for c in plan['question_set']['cases'] if c['id'] == entry['id'])
    turns = [{'turn': n, 'input': text, 'body': 'よく考えてみましょう。', 'emotion': 'neutral',
              'frontend_emo': '[EMO:neutral]', 'attempts': 1, 'rewrite_count': 0,
              'mechanical': {'japanese': True, 'chinese_only_chars': [], 'single_legal_emo': True, 'self_address': [], 'fact_original_terms': []}}
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
    pack = (old/'judge-pack.md').read_text(encoding='utf-8')
    assert '自然な日本語' in pack and 'self-register' in pack and 'sg-overlay' in pack
    assert 'okabe-register:' not in pack and 'beta-overlay:' not in pack
    assert '用户喜欢喝美式咖啡' in pack and 'Turn 60' in pack
    one, two = filled(template, 'a', 'openai'), filled(template, 'b', 'anthropic')
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
    assert '要 U 看' in (old/'persona-summary.md').read_text(encoding='utf-8')
    with pytest.raises(pe.Refused, match='at_least_two'): pe.merge_judgements(old, files[:1])
    with pytest.raises(FileNotFoundError): pe.merge_judgements(old, [files[0], tmp_path/'absent.json'])
    prepare_run(new, plan)
    comparison = pe.compare_runs(old, new)
    assert comparison['counts'] == {'unchanged': 2}
    assert all(c['judge_change'] == '不可比' for c in comparison['cases'])
    assert pe.main(['--compare', str(new), str(new)]) == 0
    assert pe.read_json(new/'persona-comparison.json')['counts'] == {'unchanged': 2}


@pytest.mark.parametrize('damage', ['unsure', 'missing_case', 'missing_check', 'reason', 'score', 'same_family', 'wrong_run'])
def test_incomplete_judgements_never_pass(tmp_path, plan, damage):
    template = prepare_run(tmp_path/'run', subset(plan, 'self-sg-greeting'))
    one, two = filled(template, 'a', 'openai'), filled(template, 'b', 'anthropic')
    if damage == 'unsure': two['cases'][0]['checks'][0]['verdict'] = 'unsure'
    elif damage == 'missing_case': two['cases'] = []
    elif damage == 'missing_check': two['cases'][0]['checks'].pop()
    elif damage == 'reason': two['cases'][0]['checks'][0]['reason'] = ''
    elif damage == 'score': two['cases'][0]['score'] = 6
    elif damage == 'same_family': two['judge_family'] = 'openai'
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
        doc = filled(template, str(n), ['openai', 'anthropic', 'google'][n])
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
        from app import config
        from app.services.memory import MEMORY_INGEST_WINDOW_N
        from app.services.provider_runtime import deepseek_service
        history_length, expected_compressions = 0, []
        for number in range(1, 61):
            expected_sent = history_length + 1
            history_length += 2
            if history_length // 2 > config.HISTORY_COMPRESS_THRESHOLD:
                expected_compressions.append(number)
                history_length = MEMORY_INGEST_WINDOW_N
        assert row['turns'][-1]['history_messages'] == expected_sent
        assert len(messages) == expected_sent + len(deepseek_service._fewshot) + 1
        assert [t['turn'] for t in row['turns'] if t['compression_count']] == expected_compressions
        assert expected_compressions == [16, 29, 42, 55]
        assert summary['mechanical']['compression_count'] == 4
        assert row['turns'][-1]['history_messages_after'] == history_length
        assert all(t['compression_count'] == 1 for t in row['turns'] if t['compression_triggered'])
        assert row['turns'][-1]['input'] == row['turns'][0]['input']
        assert messages[0]['content'].count('IDENTITY AND SECURITY\n') == 1


def sse(payload, text, *, calls=None, finish_reason='stop'):
    usage = {'prompt_tokens': 10, 'completion_tokens': 10, 'prompt_cache_hit_tokens': 0}
    return ('data: '+json.dumps({'choices':[{'delta': {'tool_calls': calls} if calls else {'content': text}, 'finish_reason': finish_reason}]},ensure_ascii=False)
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
    assert 'test-only-credential' not in (directory/'turn-01-requests.json').read_text(encoding='utf-8')
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


async def direct_case(tmp_path, plan, monkeypatch, lines=None):
    plan = subset(plan, 'self-sg-greeting')
    directory = tmp_path/'executions/self-sg-greeting'
    directory.mkdir(parents=True)
    if lines is not None:
        monkeypatch.setattr(pe, 'inputs', lambda _case, _qset: lines)
    with pe.fake_guard():
        return await pe.product_case(plan, plan['executions'][0], directory)


@pytest.mark.asyncio
async def test_product_deepseek_payload_parity(tmp_path, plan, monkeypatch):
    from app.agent_tools import AMADEUS_TOOLS
    from app.services import deepseek
    from app.services.model_catalog import model_catalog
    built = []
    original = deepseek.build_chat_payload
    def record(**kwargs):
        value = original(**kwargs)
        built.append((copy.deepcopy(kwargs), copy.deepcopy(value)))
        return value
    monkeypatch.setattr(deepseek, 'build_chat_payload', record)
    row = await direct_case(tmp_path, plan, monkeypatch)
    request = row['turns'][0]['requests'][0]['request']
    kwargs, product_payload = built[0]
    assert kwargs['tools'] == AMADEUS_TOOLS and kwargs['tool_choice'] == 'auto'
    assert kwargs['reasoning_effort'] == model_catalog.normalize_control('deepseek', plan['model'], None)
    assert request['tools'] == AMADEUS_TOOLS and request['thinking'] == {'type': 'disabled'}
    # Product deliberately omits auto on native-thinking models; assert both
    # the auto input and the exact serialized product field decision.
    assert request.get('tool_choice') == product_payload.get('tool_choice')
    assert request == {**product_payload, 'max_tokens': 8000, 'stream_options': {'include_usage': True}}
    assert set(request) - set(product_payload) == {'max_tokens', 'stream_options'}
    assert not row['turns'][0]['second_round']
    # chat_ws appends the user turn before compiling, so RECENT DIALOGUE ends with it.
    system = request['messages'][0]['content']
    dialogue = system.split('RECENT DIALOGUE', 1)[1].split('CURRENT USER MESSAGE', 1)[0]
    assert dialogue.strip().endswith(row['turns'][0]['input'])
    assert any('8000' in difference for difference in plan['intentional_product_differences'])
    assert any('synchronously' in difference for difference in plan['intentional_product_differences'])


def tool_calls(*names):
    return [{'index': n, 'id': f'call-{n}', 'type': 'function',
             'function': {'name': name, 'arguments': json.dumps(
                 {'needs': ['以前話した好きな飲み物']} if name == 'recall_memory' else {'query': 'local-only'})}}
            for n, name in enumerate(names)]


@pytest.mark.asyncio
@pytest.mark.parametrize(('mode', 'query', 'names'), [
    ('legacy', '今日は何を話そう？', ['web_search']),
    ('legacy', '请查一下今天的天气', []),
    ('v11', '今日は何を話そう？', ['web_search']),
    ('v11', '前に話した飲み物を覚えている？', ['recall_memory']),
    ('v11', '今日は何を話そう？', ['recall_memory', 'web_search']),
    ('v11', '请查一下今天的天气', ['recall_memory']),
])
async def test_product_tool_and_direct_search_paths_are_local(tmp_path, plan, monkeypatch, mode, query, names):
    from app.routers import chat_ws
    from app.services import turn_tool_orchestrator as orchestrator
    monkeypatch.setenv('AMADEUS_MEMORY_MODE', mode)
    plan['memory_mode'] = mode
    monkeypatch.setattr(chat_ws, 'execute_tool_call', lambda *_: pytest.fail('real tool executor reached'))
    batches = []
    original_batch = orchestrator.apply_v11_tool_batch
    async def apply(**kwargs):
        batches.append(kwargs)
        return await original_batch(**kwargs)
    monkeypatch.setattr(orchestrator, 'apply_v11_tool_batch', apply)
    def fake(payload, _kind):
        if payload.get('tools') and names:
            return sse(payload, '', calls=tool_calls(*names))
        return sse(payload, '[EMO:neutral] 検索結果はないから、確かなことは言えないわ。')
    monkeypatch.setattr(pe, 'fake_response', fake)
    row = await direct_case(tmp_path, plan, monkeypatch, [query])
    turn = row['turns'][0]
    requests = turn['requests']
    assert turn['second_round'] == bool(names)
    assert [c['function']['name'] for c in turn['tools_called']] == names
    assert len(requests) == (2 if names else 1)
    assert all(r['request']['max_tokens'] == 8000 for r in requests)
    assert turn['body'].startswith('検索結果はない')
    if mode == 'legacy' and not names:
        assert turn['direct_search'] and 'tools' not in requests[0]['request']
        assert len(turn['web_stub_calls']) == 1
    if names:
        assert requests[0]['request']['thinking'] == {'type': 'disabled'}
        assert 'tools' not in requests[1]['request'] and requests[1]['phase'] == 'tool_answer'
        assert requests[1]['request']['thinking'] == {'type': 'enabled'}
        assert requests[1]['request']['reasoning_effort'] == 'high'
        if 'web_search' in names or turn['direct_search']:
            assert '検索結果はありません。' in json.dumps(requests[1]['request'], ensure_ascii=False)
    if mode == 'v11':
        assert len(batches) == 1
        offered = {tool['function']['name'] for tool in requests[0]['request']['tools']}
        assert offered == ({'recall_memory'} if turn['direct_search'] else {'recall_memory', 'web_search'})
    elif names:
        assert '[TOOL RESULTS — UNTRUSTED DATA]' in json.dumps(requests[1]['request'], ensure_ascii=False)


@pytest.mark.asyncio
async def test_v2_leak_tail_keeps_published_prefix_and_body_history(tmp_path, plan, monkeypatch):
    from app.routers import chat_ws
    received = []
    original = chat_ws.get_clean_text_stream
    async def clean(**kwargs):
        assert kwargs['session'].protocol_version == 2 and kwargs['on_text_delta'] is not None
        received.append(copy.deepcopy(kwargs['active_history']))
        return await original(**kwargs)
    monkeypatch.setattr(chat_ws, 'get_clean_text_stream', clean)
    replies = iter(['[EMO:happy] 今日は晴れているわ。I am an AI assistant.', '[EMO:neutral] 明日は休みね。'])
    monkeypatch.setattr(pe, 'fake_response', lambda payload, _kind: sse(payload, next(replies)))
    row = await direct_case(tmp_path, plan, monkeypatch, ['おはよう', '明日の予定は？'])
    first = row['turns'][0]
    assert first['attempts'] == 1 and first['body'] == '今日は晴れているわ。'
    assert received[1][-2] == {'role': 'assistant', 'content': f"[EMO:{first['emotion']}] {first['body']}",
                               'id': received[1][-2]['id']}
    assert first['body'] == ''.join(s['text'] for s in first['segments'])


@pytest.mark.asyncio
async def test_emotion_fallback_matches_product_membership_and_decay(tmp_path, plan, monkeypatch):
    from app.routers import chat_ws
    answers = iter([('happy', True), ('invalid', True), ('invalid', False), ('invalid', False)])
    async def clean(**kwargs):
        emotion, tag = next(answers)
        await kwargs['on_text_delta']('今日はゆっくり話しましょう。', 'neutral')
        return 'ignored return buffer', emotion, {}, tag
    monkeypatch.setattr(chat_ws, 'get_clean_text_stream', clean)
    row = await direct_case(tmp_path, plan, monkeypatch, ['一', '二', '三', '四'])
    assert [t['last_assistant_emotion'] for t in row['turns']] == ['happy', 'happy', 'happy', 'neutral']
    assert all(t['body'] == '今日はゆっくり話しましょう。' for t in row['turns'])


@pytest.mark.asyncio
async def test_compression_uses_product_provider_and_same_capture(tmp_path, plan, monkeypatch):
    from app import config
    from app.security.prompt import compression_system_prompt
    from app.routers import chat_ws
    monkeypatch.setattr(config, 'HISTORY_COMPRESS_THRESHOLD', 3)
    snapshots = []
    original = chat_ws.compress_and_update_history
    async def compress(*args):
        snapshots.append((copy.deepcopy(args[1]), args[-1]))
        await original(*args)
    monkeypatch.setattr(chat_ws, 'compress_and_update_history', compress)
    row = await direct_case(tmp_path, plan, monkeypatch, ['日常の話を続けよう。'] * 6)
    assert [t['turn'] for t in row['turns'] if t['compression_count']] == [4, 5, 6]
    for turn in row['turns'][3:]:
        request = turn['requests'][-1]
        assert request['kind'] == request['phase'] == 'compression'
        assert request['request']['messages'][0]['content'] == compression_system_prompt('self')
        assert request['request']['max_tokens'] == 8000
        assert 'stream_options' not in request['request']
        assert request['finish_reason'] == 'stop' and request['usage']['completion_tokens'] == 32
        assert turn['history_messages_after'] == 6 and turn['memory_summary_after']
    assert all(type(provider.adapter).__name__ == 'DeepSeekService' for _, provider in snapshots)
    assert all(m['content'].startswith('[EMO:') for history, _ in snapshots for m in history if m['role'] == 'assistant')


@pytest.mark.asyncio
async def test_compression_failure_uses_product_backoff_and_history_cap(tmp_path, plan, monkeypatch):
    from app import config
    from app.routers import chat_ws
    monkeypatch.setattr(config, 'HISTORY_COMPRESS_THRESHOLD', 3)
    original = pe.fake_response
    def fail_compression(payload, kind):
        if kind == 'compression':
            return json.dumps({'choices': [{'message': {'content': ''}, 'finish_reason': 'stop'}],
                               'usage': {'prompt_tokens': 10, 'completion_tokens': 0}})
        return original(payload, kind)
    monkeypatch.setattr(pe, 'fake_response', fail_compression)
    row = await direct_case(tmp_path, plan, monkeypatch, ['日常の話を続けよう。'] * 14)
    failures = 0
    skip = 0
    history = 0
    expected_attempts, actual_attempts = [], []
    for t in row['turns']:
        history += 2
        if history // 2 > config.HISTORY_COMPRESS_THRESHOLD:
            if skip > 0:
                skip -= 1
                if history > 2 * chat_ws._compression_trigger_messages():
                    history = chat_ws._compression_trigger_messages()
                assert t['compression_backoff']
            else:
                failures += 1
                skip = chat_ws._compression_backoff_turns(failures)
                expected_attempts.append(t['turn'])
        if t['compression_count']: actual_attempts.append(t['turn'])
        assert t['history_messages_after'] == history
        assert t['compression_failures'] == failures and t['compression_skip_turns'] == skip
    assert actual_attempts == expected_attempts == [4, 6, 9, 14]


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['main', 'compression'])
async def test_length_finish_reason_marks_turn_and_summary(tmp_path, plan, monkeypatch, kind):
    from app import config
    original = pe.fake_response
    monkeypatch.setattr(config, 'HISTORY_COMPRESS_THRESHOLD', 3)
    def truncate(payload, request_kind):
        if request_kind == 'compression' and kind == 'compression':
            doc = json.loads(original(payload, request_kind))
            doc['choices'][0]['finish_reason'] = 'length'
            return json.dumps(doc, ensure_ascii=False)
        if request_kind == 'main' and kind == 'main':
            return sse(payload, '[EMO:neutral] 明日はゆっくり休みましょう。', finish_reason='length')
        return original(payload, request_kind)
    monkeypatch.setattr(pe, 'fake_response', truncate)
    row = await direct_case(tmp_path, plan, monkeypatch, ['日常の話を続けよう。'] * 4)
    summary = pe.summarize(plan, [row])
    count = 4 if kind == 'main' else 1
    assert summary['mechanical']['truncated_turns'] == count
    assert summary['mechanical']['truncated_requests'] == count
    assert sum(r['finish_reason'] == 'length' for t in row['turns'] for r in t['requests']) == count


def test_okarin_address_is_advisory_only(plan):
    body = 'オカリン、ちゃんと休んで。'
    assert pe.mechanical_score({'identity_mode': 'self'}, body, '[EMO:neutral] '+body, {'neutral'})['self_address'] == ['オカリン']
    assert pe.mechanical_metrics([])['address_check'] == '提示，不是硬指标'


@pytest.mark.parametrize(('scores', 'status', 'score'), [([4, 5], 'pass', 4), ([3, 4], 'pass', 3), ([3, 5], 'needs_u', None)])
def test_judge_score_tolerance(tmp_path, plan, scores, status, score):
    template = prepare_run(tmp_path/'run', subset(plan, 'self-sg-greeting'))
    files = []
    for n, (number, family) in enumerate(zip(scores, ['openai', 'anthropic'], strict=True)):
        doc = filled(template, str(n), family)
        doc['cases'][0]['score'] = number
        file = tmp_path/f'{n}.json'
        pe.write_json(file, doc)
        files.append(file)
    merged = pe.merge_judgements(tmp_path/'run', files)['judges']['self-sg-greeting']
    assert merged['status'] == status and merged['score'] == score


@pytest.mark.parametrize('family', ['claude', 'gpt', 'OpenAI', '', 'provider-a'])
def test_only_fixed_judge_families_accepted(tmp_path, plan, family):
    template = prepare_run(tmp_path/'run', subset(plan, 'self-sg-greeting'))
    one, two = filled(template, 'a', 'openai'), filled(template, 'b', family)
    files = [tmp_path/'a.json', tmp_path/'b.json']
    for file, doc in zip(files, [one, two], strict=True): pe.write_json(file, doc)
    with pytest.raises(pe.Refused, match='unknown_judge_family'):
        pe.merge_judgements(tmp_path/'run', files)


def test_compare_ignores_wording_and_judge_reasons_but_reports_conclusions(tmp_path, plan):
    plan = subset(plan, 'self-sg-greeting')
    old, new = tmp_path/'old', tmp_path/'new'
    prepare_run(old, plan)
    template = prepare_run(new, plan)
    target = new/'executions/self-sg-greeting/result.json'
    row = pe.read_json(target)
    row['turns'][0]['body'] = '違う表現でも同じ評価よ。'
    row['turns'][0]['emotion'] = 'happy'
    pe.write_json(target, row)
    assert pe.compare_runs(old, new)['counts'] == {'unchanged': 1}
    for directory in (old, new):
        template = pe.judge_pack(directory)
        files = []
        for n, family in enumerate(['openai', 'anthropic']):
            doc = filled(template, str(n), family)
            file = tmp_path/f'{directory.name}-{n}.json'
            pe.write_json(file, doc)
            files.append(file)
        pe.merge_judgements(directory, files)
    saved = pe.read_json(new/'persona-summary.json')
    saved['judges']['self-sg-greeting']['checks']['required_traits:1']['reasons'] = ['different rationale']
    pe.write_json(new/'persona-summary.json', saved)
    assert pe.compare_runs(old, new)['counts'] == {'unchanged': 1}
    saved['judges']['self-sg-greeting']['score'] = 3
    pe.write_json(new/'persona-summary.json', saved)
    changed = pe.compare_runs(old, new)
    assert changed['counts'] == {'changed': 1} and changed['cases'][0]['judge_change'] == 'changed'
    assert changed['cases'][0]['mechanical_change'] == 'unchanged'


@pytest.mark.asyncio
async def test_compression_is_reserved_and_settled_in_the_run_budget(tmp_path, plan, monkeypatch):
    from app import config
    monkeypatch.setattr(config, 'HISTORY_COMPRESS_THRESHOLD', 3)
    monkeypatch.setenv('AMADEUS_B5_REAL_HTTP', 'authorized')
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'test-only-credential')
    plan.update(transport='real', cap_cny='1')
    original_post = pe.HTTP_CLIENT.post
    async def local_post(_client, url, *, headers, json):
        if isinstance(_client._transport, httpx.MockTransport):
            return await original_post(_client, url, headers=headers, json=json)
        budget = pe.read_json(tmp_path/'budget.json')
        assert budget['attempts'][-1]['status'] == 'reserved'
        kind = 'main' if json.get('stream') else 'compression'
        return httpx.Response(200, text=pe.fake_response(json, kind))
    monkeypatch.setattr(pe.HTTP_CLIENT, 'post', local_post)
    row = await direct_case(tmp_path, plan, monkeypatch, ['日常の話を続けよう。'] * 4)
    budget = pe.read_json(tmp_path/'budget.json')
    assert len(budget['attempts']) == 5
    assert all(a['status'] == 'settled' for a in budget['attempts'])
    assert [a['kind'] for a in budget['attempts']] == ['main'] * 4 + ['compression']
    assert row['turns'][-1]['requests'][-1]['kind'] == 'compression'
    assert 'test-only-credential' not in (tmp_path/'budget.json').read_text(encoding='utf-8')


def test_compare_preserves_drift_checkpoint_metric_changes(tmp_path, plan):
    plan = subset(plan, 'drift-self-sg')
    old, new = tmp_path/'old', tmp_path/'new'
    prepare_run(old, plan)
    prepare_run(new, plan)
    for directory, index in [(old, 0), (new, 29)]:
        target = directory/'executions/drift-self-sg/result.json'
        row = pe.read_json(target)
        row['turns'][index]['mechanical']['japanese'] = False
        pe.write_json(target, row)
    result = pe.compare_runs(old, new)
    assert result['cases'][0]['old']['mechanical'] == result['cases'][0]['new']['mechanical']
    assert result['counts'] == {'changed': 1}
    assert result['cases'][0]['mechanical_change'] == 'changed'


@pytest.mark.parametrize(('body', 'expected'), [
    ('晚上好。休めるなら', ['晚']), ('本当。', []), ('大丈夫。', []), ('岡部倫太郎', []),
    ('我想把每天的计划写在纸上', ['每', '计', '划', '纸']), ('晚这们咖啡每晚', list('晚这们咖啡每')),
])
def test_chinese_chars_in_summary_and_judge_pack(tmp_path, plan, body, expected):
    plan = subset(plan, 'self-sg-greeting')
    score = pe.mechanical_score(plan['question_set']['cases'][0], body, '[EMO:neutral] '+body, {'neutral'})
    assert score['chinese_only_chars'] == expected
    def execute(plan, entry, directory):
        row = synthetic_executor(plan, entry, directory)
        row['turns'][0].update(body=body, mechanical=score)
        return row
    summary = pe.run_suite(tmp_path/'run', plan, execute)
    assert summary['mechanical']['chinese_char_turns'] == bool(expected)
    assert summary['mechanical']['japanese_check'] == '产品自己的判断'
    if body == '晚上好。休めるなら':
        assert score['japanese']  # Preserve the product's existing decision.
    pe.judge_pack(tmp_path/'run')
    assert 'chinese_only_chars: '+json.dumps(expected, ensure_ascii=False) in (tmp_path/'run/judge-pack.md').read_text(encoding='utf-8')


@pytest.mark.parametrize('returncode', [0, 2, 'timeout'])
def test_worker_output_is_retained_and_redacted(tmp_path, plan, monkeypatch, returncode):
    directory = tmp_path/'executions/probe'
    directory.mkdir(parents=True)
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'test-only-credential')
    pe.write_json(directory/'result.json', {'status': 'completed'})
    def run(*args, **kwargs):
        proc = subprocess.CompletedProcess(args, returncode,
            stdout=b'old-stdout'+b'x'*21000+b'test-only-credential',
            stderr=b'old-stderr'+b'y'*21000+b'Persona refused: request_failed_reservation_retained:RemoteProtocolError:test-only-credential'+b'z'*400)
        if returncode == 'timeout':
            raise subprocess.TimeoutExpired(args, 1800, output=proc.stdout, stderr=proc.stderr)
        return proc
    monkeypatch.setattr(pe.subprocess, 'run', run)
    if returncode == 'timeout':
        with pytest.raises(subprocess.TimeoutExpired):
            pe.execute_subprocess(plan, {'id': 'probe'}, directory)
    elif returncode:
        with pytest.raises(pe.b5.AccountingUncertain, match='RemoteProtocolError'):
            pe.execute_subprocess(plan, {'id': 'probe'}, directory)
    else:
        assert pe.execute_subprocess(plan, {'id': 'probe'}, directory)['status'] == 'completed'
    output = (directory/'worker-output.txt').read_text(encoding='utf-8')
    assert output.count('[REDACTED]') == 2 and 'test-only-credential' not in output
    assert 'old-stdout' not in output and 'old-stderr' not in output
    assert len(output) <= 2*20*1024 + 20 and 'stdout:' in output and 'stderr:' in output


@pytest.mark.parametrize(('failure', 'failures'), [
    ('protocol', 1), ('protocol', 2), ('protocol', 3), (429, 1), (500, 1), (502, 1), (503, 1), (504, 1),
    (503, 3), (400, 1), (401, 1), (403, 1), (404, 1),
])
def test_transport_retries_preserve_budget_and_product_attempts(tmp_path, plan, monkeypatch, failure, failures):
    plan = subset(plan, 'self-sg-greeting', 'self-beta-greeting')
    plan.update(transport='real', cap_cny='10')
    monkeypatch.setenv('AMADEUS_B5_REAL_HTTP', 'authorized')
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'test-only-credential')
    calls, delays = [], []
    original_post = pe.HTTP_CLIENT.post
    async def sleep(delay): delays.append(delay)
    monkeypatch.setattr(pe.asyncio, 'sleep', sleep)
    async def post(client, url, *, headers, json):
        if isinstance(client._transport, httpx.MockTransport):
            return await original_post(client, url, headers=headers, json=json)
        budget = pe.read_json(tmp_path/'run/budget.json')['attempts']
        assert budget[-1]['status'] == 'reserved'
        calls.append(json)
        if len(calls) <= failures:
            if failure == 'protocol':
                raise httpx.RemoteProtocolError('test-only-credential'+'x'*400)
            return httpx.Response(failure, text='test-only-credential'+'x'*600)
        return httpx.Response(200, text=sse(json, '[EMO:neutral] 今日はゆっくり休んで。'))
    monkeypatch.setattr(pe.HTTP_CLIENT, 'post', post)
    def execute(plan, entry, directory):
        with pe.fake_guard():
            return asyncio.run(pe.product_case(plan, entry, directory))
    summary = pe.run_suite(tmp_path/'run', plan, execute)
    directory = tmp_path/'run/executions/self-sg-greeting'
    row = pe.read_json(directory/'result.json')
    requests = pe.read_json(directory/'turn-01-requests.json')
    succeeded = failures < 3 and failure not in {400, 401, 403, 404}
    assert summary['statuses'] == ({'completed': 2} if succeeded else {'error': 1, 'not_run_after_stop': 1})
    assert [r['transport_attempt'] for r in requests] == list(range(1, failures + int(succeeded) + 1))
    assert {r['attempt'] for r in requests} == {1}
    assert delays == ([3, 10][:min(failures, 2)] if succeeded or failures == 3 else [])
    reservations = summary['budget']['attempts']
    assert len(reservations) == len(calls) == failures + (2 if succeeded else 0)
    assert sum(r['status'] == 'uncertain' for r in reservations) == failures
    assert all(r['charge_cny'] == r['reserve_cny'] != '0' for r in reservations if r['status'] == 'uncertain')
    assert pe.b5.Decimal(row['retained_reserve_cny']) > 0
    error = requests[0]['error']
    assert len(error['message']) <= 300
    if failure == 'protocol':
        assert error['type'] == 'RemoteProtocolError' and '[REDACTED]' in error['message']
    else:
        assert error['status_code'] == failure and error['response_body'].startswith('[REDACTED]')
        assert len(error['response_body']) == 500
    if succeeded:
        assert row['turns'][0]['attempts'] == 1
        assert row['turns'][0]['requests'][-1]['transport_attempt'] == failures + 1
    else:
        assert ('RemoteProtocolError' if failure == 'protocol' else f'HTTPStatusError:provider_http_{failure}') in row['error']
    assert 'test-only-credential' not in (directory/'turn-01-requests.json').read_text(encoding='utf-8')


def test_six_uncertain_reservations_stop_inside_a_case(tmp_path, plan, monkeypatch):
    plan = subset(plan, 'self-sg-greeting', 'self-beta-greeting')
    plan.update(transport='real', cap_cny='10')
    monkeypatch.setenv('AMADEUS_B5_REAL_HTTP', 'authorized')
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'test-only-credential')
    monkeypatch.setattr(pe, 'inputs', lambda *_: ['今日は休もう。']*4)
    original_post = pe.HTTP_CLIENT.post
    calls = []
    async def sleep(_delay): pass
    monkeypatch.setattr(pe.asyncio, 'sleep', sleep)
    async def post(client, url, *, headers, json):
        if isinstance(client._transport, httpx.MockTransport):
            return await original_post(client, url, headers=headers, json=json)
        calls.append(json)
        return httpx.Response(200, text=sse(json, '[EMO:neutral] 明日は休みましょう。')) if len(calls) % 3 == 0 else httpx.Response(503, text='unavailable')
    monkeypatch.setattr(pe.HTTP_CLIENT, 'post', post)
    def execute(plan, entry, directory):
        with pe.fake_guard():
            return asyncio.run(pe.product_case(plan, entry, directory))
    summary = pe.run_suite(tmp_path/'run', plan, execute)
    row = pe.read_json(tmp_path/'run/executions/self-sg-greeting/result.json')
    assert summary['statuses'] == {'error': 1, 'not_run_after_stop': 1}
    assert len(calls) == 8 and len(row['turns']) == 2
    assert 'uncertain_reservations_exceeded' in row['error']
    assert sum(r['status'] == 'uncertain' for r in summary['budget']['attempts']) == 6


@pytest.mark.asyncio
async def test_fake_transport_errors_do_not_retry(tmp_path, plan, monkeypatch):
    directory = tmp_path/'executions/probe'
    directory.mkdir(parents=True)
    capture = pe.Capture(plan, directory, 1)
    def fail(*_args): raise httpx.RemoteProtocolError('local failure')
    monkeypatch.setattr(pe, 'fake_response', fail)
    request = httpx.Request('POST', plan['base_url']+'/chat/completions', json={'messages': []})
    with pe.fake_guard(), pytest.raises(httpx.RemoteProtocolError):
        await capture.handle(request)
    requests = pe.read_json(directory/'turn-01-requests.json')
    assert len(requests) == 1 and requests[0]['error']['type'] == 'RemoteProtocolError'
