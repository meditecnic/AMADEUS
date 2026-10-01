from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

from app.db import Worldline, normalize_worldline


@dataclass(frozen=True, slots=True)
class TurnContext:
    session_id: str
    worldline: Worldline
    revision: int
    turn_id: str

    @classmethod
    def create(cls, session_id: str, worldline: str, revision: int) -> "TurnContext":
        return cls(session_id=session_id, worldline=normalize_worldline(worldline), revision=revision, turn_id=str(uuid4()))

    @property
    def task_key(self) -> str:
        return f"{self.session_id}:{self.worldline}:{self.revision}:{self.turn_id}"
