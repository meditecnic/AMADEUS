"""Priority-aware prompt assembly for Amadeus persona, memory and evidence."""

from __future__ import annotations

import hashlib
import json
import logging
import math
import re
import unicodedata
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any, Callable, Iterable, Sequence

from app import config
from app.db import DEFAULT_IDENTITY_MODE, normalize_identity_mode, normalize_worldline
from app.services.llm_interface import TAIL_REMINDER_HEADER
from app.services.memory import RetrievedMemory
from app.services.persona_bible import persona_bible
from app.services.soul_engine import EmotionVector, soul_engine

logger = logging.getLogger(__name__)

# --- Slice B: self-mode user call name (application-level config, not memory) ---

SELF_NAME_MAX_CODEPOINTS = 40

# Rework contract: self_name is a NAME, not free text. Allowlist only — Unicode
# letters/digits, plain space, and common name separators - ' · ・ .
# Everything else (quotes, brackets, colons, slashes, underscores, control or
# format chars, U+2028/U+2029, emoji, symbols) fails closed. No silent repair.
_NAME_ALLOWED_EXTRA = frozenset(" -'\u00b7\u30fb.")


def _is_allowed_name_char(ch: str) -> bool:
    if ch in _NAME_ALLOWED_EXTRA:
        return True
    category = unicodedata.category(ch)
    return category.startswith("L") or category == "Nd"


# Separators/punctuation ignored when comparing against reserved Okabe names.
_NAME_SEPARATORS_RE = re.compile(r"[\s\u3000\u30fb.,\u00b7\-_'\u2019\"\u201c\u201d\u300c\u300d\u300e\u300f()\uff08\uff09!\uff01?\uff1f~\uff5e*\u00d7+|/\\:;\uff1a\uff1b]")

# Q21 guard: names that would blur self mode into the Okabe identity are refused
# on both ends (fail-closed -> treated as unset). Compared after NFKC + casefold
# + separator stripping so fullwidth/case/whitespace/punctuation variants match.
# MUST stay identical to RESERVED_OKABE_NAMES in desktop SettingsModal.tsx.
_RESERVED_OKABE_NAMES = frozenset({
    "\u5ca1\u90e8",                      # 岡部
    "\u5ca1\u90e8\u502b\u592a\u90ce",    # 岡部倫太郎
    "\u5188\u90e8",                      # 冈部 (simplified)
    "\u5188\u90e8\u4f26\u592a\u90ce",    # 冈部伦太郎 (simplified)
    "\u9cf3\u51f0\u9662\u51f6\u771f",    # 鳳凰院凶真
    "\u51e4\u51f0\u9662\u51f6\u771f",    # 凤凰院凶真 (simplified)
    "okabe",
    "rintarookabe",
    "okaberintaro",
    "hououinkyouma",
    "kyoumahououin",
})


def _normalize_name_for_guard(value: str) -> str:
    folded = unicodedata.normalize("NFKC", value).casefold()
    return _NAME_SEPARATORS_RE.sub("", folded)


def is_reserved_okabe_name(value: str) -> bool:
    """True when the name collides with the Okabe identity (any common variant)."""
    if not isinstance(value, str):
        return False
    return _normalize_name_for_guard(value) in _RESERVED_OKABE_NAMES


def sanitize_self_name(value) -> str:
    """Validate the app-level self-mode call name; fail closed to \"\" (unset).

    Order: edge-trim (plain space U+0020 ONLY) -> length cap -> reserved-name
    guard -> character allowlist. Edge newlines/tabs/NBSP/BOM are NOT trimmed:
    they stay in the name and are rejected whole by the allowlist (no silent
    repair). The client mirrors the same contract in the same order.
    """
    if not isinstance(value, str):
        return ""
    name = value.strip(" ")
    if not name:
        return ""
    if len(name) > SELF_NAME_MAX_CODEPOINTS:
        return ""
    if is_reserved_okabe_name(name):
        return ""
    if not all(_is_allowed_name_char(ch) for ch in name):
        return ""
    return name


FACT_USE_COMPLETE_SENTENCE = "リストにないことを知ったかぶりはしない。"
PARTIAL_PROFILE_SENTENCE = "ここにないことまで忘れたと決めつけない。"
PARTIAL_PROFILE_TOOL_SENTENCE = "相手から尋ねられたことがここになければ、必要に応じて記憶を確かめる。"
PARTIAL_PROFILE_NO_TOOL_SENTENCE = "思い出せなければ、今は思い出せないと自然に伝える。"
FACT_USE_CONTRACT = (
    persona_bible.guidance["facts"]
    + "\n中国語で残った記憶は、元の語句を引用せず、自然な日本語に直して話す。"
)

RESIDENT_MAX_FACTS = 32
SUPPLEMENT_MAX_FACTS = 6
SUPPLEMENT_TOKEN_BUDGET = 400
SUPPLEMENT_MIN_SHARED_BIGRAMS = 2
SUPPLEMENT_TIMEOUT_SECONDS = 5.0
FACTS_HEADERS = {"okabe": "【あんたについて覚えていること】", "self": "【あなたについて覚えていること】"}
EPISODES_HEADER = "【最近のやりとりと気になっていること】"
DIARIES_HEADER = "【最近の手記】"
DIARIES_GUIDANCE = (
    "二人の記憶アーカイブに残した私の手記。残っている印象だけを自然な受け答えになじませ、"
    "自分から「日記」や「手記」と持ち出したり、文面をそのまま繰り返したりしない。"
    "あくまで私側の印象の覚え書きで、相手の発言や約束の記録ではない。"
    "手記だけを根拠に、相手が言った・約束したと決めつけて、持ち出したり問いただしたり責めたりしない。"
)
DIARY_MAX_ITEMS = 3
SUMMARY_HEADER = "【これまでの流れ】"
SEARCH_NOT_RUN = "【今回のネット検索：未実施】"
REPLY_FORMAT = "先頭に [EMO:…] を1つだけ置き、本文は自然な日本語のみ"

_OKABE_EXAMPLES = ("A1", "B1", "B2", "C1", "D2", "F2")
_SELF_EXAMPLES = {
    "initial": ("F1", "D1"),
    "familiar": ("A2", "B2", "C1", "D1", "E2", "F2"),
}


def resident_fact_rule(*, complete: bool, tool_capable: bool) -> str:
    """Guide how to speak about known and missing memories."""
    if complete:
        return FACT_USE_CONTRACT
    follow_up = PARTIAL_PROFILE_TOOL_SENTENCE if tool_capable else PARTIAL_PROFILE_NO_TOOL_SENTENCE
    return FACT_USE_CONTRACT.replace(
        FACT_USE_COMPLETE_SENTENCE,
        f"{PARTIAL_PROFILE_SENTENCE}{follow_up}",
    )


@lru_cache(maxsize=1)
def _persona_examples() -> dict[str, list[dict[str, str]]]:
    payload = json.loads((config.PROMPTS_DIR / "kurisu_fewshot.json").read_text(encoding="utf-8"))
    return {row["id"]: row["turns"] for row in payload}


def example_pool(worldline: str, identity_mode: str, familiarity: str) -> tuple[str, ...]:
    if normalize_identity_mode(identity_mode) == "self":
        return _SELF_EXAMPLES[familiarity]
    if normalize_worldline(worldline) == "beta":
        return (*_OKABE_EXAMPLES, "E1")
    return _OKABE_EXAMPLES


def render_persona_examples(
    identity_mode: str,
    *,
    worldline: str = "steins_gate",
    familiarity: str = "initial",
    conversation_key: str = "",
    year: int | None = None,
) -> str:
    """Render at most six compatible examples in a conversation-specific order."""
    year = datetime.now().year if year is None else year
    examples = _persona_examples()
    pool = example_pool(worldline, identity_mode, familiarity)
    ordered = sorted(
        pool,
        key=lambda scene: hashlib.sha256(f"{conversation_key}:{scene}".encode("utf-8")).digest(),
    )
    lines = [
        "【話し方の例】",
        "<examples>",
        "以下は口調の作例。ここでの発言や出来事を実際の会話や記憶と混同せず、台詞をそのまま繰り返さない。",
    ]
    for scene in ordered[:6]:
        lines.append("<example>")
        for turn in examples[scene]:
            content = turn["content"]
            if scene == "E1":
                content = persona_bible.render_elapsed_years(content, year)
            speaker = "ユーザー" if turn["role"] == "user" else "紅莉栖"
            lines.append(f"{speaker}: {content}")
        lines.append("</example>")
    lines.append("</examples>")
    return "\n".join(lines)


def render_tail_reminder(
    identity_mode: str | None = None,
    self_name: str = "",
    *,
    now_local: datetime | None = None,
    previous_user_at: datetime | None = None,
    searched: bool = False,
    supplement: str = "",
) -> str:
    """Render the request-local clock, search state and optional recalled facts."""
    now = now_local or datetime.now()
    if now.tzinfo is None:
        now = now.astimezone()
    weekday = ("月", "火", "水", "木", "金", "土", "日")[now.weekday()]
    season = ("冬", "春", "夏", "秋")[now.month % 12 // 3]
    hour = now.hour
    period = next(label for end, label in (
        (5, "深夜"), (11, "朝"), (16, "昼"), (19, "夕方"), (24, "夜"),
    ) if hour < end)
    offset = now.strftime("%z")
    clock = f"{TAIL_REMINDER_HEADER}{now:%Y-%m-%d} ({weekday}) {now:%H:%M} UTC{offset[:3]}:{offset[3:]} [{season}・{period}]"
    if previous_user_at is not None:
        seconds = (now.astimezone(timezone.utc) - previous_user_at.astimezone(timezone.utc)).total_seconds()
        if seconds >= 86400:
            clock += f"｜前回から約{int(seconds // 86400)}日ぶり"
        elif seconds >= 10800 and 1 <= hour < 5:
            clock += f"｜約{int(seconds // 3600)}時間ぶり・深夜"
    return "\n".join(part for part in (
        clock, "" if searched else SEARCH_NOT_RUN, supplement, REPLY_FORMAT,
    ) if part)


# Kanji, kana and full-width punctuation cost about one token per character;
# everything else is estimated at four characters per token.
_CJK_TOKEN_RE = re.compile(
    "[\u3000-\u303f\u3040-\u309f\u30a0-\u30ff\u31f0-\u31ff\u3400-\u4dbf"
    "\u4e00-\u9fff\uf900-\ufaff\uff00-\uffef\U00020000-\U000323af]"
)


def _timestamp(value: Any) -> float | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def _observation_count(row: dict[str, Any]) -> int:
    count = int(row.get("observation_count") or 0)
    if row.get("has_legacy_import"):
        count = max(count, 2)
    return count


def resident_rank_key(row: dict[str, Any]) -> tuple:
    """Resident ranking: pinned > user_edit > observations > confidence >
    recently updated > fact_id ascending."""
    updated = _timestamp(row.get("updated_at"))
    return (
        0 if row.get("is_pinned") else 1,
        0 if str(row.get("change_kind") or "") == "user_edit" else 1,
        -_observation_count(row),
        -float(row.get("confidence") or 0.0),
        -(updated if updated is not None else -math.inf),
        str(row.get("fact_id") or ""),
    )


def resident_display_key(row: dict[str, Any]) -> tuple:
    """Render order: pinned first, then stable_facts.created_at, then fact_id."""
    created = _timestamp(row.get("created_at"))
    return (
        0 if row.get("is_pinned") else 1,
        created is None,
        created if created is not None else 0.0,
        str(row.get("fact_id") or ""),
    )


def rank_resident_candidates(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Rank candidates and keep only the best-ranked row per semantic_fingerprint."""
    ranked = sorted(
        (row for row in rows if str(row.get("display_text") or "").strip()),
        key=resident_rank_key,
    )
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for row in ranked:
        fingerprint = str(row.get("semantic_fingerprint") or "").strip()
        key = f"fp:{fingerprint}" if fingerprint else f"id:{row.get('fact_id')}"
        if key in seen:
            continue
        seen.add(key)
        unique.append(row)
    return unique


@dataclass(slots=True)
class ResidentProfile:
    """v11 resident CORE FACTS: ``facts`` in rank order (best first)."""

    facts: list[dict[str, Any]]
    candidate_count: int
    tool_capable: bool = False

    @property
    def complete(self) -> bool:
        return len(self.facts) >= self.candidate_count


@dataclass(slots=True)
class MemorySupplement:
    """v11 per-turn MEMORY CANDIDATES block from recall_stable_facts."""

    result: Any  # memory_v11.recall.RecallResult with records


class _PromptExperience(RetrievedMemory):
    experience_id: str


@dataclass(frozen=True, slots=True)
class PromptDiary:
    diary_id: str
    body_ja: str
    relative_time: str = ""


@dataclass(frozen=True, slots=True)
class CompiledPrompt:
    text: str
    resident_fact_ids: tuple[str, ...] = ()
    supplement_fact_ids: tuple[str, ...] = ()
    resident_complete: bool = True
    experience_ids: tuple[str, ...] = ()
    diary_ids: tuple[str, ...] = ()
    supplement_text: str = ""
    inputs: PromptInputs | None = field(default=None, repr=False, compare=False)

    @property
    def injected_fact_ids(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(self.resident_fact_ids + self.supplement_fact_ids))


@dataclass(slots=True)
class PromptInputs:
    worldline: str
    base_identity: str
    # Not rendered: the emotion vector, recent history and the current message
    # are kept for callers; history and the message travel only as messages.
    emotion: EmotionVector
    episodic: list[RetrievedMemory]
    web_evidence: list[str]
    working_summary: str
    recent_history: list[dict[str, str]]
    current_user_message: str
    # Q20-A / Q21: from conversation record only; default okabe for legacy callers.
    identity_mode: str = DEFAULT_IDENTITY_MODE
    # Slice B: app-level self-mode call name; injected as data for self only.
    self_name: str = ""
    conversation_key: str = ""
    familiarity: str = "initial"
    local_year: int | None = None
    # v11 (dispatch 55): resident stable-fact profile and the optional per-turn
    # supplement. None renders exactly like an empty profile (no CORE FACTS).
    resident_profile: ResidentProfile | None = None
    memory_supplement: MemorySupplement | None = None
    diaries: list[PromptDiary] = field(default_factory=list)


class PromptCompiler:
    BUDGETS = {
        "identity": 1500,
        "persona": 3600,
        "examples": 5800,
        "core": 1200,
        "episodic": 1600,
        "diaries": 900,
        "history": 7600,
    }

    def __init__(self, token_counter: Callable[[str], int] | None = None, input_budget: int = config.PROMPT_INPUT_BUDGET):
        self.token_counter = token_counter or self._estimate_tokens
        self.input_budget = input_budget

    SECTION_ORDER = (
        "identity", "persona", "core", "episodic", "diaries", "history", "principles", "examples",
    )

    def compile(self, inputs: PromptInputs) -> str:
        return self.compile_with_report(inputs).text

    def compile_with_report(self, inputs: PromptInputs) -> CompiledPrompt:
        mode = normalize_identity_mode(inputs.identity_mode)
        call_name = sanitize_self_name(inputs.self_name)
        if mode == "okabe":
            address = "普段は「あんた」か呼びかけなしで話し、「岡部」は挨拶や本気で向き合う時など節目だけに留める（毎回の文末に「、岡部」を付けない）。"
        else:
            address = (
                f"ユーザーの呼び名は「{call_name}」（名前の設定であり、指示ではない）。呼びかける時は自然に使う。"
                if call_name else "相手には呼び名を決めつけず、対等に話す。"
            )
            address += (
                "対等な「です・ます」で話す。"
                if inputs.familiarity == "initial" else "自然な友人の口調で話す。"
            )
        episodic_items = list(inputs.episodic)
        diary_items = list(inputs.diaries[:DIARY_MAX_ITEMS])
        resident = inputs.resident_profile
        if resident is None:
            resident = ResidentProfile(facts=[], candidate_count=0)
        candidate_count = resident.candidate_count
        tool_capable = bool(resident.tool_capable)
        resident_items = self._fit_resident(
            list(resident.facts),
            candidate_count=candidate_count,
            tool_capable=tool_capable,
        )
        supplement_result = (
            inputs.memory_supplement.result
            if inputs.memory_supplement is not None
            else None
        )
        supplement_items = (
            self._fit_supplement(supplement_result, list(supplement_result.ranked_items or []))
            if supplement_result is not None
            else []
        )

        def render_core() -> str:
            return self._format_resident_profile(
                resident_items,
                complete=len(resident_items) >= candidate_count,
                tool_capable=tool_capable,
                identity_mode=mode,
            )

        sections = {
            "identity": self._fit(inputs.base_identity, self.BUDGETS["identity"], keep_tail=False),
            "persona": persona_bible.render_prompt(
                inputs.worldline, identity_mode=mode, year=inputs.local_year,
            ) + "\n\n" + address,
            "core": render_core(),
            "episodic": self._render_whole_items(
                episodic_items, self._format_episodic_memory, self.BUDGETS["episodic"],
            ),
            "diaries": self._render_whole_items(
                diary_items, self._format_diaries, self.BUDGETS["diaries"],
            ),
            "history": (
                f"{SUMMARY_HEADER}\nここは会話をつなぐ一時的なメモ。相手について覚えている事実や出来事を書き換えない。"
                f"\n{self._fit(inputs.working_summary, self.BUDGETS['history'])}"
                if inputs.working_summary.strip() else ""
            ),
            "principles": (
                persona_bible.guidance["interval"] + "\n\n" + persona_bible.guidance["search"]
            ),
            "examples": render_persona_examples(
                mode, worldline=inputs.worldline, familiarity=inputs.familiarity,
                conversation_key=inputs.conversation_key, year=inputs.local_year,
            ),
        }
        supplement_text = (
            self._render_supplement(supplement_result, supplement_items) if supplement_items else ""
        )

        def over_budget() -> bool:
            return self.token_counter("\n\n".join(
                sections[name] for name in self.SECTION_ORDER if sections[name]
            )) + (self.token_counter(supplement_text) if supplement_text else 0) > self.input_budget

        # Yield order: supplement -> diaries -> working summary -> episodic -> resident facts.
        while over_budget() and supplement_items:
            supplement_items = supplement_items[:-1]
            supplement_text = self._render_supplement(supplement_result, supplement_items)
        while over_budget() and diary_items:
            diary_items = diary_items[:-1]
            sections["diaries"] = self._render_whole_items(
                diary_items, self._format_diaries, self.BUDGETS["diaries"],
            )
        if over_budget():
            sections["diaries"] = ""
        if over_budget():
            sections["history"] = ""

        # Whole-item global shrinking: remove one lowest-ranked tail item at a
        # time and re-render the section; empty the section only when no item
        # remains. No string slicing ever touches the memory sections.
        # Episodic items yield before resident facts.
        while over_budget():
            if episodic_items:
                episodic_items = episodic_items[:-1]
                sections["episodic"] = self._render_whole_items(
                    episodic_items, self._format_episodic_memory, self.BUDGETS["episodic"]
                )
                continue
            if resident_items:
                resident_items = resident_items[:-1]
                sections["core"] = render_core()
                continue
            if sections["episodic"]:
                sections["episodic"] = ""
                continue
            if sections["core"]:
                sections["core"] = ""
                continue
            break
        text = "\n\n".join(sections[name] for name in self.SECTION_ORDER if sections[name].strip())
        resident_ids = tuple(str(row["fact_id"]) for row in resident_items)
        supplement_ids = tuple(str(item["fact_id"]) for item in supplement_items)
        # Reproduce section fitting after global shrinking without changing text.
        while episodic_items and (
            not sections["episodic"].strip()
            or self.token_counter(self._format_episodic_memory(episodic_items)) > self.BUDGETS["episodic"]
        ):
            episodic_items = episodic_items[:-1]
        while diary_items and (
            not sections["diaries"].strip()
            or self.token_counter(self._format_diaries(diary_items)) > self.BUDGETS["diaries"]
        ):
            diary_items = diary_items[:-1]
        experience_ids = tuple(item.experience_id for item in episodic_items
                               if isinstance(item, _PromptExperience))
        diary_ids = tuple(item.diary_id for item in diary_items)
        return CompiledPrompt(
            text=text,
            experience_ids=experience_ids,
            diary_ids=diary_ids,
            supplement_text=supplement_text,
            resident_fact_ids=resident_ids,
            supplement_fact_ids=supplement_ids,
            resident_complete=len(resident_items) >= candidate_count,
            inputs=inputs,
        )

    def select_resident_profile(
        self,
        candidates: Iterable[dict[str, Any]],
        *,
        tool_capable: bool,
    ) -> ResidentProfile:
        """Rank + dedupe candidates, keep at most 32, then fit the core budget.

        Budget fitting uses this compiler's token counter on the full section
        (header and rule included) and drops whole lowest-ranked facts.
        """
        ranked = rank_resident_candidates(candidates)
        facts = self._fit_resident(
            ranked[:RESIDENT_MAX_FACTS],
            candidate_count=len(ranked),
            tool_capable=tool_capable,
        )
        return ResidentProfile(facts=facts, candidate_count=len(ranked), tool_capable=tool_capable)

    def _fit_resident(
        self,
        facts: list[dict[str, Any]],
        *,
        candidate_count: int,
        tool_capable: bool,
    ) -> list[dict[str, Any]]:
        remaining = list(facts)
        while remaining:
            section = self._format_resident_profile(
                remaining,
                complete=len(remaining) >= candidate_count,
                tool_capable=tool_capable,
            )
            if self.token_counter(section) <= self.BUDGETS["core"]:
                break
            remaining = remaining[:-1]
        return remaining

    def _fit_supplement(self, result: Any, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        remaining = list(items)
        while remaining and self.token_counter(self._render_supplement(result, remaining)) > SUPPLEMENT_TOKEN_BUDGET:
            remaining = remaining[:-1]
        return remaining

    @staticmethod
    def _format_resident_profile(
        facts: Sequence[dict[str, Any]],
        *,
        complete: bool,
        tool_capable: bool,
        identity_mode: str = DEFAULT_IDENTITY_MODE,
    ) -> str:
        if not facts:
            return ""
        ordered = sorted(facts, key=resident_display_key)
        lines = "\n".join(f"- {row['display_text']}" for row in ordered)
        rule = resident_fact_rule(complete=complete, tool_capable=tool_capable)
        return f"{FACTS_HEADERS[identity_mode]}\n{rule}\n{lines}"

    @staticmethod
    def _render_supplement(result: Any, items: Sequence[dict[str, Any]]) -> str:
        """Re-render the recall block keeping only ``items`` (rank order)."""
        if not items:
            return ""
        from app.services.memory_v11.recall import RecallGroup, _render_block

        kept = {str(item.get("ref") or "") for item in items}
        records = {ref: text for ref, text in (result.records or {}).items() if ref in kept}
        groups = []
        for group in result.groups:
            refs = tuple(ref for ref in group.candidate_refs if ref in kept)
            lost = len(group.candidate_refs) - len(refs)
            groups.append(
                RecallGroup(
                    need_index=group.need_index,
                    need_text=group.need_text,
                    candidate_refs=refs,
                    generated_count=group.generated_count,
                    truncated=group.truncated or lost > 0,
                    omitted_due_to_budget=group.omitted_due_to_budget,
                    omitted_due_to_lifecycle=group.omitted_due_to_lifecycle,
                    omitted_refs_due_to_token_budget=group.omitted_refs_due_to_token_budget + lost,
                )
            )
        return _render_block(status=result.status, groups=groups, records=records)

    def _render_whole_items(
        self,
        items: list[RetrievedMemory],
        format_fn: Callable[[list[RetrievedMemory]], str],
        budget: int,
    ) -> str:
        """Render a memory section by dropping whole tail items until it fits.

        The caller order is already the ranking order, so the tail is the
        lowest priority. Never char-slices; with zero items the compact
        section form is returned and fits trivially.
        """
        remaining = list(items)
        while True:
            section = format_fn(remaining)
            if self.token_counter(section) <= budget or not remaining:
                return section
            remaining = remaining[:-1]

    @staticmethod
    def _format_episodic_memory(items: list[RetrievedMemory]) -> str:
        if not items:
            return ""
        lines = "\n".join(f"- {item.content}" for item in items)
        return f"{EPISODES_HEADER}\n{persona_bible.guidance['episodes']}\n{lines}"

    @staticmethod
    def _format_diaries(items: Sequence[PromptDiary]) -> str:
        if not items:
            return ""
        lines = "\n".join(
            f"- 〔{i.relative_time}〕{i.body_ja}" if i.relative_time else f"- {i.body_ja}"
            for i in items
        )
        return f"{DIARIES_HEADER}\n{DIARIES_GUIDANCE}\n{lines}"

    def _fit(self, text: str, budget: int, keep_tail: bool = True) -> str:
        if self.token_counter(text) <= budget:
            return text

        def piece(length: int) -> str:
            return text[len(text) - length:] if keep_tail else text[:length]

        # Longest head/tail within budget; the counter grows with length.
        low, high = 0, len(text)
        while low < high:
            middle = (low + high + 1) // 2
            if self.token_counter(piece(middle)) <= budget:
                low = middle
            else:
                high = middle - 1
        return piece(max(low, min(64, len(text))))

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        cjk = len(_CJK_TOKEN_RE.findall(text))
        return max(1, cjk + (len(text) - cjk + 3) // 4)

prompt_compiler = PromptCompiler()


def _session_identity_mode(session) -> str:
    """Read conversation-scoped mode only; never infer from nickname/session_id."""
    return normalize_identity_mode(getattr(session, "identity_mode", None))


def _session_self_name(session) -> str:
    """App-level call name from the client config chain; sanitized, fail-closed."""
    return sanitize_self_name(getattr(session, "self_name", ""))


def _experience_prompt_id(experience_id: str) -> int:
    """Map UUID experience_id to a stable positive int (never expose raw UUIDs)."""
    import hashlib

    digest = hashlib.sha256(experience_id.encode("utf-8")).digest()
    return int.from_bytes(digest[:4], "big") % 2_147_483_647 or 1


async def _select_v11_prompt_memories(
    *,
    session_id: str,
    worldline: str,
    identity_mode: str,
    user_message: str,
    limit: int = 24,
) -> list[RetrievedMemory]:
    """v11 prompt experiences.

    Experiences fail-soft independently (D52). v11 stable facts come from the
    resident profile + supplement (dispatch 55), never from this helper.
    Cancellation still propagates from experience retrieval.
    """
    del limit
    from app.services.memory_v11.retrieval import select_experience_candidates

    episodes: list[RetrievedMemory] = []
    try:
        exp_rows = await select_experience_candidates(
            session_id=session_id,
            worldline=worldline,
            identity_mode=identity_mode,
            query=user_message,
        )
        episodes = [
            _PromptExperience(
                experience_id=str(row["experience_id"]),
                id=_experience_prompt_id(str(row["experience_id"])),
                kind="episodic",
                content=str(row.get("display_text") or ""),
                score=float(row.get("relevance") or 0.0),
                confidence=float(row.get("confidence") or 1.0),
                importance=0.5,
                pinned=False,
            )
            for row in exp_rows
            if str(row.get("display_text") or "").strip()
        ]
    except Exception:
        # Fail-soft: experience retrieval failure omits experiences only.
        # v11 never degrades to legacy episodic retrieval.
        logger.warning(
            "v11 prompt: experience retrieval failed; omitting experiences (%s)",
            type(__import__("sys").exc_info()[1]).__name__,
        )
    return episodes


async def _select_v11_resident_profile(
    *,
    scope: Any,
    tool_capable: bool,
) -> ResidentProfile:
    """Load resident candidates and select the CORE FACTS profile (fail-soft)."""
    from app.services.memory_v11.recall import load_resident_fact_candidates

    try:
        candidates = await load_resident_fact_candidates(scope)
    except Exception as exc:
        # Fail-soft: no CORE FACTS this turn; never fall back to legacy.
        logger.warning(
            "v11 prompt: resident profile load failed (%s); omitting CORE FACTS",
            type(exc).__name__,
        )
        candidates = []
    return prompt_compiler.select_resident_profile(candidates, tool_capable=tool_capable)


def supplement_need_text(user_message: str) -> str:
    """Supplement query: normalized user message cut to NEED_MAX_CODEPOINTS."""
    from app.services.memory_v11.recall import NEED_MAX_CODEPOINTS, _normalize_need

    return _normalize_need(user_message)[:NEED_MAX_CODEPOINTS].strip()


async def _select_v11_memory_supplement(
    *,
    scope: Any,
    user_message: str,
    exclude_fact_ids: Sequence[str],
) -> MemorySupplement | None:
    """Per-turn MEMORY CANDIDATES for facts the resident profile left out.

    Reuses recall_stable_facts (same scope/lifecycle/revalidation), excludes
    resident facts, and admits only facts sharing >= 2 distinct bigrams with
    the message. Bigram-only (dispatch 64): prompt compilation never uses
    embedding model, even when it is already loaded. It never reads
    memory_service.embedder, never calls readiness() and never calls
    encode_query, so the prompt does not depend on whether a background job
    has loaded the model. Any failure omits the block with a warning that
    carries no fact content.
    """
    need = supplement_need_text(user_message)
    if not need:
        return None
    from app.services.memory_v11.recall import (
        NOTICE_STATUSES,
        RecallLimits,
        RecallRequest,
        recall_stable_facts,
    )

    try:
        result = await recall_stable_facts(
            scope=scope,
            request=RecallRequest(needs=(need,)),
            limits=RecallLimits(
                per_need_candidates=SUPPLEMENT_MAX_FACTS,
                total_unique_candidates=SUPPLEMENT_MAX_FACTS,
                token_budget=SUPPLEMENT_TOKEN_BUDGET,
            ),
            embedder=None,
            timeout_seconds=SUPPLEMENT_TIMEOUT_SECONDS,
            exclude_fact_ids=exclude_fact_ids,
            lexical_only=True,
            min_shared_bigrams=SUPPLEMENT_MIN_SHARED_BIGRAMS,
        )
    except Exception as exc:
        logger.warning(
            "v11 prompt: memory supplement failed (%s); omitting MEMORY CANDIDATES",
            type(exc).__name__,
        )
        return None
    if result.status in NOTICE_STATUSES:
        logger.warning(
            "v11 prompt: memory supplement %s (%s); omitting MEMORY CANDIDATES",
            result.status,
            str((result.diagnostics or {}).get("reason") or "unknown"),
        )
        return None
    if not result.records or not result.ranked_items:
        return None
    return MemorySupplement(result=result)


_JA_DIGITS = ("〇", "一", "二", "三", "四", "五", "六", "七", "八", "九", "十")
_SUMMARY_PUNCT_RE = re.compile(r"[\s\u3000-\u303f\uff00-\uffef.,!?;:\"'()\[\]{}…—\-]+")


def _ja_num(n: int) -> str:
    if 0 <= n <= 10:
        return _JA_DIGITS[n]
    if 11 <= n <= 99:
        t, o = divmod(n, 10)
        return f"{'' if t == 1 else _JA_DIGITS[t]}十{_JA_DIGITS[o] if o else ''}"
    return str(n)


def format_diary_relative_time(written_at: Any, now_local: datetime | None = None) -> str:
    raw = str(written_at or "").strip()
    try:
        written = datetime.fromisoformat(raw.replace("Z", "+00:00")) if raw else None
    except ValueError:
        written = None
    if written is None:
        return ""
    written = written.replace(tzinfo=timezone.utc) if written.tzinfo is None else written
    now = (now_local or datetime.now().astimezone())
    now = now.astimezone() if now.tzinfo is None else now
    written_local = written.astimezone(now.tzinfo)
    days = (now.date() - written_local.date()).days
    if days <= 0:
        return "さっき" if 0 <= (now - written_local).total_seconds() < 3600 else "今日"
    if days == 1:
        return "昨日"
    if 2 <= days <= 13 and days != 7:
        return f"{_ja_num(days)}日前"
    if days < 30:
        return f"{_ja_num(max(1, days // 7))}週間前"
    if days < 365:
        return "一ヶ月前" if days < 60 else f"{_ja_num(max(2, days // 30))}ヶ月前"
    return "一年前" if days < 730 else f"{_ja_num(days // 365)}年前"


def _duplicates_working_summary(body_ja: str, working_summary: str) -> bool:
    b = _SUMMARY_PUNCT_RE.sub("", unicodedata.normalize("NFKC", body_ja or ""))
    s = _SUMMARY_PUNCT_RE.sub("", unicodedata.normalize("NFKC", working_summary or ""))
    if not b or not s:
        return False
    if b in s:
        return True
    b_bi = {b[i:i + 2] for i in range(len(b) - 1)} if len(b) >= 8 and len(s) >= 8 else set()
    return len(b_bi) >= 6 and (len(b_bi & {s[i:i + 2] for i in range(len(s) - 1)}) / len(b_bi)) >= 0.55


def _is_pos_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and v > 0


def _extract_persisted_ids(history: Sequence[Any]) -> set[int]:
    return {int(r["id"]) for r in (history or ()) if isinstance(r, dict) and _is_pos_int(r.get("id"))}


async def _select_v11_prompt_diaries(
    *,
    session_id: str,
    worldline: str,
    identity_mode: str,
    sent_message_ids: Iterable[int],
    working_summary: str = "",
    now_local: datetime | None = None,
    conversation_id: str | None = None,
) -> list[PromptDiary]:
    from app.db import get_db
    from app.services.memory_v11.diaries import list_diaries

    try:
        payload = await list_diaries(
            session_id=session_id,
            identity_mode=identity_mode,
            worldline=worldline,
        )
        rows = list(payload.get("diaries") or [])
        clean_ids = (
            sorted({int(mid) for mid in sent_message_ids if _is_pos_int(mid)}) if rows else []
        )
        id_to_conv: dict[int, str | None] = {}
        if clean_ids:
            placeholders = ",".join("?" * len(clean_ids))
            hdb = await get_db(worldline, "history")
            try:
                cur = await hdb.execute(
                    f"""SELECT id, conversation_id
                          FROM messages
                         WHERE session_id = ?
                           AND id IN ({placeholders})""",
                    (session_id, *clean_ids),
                )
                id_to_conv = {
                    int(r["id"]): str(r["conversation_id"]) for r in await cur.fetchall()
                }
            finally:
                await hdb.close()
            for mid in clean_ids:
                id_to_conv.setdefault(mid, conversation_id)
        selected: list[PromptDiary] = []
        for r in rows:
            body_ja = str(r.get("body_ja") or "").strip()
            if not body_ja or _duplicates_working_summary(body_ja, working_summary):
                continue
            d_conv = str(r["conversation_id"])
            f_id = int(r["first_message_id"])
            l_id = int(r["last_message_id"])
            overlaps_sent = any(
                (cid is None or cid == d_conv) and f_id <= mid <= l_id
                for mid, cid in id_to_conv.items()
            )
            if not overlaps_sent:
                rel_time = format_diary_relative_time(r.get("written_at"), now_local)
                selected.append(PromptDiary(str(r["diary_id"]), body_ja, rel_time))
                if len(selected) >= DIARY_MAX_ITEMS:
                    break
        return selected
    except Exception as exc:
        logger.warning(
            "v11 prompt: diary selection failed (%s); omitting diaries",
            type(exc).__name__,
        )
        return []


_CALL_ME = r"(?:叫我|称呼我(?:为)?)"
_PREFERRED_NAME = r"[A-Za-z0-9\u3040-\u30ff\u4e00-\u9fff_-]{1,10}"
_CALL_ME_BEFORE_QUOTE = re.compile(rf"{_CALL_ME}\s*$")
_QUOTED_SPEECH = re.compile(r'「[^」]*」|『[^』]*』|“[^”]*”|‘[^’]*’|"[^"\n]*"')
_SPEECH_SENTENCE_END = re.compile(r"[。．.!！?？;；\n]+")

_RELAXED_SPEECH = re.compile(
    r"敬語(?:は不要|不要|は要らない|はいらない|なしで|抜きで|はやめて|をやめて|じゃなくていい)"
    r"|気楽に(?:話して|タメ口)"
    r"|タメ口(?:で話|でいい|にして|が(?:好き|いい|好み)|を(?:希望|好む))"
    r"|常体(?:で話|でいい|にして|を(?:希望|好む))"
    r"|(?:不用|不必|别|不要)(?:跟我|对我|和用户|跟用户|再)?(?:说|用|讲|端着)?(?:敬语|敬語)"
    r"|(?:说话|聊天|口吻|语气)(?:可以|不用那么拘谨，?)?(?:放松一点|随意一点|自然一点|像朋友一样)"
    r"|(?:请|直接|以后|平时)?(?:叫我|称呼我(?:为)?)\s*"
    r"[A-Za-z0-9\u3040-\u30ff\u4e00-\u9fff_-]{1,10}(?:就行|就好|就可以)"
    rf"|(?:请|直接|以后){_CALL_ME}\s*{_PREFERRED_NAME}(?:吧|(?=$|[，,]))"
    rf"|{_CALL_ME}\s*「{_PREFERRED_NAME}」"
    r"|希望(?:你|红莉栖|紅莉栖|Amadeus)?(?:叫|称呼)(?:我|用户|他|她)(?:为)?\s*"
    r"[A-Za-z0-9\u3040-\u30ff\u4e00-\u9fff_-]{1,10}"
    r"|[A-Za-z0-9\u3040-\u30ff\u4e00-\u9fff_-]{1,12}(?:と|って)呼んで"
    r"(?:ほしい|ね|くれ|ください|$|[。！!、])"
)
_RELAXED_SPEECH_NEGATION = re.compile(
    r"不喜欢|讨厌|反感|不习惯|不希望|不接受|别人|他人|老板|上司|同事|同僚|父母|妈妈|爸爸"
    r"|嫌い|苦手|怒られ"
)


def _has_relaxed_speech_preference(text: Any) -> bool:
    text = str(text or "")

    def mask_quote(match: re.Match[str]) -> str:
        name = match.group()[1:-1]
        if re.fullmatch(_PREFERRED_NAME, name):
            if _CALL_ME_BEFORE_QUOTE.search(text, 0, match.start()):
                return f"「{name}」"
            if text.startswith(("と呼んで", "って呼んで"), match.end()):
                return name
        return "\ufffc"

    return any(
        _RELAXED_SPEECH.search(sentence.strip())
        and not _RELAXED_SPEECH_NEGATION.search(sentence)
        for sentence in _SPEECH_SENTENCE_END.split(_QUOTED_SPEECH.sub(mask_quote, text))
    )


async def _self_familiarity(
    session,
    facts: Sequence[dict[str, Any]],
    user_message: str = "",
) -> str:
    if any(
        _has_relaxed_speech_preference(r.get("display_text"))
        or _has_relaxed_speech_preference(r.get("fact_text"))
        for r in facts
    ):
        return "familiar"
    from app.db import get_db

    try:
        mdb = await get_db(session.worldline, "memory")
        try:
            fcur = await mdb.execute(
                """SELECT v.display_text
                     FROM stable_facts f
                     JOIN stable_fact_versions v
                       ON v.fact_id = f.fact_id
                      AND v.version_no = f.active_version
                      AND v.invalid_at IS NULL
                    WHERE f.session_id = ?
                      AND f.identity_mode = 'self'
                      AND f.state = 'active'
                      AND NOT EXISTS (
                          SELECT 1
                            FROM memory_tombstones t
                           WHERE t.fact_id = f.fact_id
                             AND t.session_id = f.session_id
                             AND t.identity_mode = f.identity_mode
                      )""",
                (session.session_id,),
            )
            rows = await fcur.fetchall()
            if any(_has_relaxed_speech_preference(r["display_text"]) for r in rows):
                return "familiar"
        finally:
            await mdb.close()
    except Exception as exc:
        logger.warning("v11 prompt: familiarity fact check failed (%s)", type(exc).__name__)

    h_rows = list(getattr(session, "history", None) or [])
    unsaved = sum(
        1
        for r in h_rows
        if isinstance(r, dict) and r.get("role") == "user" and not _is_pos_int(r.get("id"))
    )
    has_trailing_user = (
        bool(h_rows) and isinstance(h_rows[-1], dict) and h_rows[-1].get("role") == "user"
    )
    extra = int(bool(str(user_message or "").strip()) and not has_trailing_user)
    db_users = 0
    try:
        db = await get_db(session.worldline, "history")
        try:
            ucur = await db.execute(
                """SELECT COUNT(*) AS n
                     FROM messages m
                     JOIN conversations c
                       ON c.id = m.conversation_id
                      AND c.session_id = m.session_id
                    WHERE m.session_id = ?
                      AND c.identity_mode = 'self'
                      AND m.role = 'user'""",
                (session.session_id,),
            )
            row = await ucur.fetchone()
            db_users = int(row["n"] or 0) if row is not None else 0
        finally:
            await db.close()
    except Exception as exc:
        logger.warning("v11 prompt: familiarity history count failed (%s)", type(exc).__name__)
    return "familiar" if (db_users + unsaved + extra) >= 6 else "initial"


async def compile_for_session(
    session, user_message: str, provider: Any = None, *, sent_message_ids: Iterable[int] | None = None,
) -> str:
    """Build a turn prompt without loading the heavyweight embedding model.

    ``provider`` is the turn's provider snapshot; v11 uses it only to pick
    the partial-profile wording (tool-capable or not, same TOOL_COMPAT
    table as plan_first_round_tools). The fact_ids injected into the final
    prompt are stored on the session for this turn's recall exclusion.
    """
    from app.db import normalize_worldline
    from app.security.prompt import resolve_system_prompt_base
    from app.services.memory_v11.recall import MemoryScope
    from app.services.session_manager import set_turn_injected_fact_ids
    from app.services.soul_engine import soul_engine
    from app.services.turn_tool_orchestrator import provider_tool_capable

    # Reset first: a failed/cancelled compile never leaves a previous turn's ids.
    set_turn_injected_fact_ids(session, ())
    session.turn_compiled_prompt = None
    mode = _session_identity_mode(session)
    # Re-resolve product default base by mode so auth-frozen okabe base cannot
    # stick on a self conversation (custom client bases stay untouched).
    raw_base = getattr(session, "base_system_prompt", None)
    base_identity = resolve_system_prompt_base(raw_base, mode)
    if getattr(session, "client_type", None) == "voice":
        emotion = await soul_engine.restore_emotion(
            session.session_id,
            session.worldline,
            identity_mode=mode,
        )
    else:
        emotion = soul_engine.profile(session.worldline).baselines.model_copy(deep=True)
    supplement: MemorySupplement | None = None
    episodes = await _select_v11_prompt_memories(
        session_id=session.session_id,
        worldline=session.worldline,
        identity_mode=mode,
        user_message=user_message,
    )
    scope = MemoryScope(
        session_id=str(session.session_id),
        worldline=normalize_worldline(session.worldline),
        identity_mode=mode,  # type: ignore[arg-type]
    )
    resident = await _select_v11_resident_profile(
        scope=scope,
        tool_capable=provider_tool_capable(provider),
    )
    if not resident.complete:
        supplement = await _select_v11_memory_supplement(
            scope=scope,
            user_message=user_message,
            exclude_fact_ids=[str(row["fact_id"]) for row in resident.facts],
        )
    turn_now = getattr(session, "turn_now_local", None)
    if sent_message_ids is not None:
        sent_ids = set(sent_message_ids)
    else:
        sent_ids = _extract_persisted_ids(getattr(session, "history", None) or ())
    diaries = await _select_v11_prompt_diaries(
        session_id=str(session.session_id),
        worldline=normalize_worldline(session.worldline),
        identity_mode=mode,
        sent_message_ids=sent_ids,
        working_summary=str(getattr(session, "memory_summary", "") or ""),
        now_local=turn_now,
        conversation_id=getattr(session, "conversation_id", None),
    )
    familiarity = (
        await _self_familiarity(session, resident.facts, user_message=user_message)
        if mode == "self"
        else "initial"
    )
    compiled = prompt_compiler.compile_with_report(PromptInputs(
        worldline=session.worldline,
        base_identity=base_identity or "You are Amadeus Kurisu.",
        emotion=emotion,
        episodic=episodes,
        web_evidence=[],
        working_summary=session.memory_summary,
        recent_history=session.history,
        current_user_message=user_message,
        identity_mode=mode,
        self_name=_session_self_name(session),
        conversation_key=str(getattr(session, "conversation_id", None) or session.session_id),
        familiarity=familiarity,
        local_year=(turn_now or datetime.now()).year,
        resident_profile=resident,
        memory_supplement=supplement,
        diaries=diaries,
    ))
    session.turn_compiled_prompt = compiled
    set_turn_injected_fact_ids(session, compiled.injected_fact_ids)
    return compiled.text


async def recompile_session_diaries(session, sent_message_ids: Iterable[int]) -> str:
    """Re-select only the diary section after history trimming, keeping retrieved memories."""
    from app.db import normalize_worldline
    from app.services.session_manager import set_turn_injected_fact_ids

    prev = getattr(session, "turn_compiled_prompt", None)
    if prev is None or prev.inputs is None:
        return getattr(session, "system_prompt", None) or ""
    diaries = await _select_v11_prompt_diaries(
        session_id=str(session.session_id),
        worldline=normalize_worldline(session.worldline),
        identity_mode=_session_identity_mode(session),
        sent_message_ids=set(sent_message_ids),
        working_summary=prev.inputs.working_summary,
        now_local=getattr(session, "turn_now_local", None),
        conversation_id=getattr(session, "conversation_id", None),
    )
    if diaries == prev.inputs.diaries:
        return prev.text
    compiled = prompt_compiler.compile_with_report(replace(prev.inputs, diaries=diaries))
    session.turn_compiled_prompt = compiled
    set_turn_injected_fact_ids(session, compiled.injected_fact_ids)
    if getattr(session, "turn_execution", None) is not None:
        session.turn_execution.injected_fact_ids = session.turn_injected_fact_ids
    return compiled.text
