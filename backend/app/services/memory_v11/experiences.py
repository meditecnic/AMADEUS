"""Memory v11 first-class experience storage primitives (S3C-1).

Experiences are stored per worldline in the physical memory DB (no worldline
column), scoped by (session_id, identity_mode), and always linked to exactly
one memory_observations row that carries the authoritative source lineage.
Only storage: retrieval / expiry filtering / TTL cleanup belong to S3C-2+.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from app.services.memory_v11.repository import _canonical_fingerprint


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _semantic_blob_and_fp(semantic: Any) -> tuple[str, str]:
    if isinstance(semantic, str):
        return semantic, _canonical_fingerprint({"raw": semantic})
    blob = json.dumps(
        semantic or {}, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return blob, _canonical_fingerprint(semantic or {})


async def insert_experience(
    db: Any,
    *,
    session_id: str,
    conversation_id: str,
    identity_mode: str,
    observation_id: str,
    display_text: str,
    semantic: Any,
    confidence: float,
    expires_at: str | None = None,
) -> dict[str, Any]:
    """Insert one active experience inside the caller's write transaction.

    experience_id is always backend-generated; provider output never supplies
    storage identity. The FK to memory_observations guarantees every
    experience has exactly one observation with its source lineage.
    """
    experience_id = str(uuid.uuid4())
    semantic_blob, fingerprint = _semantic_blob_and_fp(semantic)
    now = _utc_now()
    await db.execute(
        """INSERT INTO experiences(
               experience_id, session_id, conversation_id, identity_mode,
               observation_id, display_text, semantic_json,
               semantic_fingerprint, confidence, status, expires_at,
               created_at, updated_at, deleted_at
           ) VALUES(?,?,?,?,?,?,?,?,?, 'active', ?, ?, ?, NULL)""",
        (
            experience_id,
            session_id,
            conversation_id,
            identity_mode,
            observation_id,
            (display_text or "").strip(),
            semantic_blob,
            fingerprint,
            float(confidence),
            expires_at,
            now,
            now,
        ),
    )
    return {
        "experience_id": experience_id,
        "observation_id": observation_id,
        "session_id": session_id,
        "identity_mode": identity_mode,
    }
