from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from starlette.websockets import WebSocketDisconnect

from app.services.session_manager import SessionCoordinator


@pytest.mark.parametrize("kind", ["chat", "voice"])
async def test_same_worldline_replaces_connection_without_closing_itself(kind):
    coordinator = SessionCoordinator()
    old, new = AsyncMock(), AsyncMock()
    await coordinator.acquire_lease("same", "steins_gate", old, kind=kind)
    await coordinator.acquire_lease("same", "steins_gate", old, kind=kind)
    old.close.assert_not_awaited()
    await coordinator.acquire_lease("same", "steins_gate", new, kind=kind)
    old.close.assert_awaited_once_with(code=4409, reason="session connection replaced")
    await coordinator.release_lease("same", "steins_gate", old, kind=kind)
    assert coordinator._leases[("same", kind, "steins_gate")] is new
    await coordinator.acquire_lease("same", "steins_gate", new, kind=kind)
    new.close.assert_not_awaited()


@pytest.mark.timeout(10)
def test_voice_connect_does_not_kick_chat(app_client):
    session_id = f"lease-kinds-{uuid4()}"
    with app_client.websocket_connect(f"/ws/chat?session_id={session_id}") as chat:
        chat.send_json({"type": "auth", "protocol_version": 2, "enable_tts": False})
        assert chat.receive_json()["type"] == "session.ready"
        with app_client.websocket_connect(
            f"/ws/voice?session_id={session_id}&worldline=steins_gate"
        ) as voice:
            voice.send_json({"type": "auth"})
            assert voice.receive_json()["type"] == "voice.ready"
            # A request/reply on each socket proves both transports remain usable.
            chat.send_json({"type": "auth"})
            assert chat.receive_json() == {"type": "error", "message": "已完成认证，请勿重复操作"}
            voice.send_json({"type": "ping"})
            assert voice.receive_json() == {"type": "pong"}


@pytest.mark.timeout(10)
@pytest.mark.parametrize("kind", ["chat", "voice"])
def test_second_socket_replaces_only_same_kind(app_client, kind):
    session_id = f"replace-{kind}-{uuid4()}"
    with app_client.websocket_connect(f"/ws/{kind}?session_id={session_id}") as old:
        old.send_json({"type": "auth", "enable_tts": False})
        assert old.receive_json()["type"] == ("session.ready" if kind == "chat" else "voice.ready")
        with app_client.websocket_connect(f"/ws/{kind}?session_id={session_id}") as new:
            new.send_json({"type": "auth", "enable_tts": False})
            assert new.receive_json()["type"] == ("session.ready" if kind == "chat" else "voice.ready")
            with pytest.raises(WebSocketDisconnect) as replaced:
                old.receive_json()
            assert replaced.value.code == 4409
            assert replaced.value.reason == "session connection replaced"


@pytest.mark.parametrize("kind", ["chat", "voice"])
async def test_worldline_replacement_keeps_other_kind_lease(kind):
    coordinator = SessionCoordinator()
    old, other, new = AsyncMock(), AsyncMock(), AsyncMock()
    other_kind = "voice" if kind == "chat" else "chat"
    await coordinator.acquire_lease("same", "steins_gate", old, kind=kind)
    await coordinator.acquire_lease("same", "steins_gate", other, kind=other_kind)
    await coordinator.acquire_lease("same", "beta", new, kind=kind)
    old.close.assert_awaited_once_with(code=4409, reason="session connection replaced")
    other.close.assert_not_awaited()
    assert coordinator._leases == {
        ("same", kind, "beta"): new, ("same", other_kind, "steins_gate"): other,
    }
