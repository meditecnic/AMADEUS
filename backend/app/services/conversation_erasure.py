"""Recoverable history erasure; memory is settled before history disappears.

History and memory are separate WAL databases, so pretending one cross-file
transaction is crash-atomic would be unsafe. A durable non-content request
blocks late memory writes and lets startup/retry finish an interrupted erasure.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from itertools import groupby
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


# Earlier requests committed their intent before applying memory changes.
_ATOMIC_REQUEST_PREFIX = "v17:"
_OPTIMISTIC_ATTEMPTS = 3


class TruncateError(Exception):
    pass


@dataclass
class FactRecompute:
    fact_id: str
    active_version: int
    dead_versions: list[int]
    dead_evidence: set[str]
    live_support: set[str]
    restored_from: int | None
    restored_text: str | None


async def request_erasure(*, session_id: str, identity_mode: str, worldline: str,
                          conversation_id: str, action: str, forget_long_term: bool,
                          cutoff: tuple[int, int] | None = None) -> dict[str, int]:
    await resume_erasures(worldline=worldline, session_id=session_id, conversation_id=conversation_id)
    history = await get_db(worldline, 'history')
    try:
        point = cutoff[0] if cutoff else 0
        scope = (session_id, conversation_id)
        await history.execute('BEGIN')
        rows = await (await history.execute(
            'SELECT id,role FROM messages WHERE session_id=? AND conversation_id=? AND id>=? ORDER BY id',
            (*scope, point),
        )).fetchall()
        history_ids = {int(row['id']) for row in rows}
        if cutoff:
            last_retained = await (await history.execute(
                'SELECT MAX(id) FROM messages WHERE session_id=? AND conversation_id=? AND id<?',
                (*scope, point),
            )).fetchone()
            if (rows[-1]['id'] if rows else last_retained[0]) != cutoff[1]:
                raise TruncateError('conversation_changed')
            if not rows or rows[0]['id'] != point or rows[0]['role'] != 'user':
                raise TruncateError('invalid_truncate_point')
    finally:
        await history.close()

    request = dict(request_id=_ATOMIC_REQUEST_PREFIX + str(uuid.uuid4()), session_id=session_id,
                   identity_mode=identity_mode, conversation_id=conversation_id,
                   action=action, forget_long_term=int(forget_long_term),
                   state='pending', created_at=datetime.now(timezone.utc).isoformat(),
                   cutoff_message_id=point if cutoff else None)
    if cutoff:
        request['last_retained_message_id'] = last_retained[0]
        request['source_message_ids'] = json.dumps(sorted(history_ids))
        counts = await _commit_erasure_memory(request, worldline)
    else:
        counts = await _commit_erasure_memory(request, worldline, history_ids=history_ids)
    return {**await _finish_erasure(request, worldline), **counts}


async def truncate_cursor(db, *, session_id, identity_mode, conversation_id) -> int:
    row = await (await db.execute(
        """SELECT COALESCE(MAX(rowid),0) FROM conversation_erasure_requests
           WHERE session_id=? AND identity_mode=? AND conversation_id=? AND cutoff_message_id IS NOT NULL""",
        (session_id, identity_mode, conversation_id),
    )).fetchone()
    return int(row[0])


async def validate_ingest_sources(db, *, session_id, identity_mode, conversation_id,
                                  source_message_ids, expected_truncate=None) -> None:
    from app.services.memory_v11.contracts import MemoryValidationError

    scope = dict(session_id=session_id, identity_mode=identity_mode, conversation_id=conversation_id)
    if await sources_erased(db, **scope, source_message_ids=source_message_ids):
        raise MemoryValidationError('source_erased', 'source history has been erased')
    if expected_truncate is not None and await truncate_cursor(db, **scope) != expected_truncate:
        raise MemoryValidationError('conversation_truncated', 'conversation was truncated during extraction')


async def _whole_conversation_sources(db, request, history_ids):
    # History-only clearing leaves sources that a later explicit forget can withdraw.
    scope = (request['session_id'], request['identity_mode'], request['conversation_id'])
    retained = await (await db.execute(
        """SELECT s.source_message_id FROM memory_observation_sources s
           JOIN memory_observations o ON o.observation_id=s.observation_id
           WHERE o.session_id=? AND o.identity_mode=? AND s.conversation_id=?""", scope,
    )).fetchall()
    earlier = await (await db.execute(
        """SELECT source_message_ids FROM conversation_erasure_requests
           WHERE session_id=? AND identity_mode=? AND conversation_id=?""", scope,
    )).fetchall()
    return history_ids | {int(row[0]) for row in retained} | {
        int(mid) for row in earlier for mid in json.loads(row[0])
    }


async def _authorized_sources(db, request, ids):
    owner, mode, conversation = (request[key] for key in ('session_id', 'identity_mode', 'conversation_id'))
    rows = await (await db.execute(
        """SELECT conversation_id,source_message_ids FROM conversation_erasure_requests
           WHERE session_id=? AND identity_mode=? AND forget_long_term=1""", (owner, mode),
    )).fetchall()
    return {(conversation, mid) for mid in ids} | {
        (row['conversation_id'], int(mid)) for row in rows for mid in json.loads(row['source_message_ids'])
    }


async def _plan_recompute(db, request, ids, authorized):
    owner, mode, conversation = (request[key] for key in ('session_id', 'identity_mode', 'conversation_id'))
    rows = await (await db.execute(
        """SELECT s.observation_id,s.source_message_id,s.conversation_id
           FROM memory_observation_sources s JOIN memory_observations o
             ON o.observation_id=s.observation_id
           WHERE o.session_id=? AND o.identity_mode=?""", (owner, mode),
    )).fetchall()
    live, affected = set(), set()
    for row in rows:
        source = (row['conversation_id'], int(row['source_message_id']))
        if source not in authorized:
            live.add(row['observation_id'])
        if source[0] == conversation and source[1] in ids:
            affected.add(row['observation_id'])

    rows = await (await db.execute(
        """SELECT f.fact_id,f.active_version,v.version_no,v.display_text,e.observation_id,e.evidence_role
           FROM stable_facts f JOIN stable_fact_versions v ON v.fact_id=f.fact_id
           LEFT JOIN stable_fact_evidence e ON e.fact_id=v.fact_id AND e.version_no=v.version_no
           WHERE f.session_id=? AND f.identity_mode=? AND f.state='active' AND f.is_pinned=0
             AND EXISTS (SELECT 1 FROM stable_fact_evidence touched WHERE touched.fact_id=f.fact_id
                         AND touched.observation_id IN (SELECT value FROM json_each(?)))
             AND NOT EXISTS (SELECT 1 FROM stable_fact_versions edited
                             WHERE edited.fact_id=f.fact_id AND edited.change_kind='user_edit')
           ORDER BY f.fact_id,v.version_no""", (owner, mode, json.dumps(sorted(affected))),
    )).fetchall()
    facts = []
    dead_observations = affected - live
    for (fact_id, active), fact_rows in groupby(rows, key=lambda row: (row['fact_id'], row['active_version'])):
        alive_versions = {}
        dead_versions = []
        dead_evidence = set()
        live_support = set()
        for version, evidence in groupby(fact_rows, key=lambda row: row['version_no']):
            links = list(evidence)
            dead_links = {
                row['observation_id'] for row in links
                if row['observation_id'] is not None and row['observation_id'] not in live
            }
            dead_observations.update(dead_links)
            if any(row['evidence_role'] == 'primary' and row['observation_id'] in live for row in links):
                alive_versions[version] = links[0]['display_text']
                dead_evidence.update(dead_links)
            else:
                dead_versions.append(version)
                live_support.update(
                    row['observation_id'] for row in links
                    if row['evidence_role'] == 'supporting' and row['observation_id'] in live
                )
        if dead_versions or dead_evidence:
            restored_from = max(alive_versions) if active in dead_versions and alive_versions else None
            facts.append(FactRecompute(
                fact_id, active, dead_versions, dead_evidence, live_support, restored_from,
                alive_versions.get(restored_from),
            ))

    experiences = await (await db.execute(
        """SELECT experience_id,observation_id FROM experiences
           WHERE session_id=? AND identity_mode=? AND status!='deleted' AND is_pinned=0""", (owner, mode),
    )).fetchall()
    erased_experiences = [row['experience_id'] for row in experiences if row['observation_id'] in affected and row['observation_id'] not in live]
    return facts, erased_experiences, dead_observations


async def _restore_fact(db, fact, now, embedding):
    from app.services.memory_v11.indexing import insert_version_embedding

    version = fact.active_version + 1
    await db.execute(
        'UPDATE stable_fact_versions SET invalid_at=? WHERE fact_id=? AND version_no=?',
        (now, fact.fact_id, fact.active_version),
    )
    await db.execute(
        """INSERT INTO stable_fact_versions(
               fact_id,version_no,display_text,semantic_json,semantic_fingerprint,confidence,
               change_kind,previous_version,valid_from,restored_from_version)
           SELECT fact_id,?,display_text,semantic_json,semantic_fingerprint,confidence,
                  change_kind,?,?,version_no
           FROM stable_fact_versions WHERE fact_id=? AND version_no=?""",
        (version, fact.active_version, now, fact.fact_id, fact.restored_from),
    )
    await db.execute(
        """INSERT INTO stable_fact_evidence(fact_id,version_no,observation_id,evidence_role)
           SELECT fact_id,?,observation_id,evidence_role FROM stable_fact_evidence
           WHERE fact_id=? AND version_no=? AND evidence_role='primary'""",
        (version, fact.fact_id, fact.restored_from),
    )
    if embedding is not None:
        await insert_version_embedding(db, fact_id=fact.fact_id, version_no=version, prepared=embedding)
    await db.execute(
        'UPDATE stable_facts SET active_version=?,updated_at=? WHERE fact_id=?',
        (version, now, fact.fact_id),
    )
    return version


async def _apply_recompute(db, request, worldline, plan, embeddings):
    from app.services.memory_v11.experiences import delete_experience
    from app.services.memory_v11.facts import delete_fact
    from app.services.memory_v11.indexing import delete_version_embedding

    facts, experiences, dead_observations = plan
    scope = dict(session_id=request['session_id'], identity_mode=request['identity_mode'], worldline=worldline)
    now = request['created_at']
    deleted = 0
    candidates = set()
    for fact in facts:
        if fact.active_version in fact.dead_versions and fact.restored_from is None:
            # Retained support can return to the archive queue without deletion receipts.
            await db.executemany(
                'DELETE FROM stable_fact_evidence WHERE fact_id=? AND observation_id=?',
                [(fact.fact_id, observation) for observation in sorted(fact.live_support)],
            )
            candidates.update(fact.live_support)
            await delete_fact(**scope, fact_id=fact.fact_id, expected_version=fact.active_version, _transaction=db)
            deleted += 1
            continue

        version = fact.active_version
        if fact.restored_from is not None:
            version = await _restore_fact(db, fact, now, embeddings.get(fact.fact_id))
        await db.executemany(
            """INSERT OR IGNORE INTO stable_fact_evidence(fact_id,version_no,observation_id,evidence_role)
               VALUES(?,?,?,'supporting')""",
            [(fact.fact_id, version, observation) for observation in sorted(fact.live_support)],
        )
        dead_versions = json.dumps(fact.dead_versions)
        await db.execute(
            """DELETE FROM stable_fact_evidence WHERE fact_id=?
               AND (version_no IN (SELECT value FROM json_each(?))
                    OR observation_id IN (SELECT value FROM json_each(?)))""",
            (fact.fact_id, dead_versions, json.dumps(sorted(fact.dead_evidence))),
        )
        for obsolete in fact.dead_versions:
            await delete_version_embedding(db, fact_id=fact.fact_id, version_no=obsolete)
        await db.execute(
            'DELETE FROM stable_fact_versions WHERE fact_id=? AND version_no IN (SELECT value FROM json_each(?))',
            (fact.fact_id, dead_versions),
        )
    for experience_id in experiences:
        await delete_experience(**scope, experience_id=experience_id, _transaction=db)
    await db.execute(
        """UPDATE memory_observations AS o SET status='candidate', updated_at=?
           WHERE observation_id IN (SELECT value FROM json_each(?)) AND status='attached'
             AND NOT EXISTS (SELECT 1 FROM stable_fact_evidence e WHERE e.observation_id=o.observation_id)""",
        (now, json.dumps(sorted(candidates))),
    )
    await db.execute(
        """UPDATE memory_observations AS o SET status='deleted',display_text=NULL,semantic_json=NULL,
                  topic_label_proposal=NULL,updated_at=?
           WHERE observation_id IN (SELECT value FROM json_each(?))
             AND NOT EXISTS (SELECT 1 FROM stable_fact_evidence e WHERE e.observation_id=o.observation_id)
             AND NOT EXISTS (SELECT 1 FROM experiences x WHERE x.observation_id=o.observation_id AND x.status!='deleted')""",
        (now, json.dumps(sorted(dead_observations))),
    )
    return {'episodic_deleted': len(experiences), 'core_facts_deleted': deleted}


async def _record_erasure(db, request):
    from app.services.memory_v11.diaries import erase_conversation_diaries

    await db.execute(
        """INSERT INTO conversation_erasure_requests(
               request_id,session_id,identity_mode,conversation_id,action,forget_long_term,
               source_message_ids,state,created_at,cutoff_message_id)
           VALUES(:request_id,:session_id,:identity_mode,:conversation_id,:action,:forget_long_term,
                  :source_message_ids,:state,:created_at,:cutoff_message_id)""", request,
    )
    discarded_diaries = await erase_conversation_diaries(db, request)
    await db.execute(
        """UPDATE memory_ingest_jobs SET state='cancelled', retryable=0,
           lease_owner=NULL, lease_expires_at=NULL, next_attempt_at=NULL,
           last_error_code='source_missing', updated_at=?
           WHERE session_id=? AND identity_mode=? AND conversation_id=?
             AND state IN ('pending','processing','failed')
             AND (?='delete' OR source_message_id IN (SELECT value FROM json_each(?))
                  OR job_id IN (SELECT value FROM json_each(?))
                  OR EXISTS (SELECT 1 FROM memory_diaries d
                             WHERE d.diary_id=memory_ingest_jobs.job_id AND d.state='discarded'))""",
        (request['created_at'], request['session_id'], request['identity_mode'], request['conversation_id'],
         request['action'], request['source_message_ids'], json.dumps(discarded_diaries)),
    )


async def _commit_erasure_memory(request, worldline, *, history_ids: set[int] | None = None, legacy_pending=False):
    from app.services.memory import memory_service
    from app.services.memory_v11.contracts import MemoryValidationError
    from app.services.memory_v11.indexing import prepare_passage_embedding

    db = await get_db(worldline, 'memory')
    try:
        ids = None
        embedding_cache = {}
        for attempt in range(_OPTIMISTIC_ATTEMPTS + 1):
            write_locked = attempt == _OPTIMISTIC_ATTEMPTS
            if write_locked:
                # Sustained writes require a fresh plan held stable through commit.
                await db.execute('BEGIN IMMEDIATE')
            else:
                revision = (await (await db.execute('PRAGMA data_version')).fetchone())[0]
                await db.execute('BEGIN')
            if ids is None:
                ids = (await _whole_conversation_sources(db, request, history_ids) if history_ids is not None
                       else set(json.loads(request['source_message_ids'])))
                request['source_message_ids'] = json.dumps(sorted(ids))
            plan = None
            if request['forget_long_term']:
                authorized = await _authorized_sources(db, request, ids)
                plan = await _plan_recompute(db, request, ids, authorized)
            if not write_locked:
                await db.rollback()

            embeddings = {}
            if plan is not None and not legacy_pending:
                for fact in plan[0]:
                    if fact.restored_from is not None:
                        key = (fact.fact_id, fact.restored_from, fact.restored_text)
                        if key not in embedding_cache:
                            try:
                                embedding_cache[key] = await prepare_passage_embedding(fact.restored_text, local_only=True)
                            except MemoryValidationError:
                                # A restored version can still be recalled through its text.
                                embedding_cache[key] = None
                        embeddings[fact.fact_id] = embedding_cache[key]

            if write_locked:
                break
            # Evidence can change without advancing a fact's version.
            await db.execute('BEGIN IMMEDIATE')
            if (await (await db.execute('PRAGMA data_version')).fetchone())[0] == revision:
                break
            await db.rollback()

        if not legacy_pending:
            await _record_erasure(db, request)
        counts = {'episodic_deleted': 0, 'core_facts_deleted': 0}
        if plan is not None:
            counts = await _apply_recompute(db, request, worldline, plan, embeddings)
            legacy = await memory_service.forget_exclusive_sources(
                request['session_id'], worldline, {mid for _, mid in authorized},
                identity_mode=request['identity_mode'], _transaction=db,
            )
            counts = {key: value + legacy[key] for key, value in counts.items()}

        await db.execute(
            """UPDATE memory_observation_sources SET source_state='deleted',excerpt=NULL
               WHERE conversation_id=? AND source_message_id IN (SELECT value FROM json_each(?))
                 AND observation_id IN (SELECT observation_id FROM memory_observations
                                        WHERE session_id=? AND identity_mode=?)""",
            (request['conversation_id'], request['source_message_ids'], request['session_id'], request['identity_mode']),
        )
        await db.commit()
        return counts
    except BaseException:
        await db.rollback()
        raise
    finally:
        await db.close()


async def remove_truncated_audio(worldline: str, conversation_id: str, message_ids: list[int]) -> None:
    """Post-commit hook for removing audio associated with the frozen message IDs."""


async def _finish_erasure(request: dict, worldline: str) -> dict[str, int]:
    counts = await _erase_history(request, worldline)
    if request.get('cutoff_message_id') is not None:
        await remove_truncated_audio(worldline, request['conversation_id'], json.loads(request['source_message_ids']))
    db = await get_db(worldline, 'memory')
    try:
        await db.execute("UPDATE conversation_erasure_requests SET state='completed' WHERE request_id=?", (request['request_id'],))
        await db.commit()
    finally:
        await db.close()
    return counts


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
        elif request.get('cutoff_message_id') is not None:
            from app.services.conversations import DEFAULT_CONVERSATION_TITLE, NEW_CONVERSATION_TITLE
            await db.execute(
                """UPDATE conversations SET title=CASE WHEN is_default THEN ? ELSE ? END,updated_at=?
                   WHERE id=? AND session_id=? AND title_source='auto'""",
                (DEFAULT_CONVERSATION_TITLE, NEW_CONVERSATION_TITLE, request['created_at'],
                 request['conversation_id'], request['session_id']),
            )
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
        request = dict(row)
        if not request['request_id'].startswith(_ATOMIC_REQUEST_PREFIX):
            await _commit_erasure_memory(request, worldline, legacy_pending=True)
        await _finish_erasure(request, worldline)
