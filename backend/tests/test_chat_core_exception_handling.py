"""Audit #12: broad ``except Exception`` handlers narrowed or made visible.

Covers the handlers whose behaviour changed: narrowed catches still cover
every failure they used to absorb on their path, and handlers that must keep
swallowing now leave a log record (exception type only, never its text).
"""

from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest

from app.domain import segments
from app.domain.segments import Segment, SegmentPipeline, TurnIdentity
from app.routers import chat_ws
from app.services import search as search_module
from app.services import session_manager
from app.services.search import search_service

SECRET_TEXT = "private detail that must not reach the log"


def _messages(caplog, logger_name: str) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.name == logger_name]


# ---------------------------------------------------------------------------
# chat_ws.execute_tool_call: narrowed to the JSON/shape errors it can raise
# ---------------------------------------------------------------------------


class _Rendered:
    failure_code = None

    def render(self) -> str:
        return "rendered"


@pytest.mark.parametrize(
    "raw",
    [
        "not json",
        "[1, 2]",  # valid JSON, not an object -> AttributeError
        '"plain string"',
        None,  # TypeError
        # Short explicit id: the 200k-char value would otherwise become the
        # node ID, and PYTEST_CURRENT_TEST overflows Windows' 32767-char limit.
        pytest.param("[" * 100_000 + "]" * 100_000, id="deep-nesting"),  # RecursionError
    ],
)
async def test_tool_call_bad_arguments_still_fall_back_to_raw_text(raw, monkeypatch):
    queries = []

    async def fake_search(self, query):
        queries.append(query)
        return _Rendered()

    monkeypatch.setattr(type(search_service), "search", fake_search)
    assert await chat_ws.execute_tool_call("web_search", raw) == "rendered"
    assert queries == [raw]


async def test_tool_call_json_object_uses_query_field(monkeypatch):
    queries = []

    async def fake_search(self, query):
        queries.append(query)
        return _Rendered()

    monkeypatch.setattr(type(search_service), "search", fake_search)
    await chat_ws.execute_tool_call("web_search", '{"query": "牧瀬紅莉栖"}')
    assert queries == ["牧瀬紅莉栖"]


# ---------------------------------------------------------------------------
# chat_ws conversation.switch: rollback failures are logged, reader survives
# ---------------------------------------------------------------------------


def test_conversation_switch_restore_failure_is_logged_and_reader_survives(
    app_client, monkeypatch, caplog
):
    from app.services.conversations import conversation_service

    session_id = f"switch-restore-{uuid4()}"
    target = app_client.post(
        "/api/conversations",
        json={"session_id": session_id, "worldline": "steins_gate"},
    ).json()

    with app_client.websocket_connect(f"/ws/chat?session_id={session_id}") as websocket:
        websocket.send_json({
            "type": "auth",
            "client": "desktop",
            "protocol_version": 2,
            "worldline": "steins_gate",
            "conversation_mode": "draft",
        })
        assert websocket.receive_json()["type"] == "session.ready"
        # Both the switch and the best-effort restore of the previous
        # selection fail.
        monkeypatch.setattr(
            type(conversation_service),
            "select",
            AsyncMock(side_effect=RuntimeError(SECRET_TEXT)),
        )
        with caplog.at_level(logging.WARNING, logger="app.routers.chat_ws"):
            websocket.send_json({
                "type": "conversation.switch",
                "request_id": "switch-restore-failure",
                "conversation_id": target["id"],
            })
            error = websocket.receive_json()
        # Unchanged contract: correlated, sanitized switch_error frame.
        assert error["type"] == "conversation.switch_error"
        assert error["request_id"] == "switch-restore-failure"
        assert error["code"] == "conversation_switch_failed"
        # The reader loop is still serving this socket.
        websocket.send_json({"type": "no.such.frame"})
        follow_up = websocket.receive_json()
        assert follow_up["type"] == "error"
        assert "no.such.frame" in follow_up["message"]

    messages = _messages(caplog, "app.routers.chat_ws")
    assert any("switch failed" in m and "RuntimeError" in m for m in messages), messages
    assert any("could not restore previous selection" in m for m in messages), messages
    assert all(SECRET_TEXT not in m for m in messages)


# ---------------------------------------------------------------------------
# segments: heartbeat consumer narrowed; observer failures logged
# ---------------------------------------------------------------------------


async def test_heartbeat_consumer_handles_done_and_pending_tasks(caplog):
    async def _fails():
        raise RuntimeError(SECRET_TEXT)

    failed = asyncio.create_task(_fails())
    await asyncio.gather(failed, return_exceptions=True)
    with caplog.at_level(logging.DEBUG, logger="app.domain.segments"):
        segments._consume_heartbeat_task(failed)  # retrieves, never raises
    assert any("RuntimeError" in m for m in _messages(caplog, "app.domain.segments"))
    assert all(SECRET_TEXT not in m for m in _messages(caplog, "app.domain.segments"))

    pending = asyncio.create_task(asyncio.sleep(10))
    try:
        segments._consume_heartbeat_task(pending)  # InvalidStateError absorbed
    finally:
        pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)
    segments._consume_heartbeat_task(pending)  # cancelled: no-op


class _ExplodingObserver:
    def __call__(self, identity, segment, audio, audio_error):
        raise RuntimeError(SECRET_TEXT)

    def turn_started(self, identity):
        raise RuntimeError(SECRET_TEXT)

    def turn_terminal(self, identity, status, reason_code=None, error_summary=None):
        raise ValueError(SECRET_TEXT)

    def segment_committed(self, identity, segment):
        raise KeyError(SECRET_TEXT)


def _pipeline(observer, emitted: list) -> SegmentPipeline:
    async def emit(event):
        emitted.append(event)

    async def translate(text):
        return "译文"

    async def synthesize(text, emotion):
        return b"wav"

    return SegmentPipeline(
        TurnIdentity("conversation-1", "turn-1", 3),
        emit=emit,
        translate=translate,
        synthesize=synthesize,
        tts_observer=observer,
        turn_observer=observer,
        heartbeat_interval=None,
    )


async def test_failing_observers_are_logged_and_never_break_the_turn(caplog):
    emitted: list = []
    observer = _ExplodingObserver()
    with caplog.at_level(logging.WARNING, logger="app.domain.segments"):
        await _pipeline(observer, emitted).run([Segment(0, "これはテストです。")])

    assert [event["type"] for event in emitted] == [
        "turn.started",
        "segment.ready",
        "segment.audio",
        "turn.completed",
    ]
    messages = _messages(caplog, "app.domain.segments")
    for hook, error_type in (
        ("turn_started", "RuntimeError"),
        ("segment_committed", "KeyError"),
        ("tts", "RuntimeError"),
        ("turn_terminal", "ValueError"),
    ):
        assert any(f"{hook} observer failed ({error_type})" in m for m in messages), (hook, messages)
    assert all(SECRET_TEXT not in m for m in messages)


async def test_failing_observer_on_cancel_is_logged(caplog):
    emitted: list = []
    pipeline = _pipeline(_ExplodingObserver(), emitted)
    await pipeline.start()
    with caplog.at_level(logging.WARNING, logger="app.domain.segments"):
        await pipeline.cancel(reason="user_turn_cancel")
    assert emitted[-1]["type"] == "turn.cancelled"
    assert any(
        "turn_terminal observer failed (ValueError)" in m
        for m in _messages(caplog, "app.domain.segments")
    )


# ---------------------------------------------------------------------------
# session_manager: swallowed cleanup failures now leave a record
# ---------------------------------------------------------------------------


class _BrokenStream:
    async def aclose(self):
        raise RuntimeError(SECRET_TEXT)


async def test_cancel_inflight_logs_stream_close_failure_and_still_clears_state(caplog):
    session = SimpleNamespace(
        active_task=None, active_streams={_BrokenStream()}, is_busy=True
    )
    with caplog.at_level(logging.WARNING, logger="app.services.session_manager"):
        assert await session_manager.cancel_inflight(session) == 0
    assert session.active_streams == set()
    assert session.is_busy is False and session.active_task is None
    messages = _messages(caplog, "app.services.session_manager")
    assert any("closing an in-flight stream failed (RuntimeError)" in m for m in messages)
    assert all(SECRET_TEXT not in m for m in messages)


class _ClosedSocket:
    async def close(self, code=1000, reason=None):
        raise RuntimeError(SECRET_TEXT)


async def test_acquire_lease_logs_failed_close_of_replaced_connection(caplog):
    coordinator = session_manager.SessionCoordinator()
    old, new = _ClosedSocket(), object()
    await coordinator.acquire_lease("lease-session", "steins_gate", old)
    with caplog.at_level(logging.WARNING, logger="app.services.session_manager"):
        await coordinator.acquire_lease("lease-session", "beta", new)
    assert coordinator._leases == {("lease-session", "beta"): new}
    assert any(
        "closing replaced connection for session lease-session failed (RuntimeError)" in m
        for m in _messages(caplog, "app.services.session_manager")
    )


async def test_switch_rollback_persist_failure_is_logged_and_original_error_raised(caplog):
    coordinator = session_manager.SessionCoordinator()

    async def failing_enter_draft():
        raise LookupError("switch target unavailable")

    session = SimpleNamespace(
        session_id="rollback-session",
        worldline="steins_gate",
        conversation_id="conv-1",
        conversation_mode="history",
        history=[{"role": "user", "content": "x", "id": 1}],
        memory_summary="summary",
        provider_id="deepseek",
        model="m",
        api_key="k",
        identity_mode="okabe",
        last_assistant_emotion="neutral",
        consecutive_no_emo_tags=0,
        revision=1,
        current_epoch=1,
        history_epoch=0,
        active_task=None,
        active_streams=set(),
        is_busy=False,
        enter_draft=failing_enter_draft,
    )
    coordinator.persist_current = AsyncMock(side_effect=OSError(SECRET_TEXT))

    with caplog.at_level(logging.WARNING, logger="app.services.session_manager"):
        with pytest.raises(LookupError, match="switch target unavailable"):
            await coordinator.switch(session, "beta", conversation_mode="draft")

    assert session.worldline == "steins_gate", "runtime snapshot restored"
    assert session.history == [{"role": "user", "content": "x", "id": 1}]
    messages = _messages(caplog, "app.services.session_manager")
    assert any("rollback persist failed for session rollback-session (OSError)" in m for m in messages)
    assert all(SECRET_TEXT not in m for m in messages)


# ---------------------------------------------------------------------------
# search: credential reads narrowed; legacy weather fallback kept broad + logged
# ---------------------------------------------------------------------------


class _RaisingStore:
    def __init__(self, exc: BaseException):
        self.exc = exc

    def get(self, provider_id):
        raise self.exc


@pytest.mark.parametrize("exc", [OSError(5, "Access is denied"), ValueError("invalid provider id")])
def test_credential_store_failures_fall_back_to_environment(exc, monkeypatch):
    monkeypatch.setattr(search_module, "credential_store", _RaisingStore(exc))
    monkeypatch.setenv("TAVILY_API_KEY", "env-tavily-key")
    assert search_module.SearchService.configured_key("tavily") == "env-tavily-key"
    assert search_module.SearchService.get_active_provider() == "tavily"


def test_unexpected_credential_store_errors_are_no_longer_hidden(monkeypatch):
    monkeypatch.setattr(search_module, "credential_store", _RaisingStore(KeyError("bug")))
    with pytest.raises(KeyError):
        search_module.SearchService.configured_key("tavily")


@pytest.mark.parametrize(
    "failure",
    [
        httpx.ConnectError("offline"),
        httpx.InvalidURL("bad city"),
        RuntimeError("blocked non-loopback HTTP"),  # outbound transport guards
        "list-payload",  # JSON of the wrong shape -> AttributeError
    ],
)
def test_legacy_weather_failures_are_logged_and_fall_back_to_ddg(failure, monkeypatch, caplog):
    class _Response:
        status_code = 200

        def json(self):
            return ["not", "a", "dict"]

    def fake_get(url, timeout):
        if isinstance(failure, BaseException):
            raise failure
        return _Response()

    monkeypatch.setattr(search_module.httpx, "get", fake_get)
    monkeypatch.setattr(search_module, "_legacy_ddg", lambda query: f"ddg:{query}")
    with caplog.at_level(logging.WARNING, logger="app.services.search"):
        assert search_module.web_search("東京 天気") == "ddg:東京 weather"
    assert any(
        "legacy weather lookup failed" in m for m in _messages(caplog, "app.services.search")
    )
