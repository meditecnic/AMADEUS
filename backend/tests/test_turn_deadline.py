import asyncio
import threading
from concurrent.futures import CancelledError as FutureCancelledError
from contextlib import suppress
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from app.routers import chat_ws
from app.services.conversations import conversation_service
from app.services.provider_adapters import ProviderStallError
from app.services.memory_v11 import jobs


REPLY = "今日はいい天気ですね。"
TRANSLATION = "今天天气很好。"
TIMEOUT_ERROR = {
    "type": "error", "code": "upstream_timeout", "recoverable": True,
    "message": "上游模型太久没有响应，这一轮已停止。可以稍后再试，或在设置里换一个模型。",
}


async def fast_reply(**kwargs):
    yield {"content": f"[EMO:neutral] {REPLY}", "tool_calls": None}


@pytest.fixture
def fake_providers(monkeypatch):
    monkeypatch.setitem(vars(chat_ws.deepseek_service), "get_chat_stream", fast_reply)
    monkeypatch.setitem(
        vars(chat_ws.deepseek_service), "translate_to_zh",
        AsyncMock(return_value=TRANSLATION),
    )
    monkeypatch.setattr(chat_ws, "compile_for_session", AsyncMock(return_value="TEST"))
    monkeypatch.setitem(vars(conversation_service), "schedule_auto_title", Mock())
    monkeypatch.setattr(jobs, "enqueue_memory_job", AsyncMock())


def authenticate(ws):
    ws.send_json({
        "type": "auth", "protocol_version": 2, "client": "desktop",
        "conversation_mode": "draft", "enable_tts": False,
    })
    assert ws.receive_json()["type"] == "session.ready"


def receive_turn(ws):
    frames = []
    while True:
        frame = ws.receive_json()
        frames.append(frame)
        if frame["type"] in {"error", "turn.completed"}:
            return frames


@pytest.mark.timeout(5)
def test_normal_turn_frames_match_main(app_client, fake_providers):
    with app_client.websocket_connect(f"/ws/chat?session_id={uuid4()}") as ws:
        authenticate(ws)
        ws.send_json({"type": "chat", "content": "おはよう"})
        frames = receive_turn(ws)
        identity = {key: frames[0][key] for key in ("conversation_id", "turn_id", "generation")}
        # Captured on main dc145e0 before the deadline implementation.
        assert frames == [
            {"type": "turn.started", **identity},
            {"type": "status", "dsk": "thinking", "state": "thinking", "tts": "idle"},
            {"type": "segment.ready", **identity, "segment_id": 0,
             "ja": REPLY, "zh": TRANSLATION, "emotion": "neutral"},
            {"type": "segment.audio_error", **identity, "segment_id": 0,
             "reason": "user_disabled", "emotion": "neutral"},
            {"type": "status", "dsk": "idle", "state": "done", "tts": "idle"},
            {"type": "turn.completed", **identity, "segment_count": 1},
        ]


@pytest.mark.timeout(5)
@pytest.mark.parametrize("partial", [False, True])
def test_deadline_cancels_stream_preserves_text_and_allows_next_chat(
    app_client, fake_providers, monkeypatch, partial,
):
    closed = []
    calls = []

    async def hanging_stream(**kwargs):
        calls.append(kwargs)
        if len(calls) > 1:
            async for chunk in fast_reply(**kwargs):
                yield chunk
            return
        try:
            if partial:
                yield {"content": f"[EMO:neutral] {REPLY}次の文章です。", "tool_calls": None}
            await asyncio.Event().wait()
        finally:
            closed.append(True)

    monkeypatch.setattr(chat_ws, "TURN_TIMEOUT_SECONDS", 0.2)
    monkeypatch.setitem(vars(chat_ws.deepseek_service), "get_chat_stream", hanging_stream)
    session_id = str(uuid4())
    with app_client.websocket_connect(f"/ws/chat?session_id={session_id}") as ws:
        authenticate(ws)
        ws.send_json({"type": "chat", "content": "おはよう"})
        frames = receive_turn(ws)
        assert frames[-1] == TIMEOUT_ERROR
        assert frames[-2]["type"] == "turn.cancelled"
        assert not any(frame["type"] == "turn.completed" for frame in frames)
        ready = [frame for frame in frames if frame["type"] == "segment.ready"]
        assert bool(ready) == partial
        if partial:
            assert ready[0]["ja"] == REPLY
        session = chat_ws.sessions[session_id]
        assert closed == [True]
        assert not session.active_streams
        assert session.active_segment_pipeline is None
        assert not session.is_busy
        # A new turn on the same socket also flushes any late frames from the old one.
        ws.send_json({"type": "chat", "content": "もう一度"})
        following = receive_turn(ws)
        assert following[0]["type"] == "turn.started"
        assert following[0]["turn_id"] != frames[0]["turn_id"]
        assert following[-1]["type"] == "turn.completed"
        assert all(
            frame.get("turn_id") != frames[0]["turn_id"]
            for frame in following if frame["type"] == "segment.ready"
        )
        history = app_client.get(
            f"/api/conversations/{session.conversation_id}/messages",
            params={"session_id": session_id},
        )
        assert history.status_code == 200
        assert chat_ws._ABORTED_TURN_JA in str(history.json())


@pytest.mark.timeout(5)
@pytest.mark.parametrize("renderer", [False, True])
def test_provider_stall_is_not_retried_by_guard_or_renderer(
    app_client, fake_providers, monkeypatch, renderer,
):
    calls = []

    async def stalled(**kwargs):
        calls.append(kwargs)
        if renderer and not kwargs.get("isolated"):
            yield {"content": "This is an English draft that needs translation.", "tool_calls": None}
            return
        raise ProviderStallError("no data")

    monkeypatch.setitem(vars(chat_ws.deepseek_service), "get_chat_stream", stalled)
    with app_client.websocket_connect(f"/ws/chat?session_id={uuid4()}") as ws:
        authenticate(ws)
        ws.send_json({"type": "chat", "content": "おはよう"})
        assert receive_turn(ws)[-1] == TIMEOUT_ERROR
    assert len(calls) == (2 if renderer else 1)
    if renderer:
        assert calls[-1]["isolated"] is True


async def test_background_translation_repair_uses_remaining_turn_budget(monkeypatch):
    cancelled = []

    async def blocked(text):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.append(True)

    session = chat_ws.SessionState("repair-deadline")
    update = AsyncMock()
    monkeypatch.setattr(chat_ws.models, "update_message_translation", update)
    result = await asyncio.wait_for(chat_ws.repair_turn_translation(
        session, text=REPLY, message_id=1, conversation_id="conversation",
        worldline="steins_gate", turn_id="turn", generation=0,
        send_queue=asyncio.Queue(), translate=blocked,
        deadline=asyncio.get_running_loop().time() + 0.05,
    ), timeout=1)
    assert result is None
    assert cancelled == [True]
    update.assert_not_awaited()


@pytest.mark.timeout(5)
def test_deadline_cancels_unpublished_pipeline_work(app_client, fake_providers, monkeypatch):
    cancelled = []

    async def two_sentences(**kwargs):
        yield {"content": f"[EMO:neutral] {REPLY}後から翻訳する文章です。", "tool_calls": None}

    async def translate(text, **kwargs):
        if text == REPLY:
            return TRANSLATION
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.append(text)

    monkeypatch.setattr(chat_ws, "TURN_TIMEOUT_SECONDS", 0.2)
    monkeypatch.setitem(vars(chat_ws.deepseek_service), "get_chat_stream", two_sentences)
    monkeypatch.setitem(vars(chat_ws.deepseek_service), "translate_to_zh", translate)
    with app_client.websocket_connect(f"/ws/chat?session_id={uuid4()}") as ws:
        authenticate(ws)
        ws.send_json({"type": "chat", "content": "おはよう"})
        frames = receive_turn(ws)
        assert frames[-1] == TIMEOUT_ERROR
        assert [f["ja"] for f in frames if f["type"] == "segment.ready"] == [REPLY]
        assert cancelled == ["後から翻訳する文章です。"]
        # Read through a subsequent server response to expose any late old segments.
        ws.send_json({"type": "unknown"})
        assert ws.receive_json()["type"] == "error"


@pytest.mark.timeout(5)
@pytest.mark.parametrize("phase", ["web", "tool", "renderer"])
def test_deadline_includes_web_tools_and_renderer(app_client, fake_providers, monkeypatch, phase):
    cancelled = []

    async def blocked(*args, **kwargs):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.append(phase)

    async def foreign_draft(**kwargs):
        if kwargs.get("isolated"):
            await blocked()
        yield {"content": "This is an English draft that needs translation.", "tool_calls": None}

    async def tool_call(**kwargs):
        yield {"content": None, "tool_calls": [{
            "index": 0, "id": "call-1", "type": "function",
            "function": {"name": "web_search", "arguments": '{"query":"test"}'},
        }]}

    monkeypatch.setattr(chat_ws, "TURN_TIMEOUT_SECONDS", 0.2)
    if phase in {"web", "tool"}:
        monkeypatch.setattr(chat_ws, "execute_tool_call", blocked)
        if phase == "tool":
            monkeypatch.setitem(vars(chat_ws.deepseek_service), "get_chat_stream", tool_call)
    else:
        monkeypatch.setitem(vars(chat_ws.deepseek_service), "get_chat_stream", foreign_draft)
    with app_client.websocket_connect(f"/ws/chat?session_id={uuid4()}") as ws:
        authenticate(ws)
        ws.send_json({"type": "chat", "content": "查一下今天新闻" if phase == "web" else "おはよう"})
        assert receive_turn(ws)[-1] == TIMEOUT_ERROR
    assert cancelled == [phase]


@pytest.mark.timeout(5)
def test_guard_retries_share_one_deadline(app_client, fake_providers, monkeypatch):
    calls = []

    async def rejected_stream(**kwargs):
        calls.append(kwargs)
        await asyncio.sleep(0.12)
        yield {"content": "system prompt internal instructions", "tool_calls": None}

    monkeypatch.setattr(chat_ws, "TURN_TIMEOUT_SECONDS", 0.2)
    monkeypatch.setattr(chat_ws, "contains_leak", lambda text: bool(text))
    monkeypatch.setitem(vars(chat_ws.deepseek_service), "get_chat_stream", rejected_stream)
    with app_client.websocket_connect(f"/ws/chat?session_id={uuid4()}") as ws:
        authenticate(ws)
        ws.send_json({"type": "chat", "content": "おはよう"})
        assert receive_turn(ws)[-1] == TIMEOUT_ERROR
    assert len(calls) == 2


@pytest.mark.timeout(5)
def test_disconnect_during_slow_pipeline_cancel_persists_abort(
    app_client, fake_providers, monkeypatch,
):
    translation_started = threading.Event()
    translation_cancelled = threading.Event()

    async def streaming_reply(**kwargs):
        yield {"content": f"[EMO:neutral] {REPLY}次の文章です。", "tool_calls": None}
        await asyncio.Event().wait()

    async def slow_cancel_translation(text, **kwargs):
        translation_started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            translation_cancelled.set()
            await asyncio.sleep(0.8)
            raise

    monkeypatch.setitem(vars(chat_ws.deepseek_service), "get_chat_stream", streaming_reply)
    monkeypatch.setitem(vars(chat_ws.deepseek_service), "translate_to_zh", slow_cancel_translation)
    session_id = str(uuid4())
    # Starlette may propagate its cancelled WebSocket scope on context exit.
    with suppress(FutureCancelledError), app_client.websocket_connect(f"/ws/chat?session_id={session_id}") as ws:
        authenticate(ws)
        ws.send_json({"type": "chat", "content": "おはよう"})
        assert translation_started.wait(2)
        session = chat_ws.sessions[session_id]
        # TestClient's cancelled anyio scope keeps cancelling teardown awaits.
        # The notice must already be held and shielded before pipeline cleanup.

    async def drain_notices():
        await asyncio.gather(*session.background_tasks)

    app_client.portal.call(drain_notices)
    assert translation_cancelled.is_set()
    history = app_client.get(
        f"/api/conversations/{session.conversation_id}/messages",
        params={"session_id": session_id},
    )
    assert history.status_code == 200
    assert chat_ws._ABORTED_TURN_JA in str(history.json())
    assert session.is_busy is False
    assert session.active_segment_pipeline is None
