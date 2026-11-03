"""Compare the linear path with the unchanged pre-rework trimming loop."""

from copy import deepcopy
import random

import pytest

from app.services import turn_tool_orchestrator as orchestrator
from app.services.provider_runtime import deepseek_service


def original_trim_request_history(
    old_history, turn_messages, system_prompt, memory_summary, message_builder, budget,
):
    # Exact implementation from 06f5310; keep this independent reference unchanged.
    kept = list(old_history)
    while kept and orchestrator.request_envelope_tokens(
        kept + turn_messages, system_prompt, memory_summary, message_builder,
    ) > budget:
        kept.pop(0)
        while kept and kept[0].get("role") == "assistant":
            kept.pop(0)
    return kept + list(turn_messages), len(old_history) - len(kept)


def extra_system_builder(history, summary, system):
    return [{"role": "system", "content": "追加のシステム" * 120}] + deepseek_service.build_persona_messages(
        history, summary, system,
    )


def filtering_builder(history, summary, system):
    return deepseek_service.build_persona_messages(
        [row for row in history if not row.get("skip")], summary, system,
    )


def prefixing_builder(history, summary, system):
    return deepseek_service.build_persona_messages(
        [{**row, "content": "履歴の前置き" * 10 + row["content"]} for row in history],
        summary, system,
    )


BUILDERS = [deepseek_service.build_persona_messages, extra_system_builder, filtering_builder, prefixing_builder]


@pytest.mark.parametrize("builder", BUILDERS, ids=["persona", "extra_system", "filter", "prefix"])
def test_linear_matches_original_in_240_random_cases(builder):
    rng = random.Random(18203)
    for case in range(240):
        count = case % 41  # Includes every length from zero through forty.
        old = [
            {"role": rng.choice(["user", "assistant"]),
             "content": "".join(rng.choices("あいう漢字hello 你好。", k=rng.randint(0, 180))),
             "skip": rng.choice([True, False]), "id": index}
            for index in range(count)
        ]
        if old:
            old[0]["role"] = "assistant" if case % 2 else "user"
        turn = [{"role": "user", "content": "本輪 user 中文" * rng.randint(1, 20)},
                {"role": "system", "content": "evidence 原文" * rng.randint(0, 20)}]
        system = "PERSONA BIBLE v1\n" + rng.choice(["s", "日", "中"]) * rng.randint(0, 700)
        summary = "要約" * rng.randint(0, 15)
        cut = rng.randint(0, count)
        while cut < count and old[cut]["role"] == "assistant":
            cut += 1
        edge = orchestrator.request_envelope_tokens(old[cut:] + turn, system, summary, builder)
        budget = edge + (case % 3 - 1)
        original = deepcopy((old, turn))
        expected = original_trim_request_history(old, turn, system, summary, builder, budget)
        actual = orchestrator.trim_request_history(old, turn, system, summary, builder, budget)
        assert actual == expected, (case, count, budget, actual[1], expected[1])
        assert (old, turn) == original


def worst_case():
    old = [{"role": "user" if i % 2 == 0 else "assistant", "content": "あ" * 8000}
           for i in range(32)]
    turn = [{"role": "user", "content": "今" * 4000},
            {"role": "system", "content": "evidence " * 1000},
            {"role": "system", "content": "memory " * 1000}]
    return old, turn, "PERSONA BIBLE v1\n" + "s" * 12000, "", deepseek_service.build_persona_messages, 32000


@pytest.mark.parametrize("budget", [32000, 1])
def test_persona_worst_case_has_at_most_three_full_estimates(monkeypatch, budget):
    args = list(worst_case())
    args[-1] = budget
    expected = original_trim_request_history(*args)
    estimate = orchestrator.request_envelope_tokens
    calls = []

    def counted(*args):
        calls.append(len(args[0]))
        return estimate(*args)

    monkeypatch.setattr(orchestrator, "request_envelope_tokens", counted)
    assert orchestrator.trim_request_history(*args) == expected
    assert len(calls) <= 3


def changing_prefix_builder(history, summary, system):
    return [{"role": "system", "content": "動的前置き" * len(history)}] + deepseek_service.build_persona_messages(
        history, summary, system,
    )


@pytest.mark.parametrize("builder", [filtering_builder, prefixing_builder, changing_prefix_builder],
                         ids=["filter", "prefix", "changing_header"])
def test_rewritten_history_uses_original_fallback(monkeypatch, builder):
    old = [{"role": "user" if i % 2 == 0 else "assistant", "content": "test " * 30, "skip": i % 3 == 0}
           for i in range(20)]
    turn = [{"role": "user", "content": "本輪"}]
    budget = orchestrator.request_envelope_tokens(old[-2:] + turn, "system", "", builder)
    expected = original_trim_request_history(old, turn, "system", "", builder, budget)
    estimate = orchestrator.request_envelope_tokens
    calls = []

    def counted(*args):
        calls.append(len(args[0]))
        return estimate(*args)

    monkeypatch.setattr(orchestrator, "request_envelope_tokens", counted)
    assert orchestrator.trim_request_history(old, turn, "system", "", builder, budget) == expected
    assert calls.count(21) >= 2  # The old algorithm restarts with the full history.
    assert len(calls) > 3
