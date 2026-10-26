"""Proper names in Japanese must pass both independent language classifiers."""

import pytest

from app.domain import language_publication, segments


PUBLISHABLE = (
    "GPT-4o、Claude、Geminiの比較",
    "ClaudeとGeminiを比べると、どっちが速い？",
    "Claude Opus、Gemini Proの比較だよ。",
    # These names are absent from both allowlists, so both fixes are exercised.
    "Aster、Beryl、Cedarの比較",
    "Aster Beryl、Cedar Dorianの比較だよ。",
    "A" + "b" * 23 + "、C" + "d" * 23 + "、E" + "f" * 23 + "の比較",
)

FOREIGN_PROSE = (
    "The Quick Brown Fox Jumps だよ。",
    "これは This Is The Complete Answer だよ。",
    "これは This is the complete answer だよ。",
    "Das ist eine vollständige Antwortだよ。",
)


@pytest.mark.parametrize("text", PUBLISHABLE, ids=(
    "product-list", "two-names", "paired-names", "new-names", "new-pairs", "24-letter-names",
))
def test_title_case_names_publish_without_language_recovery(text):
    assert language_publication.is_ui_publishable_segment(text)
    assert not language_publication.is_high_confidence_foreign_prose(text)
    assert not language_publication._has_dominant_unbounded_latin_prose(text)
    assert not segments._has_dominant_unbounded_latin_prose(text)
    assert segments.is_plausible_japanese_tts_source(text)
    assert segments.prepare_tts_text(text).error is None


@pytest.mark.parametrize("text", FOREIGN_PROSE, ids=(
    "title-case-prose", "japanese-prefix", "lowercase-prose", "german-prose",
))
def test_space_joined_foreign_prose_is_still_rejected(text):
    assert language_publication.is_high_confidence_foreign_prose(text)
    assert not language_publication.is_ui_publishable_segment(text)
    assert language_publication._has_dominant_unbounded_latin_prose(text)
    assert segments._has_dominant_unbounded_latin_prose(text)
    assert not segments.is_plausible_japanese_tts_source(text)


@pytest.mark.parametrize("classifier", (language_publication, segments), ids=("ui", "tts"))
@pytest.mark.parametrize("text", (
    "Aster、Beryl、Cedar比較",  # No kana: no proper-name exemption.
    "A" + "b" * 24 + "、C" + "d" * 24 + "、E" + "f" * 24 + "の比較",
    "aster、beryl、cedarの比較",  # Lowercase is not a proper-name signal.
    "Aster Beryl Cedarの比較",  # Exactly three spaced words retain prose detection.
), ids=("no-kana", "25-letter-names", "lowercase", "three-spaced-words"))
def test_name_exemption_requires_kana_title_case_and_bounded_length(classifier, text):
    assert classifier._has_dominant_unbounded_latin_prose(text)


@pytest.mark.parametrize("classifier", (language_publication, segments), ids=("ui", "tts"))
def test_kana_inside_bounded_embed_does_not_exempt_outer_names(classifier):
    assert classifier._has_dominant_unbounded_latin_prose("Aster、Beryl、Cedar『あ』")
