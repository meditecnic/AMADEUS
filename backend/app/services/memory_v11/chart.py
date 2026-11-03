"""Flat, scoped chart snapshot. Both physical stores are opened read-only."""

from collections import Counter
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite

from app.db import _resolve_path
from app.services.memory_v11.contracts import MemoryValidationError
from app.services.memory_v11.projection import (
    _is_expired,
    _is_unavailable_store_error,
    _parse_row_time,
    _utc_now,
    load_visible_memory_rows,
)
from app.services.memory_v11.recall import MemoryScope
from app.services.memory_v11.resident import load_resident_fact_ids

CHART_ITEM_LIMIT = 2000


class ChartError(MemoryValidationError):
    """Stable chart error envelope."""


@asynccontextmanager
async def _read_snapshot(worldline: str, kind: str):
    uri = Path(_resolve_path(worldline, kind)).resolve().as_uri() + "?mode=ro"
    db = await aiosqlite.connect(uri, uri=True, timeout=30.0)
    try:
        db.row_factory = aiosqlite.Row
        await db.execute("BEGIN")
        yield db
        await db.execute("COMMIT")
    finally:
        # Closing also rolls back an interrupted read transaction.
        await db.close()


def _created_key(item):
    return _parse_row_time(item["created_at"]), item["id"]


async def build_chart(scope: MemoryScope) -> dict:
    now = datetime.now(timezone.utc)
    generated_at = _utc_now()
    try:
        async with _read_snapshot(scope.worldline, "memory") as db:
            fact_rows, exp_rows, topic_rows = await load_visible_memory_rows(
                db, scope.session_id, scope.identity_mode
            )
            statistics = await (await db.execute(
                """SELECT f.fact_id, COUNT(DISTINCT e.observation_id) AS evidence_count,
                          (SELECT COUNT(*) FROM stable_fact_versions v
                            WHERE v.fact_id=f.fact_id) AS version_count
                     FROM stable_facts f
                     LEFT JOIN stable_fact_evidence e ON e.fact_id=f.fact_id
                    WHERE f.session_id=? AND f.identity_mode=?
                    GROUP BY f.fact_id""",
                (scope.session_id, scope.identity_mode),
            )).fetchall()
            resident_ids = await load_resident_fact_ids(scope, db=db)

        async with _read_snapshot(scope.worldline, "history") as db:
            first_message = await (await db.execute(
                """SELECT m.created_at FROM messages m
                     JOIN conversations c ON c.id=m.conversation_id
                    WHERE m.session_id=? AND c.session_id=? AND c.identity_mode=?
                    ORDER BY julianday(m.created_at), m.id LIMIT 1""",
                (scope.session_id, scope.session_id, scope.identity_mode),
            )).fetchone()
    except Exception as exc:
        if _is_unavailable_store_error(exc):
            raise ChartError("chart_unavailable", "memory store unavailable") from None
        raise

    stats = {row["fact_id"]: row for row in statistics}
    facts = [
        {
            "id": str(row["fact_id"]), "label": str(row["display_text"] or ""),
            "topic_id": str(row["topic_id"]) if row["topic_id"] else None,
            "created_at": row["created_at"], "updated_at": row["updated_at"],
            "is_pinned": bool(row["is_pinned"]),
            "evidence_count": int(stats[row["fact_id"]]["evidence_count"]),
            "version_count": int(stats[row["fact_id"]]["version_count"]),
            "resident": str(row["fact_id"]) in resident_ids,
        }
        for row in fact_rows
    ]
    experiences = [
        {
            "id": str(row["experience_id"]), "label": str(row["display_text"] or ""),
            "created_at": row["created_at"], "updated_at": row["updated_at"],
            "is_pinned": bool(row["is_pinned"]), "expires_at": row["expires_at"],
        }
        for row in exp_rows if not _is_expired(row["status"], row["expires_at"], now)
    ]
    topic_counts = Counter(fact["topic_id"] for fact in facts)
    topics = [
        {"id": str(row["topic_id"]), "label": str(row["display_label"] or ""),
         "created_at": row["created_at"], "fact_count": topic_counts[row["topic_id"]]}
        for row in topic_rows
    ]
    counts = {"facts": len(facts), "experiences": len(experiences), "topics": len(topics)}
    combined = sorted(facts + experiences, key=_created_key)
    origin_at = first_message["created_at"] if first_message else (
        combined[0]["created_at"] if combined else None
    )
    truncated = len(combined) > CHART_ITEM_LIMIT
    if truncated:
        # Keep the newest items across both kinds, then emit each in time order.
        kept = {id(item) for item in combined[-CHART_ITEM_LIMIT:]}
        facts = [item for item in facts if id(item) in kept]
        experiences = [item for item in experiences if id(item) in kept]
    return {
        "scope": {"session_id": scope.session_id, "worldline": scope.worldline,
                  "identity_mode": scope.identity_mode},
        "generated_at": generated_at, "origin_at": origin_at,
        "topics": sorted(topics, key=_created_key),
        "facts": sorted(facts, key=_created_key),
        "experiences": sorted(experiences, key=_created_key),
        "counts": counts, "truncated": truncated,
    }
