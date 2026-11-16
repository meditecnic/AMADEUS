import asyncio
import json

import pytest
from starlette.websockets import WebSocket, WebSocketState

from app.routers import chat_ws, voice_ws
from app.services import data_relocation, data_startup
from app.services.session_manager import SessionCoordinator


@pytest.mark.timeout(10)
async def test_replaced_voice_late_frame_exits_and_keeps_new_lease(monkeypatch):
    coordinator = SessionCoordinator()
    monkeypatch.setattr(voice_ws, "session_coordinator", coordinator)
    monkeypatch.setattr(chat_ws, "sessions", {})

    async def load(self):
        return None

    async def accept(socket):
        await socket.accept()
        return True

    monkeypatch.setattr(chat_ws.SessionState, "load_from_db", load)
    monkeypatch.setattr("app.security.local_transport.accept_local_websocket", accept)
    sockets, queues, tasks = [], [], []
    for _ in range(2):
        queue = asyncio.Queue()
        sent = []

        async def send(message, sent=sent):
            sent.append(message)

        socket = WebSocket({"type": "websocket", "query_string": b"session_id=late182"}, queue.get, send)
        await queue.put({"type": "websocket.connect"})
        sockets.append(socket)
        queues.append(queue)
        tasks.append(asyncio.create_task(voice_ws.websocket_voice_endpoint(socket)))
        while coordinator._leases.get(("late182", "voice", "steins_gate")) is not socket:
            await asyncio.sleep(0)
    try:
        assert sockets[0].application_state == WebSocketState.DISCONNECTED
        await queues[0].put({"type": "websocket.receive", "text": json.dumps({"type": "ping"})})
        await tasks[0]  # Starlette raises RuntimeError on the baseline's late send_json.
        assert coordinator._leases[("late182", "voice", "steins_gate")] is sockets[1]
        assert not tasks[1].done()
    finally:
        await queues[1].put({"type": "websocket.disconnect", "code": 1000})
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


@pytest.mark.parametrize("code", ["data_unavailable", "data_relocating", "invalid_worldline"])
def test_voice_rejects_before_loading_database(app_client, monkeypatch, code):
    async def unexpected_load(self):
        pytest.fail("voice loaded history before checking admission")

    monkeypatch.setattr(chat_ws.SessionState, "load_from_db", unexpected_load)
    path = "/ws/voice?session_id=voice-admission"
    if code == "data_unavailable":
        monkeypatch.setattr(data_startup, "available", lambda: False)
    elif code == "data_relocating":
        app_client.portal.call(data_relocation.begin)
    else:
        path += "&worldline=unsupported"
    try:
        with app_client.websocket_connect(path) as ws:
            error = ws.receive_json()
            assert (error["type"], error["code"]) == ("voice.error", code)
            assert ws.receive()["type"] == "websocket.close"
    finally:
        if code == "data_relocating":
            app_client.portal.call(data_relocation.abort)


@pytest.mark.parametrize("frame", ["voice.start", "voice.commit", b"pcm"])
def test_voice_closes_on_unauthenticated_audio(app_client, frame):
    with app_client.websocket_connect("/ws/voice?session_id=voice-unauthenticated") as ws:
        if isinstance(frame, bytes):
            ws.send_bytes(frame)
        else:
            ws.send_json({"type": frame})
        error = ws.receive_json()
        assert (error["type"], error["code"]) == ("voice.error", "unauthorized")
        assert ws.receive()["type"] == "websocket.close"
