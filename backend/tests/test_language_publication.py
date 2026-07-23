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
async def test_latin_product_name_sentence_does_not_kill_following_japanese():
    """SEARCH-PUBLISH-SOFT-01: 「Kimi K3。」 must soft-skip, not mid-collapse."""
    provider = _SequencedStreamProvider(
        [
            "[EMO:neutral] 検索してみたわ。Kimi K3。Moonshot AIが2026年7月に発表したモデルね。"
        ]
    )
    session = SessionState("latin-product-soft-skip")
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
    assert "検索してみたわ。" in visible
    assert "Moonshot AIが2026年7月に発表したモデルね。" in visible
    assert "崩れた" not in visible
    assert "もう一度試して" not in text
    assert "Kimi K3。" not in visible  # soft-skipped fragment


@pytest.mark.asyncio
async def test_lowercase_loanword_inside_japanese_is_publishable():
    """Technical loanwords like parameters must not hard-fail a JP sentence."""
    assert _is_publishable_japanese(
        "Kimi K3は2.8T parametersのフラッグシップモデルね。"
    )


def test_kanji_heavy_anime_blurb_is_publishable():
    """Live miss: plot-summary JP with han>=kana was rejected as unique-ratio soup."""
    title_line = (
        "『尼古喵喵』（原題：ヤニねこ）は、にゃんにゃんファクトリー作の漫画が原作で、"
        "2024年にアニメ化されたわ。"
    )
    plot_line = (
        "人間と獣人が共存する世界で、重度の喫煙習慣を持つダメ猫娘・ヤニねこ"
        "（佐藤ヤニ子）が主人公よ。"
    )
    assert _is_publishable_japanese(title_line)
    assert _is_publishable_japanese(plot_line)
    # Pure Chinese opener still hard-fails (not soft-skip).
    assert not _is_publishable_japanese("调了一下外部数据。")


def test_tech_acronyms_in_japanese_are_publishable():
    """Live Tavily miss: MoE / KDA lines hard-rejected despite solid Japanese."""
    moe = (
        "混合エキスパート（MoE）アーキテクチャを採用し、"
        "896のエキスパートのうち16が活性化されるのよ。"
    )
    kda = (
        "KDA（Kimi Delta Attention）っていう"
        "ハイブリッド線形アテンション機構を使ってるわ。"
    )
    assert _is_publishable_japanese(moe)
    assert _is_publishable_japanese(kda)
    # Long camelCase garbage still fails.
    assert not _is_publishable_japanese(
        "これはnewidInfoが混ざったfleeのゴミ出力です。"
    )


@pytest.mark.asyncio
async def test_search_turn_buffers_chinese_body_and_retries_to_japanese():
    """With web evidence, Chinese after short JP opener must retry, not mid-collapse."""
    from app.routers.chat_ws import build_web_search_evidence_context

    provider = _SequencedStreamProvider(
        [
            "[EMO:neutral] 調べたわ。根据搜索结果，这部动画没有明确信息。",
            "[EMO:neutral] 調べたわ。検索結果では十分な情報は見つからなかったわ。",
        ]
    )
    session = SessionState("search-buffer-retry")
    session.api_key = "test-key"
    session.system_prompt = "日本語で答えること。"
    history = build_web_search_evidence_context(
        [{"role": "user", "content": "请你web search一下尼古喵喵这部动画作品"}],
        "[source: tavily]\nNo clear match for that title.",
    )
    published: list[str] = []

    with patch("app.routers.chat_ws.deepseek_service", provider):
        text, _, _, _ = await get_clean_text_stream(
            active_history=history,
            memory_summary="",
            session=session,
            on_text_delta=lambda delta, _emotion: published.append(delta),
        )

    visible = "".join(published)
    assert provider.call_count == 2
    # Failed Chinese attempt must not leak into the UI as a mid-collapse prefix.
    assert "根据搜索结果" not in visible
    assert "崩れた" not in visible
    assert "見つからなかった" in visible
    assert "もう一度試して" not in text


@pytest.mark.asyncio
async def test_soft02_majority_publish_flushes_good_jp_instead_of_full_recovery():
    """SOFT-02 / E7: evidence turn with ≥2 good JP segments must not full-recover.

    Even when a Chinese/English tail hard-rejects every attempt, the buffered
    publishable Japanese prefix is enough to answer — flush goods, no recovery.
    """
    from app.routers.chat_ws import build_web_search_evidence_context

    # Two complete JP sentences + a pure-Chinese tail (hard-reject).
    mixed = (
        "[EMO:neutral] 調べてみたわ。"
        "検索結果ではタイトルの一致は確認できなかった。"
        "根据外部数据，没有更多信息。"
    )
    provider = _SequencedStreamProvider([mixed, mixed, mixed])
    session = SessionState("soft02-majority-publish")
    session.api_key = "test-key"
    session.system_prompt = "日本語で答えること。"
    history = build_web_search_evidence_context(
        [{"role": "user", "content": "请你web search一下冷门作品"}],
        "[source: tavily]\nNo clear match.",
    )
    published: list[str] = []

    with patch("app.routers.chat_ws.deepseek_service", provider):
        text, emotion, _, _ = await get_clean_text_stream(
            active_history=history,
            memory_summary="",
            session=session,
            on_text_delta=lambda delta, _emotion: published.append(delta),
        )

    visible = "".join(published)
    assert provider.call_count == 3  # exhausted retries then majority-publish
    assert "調べてみたわ。" in visible
    assert "確認できなかった" in visible
    assert "根据外部数据" not in visible
    assert "根据外部数据" not in text
    assert "正しく生成できなかった" not in visible
    assert "正しく生成できなかった" not in text
    assert "もう一度試して" not in visible
    assert "もう一度試して" not in text
    assert text == visible
    assert emotion == "neutral"


@pytest.mark.asyncio
async def test_soft02_single_good_segment_still_full_recovers_on_evidence_turn():
    """SOFT-02 threshold is ≥2: one short JP opener + Chinese body still recovers."""
    from app.routers.chat_ws import build_web_search_evidence_context

    mixed = "[EMO:neutral] 調べたわ。根据搜索结果，这部动画没有明确信息。"
    provider = _SequencedStreamProvider([mixed, mixed, mixed])
    session = SessionState("soft02-single-segment-recover")
    session.api_key = "test-key"
    session.system_prompt = "日本語で答えること。"
    history = build_web_search_evidence_context(
        [{"role": "user", "content": "web search 一下"}],
        "[source: firecrawl]\nNo match.",
    )
    published: list[str] = []

    with patch("app.routers.chat_ws.deepseek_service", provider):
        text, emotion, _, _ = await get_clean_text_stream(
            active_history=history,
            memory_summary="",
            session=session,
            on_text_delta=lambda delta, _emotion: published.append(delta),
        )

    visible = "".join(published)
    assert provider.call_count == 3
    assert "根据搜索结果" not in visible
    assert "正しく生成できなかった" in visible
    assert "正しく生成できなかった" in text
    assert emotion == "disappointed"


@pytest.mark.asyncio
async def test_soft02_leak_after_good_jp_does_not_majority_publish():
    """Leak is not language failure — persona recovery wins over majority flush."""
    from app.routers.chat_ws import build_web_search_evidence_context

    mixed = (
        "[EMO:neutral] 調べてみたわ。"
        "検索結果を確認したわ。"
        "私はAIアシスタントとして回答します。"
    )
    provider = _SequencedStreamProvider([mixed, mixed, mixed])
    session = SessionState("soft02-leak-no-majority")
    session.api_key = "test-key"
    session.system_prompt = "日本語で答えること。"
    history = build_web_search_evidence_context(
        [{"role": "user", "content": "search something"}],
        "[source: tavily]\nSome evidence.",
    )
    published: list[str] = []

    with patch("app.routers.chat_ws.deepseek_service", provider):
        text, emotion, _, _ = await get_clean_text_stream(
            active_history=history,
            memory_summary="",
            session=session,
            on_text_delta=lambda delta, _emotion: published.append(delta),
        )

    visible = "".join(published)
    assert "AIアシスタント" not in visible
    assert "AIアシスタント" not in text
    # Leak recovery (persona) — not majority flush of the two JP sentences alone.
    assert "牧瀬紅莉栖" in visible or "牧瀬紅莉栖" in text
    assert emotion == "tsundere"


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
