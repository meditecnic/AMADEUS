"""Dispatch 76: real sockets share a session, but own their active turns."""

import asyncio
from contextlib import ExitStack
import threading
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.routers import chat_ws, voice_ws
from app.services.session_manager import cancel_inflight

REPLY = "次の回答は正常に完了したわ。"


class GatedProvider:
    def __init__(self):
        self.started = [threading.Event() for _ in range(2)]
        self.release = [threading.Event() for _ in range(2)]
        self.cancelled = [threading.Event() for _ in range(2)]
        self.calls = 0

    async def get_chat_stream(self, **kwargs):
        index = self.calls
        self.calls += 1
        self.started[index].set()
        try:
            while not self.release[index].is_set():
                await asyncio.sleep(0.005)
            yield {"content": REPLY, "tool_calls": None}
        except asyncio.CancelledError:
            self.cancelled[index].set()
            raise

    async def translate_to_zh(self, text, api_key=None):
        return "正常回复"

    async def compress_history(self, history, api_key=None, old_summary=""):
        return old_summary


def auth(ws):
    ws.send_json({"type": "auth", "client": "desktop", "protocol_version": 2, "enable_tts": False})
    return receive_until(ws, "session.ready")


def receive_until(ws, kind):
    frames = []
    while True:
        frame = ws.receive_json()
        frames.append(frame)
        assert frame["type"] not in {"error", "turn.cancelled"}, frame
        if frame["type"] == kind:
            return frames


def rows(client, session_id):
    session = chat_ws.sessions[session_id]
    response = client.get(
        f"/api/conversations/{session.conversation_id}/messages",
        params={"session_id": session_id, "worldline": "steins_gate"},
    )
    assert response.status_code == 200
    return response.json()


@pytest.mark.timeout(15)
def test_real_reconnect_cancels_old_turn_once_and_old_disconnect_keeps_new_turn(app_client, monkeypatch):
    provider = GatedProvider()
    monkeypatch.setattr(chat_ws, "deepseek_service", provider)
    cancelled_pipelines = []
    original_cancel = chat_ws.SegmentPipeline.cancel

    async def record_cancel(self, *args, **kwargs):
        cancelled_pipelines.append(self.identity.turn_id)
        return await original_cancel(self, *args, **kwargs)

    monkeypatch.setattr(chat_ws.SegmentPipeline, "cancel", record_cancel)
    session_id = f"real-reconnect-{uuid4()}"
    with ExitStack() as sockets:
        old_socket = sockets.enter_context(ExitStack())
        a = old_socket.enter_context(app_client.websocket_connect(f"/ws/chat?session_id={session_id}"))
        auth(a)
        a.send_json({"type": "chat.send", "content": "一問目"})
        assert provider.started[0].wait(3)
        session = chat_ws.sessions[session_id]
        old_task = session.active_task
        old_owner = session.active_task_connection_id
        old_turn = session.active_turn_id
        assert old_owner

        b = sockets.enter_context(app_client.websocket_connect(f"/ws/chat?session_id={session_id}"))
        auth(b)
        assert old_task.cancelled()
        assert provider.cancelled[0].is_set()
        assert cancelled_pipelines == [old_turn]
        assert session.is_busy is False
        assert session.active_task is None
        assert session.active_task_connection_id is None
        assert sum(chat_ws._ABORTED_TURN_JA in row["content"] for row in rows(app_client, session_id)) == 1

        b.send_json({"type": "chat.send", "content": "二問目"})
        assert provider.started[1].wait(3)
        new_task = session.active_task
        assert session.active_task_connection_id not in {None, old_owner}
        old_socket.close()  # Wait until A's reader finally has finished.
        assert not new_task.done()
        assert session.active_task is new_task
        assert session.is_busy
        provider.release[1].set()
        frames = receive_until(b, "turn.completed")
        assert not any(frame["type"] == "turn.cancelled" for frame in frames)
        assert "".join(frame["ja"] for frame in frames if frame["type"] == "segment.ready") == REPLY
        history = rows(app_client, session_id)
        assert [row["role"] for row in history] == ["user", "assistant", "user", "assistant"]
        assert sum(chat_ws._ABORTED_TURN_JA in row["content"] for row in history) == 1
        assert history[-1]["content"].endswith(REPLY)


@pytest.mark.timeout(15)
def test_failed_auth_and_disconnect_do_not_cancel_another_connections_turn(
    app_client, monkeypatch, isolated_provider_credentials
):
    provider = GatedProvider()
    monkeypatch.setattr(chat_ws, "deepseek_service", provider)
    session_id = f"failed-auth-{uuid4()}"
    with app_client.websocket_connect(f"/ws/chat?session_id={session_id}") as a:
        auth(a)
        a.send_json({"type": "chat.send", "content": "一問目"})
        assert provider.started[0].wait(3)
        session = chat_ws.sessions[session_id]
        task = session.active_task
        owner = session.active_task_connection_id
        isolated_provider_credentials.delete("deepseek")
        with app_client.websocket_connect(f"/ws/chat?session_id={session_id}") as b:
            b.send_json({"type": "auth", "protocol_version": 2})
            error = b.receive_json()
            assert error["type"] == "error"
            assert "API Key" in error["message"]
            assert not task.done()
            assert session.active_task_connection_id == owner
        assert not task.done()
        assert not provider.cancelled[0].is_set()
        assert session.is_busy
        provider.release[0].set()

        async def wait_finished():
            await asyncio.wait_for(asyncio.shield(task), 3)

        app_client.portal.call(wait_finished)
        assert not task.cancelled()
        assert rows(app_client, session_id)[-1]["content"].endswith(REPLY)
        assert not any(chat_ws._ABORTED_TURN_JA in row["content"] for row in rows(app_client, session_id))


@pytest.mark.timeout(15)
def test_chat_disconnect_preserves_real_voice_owned_turn(app_client, monkeypatch):
    provider = GatedProvider()
    monkeypatch.setattr(chat_ws, "deepseek_service", provider)
    monkeypatch.setattr(type(voice_ws.speech_service), "transcribe_pcm", AsyncMock(return_value=("一問目", "neutral", 0.9)))
    session_id = f"voice-owner-{uuid4()}"
    with ExitStack() as sockets:
        chat_socket = sockets.enter_context(ExitStack())
        chat = chat_socket.enter_context(app_client.websocket_connect(f"/ws/chat?session_id={session_id}"))
        auth(chat)
        voice = sockets.enter_context(app_client.websocket_connect(f"/ws/voice?session_id={session_id}"))
        voice.send_json({"type": "auth"})
        receive_until(voice, "voice.ready")
        voice.send_json({"type": "voice.start", "sample_rate": 16000, "channels": 1})
        voice.send_bytes(b"\0" * 16000)
        voice.send_json({"type": "voice.commit"})
        assert provider.started[0].wait(3)
        session = chat_ws.sessions[session_id]
        task = session.active_task
        assert session.active_task_connection_id
        chat_socket.close()
        assert not task.done()
        assert session.is_busy
        provider.release[0].set()
        frames = receive_until(voice, "turn.completed")
        assert "".join(frame["ja"] for frame in frames if frame["type"] == "segment.ready") == REPLY
        assert rows(app_client, session_id)[-1]["content"].endswith(REPLY)


@pytest.mark.timeout(15)
def test_tts_debug_records_owner_and_disconnect_cancels_own_task(app_client, monkeypatch):
    started = threading.Event()

    async def blocked_tts(*args):
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(chat_ws, "run_tts_pipeline", blocked_tts)
    session_id = f"debug-owner-{uuid4()}"
    with app_client.websocket_connect(f"/ws/chat?session_id={session_id}") as ws:
        auth(ws)
        ws.send_json({"type": "tts_debug", "content": "日本語の試験よ。", "emotion": "neutral"})
        assert started.wait(3)
        session = chat_ws.sessions[session_id]
        task = session.active_task
        assert session.active_task_connection_id
    assert task.cancelled()


async def test_cancel_inflight_clears_task_and_connection_owner():
    session = chat_ws.SessionState("clear-owner")
    session.active_task = asyncio.create_task(asyncio.Event().wait())
    session.active_task_connection_id = "connection-id"
    session.is_busy = True
    assert await cancel_inflight(session) == 1
    assert session.active_task is None
    assert session.active_task_connection_id is None
    assert session.is_busy is False
