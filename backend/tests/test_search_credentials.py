from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from app.services.credentials import InMemoryCredentialStore
from app.services.search import SEARCH_CREDENTIAL_ID, SearchResponse, SearchService


def test_search_credential_is_write_only_and_blank_preserves_existing(
    app_client,
    monkeypatch,
):
    store = InMemoryCredentialStore()
    monkeypatch.setattr("app.routers.system_api.credential_store", store)

    initial = app_client.get("/api/search/settings")
    saved = app_client.put(
        "/api/search/credential",
        json={"api_key": "tvly-super-secret"},
    )
    preserved = app_client.put("/api/search/credential", json={"api_key": ""})
    configured = app_client.get("/api/search/settings")

    assert initial.json() == {
        "provider": "tavily",
        "configured": False,
        "fallback": "duckduckgo",
    }
    assert saved.json() == {"provider": "tavily", "configured": True}
    assert preserved.json() == {"provider": "tavily", "configured": True}
    assert store.get(SEARCH_CREDENTIAL_ID) == "tvly-super-secret"
    serialized = json.dumps(configured.json())
    assert configured.json()["configured"] is True
    assert "tvly-super-secret" not in serialized
    assert "api_key" not in serialized


def test_search_credential_can_be_deleted(app_client, monkeypatch):
    store = InMemoryCredentialStore()
    store.set(SEARCH_CREDENTIAL_ID, "tvly-secret")
    monkeypatch.setattr("app.routers.system_api.credential_store", store)

    response = app_client.delete("/api/search/credential")

    assert response.json() == {"provider": "tavily", "configured": False}
    assert store.get(SEARCH_CREDENTIAL_ID) is None


def test_search_credential_change_invalidates_cached_failures():
    service = SearchService()
    service._cache["same query"] = (
        float("inf"),
        SearchResponse("same query", "none", "unavailable", "tavily_key_missing"),
    )
    service._usage_cache = (float("inf"), {"usage": 1})

    service.credentials_changed()

    assert service._cache == {}
    assert service._usage_cache is None


@pytest.mark.asyncio
async def test_search_reads_latest_stored_tavily_key_without_restart(monkeypatch):
    store = InMemoryCredentialStore()
    store.set(SEARCH_CREDENTIAL_ID, "tvly-latest")
    monkeypatch.setattr("app.services.search.credential_store", store)
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    service = SearchService()
    service._consume_tavily_credit = AsyncMock(return_value=(True, None))
    service._tavily = AsyncMock(
        return_value=SearchResponse("query", "tavily", "verified evidence")
    )

    response = await service.search("query")

    assert response.source == "tavily"
    service._tavily.assert_awaited_once_with("query", "tvly-latest")


@pytest.mark.asyncio
async def test_keyless_search_does_not_consume_tavily_credit(monkeypatch):
    store = InMemoryCredentialStore()
    monkeypatch.setattr("app.services.search.credential_store", store)
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    service = SearchService()
    service._consume_tavily_credit = AsyncMock()
    service._duckduckgo = AsyncMock(
        return_value=SearchResponse("query", "none", "unavailable", "tavily_key_missing")
    )

    await service.search("query")

    service._consume_tavily_credit.assert_not_awaited()
