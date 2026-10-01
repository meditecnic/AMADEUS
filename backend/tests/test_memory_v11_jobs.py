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


def test_memory_mode_defaults_to_legacy_until_cutover(monkeypatch):
    from app.services.memory_v11.jobs import memory_mode
    monkeypatch.delenv('AMADEUS_MEMORY_MODE', raising=False)
    assert memory_mode() == 'legacy'
    for mode in ('shadow', 'v11'):
        monkeypatch.setenv('AMADEUS_MEMORY_MODE', mode)
        assert memory_mode() == mode


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

    Canonical key ``unresolved_observations`` only. Real prior-source
    chronology: every exclusion fixture's source_message_id is strictly BEFORE
    the resolving current message, so count=1 is proven by count, okabe by
    identity — never by accidental future-source ordering.
    """
    jobs = _jobs()
    session_id = f"s3d-d18-in-{uuid4().hex[:8]}"
    conv = await conversation_service.create(
        session_id, "steins_gate", title="d18-in", identity_mode="self"
    )
    conversation_id = str(conv["id"])
    mid_vague = await models.save_message(
        session_id, "user", "我最近在看一部动漫",
        worldline="steins_gate", conversation_id=conversation_id,
    )
    mid_done = await models.save_message(
        session_id, "user", "我还在追一部番剧",
        worldline="steins_gate", conversation_id=conversation_id,
    )
    mid_okabe = await models.save_message(
        session_id, "user", "我在读科幻小说",
        worldline="steins_gate", conversation_id=conversation_id,
    )
    mid_resolves = await models.save_message(
        session_id, "user", "那部动漫是《为美好的世界献上祝福》",
        worldline="steins_gate", conversation_id=conversation_id,
    )
    assert int(mid_vague) < int(mid_done) < int(mid_okabe) < int(mid_resolves)

    # Unresolved candidate (reanalysis_count=0) — must be offered.
    obs_offer = await _seed_unresolved_candidate(
        session_id=session_id,
        identity_mode="self",
        conversation_id=conversation_id,
        source_message_id=int(mid_vague),
        display_text="在看一部动漫",
    )
    # Already-reanalysed candidate (cap hit) — excluded BY COUNT.
    obs_done = await _seed_unresolved_candidate(
        session_id=session_id,
        identity_mode="self",
        conversation_id=conversation_id,
        source_message_id=int(mid_done),
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
    # Cross-identity candidate — excluded BY IDENTITY.
    obs_okabe = await _seed_unresolved_candidate(
        session_id=session_id,
        identity_mode="okabe",
        conversation_id=conversation_id,
        source_message_id=int(mid_okabe),
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
    assert "unresolved_observations" in captured["payload"], (
        "canonical unresolved_observations key required"
    )
    unresolved = list(captured["payload"]["unresolved_observations"])
    assert unresolved, "bounded unresolved context must be offered (plan §6.4)"
    offered_ids = {str(u.get("observation_id")) for u in unresolved}
    assert obs_offer in offered_ids, (
        "unresolved candidate (reanalysis_count=0) must be offered for reanalysis"
    )
    assert obs_done not in offered_ids, (
        "observation with reanalysis_count=1 must never be offered again (R08)"
    )
    assert obs_okabe not in offered_ids, (
        "D18 context must stay same-scope; okabe observation leaked in (R05)"
    )
    for item in unresolved:
        assert set(item.keys()) == {"observation_id", "display_text", "source_excerpt"}, (
            "payload items must be exactly the minimal D18 shape"
        )


@pytest.mark.asyncio
async def test_s3d_probe_d18_resolution_updates_existing_observation(
    isolated_store, monkeypatch
):
    """R08/§6.4 (output side): explicit target updates the EXISTING observation.

    Same observation_id; old + current source rows; reanalysis_count 0→1; no
    parallel observation for the old source.
    """
    jobs = _jobs()
    session_id = f"s3d-d18-out-{uuid4().hex[:8]}"
    conv = await conversation_service.create(
        session_id, "steins_gate", title="d18-out", identity_mode="self"
    )
    conversation_id = str(conv["id"])
    mid_vague = await models.save_message(
        session_id, "user", "我最近在看一部动漫",
        worldline="steins_gate", conversation_id=conversation_id,
    )
    mid_resolves = await models.save_message(
        session_id, "user", "那部动漫是《为美好的世界献上祝福》",
        worldline="steins_gate", conversation_id=conversation_id,
    )
    assert int(mid_vague) < int(mid_resolves)
    obs_id = await _seed_unresolved_candidate(
        session_id=session_id,
        identity_mode="self",
        conversation_id=conversation_id,
        source_message_id=int(mid_vague),
        display_text="在看一部动漫",
    )

    async def resolve_completion(**kwargs: Any) -> dict[str, Any]:
        unresolved = list(kwargs.get("unresolved_observations") or [])
        assert any(u["observation_id"] == obs_id for u in unresolved)
        sid = int(kwargs["source_message_id"])
        return {
            "observations": [
                {
                    "observation_ref": "o1",
                    "reanalysis_target_observation_id": obs_id,
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
                """SELECT status, reanalysis_count, display_text
                     FROM memory_observations WHERE observation_id=?""",
                (obs_id,),
            )
        ).fetchone()
        assert row is not None
        assert int(row["reanalysis_count"]) == 1, (
            "resolving turn must set reanalysis_count=1 on the EXISTING row (R08)"
        )
        assert str(row["status"]) == "attached"
        assert "为美好的世界献上祝福" in str(row["display_text"])
        sources = await (
            await db.execute(
                """SELECT source_message_id FROM memory_observation_sources
                    WHERE observation_id=? ORDER BY source_message_id""",
                (obs_id,),
            )
        ).fetchall()
        assert [int(s["source_message_id"]) for s in sources] == [
            int(mid_vague),
            int(mid_resolves),
        ], "old + current source rows must both be attached"
        parallel = await (
            await db.execute(
                """SELECT COUNT(*) AS n FROM memory_observations
                    WHERE session_id=? AND display_text=? AND observation_id != ?""",
                (session_id, "喜欢《为美好的世界献上祝福》", obs_id),
            )
        ).fetchone()
        assert int(parallel["n"]) == 0, "no parallel observation for the old source"
        evidence = await (
            await db.execute(
                """SELECT COUNT(*) AS n FROM stable_fact_evidence
                    WHERE observation_id=?""",
                (obs_id,),
            )
        ).fetchone()
        assert int(evidence["n"]) == 1, "resolved observation is the fact evidence"
    finally:
        await db.close()


# ---------------------------------------------------------------------------
# MEMORY-V11-S3D-4: D18 bounded contextual reanalysis — selector/job surface.
# ---------------------------------------------------------------------------


async def _enqueue_self_job(
    *, session_id: str, conversation_id: str, source_message_id: int
) -> str:
    jobs = _jobs()
    job = await jobs.enqueue_memory_job(
        session_id=session_id,
        worldline="steins_gate",
        conversation_id=conversation_id,
        identity_mode="self",
        source_message_id=int(source_message_id),
        pipeline_version="memory-v11-1",
        provider_id="deepseek",
        model_id="deepseek-chat",
    )
    return job["job_id"]


async def _run_capture_job(
    *,
    session_id: str,
    conversation_id: str,
    source_message_id: int,
    completion: Any,
) -> dict[str, Any]:
    jobs = _jobs()
    job_id = await _enqueue_self_job(
        session_id=session_id,
        conversation_id=conversation_id,
        source_message_id=source_message_id,
    )
    result = await jobs.process_memory_job(
        job_id=job_id, lease_owner="w-d18", memory_completion=completion
    )
    return result


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["ignored", "attached", "rejected", "deleted", "expired"])
async def test_s3d4_d3_status_excluded_by_status(isolated_store, status):
    """D3: non-candidate statuses excluded — proven by status, real prior source."""
    jobs = _jobs()
    session_id = f"s3d4-d3-{status}-{uuid4().hex[:8]}"
    conv = await conversation_service.create(
        session_id, "steins_gate", title="d3", identity_mode="self"
    )
    conversation_id = str(conv["id"])
    mid_prior = await models.save_message(
        session_id, "user", "一个旧话题",
        worldline="steins_gate", conversation_id=conversation_id,
    )
    mid_current = await models.save_message(
        session_id, "user", "关于那个话题的问题",
        worldline="steins_gate", conversation_id=conversation_id,
    )
    assert int(mid_prior) < int(mid_current)
    obs_id = await _seed_unresolved_candidate(
        session_id=session_id, identity_mode="self",
        conversation_id=conversation_id,
        source_message_id=int(mid_prior), display_text="旧话题候选",
    )
    db = await get_db("steins_gate", "memory")
    try:
        await db.execute(
            "UPDATE memory_observations SET status=? WHERE observation_id=?",
            (status, obs_id),
        )
        await db.commit()
    finally:
        await db.close()
    captured: dict[str, Any] = {}

    async def completion(**kwargs):
        captured["unresolved"] = list(kwargs.get("unresolved_observations") or [])
        return {"observations": [], "operations": []}

    result = await _run_capture_job(
        session_id=session_id, conversation_id=conversation_id,
        source_message_id=int(mid_current), completion=completion,
    )
    assert result["state"] == "completed"
    ids = {u["observation_id"] for u in captured["unresolved"]}
    assert obs_id not in ids, f"{status} observation must be excluded by status"


@pytest.mark.asyncio
async def test_s3d4_d5_other_conversation_excluded(isolated_store):
    jobs = _jobs()
    session_id = f"s3d4-d5-{uuid4().hex[:8]}"
    conv_a = await conversation_service.create(
        session_id, "steins_gate", title="d5a", identity_mode="self"
    )
    conv_b = await conversation_service.create(
        session_id, "steins_gate", title="d5b", identity_mode="self"
    )
    conv_a_id, conv_b_id = str(conv_a["id"]), str(conv_b["id"])
    mid_a = await models.save_message(
        session_id, "user", "会话A的旧话题",
        worldline="steins_gate", conversation_id=conv_a_id,
    )
    mid_b = await models.save_message(
        session_id, "user", "会话B的当前问题",
        worldline="steins_gate", conversation_id=conv_b_id,
    )
    assert int(mid_a) < int(mid_b)
    obs_id = await _seed_unresolved_candidate(
        session_id=session_id, identity_mode="self",
        conversation_id=conv_a_id, source_message_id=int(mid_a),
        display_text="会话A候选",
    )
    captured: dict[str, Any] = {}

    async def completion(**kwargs):
        captured["unresolved"] = list(kwargs.get("unresolved_observations") or [])
        return {"observations": [], "operations": []}

    result = await _run_capture_job(
        session_id=session_id, conversation_id=conv_b_id,
        source_message_id=int(mid_b), completion=completion,
    )
    assert result["state"] == "completed"
    ids = {u["observation_id"] for u in captured["unresolved"]}
    assert obs_id not in ids, "other-conversation candidate must be excluded"


@pytest.mark.asyncio
async def test_s3d4_d6_other_worldline_excluded(isolated_store):
    """Candidate living in the beta physical DB must never reach an SG job."""
    jobs = _jobs()
    session_id = f"s3d4-d6-{uuid4().hex[:8]}"
    obs_id = str(uuid4())
    now = "2026-08-01T00:00:00+00:00"
    db = await get_db("beta", "memory")
    try:
        await db.execute(
            """INSERT INTO memory_observations(
                   observation_id, session_id, identity_mode, display_text,
                   semantic_json, semantic_fingerprint, evidence_kind, memory_class,
                   confidence, topic_label_proposal, status, extractor_version,
                   reanalysis_count, expires_at, created_at, updated_at
               ) VALUES(?,?, 'self', 'beta候选', '{}', 'fp-beta', 'direct_user',
                        'stable_candidate', 0.4, NULL, 'candidate',
                        'memory-v11-1', 0, NULL, ?, ?)""",
            (obs_id, session_id, now, now),
        )
        await db.execute(
            """INSERT INTO memory_observation_sources(
                   observation_id, source_message_id, conversation_id, source_role,
                   source_fingerprint, source_created_at, source_state, excerpt
               ) VALUES(?,1,'conv-beta','user','fp-src',?,'present','beta候选')""",
            (obs_id, now),
        )
        await db.commit()
    finally:
        await db.close()

    conv = await conversation_service.create(
        session_id, "steins_gate", title="d6", identity_mode="self"
    )
    conversation_id = str(conv["id"])
    mid = await models.save_message(
        session_id, "user", "当前问题",
        worldline="steins_gate", conversation_id=conversation_id,
    )
    captured: dict[str, Any] = {}

    async def completion(**kwargs):
        captured["unresolved"] = list(kwargs.get("unresolved_observations") or [])
        return {"observations": [], "operations": []}

    result = await _run_capture_job(
        session_id=session_id, conversation_id=conversation_id,
        source_message_id=int(mid), completion=completion,
    )
    assert result["state"] == "completed"
    ids = {u["observation_id"] for u in captured["unresolved"]}
    assert obs_id not in ids, "beta candidate must never reach an SG job"


@pytest.mark.asyncio
async def test_s3d4_d7_multisource_one_slot(isolated_store):
    """One observation with two sources consumes exactly ONE pool slot."""
    jobs = _jobs()
    session_id = f"s3d4-d7-{uuid4().hex[:8]}"
    conv = await conversation_service.create(
        session_id, "steins_gate", title="d7", identity_mode="self"
    )
    conversation_id = str(conv["id"])
    mid1 = await models.save_message(
        session_id, "user", "旧发言一", worldline="steins_gate", conversation_id=conversation_id,
    )
    mid2 = await models.save_message(
        session_id, "user", "旧发言二", worldline="steins_gate", conversation_id=conversation_id,
    )
    mid_current = await models.save_message(
        session_id, "user", "当前问题", worldline="steins_gate", conversation_id=conversation_id,
    )
    obs_id = await _seed_unresolved_candidate(
        session_id=session_id, identity_mode="self",
        conversation_id=conversation_id, source_message_id=int(mid1),
        display_text="多源候选",
    )
    db = await get_db("steins_gate", "memory")
    try:
        await db.execute(
            """INSERT INTO memory_observation_sources(
                   observation_id, source_message_id, conversation_id, source_role,
                   source_fingerprint, source_created_at, source_state, excerpt
               ) VALUES(?,?,?, 'user', 'fp-extra', '2026-08-01T00:00:00+00:00',
                        'present', '旧发言二')""",
            (obs_id, int(mid2), conversation_id),
        )
        await db.commit()
    finally:
        await db.close()
    captured: dict[str, Any] = {}

    async def completion(**kwargs):
        captured["unresolved"] = list(kwargs.get("unresolved_observations") or [])
        return {"observations": [], "operations": []}

    result = await _run_capture_job(
        session_id=session_id, conversation_id=conversation_id,
        source_message_id=int(mid_current), completion=completion,
    )
    assert result["state"] == "completed"
    unresolved = captured["unresolved"]
    assert len(unresolved) == 1, "one observation must consume exactly one pool slot"
    assert unresolved[0]["observation_id"] == obs_id


@pytest.mark.asyncio
async def test_s3d4_d8_pool_bounded_16(isolated_store):
    """Recent pool capped at D18_UNRESOLVED_POOL_LIMIT (direct selector call)."""
    from app.services.memory_v11.contracts import D18_UNRESOLVED_POOL_LIMIT
    from app.services.memory_v11.retrieval import select_unresolved_observations

    session_id = f"s3d4-d8-{uuid4().hex[:8]}"
    db = await get_db("steins_gate", "memory")
    try:
        now = "2026-08-01T00:00:00+00:00"
        for i in range(18):
            await db.execute(
                """INSERT INTO memory_observations(
                       observation_id, session_id, identity_mode, display_text,
                       semantic_json, semantic_fingerprint, evidence_kind, memory_class,
                       confidence, topic_label_proposal, status, extractor_version,
                       reanalysis_count, expires_at, created_at, updated_at
                   ) VALUES(?,?, 'self', ?, '{}', 'fp', 'direct_user',
                            'stable_candidate', 0.4, NULL, 'candidate',
                            'memory-v11-1', 0, NULL, ?, ?)""",
                (str(uuid4()), session_id, f"候选{i:02d}", now, now),
            )
        rows = await (
            await db.execute(
                """SELECT observation_id, ROW_NUMBER() OVER (ORDER BY observation_id) AS rn
                     FROM memory_observations WHERE session_id=?""",
                (session_id,),
            )
        ).fetchall()
        for row in rows:
            await db.execute(
                """INSERT INTO memory_observation_sources(
                       observation_id, source_message_id, conversation_id, source_role,
                       source_fingerprint, source_created_at, source_state, excerpt
                   ) VALUES(?,?,'conv-d8','user','fp-src',?,'present','候选')""",
                (row["observation_id"], int(row["rn"]), now),
            )
        await db.commit()
    finally:
        await db.close()

    unresolved = await select_unresolved_observations(
        session_id=session_id, worldline="steins_gate", identity_mode="self",
        conversation_id="conv-d8", current_source_message_id=100, query="候选",
        context_cap=100, token_budget=100000,
    )
    assert len(unresolved) == int(D18_UNRESOLVED_POOL_LIMIT), (
        f"recent pool must be bounded at {D18_UNRESOLVED_POOL_LIMIT}, got {len(unresolved)}"
    )


@pytest.mark.asyncio
async def test_s3d4_d9_context_cap_3(isolated_store):
    """Completion context capped at D18_UNRESOLVED_CONTEXT_CAP."""
    from app.services.memory_v11.contracts import D18_UNRESOLVED_CONTEXT_CAP
    from app.services.memory_v11.retrieval import select_unresolved_observations

    session_id = f"s3d4-d9-{uuid4().hex[:8]}"
    db = await get_db("steins_gate", "memory")
    try:
        now = "2026-08-01T00:00:00+00:00"
        for i in range(5):
            await db.execute(
                """INSERT INTO memory_observations(
                       observation_id, session_id, identity_mode, display_text,
                       semantic_json, semantic_fingerprint, evidence_kind, memory_class,
                       confidence, topic_label_proposal, status, extractor_version,
                       reanalysis_count, expires_at, created_at, updated_at
                   ) VALUES(?,?, 'self', ?, '{}', 'fp', 'direct_user',
                            'stable_candidate', 0.4, NULL, 'candidate',
                            'memory-v11-1', 0, NULL, ?, ?)""",
                (str(uuid4()), session_id, f"候选{i}", now, now),
            )
        rows = await (
            await db.execute(
                "SELECT observation_id FROM memory_observations WHERE session_id=?",
                (session_id,),
            )
        ).fetchall()
        for index, row in enumerate(rows):
            await db.execute(
                """INSERT INTO memory_observation_sources(
                       observation_id, source_message_id, conversation_id, source_role,
                       source_fingerprint, source_created_at, source_state, excerpt
                   ) VALUES(?,?,'conv-d9','user','fp-src',?,'present','候选')""",
                (row["observation_id"], index + 1, now),
            )
        await db.commit()
    finally:
        await db.close()

    unresolved = await select_unresolved_observations(
        session_id=session_id, worldline="steins_gate", identity_mode="self",
        conversation_id="conv-d9", current_source_message_id=100, query="候选",
        pool_limit=100, token_budget=100000,
    )
    assert len(unresolved) == int(D18_UNRESOLVED_CONTEXT_CAP)


@pytest.mark.asyncio
async def test_s3d4_d10_token_budget_480(isolated_store):
    """Payload token estimate stays within D18_UNRESOLVED_TOKEN_BUDGET."""
    from app.services.memory_v11.contracts import D18_UNRESOLVED_TOKEN_BUDGET
    from app.services.memory_v11.retrieval import (
        _estimate_tokens,
        select_unresolved_observations,
    )

    session_id = f"s3d4-d10-{uuid4().hex[:8]}"
    db = await get_db("steins_gate", "memory")
    try:
        now = "2026-08-01T00:00:00+00:00"
        for i in range(4):
            await db.execute(
                """INSERT INTO memory_observations(
                       observation_id, session_id, identity_mode, display_text,
                       semantic_json, semantic_fingerprint, evidence_kind, memory_class,
                       confidence, topic_label_proposal, status, extractor_version,
                       reanalysis_count, expires_at, created_at, updated_at
                   ) VALUES(?,?, 'self', ?, '{}', 'fp', 'direct_user',
                            'stable_candidate', 0.4, NULL, 'candidate',
                            'memory-v11-1', 0, NULL, ?, ?)""",
                (str(uuid4()), session_id, "候选" + "词" * 40, now, now),
            )
        rows = await (
            await db.execute(
                "SELECT observation_id FROM memory_observations WHERE session_id=?",
                (session_id,),
            )
        ).fetchall()
        for index, row in enumerate(rows):
            await db.execute(
                """INSERT INTO memory_observation_sources(
                       observation_id, source_message_id, conversation_id, source_role,
                       source_fingerprint, source_created_at, source_state, excerpt
                   ) VALUES(?,?,'conv-d10','user','fp-src',?,'present','候选')""",
                (row["observation_id"], index + 1, now),
            )
        await db.commit()
    finally:
        await db.close()

    unresolved = await select_unresolved_observations(
        session_id=session_id, worldline="steins_gate", identity_mode="self",
        conversation_id="conv-d10", current_source_message_id=100, query="候选",
        pool_limit=100,
    )
    import json as _json

    cost = _estimate_tokens(
        _json.dumps(unresolved, ensure_ascii=False, sort_keys=True)
    )
    assert cost <= int(D18_UNRESOLVED_TOKEN_BUDGET), f"payload tokens {cost} > 480"


@pytest.mark.asyncio
async def test_s3d4_d11_oversized_whole_item_omitted(isolated_store):
    """An oversized whole observation is omitted; never char-sliced."""
    from app.services.memory_v11.retrieval import select_unresolved_observations

    session_id = f"s3d4-d11-{uuid4().hex[:8]}"
    db = await get_db("steins_gate", "memory")
    try:
        now = "2026-08-01T00:00:00+00:00"
        big_id = str(uuid4())
        small_id = str(uuid4())
        await db.execute(
            """INSERT INTO memory_observations(
                   observation_id, session_id, identity_mode, display_text,
                   semantic_json, semantic_fingerprint, evidence_kind, memory_class,
                   confidence, topic_label_proposal, status, extractor_version,
                   reanalysis_count, expires_at, created_at, updated_at
               ) VALUES(?,?, 'self', ?, '{}', 'fp', 'direct_user',
                        'stable_candidate', 0.4, NULL, 'candidate',
                        'memory-v11-1', 0, NULL, ?, ?)""",
            (big_id, session_id, "长" * 2000, now, now),
        )
        await db.execute(
            """INSERT INTO memory_observations(
                   observation_id, session_id, identity_mode, display_text,
                   semantic_json, semantic_fingerprint, evidence_kind, memory_class,
                   confidence, topic_label_proposal, status, extractor_version,
                   reanalysis_count, expires_at, created_at, updated_at
               ) VALUES(?,?, 'self', ?, '{}', 'fp', 'direct_user',
                        'stable_candidate', 0.4, NULL, 'candidate',
                        'memory-v11-1', 0, NULL, ?, ?)""",
            (small_id, session_id, "短候选", now, now),
        )
        for oid, mid in ((big_id, 1), (small_id, 2)):
            await db.execute(
                """INSERT INTO memory_observation_sources(
                       observation_id, source_message_id, conversation_id, source_role,
                       source_fingerprint, source_created_at, source_state, excerpt
                   ) VALUES(?,?,'conv-d11','user','fp-src',?,'present','候选')""",
                (oid, mid, now),
            )
        await db.commit()
    finally:
        await db.close()

    unresolved = await select_unresolved_observations(
        session_id=session_id, worldline="steins_gate", identity_mode="self",
        conversation_id="conv-d11", current_source_message_id=100, query="候选",
        pool_limit=100,
    )
    ids = {u["observation_id"] for u in unresolved}
    assert small_id in ids
    assert big_id not in ids, "oversized whole item must be omitted, never sliced"
    assert all(u["display_text"] in ("短候选",) for u in unresolved)


@pytest.mark.asyncio
async def test_s3d4_d12_d13_zero_lexical_eligible_relevance_ranks(isolated_store):
    """Zero lexical overlap stays eligible; positive relevance only ranks."""
    from app.services.memory_v11.retrieval import select_unresolved_observations

    session_id = f"s3d4-d12-{uuid4().hex[:8]}"
    db = await get_db("steins_gate", "memory")
    try:
        now = "2026-08-01T00:00:00+00:00"
        zero_id = str(uuid4())
        hit_id = str(uuid4())
        await db.execute(
            """INSERT INTO memory_observations(
                   observation_id, session_id, identity_mode, display_text,
                   semantic_json, semantic_fingerprint, evidence_kind, memory_class,
                   confidence, topic_label_proposal, status, extractor_version,
                   reanalysis_count, expires_at, created_at, updated_at
               ) VALUES(?,?, 'self', ?, '{}', 'fp', 'direct_user',
                        'stable_candidate', 0.4, NULL, 'candidate',
                        'memory-v11-1', 0, NULL, ?, ?)""",
            (zero_id, session_id, "在玩一个游戏", now, now),
        )
        await db.execute(
            """INSERT INTO memory_observations(
                   observation_id, session_id, identity_mode, display_text,
                   semantic_json, semantic_fingerprint, evidence_kind, memory_class,
                   confidence, topic_label_proposal, status, extractor_version,
                   reanalysis_count, expires_at, created_at, updated_at
               ) VALUES(?,?, 'self', ?, '{}', 'fp', 'direct_user',
                        'stable_candidate', 0.4, NULL, 'candidate',
                        'memory-v11-1', 0, NULL, ?, ?)""",
            (hit_id, session_id, "动漫相关话题", now, now),
        )
        # hit candidate is OLDER (source 1); zero-overlap candidate is NEWER (2).
        await db.execute(
            """INSERT INTO memory_observation_sources(
                   observation_id, source_message_id, conversation_id, source_role,
                   source_fingerprint, source_created_at, source_state, excerpt
               ) VALUES(?,1,'conv-d12','user','fp-a',?,'present','旧')""",
            (hit_id, now),
        )
        await db.execute(
            """INSERT INTO memory_observation_sources(
                   observation_id, source_message_id, conversation_id, source_role,
                   source_fingerprint, source_created_at, source_state, excerpt
               ) VALUES(?,2,'conv-d12','user','fp-b',?,'present','新')""",
            (zero_id, now),
        )
        await db.commit()
    finally:
        await db.close()

    unresolved = await select_unresolved_observations(
        session_id=session_id, worldline="steins_gate", identity_mode="self",
        conversation_id="conv-d12", current_source_message_id=100, query="动漫",
        pool_limit=100,
    )
    ids = [u["observation_id"] for u in unresolved]
    assert zero_id in ids, "zero lexical relevance must remain eligible (D12)"
    assert ids.index(hit_id) < ids.index(zero_id), (
        "positive lexical relevance must rank first (D13)"
    )


@pytest.mark.asyncio
async def test_s3d4_d15_selector_failure_skips_provider(isolated_store, monkeypatch):
    """Selector exception → validation_failed; completion/provider never runs."""
    jobs = _jobs()
    session_id = f"s3d4-d15-{uuid4().hex[:8]}"
    conv = await conversation_service.create(
        session_id, "steins_gate", title="d15", identity_mode="self"
    )
    conversation_id = str(conv["id"])
    mid = await models.save_message(
        session_id, "user", "当前问题",
        worldline="steins_gate", conversation_id=conversation_id,
    )

    async def boom(**kwargs):
        raise RuntimeError("forced unresolved selector failure")

    monkeypatch.setattr(
        "app.services.memory_v11.retrieval.select_unresolved_observations", boom
    )
    call_count = {"n": 0}

    async def must_not_run(**kwargs):
        call_count["n"] += 1
        return {"observations": [], "operations": []}

    result = await _run_capture_job(
        session_id=session_id, conversation_id=conversation_id,
        source_message_id=int(mid), completion=must_not_run,
    )
    assert call_count["n"] == 0, "completion must not run after selector failure"
    assert result["state"] in {"pending", "failed"}
    assert result.get("last_error_code") == "validation_failed"


@pytest.mark.asyncio
async def test_s3d4_d19_job_no_target_ordinary_unchanged(isolated_store):
    """Offered but untargeted candidate stays untouched after a normal job."""
    jobs = _jobs()
    session_id = f"s3d4-d19-{uuid4().hex[:8]}"
    conv = await conversation_service.create(
        session_id, "steins_gate", title="d19", identity_mode="self"
    )
    conversation_id = str(conv["id"])
    mid_prior = await models.save_message(
        session_id, "user", "一个旧话题",
        worldline="steins_gate", conversation_id=conversation_id,
    )
    mid_current = await models.save_message(
        session_id, "user", "完全无关的新话题",
        worldline="steins_gate", conversation_id=conversation_id,
    )
    obs_id = await _seed_unresolved_candidate(
        session_id=session_id, identity_mode="self",
        conversation_id=conversation_id, source_message_id=int(mid_prior),
        display_text="旧话题候选",
    )

    async def completion(**kwargs):
        return {"observations": [], "operations": []}

    result = await _run_capture_job(
        session_id=session_id, conversation_id=conversation_id,
        source_message_id=int(mid_current), completion=completion,
    )
    assert result["state"] == "completed"
    db = await get_db("steins_gate", "memory")
    try:
        row = await (await db.execute(
            "SELECT status, reanalysis_count FROM memory_observations WHERE observation_id=?",
            (obs_id,),
        )).fetchone()
    finally:
        await db.close()
    assert row["status"] == "candidate" and int(row["reanalysis_count"]) == 0


@pytest.mark.asyncio
async def test_s3d4_d26_forged_old_source_rejected_job(isolated_store):
    """Completion forging the OLD source id → validation_failed, zero mutation."""
    jobs = _jobs()
    session_id = f"s3d4-d26-{uuid4().hex[:8]}"
    conv = await conversation_service.create(
        session_id, "steins_gate", title="d26", identity_mode="self"
    )
    conversation_id = str(conv["id"])
    mid_prior = await models.save_message(
        session_id, "user", "旧话题", worldline="steins_gate", conversation_id=conversation_id,
    )
    mid_current = await models.save_message(
        session_id, "user", "当前问题", worldline="steins_gate", conversation_id=conversation_id,
    )
    obs_id = await _seed_unresolved_candidate(
        session_id=session_id, identity_mode="self",
        conversation_id=conversation_id, source_message_id=int(mid_prior),
        display_text="旧话题候选",
    )

    async def forged_completion(**kwargs):
        sid = int(kwargs["source_message_id"])
        return {
            "observations": [
                {
                    "observation_ref": "o1",
                    "reanalysis_target_observation_id": obs_id,
                    # FORGED: the OLD source id instead of the current one.
                    "source_message_ids": [int(mid_prior)],
                    "display_text": "解析后",
                    "semantic": {"subject": "user", "predicate": "x", "object": "y"},
                    "evidence_kind": "direct_user",
                    "memory_class": "stable_candidate",
                    "confidence": 0.96,
                    "topic_label": None,
                    "expires_at": None,
                }
            ],
            "operations": [
                {"op": "CREATE", "observation_ref": "o1", "fact_text": "解析后",
                 "reason_code": "d18"}
            ],
        }

    result = await _run_capture_job(
        session_id=session_id, conversation_id=conversation_id,
        source_message_id=int(mid_current), completion=forged_completion,
    )
    assert result["state"] in {"pending", "failed"}
    assert result.get("last_error_code") == "validation_failed"
    db = await get_db("steins_gate", "memory")
    try:
        row = await (await db.execute(
            "SELECT status, reanalysis_count FROM memory_observations WHERE observation_id=?",
            (obs_id,),
        )).fetchone()
    finally:
        await db.close()
    assert row["status"] == "candidate" and int(row["reanalysis_count"]) == 0


@pytest.mark.asyncio
async def test_s3d4_d37_crash_window_retry_no_duplicates(isolated_store, monkeypatch):
    """TRUE crash window: Memory commits, job crashes BEFORE state='completed'.

    The durable job is left reclaimable (expired lease); the retry MUST
    converge via the current-source receipt precheck WITHOUT running the
    completion again, and nothing may duplicate.
    """
    jobs = _jobs()
    session_id = f"s3d4-d37-{uuid4().hex[:8]}"
    conv = await conversation_service.create(
        session_id, "steins_gate", title="d37", identity_mode="self"
    )
    conversation_id = str(conv["id"])
    mid_prior = await models.save_message(
        session_id, "user", "我最近在看一部动漫",
        worldline="steins_gate", conversation_id=conversation_id,
    )
    mid_resolves = await models.save_message(
        session_id, "user", "那部动漫是《为美好的世界献上祝福》",
        worldline="steins_gate", conversation_id=conversation_id,
    )
    assert int(mid_prior) < int(mid_resolves)
    obs_id = await _seed_unresolved_candidate(
        session_id=session_id, identity_mode="self",
        conversation_id=conversation_id, source_message_id=int(mid_prior),
        display_text="在看一部动漫",
    )

    async def resolve_completion(**kwargs):
        sid = int(kwargs["source_message_id"])
        return {
            "observations": [
                {
                    "observation_ref": "o1",
                    "reanalysis_target_observation_id": obs_id,
                    "source_message_ids": [sid],
                    "display_text": "喜欢《为美好的世界献上祝福》",
                    "semantic": {"subject": "user", "predicate": "likes_work", "object": "konosuba"},
                    "evidence_kind": "direct_user",
                    "memory_class": "stable_candidate",
                    "confidence": 0.96,
                    "topic_label": "动漫",
                    "expires_at": None,
                }
            ],
            "operations": [
                {"op": "CREATE", "observation_ref": "o1", "fact_text": "喜欢《为美好的世界献上祝福》",
                 "reason_code": "direct_durable_statement"}
            ],
        }

    # Simulate crash AFTER the Memory transaction commits but BEFORE the job
    # transition to completed: the first completed-transition raises.
    original_transition = jobs._transition_owned
    crashed = {"done": False}

    async def crash_after_commit(**kwargs):
        if kwargs.get("state") == "completed" and not crashed["done"]:
            crashed["done"] = True
            raise RuntimeError("simulated crash after Memory commit")
        return await original_transition(**kwargs)

    monkeypatch.setattr(jobs, "_transition_owned", crash_after_commit)

    job_id = await _enqueue_self_job(
        session_id=session_id, conversation_id=conversation_id,
        source_message_id=int(mid_resolves),
    )
    with pytest.raises(RuntimeError):
        await jobs.process_memory_job(
            job_id=job_id, lease_owner="w-crash", memory_completion=resolve_completion
        )

    # Memory effects ARE committed (receipt for Y exists); job row is processing.
    db = await get_db("steins_gate", "memory")
    try:
        src_after_crash = await (await db.execute(
            "SELECT COUNT(*) AS n FROM memory_observation_sources WHERE observation_id=?",
            (obs_id,),
        )).fetchone()
        count_after_crash = await (await db.execute(
            "SELECT reanalysis_count FROM memory_observations WHERE observation_id=?",
            (obs_id,),
        )).fetchone()
        facts_after_crash = await (await db.execute(
            "SELECT COUNT(*) AS n FROM stable_facts WHERE session_id=?", (session_id,)
        )).fetchone()
        await db.execute(
            "UPDATE memory_ingest_jobs SET lease_expires_at=? WHERE job_id=?",
            ("2000-01-01T00:00:00+00:00", job_id),
        )
        await db.commit()
    finally:
        await db.close()
    assert int(src_after_crash["n"]) == 2
    assert int(count_after_crash["reanalysis_count"]) == 1
    assert int(facts_after_crash["n"]) == 1
    # S3F-1 E3: a provider_nonempty dedicated receipt now exists for the cursor.
    receipt_rows = await _receipt_rows(session_id)
    assert len(receipt_rows) == 1
    assert receipt_rows[0]["receipt_kind"] == "provider_nonempty"
    assert receipt_rows[0]["session_id"] == session_id
    assert receipt_rows[0]["conversation_id"] == conversation_id
    assert receipt_rows[0]["identity_mode"] == "self"
    assert int(receipt_rows[0]["source_message_id"]) == int(mid_resolves)
    assert receipt_rows[0]["pipeline_version"] == "memory-v11-1"

    call_count = {"n": 0}

    async def must_not_run(**kwargs):
        call_count["n"] += 1
        return {"observations": [], "operations": []}

    retried = await jobs.process_memory_job(
        job_id=job_id, lease_owner="w-retry", memory_completion=must_not_run
    )
    assert retried["state"] == "completed"
    assert call_count["n"] == 0, (
        "crash retry must converge via the source receipt WITHOUT re-running completion"
    )
    assert len(await _receipt_rows(session_id)) == 1, "no duplicate receipt on retry"

    db = await get_db("steins_gate", "memory")
    try:
        obs_n = await (await db.execute(
            "SELECT COUNT(*) AS n FROM memory_observations WHERE session_id=?", (session_id,)
        )).fetchone()
        src_n = await (await db.execute(
            "SELECT COUNT(*) AS n FROM memory_observation_sources WHERE observation_id=?",
            (obs_id,),
        )).fetchone()
        count_n = await (await db.execute(
            "SELECT reanalysis_count FROM memory_observations WHERE observation_id=?",
            (obs_id,),
        )).fetchone()
        facts_n = await (await db.execute(
            "SELECT COUNT(*) AS n FROM stable_facts WHERE session_id=?", (session_id,)
        )).fetchone()
        versions_n = await (await db.execute(
            """SELECT COUNT(*) AS n FROM stable_fact_versions v
                 JOIN stable_facts f ON f.fact_id = v.fact_id
                WHERE f.session_id=?""",
            (session_id,),
        )).fetchone()
        exps_n = await (await db.execute(
            "SELECT COUNT(*) AS n FROM experiences WHERE session_id=?", (session_id,)
        )).fetchone()
    finally:
        await db.close()
    assert int(obs_n["n"]) == 1, "no duplicate observation"
    assert int(src_n["n"]) == 2, "exactly X + Y sources, no duplicates"
    assert int(count_n["reanalysis_count"]) == 1
    assert int(facts_n["n"]) == 1, "no duplicate fact"
    assert int(versions_n["n"]) == 1, "no duplicate version"
    assert int(exps_n["n"]) == 0


@pytest.mark.asyncio
async def test_s3d4_d38_source_created_at_persisted(isolated_store):
    """source_created_at equals the persisted history message timestamp."""
    jobs = _jobs()
    session_id = f"s3d4-d38-{uuid4().hex[:8]}"
    conv = await conversation_service.create(
        session_id, "steins_gate", title="d38", identity_mode="self"
    )
    conversation_id = str(conv["id"])
    mid = await models.save_message(
        session_id, "user", "带着时间戳的消息",
        worldline="steins_gate", conversation_id=conversation_id,
    )
    persisted = "2020-05-06T07:08:09+00:00"
    db = await get_db("steins_gate", "history")
    try:
        await db.execute(
            "UPDATE messages SET created_at=? WHERE id=?", (persisted, int(mid))
        )
        await db.commit()
    finally:
        await db.close()

    async def create_completion(**kwargs):
        sid = int(kwargs["source_message_id"])
        return {
            "observations": [
                {
                    "observation_ref": "o1",
                    "source_message_ids": [sid],
                    "display_text": "带着时间戳的消息",
                    "semantic": {"subject": "user", "predicate": "x", "object": "y"},
                    "evidence_kind": "direct_user",
                    "memory_class": "stable_candidate",
                    "confidence": 0.96,
                    "topic_label": None,
                    "expires_at": None,
                }
            ],
            "operations": [
                {"op": "CREATE", "observation_ref": "o1", "fact_text": "带着时间戳的消息",
                 "reason_code": "direct_durable_statement"}
            ],
        }

    result = await _run_capture_job(
        session_id=session_id, conversation_id=conversation_id,
        source_message_id=int(mid), completion=create_completion,
    )
    assert result["state"] == "completed"
    db = await get_db("steins_gate", "memory")
    try:
        row = await (await db.execute(
            "SELECT source_created_at FROM memory_observation_sources WHERE source_message_id=?",
            (int(mid),),
        )).fetchone()
    finally:
        await db.close()
    assert str(row["source_created_at"]) == persisted, (
        "stored provenance time must be the persisted message time, not processing time"
    )

# ---------------------------------------------------------------------------
# MEMORY-V11-S3E-2: D30 privacy boundary — deterministic local classifier.
# ---------------------------------------------------------------------------

_S3E2_CLASSES = frozenset(
    {
        "password",
        "api_secret",
        "otp",
        "payment_card",
        "government_id",
        "precise_home_address",
    }
)


def _privacy():
    return importlib.import_module("app.services.memory_v11.privacy")


def _classify(text: str) -> frozenset[str]:
    return _privacy().classify_memory_sensitive_text(text)


@pytest.mark.parametrize(
    "text, expected",
    [
        # password / auth secret
        ("password: hunter2", "password"),
        ("password is hunter2", "password"),
        ("passphrase = hunter2", "password"),
        ("secret: hunter2", "password"),
        ("我的密码是 hunter2", "password"),
        ("密码为 hunter2", "password"),
        ("密码：abc12345", "password"),
        # api key / access secret
        ("api_key=sk-abcdefghijklmnopqr", "api_secret"),
        ("API key is sk-abcdefghijklmnopqr", "api_secret"),
        ("API 密钥是 abcdefghijklmnop", "api_secret"),
        ("access token: tok_abcdefghijklmnop", "api_secret"),
        ("访问令牌是 tok_abcdefghijklmnop", "api_secret"),
        ("sk-abcdefghijklmnopqrstuv", "api_secret"),
        ("pk-abcdefghijklmnopqrstuv", "api_secret"),
        # otp / verification code
        ("验证码是 493821", "otp"),
        ("OTP: 493821", "otp"),
        ("verification code is A7K92Q", "otp"),
        ("校验码 123456", "otp"),
        ("OTP 是 87654321", "otp"),
        ("动态码是 a1b2c3", "otp"),
        # payment card
        ("4111111111111111", "payment_card"),
        ("4111 1111 1111 1111", "payment_card"),
        ("4111-1111-1111-1111", "payment_card"),
        ("4222222222222", "payment_card"),
        ("我的信用卡号是 4111111111111112", "payment_card"),
        ("银行卡号 4111111111111113", "payment_card"),
        ("card number: 4111111111111111", "payment_card"),
        # government identifiers
        ("123-45-6789", "government_id"),
        ("我的 SSN 是 123-45-6789", "government_id"),
        ("11010519491231002X", "government_id"),
        ("我的身份证号是 11010519491231002X", "government_id"),
        ("身份证 123456789012345678", "government_id"),
        ("护照号码是 E1234567", "government_id"),
        ("national ID is AB12345678", "government_id"),
        # precise home address
        ("我住在北京市朝阳区建国路88号2单元501室", "precise_home_address"),
        ("我住在建国路88号", "precise_home_address"),
        ("我家在朝阳区幸福街12号", "precise_home_address"),
        ("住址：建国路88号", "precise_home_address"),
        ("My home address is 123 Example Street, Apt 4B", "precise_home_address"),
        ("I live at 123 Example Road", "precise_home_address"),
    ],
)
def test_s3e2_classifier_positives(text: str, expected: str):
    """Classifier returns the expected class; class names only, no raw values."""
    result = _classify(text)
    assert expected in result, f"expected {expected!r} in {result} for {text!r}"
    assert result <= _S3E2_CLASSES, "class names only — never raw matched values"


@pytest.mark.parametrize(
    "text",
    [
        # password meta / questions / too-short
        "密码学很有趣",
        "如何设置密码？",
        "如何修改密码？",
        "密码是什么？",
        "password 是什么？",
        "password: ab",
        # api key meta / questions
        "API key 是什么？",
        "什么是 access token？",
        "如何申请 API key？",
        "API 密钥是什么？",
        "secret key",
        # otp meta / questions / bare code
        "验证码一般是6位数",
        "验证码是6位数",
        "OTP 是什么意思？",
        "我收不到验证码",
        "验证码通常多久失效？",
        "493821",
        "OTP is 6 digits",
        # payment card negatives (blanket long-digit behavior removed)
        "订单号 2024080512345",
        "1234567890123",
        "银行卡通常有16位数字",
        "我的学号是 2021001",
        "邮编 100000",
        # government id negatives
        "身份证怎么办理？",
        "SSN 是什么？",
        "护照怎么申请？",
        "123456789012345678",
        "订单号 123456789012345",
        # precise home address negatives
        "我住在北京",
        "我来自上海",
        "我在纽约工作",
        "我喜欢南京路",
        "南京路很好逛",
        "123 Example Street is a famous place",
        "我住在南京路附近",
        "房间号 502",
        # health / allergy facts stay allowed
        "我对花生过敏",
        "我有哮喘",
        # ordinary durable input
        "我喜欢黑咖啡",
    ],
)
def test_s3e2_classifier_negatives(text: str):
    assert _classify(text) == frozenset(), f"expected no class for {text!r}"


def test_s3e2_redact_user_text_surface():
    """redact_user_text keeps its call surface; raw fragments never returned."""
    jobs = _jobs()
    assert jobs.redact_user_text("") == ("", False)
    assert jobs.redact_user_text("我喜欢黑咖啡") == ("我喜欢黑咖啡", False)
    assert jobs.redact_user_text("password: hunter2") == ("[REDACTED]", True)
    redacted, blocked = jobs.redact_user_text("验证码是 493821")
    assert blocked and "493821" not in redacted
    redacted2, blocked2 = jobs.redact_user_text("我的密码是 hunter2")
    assert blocked2 and "hunter2" not in redacted2
    # old blanket long-digit behavior is removed: non-Luhn long number passes.
    assert jobs.redact_user_text("订单号 2024080512345") == (
        "订单号 2024080512345",
        False,
    )


_S3E2_BLOCKED_SAMPLES = [
    ("password", "我的密码是 hunter2", "hunter2"),
    ("api_secret", "API key is sk-abcdefghijklmnopqr", "sk-abcdefghijklmnopqr"),
    ("otp", "验证码是 493821", "493821"),
    ("payment_card", "4111 1111 1111 1111", "4111 1111 1111 1111"),
    ("government_id", "我的身份证号是 11010519491231002X", "11010519491231002X"),
    ("precise_home_address", "我住在北京市朝阳区建国路88号2单元501室", "建国路88号"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("label, text, fragment", _S3E2_BLOCKED_SAMPLES)
async def test_s3e2_blocked_job_completes_zero_everything(
    isolated_store, monkeypatch, label, text, fragment
):
    """Whole-message fail-closed: no selectors, no provider, no Memory writes."""
    jobs = _jobs()
    session_id = f"s3e2-{label}-{uuid4().hex[:8]}"
    counts = {"completion": 0, "stable": 0, "unresolved": 0}

    async def fake_completion(**kwargs: Any) -> dict[str, Any]:
        counts["completion"] += 1
        return {"observations": [], "operations": []}

    async def spy_stable(**kwargs: Any) -> list[Any]:
        counts["stable"] += 1
        return []

    async def spy_unresolved(**kwargs: Any) -> list[Any]:
        counts["unresolved"] += 1
        return []

    monkeypatch.setattr(
        "app.services.memory_v11.retrieval.select_stable_fact_candidates",
        spy_stable,
    )
    monkeypatch.setattr(
        "app.services.memory_v11.retrieval.select_unresolved_observations",
        spy_unresolved,
    )

    job, _, _ = await _enqueue_user_job(
        session_id=session_id, user_text=text, title=f"s3e2-{label}"
    )
    result = await jobs.process_memory_job(
        job_id=job["job_id"],
        lease_owner="w-s3e2",
        memory_completion=fake_completion,
    )
    assert result["state"] == "completed", f"{label}: job must complete"
    assert counts["completion"] == 0, f"{label}: provider must not run"
    assert counts["stable"] == 0, f"{label}: stable selector must not run"
    assert counts["unresolved"] == 0, f"{label}: unresolved selector must not run"

    db = await get_db("steins_gate", "memory")
    try:
        for table in ("memory_observations", "experiences", "stable_facts"):
            row = await (await db.execute(
                f"SELECT COUNT(*) AS n FROM {table} WHERE session_id=?",
                (session_id,),
            )).fetchone()
            assert int(row["n"]) == 0, f"{label}: {table} must stay empty"
        src = await (await db.execute(
            """SELECT COUNT(*) AS n FROM memory_observation_sources s
                JOIN memory_observations o ON o.observation_id=s.observation_id
               WHERE o.session_id=?""",
            (session_id,),
        )).fetchone()
        assert int(src["n"]) == 0, f"{label}: observation sources must stay empty"
        ev = await (await db.execute(
            """SELECT COUNT(*) AS n FROM stable_fact_evidence e
                JOIN stable_facts f ON f.fact_id=e.fact_id
               WHERE f.session_id=?""",
            (session_id,),
        )).fetchone()
        assert int(ev["n"]) == 0, f"{label}: evidence must stay empty"
        # synthetic sensitive value must not be stored in any Memory text column.
        row = await (await db.execute(
            """SELECT
                   (SELECT COUNT(*) FROM memory_observations
                     WHERE session_id=? AND display_text LIKE ?) +
                   (SELECT COUNT(*) FROM experiences
                     WHERE session_id=? AND display_text LIKE ?) +
                   (SELECT COUNT(*) FROM stable_fact_versions v
                      JOIN stable_facts f ON f.fact_id=v.fact_id
                     WHERE f.session_id=? AND v.display_text LIKE ?) AS n""",
            (
                session_id, f"%{fragment}%",
                session_id, f"%{fragment}%",
                session_id, f"%{fragment}%",
            ),
        )).fetchone()
        assert int(row["n"]) == 0, f"{label}: raw fragment must never be stored"
    finally:
        await db.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "label, text",
    [
        ("ordinary", "我喜欢黑咖啡"),
        ("health", "我对花生过敏"),
        ("coarse_location", "我住在北京"),
        ("meta_otp", "验证码一般是6位数"),
        ("long_number", "订单号 2024080512345"),
    ],
)
async def test_s3e2_clean_input_reaches_provider_once(
    isolated_store, monkeypatch, label, text
):
    """Negative control: clean input proceeds to exactly one completion."""
    jobs = _jobs()
    session_id = f"s3e2-ok-{label}-{uuid4().hex[:8]}"
    counts = {"completion": 0}

    async def fake_completion(**kwargs: Any) -> dict[str, Any]:
        counts["completion"] += 1
        return {"observations": [], "operations": []}

    job, _, _ = await _enqueue_user_job(
        session_id=session_id, user_text=text, title=f"s3e2-ok-{label}"
    )
    result = await jobs.process_memory_job(
        job_id=job["job_id"],
        lease_owner="w-s3e2",
        memory_completion=fake_completion,
    )
    assert result["state"] == "completed"
    assert counts["completion"] == 1, f"{label}: provider must run exactly once"


@pytest.mark.asyncio
async def test_s3e2_blocked_replay_stays_zero(isolated_store, monkeypatch):
    """Blocked → completed; replay and re-run stay zero provider / zero writes."""
    jobs = _jobs()
    session_id = f"s3e2-replay-{uuid4().hex[:8]}"
    counts = {"completion": 0}

    async def fake_completion(**kwargs: Any) -> dict[str, Any]:
        counts["completion"] += 1
        return {"observations": [], "operations": []}

    job, _, _ = await _enqueue_user_job(
        session_id=session_id,
        user_text="我的密码是 hunter2",
        title="s3e2-replay",
    )
    first = await jobs.process_memory_job(
        job_id=job["job_id"],
        lease_owner="w-s3e2",
        memory_completion=fake_completion,
    )
    assert first["state"] == "completed"
    assert counts["completion"] == 0

    again = await jobs.process_memory_job(
        job_id=job["job_id"],
        lease_owner="w-s3e2",
        memory_completion=fake_completion,
    )
    assert again["state"] == "completed"
    assert counts["completion"] == 0

    # crash-before-completed equivalent: force back to pending; classifier
    # re-runs, still blocked, still zero provider / zero semantic writes.
    db = await get_db("steins_gate", "memory")
    try:
        await db.execute(
            """UPDATE memory_ingest_jobs
                SET state='pending', lease_owner=NULL, lease_expires_at=NULL,
                    last_error_code=NULL, completed_at=NULL
              WHERE job_id=?""",
            (job["job_id"],),
        )
        await db.commit()
    finally:
        await db.close()
    rerun = await jobs.process_memory_job(
        job_id=job["job_id"],
        lease_owner="w-s3e2",
        memory_completion=fake_completion,
    )
    assert rerun["state"] == "completed"
    assert counts["completion"] == 0
    db2 = await get_db("steins_gate", "memory")
    try:
        row = await (await db2.execute(
            "SELECT COUNT(*) AS n FROM memory_observations WHERE session_id=?",
            (session_id,),
        )).fetchone()
    finally:
        await db2.close()
    assert int(row["n"]) == 0, "replay must never write observations"


# ---------------------------------------------------------------------------
# S3E-2 REWORK: P1-A/P1-B/P1-C false-positive family fixes (reviewer repro).
# ---------------------------------------------------------------------------

_REWORK_REPRO_NEGATIVES = [
    # P1-A: EN password meta prose must never be a "secret value".
    "password is usually at least 8 characters",
    "password is required",
    "password is important",
    "password is incorrect",
    "password is weak",
    "password is empty",
    "the password is required",
    "密码是用来保护账户的",
    # P1-B: EN government labels must not capture ordinary prose.
    "social security is important",
    "social security benefits",
    "social security number",
    "social security is required for some forms",
    "social security is a government program",
    "passport is important",
    "passport application process",
    "national ID 是什么？",
    # P1-C: cross-clause CN address contamination.
    "我住在北京，南京路88号是公司地址",
    "我住在北京，我喜欢建国路88号那家店",
    "我住在北京；南京路88号是公司地址",
    "我住在北京。南京路88号是公司地址",
    "我住在北京，我在建国路88号上班",
    "我喜欢南京路88号那家店",
    "123 Example Street is my office",
    "I live in New York; 123 Example Street is my office",
]


@pytest.mark.parametrize("text", _REWORK_REPRO_NEGATIVES)
def test_s3e2_rework_reviewer_repro_negatives(text: str):
    assert _classify(text) == frozenset(), f"rework repro must not block {text!r}"


@pytest.mark.parametrize(
    "text, expected",
    [
        # P1-A strong presentation / ownership with alphabetic-only values.
        ("my password is huntertwo", "password"),
        ("password=abcdef", "password"),
        ("password: huntertwo", "password"),
        ("我的密码是 abcdef", "password"),
        ("我的密码为 abcdef", "password"),
        # P1-A generic copula with secret-shaped values stays blocking.
        ("password is p@ssword", "password"),
        ("password is hunter2", "password"),
        # P1-B labeled EN identifiers with identifier-shaped values.
        ("SSN is 123456789", "government_id"),
        ("social security number is 123-45-6789", "government_id"),
        ("passport number: AB123456", "government_id"),
        ("passport is AB123456", "government_id"),
        ("national ID: A1234567", "government_id"),
        ("government ID: 1234567890", "government_id"),
    ],
)
def test_s3e2_rework_positive_controls(text: str, expected: str):
    result = _classify(text)
    assert expected in result, f"expected {expected!r} in {result} for {text!r}"
    assert result <= _S3E2_CLASSES, "class names only — never raw matched values"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "label, text",
    [
        ("password_meta", "password is required"),
        ("gov_prose", "social security is important"),
        ("cross_clause_address", "我住在北京，南京路88号是公司地址"),
    ],
)
async def test_s3e2_rework_clean_families_reach_provider_once(
    isolated_store, monkeypatch, label, text
):
    """Each repaired family reaches exactly one provider completion."""
    jobs = _jobs()
    session_id = f"s3e2-rw-ok-{label}-{uuid4().hex[:8]}"
    counts = {"completion": 0}

    async def fake_completion(**kwargs: Any) -> dict[str, Any]:
        counts["completion"] += 1
        return {"observations": [], "operations": []}

    job, _, _ = await _enqueue_user_job(
        session_id=session_id, user_text=text, title=f"s3e2-rw-{label}"
    )
    result = await jobs.process_memory_job(
        job_id=job["job_id"],
        lease_owner="w-s3e2-rw",
        memory_completion=fake_completion,
    )
    assert result["state"] == "completed"
    assert counts["completion"] == 1, f"{label}: provider must run exactly once"


_S3E2_REWORK_BLOCKED = [
    ("password", "password is hunter2", "hunter2"),
    ("government_id", "SSN: 123-45-6789", "123-45-6789"),
    ("precise_home_address", "我住在建国路88号", "建国路88号"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("label, text, fragment", _S3E2_REWORK_BLOCKED)
async def test_s3e2_rework_blocked_stop_gates_still_zero(
    isolated_store, monkeypatch, label, text, fragment
):
    """Rework must not trade FP fixes for privacy false negatives."""
    jobs = _jobs()
    session_id = f"s3e2-rw-blk-{label}-{uuid4().hex[:8]}"
    counts = {"completion": 0, "stable": 0, "unresolved": 0}

    async def fake_completion(**kwargs: Any) -> dict[str, Any]:
        counts["completion"] += 1
        return {"observations": [], "operations": []}

    async def spy_stable(**kwargs: Any) -> list[Any]:
        counts["stable"] += 1
        return []

    async def spy_unresolved(**kwargs: Any) -> list[Any]:
        counts["unresolved"] += 1
        return []

    monkeypatch.setattr(
        "app.services.memory_v11.retrieval.select_stable_fact_candidates",
        spy_stable,
    )
    monkeypatch.setattr(
        "app.services.memory_v11.retrieval.select_unresolved_observations",
        spy_unresolved,
    )

    job, _, _ = await _enqueue_user_job(
        session_id=session_id, user_text=text, title=f"s3e2-rw-blk-{label}"
    )
    result = await jobs.process_memory_job(
        job_id=job["job_id"],
        lease_owner="w-s3e2-rw",
        memory_completion=fake_completion,
    )
    assert result["state"] == "completed", f"{label}: job must complete"
    assert counts["completion"] == 0, f"{label}: provider must not run"
    assert counts["stable"] == 0, f"{label}: stable selector must not run"
    assert counts["unresolved"] == 0, f"{label}: unresolved selector must not run"

    db = await get_db("steins_gate", "memory")
    try:
        for table in ("memory_observations", "experiences", "stable_facts"):
            row = await (await db.execute(
                f"SELECT COUNT(*) AS n FROM {table} WHERE session_id=?",
                (session_id,),
            )).fetchone()
            assert int(row["n"]) == 0, f"{label}: {table} must stay empty"
        src = await (await db.execute(
            """SELECT COUNT(*) AS n FROM memory_observation_sources s
                JOIN memory_observations o ON o.observation_id=s.observation_id
               WHERE o.session_id=?""",
            (session_id,),
        )).fetchone()
        assert int(src["n"]) == 0, f"{label}: observation sources must stay empty"
        row = await (await db.execute(
            """SELECT
                   (SELECT COUNT(*) FROM memory_observations
                     WHERE session_id=? AND display_text LIKE ?) +
                   (SELECT COUNT(*) FROM experiences
                     WHERE session_id=? AND display_text LIKE ?) AS n""",
            (session_id, f"%{fragment}%", session_id, f"%{fragment}%"),
        )).fetchone()
        assert int(row["n"]) == 0, f"{label}: raw fragment must never be stored"
    finally:
        await db.close()


# ---------------------------------------------------------------------------
# MEMORY-V11-S3F-1: D28 durable ingest receipts.
# ---------------------------------------------------------------------------


async def _receipt_rows(session_id: str | None = None) -> list[dict[str, Any]]:
    db = await get_db("steins_gate", "memory")
    try:
        if session_id is None:
            rows = await (
                await db.execute(
                    "SELECT * FROM memory_ingest_receipts ORDER BY created_at, session_id"
                )
            ).fetchall()
        else:
            rows = await (
                await db.execute(
                    "SELECT * FROM memory_ingest_receipts WHERE session_id=? ORDER BY created_at",
                    (session_id,),
                )
            ).fetchall()
        return [dict(row) for row in rows]
    finally:
        await db.close()


async def _session_zero_semantic_rows(session_id: str) -> None:
    """Zero observations / sources / facts / versions / evidence / experiences / vectors / topics."""
    db = await get_db("steins_gate", "memory")
    try:
        checks = {
            "observations": "SELECT COUNT(*) AS n FROM memory_observations WHERE session_id=?",
            "experiences": "SELECT COUNT(*) AS n FROM experiences WHERE session_id=?",
            "facts": "SELECT COUNT(*) AS n FROM stable_facts WHERE session_id=?",
            "versions": """SELECT COUNT(*) AS n FROM stable_fact_versions v
                             JOIN stable_facts f ON f.fact_id=v.fact_id
                            WHERE f.session_id=?""",
            "evidence": """SELECT COUNT(*) AS n FROM stable_fact_evidence e
                             JOIN stable_facts f ON f.fact_id=e.fact_id
                            WHERE f.session_id=?""",
            "sources": """SELECT COUNT(*) AS n FROM memory_observation_sources s
                            JOIN memory_observations o ON o.observation_id=s.observation_id
                           WHERE o.session_id=?""",
            "vectors": """SELECT COUNT(*) AS n FROM stable_fact_version_embeddings e
                            JOIN stable_facts f ON f.fact_id=e.fact_id
                           WHERE f.session_id=?""",
            "topics": "SELECT COUNT(*) AS n FROM memory_topics WHERE session_id=?",
        }
        for label, sql in checks.items():
            row = await (await db.execute(sql, (session_id,))).fetchone()
            assert int(row["n"]) == 0, f"{label} must be zero"
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_s3f1_e0_empty_completion_writes_provider_empty_receipt(isolated_store):
    """E0/R3: valid empty completion → one full-cursor provider_empty receipt, zero semantic rows."""
    jobs = _jobs()
    session_id = f"s3f1-e0-{uuid4().hex[:8]}"
    conv = await conversation_service.create(
        session_id, "steins_gate", title="e0", identity_mode="self"
    )
    conversation_id = str(conv["id"])
    mid = await models.save_message(
        session_id, "user", "今天天气不错",
        worldline="steins_gate", conversation_id=conversation_id,
    )
    call_count = {"n": 0}

    async def empty_completion(**kwargs):
        call_count["n"] += 1
        return {"observations": [], "operations": []}

    result = await _run_capture_job(
        session_id=session_id, conversation_id=conversation_id,
        source_message_id=int(mid), completion=empty_completion,
    )
    assert result["state"] == "completed"
    assert call_count["n"] == 1
    receipts = await _receipt_rows(session_id)
    assert len(receipts) == 1, "exactly one receipt for the empty completion"
    receipt = receipts[0]
    assert receipt["receipt_kind"] == "provider_empty"
    assert receipt["session_id"] == session_id
    assert receipt["conversation_id"] == conversation_id
    assert receipt["identity_mode"] == "self"
    assert int(receipt["source_message_id"]) == int(mid)
    assert receipt["pipeline_version"] == "memory-v11-1"
    await _session_zero_semantic_rows(session_id)


@pytest.mark.asyncio
async def test_s3f1_e1_empty_crash_window_retry_zero(isolated_store, monkeypatch):
    """E1/R2: empty receipt commits, crash before completed → retry provider/selectors/reconciler zero."""
    jobs = _jobs()
    session_id = f"s3f1-e1-{uuid4().hex[:8]}"
    conv = await conversation_service.create(
        session_id, "steins_gate", title="e1", identity_mode="self"
    )
    conversation_id = str(conv["id"])
    mid = await models.save_message(
        session_id, "user", "今天天气不错",
        worldline="steins_gate", conversation_id=conversation_id,
    )

    async def empty_completion(**kwargs):
        return {"observations": [], "operations": []}

    original_transition = jobs._transition_owned
    crashed = {"done": False}

    async def crash_after_receipt(**kwargs):
        if kwargs.get("state") == "completed" and not crashed["done"]:
            crashed["done"] = True
            raise RuntimeError("simulated crash after empty receipt commit")
        return await original_transition(**kwargs)

    monkeypatch.setattr(jobs, "_transition_owned", crash_after_receipt)
    job_id = await _enqueue_self_job(
        session_id=session_id, conversation_id=conversation_id,
        source_message_id=int(mid),
    )
    with pytest.raises(RuntimeError):
        await jobs.process_memory_job(
            job_id=job_id, lease_owner="w-e1", memory_completion=empty_completion
        )
    receipts = await _receipt_rows(session_id)
    assert len(receipts) == 1 and receipts[0]["receipt_kind"] == "provider_empty"

    db = await get_db("steins_gate", "memory")
    try:
        await db.execute(
            "UPDATE memory_ingest_jobs SET lease_expires_at=? WHERE job_id=?",
            ("2000-01-01T00:00:00+00:00", job_id),
        )
        await db.commit()
    finally:
        await db.close()

    counts = {"completion": 0, "stable": 0, "unresolved": 0, "reconciler": 0}

    async def must_not_run(**kwargs):
        counts["completion"] += 1
        return {"observations": [], "operations": []}

    async def spy_stable(**kwargs):
        counts["stable"] += 1
        return []

    async def spy_unresolved(**kwargs):
        counts["unresolved"] += 1
        return []

    async def spy_reconciler(**kwargs):
        counts["reconciler"] += 1
        return {}

    monkeypatch.setattr(
        "app.services.memory_v11.retrieval.select_stable_fact_candidates", spy_stable
    )
    monkeypatch.setattr(
        "app.services.memory_v11.retrieval.select_unresolved_observations",
        spy_unresolved,
    )
    monkeypatch.setattr(
        "app.services.memory_v11.reconciler.apply_validated_memory_operations",
        spy_reconciler,
    )

    retried = await jobs.process_memory_job(
        job_id=job_id, lease_owner="w-e1-retry", memory_completion=must_not_run
    )
    assert retried["state"] == "completed"
    assert counts == {"completion": 0, "stable": 0, "unresolved": 0, "reconciler": 0}
    assert len(await _receipt_rows(session_id)) == 1, "no duplicate receipt"


@pytest.mark.asyncio
async def test_s3f1_e2_pre_receipt_crash_retry_may_call_provider(
    isolated_store, monkeypatch
):
    """E2 honesty boundary: crash BEFORE the empty-receipt commit → no receipt;
    retry is allowed to invoke the provider again (no provider exactly-once)."""
    jobs = _jobs()
    session_id = f"s3f1-e2-{uuid4().hex[:8]}"
    conv = await conversation_service.create(
        session_id, "steins_gate", title="e2", identity_mode="self"
    )
    conversation_id = str(conv["id"])
    mid = await models.save_message(
        session_id, "user", "今天天气不错",
        worldline="steins_gate", conversation_id=conversation_id,
    )

    async def empty_completion(**kwargs):
        return {"observations": [], "operations": []}

    async def boom_insert(**kwargs):
        raise RuntimeError("simulated crash before receipt commit")

    receipts_module = importlib.import_module("app.services.memory_v11.receipts")
    original_insert = receipts_module.insert_ingest_receipt
    monkeypatch.setattr(
        "app.services.memory_v11.receipts.insert_ingest_receipt", boom_insert
    )
    job_id = await _enqueue_self_job(
        session_id=session_id, conversation_id=conversation_id,
        source_message_id=int(mid),
    )
    result = await jobs.process_memory_job(
        job_id=job_id, lease_owner="w-e2", memory_completion=empty_completion
    )
    assert result["state"] in {"pending", "failed"}
    assert await _receipt_rows(session_id) == [], "no receipt before commit"

    db = await get_db("steins_gate", "memory")
    try:
        await db.execute(
            "UPDATE memory_ingest_jobs SET state='pending', next_attempt_at=NULL WHERE job_id=?",
            (job_id,),
        )
        await db.commit()
    finally:
        await db.close()

    # restore the insert so the retry may complete normally
    monkeypatch.setattr(
        "app.services.memory_v11.receipts.insert_ingest_receipt", original_insert
    )

    counts = {"completion": 0}

    async def again(**kwargs):
        counts["completion"] += 1
        return {"observations": [], "operations": []}

    retried = await jobs.process_memory_job(
        job_id=job_id, lease_owner="w-e2-retry", memory_completion=again
    )
    assert retried["state"] == "completed"
    assert counts["completion"] == 1, "retry without receipt may call provider again"
    assert len(await _receipt_rows(session_id)) == 1


@pytest.mark.asyncio
async def test_s3f1_e4_receipt_cursor_isolation(isolated_store):
    """E4: receipt does not suppress a different identity / conversation / pipeline."""
    from app.services.memory_v11.receipts import has_ingest_receipt, insert_ingest_receipt

    db = await get_db("steins_gate", "memory")
    try:
        await db.execute("BEGIN IMMEDIATE")
        await insert_ingest_receipt(
            db,
            session_id="s4",
            conversation_id="c1",
            identity_mode="self",
            source_message_id=9001,
            pipeline_version="memory-v11-1",
            receipt_kind="provider_empty",
        )
        await db.commit()
        assert await has_ingest_receipt(
            db, session_id="s4", conversation_id="c1", identity_mode="self",
            source_message_id=9001, pipeline_version="memory-v11-1",
        )
        for cursor in (
            dict(session_id="s4", conversation_id="c1", identity_mode="okabe",
                 source_message_id=9001, pipeline_version="memory-v11-1"),
            dict(session_id="s4", conversation_id="c2", identity_mode="self",
                 source_message_id=9001, pipeline_version="memory-v11-1"),
            dict(session_id="s4", conversation_id="c1", identity_mode="self",
                 source_message_id=9002, pipeline_version="memory-v11-1"),
            dict(session_id="s4", conversation_id="c1", identity_mode="self",
                 source_message_id=9001, pipeline_version="memory-v11-2"),
            dict(session_id="other", conversation_id="c1", identity_mode="self",
                 source_message_id=9001, pipeline_version="memory-v11-1"),
        ):
            assert not await has_ingest_receipt(db, **cursor), cursor
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_s3f1_e4b_future_pipeline_not_suppressed_by_v1_receipt(
    isolated_store,
):
    """Mandatory pipeline isolation: a memory-v11-1 receipt must not suppress a v2 job."""
    jobs = _jobs()
    from app.services.memory_v11.receipts import insert_ingest_receipt

    session_id = f"s3f1-e4b-{uuid4().hex[:8]}"
    conv = await conversation_service.create(
        session_id, "steins_gate", title="e4b", identity_mode="self"
    )
    conversation_id = str(conv["id"])
    mid = await models.save_message(
        session_id, "user", "今天天气不错",
        worldline="steins_gate", conversation_id=conversation_id,
    )
    db = await get_db("steins_gate", "memory")
    try:
        await db.execute("BEGIN IMMEDIATE")
        await insert_ingest_receipt(
            db,
            session_id=session_id,
            conversation_id=conversation_id,
            identity_mode="self",
            source_message_id=int(mid),
            pipeline_version="memory-v11-1",
            receipt_kind="provider_empty",
        )
        await db.commit()
    finally:
        await db.close()

    counts = {"completion": 0}

    async def empty_completion(**kwargs):
        counts["completion"] += 1
        return {"observations": [], "operations": []}

    job = await jobs.enqueue_memory_job(
        session_id=session_id,
        worldline="steins_gate",
        conversation_id=conversation_id,
        identity_mode="self",
        source_message_id=int(mid),
        pipeline_version="memory-v11-2",
        provider_id="deepseek",
        model_id="deepseek-chat",
    )
    result = await jobs.process_memory_job(
        job_id=job["job_id"], lease_owner="w-e4b", memory_completion=empty_completion
    )
    assert result["state"] == "completed"
    assert counts["completion"] == 1, "v1 receipt must never suppress a v2 job"
    receipts = await _receipt_rows(session_id)
    assert len(receipts) == 2, "v2 job writes its own receipt"


@pytest.mark.asyncio
async def test_s3f1_e5_legacy_observation_receipt_current_pipeline_only(
    isolated_store, monkeypatch
):
    """E5: current-pipeline legacy observation proof suppresses; future pipeline does not."""
    jobs = _jobs()
    session_id = f"s3f1-e5-{uuid4().hex[:8]}"
    conv = await conversation_service.create(
        session_id, "steins_gate", title="e5", identity_mode="self"
    )
    conversation_id = str(conv["id"])
    mid = await models.save_message(
        session_id, "user", "在看一部动漫",
        worldline="steins_gate", conversation_id=conversation_id,
    )
    await _seed_unresolved_candidate(
        session_id=session_id, identity_mode="self",
        conversation_id=conversation_id, source_message_id=int(mid),
        display_text="在看一部动漫",
    )
    assert await _receipt_rows(session_id) == [], "no dedicated receipt seeded"

    counts = {"completion": 0, "stable": 0, "unresolved": 0}

    async def must_not_run(**kwargs):
        counts["completion"] += 1
        return {"observations": [], "operations": []}

    async def spy_stable(**kwargs):
        counts["stable"] += 1
        return []

    async def spy_unresolved(**kwargs):
        counts["unresolved"] += 1
        return []

    monkeypatch.setattr(
        "app.services.memory_v11.retrieval.select_stable_fact_candidates", spy_stable
    )
    monkeypatch.setattr(
        "app.services.memory_v11.retrieval.select_unresolved_observations",
        spy_unresolved,
    )
    job_id = await _enqueue_self_job(
        session_id=session_id, conversation_id=conversation_id,
        source_message_id=int(mid),
    )
    retried = await jobs.process_memory_job(
        job_id=job_id, lease_owner="w-e5", memory_completion=must_not_run
    )
    assert retried["state"] == "completed"
    assert counts == {"completion": 0, "stable": 0, "unresolved": 0}, (
        "current-pipeline legacy observation receipt must suppress replay"
    )

    future_job = await jobs.enqueue_memory_job(
        session_id=session_id,
        worldline="steins_gate",
        conversation_id=conversation_id,
        identity_mode="self",
        source_message_id=int(mid),
        pipeline_version="memory-v11-2",
        provider_id="deepseek",
        model_id="deepseek-chat",
    )
    future_result = await jobs.process_memory_job(
        job_id=future_job["job_id"], lease_owner="w-e5-future",
        memory_completion=must_not_run,
    )
    assert future_result["state"] == "completed"
    assert counts["completion"] == 1, (
        "legacy observation receipt must NOT suppress a future pipeline"
    )


@pytest.mark.asyncio
async def test_s3f1_e6_receipt_insert_failure_rolls_back_semantic_writes(
    isolated_store, monkeypatch
):
    """E6: receipt insert failure after semantic writes → full transaction rollback."""
    jobs = _jobs()
    session_id = f"s3f1-e6-{uuid4().hex[:8]}"
    conv = await conversation_service.create(
        session_id, "steins_gate", title="e6", identity_mode="self"
    )
    conversation_id = str(conv["id"])
    mid = await models.save_message(
        session_id, "user", "我喜欢黑咖啡",
        worldline="steins_gate", conversation_id=conversation_id,
    )

    async def create_completion(**kwargs):
        sid = int(kwargs["source_message_id"])
        return {
            "observations": [
                {
                    "observation_ref": "o1",
                    "source_message_ids": [sid],
                    "display_text": "我喜欢黑咖啡",
                    "semantic": {"subject": "user", "predicate": "likes", "object": "coffee"},
                    "evidence_kind": "direct_user",
                    "memory_class": "stable_candidate",
                    "confidence": 0.95,
                    "topic_label": "咖啡",
                    "expires_at": None,
                }
            ],
            "operations": [
                {"op": "CREATE", "observation_ref": "o1", "fact_text": "我喜欢黑咖啡",
                 "reason_code": "direct_durable_statement"}
            ],
        }

    async def boom_receipt(**kwargs):
        raise RuntimeError("forced receipt insert failure after semantic writes")

    monkeypatch.setattr(
        "app.services.memory_v11.reconciler.insert_ingest_receipt", boom_receipt
    )
    result = await _run_capture_job(
        session_id=session_id, conversation_id=conversation_id,
        source_message_id=int(mid), completion=create_completion,
    )
    assert result["state"] in {"pending", "failed"}
    assert result.get("last_error_code") == "validation_failed"
    assert await _receipt_rows(session_id) == [], "no receipt may survive rollback"
    await _session_zero_semantic_rows(session_id)


@pytest.mark.asyncio
async def test_s3f1_e7_semantic_failure_certifies_no_receipt(isolated_store):
    """E7: failed semantic validation never certifies a processed receipt."""
    jobs = _jobs()
    session_id = f"s3f1-e7-{uuid4().hex[:8]}"
    conv = await conversation_service.create(
        session_id, "steins_gate", title="e7", identity_mode="self"
    )
    conversation_id = str(conv["id"])
    mid = await models.save_message(
        session_id, "user", "解析目标",
        worldline="steins_gate", conversation_id=conversation_id,
    )

    async def forged_completion(**kwargs):
        sid = int(kwargs["source_message_id"])
        return {
            "observations": [
                {
                    "observation_ref": "o1",
                    "reanalysis_target_observation_id": "ghost-target",
                    "source_message_ids": [sid],
                    "display_text": "解析后",
                    "semantic": {"subject": "user", "predicate": "x", "object": "y"},
                    "evidence_kind": "direct_user",
                    "memory_class": "stable_candidate",
                    "confidence": 0.96,
                    "topic_label": None,
                    "expires_at": None,
                }
            ],
            "operations": [
                {"op": "CREATE", "observation_ref": "o1", "fact_text": "解析后",
                 "reason_code": "d18"}
            ],
        }

    result = await _run_capture_job(
        session_id=session_id, conversation_id=conversation_id,
        source_message_id=int(mid), completion=forged_completion,
    )
    assert result["state"] in {"pending", "failed"}
    assert result.get("last_error_code") == "validation_failed"
    assert await _receipt_rows(session_id) == [], (
        "failed semantic result must never be certified as processed"
    )
    await _session_zero_semantic_rows(session_id)


@pytest.mark.asyncio
async def test_s3f1_e8_privacy_blocked_no_receipt(isolated_store, monkeypatch):
    """E8: S3E-2 privacy-blocked path — provider/selectors zero AND no receipt."""
    jobs = _jobs()
    session_id = f"s3f1-e8-{uuid4().hex[:8]}"
    conv = await conversation_service.create(
        session_id, "steins_gate", title="e8", identity_mode="self"
    )
    conversation_id = str(conv["id"])
    mid = await models.save_message(
        session_id, "user", "我的密码是 hunter2",
        worldline="steins_gate", conversation_id=conversation_id,
    )
    counts = {"completion": 0, "stable": 0, "unresolved": 0}

    async def must_not_run(**kwargs):
        counts["completion"] += 1
        return {"observations": [], "operations": []}

    async def spy_stable(**kwargs):
        counts["stable"] += 1
        return []

    async def spy_unresolved(**kwargs):
        counts["unresolved"] += 1
        return []

    monkeypatch.setattr(
        "app.services.memory_v11.retrieval.select_stable_fact_candidates", spy_stable
    )
    monkeypatch.setattr(
        "app.services.memory_v11.retrieval.select_unresolved_observations",
        spy_unresolved,
    )
    job_id = await _enqueue_self_job(
        session_id=session_id, conversation_id=conversation_id,
        source_message_id=int(mid),
    )
    result = await jobs.process_memory_job(
        job_id=job_id, lease_owner="w-e8", memory_completion=must_not_run
    )
    assert result["state"] == "completed"
    assert counts == {"completion": 0, "stable": 0, "unresolved": 0}
    assert await _receipt_rows(session_id) == [], (
        "privacy-blocked path must never write a provider receipt"
    )
    await _session_zero_semantic_rows(session_id)
    db = await get_db("steins_gate", "memory")
    try:
        row = await (await db.execute(
            """SELECT
                   (SELECT COUNT(*) FROM memory_observations WHERE session_id=? AND display_text LIKE ?) +
                   (SELECT COUNT(*) FROM experiences WHERE session_id=? AND display_text LIKE ?) +
                   (SELECT COUNT(*) FROM stable_fact_versions v
                      JOIN stable_facts f ON f.fact_id=v.fact_id
                     WHERE f.session_id=? AND v.display_text LIKE ?) AS n""",
            (session_id, "%hunter2%", session_id, "%hunter2%",
             session_id, "%hunter2%"),
        )).fetchone()
        assert int(row["n"]) == 0, "raw sensitive fragment must never be stored"
    finally:
        await db.close()


# ---------------------------------------------------------------------------
# MEMORY-V11-S3F-2A: provider output type/count hard bounds.
# ---------------------------------------------------------------------------


def _s3f2_reject_batch(count: int, sid: int) -> tuple[list[dict], list[dict]]:
    observations = []
    operations = []
    for i in range(count):
        observations.append(
            {
                "observation_ref": f"r{i}",
                "source_message_ids": [sid],
                "display_text": f"候选{i}",
                "semantic": {"subject": "user", "predicate": "p", "object": str(i)},
                "evidence_kind": "direct_user",
                "memory_class": "stable_candidate",
                "confidence": 0.2,
                "topic_label": None,
                "expires_at": None,
            }
        )
        operations.append(
            {"op": "REJECT", "observation_ref": f"r{i}", "fact_text": f"候选{i}",
             "reason_code": "s3f2"}
        )
    return observations, operations


async def _run_output_job(
    *,
    session_id: str,
    conversation_id: str,
    source_message_id: int,
    completion: Any,
    monkeypatch,
    spy_reconciler: bool = True,
) -> tuple[dict[str, Any], dict[str, int]]:
    jobs = _jobs()
    counts = {"completion": 0, "reconciler": 0}

    async def counting_completion(**kwargs):
        counts["completion"] += 1
        return await completion(**kwargs)

    if spy_reconciler:
        from app.services.memory_v11 import reconciler as reconciler_mod

        original = reconciler_mod.apply_validated_memory_operations

        async def spy_apply(**kwargs):
            counts["reconciler"] += 1
            return await original(**kwargs)

        monkeypatch.setattr(
            "app.services.memory_v11.reconciler.apply_validated_memory_operations",
            spy_apply,
        )
    job_id = await _enqueue_self_job(
        session_id=session_id, conversation_id=conversation_id,
        source_message_id=source_message_id,
    )
    result = await jobs.process_memory_job(
        job_id=job_id, lease_owner="w-s3f2", memory_completion=counting_completion
    )
    return result, counts


_S3F2_MALFORMED = [
    ("observations_string", lambda sid: {"observations": "oops", "operations": []}),
    ("observations_null", lambda sid: {"observations": None, "operations": []}),
    ("operations_null", lambda sid: {"observations": [], "operations": None}),
    ("operations_dict", lambda sid: {"observations": [], "operations": {"op": "REJECT"}}),
    ("observations_tuple", lambda sid: {"observations": ("x",), "operations": []}),
    ("observations_scalar", lambda sid: {"observations": 7, "operations": []}),
    ("obs_item_string", lambda sid: {"observations": ["x"], "operations": []}),
    ("ops_item_number", lambda sid: {"observations": [], "operations": [1]}),
    ("obs_item_null", lambda sid: {"observations": [None], "operations": []}),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("label, builder", _S3F2_MALFORMED, ids=[b[0] for b in _S3F2_MALFORMED])
async def test_s3f2a_malformed_output_permanent_failed_zero_everything(
    isolated_store, monkeypatch, label, builder
):
    """A1/A2 + malformed matrix: validation_failed, failed, provider once, all zero."""
    session_id = f"s3f2a-{label}-{uuid4().hex[:8]}"
    conv = await conversation_service.create(
        session_id, "steins_gate", title="s3f2a", identity_mode="self"
    )
    conversation_id = str(conv["id"])
    mid = await models.save_message(
        session_id, "user", "测试输入",
        worldline="steins_gate", conversation_id=conversation_id,
    )
    payload = builder(int(mid))

    async def completion(**kwargs):
        return payload

    result, counts = await _run_output_job(
        session_id=session_id, conversation_id=conversation_id,
        source_message_id=int(mid), completion=completion, monkeypatch=monkeypatch,
    )
    assert result["state"] == "failed", f"{label}: final state must be failed"
    assert result.get("last_error_code") == "validation_failed"
    assert counts["completion"] == 1, f"{label}: provider exactly once"
    assert counts["reconciler"] == 0, f"{label}: reconciler must not run"
    assert await _receipt_rows(session_id) == [], f"{label}: no receipt on invalid output"
    await _session_zero_semantic_rows(session_id)


@pytest.mark.asyncio
async def test_s3f2a_nine_observations_rejected(isolated_store, monkeypatch):
    """A3: 9 observations (cap+1) via the real seam → failed, reconciler 0."""
    session_id = f"s3f2a-9obs-{uuid4().hex[:8]}"
    conv = await conversation_service.create(
        session_id, "steins_gate", title="s3f2a-9", identity_mode="self"
    )
    conversation_id = str(conv["id"])
    mid = await models.save_message(
        session_id, "user", "批量测试",
        worldline="steins_gate", conversation_id=conversation_id,
    )
    observations, operations = _s3f2_reject_batch(9, int(mid))

    async def completion(**kwargs):
        return {"observations": observations, "operations": operations}

    result, counts = await _run_output_job(
        session_id=session_id, conversation_id=conversation_id,
        source_message_id=int(mid), completion=completion, monkeypatch=monkeypatch,
    )
    assert result["state"] == "failed"
    assert result.get("last_error_code") == "validation_failed"
    assert counts["completion"] == 1
    assert counts["reconciler"] == 0
    assert await _receipt_rows(session_id) == []
    await _session_zero_semantic_rows(session_id)


@pytest.mark.asyncio
async def test_s3f2a_nine_operations_rejected(isolated_store, monkeypatch):
    """9 operations (cap+1) → failed, reconciler 0."""
    session_id = f"s3f2a-9ops-{uuid4().hex[:8]}"
    conv = await conversation_service.create(
        session_id, "steins_gate", title="s3f2a-9o", identity_mode="self"
    )
    conversation_id = str(conv["id"])
    mid = await models.save_message(
        session_id, "user", "批量测试",
        worldline="steins_gate", conversation_id=conversation_id,
    )
    observations, operations = _s3f2_reject_batch(9, int(mid))
    del observations[-1]

    async def completion(**kwargs):
        return {"observations": observations, "operations": operations}

    result, counts = await _run_output_job(
        session_id=session_id, conversation_id=conversation_id,
        source_message_id=int(mid), completion=completion, monkeypatch=monkeypatch,
    )
    assert result["state"] == "failed"
    assert result.get("last_error_code") == "validation_failed"
    assert counts["completion"] == 1
    assert counts["reconciler"] == 0
    assert await _receipt_rows(session_id) == []
    await _session_zero_semantic_rows(session_id)


@pytest.mark.asyncio
async def test_s3f2a_empty_missing_fields_still_valid(isolated_store, monkeypatch):
    """{} and exact empty arrays remain valid empty completions (S3F-1)."""
    for label, payload in (
        ("missing", {}),
        ("exact_empty", {"observations": [], "operations": []}),
    ):
        session_id = f"s3f2a-{label}-{uuid4().hex[:8]}"
        conv = await conversation_service.create(
            session_id, "steins_gate", title="s3f2a-e", identity_mode="self"
        )
        conversation_id = str(conv["id"])
        mid = await models.save_message(
            session_id, "user", "空输出",
            worldline="steins_gate", conversation_id=conversation_id,
        )

        async def completion(**kwargs):
            return dict(payload)

        result, counts = await _run_output_job(
            session_id=session_id, conversation_id=conversation_id,
            source_message_id=int(mid), completion=completion, monkeypatch=monkeypatch,
        )
        assert result["state"] == "completed", label
        assert counts["completion"] == 1, label
        receipts = await _receipt_rows(session_id)
        assert len(receipts) == 1 and receipts[0]["receipt_kind"] == "provider_empty", label
        await _session_zero_semantic_rows(session_id)


@pytest.mark.asyncio
async def test_s3f2a_exact_eight_accepted(isolated_store, monkeypatch):
    """Exactly 8 observations + 8 operations accepted → provider_nonempty receipt."""
    session_id = f"s3f2a-8ok-{uuid4().hex[:8]}"
    conv = await conversation_service.create(
        session_id, "steins_gate", title="s3f2a-8", identity_mode="self"
    )
    conversation_id = str(conv["id"])
    mid = await models.save_message(
        session_id, "user", "批量测试",
        worldline="steins_gate", conversation_id=conversation_id,
    )
    observations, operations = _s3f2_reject_batch(8, int(mid))

    async def completion(**kwargs):
        return {"observations": observations, "operations": operations}

    result, counts = await _run_output_job(
        session_id=session_id, conversation_id=conversation_id,
        source_message_id=int(mid), completion=completion, monkeypatch=monkeypatch,
    )
    assert result["state"] == "completed"
    assert counts["completion"] == 1
    assert counts["reconciler"] == 1
    receipts = await _receipt_rows(session_id)
    assert len(receipts) == 1 and receipts[0]["receipt_kind"] == "provider_nonempty"
    db = await get_db("steins_gate", "memory")
    try:
        row = await (await db.execute(
            "SELECT COUNT(*) AS n FROM memory_observations WHERE session_id=?",
            (session_id,),
        )).fetchone()
    finally:
        await db.close()
    assert int(row["n"]) == 8, "exactly 8 observations materialized (rejected)"


# ---------------------------------------------------------------------------
# S4-ARCHIVE Gate 2 rework: persisted retry-eligibility authority
# ---------------------------------------------------------------------------


async def _clear_retry_backoff(job_id: str) -> None:
    """Test orchestration: make a backoff-delayed pending job claimable now."""
    db = await get_db("steins_gate", "memory")
    try:
        await db.execute(
            "UPDATE memory_ingest_jobs SET next_attempt_at=NULL WHERE job_id=?",
            (job_id,),
        )
        await db.commit()
    finally:
        await db.close()


async def _job_row(job_id: str) -> Any:
    db = await get_db("steins_gate", "memory")
    try:
        row = await (await db.execute(
            "SELECT * FROM memory_ingest_jobs WHERE job_id=?", (job_id,)
        )).fetchone()
        assert row is not None
        return dict(row)
    finally:
        await db.close()


async def _enqueue_one(jobs: Any) -> str:
    job = await jobs.enqueue_memory_job(
        session_id="s-jobs",
        worldline="steins_gate",
        conversation_id="c-1",
        identity_mode="self",
        source_message_id=int(uuid4().int % 10**9),
        pipeline_version="memory-v11-1",
        provider_id="deepseek",
        model_id="deepseek-chat",
    )
    return job.job_id if hasattr(job, "job_id") else job["job_id"]


@pytest.mark.asyncio
async def test_s4_permanent_failure_persists_retryable_false(isolated_store):
    """Permanent failure is terminal: retryable=false is persisted, and the
    retry seam refuses to move it to pending."""
    jobs = _jobs()
    job_id = await _enqueue_one(jobs)
    claimed = await jobs.claim_memory_job(job_id=job_id, lease_owner="worker-a")
    assert claimed is not None
    await jobs.mark_memory_job_failed(
        job_id=job_id, error_code="validation_failed", permanent=True,
    )
    row = await _job_row(job_id)
    assert row["state"] == "failed"
    assert int(row["retryable"] if row["retryable"] is not None else 1) == 0, "permanent failure must persist retryable=false"
    # The retry seam re-validates against the persisted authority.
    result = await jobs.retry_memory_job(
        job_id=job_id, session_id="s-jobs", worldline="steins_gate",
    )
    result_state = result["state"] if isinstance(result, dict) else result.state
    assert result_state == "failed", "non-retryable job must not move to pending"
    row = await _job_row(job_id)
    assert row["state"] == "failed"


@pytest.mark.asyncio
async def test_s4_transient_exhaustion_persists_retryable_true(isolated_store):
    """Auto-retry budget exhausted (transient): still retryable=true."""
    jobs = _jobs()
    job_id = await _enqueue_one(jobs)
    for _ in range(3):
        claimed = await jobs.claim_memory_job(job_id=job_id, lease_owner="worker-a")
        assert claimed is not None
        await jobs.mark_memory_job_failed(
            job_id=job_id, error_code="provider_unavailable", permanent=False,
        )
        await _clear_retry_backoff(job_id)
    row = await _job_row(job_id)
    assert row["state"] == "failed"
    assert int(row["retryable"] if row["retryable"] is not None else 1) == 1, "exhausted transient failure stays retryable"
    # The retry seam accepts it: failed → pending.
    result = await jobs.retry_memory_job(
        job_id=job_id, session_id="s-jobs", worldline="steins_gate",
    )
    result_state = result["state"] if isinstance(result, dict) else result.state
    assert result_state == "pending"
    row = await _job_row(job_id)
    assert row["state"] == "pending"


@pytest.mark.asyncio
async def test_s4_ingest_jobs_retryable_column_default_true(isolated_store):
    """Compat semantics: rows predating the column (provenance never persisted)
    migrate to retryable=1. New inserts default to 1 as well."""
    db = await get_db("steins_gate", "memory")
    try:
        columns = {
            str(row[1]): row
            for row in await (await db.execute(
                "PRAGMA table_info(memory_ingest_jobs)"
            )).fetchall()
        }
        assert "retryable" in columns, "memory_ingest_jobs.retryable must exist"
        assert int(columns["retryable"][4] or 0) == 1, "default must be 1"
    finally:
        await db.close()
