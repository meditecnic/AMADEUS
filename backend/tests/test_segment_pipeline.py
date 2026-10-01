from __future__ import annotations

import asyncio

import pytest

from app.domain.segments import (
    AudioErrorCode,
    IncrementalJapaneseSegmenter,
    Segment,
    SegmentPipeline,
    TransientSegmentError,
    TurnIdentity,
    is_ignorable_non_speech_segment,
    prepare_tts_text,
)


def test_incremental_segmenter_preserves_cross_token_quotes_and_ellipsis():
    splitter = IncrementalJapaneseSegmenter()

    assert splitter.feed("「そんなこと、あるわけ…") == []
    assert splitter.feed("…ないでしょ？」次は") == [Segment(0, "「そんなこと、あるわけ……ないでしょ？」")]
    assert splitter.feed("実験よ。") == [Segment(1, "次は実験よ。")]
    assert splitter.finish() == []


def test_period_then_ellipsis_merges_into_next_sentence_not_bare_segment():
    """「ね。……続き」 must not emit a standalone 「……」 speech segment."""
    splitter = IncrementalJapaneseSegmenter()
    text = "朝からその呼び方ね。……はあ、面倒なんだから。"
    parts = splitter.feed(text) + splitter.finish()
    texts = [part.text for part in parts]
    assert "……" not in texts
    assert any("朝からその呼び方ね" in part for part in texts)
    assert any("面倒" in part for part in texts)
    joined = "".join(texts)
    assert "朝からその呼び方ね" in joined
    assert "面倒なんだから" in joined


def test_bare_ellipsis_is_ignorable_non_speech():
    assert is_ignorable_non_speech_segment("……")
    assert is_ignorable_non_speech_segment("…")
    assert is_ignorable_non_speech_segment("  。  ")
    assert not is_ignorable_non_speech_segment("朝からその呼び方ね。")


def test_incremental_segmenter_flushes_unpunctuated_tail_once():
    splitter = IncrementalJapaneseSegmenter()
    assert splitter.feed("まだ途中") == []
    assert splitter.finish() == [Segment(0, "まだ途中")]
    assert splitter.finish() == []


def test_short_fragment_merges_before_id_and_keeps_creation_emotion():
    splitter = IncrementalJapaneseSegmenter()

    assert splitter.feed("て。", emotion="embarrassed") == []
    assert splitter.feed("違うんだからね！", emotion="neutral") == [
        Segment(0, "て。違うんだからね！", "embarrassed")
    ]


def test_tts_preparation_classifies_empty_action_and_too_short():
    assert prepare_tts_text("   ").error is AudioErrorCode.EMPTY_TEXT
    assert prepare_tts_text("（ため息）").error is AudioErrorCode.ACTION_ONLY
    assert prepare_tts_text("て。").error is AudioErrorCode.TOO_SHORT
    prepared = prepare_tts_text("（ため息）違うわよ。")
    assert prepared.text == "違うわよ。"
    assert prepared.error is None


@pytest.mark.asyncio
async def test_segment_pipeline_uses_each_segment_emotion_snapshot():
    events = []
    synthesized = []

    async def translate(text):
        return f"zh:{text}"

    async def synthesize(text, emotion):
        synthesized.append((text, emotion))
        return b"audio"

    pipeline = SegmentPipeline(
        TurnIdentity("conversation", "turn", 1),
        emit=events.append,
        translate=translate,
        synthesize=synthesize,
    )
    await pipeline.run([
        Segment(0, "一。", "embarrassed"),
        Segment(1, "二。", "annoyed"),
    ])

    assert synthesized == [("一。", "embarrassed"), ("二。", "annoyed")]
    ready = [event for event in events if event["type"] == "segment.ready"]
    assert [event["emotion"] for event in ready] == ["embarrassed", "annoyed"]


@pytest.mark.asyncio
async def test_segment_pipeline_emits_in_order_despite_out_of_order_completion():
    events = []

    async def translate(text):
        await asyncio.sleep(0.03 if text == "一。" else 0)
        return f"zh:{text}"

    async def synthesize(text, _emotion):
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

    async def synthesize(_text: str, _emotion: str) -> bytes:
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

    async def synthesize(text, _emotion):
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
async def test_translation_retries_transient_failure_and_keeps_error_code_stable():
    events = []
    attempts = 0

    async def translate(_text):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise TransientSegmentError("upstream temporarily unavailable")
        return "translated"

    async def synthesize(_text, _emotion):
        return b"audio"

    pipeline = SegmentPipeline(
        TurnIdentity("conversation", "turn", 1),
        emit=events.append,
        translate=translate,
        synthesize=synthesize,
        translation_retries=1,
        translation_timeout=0.1,
    )
    await pipeline.run([Segment(0, "sentence")])

    ready = next(event for event in events if event["type"] == "segment.ready")
    assert attempts == 2
    assert ready["zh"] == "translated"
    assert "translation_error" not in ready


@pytest.mark.asyncio
async def test_translation_retries_retryable_provider_status():
    events = []
    attempts = 0

    class ProviderError(RuntimeError):
        status_code = 503

    async def translate(_text):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise ProviderError("upstream unavailable")
        return "recovered"

    async def synthesize(_text, _emotion):
        return b"audio"

    pipeline = SegmentPipeline(
        TurnIdentity("conversation", "turn", 1),
        emit=events.append,
        translate=translate,
        synthesize=synthesize,
        translation_retries=1,
        translation_timeout=0.1,
    )
    await pipeline.run([Segment(0, "sentence")])

    ready = next(event for event in events if event["type"] == "segment.ready")
    assert attempts == 2
    assert ready["zh"] == "recovered"


@pytest.mark.asyncio
async def test_translation_timeout_degrades_without_exposing_provider_exception():
    events = []

    async def translate(_text):
        await asyncio.sleep(1)
        return "late"

    async def synthesize(_text, _emotion):
        return b"audio"

    pipeline = SegmentPipeline(
        TurnIdentity("conversation", "turn", 1),
        emit=events.append,
        translate=translate,
        synthesize=synthesize,
        translation_retries=0,
        translation_timeout=0.01,
    )
    await pipeline.run([Segment(0, "sentence")])

    ready = next(event for event in events if event["type"] == "segment.ready")
    assert ready["zh"] == ""
    assert ready["translation_error"] == "translation_timeout"
    assert pipeline.has_translation_errors is True
    assert "late" not in str(ready)
    assert any(event["type"] == "segment.audio" for event in events)


@pytest.mark.asyncio
async def test_voice_stop_keeps_text_but_invalidates_audio_results():
    events = []
    tts_started = asyncio.Event()

    async def translate(text):
        return f"zh:{text}"

    async def synthesize(text, _emotion):
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

    async def synthesize(text, _emotion):
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
