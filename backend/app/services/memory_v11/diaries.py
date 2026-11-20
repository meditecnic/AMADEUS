"""Durable diary windows and scoped diary storage."""

import hashlib
import json
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from app.db import get_db, normalize_identity_mode, normalize_worldline
from app.services.conversation_erasure import sources_erased
from app.services.memory_v11.contracts import MemoryValidationError

_CLEAR_BODY = """state='discarded', deleted_at=?, body_ja=NULL, body_zh=NULL,
                 verified_phrases_json=NULL"""


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds")


@asynccontextmanager
async def _write_window(worldline):
    # Lock memory before history and hold both locks until the memory commit.
    memory = await get_db(worldline, "memory")
    try:
        await memory.execute("BEGIN IMMEDIATE")
        history = await get_db(worldline, "history")
        try:
            await history.execute("BEGIN IMMEDIATE")
            yield memory, history
            await memory.commit()
        finally:
            try:
                await history.rollback()
            finally:
                await history.close()
    finally:
        await memory.close()


async def _read_source(history, session_id, identity_mode, conversation_id, first, last):
    return await (await history.execute(
        """SELECT m.id,m.role,m.content,m.translation,m.turn_id,m.revision,m.created_at,
                  COALESCE(e.epoch,0) AS content_epoch
           FROM messages m JOIN conversations c ON c.id=m.conversation_id AND c.session_id=m.session_id
           LEFT JOIN conversation_content_epochs e ON e.session_id=m.session_id AND e.conversation_id=c.id
           WHERE m.session_id=? AND c.identity_mode=? AND c.id=? AND m.id BETWEEN ? AND ?
           ORDER BY m.id""",
        (session_id, identity_mode, conversation_id, first, last),
    )).fetchall()


def _fingerprint(rows) -> str:
    messages = [(row["id"], row["role"], row["content"], row["turn_id"], row["created_at"]) for row in rows]
    data = json.dumps(messages, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def _matches_source(row, source) -> bool:
    return bool(source and len(source) == row["source_count"]
                and source[0]["content_epoch"] == row["content_epoch"]
                and _fingerprint(source) == row["source_fingerprint"])


async def register_diary_window(
    *, session_id: str, identity_mode: str, worldline: str, conversation_id: str,
    first_message_id: int, last_message_id: int, due_at: datetime,
) -> str | None:
    """Reserve one inclusive window. Stale, duplicate and overlapping windows return None."""
    mode = normalize_identity_mode(identity_mode)
    scope = (session_id, mode, conversation_id)
    async with _write_window(worldline) as (db, history):
        overlap = await (await db.execute(
            """SELECT 1 FROM memory_diaries WHERE session_id=? AND identity_mode=? AND conversation_id=?
               AND first_message_id<=? AND last_message_id>=? LIMIT 1""",
            (*scope, last_message_id, first_message_id),
        )).fetchone()
        if overlap:
            return None
        rows = await _read_source(history, *scope, first_message_id, last_message_id)
        if not rows or rows[0]["id"] != first_message_id or rows[-1]["id"] != last_message_id:
            return None
        if await sources_erased(db, session_id=session_id, identity_mode=mode,
                                conversation_id=conversation_id, source_message_ids=[row["id"] for row in rows]):
            return None
        diary_id = str(uuid.uuid4())
        await db.execute(
            """INSERT INTO memory_diaries(diary_id,session_id,identity_mode,conversation_id,
                    first_message_id,last_message_id,source_count,source_fingerprint,content_epoch,due_at,state,created_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,'pending',?)""",
            (diary_id, *scope, first_message_id, last_message_id, len(rows), _fingerprint(rows),
             rows[0]["content_epoch"], _iso(due_at), _iso(datetime.now(timezone.utc))),
        )
    return diary_id


async def list_due_diary_windows(*, worldline: str, now: datetime, limit: int = 8) -> list[dict]:
    """Return due windows awaiting promotion to the ingest queue."""
    db = await get_db(worldline, "memory")
    try:
        rows = await (await db.execute(
            """SELECT d.* FROM memory_diaries d WHERE d.state='pending' AND d.deleted_at IS NULL
                 AND d.due_at<=? AND NOT EXISTS (SELECT 1 FROM memory_ingest_jobs j WHERE j.job_id=d.diary_id)
               ORDER BY d.due_at,d.diary_id LIMIT ?""", (_iso(now), limit),
        )).fetchall()
        return [dict(row) for row in rows]
    finally:
        await db.close()


async def load_diary_source(*, worldline: str, window: dict) -> list[dict] | None:
    """Read the registered snapshot for generation."""
    history = await get_db(worldline, "history")
    try:
        source = await _read_source(history, window["session_id"], window["identity_mode"],
                                    window["conversation_id"], window["first_message_id"], window["last_message_id"])
        return [dict(message) for message in source] if _matches_source(window, source) else None
    finally:
        await history.close()


async def commit_diary(
    *, session_id: str, identity_mode: str, worldline: str, diary_id: str,
    body_ja: str, body_zh: str, verified_phrases: list[str], lease_owner: str | None = None,
) -> bool:
    """Publish a generated diary if its window is still writable and unchanged."""
    mode = normalize_identity_mode(identity_mode)
    async with _write_window(worldline) as (db, history):
        row = await (await db.execute(
            """SELECT * FROM memory_diaries WHERE diary_id=? AND session_id=? AND identity_mode=?
                 AND state='pending' AND deleted_at IS NULL""", (diary_id, session_id, mode),
        )).fetchone()
        if row is None:
            return False
        source = await _read_source(history, session_id, mode, row["conversation_id"],
                                    row["first_message_id"], row["last_message_id"])
        erased = await sources_erased(db, session_id=session_id, identity_mode=mode,
                                      conversation_id=row["conversation_id"],
                                      source_message_ids=[message["id"] for message in source])
        now = _iso(datetime.now(timezone.utc))
        job = await (await db.execute(
            "SELECT state,lease_owner,lease_expires_at FROM memory_ingest_jobs WHERE job_id=?", (diary_id,),
        )).fetchone()
        if job is not None and (lease_owner is None or job["state"] != "processing"
                                or job["lease_owner"] != lease_owner or not job["lease_expires_at"]
                                or job["lease_expires_at"] <= now):
            return False
        if erased or not _matches_source(row, source):
            await db.execute("UPDATE memory_diaries SET state='discarded' WHERE diary_id=?", (diary_id,))
            return False
        await db.execute(
            """UPDATE memory_diaries SET state='written',body_ja=?,body_zh=?,verified_phrases_json=?,written_at=?
               WHERE diary_id=?""",
            (body_ja, body_zh, json.dumps(verified_phrases, ensure_ascii=False), now, diary_id),
        )
    return True


async def list_diaries(*, session_id: str, identity_mode: str, worldline: str) -> dict:
    wl, mode = normalize_worldline(worldline), normalize_identity_mode(identity_mode)
    db = await get_db(wl, "memory")
    try:
        rows = await (await db.execute(
            """SELECT diary_id,conversation_id,first_message_id,last_message_id,source_count,content_epoch,
                      body_ja,body_zh,verified_phrases_json,written_at
               FROM memory_diaries WHERE session_id=? AND identity_mode=? AND state='written' AND deleted_at IS NULL
               ORDER BY written_at DESC,diary_id""", (session_id, mode),
        )).fetchall()
        return {"scope": {"session_id": session_id, "identity_mode": mode, "worldline": wl},
                "diaries": [dict(row) for row in rows]}
    finally:
        await db.close()


async def delete_diary(*, session_id: str, identity_mode: str, worldline: str, diary_id: str) -> None:
    db = await get_db(worldline, "memory")
    try:
        now = _iso(datetime.now(timezone.utc))
        result = await db.execute(
            f"""UPDATE memory_diaries SET {_CLEAR_BODY}
                WHERE diary_id=? AND session_id=? AND identity_mode=? AND deleted_at IS NULL""",
            (now, diary_id, session_id, normalize_identity_mode(identity_mode)),
        )
        if not result.rowcount:
            raise MemoryValidationError("diary_not_found", f"diary_id={diary_id}")
        await db.execute(
            """UPDATE memory_ingest_jobs SET state='cancelled',retryable=0,lease_owner=NULL,
                      lease_expires_at=NULL,next_attempt_at=NULL,last_error_code='source_missing',updated_at=?
               WHERE job_id=? AND state IN ('pending','processing','failed')""", (now, diary_id),
        )
        await db.commit()
    finally:
        await db.close()


async def erase_conversation_diaries(db, request: dict) -> None:
    """Apply a durable erasure intent in its existing memory write transaction."""
    scope = (request["session_id"], request["identity_mode"], request["conversation_id"])
    if request["action"] == "delete" or request["forget_long_term"]:
        await db.execute(
            f"""UPDATE memory_diaries SET {_CLEAR_BODY}
                WHERE session_id=? AND identity_mode=? AND conversation_id=? AND deleted_at IS NULL""",
            (request["created_at"], *scope),
        )
    else:
        await db.execute(
            """UPDATE memory_diaries SET state='discarded'
               WHERE session_id=? AND identity_mode=? AND conversation_id=? AND state='pending'
                 AND EXISTS (SELECT 1 FROM json_each(?) WHERE value BETWEEN first_message_id AND last_message_id)""",
            (*scope, request["source_message_ids"]),
        )
