"""Stable-fact lifecycle primitives (S3D-1): user edit / delete / details.

All three operations are strict-scope, deterministic, and never call a
provider/LLM/network. Indexing follows the S3B-2 pattern: prepare passage
embeddings before BEGIN IMMEDIATE, then mutate BLOB rows inside the same
transaction as the fact/version rows.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from app.db import get_db, normalize_identity_mode, normalize_worldline
from app.services.memory_v11.contracts import (
    MAX_PINNED_FACTS_PER_SCOPE,
    MemoryValidationError,
)
from app.services.memory_v11.indexing import (
    delete_version_embedding,
    insert_version_embedding,
    prepare_passage_embedding,
)
from app.services.memory_v11.repository import _canonical_fingerprint


# Sentinel distinguishing "field omitted" from "topic_id=null (ungroup)".
_UNSET = object()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _require_session_id(session_id: str | None) -> str:
    session = (session_id or "").strip()
    if not session:
        raise MemoryValidationError("empty_session_id", "session_id is required")
    return session


def _require_fact_id(fact_id: str | None) -> str:
    fid = (fact_id or "").strip()
    if not fid:
        raise MemoryValidationError("empty_fact_id", "fact_id is required")
    return fid


def _parse_expected_version(expected_version: int | None) -> int:
    if expected_version is None:
        raise MemoryValidationError(
            "missing_expected_version", "expected_version is required"
        )
    if isinstance(expected_version, bool) or not isinstance(expected_version, int):
        raise MemoryValidationError(
            "invalid_expected_version", "expected_version must be a positive int"
        )
    if int(expected_version) <= 0:
        raise MemoryValidationError(
            "invalid_expected_version", "expected_version must be a positive int"
        )
    return int(expected_version)


async def user_edit_fact(
    *,
    session_id: str,
    worldline: str,
    identity_mode: str,
    fact_id: str,
    display_text: str,
    expected_version: int,
    embedder: Any | None = None,
) -> dict[str, Any]:
    """User edit (plan §7.1): one new ``user_edit`` version on the SAME fact.

    Deterministic canonical semantic payload (no provider/LLM in this slice);
    the edited text is user-authoritative. Confidence is preserved from the
    previous active version. The old active vector is removed and the new
    version vector inserted in the same transaction as the version rows.
    """
    wl = normalize_worldline(worldline)
    mode = normalize_identity_mode(identity_mode)
    session = _require_session_id(session_id)
    fid = _require_fact_id(fact_id)
    text = (display_text or "").strip()
    if not text:
        raise MemoryValidationError("empty_display_text", "display_text required")
    version = _parse_expected_version(expected_version)

    semantic = {
        "subject": "user",
        "predicate": "states",
        "object": {"text": text},
        "qualifiers": {"source": "user_edit"},
    }
    semantic_blob = json.dumps(
        semantic, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    fingerprint = _canonical_fingerprint(semantic)

    # S3B-2 pattern: encode OUTSIDE the write lock, fail-closed before txn.
    prepared = await prepare_passage_embedding(text, embedder=embedder)

    now = _utc_now()
    db = await get_db(wl, "memory")
    try:
        await db.execute("BEGIN IMMEDIATE")
        row = await (
            await db.execute(
                """SELECT f.active_version, v.confidence AS active_confidence
                     FROM stable_facts f
                     JOIN stable_fact_versions v
                       ON v.fact_id = f.fact_id AND v.version_no = f.active_version
                    WHERE f.fact_id=? AND f.session_id=? AND f.identity_mode=?
                      AND f.state='active'""",
                (fid, session, mode),
            )
        ).fetchone()
        if row is None:
            raise MemoryValidationError("fact_not_found", f"fact_id={fid}")
        active_version = int(row["active_version"])
        if version != active_version:
            raise MemoryValidationError(
                "expected_version_mismatch",
                f"expected={version} active={active_version}",
            )
        confidence = float(row["active_confidence"])
        new_version = active_version + 1

        await db.execute(
            """UPDATE stable_fact_versions
               SET invalid_at=?
             WHERE fact_id=? AND version_no=? AND invalid_at IS NULL""",
            (now, fid, active_version),
        )
        await db.execute(
            """INSERT INTO stable_fact_versions(
                   fact_id, version_no, display_text, semantic_json,
                   semantic_fingerprint, confidence, change_kind,
                   previous_version, valid_from, invalid_at, created_by_job_id
               ) VALUES(?,?,?,?,?,?, 'user_edit', ?, ?, NULL, NULL)""",
            (
                fid,
                new_version,
                text,
                semantic_blob,
                fingerprint,
                confidence,
                active_version,
                now,
            ),
        )
        await db.execute(
            """UPDATE stable_facts
               SET active_version=?, updated_at=?
             WHERE fact_id=? AND session_id=? AND identity_mode=?""",
            (new_version, now, fid, session, mode),
        )
        # Old vector must not remain indexable; new vector is required.
        await delete_version_embedding(db, fact_id=fid, version_no=active_version)
        await insert_version_embedding(
            db, fact_id=fid, version_no=new_version, prepared=prepared
        )
        await db.commit()
    except Exception:
        await db.rollback()
        raise
    finally:
        await db.close()

    return {
        "fact_id": fid,
        "version_no": new_version,
        "active_version": new_version,
        "display_text": text,
        "change_kind": "user_edit",
        "scope": {"session_id": session, "worldline": wl, "identity_mode": mode},
    }


async def delete_fact(
    *,
    session_id: str,
    worldline: str,
    identity_mode: str,
    fact_id: str,
    expected_version: int | None = None,
    _transaction: Any | None = None,
) -> dict[str, Any]:
    """Delete (plan §7.2): tombstone + content erasure, one atomic transaction.

    Recoverable content (versions, vectors, evidence) leaves the v11 surface;
    the stable_facts shell is retained as state='deleted'. Non-content
    tombstone rows keep replay protection (source receipts retained, never
    deleted). Repeated DELETE of an already-deleted fact in the same scope is
    idempotent (200, no duplicate destructive effects).
    """
    wl = normalize_worldline(worldline)
    mode = normalize_identity_mode(identity_mode)
    session = _require_session_id(session_id)
    fid = _require_fact_id(fact_id)
    version = _parse_expected_version(expected_version)

    now = _utc_now()
    db = _transaction if _transaction is not None else await get_db(wl, "memory")
    try:
        if _transaction is None:
            await db.execute("BEGIN IMMEDIATE")
        row = await (
            await db.execute(
                """SELECT state, active_version FROM stable_facts
                    WHERE fact_id=? AND session_id=? AND identity_mode=?""",
                (fid, session, mode),
            )
        ).fetchone()
        if row is None:
            raise MemoryValidationError("fact_not_found", f"fact_id={fid}")
        if str(row["state"]) == "deleted":
            # Idempotent repeat: destructive effects are already applied; the
            # caller's expected_version is no longer meaningful.
            if _transaction is None:
                await db.commit()
            return {
                "fact_id": fid,
                "scope": {
                    "session_id": session,
                    "worldline": wl,
                    "identity_mode": mode,
                },
                "state": "deleted",
                "deleted": True,
                "idempotent": True,
            }
        active_version = int(row["active_version"] or 0)
        if version != active_version:
            raise MemoryValidationError(
                "expected_version_mismatch",
                f"expected={version} active={active_version}",
            )

        # 1. Collect NON-CONTENT tombstone identity before erasure.
        semantic_fps = [
            str(r["semantic_fingerprint"])
            for r in await (
                await db.execute(
                    """SELECT DISTINCT semantic_fingerprint FROM stable_fact_versions
                        WHERE fact_id=?""",
                    (fid,),
                )
            ).fetchall()
        ]
        source_fps = [
            str(r["source_fingerprint"])
            for r in await (
                await db.execute(
                    """SELECT DISTINCT s.source_fingerprint
                         FROM stable_fact_evidence e
                         JOIN memory_observation_sources s
                           ON s.observation_id = e.observation_id
                        WHERE e.fact_id=?""",
                    (fid,),
                )
            ).fetchall()
        ]
        # 2. Insert non-content tombstone rows (reason="user_delete").
        for fp in semantic_fps:
            await db.execute(
                """INSERT INTO memory_tombstones(
                       tombstone_id, session_id, identity_mode, fact_id,
                       source_fingerprint, semantic_fingerprint, reason, deleted_at
                   ) VALUES(?,?,?,?,NULL,?, 'user_delete', ?)""",
                (str(uuid.uuid4()), session, mode, fid, fp, now),
            )
        for fp in source_fps:
            await db.execute(
                """INSERT INTO memory_tombstones(
                       tombstone_id, session_id, identity_mode, fact_id,
                       source_fingerprint, semantic_fingerprint, reason, deleted_at
                   ) VALUES(?,?,?,?,?,NULL, 'user_delete', ?)""",
                (str(uuid.uuid4()), session, mode, fid, fp, now),
            )
        # 3. Observation handling: scrub exclusive observations, keep shared.
        obs_rows = await (
            await db.execute(
                """SELECT DISTINCT observation_id FROM stable_fact_evidence
                    WHERE fact_id=?""",
                (fid,),
            )
        ).fetchall()
        for obs_row in obs_rows:
            observation_id = str(obs_row["observation_id"])
            shared = await (
                await db.execute(
                    """SELECT 1 FROM stable_fact_evidence
                        WHERE observation_id=? AND fact_id != ? LIMIT 1""",
                    (observation_id, fid),
                )
            ).fetchone()
            if shared is None:
                # Exclusive: scrub semantic content, keep source receipt /
                # provenance identity so old-source replay stays blocked.
                await db.execute(
                    """UPDATE memory_observations
                       SET status='deleted', display_text=NULL, semantic_json=NULL,
                           topic_label_proposal=NULL, updated_at=?
                     WHERE observation_id=?""",
                    (now, observation_id),
                )
                await db.execute(
                    "UPDATE memory_observation_sources SET excerpt=NULL WHERE observation_id=?",
                    (observation_id,),
                )
        # 4. Evidence / vectors / versions removal.
        await db.execute("DELETE FROM stable_fact_evidence WHERE fact_id=?", (fid,))
        vector_rows = await (
            await db.execute(
                """SELECT version_no FROM stable_fact_version_embeddings
                    WHERE fact_id=?""",
                (fid,),
            )
        ).fetchall()
        for vector_row in vector_rows:
            await delete_version_embedding(
                db, fact_id=fid, version_no=int(vector_row["version_no"])
            )
        await db.execute("DELETE FROM stable_fact_versions WHERE fact_id=?", (fid,))
        # 5. Retain the shell only as state='deleted'.
        await db.execute(
            """UPDATE stable_facts
               SET state='deleted', active_version=NULL, is_pinned=0,
                   topic_id=NULL, deleted_at=?, updated_at=?
             WHERE fact_id=? AND session_id=? AND identity_mode=?""",
            (now, now, fid, session, mode),
        )
        if _transaction is None:
            await db.commit()
    except Exception:
        if _transaction is None:
            await db.rollback()
        raise
    finally:
        if _transaction is None:
            await db.close()

    return {
        "fact_id": fid,
        "scope": {"session_id": session, "worldline": wl, "identity_mode": mode},
        "state": "deleted",
        "deleted": True,
        "idempotent": False,
    }


async def fact_details(
    *,
    session_id: str,
    worldline: str,
    identity_mode: str,
    fact_id: str,
) -> dict[str, Any]:
    """Details (plan §8.2): active-fact version history + minimal provenance.

    Only ACTIVE facts are part of this surface; deleted / cross-scope /
    missing facts raise fact_not_found. No semantic JSON / fingerprints /
    vectors are exposed, and no provider/LLM/network call happens.
    """
    wl = normalize_worldline(worldline)
    mode = normalize_identity_mode(identity_mode)
    session = _require_session_id(session_id)
    fid = _require_fact_id(fact_id)

    db = await get_db(wl, "memory")
    try:
        row = await (
            await db.execute(
                """SELECT f.active_version, f.topic_id, f.is_pinned, v.display_text
                     FROM stable_facts f
                     JOIN stable_fact_versions v
                       ON v.fact_id = f.fact_id AND v.version_no = f.active_version
                    WHERE f.fact_id=? AND f.session_id=? AND f.identity_mode=?
                      AND f.state='active'""",
                (fid, session, mode),
            )
        ).fetchone()
        if row is None:
            raise MemoryValidationError("fact_not_found", f"fact_id={fid}")
        active_version = int(row["active_version"])

        version_rows = await (
            await db.execute(
                """SELECT version_no, display_text, confidence, change_kind,
                          previous_version, valid_from, invalid_at
                     FROM stable_fact_versions
                    WHERE fact_id=?
                    ORDER BY version_no ASC""",
                (fid,),
            )
        ).fetchall()
        versions = [
            {
                "version_no": int(vr["version_no"]),
                "display_text": str(vr["display_text"] or ""),
                "confidence": float(vr["confidence"] or 0.0),
                "change_kind": str(vr["change_kind"] or ""),
                "previous_version": (
                    int(vr["previous_version"])
                    if vr["previous_version"] is not None
                    else None
                ),
                "valid_from": vr["valid_from"],
                "invalid_at": vr["invalid_at"],
                "is_active": int(vr["version_no"]) == active_version,
            }
            for vr in version_rows
        ]

        prov_rows = await (
            await db.execute(
                """SELECT e.version_no, e.observation_id, s.source_message_id,
                          s.conversation_id, s.source_created_at, s.source_state,
                          s.excerpt
                     FROM stable_fact_evidence e
                     JOIN memory_observation_sources s
                       ON s.observation_id = e.observation_id
                    WHERE e.fact_id=?
                    ORDER BY e.version_no ASC, e.observation_id ASC""",
                (fid,),
            )
        ).fetchall()
        provenance = []
        for pr in prov_rows:
            source_state = str(pr["source_state"] or "present")
            provenance.append(
                {
                    "version_no": int(pr["version_no"]),
                    "observation_id": str(pr["observation_id"]),
                    "source_message_id": int(pr["source_message_id"]),
                    "conversation_id": str(pr["conversation_id"] or ""),
                    "source_created_at": pr["source_created_at"],
                    "source_state": source_state,
                    # REST contract: deleted source forces excerpt=null even if
                    # stale content somehow remains in the DB.
                    "excerpt": None if source_state == "deleted" else pr["excerpt"],
                }
            )

        return {
            "scope": {
                "session_id": session,
                "worldline": wl,
                "identity_mode": mode,
            },
            "fact": {
                "fact_id": fid,
                "topic_id": row["topic_id"],
                "is_pinned": bool(row["is_pinned"]),
                "active_version": active_version,
                "display_text": str(row["display_text"] or ""),
            },
            "versions": versions,
            "provenance": provenance,
        }
    finally:
        await db.close()


async def update_fact_presentation(
    *,
    session_id: str,
    worldline: str,
    identity_mode: str,
    fact_id: str,
    is_pinned: bool | None = None,
    topic_id: str | None | object = _UNSET,
) -> dict[str, Any]:
    """Presentation-only update (plan §7.3 / §8.2): pin and/or topic move.

    - is_pinned=None → preserved; topic_id=_UNSET → preserved;
      topic_id=None → explicit ungroup
    - pin false→true counts ACTIVE same-scope pinned facts inside the SAME
      BEGIN IMMEDIATE and rejects at MAX_PINNED_FACTS_PER_SCOPE
    - target topic must exist in the SAME scope (never auto-created)
    - no version/vector/observation/evidence/provider side effects
    """
    wl = normalize_worldline(worldline)
    mode = normalize_identity_mode(identity_mode)
    session = _require_session_id(session_id)
    fid = _require_fact_id(fact_id)
    if is_pinned is not None and not isinstance(is_pinned, bool):
        raise MemoryValidationError(
            "invalid_is_pinned", "is_pinned must be a boolean"
        )

    now = _utc_now()
    db = await get_db(wl, "memory")
    try:
        await db.execute("BEGIN IMMEDIATE")
        row = await (
            await db.execute(
                """SELECT state, active_version, is_pinned, topic_id
                     FROM stable_facts
                    WHERE fact_id=? AND session_id=? AND identity_mode=?""",
                (fid, session, mode),
            )
        ).fetchone()
        if row is None or str(row["state"]) != "active":
            raise MemoryValidationError("fact_not_found", f"fact_id={fid}")
        current_pinned = bool(row["is_pinned"])
        new_pinned = current_pinned if is_pinned is None else bool(is_pinned)
        new_topic = row["topic_id"]
        if topic_id is not _UNSET:
            target = topic_id
            if target is not None:
                if not isinstance(target, str) or not str(target).strip():
                    raise MemoryValidationError(
                        "invalid_topic_id", "topic_id must be a non-empty string or null"
                    )
                exists = await (
                    await db.execute(
                        """SELECT 1 FROM memory_topics
                            WHERE topic_id=? AND session_id=? AND identity_mode=?""",
                        (str(target), session, mode),
                    )
                ).fetchone()
                if exists is None:
                    raise MemoryValidationError(
                        "topic_not_found", f"topic_id={target}"
                    )
            new_topic = target

        if new_pinned and not current_pinned:
            count_row = await (
                await db.execute(
                    """SELECT COUNT(*) AS n FROM stable_facts
                        WHERE session_id=? AND identity_mode=?
                          AND state='active' AND is_pinned=1""",
                    (session, mode),
                )
            ).fetchone()
            if int(count_row["n"]) >= MAX_PINNED_FACTS_PER_SCOPE:
                raise MemoryValidationError(
                    "pin_limit_reached",
                    f"max pinned facts per scope is {MAX_PINNED_FACTS_PER_SCOPE}",
                )

        await db.execute(
            """UPDATE stable_facts
               SET is_pinned=?, topic_id=?, updated_at=?
             WHERE fact_id=? AND session_id=? AND identity_mode=?""",
            (1 if new_pinned else 0, new_topic, now, fid, session, mode),
        )
        await db.commit()
    except Exception:
        await db.rollback()
        raise
    finally:
        await db.close()

    return {
        "fact_id": fid,
        "is_pinned": new_pinned,
        "topic_id": new_topic,
        "scope": {"session_id": session, "worldline": wl, "identity_mode": mode},
    }
