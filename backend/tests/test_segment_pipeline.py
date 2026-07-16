from __future__ import annotations

import asyncio

import pytest

from app.domain.segments import (
    IncrementalJapaneseSegmenter,
    Segment,
    SegmentPipeline,
    TransientSegmentError,
    TurnIdentity,
)


def test_incremental_segmenter_preserves_cross_token_quotes_and_ellipsis():
    splitter = IncrementalJapaneseSegmenter()

    assert splitter.feed("「そんなこと、あるわけ…") == []
    assert splitter.feed("…ないでしょ？」次は") == [Segment(0, "「そんなこと、あるわけ……ないでしょ？」")]
    assert splitter.feed("実験よ。") == [Segment(1, "次は実験よ。")]
    assert splitter.finish() == []


def test_incremental_segmenter_flushes_unpunctuated_tail_once():
    splitter = IncrementalJapaneseSegmenter()
    assert splitter.feed("まだ途中") == []
    assert splitter.finish() == [Segment(0, "まだ途中")]
    assert splitter.finish() == []


@pytest.mark.asyncio
async def test_segment_pipeline_emits_in_order_despite_out_of_order_completion():
    events = []

    async def translate(text):
        await asyncio.sleep(0.03 if text == "一。" else 0)
        return f"zh:{text}"

    async def synthesize(text):
        await asyncio.sleep(0.03 if text == "二。" else 0)
        return f"audio:{text}".encode()

    pipeline = SegmentPipeline(
        TurnIdentity("conversation", "turn", 7),
        emit=events.append,
        translate=translate,
        synthesize=synthesize,
    )
    await pipeline.run([Segment(0, "一。"), Segment(1, "二。"), Segment(2, "三。")])

    ready_ids = [event["segment_id"] for event in events if event["type"] == "segment.ready"]
    audio_ids = [event["segment_id"] for event in events if event["type"] == "segment.audio"]
    assert ready_ids == [0, 1, 2]
    assert audio_ids == [0, 1, 2]
    assert events[0]["type"] == "turn.started"
    assert events[-1]["type"] == "turn.completed"


@pytest.mark.asyncio
async def test_segment_pipeline_exposes_committed_translation_for_history():
    async def translate(text: str) -> str:
        return f"中:{text}"

    async def synthesize(_text: str) -> bytes:
        return b"audio"

    pipeline = SegmentPipeline(
        TurnIdentity("conversation", "turn", 1),
        emit=lambda _event: None,
        translate=translate,
        synthesize=synthesize,
    )
    await pipeline.run([Segment(0, "一。"), Segment(1, "二。")])

    assert pipeline.translation_text == "中:一。中:二。"


@pytest.mark.asyncio
async def test_transient_tts_retries_once_and_one_failure_does_not_block_later_segments():
    events = []
    attempts = {}

    async def translate(text):
        return f"zh:{text}"

    async def synthesize(text):
        attempts[text] = attempts.get(text, 0) + 1
        if text == "retry。" and attempts[text] == 1:
            raise TransientSegmentError("temporary")
        if text == "fail。":
            raise RuntimeError("broken")
        return text.encode()

    pipeline = SegmentPipeline(
        TurnIdentity("conversation", "turn", 1),
        emit=events.append,
        translate=translate,
        synthesize=synthesize,
        tts_retries=1,
    )
    await pipeline.run([Segment(0, "retry。"), Segment(1, "fail。"), Segment(2, "later。")])

    assert attempts["retry。"] == 2
    assert [event["segment_id"] for event in events if event["type"] == "segment.audio_error"] == [1]
    assert [event["segment_id"] for event in events if event["type"] == "segment.audio"] == [0, 2]
    assert events[-1]["type"] == "turn.completed"


@pytest.mark.asyncio
async def test_voice_stop_keeps_text_but_invalidates_audio_results():
    events = []
    tts_started = asyncio.Event()

    async def translate(text):
        return f"zh:{text}"

    async def synthesize(text):
        tts_started.set()
        await asyncio.sleep(60)
        return b"late"

    pipeline = SegmentPipeline(
        TurnIdentity("conversation", "turn", 2),
        emit=events.append,
        translate=translate,
        synthesize=synthesize,
    )
    running = asyncio.create_task(pipeline.run([Segment(0, "止めて。")]))
    await tts_started.wait()
    await pipeline.stop_voice()
    await running

    assert any(event["type"] == "segment.ready" for event in events)
    assert not any(event["type"] == "segment.audio" for event in events)
    assert any(
        event["type"] == "segment.audio_error" and event["reason"] == "voice_stopped"
        for event in events
    )
    assert events[-1]["type"] == "turn.completed"


@pytest.mark.asyncio
async def test_turn_cancel_drops_late_events_and_never_completes():
    events = []
    started = asyncio.Event()

    async def translate(text):
        started.set()
        await asyncio.sleep(60)
        return "late translation"

    async def synthesize(text):
        await asyncio.sleep(60)
        return b"late audio"

    pipeline = SegmentPipeline(
        TurnIdentity("conversation", "turn", 3),
        emit=events.append,
        translate=translate,
        synthesize=synthesize,
    )
    running = asyncio.create_task(pipeline.run([Segment(0, "キャンセル。")]))
    await started.wait()
    await pipeline.cancel()
    await running

    assert [event["type"] for event in events] == ["turn.started", "turn.cancelled"]
