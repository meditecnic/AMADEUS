"""Offline request captures for dispatch 182; no model or TTS calls."""

import asyncio
from datetime import datetime
from copy import deepcopy
from unittest.mock import AsyncMock

import pytest

from app.routers import chat_ws
from app.services.llm_interface import TAIL_REMINDER_HEADER, build_stream_messages
from app.services.memory_v11.recall import RecallGroup, RecallResult, _render_block
from app.services.prompt_compiler import PromptCompiler, render_tail_reminder
from app.services.turn_tool_orchestrator import (
    MEMORY_SECOND_TURN_NOTE,
    ToolBatchOutcome,
    prepare_second_round_payload,
    web_evidence_system_message,
)
from test_chat_ws_background_tasks import _new_session, _suppress_auto_title, isolated_store  # noqa: F401

BUDGET = 32000
CURRENT = "今の質問をそのまま残して。"
SYSTEM = "PERSONA BIBLE v1\nシステム原文\nWORKING CONTEXT\n圧縮摘要"
WEB = [("web_search", "証拠の原文" * 200)]


def history(long=True):
    return [
        {"role": "user" if i % 2 == 0 else "assistant", "content": f"履歴{i}:" + "あ" * (3000 if long else 3)}
        for i in range(30 if long else 4)
    ] + [{"role": "user", "content": CURRENT}]


def memory(size=600):
    records = {"m1": "猫" * size, "m2": "茶" * size}
    groups = (RecallGroup(0, "好み", ("m1", "m2"), 2, False, 0),)
    return RecallResult(status="ok", groups=groups, records=records,
                        block=_render_block(status="ok", groups=groups, records=records))


def envelope(rows, system=SYSTEM, builder=None):
    return build_stream_messages(builder or chat_ws.deepseek_service.build_persona_messages,
                                 rows, "", system, isolated=False)


def tokens(messages):
    return sum(PromptCompiler._estimate_tokens(str(row.get("content") or "")) for row in messages)


@pytest.mark.parametrize("kind", ["first", "short", "forced", "web_second", "recall", "refresh"])
async def test_processor_request_budget(isolated_store, monkeypatch, caplog, kind):
    import app.services.turn_tool_orchestrator as orchestrator

    session = await _new_session()
    session.history = history(kind != "short")
    original = deepcopy(session.history)
    captures = []
    sent_envelopes = []
    _suppress_auto_title(monkeypatch)
    monkeypatch.setattr(chat_ws, "compile_for_session", AsyncMock(return_value=SYSTEM))
    monkeypatch.setattr(chat_ws, "should_force_search", lambda text: kind == "forced")
    monkeypatch.setattr(chat_ws, "execute_tool_call", AsyncMock(return_value=WEB[0][1]))
    monkeypatch.setattr(chat_ws.config, "HISTORY_COMPRESS_THRESHOLD", 1000)
    monkeypatch.setattr("app.services.memory_v11.jobs.enqueue_memory_job", AsyncMock())
    recalled = memory()
    if kind == "refresh":
        # Fill the closed-tools envelope up to the budget. A leak retry adds
        # its own system instruction and must re-budget Memory as well.
        # Same shape as the product: strftime gets only the ASCII part (Windows
        # encodes the format with the locale codec).
        now = datetime.now()
        weekday = ("月", "火", "水", "木", "金", "土", "日")[now.weekday()]
        effective = SYSTEM + f"\n【現在時刻：{now.strftime('%Y-%m-%d %H:%M')}（{weekday}）】"
        tail = {"role": "system", "content": render_tail_reminder(
            session.identity_mode, getattr(session, "self_name", ""),
        )}
        empty_memory = {"role": "system", "content": memory(0).block + "\n" + MEMORY_SECOND_TURN_NOTE}
        base = tokens(envelope([original[-1], empty_memory, tail], effective))
        recalled = memory((BUDGET - base) // 2)
    monkeypatch.setattr(orchestrator, "apply_v11_tool_batch", AsyncMock(return_value=ToolBatchOutcome(
        status="ok", recall_result=recalled if kind in {"recall", "refresh"} else None,
    )))
    monkeypatch.setattr(orchestrator, "refresh_candidates_for_send", AsyncMock(return_value=recalled))

    async def capture(self, **kwargs):
        rows = kwargs["active_history"]
        # Persistence may assign the current user ID; budgeting must leave the
        # complete stored history and all previous message text untouched.
        assert len(session.history) == len(original)
        assert session.history[:-1] == original[:-1]
        captures.append(deepcopy(rows))
        sent_envelopes.append(envelope(rows, kwargs["system_prompt"]))
        if len(captures) == 1 and kind in {"web_second", "recall", "refresh"}:
            if kind == "web_second":
                session.turn_execution.web_results.extend(WEB)
            yield {"content": None, "tool_calls": [{"index": 0, "id": "budget182",
                   "type": "function", "function": {"name": "web_search", "arguments": "{}"}}]}
            return
        if kind == "refresh" and len(captures) == 2:
            yield {"content": "私はAIアシスタントです。", "tool_calls": None}
        else:
            yield {"content": "条件を確認するわ。", "tool_calls": None}

    monkeypatch.setattr(type(chat_ws.deepseek_service), "get_chat_stream", capture)
    monkeypatch.setattr(chat_ws, "translate_with_provider", AsyncMock(return_value="翻译"))
    with caplog.at_level("INFO", logger=chat_ws.__name__):
        await chat_ws.processor_loop(session, CURRENT, asyncio.Queue(), 0, 0)
    assert captures
    assert len(session.history) == len(original) + 1
    assert session.history[:-2] == original[:-1]
    assert all(tokens(messages) <= BUDGET for messages in sent_envelopes)
    for rows in captures:
        messages = envelope(rows)
        assert tokens(messages) <= BUDGET
        assert rows[0]["role"] == "user"
        users = [row for row in rows if row["role"] == "user"]
        assert users[-1]["content"] == original[-1]["content"]
        old_kept = [row for row in rows if row in original[:-1]]
        assert old_kept == original[len(original) - 1 - len(old_kept):-1]
        assert SYSTEM in messages[0]["content"]
        assert sum(TAIL_REMINDER_HEADER in row["content"] for row in messages) == 1
    if kind == "short":
        baseline = deepcopy(original)
        assert captures[0][:-1] == baseline
        assert envelope(captures[0]) == envelope(baseline + [captures[0][-1]])
        assert getattr(session.turn_execution, "dropped_history_messages", 0) == 0
    else:
        assert getattr(session.turn_execution, "dropped_history_messages", 0) > 0
        assert "budget=32000" in caplog.text
    if kind in {"forced", "web_second"}:
        assert {"role": "system", "content": web_evidence_system_message(WEB)} in captures[-1]
    if kind in {"recall", "refresh"}:
        assert {"role": "system", "content": recalled.block + "\n" + MEMORY_SECOND_TURN_NOTE} in captures[1]
    if kind == "refresh":
        assert len(captures) == 3
        assert recalled.block not in "".join(row["content"] for row in captures[-1])


def test_memory_yields_only_after_all_old_history():
    old = history()
    recalled = memory(3000)
    full = old + [{"role": "system", "content": recalled.block + "\n" + MEMORY_SECOND_TURN_NOTE}]
    assert tokens(envelope(full)) > BUDGET
    payload = prepare_second_round_payload(old, recalled, system_prompt=SYSTEM)
    assert payload.status == "ok"
    assert payload.result.records == recalled.records
    assert tokens(envelope(payload.history)) <= BUDGET
    assert old == history()
    tight = prepare_second_round_payload(old, recalled, input_budget=5000, system_prompt=SYSTEM)
    assert tight.history[0] == old[-1]
    assert len([row for row in tight.history if row["role"] != "system"]) == 1
    assert len(tight.result.records) < len(recalled.records)
    assert tokens(envelope(tight.history)) <= 5000


def test_current_content_over_budget_preserves_evidence():
    current = [{"role": "user", "content": "あ" * 33000}]
    payload = prepare_second_round_payload(current, memory(), WEB, system_prompt=SYSTEM)
    assert payload.reason == "input_budget"
    assert payload.history[0] == current[0]
    assert {"role": "system", "content": web_evidence_system_message(WEB)} in payload.history


async def test_current_content_over_budget_logs_warning(isolated_store, monkeypatch, caplog):
    session = await _new_session()
    session.history = [{"role": "user", "content": "あ" * 33000}]
    monkeypatch.setattr(chat_ws, "compile_for_session", AsyncMock(return_value=SYSTEM))
    monkeypatch.setattr(chat_ws, "should_force_search", lambda text: False)
    captures = []

    async def capture(self, **kwargs):
        captures.append(kwargs["active_history"])
        yield {"content": "", "tool_calls": None}

    monkeypatch.setattr(type(chat_ws.deepseek_service), "get_chat_stream", capture)
    with caplog.at_level("WARNING", logger=chat_ws.__name__):
        await chat_ws.processor_loop(session, session.history[0]["content"], asyncio.Queue(), 0, 0)
    assert captures[0][0]["content"] == "あ" * 33000
    assert "budget=32000" in caplog.text
    assert "あ" * 10 not in caplog.text


def test_custom_message_builder_envelope_is_counted():
    def builder(rows, summary, system):
        return [{"role": "system", "content": "人" * 6000}] + rows

    payload = prepare_second_round_payload(history(), memory(), system_prompt=SYSTEM, message_builder=builder)
    assert tokens(envelope(payload.history, builder=builder)) <= BUDGET
    assert payload.history[0]["role"] == "user"
