"""Versioned personality loading and persistent emotion-state reduction."""

from __future__ import annotations

import json
import logging
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app import config
from app.db import DEFAULT_IDENTITY_MODE, get_db, normalize_identity_mode, normalize_worldline

logger = logging.getLogger(__name__)
EMOTION_KEYS = ("trust", "attachment", "irritation", "anxiety", "jealousy", "volatility")


class EmotionVector(BaseModel):
    model_config = ConfigDict(extra="forbid")
    trust: float = Field(ge=0, le=1)
    attachment: float = Field(ge=0, le=1)
    irritation: float = Field(ge=0, le=1)
    anxiety: float = Field(ge=0, le=1)
    jealousy: float = Field(ge=0, le=1)
    volatility: float = Field(ge=0, le=1)


class HalfLives(BaseModel):
    model_config = ConfigDict(extra="forbid")
    trust: float = Field(gt=0)
    attachment: float = Field(gt=0)
    irritation: float = Field(gt=0)
    anxiety: float = Field(gt=0)
    jealousy: float = Field(gt=0)
    volatility: float = Field(gt=0)


class CharacterProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: int
    worldline: str
    display_name: str
    memory_anchor_year: int
    speech_emotion_scale: float = Field(gt=0, le=2)
    max_speech_delta_per_turn: float = Field(gt=0, le=1)
    baselines: EmotionVector
    half_lives_seconds: HalfLives
    style_rules: list[str]

    @field_validator("schema_version")
    @classmethod
    def supported_schema(cls, value: int) -> int:
        if value != 1:
            raise ValueError(f"unsupported character schema version: {value}")
        return value


class SpeechEmotionMapper:
    MAPPING: ClassVar[dict[str, dict[str, float]]] = {
        "HAPPY": {"attachment": 0.06, "trust": 0.04, "anxiety": -0.05},
        "SAD": {"anxiety": 0.08, "attachment": 0.03, "volatility": 0.04},
        "ANGRY": {"irritation": 0.12, "trust": -0.05, "volatility": 0.06},
        "NEUTRAL": {"volatility": -0.02},
        "FEARFUL": {"anxiety": 0.13, "volatility": 0.06},
        "DISGUSTED": {"irritation": 0.09, "attachment": -0.03},
        "SURPRISED": {"volatility": 0.08, "anxiety": 0.03},
    }

    @classmethod
    def map(cls, label: str, confidence: float, profile: CharacterProfile) -> dict[str, float]:
        candidate = {
            key: delta * max(0.0, min(1.0, confidence)) * profile.speech_emotion_scale
            for key, delta in cls.MAPPING.get(label.upper(), {}).items()
        }
        for relationship_key in ("trust", "attachment"):
            if relationship_key in candidate:
                candidate[relationship_key] = max(-0.03, min(0.03, candidate[relationship_key]))
        magnitude = sum(abs(value) for value in candidate.values())
        if magnitude > profile.max_speech_delta_per_turn:
            scale = profile.max_speech_delta_per_turn / magnitude
            candidate = {key: value * scale for key, value in candidate.items()}
        return candidate


class SoulEngine:
    def __init__(self, characters_dir: Path | None = None):
        self.characters_dir = characters_dir or config.CHARACTERS_DIR
        self._profiles: dict[str, CharacterProfile] = {}

    def load_profiles(self) -> None:
        profiles = {}
        for worldline, filename in (("steins_gate", "amadeus_sg.json"), ("beta", "amadeus_beta.json")):
            payload = json.loads((self.characters_dir / filename).read_text(encoding="utf-8"))
            profile = CharacterProfile.model_validate(payload)
            if normalize_worldline(profile.worldline) != worldline:
                raise ValueError(f"{filename} declares the wrong worldline")
            profiles[worldline] = profile
        self._profiles = profiles

    def profile(self, worldline: str) -> CharacterProfile:
        if not self._profiles:
            self.load_profiles()
        return self._profiles[normalize_worldline(worldline)]

    async def restore_emotion(
        self,
        session_id: str,
        worldline: str,
        now: datetime | None = None,
        identity_mode: str = DEFAULT_IDENTITY_MODE,
    ) -> EmotionVector:
        """Load/decay emotion for (session, worldline, identity_mode).

        Q25: modes are isolated; missing row → profile baseline (self starts blank).
        Default identity_mode=okabe keeps legacy callers on the okabe lane (U-M2).
        """
        wl = normalize_worldline(worldline)
        mode = normalize_identity_mode(identity_mode)
        profile = self.profile(wl)
        now = now or datetime.now(timezone.utc)
        db = await get_db(wl, "memory")
        try:
            cur = await db.execute(
                "SELECT * FROM emotion_states WHERE session_id=? AND identity_mode=?",
                (session_id, mode),
            )
            row = await cur.fetchone()
            if not row:
                state = profile.baselines.model_copy(deep=True)
            else:
                state = EmotionVector.model_validate({key: row[key] for key in EMOTION_KEYS})
                updated = datetime.fromisoformat(row["updated_at_utc"].replace("Z", "+00:00"))
                delta_seconds = (now - updated).total_seconds()
                if delta_seconds < 0:
                    logger.warning("system clock moved backwards; emotion decay skipped")
                    delta_seconds = 0
                state = self._decay(state, profile, delta_seconds)
            await self._persist(db, session_id, mode, state, now)
            await db.commit()
            return state
        finally:
            await db.close()

    async def apply_deltas(
        self,
        session_id: str,
        worldline: str,
        deltas: dict[str, float],
        now: datetime | None = None,
        identity_mode: str = DEFAULT_IDENTITY_MODE,
    ) -> EmotionVector:
        now = now or datetime.now(timezone.utc)
        mode = normalize_identity_mode(identity_mode)
        state = await self.restore_emotion(
            session_id, worldline, now, identity_mode=mode
        )
        payload = state.model_dump()
        for key, delta in deltas.items():
            if key in payload:
                payload[key] = max(0.0, min(1.0, payload[key] + float(delta)))
        updated_state = EmotionVector.model_validate(payload)
        db = await get_db(normalize_worldline(worldline), "memory")
        try:
            await self._persist(db, session_id, mode, updated_state, now)
            await db.commit()
        finally:
            await db.close()
        return updated_state

    async def apply_speech_emotion(
        self,
        session_id: str,
        worldline: str,
        label: str,
        confidence: float,
        identity_mode: str = DEFAULT_IDENTITY_MODE,
    ) -> EmotionVector:
        profile = self.profile(worldline)
        return await self.apply_deltas(
            session_id,
            worldline,
            SpeechEmotionMapper.map(label, confidence, profile),
            identity_mode=identity_mode,
        )

    @staticmethod
    def _decay(state: EmotionVector, profile: CharacterProfile, delta_seconds: float) -> EmotionVector:
        current = state.model_dump()
        baseline = profile.baselines.model_dump()
        half_lives = profile.half_lives_seconds.model_dump()
        return EmotionVector.model_validate({
            key: baseline[key] + (current[key] - baseline[key]) * math.pow(2.0, -delta_seconds / half_lives[key])
            for key in EMOTION_KEYS
        })

    @staticmethod
    async def _persist(
        db,
        session_id: str,
        identity_mode: str,
        state: EmotionVector,
        now: datetime,
    ) -> None:
        mode = normalize_identity_mode(identity_mode)
        values = state.model_dump()
        await db.execute(
            """INSERT INTO emotion_states(
                   session_id,identity_mode,trust,attachment,irritation,anxiety,jealousy,volatility,updated_at_utc
               )
               VALUES(?,?,?,?,?,?,?,?,?)
               ON CONFLICT(session_id, identity_mode) DO UPDATE SET
               trust=excluded.trust,attachment=excluded.attachment,irritation=excluded.irritation,
               anxiety=excluded.anxiety,jealousy=excluded.jealousy,volatility=excluded.volatility,
               updated_at_utc=excluded.updated_at_utc""",
            (
                session_id,
                mode,
                *(values[key] for key in EMOTION_KEYS),
                now.astimezone(timezone.utc).isoformat(),
            ),
        )

    def readiness(self) -> dict[str, object]:
        try:
            self.load_profiles()
            return {"ok": True, "schema_version": 1, "profiles": sorted(self._profiles)}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}


soul_engine = SoulEngine()
