"""Capture actual HTTP messages across forced and model-requested web rounds."""

import asyncio
import json
from unittest.mock import AsyncMock

import httpx
import pytest

from app.routers import chat_ws
from app.services import deepseek, turn_tool_orchestrator
from app.services.turn_tool_orchestrator import ToolBatchOutcome
from test_chat_ws_background_tasks import _new_session, _suppress_auto_title, isolated_store  # noqa: F401

FORCED = "FORCED_EVIDENCE_182_R1"
FRESH = "FRESH_EVIDENCE_182_R1"


def frame(delta):
    return ("data: " + json.dumps({"choices": [{"delta": delta}]}, ensure_ascii=False) + "\n\n").encode("utf-8")


@pytest.mark.parametrize(("forced", "extra"), [(True, True), (False, True), (True, False)],
                         ids=["forced_and_new", "new_only", "forced_no_extra"])
async def test_web_second_round_preserves_each_evidence_once(isolated_store, monkeypatch, forced, extra):
    session = await _new_session()
    session.protocol_version = 2
    session.append_message({"role": "user", "content": "今日の天気を教えて。", "id": None})
    captured, event_types, buffered = [], [], []

    class RecordingQueue(asyncio.Queue):
        async def put(self, item):
            event_types.append(item[1]["type"])
            await super().put(item)

    class ReplyStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield frame({"content": "[EMO:neutral] 天気を確認したわ。"})
            # A complete sentence has arrived, but evidence rounds must hold it.
            buffered.append("segment.ready" not in event_types)
            yield frame({"content": "午後も晴れる見込みよ。"})
            yield b"data: [DONE]\n\n"

    async def handler(request):
        captured.append(json.loads(request.content))
        if len(captured) == 1:
            delta = {"tool_calls": [{"index": 0, "id": "web182r1", "type": "function",
                                     "function": {"name": "web_search", "arguments": '{"query":"weather"}'}}]}
            return httpx.Response(200, content=frame(delta) + b"data: [DONE]\n\n")
        return httpx.Response(200, stream=ReplyStream())

    _suppress_auto_title(monkeypatch)
    monkeypatch.setattr(chat_ws, "compile_for_session", AsyncMock(return_value="PERSONA BIBLE v1\n天気の確認"))
    monkeypatch.setattr(chat_ws, "should_force_search", lambda text: forced)
    monkeypatch.setattr(chat_ws, "translate_with_provider", AsyncMock(return_value="天气。"))
    monkeypatch.setattr("app.services.memory_v11.jobs.enqueue_memory_job", AsyncMock())
    monkeypatch.setattr(chat_ws, "execute_tool_call", AsyncMock(side_effect=[FORCED, FRESH] if forced else [FRESH]))
    if not extra:
        # The tool round produced no additional evidence after the forced search.
        monkeypatch.setattr(turn_tool_orchestrator, "apply_v11_tool_batch",
                            AsyncMock(return_value=ToolBatchOutcome(status="ok")))
    client_type = httpx.AsyncClient
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(deepseek.httpx, "AsyncClient", lambda **kwargs: client_type(transport=transport, **kwargs))
    await chat_ws.processor_loop(session, "今日の天気を教えて。", RecordingQueue(), 0, 0)
    if session.background_tasks:
        await asyncio.gather(*session.background_tasks)

    assert len(captured) == 2
    assert session.turn_execution.recall_result is None
    messages = captured[1]["messages"]
    assert sum(row["content"].count(FORCED) for row in messages) == int(forced)
    assert sum(row["content"].count(FRESH) for row in messages) == int(extra)
    assert buffered == [True]
    assert "segment.ready" in event_types
    assert "error" not in event_types
