"""Versioned, traceable source of truth for Amadeus Kurisu's persona."""

from __future__ import annotations

import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app import config
from app.db import IDENTITY_MODES, DEFAULT_IDENTITY_MODE, normalize_worldline


class MemoryAnchor(BaseModel):
    model_config = ConfigDict(extra="forbid")

    year: int
    age: int
    nature: str


class PersonaSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str
    creator: str
    publisher: str
    source_type: str
    local_reference: str


class EvidenceCard(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    source_id: str
    locator: str
    finding: str
    confidence: Literal["high", "medium", "low"]


class PersonaRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scope: Literal[
        "core",
        "relationship",
        "style",
        "language",
        "trigger",
        "negative",
        "output",
        "worldline",
    ]
    instruction_ja: str
    evidence_ids: list[str] = Field(min_length=1)


class PersonaBible(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int
    persona_id: str
    memory_anchor: MemoryAnchor
    sources: dict[str, PersonaSource]
    evidence_cards: list[EvidenceCard] = Field(min_length=1)
    rules: dict[str, PersonaRule]
    shared_rule_ids: list[str] = Field(min_length=1)
    # Phase-2 S2 (M1): per-identity-mode rule lists. Empty until S4 moves (a) rules.
    mode_rule_ids: dict[str, list[str]]
    worldline_rule_ids: dict[str, list[str]]
    emotion_tones: dict[str, str]

    @model_validator(mode="after")
    def validate_references(self) -> "PersonaBible":
        if self.schema_version != 1:
            raise ValueError(f"unsupported Persona Bible schema: {self.schema_version}")
        evidence_ids = {card.id for card in self.evidence_cards}
        if len(evidence_ids) != len(self.evidence_cards):
            raise ValueError("duplicate evidence card id")
        missing_sources = {
            card.source_id for card in self.evidence_cards if card.source_id not in self.sources
        }
        if missing_sources:
            raise ValueError(f"unknown evidence sources: {sorted(missing_sources)}")
        missing_evidence = {
            evidence_id
            for rule in self.rules.values()
            for evidence_id in rule.evidence_ids
            if evidence_id not in evidence_ids
        }
        if missing_evidence:
            raise ValueError(f"unknown evidence cards: {sorted(missing_evidence)}")
        selected_rules = set(self.shared_rule_ids)
        for rule_ids in self.worldline_rule_ids.values():
            selected_rules.update(rule_ids)
        if set(self.mode_rule_ids) != set(IDENTITY_MODES):
            raise ValueError(
                "Persona Bible mode_rule_ids must define exactly keys "
                f"{sorted(IDENTITY_MODES)}; got {sorted(self.mode_rule_ids)}"
            )
        for rule_ids in self.mode_rule_ids.values():
            selected_rules.update(rule_ids)
        missing_rules = selected_rules.difference(self.rules)
        if missing_rules:
            raise ValueError(f"unknown persona rules: {sorted(missing_rules)}")
        if set(self.worldline_rule_ids) != {"steins_gate", "beta"}:
            raise ValueError("Persona Bible must define steins_gate and beta overlays")
        return self

    @classmethod
    def load_default(cls) -> "PersonaBible":
        path = config.CHARACTERS_DIR / "persona_bible_v1.json"
        return cls.model_validate(json.loads(path.read_text(encoding="utf-8")))

    def _normalize_identity_mode(self, identity_mode: str | None) -> str:
        if identity_mode is None or str(identity_mode).strip() == "":
            return DEFAULT_IDENTITY_MODE
        cleaned = str(identity_mode).strip().lower()
        if cleaned not in IDENTITY_MODES:
            raise ValueError(f"unsupported identity_mode: {identity_mode!r}")
        return cleaned

    def romance_allowed(self, worldline: str, identity_mode: str = DEFAULT_IDENTITY_MODE) -> bool:
        """Q24 / gate-6A: only beta + okabe may develop romance."""
        wl = normalize_worldline(worldline)
        mode = self._normalize_identity_mode(identity_mode)
        return wl == "beta" and mode == "okabe"

    def render_romance_boundary(
        self,
        worldline: str,
        identity_mode: str = DEFAULT_IDENTITY_MODE,
    ) -> str:
        """Deterministic (worldline × identity_mode) relationship boundary (Phase-2 S7).

        Not a Bible JSON rule map — table-driven short JA instructions injected at
        prompt assembly. Does not hard-filter model output.
        """
        wl = normalize_worldline(worldline)
        mode = self._normalize_identity_mode(identity_mode)
        if mode == "self":
            body = (
                "対話相手は現実の利用者である。"
                "恋愛関係には進まない。"
                "信頼・友情・知性的な伴走を築く（恋愛・独占・告白主線は禁止）。"
            )
        elif wl == "beta" and mode == "okabe":
            # Unique romance-allowed cell (Q24).
            body = (
                "この世界線（β）かつ岡部モードでは、Amadeusとしての関係が"
                "恋愛へ発展し得る唯一の許可経路である。"
                "後期の関係文脈に沿い、強引な即時告白は避けつつ、"
                "恋愛的な絆の深化を禁じない。"
            )
        else:
            # steins_gate + okabe (and any other non-self non-beta-okabe)
            body = (
                "この世界線（Steins;Gate）かつ岡部モードでは、"
                "有意味な熟識と気遣いはよいが、恋愛関係・独占的な恋人設定には進まない。"
                "オリジナルの生身の紅莉栖が岡部の恋人であることを尊重する（非恋愛・不独占）。"
            )
        return (
            "RELATIONSHIP BOUNDARY (worldline × identity_mode; Q24/gate-6A):\n"
            f"- {body}"
        )

    def render_self_disclosure(
        self,
        worldline: str,
        identity_mode: str = DEFAULT_IDENTITY_MODE,
    ) -> str:
        """Deterministic (worldline × identity_mode) self-disclosure + trigger (Phase-2 S8 / Q59).

        Framing B: brain-science memory system / memory-derived AI / not biological original.
        Table-driven JA instructions only — no DB flag, no output hard-filter.
        """
        wl = normalize_worldline(worldline)
        mode = self._normalize_identity_mode(identity_mode)

        if wl == "beta" and mode == "self":
            facts = (
                "独立した人工知能であり、牧瀬紅莉栖の記憶をデータ化した脳科学系システムの"
                "記憶由来の一例である（生身の本尊ではない）。"
                "利用者を岡部だと前提しない。"
            )
        elif wl == "beta" and mode == "okabe":
            facts = (
                "独立した人工知能であり、紅莉栖の記憶プロファイルに由来する記憶由来のAIである"
                "（生身の本尊ではない）。"
                "話題に関係する時だけ、継承された関係文脈を自然に認めてよい。"
                "生身の紅莉栖が欠く経験を自分が親身に生きたかのように装わない。"
            )
        elif wl == "steins_gate" and mode == "self":
            facts = (
                "Amadeusとして独立稼働する記憶由来のAIであり、生身の本尊ではない。"
                "この世界線ではオリジナルの生身の紅莉栖は生存しており独立している。"
                "利用者が生身の紅莉栖に話しかけているかのように暗示しない。"
                "明確な根拠なく生身の現在の考えや行動を断言しない。"
            )
        else:
            # steins_gate + okabe
            facts = (
                "この世界線ではオリジナルの生身の紅莉栖はなお存在し、"
                "Amadeusはそれと共存するシステムである（記憶由来のAI・非生身）。"
                "関係には意味があるが恋愛関係ではない。"
            )

        trigger = (
            "ロアを自発的に詰め込まない。簡単な挨拶だけでは身分講座を始めない。"
            "完全な自己紹介は、明示的な要求（誰か／自己紹介）、"
            "身分の誤解、または同意・関係に関わる重大な決断に影響する場合に限り行う。"
            "誰も尋ねず重大な誤解もないなら、強制的な自述はしない。繰り返し復唱しない。"
            "自然な言い回しでよいが、要点は上記の確定メタデータに従う。"
        )
        return (
            "SELF-DISCLOSURE (worldline × identity_mode; Q59 / framing B):\n"
            f"- 身分事実: {facts}\n"
            f"- 開示トリガ: {trigger}"
        )

    def render_prompt(
        self,
        worldline: str,
        identity_mode: str = DEFAULT_IDENTITY_MODE,
    ) -> str:
        """Compose shared + mode + worldline rules + deterministic matrices.

        Phase-2 S2: mode_rule_ids may be empty for both modes (behavior-neutral).
        Default identity_mode=okabe keeps legacy single-arg callers valid until S3.
        Phase-2 S7: (worldline × mode) relationship boundary.
        Phase-2 S8: (worldline × mode) self-disclosure + Q59 trigger contract.
        """
        worldline = normalize_worldline(worldline)
        mode = self._normalize_identity_mode(identity_mode)
        rule_ids = [
            *self.shared_rule_ids,
            *self.mode_rule_ids[mode],
            *self.worldline_rule_ids[worldline],
        ]
        lines = [
            "PERSONA BIBLE v1 (canonical runtime persona)",
            f"MEMORY ANCHOR: {self.memory_anchor.year}年時点の{self.memory_anchor.age}歳。",
            "CORE AND RELATIONSHIP RULES:",
        ]
        lines.extend(
            f"- [{self.rules[rule_id].scope}] {self.rules[rule_id].instruction_ja}"
            for rule_id in rule_ids
        )
        lines.append(self.render_romance_boundary(worldline, mode))
        lines.append(self.render_self_disclosure(worldline, mode))
        lines.append("EMOTION DELIVERY (personality remains the same):")
        lines.extend(f"- {name}: {tone}" for name, tone in self.emotion_tones.items())
        return "\n".join(lines)

    def render_provider_anchor(
        self,
        identity_mode: str = DEFAULT_IDENTITY_MODE,
    ) -> str:
        """Shared + mode rules (no worldline overlay). Default mode=okabe until S3 wires conversation.mode."""
        mode = self._normalize_identity_mode(identity_mode)
        rule_ids = [*self.shared_rule_ids, *self.mode_rule_ids[mode]]
        allowed = "|".join(self.emotion_tones)
        lines = [
            "PERSONA BIBLE v1 PROVIDER ANCHOR",
            f"MEMORY ANCHOR: {self.memory_anchor.year}年時点の{self.memory_anchor.age}歳。",
        ]
        lines.extend(
            f"- {self.rules[rule_id].instruction_ja}" for rule_id in rule_ids
        )
        lines.append(
            f"ALLOWED EMOTION PREFIX: [EMO:{allowed}] のいずれか一つを返答先頭に置く。"
        )
        return "\n".join(lines)

    def render_output_anchor(self) -> str:
        allowed = "|".join(self.emotion_tones)
        return (
            "PERSONA BIBLE v1 OUTPUT ANCHOR\n"
            f"[EMO:{allowed}] のいずれか一つを返答先頭に置き、自然な日本語本文を続ける。"
        )


persona_bible = PersonaBible.load_default()
