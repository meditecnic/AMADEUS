"""D-01 tests at the existing server websocket.send_json boundary."""

import asyncio
from types import SimpleNamespace

import pytest
from starlette.websockets import WebSocketDisconnect

from app.routers import chat_ws


class _Observer:
    def __init__(self):
        self.calls = []

    def delivery(self, payload, status, error_summary=None):
        self.calls.append((payload["type"], status, error_summary, payload.get("segment_id")))


class _Runtime:
    def __init__(self, observer):
        self.observer = observer
        self.released = []

    def find_turn_observer(self, turn_id, generation):
        if (turn_id, generation) == ("turn-1", 7):
            return self.observer
        return None

    def release_turn_observer(self, turn_id, generation):
        self.released.append((turn_id, generation))


class _WebSocket:
    def __init__(self, error=None):
        self.error = error
        self.sent = []

    async def send_json(self, payload):
        if self.error:
            raise self.error
        self.sent.append(payload)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (None, ("segment.audio", "succeeded", None, 3)),
        (WebSocketDisconnect(), ("segment.audio", "failed", "websocket_disconnected", 3)),
        (RuntimeError("C:\\private\\raw upstream body"), ("segment.audio", "failed", "transport_failed", 3)),
    ],
)
async def test_writer_loop_records_only_redacted_v2_delivery_outcome(monkeypatch, error, expected):
    observer = _Observer()
    monkeypatch.setattr(chat_ws, "diagnostic_runtime", _Runtime(observer))
    queue = asyncio.Queue()
    payload = {
        "type": "segment.audio",
        "turn_id": "turn-1",
        "generation": 7,
        "segment_id": 3,
        "data": "base64-is-never-forwarded-to-diagnostics",
    }
    await queue.put((7, payload))
    await queue.put(None)
    await chat_ws.writer_loop(queue, SimpleNamespace(current_epoch=7, session_id="s"), _WebSocket(error))
    assert observer.calls == [expected]
    assert "private" not in repr(observer.calls)


@pytest.mark.asyncio
async def test_writer_loop_does_not_guess_diagnostic_event_for_epoch_rejection(monkeypatch):
    observer = _Observer()
    monkeypatch.setattr(chat_ws, "diagnostic_runtime", _Runtime(observer))
    queue = asyncio.Queue()
    await queue.put((6, {"type": "segment.audio", "turn_id": "turn-1", "generation": 6, "segment_id": 3}))
    await queue.put(None)
    websocket = _WebSocket()
    await chat_ws.writer_loop(queue, SimpleNamespace(current_epoch=7, session_id="s"), websocket)
    assert observer.calls == []
    assert websocket.sent == []
