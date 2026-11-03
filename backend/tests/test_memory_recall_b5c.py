"""B5c fixture, real product/fake transport, seed and offline gate contracts."""

import copy

import json

import sys

from collections import Counter

from pathlib import Path

from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))

import memory_recall_b5_real_run as b5

import memory_recall_b5c as b5c

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
    assert Counter(e['group'] for e in b5.make_plan(q,digest)['executions']) == {'C':48}
    assert Counter(e['mode'] for e in b5.make_plan(*b5.load_questions())['executions']) == {'v11':48}

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

@pytest.mark.parametrize('mode', ['v11'])
async def test_seed_versions_and_file_order(frozen,mode):
    from app.db import get_db, init_db
    from app.services.memory_v11.repository import validate_active_version_invariants
    q,_=frozen
    await init_db()
    await b5.seed_store(q,q['cases'][0],mode,SimpleNamespace(_model=None),'C')
    from app.services.prompt_compiler import _select_v11_resident_profile
    from app.services.memory_v11.recall import MemoryScope
    profile=await _select_v11_resident_profile(scope=MemoryScope('ownerA','steins_gate','self'),tool_capable=True)
    assert {r['display_text'] for r in profile.facts}=={f['display_text'] for f in q['facts'][16:]}
    db=await get_db('steins_gate','memory')
    try:
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


def test_fixture_sha256_matches_archive_blob():
    import hashlib

    path = Path(__file__).parent / 'fixtures/memory_recall_b5c_question_set.json'
    raw = path.read_bytes().replace(b'\r\n', b'\n')  # Git's CRLF checkout conversion only.
    assert hashlib.sha256(raw).hexdigest() == '7709f978be0ce76976b5039633e64fa495ebc74120a4d4175d2375ad44a3efcb'
