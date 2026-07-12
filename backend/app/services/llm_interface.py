from abc import ABC, abstractmethod
from typing import AsyncGenerator, Optional


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
    ) -> AsyncGenerator[dict, None]:
        """Yield chunks from the LLM chat completion stream."""
        ...

    @abstractmethod
    async def compress_history(
        self,
        history: list,
        api_key: Optional[str] = None,
        old_summary: str = "",
    ) -> str:
        """Compress conversation history into a single summary sentence."""
        ...

    @abstractmethod
    async def translate_to_zh(
        self,
        text: str,
        api_key: Optional[str] = None,
    ) -> str:
        """Translate text (typically Japanese) to Chinese."""
        ...
