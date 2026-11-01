"""[150] Exercise publication, speech, persistence and extraction boundaries offline."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app import models
from app.db import init_db
from app.domain.emotion_tags import EmotionTagFilter, strip_emotion_tags
from app.domain.japanese_response_renderer import normalize_rendered_japanese
from app.routers import chat_ws
from app.services.conversations import conversation_service
from app.services.memory import memory_service
from app.services.memory_v11.jobs import _load_user_source

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
    monkeypatch.setattr(chat_ws.tts_manager, "synthesize_async", speech)
    monkeypatch.setattr(conversation_service, "schedule_auto_title", lambda *a, **k: None)
    monkeypatch.setattr(memory_service, "schedule_turn_extraction", lambda **kwargs: None)
    session = chat_ws.SessionState("emo-test")
    session.client_type = client
    session.protocol_version = protocol
    session.enable_tts = protocol >= 2
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


async def test_legacy_extraction_context(monkeypatch, conversation):
    complete = AsyncMock(return_value={"facts": [], "working_summary": "NO_WORKING_CONTEXT"})
    snapshot = SimpleNamespace(
        require=lambda task: None, adapter=SimpleNamespace(complete_json=complete), model_id="fake",
    )
    await memory_service._extract_turn(
        "emo-test", "steins_gate", "元気？", RAW, [], None, snapshot,
        conversation_id=conversation,
    )
    payload = json.loads(complete.call_args.kwargs["messages"][-1]["content"])
    assert payload["messages"][-1]["content"] == BODY


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


async def test_sentence_gap_survives_publication_and_history(monkeypatch, conversation):
    raw = f"[EMO:happy] {FIRST}  [EMO:sad]  {BODY[len(FIRST):]}"
    expected = FIRST + " " + BODY[len(FIRST):]
    events, speech, rows, _ = await run_turn(monkeypatch, conversation, [raw])
    assert "".join(e["ja"] for e in events if e.get("type") == "segment.ready") == expected
    assert rows[-1]["content"] == f"[EMO:happy] {expected}"
    # Speech preflight continues to trim each individual utterance.
    assert "".join(c.args[0] for c in speech.call_args_list) == BODY


@pytest.mark.parametrize("client", ["desktop", "voice"])
async def test_v1_sentence_gap(monkeypatch, conversation, client):
    raw = f"[EMO:happy] {FIRST} [EMO:sad]  {BODY[len(FIRST):]}"
    expected = FIRST + " " + BODY[len(FIRST):]
    events, _, rows, _ = await run_turn(
        monkeypatch, conversation, [raw], protocol=1, client=client,
    )
    assert "".join(e["content"] for e in events if e.get("type") == "text_chunk") == expected
    assert rows[-1]["content"] == f"[EMO:happy] {expected}"
