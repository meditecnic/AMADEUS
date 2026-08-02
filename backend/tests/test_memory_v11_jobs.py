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
