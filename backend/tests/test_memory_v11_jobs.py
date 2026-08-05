"""MEMORY-V11 S0 red contracts: durable jobs (D22–D25, B03–B07).

Fixed import surface only:
  app.services.memory_v11.jobs
  app.services.memory_v11.contracts
No fallback to legacy app.services.memory.
"""
from __future__ import annotations

import hashlib
import importlib
import json
import math
import struct
from typing import Any
from uuid import uuid4

import pytest

from app import models
from app.db import get_db, init_db, reset_initialization_cache
from app.services.conversations import conversation_service
from app.services.memory import EmbeddingAdapter, memory_service
from app.services.memory_v11.repository import create_stable_fact


def _jobs():
    return importlib.import_module("app.services.memory_v11.jobs")


def _contracts():
    return importlib.import_module("app.services.memory_v11.contracts")


def _unit_vector(seed: str) -> list[float]:
    values: list[float] = []
    for block in range(12):
        digest = hashlib.sha256(f"{seed}:{block}".encode("utf-8")).digest()
        values.extend(int(byte) - 127.5 for byte in digest)
    norm = math.sqrt(sum(v * v for v in values))
    return [v / norm for v in values]


class _DeterministicEmbedder(EmbeddingAdapter):
    """Offline EmbeddingAdapter stub (no SentenceTransformer / network)."""

    def __init__(
        self,
        vocabulary: dict[str, str] | None = None,
        *,
        model_name: str = "test-deterministic-384",
    ):
        super().__init__(model_name=model_name, dimensions=384)
        self._vocabulary = dict(vocabulary or {})
        self.encode_passage_calls = 0
        self.encode_query_calls = 0

    async def encode_passage(self, text: str) -> list[float]:
        self.encode_passage_calls += 1
        return _unit_vector(self._vocabulary.get(text, f"passage:{text}"))

    async def encode_query(self, text: str) -> list[float]:
        self.encode_query_calls += 1
        return _unit_vector(self._vocabulary.get(text, f"query:{text}"))


@pytest.fixture
async def isolated_store(tmp_path, monkeypatch):
    monkeypatch.delenv("AMADEUS_DB_PATH", raising=False)
    monkeypatch.setenv("AMADEUS_DATA_DIR", str(tmp_path))
    reset_initialization_cache()
    await init_db()
    # CREATE/REFINE through jobs need passage embeddings offline.
    monkeypatch.setattr(memory_service, "embedder", _DeterministicEmbedder())
    return tmp_path


def test_s0_jobs_module_exists():
    importlib.import_module("app.services.memory_v11.jobs")
    importlib.import_module("app.services.memory_v11.contracts")


@pytest.mark.asyncio
async def test_s0_enqueue_idempotent_on_source_cursor(isolated_store):
    jobs = _jobs()
    payload = dict(
        session_id="s-jobs",
        worldline="steins_gate",
        conversation_id="c-1",
        identity_mode="self",
        source_message_id=101,
        pipeline_version="memory-v11-1",
        provider_id="deepseek",
        model_id="deepseek-chat",
    )
    first = await jobs.enqueue_memory_job(**payload)
    second = await jobs.enqueue_memory_job(**payload)
    first_id = first.job_id if hasattr(first, "job_id") else first["job_id"]
    second_id = second.job_id if hasattr(second, "job_id") else second["job_id"]
    assert first_id == second_id


@pytest.mark.asyncio
async def test_s2_enqueue_concurrent_gather_single_row(isolated_store):
    """Concurrent enqueue racers share one job_id; DB has exactly one row."""
    import asyncio

    from app.db import get_db

    jobs = _jobs()
    payload = dict(
        session_id="s-concurrent",
        worldline="steins_gate",
        conversation_id="c-concurrent",
        identity_mode="self",
        source_message_id=4242,
        pipeline_version="memory-v11-1",
        provider_id="deepseek",
        model_id="deepseek-chat",
    )
    results = await asyncio.gather(
        jobs.enqueue_memory_job(**payload),
        jobs.enqueue_memory_job(**payload),
        jobs.enqueue_memory_job(**payload),
        jobs.enqueue_memory_job(**payload),
    )
    ids = [r["job_id"] for r in results]
    assert len(set(ids)) == 1, f"expected one job_id, got {ids}"

    db = await get_db("steins_gate", "memory")
    try:
        row = await (
            await db.execute(
                """SELECT COUNT(*) AS n FROM memory_ingest_jobs
                    WHERE session_id=? AND conversation_id=?
                      AND identity_mode=? AND source_message_id=?
                      AND pipeline_version=?""",
                (
                    payload["session_id"],
                    payload["conversation_id"],
                    payload["identity_mode"],
                    payload["source_message_id"],
                    payload["pipeline_version"],
                ),
            )
        ).fetchone()
    finally:
        await db.close()
    assert int(row["n"]) == 1


@pytest.mark.asyncio
async def test_s0_claim_retry_reuses_job_id(isolated_store):
    jobs = _jobs()
    contracts = _contracts()
    job = await jobs.enqueue_memory_job(
        session_id="s-jobs",
        worldline="steins_gate",
        conversation_id="c-1",
        identity_mode="self",
        source_message_id=202,
        pipeline_version="memory-v11-1",
        provider_id="deepseek",
        model_id="deepseek-chat",
    )
    job_id = job.job_id if hasattr(job, "job_id") else job["job_id"]
    claimed = await jobs.claim_memory_job(job_id=job_id, lease_owner="worker-a")
    assert claimed is not None
    await jobs.mark_memory_job_failed(
        job_id=job_id,
        error_code="provider_unavailable",
        permanent=True,
    )
    retried = await jobs.retry_memory_job(
        job_id=job_id,
        session_id="s-jobs",
        worldline="steins_gate",
    )
    retried_id = retried.job_id if hasattr(retried, "job_id") else retried["job_id"]
    assert retried_id == job_id
    assert hasattr(contracts, "MemoryJobErrorCode") or hasattr(
        contracts, "MEMORY_JOB_ERROR_CODES"
    )


@pytest.mark.asyncio
async def test_s0_process_job_one_llm_call_via_fake_provider(isolated_store):
    """R11/D21: real conversation + message_id enqueue → exactly one completion + completed job."""
    jobs = _jobs()
    session_id = "s-jobs-llm"
    conv = await conversation_service.create(
        session_id, "steins_gate", title="llm-once", identity_mode="self"
    )
    conversation_id = str(conv["id"])
    message_id = await models.save_message(
        session_id,
        "user",
        "我喜欢看动漫",
        worldline="steins_gate",
        conversation_id=conversation_id,
    )
    assert isinstance(message_id, int) and message_id > 0

    call_count = {"n": 0}

    async def fake_memory_completion(**kwargs: Any) -> dict[str, Any]:
        call_count["n"] += 1
        return {"observations": [], "operations": []}

    job = await jobs.enqueue_memory_job(
        session_id=session_id,
        worldline="steins_gate",
        conversation_id=conversation_id,
        identity_mode="self",
        source_message_id=int(message_id),
        pipeline_version="memory-v11-1",
        provider_id="deepseek",
        model_id="deepseek-chat",
    )
    job_id = job.job_id if hasattr(job, "job_id") else job["job_id"]

    result = await jobs.process_memory_job(
        job_id=job_id,
        memory_completion=fake_memory_completion,
        lease_owner="worker-test",
    )
    assert call_count["n"] == 1, f"expected exactly one Memory LLM call, got {call_count['n']}"

    state = None
    if isinstance(result, dict):
        state = result.get("state")
    else:
        state = getattr(result, "state", None)
    if state is None:
        get_job = getattr(jobs, "get_memory_job", None) or getattr(jobs, "get_job", None)
        assert callable(get_job), "process result lacked state and get_memory_job missing"
        stored = await get_job(job_id=job_id)
        state = stored["state"] if isinstance(stored, dict) else stored.state
    assert state == "completed", f"expected job completed, got {state!r}"


def test_s0_chat_ws_enqueues_not_legacy_only():
    import inspect

    from app.routers import chat_ws

    source = inspect.getsource(chat_ws)
    assert "enqueue_memory_job" in source or "memory_v11" in source, (
        "chat_ws still only uses legacy schedule_turn_extraction"
    )
    # Default production mode is legacy; shadow only when explicitly set.
    assert 'or "legacy"' in source or "or 'legacy'" in source or 'or "legacy"' in source.replace(
        "'", '"'
    )
    assert "legacy" in source


@pytest.mark.asyncio
async def test_s2_stale_worker_cannot_complete_foreign_lease(isolated_store):
    """P1: worker-B must not process/complete a job held by worker-A."""
    jobs = _jobs()
    job = await jobs.enqueue_memory_job(
        session_id="s-lease",
        worldline="steins_gate",
        conversation_id="c-lease",
        identity_mode="self",
        source_message_id=501,
        pipeline_version="memory-v11-1",
        provider_id="deepseek",
        model_id="deepseek-chat",
    )
    job_id = job["job_id"]
    claimed = await jobs.claim_memory_job(job_id=job_id, lease_owner="worker-A")
    assert claimed is not None
    assert claimed["lease_owner"] == "worker-A"
    assert claimed["state"] == "processing"

    async def boom(**kwargs):
        raise AssertionError("worker-B must not invoke Memory completion")

    result = await jobs.process_memory_job(
        job_id=job_id,
        lease_owner="worker-B",
        memory_completion=boom,
    )
    assert result["state"] == "processing"
    assert result["lease_owner"] == "worker-A"
    stored = await jobs.get_memory_job(job_id)
    assert stored["state"] == "processing"
    assert stored["lease_owner"] == "worker-A"
    assert stored["completed_at"] is None


@pytest.mark.asyncio
async def test_s2_provider_throw_enters_retry_not_stuck_processing(isolated_store):
    """P1: provider exception → retry/failed with error code; not stuck processing."""
    from app import models
    from app.services.conversations import conversation_service

    jobs = _jobs()
    session_id = "s-prov"
    conv = await conversation_service.create(
        session_id, "steins_gate", title="prov", identity_mode="self"
    )
    mid = await models.save_message(
        session_id,
        "user",
        "我喜欢茶",
        worldline="steins_gate",
        conversation_id=str(conv["id"]),
    )
    job = await jobs.enqueue_memory_job(
        session_id=session_id,
        worldline="steins_gate",
        conversation_id=str(conv["id"]),
        identity_mode="self",
        source_message_id=int(mid),
        pipeline_version="memory-v11-1",
        provider_id="deepseek",
        model_id="deepseek-chat",
    )
    job_id = job["job_id"]

    async def raise_provider(**kwargs):
        raise RuntimeError("provider down")

    result = await jobs.process_memory_job(
        job_id=job_id,
        lease_owner="worker-A",
        memory_completion=raise_provider,
    )
    assert result["state"] in {"pending", "failed"}
    assert result["state"] != "processing"
    assert result["last_error_code"] == "provider_unavailable"
    assert result["lease_owner"] is None


@pytest.mark.asyncio
async def test_s2_no_completion_without_provider_enters_retry(isolated_store):
    """P1: memory_completion=None without usable provider must not complete."""
    from app import models
    from app.services.conversations import conversation_service

    jobs = _jobs()
    session_id = "s-no-prov"
    conv = await conversation_service.create(
        session_id, "steins_gate", title="noprov", identity_mode="self"
    )
    mid = await models.save_message(
        session_id,
        "user",
        "我喜欢编程",
        worldline="steins_gate",
        conversation_id=str(conv["id"]),
    )
    job = await jobs.enqueue_memory_job(
        session_id=session_id,
        worldline="steins_gate",
        conversation_id=str(conv["id"]),
        identity_mode="self",
        source_message_id=int(mid),
        pipeline_version="memory-v11-1",
        provider_id="deepseek",
        model_id="deepseek-chat",
    )
    # Default path (None → default_memory_completion) uses test credential → unavailable.
    result = await jobs.process_memory_job(
        job_id=job["job_id"],
        lease_owner="worker-A",
        memory_completion=None,
    )
    assert result["state"] in {"pending", "failed"}
    assert result["state"] != "completed"
    assert result["last_error_code"] == "provider_unavailable"


@pytest.mark.asyncio
async def test_s2_transition_rejects_expired_lease(isolated_store):
    """P2: complete/fail CAS requires unexpired lease_expires_at."""
    from app.db import get_db

    jobs = _jobs()
    job = await jobs.enqueue_memory_job(
        session_id="s-exp",
        worldline="steins_gate",
        conversation_id="c-exp",
        identity_mode="self",
        source_message_id=901,
        pipeline_version="memory-v11-1",
        provider_id="deepseek",
        model_id="deepseek-chat",
    )
    job_id = job["job_id"]
    claimed = await jobs.claim_memory_job(job_id=job_id, lease_owner="worker-A")
    assert claimed is not None

    db = await get_db("steins_gate", "memory")
    try:
        await db.execute(
            """UPDATE memory_ingest_jobs
               SET lease_expires_at=?
             WHERE job_id=?""",
            ("2000-01-01T00:00:00+00:00", job_id),
        )
        await db.commit()
    finally:
        await db.close()

    with pytest.raises(jobs.LeaseLostError):
        await jobs._transition_owned(
            job_id=job_id,
            worldline="steins_gate",
            lease_owner="worker-A",
            state="completed",
        )
    stored = await jobs.get_memory_job(job_id)
    assert stored["state"] == "processing"
    assert stored["completed_at"] is None


@pytest.mark.asyncio
async def test_s2_worker_process_due_creates_observation_and_fact(isolated_store):
    """Integration: process_due_jobs_once with real user message → observation+fact."""
    from app import models
    from app.db import get_db
    from app.services.conversations import conversation_service

    jobs = _jobs()
    session_id = "s-worker-int"
    conv = await conversation_service.create(
        session_id, "steins_gate", title="worker-int", identity_mode="self"
    )
    mid = await models.save_message(
        session_id,
        "user",
        "我喜欢黑咖啡",
        worldline="steins_gate",
        conversation_id=str(conv["id"]),
    )
    await jobs.enqueue_memory_job(
        session_id=session_id,
        worldline="steins_gate",
        conversation_id=str(conv["id"]),
        identity_mode="self",
        source_message_id=int(mid),
        pipeline_version="memory-v11-1",
        provider_id="deepseek",
        model_id="deepseek-chat",
    )

    async def structured_completion(**kwargs):
        sid = int(kwargs["source_message_id"])
        return {
            "observations": [
                {
                    "observation_ref": "o1",
                    "source_message_ids": [sid],
                    "display_text": "喜欢黑咖啡",
                    "semantic": {
                        "subject": "user",
                        "predicate": "likes",
                        "object": {"name": "black_coffee"},
                    },
                    "evidence_kind": "direct_user",
                    "memory_class": "stable_candidate",
                    "confidence": 0.96,
                    "topic_label": "饮食",
                }
            ],
            "operations": [
                {
                    "op": "CREATE",
                    "observation_ref": "o1",
                    "target_fact_id": None,
                    "expected_version": None,
                    "fact_text": "喜欢黑咖啡",
                    "reason_code": "direct_durable_statement",
                }
            ],
        }

    results = await jobs.process_due_jobs_once(
        lease_owner="worker-int",
        memory_completion=structured_completion,
        worldlines=("steins_gate",),
    )
    assert results
    assert results[0]["state"] == "completed"

    db = await get_db("steins_gate", "memory")
    try:
        facts = await (
            await db.execute(
                "SELECT COUNT(*) AS n FROM stable_facts WHERE session_id=?",
                (session_id,),
            )
        ).fetchone()
        obs = await (
            await db.execute(
                "SELECT COUNT(*) AS n FROM memory_observations WHERE session_id=?",
                (session_id,),
            )
        ).fetchone()
        texts = await (
            await db.execute(
                """SELECT v.display_text FROM stable_facts f
                   JOIN stable_fact_versions v
                     ON v.fact_id=f.fact_id AND v.version_no=f.active_version
                  WHERE f.session_id=?""",
                (session_id,),
            )
        ).fetchall()
    finally:
        await db.close()
    assert int(facts["n"]) == 1
    assert int(obs["n"]) == 1
    assert any("黑咖啡" in str(row["display_text"]) for row in texts)


@pytest.mark.asyncio
async def test_s2_default_worker_path_uses_selected_model_and_writes(
    isolated_store, monkeypatch
):
    """Default path (memory_completion=None): snapshot(provider, model), one complete_json, writes."""
    from unittest.mock import AsyncMock

    from app import models
    from app.db import get_db
    from app.services import credentials
    from app.services.conversations import conversation_service
    from app.services.provider_registry import (
        ProviderCapabilities,
        ProviderSnapshot,
        ProviderTask,
    )
    from app.services.provider_runtime import provider_registry

    jobs = _jobs()
    session_id = "s-default-worker"
    selected_model = "selected-memory-model-v11"
    conv = await conversation_service.create(
        session_id, "steins_gate", title="default-worker", identity_mode="self"
    )
    mid = await models.save_message(
        session_id,
        "user",
        "我喜欢抹茶",
        worldline="steins_gate",
        conversation_id=str(conv["id"]),
    )
    await jobs.enqueue_memory_job(
        session_id=session_id,
        worldline="steins_gate",
        conversation_id=str(conv["id"]),
        identity_mode="self",
        source_message_id=int(mid),
        pipeline_version="memory-v11-1",
        provider_id="deepseek",
        model_id=selected_model,
    )

    # Non-test credential so default_memory_completion does not short-circuit.
    store = credentials.InMemoryCredentialStore()
    store.set("deepseek", "live-style-key-not-test-prefix")
    monkeypatch.setattr(credentials, "credential_store", store)
    # jobs module imports credential_store at call time from app.services.credentials

    call_kwargs: list[dict] = []

    async def fake_complete_json(**kwargs):
        call_kwargs.append(kwargs)
        sid = int(mid)
        return {
            "observations": [
                {
                    "observation_ref": "o1",
                    "source_message_ids": [sid],
                    "display_text": "喜欢抹茶",
                    "semantic": {
                        "subject": "user",
                        "predicate": "likes",
                        "object": {"name": "matcha"},
                    },
                    "evidence_kind": "direct_user",
                    "memory_class": "stable_candidate",
                    "confidence": 0.97,
                }
            ],
            "operations": [
                {
                    "op": "CREATE",
                    "observation_ref": "o1",
                    "target_fact_id": None,
                    "expected_version": None,
                    "fact_text": "喜欢抹茶",
                    "reason_code": "direct_durable_statement",
                }
            ],
        }

    adapter = AsyncMock()
    adapter.complete_json = AsyncMock(side_effect=fake_complete_json)

    def fake_snapshot(provider_id, model_id=None, **kwargs):
        return ProviderSnapshot(
            provider_id=provider_id,
            model_id=model_id or "fallback-default",
            capabilities=ProviderCapabilities(
                tasks=frozenset({ProviderTask.MEMORY, ProviderTask.CHAT}),
                structured_output=True,
            ),
            adapter=adapter,
        )

    monkeypatch.setattr(provider_registry, "snapshot", fake_snapshot)

    results = await jobs.process_due_jobs_once(
        lease_owner="worker-default",
        memory_completion=None,  # force default_memory_completion
        worldlines=("steins_gate",),
    )
    assert results and results[0]["state"] == "completed"
    adapter.complete_json.assert_awaited_once()
    assert adapter.complete_json.await_args.kwargs["model"] == selected_model
    assert len(call_kwargs) == 1

    db = await get_db("steins_gate", "memory")
    try:
        facts = await (
            await db.execute(
                "SELECT COUNT(*) AS n FROM stable_facts WHERE session_id=?",
                (session_id,),
            )
        ).fetchone()
        obs = await (
            await db.execute(
                "SELECT COUNT(*) AS n FROM memory_observations WHERE session_id=?",
                (session_id,),
            )
        ).fetchone()
    finally:
        await db.close()
    assert int(facts["n"]) == 1
    assert int(obs["n"]) == 1


@pytest.mark.asyncio
async def test_s2_source_replay_is_idempotent(isolated_store):
    """P1: replaying the same source/job does not create duplicate facts/observations."""
    from app import models
    from app.db import get_db
    from app.services.conversations import conversation_service

    jobs = _jobs()
    recon = importlib.import_module("app.services.memory_v11.reconciler")
    session_id = "s-replay"
    conv = await conversation_service.create(
        session_id, "steins_gate", title="replay", identity_mode="self"
    )
    mid = await models.save_message(
        session_id,
        "user",
        "我喜欢咖啡",
        worldline="steins_gate",
        conversation_id=str(conv["id"]),
    )
    ops = {
        "observations": [
            {
                "observation_ref": "o1",
                "source_message_ids": [int(mid)],
                "display_text": "喜欢咖啡",
                "semantic": {
                    "subject": "user",
                    "predicate": "likes",
                    "object": {"name": "coffee"},
                },
                "evidence_kind": "direct_user",
                "memory_class": "stable_candidate",
                "confidence": 0.95,
            }
        ],
        "operations": [
            {
                "op": "CREATE",
                "observation_ref": "o1",
                "target_fact_id": None,
                "expected_version": None,
                "fact_text": "喜欢咖啡",
                "reason_code": "direct_durable_statement",
            }
        ],
    }

    async def completion(**kwargs):
        return ops

    job = await jobs.enqueue_memory_job(
        session_id=session_id,
        worldline="steins_gate",
        conversation_id=str(conv["id"]),
        identity_mode="self",
        source_message_id=int(mid),
        pipeline_version="memory-v11-1",
        provider_id="deepseek",
        model_id="deepseek-chat",
    )
    job_id = job["job_id"]
    first = await jobs.process_memory_job(
        job_id=job_id, lease_owner="worker-A", memory_completion=completion
    )
    assert first["state"] == "completed"
    second = await jobs.process_memory_job(
        job_id=job_id, lease_owner="worker-B", memory_completion=completion
    )
    assert second["state"] == "completed"

    # Direct apply replay with same source must also be idempotent.
    await recon.apply_validated_memory_operations(
        session_id=session_id,
        worldline="steins_gate",
        identity_mode="self",
        observations=ops["observations"],
        operations=ops["operations"],
        source_evidence=[
            {
                "source_message_id": int(mid),
                "conversation_id": str(conv["id"]),
                "source_role": "user",
                "source_fingerprint": "fp-coffee",
                "source_created_at": "2026-08-02T00:00:00+00:00",
                "excerpt": "我喜欢咖啡",
            }
        ],
        candidate_allowlist=[],
    )

    db = await get_db("steins_gate", "memory")
    try:
        facts = await (
            await db.execute(
                "SELECT COUNT(*) AS n FROM stable_facts WHERE session_id=?",
                (session_id,),
            )
        ).fetchone()
        obs = await (
            await db.execute(
                "SELECT COUNT(*) AS n FROM memory_observations WHERE session_id=?",
                (session_id,),
            )
        ).fetchone()
    finally:
        await db.close()
    assert int(facts["n"]) == 1
    assert int(obs["n"]) == 1


# ---------------------------------------------------------------------------
# MEMORY-V11-S3B-3: pre-completion same-scope candidate context
# ---------------------------------------------------------------------------


async def _enqueue_user_job(
    *,
    session_id: str,
    user_text: str,
    identity_mode: str = "self",
    title: str = "s3b3",
) -> tuple[Any, str, int]:
    jobs = _jobs()
    conv = await conversation_service.create(
        session_id, "steins_gate", title=title, identity_mode=identity_mode
    )
    mid = await models.save_message(
        session_id,
        "user",
        user_text,
        worldline="steins_gate",
        conversation_id=str(conv["id"]),
    )
    job = await jobs.enqueue_memory_job(
        session_id=session_id,
        worldline="steins_gate",
        conversation_id=str(conv["id"]),
        identity_mode=identity_mode,
        source_message_id=int(mid),
        pipeline_version="memory-v11-1",
        provider_id="deepseek",
        model_id="deepseek-chat",
    )
    return job, str(conv["id"]), int(mid)


@pytest.mark.asyncio
async def test_s3b3_candidates_arrive_before_completion(isolated_store, monkeypatch):
    """A: completion receives bounded same-scope candidates only."""
    jobs = _jobs()
    session_id = f"s3b3-cand-{uuid4().hex[:8]}"
    relevant = await create_stable_fact(
        session_id=session_id,
        worldline="steins_gate",
        identity_mode="self",
        display_text="喜欢看动漫",
        semantic_json={"subject": "user", "relation": "likes", "object": "anime"},
    )
    await create_stable_fact(
        session_id=session_id,
        worldline="steins_gate",
        identity_mode="self",
        display_text="数学の本を読んでいる",
        semantic_json={"subject": "user", "relation": "study", "object": "math"},
    )
    okabe = await create_stable_fact(
        session_id=session_id,
        worldline="steins_gate",
        identity_mode="okabe",
        display_text="喜欢看动漫",
        semantic_json={"subject": "user", "relation": "likes", "object": "anime"},
    )

    captured: dict[str, Any] = {}

    async def fake_completion(**kwargs: Any) -> dict[str, Any]:
        captured["candidates"] = list(kwargs.get("candidates") or [])
        captured["user_text"] = kwargs.get("user_text")
        return {"observations": [], "operations": []}

    job, _, _ = await _enqueue_user_job(
        session_id=session_id,
        # Query must lexically hit display_text (retrieval uses substring terms).
        user_text="喜欢看动漫",
        title="cand",
    )
    result = await jobs.process_memory_job(
        job_id=job["job_id"],
        lease_owner="w-cand",
        memory_completion=fake_completion,
    )
    assert result["state"] == "completed"
    cands = captured["candidates"]
    ids = {str(c["fact_id"]) for c in cands}
    assert relevant["fact_id"] in ids
    assert okabe["fact_id"] not in ids
    assert all("数学" not in str(c.get("display_text") or "") for c in cands)
    for c in cands:
        assert set(c.keys()) >= {"fact_id", "version_no", "display_text", "is_pinned"}
        assert isinstance(c["version_no"], int) and c["version_no"] >= 1
    hit = next(c for c in cands if c["fact_id"] == relevant["fact_id"])
    assert hit["display_text"] == "喜欢看动漫"
    assert hit["version_no"] == 1


@pytest.mark.asyncio
async def test_s3b3_selected_target_refine_succeeds(isolated_store, monkeypatch):
    """B: REFINE on a preselected candidate applies successfully (fully offline)."""
    jobs = _jobs()
    # Explicit deterministic stub — never the real SentenceTransformer embedder.
    embedder = _DeterministicEmbedder()
    monkeypatch.setattr(memory_service, "embedder", embedder)
    assert isinstance(memory_service.embedder, _DeterministicEmbedder)

    session_id = f"s3b3-refine-{uuid4().hex[:8]}"
    text_v1 = "喜欢咖啡"
    text_v2 = "喜欢黑咖啡"
    seed = await create_stable_fact(
        session_id=session_id,
        worldline="steins_gate",
        identity_mode="self",
        display_text=text_v1,
        semantic_json={"subject": "user", "relation": "likes", "object": "coffee"},
    )
    vec_v1 = await embedder.encode_passage(text_v1)
    db = await get_db("steins_gate", "memory")
    try:
        await db.execute(
            """INSERT INTO stable_fact_version_embeddings(
                   fact_id, version_no, model, dimensions, vector
               ) VALUES(?,?,?,?,?)""",
            (
                seed["fact_id"],
                1,
                embedder.model_name,
                384,
                struct.pack(f"{384}f", *vec_v1),
            ),
        )
        await db.commit()
    finally:
        await db.close()

    async def refine_completion(**kwargs: Any) -> dict[str, Any]:
        cands = list(kwargs.get("candidates") or [])
        assert any(c["fact_id"] == seed["fact_id"] for c in cands)
        target = next(c for c in cands if c["fact_id"] == seed["fact_id"])
        sid = int(kwargs["source_message_id"])
        return {
            "observations": [
                {
                    "observation_ref": "o1",
                    "source_message_ids": [sid],
                    "display_text": text_v2,
                    "semantic": {
                        "subject": "user",
                        "predicate": "likes",
                        "object": {"name": "black_coffee"},
                    },
                    "evidence_kind": "direct_user",
                    "memory_class": "stable_candidate",
                    "confidence": 0.96,
                }
            ],
            "operations": [
                {
                    "op": "REFINE",
                    "observation_ref": "o1",
                    "target_fact_id": target["fact_id"],
                    "expected_version": target["version_no"],
                    "fact_text": text_v2,
                    "reason_code": "compatible_refinement",
                }
            ],
        }

    job, _, _ = await _enqueue_user_job(
        session_id=session_id,
        user_text=text_v1,
        title="refine",
    )
    result = await jobs.process_memory_job(
        job_id=job["job_id"],
        lease_owner="w-refine",
        memory_completion=refine_completion,
    )
    assert result["state"] == "completed"
    expected_v2 = struct.pack(f"{384}f", *(await embedder.encode_passage(text_v2)))
    db = await get_db("steins_gate", "memory")
    try:
        cur = await db.execute(
            "SELECT active_version FROM stable_facts WHERE fact_id=?",
            (seed["fact_id"],),
        )
        assert int((await cur.fetchone())["active_version"]) == 2
        cur = await db.execute(
            """SELECT display_text FROM stable_fact_versions
                WHERE fact_id=? AND version_no=2""",
            (seed["fact_id"],),
        )
        assert "黑咖啡" in str((await cur.fetchone())["display_text"])
        # v1 vector removed; v2 vector present under the same offline model_name.
        cur = await db.execute(
            """SELECT COUNT(*) AS n FROM stable_fact_version_embeddings
                WHERE fact_id=? AND version_no=1""",
            (seed["fact_id"],),
        )
        assert int((await cur.fetchone())["n"]) == 0
        cur = await db.execute(
            """SELECT model, dimensions, vector FROM stable_fact_version_embeddings
                WHERE fact_id=? AND version_no=2""",
            (seed["fact_id"],),
        )
        emb = await cur.fetchone()
        assert emb is not None
        assert emb["model"] == embedder.model_name
        assert int(emb["dimensions"]) == 384
        assert bytes(emb["vector"]) == expected_v2
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_s3b3_unselected_same_scope_target_rejected(isolated_store, monkeypatch):
    """C: unselected same-scope fact_id fails validation; no mutation."""
    jobs = _jobs()
    session_id = f"s3b3-unsel-{uuid4().hex[:8]}"
    selected = await create_stable_fact(
        session_id=session_id,
        worldline="steins_gate",
        identity_mode="self",
        display_text="喜欢看动漫",
        semantic_json={"subject": "user", "relation": "likes", "object": "anime"},
    )
    unselected = await create_stable_fact(
        session_id=session_id,
        worldline="steins_gate",
        identity_mode="self",
        display_text="数学の本を読んでいる",
        semantic_json={"subject": "user", "relation": "study", "object": "math"},
    )

    async def malicious_completion(**kwargs: Any) -> dict[str, Any]:
        cands = list(kwargs.get("candidates") or [])
        assert all(c["fact_id"] != unselected["fact_id"] for c in cands)
        assert any(c["fact_id"] == selected["fact_id"] for c in cands)
        sid = int(kwargs["source_message_id"])
        return {
            "observations": [
                {
                    "observation_ref": "o1",
                    "source_message_ids": [sid],
                    "display_text": "喜欢拓扑学",
                    "semantic": {
                        "subject": "user",
                        "predicate": "likes",
                        "object": {"name": "topology"},
                    },
                    "evidence_kind": "direct_user",
                    "memory_class": "stable_candidate",
                    "confidence": 0.96,
                }
            ],
            "operations": [
                {
                    "op": "REFINE",
                    "observation_ref": "o1",
                    "target_fact_id": unselected["fact_id"],
                    "expected_version": 1,
                    "fact_text": "喜欢拓扑学",
                    "reason_code": "malicious_unselected",
                }
            ],
        }

    job, _, _ = await _enqueue_user_job(
        session_id=session_id,
        user_text="喜欢看动漫",
        title="unsel",
    )
    result = await jobs.process_memory_job(
        job_id=job["job_id"],
        lease_owner="w-unsel",
        memory_completion=malicious_completion,
    )
    assert result["state"] != "completed"
    assert result["state"] in {"pending", "failed"}
    assert result.get("last_error_code") == "validation_failed"
    db = await get_db("steins_gate", "memory")
    try:
        cur = await db.execute(
            "SELECT active_version FROM stable_facts WHERE fact_id=?",
            (unselected["fact_id"],),
        )
        assert int((await cur.fetchone())["active_version"]) == 1
        cur = await db.execute(
            """SELECT invalid_at FROM stable_fact_versions
                WHERE fact_id=? AND version_no=1""",
            (unselected["fact_id"],),
        )
        assert (await cur.fetchone())["invalid_at"] is None
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_s3b3_cross_identity_target_impossible(isolated_store, monkeypatch):
    """D: self job never lists okabe candidates; okabe target is rejected."""
    jobs = _jobs()
    session_id = f"s3b3-xident-{uuid4().hex[:8]}"
    self_fact = await create_stable_fact(
        session_id=session_id,
        worldline="steins_gate",
        identity_mode="self",
        display_text="喜欢看动漫",
        semantic_json={"subject": "user", "relation": "likes", "object": "anime"},
    )
    okabe_fact = await create_stable_fact(
        session_id=session_id,
        worldline="steins_gate",
        identity_mode="okabe",
        display_text="喜欢看动漫",
        semantic_json={"subject": "user", "relation": "likes", "object": "anime"},
    )

    async def cross_completion(**kwargs: Any) -> dict[str, Any]:
        cands = list(kwargs.get("candidates") or [])
        assert all(c["fact_id"] != okabe_fact["fact_id"] for c in cands)
        sid = int(kwargs["source_message_id"])
        return {
            "observations": [
                {
                    "observation_ref": "o1",
                    "source_message_ids": [sid],
                    "display_text": "喜欢动漫",
                    "semantic": {
                        "subject": "user",
                        "predicate": "likes",
                        "object": {"name": "anime"},
                    },
                    "evidence_kind": "direct_user",
                    "memory_class": "stable_candidate",
                    "confidence": 0.96,
                }
            ],
            "operations": [
                {
                    "op": "ATTACH",
                    "observation_ref": "o1",
                    "target_fact_id": okabe_fact["fact_id"],
                    "expected_version": 1,
                    "fact_text": "喜欢动漫",
                    "reason_code": "cross_identity_attempt",
                }
            ],
        }

    job, _, _ = await _enqueue_user_job(
        session_id=session_id,
        user_text="喜欢看动漫",
        title="xident",
    )
    result = await jobs.process_memory_job(
        job_id=job["job_id"],
        lease_owner="w-xident",
        memory_completion=cross_completion,
    )
    assert result["state"] != "completed"
    db = await get_db("steins_gate", "memory")
    try:
        cur = await db.execute(
            """SELECT COUNT(*) AS n FROM stable_fact_evidence e
                 WHERE e.fact_id=?""",
            (okabe_fact["fact_id"],),
        )
        assert int((await cur.fetchone())["n"]) == 0
        cur = await db.execute(
            "SELECT active_version FROM stable_facts WHERE fact_id=?",
            (self_fact["fact_id"],),
        )
        assert int((await cur.fetchone())["active_version"]) == 1
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_s3b3_empty_candidates_allows_create(isolated_store, monkeypatch):
    """E: candidates [] still allows CREATE with one completion + vector row."""
    jobs = _jobs()
    session_id = f"s3b3-empty-{uuid4().hex[:8]}"
    call_count = {"n": 0}

    async def create_completion(**kwargs: Any) -> dict[str, Any]:
        call_count["n"] += 1
        assert list(kwargs.get("candidates") or []) == []
        sid = int(kwargs["source_message_id"])
        return {
            "observations": [
                {
                    "observation_ref": "o1",
                    "source_message_ids": [sid],
                    "display_text": "喜欢抹茶",
                    "semantic": {
                        "subject": "user",
                        "predicate": "likes",
                        "object": {"name": "matcha"},
                    },
                    "evidence_kind": "direct_user",
                    "memory_class": "stable_candidate",
                    "confidence": 0.97,
                }
            ],
            "operations": [
                {
                    "op": "CREATE",
                    "observation_ref": "o1",
                    "target_fact_id": None,
                    "expected_version": None,
                    "fact_text": "喜欢抹茶",
                    "reason_code": "direct_durable_statement",
                }
            ],
        }

    job, _, _ = await _enqueue_user_job(
        session_id=session_id,
        user_text="我喜欢抹茶",
        title="empty",
    )
    result = await jobs.process_memory_job(
        job_id=job["job_id"],
        lease_owner="w-empty",
        memory_completion=create_completion,
    )
    assert result["state"] == "completed"
    assert call_count["n"] == 1
    db = await get_db("steins_gate", "memory")
    try:
        cur = await db.execute(
            "SELECT fact_id FROM stable_facts WHERE session_id=?",
            (session_id,),
        )
        rows = await cur.fetchall()
        assert len(rows) == 1
        fact_id = rows[0]["fact_id"]
        cur = await db.execute(
            """SELECT COUNT(*) AS n FROM stable_fact_version_embeddings
                WHERE fact_id=? AND version_no=1""",
            (fact_id,),
        )
        assert int((await cur.fetchone())["n"]) == 1
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_s3b3_one_completion_only(isolated_store, monkeypatch):
    """F: one job → completion_fn called exactly once."""
    jobs = _jobs()
    session_id = f"s3b3-once-{uuid4().hex[:8]}"
    call_count = {"n": 0}

    async def counting_completion(**kwargs: Any) -> dict[str, Any]:
        call_count["n"] += 1
        return {"observations": [], "operations": []}

    job, _, _ = await _enqueue_user_job(
        session_id=session_id,
        user_text="你好",
        title="once",
    )
    result = await jobs.process_memory_job(
        job_id=job["job_id"],
        lease_owner="w-once",
        memory_completion=counting_completion,
    )
    assert result["state"] == "completed"
    assert call_count["n"] == 1


@pytest.mark.asyncio
async def test_s3b3_default_provider_payload_includes_candidates(
    isolated_store, monkeypatch
):
    """G: default_memory_completion user JSON includes candidates."""
    from unittest.mock import AsyncMock

    from app.services import credentials
    from app.services.provider_registry import (
        ProviderCapabilities,
        ProviderSnapshot,
        ProviderTask,
    )
    from app.services.provider_runtime import provider_registry

    jobs = _jobs()
    session_id = f"s3b3-default-{uuid4().hex[:8]}"
    seed = await create_stable_fact(
        session_id=session_id,
        worldline="steins_gate",
        identity_mode="self",
        display_text="喜欢动漫",
        semantic_json={"subject": "user", "relation": "likes", "object": "anime"},
    )
    store = credentials.InMemoryCredentialStore()
    store.set("deepseek", "live-style-key-not-test-prefix")
    monkeypatch.setattr(credentials, "credential_store", store)

    captured_messages: list[Any] = []

    async def fake_complete_json(**kwargs: Any):
        captured_messages.append(kwargs.get("messages"))
        return {"observations": [], "operations": []}

    adapter = AsyncMock()
    adapter.complete_json = AsyncMock(side_effect=fake_complete_json)

    def fake_snapshot(provider_id, model_id=None, **kwargs):
        return ProviderSnapshot(
            provider_id=provider_id,
            model_id=model_id or "fallback",
            capabilities=ProviderCapabilities(
                tasks=frozenset({ProviderTask.MEMORY, ProviderTask.CHAT}),
                structured_output=True,
            ),
            adapter=adapter,
        )

    monkeypatch.setattr(provider_registry, "snapshot", fake_snapshot)

    job, _, _ = await _enqueue_user_job(
        session_id=session_id,
        user_text="喜欢动漫",
        title="default-cand",
    )
    result = await jobs.process_memory_job(
        job_id=job["job_id"],
        lease_owner="w-default",
        memory_completion=None,
    )
    assert result["state"] == "completed"
    adapter.complete_json.assert_awaited_once()
    user_content = captured_messages[0][1]["content"]
    payload = json.loads(user_content)
    assert "candidates" in payload
    assert any(c.get("fact_id") == seed["fact_id"] for c in payload["candidates"])
    cand = next(c for c in payload["candidates"] if c["fact_id"] == seed["fact_id"])
    assert set(cand.keys()) >= {"fact_id", "version_no", "display_text", "is_pinned"}


@pytest.mark.asyncio
async def test_s3b3_candidate_selection_failure_skips_provider(
    isolated_store, monkeypatch
):
    """Selection raise → no completion call; failed_or_retry + validation_failed."""
    jobs = _jobs()
    session_id = f"s3b3-sel-fail-{uuid4().hex[:8]}"
    seed = await create_stable_fact(
        session_id=session_id,
        worldline="steins_gate",
        identity_mode="self",
        display_text="喜欢动漫",
        semantic_json={"subject": "user", "relation": "likes", "object": "anime"},
    )

    async def boom_select(**kwargs: Any):
        raise RuntimeError("forced candidate selection failure")

    monkeypatch.setattr(
        "app.services.memory_v11.retrieval.select_stable_fact_candidates",
        boom_select,
    )

    call_count = {"n": 0}

    async def must_not_run(**kwargs: Any) -> dict[str, Any]:
        call_count["n"] += 1
        return {"observations": [], "operations": []}

    job, _, _ = await _enqueue_user_job(
        session_id=session_id,
        user_text="喜欢动漫",
        title="sel-fail",
    )
    result = await jobs.process_memory_job(
        job_id=job["job_id"],
        lease_owner="w-sel-fail",
        memory_completion=must_not_run,
    )
    assert call_count["n"] == 0, "provider/completion must not run after selection fail"
    assert result["state"] != "processing"
    assert result["state"] in {"pending", "failed"}
    assert result.get("last_error_code") == "validation_failed"

    db = await get_db("steins_gate", "memory")
    try:
        cur = await db.execute(
            "SELECT active_version, state FROM stable_facts WHERE fact_id=?",
            (seed["fact_id"],),
        )
        row = await cur.fetchone()
        assert int(row["active_version"]) == 1
        assert row["state"] == "active"
        cur = await db.execute(
            "SELECT COUNT(*) AS n FROM memory_observations WHERE session_id=?",
            (session_id,),
        )
        assert int((await cur.fetchone())["n"]) == 0
        cur = await db.execute(
            """SELECT COUNT(*) AS n FROM stable_fact_evidence e
                 JOIN stable_facts f ON f.fact_id=e.fact_id
                WHERE f.session_id=?""",
            (session_id,),
        )
        assert int((await cur.fetchone())["n"]) == 0
        cur = await db.execute(
            """SELECT COUNT(*) AS n FROM stable_fact_version_embeddings e
                 JOIN stable_facts f ON f.fact_id=e.fact_id
                WHERE f.session_id=?""",
            (session_id,),
        )
        assert int((await cur.fetchone())["n"]) == 0
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_s3b3_vector_only_candidate_reaches_completion(
    isolated_store, monkeypatch
):
    """H: zero lexical + compatible high-sim vector → in completion candidates."""
    jobs = _jobs()
    session_id = f"s3b3-vec-{uuid4().hex[:8]}"
    query_text = "ペット"
    target_text = "犬を飼っている"
    embedder = _DeterministicEmbedder(
        vocabulary={query_text: "pet", target_text: "pet"}
    )
    monkeypatch.setattr(memory_service, "embedder", embedder)

    target = await create_stable_fact(
        session_id=session_id,
        worldline="steins_gate",
        identity_mode="self",
        display_text=target_text,
        semantic_json={"subject": "user", "relation": "has_pet", "object": "dog"},
        confidence=0.5,
    )
    for i in range(29):
        await create_stable_fact(
            session_id=session_id,
            worldline="steins_gate",
            identity_mode="self",
            display_text=f"FILLER_{i:02d} study notes about topology",
            semantic_json={
                "subject": "user",
                "relation": "study",
                "object": f"topic_{i:02d}",
            },
            confidence=0.9,
        )

    db = await get_db("steins_gate", "memory")
    try:
        cur = await db.execute(
            """SELECT f.fact_id, v.display_text FROM stable_facts f
                 JOIN stable_fact_versions v
                   ON v.fact_id=f.fact_id AND v.version_no=f.active_version
                WHERE f.session_id=? AND f.identity_mode='self' AND f.state='active'""",
            (session_id,),
        )
        rows = await cur.fetchall()
        for row in rows:
            text = str(row["display_text"])
            vector = await embedder.encode_passage(text)
            await db.execute(
                """INSERT INTO stable_fact_version_embeddings(
                       fact_id, version_no, model, dimensions, vector
                   ) VALUES(?,?,?,?,?)""",
                (
                    row["fact_id"],
                    1,
                    embedder.model_name,
                    384,
                    struct.pack(f"{384}f", *vector),
                ),
            )
        await db.commit()
    finally:
        await db.close()

    captured: dict[str, Any] = {}

    async def vec_completion(**kwargs: Any) -> dict[str, Any]:
        captured["candidates"] = list(kwargs.get("candidates") or [])
        return {"observations": [], "operations": []}

    job, _, _ = await _enqueue_user_job(
        session_id=session_id,
        user_text=query_text,
        title="vec",
    )
    result = await jobs.process_memory_job(
        job_id=job["job_id"],
        lease_owner="w-vec",
        memory_completion=vec_completion,
    )
    assert result["state"] == "completed"
    ids = {c["fact_id"] for c in captured["candidates"]}
    assert target["fact_id"] in ids


# ---------------------------------------------------------------------------
# MEMORY-V11-S3D-PROBE: D18 bounded reanalysis (plan §6.4 / R08).
#
# Permanent RED contracts — the current job payload carries stable-fact
# candidates only (jobs.default_memory_completion / process_memory_job), and
# the reconciler always INSERTs a new observation with reanalysis_count=0
# (_insert_observation). Nothing today:
#   - carries bounded unresolved-observation context into the single completion
#   - updates the EXISTING observation on resolution
#   - increments reanalysis_count / respects its 0..1 cap
#
# Unspecified surface → Coordinator decision point (NOT guessed here):
#   - the exact context key name carried into the completion (the probe
#     accepts a small documented alias set)
#   - the bound on how many unresolved observations are offered
#   - the resolution op shape returned by the model
# ---------------------------------------------------------------------------


async def _seed_unresolved_candidate(
    *,
    session_id: str,
    identity_mode: str,
    conversation_id: str,
    source_message_id: int,
    display_text: str,
) -> str:
    """Low-confidence CREATE → status='candidate', reanalysis_count=0, source linked."""
    recon = importlib.import_module("app.services.memory_v11.reconciler")
    await recon.apply_validated_memory_operations(
        session_id=session_id,
        worldline="steins_gate",
        identity_mode=identity_mode,
        observations=[
            {
                "observation_ref": "o1",
                "source_message_ids": [int(source_message_id)],
                "display_text": display_text,
                "semantic": {
                    "subject": "user",
                    "predicate": "watching",
                    "object": {"kind": "anime"},
                },
                "evidence_kind": "direct_user",
                "memory_class": "stable_candidate",
                "confidence": 0.4,
                "topic_label": "动漫",
                "expires_at": None,
            }
        ],
        operations=[
            {
                "op": "CREATE",
                "observation_ref": "o1",
                "target_fact_id": None,
                "expected_version": None,
                "fact_text": display_text,
                "reason_code": "unresolved_placeholder",
            }
        ],
        source_evidence=[
            {
                "source_message_id": int(source_message_id),
                "conversation_id": conversation_id,
                "source_role": "user",
                "source_fingerprint": f"fp-{display_text[:12]}",
                "source_created_at": "2026-08-01T00:00:00+00:00",
                "excerpt": display_text,
            }
        ],
        candidate_allowlist=[],
    )
    db = await get_db("steins_gate", "memory")
    try:
        row = await (
            await db.execute(
                """SELECT observation_id FROM memory_observations
                    WHERE session_id=? AND identity_mode=?
                      AND display_text=? AND reanalysis_count=0
                    ORDER BY created_at DESC LIMIT 1""",
                (session_id, identity_mode, display_text),
            )
        ).fetchone()
        assert row is not None
        return str(row["observation_id"])
    finally:
        await db.close()


def _unresolved_context(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Locate the unresolved-observation context under any documented key.

    Key naming is a Coordinator decision point; the probe accepts the small
    alias set below and fails with a clear message when none is present.
    """
    for key in ("unresolved_observations", "unresolved_context", "pending_observations"):
        value = payload.get(key)
        if isinstance(value, list):
            return value
    return []


@pytest.mark.asyncio
async def test_s3d_probe_d18_completion_receives_bounded_unresolved_context(
    isolated_store, monkeypatch
):
    """R08/§6.4 (input side): the SINGLE completion receives bounded unresolved context.

    Scenario: same-scope candidate with reanalysis_count=0; new message
    resolves it. RED: today the payload carries stable-fact candidates only
    (jobs.py default_memory_completion) — no unresolved-observation context.
    """
    jobs = _jobs()
    session_id = f"s3d-d18-in-{uuid4().hex[:8]}"
    conv = await conversation_service.create(
        session_id, "steins_gate", title="d18-in", identity_mode="self"
    )
    conversation_id = str(conv["id"])
    mid_vague = await models.save_message(
        session_id,
        "user",
        "我最近在看一部动漫",
        worldline="steins_gate",
        conversation_id=conversation_id,
    )
    mid_resolves = await models.save_message(
        session_id,
        "user",
        "那部动漫是《为美好的世界献上祝福》",
        worldline="steins_gate",
        conversation_id=conversation_id,
    )
    # Unresolved candidate (reanalysis_count=0) — must be offered.
    obs_offer = await _seed_unresolved_candidate(
        session_id=session_id,
        identity_mode="self",
        conversation_id=conversation_id,
        source_message_id=int(mid_vague),
        display_text="在看一部动漫",
    )
    # Already-reanalysed candidate (cap hit) — must NEVER be offered again.
    obs_done = await _seed_unresolved_candidate(
        session_id=session_id,
        identity_mode="self",
        conversation_id=conversation_id,
        source_message_id=int(mid_vague) + 1,
        display_text="在玩一个游戏",
    )
    db = await get_db("steins_gate", "memory")
    try:
        await db.execute(
            """UPDATE memory_observations SET reanalysis_count=1, status='candidate'
                WHERE observation_id=?""",
            (obs_done,),
        )
        await db.commit()
    finally:
        await db.close()
    # Cross-identity candidate must never enter a self-scope context.
    obs_okabe = await _seed_unresolved_candidate(
        session_id=session_id,
        identity_mode="okabe",
        conversation_id=conversation_id,
        source_message_id=int(mid_vague) + 2,
        display_text="在看科幻小说",
    )
    assert obs_offer != obs_done and obs_offer != obs_okabe

    call_count = {"n": 0}
    captured: dict[str, Any] = {}

    async def d18_completion(**kwargs: Any) -> dict[str, Any]:
        call_count["n"] += 1
        captured["payload"] = kwargs
        return {"observations": [], "operations": []}

    job = await jobs.enqueue_memory_job(
        session_id=session_id,
        worldline="steins_gate",
        conversation_id=conversation_id,
        identity_mode="self",
        source_message_id=int(mid_resolves),
        pipeline_version="memory-v11-1",
        provider_id="deepseek",
        model_id="deepseek-chat",
    )
    result = await jobs.process_memory_job(
        job_id=job["job_id"],
        lease_owner="w-d18-in",
        memory_completion=d18_completion,
    )
    assert result["state"] == "completed"
    assert call_count["n"] == 1, "D18 stays inside the single-completion rule (R11)"
    unresolved = _unresolved_context(captured["payload"])
    assert unresolved, (
        "D18: completion payload carries no bounded unresolved-observation "
        "context; job payload currently contains stable-fact candidates only "
        "(plan §6.4)"
    )
    offered_ids = {str(u.get("observation_id") or u.get("id") or "") for u in unresolved}
    assert obs_offer in offered_ids, (
        "unresolved candidate (reanalysis_count=0) must be offered for reanalysis"
    )
    assert obs_done not in offered_ids, (
        "observation with reanalysis_count=1 must never be offered again (R08)"
    )
    assert obs_okabe not in offered_ids, (
        "D18 context must stay same-scope; okabe observation leaked in (R05)"
    )


@pytest.mark.asyncio
async def test_s3d_probe_d18_resolution_updates_existing_observation(
    isolated_store, monkeypatch
):
    """R08/§6.4 (output side): resolution updates the EXISTING observation.

    After a resolving turn, the original observation row must carry
    reanalysis_count=1 and no parallel duplicate observation may appear for
    the old source. RED: reconciler._insert_observation always inserts a new
    row with reanalysis_count=0; nothing updates the existing candidate.
    """
    jobs = _jobs()
    session_id = f"s3d-d18-out-{uuid4().hex[:8]}"
    conv = await conversation_service.create(
        session_id, "steins_gate", title="d18-out", identity_mode="self"
    )
    conversation_id = str(conv["id"])
    mid_vague = await models.save_message(
        session_id,
        "user",
        "我最近在看一部动漫",
        worldline="steins_gate",
        conversation_id=conversation_id,
    )
    mid_resolves = await models.save_message(
        session_id,
        "user",
        "那部动漫是《为美好的世界献上祝福》",
        worldline="steins_gate",
        conversation_id=conversation_id,
    )
    obs_id = await _seed_unresolved_candidate(
        session_id=session_id,
        identity_mode="self",
        conversation_id=conversation_id,
        source_message_id=int(mid_vague),
        display_text="在看一部动漫",
    )

    async def resolve_completion(**kwargs: Any) -> dict[str, Any]:
        sid = int(kwargs["source_message_id"])
        return {
            "observations": [
                {
                    "observation_ref": "o1",
                    "source_message_ids": [sid],
                    "display_text": "喜欢《为美好的世界献上祝福》",
                    "semantic": {
                        "subject": "user",
                        "predicate": "likes_work",
                        "object": {"name": "konosuba"},
                    },
                    "evidence_kind": "direct_user",
                    "memory_class": "stable_candidate",
                    "confidence": 0.96,
                    "topic_label": "动漫",
                    "expires_at": None,
                }
            ],
            "operations": [
                {
                    "op": "CREATE",
                    "observation_ref": "o1",
                    "target_fact_id": None,
                    "expected_version": None,
                    "fact_text": "喜欢《为美好的世界献上祝福》",
                    "reason_code": "direct_durable_statement",
                }
            ],
        }

    job = await jobs.enqueue_memory_job(
        session_id=session_id,
        worldline="steins_gate",
        conversation_id=conversation_id,
        identity_mode="self",
        source_message_id=int(mid_resolves),
        pipeline_version="memory-v11-1",
        provider_id="deepseek",
        model_id="deepseek-chat",
    )
    result = await jobs.process_memory_job(
        job_id=job["job_id"],
        lease_owner="w-d18-out",
        memory_completion=resolve_completion,
    )
    assert result["state"] == "completed"

    db = await get_db("steins_gate", "memory")
    try:
        row = await (
            await db.execute(
                """SELECT reanalysis_count, status FROM memory_observations
                    WHERE observation_id=? AND session_id=?""",
                (obs_id, session_id),
            )
        ).fetchone()
        assert row is not None
        assert int(row["reanalysis_count"]) == 1, (
            "D18: resolving turn must update the EXISTING observation and set "
            f"reanalysis_count=1; still reanalysis_count={row['reanalysis_count']} "
            "status=" + str(row["status"]) + " (plan §6.4 / R08)"
        )
        parallel = await (
            await db.execute(
                """SELECT COUNT(*) AS n FROM memory_observation_sources s
                    JOIN memory_observations o ON o.observation_id = s.observation_id
                   WHERE s.source_message_id=? AND o.session_id=?
                     AND o.observation_id != ?""",
                (int(mid_vague), session_id, obs_id),
            )
        ).fetchone()
        assert int(parallel["n"]) == 0, (
            "D18: no parallel duplicate observation may be created for the old source"
        )
    finally:
        await db.close()
