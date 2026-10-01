from __future__ import annotations

import json
from unittest.mock import AsyncMock

import httpx

from app.services.credentials import InMemoryCredentialStore, scrub_dotenv_key


def test_settings_response_never_contains_api_key(app_client):
    response = app_client.get("/api/settings")

    assert response.status_code == 200
    assert "api_key" not in response.json()
    assert "deepseek" not in json.dumps(response.json()).lower()


def test_provider_credentials_are_write_only_and_list_is_secret_free(
    app_client,
    monkeypatch,
):
    store = InMemoryCredentialStore()
    monkeypatch.setattr("app.routers.providers.credential_store", store)
    secret = "sk-super-secret-provider-value"

    saved = app_client.post(
        "/api/providers/deepseek/credentials",
        json={"api_key": secret},
    )
    listed = app_client.get("/api/providers")

    assert saved.status_code == 200
    assert saved.json() == {"provider_id": "deepseek", "configured": True}
    assert store.get("deepseek") == secret
    serialized = json.dumps(listed.json())
    assert listed.status_code == 200
    assert secret not in serialized
    assert "api_key" not in serialized
    deepseek = next(row for row in listed.json() if row["id"] == "deepseek")
    assert deepseek["configured"] is True


def test_custom_provider_configuration_is_validated_and_replaces_future_snapshots(
    app_client,
    monkeypatch,
):
    from app.routers import providers
    from app.services.provider_runtime import custom_provider_settings

    original = custom_provider_settings()

    async def persist(provider_id, **kwargs):
        return {
            "provider_id": provider_id,
            "base_url": kwargs["base_url"],
            "default_model": kwargs["default_model"],
            "display_name": kwargs.get("display_name") or "OpenAI-compatible",
            "enabled": bool(kwargs.get("enabled", True)),
            "created_at_utc": "2026-07-30T00:00:00Z",
            "updated_at_utc": "2026-07-30T00:00:00Z",
            "selector_admission": "admitted",
            "configuration_version": "2026-07-30T00:00:00Z",
        }

    monkeypatch.setattr(providers, "save_provider_configuration", persist)
    try:
        before = providers.provider_registry.snapshot("custom")
        response = app_client.patch(
            "/api/providers/custom/configuration",
            json={
                "base_url": "http://127.0.0.1:11434/v1/",
                "default_model": "qwen-local",
            },
        )
        after = providers.provider_registry.snapshot("custom")

        assert response.status_code == 200
        assert response.json()["base_url"] == "http://127.0.0.1:11434/v1"
        assert response.json()["default_model"] == "qwen-local"
        assert before.adapter is not after.adapter
        assert before.adapter.base_url == original["base_url"]
        assert after.adapter.base_url == "http://127.0.0.1:11434/v1"

        invalid = app_client.patch(
            "/api/providers/custom/configuration",
            json={"base_url": "https://user:password@example.com/v1", "default_model": "x"},
        )
        assert invalid.status_code == 422
    finally:
        providers.configure_custom_provider(**original)


def test_provider_credential_delete_returns_no_secret(app_client, monkeypatch):
    store = InMemoryCredentialStore()
    store.set("deepseek", "secret")
    monkeypatch.setattr("app.routers.providers.credential_store", store)

    response = app_client.delete("/api/providers/deepseek/credentials")

    assert response.status_code == 200
    assert response.json() == {"provider_id": "deepseek", "configured": False}
    assert store.get("deepseek") is None


def test_unknown_provider_cannot_create_a_credential(app_client):
    response = app_client.post(
        "/api/providers/missing/credentials",
        json={"api_key": "secret"},
    )

    assert response.status_code == 404


def test_websocket_auth_reads_credential_store_without_key_frame(
    app_client,
    monkeypatch,
):
    store = InMemoryCredentialStore()
    store.set("deepseek", "stored-provider-secret")
    monkeypatch.setattr("app.routers.chat_ws.credential_store", store)

    with app_client.websocket_connect(
        "/ws/chat?session_id=credential-auth"
    ) as websocket:
        websocket.send_json({"type": "auth", "protocol_version": 2})
        websocket.send_json({"type": "auth"})
        assert websocket.receive_json()["type"] == "session.ready"
        response = websocket.receive_json()

    assert response["type"] == "error"
    from app.routers.chat_ws import sessions

    assert sessions["credential-auth"].api_key == "stored-provider-secret"


def test_websocket_key_frame_cannot_override_stored_credential(
    app_client,
    monkeypatch,
):
    store = InMemoryCredentialStore()
    store.set("deepseek", "stored-provider-secret")
    monkeypatch.setattr("app.routers.chat_ws.credential_store", store)

    with app_client.websocket_connect(
        "/ws/chat?session_id=credential-frame-is-ignored"
    ) as websocket:
        websocket.send_json({"type": "auth", "api_key": "untrusted-frame-secret"})
        websocket.send_json({"type": "auth"})
        assert websocket.receive_json()["type"] == "session.ready"
        assert websocket.receive_json()["type"] == "error"

    assert store.get("deepseek") == "stored-provider-secret"
    from app.routers.chat_ws import sessions

    assert sessions["credential-frame-is-ignored"].api_key == "stored-provider-secret"


def test_custom_local_provider_can_authenticate_without_a_credential(
    app_client,
    isolated_provider_credentials,
):
    session_id = "custom-no-credential"
    isolated_provider_credentials.delete("custom")
    created = app_client.post(
        "/api/conversations",
        json={
            "session_id": session_id,
            "worldline": "steins_gate",
            "provider_id": "custom",
            "model_id": "local-model",
        },
    )
    conversation_id = created.json()["id"]

    with app_client.websocket_connect(f"/ws/chat?session_id={session_id}") as websocket:
        websocket.send_json({
            "type": "auth",
            "conversation_id": conversation_id,
            "protocol_version": 2,
        })
        websocket.send_json({"type": "auth"})
        assert websocket.receive_json()["type"] == "session.ready"
        response = websocket.receive_json()

    assert response["type"] == "error"
    assert "已完成认证" in response["message"]


def test_scrub_dotenv_key_removes_only_legacy_secret(tmp_path):
    env_file = tmp_path / ".env"
    secret = "sk-plaintext-must-disappear"
    env_file.write_text(
        "DEEPSEEK_MODEL=deepseek-chat\n"
        f"DEEPSEEK_API_KEY={secret}\n"
        "AMADEUS_VAD_THRESHOLD=0.5\n",
        encoding="utf-8",
    )

    assert scrub_dotenv_key(env_file, "DEEPSEEK_API_KEY") is True
    content = env_file.read_text(encoding="utf-8")
    assert secret not in content
    assert "DEEPSEEK_API_KEY" not in content
    assert "DEEPSEEK_MODEL=deepseek-chat" in content
    assert "AMADEUS_VAD_THRESHOLD=0.5" in content
    assert scrub_dotenv_key(env_file, "DEEPSEEK_API_KEY") is False


def test_provider_models_are_cached_and_refreshable(app_client, monkeypatch):
    from app.routers import providers
    from app.services.model_catalog import model_catalog
    from app.services.provider_runtime import deepseek_service

    providers.provider_model_cache.clear("deepseek")
    discover = AsyncMock(return_value=["deepseek-reasoner", "deepseek-chat"])
    monkeypatch.setattr(deepseek_service, "list_models", discover)

    try:
        first = app_client.get("/api/providers/deepseek/models")
        cached = app_client.get("/api/providers/deepseek/models")
        refreshed = app_client.get("/api/providers/deepseek/models?refresh=true")

        assert first.status_code == 200
        assert first.json()["provider_id"] == "deepseek"
        assert first.json()["models"] == ["deepseek-chat", "deepseek-reasoner"]
        assert first.json()["catalog_version"] == 1
        assert first.json()["cache_hit"] is False
        assert first.json()["discovery_status"] == "live"
        assert cached.json()["cache_hit"] is True
        assert cached.json()["discovery_status"] == "cache"
        assert refreshed.json()["cache_hit"] is False
        assert refreshed.json()["discovery_status"] == "live"
        assert discover.await_count == 2
    finally:
        model_catalog.clear_discovery("deepseek")


def test_refresh_failure_keeps_last_discovery_cache(app_client, monkeypatch):
    from app.routers import providers
    from app.services.model_catalog import model_catalog
    from app.services.provider_runtime import deepseek_service

    providers.provider_model_cache.clear("deepseek")
    discover = AsyncMock(
        side_effect=[
            ["deepseek-flash"],
            RuntimeError("upstream-timeout"),
        ]
    )
    monkeypatch.setattr(deepseek_service, "list_models", discover)
    try:
        first = app_client.get("/api/providers/deepseek/models?refresh=true")
        failed = app_client.get("/api/providers/deepseek/models?refresh=true")

        assert first.status_code == 200
        assert first.json()["discovery_status"] == "live"
        assert failed.status_code == 200
        assert failed.json()["discovery_status"] == "failed_using_cache"
        assert failed.json()["cache_hit"] is True
        assert failed.json()["models"] == ["deepseek-flash"]
        assert "upstream-timeout" not in json.dumps(failed.json())
    finally:
        model_catalog.clear_discovery("deepseek")


def test_provider_test_is_secret_free_and_normalizes_upstream_errors(
    app_client,
    monkeypatch,
):
    from app.routers import providers
    from app.services.provider_runtime import deepseek_service

    providers.provider_model_cache.clear("deepseek")
    secret = "secret-that-must-not-escape"
    monkeypatch.setattr(
        deepseek_service,
        "list_models",
        AsyncMock(side_effect=RuntimeError(secret)),
    )

    response = app_client.post("/api/providers/deepseek/test")

    assert response.status_code == 502
    serialized = json.dumps(response.json())
    assert secret not in serialized
    assert response.json()["detail"]["code"] == "provider_unavailable"


def test_provider_models_require_a_local_credential(
    app_client,
    isolated_provider_credentials,
):
    isolated_provider_credentials.delete("deepseek")

    response = app_client.get("/api/providers/deepseek/models")

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "credential_missing"


def test_selected_model_chat_check_uses_bounded_isolated_request(app_client, monkeypatch):
    from app.routers import providers
    from app.services.provider_adapters import OpenAICompatibleAdapter
    from app.services.provider_registry import (
        ProviderCapabilities,
        ProviderSnapshot,
        ProviderTask,
    )

    requests = []

    def respond(request):
        requests.append(json.loads(request.content))
        return httpx.Response(
            200,
            text='data: {"choices":[{"delta":{"content":"OK"}}]}\n\ndata: [DONE]\n\n',
        )

    adapter = OpenAICompatibleAdapter(
        provider_id="custom",
        base_url="http://127.0.0.1:1234/v1",
        default_model="default-model",
        message_builder=lambda *_args: (_ for _ in ()).throw(
            AssertionError("persona envelope must not be used")
        ),
        credential_required=False,
        transport=httpx.MockTransport(respond),
    )
    snapshot = ProviderSnapshot(
        provider_id="custom",
        model_id="selected-model",
        capabilities=ProviderCapabilities(tasks=frozenset({ProviderTask.CHAT})),
        adapter=adapter,
        credential_required=False,
    )
    monkeypatch.setattr(
        providers.provider_registry,
        "snapshot",
        lambda provider_id, model_id=None, **_kwargs: snapshot,
    )

    response = app_client.post(
        "/api/providers/custom/chat-check", json={"model_id": "selected-model"}
    )

    assert response.status_code == 200
    assert response.json() == {
        "provider_id": "custom",
        "model_id": "selected-model",
        "ok": True,
    }
    assert len(requests) == 1
    assert requests[0]["model"] == "selected-model"
    assert requests[0]["messages"] == [{"role": "user", "content": "Reply OK."}]
    assert requests[0]["max_tokens"] == 512
    assert "tools" not in requests[0]


def test_chat_check_does_not_expose_upstream_error_or_secret(app_client, monkeypatch):
    from app.routers import providers
    from app.services.provider_adapters import OpenAICompatibleAdapter
    from app.services.provider_registry import (
        ProviderCapabilities,
        ProviderSnapshot,
        ProviderTask,
    )

    secret = "sk-private-check-value"
    providers.credential_store.set("custom", secret)
    adapter = OpenAICompatibleAdapter(
        provider_id="custom",
        base_url="http://127.0.0.1:1234/v1",
        default_model="selected-model",
        message_builder=lambda *_args: [],
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(401, text=f"invalid {secret}")
        ),
    )
    snapshot = ProviderSnapshot(
        provider_id="custom",
        model_id="selected-model",
        capabilities=ProviderCapabilities(tasks=frozenset({ProviderTask.CHAT})),
        adapter=adapter,
    )
    monkeypatch.setattr(
        providers.provider_registry,
        "snapshot",
        lambda provider_id, model_id=None, **_kwargs: snapshot,
    )

    response = app_client.post(
        "/api/providers/custom/chat-check", json={"model_id": "selected-model"}
    )

    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "credential_rejected"
    assert secret not in json.dumps(response.json())
