"""Credential policy for v11 memory extraction, without provider HTTP."""

from __future__ import annotations

from dataclasses import replace
from unittest.mock import AsyncMock

import pytest

from app.services.memory_v11.jobs import default_memory_completion
from app.services.provider_registry import ProviderCapabilities, ProviderTask
from app.services.provider_runtime import configure_custom_provider, provider_registry


_PROFILE_ID = "custom:11111111-2222-4333-8444-555555555555"
_KEYS = [
    pytest.param(None, id="no-key"),
    pytest.param("", id="empty-key"),
    pytest.param("test", id="short-test-key"),
    pytest.param("TeSt-do-not-send", id="mixed-case-test-key"),
    pytest.param("local", id="short-optional-key"),
    pytest.param("1234567", id="seven-char-key"),
    pytest.param("12345678", id="eight-char-key"),
]


@pytest.fixture(params=["custom", _PROFILE_ID, "deepseek", "glm"])
def provider(request, monkeypatch):
    provider_id = request.param
    if provider_id == _PROFILE_ID:
        configure_custom_provider(
            provider_id=provider_id,
            base_url="http://127.0.0.1:1234/v1",
            default_model="local-model",
            display_name="Memory credential regression",
        )
    try:
        snapshot = provider_registry.snapshot(provider_id)
        completion = AsyncMock(return_value={"facts": [], "observations": [], "operations": []})
        monkeypatch.setattr(type(snapshot.adapter), "complete_json", staticmethod(completion))
        yield snapshot, completion
    finally:
        if provider_id == _PROFILE_ID:
            provider_registry.unregister(provider_id)


@pytest.mark.parametrize("api_key", _KEYS)
async def test_v11_credential_policy(provider, isolated_provider_credentials, monkeypatch, api_key):
    snapshot, completion = provider
    isolated_provider_credentials.delete(snapshot.provider_id)
    if api_key:
        isolated_provider_credentials.set(snapshot.provider_id, api_key)
    elif api_key == "":
        # The concrete store rejects empty writes; exercise defensive empty reads.
        monkeypatch.setattr(isolated_provider_credentials, "get", lambda provider_id: "")
    kwargs = dict(
        provider_id=snapshot.provider_id,
        model_id=snapshot.model_id,
        user_text="I like astronomy",
        source_message_id=101,
    )
    allowed = (
        not (api_key or "").lower().startswith("test")
        and (not snapshot.credential_required or len(api_key or "") >= 8)
    )
    if allowed:
        payload = await default_memory_completion(**kwargs)
        assert payload["observations"] == []
        completion.assert_awaited_once()
        assert completion.await_args.kwargs["api_key"] == (api_key or None)
        assert completion.await_args.kwargs["model"] == snapshot.model_id
    else:
        with pytest.raises(RuntimeError, match="provider_unavailable"):
            await default_memory_completion(**kwargs)
        completion.assert_not_called()


async def test_memory_capability_still_required(provider, isolated_provider_credentials, monkeypatch):
    snapshot, completion = provider
    snapshot = replace(snapshot, capabilities=ProviderCapabilities(tasks=frozenset({ProviderTask.CHAT})))
    isolated_provider_credentials.set(snapshot.provider_id, "12345678")
    monkeypatch.setattr(type(provider_registry), "snapshot", staticmethod(lambda *args: snapshot))
    with pytest.raises(RuntimeError, match="provider_unavailable"):
        await default_memory_completion(provider_id=snapshot.provider_id)
    completion.assert_not_called()


@pytest.mark.parametrize(
    "api_key,reason",
    [(None, "required_key_missing_or_short"), ("tiny", "required_key_missing_or_short"), ("TeSt-private-value", "test_key")],
)
async def test_skip_reasons_do_not_expose_credentials(
    monkeypatch, isolated_provider_credentials, caplog, api_key, reason
):
    snapshot = provider_registry.snapshot("deepseek")
    completion = AsyncMock()
    monkeypatch.setattr(type(snapshot.adapter), "complete_json", staticmethod(completion))
    isolated_provider_credentials.delete("deepseek")
    if api_key is not None:
        isolated_provider_credentials.set("deepseek", api_key)
    with pytest.raises(RuntimeError, match="provider_unavailable") as exc:
        await default_memory_completion(provider_id="deepseek")
    assert f"reason={reason}" in caplog.text
    if api_key:
        assert api_key not in caplog.text + str(exc.value)
    completion.assert_not_called()
