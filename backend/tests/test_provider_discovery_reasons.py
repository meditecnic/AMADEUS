from unittest.mock import AsyncMock

import httpx
import pytest

from app.routers import providers
from app.services.provider_adapters import ProviderRequestError


@pytest.mark.parametrize("error,status,code", [
    (ProviderRequestError("custom", 401), 401, "credential_rejected"),
    (ProviderRequestError("custom", 403), 401, "credential_rejected"),
    (ProviderRequestError("custom", 404), 502, "address_unreachable"),
    (ProviderRequestError("custom", 429), 429, "rate_limited"),
    (ProviderRequestError("custom", 402), 402, "insufficient_balance"),
    (httpx.ConnectError("private address"), 502, "address_unreachable"),
    (httpx.ReadTimeout("private address"), 504, "discovery_timeout"),
    (TimeoutError(), 504, "discovery_timeout"),
    (RuntimeError("private key"), 502, "provider_unavailable"),
])
@pytest.mark.parametrize("operation", ["models", "chat-check"])
def test_discovery_reasons(app_client, monkeypatch, error, status, code, operation):
    snapshot = providers.provider_registry.snapshot("custom")
    providers.provider_model_cache.clear("custom")
    if operation == "models":
        monkeypatch.setattr(snapshot.adapter, "list_models", AsyncMock(side_effect=error))
        response = app_client.get("/api/providers/custom/models?refresh=true")
    else:
        async def stream(**kwargs):
            raise error
            yield
        monkeypatch.setattr(snapshot.adapter, "get_chat_stream", stream)
        response = app_client.post("/api/providers/custom/chat-check", json={"model_id": "local-model"})
    assert response.status_code == status
    assert response.json() == {"detail": {"code": code}}
