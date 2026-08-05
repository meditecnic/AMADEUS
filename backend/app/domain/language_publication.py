"""UI publication vs language-recovery vs TTS eligibility classifiers.

LANG-GATE-PUBLICATION-RETIRE-04 (rework)
---------------------------------------
Three duties must not share one predicate:

1. **Publication (UI / history)** — default allow; block leaks (caller),
   high-confidence foreign *prose*, and collapse garbage.
2. **Language recovery** — isolated Japanese Renderer only for high-confidence
   foreign body text (never neutral data, never collapse garbage).
3. **TTS eligibility** — ``prepare_tts_text`` at the GPT-SoVITS boundary;
   neutral labels are display-only or merged, never toast-grade failures.

No project/topic allowlists. Uncertainty fails open for publication.
Uses only public helpers from ``segments`` plus local category-level Unicode.
"""

from __future__ import annotations

import re
import unicodedata

from app.domain.segments import (
    analysis_surface_without_bounded_embeds,
    is_allowlisted_short_ja_kanji,
    is_ignorable_non_speech_segment,
    meaningful_character_count,
)

_KANA_RE = re.compile(r"[\u3040-\u30ff\u31f0-\u31ff]")
_HAN_RE = re.compile(r"[\u3400-\u9fff\uf900-\ufaff]")
_LATIN_RUN_RE = re.compile(r"[A-Za-z]{2,}")
_HALFWIDTH_KATA_RE = re.compile(r"[\uff61-\uff9f]")
_COLLAPSE_MARK_RE = re.compile(r"[＿_―─━]")
_MULTIWORD_LATIN_RE = re.compile(
    r"[A-Za-zÀ-ÖØ-öø-ÿ]{2,}\s+[A-Za-zÀ-ÖØ-öø-ÿ]{2,}"
)
_MULTIWORD_LATIN_3_RE = re.compile(
    r"[A-Za-zÀ-ÖØ-öø-ÿ]{2,}(?:\s+[A-Za-zÀ-ÖØ-öø-ÿ]{2,}){2,}"
)

# Fully matched title/term wrappers — interior language is irrelevant.
# Category-level paired delimiters (not a vocabulary list).
_FULLY_BOUNDED_FRAGMENT_RES = (
    re.compile(r"^「[^」]+」[。．.!！?？…]*$"),
    re.compile(r"^『[^』]+』[。．.!！?？…]*$"),
    re.compile(r"^《[^》]+》[。．.!！?？…]*$"),
    re.compile(r"^【[^】]+】[。．.!！?？…]*$"),
    re.compile(r"^（[^）]+）[。．.!！?？…]*$"),
    re.compile(r"^\([^)]+\)[。．.!！?？…]*$"),
    re.compile(r'^\[[^\]]+\][。．.!！?？…]*$'),
    re.compile(r'^"[^"\r\n]+"[。．.!！?？…]*$'),
    re.compile(r"^“[^”\r\n]+”[。．.!！?？…]*$"),
    re.compile(r"^`[^`\r\n]+`[。．.!！?？…]*$"),
)

# High-confidence Simplified-Chinese body cues (shared with segments contract).
_CHINESE_ONLY_HAN = frozenset(
    "你您咱们这這谢见对说问请语门马东车红时实现发头买卖过还进远运动让认识记应给谁怎么吗呢吧啊哦呀哈喽嘛哟调开为话很"
)
_CHINESE_ONLY_WORDS = (
    "没有", "根据", "数据", "信息", "什么", "一下", "更多", "可以", "但是",
    "這個", "这个", "等于", "等於", "还是", "還是", "已经", "已經",
    "因为", "因為", "所以", "如果", "我们", "我們", "应该", "應該",
)
_SAFE_LATIN_TOKENS = frozenset({
    "pdf", "api", "dna", "gpt", "url", "http", "https", "json", "xml",
    "cpu", "gpu", "tts", "stt", "id", "ok", "ng", "pc", "ai", "llm",
    "sdk", "cli", "ui", "ux", "csv", "png", "wav", "mp3", "tcp", "udp",
    "ip", "lan", "wan", "ssh", "tls", "ssl", "sql", "rest", "web", "html",
    "css", "js", "mac", "windows", "linux", "ios", "android", "okabe",
    "kurisu", "amadeus", "mayuri", "daru", "suzuha", "lab",
})


def _looks_like_chinese_wording(text: str) -> bool:
    if any(ch in _CHINESE_ONLY_HAN for ch in text):
        return True
    return any(word in text for word in _CHINESE_ONLY_WORDS)


def is_fully_bounded_data_fragment(text: str) -> bool:
    """True when the whole segment is one paired title/term wrapper (+ trailing punct)."""
    stripped = (text or "").strip()
    if not stripped:
        return False
    return any(pattern.fullmatch(stripped) for pattern in _FULLY_BOUNDED_FRAGMENT_RES)


def _strip_trailing_sentence_punct(text: str) -> str:
    return re.sub(r"[。．.!！?？…]+$", "", (text or "").strip()).strip()


def _count_foreign_non_cjk_letters(text: str) -> int:
    """Count letters that are neither Kana nor Han (category-level Unicode).

    Includes Arabic, Hangul, Greek, Cyrillic, Hebrew, Thai, Devanagari, etc.
    Basic Latin is counted separately for product-code vs prose logic.
    """
    n = 0
    for ch in text or "":
        if _KANA_RE.match(ch) or _HAN_RE.match(ch):
            continue
        if ord(ch) < 128:
            continue
        if unicodedata.category(ch).startswith("L"):
            n += 1
    return n


def _has_suspicious_latin_embedding(text: str) -> bool:
    kana_n = len(_KANA_RE.findall(text))
    has_ja_support = kana_n >= 2
    for match in _LATIN_RUN_RE.finditer(text):
        token = match.group(0)
        lowered = token.lower()
        if lowered in _SAFE_LATIN_TOKENS:
            continue
        if token.isupper() and 2 <= len(token) <= 8:
            continue
        if (
            len(token) >= 2
            and token[0].isupper()
            and token[1:].islower()
            and len(token) <= 24
        ):
            continue
        if re.fullmatch(r"[A-Za-z]+\d+|\d+[A-Za-z]+", token) and len(token) <= 12:
            continue
        if re.search(r"[a-z][A-Z]", token):
            if len(token) >= 8:
                return True
            if token[0].isupper() and len(token) <= 6:
                continue
            return True
        if token.islower() and len(token) >= 3:
            if has_ja_support and len(token) <= 16:
                continue
            return True
        if len(token) >= 10 and not has_ja_support:
            return True
    return False


def _has_dominant_unbounded_latin_prose(text: str) -> bool:
    surface = analysis_surface_without_bounded_embeds(text)
    tokens = _LATIN_RUN_RE.findall(surface)
    if len(tokens) < 3:
        return False
    ordinary = [
        token
        for token in tokens
        if token.lower() not in _SAFE_LATIN_TOKENS
        and not (token.isupper() and 2 <= len(token) <= 8)
    ]
    if len(ordinary) < 2:
        return False
    latin_letters = sum(len(token) for token in tokens)
    japanese_letters = len(_KANA_RE.findall(surface)) + len(_HAN_RE.findall(surface))
    return latin_letters >= 8 and latin_letters > japanese_letters


def is_neutral_data_fragment(text: str) -> bool:
    """True for structural data labels / identifiers / fully bounded titles.

    Fully matched bounded fragments (「…」/『…』/《…》/…) are neutral **before**
    any interior language inspection — Chinese titles stay data, not foreign prose.
    """
    if is_ignorable_non_speech_segment(text):
        return False
    raw = (text or "").strip()
    if not raw:
        return False

    # P1-1: complete bounded wrapper → neutral, never inspect interior script.
    if is_fully_bounded_data_fragment(raw):
        return True

    core = _strip_trailing_sentence_punct(raw)
    if not core:
        return False

    # Unquoted Chinese/Japanese prose markers → not a bare data tag.
    if _looks_like_chinese_wording(core) or any(w in core for w in _CHINESE_ONLY_WORDS):
        return False
    if any(ch in _CHINESE_ONLY_HAN for ch in core):
        return False

    kana_n = len(_KANA_RE.findall(core))
    han_n = len(_HAN_RE.findall(core))
    if kana_n >= 2:
        return False
    if kana_n >= 1 and han_n >= 1:
        return False

    # Foreign non-CJK letters (Arabic/Hangul/Greek…) are not product labels.
    if _count_foreign_non_cjk_letters(core) >= 2:
        return False

    if _MULTIWORD_LATIN_3_RE.search(core):
        return False
    if _MULTIWORD_LATIN_RE.search(core):
        tokens = _LATIN_RUN_RE.findall(core)
        ordinary = [t for t in tokens if len(t) >= 3]
        if len(ordinary) >= 2 and sum(len(t) for t in ordinary) >= 12:
            if " " in core or "\u3000" in core:
                return False

    letters = meaningful_character_count(core)
    if letters <= 0 or letters > 64:
        return False

    if kana_n == 0 and han_n == 0:
        compacted = re.sub(r"\s+", "", core)
        if re.search(r"[\d_/;+#]|[A-Za-z]-[A-Za-z0-9]|[A-Za-z0-9]-[A-Za-z0-9]", compacted):
            if not _MULTIWORD_LATIN_3_RE.search(core):
                return True
        if re.fullmatch(r"[A-Z0-9]{1,16}", compacted):
            return True
        if " " not in core and "\u3000" not in core:
            if re.fullmatch(r"[A-Za-z][A-Za-z0-9]*(?:[._][A-Za-z0-9]+)+", compacted):
                return True
            if re.fullmatch(r"[A-Za-z]+(?:[A-Z][a-z0-9]+){1,}", compacted) and letters <= 32:
                return True
        if re.search(r"\d", core) and not _MULTIWORD_LATIN_3_RE.search(core) and letters <= 24:
            return True
        return False

    if han_n > 0 and kana_n == 0:
        if re.search(r"[A-Za-z0-9]", core) and letters <= 24:
            return True
        return False

    return False


def is_high_confidence_foreign_prose(text: str) -> bool:
    """True only for high-confidence complete foreign body text.

    Category-level Unicode: Chinese body cues, Latin/German prose, and any
    non-CJK letter script (Arabic, Hangul, Greek, Cyrillic, …) without a solid
    Japanese frame. Neutral data and ambiguous Han-only Japanese are excluded.
    """
    if is_ignorable_non_speech_segment(text):
        return False
    if is_neutral_data_fragment(text):
        return False
    if is_allowlisted_short_ja_kanji(text):
        return False

    raw = (text or "").strip()
    if not raw:
        return False

    analysis = analysis_surface_without_bounded_embeds(raw)
    surface = analysis if meaningful_character_count(analysis) > 0 else raw

    kana_n = len(_KANA_RE.findall(surface))
    han_n = len(_HAN_RE.findall(surface))
    chinese_word_hits = sum(1 for word in _CHINESE_ONLY_WORDS if word in surface)
    chinese_han_hits = sum(1 for ch in surface if ch in _CHINESE_ONLY_HAN)
    foreign_letters = _count_foreign_non_cjk_letters(surface)

    # Solid Japanese frame: recover only when foreign body remains *outside* embeds.
    if kana_n >= 2:
        if chinese_word_hits >= 1 or chinese_han_hits >= 2:
            return True
        if foreign_letters >= 4:
            return True
        if _has_dominant_unbounded_latin_prose(raw):
            return True
        return False

    # Chinese body on outer surface (no solid kana frame).
    if _looks_like_chinese_wording(surface):
        return True

    if _has_dominant_unbounded_latin_prose(raw):
        return True

    # Category-level non-CJK letter prose (Arabic, Hangul, Greek, Cyrillic, …).
    if kana_n == 0 and han_n == 0 and foreign_letters >= 2:
        return True
    if kana_n == 0 and foreign_letters >= 6 and foreign_letters > han_n:
        return True

    # Pure Latin answers / greetings (not product ids).
    if kana_n == 0 and han_n == 0:
        if re.search(r"[A-Za-zÀ-ÖØ-öø-ÿ]", surface):
            if _MULTIWORD_LATIN_RE.search(surface) or _MULTIWORD_LATIN_3_RE.search(surface):
                return True
            tokens = _LATIN_RUN_RE.findall(surface)
            if tokens and not re.search(r"[\d_/;+#]", surface):
                return True
        return False

    # Shared-Han ambiguous lines without Chinese-only cues fail open.
    return False


def is_language_collapse_garbage(text: str) -> bool:
    """High-confidence model collapse / halfwidth soup — not open-domain Japanese."""
    if is_ignorable_non_speech_segment(text):
        return False
    if is_neutral_data_fragment(text):
        return False
    raw = (text or "").strip()
    if not raw:
        return False
    if _HALFWIDTH_KATA_RE.search(raw):
        return True
    lowered = raw.lower()
    if "flee" in lowered or "newidinfo" in lowered:
        return True
    analysis = analysis_surface_without_bounded_embeds(raw)
    gate = analysis if analysis.strip() else raw
    letters = meaningful_character_count(gate)
    kana_n = len(_KANA_RE.findall(gate))
    han_n = len(_HAN_RE.findall(gate))
    collapse_marks = len(_COLLAPSE_MARK_RE.findall(raw))
    has_latin = bool(_LATIN_RUN_RE.search(raw))
    # Pure non-CJK Latin/foreign letter bodies are foreign *prose*, not collapse.
    # Collapse requires halfwidth kata, incident tokens, bar soup, or CJK noise.
    if kana_n == 0 and han_n == 0:
        return False
    if _has_suspicious_latin_embedding(raw):
        # Mixed CJK + garbage Latin (flee/newidInfo class) only.
        if letters >= 16 and kana_n < 4 and (collapse_marks >= 1 or han_n >= 4):
            return True
    if letters >= 20 and collapse_marks >= 2 and has_latin:
        return True
    if letters >= 40:
        unique = len({ch for ch in gate if not ch.isspace()})
        unique_ratio = unique / max(letters, 1)
        kana_ratio = kana_n / max(letters, 1)
        if (
            unique_ratio > 0.82
            and kana_ratio < 0.12
            and han_n > kana_n * 2
            and (collapse_marks >= 1 or has_latin)
        ):
            return True
    if letters >= 16 and kana_n < 4 and han_n >= max(10, kana_n * 4):
        if collapse_marks >= 1 or has_latin:
            return True
    return False


def is_ui_publishable_segment(text: str) -> bool:
    """Publication gate: fail-open except foreign prose and collapse garbage."""
    if is_ignorable_non_speech_segment(text):
        return False
    if is_neutral_data_fragment(text):
        return True
    if is_high_confidence_foreign_prose(text):
        return False
    if is_language_collapse_garbage(text):
        return False
    return True


def should_route_to_language_renderer(text: str) -> bool:
    """True only for high-confidence foreign prose — never neutral data / collapse."""
    if is_neutral_data_fragment(text):
        return False
    if is_language_collapse_garbage(text):
        return False
    return is_high_confidence_foreign_prose(text)
