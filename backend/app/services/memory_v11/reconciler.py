"""Memory v11 one-call reconciler (S2 rework: single-connection batch transaction)."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from app.db import get_db, normalize_identity_mode, normalize_worldline
from app.services.memory_v11.contracts import (
    ALLOWED_OPS,
    AUTO_STABLE_CONFIDENCE,
    MemoryValidationError,
)
from app.services.memory_v11.indexing import (
    delete_version_embedding,
    insert_version_embedding,
    prepare_passage_embedding,
)
from app.services.memory_v11.repository import (
    _canonical_fingerprint,
    list_active_stable_facts,
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _obs_by_ref(observations: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for obs in observations:
        ref = str(obs.get("observation_ref") or "")
        if not ref:
            raise MemoryValidationError(
                "missing_observation_ref", "observation_ref required"
            )
        if ref in out:
            raise MemoryValidationError(
                "duplicate_observation_ref", f"duplicate observation_ref={ref}"
            )
        out[ref] = obs
    return out


def _validate_source_evidence(
    source_evidence: list[dict[str, Any]],
) -> dict[int, dict[str, Any]]:
    by_id: dict[int, dict[str, Any]] = {}
    for ev in source_evidence:
        role = str(ev.get("source_role") or "").lower()
        if role != "user":
            raise MemoryValidationError(
                "assistant_source_forbidden"
                if role == "assistant"
                else "source_role_not_user",
                f"source_role={role!r} cannot establish user evidence",
            )
        mid = ev.get("source_message_id")
        if mid is None:
            raise MemoryValidationError(
                "missing_source_message_id", "source_message_id required"
            )
        by_id[int(mid)] = ev
    return by_id


def _semantic_blob_and_fp(semantic: Any) -> tuple[str, str]:
    if isinstance(semantic, str):
        return semantic, _canonical_fingerprint({"raw": semantic})
    blob = json.dumps(
        semantic or {}, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return blob, _canonical_fingerprint(semantic or {})


async def _sources_already_applied(
    db: Any,
    *,
    session_id: str,
    identity_mode: str,
    source_message_ids: set[int],
) -> bool:
    """True if any listed source message already has an observation receipt in scope."""
    if not source_message_ids:
        return False
    for mid in source_message_ids:
        cur = await db.execute(
            """SELECT 1
                 FROM memory_observation_sources s
                 JOIN memory_observations o ON o.observation_id = s.observation_id
                WHERE s.source_message_id=?
                  AND o.session_id=?
                  AND o.identity_mode=?
                LIMIT 1""",
            (int(mid), session_id, identity_mode),
        )
        if await cur.fetchone() is not None:
            return True
    return False


async def _insert_observation(
    db: Any,
    *,
    session_id: str,
    identity_mode: str,
    obs: dict[str, Any],
    status: str,
    source_by_id: dict[int, dict[str, Any]],
) -> str:
    observation_id = str(uuid.uuid4())
    semantic_blob, fingerprint = _semantic_blob_and_fp(obs.get("semantic") or {})
    now = _utc_now()
    await db.execute(
        """INSERT INTO memory_observations(
               observation_id, session_id, identity_mode, display_text,
               semantic_json, semantic_fingerprint, evidence_kind, memory_class,
               confidence, topic_label_proposal, status, extractor_version,
               reanalysis_count, expires_at, created_at, updated_at
           ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,0,?,?,?)""",
        (
            observation_id,
            session_id,
            identity_mode,
            obs.get("display_text"),
            semantic_blob,
            fingerprint,
            str(obs.get("evidence_kind") or "direct_user"),
            str(obs.get("memory_class") or "stable_candidate"),
            float(obs.get("confidence") or 0.0),
            obs.get("topic_label"),
            status,
            "memory-v11-1",
            obs.get("expires_at"),
            now,
            now,
        ),
    )
    for mid in obs.get("source_message_ids") or []:
        mid_i = int(mid)
        ev = source_by_id.get(mid_i)
        if ev is None:
            raise MemoryValidationError(
                "unknown_source_message_id",
                f"source_message_id={mid_i} not in source_evidence",
            )
        await db.execute(
            """INSERT INTO memory_observation_sources(
                   observation_id, source_message_id, conversation_id,
                   source_role, source_fingerprint, source_created_at,
                   source_state, excerpt
               ) VALUES(?,?,?,?,?,?, 'present', ?)""",
            (
                observation_id,
                mid_i,
                str(ev.get("conversation_id") or ""),
                "user",
                str(ev.get("source_fingerprint") or ""),
                str(ev.get("source_created_at") or now),
                ev.get("excerpt"),
            ),
        )
    return observation_id


async def _link_evidence(
    db: Any,
    *,
    fact_id: str,
    version_no: int,
    observation_id: str,
    role: str = "primary",
) -> None:
    await db.execute(
        """INSERT OR IGNORE INTO stable_fact_evidence(
               fact_id, version_no, observation_id, evidence_role
           ) VALUES(?,?,?,?)""",
        (fact_id, int(version_no), observation_id, role),
    )


async def _create_fact_on_conn(
    db: Any,
    *,
    session_id: str,
    identity_mode: str,
    display_text: str,
    semantic: Any,
    confidence: float,
    change_kind: str = "create",
) -> dict[str, Any]:
    fact_id = str(uuid.uuid4())
    version_no = 1
    now = _utc_now()
    semantic_blob, fingerprint = _semantic_blob_and_fp(semantic)
    text = (display_text or "").strip()
    if not text:
        raise MemoryValidationError("empty_display_text", "display_text required")
    await db.execute(
        """INSERT INTO stable_facts(
               fact_id, session_id, identity_mode, topic_id, state,
               active_version, is_pinned, created_at, updated_at, deleted_at
           ) VALUES(?,?,?,NULL,'active',?,0,?,?,NULL)""",
        (fact_id, session_id, identity_mode, version_no, now, now),
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
            change_kind,
            now,
        ),
    )
    return {
        "fact_id": fact_id,
        "version_no": version_no,
        "display_text": text,
    }


async def _get_fact_active_version(
    db: Any,
    *,
    fact_id: str,
    session_id: str,
    identity_mode: str,
) -> int | None:
    cur = await db.execute(
        """SELECT active_version, state FROM stable_facts
            WHERE fact_id=? AND session_id=? AND identity_mode=?""",
        (fact_id, session_id, identity_mode),
    )
    row = await cur.fetchone()
    if row is None or str(row["state"]) != "active" or row["active_version"] is None:
        return None
    return int(row["active_version"])


def _will_create_stable_fact(obs: dict[str, Any]) -> bool:
    """True when CREATE will open a new stable fact (not candidate-only)."""
    evidence_kind = str(obs.get("evidence_kind") or "direct_user")
    memory_class = str(obs.get("memory_class") or "stable_candidate")
    confidence = float(obs.get("confidence") or 0.0)
    return (
        memory_class == "stable_candidate"
        and evidence_kind == "direct_user"
        and confidence >= AUTO_STABLE_CONFIDENCE
    )


def _op_fact_text(op: dict[str, Any], obs: dict[str, Any]) -> str:
    return str(op.get("fact_text") or obs.get("display_text") or "").strip()


async def apply_validated_memory_operations(
    *,
    session_id: str,
    worldline: str,
    identity_mode: str,
    observations: list[dict[str, Any]],
    operations: list[dict[str, Any]],
    source_evidence: list[dict[str, Any]],
    candidate_allowlist: list[str],
    embedder: Any | None = None,
) -> dict[str, Any]:
    """Validate then apply all operations in ONE connection / ONE transaction.

    Passage embeddings for CREATE / REFINE / SUPERSEDE are prepared *before*
    BEGIN IMMEDIATE. Indexing failure aborts the whole batch with no residue.
    Source replay is idempotent via observation-source receipts.
    """
    wl = normalize_worldline(worldline)
    mode = normalize_identity_mode(identity_mode)
    session = (session_id or "").strip()
    if not session:
        raise MemoryValidationError("empty_session_id", "session_id required")

    source_by_id = _validate_source_evidence(source_evidence)
    obs_map = _obs_by_ref(list(observations or []))
    allow = {str(x) for x in (candidate_allowlist or []) if x}
    consumed: set[str] = set()
    created_facts: list[dict[str, Any]] = []

    all_source_ids: set[int] = set(source_by_id.keys())
    for obs in obs_map.values():
        for mid in obs.get("source_message_ids") or []:
            all_source_ids.add(int(mid))

    # Pre-validate ops (no writes).
    for op in operations or []:
        op_name = str(op.get("op") or "").upper()
        if op_name not in ALLOWED_OPS:
            raise MemoryValidationError("invalid_op", f"op={op_name!r}")
        ref = str(op.get("observation_ref") or "")
        if ref not in obs_map:
            raise MemoryValidationError(
                "unknown_observation_ref", f"observation_ref={ref}"
            )
        if ref in consumed:
            raise MemoryValidationError(
                "observation_multi_consumed",
                f"observation_ref={ref} used by multiple ops",
            )
        consumed.add(ref)
        target = op.get("target_fact_id")
        if op_name in {"ATTACH", "REFINE", "SUPERSEDE"}:
            if not target:
                raise MemoryValidationError(
                    "missing_target_fact_id", f"{op_name} requires target_fact_id"
                )
            if str(target) not in allow:
                raise MemoryValidationError(
                    "target_not_in_allowlist",
                    f"target_fact_id={target} not on allowlist",
                )
        for mid in obs_map[ref].get("source_message_ids") or []:
            if int(mid) not in source_by_id:
                raise MemoryValidationError(
                    "unknown_source_message_id",
                    f"source_message_id={mid} not in source_evidence",
                )

    # Short read-only replay precheck: avoid heavyweight encode on pure replays.
    db_pre = await get_db(wl, "memory")
    try:
        if await _sources_already_applied(
            db_pre,
            session_id=session,
            identity_mode=mode,
            source_message_ids=all_source_ids,
        ):
            active = await list_active_stable_facts(
                session_id=session, worldline=wl, identity_mode=mode
            )
            return {
                "created_facts": [],
                "active_facts": active,
                "session_id": session,
                "worldline": wl,
                "identity_mode": mode,
                "replay": True,
            }
    finally:
        await db_pre.close()

    # Plan embeddings for ops that open a new fact version (outside write lock).
    prepared_by_op: dict[int, dict[str, Any]] = {}
    for index, op in enumerate(operations or []):
        op_name = str(op.get("op") or "").upper()
        ref = str(op.get("observation_ref") or "")
        obs = obs_map[ref]
        if op_name == "CREATE" and _will_create_stable_fact(obs):
            prepared_by_op[index] = await prepare_passage_embedding(
                _op_fact_text(op, obs),
                embedder=embedder,
            )
        elif op_name in {"REFINE", "SUPERSEDE"}:
            prepared_by_op[index] = await prepare_passage_embedding(
                _op_fact_text(op, obs),
                embedder=embedder,
            )
        # ATTACH / REJECT / candidate-only CREATE: no embedding.

    db = await get_db(wl, "memory")
    try:
        await db.execute("BEGIN IMMEDIATE")

        # Final replay guard under write lock (race-safe).
        if await _sources_already_applied(
            db,
            session_id=session,
            identity_mode=mode,
            source_message_ids=all_source_ids,
        ):
            await db.commit()
            active = await list_active_stable_facts(
                session_id=session, worldline=wl, identity_mode=mode
            )
            return {
                "created_facts": [],
                "active_facts": active,
                "session_id": session,
                "worldline": wl,
                "identity_mode": mode,
                "replay": True,
            }

        for index, op in enumerate(operations or []):
            op_name = str(op.get("op") or "").upper()
            ref = str(op.get("observation_ref") or "")
            obs = obs_map[ref]
            evidence_kind = str(obs.get("evidence_kind") or "direct_user")
            memory_class = str(obs.get("memory_class") or "stable_candidate")
            confidence = float(obs.get("confidence") or 0.0)
            fact_text = _op_fact_text(op, obs)

            if op_name == "REJECT":
                await _insert_observation(
                    db,
                    session_id=session,
                    identity_mode=mode,
                    obs=obs,
                    status="rejected",
                    source_by_id=source_by_id,
                )
                continue

            if op_name == "CREATE":
                if not _will_create_stable_fact(obs):
                    await _insert_observation(
                        db,
                        session_id=session,
                        identity_mode=mode,
                        obs=obs,
                        status="candidate",
                        source_by_id=source_by_id,
                    )
                    continue

                prepared = prepared_by_op.get(index)
                if prepared is None:
                    raise MemoryValidationError(
                        "embedding_missing",
                        "CREATE stable fact requires a prepared passage embedding",
                    )
                created = await _create_fact_on_conn(
                    db,
                    session_id=session,
                    identity_mode=mode,
                    display_text=fact_text,
                    semantic=obs.get("semantic") or {},
                    confidence=confidence,
                    change_kind="create",
                )
                await insert_version_embedding(
                    db,
                    fact_id=created["fact_id"],
                    version_no=int(created["version_no"]),
                    prepared=prepared,
                )
                observation_id = await _insert_observation(
                    db,
                    session_id=session,
                    identity_mode=mode,
                    obs=obs,
                    status="attached",
                    source_by_id=source_by_id,
                )
                await _link_evidence(
                    db,
                    fact_id=created["fact_id"],
                    version_no=int(created["version_no"]),
                    observation_id=observation_id,
                    role="primary",
                )
                created_facts.append(created)
                allow.add(str(created["fact_id"]))
                continue

            if op_name == "ATTACH":
                target = str(op.get("target_fact_id"))
                expected = op.get("expected_version")
                active_version = await _get_fact_active_version(
                    db, fact_id=target, session_id=session, identity_mode=mode
                )
                if active_version is None:
                    raise MemoryValidationError(
                        "fact_not_found", f"target_fact_id={target}"
                    )
                if expected is not None and int(expected) != active_version:
                    raise MemoryValidationError(
                        "expected_version_mismatch",
                        f"expected={expected} active={active_version}",
                    )
                observation_id = await _insert_observation(
                    db,
                    session_id=session,
                    identity_mode=mode,
                    obs=obs,
                    status="attached",
                    source_by_id=source_by_id,
                )
                await _link_evidence(
                    db,
                    fact_id=target,
                    version_no=active_version,
                    observation_id=observation_id,
                    role="supporting",
                )
                continue

            if op_name in {"REFINE", "SUPERSEDE"}:
                target = str(op.get("target_fact_id"))
                expected = op.get("expected_version")
                change_kind = "refine" if op_name == "REFINE" else "supersede"
                prepared = prepared_by_op.get(index)
                if prepared is None:
                    raise MemoryValidationError(
                        "embedding_missing",
                        f"{op_name} requires a prepared passage embedding",
                    )
                active_version = await _get_fact_active_version(
                    db, fact_id=target, session_id=session, identity_mode=mode
                )
                if active_version is None:
                    raise MemoryValidationError(
                        "fact_not_found", f"target_fact_id={target}"
                    )
                if expected is not None and int(expected) != active_version:
                    raise MemoryValidationError(
                        "expected_version_mismatch",
                        f"expected={expected} active={active_version}",
                    )
                now = _utc_now()
                new_version = active_version + 1
                semantic_blob, fingerprint = _semantic_blob_and_fp(
                    obs.get("semantic") or {}
                )
                await db.execute(
                    """UPDATE stable_fact_versions
                       SET invalid_at=?
                     WHERE fact_id=? AND version_no=? AND invalid_at IS NULL""",
                    (now, target, active_version),
                )
                await db.execute(
                    """INSERT INTO stable_fact_versions(
                           fact_id, version_no, display_text, semantic_json,
                           semantic_fingerprint, confidence, change_kind,
                           previous_version, valid_from, invalid_at, created_by_job_id
                       ) VALUES(?,?,?,?,?,?,?,?,?,NULL,NULL)""",
                    (
                        target,
                        new_version,
                        fact_text,
                        semantic_blob,
                        fingerprint,
                        confidence,
                        change_kind,
                        active_version,
                        now,
                    ),
                )
                await db.execute(
                    """UPDATE stable_facts
                       SET active_version=?, updated_at=?
                     WHERE fact_id=? AND session_id=? AND identity_mode=?""",
                    (new_version, now, target, session, mode),
                )
                # Old vector must not remain indexable; new vector is required.
                await delete_version_embedding(
                    db, fact_id=target, version_no=active_version
                )
                await insert_version_embedding(
                    db,
                    fact_id=target,
                    version_no=new_version,
                    prepared=prepared,
                )
                observation_id = await _insert_observation(
                    db,
                    session_id=session,
                    identity_mode=mode,
                    obs=obs,
                    status="attached",
                    source_by_id=source_by_id,
                )
                await _link_evidence(
                    db,
                    fact_id=target,
                    version_no=new_version,
                    observation_id=observation_id,
                    role="primary",
                )
                continue

        await db.commit()
    except Exception:
        await db.rollback()
        raise
    finally:
        await db.close()

    active = await list_active_stable_facts(
        session_id=session, worldline=wl, identity_mode=mode
    )
    return {
        "created_facts": created_facts,
        "active_facts": active,
        "session_id": session,
        "worldline": wl,
        "identity_mode": mode,
        "replay": False,
    }
