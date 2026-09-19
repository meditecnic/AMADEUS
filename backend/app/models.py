"""Compatibility repository API backed by physically isolated history databases."""

from __future__ import annotations

import sqlite3

from app.db import get_db, normalize_worldline
from app.services.working_summary import decode_working_summary, encode_working_summary


async def _resolve_conversation_id(
    session_id: str,
    worldline: str,
    conversation_id: str | None,
) -> str:
    from app.services.conversations import conversation_service

    if session_id is None:
        raise sqlite3.IntegrityError("NOT NULL constraint failed: messages.session_id")
    if conversation_id:
        await conversation_service.require_owned(conversation_id, session_id, worldline)
        return conversation_id
    default = await conversation_service.get_or_create_default(session_id, worldline)
    return str(default["id"])


async def save_message(
    session_id: str,
    role: str,
    content: str,
    worldline: str = "steins_gate",
    turn_id: str | None = None,
    revision: int = 0,
    conversation_id: str | None = None,
    translation: str | None = None,
):
    wl = normalize_worldline(worldline)
    cid = await _resolve_conversation_id(session_id, wl, conversation_id)
    db = await get_db(wl, "history")
    try:
        cur = await db.execute(
            """INSERT INTO messages(
                   session_id,conversation_id,role,content,translation,turn_id,revision
               ) VALUES(?,?,?,?,?,?,?)""",
            (session_id, cid, role, content, translation, turn_id, revision),
        )
        await db.commit()
        return cur.lastrowid
    finally:
        await db.close()


async def update_message_translation(
    message_id: int,
    session_id: str,
    conversation_id: str,
    translation: str,
    worldline: str = "steins_gate",
) -> bool:
    """Repair one assistant translation without crossing owner or conversation scope."""
    wl = normalize_worldline(worldline)
    db = await get_db(wl, "history")
    try:
        cur = await db.execute(
            """UPDATE messages SET translation=?
               WHERE id=? AND session_id=? AND conversation_id=? AND role='assistant'""",
            (translation, message_id, session_id, conversation_id),
        )
        await db.commit()
        return cur.rowcount == 1
    finally:
        await db.close()


async def get_recent_messages(
    session_id: str,
    limit: int = 6,
    worldline: str = "steins_gate",
    conversation_id: str | None = None,
):
    wl = normalize_worldline(worldline)
    cid = await _resolve_conversation_id(session_id, wl, conversation_id)
    db = await get_db(wl, "history")
    try:
        cur = await db.execute(
            """SELECT id,role,content FROM messages
               WHERE session_id=? AND conversation_id=? ORDER BY id DESC LIMIT ?""",
            (session_id, cid, limit),
        )
        rows = await cur.fetchall()
        # Include message id for Memory Ingest v2 provenance windows.
        return [
            {"id": int(row["id"]), "role": row["role"], "content": row["content"]}
            for row in reversed(rows)
        ]
    finally:
        await db.close()


async def get_session_messages(
    session_id: str,
    worldline: str = "steins_gate",
    conversation_id: str | None = None,
):
    wl = normalize_worldline(worldline)
    cid = await _resolve_conversation_id(session_id, wl, conversation_id)
    db = await get_db(wl, "history")
    try:
        cur = await db.execute(
            """SELECT conversation_id,role,content,translation,created_at,turn_id,revision
               FROM messages WHERE session_id=? AND conversation_id=? ORDER BY id""",
            (session_id, cid),
        )
        return [dict(row) for row in await cur.fetchall()]
    finally:
        await db.close()


async def get_conversation_content_epoch(
    session_id: str,
    worldline: str = "steins_gate",
    conversation_id: str | None = None,
) -> int:
    """Freeze the history generation before starting asynchronous summarization."""
    wl = normalize_worldline(worldline)
    cid = await _resolve_conversation_id(session_id, wl, conversation_id)
    db = await get_db(wl, "history")
    try:
        row = await (await db.execute(
            "SELECT epoch FROM conversation_content_epochs WHERE session_id=? AND conversation_id=?",
            (session_id, cid),
        )).fetchone()
        return int(row["epoch"]) if row else 0
    finally:
        await db.close()


async def save_memory_summary(
    session_id: str,
    summary: str,
    worldline: str = "steins_gate",
    conversation_id: str | None = None,
    *,
    expected_epoch: int | None = None,
) -> bool:
    """Persist a working summary (D38).

    Accepts decoded/runtime PLAIN text and encodes internally before INSERT so
    the storage marker can never escape through this API. Callers never encode
    DB strings manually. A stale generated result returns False without writing.
    """
    wl = normalize_worldline(worldline)
    cid = await _resolve_conversation_id(session_id, wl, conversation_id)
    db = await get_db(wl, "history")
    try:
        await db.execute("BEGIN IMMEDIATE")
        if expected_epoch is not None:
            row = await (await db.execute(
                "SELECT epoch FROM conversation_content_epochs WHERE session_id=? AND conversation_id=?",
                (session_id, cid),
            )).fetchone()
            current_epoch = int(row["epoch"]) if row else 0
            if current_epoch != expected_epoch:
                await db.rollback()
                return False
        await db.execute(
            "INSERT INTO memory_summaries(session_id,conversation_id,summary) VALUES(?,?,?)",
            (session_id, cid, encode_working_summary(summary)),
        )
        await db.commit()
        return True
    finally:
        await db.close()


async def get_latest_summary(
    session_id: str,
    worldline: str = "steins_gate",
    conversation_id: str | None = None,
) -> str:
    """Read the newest working summary as decoded PLAIN text (fail closed)."""
    wl = normalize_worldline(worldline)
    cid = await _resolve_conversation_id(session_id, wl, conversation_id)
    db = await get_db(wl, "history")
    try:
        cur = await db.execute(
            """SELECT summary FROM memory_summaries
               WHERE session_id=? AND conversation_id=? ORDER BY id DESC LIMIT 1""",
            (session_id, cid),
        )
        row = await cur.fetchone()
        return decode_working_summary(row["summary"]) if row else ""
    finally:
        await db.close()


async def clear_session_data(session_id: str, worldline: str = "steins_gate", include_memories: bool = False):
    wl = normalize_worldline(worldline)
    db = await get_db(wl, "history")
    try:
        await db.execute("DELETE FROM messages WHERE session_id=?", (session_id,))
        await db.execute("DELETE FROM memory_summaries WHERE session_id=?", (session_id,))
        await db.commit()
    finally:
        await db.close()
    if include_memories:
        memory = await get_db(wl, "memory")
        try:
            await memory.execute("DELETE FROM episodic_memories WHERE session_id=?", (session_id,))
            await memory.execute("DELETE FROM core_facts WHERE session_id=?", (session_id,))
            await memory.execute("DELETE FROM emotion_states WHERE session_id=?", (session_id,))
            await memory.commit()
        finally:
            await memory.close()
