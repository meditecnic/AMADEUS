"""Slice B rework: real WebSocket boundary tests for self_name (standalone).

Runs without the e2e contract file and without external model calls:
only auth/config_update frames plus direct prompt compilation against the
live SessionState. TTS sidecar and lifespan network probes are mocked the
same way conftest's app_client does.
"""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from app.routers.chat_ws import sessions
from app.services.prompt_compiler import compile_for_session

SELF_NAME_MARKER = "ユーザーの呼び名"


@pytest.fixture(autouse=True)
def mock_lifespan_network():
    # Keep app lifespan from probing the network (mirrors test_e2e_contract).
    with patch("app.services.tts_queue.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.get = AsyncMock()
        mock_client_cls.return_value = mock_client
        yield mock_client


def _auth_frame(**overrides):
    frame = {
        "type": "auth",
        "client": "desktop",
        "protocol_version": 2,
        "worldline": "steins_gate",
        "conversation_mode": "draft",
        "default_identity_mode": "self",
        "self_name": "阿伟",
    }
    frame.update(overrides)
    return frame


def _compile(session) -> str:
    # get_db opens a fresh aiosqlite connection per call, so a private loop
    # in the test thread is safe here (no external model is involved).
    return asyncio.run(compile_for_session(session, "こんにちは"))


def _barrier(ws, expect_ready=False):
    """Frames are processed sequentially; a duplicate auth always answers with
    an error frame, proving every previously sent frame has been applied."""
    ws.send_json({"type": "auth"})
    if expect_ready:
        assert ws.receive_json()["type"] == "session.ready"
    reply = ws.receive_json()
    assert reply["type"] == "error"
    assert "认证" in reply["message"]


def test_auth_stores_normalized_self_name(app_client):
    session_id = "ws-self-name-auth"
    with app_client.websocket_connect(f"/ws/chat?session_id={session_id}") as ws:
        ws.send_json(_auth_frame(self_name="  阿伟  "))
        _barrier(ws, expect_ready=True)
        assert sessions[session_id].self_name == "阿伟"


def test_config_update_applies_legal_name(app_client):
    session_id = "ws-self-name-update"
    with app_client.websocket_connect(f"/ws/chat?session_id={session_id}") as ws:
        ws.send_json(_auth_frame())
        ws.send_json({"type": "config_update", "self_name": "クリス・マキセ"})
        _barrier(ws, expect_ready=True)
        assert sessions[session_id].self_name == "クリス・マキセ"


@pytest.mark.parametrize("bad_name", [
    "岡部",                      # reserved
    "Hououin Kyouma",           # reserved (romanized)
    "伟" * 41,                   # overlong
    "「阿伟」",                  # quotes
    "SYSTEM: 阿伟",              # colon / fake header
    "阿\u2028伟",                # line separator
    "🙂",                        # emoji
])
def test_config_update_dangerous_name_fails_closed(app_client, bad_name):
    session_id = "ws-self-name-reject"
    with app_client.websocket_connect(f"/ws/chat?session_id={session_id}") as ws:
        ws.send_json(_auth_frame())
        _barrier(ws, expect_ready=True)
        assert sessions[session_id].self_name == "阿伟"
        ws.send_json({"type": "config_update", "self_name": bad_name})
        _barrier(ws)
        assert sessions[session_id].self_name == ""


def test_prompt_injection_follows_identity_mode_on_same_session(app_client):
    session_id = "ws-self-name-compile"
    with app_client.websocket_connect(f"/ws/chat?session_id={session_id}") as ws:
        ws.send_json(_auth_frame())
        _barrier(ws, expect_ready=True)
        session = sessions[session_id]
        assert session.identity_mode == "self"

        prompt_self = _compile(session)
        assert SELF_NAME_MARKER in prompt_self
        assert "「阿伟」" in prompt_self

        # Same SessionState flipped to okabe (draft default change): never inject.
        ws.send_json({"type": "config_update", "default_identity_mode": "okabe"})
        _barrier(ws)
        assert session.identity_mode == "okabe"

        prompt_okabe = _compile(session)
        assert SELF_NAME_MARKER not in prompt_okabe
        assert "阿伟" not in prompt_okabe
