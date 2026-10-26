"""D28 durable ingest receipts (S3F-1) — processing infrastructure, not Memory.

Pure/local/SQLite only. Both helpers receive the caller's existing DB
connection and never open or commit their own: reconciler.py must be able to
insert the receipt inside its already-existing semantic write transaction.

Receipt identity = session_id + conversation_id + identity_mode +
source_message_id + pipeline_version (mirrors the ingest job source cursor).
pipeline_version is mandatory: a receipt for memory-v11-1 must never suppress
deliberate reprocessing under memory-v11-2.

Honesty boundary: a crash after the external provider returned but BEFORE any
local receipt transaction committed may cause the provider to be called again.
No provider-side idempotency mechanism exists in this slice.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

RECEIPT_KIND_EMPTY = "provider_empty"
RECEIPT_KIND_NONEMPTY = "provider_nonempty"

_RECEIPT_KINDS = frozenset({RECEIPT_KIND_EMPTY, RECEIPT_KIND_NONEMPTY})


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def has_ingest_receipt(
    db: Any,
    *,
    session_id: str,
    conversation_id: str,
    identity_mode: str,
    source_message_id: int,
    pipeline_version: str,
) -> bool:
    """True when the full-cursor dedicated receipt exists."""
    row = await (
        await db.execute(
            """SELECT 1
                 FROM memory_ingest_receipts
                WHERE session_id=? AND conversation_id=? AND identity_mode=?
                  AND source_message_id=? AND pipeline_version=?
                LIMIT 1""",
            (
                session_id,
                conversation_id,
                identity_mode,
                int(source_message_id),
                pipeline_version,
            ),
        )
    ).fetchone()
    return row is not None


async def insert_ingest_receipt(
    db: Any,
    *,
    session_id: str,
    conversation_id: str,
    identity_mode: str,
    source_message_id: int,
    pipeline_version: str,
    receipt_kind: str,
) -> None:
    """Insert one receipt; the caller owns the transaction (PK is idempotent)."""
    if receipt_kind not in _RECEIPT_KINDS:
        raise ValueError(f"invalid receipt_kind: {receipt_kind!r}")
    await db.execute(
        """INSERT OR IGNORE INTO memory_ingest_receipts(
               session_id, conversation_id, identity_mode, source_message_id,
               pipeline_version, receipt_kind, created_at
           ) VALUES(?,?,?,?,?,?,?)""",
        (
            session_id,
            conversation_id,
            identity_mode,
            int(source_message_id),
            pipeline_version,
            receipt_kind,
            _now(),
        ),
    )


async def insert_operation_results(db: Any, *, session_id: str, identity_mode: str, cursor: Any, results: list[str]) -> None:
    """Code-only attempt receipt; transaction owned by caller, never replay authority.

    Lazy additive migration also covers existing databases. No observation refs,
    provider values, text, offsets or exception messages enter this table.
    """
    await db.execute("""CREATE TABLE IF NOT EXISTS memory_ingest_operation_results (
        session_id TEXT NOT NULL,
        conversation_id TEXT NOT NULL,
        identity_mode TEXT NOT NULL,
        source_message_id INTEGER NOT NULL,
        pipeline_version TEXT NOT NULL,
        attempt_no INTEGER NOT NULL,
        operation_index INTEGER NOT NULL,
        result_code TEXT NOT NULL,
        PRIMARY KEY(session_id, conversation_id, identity_mode, source_message_id,
                    pipeline_version, attempt_no, operation_index)
    )""")
    for index, code in enumerate(results, 1):
        await db.execute("""INSERT OR IGNORE INTO memory_ingest_operation_results
            VALUES(?,?,?,?,?,?,?,?)""", (
                session_id, cursor["conversation_id"], identity_mode,
                int(cursor["source_message_id"]), cursor["pipeline_version"],
                int(cursor.get("attempt_no") or 1), index, code,
            ))
