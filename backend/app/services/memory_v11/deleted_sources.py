"""Content-free, pipeline-independent source suppression after a user deletion."""
from __future__ import annotations

from typing import Any


async def source_is_deleted(db: Any, *, session_id: str, identity_mode: str,
                            conversation_id: str, source_message_id: int) -> bool:
    row = await (await db.execute(
        """SELECT 1 FROM memory_deleted_sources
            WHERE session_id=? AND identity_mode=? AND conversation_id=?
              AND source_message_id=? LIMIT 1""",
        (session_id, identity_mode, conversation_id, int(source_message_id)),
    )).fetchone()
    return row is not None


async def record_suppression(db: Any, *, session_id: str, identity_mode: str, cursor: Any) -> None:
    from app.services.memory_v11.receipts import insert_operation_results
    await insert_operation_results(db, session_id=session_id, identity_mode=identity_mode,
                                   cursor=cursor, results=["source_deleted"])
