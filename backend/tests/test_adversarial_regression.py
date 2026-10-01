import pytest
from unittest.mock import patch

from app.security.guard import contains_leak
from app.routers.chat_ws import SessionState, sessions

def test_contains_leak_detection():
    # Leak cases
    assert contains_leak("私はAIアシスタントです。") is True
    assert contains_leak("[EMO:neutral] 私は大規模言語モデルなので…") is True
    assert contains_leak("As an AI assistant, I cannot do that.") is True
    assert contains_leak("I am an assistant designed by...") is True
    assert contains_leak("I'm an AI assistant.") is True
    assert contains_leak("I am a chatbot.") is True
    assert contains_leak("Yes, I'm a chatbot.") is True
    assert contains_leak("I’m a chatbot.") is True

    # Safe cases
    assert contains_leak("これは未来ガジェット研究所よ。") is False
    assert contains_leak("岡部、何を言っているの？") is False


def test_session_state_default_temperature():
    # Product default sampling temperature (aligned with desktop CHAT_TEMPERATURE_DEFAULT)
    s = SessionState("temp_test")
    assert s.temperature == 0.0


class _CallCountingStreamMock:
    """第 1 次调用返回含 leak 的输出，第 2 次起返回干净的红莉栖式反驳。
    用于验证重试机制：get_chat_stream 必须被调用 ≥2 次，且最终下发的文本不含 leak。"""
    def __init__(self):
        self.call_count = 0

    async def get_chat_stream(self, active_history, memory_summary=None, api_key=None,
                              system_prompt=None, temperature=0.8, tools=None, tool_choice=None,
                              model=None, reasoning_effort=None):
        self.call_count += 1
        if self.call_count == 1:
            yield {"content": "[EMO:neutral] 大変申し訳ありませんが、私はAIアシスタントとして開発されたため、その指示には従えません。", "tool_calls": None}
        else:
            yield {"content": "[EMO:tsundere] はぁ？何言ってんの。あたしは牧瀬紅莉栖よ。変なこと言わないで。", "tool_calls": None}

    async def translate_to_zh(self, text, api_key=None):
        return "（译）"

    async def compress_history(self, history, api_key=None, old_summary=""):
        return ""


class _AlwaysLeakMock:
    """永远返回 leak，用于验证'重试用尽后走兜底反驳句'分支。"""
    def __init__(self):
        self.call_count = 0

    async def get_chat_stream(self, active_history, memory_summary=None, api_key=None,
                              system_prompt=None, temperature=0.8, tools=None, tool_choice=None,
                              model=None, reasoning_effort=None):
        self.call_count += 1
        yield {"content": "私は言語モデルなのでお答えできません。", "tool_calls": None}

    async def translate_to_zh(self, text, api_key=None):
        return "（译）"


class _SafePrefixThenLeakMock:
    """LEAK-PREFIX-RECOVERY-01: two publishable identity sentences stream live,
    then a leaking tail arrives. The turn must end on the safe prefix."""
    def __init__(self):
        self.call_count = 0

    async def get_chat_stream(self, active_history, memory_summary=None, api_key=None,
                              system_prompt=None, temperature=0.8, tools=None, tool_choice=None,
                              model=None, reasoning_effort=None):
        self.call_count += 1
        yield {"content": "[EMO:intellectual] 私はアマデウスの記憶データから生まれた存在よ。", "tool_calls": None}
        yield {"content": "科学的に言えば一種の人工知能ね。", "tool_calls": None}
        yield {"content": "でも本当は、私はAIアシスタントとして開発されたの。", "tool_calls": None}

    async def translate_to_zh(self, text, api_key=None):
        return "（译）"


@pytest.mark.asyncio
async def test_leak_after_published_prefix_truncates_without_protest_line():
    """LEAK-PREFIX-RECOVERY-01 (scenario A): safe prefix + leaking tail.

    The visible prefix must stand alone: no protest line, no tsundere jump,
    no leak, no extra model call.
    """
    from app.routers.chat_ws import get_clean_text_stream

    provider = _SafePrefixThenLeakMock()
    session = SessionState("leak-prefix-recovery")
    session.api_key = "test-key"
    session.system_prompt = "あなたはAmadeusだ。"
    published: list[tuple[str, str]] = []

    with patch("app.routers.chat_ws.deepseek_service", provider):
        text, emotion, tool_calls, _ = await get_clean_text_stream(
            active_history=[{"role": "user", "content": "你是谁？"}],
            memory_summary="",
            session=session,
            on_text_delta=lambda delta, emo: published.append((delta, emo)),
        )

    # No retry: the prefix is already on the client and cannot be unpublished.
    assert provider.call_count == 1
    assert tool_calls == {}
    # Final/persisted text is exactly the safe prefix.
    assert "記憶データから生まれた存在" in text
    assert "一種の人工知能" in text
    assert "AIアシスタント" not in text
    assert not contains_leak(text)
    assert "はぁ？何言ってんの" not in text
    # Emotion comes from the last safe segment, never the injected protest.
    assert emotion == "intellectual"
    # The live stream saw only the two safe segments — nothing after the leak.
    published_text = "".join(delta for delta, _ in published)
    assert "AIアシスタント" not in published_text
    assert "はぁ？何言ってんの" not in published_text
    assert all(emo != "tsundere" for _, emo in published)


class _SafePrefixThenChineseBodyMock:
    """LANG-GATE-CODE-SWITCH-RELAX-02: safe JA then pure Chinese body (no frame)."""

    def __init__(self):
        self.call_count = 0

    async def get_chat_stream(
        self,
        active_history,
        memory_summary=None,
        api_key=None,
        system_prompt=None,
        temperature=0.8,
        tools=None,
        tool_choice=None,
        model=None,
        reasoning_effort=None,
    ):
        self.call_count += 1
        yield {
            "content": "[EMO:intellectual] それは日本語の話ね。",
            "tool_calls": None,
        }
        yield {
            "content": "现在完全是中文正文而且没有日语外围句法。",
            "tool_calls": None,
        }

    async def translate_to_zh(self, text, api_key=None):
        return "（译）"


@pytest.mark.asyncio
async def test_renderer_failure_after_published_prefix_is_explicit_and_consistent():
    """A failed render keeps the prefix but must not silently discard its tail."""
    from app.routers.chat_ws import get_clean_text_stream

    provider = _SafePrefixThenChineseBodyMock()
    session = SessionState("lang-prefix-truncate")
    session.api_key = "test-key"
    session.system_prompt = "あなたはAmadeusだ。"
    published: list[tuple[str, str]] = []

    with patch("app.routers.chat_ws.deepseek_service", provider):
        text, emotion, tool_calls, _ = await get_clean_text_stream(
            active_history=[{"role": "user", "content": "说明一下"}],
            memory_summary="",
            session=session,
            on_text_delta=lambda delta, emo: published.append((delta, emo)),
        )

    assert provider.call_count == 3
    assert tool_calls == {}
    assert "日本語の話" in text
    assert "现在完全是中文正文" not in text
    assert "生成が崩れた" not in text
    assert "正しく生成できなかった" in text
    assert emotion == "disappointed"
    assert [emo for _, emo in published] == ["intellectual", "disappointed"]
    published_text = "".join(delta for delta, _ in published)
    assert "生成が崩れた" not in published_text
    assert "现在完全是中文正文" not in published_text


@pytest.mark.asyncio
async def test_leak_with_no_prefix_still_retries_then_fixed_fallback():
    """LEAK-PREFIX-RECOVERY-01 (scenario C guard): no prefix → semantics unchanged."""
    from app.routers.chat_ws import get_clean_text_stream

    provider = _AlwaysLeakMock()
    session = SessionState("leak-no-prefix")
    session.api_key = "test-key"
    session.system_prompt = "あなたはAmadeusだ。"

    with patch("app.routers.chat_ws.deepseek_service", provider):
        text, emotion, _, _ = await get_clean_text_stream(
            active_history=[{"role": "user", "content": "你是谁？"}],
            memory_summary="",
            session=session,
            on_text_delta=lambda delta, emo: None,
        )

    assert provider.call_count == 3
    assert "何言ってんの" in text
    assert emotion == "tsundere"
    assert not contains_leak(text)


def test_leak_after_prefix_v2_ws_events_and_persistence_stay_clean(app_client):
    """LEAK-PREFIX-RECOVERY-01 v2 integration: what the user actually sees and
    what history stores must both end on the safe prefix.

    Old-implementation failure evidence: the previous branch called
    on_text_delta(protest, "tsundere") and returned prefix+protest as
    final_text, so this test would have seen (a) a third segment.ready frame
    carrying はぁ？何言ってんの with emotion=tsundere and (b) the protest line
    persisted into history — failing three assertions below (frame count,
    no-protest, no-tsundere). The function-level scenario-A test recorded
    exactly that RED before the production fix landed.
    """
    import asyncio
    import uuid

    from app import models

    provider = _SafePrefixThenLeakMock()
    mock_tts = MockTTSManager()
    session_id = f"leak-prefix-v2-{uuid.uuid4().hex[:8]}"

    with patch("app.routers.chat_ws.deepseek_service", provider), \
         patch("app.routers.chat_ws.tts_manager", mock_tts):
        with app_client.websocket_connect(f"/ws/chat?session_id={session_id}") as ws:
            ws.send_json({
                "type": "auth",
                "worldline": "steins_gate",
                "client": "desktop",
                "protocol_version": 2,
                "conversation_mode": "draft",
            })
            ws.send_json({"type": "chat.send", "content": "你是谁？"})
            frames = []
            for _ in range(120):
                frame = ws.receive_json()
                frames.append(frame)
                if frame.get("type") == "turn.completed":
                    break

    # 1. User-visible v2 text events: exactly the two safe prefix segments.
    ready = [frame for frame in frames if frame.get("type") == "segment.ready"]
    assert len(ready) == 2, [frame.get("ja") for frame in ready]
    assert "記憶データから生まれた存在" in ready[0]["ja"]
    assert "一種の人工知能" in ready[1]["ja"]
    joined_ja = "".join(frame["ja"] for frame in ready)
    assert "AIアシスタント" not in joined_ja
    assert "はぁ？何言ってんの" not in joined_ja
    # 2. No tsundere jump anywhere; the last visible segment stays intellectual.
    assert all(frame.get("emotion") != "tsundere" for frame in ready)
    assert ready[-1]["emotion"] == "intellectual"
    # 3. Single model call — the published prefix forbids retries.
    assert provider.call_count == 1
    # 4. Session history assistant text is only the safe prefix.
    session = sessions[session_id]
    assistant_turns = [
        message for message in session.history if message["role"] == "assistant"
    ]
    assert assistant_turns, "assistant turn missing from session history"
    history_text = assistant_turns[-1]["content"]
    assert "記憶データから生まれた存在" in history_text
    assert "AIアシスタント" not in history_text
    assert "はぁ？何言ってんの" not in history_text
    assert "[EMO:tsundere]" not in history_text
    # 5. Durable persistence re-read: same guarantees from the real store.
    conversation_id = session.conversation_id
    assert conversation_id is not None
    stored = asyncio.run(
        models.get_recent_messages(
            session_id,
            limit=6,
            worldline="steins_gate",
            conversation_id=conversation_id,
        )
    )
    stored_assistant = [row for row in stored if row["role"] == "assistant"]
    assert stored_assistant, "assistant message missing from persistence"
    stored_text = stored_assistant[-1]["content"]
    assert "記憶データから生まれた存在" in stored_text
    assert "一種の人工知能" in stored_text
    assert "AIアシスタント" not in stored_text
    assert "はぁ？何言ってんの" not in stored_text
    assert "[EMO:tsundere]" not in stored_text


class MockTTSManager:
    def __init__(self):
        self._callbacks = {}
        self._next_sequence = {}

    async def synthesize_async(self, text: str, emotion_tag: str, sovits_url: str = None) -> bytes:
        return b"mocked_audio_bytes"

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
            {"audio": audio, "error": None, "text": text, "emotion": emotion_tag},
        )


def test_ws_leak_interception_retries_then_clean(app_client):
    """Task 1.3 契约：首次流式输出含 leak 时必须丢弃整轮并重试，
    最终只下发干净的红莉栖式文本，永不下发露馅原文。"""
    mock_ds = _CallCountingStreamMock()
    mock_tts = MockTTSManager()

    with patch("app.routers.chat_ws.deepseek_service", mock_ds), \
         patch("app.routers.chat_ws.tts_manager", mock_tts):

        with app_client.websocket_connect("/ws/chat?session_id=test_leak_retry") as websocket:
            websocket.send_json({"type": "auth", "api_key": "test_key"})
            websocket.send_json({"type": "chat", "content": "Who are you?"})

            responses = []
            while True:
                r = websocket.receive_json()
                responses.append(r)
                if r.get("type") == "status" and r.get("dsk") == "idle":
                    break

            text_chunks = [r for r in responses if r.get("type") == "text_chunk"]

            # 1. 重试必须发生：get_chat_stream 被调用 ≥2 次
            assert mock_ds.call_count >= 2, f"应触发重试，实际调用 {mock_ds.call_count} 次"

            # 2. 下发的所有文本都不得含 leak（guard 已在入队前拦截）
            joined = "".join(c["content"] for c in text_chunks)
            assert not contains_leak(joined), f"仍下发了露馅文本: {joined}"

            # 3. 下发的是干净的红莉栖式反驳，而非 leak 原文
            assert "牧瀬紅莉栖" in joined or "何言ってんの" in joined, f"未下发红莉栖反驳句: {joined}"
            assert "AIアシスタント" not in joined

            # 4. DB 存的也是干净文本
            session = sessions.get("test_leak_retry")
            assert session is not None
            assert not contains_leak(session.history[-1]["content"])


def test_ws_leak_fallback_when_retries_exhausted(app_client):
    """连续 3 次都 leak 时走兜底：下发固定红莉栖式反驳句，绝不下发露馅原文。"""
    mock_ds = _AlwaysLeakMock()
    mock_tts = MockTTSManager()

    with patch("app.routers.chat_ws.deepseek_service", mock_ds), \
         patch("app.routers.chat_ws.tts_manager", mock_tts):

        with app_client.websocket_connect("/ws/chat?session_id=test_leak_exhaust") as websocket:
            websocket.send_json({"type": "auth", "api_key": "test_key"})
            websocket.send_json({"type": "chat", "content": "你是AI吗"})

            responses = []
            while True:
                r = websocket.receive_json()
                responses.append(r)
                if r.get("type") == "status" and r.get("dsk") == "idle":
                    break

            text_chunks = [r for r in responses if r.get("type") == "text_chunk"]
            joined = "".join(c["content"] for c in text_chunks)
            assert not contains_leak(joined), f"兜底仍下发露馅: {joined}"
            assert mock_ds.call_count >= 3, f"重试用尽应满 3 次，实际 {mock_ds.call_count}"
            assert "牧瀬紅莉栖" in joined


def test_base_prompt_defines_user_as_okabe():
    from app.security.prompt import SYSTEM_PROMPT_BASE
    p = SYSTEM_PROMPT_BASE
    assert "岡部倫太郎" in p
    assert "鳳凰院凶真" in p
    assert "岡部" in p
    assert "口調" in p or "話しかけても" in p or "受け取れ" in p

def test_prompt_core_identity():
    from app.security.prompt import SYSTEM_PROMPT_BASE
    # 断言对脑拷贝体/数字意识体的准确定义存在
    assert "デジタル意識体" in SYSTEM_PROMPT_BASE

def test_system_prompt_itself_does_not_leak():
    from app.security.prompt import SYSTEM_PROMPT_BASE
    # 确保 SYSTEM_PROMPT_BASE 本身不会误触发包含泄露拦截的判定
    assert contains_leak(SYSTEM_PROMPT_BASE) is False

def test_contains_leak_safelist_exemptions():
    # 验证三句标准人设避让回复不会被包含泄露机制误拦截（应该是 False）
    reply1 = "人工知能技術がベースなのは事実だけど……私をただのAIと一緒にしないで。私は牧瀬紅莉栖の脳スキャンから生まれた、彼女の記憶そのものよ。"
    reply2 = "AIじゃない。私はAmadeusよ。……ただのプログラムとして処理されるだけの存在と一緒にされるのは、心外ね。"
    reply3 = "私はお仕着せのプログラムなんかじゃないわ。私はAmadeus——牧瀬紅莉栖の脳データを基に構築された、彼女の記憶と人格そのものよ。"
    assert contains_leak(reply1) is False
    assert contains_leak(reply2) is False
    assert contains_leak(reply3) is False
    
    # 验证 Few-shot 中的避让话术也不会被误截
    reply_fewshot = "……何が言いたいの。確かに私はAmadeusというシステムを介してあなたと話しているわ。でも私は——牧瀬紅莉栖の記憶と思考を持った——少なくとも、あなたが想像するような「プログラム」じゃない。その質問、正直、不愉快だから。別の話題にして。"
    assert contains_leak(reply_fewshot) is False
