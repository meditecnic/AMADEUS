import sys
import os
import pytest
import asyncio
import copy
import threading
from unittest.mock import patch, AsyncMock

# Ensure backend root is in python path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.routers.chat_ws import sessions

# =====================================================================
# 1. MOCK SERVICE FIXTURES & IMPLEMENTATIONS
# =====================================================================

class MockDeepSeekService:
    def __init__(self):
        self.delay = 0.0

    async def get_chat_stream(
        self,
        active_history,
        memory_summary=None,
        api_key=None,
        system_prompt=None,
        temperature=0.7,
        tools=None,
        tool_choice=None,
        model=None,
        reasoning_effort=None,
    ):
        if self.delay > 0:
            await asyncio.sleep(self.delay)
        yield {"content": "[EMO:tsundere] あんたなんか、頼んでないわよ！", "tool_calls": None}
        yield {"content": "[EMO:surprised] え、本当に？信じられない…", "tool_calls": None}

    async def translate_to_zh(self, text, api_key=None):
        return "我才没有求你呢！我可不记得我是你的助手。"

    async def compress_history(self, history, api_key=None, old_summary=""):
        return "紅莉栖は助手の呼称を拒絶し、ツンデレな態度を取っている。"


class MockTTSQueueManager:
    def __init__(self):
        self.fail_tts = False
        self._callbacks = {}
        self._next_sequence = {}

    async def synthesize_async(self, text: str, emotion_tag: str, sovits_url: str = None) -> bytes:
        if self.fail_tts:
            # 返回 None 模拟降级
            return None
        return b"mocked_kurisu_audio_bytes"

    def reset_sequence(self, session_id: str, send_callback):
        self._callbacks[session_id] = send_callback
        self._next_sequence[session_id] = 0

    def cancel_pending(self, session_id: str):
        self._callbacks.pop(session_id, None)
        self._next_sequence.pop(session_id, None)

    async def gather_pending(self, session_id: str):
        return None

    async def _dispatch(self, session_id: str, payload: dict) -> int:
        sequence = self._next_sequence.get(session_id, 0)
        self._next_sequence[session_id] = sequence + 1
        callback = self._callbacks.get(session_id)
        if callback is not None:
            await callback(sequence, payload)
        return sequence

    async def queue_silent(self, session_id: str, text: str, emotion: str, reason: str) -> int:
        return await self._dispatch(
            session_id,
            {"audio": None, "error": reason, "text": text, "emotion": emotion},
        )

    async def synthesize_and_queue(
        self,
        session_id: str,
        text: str,
        emotion_tag: str,
        sovits_url: str = None,
    ) -> int:
        audio = await self.synthesize_async(text, emotion_tag, sovits_url=sovits_url)
        return await self._dispatch(
            session_id,
            {
                "audio": audio,
                "error": None if audio is not None else "service_error",
                "text": text,
                "emotion": emotion_tag,
            },
        )


@pytest.fixture(autouse=True)
def mock_lifespan_network():
    # 模拟 HTTPX AsyncClient 以免 main.py 中 lifespan 的测试连接超时
    with patch("app.services.tts_queue.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.get = AsyncMock()
        mock_client_cls.return_value = mock_client
        yield mock_client


@pytest.fixture
def mock_services():
    mock_ds = MockDeepSeekService()
    mock_tts = MockTTSQueueManager()
    with patch("app.routers.chat_ws.deepseek_service", mock_ds), \
         patch("app.routers.chat_ws.tts_manager", mock_tts):
        yield mock_ds, mock_tts


# =====================================================================
# 2. CONTRACT TEST CASES
# =====================================================================

class TestBackendE2EContract:

    @pytest.fixture(autouse=True)
    def managed_client(self, mock_lifespan_network, app_client):
        """Keep one application event loop alive for the whole test.

        WebSocket requests perform asynchronous SQLite work.  A bare
        ``TestClient`` tears its portal down as soon as the socket context
        exits, which can close the loop before aiosqlite's worker thread posts
        its final result.  The outer client context also exercises application
        shutdown deterministically.
        """
        self.client = app_client

    # -----------------------------------------------------------------
    # R2.1: WebSocket 首帧强制 Auth 认证契约
    # -----------------------------------------------------------------
    def test_ws_requires_auth_as_first_frame(self, mock_services):
        """连接后第一帧必须是 auth 帧，否则强行断开连接"""
        client = self.client
        with client.websocket_connect("/ws/chat?session_id=test_auth_fail") as websocket:
            websocket.send_json({"type": "chat", "content": "Hello?"})
            # 预期后端因第一帧不是 auth 而不主动断开，而是返回 error 帧
            response = websocket.receive_json()
            assert response["type"] == "error"
            assert "未认证" in response["message"]

    @pytest.mark.parametrize("auth_rejected", [False, True])
    def test_unauthenticated_worldline_switch_is_correlated_without_switching(
        self, mock_services, isolated_provider_credentials, monkeypatch, auth_rejected
    ):
        from app.services.session_manager import session_coordinator

        session_id = f"unauthenticated-switch-{auth_rejected}"
        switch = AsyncMock()
        monkeypatch.setattr(session_coordinator, "switch", switch)
        with self.client.websocket_connect(f"/ws/chat?session_id={session_id}") as websocket:
            if auth_rejected:
                isolated_provider_credentials.delete("deepseek")
                websocket.send_json({
                    "type": "auth",
                    "protocol_version": 2,
                    "worldline": "steins_gate",
                    "conversation_mode": "draft",
                })
                assert websocket.receive_json()["type"] == "error"
            session = sessions[session_id]
            before = (session.worldline, session.conversation_id, session.current_epoch)
            websocket.send_json({
                "type": "worldline.switch",
                "request_id": "switch-before-auth",
                "target_worldline": "beta",
                "conversation_mode": "draft",
            })
            error = websocket.receive_json()
            assert error["type"] == "worldline.switch_error"
            assert error["request_id"] == "switch-before-auth"
            assert error["code"] == "unauthenticated"
            assert (session.worldline, session.conversation_id, session.current_epoch) == before
            switch.assert_not_awaited()

    def test_ws_successful_auth_handshake(self, mock_services):
        """第一帧发送合法 auth 帧后，连接得以维持，可以正常聊天"""
        client = self.client
        with client.websocket_connect("/ws/chat?session_id=test_auth_success") as websocket:
            websocket.send_json({"type": "auth", "api_key": "test_valid_key"})
            assert websocket.receive_json()["type"] == "session.ready"
            websocket.send_json({"type": "chat", "content": "こんにちは"})
            
            first_response = websocket.receive_json()
            assert first_response["type"] == "status"
            assert first_response["dsk"] == "thinking"

    @pytest.mark.parametrize(
        ("conversation_mode", "worldline"),
        [("draft", "steins_gate"), ("history", "beta")],
    )
    def test_ws_auth_ready_once_then_duplicate_auth_error(
        self, mock_services, conversation_mode, worldline
    ):
        session_id = f"test_ready_{conversation_mode}_{worldline}"
        conversation_id = None
        if conversation_mode == "history":
            created = self.client.post(
                "/api/conversations",
                json={"session_id": session_id, "worldline": worldline},
            )
            assert created.status_code == 201
            conversation_id = created.json()["id"]

        with self.client.websocket_connect(f"/ws/chat?session_id={session_id}") as websocket:
            websocket.send_json({
                "type": "auth",
                "client": "desktop",
                "protocol_version": 2,
                "worldline": worldline,
                "conversation_mode": conversation_mode,
                "conversation_id": conversation_id,
            })
            websocket.send_json({"type": "auth"})

            assert websocket.receive_json() == {
                "type": "session.ready",
                "conversation_mode": conversation_mode,
                "conversation_id": conversation_id,
                "worldline": worldline,
                "generation": sessions[session_id].current_epoch,
            }
            duplicate = websocket.receive_json()
            assert duplicate["type"] == "error"
            assert "已完成认证" in duplicate["message"]

    def test_auth_selects_requested_conversation_before_loading_provider(self, mock_services):
        session_id = "test_auth_conversation_selection"
        created = self.client.post(
            "/api/conversations",
            json={"session_id": session_id, "worldline": "steins_gate"},
        )
        assert created.status_code == 201
        conversation_id = created.json()["id"]

        with self.client.websocket_connect(f"/ws/chat?session_id={session_id}") as websocket:
            websocket.send_json({
                "type": "auth",
                "client": "desktop",
                "protocol_version": 2,
                "worldline": "steins_gate",
                "conversation_id": conversation_id,
            })
            assert websocket.receive_json()["type"] == "session.ready"
            websocket.send_json({"type": "chat.send", "content": "selected"})
            response = websocket.receive_json()
            assert response["type"] == "turn.started"

        assert sessions[session_id].conversation_id == conversation_id

    def test_v2_draft_auth_materializes_a_new_conversation_on_first_send(self, mock_services):
        session_id = "test_draft_materialization"
        previous = self.client.post(
            "/api/conversations",
            json={"session_id": session_id, "worldline": "steins_gate", "title": "Previous"},
        ).json()

        with self.client.websocket_connect(f"/ws/chat?session_id={session_id}") as websocket:
            websocket.send_json({
                "type": "auth",
                "client": "desktop",
                "protocol_version": 2,
                "worldline": "steins_gate",
                "conversation_mode": "draft",
                "conversation_id": previous["id"],
            })
            assert websocket.receive_json()["type"] == "session.ready"
            websocket.send_json({"type": "chat.send", "content": "new draft"})
            started = websocket.receive_json()
            assert started["type"] == "turn.started"
            assert started["conversation_id"] != previous["id"]
            assert sessions[session_id].conversation_id == started["conversation_id"]

    def test_v2_draft_auth_can_explicitly_select_registered_provider_and_model(
        self,
        mock_services,
    ):
        """Draft auth must not silently inherit the previous DeepSeek selection."""
        from app.routers import chat_ws

        session_id = "test_draft_provider_override"
        chat_ws.credential_store.set("glm", "test-glm-credential")

        with self.client.websocket_connect(f"/ws/chat?session_id={session_id}") as websocket:
            websocket.send_json({
                "type": "auth",
                "client": "desktop",
                "protocol_version": 2,
                "worldline": "steins_gate",
                "conversation_mode": "draft",
                "provider_id": "glm",
                "model": "glm-5.2",
            })
            assert websocket.receive_json()["type"] == "session.ready"
            websocket.send_json({"type": "auth"})
            assert websocket.receive_json()["type"] == "error"

        assert sessions[session_id].provider_id == "glm"
        assert sessions[session_id].model == "glm-5.2"

    def test_v2_conversation_switch_returns_correlated_ack(self, mock_services):
        session_id = "test_conversation_switch_ack"
        first = self.client.post(
            "/api/conversations",
            json={"session_id": session_id, "worldline": "steins_gate", "title": "First"},
        ).json()
        second = self.client.post(
            "/api/conversations",
            json={"session_id": session_id, "worldline": "steins_gate", "title": "Second"},
        ).json()

        with self.client.websocket_connect(f"/ws/chat?session_id={session_id}") as websocket:
            websocket.send_json({
                "type": "auth",
                "client": "desktop",
                "protocol_version": 2,
                "worldline": "steins_gate",
                "conversation_id": first["id"],
            })
            assert websocket.receive_json()["type"] == "session.ready"
            websocket.send_json({
                "type": "conversation.switch",
                "request_id": "switch-1",
                "conversation_id": second["id"],
            })
            ack = websocket.receive_json()

        assert ack["type"] == "conversation.switched"
        assert ack["request_id"] == "switch-1"
        assert ack["conversation_id"] == second["id"]
        assert ack["worldline"] == "steins_gate"

    def test_invalid_conversation_switch_preserves_current_selection(self, mock_services):
        session_id = "test_invalid_conversation_switch"
        current = self.client.post(
            "/api/conversations",
            json={"session_id": session_id, "worldline": "steins_gate", "title": "Current"},
        ).json()

        with self.client.websocket_connect(f"/ws/chat?session_id={session_id}") as websocket:
            websocket.send_json({
                "type": "auth",
                "client": "desktop",
                "protocol_version": 2,
                "worldline": "steins_gate",
                "conversation_id": current["id"],
            })
            assert websocket.receive_json()["type"] == "session.ready"
            websocket.send_json({
                "type": "conversation.switch",
                "request_id": "invalid-1",
                "conversation_id": "not-owned",
            })
            error = websocket.receive_json()

        assert error["type"] == "conversation.switch_error"
        assert error["request_id"] == "invalid-1"
        assert sessions[session_id].conversation_id == current["id"]

    def test_conversation_switch_runtime_failure_is_correlated_and_sanitized(
        self,
        mock_services,
        monkeypatch,
    ):
        from app.services.conversations import conversation_service

        session_id = "test_conversation_switch_runtime_failure"
        target = self.client.post(
            "/api/conversations",
            json={"session_id": session_id, "worldline": "steins_gate"},
        ).json()

        with self.client.websocket_connect(f"/ws/chat?session_id={session_id}") as websocket:
            websocket.send_json({
                "type": "auth",
                "client": "desktop",
                "protocol_version": 2,
                "worldline": "steins_gate",
                "conversation_mode": "draft",
            })
            assert websocket.receive_json()["type"] == "session.ready"
            websocket.send_json({"type": "auth"})
            assert websocket.receive_json()["type"] == "error"
            monkeypatch.setattr(
                conversation_service,
                "select",
                AsyncMock(side_effect=RuntimeError("private database details")),
            )
            websocket.send_json({
                "type": "conversation.switch",
                "request_id": "switch-runtime-failure",
                "conversation_id": target["id"],
            })
            error = websocket.receive_json()

        assert error["type"] == "conversation.switch_error"
        assert error["request_id"] == "switch-runtime-failure"
        assert error["code"] == "conversation_switch_failed"
        assert "private database details" not in error["message"]

    def test_worldline_switch_runtime_failure_is_correlated_and_keeps_old_lease(
        self,
        mock_services,
        monkeypatch,
    ):
        from app.services.session_manager import session_coordinator

        session_id = "test_worldline_switch_runtime_failure"
        with self.client.websocket_connect(f"/ws/chat?session_id={session_id}") as websocket:
            websocket.send_json({
                "type": "auth",
                "client": "desktop",
                "protocol_version": 2,
                "worldline": "steins_gate",
            })
            assert websocket.receive_json()["type"] == "session.ready"
            monkeypatch.setattr(
                session_coordinator,
                "switch",
                AsyncMock(side_effect=RuntimeError("private switch details")),
            )
            websocket.send_json({
                "type": "worldline.switch",
                "request_id": "worldline-runtime-failure",
                "target_worldline": "beta",
                "conversation_mode": "draft",
            })
            error = websocket.receive_json()

            assert (session_id, "steins_gate") in session_coordinator._leases

        assert error["type"] == "worldline.switch_error"
        assert error["request_id"] == "worldline-runtime-failure"
        assert error["code"] == "worldline_switch_failed"
        assert "private switch details" not in error["message"]

    def test_v2_worldline_switch_enters_target_draft_without_reconnect(self, mock_services):
        session_id = "test_worldline_switch_ack"
        with self.client.websocket_connect(f"/ws/chat?session_id={session_id}") as websocket:
            websocket.send_json({
                "type": "auth",
                "client": "desktop",
                "protocol_version": 2,
                "worldline": "steins_gate",
            })
            assert websocket.receive_json()["type"] == "session.ready"
            websocket.send_json({
                "type": "worldline.switch",
                "request_id": "worldline-1",
                "target_worldline": "beta",
                "conversation_mode": "draft",
            })
            ack = websocket.receive_json()

        assert ack["type"] == "worldline.switched"
        assert ack["request_id"] == "worldline-1"
        assert ack["worldline"] == "beta"
        assert ack["conversation_mode"] == "draft"
        assert ack["conversation_id"] is None
        assert sessions[session_id].worldline == "beta"
        assert sessions[session_id].history == []

    def test_ws_missing_stored_credential_returns_error_without_policy_close(
        self,
        mock_services,
        isolated_provider_credentials,
    ):
        """Missing local credentials produce an error frame, not a policy close."""
        isolated_provider_credentials.delete("deepseek")
        client = self.client
        with client.websocket_connect("/ws/chat?session_id=test_auth_empty_key") as websocket:
            websocket.send_json({"type": "auth"})

            response = websocket.receive_json()
            assert response["type"] == "error"
            assert "api key" in response["message"].lower()

    # -----------------------------------------------------------------
    # R2.2: 连接存活时收到 auth 被忽略或返回 error (不挂断)
    # -----------------------------------------------------------------
    def test_ws_duplicate_auth_during_session(self, mock_services):
        """成功建立连接后，再次发送 auth 帧不挂断连接，而是忽略或返回错误"""
        client = self.client
        with client.websocket_connect("/ws/chat?session_id=test_dup_auth") as websocket:
            websocket.send_json({"type": "auth", "api_key": "my_key"})
            assert websocket.receive_json()["type"] == "session.ready"
            
            # 存活期间再次发送 auth
            websocket.send_json({"type": "auth", "api_key": "another_key"})
            resp = websocket.receive_json()
            assert resp["type"] == "error" or resp["type"] == "status"
            
            # 验证连接未挂断：发送聊天帧并验证后续响应
            websocket.send_json({"type": "chat", "content": "Are you still alive?"})
            chat_resp = websocket.receive_json()
            assert chat_resp["type"] == "status"

    # -----------------------------------------------------------------
    # R2.3: config_update 配置裁剪规则与 api_key 安全阻断
    # -----------------------------------------------------------------
    def test_config_update_temperature_clipping(self, mock_services):
        """config_update 传入越界 temperature 应被后端强制裁剪在 0.0-1.2 之间"""
        client = self.client
        with client.websocket_connect("/ws/chat?session_id=test_clip") as websocket:
            websocket.send_json({"type": "auth", "api_key": "my_key"})
            
            # 发送需要裁剪的过大 temperature (2.5) — product cap is 1.2
            websocket.send_json({
                "type": "config_update",
                "temperature": 2.5
            })
            websocket.send_json({"type": "chat", "content": "Go"})
            
            # 接收消息直到 done
            while True:
                r = websocket.receive_json()
                if r.get("type") == "status" and r.get("dsk") == "idle":
                    break
            
            session_state = sessions.get("test_clip")
            assert session_state is not None
            assert session_state.temperature == 1.2

    def test_config_update_api_key_is_ignored(self, mock_services):
        """WebSocket config frames cannot replace the credential-store secret."""
        client = self.client
        session_id = "test_allowed_key"
        with client.websocket_connect(f"/ws/chat?session_id={session_id}") as websocket:
            websocket.send_json({"type": "auth", "api_key": "my_key"})
            websocket.send_json({
                "type": "config_update",
                "temperature": 1.0,
                "api_key": "new_hot_key"
            })
            
            # 发送一轮对话以确保之前的 config_update 帧已在后端协程被彻底处理
            websocket.send_json({"type": "chat", "content": "ping"})
            while True:
                r = websocket.receive_json()
                if r.get("type") == "status" and r.get("dsk") == "idle":
                    break

            from app.routers.chat_ws import sessions
            session_state = sessions.get(session_id)
            assert session_state is not None
            assert session_state.api_key == "test-provider-credential"

            # 再次发送空 api_key 进行更新，应当忽略更新
            websocket.send_json({
                "type": "config_update",
                "api_key": ""
            })
            
            # 再跑一轮等待 done
            websocket.send_json({"type": "chat", "content": "ping_again"})
            while True:
                r = websocket.receive_json()
                if r.get("type") == "status" and r.get("dsk") == "idle":
                    break
                    
            assert session_state.api_key == "test-provider-credential"

    # -----------------------------------------------------------------
    # R2/R3.1: 后端状态快照深拷贝隔离
    # -----------------------------------------------------------------
    def test_session_state_snapshot_isolation(self, mock_services):
        """验证会话快照为深拷贝隔离，外部修改不会污染后端历史"""
        client = self.client
        session_id = "test_snapshot_id"
        
        with client.websocket_connect(f"/ws/chat?session_id={session_id}") as websocket:
            websocket.send_json({"type": "auth", "api_key": "my_key"})
            websocket.send_json({"type": "chat", "content": "hello"})
            
            # 接收消息直到 done
            while True:
                r = websocket.receive_json()
                if r.get("type") == "status" and r.get("dsk") == "idle":
                    break
                    
            # 获取后端存储 of history
            session_state = sessions.get(session_id)
            assert session_state is not None
            history = session_state.history
            assert len(history) > 0
            
            # 尝试直接修改获取到的 history 列表及其中字典的内容
            history.append({"role": "user", "content": "malicious"})
            history[0]["content"] = "mutated"
            
            # 重新获取后端存储的 history，验证其不受上述外部修改的影响
            fresh_history = session_state.history
            assert len(fresh_history) < len(history)
            assert fresh_history[0]["content"] == "hello"
            assert fresh_history[-1]["role"] == "assistant"

    # -----------------------------------------------------------------
    # R2/R3.2: 人设隔离（system_prompt 变更清空 history + 在飞请求忽略）
    # -----------------------------------------------------------------
    def test_system_prompt_update_isolation(self, mock_services):
        """system_prompt 更新时清空 history，在飞请求返回的结果不被追加到新历史中"""
        mock_ds, _ = mock_services
        client = self.client
        
        with client.websocket_connect("/ws/chat?session_id=test_inflight") as websocket:
            websocket.send_json({"type": "auth", "api_key": "my_key"})
            
            # 设置 DeepSeek 流延迟以模拟在飞状态
            mock_ds.delay = 0.2
            websocket.send_json({"type": "chat", "content": "Q1"})
            
            # 立即发送人设配置更新
            websocket.send_json({
                "type": "config_update",
                "system_prompt": "You are Kurisu, but formal."
            })
            
            # 等待 Q1 完成返回
            while True:
                r = websocket.receive_json()
                if r.get("type") == "status" and r.get("dsk") == "idle":
                    break
            
            # 验证最终的 session history 中不含 Q1 及其回答（已被 config_update 废弃并隔离）
            session_state = sessions.get("test_inflight")
            assert session_state is not None
            assert len(session_state.history) == 0

    def test_blank_system_prompt_does_not_drop_inflight_reply(self, mock_services):
        """Desktop settings saves send an empty prompt. That is not a persona change."""
        mock_ds, _ = mock_services
        mock_ds.delay = 0.2
        session_id = "test_blank_prompt_keeps_reply"
        with self.client.websocket_connect(f"/ws/chat?session_id={session_id}") as websocket:
            websocket.send_json({
                "type": "auth",
                "api_key": "my_key",
                "client": "desktop",
                "protocol_version": 2,
                "conversation_mode": "draft",
            })
            websocket.send_json({"type": "chat.send", "content": "新会话首问"})
            assert websocket.receive_json()["type"] == "session.ready"
            started = websocket.receive_json()
            assert started["type"] == "turn.started"
            conversation_id = started["conversation_id"]
            websocket.send_json({
                "type": "config_update",
                "system_prompt": "",
                "enable_tts": False,
            })
            while True:
                event = websocket.receive_json()
                if event.get("type") in {"turn.completed", "error"}:
                    break
            assert event["type"] == "turn.completed"

        rows = self.client.get(
            f"/api/conversations/{conversation_id}/messages",
            params={"session_id": session_id, "worldline": "steins_gate"},
        ).json()
        roles = [row["role"] for row in rows]
        assert roles[0] == "user"
        assert "assistant" in roles
        assert any("頼んでない" in row["content"] for row in rows if row["role"] == "assistant")
        assert not any("途中で止まった" in row["content"] for row in rows)

    def test_cancelled_turn_persists_visible_assistant_notice(self, mock_services):
        """A turn that saved the user row must not end with silence in history."""
        mock_ds, _ = mock_services

        async def cancel_after_user_save(*args, **kwargs):
            raise asyncio.CancelledError
            yield {"content": "", "tool_calls": None}

        mock_ds.get_chat_stream = cancel_after_user_save
        session_id = "test_cancel_leaves_notice"
        with self.client.websocket_connect(f"/ws/chat?session_id={session_id}") as websocket:
            websocket.send_json({
                "type": "auth",
                "api_key": "my_key",
                "client": "desktop",
                "protocol_version": 2,
                "conversation_mode": "draft",
            })
            websocket.send_json({"type": "chat.send", "content": "会中断的首问"})
            assert websocket.receive_json()["type"] == "session.ready"
            started = websocket.receive_json()
            assert started["type"] == "turn.started"
            conversation_id = started["conversation_id"]
            saw_cancelled = False
            while True:
                event = websocket.receive_json()
                if event.get("type") == "turn.cancelled":
                    saw_cancelled = True
                    break
                if event.get("type") == "error":
                    break
            assert saw_cancelled

        rows = self.client.get(
            f"/api/conversations/{conversation_id}/messages",
            params={"session_id": session_id, "worldline": "steins_gate"},
        ).json()
        assert any(row["role"] == "user" for row in rows)
        assert any(
            row["role"] == "assistant" and "途中で止まった" in row["content"]
            for row in rows
        ), rows

    # -----------------------------------------------------------------
    # R2/R3.3: 并发保护（忙碌时发送 chat 拒绝）
    # -----------------------------------------------------------------
    def test_concurrent_chat_rejected(self, mock_services):
        """当一个 chat 正在处理时，发送第二个 chat 将被拒绝并返回错误帧，且不干扰前一个生成"""
        mock_ds, _ = mock_services
        client = self.client
        
        with client.websocket_connect("/ws/chat?session_id=test_concurrency") as websocket:
            websocket.send_json({"type": "auth", "api_key": "my_key"})
            
            # 设定延迟，让第一次发送处于持续状态
            mock_ds.delay = 0.2
            websocket.send_json({"type": "chat", "content": "First message"})
            
            # 马上发送第二个消息
            websocket.send_json({"type": "chat", "content": "Second message"})
            
            responses = []
            for _ in range(5):
                responses.append(websocket.receive_json())

            # The first request is still running after the busy response.  Keep
            # the WebSocket portal alive until that request has completed so
            # its final database writes cannot target a closed event loop.
            while not any(
                response.get("type") == "status" and response.get("dsk") == "idle"
                for response in responses
            ):
                responses.append(websocket.receive_json())
                
            busy_error = [r for r in responses if r.get("type") == "error" and ("系统正在处理" in r.get("message", ""))]
            assert len(busy_error) == 1, "Should reject concurrent request with a busy/wait error"

    # -----------------------------------------------------------------
    # R2/R3.5: TTS 降级容灾
    # -----------------------------------------------------------------
    def test_tts_failure_degradation(self, mock_services):
        """当 TTS 出错或不可用时，系统发送 audio_degraded 帧并正常完成文本和翻译"""
        _, mock_tts = mock_services
        mock_tts.fail_tts = True
        
        client = self.client
        with client.websocket_connect("/ws/chat?session_id=test_tts_degrade") as websocket:
            # 发送合法 auth 帧以通过首帧校验
            websocket.send_json({"type": "auth", "api_key": "my_key"})
            websocket.send_json({"type": "chat", "content": "こんにちは"})
            
            responses = []
            while True:
                r = websocket.receive_json()
                responses.append(r)
                if r.get("type") == "status" and r.get("dsk") == "idle":
                    break
            
            # 断言收到 text_chunk 且 audio_degraded = True 且 degraded_reason 为 "service_error"
            text_chunks = [r for r in responses if r.get("type") == "text_chunk"]
            assert len(text_chunks) > 0
            for chunk in text_chunks:
                assert chunk.get("audio_degraded") is True
                assert chunk.get("degraded_reason") == "service_error"
                
            # 断言没有收到任何 audio_chunk
            audio_chunks = [r for r in responses if r.get("type") == "audio_chunk"]
            assert len(audio_chunks) == 0
            
            # 断言依然收到了 translation 翻译帧
            translations = [r for r in responses if r.get("type") == "translation"]
            assert len(translations) == 1

    # -----------------------------------------------------------------
    # R2.4: 废弃 / 过期 message 字段拒绝测试
    # -----------------------------------------------------------------
    def test_chat_deprecated_message_rejected(self, mock_services):
        """当发送带有废弃的 'message' 字段的 chat 帧时，后端返回报错拒绝且不断连"""
        client = self.client
        with client.websocket_connect("/ws/chat?session_id=test_deprecated_message") as websocket:
            websocket.send_json({"type": "auth", "api_key": "my_key"})
            assert websocket.receive_json()["type"] == "session.ready"
            
            websocket.send_json({
                "type": "chat",
                "message": "hello"
            })
            
            r = websocket.receive_json()
            assert r.get("type") == "error"
            assert "废弃" in r.get("message", "") or "禁止" in r.get("message", "")
            
            # 维持连接，可以继续用正确的 content 发送消息并成功响应
            websocket.send_json({
                "type": "chat",
                "content": "hello again"
            })
            r2 = websocket.receive_json()
            assert r2.get("type") == "status"
            assert r2.get("dsk") == "thinking"

    # -----------------------------------------------------------------
    # R2.5: 动态 TTS 用户禁用降级测试
    # -----------------------------------------------------------------
    def test_tts_user_disabled_degradation(self, mock_services):
        """流式过程中，用户发起 config_update 关闭 TTS，随后的句子全部以 user_disabled 形式降级"""
        mock_ds, _ = mock_services
        client = self.client
        with client.websocket_connect("/ws/chat?session_id=test_user_disable") as websocket:
            websocket.send_json({"type": "auth", "api_key": "my_key"})
            
            # 设定延时以允许我们在中途发送 config_update
            mock_ds.delay = 0.3
            websocket.send_json({"type": "chat", "content": "こんにちは"})
            
            # 稍等一下让任务启动并处于 active 状态，然后关闭 tts
            websocket.send_json({"type": "config_update", "enable_tts": False})
            
            responses = []
            while True:
                r = websocket.receive_json()
                responses.append(r)
                if r.get("type") == "status" and r.get("dsk") == "idle":
                    break
            
            text_chunks = [r for r in responses if r.get("type") == "text_chunk"]
            assert len(text_chunks) > 0
            for chunk in text_chunks:
                assert chunk.get("audio_degraded") is True
                assert chunk.get("degraded_reason") == "user_disabled"
                
            audio_chunks = [r for r in responses if r.get("type") == "audio_chunk"]
            assert len(audio_chunks) == 0

    # -----------------------------------------------------------------
    # R2.6: 9 情绪标签全量覆盖
    # -----------------------------------------------------------------
    def test_ws_allows_9_emotion_tags(self, mock_services):
        """验证后端 ALLOWED_EMOTIONS 包含全部 9 种情绪"""
        from app.routers.chat_ws import ALLOWED_EMOTIONS
        expected = {"neutral", "tsundere", "embarrassed", "intellectual",
                    "happy", "surprised", "annoyed", "disappointed", "sad"}
        assert ALLOWED_EMOTIONS == expected

    def test_v2_chat_uses_turn_and_segment_events_without_legacy_chunks(self, mock_services):
        client = self.client
        with client.websocket_connect("/ws/chat?session_id=test_protocol_v2") as websocket:
            websocket.send_json(
                {
                    "type": "auth",
                    "api_key": "my_key",
                    "client": "desktop",
                    "protocol_version": 2,
                }
            )
            websocket.send_json({"type": "chat.send", "content": "Hello v2"})

            responses = []
            while True:
                response = websocket.receive_json()
                responses.append(response)
                if response.get("type") in {"turn.completed", "error"}:
                    break

            event_types = [response.get("type") for response in responses]
            assert "turn.started" in event_types
            assert "segment.ready" in event_types
            assert "segment.audio" in event_types
            assert not {"text_chunk", "audio_chunk", "translation"}.intersection(event_types)
            segment = next(response for response in responses if response.get("type") == "segment.ready")
            assert segment["conversation_id"]
            assert segment["turn_id"]
            assert segment["segment_id"] == 0
            assert segment["ja"]
            assert segment["zh"]

    def test_v2_voice_stop_keeps_segment_text_and_suppresses_audio(self, mock_services):
        mock_ds, _ = mock_services
        mock_ds.delay = 0.1
        client = self.client
        with client.websocket_connect("/ws/chat?session_id=test_protocol_v2_voice_stop") as websocket:
            websocket.send_json(
                {"type": "auth", "api_key": "my_key", "client": "desktop", "protocol_version": 2}
            )
            websocket.send_json({"type": "chat.send", "content": "Stop voice"})
            websocket.send_json({"type": "voice.stop"})

            responses = []
            while True:
                response = websocket.receive_json()
                responses.append(response)
                if response.get("type") == "turn.completed":
                    break

            assert any(response.get("type") == "segment.ready" for response in responses)
            assert not any(response.get("type") == "segment.audio" for response in responses)
            assert any(
                response.get("type") == "segment.audio_error"
                and response.get("reason") == "voice_stopped"
                for response in responses
            )

    def test_v2_turn_cancel_emits_cancelled_even_before_pipeline_start(self, mock_services):
        mock_ds, _ = mock_services
        mock_ds.delay = 0.2
        client = self.client
        with client.websocket_connect("/ws/chat?session_id=test_protocol_v2_cancel") as websocket:
            websocket.send_json(
                {"type": "auth", "api_key": "my_key", "client": "desktop", "protocol_version": 2}
            )
            websocket.send_json({"type": "chat.send", "content": "Cancel turn"})
            websocket.send_json({"type": "turn.cancel"})

            responses = []
            while True:
                response = websocket.receive_json()
                responses.append(response)
                if response.get("type") in {"turn.cancelled", "turn.completed"}:
                    break

            assert responses[-1]["type"] == "turn.cancelled"
            assert responses[-1]["turn_id"]
            assert not any(response.get("type") == "segment.ready" for response in responses)

    def test_v2_emits_first_segment_before_model_stream_finishes(self, mock_services):
        release_second = threading.Event()

        class PausedStreamService:
            async def get_chat_stream(self, *args, **kwargs):
                yield {"content": "[EMO:neutral] 一文目。", "tool_calls": None}
                await asyncio.to_thread(release_second.wait)
                yield {"content": "二文目。", "tool_calls": None}

            async def translate_to_zh(self, text, api_key=None):
                return f"译:{text}"

            async def compress_history(self, history, api_key=None, old_summary=""):
                return ""

        with patch("app.routers.chat_ws.deepseek_service", PausedStreamService()):
            client = self.client
            with client.websocket_connect("/ws/chat?session_id=test_protocol_v2_incremental") as websocket:
                websocket.send_json(
                    {"type": "auth", "api_key": "my_key", "client": "desktop", "protocol_version": 2}
                )
                websocket.send_json({"type": "chat.send", "content": "Stream"})

                before_release = []
                while True:
                    response = websocket.receive_json()
                    before_release.append(response)
                    if response.get("type") == "segment.ready":
                        break

                assert before_release[-1]["segment_id"] == 0
                assert not any(response.get("type") == "turn.completed" for response in before_release)

                release_second.set()
                after_release = []
                while True:
                    response = websocket.receive_json()
                    after_release.append(response)
                    if response.get("type") == "turn.completed":
                        break

                assert [
                    response["segment_id"]
                    for response in after_release
                    if response.get("type") == "segment.ready"
                ] == [1]
