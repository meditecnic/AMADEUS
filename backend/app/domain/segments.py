"""Incremental sentence boundaries and ordered per-turn segment processing."""

from __future__ import annotations

import asyncio
import inspect
import json
import re
from dataclasses import dataclass
from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Any, Awaitable, Callable, Iterable


class AudioErrorCode(StrEnum):
    EMPTY_TEXT = "empty_text"
    ACTION_ONLY = "action_only"
    TOO_SHORT = "too_short"
    # Fail-closed preflight: source is not safe Japanese speech for default TTS.
    INVALID_LANGUAGE = "invalid_language"
    # Intentional UI-only segment (neutral data label): no sidecar call, no toast.
    DISPLAY_ONLY = "display_only"
    # Fail-closed preflight: segment source exceeds the sidecar-safe codepoint budget.
    TEXT_TOO_LONG = "text_too_long"
    USER_DISABLED = "user_disabled"
    VOICE_STOPPED = "voice_stopped"
    TIMEOUT = "timeout"
    RATE_LIMITED = "rate_limited"
    SERVICE_UNAVAILABLE = "service_unavailable"
    SYNTHESIS_FAILED = "synthesis_failed"


# Intentional UI-only skips: no segment.audio_error (desktop toasts any reason
# except voice_stopped). Real voice_stopped still emits for protocol.
SILENT_AUDIO_SKIP_REASONS = frozenset({
    AudioErrorCode.DISPLAY_ONLY.value,
})


@dataclass(frozen=True, slots=True)
class PreparedTTSText:
    text: str
    error: AudioErrorCode | None = None


# Per-request budget keeps GPT-SoVITS T2S well below the ~1500 decode ceiling.
# Segmentation already splits on punctuation; a single spoken unit should not approach this.
MAX_TTS_SOURCE_CODEPOINTS = 280

_NON_SPEECH_RE = re.compile(
    r"[\s。！\uff01？\uff1f!?,.\uff0c\u3001\"'`「」『』()（）\-—_+=\[\]{}<>#~*…]"
)
# Bare ellipsis / punctuation runs must never fail closed as "language collapse".
_IGNORABLE_NON_SPEECH_RE = re.compile(
    r"^[\s。！\uff01？\uff1f!?,.\uff0c\u3001\"'`「」『』()（）\-—_+=\[\]{}<>#~*…・．]+$"
)
_KANA_RE = re.compile(r"[\u3040-\u30ff\u31f0-\u31ff]")
_HAN_RE = re.compile(r"[\u3400-\u9fff\uf900-\ufaff]")
_LATIN_RUN_RE = re.compile(r"[A-Za-z]{2,}")
_HALFWIDTH_KATA_RE = re.compile(r"[\uff61-\uff9f]")
# Collapse-only marks for incident soup (not fullwidth JP punctuation （）『』).
# Former _WEIRD_MARK_RE (U+FF00 block) mis-counted fullwidth parens as weird.
_COLLAPSE_MARK_RE = re.compile(r"[＿_―─━]")
# Allowlisted Latin tokens that appear in legitimate Kurisu-register replies.
_SAFE_LATIN_TOKENS = frozenset({
    "pdf", "api", "dna", "gpt", "url", "http", "https", "json", "xml",
    "cpu", "gpu", "tts", "stt", "id", "ok", "ng", "pc", "ai", "llm",
    "sdk", "cli", "ui", "ux", "csv", "png", "wav", "mp3", "tcp", "udp",
    "ip", "lan", "wan", "ssh", "tls", "ssl", "sql", "rest", "web", "html",
    "css", "js", "mac", "windows", "linux", "ios", "android", "mackenzie",
    "okabe", "kurisu", "amadeus", "mayuri", "daru", "suzuha", "fb", "lab",
    "vhf", "uhf", "ghz", "mhz", "httpx", "openai", "deepseek",
    # Common technical loanwords that appear inside legitimate Japanese speech.
    "model", "models", "parameter", "parameters", "version", "update", "search",
    "result", "results", "flagship", "token", "tokens", "context", "prompt",
    "chat", "agent", "agents", "benchmark", "release", "beta", "alpha",
    "moonshot", "kimi", "claude", "gemini", "grok",
})
# High-frequency Japanese function/grammar markers that real speech almost always carries once it is long enough.
_JA_FUNCTION_HINT_RE = re.compile(
    r"[はがをにのでへとやもかねしか]|です|ます|だ|た|て|ない|いる|ある|する|なる|こと|もの|よう|から|まで|より"
)
# Conservative simplified-Chinese markers (aligned with diagnostics language gate).
_CONSERVATIVE_ZH_MARKERS = frozenset("的是不了在有人这中大为上个国我以要他时来用们生到作地于出就分对成会可主发年动同工也能下过子说产种面而方后多定行学法所民得经十三之进着等部度家电力里如水化高自二理起小物现实加量都两体制机当使点从业本去把性好应开它合还因由其些然前外天政四日那社义事平形相全表间样与关各重新线内数正心反你明看原又么利比或但质气第向道命此变条只没结解问意建月公无系军很情者最立代想已通并提直题党程展五果料象员革位入常文总次品式活设及管特件长求老头基资边流路级少图山统接知较将组见计别她手角期根论运农指几九区强放决西被干做必战先回则任取据处队南给色光门即保治北造百规热领七海口东导器压志世金增争济阶油思术极交受联什认六共权收证改清己美再采转更单风切打白教速花带安场身车例真务具万每目至达走积示议声报斗完类八离华名确才科张信马节话米整空元况今集温传土许步群广石记需段研界拉林律叫且究观越织装影算低持音众书布复容儿须际商非验连断深难近矿千周委素技备半办青省列习响约支般史感劳便团往酸历市克何除消构府称太准精值号率族维划选标写存候毛亲快效斯院查江型眼王按格养易置派层片始却专状育厂京识适属圆包火住调满县局照参红细引听该铁价严龙飞")

# Characters that natural Japanese essentially never prints (simplified-only
# forms and Chinese colloquial particles). Presence is a HIGH-CONFIDENCE sign
# the Han-only line is Chinese wording, not kana-less Japanese.
# NOTE: shinjitai shared with simplified (学/国/体…) must stay OUT of this set.
_CHINESE_ONLY_HAN = frozenset(
    "你您咱们这這谢见对说问请语门马东车红时实现发头买卖过还进远运动让认识记应给谁怎么吗呢吧啊哦呀哈喽嘛哟调开为话很"
)

# Chinese-only multi-character words whose glyphs are all shared with Japanese
# (没有/根据/数据…). Japanese never writes these compounds; they mark a
# Chinese draft even when every single character looks JP-safe.
_CHINESE_ONLY_WORDS = (
    "没有", "根据", "数据", "信息", "什么", "一下", "更多", "可以", "但是",
    "這個", "这个", "等于", "等於", "还是", "還是", "已经", "已經",
    "因为", "因為", "所以", "如果", "我们", "我們", "应该", "應該",
)

# LANG-GATE-CODE-SWITCH-RELAX-02: bounded quote/paren/bracket interiors are
# embedded data (terms, titles, citations). Their Chinese/Latin content must not
# alone hard-reject a Japanese outer sentence. Category-level — not per-term
# allowlists. Patterns are non-greedy single-level pairs.
_BOUNDED_EMBED_RES = (
    re.compile(r"「[^」]*」"),
    re.compile(r"『[^』]*』"),
    re.compile(r"（[^）]*）"),
    re.compile(r"\([^)]*\)"),
    re.compile(r"【[^】]*】"),
    re.compile(r'"[^"\r\n]*"'),
    re.compile(r"“[^”\r\n]*”"),
    re.compile(r"`[^`\r\n]*`"),
)


def analysis_surface_without_bounded_embeds(text: str) -> str:
    """Return text with bounded embed interiors removed for language analysis.

    Delimiters and interiors are stripped (category-level). Callers that publish
    or synthesize must keep the original string; this surface is analysis-only.
    """
    out = text or ""
    for _ in range(4):
        prev = out
        for pattern in _BOUNDED_EMBED_RES:
            out = pattern.sub("", out)
        if out == prev:
            break
    return out


def _looks_like_chinese_wording(text: str) -> bool:
    if any(ch in _CHINESE_ONLY_HAN for ch in text):
        return True
    return any(word in text for word in _CHINESE_ONLY_WORDS)

# Short kanji-only Japanese speech (no kana) that must remain publishable.
# Pure Chinese greetings (你好/谢谢/…) must NOT match this set.
_SHORT_JA_KANJI_ALLOWLIST = frozenset({
    "了解", "大丈夫", "承知", "感謝", "失礼", "本当", "馬鹿",
    "何故", "勿論", "当然", "結構", "残念", "無事", "無理",
    "駄目", "承知した", "了解した", "大丈夫か", "本当か",
})

def _core_cjk_letters(text: str) -> str:
    """Letters only (no punctuation/whitespace) for short allowlist match."""
    return _NON_SPEECH_RE.sub("", text or "")


def is_allowlisted_short_ja_kanji(text: str) -> bool:
    """True for brief Japanese kanji-only utterances (了解。/大丈夫？)."""
    core = _core_cjk_letters(text)
    if not core or len(core) > 6:
        return False
    if _KANA_RE.search(core) or _LATIN_RUN_RE.search(core):
        return False
    return core in _SHORT_JA_KANJI_ALLOWLIST


def _is_publishable_kanji_only_japanese(text: str) -> bool:
    """LANG-GATE-RELAX-01: Han-only (no kana) lines are publishable Japanese
    unless a HIGH-CONFIDENCE Chinese/garbage signal fires.

    Live incident: 『岡部倫太郎、通称鳳凰院凶真。』 was hard-rejected three
    times just for lacking kana, and the whole turn was replaced by the
    language-failure fallback. Missing kana and raw length are NOT collapse
    evidence — kanji-dense titles/terms/quotes stay publishable at any length.
    """
    core = _core_cjk_letters(text)
    if len(core) <= 0:
        return False
    # Definitely-Chinese wording (你好/谢谢/没有/根据…): only the short Japanese
    # interjection allowlist may pass — the E-series contract stays intact.
    if _looks_like_chinese_wording(text):
        return is_allowlisted_short_ja_kanji(text)
    if _HALFWIDTH_KATA_RE.search(text):
        return False
    if _has_suspicious_latin_embedding(text):
        return False
    if len(_COLLAPSE_MARK_RE.findall(text)) >= 2:
        return False
    # Open-domain contract: missing kana is not evidence of Chinese. Shared-Han
    # surfaces such as 「一文目」「笑話」「世界線収束範囲」 cannot be classified
    # safely by a topic whitelist. They remain publishable unless one of the
    # high-confidence Chinese/garbage checks above fires.
    return True


def clean_text_for_tts(text: str) -> str:
    """Remove stage directions while preserving the spoken Japanese text."""
    cleaned = re.sub(r"\(.*?\)", "", text)
    cleaned = re.sub(r"（.*?）", "", cleaned)
    cleaned = re.sub(r"\[.*?\]", "", cleaned)
    cleaned = re.sub(r"\*.*?\*", "", cleaned)
    return cleaned.strip()


def meaningful_character_count(text: str) -> int:
    return len(_NON_SPEECH_RE.sub("", text))


def is_meaningful_sentence(text: str) -> bool:
    return meaningful_character_count(text) > 0


def is_ignorable_non_speech_segment(text: str) -> bool:
    """True for bare ellipsis/punctuation that must not reject a turn mid-stream.

    Japanese writers often produce 「ね。……続き」; the segmenter may split a
    standalone 「……」 between sentences. That fragment is not speech and must
    never flip publication_rejected / source_collapse.
    """
    stripped = (text or "").strip()
    if not stripped:
        return True
    if _IGNORABLE_NON_SPEECH_RE.fullmatch(stripped):
        return True
    # No kana/han/latin left after stripping common non-speech marks.
    return meaningful_character_count(stripped) <= 0


def classify_tts_script(text: str) -> str:
    """Local script class for TTS preflight (mirrors diagnostics, no DB dependency)."""
    has_kana = bool(_KANA_RE.search(text))
    has_han = bool(_HAN_RE.search(text))
    has_alnum = bool(re.search(r"[A-Za-z0-9]", text))
    if not has_kana and not has_han and not has_alnum:
        return "symbol_only"
    has_zh_marker = any(character in _CONSERVATIVE_ZH_MARKERS for character in text)
    if has_kana and has_zh_marker:
        return "mixed"
    if has_kana:
        return "ja"
    if has_han:
        return "zh"
    return "unknown"


def _has_suspicious_latin_embedding(text: str) -> bool:
    """Detect garbage Latin (flee/newidInfo) without banning PDF/API/DNA/names.

    Japanese technical speech often embeds lowercase loanwords (parameters).
    Those are allowed when the sentence already has solid Japanese support.
    """
    kana_n = len(_KANA_RE.findall(text))
    has_ja_support = kana_n >= 2 or bool(_JA_FUNCTION_HINT_RE.search(text))
    for match in _LATIN_RUN_RE.finditer(text):
        token = match.group(0)
        lowered = token.lower()
        if lowered in _SAFE_LATIN_TOKENS:
            continue
        # Short all-caps tokens (PDF, API, DNA, HTML) are treated as safe labels.
        if token.isupper() and 2 <= len(token) <= 8:
            continue
        # Title Case proper names (Mackenzie / Moonshot) are allowed.
        if (
            len(token) >= 2
            and token[0].isupper()
            and token[1:].islower()
            and len(token) <= 24
        ):
            continue
        # Mixed alnum product codes (K3, GPT4) are not collapse by themselves.
        if re.fullmatch(r"[A-Za-z]+\d+|\d+[A-Za-z]+", token) and len(token) <= 12:
            continue
        # camelCase / internal capitals:
        # - newidInfo (long) → hard-fail collapse
        # - MoE / PoC / TfT (short tech acronyms) → allowed in JP tech speech
        if re.search(r"[a-z][A-Z]", token):
            if len(token) >= 8:
                return True
            if token[0].isupper() and len(token) <= 6:
                continue
            return True
        # Lowercase words: allow short/common loanwords inside real Japanese.
        if token.islower() and len(token) >= 3:
            if has_ja_support and len(token) <= 16:
                continue
            return True
        if len(token) >= 10 and not has_ja_support:
            return True
    return False


def _has_dominant_unbounded_latin_prose(text: str) -> bool:
    """Reject foreign prose disguised by a small Japanese prefix or suffix.

    Product names and short technical labels remain valid. Multi-word foreign
    quotations remain valid when bounded by quotes/brackets and are removed
    from the analysis surface above. What is rejected here is an unbounded run
    whose Latin prose dominates the actual Japanese sentence frame.
    """

    surface = analysis_surface_without_bounded_embeds(text)
    tokens = _LATIN_RUN_RE.findall(surface)
    if len(tokens) < 3:
        return False
    ordinary_tokens = [
        token
        for token in tokens
        if token.lower() not in _SAFE_LATIN_TOKENS
        and not (token.isupper() and 2 <= len(token) <= 8)
    ]
    if len(ordinary_tokens) < 2:
        return False
    latin_letters = sum(len(token) for token in tokens)
    japanese_letters = len(_KANA_RE.findall(surface)) + len(_HAN_RE.findall(surface))
    return latin_letters >= 8 and latin_letters > japanese_letters


def is_soft_skip_unpublishable_segment(text: str) -> bool:
    """Fragments that must not abort the rest of a turn when unpublishable.

    Examples: bare ellipsis; short product-name sentences like 「Kimi K3。」.
    Long Chinese/English prose returns False so callers hard-reject.
    """
    if is_ignorable_non_speech_segment(text):
        return True
    stripped = (text or "").strip()
    if not stripped:
        return True
    has_kana = bool(_KANA_RE.search(stripped))
    has_han = bool(_HAN_RE.search(stripped))
    if has_kana or has_han:
        return False
    # Pure Latin / digits / punctuation product fragments.
    letters = meaningful_character_count(stripped)
    return 0 < letters <= 32


def is_plausible_japanese_tts_source(text: str) -> bool:
    """Reject Chinese-only, random CJK soup, and non-speech noise for default JA TTS.

    Prior iterations over-trusted kana count and particle hits, so kana-heavy
    garbage with embedded Latin (flee/newidInfo/halfwidth kata) still passed.
    Later over-tightened gates false-rejected legitimate speech (bare ellipsis
    segments, short kanji interjections, medium-length low-kana lines).

    LANG-GATE-CODE-SWITCH-RELAX-02: Chinese/Latin *inside* bounded quotes or
    fullwidth/halfwidth parentheses is treated as embedded data. Chinese-marker
    and Han/kana density checks run on the outer Japanese frame only. Halfwidth
    kata and flee/newidInfo-class Latin collapse still inspect the full string.
    Published/TTS canonical text remains the original (this function does not
    rewrite).
    """
    if not text or not text.strip():
        return False
    if is_ignorable_non_speech_segment(text):
        return False

    # Analysis surface: drop bounded embeds for Chinese-marker + density gates.
    analysis = analysis_surface_without_bounded_embeds(text)
    analysis_letters = meaningful_character_count(analysis)
    # Value compare (not object identity): True when embed interiors were removed.
    analysis_changed = analysis != text
    # No outer frame left → not a Japanese sentence with cited embeds; fall back
    # to full-text checks (pure Chinese / pure English / quote-only body).
    gate_text = analysis if analysis_letters > 0 else text

    script = classify_tts_script(gate_text)
    if script in {"symbol_only", "unknown"}:
        # Outer frame may be Latin-only product labels after stripping embeds;
        # fall back to full-text script once before fail-closed.
        if analysis_changed:
            script = classify_tts_script(text)
        if script in {"symbol_only", "unknown"}:
            return False
    # High-confidence Chinese wording on the *outer* frame only.
    if _looks_like_chinese_wording(gate_text):
        return is_allowlisted_short_ja_kanji(text)
    # Han-only lines (script "zh") are no longer fail-closed on missing kana;
    # they publish unless a high-confidence garbage signal fires.
    if script == "zh":
        return _is_publishable_kanji_only_japanese(gate_text)

    kana = _KANA_RE.findall(gate_text)
    han = _HAN_RE.findall(gate_text)
    kana_n = len(kana)
    han_n = len(han)
    letters = meaningful_character_count(gate_text)
    if letters <= 0:
        return False
    if _has_dominant_unbounded_latin_prose(text):
        return False
    # Short Japanese is often kana-only ("うん").
    if letters <= 10:
        if kana_n >= 1:
            return True
        return False
    # Collapse signals inspect the full original (not only the outer frame).
    if _HALFWIDTH_KATA_RE.search(text):
        return False
    if _has_suspicious_latin_embedding(text):
        return False
    # Random Han-dominant soup with a couple of kana (the incident shape).
    if letters >= 16 and kana_n < 4 and han_n >= max(10, kana_n * 4):
        return False
    if letters >= 24 and (kana_n / letters) < 0.12 and han_n > kana_n * 2:
        return False
    # Long speech without any Japanese function/grammar hints is almost never legitimate.
    if letters >= 28 and not _JA_FUNCTION_HINT_RE.search(gate_text) and (kana_n / letters) < 0.22:
        return False
    # Fullwidth JP punctuation （）『』 must NOT count as collapse marks (MoE parens).
    collapse_marks = len(_COLLAPSE_MARK_RE.findall(text))
    if letters >= 20 and collapse_marks >= 2 and _LATIN_RUN_RE.search(text):
        return False
    # Extremely high unique-character density is typical of model garbage, not dialogue.
    # Real Japanese plot/tech summaries are often kanji-heavy with high uniqueness
    # and fullwidth parentheses around acronyms (MoE) — that must remain publishable.
    unique = len(set(ch for ch in gate_text if not ch.isspace()))
    unique_ratio = unique / max(letters, 1)
    if letters >= 40 and unique_ratio > 0.82:
        has_hint = bool(_JA_FUNCTION_HINT_RE.search(gate_text))
        kana_ratio = kana_n / max(letters, 1)
        # Collapse soup: high uniqueness AND no grammar / almost no kana.
        if not has_hint and kana_ratio < 0.22:
            return False
        # Underscore / bar soup (incident fixtures) even if a particle appears.
        if collapse_marks >= 2 and unique_ratio > 0.90 and han_n >= kana_n:
            return False
        if collapse_marks >= 1 and _LATIN_RUN_RE.search(text) and kana_ratio < 0.32:
            return False
    return True


def should_hard_reject_tts_source(text: str) -> bool:
    """True only for substantive bad sources that must stop further segment accept.

    LANG-GATE-PUBLICATION-RETIRE-04: neutral data fragments and soft TTS
    preflight failures must NOT flip source_collapse. Only high-confidence
    foreign prose and collapse garbage hard-stop the TTS accept path.
    """
    if is_ignorable_non_speech_segment(text):
        return False
    # Local import avoids circular import at module load.
    from app.domain.language_publication import (
        is_high_confidence_foreign_prose,
        is_language_collapse_garbage,
        is_neutral_data_fragment,
    )

    if is_neutral_data_fragment(text):
        return False
    if is_soft_skip_unpublishable_segment(text):
        return False
    if is_high_confidence_foreign_prose(text) or is_language_collapse_garbage(text):
        return True
    prepared = prepare_tts_text(text)
    if prepared.error is None:
        return False
    if prepared.error in {
        AudioErrorCode.EMPTY_TEXT,
        AudioErrorCode.ACTION_ONLY,
        AudioErrorCode.TOO_SHORT,
        AudioErrorCode.INVALID_LANGUAGE,
    }:
        # Non-speech, data, or soft language: skip/fail audio, do not collapse turn.
        return False
    if prepared.error is AudioErrorCode.TEXT_TOO_LONG:
        return False
    return True


def prepare_tts_text(text: str) -> PreparedTTSText:
    """Fail-closed preflight for every sidecar-bound utterance.

    Rejects empty/action/too-short text, non-Japanese speech sources, and overlong
    segments before any GPT-SoVITS HTTP call. Returns an immutable cleaned snapshot.
    """
    if not text or not text.strip():
        return PreparedTTSText("", AudioErrorCode.EMPTY_TEXT)
    if is_ignorable_non_speech_segment(text):
        return PreparedTTSText(text.strip(), AudioErrorCode.ACTION_ONLY)
    cleaned = clean_text_for_tts(text)
    if not cleaned or not is_meaningful_sentence(cleaned):
        return PreparedTTSText(cleaned, AudioErrorCode.ACTION_ONLY)
    if meaningful_character_count(cleaned) < 2:
        return PreparedTTSText(cleaned, AudioErrorCode.TOO_SHORT)
    if len(cleaned) > MAX_TTS_SOURCE_CODEPOINTS:
        return PreparedTTSText(cleaned, AudioErrorCode.TEXT_TOO_LONG)
    if not is_plausible_japanese_tts_source(cleaned):
        return PreparedTTSText(cleaned, AudioErrorCode.INVALID_LANGUAGE)
    return PreparedTTSText(cleaned)


# ---------------------------------------------------------------------------
# Canonical pronunciation lexicon (TTS surface only).
# Rewrites proper-noun readings for the GPT-SoVITS target text exclusively;
# canonical UI text, translations, history, prompts and client payloads keep
# the original surface. The lexicon never injects lore or knowledge — it only
# applies when the model already produced the proper noun.
# ---------------------------------------------------------------------------

_PRONUNCIATION_LEXICON_PATH = (
    Path(__file__).resolve().parents[2] / "characters" / "tts_pronunciation_lexicon_v1.json"
)
_PRONUNCIATION_KINDS = frozenset(
    {"character", "alias", "organization", "system", "location", "handle"}
)


def _is_ascii_surface(surface: str) -> bool:
    return all(ord(ch) < 128 for ch in surface)


@lru_cache(maxsize=1)
def _pronunciation_rules() -> tuple[re.Pattern[str], dict[str, str]]:
    """Compile the version-controlled lexicon into one longest-first pattern.

    Exact surface matches only — no generic transliteration, no fuzzy match.
    ASCII surfaces match case-insensitively with word boundaries so partial
    Latin words are never rewritten.
    """
    data = json.loads(_PRONUNCIATION_LEXICON_PATH.read_text(encoding="utf-8"))
    entries = data["entries"]
    seen: set[str] = set()
    spoken_by_key: dict[str, str] = {}
    parts: list[str] = []
    for entry in sorted(entries, key=lambda e: len(e["surface"]), reverse=True):
        surface = entry["surface"]
        spoken = entry["spoken"]
        kind = entry["kind"]
        folded = surface.casefold()
        if not surface or not spoken or kind not in _PRONUNCIATION_KINDS:
            raise ValueError(f"invalid pronunciation lexicon entry: {entry!r}")
        if folded in seen:
            raise ValueError(f"duplicate pronunciation surface: {surface}")
        seen.add(folded)
        if _is_ascii_surface(surface):
            # Case-insensitive, guarded against partial Latin-word matches.
            parts.append(rf"(?<![0-9A-Za-z])(?i:{re.escape(surface)})(?![0-9A-Za-z])")
            spoken_by_key[folded] = spoken
        else:
            parts.append(re.escape(surface))
            spoken_by_key[surface] = spoken
    return re.compile("|".join(parts)), spoken_by_key


def render_tts_pronunciation_surface(text: str) -> str:
    """Return the sidecar-bound spoken surface for canonical Japanese text.

    Pure and idempotent: longest surface wins, exact matches only, already-kana
    text is untouched, and unmatched text passes through unchanged. Call this
    solely at the GPT-SoVITS HTTP `text` boundary.
    """
    if not text:
        return text
    pattern, spoken_by_key = _pronunciation_rules()

    def _replace(match: re.Match[str]) -> str:
        token = match.group(0)
        return spoken_by_key.get(token) or spoken_by_key.get(token.casefold(), token)

    return pattern.sub(_replace, text)


@dataclass(frozen=True, slots=True)
class Segment:
    id: int
    text: str
    emotion: str = "neutral"


@dataclass(frozen=True, slots=True)
class TurnIdentity:
    conversation_id: str
    turn_id: str
    generation: int


class TransientSegmentError(RuntimeError):
    """A segment operation that is safe to retry within the same turn."""


class _VoiceStopped(RuntimeError):
    pass


def _consume_heartbeat_task(task: asyncio.Task) -> None:
    """Swallow a best-effort heartbeat stop so teardown never fails."""
    try:
        if not task.cancelled():
            task.exception()
    except Exception:
        pass


class IncrementalJapaneseSegmenter:
    _OPEN_TO_CLOSE = {
        "「": "」",
        "『": "』",
        "（": "）",
        "(": ")",
        "【": "】",
        "[": "]",
        "“": "”",
        "‘": "’",
    }
    _CLOSERS = set(_OPEN_TO_CLOSE.values())
    _TERMINATORS = set("。！？!?\n")

    def __init__(self) -> None:
        self._pending = ""
        self._pending_emotions: list[str] = []
        self._short_candidate: tuple[str, str] | None = None
        self._next_id = 0

    @property
    def pending(self) -> str:
        return self._pending

    def feed(self, chunk: str, emotion: str = "neutral") -> list[Segment]:
        if chunk:
            self._pending += chunk
            self._pending_emotions.extend([emotion] * len(chunk))
        return self._drain(final=False)

    def finish(self) -> list[Segment]:
        segments = self._drain(final=True)
        self._pending = ""
        self._pending_emotions = []
        return segments

    def _drain(self, *, final: bool) -> list[Segment]:
        emitted: list[Segment] = []
        while self._pending:
            boundary = self._find_boundary(self._pending)
            if boundary is None:
                break
            raw = self._pending[:boundary]
            text = raw.strip()
            leading = len(raw) - len(raw.lstrip())
            emotion = (
                self._pending_emotions[leading]
                if text and leading < len(self._pending_emotions)
                else "neutral"
            )
            self._pending = self._pending[boundary:]
            self._pending_emotions = self._pending_emotions[boundary:]
            if text:
                self._accept_candidate(emitted, text, emotion)

        if final:
            raw_tail = self._pending
            tail = raw_tail.strip()
            leading = len(raw_tail) - len(raw_tail.lstrip())
            emotion = (
                self._pending_emotions[leading]
                if tail and leading < len(self._pending_emotions)
                else "neutral"
            )
            self._pending = ""
            self._pending_emotions = []
            if tail:
                self._accept_candidate(emitted, tail, emotion)
            if self._short_candidate is not None:
                short_text, short_emotion = self._short_candidate
                self._short_candidate = None
                # Drop trailing bare ellipsis/punctuation; never emit as speech.
                if not is_ignorable_non_speech_segment(short_text):
                    emitted.append(Segment(self._next_id, short_text, short_emotion))
                    self._next_id += 1
        return emitted

    def _accept_candidate(
        self,
        emitted: list[Segment],
        text: str,
        emotion: str,
    ) -> None:
        if self._short_candidate is not None:
            short_text, short_emotion = self._short_candidate
            self._short_candidate = None
            text = short_text + text
            emotion = short_emotion
        # Bare 「……」 after 「ね。」 must merge into the next sentence, not emit alone.
        if is_ignorable_non_speech_segment(text):
            self._short_candidate = (text, emotion)
            return
        if prepare_tts_text(text).error is AudioErrorCode.TOO_SHORT:
            self._short_candidate = (text, emotion)
            return
        emitted.append(Segment(self._next_id, text, emotion))
        self._next_id += 1

    def _find_boundary(self, text: str) -> int | None:
        stack: list[str] = []
        i = 0
        while i < len(text):
            char = text[i]
            expected_close = self._OPEN_TO_CLOSE.get(char)
            if expected_close:
                stack.append(expected_close)
                i += 1
                continue
            if char in self._CLOSERS:
                if stack and stack[-1] == char:
                    stack.pop()
                i += 1
                continue

            end = None
            if char in self._TERMINATORS:
                end = i + 1
                while end < len(text) and text[end] in self._TERMINATORS:
                    end += 1
            elif char == "…":
                end = i
                while end < len(text) and text[end] == "…":
                    end += 1
                if end - i < 2:
                    i = end
                    continue
            elif char == ".":
                end = i
                while end < len(text) and text[end] == ".":
                    end += 1
                if end - i < 3:
                    i = end
                    continue

            if end is None:
                i += 1
                continue

            remaining_stack = list(stack)
            while end < len(text) and text[end] in self._CLOSERS:
                closer = text[end]
                if remaining_stack and remaining_stack[-1] == closer:
                    remaining_stack.pop()
                end += 1
            if not remaining_stack:
                return end
            i = end
        return None


EventEmitter = Callable[[dict], object]
TextOperation = Callable[[str], Awaitable[str]]
AudioOperation = Callable[[str, str], Awaitable[bytes]]
TtsObserver = Callable[[TurnIdentity, Segment, bytes | None, str | None], object]
TurnObserver = Any


class SegmentPipeline:
    """Process segments concurrently while committing their events in order.

    B1 dual-cursor: ``segment.ready`` (ja+zh) commits as soon as translation
    finishes, in segment-id order, without waiting for TTS. Audio commits on
    an independent ordered cursor so a slow TTS never blocks later text.
    ``turn.completed`` still waits for both cursors (necessary attachments).
    """

    def __init__(
        self,
        identity: TurnIdentity,
        *,
        emit: EventEmitter,
        translate: TextOperation,
        synthesize: AudioOperation,
        tts_concurrency: int = 2,
        tts_retries: int = 1,
        translation_retries: int = 1,
        translation_timeout: float = 10.0,
        tts_observer: TtsObserver | None = None,
        turn_observer: TurnObserver | None = None,
        heartbeat_interval: float | None = 10.0,
    ) -> None:
        self.identity = identity
        self._emit_callback = emit
        self._translate = translate
        self._synthesize = synthesize
        self._tts_observer = tts_observer
        self._turn_observer = turn_observer
        self._tts_semaphore = asyncio.Semaphore(max(1, tts_concurrency))
        self._tts_retries = max(0, tts_retries)
        self._translation_retries = max(0, translation_retries)
        if translation_timeout <= 0:
            raise ValueError("translation timeout must be positive")
        self._translation_timeout = translation_timeout
        if heartbeat_interval is not None and heartbeat_interval <= 0:
            raise ValueError("heartbeat interval must be positive or None")
        self._heartbeat_interval = heartbeat_interval
        self._heartbeat_task: asyncio.Task | None = None
        # Text cursor (segment.ready) and audio cursor (segment.audio/error)
        # are independent; only the public event order is contractual.
        self._text_buffer: dict[int, tuple[Segment, str, str | None]] = {}
        self._audio_buffer: dict[int, tuple[Segment, bytes | None, str | None]] = {}
        self._committed_translations: list[str] = []
        self._translation_error_count = 0
        self._next_text_id = 0
        self._next_audio_id = 0
        self._text_lock = asyncio.Lock()
        self._audio_lock = asyncio.Lock()
        self._segment_tasks: set[asyncio.Task] = set()
        self._voice_tasks: set[asyncio.Task] = set()
        self._voice_stopped = False
        self._cancelled = False
        self._cancel_event_sent = False
        self._complete_event_sent = False
        self._running = False

    @property
    def translation_text(self) -> str:
        """Return translations in the exact order committed to the client."""
        return "".join(self._committed_translations)

    @property
    def has_translation_errors(self) -> bool:
        """Whether any committed segment exhausted its translation attempts."""
        return self._translation_error_count > 0

    async def run(self, segments: Iterable[Segment]) -> None:
        await self.start()
        items = list(segments)
        if items:
            lowest = min(segment.id for segment in items)
            self._next_text_id = lowest
            self._next_audio_id = lowest
        for segment in items:
            self.submit(segment)
        await self.finish(segment_count=len(items))

    async def start(self) -> None:
        if self._running:
            raise RuntimeError("segment pipeline can only run once")
        self._running = True
        await self._emit(self._event("turn.started"))
        if self._turn_observer is not None:
            try:
                self._turn_observer.turn_started(self.identity)
            except Exception:
                pass
        self._start_heartbeat()

    def submit(self, segment: Segment) -> None:
        if not self._running:
            raise RuntimeError("segment pipeline has not started")
        if self._cancelled:
            return
        self._segment_tasks.add(asyncio.create_task(self._process(segment)))

    async def finish(
        self,
        *,
        segment_count: int | None = None,
        emit_completed: bool = True,
    ) -> None:
        if self._segment_tasks:
            await asyncio.gather(*self._segment_tasks, return_exceptions=True)
        completed_count = segment_count
        if completed_count is None:
            completed_count = self._next_text_id
        self._segment_tasks.clear()
        if emit_completed:
            await self.complete(segment_count=completed_count)

    async def complete(self, *, segment_count: int) -> None:
        if self._cancelled or self._complete_event_sent:
            return
        self._complete_event_sent = True
        await self._stop_heartbeat()
        await self._emit(self._event("turn.completed", segment_count=segment_count))
        if self._turn_observer is not None:
            try:
                self._turn_observer.turn_terminal(self.identity, "succeeded")
            except Exception:
                pass

    def _start_heartbeat(self) -> None:
        """Start the turn-scoped liveness loop (proves alive, never progress)."""
        if self._heartbeat_interval is None or self._heartbeat_task is not None:
            return
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

    async def _stop_heartbeat(self) -> None:
        task = self._heartbeat_task
        self._heartbeat_task = None
        if task is None:
            return
        # Best-effort stop: never await teardown here. Awaiting a background
        # sleep races connection teardown (TestClient portal cancels the
        # awaiting chain and the whole websocket exit fails). Flags set by
        # complete()/cancel() already prevent any further heartbeat emit.
        if not task.done():
            task.cancel()
            task.add_done_callback(_consume_heartbeat_task)

    async def _heartbeat_loop(self) -> None:
        assert self._heartbeat_interval is not None
        try:
            while True:
                await asyncio.sleep(self._heartbeat_interval)
                if self._cancelled or self._complete_event_sent:
                    return
                await self._emit(self._event("turn.heartbeat"))
        except asyncio.CancelledError:
            pass

    async def stop_voice(self) -> None:
        self._voice_stopped = True
        tasks = list(self._voice_tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def cancel(self, *, reason: str | None = "user_turn_cancel") -> None:
        if self._cancelled:
            return
        self._cancelled = True
        tasks = [*self._segment_tasks, *self._voice_tasks]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await self._stop_heartbeat()
        if not self._cancel_event_sent:
            self._cancel_event_sent = True
            await self._emit(self._event("turn.cancelled"))
            if self._turn_observer is not None and reason is not None:
                try:
                    self._turn_observer.turn_terminal(self.identity, "cancelled", reason)
                except Exception:
                    pass

    async def _process(self, segment: Segment) -> None:
        translation_task = asyncio.create_task(self._translate_safe(segment.text))
        voice_task = asyncio.create_task(self._synthesize_safe(segment))
        self._voice_tasks.add(voice_task)
        try:
            try:
                translation, translation_error = await translation_task
            except asyncio.CancelledError:
                voice_task.cancel()
                await asyncio.gather(translation_task, voice_task, return_exceptions=True)
                raise
            # Text commits as soon as translation finishes; never wait for TTS.
            await self._submit_text(segment, translation, translation_error)
            try:
                audio, audio_error = await voice_task
            except asyncio.CancelledError:
                # Only a real stop_voice() may synthesize voice_stopped: it sets
                # _voice_stopped before cancelling. Outer-task-first cancellation
                # (flag not yet set) must propagate, never forge a user event.
                if self._cancelled or not self._voice_stopped:
                    raise
                audio, audio_error = None, "voice_stopped"
        except asyncio.CancelledError:
            translation_task.cancel()
            voice_task.cancel()
            await asyncio.gather(translation_task, voice_task, return_exceptions=True)
            raise
        finally:
            self._voice_tasks.discard(voice_task)

        await self._submit_audio(segment, audio, audio_error)

    async def _translate_safe(self, text: str) -> tuple[str, str | None]:
        attempts = self._translation_retries + 1
        last_error: Exception | None = None
        for attempt in range(attempts):
            try:
                translated = await asyncio.wait_for(
                    self._translate(text),
                    timeout=self._translation_timeout,
                )
                if not translated or not translated.strip():
                    return "", "translation_empty"
                return translated.strip(), None
            except asyncio.CancelledError:
                raise
            except asyncio.TimeoutError as exc:
                last_error = exc
            except Exception as exc:
                last_error = exc
                if not self._is_retryable_translation_error(exc):
                    break

            if attempt + 1 < attempts:
                await asyncio.sleep(0.15 * (attempt + 1))

        if isinstance(last_error, asyncio.TimeoutError):
            return "", "translation_timeout"
        return "", "translation_unavailable"

    @staticmethod
    def _is_retryable_translation_error(error: Exception) -> bool:
        if isinstance(error, (TransientSegmentError, TimeoutError, ConnectionError, OSError)):
            return True
        status_code = getattr(error, "status_code", None)
        return status_code in {408, 425, 429} or (
            isinstance(status_code, int) and status_code >= 500
        )

    async def _synthesize_safe(self, segment: Segment) -> tuple[bytes | None, str | None]:
        if self._voice_stopped:
            return None, "voice_stopped"
        attempts = self._tts_retries + 1
        for attempt in range(attempts):
            try:
                async with self._tts_semaphore:
                    if self._voice_stopped:
                        raise _VoiceStopped()
                    return await self._synthesize(segment.text, segment.emotion), None
            except asyncio.CancelledError:
                raise
            except _VoiceStopped:
                return None, "voice_stopped"
            except TransientSegmentError as exc:
                if attempt + 1 >= attempts:
                    return None, str(exc) or "transient_error"
            except Exception as exc:
                code = getattr(exc, "code", None)
                return None, getattr(code, "value", None) or str(exc) or "service_error"
        return None, "service_error"

    async def _submit_text(
        self, segment: Segment, translation: str, translation_error: str | None
    ) -> None:
        """Commit ordered text (segment.ready) without waiting for audio."""
        if self._cancelled:
            return
        async with self._text_lock:
            if self._cancelled:
                return
            self._text_buffer[segment.id] = (segment, translation, translation_error)
            while self._next_text_id in self._text_buffer:
                current_segment, current_translation, current_error = self._text_buffer.pop(
                    self._next_text_id
                )
                ready = self._event(
                    "segment.ready",
                    segment_id=current_segment.id,
                    ja=current_segment.text,
                    zh=current_translation,
                    emotion=current_segment.emotion,
                )
                if current_error:
                    ready["translation_error"] = current_error
                    self._translation_error_count += 1
                await self._emit(ready)
                if self._turn_observer is not None:
                    try:
                        self._turn_observer.segment_committed(self.identity, current_segment)
                    except Exception:
                        pass
                self._committed_translations.append(current_translation)
                self._next_text_id += 1

    async def _submit_audio(
        self, segment: Segment, audio: bytes | None, audio_error: str | None
    ) -> None:
        """Commit ordered audio on its own cursor; never blocks text."""
        if self._cancelled:
            return
        async with self._audio_lock:
            if self._cancelled:
                return
            self._audio_buffer[segment.id] = (segment, audio, audio_error)
            while self._next_audio_id in self._audio_buffer:
                current_segment, current_audio, current_error = self._audio_buffer.pop(
                    self._next_audio_id
                )
                if self._tts_observer is not None:
                    try:
                        observer_error = current_error
                        if (
                            observer_error is None
                            and self._voice_stopped
                            and current_audio is None
                        ):
                            observer_error = "voice_stopped"
                        self._tts_observer(
                            self.identity,
                            current_segment,
                            current_audio,
                            observer_error,
                        )
                    except Exception:
                        # The observer is a strictly best-effort side channel.
                        # Its owner is responsible for recording this failure.
                        pass
                if current_audio is not None and not self._voice_stopped:
                    await self._emit(
                        self._event(
                            "segment.audio",
                            segment_id=current_segment.id,
                            data=current_audio,
                            emotion=current_segment.emotion,
                        )
                    )
                else:
                    reason = current_error or "voice_stopped"
                    # Display-only / non-speech skips: keep segment.ready, never
                    # emit audio_error (desktop treats it as TTS degradation toast).
                    if reason not in SILENT_AUDIO_SKIP_REASONS:
                        await self._emit(
                            self._event(
                                "segment.audio_error",
                                segment_id=current_segment.id,
                                reason=reason,
                                emotion=current_segment.emotion,
                            )
                        )
                self._next_audio_id += 1

    def _event(self, event_type: str, **payload) -> dict:
        return {
            "type": event_type,
            "conversation_id": self.identity.conversation_id,
            "turn_id": self.identity.turn_id,
            "generation": self.identity.generation,
            **payload,
        }

    async def _emit(self, event: dict) -> None:
        result = self._emit_callback(event)
        if inspect.isawaitable(result):
            await result
