"""GATE7A-NAT-01: shared facts must reach the self prompt as usable CORE FACTS.

Layered probes (plan 2026-07-26-gate7a-shared-fact-naturalization.md):
- L1 retrieval: select_core_facts(β, self, CJK query) contains the shared fact
- L2 compile: compile_for_session output contains the fact text
- L3 expression: FACT_USE_CONTRACT instructs natural familiarity, forbids
  deny-then-guess and any worldline/DB/MEMORY mechanism talk
- L4 isolation: okabe stays blind on both worldlines
"""
from __future__ import annotations

import pytest

from app.db import init_db, reset_initialization_cache
from app import models
from app.services.conversations import conversation_service
from app.services.memory import CoreFactCandidate, memory_service
from app.services.prompt_compiler import compile_for_session


@pytest.fixture
async def isolated_store(tmp_path, monkeypatch):
    monkeypatch.delenv("AMADEUS_DB_PATH", raising=False)
    monkeypatch.setenv("AMADEUS_DATA_DIR", str(tmp_path))
    reset_initialization_cache()
    await init_db()
    return tmp_path


class _FakeSession:
    def __init__(self, session_id: str, worldline: str, identity_mode: str):
        self.session_id = session_id
        self.worldline = worldline
        self.identity_mode = identity_mode
        self.base_system_prompt = ""
        self.system_prompt = ""
        self.memory_summary = ""
        self.history: list[dict[str, str]] = []
        self.self_name = ""
        self.identity_acknowledged = False


async def _seed_and_promote(
    owner: str,
    worldline: str,
    *,
    key: str = "favorite_food",
    value: str = "炸鸡",
) -> int:
    """Real self conversation + user message + local fact + promote (Gate 7A)."""
    conversation = await conversation_service.create(
        owner, worldline, identity_mode="self"
    )
    conversation_id = str(conversation["id"])
    message_id = await models.save_message(
        owner, "user", f"私は{value}が好き。", worldline=worldline,
        conversation_id=conversation_id,
    )
    fact_id = await memory_service.upsert_core_fact(
        owner,
        worldline,
        CoreFactCandidate(
            fact_key=key,
            fact_value=value,
            confidence=0.9,
            importance=0.8,
            source_message_ids=[message_id],
        ),
        identity_mode="self",
    )
    shared_id, _ = await memory_service.promote_local_self_fact(
        owner, worldline, fact_id
    )
    return shared_id


# ---------------------------------------------------------------------------
# Task 1 probes: L1 retrieval / L2 compile / L4 isolation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_promoted_fact_in_beta_select_core_facts(isolated_store):
    owner = "nat-food-1"
    await _seed_and_promote(owner, "steins_gate")
    # CJK user question — matches the live acceptance phrasing (no spaces).
    rows = await memory_service.select_core_facts(
        owner, "beta", "我喜欢吃什么", identity_mode="self"
    )
    blob = "\n".join(item.content for item in rows)
    assert "炸鸡" in blob


@pytest.mark.asyncio
async def test_promoted_fact_in_beta_compiled_prompt(isolated_store):
    owner = "nat-food-2"
    await _seed_and_promote(owner, "steins_gate")
    prompt = await compile_for_session(
        _FakeSession(owner, "beta", "self"), "我喜欢吃什么？"
    )
    assert "炸鸡" in prompt
    assert "CORE FACTS" in prompt
    # Implementation trivia must never become speech fuel for the model.
    assert "shared_user_facts" not in prompt
    assert "origin_worldline" not in prompt


@pytest.mark.asyncio
async def test_okabe_beta_still_blind_to_shared(isolated_store):
    owner = "nat-food-3"
    await _seed_and_promote(owner, "steins_gate")
    rows = await memory_service.select_core_facts(
        owner, "beta", "我喜欢吃什么", identity_mode="okabe"
    )
    assert all("炸鸡" not in item.content for item in rows)
    prompt = await compile_for_session(
        _FakeSession(owner, "beta", "okabe"), "我喜欢吃什么？"
    )
    assert "炸鸡" not in prompt


# ---------------------------------------------------------------------------
# Task 2: retrieval hardening — CJK query with zero lexical overlap must not
# let unrelated high-importance local noise evict the shared profile.
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cjk_query_keeps_promoted_food_among_many_facts(isolated_store):
    owner = "nat-food-noise"
    await _seed_and_promote(owner, "steins_gate")
    # 30 unrelated high-importance local self facts on β. upsert_core_fact
    # does not verify source provenance, so synthetic ids are legal here
    # (only shared writes / promotion verify evidence).
    for i in range(30):
        await memory_service.upsert_core_fact(
            owner, "beta",
            CoreFactCandidate(
                fact_key=f"noise_{i}",
                fact_value=f"noise-value-{i}",
                confidence=0.99,
                importance=0.99,
                source_message_ids=[1000 + i],
                is_pinned=False,
            ),
            identity_mode="self",
        )
    rows = await memory_service.select_core_facts(
        owner, "beta", "我喜欢吃什么", identity_mode="self", limit=24
    )
    assert any("炸鸡" in item.content for item in rows)


# ---------------------------------------------------------------------------
# Task 3: CORE FACTS usage contract (L3 expression layer)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_core_facts_section_has_naturalization_contract(isolated_store):
    owner = "nat-contract"
    await _seed_and_promote(owner, "steins_gate")
    prompt = await compile_for_session(
        _FakeSession(owner, "beta", "self"), "我喜欢吃什么？"
    )
    assert "炸鸡" in prompt
    assert "CORE FACTS" in prompt
    # Stable ASCII anchor — tests never depend on brittle JP/EN copy.
    assert "FACT_USE_CONTRACT" in prompt
    # The contract must precede the fact list (kept under budget truncation).
    assert prompt.index("FACT_USE_CONTRACT") < prompt.index("炸鸡")


@pytest.mark.asyncio
async def test_contract_instructs_selective_answer_not_full_recital(isolated_store):
    """Live-feedback fix: asked one preference, Amadeus must answer that one and
    not recite every shared fact at once. All facts still reach the prompt (so
    any single one can be answered) — the contract governs selectivity."""
    owner = "nat-selective"
    await _seed_and_promote(owner, "steins_gate", key="favorite_food", value="炸鸡")
    await _seed_and_promote(owner, "steins_gate", key="favorite_drink", value="雪碧")
    await _seed_and_promote(owner, "steins_gate", key="hobby", value="実況サッカー")
    prompt = await compile_for_session(
        _FakeSession(owner, "beta", "self"), "我喜欢吃什么？"
    )
    # Every fact is still available in context (needed to answer any single one).
    for value in ("炸鸡", "雪碧", "実況サッカー"):
        assert value in prompt
    # The contract governs MANNER, not a rigid count. It must kill the
    # pathological behavior (padding an unasked fact via a filler transition
    # to show off memory) while NOT hard-forbidding a natural mention that the
    # user's own words lead to. The old permissive "naturally relevant" licence
    # that caused food→drink volunteering must be gone.
    lowered = prompt.lower()
    assert "genuinely knows the user" in lowered
    assert "do not pad" in lowered
    assert "let it stay unsaid" in lowered
    assert "naturally relevant" not in lowered


@pytest.mark.asyncio
async def test_empty_core_facts_do_not_carry_contract(isolated_store):
    owner = "nat-empty"
    prompt = await compile_for_session(
        _FakeSession(owner, "beta", "self"), "こんにちは"
    )
    # No facts → no contract (budget is not wasted on an unused instruction).
    assert "FACT_USE_CONTRACT" not in prompt


@pytest.mark.asyncio
async def test_contract_and_facts_survive_core_budget_fit(isolated_store):
    """Review ISSUE-2/3: contract head + facts must both survive _fit; the
    core section keeps its head (contract) and trims tail facts, never the
    other way around."""
    from app.services.memory import RetrievedMemory
    from app.services.prompt_compiler import PromptCompiler, PromptInputs
    from app.services.soul_engine import EmotionVector

    facts = [
        RetrievedMemory(
            id=index + 1,
            kind="core",
            content=f"fact_{index}: value-{index}-" + "x" * 120,
            score=1.0 - index * 0.01,
        )
        for index in range(24)
    ]
    compiled = PromptCompiler().compile(PromptInputs(
        worldline="beta",
        base_identity="You are Amadeus Kurisu.",
        emotion=EmotionVector(
            trust=0.5, attachment=0.5, irritation=0.1,
            anxiety=0.1, jealousy=0.1, volatility=0.2,
        ),
        core_facts=facts,
        episodic=[],
        web_evidence=[],
        working_summary="",
        recent_history=[],
        current_user_message="事実について",
        identity_mode="self",
    ))
    # The contract anchor must survive even when the fact list overflows the
    # core budget; the highest-scored facts stay, tail facts get trimmed.
    assert "FACT_USE_CONTRACT" in compiled
    assert "fact_0:" in compiled


# ---------------------------------------------------------------------------
# Task 5: worldline × identity matrix and multi-fact coexistence
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("worldline", "identity_mode", "visible"),
    [
        ("beta", "self", True),
        ("steins_gate", "self", True),
        ("beta", "okabe", False),
        ("steins_gate", "okabe", False),
    ],
)
async def test_shared_fact_prompt_visibility_matrix(
    isolated_store, worldline, identity_mode, visible
):
    owner = f"nat-matrix-{worldline}-{identity_mode}"
    await _seed_and_promote(owner, "steins_gate")
    prompt = await compile_for_session(
        _FakeSession(owner, worldline, identity_mode), "我喜欢吃什么？"
    )
    assert ("炸鸡" in prompt) is visible


@pytest.mark.asyncio
async def test_second_promoted_fact_does_not_evict_the_first(isolated_store):
    owner = "nat-two-facts"
    await _seed_and_promote(owner, "steins_gate", key="favorite_food", value="炸鸡")
    await _seed_and_promote(owner, "steins_gate", key="favorite_drink", value="雪碧")
    prompt = await compile_for_session(
        _FakeSession(owner, "beta", "self"), "我喜欢吃什么、喝什么？"
    )
    assert "炸鸡" in prompt
    assert "雪碧" in prompt
