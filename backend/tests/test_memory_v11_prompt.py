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
        # Display text carries the query term so relevance-aware retrieval
        # can select the same-scope card (S3b does not bulk-inject zeros).
        display_text=f"{self_marker} 喜欢动漫",
        semantic_json={"subject": "user", "relation": "likes", "object": "anime"},
    )
    await create_stable_fact(
        session_id=session_id,
        worldline="steins_gate",
        identity_mode="okabe",
        display_text=f"{okabe_marker} 喜欢水果",
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


# ---------------------------------------------------------------------------
# MEMORY-V11-S3-GAP-PROBE R1: bounded, relevance-aware stable-fact budget.
# Red until S3 replaces bulk-pull + char-slice with a bounded selection.
# Behavioral only: assert on the final compiled prompt, never on internals.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_r1_irrelevant_facts_must_not_flood_prompt(isolated_store, monkeypatch):
    """P02/P03: obviously irrelevant facts must not bulk-enter the prompt."""
    monkeypatch.setenv("AMADEUS_MEMORY_MODE", "v11")
    session_id = f"r1-noise-{uuid4()}"
    markers = []
    for i in range(10):
        marker = f"IRR_{i:02d}"
        markers.append(marker)
        await create_stable_fact(
            session_id=session_id,
            worldline="steins_gate",
            identity_mode="self",
            display_text=f"{marker} 数学の本を読んでいる",
            semantic_json={"subject": "user", "relation": "study", "object": f"math_{i}"},
        )

    prompt = await compile_for_session(_session_obj(session_id), "zqx_nebula_9")
    for marker in markers:
        assert marker not in prompt, (
            f"irrelevant fact leaked into prompt ({marker}); no relevance "
            "threshold/budget is applied, everything under the count cap is injected"
        )


@pytest.mark.asyncio
async def test_r1_budget_selects_whole_facts_not_char_slices(
    isolated_store, monkeypatch
):
    """P03: the final injection is a bounded selection, not a char slice.

    The total display text far exceeds the core token budget, so a selection
    budget must drop whole facts; a char slice leaves truncated fragments in
    the prompt. Red while PromptCompiler._fit truncates mid-fact.
    """
    monkeypatch.setenv("AMADEUS_MEMORY_MODE", "v11")
    session_id = f"r1-budget-{uuid4()}"
    texts = []
    for i in range(30):
        text = f"MARK_{i:02d} " + "好" * 300
        texts.append(text)
        await create_stable_fact(
            session_id=session_id,
            worldline="steins_gate",
            identity_mode="self",
            display_text=text,
            semantic_json={"subject": "user", "relation": "marker", "object": f"m{i}"},
        )

    prompt = await compile_for_session(_session_obj(session_id), "你好")
    injected = [t for t in texts if t in prompt]
    assert len(injected) <= 24, "injection must be bounded by an explicit count budget"
    for text in texts:
        marker = text.split()[0]
        assert marker not in prompt or text in prompt, (
            f"char-sliced fact fragment in prompt: {marker!r} present but the "
            "fact body is truncated; budget must select whole facts, not slice text"
        )


# ---------------------------------------------------------------------------
# MEMORY-V11-S3-GAP-PROBE R2: final-injection dedupe by semantic fingerprint.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_r2_semantic_fingerprint_dedupe_before_injection(
    isolated_store, monkeypatch
):
    """P03: equal active semantic fingerprints must not double-occupy budget.

    Two fact_ids that canonicalize to the same fingerprint (the motivating
    likes_anime / like_anime drift) must inject at most one card. Red while
    the prompt surface injects every active row without fingerprint dedupe.
    """
    monkeypatch.setenv("AMADEUS_MEMORY_MODE", "v11")
    session_id = f"r2-fp-{uuid4()}"
    marker_a = f"KONO_A_{uuid4().hex[:8]}"
    marker_b = f"KONO_B_{uuid4().hex[:8]}"
    shared_semantic = {"subject": "user", "relation": "likes_anime", "object": "konosuba"}
    await create_stable_fact(
        session_id=session_id,
        worldline="steins_gate",
        identity_mode="self",
        display_text=f"{marker_a} 好きなアニメはこのすば",
        semantic_json=shared_semantic,
    )
    await create_stable_fact(
        session_id=session_id,
        worldline="steins_gate",
        identity_mode="self",
        display_text=f"{marker_b} このすばが好き",
        semantic_json=shared_semantic,
    )

    prompt = await compile_for_session(_session_obj(session_id), "このすば")
    assert not (marker_a in prompt and marker_b in prompt), (
        "same semantic fingerprint injected twice; final injection surface "
        "must dedupe by fingerprint before consuming budget"
    )
