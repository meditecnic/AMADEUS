import pytest

from app.domain.segments import (
    AudioErrorCode, IncrementalJapaneseSegmenter,
    MAX_SEGMENT_SOURCE_CODEPOINTS, prepare_tts_text,
)


@pytest.mark.parametrize("chunk_size", [1, 17, 403])
def test_forced_short_segment_merges_without_loss(chunk_size):
    source = "（あ。" + "い" * 400
    segmenter = IncrementalJapaneseSegmenter()
    segments = []
    for offset in range(0, len(source), chunk_size):
        segments.extend(segmenter.feed(source[offset:offset + chunk_size]))
    segments.extend(segmenter.finish())
    assert "".join(row.text for row in segments) == source
    assert all(len(row.text) <= MAX_SEGMENT_SOURCE_CODEPOINTS for row in segments)
    assert all(prepare_tts_text(row.text).error is not AudioErrorCode.TOO_SHORT for row in segments)


