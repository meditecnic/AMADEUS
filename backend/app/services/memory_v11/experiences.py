"""Memory v11 first-class experience storage primitives (S3C-1 + S3D-3).

Experiences are stored per worldline in the physical memory DB (no worldline
column), scoped by (session_id, identity_mode), and always linked to exactly
one memory_observations row that carries the authoritative source lineage.

S3D-3 adds the user-facing browse lifecycle: scoped list/details and explicit
delete with real content erasure (scrubbed shell + retained source receipts).
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from app.db import get_db, normalize_identity_mode, normalize_worldline
from app.services.memory_v11.contracts import MemoryValidationError
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


def _require_session_id(session_id: str | None) -> str:
    session = (session_id or "").strip()
    if not session:
        raise MemoryValidationError("empty_session_id", "session_id is required")
    return session


def _is_expired(status: Any, expires_at: Any, now_iso: str) -> bool:
    if str(status or "") == "expired":
        return True
    if expires_at is None:
        return False
    try:
        return datetime.fromisoformat(expires_at) <= datetime.fromisoformat(now_iso)
    except (TypeError, ValueError):
        # Unparseable expiry in the browse surface: fail soft (visible history).
        return False


async def list_experiences(
    *,
    session_id: str,
    worldline: str,
    identity_mode: str,
    limit: int = 20,
    offset: int = 0,
) -> dict[str, Any]:
    """User BROWSE surface (NOT prompt retrieval).

    Visible: status 'active' + 'expired' (past-expiry active rows included,
    flagged is_expired). Hidden: 'deleted' and 'pending_source_delete'.
    Deterministic local ordering; total counts the identical visible set.
    """
    wl = normalize_worldline(worldline)
    mode = normalize_identity_mode(identity_mode)
    session = _require_session_id(session_id)
    now_iso = _utc_now()

    db = await get_db(wl, "memory")
    try:
        rows = await (
            await db.execute(
                """SELECT experience_id, conversation_id, display_text,
                          confidence, status, expires_at, created_at
                     FROM experiences
                    WHERE session_id=? AND identity_mode=?
                      AND status IN ('active','expired')
                    ORDER BY created_at DESC, experience_id ASC
                    LIMIT ? OFFSET ?""",
                (session, mode, int(limit), int(offset)),
            )
        ).fetchall()
        total_row = await (
            await db.execute(
                """SELECT COUNT(*) AS n FROM experiences
                    WHERE session_id=? AND identity_mode=?
                      AND status IN ('active','expired')""",
                (session, mode),
            )
        ).fetchone()
        total = int(total_row["n"])
        experiences = [
            {
                "experience_id": str(row["experience_id"]),
                "conversation_id": str(row["conversation_id"] or ""),
                "display_text": str(row["display_text"] or ""),
                "confidence": float(row["confidence"] or 0.0),
                "status": str(row["status"]),
                "expires_at": row["expires_at"],
                "created_at": row["created_at"],
                "is_expired": _is_expired(row["status"], row["expires_at"], now_iso),
            }
            for row in rows
        ]
        return {
            "scope": {
                "session_id": session,
                "worldline": wl,
                "identity_mode": mode,
            },
            "experiences": experiences,
            "pagination": {
                "limit": int(limit),
                "offset": int(offset),
                "total": total,
                "has_more": int(offset) + len(experiences) < total,
            },
        }
    finally:
        await db.close()


async def experience_details(
    *,
    session_id: str,
    worldline: str,
    identity_mode: str,
    experience_id: str,
) -> dict[str, Any]:
    """Scoped details with minimal provenance (never semantic/internal fields)."""
    wl = normalize_worldline(worldline)
    mode = normalize_identity_mode(identity_mode)
    session = _require_session_id(session_id)
    eid = (experience_id or "").strip()
    if not eid:
        raise MemoryValidationError("empty_experience_id", "experience_id is required")
    now_iso = _utc_now()

    db = await get_db(wl, "memory")
    try:
        row = await (
            await db.execute(
                """SELECT experience_id, conversation_id, display_text,
                          confidence, status, expires_at, created_at, observation_id
                     FROM experiences
                    WHERE experience_id=? AND session_id=? AND identity_mode=?
                      AND status IN ('active','expired')""",
                (eid, session, mode),
            )
        ).fetchone()
        if row is None:
            raise MemoryValidationError(
                "experience_not_found", f"experience_id={eid}"
            )
        prov_rows = await (
            await db.execute(
                """SELECT source_message_id, conversation_id, source_created_at,
                          source_state, excerpt
                     FROM memory_observation_sources
                    WHERE observation_id=?
                    ORDER BY source_created_at ASC, source_message_id ASC""",
                (row["observation_id"],),
            )
        ).fetchall()
        provenance = []
        for pr in prov_rows:
            source_state = str(pr["source_state"] or "present")
            provenance.append(
                {
                    "source_message_id": int(pr["source_message_id"]),
                    "conversation_id": str(pr["conversation_id"] or ""),
                    "source_created_at": pr["source_created_at"],
                    "source_state": source_state,
                    # Deleted source forces excerpt=null even if stale content
                    # somehow remains in the DB.
                    "excerpt": None if source_state == "deleted" else pr["excerpt"],
                }
            )
        return {
            "scope": {
                "session_id": session,
                "worldline": wl,
                "identity_mode": mode,
            },
            "experience": {
                "experience_id": str(row["experience_id"]),
                "conversation_id": str(row["conversation_id"] or ""),
                "display_text": str(row["display_text"] or ""),
                "confidence": float(row["confidence"] or 0.0),
                "status": str(row["status"]),
                "expires_at": row["expires_at"],
                "created_at": row["created_at"],
                "is_expired": _is_expired(row["status"], row["expires_at"], now_iso),
            },
            "observation_id": str(row["observation_id"]),
            "provenance": provenance,
        }
    finally:
        await db.close()


async def _scrub_observation_sources(
    db: Any, *, observation_id: str, now: str
) -> None:
    """Scrub excerpt content; PRESERVE source receipts and source_state."""
    await db.execute(
        """UPDATE memory_observation_sources
           SET excerpt=NULL
         WHERE observation_id=?""",
        (observation_id,),
    )


async def delete_experience(
    *,
    session_id: str,
    worldline: str,
    identity_mode: str,
    experience_id: str,
) -> dict[str, Any]:
    """Explicit user delete with real content erasure, one atomic transaction.

    - experience shell: status='deleted', display_text='', semantic_json='{}'
      (syntactically valid JSON, no recoverable semantic content), fingerprints
      preserved, deleted_at set
    - linked observation (episodic-exclusive): status='deleted', semantic
      content scrubbed to NULL, fingerprint preserved
    - source receipts preserved with excerpt=NULL; source_state never changed
      by a Memory-card delete
    - integrity guard: the linked observation must not appear in
      stable_fact_evidence (schema does not enforce this) — fail closed
    - repeated delete of an already-deleted shell is idempotent (200)
    - pending_source_delete rows belong to the future conversation-delete saga
      and cannot be deleted here (409)
    """
    wl = normalize_worldline(worldline)
    mode = normalize_identity_mode(identity_mode)
    session = _require_session_id(session_id)
    eid = (experience_id or "").strip()
    if not eid:
        raise MemoryValidationError("empty_experience_id", "experience_id is required")

    db = await get_db(wl, "memory")
    try:
        await db.execute("BEGIN IMMEDIATE")
        row = await (
            await db.execute(
                """SELECT experience_id, status, observation_id FROM experiences
                    WHERE experience_id=? AND session_id=? AND identity_mode=?""",
                (eid, session, mode),
            )
        ).fetchone()
        if row is None:
            raise MemoryValidationError(
                "experience_not_found", f"experience_id={eid}"
            )
        status = str(row["status"])
        if status == "deleted":
            await db.commit()
            return {
                "experience_id": eid,
                "scope": {
                    "session_id": session,
                    "worldline": wl,
                    "identity_mode": mode,
                },
                "state": "deleted",
                "deleted": True,
                "idempotent": True,
            }
        if status == "pending_source_delete":
            raise MemoryValidationError(
                "experience_delete_pending", f"experience_id={eid}"
            )
        observation_id = str(row["observation_id"])

        # P0 integrity guard: the linked episodic observation must never act as
        # stable-fact evidence (schema has no enforcement).
        guard = await (
            await db.execute(
                """SELECT COUNT(*) AS n FROM stable_fact_evidence
                    WHERE observation_id=?""",
                (observation_id,),
            )
        ).fetchone()
        if int(guard["n"]) > 0:
            raise MemoryValidationError(
                "experience_observation_conflict",
                f"observation_id={observation_id} referenced by stable fact evidence",
            )

        now = _utc_now()
        await db.execute(
            """UPDATE experiences
               SET status='deleted', display_text='', semantic_json='{}',
                   deleted_at=?, updated_at=?
             WHERE experience_id=? AND session_id=? AND identity_mode=?""",
            (now, now, eid, session, mode),
        )
        await db.execute(
            """UPDATE memory_observations
               SET status='deleted', display_text=NULL, semantic_json=NULL,
                   topic_label_proposal=NULL, updated_at=?
             WHERE observation_id=?""",
            (now, observation_id),
        )
        await _scrub_observation_sources(db, observation_id=observation_id, now=now)
        await db.commit()
    except Exception:
        await db.rollback()
        raise
    finally:
        await db.close()

    return {
        "experience_id": eid,
        "scope": {"session_id": session, "worldline": wl, "identity_mode": mode},
        "state": "deleted",
        "deleted": True,
        "idempotent": False,
    }
