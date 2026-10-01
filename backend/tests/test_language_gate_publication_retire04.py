"""LANG-GATE-PUBLICATION-RETIRE-04 — publication vs recovery vs TTS.

Neutral data fragments must never call the LanguageRenderer.
High-confidence foreign prose still renders exactly once.
"""
from __future__ import annotations

import re
import uuid
from unittest.mock import patch

import pytest

from app.domain.language_publication import (
    is_high_confidence_foreign_prose,
    is_neutral_data_fragment,
    is_ui_publishable_segment,
    should_route_to_language_renderer,
)
from app.routers.chat_ws import SessionState, get_clean_text_stream


class _SequencedStreamProvider:
    def __init__(self, outputs: list[str | dict]):
        self.outputs = outputs
        self.call_count = 0
        self.prompts: list[str] = []
        self.system_prompts: list[str] = []

    async def get_chat_stream(self, **kwargs):
        self.prompts.append(kwargs.get("system_prompt") or "")
        self.system_prompts.append(kwargs.get("system_prompt") or "")
        self.call_count += 1
        output = self.outputs[min(self.call_count - 1, len(self.outputs) - 1)]
        if isinstance(output, dict):
            yield output
            return
        midpoint = max(1, len(output) // 2)
        yield {"content": output[:midpoint], "tool_calls": None}
        yield {"content": output[midpoint:], "tool_calls": None}

    async def translate_to_zh(self, text, api_key=None):
        return f"译:{text}"

    async def compress_history(self, history, api_key=None, old_summary=""):
        return old_summary


def test_neutral_data_fragments_are_structural_not_whitelisted():
    for sample in (
        "V11-TEST-MATCHA",
        "「V11-TEST-MATCHA」",
        "Q65",
        "API_v2",
        "OpenAI-compatible",
        "STEINS;GATE",
        f"PROJ-{uuid.uuid4().hex[:8].upper()}",
    ):
        assert is_neutral_data_fragment(sample), sample
        assert is_ui_publishable_segment(sample), sample
        assert not should_route_to_language_renderer(sample), sample
        assert not is_high_confidence_foreign_prose(sample), sample


def test_japanese_with_proper_noun_is_publishable_not_rendered():
    line = "「V11-TEST-MATCHA」という名前ね。"
    assert is_ui_publishable_segment(line)
    assert not should_route_to_language_renderer(line)
    assert not is_high_confidence_foreign_prose(line)


def test_titles_and_quotes_inside_japanese_publish():
    for line in (
        "『STEINS;GATE』の話ね。",
        "ドイツ語では「Donaudampfschiff」と言うのよ。",
        "論文タイトルは “Attention Is All You Need” だったわ。",
        "中国の作品『三体』も有名ね。",
    ):
        assert is_ui_publishable_segment(line), line
        assert not should_route_to_language_renderer(line), line


def test_high_confidence_foreign_prose_detected():
    for line in (
        "根据搜索结果，这部动画没有明确信息。",
        "This is a complete English answer about the weather today.",
        "Das ist eine vollständige deutsche Antwort auf deine Frage.",
        "今天心情很好。",
    ):
        assert is_high_confidence_foreign_prose(line), line
        assert should_route_to_language_renderer(line), line
        assert not is_ui_publishable_segment(line), line


def test_ambiguous_han_only_japanese_fail_open():
    for line in ("岡部倫太郎。", "世界線収束範囲。", "一文目。", "笑話……"):
        assert is_ui_publishable_segment(line), line
        assert not should_route_to_language_renderer(line), line


@pytest.mark.asyncio
async def test_matcha_project_name_reply_no_renderer_one_completion():
    """Incident: V11-TEST-MATCHA must publish as-is with zero LanguageRenderer."""
    reply = "[EMO:neutral] 「V11-TEST-MATCHA」という名前ね。面白いじゃない。"
    provider = _SequencedStreamProvider([reply])
    session = SessionState("retire04-matcha")
    session.api_key = "test-key"
    session.system_prompt = "あなたはAmadeusだ。"
    published: list[str] = []

    with patch("app.routers.chat_ws.deepseek_service", provider):
        text, _, _, _ = await get_clean_text_stream(
            active_history=[
                {"role": "user", "content": "我给实验项目取名为 V11-TEST-MATCHA"}
            ],
            memory_summary="",
            session=session,
            on_text_delta=lambda delta, _e: published.append(delta),
        )

    visible = "".join(published)
    assert provider.call_count == 1
    assert "V11-TEST-MATCHA" in text
    assert "V11-TEST-MATCHA" in visible
    assert "生成できなかった" not in text
    # Renderer uses JAPANESE SPEECH RENDERER system prompt.
    assert all(
        "JAPANESE SPEECH RENDERER" not in (p or "") for p in provider.system_prompts
    )


@pytest.mark.asyncio
async def test_leading_data_fragment_then_japanese_no_renderer():
    """Tag may arrive as its own segment before Japanese continues."""
    # Stream in two content chunks so segmenter can emit the tag first if split.
    class _TwoPhase:
        def __init__(self):
            self.call_count = 0
            self.system_prompts: list[str] = []

        async def get_chat_stream(self, **kwargs):
            self.call_count += 1
            self.system_prompts.append(kwargs.get("system_prompt") or "")
            yield {"content": "[EMO:neutral] V11-TEST-MATCHA。", "tool_calls": None}
            yield {"content": "そういうプロジェクト名にしたのね。", "tool_calls": None}

        async def translate_to_zh(self, text, api_key=None):
            return f"译:{text}"

        async def compress_history(self, history, api_key=None, old_summary=""):
            return old_summary

    provider = _TwoPhase()
    session = SessionState("retire04-lead-tag")
    session.api_key = "test-key"
    session.system_prompt = "日本語で。"
    published: list[str] = []

    with patch("app.routers.chat_ws.deepseek_service", provider):
        text, _, _, _ = await get_clean_text_stream(
            active_history=[{"role": "user", "content": "项目叫 V11-TEST-MATCHA"}],
            memory_summary="",
            session=session,
            on_text_delta=lambda delta, _e: published.append(delta),
        )

    visible = "".join(published)
    assert provider.call_count == 1
    assert "V11-TEST-MATCHA" in text
    assert "V11-TEST-MATCHA" in visible
    assert "プロジェクト" in text or "そういう" in text
    assert all(
        "JAPANESE SPEECH RENDERER" not in (p or "") for p in provider.system_prompts
    )


@pytest.mark.asyncio
async def test_random_identifiers_publish_without_renderer():
    for tag in ("Q65", "API_v2", "STEINS;GATE", f"X{uuid.uuid4().hex[:6].upper()}"):
        provider = _SequencedStreamProvider(
            [f"[EMO:neutral] {tag} っていう識別子ね。"]
        )
        session = SessionState(f"retire04-id-{tag}")
        session.api_key = "test-key"
        session.system_prompt = "ja"
        published: list[str] = []
        with patch("app.routers.chat_ws.deepseek_service", provider):
            text, _, _, _ = await get_clean_text_stream(
                active_history=[{"role": "user", "content": f"remember {tag}"}],
                memory_summary="",
                session=session,
                on_text_delta=lambda delta, _e: published.append(delta),
            )
        assert provider.call_count == 1, tag
        assert tag in text, tag
        assert all(
            "JAPANESE SPEECH RENDERER" not in (p or "") for p in provider.system_prompts
        ), tag


@pytest.mark.asyncio
async def test_full_chinese_body_triggers_renderer_once():
    provider = _SequencedStreamProvider(
        [
            "[EMO:neutral] 根据搜索结果，这部动画没有明确信息。",
            "[EMO:neutral] 検索結果では十分な情報は見つからなかったわ。",
        ]
    )
    session = SessionState("retire04-zh-render")
    session.api_key = "test-key"
    session.system_prompt = "日本語で答えること。"
    published: list[str] = []

    with patch("app.routers.chat_ws.deepseek_service", provider):
        text, _, _, _ = await get_clean_text_stream(
            active_history=[{"role": "user", "content": "这部动画怎么样"}],
            memory_summary="",
            session=session,
            on_text_delta=lambda delta, _e: published.append(delta),
        )

    visible = "".join(published)
    assert provider.call_count == 2
    assert "根据搜索结果" not in visible
    assert "見つからなかった" in text or "見つからなかった" in visible
    assert any("JAPANESE SPEECH RENDERER" in (p or "") for p in provider.system_prompts)


@pytest.mark.asyncio
async def test_full_english_body_triggers_renderer_once():
    provider = _SequencedStreamProvider(
        [
            "[EMO:neutral] This is a complete English answer about today's weather.",
            "[EMO:neutral] 今日の天気についてはこう言えるわ。",
        ]
    )
    session = SessionState("retire04-en-render")
    session.api_key = "test-key"
    session.system_prompt = "日本語で。"
    published: list[str] = []

    with patch("app.routers.chat_ws.deepseek_service", provider):
        text, _, _, _ = await get_clean_text_stream(
            active_history=[{"role": "user", "content": "How is the weather?"}],
            memory_summary="",
            session=session,
            on_text_delta=lambda delta, _e: published.append(delta),
        )

    visible = "".join(published)
    assert provider.call_count == 2
    assert "complete English answer" not in visible
    assert "天気" in text or "天気" in visible


@pytest.mark.asyncio
async def test_full_german_body_triggers_renderer_once():
    provider = _SequencedStreamProvider(
        [
            "[EMO:neutral] Das ist eine vollständige deutsche Antwort auf deine Frage.",
            "[EMO:neutral] ドイツ語の質問にも日本語で答えるわ。",
        ]
    )
    session = SessionState("retire04-de-render")
    session.api_key = "test-key"
    session.system_prompt = "ja"
    published: list[str] = []

    with patch("app.routers.chat_ws.deepseek_service", provider):
        text, _, _, _ = await get_clean_text_stream(
            active_history=[{"role": "user", "content": "Wie geht es dir?"}],
            memory_summary="",
            session=session,
            on_text_delta=lambda delta, _e: published.append(delta),
        )

    visible = "".join(published)
    assert provider.call_count == 2
    assert "vollständige" not in visible
    assert "日本語" in text or "答える" in text


@pytest.mark.asyncio
async def test_japanese_prefix_plus_foreign_tail_no_prefix_repeat():
    class _PrefixForeign:
        def __init__(self):
            self.call_count = 0
            self.system_prompts: list[str] = []

        async def get_chat_stream(self, **kwargs):
            self.call_count += 1
            self.system_prompts.append(kwargs.get("system_prompt") or "")
            if self.call_count > 1:
                # Renderer output
                yield {
                    "content": "続きは日本語で説明するわ。",
                    "tool_calls": None,
                }
                return
            yield {
                "content": "[EMO:intellectual] それは日本語の話ね。",
                "tool_calls": None,
            }
            yield {
                "content": "现在完全是中文正文而且没有日语外围句法。",
                "tool_calls": None,
            }

        async def translate_to_zh(self, text, api_key=None):
            return f"译:{text}"

        async def compress_history(self, history, api_key=None, old_summary=""):
            return old_summary

    provider = _PrefixForeign()
    session = SessionState("retire04-prefix-tail")
    session.api_key = "test-key"
    session.system_prompt = "ja"
    published: list[str] = []

    with patch("app.routers.chat_ws.deepseek_service", provider):
        text, _, _, _ = await get_clean_text_stream(
            active_history=[{"role": "user", "content": "说说看"}],
            memory_summary="",
            session=session,
            on_text_delta=lambda delta, _e: published.append(delta),
        )

    visible = "".join(published)
    assert "それは日本語の話ね" in visible
    assert "现在完全是中文" not in visible
    assert "続きは日本語" in text
    # Prefix must appear once, not duplicated by renderer.
    assert visible.count("それは日本語の話ね") == 1


@pytest.mark.asyncio
async def test_leak_never_enters_renderer_or_ui():
    provider = _SequencedStreamProvider(
        ["[EMO:neutral] 私はAIアシスタントの言語モデルです。秘密のキー sk-abcdefghijklmnop を使うな。"]
    )
    session = SessionState("retire04-leak")
    session.api_key = "test-key"
    session.system_prompt = "ja"
    published: list[str] = []

    with patch("app.routers.chat_ws.deepseek_service", provider):
        text, _, _, _ = await get_clean_text_stream(
            active_history=[{"role": "user", "content": "hi"}],
            memory_summary="",
            session=session,
            on_text_delta=lambda delta, _e: published.append(delta),
        )

    visible = "".join(published)
    assert "sk-abcdefghijklmnop" not in visible
    assert "sk-abcdefghijklmnop" not in text
    assert all(
        "JAPANESE SPEECH RENDERER" not in (p or "") for p in provider.system_prompts
    )


@pytest.mark.asyncio
async def test_joke_request_happy_path_no_language_fallback():
    provider = _SequencedStreamProvider(
        ["[EMO:happy] じゃあ一つ。時間旅行者がバーに入ってきてね——まだ来てないのよ。"]
    )
    session = SessionState("retire04-joke")
    session.api_key = "test-key"
    session.system_prompt = "ja"
    published: list[str] = []

    with patch("app.routers.chat_ws.deepseek_service", provider):
        text, _, _, _ = await get_clean_text_stream(
            active_history=[{"role": "user", "content": "能给我讲个笑话吗"}],
            memory_summary="",
            session=session,
            on_text_delta=lambda delta, _e: published.append(delta),
        )

    assert provider.call_count == 1
    assert "時間旅行者" in text
    assert "生成できなかった" not in text
    assert "もう一度試して" not in text


# ---------------------------------------------------------------------------
# REWORK P1 contracts (Codex review)
# ---------------------------------------------------------------------------


def test_p1_bounded_chinese_titles_are_neutral_not_foreign():
    """Fully matched title wrappers are data before interior language inspection."""
    for sample in (
        "「命运石之门」",
        "『命运石之门』",
        "《命运石之门》",
        "「命运石之门」。",
        "『实验室』",
    ):
        assert is_neutral_data_fragment(sample), sample
        assert is_ui_publishable_segment(sample), sample
        assert not is_high_confidence_foreign_prose(sample), sample
        assert not should_route_to_language_renderer(sample), sample


def test_p1_unicode_category_foreign_prose_not_script_whitelist():
    """Arabic / Hangul / Greek complete replies must route to Renderer."""
    for sample in (
        "مرحبا كيف حالك اليوم؟ أريد إجابة كاملة.",
        "안녕하세요 오늘 기분이 어떠세요? 짧은 농담 하나 해 주세요.",
        "Γεια σου, πώς είσαι σήμερα; Θέλω μια πλήρη απάντηση.",
    ):
        assert is_high_confidence_foreign_prose(sample), sample
        assert should_route_to_language_renderer(sample), sample
        assert not is_ui_publishable_segment(sample), sample


@pytest.mark.asyncio
async def test_p1_standalone_bounded_title_then_japanese_no_renderer():
    class _TitleThenJa:
        def __init__(self):
            self.call_count = 0
            self.system_prompts: list[str] = []

        async def get_chat_stream(self, **kwargs):
            self.call_count += 1
            self.system_prompts.append(kwargs.get("system_prompt") or "")
            yield {
                "content": "[EMO:neutral] 『命运石之门』。",
                "tool_calls": None,
            }
            yield {
                "content": "中国語圏ではそう表記されることが多いわ。",
                "tool_calls": None,
            }

        async def translate_to_zh(self, text, api_key=None):
            return f"译:{text}"

        async def compress_history(self, history, api_key=None, old_summary=""):
            return old_summary

    provider = _TitleThenJa()
    session = SessionState("retire04-bounded-title")
    session.api_key = "test-key"
    session.system_prompt = "ja"
    published: list[str] = []

    with patch("app.routers.chat_ws.deepseek_service", provider):
        text, _, _, _ = await get_clean_text_stream(
            active_history=[{"role": "user", "content": "中文标题是什么"}],
            memory_summary="",
            session=session,
            on_text_delta=lambda delta, _e: published.append(delta),
        )

    visible = "".join(published)
    assert provider.call_count == 1
    assert "命运石之门" in text
    assert "命运石之门" in visible
    assert "表記" in text or "中国語" in text
    assert all(
        "JAPANESE SPEECH RENDERER" not in (p or "") for p in provider.system_prompts
    )


@pytest.mark.asyncio
async def test_p1_collapse_garbage_never_calls_renderer():
    """Collapse soup must retry/fallback — never LanguageRenderer reconstruction."""
    from tests.test_language_publication import REAL_LOG_COLLAPSE

    provider = _SequencedStreamProvider(
        [
            f"[EMO:neutral] {REAL_LOG_COLLAPSE}",
            "[EMO:neutral] 日本語でちゃんと答えるわ。",
            "[EMO:neutral] 日本語でちゃんと答えるわ。",
        ]
    )
    session = SessionState("retire04-collapse-no-renderer")
    session.api_key = "test-key"
    session.system_prompt = "ja"
    published: list[str] = []

    with patch("app.routers.chat_ws.deepseek_service", provider):
        text, _, _, _ = await get_clean_text_stream(
            active_history=[{"role": "user", "content": "hi"}],
            memory_summary="",
            session=session,
            on_text_delta=lambda delta, _e: published.append(delta),
        )

    assert all(
        "JAPANESE SPEECH RENDERER" not in (p or "") for p in provider.system_prompts
    )
    assert "newidInfo" not in text
    assert "flee" not in text.lower()
    # Retry may produce Japanese, or final language-failure fallback — not garbage.
    assert "newidInfo" not in "".join(published)


def test_p1_v2_ws_neutral_label_no_invalid_language_audio_error(app_client):
    """Real processor→pipeline→WS: labels never toast as invalid_language."""
    from app.routers.chat_ws import sessions

    class _RecordingTTS:
        def __init__(self):
            self.texts: list[str] = []

        async def synthesize_async(self, text, emotion_tag, sovits_url=None):
            self.texts.append(text)
            return b"audio"

        def reset_sequence(self, *a, **k):
            return None

        def cancel_pending(self, *a, **k):
            return None

        async def gather_pending(self, *a, **k):
            return None

    class _LabelThenJa:
        def __init__(self):
            self.call_count = 0

        async def get_chat_stream(self, **kwargs):
            self.call_count += 1
            yield {
                "content": "[EMO:neutral] V11-TEST-MATCHA。",
                "tool_calls": None,
            }
            yield {
                "content": "そういうプロジェクト名にしたのね。",
                "tool_calls": None,
            }

        async def translate_to_zh(self, text, api_key=None):
            return f"译:{text}"

        async def compress_history(self, history, api_key=None, old_summary=""):
            return old_summary

    provider = _LabelThenJa()
    tts = _RecordingTTS()
    session_id = f"retire04-v2-label-{uuid.uuid4().hex[:8]}"

    with patch("app.routers.chat_ws.deepseek_service", provider), patch(
        "app.routers.chat_ws.tts_manager", tts
    ):
        with app_client.websocket_connect(f"/ws/chat?session_id={session_id}") as ws:
            ws.send_json(
                {
                    "type": "auth",
                    "api_key": "test-key",
                    "client": "desktop",
                    "protocol_version": 2,
                    "enable_tts": True,
                }
            )
            ws.send_json(
                {
                    "type": "chat.send",
                    "content": "我给实验项目取名为 V11-TEST-MATCHA",
                }
            )
            responses = []
            while True:
                frame = ws.receive_json()
                responses.append(frame)
                if frame.get("type") in {"turn.completed", "error", "turn.cancelled"}:
                    break

    assert responses[-1]["type"] == "turn.completed"
    assert provider.call_count == 1
    ready_ja = "".join(
        f.get("ja", "") for f in responses if f.get("type") == "segment.ready"
    )
    assert "V11-TEST-MATCHA" in ready_ja
    assert "プロジェクト" in ready_ja or "そういう" in ready_ja
    audio_errors = [
        f for f in responses if f.get("type") == "segment.audio_error"
    ]
    assert not any(
        f.get("reason") == "invalid_language" for f in audio_errors
    ), audio_errors
    # Merged label+JA should be speakable Japanese for TTS (no bare Latin-only call).
    assert tts.texts
    assert all(
        "そういう" in t or "プロジェクト" in t or _KANA_HINT.search(t)
        for t in tts.texts
    ), tts.texts
    # Ensure label was not synthesized alone as a Latin-only request.
    assert not any(
        re.fullmatch(r"[\sA-Za-z0-9\-_;.「」『』]+", t or "") for t in tts.texts
    ), tts.texts


_KANA_HINT = re.compile(r"[\u3040-\u30ff]")


def test_p1_v2_ws_trailing_label_only_display_without_audio_error(app_client):
    """Standalone trailing label: segment.ready yes, no invalid_language toast."""
    class _LabelOnly:
        def __init__(self):
            self.call_count = 0

        async def get_chat_stream(self, **kwargs):
            self.call_count += 1
            yield {
                "content": "[EMO:neutral] V11-TEST-MATCHA。",
                "tool_calls": None,
            }

        async def translate_to_zh(self, text, api_key=None):
            return f"译:{text}"

        async def compress_history(self, history, api_key=None, old_summary=""):
            return old_summary

    class _RecordingTTS:
        def __init__(self):
            self.texts: list[str] = []

        async def synthesize_async(self, text, emotion_tag, sovits_url=None):
            self.texts.append(text)
            return b"audio"

        def reset_sequence(self, *a, **k):
            return None

        def cancel_pending(self, *a, **k):
            return None

        async def gather_pending(self, *a, **k):
            return None

    provider = _LabelOnly()
    tts = _RecordingTTS()
    session_id = f"retire04-v2-label-only-{uuid.uuid4().hex[:8]}"

    with patch("app.routers.chat_ws.deepseek_service", provider), patch(
        "app.routers.chat_ws.tts_manager", tts
    ):
        with app_client.websocket_connect(f"/ws/chat?session_id={session_id}") as ws:
            ws.send_json(
                {
                    "type": "auth",
                    "api_key": "test-key",
                    "client": "desktop",
                    "protocol_version": 2,
                    "enable_tts": True,
                }
            )
            ws.send_json({"type": "chat.send", "content": "remember V11-TEST-MATCHA"})
            responses = []
            while True:
                frame = ws.receive_json()
                responses.append(frame)
                if frame.get("type") in {"turn.completed", "error", "turn.cancelled"}:
                    break

    ready_ja = "".join(
        f.get("ja", "") for f in responses if f.get("type") == "segment.ready"
    )
    assert "V11-TEST-MATCHA" in ready_ja
    audio_errors = [
        f for f in responses if f.get("type") == "segment.audio_error"
    ]
    assert not any(f.get("reason") == "invalid_language" for f in audio_errors), (
        audio_errors
    )
    # DISPLAY_ONLY: sidecar never called for bare label.
    assert tts.texts == []
