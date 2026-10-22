"""Audit #10: turn follow-up work runs as referenced, logged background tasks.

``asyncio`` keeps only weak references to tasks. The memory enqueue and the
history compression used to be started with a bare ``asyncio.create_task``;
they now go through ``_schedule_background_task`` so the session holds a
strong reference until they finish and a failure is logged instead of being
dropped. The cancelled-turn notice (``_persist_aborted_turn``) uses the same
helper, so it survives a second cancel that interrupts its shield.
"""

from __future__ import annotations

import asyncio
import gc
import logging
import weakref
from uuid import uuid4

import pytest

from fastapi import WebSocketDisconnect
from starlette.datastructures import Headers

from app.db import init_db, reset_initialization_cache
from app.routers import chat_ws
from app.routers.chat_ws import (
    SessionState,
    processor_loop,
    sessions,
    websocket_chat_endpoint,
)
from app.services.deepseek import build_stream_messages
from app.services.provider_runtime import deepseek_service

SG = "steins_gate"
REPLY_JA = "そうですね、それは良いですね。"
CHAT_LOGGER = "app.routers.chat_ws"


def _background_records(caplog) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.getMessage().startswith("[Background]")]


@pytest.fixture
async def isolated_store(tmp_path, monkeypatch):
    monkeypatch.delenv("AMADEUS_DB_PATH", raising=False)
    monkeypatch.setenv("AMADEUS_DATA_DIR", str(tmp_path))
    reset_initialization_cache()
    await init_db()
    yield tmp_path
    reset_initialization_cache()


# ---------------------------------------------------------------------------
# _schedule_background_task contract
# ---------------------------------------------------------------------------


async def test_scheduled_task_is_strongly_referenced_until_done():
    session = SessionState(f"bg-ref-{uuid4()}")
    release = asyncio.Event()

    async def _follow_up():
        await release.wait()
        return "ok"

    task_ref = weakref.ref(chat_ws._schedule_background_task(session, _follow_up()))
    gc.collect()

    task = task_ref()
    assert task is not None, "the session must keep the task alive"
    assert task in session.background_tasks
    assert task.get_name() == "chat-bg:test_scheduled_task_is_strongly_referenced_until_done.<locals>._follow_up"

    release.set()
    assert await asyncio.wait_for(task, 1) == "ok"
    await asyncio.sleep(0)
    assert task not in session.background_tasks, "finished tasks are released"


async def test_scheduled_task_failure_is_logged_with_type_only(caplog):
    session = SessionState(f"bg-fail-{uuid4()}")

    async def _boom():
        raise RuntimeError("provider said: user text must not reach the log")

    with caplog.at_level(logging.ERROR, logger=CHAT_LOGGER):
        task = chat_ws._schedule_background_task(session, _boom())
        await asyncio.gather(task, return_exceptions=True)
        await asyncio.sleep(0)

    records = _background_records(caplog)
    assert len(records) == 1
    message = records[0].getMessage()
    assert records[0].levelno == logging.ERROR
    assert "_boom" in message and "RuntimeError" in message
    assert "user text" not in message, "exception text may carry chat content"
    assert not session.background_tasks


async def test_cancelled_or_successful_tasks_are_not_reported(caplog):
    session = SessionState(f"bg-quiet-{uuid4()}")

    async def _ok():
        return None

    async def _forever():
        await asyncio.Event().wait()

    with caplog.at_level(logging.DEBUG, logger=CHAT_LOGGER):
        ok_task = chat_ws._schedule_background_task(session, _ok())
        slow_task = chat_ws._schedule_background_task(session, _forever())
        await ok_task
        slow_task.cancel()
        await asyncio.gather(slow_task, return_exceptions=True)
        await asyncio.sleep(0)

    assert _background_records(caplog) == []
    assert not session.background_tasks


# ---------------------------------------------------------------------------
# Both processor_loop call sites use it (real turn, fake provider)
# ---------------------------------------------------------------------------


def _install_fake_provider(monkeypatch) -> None:
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

    # Patch the classes, not the shared instances: undoing an instance patch
    # would leave the original bound method shadowing later class patches.
    monkeypatch.setattr(type(deepseek_service), "get_chat_stream", fake_get_chat_stream)
    monkeypatch.setattr(type(deepseek_service), "translate_to_zh", fake_translate_to_zh)


def _suppress_auto_title(monkeypatch) -> None:
    from app.services.conversations import conversation_service

    def fake_schedule(self, *args, **kwargs):
        async def _noop():
            return None

        return asyncio.get_running_loop().create_task(_noop())

    monkeypatch.setattr(type(conversation_service), "schedule_auto_title", fake_schedule)


async def _new_session() -> SessionState:
    from app.services.conversations import conversation_service

    session = SessionState(f"bg-turn-{uuid4()}")
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


async def _run_turn(session: SessionState, user_msg: str) -> list[dict]:
    session.append_message({"role": "user", "content": user_msg, "id": None})
    send_queue: asyncio.Queue = asyncio.Queue()
    await processor_loop(
        session, user_msg, send_queue, session.current_epoch, session.history_epoch
    )
    events = []
    while not send_queue.empty():
        events.append(send_queue.get_nowait()[1])
    return events


async def _until(predicate, timeout: float = 2.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        assert asyncio.get_running_loop().time() < deadline, "condition not reached"
        await asyncio.sleep(0.01)


async def test_memory_enqueue_and_compression_tasks_are_held_and_failures_logged(
    isolated_store, monkeypatch, caplog
):
    import app.services.memory_v11.jobs as jobs

    monkeypatch.setenv("AMADEUS_MEMORY_MODE", "shadow")
    monkeypatch.setattr(chat_ws.config, "HISTORY_COMPRESS_THRESHOLD", 0)
    _install_fake_provider(monkeypatch)
    _suppress_auto_title(monkeypatch)

    release = asyncio.Event()
    running: dict[str, asyncio.Task] = {}

    async def fake_enqueue_memory_job(**kwargs):
        running["enqueue"] = asyncio.current_task()
        await release.wait()

    async def fake_compress(session_id, current_history, *args, **kwargs):
        running["compress"] = asyncio.current_task()
        await release.wait()
        raise RuntimeError("compression detail that must not be logged")

    monkeypatch.setattr(jobs, "enqueue_memory_job", fake_enqueue_memory_job)
    monkeypatch.setattr(chat_ws, "compress_and_update_history", fake_compress)

    session = await _new_session()
    events = await _run_turn(session, "このすば")
    assert any(e.get("type") == "status" and e.get("state") == "done" for e in events)
    assert not [e for e in events if e.get("type") == "error"], events

    await _until(lambda: {"enqueue", "compress"} <= running.keys())
    gc.collect()
    assert running["enqueue"] in session.background_tasks
    assert running["compress"] in session.background_tasks
    assert session.compressing is True

    with caplog.at_level(logging.ERROR, logger=CHAT_LOGGER):
        release.set()
        await asyncio.gather(*running.values(), return_exceptions=True)
        await asyncio.sleep(0)

    assert not session.background_tasks, "finished tasks are released"
    records = _background_records(caplog)
    assert len(records) == 1, [r.getMessage() for r in records]
    message = records[0].getMessage()
    assert "fake_compress" in message and "RuntimeError" in message
    assert "must not be logged" not in message


async def test_aborted_turn_notice_is_held_when_the_shield_is_interrupted(
    isolated_store, monkeypatch
):
    """The cancelled-turn notice survives a second cancel and still persists."""
    from app import models

    _suppress_auto_title(monkeypatch)
    stream_started = asyncio.Event()

    async def blocking_get_chat_stream(self, *args, **kwargs):
        stream_started.set()
        await asyncio.Event().wait()  # cancelled from outside
        yield {"content": "", "tool_calls": None}

    monkeypatch.setattr(type(deepseek_service), "get_chat_stream", blocking_get_chat_stream)

    release = asyncio.Event()
    running: dict[str, asyncio.Task] = {}
    original_persist = chat_ws._persist_aborted_turn

    async def gated_persist(**kwargs):
        running["notice"] = asyncio.current_task()
        await release.wait()
        await original_persist(**kwargs)

    monkeypatch.setattr(chat_ws, "_persist_aborted_turn", gated_persist)

    session = await _new_session()
    user_msg = "途中で切れる質問"
    session.append_message({"role": "user", "content": user_msg, "id": None})
    processor = asyncio.create_task(
        processor_loop(
            session, user_msg, asyncio.Queue(), session.current_epoch, session.history_epoch
        )
    )
    await asyncio.wait_for(stream_started.wait(), 2)

    processor.cancel()  # turn cancelled after the user row was saved
    await _until(lambda: "notice" in running)
    processor.cancel()  # interrupts the shield; the notice must keep running
    with pytest.raises(asyncio.CancelledError):
        await processor

    gc.collect()
    notice = running["notice"]
    assert not notice.done()
    assert notice in session.background_tasks, "the session must keep the notice alive"
    assert notice.get_name().startswith("chat-bg:") and "gated_persist" in notice.get_name()

    release.set()
    await asyncio.wait_for(notice, 2)
    await asyncio.sleep(0)
    assert notice not in session.background_tasks, "finished tasks are released"

    rows = await models.get_session_messages(
        session.session_id, worldline=SG, conversation_id=session.conversation_id
    )
    assert [row["role"] for row in rows] == ["user", "assistant"], rows
    assert chat_ws._ABORTED_TURN_JA in rows[1]["content"]


# ---------------------------------------------------------------------------
# Real disconnect (PR #10 review, item 1): the reader exits, its finally
# cancels the current turn, and follow-up work is not cancelled with it.
# ---------------------------------------------------------------------------


class _ClientSocket:
    """Minimal WebSocket double: frames in, frames out; ``None`` disconnects."""

    def __init__(self, session_id: str):
        self.query_params = {"session_id": session_id}
        self.headers = Headers({"origin": "http://localhost"})
        self.incoming: asyncio.Queue = asyncio.Queue()
        self.outgoing: asyncio.Queue = asyncio.Queue()

    async def accept(self):
        return None

    async def receive_json(self):
        frame = await self.incoming.get()
        if frame is None:
            raise WebSocketDisconnect()
        return frame

    async def send_json(self, data):
        await self.outgoing.put(data)

    async def close(self, code=1000, reason=None):
        await self.incoming.put(None)

    async def wait_for_frame(self, predicate, timeout: float = 5.0) -> dict:
        while True:
            frame = await asyncio.wait_for(self.outgoing.get(), timeout)
            if predicate(frame):
                return frame


async def test_follow_up_tasks_outlive_a_real_disconnect(isolated_store, monkeypatch):
    import app.services.memory_v11.jobs as jobs
    from app import models

    monkeypatch.setenv("AMADEUS_MEMORY_MODE", "shadow")
    monkeypatch.setattr(chat_ws.config, "HISTORY_COMPRESS_THRESHOLD", 0)
    _suppress_auto_title(monkeypatch)

    block_stream = {"on": False}
    blocked_stream_started = asyncio.Event()

    async def scripted_stream(self, *args, **kwargs):
        if block_stream["on"]:
            blocked_stream_started.set()
            await asyncio.Event().wait()  # only the disconnect cancels it
        yield {"content": REPLY_JA, "tool_calls": None}

    async def fake_translate_to_zh(self, text, api_key=None, model=None):
        return "中文翻译"

    monkeypatch.setattr(type(deepseek_service), "get_chat_stream", scripted_stream)
    monkeypatch.setattr(type(deepseek_service), "translate_to_zh", fake_translate_to_zh)

    release_follow_ups = asyncio.Event()
    release_notice = asyncio.Event()
    running: dict[str, asyncio.Task] = {}
    finished: set[str] = set()

    async def gated_enqueue(**kwargs):
        running["enqueue"] = asyncio.current_task()
        await release_follow_ups.wait()
        finished.add("enqueue")

    async def gated_compress(*args, **kwargs):
        running["compress"] = asyncio.current_task()
        await release_follow_ups.wait()
        finished.add("compress")

    original_persist = chat_ws._persist_aborted_turn

    async def gated_persist(**kwargs):
        running["notice"] = asyncio.current_task()
        await release_notice.wait()
        await original_persist(**kwargs)
        finished.add("notice")

    monkeypatch.setattr(jobs, "enqueue_memory_job", gated_enqueue)
    monkeypatch.setattr(chat_ws, "compress_and_update_history", gated_compress)
    monkeypatch.setattr(chat_ws, "_persist_aborted_turn", gated_persist)

    session_id = f"bg-disconnect-{uuid4()}"
    ws = _ClientSocket(session_id)
    endpoint = asyncio.create_task(websocket_chat_endpoint(ws))
    try:
        await ws.incoming.put(
            {"type": "auth", "api_key": "test-provider-credential", "enable_tts": False}
        )
        await ws.wait_for_frame(lambda f: f.get("type") == "session.ready")
        session = sessions[session_id]

        # Turn 1 completes and leaves memory enqueue + compression running.
        await ws.incoming.put({"type": "chat", "content": "一問目"})
        await ws.wait_for_frame(lambda f: f.get("type") == "status" and f.get("state") == "done")
        await _until(lambda: {"enqueue", "compress"} <= running.keys())

        # Turn 2 saves its user row, then the client disconnects mid-stream.
        block_stream["on"] = True
        await ws.incoming.put({"type": "chat", "content": "二問目"})
        await asyncio.wait_for(blocked_stream_started.wait(), 5)
        await ws.incoming.put(None)  # WebSocketDisconnect -> reader exits

        # The reader's finally cancelled the turn; it scheduled the notice.
        await _until(lambda: "notice" in running)
        assert not endpoint.done(), "finally is still waiting for the cancelled turn"
        assert set(running.values()) <= session.background_tasks

        release_notice.set()
        await asyncio.wait_for(endpoint, 5)  # connection fully torn down
        await asyncio.sleep(0)
        assert "notice" in finished
        assert running["notice"] not in session.background_tasks

        # After the disconnect the session object stays and keeps its tasks.
        gc.collect()
        assert sessions.get(session_id) is session
        for name in ("enqueue", "compress"):
            assert not running[name].done(), name
            assert running[name] in session.background_tasks, name

        release_follow_ups.set()
        await asyncio.wait_for(
            asyncio.gather(running["enqueue"], running["compress"]), 5
        )
        await asyncio.sleep(0)
        assert {"enqueue", "compress"} <= finished, "both ran to completion"
        assert not session.background_tasks, "finished tasks are released"

        rows = await models.get_session_messages(
            session_id, worldline=SG, conversation_id=session.conversation_id
        )
        assert [row["role"] for row in rows] == ["user", "assistant", "user", "assistant"], rows
        assert chat_ws._ABORTED_TURN_JA in rows[-1]["content"]
    finally:
        release_notice.set()
        release_follow_ups.set()
        if not endpoint.done():
            await ws.incoming.put(None)
            await asyncio.wait_for(endpoint, 5)
        sessions.pop(session_id, None)


# ---------------------------------------------------------------------------
# Memory enqueue errors are logged by type only (PR #10 review, item 3)
# ---------------------------------------------------------------------------

PRIVATE_TEXT = "ユーザーの秘密メモ-7f3a"


async def test_memory_enqueue_error_is_logged_without_its_message(
    isolated_store, monkeypatch, caplog, capsys
):
    import app.services.memory_v11.jobs as jobs

    monkeypatch.setenv("AMADEUS_MEMORY_MODE", "shadow")
    _install_fake_provider(monkeypatch)
    _suppress_auto_title(monkeypatch)
    attempts: dict[str, asyncio.Task] = {}

    async def failing_enqueue(**kwargs):
        attempts["task"] = asyncio.current_task()
        raise RuntimeError(f"queue rejected payload: {PRIVATE_TEXT}")

    monkeypatch.setattr(jobs, "enqueue_memory_job", failing_enqueue)

    session = await _new_session()
    with caplog.at_level(logging.DEBUG):
        events = await _run_turn(session, "このすば")
        await _until(lambda: "task" in attempts)
        await asyncio.wait_for(attempts["task"], 2)
        await asyncio.sleep(0)

    assert any(e.get("type") == "status" and e.get("state") == "done" for e in events)
    assert any(
        r.getMessage() == "[MemoryV11] enqueue failed (RuntimeError); chat continues"
        for r in caplog.records
    )
    assert PRIVATE_TEXT not in caplog.text
    captured = capsys.readouterr()
    assert PRIVATE_TEXT not in captured.out
    assert PRIVATE_TEXT not in captured.err
