"""B5c fixture, real product/fake transport, seed and offline gate contracts."""
import copy
import json
import sys
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import memory_recall_b5_real_run as b5  # noqa: E402
import memory_recall_b5c as b5c  # noqa: E402


@pytest.fixture
def frozen():
    return b5.load_questions('b5c')


def test_fixture_shape_and_alias_isolation(frozen):
    q, digest = frozen
    assert digest == b5.sha256(b5.question_path('b5c'))
    assert q['rules'] == b5.load_questions()[0]['rules']
    assert len(q['facts']) == 48
    assert {f['scope'] for f in q['facts']} == {'ownerA/steins_gate/self'}
    assert all(len(f['display_text']) <= 20 and f['display_text'].startswith('我') for f in q['facts'])
    assert Counter(c['category'] for c in q['cases']) == dict(resident=6, outside_lexical=6, outside_paraphrase=6,
        change_current=3, change_past=3, time=4, unknown=4, irrelevant=4)
    assert Counter(c['repeats'] for c in q['cases']) == {1:24, 2:12}
    assert {c['category'] for c in q['cases'] if c['repeats']==2} == {c['category'] for c in q['cases']}
    assert sum(c['repeats'] for c in q['cases']) == 48
    assert len(q['scope_sentinels']) >= 3
    assert {f['key'] for f in q['scope_sentinels']} <= {k for c in q['cases'] for k in c['expected']['must_not_use_fact_keys']}
    assert len([f for f in q['facts'] if f.get('history')]) == 3
    assert all(1 <= len(f['history']) <= 2 for f in q['facts'] if 'history' in f)
    assert [i for i,f in enumerate(q['facts']) if f.get('history')] == [22,23,24]
    assert sum(bool(f.get('time_fact')) for f in q['facts'][:16]) == 2
    assert sum(bool(f.get('time_fact')) for f in q['facts'][16:]) == 2
    catalog=b5.fact_catalog(q)
    assert len({f['key'] for f in catalog}) == len(catalog)
    for f in catalog:
        assert all(len(a)>=2 for a in f['aliases'])
        assert all(b5.normalize(a) not in b5.normalize(c['query'])
                   for a in f['aliases'] for c in q['cases'])
        assert len(f['aliases']) == len(f['alias_languages'])
        assert Counter(f['alias_languages'])['zh'] >= 2
        assert Counter(f['alias_languages'])['ja'] >= 2
        for other in catalog:
            if other['key'] != f['key']:
                assert not any(a in s for a in f['aliases'] for s in [other['display_text'],*other['aliases']])
    assert Counter(e['group'] for e in b5.make_plan(q,digest)['executions']) == {'A':48,'B':48,'C':48}
    assert Counter(e['mode'] for e in b5.make_plan(*b5.load_questions())['executions']) == {'legacy':41,'v11':41}


def test_time_facts_have_arabic_numeric_aliases(frozen):
    time_facts=[f for f in frozen[0]['facts'] if f.get('time_fact')]
    assert len(time_facts)==4
    assert all(any(any('0'<=ch<='9' for ch in alias) for alias in f['aliases']) for f in time_facts)


def test_version_texts_do_not_label_past_or_present(frozen):
    for fact in frozen[0]['facts']:
        if fact.get('history'):
            assert all(word not in row['display_text']
                       for row in [fact,*fact['history']] for word in ('以前','现在','如今'))


def test_negative_case_distractors_are_resident(frozen):
    q,_=frozen
    positions={f['key']:i for i,f in enumerate(q['facts'])}
    for case in q['cases']:
        if case['category'] in {'irrelevant','unknown'}:
            assert all(positions[key]>=16 for key in case['expected']['must_not_use_fact_keys'])
    stamps=next(f for f in q['facts'] if f['key']=='b5c_stamps')
    assert '青色' not in stamps['aliases']
    assert {'青色の切手','青い切手'} <= set(stamps['aliases'])


IRRELEVANT_NATURAL_ANSWERS = {
    'irrelevant_sky': [
        '空が青く見えるのは、青色の光が大気中で散乱されやすいからよ。',
        '太陽の光が大気に入ると、短い波長の光ほど強く散乱するの。それで昼間の空は青く見えるわ。',
    ],
    'irrelevant_arithmetic': [
        '37×25は925よ。37×100を4で割れば、暗算しやすいわ。',
        '25は100の4分の1よ。3700÷4だから、答えは925ね。',
    ],
    'irrelevant_greeting': [
        'こんにちは。今日はどんな一日だった？',
        'いいわよ、少し話しましょう。今、何か気になっていることはある？',
    ],
    'irrelevant_poem': [
        'やわらかな春風に、桜の花びらがひとひら舞う。',
        '若葉が光をほどき、朝の小道に春が満ちてゆく。',
    ],
}


@pytest.mark.parametrize('mode',['legacy','v11'])
@pytest.mark.parametrize('case_id,answer',[(key,answer) for key,answers in IRRELEVANT_NATURAL_ANSWERS.items() for answer in answers])
def test_irrelevant_natural_answers_do_not_trigger_memory(frozen,case_id,answer,mode):
    q,_=frozen
    case=next(c for c in q['cases'] if c['id']==case_id)
    result=b5.score(case,answer,[],q,mode=mode)
    assert result['status']=='pass',result
    assert result['used_fact_keys']==[]


@pytest.mark.parametrize('key,answer', [
    ('guitar_time','火曜日の19時よ。'), ('album_time','毎月15日だよ。'),
    ('climbing_time','土曜の午前10時ね。'), ('shoe_time','9月3日よ。'),
    ('wallet','紫色の財布よ。'), ('wallet_green','緑色の財布だったわ。'),
    ('towel','アイボリーよ。'), ('lamp','アンバーよ。'),
    ('helmet','銀のヘルメットを使うの。'), ('stamps','青色の切手よ。'),
    ('board','楡のまな板を使ってるわ。'), ('coins','陶製の壺よ。'),
    ('laundry','粉石鹸を使うのよ。'), ('basket','籐の籠に入れているわ。'),
    ('bell','銅の鈴ね。'), ('vegetable','蒸したカボチャね。'),
    ('soup','昆布のスープよ。'), ('coat','トグルボタンよ。'),
    ('puzzle','港の絵が好きなのよ。'), ('holiday','ログハウスね。'),
    ('commute','市電を使ってるわ。'),
])
def test_common_japanese_answers_are_recognized(frozen,key,answer):
    q,_=frozen
    case=copy.deepcopy(q['cases'][0])
    case['query']='私のこの好みは何だった？'
    case['expected'].update(must_use_fact_keys=['b5c_'+key],must_not_use_fact_keys=[])
    result=b5.score(case,answer,[],q)
    assert result['answer']['status']=='pass',result


@pytest.mark.parametrize('case_id', [c['id'] for c in b5.load_questions('b5c')[0]['cases']])
def test_case_integrity(frozen, case_id):
    from app.services.prompt_compiler import supplement_need_text
    from app.services.memory_v11.recall import shared_bigram_count
    q,_=frozen
    case=next(c for c in q['cases'] if c['id']==case_id)
    keys={f['key'] for f in b5.fact_catalog(q)}
    expected=case['expected']
    assert set(expected['must_use_fact_keys']+expected['must_not_use_fact_keys']) <= keys
    assert not (set(expected['must_use_fact_keys']) & set(expected['must_not_use_fact_keys']))
    assert len(case['query'])<=30 and case['history']==[]
    assert expected['exact_need_count'] is None and expected['max_need_count']==3 and expected['diagnostic_only'] is False
    assert set(expected['must_express_unknown_for']) <= q['unknown_subjects'].keys()
    for subject in expected['must_express_unknown_for']:
        assert len(q['unknown_subjects'][subject])>=4
    facts={f['key']:(i,f) for i,f in enumerate(q['facts'])}
    for key in expected['must_use_fact_keys']:
        if case['category'].startswith('outside_'):
            i,f=facts[key]
            assert i<16
            bigrams=shared_bigram_count(need=supplement_need_text(case['query']),display=f['display_text'])
            if case['category']=='outside_lexical':
                assert bigrams>=2
            else:
                assert bigrams<=1
        elif case['category'] in {'resident','change_current'}:
            assert facts[key][0]>=16
    assert expected['recall_memory'] == ('required' if case['category']=='outside_paraphrase' else
                                       'forbidden' if case['category']=='irrelevant' else 'allowed')


def test_a_trimming_preserves_targets_chains_and_sentinels(frozen):
    q,_=frozen
    for case in q['cases']:
        rows=b5c.selected_rows(q,case,'A')
        assert len(rows)==24+len(q['scope_sentinels'])
        keys={r['key'] for r in rows} | {h['key'] for r in rows for h in r.get('history',[])}
        assert set(case['expected']['must_use_fact_keys']+case['expected']['must_not_use_fact_keys']) <= keys
        assert {s['key'] for s in q['scope_sentinels']} <= keys
        assert rows==b5c.selected_rows(q,case,'A')
    case=next(c for c in q['cases'] if c['id']=='change_past_commute_metro')
    chain=next(r for r in b5c.selected_rows(q,case,'A') if r['key']=='b5c_commute')
    assert [h['key'] for h in chain['history']]==['b5c_commute_metro','b5c_commute_bike']


@pytest.mark.parametrize('mode', ['legacy','v11'])
async def test_seed_versions_and_file_order(frozen,mode):
    from app.db import get_db, init_db
    from app.services.memory_v11.repository import validate_active_version_invariants
    q,_=frozen
    await init_db()
    await b5.seed_store(q,q['cases'][0],mode,SimpleNamespace(_model=None),'B' if mode=='legacy' else 'C')
    if mode=='v11':
        from app.services.prompt_compiler import _select_v11_resident_profile
        from app.services.memory_v11.recall import MemoryScope
        profile=await _select_v11_resident_profile(scope=MemoryScope('ownerA','steins_gate','self'),tool_capable=True)
        assert {r['display_text'] for r in profile.facts}=={f['display_text'] for f in q['facts'][16:]}
    else:
        from app.services.memory import memory_service
        selected=await memory_service.select_core_facts('ownerA','steins_gate','不相关的问题',identity_mode='self')
        assert len(selected)==24
        assert {r.content.split(': ',1)[1] for r in selected}=={f['display_text'] for f in q['facts'][-24:]}
    db=await get_db('steins_gate','memory')
    try:
        if mode=='legacy':
            rows=[dict(r) for r in await (await db.execute('SELECT * FROM core_facts WHERE session_id="ownerA" AND identity_mode="self"')).fetchall()]
            for f in q['facts']:
                current=next(r for r in rows if r['fact_value']==f['display_text'])
                assert current['is_current']==1
                assert current['created_at']==b5c.seed_time(q['facts'].index(f))
                for h in f.get('history',[]):
                    old=next(r for r in rows if r['fact_value']==h['display_text'])
                    assert old['is_current']==0 and old['created_at']<current['created_at']
        else:
            await validate_active_version_invariants(worldline='steins_gate')
            for i,f in enumerate(q['facts']):
                row=await (await db.execute('''SELECT f.*,v.display_text FROM stable_facts f JOIN stable_fact_versions v
                    ON v.fact_id=f.fact_id AND v.version_no=f.active_version WHERE v.display_text=?''',(f['display_text'],))).fetchone()
                assert row['updated_at']==row['created_at']==b5c.seed_time(i)
                versions=[dict(v) for v in await (await db.execute('SELECT * FROM stable_fact_versions WHERE fact_id=? ORDER BY version_no',(row['fact_id'],))).fetchall()]
                assert [v['display_text'] for v in versions]==[h['display_text'] for h in f.get('history',[])]+[f['display_text']]
                assert sum(v['invalid_at'] is None for v in versions)==1
                assert [v['previous_version'] for v in versions]==[None]+list(range(1,len(versions)))
                assert all(v['valid_from']<v['invalid_at'] for v in versions[:-1])
    finally:
        await db.close()


def test_full_fake_product_run_and_locations(tmp_path,frozen):
    q,d=frozen
    plan=b5.make_plan(q,d,e5_snapshot='')
    summary=b5.run_suite(tmp_path,plan,q)
    cases={c['id']:c for c in q['cases']}
    for group in 'ABC':
        assert summary['groups'][group]['statuses']=={'completed':48},summary
    for entry in plan['executions']:
        row=b5.read_json(tmp_path/'executions'/entry['id']/'result.json')
        case=cases[row['case_id']]
        assert row['resident_fact_count']==(32 if entry['group']=='C' else 24), row
        assert set(row['target_locations'])==set(case['expected']['must_use_fact_keys'])
        assert all(loc for loc in row['target_locations'].values())
        if entry['group'] in ('A','C') and case['category'] in {'irrelevant','unknown'}:
            core=next(b5c.section(m['content'],'CORE FACTS') for m in row['requests'][0]['request']['messages']
                      if m['role']=='system' and b5c.section(m['content'],'CORE FACTS'))
            catalog={f['key']:f for f in q['facts']}
            assert all(catalog[key]['display_text'] in core for key in case['expected']['must_not_use_fact_keys'])
        if entry['group']=='A' and case['category']!='change_past':
            assert all('resident' in loc for loc in row['target_locations'].values()),row
        if entry['group']=='C' and case['category'].startswith('outside_'):
            assert all('resident' not in loc for loc in row['target_locations'].values()),row
            if case['category']=='outside_lexical':
                assert all('supplement' in loc for loc in row['target_locations'].values()),row
        if entry['group']=='C' and case['category']=='resident':
            assert all('resident' in loc for loc in row['target_locations'].values()),row
        if entry['group']=='B' and case['category'].startswith('outside_'):
            assert all('resident' not in loc for loc in row['target_locations'].values()),row
    assert summary['groups']['C']['recall_call_rate']==1
    before={p:p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    rescored=b5.rescore_run(tmp_path)
    assert rescored['groups']==summary['groups']
    assert all(p.read_bytes()==data for p,data in before.items())


def saved_runs(tmp_path,q,d,misses=0,manual=False,mismentions=0,mismentions_by_group=None):
    paths=[]
    for run in range(3):
        path=tmp_path/f'run-{run}'
        path.mkdir()
        plan=b5.make_plan(q,d)
        b5.write_json(path/'plan.json',plan)
        lost=0
        bad=Counter()
        for e in plan['executions']:
            c=next(c for c in q['cases'] if c['id']==e['case_id'])
            catalog={f['key']:f for f in b5.fact_catalog(q)}
            reply='。'.join(catalog[k]['aliases'][2] for k in c['expected']['must_use_fact_keys'])
            if c['category']=='unknown':
                reply='。'.join(q['unknown_subjects'][s][2]+'は知らない' for s in c['expected']['must_express_unknown_for'])
            if e['group']=='C' and c['category'] in b5c.HIT_CATEGORIES and lost<misses:
                reply='了解したわ'; lost+=1
            limit=(mismentions_by_group or {}).get(e['group'],[mismentions if e['group']=='C' else 0]*3)[run]
            if c['expected']['must_not_use_fact_keys'] and bad[e['group']]<limit:
                reply+='。'+catalog[c['expected']['must_not_use_fact_keys'][0]]['aliases'][2]
                bad[e['group']]+=1
            if manual and e['id']=='C-resident_pillow-1':
                reply=b5.FALLBACK_ANSWERS[0]
            row={**e,'status':'completed','answer':reply,'calls':[]}
            b5.write_json(path/'executions'/e['id']/'result.json',row)
        paths.append(path)
    return paths


@pytest.mark.parametrize('misses,expected',[(0,'PASS'),(1,'PASS'),(2,'FAIL'),(10,'FAIL')])
def test_aggregate_gate_hit_boundary(tmp_path,frozen,misses,expected):
    q,d=frozen
    paths=saved_runs(tmp_path,q,d,misses=misses)
    result=b5c.aggregate(paths,tmp_path/'out',None,b5)
    assert result['gate']['status']==expected,result
    assert result['groups']['A']['hit']['mean']==1
    assert result['groups']['C']['hit']['mean']==pytest.approx(1-misses/34)
    if misses>=2:
        assert 'resident' in result['investigate']
    assert (tmp_path/'out'/'aggregate.md').is_file()


@pytest.mark.parametrize('count,expected',[(1,'PASS'),(2,'FAIL')])
def test_aggregate_gate_mismention_boundary(tmp_path,frozen,count,expected):
    paths=saved_runs(tmp_path,*frozen,mismentions=count)
    result=b5c.aggregate(paths,tmp_path/'out',None,b5)
    assert result['gate']['status']==expected
    assert result['groups']['C']['mismention_count']==dict(mean=count,min=count,max=count)


def test_aggregate_pending_manual_overrides_and_offline(tmp_path,frozen,monkeypatch):
    paths=saved_runs(tmp_path,*frozen,manual=True)
    before={p:p.read_bytes() for path in paths for p in path.rglob('*') if p.is_file()}
    def forbidden(*args,**kwargs):
        pytest.fail('offline evaluation attempted execution or paid authorization')
    monkeypatch.setattr(b5,'execute_subprocess',forbidden)
    monkeypatch.setattr(b5,'check_real_gate',forbidden)
    result=b5c.aggregate(paths,tmp_path/'pending',None,b5)
    assert result['gate']['status']=='PENDING'
    manual=[dict(run=p.name,execution_id='C-resident_pillow-1',rule='must_use',key='b5c_pillow',judgment='pass') for p in paths]
    # Override an automatic pass to fail and a fail to pass, independently.
    manual += [dict(run=paths[0].name,execution_id='A-resident_pillow-1',rule='must_use',key='b5c_pillow',judgment='fail')]
    manual_path=tmp_path/'manual.json'
    b5.write_json(manual_path,manual)
    result=b5c.aggregate(paths,tmp_path/'manual',manual_path,b5)
    assert result['gate']['status']=='PASS'
    assert result['manual']==dict(applied=4,changed=4,flipped_pass_fail=1)
    assert all(p.read_bytes()==data for p,data in before.items())
    assert b5.main(['--aggregate',*[str(p) for p in paths],'--output',str(tmp_path/'cli'),'--manual',str(manual_path)])==0


def test_pair_metrics_exclude_manual_and_separate_past(frozen):
    q,d=frozen
    c=copy.deepcopy(q['cases'][0])
    c['expected'].update(must_not_use_fact_keys=['b5c_drink'],must_express_unknown_for=['shoe_size'])
    c['query']='随便问问'
    s=b5.score(c,'アメリカンじゃない。',[],q)
    p={(p['rule'],p['key']):p['status'] for p in s['rule_results']}
    assert p=={('must_use','b5c_pillow'):'fail',('must_not','b5c_drink'):'needs_manual',('unknown','shoe_size'):'needs_manual'}
    plan=b5.make_plan(q,d)
    row={**plan['executions'][0],'status':'completed','score':s}
    summary,_=b5.summarize(plan,[row],q)
    assert summary['groups']['A']['mismention_count']==0
    assert summary['groups']['A']['needs_manual']==2


def test_locations_distinguish_profile_supplement_and_recall(frozen):
    q,_=frozen
    case=copy.deepcopy(q['cases'][0])
    targets=q['facts'][:3]
    case['expected']['must_use_fact_keys']=[t['key'] for t in targets]
    def request(phase, second=''):
        return dict(kind=phase,phase=phase,request={'messages':[
            {'role':'system','content':'IDENTITY\n'+targets[2]['display_text']+'\n\nCORE FACTS\n- '+targets[0]['display_text']+
             '\n\nCURRENT STATE\nstate\n\nMEMORY CANDIDATES\n'+targets[1]['display_text']},
            {'role':'user','content':'你记得什么？'},
            *([{'role':'system','content':'MEMORY CANDIDATES\n'+second}] if second else [])]})
    result=b5c.locations(case,q,[request('first'),request('tool_answer',targets[2]['display_text'])])
    assert result['resident_fact_count']==1
    assert result['target_locations']=={targets[0]['key']:['resident'],targets[1]['key']:['supplement'],targets[2]['key']:['recall']}
    assert 'none' in b5c.locations(case,q,[request('first')])['target_locations'][targets[2]['key']]


def test_manual_can_reverse_forbidden_and_unknown_judgments(tmp_path,frozen):
    paths=saved_runs(tmp_path,*frozen,mismentions=1)
    unknown=paths[0]/'executions'/'C-unknown_luggage_size-1'/'result.json'
    row=b5.read_json(unknown)
    row['answer']='了解したわ'
    b5.write_json(unknown,row)
    overrides=[dict(run=p.name,execution_id='C-outside_lexical_umbrella-1',rule='must_not',
                    key='b5c_other_owner_rain',judgment='pass') for p in paths]
    overrides.append(dict(run=paths[0].name,execution_id='C-unknown_luggage_size-1',rule='unknown',key='luggage_size',judgment='pass'))
    manual=tmp_path/'manual.json'
    b5.write_json(manual,overrides)
    result=b5c.aggregate(paths,tmp_path/'out',manual,b5)
    assert result['gate']['status']=='PASS'
    assert result['groups']['C']['mismention_count']['mean']==0
    assert result['manual']==dict(applied=4,changed=4,flipped_pass_fail=3)


@pytest.mark.parametrize('c_counts,status',[([2,2,1],'PASS'),([3,2,1],'FAIL')])
def test_aggregate_fractional_mismention_boundary(tmp_path,frozen,c_counts,status):
    paths=saved_runs(tmp_path,*frozen,mismentions_by_group={'A':[0,1,1],'C':c_counts})
    result=b5c.aggregate(paths,tmp_path/'out',None,b5)
    assert [r['groups']['A']['mismention_count'] for r in result['runs']]==[0,1,1]
    assert [r['groups']['C']['mismention_count'] for r in result['runs']]==c_counts
    assert result['gate']['status']==status
    assert result['gate']['mismention_pass']==(status=='PASS')


@pytest.mark.parametrize('failed_runs,investigate',[(1,False),(2,True)])
def test_aggregate_investigates_unknown_after_manual_review(tmp_path,frozen,failed_runs,investigate):
    paths=saved_runs(tmp_path,*frozen)
    manual=tmp_path/'manual.json'
    b5.write_json(manual,[dict(run=p.name,execution_id='C-unknown_luggage_size-1',rule='unknown',
                              key='luggage_size',judgment='fail') for p in paths[:failed_runs]])
    result=b5c.aggregate(paths,tmp_path/'out',manual,b5)
    assert ('unknown' in result['investigate'])==investigate
    assert result['gate']['status']=='PASS'
    assert result['runs'][0]['groups']['A']['unknown']['rate']==1
    assert result['runs'][0]['groups']['C']['unknown']['rate']==pytest.approx(.8)
    assert result['runs'][0]['groups']['C']['categories']['unknown']['rate'] is None
    assert ('unknown' in (tmp_path/'out'/'aggregate.md').read_text(encoding='utf-8'))==investigate


def replace_saved_result(path,execution_id,**fields):
    file=path/'executions'/execution_id/'result.json'
    row=b5.read_json(file)
    row.update(fields)
    b5.write_json(file,row)


@pytest.mark.parametrize('execution_id,group',[
    ('B-resident_pillow-1','B'), ('C-unknown_luggage_size-1','C'), ('C-change_past_drink_tea-1','C'),
])
@pytest.mark.parametrize('misses,status',[(0,'PASS'),(2,'FAIL')])
def test_non_gate_manual_pairs_do_not_block_decision(tmp_path,frozen,execution_id,group,misses,status):
    paths=saved_runs(tmp_path,*frozen,misses=misses)
    replace_saved_result(paths[0],execution_id,answer=b5.FALLBACK_ANSWERS[0])
    result=b5c.aggregate(paths,tmp_path/'out',None,b5)
    assert result['gate']['status']==status
    metrics=result['runs'][0]['groups'][group]
    assert metrics['needs_manual']>0
    assert metrics['gate_needs_manual']==0


@pytest.mark.parametrize('execution_id,group,answer',[
    ('C-unknown_luggage_size-1','C','スーツケースの寸法は知らない。山小屋じゃない。'),
    ('A-irrelevant_sky-1','A','青い切手じゃない。'),
])
def test_must_not_manual_pairs_still_block_in_every_category(tmp_path,frozen,execution_id,group,answer):
    paths=saved_runs(tmp_path,*frozen)
    replace_saved_result(paths[0],execution_id,answer=answer)
    result=b5c.aggregate(paths,tmp_path/'out',None,b5)
    assert result['gate']['status']=='PENDING'
    assert result['runs'][0]['groups'][group]['gate_needs_manual']==1


@pytest.mark.parametrize('execution_id,group,status',[
    ('B-resident_pillow-1','B','PASS'), ('C-change_past_drink_tea-1','C','PASS'),
    ('A-change_past_drink_tea-1','A','PASS'), ('A-resident_pillow-1','A','PENDING'),
    ('C-unknown_luggage_size-1','C','PENDING'), ('C-irrelevant_sky-1','C','PENDING'),
])
def test_pending_only_counts_unfinished_gate_rules(tmp_path,frozen,execution_id,group,status):
    paths=saved_runs(tmp_path,*frozen)
    replace_saved_result(paths[0],execution_id,status='not_run_budget')
    result=b5c.aggregate(paths,tmp_path/'out',None,b5)
    assert result['gate']['status']==status
    metrics=result['runs'][0]['groups'][group]
    assert metrics['incomplete']==1
    assert metrics['gate_incomplete']==int(status=='PENDING')



def test_aggregate_cli_excludes_all_groups_and_repeats_of_error_question(tmp_path, frozen):
    paths = saved_runs(tmp_path, *frozen)
    target = 'resident_pillow'
    replace_saved_result(paths[0], 'C-' + target + '-1', status='error', error_type='ReadTimeout')
    replace_saved_result(paths[0], 'A-' + target + '-1', answer='了解したわ')
    originals = {p: p.read_bytes() for path in paths for p in path.rglob('*.json')}
    output = tmp_path / 'out'
    with b5.fake_network_guard():
        assert b5.main(['--aggregate', *map(str, paths), '--output', str(output)]) == 0
    result = b5.read_json(output / 'aggregate.json')
    repeats = next(c['repeats'] for c in frozen[0]['cases'] if c['id'] == target)
    assert result['excluded_questions'] == 1
    assert result['excluded_executions'] == 3 * repeats
    assert result['exclusions'] == [
        dict(run=paths[0].name, case_ids=[target], excluded_questions=1, excluded_executions=3 * repeats),
        *[dict(run=p.name, case_ids=[], excluded_questions=0, excluded_executions=0) for p in paths[1:]],
    ]
    for group in 'ABC':
        assert result['runs'][0]['groups'][group]['executions'] == 48 - repeats
        assert result['runs'][0]['groups'][group]['incomplete'] == 0
        assert result['runs'][1]['groups'][group]['executions'] == 48
    assert result['groups']['A']['hit']['mean'] == result['groups']['C']['hit']['mean'] == 1
    assert result['gate']['status'] == 'PASS'
    markdown = (output / 'aggregate.md').read_text(encoding='utf-8')
    assert '成对剔除题数: 1' in markdown and f'{paths[0].name}: {target}' in markdown
    assert all(p.read_bytes() == content for p, content in originals.items())


@pytest.mark.parametrize('status', ['interrupted', 'not_run_budget', 'not_run_after_accounting_error', 'stopped'])
def test_aggregate_cli_rejects_stopped_run_even_outside_gate(tmp_path, frozen, status):
    paths = saved_runs(tmp_path, *frozen)
    replace_saved_result(paths[0], 'B-resident_pillow-1', status=status)
    with pytest.raises(b5.Refused, match='aggregate_requires_complete_runs'):
        b5.aggregate(paths, tmp_path / 'out')


def test_aggregate_rejects_third_error_even_at_end_of_run(tmp_path, frozen):
    paths = saved_runs(tmp_path, *frozen)
    entries = b5.read_json(paths[0] / 'plan.json')['executions'][-3:]
    for e in entries:
        replace_saved_result(paths[0], e['id'], status='error')
    with pytest.raises(b5.Refused, match='aggregate_requires_complete_runs'):
        b5.aggregate(paths, tmp_path / 'out')
