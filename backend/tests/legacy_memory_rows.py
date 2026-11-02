"""Write rows straight into the legacy memory tables.

The legacy extraction/write path is gone, but the tables and any rows already
in users' databases remain: migration, conversation erasure and v11 tests seed
them through these helpers.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from app.db import get_db, normalize_identity_mode, normalize_worldline


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def insert_legacy_core_fact(
    session_id: str,
    worldline: str,
    fact_key: str,
    fact_value: str,
    *,
    identity_mode: str = "okabe",
    confidence: float = 0.9,
    importance: float = 0.8,
    source_message_ids: list[int] | None = None,
    is_pinned: bool = False,
    created_at: str | None = None,
) -> int:
    """Insert a current core_facts row, superseding the current row for the key."""
    db = await get_db(normalize_worldline(worldline), "memory")
    try:
        await db.execute("BEGIN IMMEDIATE")
        mode = normalize_identity_mode(identity_mode)
        old = await (await db.execute(
            """SELECT id FROM core_facts
                WHERE session_id=? AND identity_mode=? AND fact_key=? AND is_current=1""",
            (session_id, mode, fact_key),
        )).fetchone()
        if old is not None:
            await db.execute("UPDATE core_facts SET is_current=0 WHERE id=?", (old["id"],))
        cursor = await db.execute(
            """INSERT INTO core_facts(
                   session_id,identity_mode,fact_key,fact_value,confidence,importance,
                   is_current,is_pinned,source_message_ids,created_at
               ) VALUES(?,?,?,?,?,?,1,?,?,?)""",
            (
                session_id, mode, fact_key, fact_value, confidence, importance,
                int(is_pinned), json.dumps(list(source_message_ids or [1])),
                created_at or _now(),
            ),
        )
        new_id = int(cursor.lastrowid)
        if old is not None:
            await db.execute("UPDATE core_facts SET superseded_by=? WHERE id=?", (new_id, old["id"]))
        await db.commit()
        return new_id
    finally:
        await db.close()


async def insert_legacy_episode(
    session_id: str,
    worldline: str,
    content: str,
    *,
    identity_mode: str = "okabe",
    confidence: float = 0.9,
    importance: float = 0.6,
    source_message_ids: list[int] | None = None,
    created_at: str | None = None,
) -> int:
    db = await get_db(normalize_worldline(worldline), "memory")
    try:
        cursor = await db.execute(
            """INSERT INTO episodic_memories(
                   session_id,identity_mode,content,confidence,importance,source_message_ids,created_at
               ) VALUES(?,?,?,?,?,?,?)""",
            (
                session_id, normalize_identity_mode(identity_mode), content, confidence,
                importance, json.dumps(list(source_message_ids or [1])), created_at or _now(),
            ),
        )
        await db.commit()
        return int(cursor.lastrowid)
    finally:
        await db.close()


async def insert_legacy_shared_fact(
    owner_session_id: str,
    fact_key: str,
    fact_value: str,
    *,
    origin_worldline: str,
    origin_conversation_id: str,
    confidence: float = 0.9,
    importance: float = 0.8,
    source_message_ids: list[int] | None = None,
    is_pinned: bool = False,
    created_at: str | None = None,
) -> int:
    """Insert a current shared_user_facts row (control store)."""
    db = await get_db("steins_gate", "control")
    try:
        await db.execute("BEGIN IMMEDIATE")
        old = await (await db.execute(
            """SELECT id FROM shared_user_facts
                WHERE owner_session_id=? AND fact_key=? AND is_current=1""",
            (owner_session_id, fact_key),
        )).fetchone()
        if old is not None:
            await db.execute("UPDATE shared_user_facts SET is_current=0 WHERE id=?", (old["id"],))
        cursor = await db.execute(
            """INSERT INTO shared_user_facts(
                   owner_session_id,fact_key,fact_value,confidence,importance,
                   is_current,is_pinned,origin_worldline,origin_conversation_id,
                   origin_identity_mode,source_message_ids,created_at
               ) VALUES(?,?,?,?,?,1,?,?,?,'self',?,?)""",
            (
                owner_session_id, fact_key, fact_value, confidence, importance,
                int(is_pinned), normalize_worldline(origin_worldline), origin_conversation_id,
                json.dumps(list(source_message_ids or [1])), created_at or _now(),
            ),
        )
        new_id = int(cursor.lastrowid)
        if old is not None:
            await db.execute(
                "UPDATE shared_user_facts SET superseded_by=? WHERE id=?", (new_id, old["id"])
            )
        await db.commit()
        return new_id
    finally:
        await db.close()
