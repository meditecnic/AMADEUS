from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import AsyncGenerator, Optional

from app.db import DEFAULT_IDENTITY_MODE


def build_stream_messages(
    message_builder: Callable[..., list[dict]],
    active_history: list,
    memory_summary: Optional[str],
    system_prompt: Optional[str],
    *,
    isolated: bool,
) -> list[dict]:
    """Build either the normal Persona envelope or an isolated request.

    Isolated calls are presentation-layer transforms. They must not inherit
    few-shot examples, Persona anchors, memory, or any other chat envelope
    supplied by ``message_builder``.
    """

    if not isolated:
        return message_builder(active_history, memory_summary, system_prompt)
    messages: list[dict] = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.extend(dict(message) for message in active_history)
    return messages


class LLMInterface(ABC):
    """Abstract base class for LLM service providers."""

    @abstractmethod
    async def get_chat_stream(
        self,
        active_history: list,
        memory_summary: Optional[str] = None,
        api_key: Optional[str] = None,
        system_prompt: Optional[str] = None,
        temperature: float = 0.7,
        tools: Optional[list] = None,
        tool_choice: Optional[str] = None,
        model: Optional[str] = None,
        reasoning_effort: Optional[str] = None,
        isolated: bool = False,
        max_output_tokens: Optional[int] = None,
    ) -> AsyncGenerator[dict, None]:
        """Yield chunks from the LLM chat completion stream."""
        ...

    @abstractmethod
    async def compress_history(
        self,
        history: list,
        api_key: Optional[str] = None,
        old_summary: str = "",
        model: Optional[str] = None,
        identity_mode: str = DEFAULT_IDENTITY_MODE,
    ) -> str:
        """Compress conversation history into a working-summary line (D38)."""
        ...

    @abstractmethod
    async def translate_to_zh(
        self,
        text: str,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
    ) -> str:
        """Translate text (typically Japanese) to Chinese."""
        ...

    @abstractmethod
    async def complete_json(
        self,
        messages: list[dict],
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        temperature: float = 0.1,
    ) -> dict:
        """Return one validated JSON object from a non-streaming completion."""
        ...

    @abstractmethod
    async def list_models(
        self,
        api_key: Optional[str] = None,
    ) -> list[str | dict]:
        """Return provider model identifiers and any safe discovery metadata."""
        ...
