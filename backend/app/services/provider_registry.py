"""Provider definitions, capability filtering, and immutable per-turn snapshots."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Awaitable, Callable, Protocol


class ProviderTask(StrEnum):
    CHAT = "chat"
    TRANSLATION = "translation"
    MEMORY = "memory"
    COMPRESSION = "compression"
    TITLE = "title"
    MODELS = "models"


class ProviderAdapter(Protocol):
    async def complete_json(self, **kwargs: Any) -> dict[str, Any]: ...


@dataclass(frozen=True, slots=True)
class ProviderCapabilities:
    tasks: frozenset[ProviderTask]
    parameters: frozenset[str] = frozenset()
    structured_output: bool = False
    tools: bool = False
    model_discovery: bool = False


@dataclass(frozen=True, slots=True)
class ProviderDefinition:
    id: str
    display_name: str
    default_model: str
    capabilities: ProviderCapabilities
    base_url_configurable: bool = False
    credential_required: bool = True


@dataclass(frozen=True, slots=True)
class ParameterFilterResult:
    accepted: dict[str, Any]
    unsupported: dict[str, Any]

    @property
    def diagnostic(self) -> str:
        if not self.unsupported:
            return ""
        names = ", ".join(sorted(self.unsupported))
        return f"provider does not support parameters: {names}"


@dataclass(frozen=True, slots=True)
class ProviderSnapshot:
    provider_id: str
    model_id: str
    capabilities: ProviderCapabilities
    adapter: ProviderAdapter
    credential_required: bool = True
    base_url_configurable: bool = False

    def require(self, task: ProviderTask) -> "ProviderSnapshot":
        if task not in self.capabilities.tasks:
            raise ValueError(f"provider {self.provider_id!r} does not support {task.value}")
        return self

    def filter_parameters(self, values: dict[str, Any]) -> ParameterFilterResult:
        accepted = {
            name: value
            for name, value in values.items()
            if name in self.capabilities.parameters
        }
        unsupported = {
            name: value
            for name, value in values.items()
            if name not in self.capabilities.parameters and value is not None
        }
        return ParameterFilterResult(accepted=accepted, unsupported=unsupported)


@dataclass(frozen=True, slots=True)
class ModelDiscoveryResult:
    models: tuple[str, ...]
    cache_hit: bool


@dataclass(frozen=True, slots=True)
class _ModelCacheEntry:
    models: tuple[str, ...]
    expires_at: float


class ProviderModelCache:
    """Concurrency-safe provider model cache with a 24-hour default TTL."""

    def __init__(
        self,
        *,
        ttl_seconds: float = 24 * 60 * 60,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError("model cache TTL must be positive")
        self._ttl_seconds = ttl_seconds
        self._clock = clock
        self._entries: dict[str, _ModelCacheEntry] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    async def get(
        self,
        provider_id: str,
        loader: Callable[[], Awaitable[list[str]]],
        *,
        refresh: bool = False,
    ) -> ModelDiscoveryResult:
        entry = self._entries.get(provider_id)
        now = self._clock()
        if not refresh and entry is not None and entry.expires_at > now:
            return ModelDiscoveryResult(entry.models, cache_hit=True)

        lock = self._locks.setdefault(provider_id, asyncio.Lock())
        async with lock:
            entry = self._entries.get(provider_id)
            now = self._clock()
            if not refresh and entry is not None and entry.expires_at > now:
                return ModelDiscoveryResult(entry.models, cache_hit=True)

            discovered = await loader()
            models = tuple(
                sorted(
                    {
                        model.strip()
                        for model in discovered
                        if isinstance(model, str) and model.strip()
                    }
                )
            )
            if not models:
                raise ValueError("provider returned no usable models")
            self._entries[provider_id] = _ModelCacheEntry(
                models=models,
                expires_at=now + self._ttl_seconds,
            )
            return ModelDiscoveryResult(models, cache_hit=False)

    def clear(self, provider_id: str | None = None) -> None:
        if provider_id is None:
            self._entries.clear()
            self._locks.clear()
            return
        self._entries.pop(provider_id, None)
        self._locks.pop(provider_id, None)


class ProviderRegistry:
    def __init__(self) -> None:
        self._entries: dict[str, tuple[ProviderDefinition, ProviderAdapter]] = {}

    def register(self, definition: ProviderDefinition, adapter: ProviderAdapter) -> None:
        if definition.id in self._entries:
            raise ValueError(f"provider {definition.id!r} already registered")
        self._entries[definition.id] = (definition, adapter)

    def list_definitions(self) -> list[ProviderDefinition]:
        return [entry[0] for entry in self._entries.values()]

    def replace(self, definition: ProviderDefinition, adapter: ProviderAdapter) -> None:
        if definition.id not in self._entries:
            raise KeyError(f"unknown provider: {definition.id}")
        self._entries[definition.id] = (definition, adapter)

    def snapshot(self, provider_id: str, model_id: str | None = None) -> ProviderSnapshot:
        try:
            definition, adapter = self._entries[provider_id]
        except KeyError as exc:
            raise KeyError(f"unknown provider: {provider_id}") from exc
        return ProviderSnapshot(
            provider_id=definition.id,
            model_id=model_id or definition.default_model,
            capabilities=definition.capabilities,
            adapter=adapter,
            credential_required=definition.credential_required,
            base_url_configurable=definition.base_url_configurable,
        )
