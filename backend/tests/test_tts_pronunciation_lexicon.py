"""Schema and behavior tests for the canonical TTS pronunciation lexicon.

TTS-CANONICAL-PRONUNCIATION-LEXICON-01: the lexicon rewrites only the
sidecar-bound TTS surface text. Canonical UI Japanese, Chinese subtitles,
history, prompts, diagnostics hashes and client payloads must stay untouched.
The lexicon never injects lore knowledge — it only affects pronunciation of
proper nouns the model has already produced.
"""
import json
from pathlib import Path

from app.domain.segments import render_tts_pronunciation_surface

LEXICON_PATH = (
    Path(__file__).resolve().parents[1]
    / "characters"
    / "tts_pronunciation_lexicon_v1.json"
)

# Contract minimum: characters and aliases.
REQUIRED_CHARACTER_READINGS = {
    "牧瀬紅莉栖": "マキセクリス",
    "牧瀬": "マキセ",
    "紅莉栖": "クリス",
    "岡部倫太郎": "オカベリンタロウ",
    "岡部": "オカベ",
    "鳳凰院凶真": "ホウオウインキョウマ",
    "鳳凰院": "ホウオウイン",
    "凶真": "キョウマ",
    "椎名まゆり": "シイナマユリ",
    "橋田至": "ハシダイタル",
    "阿万音鈴羽": "アマネスズハ",
    "天王寺裕吾": "テンノウジユウゴ",
    "桐生萌郁": "キリュウモエカ",
    "漆原るか": "ウルシバラルカ",
    "秋葉留未穂": "アキハルミホ",
    "比屋定真帆": "ヒヤジョウマホ",
    "阿万音由季": "アマネユキ",
    "椎名かがり": "シイナカガリ",
}

# Contract minimum: high-frequency system / organization / handle surfaces.
REQUIRED_SYSTEM_READINGS = {
    "Amadeus": "アマデウス",
    "STEINS;GATE": "シュタインズゲート",
    "未来ガジェット研究所": "ミライガジェットケンキュウジョ",
    "Dメール": "ディーメール",
    "SERN": "セルン",
    "@ちゃんねる": "アットチャンネル",
    "@ch": "アットチャンネル",
    "栗悟飯とカメハメ波": "クリゴハントカメハメハ",
}

ALLOWED_KINDS = {"character", "alias", "organization", "system", "location", "handle"}

COMBINED_SENTENCE = (
    "牧瀬紅莉栖、鳳凰院凶真と岡部倫太郎。Amadeusと未来ガジェット研究所について話す。"
)
COMBINED_EXPECTED = (
    "マキセクリス、ホウオウインキョウマとオカベリンタロウ。"
    "アマデウスとミライガジェットケンキュウジョについて話す。"
)


def _load_entries() -> list[dict]:
    data = json.loads(LEXICON_PATH.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    assert data.get("version") == 1
    entries = data.get("entries")
    assert isinstance(entries, list) and entries
    return entries


# ---------------------------------------------------------------------------
# A. Lexicon schema / coverage
# ---------------------------------------------------------------------------

def test_required_character_coverage():
    readings = {e["surface"]: e["spoken"] for e in _load_entries()}
    for surface, spoken in REQUIRED_CHARACTER_READINGS.items():
        assert readings.get(surface) == spoken, f"missing/incorrect: {surface}"


def test_required_system_coverage():
    readings = {e["surface"]: e["spoken"] for e in _load_entries()}
    for surface, spoken in REQUIRED_SYSTEM_READINGS.items():
        assert readings.get(surface) == spoken, f"missing/incorrect: {surface}"


def test_no_duplicate_surfaces_even_case_insensitive():
    surfaces = [e["surface"] for e in _load_entries()]
    assert len(surfaces) == len(set(surfaces))
    folded = [s.casefold() for s in surfaces]
    assert len(folded) == len(set(folded))


def test_entries_have_valid_kind_evidence_and_katakana_spoken():
    import re

    katakana_only = re.compile(r"^[\u30a1-\u30f4\u30fc]+$")
    for entry in _load_entries():
        assert entry["kind"] in ALLOWED_KINDS, entry["surface"]
        assert isinstance(entry["evidence"], str) and entry["evidence"].strip()
        assert katakana_only.match(entry["spoken"]), (
            f"spoken must be pure katakana: {entry['surface']} -> {entry['spoken']}"
        )
        # No pointless identity entries (already-katakana surfaces).
        assert entry["surface"] != entry["spoken"]


# ---------------------------------------------------------------------------
# A. Rendering semantics
# ---------------------------------------------------------------------------

def test_longest_surface_wins_over_partial_aliases():
    assert render_tts_pronunciation_surface("牧瀬紅莉栖") == "マキセクリス"
    assert render_tts_pronunciation_surface("牧瀬") == "マキセ"
    assert render_tts_pronunciation_surface("鳳凰院凶真") == "ホウオウインキョウマ"
    assert render_tts_pronunciation_surface("鳳凰院") == "ホウオウイン"
    # The full-name reading must not be assembled from partial replacements.
    assert "マキセ紅莉栖" not in render_tts_pronunciation_surface("牧瀬紅莉栖")


def test_idempotent_for_every_entry_and_combined_sentence():
    for entry in _load_entries():
        once = render_tts_pronunciation_surface(entry["surface"])
        assert render_tts_pronunciation_surface(once) == once
    once = render_tts_pronunciation_surface(COMBINED_SENTENCE)
    assert render_tts_pronunciation_surface(once) == once


def test_plain_japanese_is_never_changed():
    plain = [
        "今日は天気がいいから研究が捗るわね。",
        "紅茶を飲みながら論文を読むのが好きよ。",
        "その仮説は科学的に検証できるはずだわ。",
        "はぁ？何言ってんの、あんた。",
    ]
    for text in plain:
        assert render_tts_pronunciation_surface(text) == text


def test_already_kana_surfaces_are_not_touched():
    for text in ("アマデウス", "クリス", "マキセクリス", "シュタインズゲート"):
        assert render_tts_pronunciation_surface(text) == text


def test_ascii_surfaces_match_case_insensitively():
    for variant in ("Amadeus", "AMADEUS", "amadeus"):
        assert render_tts_pronunciation_surface(variant) == "アマデウス"
    assert render_tts_pronunciation_surface("Steins;Gate") == "シュタインズゲート"
    assert render_tts_pronunciation_surface("sern") == "セルン"


def test_ascii_surface_embedded_in_longer_latin_word_is_not_replaced():
    assert render_tts_pronunciation_surface("AmadeusEngine") == "AmadeusEngine"
    assert render_tts_pronunciation_surface("XSERNX") == "XSERNX"
    # But adjacent Japanese context is a legitimate boundary.
    assert render_tts_pronunciation_surface("Amadeusって") == "アマデウスって"


def test_at_channel_handle_boundaries():
    assert render_tts_pronunciation_surface("@ch") == "アットチャンネル"
    assert render_tts_pronunciation_surface("@ちゃんねる") == "アットチャンネル"
    # A longer latin token must not be partially rewritten.
    assert render_tts_pronunciation_surface("@channel") == "@channel"


# ---------------------------------------------------------------------------
# B. Contract combined sentence
# ---------------------------------------------------------------------------

def test_combined_sentence_renders_expected_reading_surface():
    assert render_tts_pronunciation_surface(COMBINED_SENTENCE) == COMBINED_EXPECTED
