"""Signed first-person persona and conversation settings."""

from __future__ import annotations

import re
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app import config
from app.db import DEFAULT_IDENTITY_MODE, normalize_identity_mode, normalize_worldline


class MemoryAnchor(BaseModel):
    year: int
    month: int
    age: int


def _year_number_ja(value: int) -> str:
    if value == 0:
        return "零"
    if value >= 100:
        return str(value)
    digits = "零一二三四五六七八九"
    tens, ones = divmod(value, 10)
    return (("" if tens == 1 else digits[tens]) + "十" if tens else "") + (
        digits[ones] if ones else ""
    )


class PersonaBible(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int
    persona_id: str
    memory_anchor: MemoryAnchor
    biography: list[str] = Field(min_length=6, max_length=6)
    situations: dict[str, dict[str, str]]
    guidance: dict[str, str]
    emotion_tones: dict[str, str]

    @classmethod
    def load_default(cls) -> "PersonaBible":
        path = config.CHARACTERS_DIR / "persona_bible_v1.json"
        return cls.model_validate_json(path.read_text(encoding="utf-8"))

    def romance_allowed(self, worldline: str, identity_mode: str = DEFAULT_IDENTITY_MODE) -> bool:
        return normalize_worldline(worldline) == "beta" and normalize_identity_mode(identity_mode) == "okabe"

    def render_elapsed_years(self, text: str, year: int) -> str:
        elapsed = max(0, year - self.memory_anchor.year)
        replacements = {
            "2026年": f"{year}年",
            "16年": f"{elapsed}年",
            "十六年": f"{_year_number_ja(elapsed)}年",
        }
        return re.sub(r"(?<!\d)(?:2026|16)年|十六年", lambda match: replacements[match[0]], text)

    def render_prompt(
        self,
        worldline: str,
        identity_mode: str = DEFAULT_IDENTITY_MODE,
        *,
        year: int | None = None,
    ) -> str:
        mode = normalize_identity_mode(identity_mode)
        year = datetime.now().year if year is None else year
        biography = list(self.biography)
        biography[3] = self.render_elapsed_years(biography[3], year)
        # The signed situation already contains its My Fork reaction.
        situation = self.situations[normalize_worldline(worldline)][mode]
        return "\n\n".join((
            "【わたしについて】\n" + "\n\n".join(biography),
            "【今の私と話し相手】\n" + self.render_elapsed_years(situation, year),
        ))

    def render_provider_anchor(self, identity_mode: str = DEFAULT_IDENTITY_MODE) -> str:
        return self.render_output_anchor()

    def render_output_anchor(self) -> str:
        allowed = "|".join(self.emotion_tones)
        return f"[EMO:{allowed}] のいずれか一つを返答先頭に置き、自然な日本語本文を続ける。"


persona_bible = PersonaBible.load_default()
