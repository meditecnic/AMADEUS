"""Byte-for-byte prompt snapshots for legacy / shadow memory modes.

Dispatch 55 (v11 resident profile) must leave legacy and shadow prompts
unchanged. These snapshots were generated from the pre-change compiler
(main 578d5129b60fd7953e31f136ab0e3f7cd52f8274) and are compared verbatim.

Regenerate only when a change is *meant* to alter legacy/shadow prompts:
    AMADEUS_REGEN_PROMPT_SNAPSHOTS=1 python -m pytest -q tests/test_prompt_snapshot_legacy_shadow.py
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.db import init_db, reset_initialization_cache
from app.services.memory import RetrievedMemory
from app.services.prompt_compiler import PromptCompiler, PromptInputs, compile_for_session
from app.services.soul_engine import EmotionVector

SNAPSHOT_PATH = Path(__file__).parent / "fixtures" / "prompt_snapshots_legacy_shadow.json"
REGEN = os.environ.get("AMADEUS_REGEN_PROMPT_SNAPSHOTS") == "1"

EMOTION = EmotionVector(
    trust=0.55,
    attachment=0.35,
    irritation=0.2,
    anxiety=0.1,
    jealousy=0.05,
    volatility=0.3,
)


def _facts(count: int, *, body: str = "", prefix: str = "FACT") -> list[RetrievedMemory]:
    return [
        RetrievedMemory(
            id=index + 1,
            kind="core",
            content=f"{prefix}_{index:02d} ユーザーは紅茶が好き{body}",
            score=1.0 - index / 100,
            confidence=0.9,
            importance=0.5,
            pinned=index == 0,
        )
        for index in range(count)
    ]


def _episodes(count: int, *, body: str = "", prefix: str = "EXP") -> list[RetrievedMemory]:
    return [
        RetrievedMemory(
            id=100 + index,
            kind="episodic",
            content=f"{prefix}_{index:02d} 先週秋葉原で本を買った{body}",
            score=0.8 - index / 100,
            confidence=0.9,
            importance=0.5,
            pinned=False,
        )
        for index in range(count)
    ]


def _history(turns: int, *, body: str = "") -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for index in range(turns):
        rows.append({"role": "user", "content": f"U{index:03d} 今日の実験はどうだった？{body}"})
        rows.append({"role": "assistant", "content": f"A{index:03d} まあまあね。{body}"})
    return rows


# Session-level cases: compile_for_session with stubbed memory reads. The
# over-budget message lengths are tuned so each case stops at a different
# shrink stage of the 12000-token total budget.
_OVER = dict(
    self_name="",
    facts=_facts(8, body="。" * 120),
    episodes=_episodes(10, body="、" * 150),
    summary="長い会話の要約" * 40,
    history=_history(40, body="ね" * 120),
)

SESSION_CASES = {
    "okabe_with_facts": dict(
        identity_mode="okabe",
        self_name="",
        identity_acknowledged=False,
        facts=_facts(3),
        episodes=_episodes(2),
        summary="比較中、候補Bの確認待ち",
        history=_history(2),
        message="紅茶の話を覚えてる？",
    ),
    "self_no_facts": dict(
        identity_mode="self",
        self_name="マユリ",
        identity_acknowledged=True,
        facts=[],
        episodes=[],
        summary="",
        history=[],
        message="こんにちは",
    ),
    # history sliced by 1/5 steps; all memory kept
    "self_over_budget_history_slice": dict(
        _OVER, identity_mode="self", identity_acknowledged=False,
        message="長い質問" + "あ" * 24000,
    ),
    # history gone; tail experiences dropped whole
    "self_over_budget_episodic_drop": dict(
        _OVER, identity_mode="self", identity_acknowledged=False,
        message="長い質問" + "あ" * 28000,
    ),
    # experiences gone; tail core facts dropped whole
    "self_over_budget_core_drop": dict(
        _OVER, identity_mode="self", identity_acknowledged=False,
        message="長い質問" + "あ" * 30000,
    ),
    # everything droppable gone; state / persona char-sliced
    "okabe_over_budget_extreme": dict(
        _OVER, identity_mode="okabe", identity_acknowledged=True,
        message="とても長い質問" + "い" * 36000,
    ),
}

# Compiler-level cases: PromptCompiler.compile with shrinking total budgets so
# every stage (history/web slice, episodic drop, core drop, clearing,
# state/persona slice) is pinned.
_COMPILER_BASE = dict(
    identity_mode="okabe",
    facts=_facts(5, body="。" * 60),
    episodes=_episodes(5, body="、" * 60),
    web=[f"WEB_{i} " + "ウェブ" * 80 for i in range(4)],
    summary="要約" * 20,
    history=_history(12, body="ね" * 40),
    message="ウェブも見て",
)

COMPILER_CASES = {
    f"compile_budget_{budget}": dict(_COMPILER_BASE, input_budget=budget)
    for budget in (2500, 2100, 1800, 1500, 1200, 700)
}
COMPILER_CASES["compile_no_facts_no_episodes"] = dict(
    input_budget=12000,
    identity_mode="self",
    facts=[],
    episodes=[],
    web=[],
    summary="",
    history=[],
    message="やあ",
)


@pytest.fixture
async def isolated_store(tmp_path, monkeypatch):
    monkeypatch.delenv("AMADEUS_DB_PATH", raising=False)
    monkeypatch.setenv("AMADEUS_DATA_DIR", str(tmp_path))
    reset_initialization_cache()
    await init_db()
    return tmp_path


def _load_snapshots() -> dict[str, str]:
    if not SNAPSHOT_PATH.is_file():
        return {}
    return json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))


def _check(name: str, prompt: str, message: str) -> None:
    # The current user message is appended verbatim and never shrunk; store
    # the prompt without it so the fixture stays small, and pin it separately.
    assert prompt.endswith(message), f"{name}: current message not intact at prompt end"
    prompt = prompt[: len(prompt) - len(message)]
    if REGEN:
        data = _load_snapshots()
        data[name] = prompt
        SNAPSHOT_PATH.write_text(
            json.dumps(data, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        return
    snapshots = _load_snapshots()
    assert name in snapshots, f"missing snapshot {name}"
    expected = snapshots[name]
    if prompt != expected:
        # Locate the first divergence for a readable failure.
        index = next(
            (i for i, (a, b) in enumerate(zip(prompt, expected)) if a != b),
            min(len(prompt), len(expected)),
        )
        raise AssertionError(
            f"{name}: prompt differs from snapshot at char {index} "
            f"(len {len(prompt)} vs {len(expected)}): "
            f"got {prompt[max(0, index - 80):index + 80]!r} "
            f"expected {expected[max(0, index - 80):index + 80]!r}"
        )


def _install_memory_stubs(monkeypatch, case: dict) -> dict[str, int]:
    from app.services.memory import memory_service
    from app.services.soul_engine import soul_engine

    calls = {"select_core_facts": 0, "lexical_search": 0}

    async def fake_core(session_id, worldline, query, identity_mode="okabe", **kwargs):
        calls["select_core_facts"] += 1
        return list(case["facts"])

    async def fake_lexical(session_id, worldline, query, identity_mode="okabe", **kwargs):
        calls["lexical_search"] += 1
        return list(case["episodes"])

    async def fake_emotion(*args, **kwargs):
        return EMOTION

    monkeypatch.setattr(memory_service, "select_core_facts", fake_core)
    monkeypatch.setattr(memory_service, "lexical_search", fake_lexical)
    monkeypatch.setattr(soul_engine, "restore_emotion", fake_emotion)
    return calls


def _session(case: dict) -> SimpleNamespace:
    return SimpleNamespace(
        session_id="snapshot-session",
        worldline="steins_gate",
        identity_mode=case["identity_mode"],
        base_system_prompt=None,
        system_prompt=None,
        memory_summary=case["summary"],
        history=list(case["history"]),
        self_name=case["self_name"],
        identity_acknowledged=case["identity_acknowledged"],
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("memory_mode", ["legacy", "shadow"])
@pytest.mark.parametrize("case_name", sorted(SESSION_CASES))
async def test_session_prompt_snapshot(isolated_store, monkeypatch, memory_mode, case_name):
    case = SESSION_CASES[case_name]
    monkeypatch.setenv("AMADEUS_MEMORY_MODE", memory_mode)
    calls = _install_memory_stubs(monkeypatch, case)
    prompt = await compile_for_session(_session(case), case["message"])
    # Legacy and shadow both still read through memory_service.select_core_facts.
    assert calls == {"select_core_facts": 1, "lexical_search": 1}
    # Both modes share one snapshot: shadow must never diverge from legacy.
    _check(f"session::{case_name}", prompt, case["message"])


@pytest.mark.parametrize("case_name", sorted(COMPILER_CASES))
def test_compiler_prompt_snapshot(case_name):
    case = COMPILER_CASES[case_name]
    compiler = PromptCompiler(input_budget=case["input_budget"])
    prompt = compiler.compile(
        PromptInputs(
            worldline="steins_gate",
            base_identity="You are Amadeus Kurisu.",
            emotion=EMOTION,
            core_facts=list(case["facts"]),
            episodic=list(case["episodes"]),
            web_evidence=list(case["web"]),
            working_summary=case["summary"],
            recent_history=list(case["history"]),
            current_user_message=case["message"],
            identity_mode=case["identity_mode"],
        )
    )
    _check(f"compiler::{case_name}", prompt, case["message"])


def test_snapshot_file_has_no_stale_entries():
    if REGEN:
        pytest.skip("regenerating")
    expected = {f"session::{name}" for name in SESSION_CASES} | {
        f"compiler::{name}" for name in COMPILER_CASES
    }
    assert set(_load_snapshots()) == expected
