import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from app import models
from app.routers.chat_ws import sessions


class _ScriptedDeepSeek:
    def __init__(self, replies: list[str]):
        self._replies = list(replies)

    async def get_chat_stream(self, active_history, **kwargs):
        reply = self._replies.pop(0)
        yield {"content": reply, "tool_calls": None}

    async def translate_to_zh(self, text, api_key=None, **kwargs):
        return "译文"


class _SilentTTS:
    async def synthesize_async(self, text: str, emotion_tag: str, sovits_url=None, **kwargs) -> bytes:
        return b"mock_audio"

    def cancel_pending(self, key: str) -> None:
        return None


def _send_turn(ws, content: str) -> list[dict]:
    ws.send_json({"type": "chat.send", "content": content})
    frames = []
    while True:
        frame = ws.receive_json()
        frames.append(frame)
        if frame["type"] == "error" or (
            frame["type"] == "conversation.turn_state" and not frame["running"] and not frame["queued"]
        ):
            return frames


def _create_conv(client, session_id: str, worldline: str = "steins_gate") -> str:
    resp = client.post("/api/conversations", json={"session_id": session_id, "worldline": worldline})
    assert resp.status_code == 201
    return resp.json()["id"]


@pytest.fixture(autouse=True)
def _mock_lifespan_http():
    with patch("app.services.tts_queue.httpx.AsyncClient") as cls:
        inst = AsyncMock()
        inst.get = AsyncMock()
        cls.return_value = inst
        yield


def test_switch_reconnect_and_draft_restore_conversation_emotion(app_client, isolated_provider_credentials):
    isolated_provider_credentials.set("deepseek", "sk-test-272")
    sid = "emo-272-switch"
    conv_a = _create_conv(app_client, sid)
    conv_b = _create_conv(app_client, sid)
    conv_empty = _create_conv(app_client, sid)
    conv_unknown = _create_conv(app_client, sid)

    asyncio.run(models.save_message(sid, "assistant", "[EMO:sad] かなしいわ。", conversation_id=conv_a))
    asyncio.run(models.save_message(sid, "assistant", "[EMO:happy] うれしいわ！", conversation_id=conv_b))
    asyncio.run(models.save_message(sid, "user", "続けて", conversation_id=conv_b))
    asyncio.run(models.save_message(sid, "assistant", "[EMO:bogus] 不明なタグ。", conversation_id=conv_unknown))

    with app_client.websocket_connect(f"/ws/chat?session_id={sid}") as ws:
        ws.send_json({
            "type": "auth", "client": "desktop", "protocol_version": 2,
            "worldline": "steins_gate", "conversation_mode": "history", "conversation_id": conv_a,
        })
        ready = ws.receive_json()
        assert ready["type"] == "session.ready"
        assert ready["emotion"] == "sad"
        assert sessions[sid].last_assistant_emotion == "sad"

        ws.send_json({"type": "conversation.switch", "request_id": "r1", "conversation_id": conv_b})
        sw_b = ws.receive_json()
        assert sw_b["type"] == "conversation.switched"
        assert sw_b["emotion"] == "happy"
        assert sessions[sid].last_assistant_emotion == "happy"

        ws.send_json({"type": "conversation.switch", "request_id": "r2", "conversation_id": conv_a})
        sw_a = ws.receive_json()
        assert sw_a["emotion"] == "sad"
        assert sessions[sid].last_assistant_emotion == "sad"

        ws.send_json({"type": "conversation.switch", "request_id": "r3", "conversation_id": conv_empty})
        assert ws.receive_json()["emotion"] == "neutral"
        assert sessions[sid].last_assistant_emotion == "neutral"

        ws.send_json({"type": "conversation.switch", "request_id": "r4", "conversation_id": conv_unknown})
        assert ws.receive_json()["emotion"] == "neutral"
        assert sessions[sid].last_assistant_emotion == "neutral"

        ws.send_json({"type": "worldline.switch", "request_id": "w1", "target_worldline": "beta", "conversation_mode": "draft"})
        wl_ack = ws.receive_json()
        assert wl_ack["type"] == "worldline.switched"
        assert wl_ack["emotion"] == "neutral"
        assert sessions[sid].last_assistant_emotion == "neutral"

    # Reconnect in draft mode stays neutral, reconnect into conv_b restores happy even on fresh SessionState.
    sessions.pop(sid, None)
    with app_client.websocket_connect(f"/ws/chat?session_id={sid}") as ws:
        ws.send_json({
            "type": "auth", "client": "desktop", "protocol_version": 2,
            "worldline": "steins_gate", "conversation_mode": "draft",
        })
        assert ws.receive_json()["emotion"] == "neutral"

    sessions.pop(sid, None)
    with app_client.websocket_connect(f"/ws/chat?session_id={sid}") as ws:
        ws.send_json({
            "type": "auth", "client": "desktop", "protocol_version": 2,
            "worldline": "steins_gate", "conversation_mode": "history", "conversation_id": conv_b,
        })
        assert ws.receive_json()["emotion"] == "happy"
        assert sessions[sid].last_assistant_emotion == "happy"


def test_missing_emo_tag_never_leaks_across_conversations_and_resets_counter(
    app_client, isolated_provider_credentials
):
    isolated_provider_credentials.set("deepseek", "sk-test-272")
    sid = "emo-272-no-leak"
    conv_a = _create_conv(app_client, sid)
    conv_b_tagged = _create_conv(app_client, sid)
    conv_c_empty = _create_conv(app_client, sid)

    asyncio.run(models.save_message(sid, "assistant", "[EMO:sad] ちょっと疲れたわ。", conversation_id=conv_a))
    asyncio.run(models.save_message(sid, "assistant", "[EMO:happy] 実験成功よ！", conversation_id=conv_b_tagged))

    fake_ds = _ScriptedDeepSeek([
        "タグなしの返信よ。",       # A miss #1 -> keeps A's sad, counter=1
        "次もタグなしよ。",         # B miss #1 after switch -> counter reset, keeps B's happy (not neutral, not sad)
        "さらにタグなしよ。",       # B miss #2 in same conv -> decays last_assistant_emotion to neutral
        "空の会話でタグなしよ。",   # C (empty) miss #1 -> neutral (never inherits A or B)
    ])
    with patch("app.routers.chat_ws.deepseek_service", fake_ds), \
         patch("app.routers.chat_ws.tts_manager", _SilentTTS()):
        with app_client.websocket_connect(f"/ws/chat?session_id={sid}") as ws:
            ws.send_json({
                "type": "auth", "client": "desktop", "protocol_version": 2,
                "worldline": "steins_gate", "conversation_mode": "history", "conversation_id": conv_a,
            })
            assert ws.receive_json()["emotion"] == "sad"

            # A misses tag once -> keeps sad, consecutive_no_emo_tags becomes 1
            frames_a = _send_turn(ws, "大丈夫？")
            seg_a = next(f for f in frames_a if f.get("type") == "segment.ready")
            assert seg_a["emotion"] == "sad"
            assert sessions[sid].consecutive_no_emo_tags == 1

            # Switch to B (tagged happy): counter must reset to 0 and default emotion must be happy.
            ws.send_json({"type": "conversation.switch", "request_id": "s-b", "conversation_id": conv_b_tagged})
            assert ws.receive_json()["emotion"] == "happy"
            assert sessions[sid].consecutive_no_emo_tags == 0

            # B misses tag once: keeps B's own happy, does not decay to neutral.
            frames_b1 = _send_turn(ws, "すごいな")
            seg_b1 = next(f for f in frames_b1 if f.get("type") == "segment.ready")
            assert seg_b1["emotion"] == "happy"
            assert sessions[sid].last_assistant_emotion == "happy"
            assert sessions[sid].consecutive_no_emo_tags == 1

            # Second consecutive miss within B decays to neutral for the next turn.
            _send_turn(ws, "それで？")
            assert sessions[sid].last_assistant_emotion == "neutral"
            assert sessions[sid].consecutive_no_emo_tags == 2

            # Switch back to A (sad) then immediately to empty C: C misses tag -> neutral, not sad.
            ws.send_json({"type": "conversation.switch", "request_id": "s-a", "conversation_id": conv_a})
            assert ws.receive_json()["emotion"] == "sad"
            ws.send_json({"type": "conversation.switch", "request_id": "s-c", "conversation_id": conv_c_empty})
            assert ws.receive_json()["emotion"] == "neutral"
            frames_c = _send_turn(ws, "はじめまして")
            seg_c = next(f for f in frames_c if f.get("type") == "segment.ready")
            assert seg_c["emotion"] == "neutral"
