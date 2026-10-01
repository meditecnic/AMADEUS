"""BE-语音可靠性 B1: TTS 解耦 + turn liveness.

Public-event only: tests observe emitted dicts, never private Maps/Tasks.
"""
from __future__ import annotations

import asyncio

import pytest

from app.domain.segments import Segment, SegmentPipeline, TurnIdentity


def _identity(turn="turn-b1", generation=1):
    return TurnIdentity("conversation-b1", turn, generation)


@pytest.mark.asyncio
async def test_b1_ready_emitted_while_tts_hangs():
    """TTS promise 挂起时, 该段 segment.ready 已经发出."""
    events: list[dict] = []
    tts_gate = asyncio.Event()
    ready_emitted = asyncio.Event()

    def emit(event):
        events.append(event)
        if event["type"] == "segment.ready":
            ready_emitted.set()

    async def translate(text):
        return f"zh:{text}"

    async def synthesize(text, _emotion):
        await tts_gate.wait()
        return b"audio"

    pipeline = SegmentPipeline(
        _identity(), emit=emit, translate=translate, synthesize=synthesize
    )
    await pipeline.start()
    pipeline.submit(Segment(0, "一文目。"))
    try:
        await asyncio.wait_for(ready_emitted.wait(), timeout=2)
        ready = [e for e in events if e["type"] == "segment.ready"]
        assert [e["segment_id"] for e in ready] == [0], f"ready blocked by hanging TTS: {events!r}"
        assert ready[0]["ja"] == "一文目。"
        assert ready[0]["zh"] == "zh:一文目。"
    finally:
        tts_gate.set()
        await pipeline.finish(segment_count=1)


@pytest.mark.asyncio
async def test_b1_later_ready_not_blocked_by_earlier_hanging_tts():
    """segment 1 的 TTS 挂起时, segment 2 的 segment.ready 仍可按序发出."""
    events: list[dict] = []
    gate0 = asyncio.Event()
    second_ready_emitted = asyncio.Event()

    def emit(event):
        events.append(event)
        if event["type"] == "segment.ready" and event["segment_id"] == 1:
            second_ready_emitted.set()

    async def translate(text):
        return f"zh:{text}"

    async def synthesize(text, _emotion):
        if text == "一文目。":
            await gate0.wait()
            return b"audio0"
        return b"audio1"

    pipeline = SegmentPipeline(
        _identity(), emit=emit, translate=translate, synthesize=synthesize
    )
    await pipeline.start()
    pipeline.submit(Segment(0, "一文目。"))
    pipeline.submit(Segment(1, "二文目。"))
    try:
        await asyncio.wait_for(second_ready_emitted.wait(), timeout=2)
        ready_ids = [e["segment_id"] for e in events if e["type"] == "segment.ready"]
        assert ready_ids == [0, 1], f"later ready blocked by earlier TTS: {events!r}"
    finally:
        gate0.set()
        await pipeline.finish(segment_count=2)


@pytest.mark.asyncio
async def test_b1_audio_aligned_and_terminal_after_attachments():
    """TTS 完成后 audio 与正确 segment id 对齐; 终态不早于必要附件."""
    events: list[dict] = []

    async def translate(text):
        return f"zh:{text}"

    async def synthesize(text, _emotion):
        await asyncio.sleep(0.01)
        return f"audio:{text}".encode()

    pipeline = SegmentPipeline(
        _identity(), emit=events.append, translate=translate, synthesize=synthesize
    )
    await pipeline.run([Segment(0, "一文目。"), Segment(1, "二文目。")])

    ready_ids = [e["segment_id"] for e in events if e["type"] == "segment.ready"]
    audio_ids = [e["segment_id"] for e in events if e["type"] == "segment.audio"]
    assert ready_ids == [0, 1]
    assert audio_ids == [0, 1]
    # Audio payload aligns with its segment id.
    audios = {e["segment_id"]: e["data"] for e in events if e["type"] == "segment.audio"}
    assert audios[0] == "audio:一文目。".encode()
    assert audios[1] == "audio:二文目。".encode()
    # Terminal is last, after all attachments.
    assert events[0]["type"] == "turn.started"
    assert events[-1]["type"] == "turn.completed"
    last_audio_idx = max(i for i, e in enumerate(events) if e["type"] == "segment.audio")
    completed_idx = next(i for i, e in enumerate(events) if e["type"] == "turn.completed")
    assert completed_idx > last_audio_idx


@pytest.mark.asyncio
async def test_b1_heartbeat_proves_liveness_during_active_turn():
    """活跃 turn 持续收到明确 liveness (turn.heartbeat, 存活语义, 无进度冒充)."""
    events: list[dict] = []
    release = asyncio.Event()
    heartbeats_emitted = asyncio.Event()

    def emit(event):
        events.append(event)
        if sum(e["type"] == "turn.heartbeat" for e in events) >= 2:
            heartbeats_emitted.set()

    async def translate(text):
        await release.wait()
        return f"zh:{text}"

    async def synthesize(text, _emotion):
        await release.wait()
        return b"audio"

    pipeline = SegmentPipeline(
        _identity(),
        emit=emit,
        translate=translate,
        synthesize=synthesize,
        heartbeat_interval=0.02,
    )
    await pipeline.start()
    pipeline.submit(Segment(0, "長い文です。"))
    try:
        await asyncio.wait_for(heartbeats_emitted.wait(), timeout=2)
        heartbeats = [e for e in events if e["type"] == "turn.heartbeat"]
        assert len(heartbeats) >= 2, f"no liveness during active turn: {events!r}"
        for hb in heartbeats:
            assert hb["turn_id"] == "turn-b1"
            assert hb["generation"] == 1
            assert "ja" not in hb and "zh" not in hb and "data" not in hb
            assert "segment_id" not in hb and "segment_count" not in hb
    finally:
        release.set()
        await pipeline.finish(segment_count=1)
    # Heartbeat stops after terminal.
    terminal_idx = next(i for i, e in enumerate(events) if e["type"] == "turn.completed")
    late_hb = [e for e in events[terminal_idx + 1 :] if e["type"] == "turn.heartbeat"]
    assert late_hb == []


@pytest.mark.asyncio
async def test_b1_tts_failure_keeps_text_and_completes():
    """TTS 失败时文本/字幕完整, 终态仍正常收口 (不取消整轮)."""
    events: list[dict] = []

    async def translate(text):
        return f"zh:{text}"

    async def synthesize(text, _emotion):
        raise RuntimeError("sidecar down")

    pipeline = SegmentPipeline(
        _identity(), emit=events.append, translate=translate, synthesize=synthesize
    )
    await pipeline.run([Segment(0, "一文目。"), Segment(1, "二文目。")])
    ready = [e for e in events if e["type"] == "segment.ready"]
    assert [e["segment_id"] for e in ready] == [0, 1]
    assert all(e["ja"] and e["zh"] for e in ready)
    errors = [e for e in events if e["type"] == "segment.audio_error"]
    assert [e["segment_id"] for e in errors] == [0, 1]
    assert events[-1]["type"] == "turn.completed"
    assert pipeline.translation_text == "zh:一文目。zh:二文目。"


@pytest.mark.asyncio
async def test_b1_cancel_stops_late_ready_audio_and_heartbeat():
    """取消 turn 后, 不再出现迟到 heartbeat / ready / audio."""
    events: list[dict] = []
    started = asyncio.Event()

    async def translate(text):
        started.set()
        await asyncio.sleep(60)
        return "late"

    async def synthesize(text, _emotion):
        await asyncio.sleep(60)
        return b"late"

    pipeline = SegmentPipeline(
        _identity(),
        emit=events.append,
        translate=translate,
        synthesize=synthesize,
        heartbeat_interval=0.02,
    )
    running = asyncio.create_task(pipeline.run([Segment(0, "キャンセル。")]))
    await started.wait()
    await asyncio.sleep(0.05)
    await pipeline.cancel()
    await running
    types = [e["type"] for e in events]
    # Heartbeats before cancel are legal liveness; nothing may arrive after.
    assert "turn.cancelled" in types, f"missing cancel: {types!r}"
    cancelled_idx = types.index("turn.cancelled")
    assert types[0] == "turn.started"
    assert set(types[:cancelled_idx]) <= {"turn.started", "turn.heartbeat"}, (
        f"late content leaked before cancel: {types!r}"
    )
    assert types[cancelled_idx + 1 :] == []
    # No late events after cancel even with extra time.
    await asyncio.sleep(0.06)
    assert [e["type"] for e in events][cancelled_idx:] == ["turn.cancelled"]


@pytest.mark.asyncio
async def test_b1_outer_cancel_never_forges_voice_stopped():
    """Outer-task-first cancel must not forge voice_stopped/translation errors.

    Deterministic: Event gates park seg0 at the voice await (text committed)
    and seg1 at the translation await; no wall-clock sleeps on the hot path.
    """
    events: list[dict] = []
    ready0_seen = asyncio.Event()
    audio_entered = asyncio.Event()
    release = asyncio.Event()

    async def emit(event):
        events.append(event)
        if event["type"] == "segment.ready" and event.get("segment_id") == 0:
            ready0_seen.set()

    async def translate(text):
        if text == "一文目。":
            return "zh:一文目。"
        await release.wait()
        return "zh:二文目。"

    async def synthesize(text, _emotion):
        if text == "一文目。":
            audio_entered.set()
            await release.wait()
        return b"a0"

    pipeline = SegmentPipeline(
        _identity("turn-b1-outer"),
        emit=emit,
        translate=translate,
        synthesize=synthesize,
        heartbeat_interval=None,
    )

    async def driver():
        await pipeline.start()
        pipeline.submit(Segment(0, "一文目。"))
        pipeline.submit(Segment(1, "二文目。"))
        await pipeline.finish(segment_count=2)

    outer = asyncio.create_task(driver())
    await ready0_seen.wait()
    await audio_entered.wait()
    # seg0 _process is parked at the voice await; seg1 at the translation await.
    outer.cancel()
    try:
        await outer
    except asyncio.CancelledError:
        pass
    # Production processor_loop calls pipeline.cancel() from its CancelledError handler.
    await pipeline.cancel()
    # Gate stays closed: assert first, so a task still needing release would hang loudly.
    assert [e["type"] for e in events] == [
        "turn.started",
        "segment.ready",
        "turn.cancelled",
    ]
    ready = next(e for e in events if e["type"] == "segment.ready")
    assert (ready["segment_id"], ready["ja"]) == (0, "一文目。")
    assert "translation_error" not in ready
    assert not [e for e in events if e["type"] in ("segment.audio", "segment.audio_error")]
    release.set()
    for _ in range(10):
        await asyncio.sleep(0)
    assert [e["type"] for e in events] == [
        "turn.started",
        "segment.ready",
        "turn.cancelled",
    ]
