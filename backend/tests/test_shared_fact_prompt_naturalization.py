"""GATE7A-NAT-01: CORE FACTS usage contract (FACT_USE_CONTRACT).

L3 expression: FACT_USE_CONTRACT instructs natural familiarity, forbids
deny-then-guess and any worldline/DB/MEMORY mechanism talk.

These probes feed a resident profile straight into PromptCompiler. The profile
built from stable facts (partial-profile rule, per-turn supplement) is covered
by test_memory_v11_resident_profile and test_memory_v11_prompt.
"""
from __future__ import annotations

import pytest

from app.db import init_db, reset_initialization_cache
from app.services.prompt_compiler import (
    PromptCompiler,
    PromptInputs,
    ResidentProfile,
    compile_for_session,
)
from app.services.soul_engine import EmotionVector


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


def _compile_with_facts(facts: dict[str, str], message: str) -> str:
    return PromptCompiler().compile(PromptInputs(
        worldline="beta",
        base_identity="You are Amadeus Kurisu.",
        emotion=EmotionVector(
            trust=0.5, attachment=0.5, irritation=0.1,
            anxiety=0.1, jealousy=0.1, volatility=0.2,
        ),
        episodic=[],
        web_evidence=[],
        working_summary="",
        recent_history=[],
        current_user_message=message,
        identity_mode="self",
        resident_profile=ResidentProfile(
            facts=[
                {"fact_id": f"nat-{index}", "display_text": f"{key}: {value}"}
                for index, (key, value) in enumerate(facts.items())
            ],
            candidate_count=len(facts),
        ),
    ))


# ---------------------------------------------------------------------------
# Task 3: CORE FACTS usage contract (L3 expression layer)
# ---------------------------------------------------------------------------

def test_core_facts_section_has_naturalization_contract():
    prompt = _compile_with_facts({"favorite_food": "炸鸡"}, "我喜欢吃什么？")
    assert "炸鸡" in prompt
    assert "CORE FACTS" in prompt
    # Stable ASCII anchor — tests never depend on brittle JP/EN copy.
    assert "FACT_USE_CONTRACT" in prompt
    # The contract must precede the fact list (kept under budget truncation).
    assert prompt.index("FACT_USE_CONTRACT") < prompt.index("炸鸡")


def test_contract_instructs_selective_answer_not_full_recital():
    """Live-feedback fix: asked one preference, Amadeus must answer that one and
    not recite every shared fact at once. All facts still reach the prompt (so
    any single one can be answered) — the contract governs selectivity."""
    prompt = _compile_with_facts(
        {"favorite_food": "炸鸡", "favorite_drink": "雪碧", "hobby": "実況サッカー"},
        "我喜欢吃什么？",
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
