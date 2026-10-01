"""Memory v11 one-call reconciler (S2 rework: single-connection batch transaction)."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Mapping

from app.db import get_db, normalize_identity_mode, normalize_worldline
from app.services.memory_v11.contracts import (
    ALLOWED_OPS,
    AUTO_STABLE_CONFIDENCE,
    PIPELINE_VERSION,
    MemoryValidationError,
)
from app.services.memory_v11.experiences import insert_experience
from app.services.memory_v11.indexing import (
    delete_version_embedding,
    insert_version_embedding,
    prepare_passage_embedding,
)
from app.services.memory_v11.receipts import (
    RECEIPT_KIND_NONEMPTY,
    has_ingest_receipt,
    insert_ingest_receipt,
)
from app.services.memory_v11.repository import (
    _canonical_fingerprint,
    list_active_stable_facts,
)
from app.services.memory_v11.topics import (
    prepare_topic_label,
    resolve_or_create_topic,
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


async def _source_receipt_already_applied(
    db: Any,
    *,
    session_id: str,
    conversation_id: str,
    identity_mode: str,
    source_message_id: int,
    pipeline_version: str,
) -> bool:
    """S3F-1 D28: dedicated full-cursor receipt OR legacy observation proof.

    The legacy observation-source fallback (rows created before the receipt
    table existed) requires the conversation_id too — old source rows already
    store it — and is valid ONLY for the current PIPELINE_VERSION. A future
    pipeline must never be suppressed by a legacy receipt. No backfill.
    """
    if await has_ingest_receipt(
        db,
        session_id=session_id,
        conversation_id=conversation_id,
        identity_mode=identity_mode,
        source_message_id=int(source_message_id),
        pipeline_version=pipeline_version,
    ):
        return True
    if pipeline_version != PIPELINE_VERSION:
        return False
    row = await (
        await db.execute(
            """SELECT 1
                 FROM memory_observation_sources s
                 JOIN memory_observations o ON o.observation_id = s.observation_id
                WHERE s.source_message_id=? AND s.conversation_id=?
                  AND o.session_id=? AND o.identity_mode=?
                LIMIT 1""",
            (int(source_message_id), conversation_id, session_id, identity_mode),
        )
    ).fetchone()
    return row is not None


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
               ) VALUES(?,?,?, 'user', ?, ?, 'present', ?)""",
            (
                observation_id,
                mid_i,
                str(ev.get("conversation_id") or ""),
                str(ev.get("source_fingerprint") or ""),
                str(ev.get("source_created_at") or now),
                ev.get("excerpt"),
            ),
        )
    return observation_id


def _require_target_core_fields(obs: dict[str, Any]) -> None:
    """P1-A: a targeted rewrite must carry explicit core fields.

    Absent core fields must never silently rewrite meaningful existing state
    to defaults (display_text -> '', semantic -> {}, confidence -> 0.0 ...).
    """
    missing = [
        field
        for field in (
            "display_text",
            "semantic",
            "evidence_kind",
            "memory_class",
            "confidence",
            "source_message_ids",
        )
        if field not in obs or obs[field] is None
    ]
    if missing:
        raise MemoryValidationError(
            "reanalysis_target_incomplete",
            f"target rewrite missing required fields: {sorted(missing)}",
        )
    if not str(obs["display_text"] or "").strip():
        raise MemoryValidationError(
            "reanalysis_target_incomplete", "display_text must be non-empty"
        )
    if not isinstance(obs["semantic"], dict):
        raise MemoryValidationError(
            "reanalysis_target_incomplete", "semantic must be a JSON object"
        )
    if not isinstance(obs["evidence_kind"], str) or not isinstance(
        obs["memory_class"], str
    ):
        raise MemoryValidationError(
            "reanalysis_target_incomplete",
            "evidence_kind and memory_class must be strings",
        )
    if isinstance(obs["confidence"], bool) or not isinstance(
        obs["confidence"], (int, float)
    ):
        raise MemoryValidationError(
            "reanalysis_target_incomplete", "confidence must be numeric"
        )
    if not isinstance(obs["source_message_ids"], list) or not obs[
        "source_message_ids"
    ]:
        raise MemoryValidationError(
            "reanalysis_target_incomplete",
            "source_message_ids must be a non-empty list",
        )


async def materialize_observation(
    db: Any,
    *,
    session_id: str,
    identity_mode: str,
    obs: dict[str, Any],
    status: str,
    source_by_id: dict[int, dict[str, Any]],
    reanalysis_allow: set[str],
) -> str:
    """Insert a new observation OR reanalyze an existing D18 target.

    D18 target path (SAME transaction): preserve observation_id / created_at /
    all old source rows; recompute display/semantic fields and the canonical
    semantic fingerprint; set the branch-requested status; reanalysis_count
    0→1; append the CURRENT source row. No parallel observation row.

    Fail-closed re-checks under the write lock (race-safe): target still
    candidate + reanalysis_count=0, and no illegal stable_fact_evidence or
    experiences backing. Structural corruption → reanalysis_target_conflict.
    """
    target = obs.get("reanalysis_target_observation_id")
    if target is None or str(target).strip() == "":
        return await _insert_observation(
            db,
            session_id=session_id,
            identity_mode=identity_mode,
            obs=obs,
            status=status,
            source_by_id=source_by_id,
        )
    target_id = str(target).strip()
    if target_id not in reanalysis_allow:
        raise MemoryValidationError(
            "reanalysis_target_not_allowed",
            f"reanalysis_target_observation_id={target_id} not on allowlist",
        )
    state = await (
        await db.execute(
            """SELECT status, reanalysis_count, created_at, expires_at,
                      topic_label_proposal FROM memory_observations
                WHERE observation_id=? AND session_id=? AND identity_mode=?""",
            (target_id, session_id, identity_mode),
        )
    ).fetchone()
    if (
        state is None
        or str(state["status"]) != "candidate"
        or int(state["reanalysis_count"] or 0) != 0
    ):
        raise MemoryValidationError(
            "reanalysis_target_stale", f"target={target_id} no longer unresolved"
        )
    evidence = await (
        await db.execute(
            """SELECT COUNT(*) AS n FROM stable_fact_evidence
                WHERE observation_id=?""",
            (target_id,),
        )
    ).fetchone()
    experiences = await (
        await db.execute(
            """SELECT COUNT(*) AS n FROM experiences
                WHERE observation_id=?""",
            (target_id,),
        )
    ).fetchone()
    if int(evidence["n"]) > 0 or int(experiences["n"]) > 0:
        raise MemoryValidationError(
            "reanalysis_target_conflict",
            f"target={target_id} illegally backs stable fact or experience data",
        )
    # P1-A: explicit core fields required for an existing-row rewrite.
    _require_target_core_fields(obs)
    semantic_blob, fingerprint = _semantic_blob_and_fp(obs["semantic"])
    # Three-state optional fields: ABSENT preserves, null clears, value replaces.
    new_expires_at = state["expires_at"]
    if "expires_at" in obs:
        new_expires_at = obs["expires_at"]
    new_topic = state["topic_label_proposal"]
    if "topic_label" in obs:
        new_topic = obs["topic_label"]
    now = _utc_now()
    await db.execute(
        """UPDATE memory_observations
           SET display_text=?, semantic_json=?, semantic_fingerprint=?,
               evidence_kind=?, memory_class=?, confidence=?,
               topic_label_proposal=?, expires_at=?, status=?,
               reanalysis_count=1, updated_at=?
         WHERE observation_id=?""",
        (
            obs["display_text"],
            semantic_blob,
            fingerprint,
            str(obs["evidence_kind"]),
            str(obs["memory_class"]),
            float(obs["confidence"]),
            new_topic,
            new_expires_at,
            status,
            now,
            target_id,
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
               ) VALUES(?,?,?, 'user', ?, ?, 'present', ?)""",
            (
                target_id,
                mid_i,
                str(ev.get("conversation_id") or ""),
                str(ev.get("source_fingerprint") or ""),
                str(ev.get("source_created_at") or now),
                ev.get("excerpt"),
            ),
        )
    return target_id


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
    topic_id: str | None = None,
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
           ) VALUES(?,?,?,?, 'active', ?, 0, ?, ?, NULL)""",
        (fact_id, session_id, identity_mode, topic_id, version_no, now, now),
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


def _stable_truth_eligible(obs: dict[str, Any]) -> bool:
    """S3E-1 P1-B: the single evidence authority for stable-truth writes.

    REFINE/SUPERSEDE may only rewrite stable truth when the observation meets
    the SAME predicate as an automatic stable CREATE (stable_candidate +
    direct_user + confidence >= AUTO_STABLE_CONFIDENCE). reason_code is
    provider-supplied explanatory text with ZERO write authority.
    """
    evidence_kind = str(obs.get("evidence_kind") or "direct_user")
    memory_class = str(obs.get("memory_class") or "stable_candidate")
    confidence = float(obs.get("confidence") or 0.0)
    return (
        memory_class == "stable_candidate"
        and evidence_kind == "direct_user"
        and confidence >= AUTO_STABLE_CONFIDENCE
    )


def _will_create_stable_fact(obs: dict[str, Any]) -> bool:
    """True when CREATE will open a new stable fact (not candidate-only)."""
    return _stable_truth_eligible(obs)


def _require_expected_version(op: dict[str, Any], op_name: str) -> int:
    """S3E-1 P1-A: ATTACH/REFINE/SUPERSEDE require an explicit version snapshot.

    Key ABSENT or None → missing_expected_version (mandatory).
    bool / non-int / <= 0 → invalid_expected_version.
    Never accept None, "1", 1.0, True, False, 0, -1.
    """
    expected = op.get("expected_version")
    if expected is None:
        raise MemoryValidationError(
            "missing_expected_version", f"{op_name} requires expected_version"
        )
    if isinstance(expected, bool) or not isinstance(expected, int) or expected <= 0:
        raise MemoryValidationError(
            "invalid_expected_version",
            f"{op_name} expected_version must be a positive integer, "
            f"got {expected!r}",
        )
    return expected


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
    reanalysis_allowlist: list[str] | None = None,
    processing_receipt: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate then apply all operations in ONE connection / ONE transaction.

    Passage embeddings for CREATE / REFINE / SUPERSEDE are prepared *before*
    BEGIN IMMEDIATE. Indexing failure aborts the whole batch with no residue.
    Source replay is idempotent via observation-source receipts.

    D18 (S3D-4): ``reanalysis_allowlist`` freezes the offered unresolved
    observation ids; it is optional and backward compatible — None → empty.

    S3F-1 (D28): ``processing_receipt`` is optional live-job metadata
    (conversation_id / source_message_id / pipeline_version). When supplied:
    - the replay guards (pre-embedding read-only + final under BEGIN IMMEDIATE)
      consult the dedicated full-cursor receipt OR the current-pipeline legacy
      observation proof;
    - a ``provider_nonempty`` receipt is inserted INSIDE this transaction after
      all semantic writes and before the single commit. If the insert fails the
      whole transaction rolls back — no semantic residue, no receipt.
    Existing direct callers that pass no receipt metadata are unchanged.
    """
    wl = normalize_worldline(worldline)
    mode = normalize_identity_mode(identity_mode)
    session = (session_id or "").strip()
    if not session:
        raise MemoryValidationError("empty_session_id", "session_id required")

    source_by_id = _validate_source_evidence(source_evidence)
    obs_map = _obs_by_ref(list(observations or []))
    allow = {str(x) for x in (candidate_allowlist or []) if x}
    reanalysis_allow = {str(x) for x in (reanalysis_allowlist or []) if x}
    consumed: set[str] = set()
    used_targets: dict[str, str] = {}
    created_facts: list[dict[str, Any]] = []

    all_source_ids: set[int] = set(source_by_id.keys())
    for obs in obs_map.values():
        for mid in obs.get("source_message_ids") or []:
            all_source_ids.add(int(mid))

    # Pre-validate ops (no writes).
    expected_by_op: dict[int, int] = {}
    for index, op in enumerate(operations or []):
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
            # S3C-1: episodic observations may only become experiences; they can
            # never mutate a stable fact, even if target_fact_id happens to exist.
            obs_memory_class = str(
                obs_map[ref].get("memory_class") or "stable_candidate"
            )
            if obs_memory_class == "episodic":
                raise MemoryValidationError(
                    "episodic_target_operation_forbidden",
                    f"{op_name} cannot target a stable fact from an episodic "
                    "observation",
                )
            # S3E-1 P1-A: every fact-mutating op needs an explicit positive int
            # snapshot. Key ABSENT / None → missing; bool/non-int/<=0 → invalid.
            # This closes the hole where an omitted expected_version silently
            # skipped the version equality check.
            expected_by_op[index] = _require_expected_version(op, op_name)
            # S3E-1 P1-B: REFINE/SUPERSEDE may only rewrite stable truth backed
            # by the same evidence authority as an automatic stable CREATE.
            # reason_code stays provider explanation with zero write authority.
            if op_name in {"REFINE", "SUPERSEDE"} and not _stable_truth_eligible(
                obs_map[ref]
            ):
                raise MemoryValidationError(
                    "fact_mutation_not_eligible",
                    f"{op_name} requires stable_candidate + direct_user + "
                    f"confidence>={AUTO_STABLE_CONFIDENCE}",
                )
        for mid in obs_map[ref].get("source_message_ids") or []:
            if int(mid) not in source_by_id:
                raise MemoryValidationError(
                    "unknown_source_message_id",
                    f"source_message_id={mid} not in source_evidence",
                )
        # D18: reanalysis target must be frozen, single per message, and
        # never duplicated across output observations.
        target_obs_id = obs_map[ref].get("reanalysis_target_observation_id")
        if target_obs_id is not None and str(target_obs_id).strip() != "":
            tid = str(target_obs_id).strip()
            if tid not in reanalysis_allow:
                raise MemoryValidationError(
                    "reanalysis_target_not_allowed",
                    f"reanalysis_target_observation_id={tid} not on allowlist",
                )
            if tid in used_targets:
                raise MemoryValidationError(
                    "reanalysis_target_reused",
                    f"reanalysis target {tid} used by multiple observations",
                )
            used_targets[tid] = ref

    if len(used_targets) > 1:
        raise MemoryValidationError(
            "reanalysis_target_reused",
            "at most one distinct reanalysis target per processed message",
        )
    # P0: a targeted observation must be consumed by exactly one operation.
    for ref, obs in obs_map.items():
        target_obs_id = obs.get("reanalysis_target_observation_id")
        if target_obs_id is not None and str(target_obs_id).strip() != "":
            if ref not in consumed:
                raise MemoryValidationError(
                    "reanalysis_target_unconsumed",
                    f"observation_ref={ref} targets an observation but has no "
                    "operation",
                )

    # Short read-only replay precheck: avoid heavyweight encode on pure replays.
    db_pre = await get_db(wl, "memory")
    try:
        if processing_receipt is not None:
            already = await _source_receipt_already_applied(
                db_pre,
                session_id=session,
                conversation_id=processing_receipt["conversation_id"],
                identity_mode=mode,
                source_message_id=int(processing_receipt["source_message_id"]),
                pipeline_version=processing_receipt["pipeline_version"],
            )
        else:
            already = await _sources_already_applied(
                db_pre,
                session_id=session,
                identity_mode=mode,
                source_message_ids=all_source_ids,
            )
        if already:
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
        from app.services.conversation_erasure import sources_erased
        if await sources_erased(db, session_id=session, identity_mode=mode,
                                source_message_ids=list(all_source_ids),
                                conversation_id=processing_receipt.get('conversation_id') if processing_receipt else None):
            raise MemoryValidationError('source_erased', 'source history has been erased')
        if processing_receipt is not None:
            already = await _source_receipt_already_applied(
                db,
                session_id=session,
                conversation_id=processing_receipt["conversation_id"],
                identity_mode=mode,
                source_message_id=int(processing_receipt["source_message_id"]),
                pipeline_version=processing_receipt["pipeline_version"],
            )
        else:
            already = await _sources_already_applied(
                db,
                session_id=session,
                identity_mode=mode,
                source_message_ids=all_source_ids,
            )
        if already:
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
                await materialize_observation(
                    db,
                    session_id=session,
                    identity_mode=mode,
                    obs=obs,
                    status="rejected",
                    source_by_id=source_by_id,
                    reanalysis_allow=reanalysis_allow,
                )
                continue

            if op_name == "CREATE":
                obs_memory_class = str(obs.get("memory_class") or "stable_candidate")
                if obs_memory_class == "episodic":
                    # S3C-1: episodic CREATE -> first-class experience only.
                    # No stable_facts / stable_fact_versions / embedding rows.
                    observation_id = await materialize_observation(
                        db,
                        session_id=session,
                        identity_mode=mode,
                        obs=obs,
                        status="attached",
                        source_by_id=source_by_id,
                        reanalysis_allow=reanalysis_allow,
                    )
                    source_ids = [int(m) for m in (obs.get("source_message_ids") or [])]
                    conversation_id = (
                        str(source_by_id[source_ids[0]].get("conversation_id") or "")
                        if source_ids
                        else ""
                    )
                    # Episodic expiry alignment: the Experience must inherit the
                    # EFFECTIVE persisted observation expiry after materialization
                    # (three-state D18 semantics), never the raw model field.
                    exp_row = await (
                        await db.execute(
                            """SELECT expires_at FROM memory_observations
                                WHERE observation_id=? AND session_id=? AND identity_mode=?""",
                            (observation_id, session, mode),
                        )
                    ).fetchone()
                    if exp_row is None:
                        raise MemoryValidationError(
                            "observation_not_found",
                            f"observation_id={observation_id} missing after materialization",
                        )
                    await insert_experience(
                        db,
                        session_id=session,
                        conversation_id=conversation_id,
                        identity_mode=mode,
                        observation_id=observation_id,
                        display_text=fact_text,
                        semantic=obs.get("semantic") or {},
                        confidence=confidence,
                        expires_at=exp_row["expires_at"],
                    )
                    continue
                if not _will_create_stable_fact(obs):
                    await materialize_observation(
                        db,
                        session_id=session,
                        identity_mode=mode,
                        obs=obs,
                        status="candidate",
                        source_by_id=source_by_id,
                        reanalysis_allow=reanalysis_allow,
                    )
                    continue

                prepared = prepared_by_op.get(index)
                if prepared is None:
                    raise MemoryValidationError(
                        "embedding_missing",
                        "CREATE stable fact requires a prepared passage embedding",
                    )
                # S3D-2: CREATE-only automatic topic materialization. Only a
                # stable fact's first CREATE may use the model topic_label;
                # resolution/reuse/create happens inside THIS transaction so a
                # later failure rolls the topic back with the fact batch.
                fact_topic_id: str | None = None
                prepared_label = prepare_topic_label(obs.get("topic_label"))
                if prepared_label is not None:
                    label_display, label_normalized = prepared_label
                    fact_topic_id = await resolve_or_create_topic(
                        db,
                        session_id=session,
                        identity_mode=mode,
                        display_label=label_display,
                        normalized_label=label_normalized,
                    )
                created = await _create_fact_on_conn(
                    db,
                    session_id=session,
                    identity_mode=mode,
                    display_text=fact_text,
                    semantic=obs.get("semantic") or {},
                    confidence=confidence,
                    change_kind="create",
                    topic_id=fact_topic_id,
                )
                await insert_version_embedding(
                    db,
                    fact_id=created["fact_id"],
                    version_no=int(created["version_no"]),
                    prepared=prepared,
                )
                observation_id = await materialize_observation(
                    db,
                    session_id=session,
                    identity_mode=mode,
                    obs=obs,
                    status="attached",
                    source_by_id=source_by_id,
                    reanalysis_allow=reanalysis_allow,
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
                expected = expected_by_op[index]
                active_version = await _get_fact_active_version(
                    db, fact_id=target, session_id=session, identity_mode=mode
                )
                if active_version is None:
                    raise MemoryValidationError(
                        "fact_not_found", f"target_fact_id={target}"
                    )
                if expected != active_version:
                    raise MemoryValidationError(
                        "expected_version_mismatch",
                        f"expected={expected} active={active_version}",
                    )
                observation_id = await materialize_observation(
                    db,
                    session_id=session,
                    identity_mode=mode,
                    obs=obs,
                    status="attached",
                    source_by_id=source_by_id,
                    reanalysis_allow=reanalysis_allow,
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
                expected = expected_by_op[index]
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
                if expected != active_version:
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
                observation_id = await materialize_observation(
                    db,
                    session_id=session,
                    identity_mode=mode,
                    obs=obs,
                    status="attached",
                    source_by_id=source_by_id,
                    reanalysis_allow=reanalysis_allow,
                )
                await _link_evidence(
                    db,
                    fact_id=target,
                    version_no=new_version,
                    observation_id=observation_id,
                    role="primary",
                )
                continue

        # S3F-1 D28: durable provider_nonempty receipt INSIDE this transaction,
        # after all semantic operations succeeded and BEFORE the single commit.
        # If the receipt insert fails, the whole transaction rolls back (no
        # semantic residue, no receipt). provider_nonempty means the validated
        # provider result was processed — it does NOT promise a stable fact
        # necessarily changed.
        if processing_receipt is not None:
            await insert_ingest_receipt(
                db,
                session_id=session,
                conversation_id=processing_receipt["conversation_id"],
                identity_mode=mode,
                source_message_id=int(processing_receipt["source_message_id"]),
                pipeline_version=processing_receipt["pipeline_version"],
                receipt_kind=RECEIPT_KIND_NONEMPTY,
            )
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
