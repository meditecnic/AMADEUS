"""Recoverable history erasure; memory is settled before history disappears.

History and memory are separate WAL databases, so pretending one cross-file
transaction is crash-atomic would be unsafe. A durable non-content request
blocks late memory writes and lets startup/retry finish an interrupted erasure.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from app.db import get_db


async def sources_erased(db: Any, *, session_id: str, identity_mode: str,
                         source_message_ids: list[int], conversation_id: str | None = None) -> bool:
    if not source_message_ids and not conversation_id:
        return False
    rows = await (await db.execute(
        """SELECT action, conversation_id, source_message_ids FROM conversation_erasure_requests
           WHERE session_id=? AND identity_mode=?""", (session_id, identity_mode),
    )).fetchall()
    sources = set(source_message_ids)
    return any(
        (row['action'] == 'delete' and conversation_id == row['conversation_id'])
        or sources.intersection(json.loads(row['source_message_ids']))
        for row in rows
    )


async def request_erasure(*, session_id: str, identity_mode: str, worldline: str,
                          conversation_id: str, action: str, forget_long_term: bool) -> dict[str, int]:
    # Finish any previous attempt before snapshotting the current history.
    await resume_erasures(worldline=worldline, session_id=session_id, conversation_id=conversation_id)
    history = await get_db(worldline, 'history')
    try:
        rows = await (await history.execute(
            'SELECT id FROM messages WHERE session_id=? AND conversation_id=?',
            (session_id, conversation_id),
        )).fetchall()
        ids = [int(row['id']) for row in rows]
    finally:
        await history.close()
    request = dict(request_id=str(uuid.uuid4()), session_id=session_id,
                   identity_mode=identity_mode, conversation_id=conversation_id,
                   action=action, forget_long_term=int(forget_long_term),
                   source_message_ids=json.dumps(ids), state='pending',
                   created_at=datetime.now(timezone.utc).isoformat())
    db = await get_db(worldline, 'memory')
    try:
        await db.execute('BEGIN IMMEDIATE')
        # Provenance and prior intents still identify this conversation's
        # sources after an earlier history-only clear. A later explicit
        # long-term forget must be able to withdraw that retained memory.
        retained_sources = await (await db.execute(
            '''SELECT s.source_message_id FROM memory_observation_sources s
               JOIN memory_observations o ON o.observation_id=s.observation_id
               WHERE o.session_id=? AND o.identity_mode=? AND s.conversation_id=?''',
            (session_id, identity_mode, conversation_id),
        )).fetchall()
        earlier = await (await db.execute(
            '''SELECT source_message_ids FROM conversation_erasure_requests
               WHERE session_id=? AND identity_mode=? AND conversation_id=?''',
            (session_id, identity_mode, conversation_id),
        )).fetchall()
        ids = sorted(set(ids) | {int(row['source_message_id']) for row in retained_sources}
                     | {int(mid) for row in earlier for mid in json.loads(row['source_message_ids'])})
        request['source_message_ids'] = json.dumps(ids)
        await db.execute(
            '''INSERT INTO conversation_erasure_requests VALUES(
               :request_id,:session_id,:identity_mode,:conversation_id,:action,
               :forget_long_term,:source_message_ids,:state,:created_at)''', request,
        )
        await db.execute(
            """UPDATE memory_ingest_jobs SET state='cancelled', retryable=0,
               lease_owner=NULL, lease_expires_at=NULL, next_attempt_at=NULL,
               last_error_code='source_missing', updated_at=?
               WHERE session_id=? AND identity_mode=? AND conversation_id=?
                 AND state IN ('pending','processing','failed')
                 AND (?='delete' OR source_message_id IN (SELECT value FROM json_each(?)))""",
            (request['created_at'], session_id, identity_mode, conversation_id, action, request['source_message_ids']),
        )
        await db.commit()
    finally:
        await db.close()
    return await _finish_erasure(request, worldline)


async def _erase_v11(request: dict, worldline: str) -> dict[str, int]:
    from app.services.memory_v11.facts import delete_fact
    from app.services.memory_v11.experiences import delete_experience

    owner, mode, conversation = (request[key] for key in ('session_id', 'identity_mode', 'conversation_id'))
    ids = set(json.loads(request['source_message_ids']))
    db = await get_db(worldline, 'memory')
    try:
        await db.execute('BEGIN IMMEDIATE')
        authorized = await (await db.execute(
            '''SELECT conversation_id,source_message_ids FROM conversation_erasure_requests
               WHERE session_id=? AND identity_mode=? AND forget_long_term=1''', (owner, mode),
        )).fetchall()
        authorized_sources = {(row['conversation_id'], int(mid))
                              for row in authorized for mid in json.loads(row['source_message_ids'])}
        rows = await (await db.execute(
            '''SELECT s.observation_id,s.source_message_id,s.conversation_id
               FROM memory_observation_sources s JOIN memory_observations o
                 ON o.observation_id=s.observation_id
               WHERE o.session_id=? AND o.identity_mode=?''', (owner, mode),
        )).fetchall()
        by_observation: dict[str, list[bool]] = {}
        affected: set[str] = set()
        for row in rows:
            targeted = row['conversation_id'] == conversation and (
                request['action'] == 'delete' or int(row['source_message_id']) in ids)
            by_observation.setdefault(row['observation_id'], []).append(
                (row['conversation_id'], int(row['source_message_id'])) in authorized_sources)
            if targeted:
                affected.add(row['observation_id'])
        exclusive = {oid for oid, targets in by_observation.items() if targets and all(targets)}
        facts = await (await db.execute(
            "SELECT fact_id,active_version FROM stable_facts WHERE session_id=? AND identity_mode=? AND state!='deleted'",
            (owner, mode),
        )).fetchall()
        erasable_facts = []
        if request['forget_long_term']:
            for fact in facts:
                evidence = await (await db.execute('SELECT observation_id FROM stable_fact_evidence WHERE fact_id=?', (fact['fact_id'],))).fetchall()
                if evidence and all(row['observation_id'] in exclusive for row in evidence):
                    erasable_facts.append(dict(fact))
        experiences = await (await db.execute(
            "SELECT experience_id,observation_id FROM experiences WHERE session_id=? AND identity_mode=? AND status!='deleted'",
            (owner, mode),
        )).fetchall()
        # Eligibility and erasure share one write lock: ATTACH can add evidence
        # without changing a fact version, so an optimistic version alone is insufficient.
        for fact in erasable_facts:
            await delete_fact(session_id=owner, worldline=worldline, identity_mode=mode,
                              fact_id=fact['fact_id'], expected_version=fact['active_version'], _transaction=db)
        erased_experiences = 0
        for experience in experiences:
            if request['forget_long_term'] and experience['observation_id'] in exclusive:
                await delete_experience(session_id=owner, worldline=worldline, identity_mode=mode,
                                        experience_id=experience['experience_id'], _transaction=db)
                erased_experiences += 1
        for observation_id in affected:
            await db.execute(
                """UPDATE memory_observation_sources SET source_state='deleted',excerpt=NULL
                   WHERE observation_id=? AND conversation_id=?
                     AND (?='delete' OR source_message_id IN (SELECT value FROM json_each(?)))""",
                (observation_id, conversation, request['action'], request['source_message_ids']),
            )
            if request['forget_long_term'] and observation_id in exclusive:
                still_referenced = await (await db.execute(
                    '''SELECT 1 FROM stable_fact_evidence WHERE observation_id=?
                       UNION ALL SELECT 1 FROM experiences WHERE observation_id=? AND status!='deleted' LIMIT 1''',
                    (observation_id, observation_id),
                )).fetchone()
                if still_referenced is None:
                    await db.execute(
                        """UPDATE memory_observations SET status='deleted',display_text=NULL,
                           semantic_json=NULL,topic_label_proposal=NULL,updated_at=? WHERE observation_id=?""",
                        (request['created_at'], observation_id),
                    )
        await db.commit()
    except BaseException:
        await db.rollback()
        raise
    finally:
        await db.close()
    return {'episodic_deleted': erased_experiences, 'core_facts_deleted': len(erasable_facts)}


async def _finish_erasure(request: dict, worldline: str) -> dict[str, int]:
    from app.services.memory import memory_service
    ids = set(json.loads(request['source_message_ids']))
    counts = await _erase_v11(request, worldline)
    if request['forget_long_term']:
        db = await get_db(worldline, 'memory')
        try:
            authorized = await (await db.execute(
                '''SELECT source_message_ids FROM conversation_erasure_requests
                   WHERE session_id=? AND identity_mode=? AND forget_long_term=1''',
                (request['session_id'], request['identity_mode']),
            )).fetchall()
            ids = {int(mid) for row in authorized for mid in json.loads(row['source_message_ids'])}
        finally:
            await db.close()
        legacy = await memory_service.forget_exclusive_sources(
            request['session_id'], worldline, ids, identity_mode=request['identity_mode'],
        )
        counts = {key: counts[key] + legacy[key] for key in counts}
    history_counts = await _erase_history(request, worldline)
    db = await get_db(worldline, 'memory')
    try:
        await db.execute("UPDATE conversation_erasure_requests SET state='completed' WHERE request_id=?", (request['request_id'],))
        await db.commit()
    finally:
        await db.close()
    return {**history_counts, **counts}


async def _erase_history(request: dict, worldline: str) -> dict[str, int]:
    db = await get_db(worldline, 'history')
    try:
        await db.execute('BEGIN IMMEDIATE')
        completed = await (await db.execute('SELECT 1 FROM conversation_erasure_commits WHERE request_id=?',
                                             (request['request_id'],))).fetchone()
        if completed:
            await db.commit()
            return {'messages_deleted': 0, 'summaries_deleted': 0}
        await db.execute('''INSERT INTO conversation_content_epochs(session_id,conversation_id,epoch) VALUES(?,?,1)
                            ON CONFLICT(session_id,conversation_id) DO UPDATE SET epoch=epoch+1''',
                         (request['session_id'], request['conversation_id']))
        # A new turn created after a forget request is not part of that request.
        messages = await db.execute(
            '''DELETE FROM messages WHERE session_id=? AND conversation_id=?
               AND (?='delete' OR id IN (SELECT value FROM json_each(?)))''',
            (request['session_id'], request['conversation_id'], request['action'], request['source_message_ids']),
        )
        summaries = await db.execute('DELETE FROM memory_summaries WHERE session_id=? AND conversation_id=?',
                                     (request['session_id'], request['conversation_id']))
        if request['action'] == 'delete':
            await db.execute('DELETE FROM conversations WHERE id=? AND session_id=?',
                             (request['conversation_id'], request['session_id']))
        await db.execute('INSERT INTO conversation_erasure_commits(request_id) VALUES(?)', (request['request_id'],))
        await db.commit()
        return {'messages_deleted': max(0, messages.rowcount), 'summaries_deleted': max(0, summaries.rowcount)}
    finally:
        await db.close()


async def resume_erasures(*, worldline: str, session_id: str | None = None,
                         conversation_id: str | None = None) -> None:
    db = await get_db(worldline, 'memory')
    try:
        rows = await (await db.execute(
            """SELECT * FROM conversation_erasure_requests WHERE state='pending'
               AND (? IS NULL OR session_id=?) AND (? IS NULL OR conversation_id=?) ORDER BY created_at""",
            (session_id, session_id, conversation_id, conversation_id),
        )).fetchall()
    finally:
        await db.close()
    for row in rows:
        await _finish_erasure(dict(row), worldline)
