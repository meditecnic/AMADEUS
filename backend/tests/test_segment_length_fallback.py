"""Length fallback must preserve streamed text and the TTS source budget."""

import pytest

from app.domain.segments import (
    MAX_TTS_SOURCE_CODEPOINTS,
    AudioErrorCode,
    IncrementalJapaneseSegmenter,
    Segment,
    clean_text_for_tts,
    prepare_tts_text,
)


LONG_SOURCES = (
    "（補足。" + "これは文。" * 80,
    "これは長い日本語の説明です" * 40,
    "（補足）これは文。" * 60,
    "「" + "これは長い説明です" * 40 + "」次の文です。",
)


@pytest.mark.parametrize(
    "source", LONG_SOURCES, ids=("unclosed", "unpunctuated", "early-closed", "long-quote")
)
def test_long_source_emits_before_finish_without_losing_text(source):
    splitter = IncrementalJapaneseSegmenter()
    streamed = splitter.feed(source)
    assert streamed
    parts = streamed + splitter.finish()
    assert "".join(part.text for part in parts) == source
    assert [part.id for part in parts] == list(range(len(parts)))
    for part in parts:
        assert len(clean_text_for_tts(part.text)) <= MAX_TTS_SOURCE_CODEPOINTS
        assert prepare_tts_text(part.text).error is not AudioErrorCode.TEXT_TOO_LONG
    assert splitter.finish() == []


@pytest.mark.parametrize(
    "source", LONG_SOURCES, ids=("unclosed", "unpunctuated", "early-closed", "long-quote")
)
@pytest.mark.parametrize("chunk_size", (1, 2, 3))
def test_long_source_boundaries_do_not_depend_on_feed_size(source, chunk_size):
    whole = IncrementalJapaneseSegmenter()
    expected = whole.feed(source) + whole.finish()
    streamed = IncrementalJapaneseSegmenter()
    actual = []
    for offset in range(0, len(source), chunk_size):
        actual.extend(streamed.feed(source[offset:offset + chunk_size]))
    actual.extend(streamed.finish())
    assert actual == expected


@pytest.mark.parametrize("terminator", ("。", "！", "?", "\n", "……", "..."))
def test_length_fallback_prefers_last_sentence_end_over_later_comma(terminator):
    prefix = "（あ。" + "い" * 80 + terminator
    source = prefix + "う" * 80 + "、" + "え" * 200
    parts = IncrementalJapaneseSegmenter().feed(source)
    assert parts[0].text == prefix.strip()


@pytest.mark.parametrize("separator", ("、", "，", ",", " ", "\t", "\u3000"))
def test_length_fallback_prefers_last_comma_or_space(separator):
    prefix = "（あ、" + "い" * 180 + separator
    source = prefix + "う" * 200
    parts = IncrementalJapaneseSegmenter().feed(source)
    assert parts[0].text == prefix.strip()


def test_length_fallback_waits_until_budget_is_exceeded():
    splitter = IncrementalJapaneseSegmenter()
    prefix = "あ" * MAX_TTS_SOURCE_CODEPOINTS
    assert splitter.feed(prefix, "happy") == []
    assert splitter.feed("い", "neutral") == [Segment(0, prefix, "happy")]
    assert splitter.finish() == [Segment(1, "い", "neutral")]


def test_short_unclosed_quote_still_waits_for_closer():
    splitter = IncrementalJapaneseSegmenter()
    assert splitter.feed("「そうなの…") == []
    assert splitter.feed("…？") == []
    assert splitter.feed("」") == [Segment(0, "「そうなの……？」")]
    assert splitter.finish() == []


def test_early_closed_bracket_keeps_normal_sentence_boundaries():
    source = "（補足）これは文。" * 60
    splitter = IncrementalJapaneseSegmenter()
    parts = splitter.feed(source) + splitter.finish()
    assert [part.text for part in parts] == ["（補足）これは文。"] * 60


def test_length_cut_resets_bracket_state_and_preserves_emotions():
    splitter = IncrementalJapaneseSegmenter()
    prefix = "（" + "あ" * (MAX_TTS_SOURCE_CODEPOINTS - 1)
    assert splitter.feed(prefix, "happy") == []
    assert splitter.feed("次の文です。", "neutral") == [
        Segment(0, prefix, "happy"),
        Segment(1, "次の文です。", "neutral"),
    ]
    assert splitter.finish() == []


def test_held_short_fragment_cannot_make_next_segment_overlong():
    splitter = IncrementalJapaneseSegmenter()
    assert splitter.feed("て。", "embarrassed") == []
    source = "あ" * MAX_TTS_SOURCE_CODEPOINTS
    streamed = splitter.feed(source, "neutral")
    assert streamed == [Segment(0, "て。", "embarrassed")]
    parts = streamed + splitter.finish()
    assert parts == [
        Segment(0, "て。", "embarrassed"),
        Segment(1, source, "neutral"),
    ]
    assert all(
        prepare_tts_text(part.text).error is not AudioErrorCode.TEXT_TOO_LONG
        for part in parts
    )


def test_held_punctuation_cannot_make_next_segment_overlong():
    source = "……" * MAX_TTS_SOURCE_CODEPOINTS + "これは文です。"
    splitter = IncrementalJapaneseSegmenter()
    parts = splitter.feed(source) + splitter.finish()
    assert "".join(part.text for part in parts) == source
    assert all(len(part.text) <= MAX_TTS_SOURCE_CODEPOINTS for part in parts)


def test_held_punctuation_counts_toward_pending_length():
    splitter = IncrementalJapaneseSegmenter()
    prefix = "……" * (MAX_TTS_SOURCE_CODEPOINTS // 2)
    assert splitter.feed(prefix) == []
    assert splitter.feed("これは続き") == [Segment(0, prefix)]
    assert splitter.finish() == [Segment(1, "これは続き")]
