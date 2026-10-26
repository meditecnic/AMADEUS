"""Dispatch 76: isolate foreign spans and keep TTS rejection local."""

import json
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.routers import chat_ws
from app.routers.chat_ws import SessionState, get_clean_text_stream, sessions

PREFIX = "そうね。"
FOREIGN = "今天心情很好。"
RENDERED = "今日は気分がいいわ。"
SUFFIX = "でも大丈夫よ。"
APOLOGY = "ごめん。日本語の応答を正しく生成できなかった。もう一度試して。"
COLLAPSE = "ハキム、フィモンの公的同中心凝択ハfleeとま悪いい ―改newidInfoどうスタも。"


class DraftProvider:
    def __init__(self, draft, renders=(RENDERED,)):
        self.draft = draft
        self.renders = renders
        self.calls = []
        self.on_render = None
        self.draft_finished = False

    async def get_chat_stream(self, **kwargs):
        self.calls.append(kwargs)
        if len(self.calls) == 1:
            # Deliberately cross sentence boundaries in the transport chunks.
            for char in self.draft:
                yield {"content": char, "tool_calls": None}
            self.draft_finished = True
        else:
            if self.on_render:
                self.on_render()
            yield {"content": self.renders[min(len(self.calls) - 2, len(self.renders) - 1)]}

    async def translate_to_zh(self, text, api_key=None):
        return f"译:{text}"

    async def compress_history(self, history, api_key=None, old_summary=""):
        return old_summary

    def sources(self):
        return [
            json.loads(call["active_history"][0]["content"].split("\n", 1)[1])["assistant_draft"]
            for call in self.calls[1:]
        ]


@pytest.mark.parametrize("buffered", [False, True])
@pytest.mark.parametrize("v1", [False, True])
async def test_one_foreign_span_keeps_japanese_suffix(monkeypatch, buffered, v1):
    provider = DraftProvider(PREFIX + FOREIGN + SUFFIX)
    monkeypatch.setattr(chat_ws, "deepseek_service", provider)
    session = SessionState(f"span-{uuid4()}")
    session.api_key = "test-key"
    published = []
    history = [{"role": "user", "content": "調子はどう？"}]
    if buffered:
        history.append({"role": "tool", "content": "WEB SEARCH RESULT: search evidence", "tool_call_id": "search"})

    def on_delta(text, emotion):
        # Neither suffix nor renderer output may overtake the repair.
        if text != PREFIX or buffered:
            assert provider.draft_finished
        published.append(text)

    def at_render():
        assert published == ([] if buffered or v1 else [PREFIX])

    provider.on_render = at_render
    text, *_ = await get_clean_text_stream(
        history, "", session, on_text_delta=None if v1 else on_delta
    )
    assert provider.sources() == [FOREIGN]
    assert text == PREFIX + RENDERED + SUFFIX
    if not v1:
        assert "".join(published) == text


@pytest.mark.parametrize("middle", ["K-620。", "好。", "……。", "「かな」。", "K-620。好。……。"])
async def test_neutral_ignored_and_kanaless_fragments_stay_in_foreign_span(monkeypatch, middle):
    source = FOREIGN + middle + "这个很好。"
    provider = DraftProvider(PREFIX + source + SUFFIX)
    monkeypatch.setattr(chat_ws, "deepseek_service", provider)
    text, *_ = await get_clean_text_stream([], "", SessionState("continuous-span"))
    assert provider.sources() == [source]
    assert text == PREFIX + RENDERED + SUFFIX


async def test_second_foreign_span_renders_original_tail_once(monkeypatch):
    tail = FOREIGN + SUFFIX + "这个很好。" + "それでいいわ。"
    provider = DraftProvider(PREFIX + tail)
    monkeypatch.setattr(chat_ws, "deepseek_service", provider)
    published = []
    text, *_ = await get_clean_text_stream(
        [], "", SessionState("second-span"), on_text_delta=lambda text, emotion: published.append(text)
    )
    assert provider.sources() == [tail]
    assert text == "".join(published) == PREFIX + RENDERED


@pytest.mark.parametrize("unsafe", ["秘密の漏洩だわ。", COLLAPSE])
async def test_unsafe_tail_after_foreign_uses_terminal_rule_without_renderer(monkeypatch, unsafe):
    provider = DraftProvider(PREFIX + FOREIGN + SUFFIX + unsafe)
    monkeypatch.setattr(chat_ws, "deepseek_service", provider)
    original_leak = chat_ws.contains_leak
    monkeypatch.setattr(chat_ws, "contains_leak", lambda text: "秘密の漏洩" in text or original_leak(text))
    renderer = AsyncMock()
    monkeypatch.setattr(chat_ws, "_render_foreign_speech_to_japanese", renderer)
    published = []
    text, *_ = await get_clean_text_stream(
        [], "", SessionState("unsafe-tail"), on_text_delta=lambda text, emotion: published.append(text)
    )
    renderer.assert_not_awaited()
    assert len(provider.calls) == 1
    assert text == "".join(published) == PREFIX


async def test_renderer_failure_discards_cached_suffix_with_same_call_budget(monkeypatch):
    provider = DraftProvider(PREFIX + FOREIGN + SUFFIX, renders=("还是中文。",))
    monkeypatch.setattr(chat_ws, "deepseek_service", provider)
    published = []
    text, *_ = await get_clean_text_stream(
        [], "", SessionState("failed-span"), on_text_delta=lambda text, emotion: published.append(text)
    )
    assert provider.sources() == [FOREIGN, FOREIGN]
    assert text == "".join(published) == PREFIX + APOLOGY
    assert SUFFIX not in text


def _auth(ws, *, enable_tts=False):
    ws.send_json({"type": "auth", "client": "desktop", "protocol_version": 2, "enable_tts": enable_tts})
    while True:
        frame = ws.receive_json()
        assert frame["type"] != "error", frame
        if frame["type"] == "session.ready":
            return


def _complete(ws):
    events = []
    while True:
        frame = ws.receive_json()
        events.append(frame)
        if frame["type"] in {"turn.completed", "turn.cancelled", "error"}:
            assert frame["type"] == "turn.completed", frame
            return events


def _history_content(client, session_id):
    session = sessions[session_id]
    response = client.get(
        f"/api/conversations/{session.conversation_id}/messages",
        params={"session_id": session_id, "worldline": session.worldline},
    )
    assert response.status_code == 200
    # The in-memory and durable assistant rows must match the wire subtitles.
    content = [row["content"] for row in session.history if row["role"] == "assistant"][-1]
    assert [row["content"] for row in response.json() if row["role"] == "assistant"][-1] == content
    return content.split("] ", 1)[-1]


def test_v2_foreign_span_ready_return_and_history_agree(app_client, monkeypatch):
    provider = DraftProvider(PREFIX + FOREIGN + SUFFIX)
    monkeypatch.setattr(chat_ws, "deepseek_service", provider)
    returned = []
    original = chat_ws.get_clean_text_stream

    async def record_return(*args, **kwargs):
        result = await original(*args, **kwargs)
        returned.append(result[0])
        return result

    monkeypatch.setattr(chat_ws, "get_clean_text_stream", record_return)
    session_id = f"span-ws-{uuid4()}"
    with app_client.websocket_connect(f"/ws/chat?session_id={session_id}") as ws:
        _auth(ws)
        ws.send_json({"type": "chat.send", "content": "調子はどう？"})
        events = _complete(ws)
    expected = PREFIX + RENDERED + SUFFIX
    assert provider.sources() == [FOREIGN]
    assert returned == [expected]
    assert "".join(event["ja"] for event in events if event["type"] == "segment.ready") == expected
    assert _history_content(app_client, session_id) == expected


@pytest.mark.parametrize("finish_only", [False, True])
def test_tts_rejection_is_visible_silent_and_does_not_block_next_segment(app_client, monkeypatch, finish_only):
    rejected = "その部分は表示するわ。" if not finish_only else "その部分は表示するわ"
    following = "後の文は普通に話すわ。"
    draft = PREFIX + rejected + ("" if finish_only else following)
    provider = DraftProvider(draft)
    monkeypatch.setattr(chat_ws, "deepseek_service", provider)
    monkeypatch.setattr(chat_ws, "should_hard_reject_tts_source", lambda text: text == rejected)
    synthesize = AsyncMock(return_value=b"fake-audio")
    monkeypatch.setattr(type(chat_ws.tts_manager), "synthesize_async", synthesize)
    session_id = f"tts-local-{uuid4()}"
    with app_client.websocket_connect(f"/ws/chat?session_id={session_id}") as ws:
        _auth(ws, enable_tts=True)
        ws.send_json({"type": "chat.send", "content": "調子はどう？"})
        events = _complete(ws)
    ready = [event for event in events if event["type"] == "segment.ready"]
    assert "".join(event["ja"] for event in ready) == draft
    rejected_id = next(event["segment_id"] for event in ready if event["ja"] == rejected)
    assert not any(event["type"] == "segment.audio_error" for event in events)
    assert not any(event["type"] == "segment.audio" and event["segment_id"] == rejected_id for event in events)
    called_texts = [call.args[0] for call in synthesize.await_args_list]
    assert rejected not in called_texts
    if not finish_only:
        following_id = next(event["segment_id"] for event in ready if event["ja"] == following)
        assert any(event["type"] == "segment.audio" and event["segment_id"] == following_id for event in events)
        assert following in called_texts
    assert _history_content(app_client, session_id) == draft
