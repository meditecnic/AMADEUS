from __future__ import annotations

from dataclasses import FrozenInstanceError
from unittest.mock import AsyncMock

import pytest

from app.services.provider_registry import (
    ProviderCapabilities,
    ProviderDefinition,
    ProviderModelCache,
    ProviderRegistry,
    ProviderTask,
)
from app.services.memory import MemoryService
from app.routers.chat_ws import SessionState, get_clean_text_stream


class FakeAdapter:
    def __init__(self, response=None):
        self.response = response or {"ok": True}
        self.calls = []

    async def complete_json(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


class StreamingFakeAdapter(FakeAdapter):
    async def get_chat_stream(self, **kwargs):
        self.calls.append(kwargs)
        yield {"content": "[EMO:neutral] テスト応答。", "tool_calls": None}


def _definition() -> ProviderDefinition:
    return ProviderDefinition(
        id="fake",
        display_name="Fake Provider",
        default_model="fake-chat",
        capabilities=ProviderCapabilities(
            tasks=frozenset(
                {
                    ProviderTask.CHAT,
                    ProviderTask.TRANSLATION,
                    ProviderTask.MEMORY,
                }
            ),
            parameters=frozenset({"temperature", "tools"}),
            structured_output=True,
        ),
    )


def test_registry_returns_secret_free_definitions_and_frozen_turn_snapshot():
    registry = ProviderRegistry()
    adapter = FakeAdapter()
    registry.register(_definition(), adapter)

    public = registry.list_definitions()
    snapshot = registry.snapshot("fake", "fake-v2")

    assert public == [_definition()]
    assert "api_key" not in repr(public).lower()
    assert snapshot.provider_id == "fake"
    assert snapshot.model_id == "fake-v2"
    assert snapshot.adapter is adapter
    with pytest.raises(FrozenInstanceError):
        snapshot.model_id = "changed"


def test_capability_layer_filters_parameters_with_explicit_diagnostics():
    registry = ProviderRegistry()
    registry.register(_definition(), FakeAdapter())
    snapshot = registry.snapshot("fake")

    result = snapshot.filter_parameters(
        {"temperature": 0.4, "tools": [], "reasoning_effort": "high"}
    )

    assert result.accepted == {"temperature": 0.4, "tools": []}
    assert result.unsupported == {"reasoning_effort": "high"}
    assert "reasoning_effort" in result.diagnostic


def test_registry_rejects_unknown_provider_and_unsupported_task():
    registry = ProviderRegistry()
    registry.register(_definition(), FakeAdapter())

    with pytest.raises(KeyError, match="unknown provider"):
        registry.snapshot("missing")
    with pytest.raises(ValueError, match="does not support"):
        registry.snapshot("fake").require(ProviderTask.COMPRESSION)


def test_duplicate_provider_registration_is_rejected():
    registry = ProviderRegistry()
    registry.register(_definition(), FakeAdapter())

    with pytest.raises(ValueError, match="already registered"):
        registry.register(_definition(), FakeAdapter())


@pytest.mark.asyncio
async def test_model_cache_uses_24_hour_ttl_and_explicit_refresh():
    now = [100.0]
    calls = []

    async def load_models():
        calls.append(now[0])
        return ["model-b", "model-a", "model-a"]

    cache = ProviderModelCache(clock=lambda: now[0])

    first = await cache.get("fake", load_models)
    second = await cache.get("fake", load_models)
    now[0] += (24 * 60 * 60) - 1
    before_expiry = await cache.get("fake", load_models)
    now[0] += 2
    expired = await cache.get("fake", load_models)
    refreshed = await cache.get("fake", load_models, refresh=True)

    assert first.models == ("model-a", "model-b")
    assert first.cache_hit is False
    assert second.cache_hit is True
    assert before_expiry.cache_hit is True
    assert expired.cache_hit is False
    assert refreshed.cache_hit is False
    assert len(calls) == 3


@pytest.mark.asyncio
async def test_memory_extraction_uses_selected_provider_snapshot(monkeypatch):
    adapter = FakeAdapter(
        {
            "working_summary": "冈部在检查实验数据。",
            "episodic": [],
            "core_facts": [],
        }
    )
    registry = ProviderRegistry()
    registry.register(_definition(), adapter)
    snapshot = registry.snapshot("fake", "memory-model")
    service = MemoryService()
    service.apply_extraction = AsyncMock()
    save_summary = AsyncMock()
    monkeypatch.setattr("app.models.save_memory_summary", save_summary)
    monkeypatch.setattr("app.models.get_conversation_content_epoch", AsyncMock(return_value=0))

    await service._extract_turn(
        session_id="okabe",
        worldline="steins_gate",
        conversation_id="conversation-1",
        user_text="记住实验编号是42。",
        assistant_text="了解。",
        source_message_ids=[10, 11],
        api_key="provider-secret",
        provider_snapshot=snapshot,
    )

    assert adapter.calls[0]["model"] == "memory-model"
    assert adapter.calls[0]["api_key"] == "provider-secret"
    service.apply_extraction.assert_awaited_once()
    save_summary.assert_awaited_once()


@pytest.mark.asyncio
async def test_turn_snapshot_keeps_model_when_session_changes_mid_turn():
    registry = ProviderRegistry()
    adapter = StreamingFakeAdapter()
    registry.register(_definition(), adapter)
    snapshot = registry.snapshot("fake", "frozen-model")
    session = SessionState("snapshot-test")
    session.api_key = "provider-secret"
    session.system_prompt = "SYSTEM"
    session.model = "changed-after-turn-start"

    text, emotion, tool_calls, found = await get_clean_text_stream(
        active_history=[],
        memory_summary="",
        session=session,
        provider_snapshot=snapshot,
    )

    assert adapter.calls[0]["model"] == "frozen-model"
    assert text.strip()
    assert emotion == "neutral"
    assert tool_calls == {}
    assert found is True
