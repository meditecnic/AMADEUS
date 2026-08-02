"""MEMORY-V11 S0 red contracts: durable jobs (D22–D25, B03–B07).

Fixed import surface only:
  app.services.memory_v11.jobs
  app.services.memory_v11.contracts
No fallback to legacy app.services.memory.
"""
from __future__ import annotations

import importlib
from typing import Any

import pytest

from app import models
from app.db import init_db, reset_initialization_cache
from app.services.conversations import conversation_service


def _jobs():
    return importlib.import_module("app.services.memory_v11.jobs")


def _contracts():
    return importlib.import_module("app.services.memory_v11.contracts")


@pytest.fixture
async def isolated_store(tmp_path, monkeypatch):
    monkeypatch.delenv("AMADEUS_DB_PATH", raising=False)
    monkeypatch.setenv("AMADEUS_DATA_DIR", str(tmp_path))
    reset_initialization_cache()
    await init_db()
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
