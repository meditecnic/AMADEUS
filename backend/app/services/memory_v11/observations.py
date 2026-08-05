"""Memory v11 observation diagnostics + ignore lifecycle (S3D-3).

Deterministic local code only (no provider/LLM/network). Ignore is neither
promote nor delete: it only blocks future reconciliation participation while
retaining the row and its diagnostic content.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.db import get_db, normalize_identity_mode, normalize_worldline
from app.services.memory_v11.contracts import MemoryValidationError


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _require_session_id(session_id: str | None) -> str:
    session = (session_id or "").strip()
    if not session:
        raise MemoryValidationError("empty_session_id", "session_id is required")
    return session


async def list_observations(
    *,
    session_id: str,
    worldline: str,
    identity_mode: str,
    limit: int = 20,
    offset: int = 0,
) -> dict[str, Any]:
    """Advanced scoped diagnostics across ALL observation statuses.

    Deleted/content-scrubbed observations return display_text=null; text is
    never reconstructed from sources.
    """
    wl = normalize_worldline(worldline)
    mode = normalize_identity_mode(identity_mode)
    session = _require_session_id(session_id)

    db = await get_db(wl, "memory")
    try:
        rows = await (
            await db.execute(
                """SELECT observation_id, display_text, evidence_kind,
                          memory_class, confidence, status, reanalysis_count,
                          expires_at, created_at
                     FROM memory_observations
                    WHERE session_id=? AND identity_mode=?
                    ORDER BY created_at DESC, observation_id ASC
                    LIMIT ? OFFSET ?""",
                (session, mode, int(limit), int(offset)),
            )
        ).fetchall()
        total_row = await (
            await db.execute(
                """SELECT COUNT(*) AS n FROM memory_observations
                    WHERE session_id=? AND identity_mode=?""",
                (session, mode),
            )
        ).fetchone()
        total = int(total_row["n"])
        observations = [
            {
                "observation_id": str(row["observation_id"]),
                "display_text": row["display_text"],
                "evidence_kind": str(row["evidence_kind"] or ""),
                "memory_class": str(row["memory_class"] or ""),
                "confidence": row["confidence"],
                "status": str(row["status"]),
                "reanalysis_count": int(row["reanalysis_count"] or 0),
                "expires_at": row["expires_at"],
                "created_at": row["created_at"],
            }
            for row in rows
        ]
        return {
            "scope": {
                "session_id": session,
                "worldline": wl,
                "identity_mode": mode,
            },
            "observations": observations,
            "pagination": {
                "limit": int(limit),
                "offset": int(offset),
                "total": total,
                "has_more": int(offset) + len(observations) < total,
            },
        }
    finally:
        await db.close()


async def ignore_observation(
    *,
    session_id: str,
    worldline: str,
    identity_mode: str,
    observation_id: str,
) -> dict[str, Any]:
    """candidate → ignored (idempotent); never promote, never delete.

    Integrity guard (fail closed): a candidate may never be silently ignored
    while it illegally acts as stable-fact evidence or experience backing
    state — schema does not enforce those exclusivities.
    """
    wl = normalize_worldline(worldline)
    mode = normalize_identity_mode(identity_mode)
    session = _require_session_id(session_id)
    oid = (observation_id or "").strip()
    if not oid:
        raise MemoryValidationError(
            "empty_observation_id", "observation_id is required"
        )

    db = await get_db(wl, "memory")
    try:
        await db.execute("BEGIN IMMEDIATE")
        row = await (
            await db.execute(
                """SELECT status FROM memory_observations
                    WHERE observation_id=? AND session_id=? AND identity_mode=?""",
                (oid, session, mode),
            )
        ).fetchone()
        if row is None:
            raise MemoryValidationError(
                "observation_not_found", f"observation_id={oid}"
            )
        status = str(row["status"])

        # Frozen order: status classification FIRST. Attached observations
        # legitimately carry stable_fact_evidence — that is not corruption.
        if status == "ignored":
            await db.commit()
            return {
                "observation_id": oid,
                "status": "ignored",
                "scope": {
                    "session_id": session,
                    "worldline": wl,
                    "identity_mode": mode,
                },
                "idempotent": True,
            }
        if status != "candidate":
            raise MemoryValidationError(
                "observation_not_ignorable",
                f"observation_id={oid} status={status}",
            )

        # ONLY a candidate may be guarded: candidate + stable_fact_evidence or
        # candidate + experience is the illegal structural state (fail closed).
        evidence = await (
            await db.execute(
                """SELECT COUNT(*) AS n FROM stable_fact_evidence
                    WHERE observation_id=?""",
                (oid,),
            )
        ).fetchone()
        experiences = await (
            await db.execute(
                """SELECT COUNT(*) AS n FROM experiences
                    WHERE observation_id=?""",
                (oid,),
            )
        ).fetchone()
        if int(evidence["n"]) > 0 or int(experiences["n"]) > 0:
            raise MemoryValidationError(
                "observation_state_conflict",
                f"observation_id={oid} is evidence or experience backing state",
            )

        await db.execute(
            """UPDATE memory_observations
               SET status='ignored', updated_at=?
             WHERE observation_id=?""",
            (_utc_now(), oid),
        )
        await db.commit()
    except Exception:
        await db.rollback()
        raise
    finally:
        await db.close()

    return {
        "observation_id": oid,
        "status": "ignored",
        "scope": {"session_id": session, "worldline": wl, "identity_mode": mode},
        "idempotent": False,
    }
