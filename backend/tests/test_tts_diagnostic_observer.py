"""T-01 observer tests; no client or playback diagnostics are covered here."""

import asyncio

import pytest

from app.domain.segments import Segment, SegmentPipeline, TurnIdentity
from app.services.diagnostics import DiagnosticTTSObserver, DiagnosticTurnObserver


class _Runtime:
    def __init__(self, observing=True, *, fail=False):
        self.observing = observing
        self.events = []
        self.failures = []
        self.fail = fail
        self.mapping_fallbacks = 0

    def append_event(self, event):
        if self.fail:
            raise RuntimeError("diagnostics writer failed")
        self.events.append(event)

    def record_observer_failure(self):
        self.failures.append("write_failed")

    def record_mapping_fallback(self):
        self.mapping_fallbacks += 1


async def _pipeline(observer, *, audio=b"wav", audio_error=None):
    emitted = []

    async def emit(event):
        emitted.append(event)

    async def translate(text):
        return "译文"

    async def synthesize(text, emotion):
        if audio_error:
            raise RuntimeError(audio_error)
        return audio

    pipeline = SegmentPipeline(
        TurnIdentity("conversation-1", "turn-1", 7),
        emit=emit,
        translate=translate,
        synthesize=synthesize,
        tts_observer=observer,
        turn_observer=observer if hasattr(observer, "turn_started") else None,
    )
    await pipeline.run([Segment(0, "これはテストです。")])
    return emitted


@pytest.mark.asyncio
async def test_flag_off_produces_zero_diagnostic_events():
    runtime = _Runtime(observing=False)
    observer = DiagnosticTTSObserver(runtime)
    emitted = await _pipeline(observer)
    await asyncio.sleep(0)
    assert len([event for event in emitted if event["type"] == "segment.audio"]) == 1
    assert runtime.events == []


@pytest.mark.asyncio
async def test_enabled_observer_emits_exactly_one_terminal_event_without_awaiting_pipeline():
    runtime = _Runtime()
    observer = DiagnosticTurnObserver(runtime)
    await _pipeline(observer)
    await asyncio.sleep(0.05)
    assert [event["status"] for event in runtime.events] == [
        "started",
        "succeeded",
        "succeeded",
        "succeeded",
    ]
    assert [event["stage"] for event in runtime.events] == [
        "generation",
        "generation",
        "tts",
        "generation",
    ]
    assert runtime.events[2]["segment_id"] == "0"
    assert runtime.events[2]["audio_bytes"] == 3
    assert [event["server_seq"] for event in runtime.events] == [1, 2, 3, 4]


@pytest.mark.asyncio
async def test_observer_failure_does_not_change_turn_events_or_escape():
    runtime = _Runtime(fail=True)
    observer = DiagnosticTTSObserver(runtime)
    emitted = await _pipeline(observer)
    await asyncio.sleep(0.05)
    assert [event["type"] for event in emitted] == [
        "turn.started",
        "segment.ready",
        "segment.audio",
        "turn.completed",
    ]
    assert runtime.failures == ["write_failed"]


@pytest.mark.asyncio
async def test_g02_exists_without_tts_terminal_and_is_joinable_by_segment():
    runtime = _Runtime()
    observer = DiagnosticTurnObserver(runtime)
    identity = TurnIdentity("conversation-1", "turn-1", 7)
    observer.turn_started(identity)
    observer.segment_committed(identity, Segment(4, "これはテストです。"))
    await asyncio.sleep(0.05)
    assert [(event["stage"], event["status"]) for event in runtime.events] == [
        ("generation", "started"),
        ("generation", "succeeded"),
    ]
    assert runtime.events[1]["segment_id"] == "4"
    assert runtime.events[1]["source_hash"]
    assert runtime.events[1]["audio_bytes"] is None


@pytest.mark.asyncio
async def test_d01_uses_shared_turn_sequence_and_never_copies_payload_data():
    runtime = _Runtime()
    observer = DiagnosticTurnObserver(runtime)
    identity = TurnIdentity("conversation-1", "turn-1", 7)
    observer.turn_started(identity)
    observer.delivery(
        {
            "type": "segment.audio",
            "turn_id": "turn-1",
            "generation": 7,
            "segment_id": 4,
            "data": "secret-base64-audio",
        },
        "succeeded",
    )
    await asyncio.sleep(0.05)
    assert [event["server_seq"] for event in runtime.events] == [1, 2]
    assert runtime.events[1]["stage"] == "delivery"
    assert runtime.events[1]["segment_id"] == "4"
    assert "secret-base64-audio" not in repr(runtime.events[1])


@pytest.mark.asyncio
async def test_g01_emits_one_started_and_one_terminal_with_immutable_identity():
    runtime = _Runtime()
    observer = DiagnosticTurnObserver(runtime)
    identity = TurnIdentity("conversation-1", "turn-1", 7)
    observer.turn_started(identity)
    observer.turn_terminal(identity, "succeeded")
    observer.turn_terminal(identity, "failed", "should_be_ignored")
    await asyncio.sleep(0.05)
    assert [(event["status"], event["stage"]) for event in runtime.events] == [
        ("started", "generation"),
        ("succeeded", "generation"),
    ]
    assert all(event["segment_id"] is None for event in runtime.events)
    assert [event["server_seq"] for event in runtime.events] == [1, 2]


@pytest.mark.asyncio
async def test_g01_preserves_hanging_started_and_does_not_synthesize_timeout():
    runtime = _Runtime()
    observer = DiagnosticTurnObserver(runtime)
    observer.turn_started(TurnIdentity("conversation-1", "turn-1", 7))
    await asyncio.sleep(0.05)
    assert [event["status"] for event in runtime.events] == ["started"]
