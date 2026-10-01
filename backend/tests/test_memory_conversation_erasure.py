"""Conversation UI actions must govern the same v11 records as Archive."""
import asyncio

import pytest

from test_memory_v11_api import (
    isolated_client, _create_conversation, _seed_fact_via_reconciler,
    _facts_scope, _db_rows, _db_execute,
    _create_episodic,
)


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


def test_forget_preserves_fact_supported_by_another_conversation(isolated_client):
    client = isolated_client
    owner = 'shared-v11-owner'
    conversation, _, fact, observation = _source(client, owner, 'first support')
    _, _, _, other_observation = _source(client, owner, 'independent support')
    _db_execute("INSERT INTO stable_fact_evidence(fact_id,version_no,observation_id,evidence_role) VALUES(?,1,?,'supporting')", (fact, other_observation))
    response = client.post(f'/api/conversations/{conversation}/forget',
        params={'session_id': owner, 'worldline': 'steins_gate'}, json={'forget_long_term': True})
    assert response.status_code == 200, response.text
    assert client.get(f'/api/memory/facts/{fact}/details', params=_facts_scope(owner)).status_code == 200
    assert _db_rows('SELECT source_state,excerpt FROM memory_observation_sources WHERE observation_id=?', (observation,)) == [{'source_state': 'deleted', 'excerpt': None}]


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
    assert client.get(f'/api/memory/facts/{fact}/details',params=_facts_scope(owner)).status_code == 200
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
    asyncio.run(erasure.resume_erasures(worldline='steins_gate'))
    assert _db_rows("SELECT state FROM conversation_erasure_requests WHERE session_id=?", (owner,)) == [{'state':'completed'}]
    assert client.get(f'/api/conversations/{conversation}/messages',params={'session_id':owner,'worldline':'steins_gate'}).json() == []
    # Explicit new evidence remains allowed after forgetting.
    from app.models import save_message
    new_source = asyncio.run(save_message(owner,'user','new explicit statement',conversation_id=conversation))
    new_fact, _ = _seed_fact_via_reconciler(session_id=owner, display_text='new explicit statement',source_message_id=new_source,conversation_id=conversation)
    assert client.get(f'/api/memory/facts/{new_fact}/details',params=_facts_scope(owner)).status_code == 200


@pytest.mark.parametrize('runtime_mode', ['legacy','shadow','v11'])
def test_status_reports_actual_chat_runtime_mode(isolated_client, monkeypatch, runtime_mode):
    monkeypatch.setenv('AMADEUS_MEMORY_MODE', runtime_mode)
    response = isolated_client.get('/api/memory/status',params=_facts_scope('mode-owner'))
    assert response.status_code == 200
    assert response.json()['runtime_mode'] == runtime_mode


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


def test_forget_erases_experience_and_its_source_content(isolated_client):
    from app.models import save_message
    client, owner = isolated_client, 'experience-forget-owner'
    conversation, _, _, _ = _source(client, owner, 'initial fact')
    message = asyncio.run(save_message(owner,'user','private event',conversation_id=conversation))
    experience, observation = _create_episodic(session_id=owner, display_text='private event',source_message_ids=[message])
    _db_execute('UPDATE experiences SET conversation_id=? WHERE experience_id=?',(conversation,experience))
    _db_execute('UPDATE memory_observation_sources SET conversation_id=? WHERE observation_id=?',(conversation,observation))
    response = client.post(f'/api/conversations/{conversation}/forget',params={'session_id':owner,'worldline':'steins_gate'},json={'forget_long_term':True})
    assert response.status_code == 200
    assert client.get(f'/api/memory/experiences/{experience}/details',params=_facts_scope(owner)).status_code == 404
    assert _db_rows('SELECT display_text,semantic_json FROM experiences WHERE experience_id=?',(experience,)) == [{'display_text':'','semantic_json':'{}'}]
    assert _db_rows('SELECT source_state,excerpt FROM memory_observation_sources WHERE observation_id=?',(observation,)) == [{'source_state':'deleted','excerpt':None}]
