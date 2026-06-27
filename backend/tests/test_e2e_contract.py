import sys
import os
import pytest
import asyncio
import copy
from unittest.mock import patch, AsyncMock
from fastapi.testclient import TestClient

# Ensure backend root is in python path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.main import app
from app.routers.chat_ws import sessions

# =====================================================================
# 1. MOCK SERVICE FIXTURES & IMPLEMENTATIONS
# =====================================================================

class MockDeepSeekService:
    def __init__(self):
        self.delay = 0.0

    async def get_chat_stream(self, active_history, memory_summary=None, api_key=None, system_prompt=None, temperature=0.7):
        if self.delay > 0:
            await asyncio.sleep(self.delay)
        yield "[EMO:tsundere] あんたなんか、頼んでないわよ！"
        yield " 助手的覚えはないわ。"

    async def translate_to_zh(self, text, api_key=None):
        return "我才没有求你呢！我可不记得我是你的助手。"

    async def compress_history(self, history, api_key=None):
        return "紅莉栖は助手の呼称を拒絶し、ツンデレな態度を取っている。"


class MockTTSQueueManager:
    def __init__(self):
        self.fail_tts = False

    async def synthesize_async(self, text: str, emotion_tag: str, sovits_url: str = None) -> bytes:
        if self.fail_tts:
            # 返回 None 模拟降级
            return None
        return b"mocked_kurisu_audio_bytes"


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

    # -----------------------------------------------------------------
    # R2.1: WebSocket 首帧强制 Auth 认证契约
    # -----------------------------------------------------------------
    def test_ws_requires_auth_as_first_frame(self, mock_services):
        """连接后第一帧必须是 auth 帧，否则强行断开连接"""
        client = TestClient(app)
        with client.websocket_connect("/ws/chat?session_id=test_auth_fail") as websocket:
            websocket.send_json({"type": "chat", "content": "Hello?"})
            # 预期后端因第一帧不是 auth 而不主动断开，而是返回 error 帧
            response = websocket.receive_json()
            assert response["type"] == "error"
            assert "未认证" in response["message"]

    def test_ws_successful_auth_handshake(self, mock_services):
        """第一帧发送合法 auth 帧后，连接得以维持，可以正常聊天"""
        client = TestClient(app)
        with client.websocket_connect("/ws/chat?session_id=test_auth_success") as websocket:
            websocket.send_json({"type": "auth", "api_key": "test_valid_key"})
            websocket.send_json({"type": "chat", "content": "こんにちは"})
            
            first_response = websocket.receive_json()
            assert first_response["type"] == "status"
            assert first_response["state"] == "thinking"

    def test_ws_empty_api_key_auth_returns_error_without_policy_close(self, mock_services):
        """Empty auth credentials should be reported as an error frame, not a 1008 policy close."""
        client = TestClient(app)
        with client.websocket_connect("/ws/chat?session_id=test_auth_empty_key") as websocket:
            websocket.send_json({"type": "auth", "api_key": ""})

            response = websocket.receive_json()
            assert response["type"] == "error"
            assert "api key" in response["message"].lower()

    # -----------------------------------------------------------------
    # R2.2: 连接存活时收到 auth 被忽略或返回 error (不挂断)
    # -----------------------------------------------------------------
    def test_ws_duplicate_auth_during_session(self, mock_services):
        """成功建立连接后，再次发送 auth 帧不挂断连接，而是忽略或返回错误"""
        client = TestClient(app)
        with client.websocket_connect("/ws/chat?session_id=test_dup_auth") as websocket:
            websocket.send_json({"type": "auth", "api_key": "my_key"})
            
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
        """config_update 传入越界 temperature 应被后端强制裁剪在 0.0-2.0 之间"""
        client = TestClient(app)
        with client.websocket_connect("/ws/chat?session_id=test_clip") as websocket:
            websocket.send_json({"type": "auth", "api_key": "my_key"})
            
            # 发送需要裁剪的过大 temperature (2.5)
            websocket.send_json({
                "type": "config_update",
                "temperature": 2.5
            })
            websocket.send_json({"type": "chat", "content": "Go"})
            
            # 接收消息直到 done
            while True:
                r = websocket.receive_json()
                if r.get("type") == "status" and r.get("state") == "done":
                    break
            
            # 此处应断言后台保存的已裁剪的 temperature 属性为 2.0
            session_state = sessions.get("test_clip")
            assert session_state is not None
            assert session_state.temperature == 2.0

    def test_config_update_api_key_forbidden(self, mock_services):
        """config_update 帧中禁止含有 api_key 字段，否则拒绝更新并返回 error 帧"""
        client = TestClient(app)
        with client.websocket_connect("/ws/chat?session_id=test_forbidden") as websocket:
            websocket.send_json({"type": "auth", "api_key": "my_key"})
            websocket.send_json({
                "type": "config_update",
                "temperature": 1.0,
                "api_key": "malicious_new_key"
            })
            
            error_response = websocket.receive_json()
            assert error_response["type"] == "error"
            assert "api key" in error_response["message"].lower()

    # -----------------------------------------------------------------
    # R2/R3.1: 后端状态快照深拷贝隔离
    # -----------------------------------------------------------------
    def test_session_state_snapshot_isolation(self, mock_services):
        """验证会话快照为深拷贝隔离，外部修改不会污染后端历史"""
        client = TestClient(app)
        session_id = "test_snapshot_id"
        
        with client.websocket_connect(f"/ws/chat?session_id={session_id}") as websocket:
            websocket.send_json({"type": "auth", "api_key": "my_key"})
            websocket.send_json({"type": "chat", "content": "hello"})
            
            # 接收消息直到 done
            while True:
                r = websocket.receive_json()
                if r.get("type") == "status" and r.get("state") == "done":
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
        client = TestClient(app)
        
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
                if r.get("type") == "status" and r.get("state") == "done":
                    break
            
            # 验证最终的 session history 中不含 Q1 及其回答（已被 config_update 废弃并隔离）
            session_state = sessions.get("test_inflight")
            assert session_state is not None
            assert len(session_state.history) == 0

    # -----------------------------------------------------------------
    # R2/R3.3: 并发保护（忙碌时发送 chat 拒绝）
    # -----------------------------------------------------------------
    def test_concurrent_chat_rejected(self, mock_services):
        """当一个 chat 正在处理时，发送第二个 chat 将被拒绝并返回错误帧，且不干扰前一个生成"""
        mock_ds, _ = mock_services
        client = TestClient(app)
        
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
                
            busy_error = [r for r in responses if r.get("type") == "error" and ("系统正在处理" in r.get("message", ""))]
            assert len(busy_error) == 1, "Should reject concurrent request with a busy/wait error"

    # -----------------------------------------------------------------
    # R2/R3.5: TTS 降级容灾
    # -----------------------------------------------------------------
    def test_tts_failure_degradation(self, mock_services):
        """当 TTS 出错或不可用时，系统发送 audio_degraded 帧并正常完成文本和翻译"""
        _, mock_tts = mock_services
        mock_tts.fail_tts = True
        
        client = TestClient(app)
        with client.websocket_connect("/ws/chat?session_id=test_tts_degrade") as websocket:
            # 发送合法 auth 帧以通过首帧校验
            websocket.send_json({"type": "auth", "api_key": "my_key"})
            websocket.send_json({"type": "chat", "content": "こんにちは"})
            
            responses = []
            while True:
                r = websocket.receive_json()
                responses.append(r)
                if r.get("type") == "status" and r.get("state") == "done":
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
        client = TestClient(app)
        with client.websocket_connect("/ws/chat?session_id=test_deprecated_message") as websocket:
            websocket.send_json({"type": "auth", "api_key": "my_key"})
            
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
            assert r2.get("state") == "thinking"

    # -----------------------------------------------------------------
    # R2.5: 动态 TTS 用户禁用降级测试
    # -----------------------------------------------------------------
    def test_tts_user_disabled_degradation(self, mock_services):
        """流式过程中，用户发起 config_update 关闭 TTS，随后的句子全部以 user_disabled 形式降级"""
        mock_ds, _ = mock_services
        client = TestClient(app)
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
                if r.get("type") == "status" and r.get("state") == "done":
                    break
            
            text_chunks = [r for r in responses if r.get("type") == "text_chunk"]
            assert len(text_chunks) > 0
            for chunk in text_chunks:
                assert chunk.get("audio_degraded") is True
                assert chunk.get("degraded_reason") == "user_disabled"
                
            audio_chunks = [r for r in responses if r.get("type") == "audio_chunk"]
            assert len(audio_chunks) == 0
