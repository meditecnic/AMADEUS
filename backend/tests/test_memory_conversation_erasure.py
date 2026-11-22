"""Conversation UI actions must govern the same v11 records as Archive."""
import asyncio
import json
from datetime import datetime, timezone
from io import BytesIO
from zipfile import ZipFile

import pytest

from test_memory_v11_api import (
    isolated_client, _create_conversation, _seed_fact_via_reconciler,
    _facts_scope, _db_rows, _db_execute,
    _create_episodic,
    _s3d2_obs,
)


def _change(owner, fact, expected, conversation, message, text, *, op="REFINE",
            worldline="steins_gate", identity_mode="self"):
    from app.services.memory_v11.reconciler import apply_validated_memory_operations

    return asyncio.run(apply_validated_memory_operations(
        session_id=owner, worldline=worldline, identity_mode=identity_mode,
        observations=[_s3d2_obs(ref="o1", display_text=text, source_message_id=message)],
        operations=[{
            "op": op, "observation_ref": "o1", "target_fact_id": fact,
            "expected_version": expected, "fact_text": text if op != "ATTACH" else None,
            "reason_code": {"CREATE": "new_fact", "REFINE": "compatible_refinement", "ATTACH": "equivalent_evidence"}[op],
        }],
        source_evidence=[{
            "source_message_id": message, "conversation_id": conversation,
            "source_role": "user", "source_fingerprint": f"source-{message}",
            "source_created_at": "2026-11-22T08:00:00+00:00", "excerpt": text,
        }],
        candidate_allowlist=[fact] if fact else [],
    ))


def _message(client, owner, text):
    from app.models import save_message

    conversation = _create_conversation(client, owner, text)
    return conversation, asyncio.run(save_message(owner, "user", text, conversation_id=conversation))


@pytest.mark.parametrize("action", ["forget", "delete"])
def test_erasure_restores_older_fact_version(isolated_client, action):
    client, owner = isolated_client, "restore-owner"
    _, _, fact, _ = _source(client, owner, "I run every week")
    conversation, message = _message(client, owner, "I run every week at a private location")
    _change(owner, fact, 1, conversation, message, "I run every week at a private location")

    params = {"session_id": owner, "worldline": "steins_gate"}
    if action == "forget":
        response = client.post(f"/api/conversations/{conversation}/forget", params=params, json={"forget_long_term": True})
        assert response.status_code == 200, response.text
    else:
        response = client.delete(f"/api/conversations/{conversation}", params={**params, "forget_long_term": True})
        assert response.status_code == 204, response.text
    details = client.get(f"/api/memory/facts/{fact}/details", params=_facts_scope(owner)).json()
    assert details["fact"]["display_text"] == "I run every week"
    assert details["fact"]["active_version"] == 3
    assert [v["version_no"] for v in details["versions"]] == [1, 3]
    assert details["versions"][-1]["restored_from_version"] == 1
    assert _db_rows(
        "SELECT version_no FROM stable_fact_version_embeddings WHERE fact_id=? ORDER BY version_no", (fact,),
    ) == [{"version_no": 3}]
    export = client.get("/api/data/export?kind=memory&worldline=sg")
    assert export.status_code == 200, export.text
    with ZipFile(BytesIO(export.content)) as archive:
        exported, = json.loads(archive.read("sg/memory.json"))["stable_facts"]
    assert exported["restored_from_version"] == 1
    assert exported["display_text"] == "I run every week"


def _source(client, owner, text):
    from app.models import save_message
    conversation = _create_conversation(client, owner, text)
    message = asyncio.run(save_message(owner, 'user', text, conversation_id=conversation))
    fact, observation = _seed_fact_via_reconciler(
        session_id=owner, display_text=text, source_message_id=message,
        conversation_id=conversation, excerpt=text,
    )
    return conversation, message, fact, observation


@pytest.mark.parametrize('action', ['forget', 'delete'])
def test_retained_memory_marks_removed_history_source_unavailable(isolated_client, action):
    client = isolated_client
    owner = 'retained-source-owner'
    conversation, _, fact, observation = _source(client, owner, 'private source excerpt')
    params = {'session_id': owner, 'worldline': 'steins_gate'}
    if action == 'forget':
        response = client.post(f'/api/conversations/{conversation}/forget', params=params, json={'forget_long_term': False})
    else:
        response = client.delete(f'/api/conversations/{conversation}', params=params)
    assert response.status_code in (200, 204), response.text
    details = client.get(f'/api/memory/facts/{fact}/details', params=_facts_scope(owner))
    assert details.status_code == 200, details.text
    sources = _db_rows('SELECT source_state, excerpt FROM memory_observation_sources WHERE observation_id=?', (observation,))
    assert sources == [{'source_state': 'deleted', 'excerpt': None}]
    assert 'private source excerpt' not in str(details.json()['provenance'])


def test_delete_can_also_erase_exclusive_long_term_memory(isolated_client):
    client, owner = isolated_client, "delete-with-forget-owner"
    conversation, _, fact, _ = _source(client, owner, "exclusive secret")
    params = {"session_id": owner, "worldline": "steins_gate", "forget_long_term": True}
    assert client.delete(f"/api/conversations/{conversation}", params=params).status_code == 204
    assert client.get(f"/api/memory/facts/{fact}/details", params=_facts_scope(owner)).status_code == 404


def test_forget_erases_v11_exclusive_fact_but_keeps_other_conversation(isolated_client):
    client = isolated_client
    owner = 'forget-v11-owner'
    conversation, _, fact, observation = _source(client, owner, 'exclusive secret')
    _, _, other_fact, other_observation = _source(client, owner, 'other conversation')
    response = client.post(f'/api/conversations/{conversation}/forget',
        params={'session_id': owner, 'worldline': 'steins_gate'}, json={'forget_long_term': True})
    assert response.status_code == 200, response.text
    assert client.get(f'/api/memory/facts/{fact}/details', params=_facts_scope(owner)).status_code == 404
    assert client.get(f'/api/memory/facts/{other_fact}/details', params=_facts_scope(owner)).status_code == 200
    assert _db_rows('SELECT display_text, semantic_json FROM memory_observations WHERE observation_id=?', (observation,)) == [{'display_text': None, 'semantic_json': None}]
    assert _db_rows('SELECT excerpt FROM memory_observation_sources WHERE observation_id=?', (other_observation,))[0]['excerpt'] == 'other conversation'


@pytest.mark.parametrize("protected_by", ["pin", "user_edit"])
def test_forget_retains_user_protected_fact_and_erases_source(isolated_client, protected_by):
    client, owner = isolated_client, "protected-fact-owner"
    conversation, _, fact, observation = _source(client, owner, "private provenance")
    fact_path = f"/api/memory/facts/{fact}"
    if protected_by == "pin":
        response = client.patch(f"{fact_path}/presentation", params=_facts_scope(owner), json={"is_pinned": True})
    else:
        response = client.patch(fact_path, params=_facts_scope(owner),
                                json={"expected_version": 1, "display_text": "manually saved fact"})
    assert response.status_code == 200, response.text

    response = client.post(f"/api/conversations/{conversation}/forget",
        params={"session_id": owner, "worldline": "steins_gate"}, json={"forget_long_term": True})
    assert response.status_code == 200, response.text
    assert client.get(f"{fact_path}/details", params=_facts_scope(owner)).status_code == 200
    assert _db_rows(
        "SELECT source_state,excerpt FROM memory_observation_sources WHERE observation_id=?", (observation,),
    ) == [{"source_state": "deleted", "excerpt": None}]


def test_forget_returns_live_support_to_candidates_when_fact_dies(isolated_client):
    client = isolated_client
    owner = 'shared-v11-owner'
    conversation, _, fact, observation = _source(client, owner, 'first support')
    other_conversation, other_message = _message(client, owner, 'independent support')
    _change(owner, fact, 1, other_conversation, other_message, 'independent support', op="ATTACH")
    response = client.post(f'/api/conversations/{conversation}/forget',
        params={'session_id': owner, 'worldline': 'steins_gate'}, json={'forget_long_term': True})
    assert response.status_code == 200, response.text
    assert client.get(f'/api/memory/facts/{fact}/details', params=_facts_scope(owner)).status_code == 404
    assert _db_rows('SELECT source_state,excerpt FROM memory_observation_sources WHERE observation_id=?', (observation,)) == [{'source_state': 'deleted', 'excerpt': None}]
    assert _db_rows(
        """SELECT o.status,o.display_text,s.excerpt FROM memory_observations o
           JOIN memory_observation_sources s USING(observation_id) WHERE s.conversation_id=?""",
        (other_conversation,),
    ) == [{"status": "candidate", "display_text": "independent support", "excerpt": "independent support"}]
    assert not _db_rows("SELECT 1 FROM memory_deleted_sources WHERE source_message_id=?", (other_message,))


@pytest.mark.parametrize('first_forget_long_term', [True, False])
@pytest.mark.parametrize('shared_observation', [True, False])
def test_successive_forget_respects_all_source_authorizations(isolated_client, first_forget_long_term, shared_observation):
    client, owner = isolated_client, 'successive-owner'
    a, _, fact, observation = _source(client, owner, 'first contribution')
    b, message_b, _, obs_b = _source(client, owner, 'second contribution')
    if shared_observation:
        _db_execute('''INSERT INTO memory_observation_sources(observation_id,source_message_id,conversation_id,
                       source_role,source_fingerprint,source_created_at,source_state,excerpt)
                       VALUES(?,?,?,'user','shared-second','2026-09-08','present','second contribution')''', (observation,message_b,b))
    else:
        _db_execute("INSERT INTO stable_fact_evidence(fact_id,version_no,observation_id,evidence_role) VALUES(?,1,?,'supporting')", (fact,obs_b))
    params = {'session_id':owner,'worldline':'steins_gate'}
    assert client.post(f'/api/conversations/{a}/forget',params=params,json={'forget_long_term':first_forget_long_term}).status_code == 200
    expected_after_first = 404 if first_forget_long_term and not shared_observation else 200
    assert client.get(f'/api/memory/facts/{fact}/details',params=_facts_scope(owner)).status_code == expected_after_first
    assert client.post(f'/api/conversations/{b}/forget',params=params,json={'forget_long_term':True}).status_code == 200
    expected = 404 if first_forget_long_term else 200
    assert client.get(f'/api/memory/facts/{fact}/details',params=_facts_scope(owner)).status_code == expected


def test_erasure_resumes_after_history_failure_without_resurrecting_memory(isolated_client, monkeypatch):
    from app.services import conversation_erasure as erasure
    client, owner = isolated_client, 'recovery-owner'
    conversation, source, fact, _ = _source(client, owner, 'erase after failure')
    original = erasure._erase_history
    async def fail(*args, **kwargs):
        raise OSError('isolated injected history failure')
    monkeypatch.setattr(erasure, '_erase_history', fail)
    with pytest.raises(OSError):
        client.post(f'/api/conversations/{conversation}/forget',
                    params={'session_id':owner,'worldline':'steins_gate'},json={'forget_long_term':True})
    assert client.get(f'/api/memory/facts/{fact}/details',params=_facts_scope(owner)).status_code == 404
    assert _db_rows("SELECT state FROM conversation_erasure_requests WHERE session_id=?", (owner,)) == [{'state':'pending'}]
    # Even a differently-versioned late provider result cannot create new content.
    from app.services.memory_v11.contracts import MemoryValidationError
    with pytest.raises(MemoryValidationError, match='source history has been erased'):
        _seed_fact_via_reconciler(session_id=owner, display_text='late result', source_message_id=source, conversation_id=conversation, pipeline_version='older-pending-pipeline')
    monkeypatch.setattr(erasure, '_erase_history', original)
    monkeypatch.setattr(erasure, '_commit_erasure_memory', fail)
    asyncio.run(erasure.resume_erasures(worldline='steins_gate'))
    asyncio.run(erasure.resume_erasures(worldline='steins_gate'))
    assert _db_rows("SELECT state FROM conversation_erasure_requests WHERE session_id=?", (owner,)) == [{'state':'completed'}]
    assert client.get(f'/api/conversations/{conversation}/messages',params={'session_id':owner,'worldline':'steins_gate'}).json() == []
    # Explicit new evidence remains allowed after forgetting.
    from app.models import save_message
    new_source = asyncio.run(save_message(owner,'user','new explicit statement',conversation_id=conversation))
    new_fact, _ = _seed_fact_via_reconciler(session_id=owner, display_text='new explicit statement',source_message_id=new_source,conversation_id=conversation)
    assert client.get(f'/api/memory/facts/{new_fact}/details',params=_facts_scope(owner)).status_code == 200


def test_rest_forget_invalidates_matching_live_conversation(isolated_client):
    from app.routers.chat_ws import SessionState, sessions
    client, owner = isolated_client, 'live-forget-owner'
    conversation, _, _, _ = _source(client, owner, 'old live context')
    session = SessionState(owner)
    session.conversation_id = conversation
    session.history = [{'role':'user','content':'old live context'}]
    session.memory_summary = 'old cached summary'
    sessions[owner] = session
    try:
        response = client.post(f'/api/conversations/{conversation}/forget',
            params={'session_id':owner,'worldline':'steins_gate'},json={'forget_long_term':False})
        assert response.status_code == 200
        assert session.history == []
        assert session.memory_summary == ''
        assert session.history_epoch == 1
        assert session.revision == 1
        assert session.erasure_pending is False
    finally:
        sessions.pop(owner, None)


def test_later_explicit_forget_can_erase_memory_retained_by_history_only_clear(isolated_client):
    client, owner = isolated_client, 'later-forget-owner'
    conversation, _, fact, _ = _source(client, owner, 'retained until explicit forget')
    params = {'session_id':owner,'worldline':'steins_gate'}
    assert client.post(f'/api/conversations/{conversation}/forget',params=params,json={'forget_long_term':False}).status_code == 200
    assert client.get(f'/api/memory/facts/{fact}/details',params=_facts_scope(owner)).status_code == 200
    assert client.post(f'/api/conversations/{conversation}/forget',params=params,json={'forget_long_term':True}).status_code == 200
    assert client.get(f'/api/memory/facts/{fact}/details',params=_facts_scope(owner)).status_code == 404


def test_recovery_after_history_commit_does_not_clear_new_history_again(isolated_client):
    from app import models
    from app.services.conversation_erasure import resume_erasures
    client, owner = isolated_client, 'history-idempotence-owner'
    conversation, _, _, _ = _source(client, owner, 'old history')
    client.post(f'/api/conversations/{conversation}/forget',params={'session_id':owner,'worldline':'steins_gate'},json={'forget_long_term':True})
    _db_execute("UPDATE conversation_erasure_requests SET state='pending' WHERE session_id=?", (owner,))
    asyncio.run(models.save_message(owner,'user','new history',conversation_id=conversation))
    epoch = asyncio.run(models.get_conversation_content_epoch(owner,conversation_id=conversation))
    asyncio.run(models.save_memory_summary(owner,'new summary',conversation_id=conversation,expected_epoch=epoch))
    asyncio.run(resume_erasures(worldline='steins_gate'))
    assert asyncio.run(models.get_conversation_content_epoch(owner,conversation_id=conversation)) == epoch
    assert asyncio.run(models.get_latest_summary(owner,conversation_id=conversation)) == 'new summary'
    assert [row['content'] for row in asyncio.run(models.get_session_messages(owner,conversation_id=conversation))] == ['new history']


@pytest.mark.parametrize("pinned", [False, True])
def test_forget_erases_unpinned_experiences_and_scrubs_provenance(isolated_client, pinned):
    from app.models import save_message
    client, owner = isolated_client, 'experience-forget-owner'
    conversation, _, _, _ = _source(client, owner, 'initial fact')
    message = asyncio.run(save_message(owner,'user','private event',conversation_id=conversation))
    experience, observation = _create_episodic(session_id=owner, display_text='private event',source_message_ids=[message])
    _db_execute('UPDATE experiences SET conversation_id=? WHERE experience_id=?',(conversation,experience))
    _db_execute('UPDATE memory_observation_sources SET conversation_id=? WHERE observation_id=?',(conversation,observation))
    if pinned:
        response = client.patch(f"/api/memory/experiences/{experience}/presentation",
                                params=_facts_scope(owner), json={"is_pinned": True})
        assert response.status_code == 200, response.text
    response = client.post(f'/api/conversations/{conversation}/forget',params={'session_id':owner,'worldline':'steins_gate'},json={'forget_long_term':True})
    assert response.status_code == 200
    details = client.get(f'/api/memory/experiences/{experience}/details',params=_facts_scope(owner))
    assert details.status_code == (200 if pinned else 404)
    if pinned:
        assert details.json()["experience"]["display_text"] == "private event"
    else:
        assert _db_rows('SELECT display_text,semantic_json FROM experiences WHERE experience_id=?',(experience,)) == [{'display_text':'','semantic_json':'{}'}]
    assert _db_rows('SELECT source_state,excerpt FROM memory_observation_sources WHERE observation_id=?',(observation,)) == [{'source_state':'deleted','excerpt':None}]


def _forget(client, owner, conversation):
    return client.post(f"/api/conversations/{conversation}/forget",
                       params={"session_id": owner, "worldline": "steins_gate"},
                       json={"forget_long_term": True})


def _details(client, owner, fact):
    response = client.get(f"/api/memory/facts/{fact}/details", params=_facts_scope(owner))
    assert response.status_code == 200, response.text
    return response.json()


def _recall(owner, text):
    from app.services.memory_v11.recall import MemoryScope, RecallRequest, recall_stable_facts

    result = asyncio.run(recall_stable_facts(
        scope=MemoryScope(**_facts_scope(owner)), request=RecallRequest(needs=(text,)),
    ))
    return " ".join(result.records.values())


def test_forget_prunes_middle_version_without_reverting_live_current_version(isolated_client):
    client, owner = isolated_client, "keep-current-owner"
    _, _, fact, _ = _source(client, owner, "I run every week")
    b, b_id = _message(client, owner, "I run beside the lighthouse")
    _change(owner, fact, 1, b, b_id, "I run beside the lighthouse")
    c, c_id = _message(client, owner, "I run on weekends")
    _change(owner, fact, 2, c, c_id, "I run on weekends")

    assert _forget(client, owner, b).status_code == 200
    details = _details(client, owner, fact)
    assert details["fact"]["active_version"] == 3
    assert details["fact"]["display_text"] == "I run on weekends"
    assert [v["version_no"] for v in details["versions"]] == [1, 3]
    assert "lighthouse" not in str(details)
    assert all(p["conversation_id"] != b for p in details["provenance"])
    assert _db_rows(
        """SELECT o.status,o.display_text FROM memory_observations o
           JOIN memory_observation_sources s USING(observation_id) WHERE s.conversation_id=?""", (b,),
    ) == [{"status": "deleted", "display_text": None}]


def test_forget_moves_live_support_after_concurrent_attach(isolated_client, monkeypatch):
    from app.services.memory_v11 import indexing

    client, owner = isolated_client, "live-support-owner"
    _, _, fact, original = _source(client, owner, "I run every week")
    b, b_id = _message(client, owner, "I run beside the lighthouse every week")
    _change(owner, fact, 1, b, b_id, "I run beside the lighthouse every week")
    c, c_id = _message(client, owner, "I still run regularly")
    prepare = indexing.prepare_passage_embedding
    calls = []

    async def prepare_with_attach(text, **kwargs):
        calls.append(text)
        if len(calls) == 1:
            await asyncio.to_thread(_change, owner, fact, 2, c, c_id, "I still run regularly", op="ATTACH")
        return await prepare(text, **kwargs)

    monkeypatch.setattr(indexing, "prepare_passage_embedding", prepare_with_attach)
    assert _forget(client, owner, b).status_code == 200
    assert calls == ["I run every week", "I run every week"]
    details = _details(client, owner, fact)
    assert "lighthouse" not in str(details)
    assert "lighthouse" not in _recall(owner, "run every week")
    assert _db_rows(
        """SELECT e.evidence_role,e.observation_id FROM stable_fact_evidence e
           WHERE e.fact_id=? AND e.version_no=3 ORDER BY e.evidence_role""", (fact,),
    )[0] == {"observation_id": original, "evidence_role": "primary"}
    assert _db_rows(
        """SELECT e.version_no,e.evidence_role FROM stable_fact_evidence e
           JOIN memory_observation_sources s USING(observation_id)
           WHERE e.fact_id=? AND s.conversation_id=?""", (fact, c),
    ) == [{"version_no": 3, "evidence_role": "supporting"}]


def test_dead_version_number_cannot_be_reused_by_a_later_refinement(isolated_client):
    from app.services.memory_v11.contracts import MemoryValidationError

    client, owner = isolated_client, "aba-owner"
    _, _, fact, _ = _source(client, owner, "I run every week")
    b, b_id = _message(client, owner, "I run at the lighthouse")
    _change(owner, fact, 1, b, b_id, "I run at the lighthouse")
    stale = _details(client, owner, fact)["fact"]["active_version"]
    d, d_id = _message(client, owner, "I run at dawn")
    assert _forget(client, owner, b).status_code == 200
    restored = _details(client, owner, fact)["fact"]["active_version"]
    c, c_id = _message(client, owner, "I run in the morning")
    _change(owner, fact, restored, c, c_id, "I run in the morning")

    with pytest.raises(MemoryValidationError) as stale_write:
        _change(owner, fact, stale, d, d_id, "I run at dawn")
    assert stale_write.value.code == "expected_version_mismatch"
    assert _details(client, owner, fact)["fact"]["display_text"] == "I run in the morning"


def test_unavailable_embedder_leaves_restored_fact_searchable_by_text(isolated_client, monkeypatch):
    from app.services.memory import memory_service

    client, owner = isolated_client, "lexical-restore-owner"
    _, _, fact, _ = _source(client, owner, "I run every week")
    b, b_id = _message(client, owner, "I run beside the lighthouse every week")
    _change(owner, fact, 1, b, b_id, "I run beside the lighthouse every week")

    async def unavailable(_text):
        raise RuntimeError("local embedding model unavailable")

    async def unexpected_passage(_text):
        pytest.fail("Erasure must request local-only passage encoding")

    monkeypatch.setattr(memory_service.embedder, "encode_cached_passage", unavailable, raising=False)
    monkeypatch.setattr(memory_service.embedder, "encode_passage", unexpected_passage)
    monkeypatch.setattr(memory_service.embedder, "encode_query", unavailable)
    assert _forget(client, owner, b).status_code == 200
    assert _details(client, owner, fact)["fact"]["display_text"] == "I run every week"
    assert not _db_rows("SELECT 1 FROM stable_fact_version_embeddings WHERE fact_id=?", (fact,))
    assert "I run every week" in _recall(owner, "run every week")
    assert "lighthouse" not in _recall(owner, "lighthouse")


def _diary_job_and_legacy_fact(owner, conversation, message):
    from app.services.memory_v11.diaries import commit_diary, register_diary_window

    scope = _facts_scope(owner)
    diary_id = asyncio.run(register_diary_window(
        **scope, conversation_id=conversation, first_message_id=message, last_message_id=message,
        due_at=datetime(2099, 1, 1, tzinfo=timezone.utc),
    ))
    assert diary_id
    assert asyncio.run(commit_diary(**scope, diary_id=diary_id, body_ja="日記", body_zh="日记",
                                    verified_phrases=[]))
    _db_execute(
        """INSERT INTO memory_ingest_jobs(job_id,session_id,identity_mode,conversation_id,source_message_id,
                 pipeline_version,provider_id,model_id,state,created_at,updated_at,next_attempt_at)
           VALUES(?,?,'self',?,?,'diary-v1','local','fixture','pending','now','now','2099-01-01')""",
        (diary_id, owner, conversation, message),
    )
    _db_execute(
        """INSERT INTO core_facts(session_id,identity_mode,fact_key,fact_value,confidence,importance,
                                  source_message_ids,created_at)
           VALUES(?,'self','legacy','legacy text',1,1,?,'now')""", (owner, json.dumps([message])),
    )


@pytest.mark.parametrize("fail_at", ["prepare", "memory_commit", "history"])
def test_erasure_transaction_boundaries(isolated_client, monkeypatch, fail_at):
    from app.services import conversation_erasure as erasure
    from app.services.memory import memory_service
    from app.services.memory_v11 import indexing

    client, owner = isolated_client, "erasure-boundaries-owner"
    _, _, fact, _ = _source(client, owner, "I run every week")
    b, message = _message(client, owner, "I run at the lighthouse")
    _change(owner, fact, 1, b, message, "I run at the lighthouse")
    _diary_job_and_legacy_fact(owner, b, message)
    tables = ("conversation_erasure_requests", "stable_facts", "stable_fact_versions",
              "stable_fact_evidence", "stable_fact_version_embeddings", "memory_observations",
              "memory_observation_sources", "memory_diaries", "memory_ingest_jobs", "core_facts")
    before = {table: _db_rows(f"SELECT * FROM {table}") for table in tables}

    async def fail(*args, **kwargs):
        raise RuntimeError("injected erasure interruption")

    remove_legacy = memory_service.forget_exclusive_sources

    async def fail_after_legacy(*args, **kwargs):
        await remove_legacy(*args, **kwargs)
        await fail()

    with monkeypatch.context() as fault:
        if fail_at == "prepare":
            fault.setattr(indexing, "prepare_passage_embedding", fail)
        elif fail_at == "memory_commit":
            fault.setattr(memory_service, "forget_exclusive_sources", fail_after_legacy)
        else:
            fault.setattr(erasure, "_erase_history", fail)
        with pytest.raises(RuntimeError, match="injected erasure interruption"):
            _forget(client, owner, b)
    messages = f"/api/conversations/{b}/messages"
    params = {"session_id": owner, "worldline": "steins_gate"}
    assert [msg["content"] for msg in client.get(messages, params=params).json()] == ["I run at the lighthouse"]
    if fail_at != "history":
        assert {table: _db_rows(f"SELECT * FROM {table}") for table in tables} == before
        return

    assert _db_rows("SELECT state FROM conversation_erasure_requests") == [{"state": "pending"}]
    assert _details(client, owner, fact)["fact"]["display_text"] == "I run every week"
    assert _db_rows("SELECT body_zh FROM memory_diaries") == [{"body_zh": None}]
    assert _db_rows("SELECT state FROM memory_ingest_jobs") == [{"state": "cancelled"}]
    assert not _db_rows("SELECT * FROM core_facts")
    monkeypatch.setattr(indexing, "prepare_passage_embedding", fail)
    monkeypatch.setattr(erasure, "_plan_recompute", fail)
    state = {table: _db_rows(f"SELECT * FROM {table}") for table in tables[1:]}
    for _ in range(2):
        asyncio.run(erasure.resume_erasures(worldline="steins_gate"))
    assert client.get(messages, params=params).json() == []
    assert _db_rows("SELECT state FROM conversation_erasure_requests") == [{"state": "completed"}]
    assert {table: _db_rows(f"SELECT * FROM {table}") for table in tables[1:]} == state


def test_forget_isolates_worldline_identity_owner_and_conversation(isolated_client):
    client, owner = isolated_client, "erasure-scope-owner"
    b, message, fact, _ = _source(client, owner, "Scoped text")
    preserved = []
    for other_scope in (
        {"session_id": owner, "worldline": "beta", "identity_mode": "self"},
        {"session_id": owner, "worldline": "steins_gate", "identity_mode": "okabe"},
        {"session_id": "other-owner", "worldline": "steins_gate", "identity_mode": "self"},
    ):
        result = _change(other_scope["session_id"], None, None, b, message, "Retained text",
                         op="CREATE", worldline=other_scope["worldline"], identity_mode=other_scope["identity_mode"])
        preserved.append((result["active_facts"][0]["fact_id"], other_scope))
    _, _, other_fact, _ = _source(client, owner, "Other conversation")
    preserved.append((other_fact, _facts_scope(owner)))

    assert _forget(client, owner, b).status_code == 200
    assert client.get(f"/api/memory/facts/{fact}/details", params=_facts_scope(owner)).status_code == 404
    for other_fact, params in preserved:
        assert client.get(f"/api/memory/facts/{other_fact}/details", params=params).status_code == 200


def test_unfinished_v16_intent_can_resume_after_additive_upgrade(isolated_client, monkeypatch):
    from app.services.conversation_erasure import resume_erasures
    from app.services.memory_v11 import indexing

    client, owner = isolated_client, "v16-pending-owner"
    _, _, fact, _ = _source(client, owner, "I run every week")
    b, message = _message(client, owner, "I run at the lighthouse")
    _change(owner, fact, 1, b, message, "I run at the lighthouse")
    # The v16 first transaction could commit this intent before touching facts.
    _db_execute(
        """INSERT INTO conversation_erasure_requests(
               request_id,session_id,identity_mode,conversation_id,action,
               forget_long_term,source_message_ids,state,created_at)
           VALUES('345a8d3d-2161-46ae-babb-ce22a052892d',?,'self',?,'forget',1,?,'pending','now')""",
        (owner, b, json.dumps([message])),
    )

    async def unexpected_embedding(*_args, **_kwargs):
        pytest.fail("Startup must not call the embedder for a pending intent")

    monkeypatch.setattr(indexing, "prepare_passage_embedding", unexpected_embedding)
    asyncio.run(resume_erasures(worldline="steins_gate"))
    assert _details(client, owner, fact)["fact"]["display_text"] == "I run every week"
    assert _db_rows("SELECT state FROM conversation_erasure_requests") == [{"state": "completed"}]
