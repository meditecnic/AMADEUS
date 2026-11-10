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


@pytest.mark.parametrize("provider_id", ["deepseek", "glm", "openai", "gemini"])
@pytest.mark.parametrize("upstream,status,code", [
    (401, 401, "credential_rejected"),
    (403, 401, "credential_rejected"),
    (404, 502, "address_unreachable"),
    (429, 429, "rate_limited"),
    (402, 402, "insufficient_balance"),
])
@pytest.mark.parametrize("operation", ["models", "chat-check"])
def test_builtin_http_failures_keep_status(app_client, monkeypatch, provider_id, upstream, status, code, operation):
    providers.credential_store.set(provider_id, "fake-rework-key")
    providers.provider_model_cache.clear(provider_id)
    requests = []

    def reject(request):
        requests.append(request)
        return httpx.Response(upstream, text="private upstream body fake-rework-key")

    client = httpx.AsyncClient
    transport = httpx.MockTransport(reject)
    monkeypatch.setattr(httpx, "AsyncClient", lambda *args, **kwargs: client(
        *args, **{**kwargs, "transport": transport},
    ))
    if operation == "models":
        response = app_client.get(f"/api/providers/{provider_id}/models?refresh=true")
    else:
        model = providers.provider_registry.snapshot(provider_id, allow_unavailable=True).model_id
        response = app_client.post(f"/api/providers/{provider_id}/chat-check", json={"model_id": model})
    assert response.status_code == status, response.text
    assert response.json() == {"detail": {"code": code}}
    assert len(requests) == 1
