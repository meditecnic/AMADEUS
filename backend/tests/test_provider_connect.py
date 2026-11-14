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


def test_missing_catalog_does_not_take_over_a_labelled_profile(connect):
    client, _ = connect
    labeled = submit(client, catalog_id="paid-plan").json()
    plain = submit(client).json()
    assert plain["provider_id"] != labeled["provider_id"]
    assert submit(client).json()["provider_id"] == plain["provider_id"]


@pytest.mark.parametrize("reuse", [False, True])
@pytest.mark.parametrize("failure", ["durable_write", "registration"])
def test_save_failure_after_partial_configuration_is_compensated(connect, monkeypatch, reuse, failure):
    client, store = connect
    if reuse:
        provider_id = submit(client, api_key="original").json()["provider_id"]
        before = client.portal.call(db.get_provider_configuration, provider_id)
    else:
        before = client.portal.call(db.list_provider_configurations)
    if failure == "durable_write":
        save = providers.save_provider_configuration
        async def partial(*args, **kwargs):
            await save(*args, **kwargs)
            raise RuntimeError("after commit")
        monkeypatch.setattr(providers, "save_provider_configuration", partial)
    else:
        from unittest.mock import Mock
        monkeypatch.setattr(providers, "configure_custom_provider", Mock(side_effect=RuntimeError("register failed")))
    response = submit(client, model_id="chosen", api_key="replacement")
    assert response.status_code == 502 and response.json()["detail"]["step"] == "save"
    if reuse:
        assert client.portal.call(db.get_provider_configuration, provider_id) == before
        assert store.get(provider_id) == "original"
    else:
        assert client.portal.call(db.list_provider_configurations) == before
        assert not store._values


@pytest.fixture(params=["deepseek", "custom"])
def connected_target(connect, monkeypatch, request):
    client, store = connect
    target = request.param
    model = "chosen" if target == "custom" else providers.provider_registry.snapshot(target).model_id
    adapter_class = type(providers.provider_registry.snapshot(target).adapter)
    monkeypatch.setattr(adapter_class, "list_models", AsyncMock(return_value=[model]))
    chat_keys = []

    async def stream(self, **kwargs):
        chat_keys.append(kwargs["api_key"])
        yield {"content": "OK"}

    monkeypatch.setattr(adapter_class, "get_chat_stream", stream)
    address = {"base_url": "https://connect.invalid/v1"} if target == "custom" else {"provider_id": target}
    first = client.post("/api/providers/connect", json={**address, "model_id": model, "api_key": "original"})
    assert first.status_code == 200, first.text
    profile_id = first.json()["provider_id"]
    chat_keys.clear()
    return client, store, address, profile_id, model, adapter_class, chat_keys


@pytest.mark.parametrize("admission", ["admitted", "manual", "pending"])
def test_reconnect_without_model_preserves_selection_and_admission(connected_target, monkeypatch, admission):
    client, store, address, profile_id, model, _, _ = connected_target
    before = client.portal.call(db.get_provider_configuration, profile_id)
    client.portal.call(db.update_provider_selector_admission, profile_id, admission, before["configuration_version"])
    chat_check = AsyncMock(side_effect=AssertionError("key-only reconnect must only list models"))
    monkeypatch.setattr(providers, "check_selected_model_chat", chat_check)
    response = client.post("/api/providers/connect", json={**address, "api_key": "replacement"})
    assert response.status_code == 200, response.text
    assert response.json()["model_id"] == model
    assert response.json()["check"] == {"ok": False, "code": "not_checked"}
    after = client.portal.call(db.get_provider_configuration, profile_id)
    assert after["default_model"] == model and after["selector_admission"] == admission
    assert store.get(profile_id) == "replacement"
    chat_check.assert_not_called()
    assert client.post("/api/conversations", json={
        "session_id": "reconnect", "provider_id": profile_id,
    }).status_code == 201


def test_rejected_reconnect_restores_key_and_keeps_conversation_callable(connected_target, monkeypatch):
    client, store, address, profile_id, model, adapter_class, chat_keys = connected_target
    before = client.portal.call(db.get_provider_configuration, profile_id)

    async def list_models(self, *, api_key):
        if api_key == "rejected":
            raise ProviderRequestError(profile_id, 401)
        return [model]

    monkeypatch.setattr(adapter_class, "list_models", list_models)
    response = client.post("/api/providers/connect", json={**address, "api_key": "rejected"})
    assert response.status_code == 401
    assert response.json() == {"detail": {"code": "credential_rejected", "step": "list"}}
    assert client.portal.call(db.get_provider_configuration, profile_id) == before
    assert store.get(profile_id) == "original"
    assert client.post("/api/conversations", json={
        "session_id": "reconnect", "provider_id": profile_id,
    }).status_code == 201
    assert client.post(f"/api/providers/{profile_id}/chat-check", json={"model_id": model}).status_code == 200
    assert chat_keys == ["original"]


@pytest.mark.parametrize("name", ["", "   ", "\t\n"])
@pytest.mark.parametrize("target", ["deepseek", "custom"])
def test_connect_rejects_blank_display_name_before_saving(connect, monkeypatch, name, target):
    client, store = connect
    before = client.portal.call(db.list_provider_configurations)
    discover = AsyncMock(side_effect=AssertionError("blank name must not reach discovery"))
    monkeypatch.setattr(providers, "_discover_models", discover)
    address = {"provider_id": target} if target == "deepseek" else {"base_url": "https://connect.invalid/v1"}
    response = client.post("/api/providers/connect", json={**address, "display_name": name, "api_key": "fake"})
    assert response.status_code == 422
    assert response.json() == {"detail": {"code": "invalid_provider_configuration", "step": "save"}}
    assert client.portal.call(db.list_provider_configurations) == before
    assert not store._values
    discover.assert_not_called()


@pytest.mark.parametrize("missing", ["http_404", "unsupported"])
def test_service_without_a_model_list_still_connects(connect, monkeypatch, missing):
    client, store = connect
    if missing == "http_404":
        monkeypatch.setattr(OpenAICompatibleAdapter, "list_models",
                            AsyncMock(side_effect=ProviderRequestError("custom", 404)))
    else:
        async def unsupported(*args, **kwargs):
            raise providers.HTTPException(400, detail={"code": "model_discovery_unsupported"})
        monkeypatch.setattr(providers, "_discover_models", unsupported)
    first = submit(client, api_key="fake")
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["models"] == [] and body["models_listed"] is False
    assert body["model_id"] is None and body["check"] == {"ok": False, "code": "model_selection_required"}
    profile_id = body["provider_id"]
    assert store.get(profile_id) == "fake"
    second = submit(client, model_id="typed-by-hand")
    assert second.status_code == 200, second.text
    assert second.json()["provider_id"] == profile_id
    assert second.json()["check"] == {"ok": True, "code": "ok"} and second.json()["models_listed"] is False
    row = client.portal.call(db.get_provider_configuration, profile_id)
    assert row["selector_admission"] == "admitted" and row["default_model"] == "typed-by-hand"


def test_an_unreachable_address_still_fails_the_listing_step(connect, monkeypatch):
    import httpx
    client, store = connect
    monkeypatch.setattr(OpenAICompatibleAdapter, "list_models", AsyncMock(side_effect=httpx.ConnectError("x")))
    response = submit(client, api_key="fake")
    assert response.status_code == 502
    assert response.json() == {"detail": {"code": "address_unreachable", "step": "list"}}


def test_labels_decide_which_profile_a_connect_reuses(connect):
    client, store = connect
    plain = submit(client, api_key="plain-key").json()["provider_id"]
    # A label adopts the unlabelled profile at the address (made before labels existed) ...
    adopted = submit(client, api_key="plan-key", catalog_id="plan")
    assert adopted.json()["provider_id"] == plain
    assert client.portal.call(db.get_provider_configuration, plain)["catalog_id"] == "plan"
    # ... and no label never takes over a labelled one.
    other = submit(client, api_key="relay-key")
    assert other.json()["provider_id"] != plain
    assert store.get(plain) == "plan-key"
    assert client.portal.call(db.get_provider_configuration, plain)["catalog_id"] == "plan"


@pytest.fixture(params=["http_404", "unsupported"])
def listless(connect, monkeypatch, request):
    """A service without a model list, already connected with a key that works."""
    client, store = connect
    if request.param == "http_404":
        monkeypatch.setattr(OpenAICompatibleAdapter, "list_models",
                            AsyncMock(side_effect=ProviderRequestError("custom", 404)))
    else:
        async def unsupported(*args, **kwargs):
            raise providers.HTTPException(400, detail={"code": "model_discovery_unsupported"})
        monkeypatch.setattr(providers, "_discover_models", unsupported)
    response = submit(client, api_key="original", model_id="chosen")
    assert response.status_code == 200, response.text
    return client, store, response.json()["provider_id"]


def test_no_list_key_only_keeps_the_old_key_when_the_new_one_is_refused(listless, monkeypatch):
    client, store, profile_id = listless
    before = client.portal.call(db.get_provider_configuration, profile_id)
    monkeypatch.setattr(providers, "check_selected_model_chat", AsyncMock(
        side_effect=providers.HTTPException(401, detail={"code": "credential_rejected"})))
    response = submit(client, api_key="rejected")
    assert response.status_code == 401
    assert response.json() == {"detail": {"code": "credential_rejected", "step": "check"}}
    assert store.get(profile_id) == "original"
    assert client.portal.call(db.get_provider_configuration, profile_id) == before


@pytest.mark.parametrize("admission", ["admitted", "manual", "pending"])
def test_no_list_key_only_checks_the_kept_model_and_leaves_admission(listless, monkeypatch, admission):
    client, store, profile_id = listless
    row = client.portal.call(db.get_provider_configuration, profile_id)
    client.portal.call(db.update_provider_selector_admission, profile_id, admission, row["configuration_version"])
    chat_keys = []

    async def stream(self, **kwargs):
        chat_keys.append((kwargs["api_key"], kwargs["model"]))
        yield {"content": "OK"}

    monkeypatch.setattr(OpenAICompatibleAdapter, "get_chat_stream", stream)
    response = submit(client, api_key="replacement")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["model_id"] == "chosen" and body["models_listed"] is False
    assert body["check"] == {"ok": True, "code": "ok"}
    assert chat_keys == [("replacement", "chosen")]
    assert store.get(profile_id) == "replacement"
    after = client.portal.call(db.get_provider_configuration, profile_id)
    assert after["default_model"] == "chosen" and after["selector_admission"] == admission


def test_no_list_reconnect_without_a_key_or_a_kept_model_says_nothing(connect, monkeypatch):
    client, store = connect
    monkeypatch.setattr(OpenAICompatibleAdapter, "list_models",
                        AsyncMock(side_effect=ProviderRequestError("custom", 404)))
    chat_check = AsyncMock(side_effect=AssertionError("nothing to check"))
    monkeypatch.setattr(providers, "check_selected_model_chat", chat_check)
    # No model was ever chosen: a new key is kept and the model is still to be typed.
    profile_id = submit(client, api_key="first").json()["provider_id"]
    waiting = submit(client, api_key="second")
    assert waiting.json()["check"] == {"ok": False, "code": "model_selection_required"}
    assert store.get(profile_id) == "second"
    chat_check.assert_not_called()


def test_no_list_reconnect_with_a_kept_model_and_no_new_key_is_not_checked(listless, monkeypatch):
    client, store, profile_id = listless
    chat_check = AsyncMock(side_effect=AssertionError("no key was written"))
    monkeypatch.setattr(providers, "check_selected_model_chat", chat_check)
    response = submit(client)
    assert response.status_code == 200, response.text
    assert response.json()["check"] == {"ok": False, "code": "not_checked"}
    assert store.get(profile_id) == "original"
    chat_check.assert_not_called()


def test_a_failed_connect_gives_an_adopted_profile_its_blank_label_back(connect, monkeypatch):
    client, store = connect
    profile_id = submit(client, api_key="original", model_id="chosen").json()["provider_id"]
    before = client.portal.call(db.get_provider_configuration, profile_id)
    assert before["catalog_id"] is None
    monkeypatch.setattr(OpenAICompatibleAdapter, "list_models",
                        AsyncMock(side_effect=ProviderRequestError("custom", 401)))
    response = submit(client, api_key="rejected", catalog_id="plan")
    assert response.status_code == 401 and response.json()["detail"]["step"] == "list"
    assert client.portal.call(db.get_provider_configuration, profile_id) == before
    assert store.get(profile_id) == "original"


def test_a_label_adopts_the_oldest_of_two_unlabelled_profiles(connect):
    client, store = connect
    made = [client.post("/api/providers/custom-profiles", json={
        "display_name": name, "base_url": "https://connect.invalid/v1"}).json()["provider_id"] for name in ("a", "b")]
    rows = {row["provider_id"]: row for row in client.portal.call(db.list_provider_configurations)}
    oldest = min(made, key=lambda provider_id: (rows[provider_id]["created_at_utc"], provider_id))
    other = next(provider_id for provider_id in made if provider_id != oldest)
    untouched = rows[other]
    assert submit(client, api_key="plan-key", catalog_id="plan").json()["provider_id"] == oldest
    assert client.portal.call(db.get_provider_configuration, oldest)["catalog_id"] == "plan"
    assert client.portal.call(db.get_provider_configuration, other) == untouched
    assert store.get(other) is None


def test_connect_by_custom_provider_id_rekeys_and_rolls_back_only_target(connect, monkeypatch):
    client, store = connect
    alpha_id = client.post("/api/providers/custom-profiles", json={
        "display_name": "Alpha", "base_url": "https://connect.invalid/v1", "default_model": "chosen",
    }).json()["provider_id"]
    beta_id = client.post("/api/providers/custom-profiles", json={
        "display_name": "Beta", "base_url": "https://connect.invalid/v1", "default_model": "chosen",
    }).json()["provider_id"]
    gamma_id = client.post("/api/providers/custom-profiles", json={
        "display_name": "Gamma", "base_url": "https://connect.invalid/v1", "default_model": "chosen",
        "catalog_id": "plan",
    }).json()["provider_id"]
    store.set(alpha_id, "key-alpha")
    store.set(beta_id, "key-beta")

    # Rekeying Beta via provider_id updates only beta_id and preserves its display_name.
    ok = client.post("/api/providers/connect", json={"provider_id": beta_id, "api_key": "key-beta-2"})
    assert ok.status_code == 200, ok.text
    assert ok.json()["provider_id"] == beta_id
    assert store.get(beta_id) == "key-beta-2"
    assert store.get(alpha_id) == "key-alpha"
    assert client.portal.call(db.get_provider_configuration, beta_id)["display_name"] == "Beta"
    assert client.portal.call(db.get_provider_configuration, alpha_id)["display_name"] == "Alpha"

    # Rekeying Gamma preserves its existing catalog_id when omitted from connect payload.
    gamma_ok = client.post("/api/providers/connect", json={"provider_id": gamma_id, "api_key": "key-gamma"})
    assert gamma_ok.status_code == 200, gamma_ok.text
    assert client.portal.call(db.get_provider_configuration, gamma_id)["catalog_id"] == "plan"

    # Failed list_models rolls back only beta_id's key and leaves alpha_id untouched.
    before_beta = client.portal.call(db.get_provider_configuration, beta_id)
    before_alpha = client.portal.call(db.get_provider_configuration, alpha_id)
    monkeypatch.setattr(OpenAICompatibleAdapter, "list_models",
                        AsyncMock(side_effect=ProviderRequestError("custom", 401)))
    failed = client.post("/api/providers/connect", json={"provider_id": beta_id, "api_key": "rejected"})
    assert failed.status_code == 401
    assert failed.json()["detail"]["step"] == "list"
    assert store.get(beta_id) == "key-beta-2"
    assert store.get(alpha_id) == "key-alpha"
    assert client.portal.call(db.get_provider_configuration, beta_id) == before_beta
    assert client.portal.call(db.get_provider_configuration, alpha_id) == before_alpha


def test_connect_rejects_unknown_disabled_and_legacy_custom_provider_ids(connect):
    client, _store = connect
    gamma_id = client.post("/api/providers/custom-profiles", json={
        "display_name": "Disabled", "base_url": "https://connect.invalid/v1",
    }).json()["provider_id"]
    assert client.delete(f"/api/providers/custom-profiles/{gamma_id}").status_code == 200

    for bad_id in ("custom:00000000-0000-0000-0000-000000000000", gamma_id, "custom"):
        resp = client.post("/api/providers/connect", json={"provider_id": bad_id, "api_key": "any-key"})
        assert resp.status_code == 422, (bad_id, resp.text)
        assert resp.json()["detail"]["code"] == "invalid_provider_configuration"
