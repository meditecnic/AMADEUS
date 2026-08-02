"""MEMORY-V11 S3a: mode-gated prompt retrieval (P01 / P02)."""
from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.db import init_db, reset_initialization_cache
from app.services.memory_v11.repository import create_stable_fact
from app.services.prompt_compiler import compile_for_session


@pytest.fixture
async def isolated_store(tmp_path, monkeypatch):
    monkeypatch.delenv("AMADEUS_DB_PATH", raising=False)
    monkeypatch.setenv("AMADEUS_DATA_DIR", str(tmp_path))
    reset_initialization_cache()
    await init_db()
    return tmp_path


def _session_obj(session_id: str, *, identity_mode: str = "self") -> SimpleNamespace:
    return SimpleNamespace(
        session_id=session_id,
        worldline="steins_gate",
        identity_mode=identity_mode,
        base_system_prompt="You are Amadeus Kurisu.",
        system_prompt="You are Amadeus Kurisu.",
        memory_summary="",
        history=[],
        self_name="",
        identity_acknowledged=False,
    )


@pytest.mark.asyncio
async def test_s3a_shadow_mode_does_not_inject_v11_facts(isolated_store, monkeypatch):
    """P01: shadow writes stay off the prompt; retrieval remains legacy."""
    monkeypatch.setenv("AMADEUS_MEMORY_MODE", "shadow")
    session_id = f"prompt-shadow-{uuid4()}"
    secret_marker = f"V11_ONLY_SHADOW_{uuid4().hex[:8]}"
    await create_stable_fact(
        session_id=session_id,
        worldline="steins_gate",
        identity_mode="self",
        display_text=secret_marker,
        semantic_json={"subject": "user", "relation": "marker", "object": "shadow"},
    )
    prompt = await compile_for_session(_session_obj(session_id), "你好")
    assert secret_marker not in prompt


@pytest.mark.asyncio
async def test_s3a_legacy_mode_does_not_inject_v11_facts(isolated_store, monkeypatch):
    monkeypatch.setenv("AMADEUS_MEMORY_MODE", "legacy")
    session_id = f"prompt-legacy-{uuid4()}"
    marker = f"V11_ONLY_LEGACY_{uuid4().hex[:8]}"
    await create_stable_fact(
        session_id=session_id,
        worldline="steins_gate",
        identity_mode="self",
        display_text=marker,
        semantic_json={"subject": "user", "relation": "marker", "object": "legacy"},
    )
    prompt = await compile_for_session(_session_obj(session_id), "你好")
    assert marker not in prompt


@pytest.mark.asyncio
async def test_s3a_v11_mode_injects_same_scope_active_facts_only(
    isolated_store, monkeypatch
):
    """P02: v11 mode injects only active same-scope stable facts."""
    monkeypatch.setenv("AMADEUS_MEMORY_MODE", "v11")
    session_id = f"prompt-v11-{uuid4()}"
    self_marker = f"SELF_ANIME_{uuid4().hex[:8]}"
    okabe_marker = f"OKABE_FRUIT_{uuid4().hex[:8]}"
    await create_stable_fact(
        session_id=session_id,
        worldline="steins_gate",
        identity_mode="self",
        display_text=self_marker,
        semantic_json={"subject": "user", "relation": "likes", "object": "anime"},
    )
    await create_stable_fact(
        session_id=session_id,
        worldline="steins_gate",
        identity_mode="okabe",
        display_text=okabe_marker,
        semantic_json={"subject": "user", "relation": "likes", "object": "fruit"},
    )

    self_prompt = await compile_for_session(
        _session_obj(session_id, identity_mode="self"),
        "动漫",
    )
    assert self_marker in self_prompt
    assert okabe_marker not in self_prompt
    assert "FACT_USE_CONTRACT" in self_prompt

    okabe_prompt = await compile_for_session(
        _session_obj(session_id, identity_mode="okabe"),
        "水果",
    )
    assert okabe_marker in okabe_prompt
    assert self_marker not in okabe_prompt
