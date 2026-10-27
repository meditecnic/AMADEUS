"""Memory v11 durable ingest jobs (S2 rework).

Lease ownership is mandatory for process/complete/fail/cancel.
Provider failures enter bounded retry; completed jobs are not re-applied.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Mapping

from app.db import get_db, normalize_identity_mode, normalize_worldline
from app.services.memory_v11.contracts import (
    LEASE_SECONDS,
    MAX_AUTO_RETRIES,
    MemoryJobErrorCode,
    PIPELINE_VERSION,
)
from app.services.memory_v11.privacy import (
    classify_memory_sensitive_text,
    find_memory_sensitive_matches,
    sensitive_spans,
)

MemoryCompletionFn = Callable[..., Awaitable[Mapping[str, Any]]]

logger = logging.getLogger(__name__)

PREVIOUS_ASSISTANT_MAX_CHARS = 600

_worker_task: asyncio.Task | None = None
_worker_stop: asyncio.Event | None = None


class LeaseLostError(RuntimeError):
    """Worker no longer holds the processing lease."""


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime | None = None) -> str:
    return (dt or _utc_now()).isoformat()


def _row_to_job(row: Any, *, worldline: str) -> dict[str, Any]:
    return {
        "job_id": row["job_id"],
        "session_id": row["session_id"],
        "conversation_id": row["conversation_id"],
        "identity_mode": row["identity_mode"],
        "source_message_id": int(row["source_message_id"]),
        "pipeline_version": row["pipeline_version"],
        "provider_id": row["provider_id"],
        "model_id": row["model_id"],
        "state": row["state"],
        "attempt_count": int(row["attempt_count"] or 0),
        "retryable": bool(int(row["retryable"] if row["retryable"] is not None else 1)),
        "next_attempt_at": row["next_attempt_at"],
        "lease_owner": row["lease_owner"],
        "lease_expires_at": row["lease_expires_at"],
        "last_error_code": row["last_error_code"],
        "last_error_hash": row["last_error_hash"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "completed_at": row["completed_at"],
        "worldline": worldline,
    }


def redact_user_text(text: str) -> tuple[str, bool]:
    """Mask locally; bool means masking failed and the whole input is blocked.

    Sensitive offsets/values stay inside this call. Only masked text may enter
    completion, source fingerprint or excerpt. Any detector/replacement failure
    completes the job without provider calls or Memory writes.
    """
    if not text:
        return "", False
    try:
        classes = classify_memory_sensitive_text(text)
        matches = find_memory_sensitive_matches(text)
        spans = sorted(sensitive_spans(text))
        if classes and not classes.issubset({category for _, _, category in spans}):
            return "[REDACTED]", True
        merged: list[tuple[int, int, str]] = []
        for start, end, category in spans:
            if not 0 <= start < end <= len(text):
                return "[REDACTED]", True
            if merged and start < merged[-1][1]:
                left, right, previous_class = merged[-1]
                merged[-1] = (left, max(right, end), previous_class)
            else:
                merged.append((start, end, category))
        if any(not any(left <= start and end <= right for left, right, _ in merged)
               for start, end, _ in matches):
            return "[REDACTED]", True
        redacted = text
        for start, end, category in reversed(merged):
            redacted = redacted[:start] + f"[REDACTED:{category}]" + redacted[end:]
        if classify_memory_sensitive_text(redacted) or find_memory_sensitive_matches(redacted):
            return "[REDACTED]", True
        return redacted, False
    except Exception:
        return "[REDACTED]", True


_MISSING = object()


def _canonical_completion_array(
    raw: Any, field: str, maximum: int
) -> list[dict[str, Any]]:
    """S3F-2A: one completion array — container type, item type, hard count.

    - _MISSING (field ABSENT) → canonical empty list (compat with injected
      completion callbacks and the S3F-1 empty-result seams);
    - field PRESENT must be a Python list — never coerced via list();
    - every item must be a Mapping (canonicalized to a plain dict);
    - len <= maximum (exact cap accepted, cap+1 rejected).
    Semantic rules stay owned by the reconciler.
    """
    if raw is _MISSING:
        return []
    if not isinstance(raw, list):
        raise ValueError(f"completion field {field} must be a list")
    items: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, Mapping):
            raise ValueError(f"completion field {field} items must be objects")
        items.append(dict(item))
    if len(items) > maximum:
        raise ValueError(f"completion field {field} exceeds {maximum} items")
    return items


def _validate_completion_collections(
    completion_payload: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """S3F-2A: deterministic container/item/count validation of provider output.

    Runs right after the outer Mapping check and BEFORE any receipt /
    source_evidence / reconciler work. Raises ValueError on shape/count
    violations; the caller transitions to failed(validation_failed).
    """
    from app.services.memory_v11.contracts import (
        MAX_MEMORY_OBSERVATIONS_PER_MESSAGE,
        MAX_MEMORY_OPERATIONS_PER_MESSAGE,
    )

    observations = _canonical_completion_array(
        completion_payload.get("observations", _MISSING),
        "observations",
        MAX_MEMORY_OBSERVATIONS_PER_MESSAGE,
    )
    operations = _canonical_completion_array(
        completion_payload.get("operations", _MISSING),
        "operations",
        MAX_MEMORY_OPERATIONS_PER_MESSAGE,
    )
    return observations, operations


async def enqueue_memory_job(
    *,
    session_id: str,
    worldline: str,
    conversation_id: str,
    identity_mode: str,
    source_message_id: int,
    pipeline_version: str = PIPELINE_VERSION,
    provider_id: str,
    model_id: str,
) -> dict[str, Any]:
    """Idempotent enqueue under concurrent callers.

    Atomic INSERT … ON CONFLICT DO NOTHING on the source-cursor unique index,
    then SELECT the surviving row so all racers share one job_id.
    """
    wl = normalize_worldline(worldline)
    mode = normalize_identity_mode(identity_mode)
    now = _iso()
    job_id = str(uuid.uuid4())
    db = await get_db(wl, "memory")
    try:
        await db.execute("BEGIN IMMEDIATE")
        await db.execute(
            """INSERT INTO memory_ingest_jobs(
                   job_id, session_id, conversation_id, identity_mode,
                   source_message_id, pipeline_version, provider_id, model_id,
                   state, attempt_count, next_attempt_at, lease_owner,
                   lease_expires_at, last_error_code, last_error_hash,
                   created_at, updated_at, completed_at
               ) VALUES(?,?,?,?,?,?,?,?,'pending',0,NULL,NULL,NULL,NULL,NULL,?,?,NULL)
               ON CONFLICT(session_id, conversation_id, identity_mode,
                           source_message_id, pipeline_version)
               DO NOTHING""",
            (
                job_id,
                session_id,
                conversation_id,
                mode,
                int(source_message_id),
                pipeline_version,
                provider_id,
                model_id,
                now,
                now,
            ),
        )
        cur = await db.execute(
            """SELECT * FROM memory_ingest_jobs
                WHERE session_id=? AND conversation_id=? AND identity_mode=?
                  AND source_message_id=? AND pipeline_version=?""",
            (
                session_id,
                conversation_id,
                mode,
                int(source_message_id),
                pipeline_version,
            ),
        )
        row = await cur.fetchone()
        await db.commit()
        assert row is not None
        return _row_to_job(row, worldline=wl)
    except Exception:
        await db.rollback()
        raise
    finally:
        await db.close()


async def _find_job(
    job_id: str, *, worldline: str | None = None
) -> tuple[dict[str, Any], str] | None:
    worlds = (normalize_worldline(worldline),) if worldline else ("steins_gate", "beta")
    for wl in worlds:
        db = await get_db(wl, "memory")
        try:
            cur = await db.execute(
                "SELECT * FROM memory_ingest_jobs WHERE job_id=?", (job_id,)
            )
            row = await cur.fetchone()
            if row is not None:
                return _row_to_job(row, worldline=wl), wl
        finally:
            await db.close()
    return None


async def get_memory_job(job_id: str, *, worldline: str | None = None) -> dict[str, Any] | None:
    found = await _find_job(job_id, worldline=worldline)
    return found[0] if found else None


get_job = get_memory_job


async def claim_memory_job(
    *,
    job_id: str,
    lease_owner: str,
    worldline: str | None = None,
    lease_seconds: int = LEASE_SECONDS,
) -> dict[str, Any] | None:
    """CAS claim: pending (due) or expired processing only."""
    found = await _find_job(job_id, worldline=worldline)
    if found is None:
        return None
    _, wl = found
    now = _utc_now()
    now_s = _iso(now)
    expires = _iso(now + timedelta(seconds=lease_seconds))
    db = await get_db(wl, "memory")
    try:
        cur = await db.execute(
            """UPDATE memory_ingest_jobs
               SET state='processing',
                   lease_owner=?,
                   lease_expires_at=?,
                   attempt_count=attempt_count+1,
                   updated_at=?,
                   last_error_code=NULL,
                   last_error_hash=NULL
             WHERE job_id=?
               AND (
                    (state='pending'
                     AND (next_attempt_at IS NULL OR next_attempt_at <= ?))
                    OR (
                        state='processing'
                        AND lease_expires_at IS NOT NULL
                        AND lease_expires_at < ?
                    )
               )""",
            (lease_owner, expires, now_s, job_id, now_s, now_s),
        )
        await db.commit()
        if cur.rowcount != 1:
            return None
        cur = await db.execute(
            "SELECT * FROM memory_ingest_jobs WHERE job_id=?", (job_id,)
        )
        row = await cur.fetchone()
        return _row_to_job(row, worldline=wl) if row else None
    except Exception:
        await db.rollback()
        raise
    finally:
        await db.close()


async def _transition_owned(
    *,
    job_id: str,
    worldline: str,
    lease_owner: str,
    state: str,
    error_code: str | None = None,
    permanent: bool = False,
    attempt_count: int = 0,
) -> dict[str, Any]:
    """Complete/fail/cancel only while this owner still holds an unexpired processing lease."""
    now = _utc_now()
    now_s = _iso(now)
    err_hash = (
        hashlib.sha256(error_code.encode("utf-8")).hexdigest()[:16] if error_code else None
    )
    next_attempt = None
    final_state = state
    # S4-ARCHIVE Gate 2: stamp the persisted retry-eligibility authority at
    # the moment the failure lands. permanent → 0; transient (including
    # auto-retry exhaustion) → 1. Non-failed outcomes keep the neutral 1.
    final_retryable = 1
    if state == "failed_or_retry":
        if permanent or attempt_count >= MAX_AUTO_RETRIES:
            final_state = "failed"
            final_retryable = 0 if permanent else 1
        else:
            final_state = "pending"
            delay = min(60, 2 ** max(1, attempt_count))
            next_attempt = _iso(now + timedelta(seconds=delay))

    db = await get_db(worldline, "memory")
    try:
        cur = await db.execute(
            """UPDATE memory_ingest_jobs
               SET state=?,
                   retryable=?,
                   last_error_code=?,
                   last_error_hash=?,
                   next_attempt_at=?,
                   lease_owner=NULL,
                   lease_expires_at=NULL,
                   updated_at=?,
                   completed_at=CASE
                       WHEN ? IN ('completed','failed','cancelled') THEN ?
                       ELSE completed_at
                   END
             WHERE job_id=?
               AND state='processing'
               AND lease_owner=?
               AND lease_expires_at IS NOT NULL
               AND lease_expires_at > ?""",
            (
                final_state,
                final_retryable,
                error_code,
                err_hash,
                next_attempt,
                now_s,
                final_state,
                now_s,
                job_id,
                lease_owner,
                now_s,
            ),
        )
        await db.commit()
        if cur.rowcount != 1:
            raise LeaseLostError(
                f"lost lease for job_id={job_id} owner={lease_owner} target={final_state}"
            )
        cur = await db.execute(
            "SELECT * FROM memory_ingest_jobs WHERE job_id=?", (job_id,)
        )
        row = await cur.fetchone()
        assert row is not None
        return _row_to_job(row, worldline=worldline)
    except LeaseLostError:
        raise
    except Exception:
        await db.rollback()
        raise
    finally:
        await db.close()


async def mark_memory_job_failed(
    *,
    job_id: str,
    error_code: str,
    permanent: bool = False,
    worldline: str | None = None,
    lease_owner: str | None = None,
) -> dict[str, Any]:
    """Mark failed/retry only while job is processing under the owner lease."""
    found = await _find_job(job_id, worldline=worldline)
    if found is None:
        raise KeyError(f"job not found: {job_id}")
    job, wl = found
    if job["state"] != "processing":
        raise LeaseLostError(
            f"cannot mark failed job_id={job_id} state={job['state']}"
        )
    owner = lease_owner or job.get("lease_owner")
    if not owner:
        raise LeaseLostError(f"no lease owner for job_id={job_id}")
    if lease_owner is not None and job.get("lease_owner") != lease_owner:
        raise LeaseLostError(
            f"lease owner mismatch job_id={job_id} have={job.get('lease_owner')} want={lease_owner}"
        )
    return await _transition_owned(
        job_id=job_id,
        worldline=wl,
        lease_owner=str(owner),
        state="failed_or_retry",
        error_code=error_code,
        permanent=permanent,
        attempt_count=int(job["attempt_count"] or 0),
    )

async def retry_memory_job(
    *,
    job_id: str,
    session_id: str,
    worldline: str,
    identity_mode: str | None = None,
) -> dict[str, Any]:
    wl = normalize_worldline(worldline)
    found = await _find_job(job_id, worldline=wl)
    if found is None:
        raise KeyError(f"job not found: {job_id}")
    job, job_wl = found
    if job["session_id"] != session_id:
        raise PermissionError("session_id mismatch for job retry")
    if identity_mode is not None and str(identity_mode).strip() != "":
        mode = normalize_identity_mode(identity_mode)
        if job["identity_mode"] != mode:
            raise PermissionError("identity_mode mismatch for job retry")
    if job["state"] != "failed":
        return job

    # S4-ARCHIVE Gate 2 re-validation against the PERSISTED authority: a
    # permanent failure (retryable=0) never moves to pending. Idempotent
    # no-op replay — returns the unchanged failed job, mirroring the
    # already-pending idempotent contract above.
    if not int(job["retryable"] if job["retryable"] is not None else 1):
        return job

    now = _iso()
    db = await get_db(job_wl, "memory")
    try:
        await db.execute(
            """UPDATE memory_ingest_jobs
               SET state='pending',
                   next_attempt_at=NULL,
                   lease_owner=NULL,
                   lease_expires_at=NULL,
                   last_error_code=NULL,
                   last_error_hash=NULL,
                   completed_at=NULL,
                   updated_at=?
             WHERE job_id=? AND state='failed'""",
            (now, job_id),
        )
        await db.commit()
        cur = await db.execute(
            "SELECT * FROM memory_ingest_jobs WHERE job_id=?", (job_id,)
        )
        row = await cur.fetchone()
        assert row is not None
        return _row_to_job(row, worldline=job_wl)
    except Exception:
        await db.rollback()
        raise
    finally:
        await db.close()


async def _current_source_already_applied(
    *,
    session_id: str,
    worldline: str,
    identity_mode: str,
    conversation_id: str,
    source_message_id: int,
    pipeline_version: str,
) -> bool:
    """D28 durable-receipt precheck for the CURRENT job source cursor.

    Dedicated full-cursor receipt (S3F-1) OR legacy observation-source proof
    allowed only for the current PIPELINE_VERSION (old observation rows store
    the conversation_id; a future pipeline must never be suppressed by them).

    Grounded ONLY in the job's frozen source cursor — never derived from model
    output / target ids / provider-supplied evidence.
    """
    wl = normalize_worldline(worldline)
    mode = normalize_identity_mode(identity_mode)
    db = await get_db(wl, "memory")
    try:
        from app.services.memory_v11.receipts import has_ingest_receipt

        if await has_ingest_receipt(
            db,
            session_id=session_id,
            conversation_id=conversation_id,
            identity_mode=mode,
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
                     JOIN memory_observations o
                       ON o.observation_id = s.observation_id
                    WHERE s.source_message_id=? AND s.conversation_id=?
                      AND o.session_id=? AND o.identity_mode=?
                    LIMIT 1""",
                (int(source_message_id), conversation_id, session_id, mode),
            )
        ).fetchone()
        return row is not None
    finally:
        await db.close()


async def _load_user_source(
    *,
    session_id: str,
    worldline: str,
    conversation_id: str,
    source_message_id: int,
) -> dict[str, Any] | None:
    wl = normalize_worldline(worldline)
    db = await get_db(wl, "history")
    try:
        cur = await db.execute(
            """SELECT id, role, content, conversation_id, session_id, created_at
                 FROM messages
                WHERE id=? AND session_id=? AND conversation_id=?""",
            (int(source_message_id), session_id, conversation_id),
        )
        row = await cur.fetchone()
        if row is None or str(row["role"]) != "user":
            return None
        previous = await (
            await db.execute(
                """SELECT role, content FROM messages
                    WHERE session_id=? AND conversation_id=? AND id<?
                    ORDER BY id DESC LIMIT 1""",
                (session_id, conversation_id, int(source_message_id)),
            )
        ).fetchone()
        previous_text = None
        if previous is not None and str(previous["role"]) == "assistant":
            context = re.sub(r"^\[EMO:[^]\r\n]*\]\s*", "", str(previous["content"] or ""))
            context, blocked = redact_user_text(context)
            if not blocked:
                previous_text = context[-PREVIOUS_ASSISTANT_MAX_CHARS:]
        return {
            "previous_assistant_text": previous_text,
            "source_message_id": int(row["id"]),
            "role": "user",
            "content": str(row["content"] or ""),
            "conversation_id": str(row["conversation_id"]),
            "session_id": str(row["session_id"]),
            "created_at": row["created_at"],
        }
    finally:
        await db.close()


_V11_MEMORY_SYSTEM = """You extract durable user memory from one user utterance.
Return ONE JSON object only:
{
  "observations": [
    {
      "observation_ref": "o1",
      "display_text": "...",
      "fact_text": "...",
      "semantic": {"subject":"user","predicate":"...","object":{},"polarity":"positive","qualifiers":{}},
      "evidence_kind": "direct_user",
      "memory_class": "stable_candidate",
      "confidence": 0.0,
      "topic_label": "...",
      "expires_at": null,
      "relation": "new|same|refines|supersedes|reject",
      "candidate_index": null,
      "reanalysis_target_observation_index": null
    }
  ]
}
Rules:
- Only user_text establishes facts; prefer empty over invention.
- previous_assistant_text is context only: use it to understand what the user
  answers or refers to. Never remember an assistant claim the user has not confirmed.
- Write display_text and fact_text in the language of the user utterance; do not translate.
  For mixed-language speech, follow the language of the part supporting that fact.
  Do not store a separate Japanese or other normalized-language version.
- Do not invent conversational-act facts (e.g. 'recommended'); confidence 0..1.
- Give each observation a relation: new creates a fact, same attaches evidence,
  refines clarifies an existing fact, supersedes replaces it, reject records a
  rejected observation without establishing a fact.
- For same/refines/supersedes, candidate_index MUST be the 1-based index from
  candidates. For new/reject use null. Never output fact IDs or version numbers.
- memory_class=episodic: only new (experience) or reject; never target a stable fact.
- unresolved_observations are OLD context, NOT new evidence. At most ONE may
  be clarified: set reanalysis_target_observation_index to its 1-based
  observation_index only when user_text directly resolves that old observation.
  Otherwise omit or null. Stable candidates and unresolved targets are separate lists.
- Never remember [REDACTED:<category>] placeholders or their contents.
- Never output source_message_ids: the backend supplies the current user source.
"""


def _candidate_payload_for_completion(rows: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Minimal fields for Memory LLM + frozen allowlist (no internal noise)."""
    out: list[dict[str, Any]] = []
    for index, row in enumerate(rows, 1):
        item: dict[str, Any] = {
            "candidate_index": index,
            "display_text": str(row.get("display_text") or ""),
            "is_pinned": bool(row.get("is_pinned")),
        }
        out.append(item)
    return out


def _indexed_item(items: list[Mapping[str, Any]], index: Any, code: str) -> Mapping[str, Any]:
    from app.services.memory_v11.contracts import MemoryValidationError
    if isinstance(index, bool) or not isinstance(index, int) or not 1 <= index <= len(items):
        raise MemoryValidationError(code)
    return items[index - 1]


def _translate_completion(observations, operations, candidates, unresolved, source_id):
    """Bind model ordinals to THIS attempt's private snapshots, never model IDs.

    Legacy CREATE/REJECT operations remain accepted for injected completion
    callbacks. Targeted legacy operations still require a candidate_index.
    """
    from app.services.memory_v11.contracts import MemoryValidationError
    relations = {"new": "CREATE", "same": "ATTACH", "refines": "REFINE", "supersedes": "SUPERSEDE", "reject": "REJECT"}
    normalized = []
    ops = []
    legacy = {str(op.get("observation_ref") or ""): op for op in operations}
    for raw in observations:
        obs = {key: value for key, value in raw.items() if key not in {
            "source_message_ids", "reanalysis_target_observation_id", "_ingest_error",
        }}
        obs["source_message_ids"] = [int(source_id)]
        old = legacy.get(str(obs.get("observation_ref") or ""), {})
        relation = obs.get("relation")
        if relation is None:
            relation = {v: k for k, v in relations.items()}.get(str(old.get("op") or "").upper())
        op = {"observation_ref": obs.get("observation_ref"), "fact_text": obs.get("fact_text") or old.get("fact_text"), "op": relations.get(str(relation), "")}
        try:
            if not op["op"]:
                raise MemoryValidationError("invalid_relation")
            if op["op"] in {"ATTACH", "REFINE", "SUPERSEDE"}:
                candidate = _indexed_item(candidates, obs.get("candidate_index", old.get("candidate_index")), "candidate_index_out_of_range")
                op["target_fact_id"] = str(candidate["fact_id"])
                op["expected_version"] = int(candidate.get("version_no") or candidate.get("active_version") or 1)
            target_index = obs.get("reanalysis_target_observation_index")
            if target_index is not None:
                target = _indexed_item(unresolved, target_index, "reanalysis_index_out_of_range")
                obs["reanalysis_target_observation_id"] = str(target["observation_id"])
        except MemoryValidationError as exc:
            op["_ingest_error"] = exc.code
        normalized.append(obs)
        ops.append(op)
    # Unknown legacy refs must not silently disappear.
    refs = {str(obs.get("observation_ref") or "") for obs in observations}
    for old in operations:
        if str(old.get("observation_ref") or "") not in refs:
            ops.append({"op": "", "_ingest_error": "unknown_observation_ref"})
    return normalized, ops


async def default_memory_completion(**kwargs: Any) -> Mapping[str, Any]:
    """One structured Memory completion via the job's selected provider.

    Raises on missing credential / capability / transport so the caller can
    enter provider_unavailable retry — never silently return empty success.
    """
    import json

    from app.services.credentials import credential_store
    from app.services.provider_registry import ProviderTask
    from app.services.provider_runtime import provider_registry

    provider_id = str(kwargs.get("provider_id") or "deepseek")
    model_id = str(kwargs.get("model_id") or "")
    user_text = str(kwargs.get("user_text") or "")
    source_message_id = int(kwargs.get("source_message_id") or 0)
    identity_mode = str(kwargs.get("identity_mode") or "okabe")
    candidates = list(kwargs.get("candidates") or [])
    unresolved = list(kwargs.get("unresolved_observations") or [])

    try:
        # Honor the job-frozen model selection; capability fail-closed on MEMORY.
        snapshot = provider_registry.snapshot(provider_id, model_id or None)
        snapshot.require(ProviderTask.MEMORY)
    except Exception as exc:
        raise RuntimeError(f"provider_unavailable: {exc}") from exc

    api_key = credential_store.get(provider_id) or None
    if api_key and api_key.lower().startswith("test"):
        logger.warning("memory_v11 skip completion reason=test_key")
        raise RuntimeError("provider_unavailable: test credential")
    if snapshot.credential_required and (not api_key or len(api_key) < 8):
        logger.warning("memory_v11 skip completion reason=required_key_missing_or_short")
        raise RuntimeError("provider_unavailable: missing or short required credential")

    messages = [
        {"role": "system", "content": _V11_MEMORY_SYSTEM},
        {
            "role": "user",
            "content": json.dumps(
                {
                    "identity_mode": identity_mode,
                    "source_message_id": source_message_id,
                    "user_text": user_text,
                    "previous_assistant_text": kwargs.get("previous_assistant_text"),
                    "candidates": candidates,
                    "unresolved_observations": unresolved,
                },
                ensure_ascii=False,
            ),
        },
    ]
    payload = await snapshot.adapter.complete_json(
        messages=messages,
        api_key=api_key,
        model=snapshot.model_id,
        temperature=0.1,
    )
    if not isinstance(payload, dict):
        raise ValueError("memory completion was not a JSON object")
    # Normalize list fields.
    if "observations" not in payload:
        payload["observations"] = []
    if "operations" not in payload:
        payload["operations"] = []
    return payload


async def _deleted_source_suppressed(job: Mapping[str, Any], worldline: str) -> bool:
    from app.services.memory_v11.deleted_sources import record_suppression, source_is_deleted
    db = await get_db(worldline, "memory")
    try:
        await db.execute("BEGIN IMMEDIATE")
        deleted = await source_is_deleted(
            db, session_id=job["session_id"], identity_mode=job["identity_mode"],
            conversation_id=job["conversation_id"], source_message_id=int(job["source_message_id"]),
        )
        if deleted:
            await record_suppression(db, session_id=job["session_id"], identity_mode=job["identity_mode"], cursor={
                "conversation_id": job["conversation_id"], "source_message_id": int(job["source_message_id"]),
                "pipeline_version": job["pipeline_version"], "attempt_no": int(job["attempt_count"] or 1),
            })
        await db.commit()
        return deleted
    except Exception:
        await db.rollback()
        raise
    finally:
        await db.close()


async def process_memory_job(
    *,
    job_id: str,
    memory_completion: MemoryCompletionFn | None = None,
    lease_owner: str = "worker",
    worldline: str | None = None,
) -> dict[str, Any]:
    """Process one job only while holding the lease; exactly one Memory completion path.

    When memory_completion is omitted, uses default_memory_completion (real provider).
    Missing provider/credential is provider_unavailable retry — never silent completed.
    """
    found = await _find_job(job_id, worldline=worldline)
    if found is None:
        raise KeyError(f"job not found: {job_id}")
    job, wl = found

    if job["state"] in {"completed", "cancelled"}:
        return job

    # Must acquire or already hold a valid lease as this owner.
    owns = (
        job["state"] == "processing"
        and job.get("lease_owner") == lease_owner
        and job.get("lease_expires_at")
        and str(job["lease_expires_at"]) > _iso()
    )
    if not owns:
        claimed = await claim_memory_job(
            job_id=job_id, lease_owner=lease_owner, worldline=wl
        )
        if claimed is None:
            # Another worker holds a valid lease — do not process.
            return (await get_memory_job(job_id, worldline=wl)) or job
        job = claimed

    if job.get("lease_owner") != lease_owner or job["state"] != "processing":
        return (await get_memory_job(job_id, worldline=wl)) or job

    # A user deletion suppresses this entire source window across pipelines.
    # Check before ordinary receipt replay so the reason is recorded even for
    # a source which already had a successful original ingest receipt.
    if await _deleted_source_suppressed(job, wl):
        return await _transition_owned(job_id=job_id, worldline=wl,
                                       lease_owner=lease_owner, state="completed")

    # P1-B + S3F-1 (D28): durable-receipt precheck for the CURRENT job source
    # cursor (dedicated full-cursor receipt OR current-pipeline legacy
    # observation proof). If the receipt exists, the Memory transaction already
    # committed atomically; converge to completed WITHOUT re-running selectors
    # or the provider. This closes the window where Memory commits but the job
    # crashes before state='completed'.
    if await _current_source_already_applied(
        session_id=job["session_id"],
        worldline=wl,
        identity_mode=job["identity_mode"],
        conversation_id=job["conversation_id"],
        source_message_id=int(job["source_message_id"]),
        pipeline_version=job["pipeline_version"],
    ):
        return await _transition_owned(
            job_id=job_id,
            worldline=wl,
            lease_owner=lease_owner,
            state="completed",
        )

    completion_fn: MemoryCompletionFn = memory_completion or default_memory_completion

    try:
        source = await _load_user_source(
            session_id=job["session_id"],
            worldline=wl,
            conversation_id=job["conversation_id"],
            source_message_id=int(job["source_message_id"]),
        )
        if source is None:
            return await _transition_owned(
                job_id=job_id,
                worldline=wl,
                lease_owner=lease_owner,
                state="cancelled",
                error_code=MemoryJobErrorCode.SOURCE_MISSING.value,
            )

        redacted, blocked = redact_user_text(source["content"])
        if blocked:
            # Masking failed: complete without provider calls or Memory receipts/writes.
            return await _transition_owned(
                job_id=job_id,
                worldline=wl,
                lease_owner=lease_owner,
                state="completed",
            )

        # §6.1: local same-scope candidate selection BEFORE the single completion.
        # Allowlist is frozen to these IDs only (no post-completion widening).
        # D18: bounded unresolved-observation context is selected locally too;
        # it rides the SAME single completion (no second model call).
        try:
            from app.services.memory_v11.reconciliation_select import (
                select_reconciliation_candidates,
            )
            from app.services.memory_v11.retrieval import (
                select_unresolved_observations,
            )

            selected_rows = await select_reconciliation_candidates(
                session_id=job["session_id"],
                worldline=wl,
                identity_mode=job["identity_mode"],
                source_text=redacted,
            )
            frozen_candidates = list(selected_rows or [])
            candidates = _candidate_payload_for_completion(frozen_candidates)
            candidate_allowlist = [str(item["fact_id"]) for item in frozen_candidates]

            unresolved_rows = await select_unresolved_observations(
                session_id=job["session_id"],
                worldline=wl,
                identity_mode=job["identity_mode"],
                conversation_id=job["conversation_id"],
                current_source_message_id=int(job["source_message_id"]),
                query=redacted,
            )
            frozen_unresolved = list(unresolved_rows or [])
            unresolved_observations = [
                {**{k: v for k, v in item.items() if k not in {"source_message_ids", "observation_id"}}, "observation_index": index}
                for index, item in enumerate(frozen_unresolved, 1)
            ]
            reanalysis_allowlist = [
                str(item["observation_id"]) for item in frozen_unresolved
            ]
        except Exception:
            # Selection failure: never call provider; use existing failed_or_retry path.
            return await _transition_owned(
                job_id=job_id,
                worldline=wl,
                lease_owner=lease_owner,
                state="failed_or_retry",
                error_code=MemoryJobErrorCode.VALIDATION_FAILED.value,
                permanent=False,
                attempt_count=int(job["attempt_count"] or 0),
            )

        try:
            completion_payload = await completion_fn(
                session_id=job["session_id"],
                worldline=wl,
                identity_mode=job["identity_mode"],
                conversation_id=job["conversation_id"],
                source_message_id=int(job["source_message_id"]),
                user_text=redacted,
                previous_assistant_text=source.get("previous_assistant_text"),
                provider_id=job["provider_id"],
                model_id=job["model_id"],
                pipeline_version=job["pipeline_version"],
                candidates=candidates,
                unresolved_observations=unresolved_observations,
            )
        except Exception:
            return await _transition_owned(
                job_id=job_id,
                worldline=wl,
                lease_owner=lease_owner,
                state="failed_or_retry",
                error_code=MemoryJobErrorCode.PROVIDER_UNAVAILABLE.value,
                permanent=False,
                attempt_count=int(job["attempt_count"] or 0),
            )
        if not isinstance(completion_payload, Mapping):
            return await _transition_owned(
                job_id=job_id,
                worldline=wl,
                lease_owner=lease_owner,
                state="failed_or_retry",
                error_code=MemoryJobErrorCode.INVALID_JSON.value,
                permanent=False,
                attempt_count=int(job["attempt_count"] or 0),
            )

        # S3F-2A: strict container/item/count validation BEFORE any receipt /
        # source_evidence / reconciler work. A provider response that already
        # succeeded but violated a deterministic local contract is not worth
        # retrying with the same schema → permanent failed(validation_failed).
        # Note: stable/unresolved selectors have already run before the provider
        # under the one-call architecture; they are not re-run here, and the
        # provider is never called a second time.
        try:
            observations, operations = _validate_completion_collections(
                completion_payload
            )
        except ValueError:
            return await _transition_owned(
                job_id=job_id,
                worldline=wl,
                lease_owner=lease_owner,
                state="failed_or_retry",
                error_code=MemoryJobErrorCode.VALIDATION_FAILED.value,
                permanent=True,
                attempt_count=int(job["attempt_count"] or 0),
            )

        if observations or operations:
            observations, operations = _translate_completion(
                observations, operations, frozen_candidates, frozen_unresolved,
                int(job["source_message_id"]),
            )
            from app.services.memory_v11.reconciler import (
                apply_validated_memory_operations,
            )

            fp = hashlib.sha256(
                f"{job['session_id']}:{job['source_message_id']}:{redacted}".encode(
                    "utf-8"
                )
            ).hexdigest()
            source_evidence = [
                {
                    "source_message_id": int(job["source_message_id"]),
                    "conversation_id": job["conversation_id"],
                    "source_role": "user",
                    "source_fingerprint": fp,
                    # Persisted message created_at (S3D-4 narrow fix); fall back
                    # to processing time only when the value is unusable.
                    "source_created_at": source.get("created_at") or _iso(),
                    "excerpt": redacted[:200],
                }
            ]
            try:
                # S3F-1 D28: the reconciler receives the live-job receipt cursor
                # and inserts a provider_nonempty receipt inside its own atomic
                # semantic transaction (after semantic writes, before commit).
                await apply_validated_memory_operations(
                    session_id=job["session_id"],
                    worldline=wl,
                    identity_mode=job["identity_mode"],
                    observations=observations,
                    operations=operations,
                    source_evidence=source_evidence,
                    candidate_allowlist=candidate_allowlist,
                    reanalysis_allowlist=reanalysis_allowlist,
                    processing_receipt={
                        "conversation_id": job["conversation_id"],
                        "source_message_id": int(job["source_message_id"]),
                        "pipeline_version": job["pipeline_version"],
                        "attempt_no": int(job["attempt_count"] or 1),
                    },
                )
            except Exception as exc:
                from app.services.memory_v11.contracts import MemoryValidationError
                if getattr(exc, 'code', None) == 'source_erased':
                    return await _transition_owned(
                        job_id=job_id, worldline=wl, lease_owner=lease_owner,
                        state='cancelled', error_code=MemoryJobErrorCode.SOURCE_MISSING.value,
                    )
                return await _transition_owned(
                    job_id=job_id,
                    worldline=wl,
                    lease_owner=lease_owner,
                    state="failed_or_retry",
                    error_code=exc.code if isinstance(exc, MemoryValidationError) else "operation_failed",
                    permanent=False,
                    attempt_count=int(job["attempt_count"] or 0),
                )
        else:
            # S3F-1 D28: valid EMPTY completion → receipt-only Memory
            # transaction (no fake observations). Receipt commit and the job
            # completion CAS stay separate on purpose: a crash between them is
            # the replay window the durable receipt now closes.
            from app.services.memory_v11.receipts import (
                RECEIPT_KIND_EMPTY,
                has_ingest_receipt,
                insert_ingest_receipt,
            )

            db = await get_db(wl, "memory")
            try:
                await db.execute("BEGIN IMMEDIATE")
                # Final full-cursor check under the write lock (idempotent).
                if not await has_ingest_receipt(
                    db,
                    session_id=job["session_id"],
                    conversation_id=job["conversation_id"],
                    identity_mode=job["identity_mode"],
                    source_message_id=int(job["source_message_id"]),
                    pipeline_version=job["pipeline_version"],
                ):
                    await insert_ingest_receipt(
                        db,
                        session_id=job["session_id"],
                        conversation_id=job["conversation_id"],
                        identity_mode=job["identity_mode"],
                        source_message_id=int(job["source_message_id"]),
                        pipeline_version=job["pipeline_version"],
                        receipt_kind=RECEIPT_KIND_EMPTY,
                    )
                await db.commit()
            except Exception:
                await db.rollback()
                return await _transition_owned(
                    job_id=job_id,
                    worldline=wl,
                    lease_owner=lease_owner,
                    state="failed_or_retry",
                    error_code=MemoryJobErrorCode.VALIDATION_FAILED.value,
                    permanent=False,
                    attempt_count=int(job["attempt_count"] or 0),
                )
            finally:
                await db.close()

        return await _transition_owned(
            job_id=job_id,
            worldline=wl,
            lease_owner=lease_owner,
            state="completed",
        )
    except LeaseLostError:
        current = await get_memory_job(job_id, worldline=wl)
        return current or job


async def list_due_job_ids(*, worldline: str, limit: int = 8) -> list[str]:
    wl = normalize_worldline(worldline)
    now = _iso()
    db = await get_db(wl, "memory")
    try:
        cur = await db.execute(
            """SELECT job_id FROM memory_ingest_jobs
                WHERE (
                    state='pending'
                    AND (next_attempt_at IS NULL OR next_attempt_at <= ?)
                ) OR (
                    state='processing'
                    AND lease_expires_at IS NOT NULL
                    AND lease_expires_at < ?
                )
                ORDER BY created_at ASC
                LIMIT ?""",
            (now, now, limit),
        )
        rows = await cur.fetchall()
        return [str(r["job_id"]) for r in rows]
    finally:
        await db.close()


async def process_due_jobs_once(
    *,
    lease_owner: str,
    memory_completion: MemoryCompletionFn | None = None,
    worldlines: tuple[str, ...] = ("steins_gate", "beta"),
    limit: int = 8,
) -> list[dict[str, Any]]:
    """Process up to `limit` due jobs per worldline (worker + integration tests).

    Omitting memory_completion uses default_memory_completion (real provider).
    """
    results: list[dict[str, Any]] = []
    for wl in worldlines:
        for job_id in await list_due_job_ids(worldline=wl, limit=limit):
            results.append(
                await process_memory_job(
                    job_id=job_id,
                    lease_owner=lease_owner,
                    worldline=wl,
                    memory_completion=memory_completion,
                )
            )
    return results


async def _worker_loop(stop: asyncio.Event, owner: str) -> None:
    while not stop.is_set():
        try:
            # Never pass None as a silent no-op completion — default provider path
            # or injected callback only.
            await process_due_jobs_once(
                lease_owner=owner,
                memory_completion=None,  # resolves to default_memory_completion
            )
        except Exception as exc:
            print(f"[MemoryV11] worker loop error: {type(exc).__name__}", flush=True)
        try:
            await asyncio.wait_for(stop.wait(), timeout=2.0)
        except asyncio.TimeoutError:
            pass


async def start_background_worker(*, owner: str | None = None) -> None:
    """Start in-process poller when shadow/v11 is explicitly enabled."""
    global _worker_task, _worker_stop
    if _worker_task is not None and not _worker_task.done():
        return
    _worker_stop = asyncio.Event()
    lease = owner or f"worker-{uuid.uuid4().hex[:8]}"
    _worker_task = asyncio.create_task(_worker_loop(_worker_stop, lease))


async def stop_background_worker() -> None:
    global _worker_task, _worker_stop
    if _worker_stop is not None:
        _worker_stop.set()
    if _worker_task is not None:
        try:
            await asyncio.wait_for(_worker_task, timeout=5.0)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            _worker_task.cancel()
    _worker_task = None
    _worker_stop = None


def memory_mode() -> str:
    return (os.environ.get("AMADEUS_MEMORY_MODE") or "legacy").strip().lower()
