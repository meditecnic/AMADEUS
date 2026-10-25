"""Byte-for-byte v11 prompt snapshots for scopes with no active stable facts.

Generated from the pre-change compiler (main 578d5129) before compile()
switched from positional section indexes to section names. A v11 scope with
no active stable facts must keep producing exactly this prompt: no CORE FACTS
section, no "(none)" placeholder, no supplement block.

Regenerate only when a change is *meant* to alter these prompts:
    AMADEUS_REGEN_PROMPT_SNAPSHOTS=1 python -m pytest -q tests/test_prompt_snapshot_v11_no_facts.py
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.db import init_db, reset_initialization_cache
from app.services.prompt_compiler import compile_for_session
from app.services.soul_engine import EmotionVector

SNAPSHOT_PATH = Path(__file__).parent / "fixtures" / "prompt_snapshots_v11_no_facts.json"
REGEN = os.environ.get("AMADEUS_REGEN_PROMPT_SNAPSHOTS") == "1"

EMOTION = EmotionVector(
    trust=0.55,
    attachment=0.35,
    irritation=0.2,
    anxiety=0.1,
    jealousy=0.05,
    volatility=0.3,
)


def _experiences(count: int, *, body: str = "") -> list[dict]:
    return [
        {
            "experience_id": f"00000000-0000-4000-8000-{index:012d}",
            "display_text": f"EXP_{index:02d} 先週秋葉原で本を買った{body}",
            "relevance": 0.8 - index / 100,
            "confidence": 0.9,
        }
        for index in range(count)
    ]


def _history(turns: int, *, body: str = "") -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for index in range(turns):
        rows.append({"role": "user", "content": f"U{index:03d} 今日の実験はどうだった？{body}"})
        rows.append({"role": "assistant", "content": f"A{index:03d} まあまあね。{body}"})
    return rows


_OVER = dict(
    self_name="",
    experiences=_experiences(10, body="、" * 150),
    summary="長い会話の要約" * 40,
    history=_history(40, body="ね" * 120),
)

CASES = {
    "okabe_with_experiences": dict(
        identity_mode="okabe",
        self_name="",
        identity_acknowledged=False,
        experiences=_experiences(2),
        summary="比較中、候補Bの確認待ち",
        history=_history(2),
        message="秋葉原の話を覚えてる？",
    ),
    "self_empty": dict(
        identity_mode="self",
        self_name="マユリ",
        identity_acknowledged=True,
        experiences=[],
        summary="",
        history=[],
        message="こんにちは",
    ),
    "self_over_budget_episodic_drop": dict(
        _OVER, identity_mode="self", identity_acknowledged=False,
        message="長い質問" + "あ" * 30000,
    ),
    "okabe_over_budget_extreme": dict(
        _OVER, identity_mode="okabe", identity_acknowledged=True,
        message="とても長い質問" + "い" * 36000,
    ),
}


@pytest.fixture
async def isolated_store(tmp_path, monkeypatch):
    monkeypatch.delenv("AMADEUS_DB_PATH", raising=False)
    monkeypatch.setenv("AMADEUS_DATA_DIR", str(tmp_path))
    reset_initialization_cache()
    await init_db()
    return tmp_path


def _load() -> dict[str, str]:
    if not SNAPSHOT_PATH.is_file():
        return {}
    return json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))


def _check(name: str, prompt: str, message: str) -> None:
    assert prompt.endswith(message), f"{name}: current message not intact at prompt end"
    prompt = prompt[: len(prompt) - len(message)]
    if REGEN:
        data = _load()
        data[name] = prompt
        SNAPSHOT_PATH.write_text(
            json.dumps(data, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        return
    snapshots = _load()
    assert name in snapshots, f"missing snapshot {name}"
    expected = snapshots[name]
    if prompt != expected:
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


@pytest.mark.asyncio
@pytest.mark.parametrize("case_name", sorted(CASES))
async def test_v11_no_fact_prompt_snapshot(isolated_store, monkeypatch, case_name):
    import app.services.memory_v11.retrieval as retrieval_module
    from app.services.memory import memory_service
    from app.services.soul_engine import soul_engine

    case = CASES[case_name]
    monkeypatch.setenv("AMADEUS_MEMORY_MODE", "v11")

    async def fake_experiences(**kwargs):
        return [dict(row) for row in case["experiences"]]

    async def fake_emotion(*args, **kwargs):
        return EMOTION

    async def legacy_forbidden(*args, **kwargs):
        raise AssertionError("v11 must not read legacy memory")

    monkeypatch.setattr(retrieval_module, "select_experience_candidates", fake_experiences)
    monkeypatch.setattr(soul_engine, "restore_emotion", fake_emotion)
    monkeypatch.setattr(memory_service, "select_core_facts", legacy_forbidden)
    monkeypatch.setattr(memory_service, "lexical_search", legacy_forbidden)

    session = SimpleNamespace(
        session_id=f"v11-snapshot-{case_name}",
        worldline="steins_gate",
        identity_mode=case["identity_mode"],
        base_system_prompt=None,
        system_prompt=None,
        memory_summary=case["summary"],
        history=list(case["history"]),
        self_name=case["self_name"],
        identity_acknowledged=case["identity_acknowledged"],
    )
    prompt = await compile_for_session(session, case["message"])
    # Standalone section heading only; persona/WORKING CONTEXT text may name it.
    assert "\n\nCORE FACTS\n" not in prompt
    assert "(none)" not in prompt
    assert "MEMORY CANDIDATES" not in prompt
    _check(f"v11::{case_name}", prompt, case["message"])


def test_v11_snapshot_file_has_no_stale_entries():
    if REGEN:
        pytest.skip("regenerating")
    assert set(_load()) == {f"v11::{name}" for name in CASES}
