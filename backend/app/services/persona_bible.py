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

    def render_prompt(
        self,
        worldline: str,
        identity_mode: str = DEFAULT_IDENTITY_MODE,
    ) -> str:
        """Compose shared + mode + worldline rules.

        Phase-2 S2: mode_rule_ids may be empty for both modes (behavior-neutral).
        Default identity_mode=okabe keeps legacy single-arg callers valid until S3.
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
