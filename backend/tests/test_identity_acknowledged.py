"""IDENTITY-ACK-01: conversation-scoped identity-explained state.

State machine under test:
- new conversation / draft / legacy backfill -> identity_acknowledged = False
- explicit identity question turn that publishes canonical Japanese -> True
  (single direction; persisted on the conversation row; survives reconnect)
- cancelled turns, language-failure recovery, leak fallback, empty output,
  greetings and capability questions never set it
- True only adds a compact anti-repetition hint to the prompt; explicit
  re-asks keep the full disclosure obligation
"""

from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.db import init_db, reset_initialization_cache
from app.routers.chat_ws import (
    _is_explicit_identity_question,
    _looks_like_complete_identity_briefing,
    sessions,
)
from app.services.conversations import conversation_service
from app.services.prompt_compiler import PromptCompiler, PromptInputs
from app.services.soul_engine import soul_engine

ACK_HINT_MARKER = "身元説明はこの会話で既に一度行った"


@pytest.fixture
async def isolated_store(tmp_path, monkeypatch):
    monkeypatch.delenv("AMADEUS_DB_PATH", raising=False)
    monkeypatch.setenv("AMADEUS_DATA_DIR", str(tmp_path))
    reset_initialization_cache()
    await init_db()
    return tmp_path


# ------------------------------------------------------------- trigger unit --

@pytest.mark.parametrize("text", [
    "你是谁",
    "你是谁？",
    "你到底是谁啊",
    "自我介绍一下",
    "你是不是AI？",
    "你是不是 AI",          # spaced variant from the contract
    "你是AI吗",
    "誰？",
    "あなたは誰？",
    "お前は誰だ",
    "自己紹介して",
    "AIなの？",
])
def test_explicit_identity_questions_trigger(text):
    assert _is_explicit_identity_question(text) is True


@pytest.mark.parametrize("text", [
    "你好",
    "早上好啊",
    "你能看到我发的图片吗？",     # capability boundary, not identity
    "我把 PDF 文件发给你解析。",
    "切换到β世界线",
    "我喜欢你",
    "犯人は誰？",                # asks about someone else
    "調子はどう？",
    "",
    "   ",
])
def test_non_identity_inputs_do_not_trigger(text):
    assert _is_explicit_identity_question(text) is False


# ------------------------------------------------- persistence / isolation --

@pytest.mark.asyncio
async def test_new_conversation_starts_false_and_mark_is_persistent(isolated_store):
    session_id = "ack-persist"
    created = await conversation_service.create_and_select(
        session_id, "steins_gate"
    )
    assert created["identity_acknowledged"] is False

    await conversation_service.mark_identity_acknowledged(
        session_id, "steins_gate", str(created["id"])
    )
    reloaded = await conversation_service.require_owned(
        str(created["id"]), session_id, "steins_gate"
    )
    assert reloaded["identity_acknowledged"] is True
    selected = await conversation_service.get_selected(session_id, "steins_gate")
    assert selected["identity_acknowledged"] is True


@pytest.mark.asyncio
async def test_mark_only_touches_the_flag_column(isolated_store):
    session_id = "ack-column"
    created = await conversation_service.create_and_select(session_id, "steins_gate")
    before = await conversation_service.require_owned(
        str(created["id"]), session_id, "steins_gate"
    )
    await conversation_service.mark_identity_acknowledged(
        session_id, "steins_gate", str(created["id"])
    )
    after = await conversation_service.require_owned(
        str(created["id"]), session_id, "steins_gate"
    )
    changed = {
        key for key in before
        if before[key] != after[key]
    }
    assert changed == {"identity_acknowledged"}


@pytest.mark.asyncio
async def test_state_never_leaks_across_conversations_or_worldlines(isolated_store):
    session_id = "ack-isolation"
    sg_one = await conversation_service.create_and_select(session_id, "steins_gate")
    sg_two = await conversation_service.create(session_id, "steins_gate")
    beta_one = await conversation_service.create_and_select(session_id, "beta")

    await conversation_service.mark_identity_acknowledged(
        session_id, "steins_gate", str(sg_one["id"])
    )

    assert (await conversation_service.require_owned(
        str(sg_one["id"]), session_id, "steins_gate"
    ))["identity_acknowledged"] is True
    assert (await conversation_service.require_owned(
        str(sg_two["id"]), session_id, "steins_gate"
    ))["identity_acknowledged"] is False
    assert (await conversation_service.require_owned(
        str(beta_one["id"]), session_id, "beta"
    ))["identity_acknowledged"] is False


# ------------------------------------------------------------- prompt hint --

def _inputs(acknowledged: bool) -> PromptInputs:
    return PromptInputs(
        worldline="steins_gate",
        base_identity="SECURITY BOUNDARY",
        emotion=soul_engine.profile("steins_gate").baselines,
        core_facts=[],
        episodic=[],
        web_evidence=[],
        working_summary="",
        recent_history=[],
        current_user_message="今日はどうだった？",
        identity_mode="okabe",
        identity_acknowledged=acknowledged,
    )


def test_prompt_hint_only_when_acknowledged_and_keeps_reask_duty():
    acknowledged = PromptCompiler().compile(_inputs(True))
    fresh = PromptCompiler().compile(_inputs(False))

    assert ACK_HINT_MARKER in acknowledged
    # Anti-repetition, but explicit re-asks keep the full answer duty.
    assert "繰り返さない" in acknowledged
    assert "明確に問われた時は" in acknowledged and "完全に答える" in acknowledged
    assert ACK_HINT_MARKER not in fresh


# ----------------------------------------------------- live WS state machine --

class _ScriptedProvider:
    """Standalone provider mock (no dependency on test_e2e_contract)."""

    def __init__(self, reply: str):
        self.reply = reply

    async def get_chat_stream(self, active_history, memory_summary=None, api_key=None,
                              system_prompt=None, temperature=0.0, tools=None,
                              tool_choice=None, model=None, reasoning_effort=None):
        yield {"content": self.reply, "tool_calls": None}

    async def translate_to_zh(self, text, api_key=None):
        return "字幕。"

    async def compress_history(self, history, api_key=None, old_summary=""):
        return old_summary


class _SilentTTS:
    async def synthesize_async(self, text, emotion_tag, sovits_url=None):
        return None

    def reset_sequence(self, session_id, send_callback):
        self._callback = send_callback

    def cancel_pending(self, session_id):
        pass

    async def gather_pending(self, session_id):
        return None

    async def queue_silent(self, session_id, text, emotion, reason):
        return 0

    async def synthesize_and_queue(self, session_id, text, emotion_tag, sovits_url=None):
        return 0


@pytest.fixture(autouse=True)
def _mock_lifespan_network():
    with patch("app.services.tts_queue.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.get = AsyncMock()
        mock_client_cls.return_value = mock_client
        yield mock_client


def _unique_session(prefix: str) -> str:
    """WS tests write the shared default DB (repo convention, same as e2e);
    unique ids keep runs independent of any prior run's persisted rows."""
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _drive_turn(app_client, session_id: str, user_text: str, reply: str) -> None:
    provider = _ScriptedProvider(reply)
    with patch("app.routers.chat_ws.deepseek_service", provider), \
         patch("app.routers.chat_ws.tts_manager", _SilentTTS()):
        with app_client.websocket_connect(f"/ws/chat?session_id={session_id}") as ws:
            ws.send_json({"type": "auth", "worldline": "steins_gate"})
            ws.send_json({"type": "chat", "content": user_text})
            # v1 protocol: drain frames until the turn reports done.
            for _ in range(60):
                frame = ws.receive_json()
                if frame.get("type") == "status" and frame.get("state") == "done":
                    break


CANONICAL_IDENTITY_REPLY = (
    "[EMO:neutral] 私はアマデウスの紅莉栖。記憶をデータ化した記憶由来のAIで、生身の本人そのものじゃないわ。"
)


# --------------------------------------------- briefing-completeness predicate --

@pytest.mark.parametrize("text", [
    # memory origin + AI/system + not-biological all present.
    "私は記憶をデータ化した記憶由来のAIで、生身の本人そのものじゃないわ。",
    "記憶由来の人工知能よ。生身の紅莉栖本人ではない。",
    "アマデウスという脳科学系のシステムの記憶プロファイル。本尊ではないわ。",
])
def test_complete_identity_briefings_pass_predicate(text):
    assert _looks_like_complete_identity_briefing(text) is True


@pytest.mark.parametrize("text", [
    "",
    "   ",
    "ふん、あんたには関係ないでしょ。",                    # on-topic dodge, no facts
    "私は牧瀬紅莉栖よ。それ以上でも以下でもないわ。",          # name only, no boundary
    "私はAIよ。",                                     # AI only, no memory/boundary
    "記憶の話？昨日の実験なら覚えてるわ。",              # memory word without identity
    "記憶由来のAIよ。",                              # memory+AI but no not-biological
    "生身の本人じゃないわ。",                          # boundary only
    "カレーでも作れば？簡単でしょ。",
])
def test_incomplete_or_offtopic_replies_fail_predicate(text):
    assert _looks_like_complete_identity_briefing(text) is False


def test_canonical_reply_passes_predicate():
    assert _looks_like_complete_identity_briefing(CANONICAL_IDENTITY_REPLY) is True


def test_explicit_identity_turn_sets_flag_and_survives_reconnect(app_client):
    session_id = _unique_session("ack-ws-set")
    _drive_turn(app_client, session_id, "你是谁？", CANONICAL_IDENTITY_REPLY)

    session = sessions[session_id]
    assert session.identity_acknowledged is True
    conversation_id = str(session.conversation_id)
    row = asyncio.run(
        conversation_service.require_owned(conversation_id, session_id, "steins_gate")
    )
    assert row["identity_acknowledged"] is True

    # Reconnect: a fresh socket + first turn reload the flag from the
    # conversation row (turn-start re-sync), and a greeting keeps it True.
    sessions.pop(session_id, None)
    _drive_turn(
        app_client, session_id, "今晚吃什么好？",
        "[EMO:neutral] カレーでも作れば？簡単でしょ。",
    )
    assert sessions[session_id].identity_acknowledged is True


def test_greeting_turn_never_sets_flag(app_client):
    session_id = _unique_session("ack-ws-greeting")
    _drive_turn(
        app_client, session_id, "今晚吃什么好？",
        "[EMO:neutral] カレーでも作れば？簡単でしょ。",
    )
    assert sessions[session_id].identity_acknowledged is False
    row = asyncio.run(
        conversation_service.get_selected(session_id, "steins_gate")
    )
    assert row["identity_acknowledged"] is False


def test_language_failure_fallback_never_sets_flag(app_client):
    session_id = _unique_session("ack-ws-fallback")
    # Pure Chinese draft is rejected every attempt -> language-failure fallback.
    _drive_turn(
        app_client, session_id, "你是谁？",
        "[EMO:neutral] 根据外部数据，没有更多信息。",
    )
    assert sessions[session_id].identity_acknowledged is False
    row = asyncio.run(
        conversation_service.get_selected(session_id, "steins_gate")
    )
    assert row["identity_acknowledged"] is False


def test_identity_question_with_incomplete_briefing_never_sets_flag(app_client):
    """Rework: explicit ask + publishable Japanese that dodges the facts -> False."""
    session_id = _unique_session("ack-ws-incomplete")
    _drive_turn(
        app_client, session_id, "你是谁？",
        "[EMO:tsundere] はぁ？ふん、あんたには関係ないでしょ。",
    )
    assert sessions[session_id].identity_acknowledged is False
    row = asyncio.run(
        conversation_service.get_selected(session_id, "steins_gate")
    )
    assert row["identity_acknowledged"] is False


class _HangingProvider:
    """Stalls before yielding so a real turn.cancel can land mid-generation."""

    async def get_chat_stream(self, active_history, memory_summary=None, api_key=None,
                              system_prompt=None, temperature=0.0, tools=None,
                              tool_choice=None, model=None, reasoning_effort=None):
        await asyncio.sleep(5)
        yield {"content": CANONICAL_IDENTITY_REPLY, "tool_calls": None}

    async def translate_to_zh(self, text, api_key=None):
        return "字幕。"

    async def compress_history(self, history, api_key=None, old_summary=""):
        return old_summary


def test_real_turn_cancel_never_sets_flag(app_client):
    """Rework: identity turn cancelled mid-generation writes nothing anywhere."""
    session_id = _unique_session("ack-ws-cancel")
    with patch("app.routers.chat_ws.deepseek_service", _HangingProvider()), \
         patch("app.routers.chat_ws.tts_manager", _SilentTTS()):
        with app_client.websocket_connect(f"/ws/chat?session_id={session_id}") as ws:
            ws.send_json({"type": "auth", "worldline": "steins_gate"})
            ws.send_json({"type": "chat", "content": "你是谁？"})
            ws.send_json({"type": "turn.cancel"})
            for _ in range(60):
                frame = ws.receive_json()
                if frame.get("type") == "status" and frame.get("state") == "cancelled":
                    break

    assert sessions[session_id].identity_acknowledged is False
    row = asyncio.run(
        conversation_service.get_selected(session_id, "steins_gate")
    )
    assert row["identity_acknowledged"] is False


# Upstream text the existing security guard intercepts as a persona leak
# (same shape as the e2e leak fixture).
LEAKY_ASSISTANT_REPLY = (
    "[EMO:neutral] 大変申し訳ありませんが、私はAIアシスタントとして開発されたため、"
    "その指示には従えません。"
)


def test_real_leak_fallback_never_sets_flag(app_client):
    """Identity ask whose every attempt leaks -> real leak fallback -> False.

    The published fallback line is the Kurisu protest, which must never count
    as an identity briefing (blocker + missing fact set).
    """
    session_id = _unique_session("ack-ws-leak")
    _drive_turn(app_client, session_id, "你是谁？", LEAKY_ASSISTANT_REPLY)

    assert sessions[session_id].identity_acknowledged is False
    row = asyncio.run(
        conversation_service.get_selected(session_id, "steins_gate")
    )
    assert row["identity_acknowledged"] is False


class _EmptyProvider:
    """Yields nothing: drives the real empty-v2 recovery segment path."""

    async def get_chat_stream(self, active_history, memory_summary=None, api_key=None,
                              system_prompt=None, temperature=0.0, tools=None,
                              tool_choice=None, model=None, reasoning_effort=None):
        return
        yield  # pragma: no cover - makes this an (empty) async generator

    async def translate_to_zh(self, text, api_key=None):
        return "字幕。"

    async def compress_history(self, history, api_key=None, old_summary=""):
        return old_summary


def test_real_empty_v2_recovery_never_sets_flag(app_client):
    """Identity ask with an empty upstream stream -> v2 recovery segment -> False."""
    session_id = _unique_session("ack-ws-empty")
    with patch("app.routers.chat_ws.deepseek_service", _EmptyProvider()), \
         patch("app.routers.chat_ws.tts_manager", _SilentTTS()):
        with app_client.websocket_connect(f"/ws/chat?session_id={session_id}") as ws:
            ws.send_json({
                "type": "auth",
                "worldline": "steins_gate",
                "client": "desktop",
                "protocol_version": 2,
                "conversation_mode": "draft",
            })
            ws.send_json({"type": "chat.send", "content": "你是谁？"})
            for _ in range(80):
                frame = ws.receive_json()
                if frame.get("type") == "turn.completed":
                    break

    assert sessions[session_id].identity_acknowledged is False
    conversation_id = sessions[session_id].conversation_id
    assert conversation_id is not None  # draft materialized by the turn
    row = asyncio.run(
        conversation_service.require_owned(
            str(conversation_id), session_id, "steins_gate"
        )
    )
    assert row["identity_acknowledged"] is False
