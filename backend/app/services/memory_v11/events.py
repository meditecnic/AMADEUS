"""Ephemeral, scoped memory notifications; delivery is never replayed.

memory.recorded carries conversation_id, turn_id, source_message_id and changes
({kind: fact|experience, id, change: create|refine|supersede, label, created_at}),
and is published once after a non-replay job completes with changes, only to v2
connections currently in that conversation and session/worldline/identity scope.

memory.referenced carries conversation_id, turn_id, generation and items
({kind: fact|experience, id, via: supplement|episodic|recall, label, created_at}),
and is queued once after reply publication immediately before turn.completed,
only for uncancelled v2 turns with nonempty, nonresident, deduplicated items
in supplement/episodic/recall order, capped at 32 after request budgeting.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable

from app.db import get_db

logger = logging.getLogger(__name__)
_subscribers: dict[tuple[str, str, str], set[Callable]] = {}
_pending: set[asyncio.Task] = set()


def subscribe(scope: tuple[str, str, str], receive: Callable) -> Callable:
    """Callbacks must only enqueue without blocking or awaiting transport."""
    _subscribers.setdefault(scope, set()).add(receive)

    def unsubscribe():
        listeners = _subscribers.get(scope)
        if listeners is not None:
            listeners.discard(receive)
            if not listeners:
                _subscribers.pop(scope, None)

    return unsubscribe


async def describe_items(db, scope, items):
    """Read chart labels and original creation times on the caller's connection."""
    result = []
    for item in items:
        if item["kind"] == "fact":
            sql = """SELECT v.display_text, f.created_at FROM stable_facts f
                     JOIN stable_fact_versions v ON v.fact_id=f.fact_id
                       AND v.version_no=f.active_version
                     WHERE f.fact_id=? AND f.session_id=? AND f.identity_mode=?
                       AND f.state='active' AND v.invalid_at IS NULL"""
        else:
            sql = """SELECT display_text, created_at FROM experiences
                     WHERE experience_id=? AND session_id=? AND identity_mode=?
                       AND status='active'"""
        row = await (await db.execute(sql, (item["id"], scope[0], scope[2]))).fetchone()
        if row is not None:
            result.append({**item, "label": row["display_text"], "created_at": row["created_at"]})
    return result


def publish_recorded(job: dict, applied: dict) -> None:
    """Schedule optional work after completion; never delay or fail the job."""
    scope = (job["session_id"], job["worldline"], job["identity_mode"])
    changes = [c for c in applied.get("changes", [])
               if c["change"] in {"create", "refine", "supersede"}]
    if (job["state"] != "completed" or applied.get("replay") or not changes
            or not _subscribers.get(scope)):
        return
    task = asyncio.create_task(_deliver_recorded(scope, job, changes))
    _pending.add(task)
    task.add_done_callback(_pending.discard)


async def _deliver_recorded(scope, job, changes):
    try:
        from app.services.conversation_erasure import sources_erased
        from app.services.memory_v11.deleted_sources import source_is_deleted

        db = await get_db(scope[1], "memory")
        try:
            if await sources_erased(
                db, session_id=scope[0], identity_mode=scope[2],
                conversation_id=job["conversation_id"],
                source_message_ids=[job["source_message_id"]],
            ) or await source_is_deleted(
                db, session_id=scope[0], identity_mode=scope[2],
                conversation_id=job["conversation_id"], source_message_id=job["source_message_id"],
            ):
                return
            items = await describe_items(db, scope, changes)
        finally:
            await db.close()
        if not items:
            return
        history = await get_db(scope[1], "history")
        try:
            source = await (await history.execute(
                """SELECT m.turn_id FROM messages m JOIN conversations c ON c.id=m.conversation_id
                   WHERE m.id=? AND m.session_id=? AND m.conversation_id=? AND m.role='user'
                     AND c.session_id=? AND c.identity_mode=?""",
                (job["source_message_id"], scope[0], job["conversation_id"], scope[0], scope[2]),
            )).fetchone()
        finally:
            await history.close()
        if source is None or not source["turn_id"]:
            return
        payload = {"type": "memory.recorded", "conversation_id": job["conversation_id"],
                   "turn_id": source["turn_id"], "source_message_id": job["source_message_id"],
                   "changes": items}
        for receive in tuple(_subscribers.get(scope, ())):
            try:
                receive(payload)
            except Exception as exc:
                logger.warning("Memory subscriber failed (%s)", type(exc).__name__)
    except Exception as exc:
        logger.warning("Memory notification failed (%s)", type(exc).__name__)


async def referenced_items_for_turn(scope, compiled, recall_ids):
    """Admit only budgeted sources, excluding residents across every source."""
    residents = set(getattr(compiled, "resident_fact_ids", ()))
    seen = {("fact", fact_id) for fact_id in residents}
    items = []
    for kind, via, ids in (
        ("fact", "supplement", getattr(compiled, "supplement_fact_ids", ())),
        ("experience", "episodic", getattr(compiled, "experience_ids", ())),
        ("fact", "recall", recall_ids),
    ):
        for memory_id in ids:
            if (kind, memory_id) not in seen:
                seen.add((kind, memory_id))
                items.append({"kind": kind, "id": memory_id, "via": via})
    if not items:
        return []
    try:
        db = await get_db(scope[1], "memory")
        try:
            return await describe_items(db, scope, items[:32])
        finally:
            await db.close()
    except Exception as exc:
        logger.warning("Memory reference unavailable (%s)", type(exc).__name__)
        return []
