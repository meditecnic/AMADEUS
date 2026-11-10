from unittest.mock import AsyncMock

import pytest

from app import db
from app.routers import providers
from app.services.credentials import InMemoryCredentialStore
from app.services.provider_adapters import OpenAICompatibleAdapter, ProviderRequestError


@pytest.fixture
def connect(app_client, monkeypatch):
    store = InMemoryCredentialStore()
    monkeypatch.setattr(providers, "credential_store", store)
    monkeypatch.setattr(OpenAICompatibleAdapter, "list_models", AsyncMock(return_value=["chosen", "other"]))
    async def stream(*args, **kwargs):
        yield {"content": "OK"}
    monkeypatch.setattr(OpenAICompatibleAdapter, "get_chat_stream", stream)
    return app_client, store


def submit(client, **kwargs):
    return client.post("/api/providers/connect", json={"base_url": "https://connect.invalid/v1", **kwargs})


def test_two_phase_connect_reuses_key_and_admits_only_after_check(connect):
    client, store = connect
    first = submit(client, api_key="fake", catalog_id="plan")
    assert first.status_code == 200
    pending = first.json()
    assert pending["model_id"] is None and pending["check"]["ok"] is False
    profile_id = pending["provider_id"]
    assert client.portal.call(db.get_provider_configuration, profile_id)["selector_admission"] == "pending"
    second = submit(client, model_id="chosen", catalog_id="plan")
    assert second.status_code == 200
    assert second.json()["provider_id"] == profile_id
    assert store.get(profile_id) == "fake"
    row = client.portal.call(db.get_provider_configuration, profile_id)
    assert row["selector_admission"] == "admitted" and row["default_model"] == "chosen"
    other = submit(client, api_key="other-fake", catalog_id="other-plan")
    assert other.json()["provider_id"] != profile_id
    no_label = submit(client)
    assert submit(client).json()["provider_id"] == no_label.json()["provider_id"]


@pytest.mark.parametrize("step", ["save", "list", "check"])
@pytest.mark.parametrize("reuse", [False, True])
def test_each_connect_failure_restores_durable_runtime_and_key(connect, monkeypatch, step, reuse):
    client, store = connect
    if reuse:
        profile_id = submit(client, api_key="original").json()["provider_id"]
        before = client.portal.call(db.get_provider_configuration, profile_id)
        old_adapter = providers.provider_registry.snapshot(profile_id, allow_unavailable=True).adapter
    else:
        before_ids = {row["provider_id"] for row in client.portal.call(db.list_provider_configurations)}
    if step == "save":
        original = store.set
        def fail_after_write(provider, value):
            original(provider, value)
            if value == "replacement":
                raise RuntimeError("private detail")
        monkeypatch.setattr(store, "set", fail_after_write)
    elif step == "list":
        monkeypatch.setattr(OpenAICompatibleAdapter, "list_models", AsyncMock(side_effect=ProviderRequestError("custom", 429)))
    else:
        async def fail(*args, **kwargs):
            raise ProviderRequestError("custom", 402)
            yield
        monkeypatch.setattr(OpenAICompatibleAdapter, "get_chat_stream", fail)
    response = submit(client, api_key="replacement", model_id="chosen")
    assert response.json()["detail"]["step"] == step
    assert response.status_code == {"save": 502, "list": 429, "check": 402}[step]
    assert "private detail" not in response.text
    if reuse:
        assert client.portal.call(db.get_provider_configuration, profile_id) == before
        assert store.get(profile_id) == "original"
        assert providers.provider_registry.snapshot(profile_id, allow_unavailable=True).adapter is old_adapter
    else:
        assert {row["provider_id"] for row in client.portal.call(db.list_provider_configurations)} == before_ids
        assert not store._values


def test_second_check_failure_keeps_pending_profile_and_key(connect, monkeypatch):
    client, store = connect
    profile_id = submit(client, api_key="original").json()["provider_id"]
    before = client.portal.call(db.get_provider_configuration, profile_id)
    monkeypatch.setattr(providers, "check_selected_model_chat", AsyncMock(side_effect=providers.HTTPException(401, detail={"code": "credential_rejected"})))
    response = submit(client, model_id="chosen")
    assert response.status_code == 401
    assert response.json()["detail"]["step"] == "check"
    assert client.portal.call(db.get_provider_configuration, profile_id) == before
    assert store.get(profile_id) == "original"


@pytest.mark.parametrize("provider_id", ["deepseek", "glm", "openai", "gemini"])
def test_builtin_connect_uses_same_steps(connect, monkeypatch, provider_id):
    client, store = connect
    adapter = providers.provider_registry.snapshot(provider_id, allow_unavailable=True).adapter
    model = next(d.default_model for d in providers.provider_registry.list_definitions() if d.id == provider_id)
    monkeypatch.setattr(type(adapter), "list_models", AsyncMock(return_value=[model]))
    async def stream(*args, **kwargs):
        yield {"content": "OK"}
    monkeypatch.setattr(type(adapter), "get_chat_stream", stream)
    response = client.post("/api/providers/connect", json={"provider_id": provider_id, "model_id": model, "api_key": "fake"})
    assert response.status_code == 200
    assert response.json()["provider_id"] == provider_id
    assert response.json()["check"]["ok"] is True
