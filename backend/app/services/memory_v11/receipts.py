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
