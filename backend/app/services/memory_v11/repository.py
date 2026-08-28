"""Memory v11 repository primitives (S1: stable facts + active-version invariants)."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any

from app.db import get_db, normalize_identity_mode, normalize_worldline
from app.services.memory_v11.contracts import (
    CHANGE_KINDS,
    MemoryConstraintError,
    MemoryValidationError,
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical_fingerprint(semantic_json: dict[str, Any] | str) -> str:
    if isinstance(semantic_json, str):
        payload = semantic_json
    else:
        payload = json.dumps(
            semantic_json,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _normalize_change_kind(change_kind: str) -> str:
    kind = (change_kind or "").strip().lower()
    if kind not in CHANGE_KINDS:
        raise MemoryValidationError(
            "invalid_change_kind",
            f"unsupported change_kind: {change_kind!r}",
        )
    return kind


def _require_session_id(session_id: str | None) -> str:
    session = (session_id or "").strip()
    if not session:
        raise MemoryValidationError("empty_session_id", "session_id is required")
    return session


async def create_stable_fact(
    *,
    session_id: str,
    worldline: str,
    identity_mode: str,
    display_text: str,
    semantic_json: dict[str, Any] | str,
    confidence: float = 0.9,
    change_kind: str = "create",
    topic_id: str | None = None,
    is_pinned: bool = False,
) -> dict[str, Any]:
    """Insert a new stable fact with version 1 as the sole open/active version.

    Fail-closed scope rules (before any fact/version write):
    - session_id non-empty
    - optional topic_id must exist under the same (session_id, identity_mode)
    """
    wl = normalize_worldline(worldline)
    mode = normalize_identity_mode(identity_mode)
    session = _require_session_id(session_id)
    kind = _normalize_change_kind(change_kind)
    text = (display_text or "").strip()
    if not text:
        raise MemoryValidationError("empty_display_text", "display_text required")
    if not (0.0 <= float(confidence) <= 1.0):
        raise MemoryValidationError("invalid_confidence", "confidence must be 0..1")

    topic_ref = (topic_id or "").strip() or None

    if isinstance(semantic_json, str):
        semantic_blob = semantic_json
        try:
            parsed = json.loads(semantic_json)
        except json.JSONDecodeError as exc:
            raise MemoryValidationError("invalid_semantic_json", str(exc)) from exc
        fingerprint = _canonical_fingerprint(
            parsed if isinstance(parsed, dict) else {"raw": parsed}
        )
    else:
        semantic_blob = json.dumps(
            semantic_json, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        fingerprint = _canonical_fingerprint(semantic_json)

    fact_id = str(uuid.uuid4())
    version_no = 1
    now = _utc_now()

    db = await get_db(wl, "memory")
    try:
        await db.execute("BEGIN IMMEDIATE")
        if topic_ref is not None:
            cur = await db.execute(
                """SELECT 1 FROM memory_topics
                    WHERE topic_id=? AND session_id=? AND identity_mode=?""",
                (topic_ref, session, mode),
            )
            if await cur.fetchone() is None:
                raise MemoryValidationError(
                    "topic_scope_mismatch",
                    f"topic_id={topic_ref} not in scope session={session} mode={mode}",
                )

        await db.execute(
            """INSERT INTO stable_facts(
                   fact_id, session_id, identity_mode, topic_id, state,
                   active_version, is_pinned, created_at, updated_at, deleted_at
               ) VALUES(?,?,?,?, 'active', ?, ?, ?, ?, NULL)""",
            (
                fact_id,
                session,
                mode,
                topic_ref,
                version_no,
                1 if is_pinned else 0,
                now,
                now,
            ),
        )
        await db.execute(
            """INSERT INTO stable_fact_versions(
                   fact_id, version_no, display_text, semantic_json,
                   semantic_fingerprint, confidence, change_kind,
                   previous_version, valid_from, invalid_at, created_by_job_id
               ) VALUES(?,?,?,?,?,?,?,NULL,?,NULL,NULL)""",
            (
                fact_id,
                version_no,
                text,
                semantic_blob,
                fingerprint,
                float(confidence),
                kind,
                now,
            ),
        )
        await db.commit()
    except MemoryValidationError:
        await db.rollback()
        raise
    except Exception:
        await db.rollback()
        raise
    finally:
        await db.close()

    return {
        "fact_id": fact_id,
        "version_no": version_no,
        "display_text": text,
        "session_id": session,
        "identity_mode": mode,
        "worldline": wl,
        "topic_id": topic_ref,
    }


# Alias used by some S0 contract probes.
insert_stable_fact_with_version = create_stable_fact


async def set_active_version(
    *,
    session_id: str,
    worldline: str,
    identity_mode: str,
    fact_id: str,
    active_version: int,
) -> None:
    """Point stable_facts.active_version at an existing open version; reject mismatches."""
    wl = normalize_worldline(worldline)
    mode = normalize_identity_mode(identity_mode)
    session = _require_session_id(session_id)
    db = await get_db(wl, "memory")
    try:
        cur = await db.execute(
            """SELECT active_version, state FROM stable_facts
                WHERE fact_id=? AND session_id=? AND identity_mode=?""",
            (fact_id, session, mode),
        )
        row = await cur.fetchone()
        if row is None:
            raise MemoryValidationError("fact_not_found", f"fact_id={fact_id}")
        if str(row["state"]) != "active":
            raise MemoryValidationError("fact_not_active", f"fact_id={fact_id}")

        cur = await db.execute(
            """SELECT version_no, invalid_at FROM stable_fact_versions
                WHERE fact_id=? AND version_no=?""",
            (fact_id, int(active_version)),
        )
        version_row = await cur.fetchone()
        if version_row is None:
            raise MemoryValidationError(
                "version_not_found",
                f"fact_id={fact_id} version={active_version}",
            )
        if version_row["invalid_at"] is not None:
            raise MemoryValidationError(
                "invalid_active_version",
                f"version {active_version} is already invalid",
            )

        await db.execute(
            """UPDATE stable_facts
               SET active_version=?, updated_at=?
             WHERE fact_id=? AND session_id=? AND identity_mode=?""",
            (int(active_version), _utc_now(), fact_id, session, mode),
        )
        await db.commit()
    except MemoryValidationError:
        await db.rollback()
        raise
    except Exception:
        await db.rollback()
        raise
    finally:
        await db.close()


update_active_version = set_active_version


async def validate_active_version_invariants(
    *,
    worldline: str,
    session_id: str | None = None,
    identity_mode: str | None = None,
) -> None:
    """Ensure each active fact has exactly one open version equal to active_version."""
    wl = normalize_worldline(worldline)
    db = await get_db(wl, "memory")
    try:
        clauses = ["state = 'active'"]
        params: list[Any] = []
        if session_id is not None:
            clauses.append("session_id = ?")
            params.append(session_id)
        if identity_mode is not None:
            clauses.append("identity_mode = ?")
            params.append(normalize_identity_mode(identity_mode))
        where = " AND ".join(clauses)
        cur = await db.execute(
            f"SELECT fact_id, session_id, identity_mode, active_version FROM stable_facts WHERE {where}",
            params,
        )
        facts = await cur.fetchall()
        for fact in facts:
            fact_id = fact["fact_id"]
            active_version = fact["active_version"]
            cur = await db.execute(
                """SELECT version_no FROM stable_fact_versions
                    WHERE fact_id=? AND invalid_at IS NULL""",
                (fact_id,),
            )
            open_versions = [int(row["version_no"]) for row in await cur.fetchall()]
            if len(open_versions) != 1:
                raise MemoryConstraintError(
                    "active_version_invariant",
                    f"fact_id={fact_id} open_versions={open_versions}",
                )
            if active_version is None or int(active_version) != open_versions[0]:
                raise MemoryConstraintError(
                    "active_version_mismatch",
                    f"fact_id={fact_id} active={active_version} open={open_versions[0]}",
                )
    finally:
        await db.close()


async def list_active_stable_facts(
    *,
    session_id: str,
    worldline: str,
    identity_mode: str,
    query: str | None = None,
    topic_id: str | None = None,
    pinned_only: bool = False,
) -> list[dict[str, Any]]:
    """List active facts with their open version display text (scope-strict).

    Local filters only (no LLM): optional substring ``query`` on display_text,
    exact ``topic_id``, and ``pinned_only``.
    """
    wl = normalize_worldline(worldline)
    mode = normalize_identity_mode(identity_mode)
    session = _require_session_id(session_id)
    topic_filter = (topic_id or "").strip() or None
    query_norm = (query or "").strip().lower() or None

    db = await get_db(wl, "memory")
    try:
        clauses = [
            "f.session_id=?",
            "f.identity_mode=?",
            "f.state='active'",
        ]
        params: list[Any] = [session, mode]
        if topic_filter is not None:
            clauses.append("f.topic_id=?")
            params.append(topic_filter)
        if pinned_only:
            clauses.append("f.is_pinned=1")
        where = " AND ".join(clauses)
        cur = await db.execute(
            f"""SELECT f.fact_id, f.active_version, f.is_pinned, f.topic_id,
                      v.display_text, v.version_no, v.confidence,
                      v.semantic_fingerprint
                 FROM stable_facts f
                 JOIN stable_fact_versions v
                   ON v.fact_id = f.fact_id
                  AND v.version_no = f.active_version
                  AND v.invalid_at IS NULL
                WHERE {where}
                ORDER BY f.is_pinned DESC, f.updated_at DESC""",
            params,
        )
        rows = await cur.fetchall()
        items = [
            {
                "fact_id": row["fact_id"],
                "display_text": row["display_text"],
                "version_no": int(row["version_no"]),
                "active_version": int(row["active_version"]),
                "is_pinned": bool(row["is_pinned"]),
                "topic_id": row["topic_id"],
                "confidence": row["confidence"],
                "semantic_fingerprint": row["semantic_fingerprint"],
            }
            for row in rows
        ]
        if query_norm is not None:
            items = [
                item
                for item in items
                if query_norm in str(item.get("display_text") or "").lower()
            ]
        return items
    finally:
        await db.close()


async def list_active_stable_facts_page(
    *,
    session_id: str,
    worldline: str,
    identity_mode: str,
    query: str | None = None,
    topic_id: str | None = None,
    pinned_only: bool = False,
    limit: int,
    offset: int,
) -> dict[str, Any]:
    """Bounded offset pagination over active stable facts (S3F-2B).

    Filter-then-page, entirely in SQL so COUNT and page share the SAME filter
    clauses: scope → active → active-version join → topic → pinned → query
    (literal substring via escaped LIKE) → deterministic order → COUNT → LIMIT.

    The unbounded sibling ``list_active_stable_facts`` is unchanged for prompt
    retrieval / reconciler replay paths.
    """
    wl = normalize_worldline(worldline)
    mode = normalize_identity_mode(identity_mode)
    session = _require_session_id(session_id)
    topic_filter = (topic_id or "").strip() or None
    query_norm = (query or "").strip().lower() or None

    clauses = [
        "f.session_id=?",
        "f.identity_mode=?",
        "f.state='active'",
    ]
    params: list[Any] = [session, mode]
    if topic_filter is not None:
        clauses.append("f.topic_id=?")
        params.append(topic_filter)
    if pinned_only:
        clauses.append("f.is_pinned=1")
    if query_norm is not None:
        # Literal substring: caller wildcards must not become pattern operators.
        escaped = (
            query_norm.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        )
        clauses.append("v.display_text LIKE ? ESCAPE '\\'")
        params.append(f"%{escaped}%")
    where = " AND ".join(clauses)

    db = await get_db(wl, "memory")
    try:
        cur = await db.execute(
            f"""SELECT COUNT(*) AS n
                  FROM stable_facts f
                  JOIN stable_fact_versions v
                    ON v.fact_id = f.fact_id
                   AND v.version_no = f.active_version
                   AND v.invalid_at IS NULL
                 WHERE {where}""",
            params,
        )
        total = int((await cur.fetchone())["n"])
        cur = await db.execute(
            f"""SELECT f.fact_id, f.active_version, f.is_pinned, f.topic_id,
                      f.updated_at,
                      v.display_text, v.version_no, v.confidence,
                      v.semantic_fingerprint
                 FROM stable_facts f
                 JOIN stable_fact_versions v
                   ON v.fact_id = f.fact_id
                  AND v.version_no = f.active_version
                  AND v.invalid_at IS NULL
                WHERE {where}
                ORDER BY f.is_pinned DESC, f.updated_at DESC, f.fact_id ASC
                LIMIT ? OFFSET ?""",
            [*params, int(limit), int(offset)],
        )
        rows = await cur.fetchall()
        items = [
            {
                "fact_id": row["fact_id"],
                "display_text": row["display_text"],
                "version_no": int(row["version_no"]),
                "active_version": int(row["active_version"]),
                "is_pinned": bool(row["is_pinned"]),
                "topic_id": row["topic_id"],
                # S4-ARCHIVE Gate 2 additive field (parent contract §8): the
                # browse row carries the fact's own updated_at; ordering and
                # admission are unchanged.
                "updated_at": row["updated_at"],
                "confidence": row["confidence"],
                "semantic_fingerprint": row["semantic_fingerprint"],
            }
            for row in rows
        ]
        return {"facts": items, "total": total}
    finally:
        await db.close()


async def job_status_summary(
    *,
    session_id: str,
    worldline: str,
    identity_mode: str,
) -> dict[str, Any]:
    """Aggregate ingest job counts for one full scope (no message bodies)."""
    wl = normalize_worldline(worldline)
    mode = normalize_identity_mode(identity_mode)
    session = _require_session_id(session_id)
    db = await get_db(wl, "memory")
    try:
        cur = await db.execute(
            """SELECT state, COUNT(*) AS n
                 FROM memory_ingest_jobs
                WHERE session_id=? AND identity_mode=?
                GROUP BY state""",
            (session, mode),
        )
        counts = {str(row["state"]): int(row["n"]) for row in await cur.fetchall()}
        cur = await db.execute(
            """SELECT MAX(completed_at) AS latest
                 FROM memory_ingest_jobs
                WHERE session_id=? AND identity_mode=? AND state='completed'""",
            (session, mode),
        )
        latest_row = await cur.fetchone()
        latest = latest_row["latest"] if latest_row is not None else None
        # S4-ARCHIVE Gate 2 §16 C3: bounded failed-jobs surface — max 8,
        # updated_at DESC / job_id ASC, fields ONLY job_id, last_error_code,
        # attempt_count, updated_at, retryable. ``state`` is never exposed
        # (the list is definitionally failed jobs). ``retryable`` is read
        # from the persisted per-job authority stamped by the ingest
        # pipeline (jobs._transition_owned) — never a client inference.
        cur = await db.execute(
            """SELECT job_id, attempt_count, last_error_code, updated_at, retryable
                 FROM memory_ingest_jobs
                WHERE session_id=? AND identity_mode=? AND state='failed'
                ORDER BY updated_at DESC, job_id ASC
                LIMIT 8""",
            (session, mode),
        )
        failed_jobs = [
            {
                "job_id": str(row["job_id"]),
                "last_error_code": row["last_error_code"],
                "attempt_count": int(row["attempt_count"] or 0),
                "updated_at": row["updated_at"],
                "retryable": bool(int(row["retryable"] if row["retryable"] is not None else 1)),
            }
            for row in await cur.fetchall()
        ]
        return {
            "session_id": session,
            "worldline": wl,
            "identity_mode": mode,
            "pending_count": int(counts.get("pending", 0)),
            "processing_count": int(counts.get("processing", 0)),
            "failed_count": int(counts.get("failed", 0)),
            "failed_jobs": failed_jobs,
            "latest_completed_at": latest,
        }
    finally:
        await db.close()
