from unittest.mock import patch

import pytest

from app.routers.chat_ws import (
    SessionState,
    _is_publishable_japanese,
    get_clean_text_stream,
    sessions,
)


class _SequencedStreamProvider:
    def __init__(self, outputs: list[str]):
        self.outputs = outputs
        self.call_count = 0
        self.prompts: list[str] = []

    async def get_chat_stream(self, **kwargs):
        self.prompts.append(kwargs["system_prompt"])
        output = self.outputs[min(self.call_count, len(self.outputs) - 1)]
        self.call_count += 1
        midpoint = max(1, len(output) // 2)
        yield {"content": output[:midpoint], "tool_calls": None}
        yield {"content": output[midpoint:], "tool_calls": None}

    async def translate_to_zh(self, text, api_key=None):
        return f"译:{text}"

    async def compress_history(self, history, api_key=None, old_summary=""):
        return old_summary


INCIDENT_GARBAGE_TAIL = (
    "嫌身無等業単美目民』―ィ閉定状る青亀若笑自伝界本国象券正門仕──"
    "県的幕・切終性石葉門造長音数輪組羽管若権言希号存圏武可徳原容"
    "超管沢号週高印情降完七装帰社郡敵道労里員野行容辞体果割。"
)

# Exact corrupt Japanese that still hit GPT-SoVITS /tts 200 in live logs.
REAL_LOG_COLLAPSE = (
    "ハキム、フィモンの公的同中心凝択ハfleeとま悪いい ―改newidInfoどうスタも"
    "子おろしてましせただしま角環プロｯtま字です、とのデータつきはしてい 。"
)


def test_publication_gate_rejects_mixed_chinese_with_only_one_kana():
    assert not _is_publishable_japanese("这就是だ")
    assert _is_publishable_japanese("中国語ではなく、日本語で答えるわ")


@pytest.mark.asyncio
async def test_period_ellipsis_does_not_trigger_mid_collapse_recovery():
    """LANG-GATE-RELAX-01: 「ね。……続き」 must not append generation-failed recovery.

    The segmenter used to emit a bare 「……」 between sentences; that fragment
    false-rejected publication and produced prefix + recovery at temperature=0.
    """
    provider = _SequencedStreamProvider(
        [
            "[EMO:tsundere] 朝からその呼び方ね。……はあ、面倒なんだから。"
            "実験の話なら聞くけど。"
        ]
    )
    session = SessionState("language-ellipsis-false-reject")
    session.api_key = "test-key"
    session.system_prompt = "日本語で答えること。"
    published: list[str] = []

    with patch("app.routers.chat_ws.deepseek_service", provider):
        text, emotion, _, found = await get_clean_text_stream(
            active_history=[],
            memory_summary="",
            session=session,
            on_text_delta=lambda delta, _emotion: published.append(delta),
        )

    visible = "".join(published)
    assert provider.call_count == 1
    assert "朝からその呼び方ね" in visible
    assert "面倒" in visible
    assert "実験の話なら聞くけど" in visible
    assert "生成が崩れた" not in visible
    assert "もう一度試して" not in visible
    assert "生成が崩れた" not in text
    assert "もう一度試して" not in text
    assert emotion == "tsundere"
    assert found is True


@pytest.mark.asyncio
async def test_chinese_only_draft_is_not_published_as_japanese():
    provider = _SequencedStreamProvider(
        [
            "[EMO:intellectual] 现在是2026年7月，搜索结果没有冠军信息。",
            "[EMO:intellectual] 検索結果には、優勝国を確認できる情報がまだないわ。",
        ]
    )
    session = SessionState("language-retry")
    session.api_key = "test-key"
    session.system_prompt = "日本語で答えること。"
    published: list[str] = []

    async def capture(delta: str, _emotion: str) -> None:
        published.append(delta)

    with patch("app.routers.chat_ws.deepseek_service", provider):
        text, emotion, tool_calls, found = await get_clean_text_stream(
            active_history=[],
            memory_summary="",
            session=session,
            on_text_delta=capture,
        )

    assert provider.call_count == 2
    assert "日本語だけで回答" in provider.prompts[1]
    assert "現在是" not in "".join(published)
    assert "検索結果には" in "".join(published)
    assert text.strip() == "検索結果には、優勝国を確認できる情報がまだないわ。"
    assert emotion == "intellectual"
    assert tool_calls == {}
    assert found is True


@pytest.mark.asyncio
async def test_valid_japanese_keeps_incremental_publication():
    provider = _SequencedStreamProvider(
        ["[EMO:neutral] そうね。検索結果を確認してから答えるわ。"]
    )
    session = SessionState("language-pass")
    session.api_key = "test-key"
    session.system_prompt = "日本語で答えること。"
    published: list[str] = []

    with patch("app.routers.chat_ws.deepseek_service", provider):
        text, _, _, _ = await get_clean_text_stream(
            active_history=[],
            memory_summary="",
            session=session,
            on_text_delta=lambda delta, _emotion: published.append(delta),
        )

    assert provider.call_count == 1
    assert "".join(published) == text
    assert text.strip() == "そうね。検索結果を確認してから答えるわ。"


@pytest.mark.asyncio
async def test_incident_garbage_tail_never_enters_incremental_publication():
    provider = _SequencedStreamProvider(
        [f"[EMO:intellectual] まず、実験結果を確認するわ。{INCIDENT_GARBAGE_TAIL}"]
    )
    session = SessionState("language-collapse-publication")
    session.api_key = "test-key"
    session.system_prompt = "日本語で答えること。"
    published: list[str] = []

    with patch("app.routers.chat_ws.deepseek_service", provider):
        text, _, _, _ = await get_clean_text_stream(
            active_history=[],
            memory_summary="",
            session=session,
            on_text_delta=lambda delta, _emotion: published.append(delta),
        )

    visible = "".join(published)
    assert "まず、実験結果を確認するわ。" in visible
    assert INCIDENT_GARBAGE_TAIL not in visible
    assert "嫌身無等業" not in visible
    assert "嫌身無等業" not in text


@pytest.mark.asyncio
async def test_incident_only_output_recovers_without_publishing_or_returning_garbage():
    provider = _SequencedStreamProvider(
        [f"[EMO:neutral] {INCIDENT_GARBAGE_TAIL}"]
    )
    session = SessionState("language-collapse-recovery")
    session.api_key = "test-key"
    session.system_prompt = "日本語で答えること。"
    published: list[str] = []

    with patch("app.routers.chat_ws.deepseek_service", provider):
        text, emotion, _, _ = await get_clean_text_stream(
            active_history=[],
            memory_summary="",
            session=session,
            on_text_delta=lambda delta, _emotion: published.append(delta),
        )

    visible = "".join(published)
    assert provider.call_count == 3
    assert "嫌身無等業" not in visible
    assert "嫌身無等業" not in text
    assert "日本語の応答を正しく生成できなかった" in visible
    assert text == visible
    assert emotion == "disappointed"


def test_v2_chinese_input_gets_visible_recovery_and_never_persists_garbage(app_client):
    provider = _SequencedStreamProvider(
        [f"[EMO:neutral] {INCIDENT_GARBAGE_TAIL}"]
    )
    session_id = "language-collapse-chinese-e2e"

    with patch("app.routers.chat_ws.deepseek_service", provider):
        with app_client.websocket_connect(f"/ws/chat?session_id={session_id}") as websocket:
            websocket.send_json(
                {
                    "type": "auth",
                    "api_key": "test-key",
                    "client": "desktop",
                    "protocol_version": 2,
                }
            )
            websocket.send_json({"type": "chat.send", "content": "你好，实验还要继续吗？"})

            responses: list[dict] = []
            while True:
                response = websocket.receive_json()
                responses.append(response)
                if response.get("type") in {"turn.completed", "error"}:
                    break

    assert responses[-1]["type"] == "turn.completed"
    visible = "".join(
        response.get("ja", "")
        for response in responses
        if response.get("type") == "segment.ready"
    )
    assert visible
    assert "日本語の応答を正しく生成できなかった" in visible or "もう一度試して" in visible
    assert "嫌身無等業" not in visible

    session = sessions[session_id]
    messages = app_client.get(
        f"/api/conversations/{session.conversation_id}/messages",
        params={"session_id": session_id, "worldline": "steins_gate"},
    )
    assert messages.status_code == 200
    assistant_messages = [
        message for message in messages.json() if message["role"] == "assistant"
    ]
    assert assistant_messages
    persisted = assistant_messages[-1]["content"]
    assert "日本語の応答を正しく生成できなかった" in persisted or "もう一度試して" in persisted
    assert "嫌身無等業" not in persisted


def test_real_log_collapse_never_enters_segment_ready_or_history(app_client):
    """Live /tts 200 fixture must never appear in segment.ready or history."""
    good = "Mackenzieの肝機能活性データをPDFにする？"
    provider = _SequencedStreamProvider(
        [f"[EMO:neutral] {good}{REAL_LOG_COLLAPSE}"]
    )
    session_id = "language-collapse-real-log-e2e"

    with patch("app.routers.chat_ws.deepseek_service", provider):
        with app_client.websocket_connect(f"/ws/chat?session_id={session_id}") as websocket:
            websocket.send_json(
                {
                    "type": "auth",
                    "api_key": "test-key",
                    "client": "desktop",
                    "protocol_version": 2,
                    "enable_tts": True,
                }
            )
            websocket.send_json({"type": "chat.send", "content": "你还好吗"})

            responses: list[dict] = []
            while True:
                response = websocket.receive_json()
                responses.append(response)
                if response.get("type") in {"turn.completed", "error"}:
                    break

    assert responses[-1]["type"] == "turn.completed"
    ready_ja = [
        response.get("ja", "")
        for response in responses
        if response.get("type") == "segment.ready"
    ]
    joined = "".join(ready_ja)
    assert "flee" not in joined
    assert "newidInfo" not in joined
    assert "プロｯt" not in joined
    assert REAL_LOG_COLLAPSE not in joined
    # May keep the legitimate prefix and append recovery, but never the corrupt tail.
    assert "もう一度試して" in joined or good in joined
    # Must not be the forbidden concatenation of good + corrupt + recovery with corrupt kept.
    assert not any("flee" in part for part in ready_ja)

    session = sessions[session_id]
    messages = app_client.get(
        f"/api/conversations/{session.conversation_id}/messages",
        params={"session_id": session_id, "worldline": "steins_gate"},
    )
    assert messages.status_code == 200
    assistant_messages = [
        message for message in messages.json() if message["role"] == "assistant"
    ]
    assert assistant_messages
    persisted = assistant_messages[-1]["content"]
    assert "flee" not in persisted
    assert "newidInfo" not in persisted
    assert REAL_LOG_COLLAPSE not in persisted
