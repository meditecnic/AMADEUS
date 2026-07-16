"""Priority-aware prompt assembly for Amadeus persona, memory and evidence."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from app.services.memory import RetrievedMemory
from app.services.persona_bible import persona_bible
from app.services.soul_engine import EmotionVector, soul_engine


@dataclass(slots=True)
class PromptInputs:
    worldline: str
    base_identity: str
    emotion: EmotionVector
    core_facts: list[RetrievedMemory]
    episodic: list[RetrievedMemory]
    web_evidence: list[str]
    working_summary: str
    recent_history: list[dict[str, str]]
    current_user_message: str


class PromptCompiler:
    BUDGETS = {
        "identity": 1500,
        "persona": 1200,
        "state": 500,
        "core": 1200,
        "episodic": 1600,
        "web": 1600,
        "history": 3900,
        "tools": 500,
    }

    def __init__(self, token_counter: Callable[[str], int] | None = None, input_budget: int = 12000):
        self.token_counter = token_counter or self._estimate_tokens
        self.input_budget = input_budget

    def compile(self, inputs: PromptInputs) -> str:
        profile = soul_engine.profile(inputs.worldline)
        sections = [
            self._fit("IDENTITY AND SECURITY\n" + inputs.base_identity, self.BUDGETS["identity"], keep_tail=False),
            self._fit(persona_bible.render_prompt(profile.worldline), self.BUDGETS["persona"], keep_tail=False),
            self._fit(f"CURRENT STATE\nworldline={profile.worldline}; memory_anchor_year={persona_bible.memory_anchor.year}; memory_anchor_age={persona_bible.memory_anchor.age}; emotion={inputs.emotion.model_dump_json()}", self.BUDGETS["state"]),
            self._fit("CORE FACTS\n" + "\n".join(f"- {item.content}" for item in inputs.core_facts), self.BUDGETS["core"]),
            self._fit("EPISODIC MEMORY\n" + "\n".join(f"- {item.content}" for item in inputs.episodic), self.BUDGETS["episodic"]),
            self._fit("WEB EVIDENCE\n" + "\n".join(inputs.web_evidence), self.BUDGETS["web"]),
            self._fit("WORKING CONTEXT\n" + inputs.working_summary + "\nRECENT DIALOGUE\n" + "\n".join(f"{row['role']}: {row['content']}" for row in inputs.recent_history), self.BUDGETS["history"], keep_tail=True),
            "CURRENT USER MESSAGE (authoritative)\n" + inputs.current_user_message,
        ]
        # Drop low-priority context first. Identity/security and the current
        # message are never removed.
        for index in (6, 5, 4, 3):
            while self.token_counter("\n\n".join(sections)) > self.input_budget and sections[index]:
                if len(sections[index]) <= 120:
                    sections[index] = ""
                else:
                    sections[index] = sections[index][max(1, len(sections[index]) // 5):]
        for index in (2, 1):
            while self.token_counter("\n\n".join(sections)) > self.input_budget and len(sections[index]) > 120:
                sections[index] = sections[index][: max(120, len(sections[index]) * 4 // 5)]
        return "\n\n".join(section for section in sections if section.strip())

    def _fit(self, text: str, budget: int, keep_tail: bool = True) -> str:
        if self.token_counter(text) <= budget:
            return text
        chars = max(64, budget * 3)
        return text[-chars:] if keep_tail else text[:chars]

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        return max(1, (len(text) + 2) // 3)

prompt_compiler = PromptCompiler()


async def compile_for_session(session, user_message: str) -> str:
    """Build a turn prompt without loading the heavyweight embedding model."""
    from app.services.memory import memory_service
    from app.services.soul_engine import soul_engine
    emotion = await soul_engine.restore_emotion(session.session_id, session.worldline)
    facts = await memory_service.select_core_facts(session.session_id, session.worldline, user_message)
    episodes = await memory_service.lexical_search(session.session_id, session.worldline, user_message)
    return prompt_compiler.compile(PromptInputs(
        worldline=session.worldline,
        base_identity=session.base_system_prompt or session.system_prompt or "You are Amadeus Kurisu.",
        emotion=emotion,
        core_facts=facts,
        episodic=episodes,
        web_evidence=[],
        working_summary=session.memory_summary,
        recent_history=session.history,
        current_user_message=user_message,
    ))
