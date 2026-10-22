"""Audit #11: history compression backs off after consecutive failures.

A failed compression used to be re-requested on every following turn. Now
each consecutive failure doubles the number of eligible turns skipped before
the next request (capped), a success resets the backoff, and while backing
off the in-memory prompt window is still bounded.
"""

from __future__ import annotations

import asyncio
import logging
from uuid import uuid4

import pytest

from app.db import init_db, reset_initialization_cache
from app.routers import chat_ws
from app.routers.chat_ws import (
    SessionState,
    compress_and_update_history,
    processor_loop,
    sessions,
)
from app.services.conversations import conversation_service
from app.services.deepseek import build_stream_messages
from app.services.provider_registry import (
    ProviderCapabilities,
    ProviderSnapshot,
    ProviderTask,
)
from app.services.provider_runtime import deepseek_service

SG = "steins_gate"
REPLY_JA = "そうですね、それは良いですね。"
GOOD_SUMMARY = "比較中、候補Bの確認待ち"


@pytest.fixture
async def isolated_store(tmp_path, monkeypatch):
    monkeypatch.delenv("AMADEUS_DB_PATH", raising=False)
    monkeypatch.setenv("AMADEUS_DATA_DIR", str(tmp_path))
    reset_initialization_cache()
    await init_db()
    yield tmp_path
    reset_initialization_cache()


def _history(pairs: int) -> list[dict]:
    rows = []
    for index in range(pairs):
        rows.append({"role": "user", "content": f"質問{index}", "id": None})
        rows.append({"role": "assistant", "content": f"[EMO:neutral] 答え{index}よ。", "id": None})
    return rows


async def _new_session(prefix: str) -> SessionState:
    session = SessionState(f"{prefix}-{uuid4()}")
    session.worldline = SG
    session.identity_mode = "self"
    session.enable_tts = False
    session.api_key = "test-provider-credential"
    selected = await conversation_service.create_and_select(
        session.session_id,
        SG,
        provider_id="deepseek",
        model_id="deepseek-v4-flash",
        identity_mode="self",
    )
    session.conversation_id = str(selected["id"])
    return session


def _snapshot(adapter) -> ProviderSnapshot:
    return ProviderSnapshot(
        provider_id="test",
        model_id="test-compression",
        capabilities=ProviderCapabilities(tasks=frozenset({ProviderTask.COMPRESSION})),
        adapter=adapter,
    )


class _ScriptedCompressor:
    """compress_history that plays back results: str returns it, Exception raises."""

    def __init__(self, *results):
        self.results = list(results)
        self.calls = 0

    async def compress_history(self, history, old_summary="", **kwargs):
        self.calls += 1
        result = self.results.pop(0) if len(self.results) > 1 else self.results[0]
        if isinstance(result, BaseException):
            raise result
        return result


# ---------------------------------------------------------------------------
# Backoff schedule
# ---------------------------------------------------------------------------


def test_backoff_doubles_per_consecutive_failure_and_is_capped():
    assert [chat_ws._compression_backoff_turns(n) for n in range(0, 8)] == [
        0, 1, 2, 4, 8, 16, 16, 16,
    ]
    assert chat_ws._compression_backoff_turns(10_000) == chat_ws._COMPRESSION_BACKOFF_MAX_TURNS


# ---------------------------------------------------------------------------
# compress_and_update_history feeds the backoff
# ---------------------------------------------------------------------------


async def _compress_once(session: SessionState, adapter) -> None:
    sessions[session.session_id] = session
    session.compressing = True
    try:
        await compress_and_update_history(
            session.session_id,
            session.history,
            worldline=SG,
            revision=session.revision,
            conversation_id=session.conversation_id,
            provider_snapshot=_snapshot(adapter),
        )
    finally:
        sessions.pop(session.session_id, None)


async def test_failures_grow_backoff_and_success_resets_it(isolated_store, caplog):
    session = await _new_session("backoff-direct")
    session.history = _history(5)
    adapter = _ScriptedCompressor("", RuntimeError("provider down"), "", GOOD_SUMMARY)

    with caplog.at_level(logging.WARNING, logger="app.routers.chat_ws"):
        await _compress_once(session, adapter)  # empty result
        assert (session.compression_failures, session.compression_skip_turns) == (1, 1)
        await _compress_once(session, adapter)  # provider exception
        assert (session.compression_failures, session.compression_skip_turns) == (2, 2)
        await _compress_once(session, adapter)  # empty result again
        assert (session.compression_failures, session.compression_skip_turns) == (3, 4)
        assert len(session.history) == 10, "failed compression never trims"

    warnings = [r.getMessage() for r in caplog.records if "consecutive failure" in r.getMessage()]
    assert len(warnings) == 3
    assert all("provider down" not in message for message in warnings)

    await _compress_once(session, adapter)  # success
    assert (session.compression_failures, session.compression_skip_turns) == (0, 0)
    assert session.memory_summary == GOOD_SUMMARY
    assert session.compressing is False
    assert adapter.calls == 4


async def test_stale_snapshot_leaves_backoff_unchanged(isolated_store):
    session = await _new_session("backoff-stale")
    session.history = _history(5)
    session.compression_failures, session.compression_skip_turns = 2, 0
    adapter = _ScriptedCompressor(GOOD_SUMMARY)
    sessions[session.session_id] = session
    try:
        await compress_and_update_history(
            session.session_id,
            session.history,
            worldline=SG,
            revision=session.revision + 1,  # superseded before the request
            conversation_id=session.conversation_id,
            provider_snapshot=_snapshot(adapter),
        )
    finally:
        sessions.pop(session.session_id, None)
    assert adapter.calls == 0
    assert (session.compression_failures, session.compression_skip_turns) == (2, 0)


# ---------------------------------------------------------------------------
# History cap while backing off
# ---------------------------------------------------------------------------


async def test_backoff_cap_trims_oldest_in_memory_rows(monkeypatch, caplog):
    monkeypatch.setattr(chat_ws.config, "HISTORY_COMPRESS_THRESHOLD", 3)  # trigger = 8 rows
    session = SessionState(f"backoff-cap-{uuid4()}")
    session.history = _history(8)  # 16 rows == 2 * trigger: still allowed
    await chat_ws._cap_history_during_compression_backoff(session)
    assert len(session.history) == 16

    session.history = _history(9)  # 18 rows > 16
    with caplog.at_level(logging.WARNING, logger="app.routers.chat_ws"):
        await chat_ws._cap_history_during_compression_backoff(session)
    assert session.history == _history(9)[10:], "oldest rows dropped, newest 8 kept"
    assert any("dropped 10 oldest" in r.getMessage() for r in caplog.records)

    session.history = _history(9)
    session.compressing = True  # an in-flight compression owns the front slice
    await chat_ws._cap_history_during_compression_backoff(session)
    assert len(session.history) == 18


# ---------------------------------------------------------------------------
# Real turns: requests are spaced out, success resets (fake provider)
# ---------------------------------------------------------------------------


def _install_fake_chat(monkeypatch) -> None:
    async def fake_get_chat_stream(
        self,
        active_history=None,
        memory_summary=None,
        api_key=None,
        system_prompt=None,
        temperature=0.7,
        tools=None,
        tool_choice=None,
        model=None,
        reasoning_effort=None,
        isolated=False,
    ):
        build_stream_messages(
            deepseek_service.build_persona_messages,
            active_history,
            memory_summary,
            system_prompt,
            isolated=isolated,
        )
        yield {"content": REPLY_JA, "tool_calls": None}

    async def fake_translate_to_zh(self, text, api_key=None, model=None):
        return "中文翻译"

    async def no_enqueue(**kwargs):
        return None

    def no_auto_title(self, *args, **kwargs):
        async def _noop():
            return None

        return asyncio.get_running_loop().create_task(_noop())

    import app.services.memory_v11.jobs as jobs

    monkeypatch.setenv("AMADEUS_MEMORY_MODE", "shadow")
    monkeypatch.setattr(jobs, "enqueue_memory_job", no_enqueue)
    monkeypatch.setattr(type(conversation_service), "schedule_auto_title", no_auto_title)
    # Patch the classes, not the shared instances: undoing an instance patch
    # would leave the original bound method shadowing later class patches.
    monkeypatch.setattr(type(deepseek_service), "get_chat_stream", fake_get_chat_stream)
    monkeypatch.setattr(type(deepseek_service), "translate_to_zh", fake_translate_to_zh)


async def _turn(session: SessionState, text: str) -> list[dict]:
    session.append_message({"role": "user", "content": text, "id": None})
    send_queue: asyncio.Queue = asyncio.Queue()
    await processor_loop(session, text, send_queue, session.current_epoch, session.history_epoch)
    # Let the background compression (if any) finish before the next turn.
    if session.background_tasks:
        await asyncio.wait_for(
            asyncio.gather(*list(session.background_tasks), return_exceptions=True), 5
        )
    events = []
    while not send_queue.empty():
        events.append(send_queue.get_nowait()[1])
    return events


async def test_turns_back_off_after_failures_and_resume_after_success(
    isolated_store, monkeypatch
):
    _install_fake_chat(monkeypatch)
    monkeypatch.setattr(chat_ws.config, "HISTORY_COMPRESS_THRESHOLD", 3)  # trigger = 8 rows

    outcomes = {"result": ""}  # "" = provider gave no working context (failure)
    requested_on: list[int] = []
    current_turn = {"n": 0}

    async def fake_compress_history(self, history, api_key=None, old_summary="", model=None, identity_mode="okabe"):
        requested_on.append(current_turn["n"])
        return outcomes["result"]

    monkeypatch.setattr(type(deepseek_service), "compress_history", fake_compress_history)

    session = await _new_session("backoff-turns")
    session.history = _history(3)  # 6 rows; each turn adds 2
    sessions[session.session_id] = session
    lengths = []
    try:
        for turn in range(1, 13):
            current_turn["n"] = turn
            if turn == 7:
                outcomes["result"] = GOOD_SUMMARY  # provider recovers
            events = await _turn(session, f"質問です{turn}")
            assert any(e.get("type") == "status" and e.get("state") == "done" for e in events)
            assert not [e for e in events if e.get("type") == "error"], events
            lengths.append(len(session.history))
    finally:
        sessions.pop(session.session_id, None)

    # Failures on turns 1, 3, 6 back off 1, 2, then 4 eligible turns; the
    # recovered provider is retried on turn 11 and, with the backoff reset,
    # the very next eligible turn (12) compresses again.
    assert requested_on == [1, 3, 6, 11, 12]
    assert (session.compression_failures, session.compression_skip_turns) == (0, 0)
    assert session.memory_summary == GOOD_SUMMARY
    # Turn 7 exceeded 2 * trigger (20 > 16) while backing off -> trimmed to 8.
    assert lengths[6] == 8
    assert max(lengths) <= 2 * chat_ws._compression_trigger_messages() + 2
