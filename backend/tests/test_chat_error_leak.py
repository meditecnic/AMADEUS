"""Client errors keep their wire contract without exposing internal exceptions."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from fastapi import WebSocketDisconnect
from starlette.datastructures import Headers

from app.routers import chat_ws
from app.services.conversations import conversation_service
from app.services.provider_adapters import ProviderRequestError
from app.services.search import SearchEvidenceError
from app.services.speech import speech_service

SECRET = "secret-marker-123 https://internal.invalid/private?payload=hidden"


def _frames(queue):
    return [queue.get_nowait()[1] for _ in range(queue.qsize())]


def _assert_logged(capsys, prefix, error_type="RuntimeError", *, include_text=True):
    output = capsys.readouterr().out
    assert prefix in output
    assert error_type in output
    if include_text:
        assert SECRET in output
    else:
        assert "secret-marker-123" not in output


@pytest.mark.parametrize("error", [RuntimeError(SECRET), ValueError(SECRET)])
async def test_turn_failed_hides_exception_and_preserves_contract(monkeypatch, capsys, error):
    session = chat_ws.SessionState("error-leak-turn")
    session.conversation_id = "conversation"
    monkeypatch.setattr(type(conversation_service), "require_owned", AsyncMock(return_value={}))
    # Earlier tests can leave a bound instance method that shadows class patches.
    # setitem installs the fault at the lookup site and restores the exact prior state.
    monkeypatch.setitem(vars(chat_ws.provider_registry), "snapshot", Mock(side_effect=error))
    queue = asyncio.Queue()
    await chat_ws.processor_loop(session, "hello", queue, 0, 0)
    assert _frames(queue) == [{
        "type": "error", "code": "turn_failed", "recoverable": True, "message": "Server error",
    }]
    assert session.is_busy is False
    _assert_logged(capsys, "[Processor Error] Session error-leak-turn:", type(error).__name__)


@pytest.mark.parametrize("status", [401, 403, 500])
async def test_turn_failed_preserves_safe_provider_status(monkeypatch, status):
    session = chat_ws.SessionState("provider-error")
    session.conversation_id = "conversation"
    monkeypatch.setattr(type(conversation_service), "require_owned", AsyncMock(return_value={}))
    error = ProviderRequestError("deepseek", status)
    monkeypatch.setitem(vars(chat_ws.provider_registry), "snapshot", Mock(side_effect=error))
    queue = asyncio.Queue()
    await chat_ws.processor_loop(session, "hello", queue, 0, 0)
    assert _frames(queue) == [{
        "type": "error", "code": "turn_failed", "recoverable": True,
        "message": f"Server error: deepseek request failed with status {status}",
    }]


async def test_search_error_keeps_fixed_domain_message(monkeypatch):
    session = chat_ws.SessionState("search-error")
    session.conversation_id = "conversation"
    monkeypatch.setattr(type(conversation_service), "require_owned", AsyncMock(return_value={}))
    error = SearchEvidenceError("unavailable")
    monkeypatch.setitem(vars(chat_ws.provider_registry), "snapshot", Mock(side_effect=error))
    queue = asyncio.Queue()
    await chat_ws.processor_loop(session, "hello", queue, 0, 0)
    assert _frames(queue) == [{
        "type": "error", "code": "web_search_unavailable", "recoverable": True,
        "message": str(error),
    }]


async def test_debug_tts_hides_exception(monkeypatch, capsys):
    session = chat_ws.SessionState("debug-error")
    monkeypatch.setattr(chat_ws, "_dispatch_tts_sentences", AsyncMock(side_effect=RuntimeError(SECRET)))
    queue = asyncio.Queue()
    await chat_ws.run_tts_pipeline(session, "こんにちは。", "neutral", queue, 0)
    assert _frames(queue)[-1] == {"type": "error", "message": "TTS debug error"}
    assert session.is_busy is False
    _assert_logged(capsys, "[TTS Debug Error]")


async def test_streaming_tts_hides_exception_and_continues(monkeypatch, capsys):
    async def broken_stream(*args, **kwargs):
        raise RuntimeError(SECRET)
        yield  # pragma: no cover

    monkeypatch.setattr(type(chat_ws.tts_manager), "stream_pcm", broken_stream)
    session = chat_ws.SessionState("stream-error")
    queue = asyncio.Queue()
    assert await chat_ws._dispatch_streaming_voice_tts(session, "こんにちは。", "neutral", queue, 0) == "こんにちは。"
    assert _frames(queue) == [
        {"type": "text_chunk", "sentence_id": 0, "content": "こんにちは。", "emotion": "neutral"},
        {"type": "voice.audio_degraded", "sentence_id": 0, "reason": "service_error"},
    ]
    assert not session.active_streams
    _assert_logged(capsys, "[Voice TTS Error]", include_text=False)


async def test_malformed_frame_hides_exception_and_reader_survives(monkeypatch, capsys):
    queued = []
    queue_type = asyncio.Queue

    class RecordingQueue(queue_type):
        async def put(self, item):
            queued.append(item[1])
            await super().put(item)

    websocket = SimpleNamespace(
        query_params={"session_id": f"invalid-frame-{uuid4()}"},
        headers=Headers({"origin": "http://localhost"}), accept=AsyncMock(),
        receive_json=AsyncMock(side_effect=[RuntimeError(SECRET), {"type": "ping"}, WebSocketDisconnect()]),
    )
    monkeypatch.setattr(chat_ws.asyncio, "Queue", RecordingQueue)
    await chat_ws.websocket_chat_endpoint(websocket)
    chat_ws.sessions.pop(websocket.query_params["session_id"])
    assert queued[0] == {"type": "error", "message": "数据格式无效"}
    assert len(queued) == 2  # the following frame reached the auth check
    _assert_logged(capsys, f"[Invalid Frame] Session {websocket.query_params['session_id']}:", include_text=False)


async def test_reader_boundary_hides_exception(monkeypatch, capsys):
    queued = []
    queue_type = asyncio.Queue

    class RecordingQueue(queue_type):
        async def put(self, item):
            queued.append(item[1])
            await super().put(item)

    bad_frame = Mock()
    bad_frame.get.side_effect = RuntimeError(SECRET)
    websocket = SimpleNamespace(
        query_params={"session_id": f"reader-error-{uuid4()}"},
        headers=Headers({"origin": "http://localhost"}), accept=AsyncMock(),
        receive_json=AsyncMock(return_value=bad_frame),
    )
    monkeypatch.setattr(chat_ws.asyncio, "Queue", RecordingQueue)
    await chat_ws.websocket_chat_endpoint(websocket)
    chat_ws.sessions.pop(websocket.query_params["session_id"])
    assert queued == [{"type": "error", "message": "Server error"}]
    _assert_logged(capsys, "[Reader Exception]")


def test_clear_history_hides_exception_and_keeps_history(app_client, monkeypatch, capsys):
    monkeypatch.setattr(chat_ws.models, "clear_session_data", AsyncMock(side_effect=RuntimeError(SECRET)))
    session_id = f"clear-error-{uuid4()}"
    with app_client.websocket_connect(f"/ws/chat?session_id={session_id}") as ws:
        ws.send_json({"type": "auth", "protocol_version": 2, "conversation_mode": "draft"})
        assert ws.receive_json()["type"] == "session.ready"
        session = chat_ws.sessions[session_id]
        session.history = [{"role": "user", "content": "keep me"}]
        ws.send_json({"type": "clear_history"})
        assert ws.receive_json() == {"type": "error", "message": "物理清除历史记录失败"}
        assert session.history == [{"role": "user", "content": "keep me"}]
    _assert_logged(capsys, f"[Clear History Error] Session {session_id}:", include_text=False)


def test_stt_hides_exception_and_keeps_connection(app_client, monkeypatch, capsys):
    monkeypatch.setattr(type(speech_service), "transcribe_pcm", AsyncMock(side_effect=RuntimeError(SECRET)))
    with app_client.websocket_connect(f"/ws/voice?session_id=stt-error-{uuid4()}") as ws:
        ws.send_json({"type": "auth"})
        assert ws.receive_json()["type"] == "voice.ready"
        ws.send_bytes((10000).to_bytes(2, "little", signed=True) * 4000)
        ws.send_json({"type": "voice.commit"})
        assert ws.receive_json()["type"] == "stt.status"
        assert ws.receive_json() == {
            "type": "voice.error", "stage": "stt", "message": "Speech recognition failed", "degraded": True,
        }
        ws.send_json({"type": "ping"})
        assert ws.receive_json() == {"type": "pong"}
    _assert_logged(capsys, "[STT Error]", include_text=False)
