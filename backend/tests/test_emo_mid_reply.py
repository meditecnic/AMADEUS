"""[150] Exercise publication, speech, persistence and extraction boundaries offline."""

import asyncio
from unittest.mock import AsyncMock

import pytest

from app import models
from app.db import init_db
from app.domain.emotion_tags import EmotionTagFilter, strip_emotion_tags
from app.domain.japanese_response_renderer import normalize_rendered_japanese
from app.routers import chat_ws
from app.services.conversations import conversation_service
from app.services.memory_v11.jobs import _load_user_source
from app.services.turn_events import TurnModeState

BODY = "それはよかったわね。べ、別に心配してたわけじゃないけど。"
TAG = "[EMO:embarrassed]"
FIRST = "それはよかったわね。"
RAW = f"[EMO:happy] {FIRST}{TAG}{BODY[len(FIRST):]}"


@pytest.fixture
async def conversation():
    await init_db()
    row = await conversation_service.create_and_select(
        "emo-test", "steins_gate", identity_mode="okabe",
        provider_id="deepseek", model_id="deepseek-v4-flash",
    )
    return str(row["id"])


async def run_turn(monkeypatch, conversation, chunks, *, protocol=2, client="desktop"):
    async def stream(*args, **kwargs):
        for chunk in chunks:
            yield {"content": chunk}

    monkeypatch.setattr(type(chat_ws.deepseek_service), "get_chat_stream", stream)
    monkeypatch.setattr(chat_ws, "translate_with_provider", AsyncMock(return_value="中文。"))
    speech = AsyncMock(return_value=b"offline-audio")
    monkeypatch.setattr(type(chat_ws.tts_manager), "synthesize_async", staticmethod(speech))

    async def queue_speech(session_id, text, emotion, **kwargs):
        await speech(text, emotion)
        return await chat_ws.tts_manager.queue_silent(session_id, text, emotion, "offline-test")

    async def stream_speech(text, emotion, **kwargs):
        await speech(text, emotion)
        yield None, b"offline-pcm"

    monkeypatch.setattr(type(chat_ws.tts_manager), "synthesize_and_queue", staticmethod(queue_speech))
    monkeypatch.setattr(type(chat_ws.tts_manager), "stream_pcm", staticmethod(stream_speech))
    monkeypatch.setattr(type(conversation_service), "schedule_auto_title", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr("app.services.memory_v11.jobs.enqueue_memory_job", AsyncMock())
    session = chat_ws.SessionState("emo-test")
    session.client_type = client
    session.protocol_version = protocol
    session.enable_tts = True
    session.conversation_id = conversation
    session.api_key = "test-key"
    session.append_message({"role": "user", "content": "元気？", "id": None})
    queue = asyncio.Queue()
    await chat_ws.processor_loop(session, "元気？", queue, 0, 0)
    if session.background_tasks:
        await asyncio.gather(*session.background_tasks)
    events = [queue.get_nowait()[1] for _ in range(queue.qsize())]
    assert not [e for e in events if e.get("type") == "error"], events
    rows = await models.get_recent_messages("emo-test", conversation_id=conversation)
    return events, speech, rows, session


@pytest.mark.parametrize("boundary", ["ui", "tts", "history"])
async def test_turn_boundaries(monkeypatch, conversation, boundary):
    events, speech, rows, session = await run_turn(monkeypatch, conversation, [RAW])
    if boundary == "ui":
        text = "".join(e["ja"] for e in events if e.get("type") == "segment.ready")
        assert text == BODY
    elif boundary == "tts":
        assert "".join(c.args[0] for c in speech.call_args_list) == BODY
    else:
        assert rows[-1]["content"] == f"[EMO:happy] {BODY}"
        assert session.history[-1]["content"] == rows[-1]["content"]
    assert session.last_assistant_emotion == "happy"


@pytest.mark.parametrize("split", range(len(TAG) + 1))
async def test_every_tag_split(monkeypatch, conversation, split):
    chunks = [f"[EMO:happy] {FIRST}{TAG[:split]}", TAG[split:] + BODY[len(FIRST):]]
    events, speech, rows, session = await run_turn(monkeypatch, conversation, chunks)
    ui = [e["ja"] for e in events if e.get("type") == "segment.ready"]
    tts = [c.args[0] for c in speech.call_args_list]
    for parts in (ui, tts):
        assert "".join(parts) == BODY
        assert all("[" not in part for part in parts)
    assert rows[-1]["content"] == f"[EMO:happy] {BODY}"
    assert session.last_assistant_emotion == "happy"


def test_renderer_output():
    assert normalize_rendered_japanese(RAW) == BODY


async def test_v11_extraction_context(conversation):
    await models.save_message("emo-test", "assistant", RAW, conversation_id=conversation)
    mid = await models.save_message("emo-test", "user", "元気？", conversation_id=conversation)
    source = await _load_user_source(
        session_id="emo-test", worldline="steins_gate",
        conversation_id=conversation, source_message_id=mid,
    )
    assert source["previous_assistant_text"] == BODY


@pytest.mark.parametrize(("raw", "expected"), [
    ("前[EMO:Sad]後", "前後"),
    ("前 [emo:unknown_123]  後", "前 後"),
    ("前[EMO:" + "x" * 24 + "]後", "前後"),
    ("前[EMO:" + "x" * 25 + "]後", "前[EMO:" + "x" * 25 + "]後"),
    ("前[EMO]、[注意]、[EMOJI]後", "前[EMO]、[注意]、[EMOJI]後"),
    ("前 [EMO:sad][EMO:Unknown]  後", "前 後"),
    ("前[EMO:sad] [EMO:Unknown]後", "前 後"),
    ("前[EMO:sad", "前"),
    ("前[EM", "前"),
])
def test_tag_grammar_and_spacing(raw, expected):
    assert strip_emotion_tags(raw) == expected
    for split in range(len(raw) + 1):
        cleaner = EmotionTagFilter()
        assert cleaner.feed(raw[:split]) + cleaner.feed(raw[split:], final=True) == expected
    cleaner = EmotionTagFilter()
    assert "".join(cleaner.feed(char) for char in raw) + cleaner.feed("", final=True) == expected


@pytest.mark.parametrize("tail", ["[EMO:sad", "[EM", "[emo:" + "x" * 24])
async def test_unfinished_stream_tag(monkeypatch, conversation, tail):
    events, speech, rows, _ = await run_turn(monkeypatch, conversation, [RAW, tail])
    assert "".join(e["ja"] for e in events if e.get("type") == "segment.ready") == BODY
    assert "".join(c.args[0] for c in speech.call_args_list) == BODY
    assert rows[-1]["content"] == f"[EMO:happy] {BODY}"


async def test_only_first_tag_sets_emotion(monkeypatch, conversation):
    events, _, rows, session = await run_turn(
        monkeypatch, conversation, ["[emo:HAPPY][EMO:sad]" + BODY],
    )
    assert rows[-1]["content"] == f"[EMO:happy] {BODY}"
    assert {e["emotion"] for e in events if e.get("type") == "segment.ready"} == {"happy"}
    assert session.last_assistant_emotion == "happy"


async def test_emo_prefix_in_separate_chunk(monkeypatch, conversation):
    events, _, _, _ = await run_turn(monkeypatch, conversation, ["[EMO:happy]", BODY])
    segments = [e for e in events if e.get("type") == "segment.ready"]
    assert "".join(e["ja"] for e in segments) == BODY
    assert {e["emotion"] for e in segments} == {"happy"}


async def test_emo_prefix_split_inside_tag(monkeypatch, conversation):
    events, _, _, _ = await run_turn(monkeypatch, conversation, ["[EM", "O:happy]", BODY])
    segments = [e for e in events if e.get("type") == "segment.ready"]
    assert "".join(e["ja"] for e in segments) == BODY
    assert {e["emotion"] for e in segments} == {"happy"}


async def test_emo_prefix_before_tool_call_is_not_published(monkeypatch):
    call = {
        "id": "emo-prefix-tool", "type": "function",
        "function": {"name": "recall_memory", "arguments": "{}"},
    }

    async def stream(*args, **kwargs):
        yield {"content": "[EMO:happy]"}
        yield {"content": BODY, "tool_calls": [call]}

    monkeypatch.setattr(type(chat_ws.deepseek_service), "get_chat_stream", stream)
    publish = AsyncMock()
    mode = TurnModeState()
    text, _, calls, found = await chat_ws.get_clean_text_stream(
        [], "", chat_ws.SessionState("emo-prefix-tool"),
        on_text_delta=publish, mode_state=mode,
    )
    assert mode.mode == "tool_mode"
    assert calls == {call["id"]: call}
    assert text == ""
    assert not found
    publish.assert_not_awaited()


async def assert_tagged_turn_matches_plain(monkeypatch, conversation, *, protocol=2, client="desktop"):
    tagged = f"[EMO:happy] {FIRST}  [EMO:sad]  {BODY[len(FIRST):]}"
    plain = f"[EMO:happy] {FIRST} {BODY[len(FIRST):]}"
    snapshots = []
    for raw in (tagged, plain):
        events, speech, rows, _ = await run_turn(
            monkeypatch, conversation, [raw], protocol=protocol, client=client,
        )
        ui = ([e["ja"] for e in events if e.get("type") == "segment.ready"]
              if protocol >= 2 else [e["content"] for e in events if e.get("type") == "text_chunk"])
        tts = [call.args[0] for call in speech.call_args_list]
        assert ui and tts and rows[-1]["role"] == "assistant"
        snapshots.append((ui, tts, rows[-1]["content"]))
    assert snapshots[0] == snapshots[1]


async def test_sentence_gap_survives_publication_and_history(monkeypatch, conversation):
    await assert_tagged_turn_matches_plain(monkeypatch, conversation)


@pytest.mark.parametrize("client", ["desktop", "voice"])
async def test_v1_sentence_gap(monkeypatch, conversation, client):
    await assert_tagged_turn_matches_plain(monkeypatch, conversation, protocol=1, client=client)
