"""Round-two evaluator helpers. Never used to construct provider messages.

Manual JSON: [{"run": "run-directory-name", "execution_id": "C-...-1",
"rule": "must_use|must_not|unknown", "key": "fact-key-or-subject",
"judgment": "pass|fail"}]. A pass means the corresponding rule is satisfied.
"""
from __future__ import annotations

import hashlib
import json
import statistics
from collections import Counter
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

VERSION = 'b5c-round2-qset-v1'
GROUPS = {'C': 'v11'}
HIT_CATEGORIES = {'resident', 'outside_lexical', 'outside_paraphrase', 'change_current', 'time'}


def selected_rows(qset, case, group=None):
    return [{**f, 'state': f.get('state', 'active')} for f in qset['facts'] + qset['scope_sentinels']]


def seed_time(index):
    return (datetime(2026, 10, 1, tzinfo=timezone.utc) + timedelta(minutes=index)).isoformat()


async def seed_store(qset, case, mode, embedder, group=None):
    from app.db import get_db
    from app.services.memory_v11.repository import create_stable_fact, validate_active_version_invariants

    positions = {r['key']: i for i, r in enumerate(qset['facts'] + qset['scope_sentinels'])}
    rows = selected_rows(qset, case, group)
    for row in rows:
        index = positions[row['key']]
        timestamp = seed_time(index)
        owner, worldline, identity = row['scope'].split('/')
        history = row.get('history', [])
        saved = await create_stable_fact(session_id=owner, worldline=worldline, identity_mode=identity,
            display_text=row['display_text'], semantic_json={'subject': 'user', 'predicate': 'note', 'object': str(index)})
        fact_id = saved['fact_id']
        version = len(history) + 1
        db = await get_db(worldline, 'memory')
        try:
            await db.execute('UPDATE stable_facts SET created_at=?,updated_at=?,active_version=? WHERE fact_id=?',
                             (timestamp, timestamp, version, fact_id))
            await db.execute('''UPDATE stable_fact_versions SET version_no=?,previous_version=?,
                change_kind=?,valid_from=? WHERE fact_id=?''',
                (version, version - 1 if history else None, 'supersede' if history else 'create', timestamp, fact_id))
            for v, old in enumerate(history, 1):
                semantic = json.dumps({'subject': 'user', 'predicate': 'note', 'object': old['display_text']}, ensure_ascii=False)
                await db.execute('''INSERT INTO stable_fact_versions(fact_id,version_no,display_text,
                    semantic_json,semantic_fingerprint,confidence,change_kind,previous_version,valid_from,invalid_at)
                    VALUES(?,?,?,?,?,0.9,?,?,?,?)''',
                    (fact_id, v, old['display_text'], semantic, hashlib.sha256(semantic.encode()).hexdigest(),
                     'create' if v == 1 else 'supersede', v-1 if v > 1 else None,
                     seed_time(index - len(history) + v - 2),
                     timestamp if v == len(history) else seed_time(index - len(history) + v - 1)))
            if row['state'] == 'deleted':
                await db.execute("UPDATE stable_facts SET state='deleted',active_version=NULL,deleted_at=? WHERE fact_id=?", (timestamp, fact_id))
                await db.execute('UPDATE stable_fact_versions SET invalid_at=? WHERE fact_id=?', (timestamp, fact_id))
            elif embedder._model is not None:
                from app.services.memory_v11.indexing import insert_version_embedding, prepare_passage_embedding
                prepared = await prepare_passage_embedding(row['display_text'], embedder=embedder)
                await insert_version_embedding(db, fact_id=fact_id, version_no=version, prepared=prepared)
            await db.commit()
        finally:
            await db.close()
    for worldline in {row['scope'].split('/')[1] for row in rows}:
        await validate_active_version_invariants(worldline=worldline)


def rule_results(case, scored):
    """Expose pair judgments from the existing scorer without changing its rules."""
    failures = scored['answer']['failures']
    manual = scored['answer']['needs_manual']
    results = []
    for rule, field, failure in [('must_use', 'must_use_fact_keys', 'missing'),
                                 ('must_not', 'must_not_use_fact_keys', 'forbidden'),
                                 ('unknown', 'must_express_unknown_for', None)]:
        for key in case['expected'][field]:
            pending = (key in manual if rule == 'unknown' else
                       f'{rule}_collision:{key}' in manual or f'forbidden_context:{key}' in manual or
                       f'{failure}:{key}' in manual)
            status = 'needs_manual' if pending else 'fail' if f'{failure}:{key}' in failures else 'pass'
            results.append({'rule': rule, 'key': key, 'status': status})
    return results


def section(text, anchor):
    # Compiler sections are separated by blank lines. Do not count a fact in
    # IDENTITY, quoted user input, or another section as resident memory.
    for block in text.split('\n\n'):
        if block.startswith(anchor + '\n'):
            return block
    return ''


def locations(case, qset, requests):
    catalog = {r['key']: r for r in qset['facts']}
    catalog.update({h['key']: h for r in qset['facts'] for h in r.get('history', [])})
    core, supplement, recall = [], [], []
    for request in requests:
        if request['kind'] == 'japanese_rewrite':
            continue
        for msg in request['request']['messages']:
            content = msg.get('content') or ''
            if not isinstance(content, str):
                continue
            if msg['role'] == 'system' and section(content, 'CORE FACTS'):
                core.append(section(content, 'CORE FACTS'))
                supplement.append(section(content, 'MEMORY CANDIDATES'))
            elif request['phase'] == 'tool_answer':
                recall.append(section(content, 'MEMORY CANDIDATES'))
    targets = {}
    for key in case['expected']['must_use_fact_keys']:
        display = catalog[key]['display_text']
        found = [name for name, blocks in [('resident', core), ('supplement', supplement), ('recall', recall)]
                 if any(display in block for block in blocks)]
        targets[key] = found or ['none']
    first_core = core[0] if core else ''
    count = sum(r['display_text'] in first_core for r in qset['facts'])
    return {'target_locations': targets, 'resident_fact_count': count}


def rate(pairs):
    counts = Counter(p['status'] for p in pairs)
    evaluated = counts['pass'] + counts['fail']
    return {'hits': counts['pass'], 'misses': counts['fail'], 'needs_manual': counts['needs_manual'],
            'pairs': len(pairs), 'evaluated': evaluated, 'rate': counts['pass']/evaluated if evaluated else None}


def summarize(plan, rows, qset, runtime):
    cases = {c['id']: c for c in qset['cases']}
    summary = {'transport': plan['transport'], 'question_set': 'b5c', 'question_sha256': plan['question_sha256'], 'groups': {}}
    lines = ['# B5c 第二轮', '', 'Fake 仅验证链路；token 为 UTF-8 字节上界估算，不能据此判断模型效果。',
             '命中按事实对计，待人工不计入命中分母；误提按执行计，待人工不计误提。', '']
    for group, mode in GROUPS.items():
        selected = [r for r in rows if r['group'] == group]
        completed = [r for r in selected if r['status'] == 'completed']
        def pairs(category=None, rules=('must_use',)):
            return [p for r in completed if category is None or cases[r['case_id']]['category'] == category
                    for p in r['score']['rule_results'] if p['rule'] in rules]
        hit_pairs = [p for r in completed if cases[r['case_id']]['category'] in HIT_CATEGORIES
                     for p in r['score']['rule_results'] if p['rule'] == 'must_use']
        mismentions = sum(any(p['rule'] == 'must_not' and p['status'] == 'fail'
                             for p in r['score']['rule_results']) for r in completed)
        requests = [p for r in selected for p in r.get('requests', [])]
        durations = [r.get('execution_wall_ms', r.get('elapsed_ms', 0)) for r in completed]
        tokens = [p['usage']['prompt_tokens'] for p in requests if p.get('usage')]
        active_keys = {f['key'] for f in qset['facts']}
        outside = [loc for r in completed for key, loc in r.get('target_locations', {}).items()
                   if key in active_keys and 'resident' not in loc]
        metrics = dict(mode=mode, executions=len(selected), statuses=dict(Counter(r['status'] for r in selected)),
            hit=rate(hit_pairs), change_past=rate(pairs('change_past')),
            categories={category: rate(pairs(category)) for category in sorted({c['category'] for c in cases.values()})},
            unknown=rate(pairs('unknown', ('unknown',))), mismention_count=mismentions,
            mismention_rate=mismentions/len(selected) if selected else None,
            needs_manual=sum(p['status'] == 'needs_manual' for p in pairs(rules=('must_use','must_not','unknown'))),
            incomplete=len(selected)-len(completed), request_count=len(requests),
            input_tokens=sum(tokens), input_tokens_p50=statistics.median(tokens) if tokens else None,
            input_tokens_max=max(tokens, default=None), elapsed_ms_p50=statistics.median(durations) if durations else None,
            elapsed_ms_p95=runtime.percentile(durations, .95),
            cost_cny=str(sum((Decimal(r.get('cost_cny', '0')) for r in selected), Decimal(0))),
            actual_cost_cny=str(sum((Decimal(r.get('actual_cost_cny', '0')) for r in selected), Decimal(0))))
        if mode == 'v11':
            metrics.update(recall_call_rate=sum(bool(r['score']['recall_called']) for r in completed)/len(completed) if completed else None,
                           outside_targets={'total': len(outside), **{k: sum(k in loc for loc in outside) for k in ('supplement','recall','none')}})
        summary['groups'][group] = metrics
        lines += [f'## {group} ({mode})', f"executions: {len(selected)}; statuses: {metrics['statuses']}",
                  f"命中: {metrics['hit']}", f"误提: {mismentions}/{len(selected)} ({metrics['mismention_rate']}); 待人工: {metrics['needs_manual']}",
                  f"以前值: {metrics['change_past']}; 不知道: {metrics['unknown']}",
                  *[f'{c}: {m}' for c,m in metrics['categories'].items()],
                  f"requests: {len(requests)}; input tokens total/P50/max: {sum(tokens)}/{metrics['input_tokens_p50']}/{metrics['input_tokens_max']}",
                  f"wall ms P50/P95: {metrics['elapsed_ms_p50']}/{metrics['elapsed_ms_p95']}",
                  f"cost CNY (synthetic if fake): {metrics['cost_cny']}; actual: {metrics['actual_cost_cny']}"]
        if mode == 'v11':
            lines += [f"recall rate: {metrics['recall_call_rate']}; outside targets: {metrics['outside_targets']}"]
        lines += ['']
    return summary, '\n'.join(lines)


def aggregate(run_dirs, output, manual_path, runtime):
    if not 1 <= len(run_dirs) <= 3:
        raise runtime.Refused('aggregate_requires_one_to_three_runs')
    judgments = runtime.read_json(manual_path) if manual_path else []
    overrides = {}
    for item in judgments:
        key = (item['run'], item['execution_id'], item['rule'], item['key'])
        if item['judgment'] not in {'pass','fail'} or key in overrides:
            raise runtime.Refused('invalid_manual_judgment')
        overrides[key] = item['judgment']
    used = set()
    changed = flipped = 0
    summaries = []
    names = [Path(d).resolve().name for d in run_dirs]
    if len(set(names)) != len(names):
        raise runtime.Refused('duplicate_run_directory_name')
    with runtime.fake_network_guard():
        qset, digest = runtime.load_questions('b5c')
        cases = {c['id']: c for c in qset['cases']}
        for directory in run_dirs:
            directory = directory.resolve()
            plan = runtime.read_json(directory/'plan.json')
            if plan.get('question_set') != 'b5c':
                raise runtime.Refused('aggregate_requires_b5c')
            rows = []
            for entry in plan['executions']:
                row = runtime.read_json(directory/'executions'/entry['id']/'result.json')
                if row['status'] == 'completed':
                    row['score'] = runtime.score(cases[row['case_id']], row['answer'], row['calls'], qset, mode=row['mode'])
                    for pair in row['score']['rule_results']:
                        key = (directory.name, row['id'], pair['rule'], pair['key'])
                        if key in overrides:
                            used.add(key)
                            value = overrides[key]
                            changed += pair['status'] != value
                            flipped += pair['status'] in {'pass','fail'} and pair['status'] != value
                            pair['status'] = value
                rows.append(row)
            s, _ = summarize({**plan, 'question_sha256': digest}, rows, qset, runtime)
            summaries.append({'run': directory.name, **s})
    if used != set(overrides):
        raise runtime.Refused('manual_target_not_found')
    def stats(values):
        values = [v for v in values if v is not None]
        return {'mean': statistics.mean(values) if values else None, 'min': min(values, default=None), 'max': max(values, default=None)}
    groups = {g: {metric: stats([s['groups'][g][metric]['rate'] if metric == 'hit' else s['groups'][g][metric] for s in summaries])
                  for metric in ('hit','mismention_count','mismention_rate')} for g in GROUPS}
    for group in GROUPS:
        for metric in ['unknown', *sorted(summaries[0]['groups'][group]['categories'])]:
            groups[group][metric] = stats([
                (run['groups'][group]['unknown'] if metric == 'unknown' else
                 run['groups'][group]['categories'][metric])['rate'] for run in summaries])
    result = dict(runs=summaries, groups=groups,
                  manual={'applied': len(used), 'changed': changed, 'flipped_pass_fail': flipped})
    lines = ['# B5c 多次汇总', '', f'人工: {result["manual"]}',
             '只报告数字；命中、误提、类别和 unknown 的分母规则与归档一致。', '',
             '|运行|组|命中率|误提次数|待人工|未完成|', '|---|---|---:|---:|---:|---:|']
    for s in summaries:
        for g, m in s['groups'].items():
            lines.append(f"|{s['run']}|{g}|{m['hit']['rate']}|{m['mismention_count']}|{m['needs_manual']}|{m['incomplete']}|")
    lines += ['', '|组|指标|平均|最小|最大|', '|---|---|---:|---:|---:|']
    for g, metrics in groups.items():
        for metric, v in metrics.items():
            lines.append(f"|{g}|{metric}|{v['mean']}|{v['min']}|{v['max']}|")
    output.mkdir(parents=True, exist_ok=True)
    runtime.write_json(output/'aggregate.json', result)
    (output/'aggregate.md').write_text('\n'.join(lines)+'\n', encoding='utf-8')
    return result
