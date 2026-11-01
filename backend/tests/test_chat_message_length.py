"""Single-message limits are checked before a chat turn starts."""

import asyncio
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.routers import chat_ws


@pytest.fixture
def ready_chat(app_client, monkeypatch):
    async def process(session, content, send_queue, epoch, history_epoch):
        await send_queue.put((epoch, {"type": "test.processed"}))
        await asyncio.Event().wait()

    processor = AsyncMock(side_effect=process)
    monkeypatch.setattr(chat_ws, "processor_loop", processor)
    session_id = f"message-length-{uuid4()}"
    with app_client.websocket_connect(f"/ws/chat?session_id={session_id}") as ws:
        ws.send_json({"type": "auth", "protocol_version": 2, "enable_tts": False})
        while True:
            frame = ws.receive_json()
            assert frame["type"] != "error", frame
            if frame["type"] == "session.ready":
                break
        yield ws, chat_ws.sessions[session_id], processor
    chat_ws.sessions.pop(session_id, None)


@pytest.mark.timeout(15)
@pytest.mark.parametrize("frame_type", ["chat", "chat.send"])
@pytest.mark.parametrize(
    "content",
    ["字" * 8000, " \t\n" + "字" * 8000 + "\n\t ", "🙂" * 8000],
    ids=["8000", "trimmed-8000", "emoji-8000"],
)
def test_accepts_8000_code_points(ready_chat, frame_type, content):
    ws, session, processor = ready_chat
    ws.send_json({"type": frame_type, "content": content})
    assert ws.receive_json() == {"type": "test.processed"}
    assert session.is_busy is True
    assert session.active_turn_id is not None
    assert session.history[-1]["role"] == "user"
    assert session.history[-1]["content"] == content.strip()
    processor.assert_awaited_once()
    assert processor.call_args.args[1] == content.strip()


@pytest.mark.timeout(15)
@pytest.mark.parametrize("frame_type", ["chat", "chat.send"])
@pytest.mark.parametrize("content", ["字" * 8001, " 🙂" + "🙂" * 8000 + "\n"], ids=["8001", "emoji-8001"])
def test_rejects_over_8000_without_starting_a_turn(ready_chat, frame_type, content):
    ws, session, processor = ready_chat
    history = list(session.history)
    ws.send_json({"type": frame_type, "content": content})
    assert ws.receive_json() == {
        "type": "error",
        "code": "message_too_long",
        "recoverable": True,
        "message": "消息太长了，请分几次发（上限 8000 字）。",
    }
    assert session.history == history
    assert session.is_busy is False
    assert session.active_turn_id is None
    assert session.active_task is None
    processor.assert_not_called()

    # The rejected frame leaves this connection ready for a valid message.
    ws.send_json({"type": frame_type, "content": "短消息"})
    assert ws.receive_json() == {"type": "test.processed"}
    processor.assert_awaited_once()
    assert session.history[-1]["content"] == "短消息"
