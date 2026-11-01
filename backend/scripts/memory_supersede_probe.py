"""Two-turn write-side probe. Fake by default; scores describe records, not prose.

From backend: python scripts/memory_supersede_probe.py --run-dir PATH
Offline: --aggregate RUN... --output PATH [--manual FILE]
Manual JSON: [{"run_dir": "run-name", "execution_id": "v11-cat_name-1",
               "judgment": "pass"}]. Only needs_manual may be overridden.
Fake scores verify plumbing and are not model-quality evidence.
HTTP adds max_tokens=2000 to the product request to enforce a hard output/budget
limit; messages and all other fields are unchanged. Only replies exceeding 2000
tokens are affected; normal memory write replies are far below this limit.
"""
from __future__ import annotations

import argparse
import ast
import asyncio
import contextlib
import hashlib
import io
import json
import math
import os
import statistics
import subprocess
import sys
from collections import Counter
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import httpx
import portalocker

SCRIPT = Path(__file__).resolve()
BACKEND = SCRIPT.parents[1]
sys.path.insert(0, str(SCRIPT.parent))
sys.path.insert(0, str(BACKEND))
import memory_recall_b5_real_run as b5  # noqa: E402

FIXTURE = BACKEND / 'tests/fixtures/memory_supersede_probe_v1.json'
DEFAULT_E5 = str(Path.home() / '.cache/huggingface/hub/models--intfloat--multilingual-e5-small/snapshots/614241f622f53c4eeff9890bdc4f31cfecc418b3')
ASSISTANT = '[EMO:neutral] ふーん、そうなんだ。'
SCOPE = dict(session_id='probe-owner', worldline='steins_gate', identity_mode='self')
CATEGORIES = ('supersede', 'keep', 'stop')
RESULTS = ('pass', 'duplicate', 'missed', 'wrongly_replaced', 'needs_manual', 'setup_failed', 'fail')
MAX_OUTPUT_TOKENS = 2000


class FakeEmbedder:
    model_name = 'probe-fake-384'
    dimensions = 384

    async def encode_passage(self, text):
        values = [byte - 127.5 for i in range(12)
                  for byte in hashlib.sha256(f'{text}:{i}'.encode()).digest()]
        norm = math.sqrt(sum(v * v for v in values))
        return [v / norm for v in values]

    encode_query = encode_passage


def load_fixture():
    return b5.read_json(FIXTURE)


def matches(text, aliases):
    return any(alias in text for alias in aliases)


def classify(case, mode, before, after, t1):
    """Snapshots contain stable fact IDs/active versions or legacy row IDs/keys."""
    if t1 is None:
        return {'status': 'setup_failed', 't1_changed': None, 'new_records': []}
    current = next((row for row in after if row['id'] == t1['id']), None)
    if mode == 'v11':
        changed = current is None or not current['active'] or current['version'] != t1['version']
    else:
        changed = current is not None and not current['active']
    prior_ids = {row['id'] for row in before}
    new = [row for row in after if row['id'] not in prior_ids
           and (mode == 'v11' or row['key'] != t1['key'])]
    new_value = any(row['active'] and matches(row['text'], case['new_aliases']) for row in after)
    if case['category'] == 'keep':
        status = 'wrongly_replaced' if changed else 'pass'
    elif case['category'] == 'stop':
        status = 'needs_manual' if changed else ('duplicate' if new else 'missed')
    elif changed:
        status = 'pass' if new_value else 'needs_manual'
    else:
        status = 'duplicate' if any(row['active'] and matches(row['text'], case['new_aliases'])
                                   for row in new) else 'missed'
    return {'status': status, 't1_changed': changed, 'new_records': new}


async def snapshot(mode):
    from app.db import get_db
    db = await get_db(SCOPE['worldline'], 'memory')
    try:
        if mode == 'v11':
            sql = """SELECT f.fact_id AS id, f.active_version AS version,
                     f.state, v.display_text AS text
                     FROM stable_facts f LEFT JOIN stable_fact_versions v
                     ON f.fact_id=v.fact_id AND f.active_version=v.version_no
                     WHERE f.session_id=? AND f.identity_mode=? ORDER BY f.fact_id"""
        else:
            sql = """SELECT id, fact_key AS key, fact_value AS text, is_current
                     FROM core_facts WHERE session_id=? AND identity_mode=? ORDER BY id"""
        rows = [dict(row) for row in await (await db.execute(
            sql, (SCOPE['session_id'], SCOPE['identity_mode']))).fetchall()]
        for row in rows:
            row['active'] = row.pop('state', None) == 'active' if mode == 'v11' else bool(row.pop('is_current'))
        return rows
    finally:
        await db.close()


async def seed_backgrounds(mode, fixture, embedder):
    from app.db import get_db
    from app.services.memory_v11.repository import create_stable_fact
    from app.services.memory_v11.indexing import insert_version_embedding, prepare_passage_embedding
    for row in fixture['background_facts']:
        if mode == 'v11':
            saved = await create_stable_fact(**SCOPE, display_text=row['text'],
                semantic_json={'subject': 'user', 'predicate': row['fact_key'], 'object': {'text': row['text']}})
            prepared = await prepare_passage_embedding(row['text'], embedder=embedder)
        db = await get_db(SCOPE['worldline'], 'memory')
        try:
            if mode == 'v11':
                await insert_version_embedding(db, fact_id=saved['fact_id'], version_no=1, prepared=prepared)
            else:
                await db.execute("""INSERT INTO core_facts(session_id,identity_mode,fact_key,fact_value,
                    confidence,importance,source_message_ids,created_at) VALUES(?,?,?,?,0.96,0.8,'[]',?)""",
                    (SCOPE['session_id'], SCOPE['identity_mode'], row['fact_key'], row['text'], '2026-01-01T00:00:00Z'))
            await db.commit()
        finally:
            await db.close()


def fake_completion(case, mode, round_no, payload):
    source = json.loads(payload['messages'][1]['content'])
    text = case['old_text'] if round_no == 1 else case['new_text']
    if mode == 'v11':
        target = next((c['candidate_index'] for c in source['candidates']
                       if matches(c['display_text'], case['old_aliases'])), None)
        relation = 'new'
        if round_no == 2:
            if case['id'] in {'friend_pet', 'hypothetical'}:
                return {'observations': []}
            if target and case['category'] in {'supersede', 'stop'}:
                relation = 'supersedes'
        return {'observations': [{'observation_ref': 'o1', 'display_text': text, 'fact_text': text,
            'semantic': {'subject': 'user', 'predicate': case['legacy_key'], 'object': {'text': text}},
            'evidence_kind': 'direct_user', 'memory_class': 'stable_candidate', 'confidence': 0.96,
            'relation': relation, 'candidate_index': target if relation == 'supersedes' else None}]}
    if round_no == 2 and case['id'] in {'friend_pet', 'hypothetical', 'one_off'}:
        return {'facts': []}
    key = case['legacy_key']
    # Deliberate independent-key failure in the fake; not a quality prediction.
    if round_no == 2 and case['id'] in {'home_city', 'add_drink'}:
        key += '_additional'
    return {'facts': [{'horizon': 'durable', 'fact_key': key, 'fact_value': text,
        'confidence': 0.96, 'importance': 0.8, 'source_message_ids': source['source_message_ids']}]}


class Capture:
    def __init__(self, plan, entry, case, directory):
        self.plan, self.entry, self.case, self.directory = plan, entry, case, directory
        self.round_no = 0
        self.conversation_id = None
        self.rows = []
        self.stop_reason = None
        self.error_code = None

    async def handle(self, request):
        payload = json.loads(request.content)
        payload['max_tokens'] = MAX_OUTPUT_TOKENS
        row = {'round': self.round_no, 'conversation_id': self.conversation_id, 'request': payload, 'sent': False}
        self.rows.append(row)
        b5.write_json(self.directory / 'requests.json', self.rows)
        if self.plan['transport'] == 'fake':
            row['sent'] = True
            completion = fake_completion(self.case, self.entry['mode'], self.round_no, payload)
            usage = {'prompt_tokens': max(1, len(json.dumps(payload, ensure_ascii=False).encode('utf-8')) // 3), 'completion_tokens': 150}
            body = {'choices': [{'finish_reason': 'stop', 'message': {'role': 'assistant', 'content': json.dumps(completion, ensure_ascii=False)}}], 'usage': usage}
            response = httpx.Response(200, json=body)
        else:
            try:
                budget = b5.Budget(self.directory.parents[1] / 'budget.json', self.plan['cap_cny'])
                reservation = budget.reserve(payload, self.entry['id'], kind='memory', phase=str(self.round_no))
                row['sent'] = True
                b5.write_json(self.directory / 'requests.json', self.rows)
                async with httpx.AsyncClient(timeout=60) as client:
                    headers = {k: v for k, v in request.headers.items() if k.lower() != 'content-length'}
                    response = await client.post(str(request.url), json=payload, headers=headers)
                budget.settle(reservation, response.json().get('usage'))
            except Exception as exc:
                self.stop_reason = str(exc) if isinstance(exc, b5.Refused) else 'http_or_accounting_failed'
                raise
            body = response.json()
            usage = body.get('usage') or {}
        row.update(response=body, usage=usage,
                   cost_cny=str(b5.usage_cost(usage)) if self.plan['transport'] == 'real' else '0')
        if any(choice.get('finish_reason') == 'length' for choice in body.get('choices', [])):
            self.error_code = row['error_code'] = 'output_truncated'
            b5.write_json(self.directory / 'requests.json', self.rows)
            raise b5.Refused('output_truncated')
        raw = str((body.get('choices') or [{}])[0].get('message', {}).get('content', '')).strip()
        try:
            cleaned = raw.split('\n', 1)[1].rsplit('```', 1)[0] if raw.startswith('```') else raw
            parsed = json.loads(cleaned)
            row['json_parse_failed'] = not isinstance(parsed, dict)
        except (ValueError, IndexError):
            parsed = {}
            row['json_parse_failed'] = True
        row.update(relations=dict(Counter(o.get('relation', 'missing') for o in parsed.get('observations', [])
                                          if isinstance(o, dict))) if isinstance(parsed, dict) else {})
        b5.write_json(self.directory / 'requests.json', self.rows)
        return response


def load_embedder(plan):
    if plan['transport'] == 'fake':
        return FakeEmbedder()
    if not Path(plan['e5_snapshot']).is_dir():
        raise b5.Refused('local_e5_unavailable')
    from app.services.memory import EmbeddingAdapter

    class LocalE5(EmbeddingAdapter):
        def _load(self):
            if self._model is None:
                raise b5.Refused('local_e5_unavailable')
            return self._model
    try:
        from sentence_transformers import SentenceTransformer
        embedder = LocalE5()
        embedder._model = SentenceTransformer(plan['e5_snapshot'], local_files_only=True, trust_remote_code=False)
        return embedder
    except Exception:
        raise b5.Refused('local_e5_unavailable') from None


async def product_execution(plan, entry, directory):
    from app import models
    from app.db import init_db, reset_initialization_cache
    from app.services.conversations import conversation_service
    from app.services.credentials import credential_store
    from app.services.memory import memory_service
    from app.services.memory_v11 import jobs
    from app.services.provider_adapters import OpenAICompatibleAdapter
    from app.services.provider_registry import ProviderSnapshot, ProviderTask, ProviderCapabilities
    from app.services.provider_runtime import provider_registry, deepseek_service

    if b5.sha256(FIXTURE) != plan['fixture_sha256']:
        raise b5.Refused('fixture_changed')
    fixture = load_fixture()
    case = next(c for c in fixture['cases'] if c['id'] == entry['case_id'])
    reset_initialization_cache()
    await init_db()
    embedder = load_embedder(plan)
    memory_service.embedder = embedder
    await seed_backgrounds(entry['mode'], fixture, embedder)
    baseline = await snapshot(entry['mode'])
    capture = Capture(plan, entry, case, directory)
    adapter = OpenAICompatibleAdapter(provider_id='deepseek', base_url=plan['base_url'], default_model=plan['model'],
        message_builder=deepseek_service.build_persona_messages, credential_required=True,
        transport=httpx.MockTransport(capture.handle))
    provider = ProviderSnapshot(provider_id='deepseek', model_id=plan['model'], adapter=adapter,
        capabilities=ProviderCapabilities(tasks=frozenset({ProviderTask.MEMORY})), credential_required=True)
    key = os.environ['DEEPSEEK_API_KEY'] if plan['transport'] == 'real' else 'offline-probe-credential'
    rounds = []
    # Only provider selection/credential lookup and HTTP are patched. The jobs,
    # candidate selector, default completion, extraction and reconciliation run intact.
    with patch.object(provider_registry, 'snapshot', return_value=provider), patch.object(credential_store, 'get', return_value=key):
        for round_no, user_text in enumerate(case['utterances'], 1):
            selected = await conversation_service.create_and_select(SCOPE['session_id'], SCOPE['worldline'],
                provider_id='deepseek', model_id=plan['model'], identity_mode=SCOPE['identity_mode'])
            conversation_id = str(selected['id'])
            messages = []
            for role, content in [('user', user_text), ('assistant', ASSISTANT)]:
                mid = await models.save_message(SCOPE['session_id'], role, content, worldline=SCOPE['worldline'], conversation_id=conversation_id)
                messages.append({'id': mid, 'role': role, 'content': content})
            capture.round_no, capture.conversation_id = round_no, conversation_id
            row = {'conversation_id': conversation_id, 'source_message_ids': [m['id'] for m in messages]}
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                if entry['mode'] == 'v11':
                    job = await jobs.enqueue_memory_job(**SCOPE, conversation_id=conversation_id,
                        source_message_id=messages[0]['id'], provider_id='deepseek', model_id=plan['model'])
                    job = await jobs.process_memory_job(job_id=job['job_id'], worldline=SCOPE['worldline'])
                    row['job'] = {k: job.get(k) for k in ('job_id', 'state', 'last_error_code')}
                else:
                    await memory_service._extract_turn(**SCOPE, user_text=user_text, assistant_text=ASSISTANT,
                        source_message_ids=row['source_message_ids'], api_key=key, provider_snapshot=provider,
                        conversation_id=conversation_id, skip_core=False, recent_messages=messages)
            log = output.getvalue()
            dropped = []
            for line in log.splitlines():
                if 'dropped=' in line:
                    raw = line.split('dropped=', 1)[1].split(' horizons=', 1)[0]
                    for item in ast.literal_eval(raw):
                        reason = 'episodic:error' if item.startswith('episodic:error:') else item.rsplit(':', 1)[-1]
                        dropped.append(reason)
            row.update(records=await snapshot(entry['mode']),
                       parse_failed='parse failed' in log or any(r.get('json_parse_failed') for r in capture.rows if r['round'] == round_no),
                       extraction_failed='extract failed' in log, dropped_reasons=dropped)
            rounds.append(row)
            if capture.stop_reason or capture.error_code:
                break
    before = rounds[0]['records']
    background_ids = {r['id'] for r in baseline}
    t1 = next((r for r in before if r['id'] not in background_ids and r['active'] and matches(r['text'], case['old_aliases'])), None)
    result = {**entry, 'transport': plan['transport'],
        'status': 'error' if capture.error_code else ('stopped' if capture.stop_reason else 'completed'),
        'stop_reason': capture.stop_reason, 'error_code': capture.error_code,
        'pid': os.getpid(), 'data_dir': os.environ['AMADEUS_DATA_DIR'], 't1': t1, 'rounds': rounds,
        'embedding': 'fake-384' if plan['transport'] == 'fake' else 'local-e5',
        'provider_injection': 'provider_registry.snapshot + credential_store.get; MEMORY-capable OpenAICompatibleAdapter + MockTransport',
        'score': None if capture.error_code else classify(case, entry['mode'], before, rounds[-1]['records'], t1),
        'request_count': sum(r['sent'] for r in capture.rows), 'relations': dict(sum((Counter(r.get('relations', {})) for r in capture.rows), Counter())),
        'usage': {k: sum(r.get('usage', {}).get(k, 0) for r in capture.rows) for k in ('prompt_tokens', 'completion_tokens')},
        'cost_cny': str(sum((Decimal(r.get('cost_cny', '0')) for r in capture.rows), Decimal(0)))}
    return result


def execute_subprocess(plan, entry, directory):
    root = directory / 'data'
    root.mkdir()
    env = b5.isolation.isolated_environ(run_root=directory, candidate=root, backend_port=18001, frontend_port=18422)
    env.update(AMADEUS_MEMORY_MODE=entry['mode'], HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', HF_HUB_DISABLE_TELEMETRY='1')
    env['PYTHONPATH'] = os.pathsep.join([str(BACKEND), *[p for p in sys.path if Path(p).name in {'site-packages', 'dist-packages'}]])
    if plan['transport'] == 'real':
        for name, value in os.environ.items():
            if name.upper().endswith('_PROXY') or name in {'SSL_CERT_FILE', 'SSL_CERT_DIR', 'REQUESTS_CA_BUNDLE', 'CURL_CA_BUNDLE', 'DEEPSEEK_API_KEY', 'AMADEUS_B5_REAL_HTTP'}:
                env[name] = value
    proc = subprocess.run([sys.executable, str(SCRIPT), '--worker', str(directory)], env=env, cwd=BACKEND,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=600, check=False)
    if proc.returncode:
        raise b5.Refused('worker_failed_' + str(proc.returncode))
    return b5.read_json(directory / 'result.json')


def summarize(rows):
    modes = {}
    for mode in ('legacy', 'v11'):
        selected = [r for r in rows if r['mode'] == mode]
        categories = {}
        for category in CATEGORIES:
            counts = Counter(r['score']['status'] for r in selected if r['category'] == category and r['score'] is not None)
            eligible = sum(counts.values()) - counts['setup_failed']
            categories[category] = {**{s: counts[s] for s in RESULTS}, 'eligible': eligible,
                'pass_rate': counts['pass'] / eligible if eligible else None}
        modes[mode] = {'categories': categories, 'statuses': dict(Counter(r['status'] for r in selected)),
            'output_truncated': sum(r.get('error_code') == 'output_truncated' for r in selected),
            'json_parse_failures': sum(bool(turn['parse_failed']) for r in selected for turn in r['rounds']),
            'job_failures': sum(turn.get('job', {}).get('state') not in {None, 'completed'} for r in selected for turn in r['rounds']),
            'request_count': sum(r['request_count'] for r in selected),
            'usage': {k: sum(r['usage'][k] for r in selected) for k in ('prompt_tokens', 'completion_tokens')},
            'cost_cny': str(sum((Decimal(r['cost_cny']) for r in selected), Decimal(0)))}
    return {'transport': rows[0].get('transport', 'fake') if rows else 'fake', 'modes': modes}


def summary_markdown(summary):
    note = 'Fake 仅验证流程；不据此推断模型质量。' if summary['transport'] == 'fake' else '实际请求用量和费用见各运行目录的 budget.json。'
    lines = ['# 写入侧改口探针', '', note, '',
             '| 模式 | 类 | pass | duplicate | missed | wrongly_replaced | needs_manual | setup_failed | manual fail |',
             '|---|---|---:|---:|---:|---:|---:|---:|---:|']
    for mode, data in summary['modes'].items():
        for category, counts in data['categories'].items():
            lines.append('| ' + ' | '.join([mode, category, *[str(counts[k]) for k in RESULTS]]) + ' |')
    for mode, data in summary['modes'].items():
        lines.extend(['', f"{mode}: requests={data['request_count']}, tokens={data['usage']}, cost={data['cost_cny']} CNY; JSON failures={data['json_parse_failures']}, job failures={data['job_failures']}, output_truncated={data['output_truncated']}", ''])
    return '\n'.join(lines) + '\n'


def run_suite(run_dir, plan):
    run_dir = run_dir.resolve()
    run_dir.mkdir(parents=True, exist_ok=False)
    b5.write_json(run_dir / 'plan.json', plan)
    rows = []
    with portalocker.Lock(str(run_dir / 'run.lock'), timeout=0):
        for entry in plan['executions']:
            directory = run_dir / 'executions' / entry['id']
            directory.mkdir(parents=True)
            b5.write_json(directory / 'started.json', entry)
            result = execute_subprocess(plan, entry, directory)
            rows.append(result)
            b5.write_json(run_dir / 'results.json', rows)
            if result.get('stop_reason'):
                break
    summary = summarize(rows)
    b5.write_json(run_dir / 'summary.json', summary)
    (run_dir / 'summary.md').write_text(summary_markdown(summary), encoding='utf-8')
    return rows


def aggregate(run_dirs, output, manual=None):
    overrides = b5.read_json(manual) if manual else []
    applied = 0
    runs = []
    for run_dir in run_dirs:
        rows = b5.read_json(run_dir / 'results.json')
        for row in rows:
            for decision in overrides:
                if decision['run_dir'] == run_dir.name and decision['execution_id'] == row['id']:
                    if (row['score'] or {}).get('status') != 'needs_manual' or decision['judgment'] not in {'pass', 'fail'}:
                        raise b5.Refused('invalid_manual_decision')
                    row['score']['status'] = decision['judgment']
                    applied += 1
        runs.append({'run_dir': run_dir.name, **summarize(rows)})
    stats = {}
    investigate = []
    for mode in ('legacy', 'v11'):
        stats[mode] = {}
        for category in CATEGORIES:
            rates = [run['modes'][mode]['categories'][category]['pass_rate'] for run in runs]
            valid = [rate for rate in rates if rate is not None]
            stats[mode][category] = {'per_run': rates, 'mean': statistics.mean(valid) if valid else None,
                'min': min(valid) if valid else None, 'max': max(valid) if valid else None}
    for category in CATEGORIES:
        legacy, v11 = stats['legacy'][category]['mean'], stats['v11'][category]['mean']
        if legacy is not None and v11 is not None and round(legacy - v11, 12) > 0.10:
            investigate.append(category)
    result = {'runs': runs, 'rates': stats, 'investigate': investigate, 'manual_overrides': applied,
              'note': '只报告，不判门槛；setup_failed 排除，needs_manual 保留在分母并单列；output_truncated 是执行错误，单列且不进评分。'}
    output.mkdir(parents=True, exist_ok=True)
    b5.write_json(output / 'aggregate.json', result)
    lines = ['# 改口探针多次汇总', '', result['note'], '', f'人工改判：{applied} 条。', '',
             '| 模式 | 类 | 每次通过率 | 平均 | 最小 | 最大 |', '|---|---|---|---|---|---|']
    def pct(rate):
        return 'N/A' if rate is None else f'{rate:.1%}'
    for mode, categories in stats.items():
        for category, rates in categories.items():
            lines.append('| ' + ' | '.join([mode, category, ', '.join(map(pct, rates['per_run'])),
                *[pct(rates[k]) for k in ('mean', 'min', 'max')]]) + ' |')
    lines.extend(['', '要查：' + ('、'.join(investigate) or '无'), ''])
    for run in runs:
        lines.extend([f"## {run['run_dir']}", '', summary_markdown(run).replace('# 写入侧改口探针', '### 分类计数', 1)])
    (output / 'aggregate.md').write_text('\n'.join(lines), encoding='utf-8')
    return result


def make_plan(args):
    fixture = load_fixture()
    return {'version': fixture['version'], 'fixture_sha256': b5.sha256(FIXTURE), 'script_sha256': b5.sha256(SCRIPT),
        'transport': args.transport, 'base_url': b5.canonical_base_url(args.base_url), 'cap_cny': args.cap_cny,
        'model': 'deepseek-flash', 'e5_snapshot': args.e5_snapshot,
        'executions': [{'id': f"{mode}-{case['id']}-{repeat}", 'mode': mode, 'case_id': case['id'],
                        'category': case['category'], 'repeat': repeat}
                       for repeat in range(1, args.repeats + 1) for case in fixture['cases'] for mode in ('legacy', 'v11')]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-dir', type=Path)
    parser.add_argument('--transport', choices=('fake', 'real'), default='fake')
    parser.add_argument('--base-url', default=b5.BUILTIN_DEEPSEEK_BASE_URL)
    parser.add_argument('--cap-cny')
    parser.add_argument('--e5-snapshot', default=DEFAULT_E5)
    parser.add_argument('--repeats', type=int, default=1)
    parser.add_argument('--aggregate', type=Path, nargs='+')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--manual', type=Path)
    parser.add_argument('--worker', type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    try:
        if args.aggregate:
            if args.output is None:
                raise b5.Refused('output_required')
            with b5.fake_network_guard():
                aggregate(args.aggregate, args.output, args.manual)
            return 0
        if args.worker:
            directory = args.worker.resolve()
            b5.check_worker_isolation(directory)
            plan = b5.read_json(directory.parents[1] / 'plan.json')
            b5.check_real_gate(plan['transport'], plan['base_url'], plan['cap_cny'])
            entry = b5.read_json(directory / 'started.json')
            with b5.fake_network_guard() if plan['transport'] == 'fake' else contextlib.nullcontext():
                b5.write_json(directory / 'result.json', asyncio.run(product_execution(plan, entry, directory)))
            return 0
        b5.check_real_gate(args.transport, args.base_url, args.cap_cny)
        if args.run_dir is None or args.repeats < 1:
            raise b5.Refused('run_directory_and_positive_repeats_required')
        rows = run_suite(args.run_dir, make_plan(args))
        print(json.dumps({'executions': len(rows), 'statuses': dict(Counter(r['status'] for r in rows))}))
        return int(any(row['status'] != 'completed' for row in rows))
    except Exception as exc:
        print('Probe refused: ' + (str(exc) if isinstance(exc, b5.Refused) else type(exc).__name__), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
