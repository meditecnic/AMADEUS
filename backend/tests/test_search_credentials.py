from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from app.services.credentials import InMemoryCredentialStore
from app.services.search import (
    SEARCH_ACTIVE_PROVIDER_ID,
    SEARCH_CREDENTIAL_BY_PROVIDER,
    SEARCH_CREDENTIAL_ID,
    SearchResponse,
    SearchService,
)


def test_search_credential_is_write_only_and_blank_preserves_existing(
    app_client,
    monkeypatch,
):
    store = InMemoryCredentialStore()
    monkeypatch.setattr("app.routers.system_api.credential_store", store)
    monkeypatch.setattr("app.services.search.credential_store", store)

    initial = app_client.get("/api/search/settings")
    saved = app_client.put(
        "/api/search/credential",
        json={"provider": "tavily", "api_key": "tvly-super-secret"},
    )
    preserved = app_client.put(
        "/api/search/credential",
        json={"provider": "tavily", "api_key": ""},
    )
    configured = app_client.get("/api/search/settings")

    assert initial.json()["providers"]["tavily"]["configured"] is False
    assert initial.json()["active_provider"] == "tavily"
    assert saved.json()["provider"] == "tavily"
    assert saved.json()["configured"] is True
    assert preserved.json()["configured"] is True
    assert store.get(SEARCH_CREDENTIAL_ID) == "tvly-super-secret"
    serialized = json.dumps(configured.json())
    assert configured.json()["providers"]["tavily"]["configured"] is True
    assert "tvly-super-secret" not in serialized
    assert "api_key" not in serialized


def test_search_supports_firecrawl_provider_and_active_switch(app_client, monkeypatch):
    store = InMemoryCredentialStore()
    monkeypatch.setattr("app.routers.system_api.credential_store", store)
    monkeypatch.setattr("app.services.search.credential_store", store)

    saved = app_client.put(
        "/api/search/credential",
        json={"provider": "firecrawl", "api_key": "fc-test-key"},
    )
    switched = app_client.patch(
        "/api/search/settings",
        json={"active_provider": "firecrawl"},
    )
    settings = app_client.get("/api/search/settings")

    assert saved.json()["provider"] == "firecrawl"
    assert saved.json()["configured"] is True
    assert store.get(SEARCH_CREDENTIAL_BY_PROVIDER["firecrawl"]) == "fc-test-key"
    assert switched.json()["active_provider"] == "firecrawl"
    body = settings.json()
    assert body["active_provider"] == "firecrawl"
    assert body["providers"]["firecrawl"]["configured"] is True
    assert body["providers"]["tavily"]["configured"] is False
    assert "fc-test-key" not in json.dumps(body)


def test_search_credential_can_be_deleted(app_client, monkeypatch):
    store = InMemoryCredentialStore()
    store.set(SEARCH_CREDENTIAL_ID, "tvly-secret")
    store.set(SEARCH_ACTIVE_PROVIDER_ID, "tavily")
    monkeypatch.setattr("app.routers.system_api.credential_store", store)
    monkeypatch.setattr("app.services.search.credential_store", store)

    response = app_client.delete("/api/search/credential?provider=tavily")

    assert response.json()["provider"] == "tavily"
    assert response.json()["configured"] is False
    assert store.get(SEARCH_CREDENTIAL_ID) is None


def test_delete_active_provider_falls_back_to_other_configured(app_client, monkeypatch):
    store = InMemoryCredentialStore()
    store.set(SEARCH_CREDENTIAL_BY_PROVIDER["tavily"], "tvly-secret")
    store.set(SEARCH_CREDENTIAL_BY_PROVIDER["firecrawl"], "fc-secret")
    store.set(SEARCH_ACTIVE_PROVIDER_ID, "tavily")
    monkeypatch.setattr("app.routers.system_api.credential_store", store)
    monkeypatch.setattr("app.services.search.credential_store", store)

    response = app_client.delete("/api/search/credential?provider=tavily")
    body = response.json()

    assert body["provider"] == "tavily"
    assert body["active_provider"] == "firecrawl"
    assert store.get(SEARCH_CREDENTIAL_BY_PROVIDER["firecrawl"]) == "fc-secret"


def test_search_credential_change_invalidates_cached_failures():
    service = SearchService()
    service._cache["tavily::same query"] = (
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
    store.set(SEARCH_ACTIVE_PROVIDER_ID, "tavily")
    monkeypatch.setattr("app.services.search.credential_store", store)
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    monkeypatch.delenv("FIRECRAWL_API_KEY", raising=False)
    service = SearchService()
    service._consume_search_credit = AsyncMock(return_value=(True, None))
    service._tavily = AsyncMock(
        return_value=SearchResponse("query", "tavily", "verified evidence")
    )

    response = await service.search("query")

    assert response.source == "tavily"
    service._tavily.assert_awaited_once_with("query", "tvly-latest")


@pytest.mark.asyncio
async def test_search_uses_active_firecrawl_provider(monkeypatch):
    store = InMemoryCredentialStore()
    store.set(SEARCH_CREDENTIAL_BY_PROVIDER["firecrawl"], "fc-latest")
    store.set(SEARCH_ACTIVE_PROVIDER_ID, "firecrawl")
    monkeypatch.setattr("app.services.search.credential_store", store)
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    monkeypatch.delenv("FIRECRAWL_API_KEY", raising=False)
    service = SearchService()
    service._consume_search_credit = AsyncMock(return_value=(True, None))
    service._firecrawl = AsyncMock(
        return_value=SearchResponse("query", "firecrawl", "fc evidence")
    )
    service._tavily = AsyncMock()

    response = await service.search("query")

    assert response.source == "firecrawl"
    service._firecrawl.assert_awaited_once_with("query", "fc-latest")
    service._tavily.assert_not_awaited()


@pytest.mark.asyncio
async def test_keyless_search_does_not_consume_tavily_credit(monkeypatch):
    store = InMemoryCredentialStore()
    monkeypatch.setattr("app.services.search.credential_store", store)
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    monkeypatch.delenv("FIRECRAWL_API_KEY", raising=False)
    service = SearchService()
    service._consume_search_credit = AsyncMock()

    response = await service.search("query")

    service._consume_search_credit.assert_not_awaited()
    assert response.source == "none"
    assert response.failure_code == "unavailable"


def test_format_firecrawl_evidence_web_hits():
    payload = {
        "success": True,
        "data": {
            "web": [
                {
                    "title": "Spain win",
                    "url": "https://example.test/final",
                    "description": "Spain defeated Argentina.",
                }
            ]
        },
    }
    text = SearchService._format_firecrawl_evidence(payload)
    assert "Spain win" in text
    assert "https://example.test/final" in text
    assert "Spain defeated Argentina" in text
