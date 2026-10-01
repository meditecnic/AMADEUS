"""Q87 subtitle glossary: deterministic Chinese-subtitle rewrites.

Applies only to translation-layer Chinese (or mixed) subtitle strings.
Never mutates Japanese main text, TTS source, or history Japanese content.

NFKC is applied only to candidate match fragments (e.g. captured identifier
tokens), never to the whole subtitle string. Unmatched characters are preserved
glyph-for-glyph.

R07 (attractor-field) is source-aware: Chinese 吸引场/吸引域 rewrites only when
the original Japanese source contains a verified アトラクタフィールド marker.
"""

from __future__ import annotations

import re
import unicodedata

# Unified identifier atoms: ASCII + fullwidth Latin, digits, underscore.
# Used for R03 whole-token capture and R05/R06 lookaround boundaries.
_ID_ATOM = (
    r"A-Za-z0-9_"
    r"\uFF21-\uFF3A"  # fullwidth A–Z
    r"\uFF41-\uFF5A"  # fullwidth a–z
    r"\uFF10-\uFF19"  # fullwidth 0–9
    r"\uFF3F"  # fullwidth underscore ＿
)
_ID_CHAR_CLASS = f"[{_ID_ATOM}]"
_ID_TOKEN_RE = re.compile(f"{_ID_CHAR_CLASS}+")

# R05 / R06: single-char lookaround (fixed width) against identifier atoms.
_R05_IBN5100_RE = re.compile(
    rf"(?<!{_ID_CHAR_CLASS})IBN5100(?!{_ID_CHAR_CLASS})"
)
_R06_DMAIL_RE = re.compile(
    rf"(?<!{_ID_CHAR_CLASS})(?:D|\uFF24)(?:[ \u3000])?(?:邮件|メール)(?!{_ID_CHAR_CLASS})"
)

# R07: product Chinese sources → target (only when JA source gates open).
_R07_TARGET = "世界线收束范围"
# Verified Japanese markers (glossary G-12 / tip アトラクタフィールド).
# Elongated ー form is the same compound orthographic variant.
_R07_JA_SOURCE_MARKERS: tuple[str, ...] = (
    "アトラクタフィールド",
    "アトラクターフィールド",
)


def _apply_r03_labmen(text: str) -> str:
    """Replace only standalone identifier tokens that NFKC+casefold to labmen."""

    def repl(match: re.Match[str]) -> str:
        raw_token = match.group(0)
        folded = unicodedata.normalize("NFKC", raw_token).casefold()
        if folded == "labmen":
            return "Labmem"
        return raw_token

    return _ID_TOKEN_RE.sub(repl, text)


def _ja_source_allows_r07(ja_source: str | None) -> bool:
    """True only when original Japanese source names the attractor-field term."""
    if not ja_source:
        return False
    return any(marker in ja_source for marker in _R07_JA_SOURCE_MARKERS)


def _apply_r07_attractor(text: str, *, ja_source: str | None) -> str:
    """吸引场/吸引域 → 世界线收束范围 iff JA source contains verified markers.

    Fail-safe: no source / source without アトラクタフィールド → no rewrite.
    Does not use Han-suffix blacklists as the primary safety mechanism.
    """
    if not _ja_source_allows_r07(ja_source):
        return text
    out = text.replace("吸引场", _R07_TARGET)
    return out.replace("吸引域", _R07_TARGET)


def _apply_rules(text: str, *, ja_source: str | None = None) -> str:
    """Ordered R01→R02→R03→R05→R06→R07. Separated for test monkeypatch of failures."""
    out = text
    # R01 — full five-character token (exact substring; half tokens never match).
    out = out.replace("克里斯缇娜", "克里斯蒂娜")
    # R02 — full phrase.
    out = out.replace("未来装置研究所", "未来道具研究所")
    # R03 — complete identifier only; NFKC+casefold on capture.
    out = _apply_r03_labmen(out)
    # R05 — IBN5100 with identifier boundaries.
    out = _R05_IBN5100_RE.sub("IBN 5100", out)
    # R06 — D-mail forms with identifier left/right boundaries.
    out = _R06_DMAIL_RE.sub("D-Mail", out)
    # R07 — source-gated attractor-field product form.
    out = _apply_r07_attractor(out, ja_source=ja_source)
    return out


def apply_subtitle_glossary_rewrites(
    text: str,
    source: str | None = None,
) -> str:
    """Normalize Q87 glossary sources in a Chinese subtitle string (P-NORM-1).

    Parameters
    ----------
    text:
        Chinese (or mixed) subtitle from the translation layer.
    source:
        Optional original Japanese segment/turn text. Required for R07 gate;
        R01–R06 ignore it. When omitted, R07 fails closed (no rewrite).

    On any unexpected error: return *text* unchanged and log a stable marker
    only (exception type). Never log the subtitle body.
    """
    if text == "":
        return text
    try:
        return _apply_rules(text, ja_source=source)
    except Exception as exc:  # noqa: BLE001 — fail open to raw subtitle
        print(
            f"[SubtitleGlossaryRewrite] failed type={type(exc).__name__}",
            flush=True,
        )
        return text
