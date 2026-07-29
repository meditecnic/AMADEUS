"""Q87 Slice A: deterministic Chinese-subtitle rewrites (first batch).

Applies only to translation-layer Chinese (or mixed) subtitle strings.
Never mutates Japanese main text, TTS source, or history Japanese content.

NFKC is applied only to candidate match fragments (e.g. captured identifier
tokens), never to the whole subtitle string. Unmatched characters are preserved
glyph-for-glyph. Chinese characters are never identifier boundaries.
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


def _apply_r03_labmen(text: str) -> str:
    """Replace only standalone identifier tokens that NFKC+casefold to labmen."""

    def repl(match: re.Match[str]) -> str:
        raw_token = match.group(0)
        folded = unicodedata.normalize("NFKC", raw_token).casefold()
        if folded == "labmen":
            return "Labmem"
        return raw_token

    return _ID_TOKEN_RE.sub(repl, text)


def _apply_rules(text: str) -> str:
    """Ordered R01→R02→R03→R05→R06. Separated for test monkeypatch of failures."""
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
    return out


def apply_first_batch_rewrites(text: str) -> str:
    """Normalize first-batch Q87 sources in a Chinese subtitle string.

    On any unexpected error: return *text* unchanged and log a stable marker
    only (exception type). Never log the subtitle body.
    """
    if text == "":
        return text
    try:
        return _apply_rules(text)
    except Exception as exc:  # noqa: BLE001 — fail open to raw subtitle
        print(
            f"[SubtitleGlossaryRewrite] failed type={type(exc).__name__}",
            flush=True,
        )
        return text
