from __future__ import annotations

import json

import pytest

from app.services.model_catalog import (
    ModelCatalog,
    ModelUnavailableError,
    model_catalog,
)
from app.services.credentials import InMemoryCredentialStore
from app.services.provider_registry import (
    ProviderCapabilities,
    ProviderDefinition,
    ProviderRegistry,
    ProviderTask,
)
from app.services.provider_runtime import provider_registry


class _Adapter:
    pass


def _registry_definition() -> ProviderDefinition:
    return ProviderDefinition(
        id="fake",
        display_name="Fake",
        default_model="stable-model",
        capabilities=ProviderCapabilities(tasks=frozenset({ProviderTask.CHAT})),
    )


def test_versioned_manifest_is_strict_and_covers_current_builtins():
    payload = model_catalog.manifest

    assert payload["version"] == 1
    assert payload["verified_at"] == "2026-09-12"
    assert set(payload["providers"]) == {
        "deepseek",
        "glm",
        "openai",
        "gemini",
        "custom",
    }
    assert "kimi" not in payload["providers"]
    assert payload["providers"]["custom"]["allow_undiscovered_models"] is True
    assert all(
        provider["allow_undiscovered_models"] is False
        for provider_id, provider in payload["providers"].items()
        if provider_id != "custom"
    )

    rows = [
        (provider_id, row["id"])
        for provider_id, provider in payload["providers"].items()
        for row in provider["models"]
    ]
    assert len(rows) == len(set(rows))

    for provider_id, provider in payload["providers"].items():
        assert provider["models"], provider_id
        for row in provider["models"]:
            assert row["lifecycle"] in {
                "stable",
                "preview",
                "deprecated",
                "retired",
                "unavailable",
                "unverified",
            }
            control = row.get("thinking_control")
            if control is None:
                continue
            values = [option["value"] for option in control["options"]]
            assert values
            assert len(values) == len(set(values))
            assert control["default"] in values
            assert all(
                alias not in values and target in values
                for alias, target in control.get("aliases", {}).items()
            )
            assert control["request_mode"] in {
                "prompt_depth",
                "reasoning_effort",
                "gemini_thinking_level",
            }


def test_registered_providers_remove_kimi_without_silent_replacement():
    definitions = {row.id: row for row in provider_registry.list_definitions()}

    assert "kimi" not in definitions
    with pytest.raises(KeyError, match="unknown provider"):
        provider_registry.snapshot("kimi", "kimi-k2.6")


def test_live_discovery_merges_known_and_unknown_models_without_guessing_controls():
    try:
        rows = model_catalog.reconcile(
            "openai",
            ["gpt-5.6-luna", "gpt-future-unverified"],
        )
        by_id = {row["id"]: row for row in rows}

        assert by_id["gpt-5.6-luna"]["lifecycle"] == "stable"
        assert by_id["gpt-5.6-luna"]["thinking_control"]["default"] == "medium"
        assert by_id["gpt-future-unverified"]["lifecycle"] == "unverified"
        assert by_id["gpt-future-unverified"]["callable"] is False
        assert by_id["gpt-future-unverified"]["thinking_control"] is None
    finally:
        model_catalog.clear_discovery("openai")


def test_successful_discovery_marks_missing_known_models_unavailable():
    try:
        rows = model_catalog.reconcile("deepseek", ["deepseek-v4-flash"])
        by_id = {row["id"]: row for row in rows}

        assert by_id["deepseek-v4-flash"]["lifecycle"] == "deprecated"
        assert by_id["deepseek-v4-flash"]["callable"] is True
        assert by_id["deepseek-flash"]["lifecycle"] == "unavailable"
        assert by_id["deepseek-v4-pro"]["lifecycle"] == "unavailable"
        assert by_id["deepseek-v4-pro"]["callable"] is False
    finally:
        model_catalog.clear_discovery("deepseek")


def test_unknown_models_fail_closed_for_builtins_but_custom_remains_extensible():
    model_catalog.clear_discovery()
    with pytest.raises(ModelUnavailableError, match="deepseek/future-model"):
        model_catalog.ensure_callable("deepseek", "future-model")

    model_catalog.ensure_callable("custom", "operator-local-model")
    custom_row = model_catalog.public_model("custom", "operator-local-model")
    assert custom_row["lifecycle"] == "unverified"
    assert custom_row["callable"] is True


def test_discovered_unknown_builtin_models_are_visible_but_not_callable():
    try:
        rows = model_catalog.reconcile(
            "deepseek",
            ["deepseek-v4-flash", "deepseek-next"],
        )
        by_id = {row["id"]: row for row in rows}
        assert by_id["deepseek-next"]["lifecycle"] == "unverified"
        assert by_id["deepseek-next"]["callable"] is False
        with pytest.raises(ModelUnavailableError, match="deepseek/deepseek-next"):
            model_catalog.ensure_callable("deepseek", "deepseek-next")
    finally:
        model_catalog.clear_discovery("deepseek")


def test_live_discovery_metadata_survives_catalog_reconciliation():
    try:
        rows = model_catalog.reconcile(
            "openai",
            ["gpt-5.6-luna"],
            [
                {
                    "id": "gpt-5.6-luna",
                    "context_window": 2_000_000,
                    "max_output_tokens": 256_000,
                    "owned_by": "openai",
                }
            ],
        )
        row = next(item for item in rows if item["id"] == "gpt-5.6-luna")

        assert row["context_window"] == 2_000_000
        assert row["max_output_tokens"] == 256_000
        assert row["live_metadata"]["owned_by"] == "openai"
    finally:
        model_catalog.clear_discovery("openai")


@pytest.mark.parametrize(
    ("provider_id", "model_id", "raw", "expected"),
    [
        ("deepseek", "deepseek-flash", "xhigh", "max"),
        ("deepseek", "deepseek-v4-flash", "xhigh", "max"),
        ("deepseek", "deepseek-v4-flash", "low", "high"),
        ("deepseek", "deepseek-v4-pro", "max", "max"),
        ("deepseek", "deepseek-v4-pro", "xhigh", "max"),
        ("glm", "glm-5.2", "minimal", "minimal"),
        ("openai", "gpt-5.6-luna", "none", "none"),
        ("gemini", "gemini-3.5-flash", "low", "low"),
        ("custom", "local-model", "high", None),
        ("openai", "unknown-live-model", "high", None),
    ],
)
def test_model_specific_control_normalization(
    provider_id: str,
    model_id: str,
    raw: str,
    expected: str | None,
):
    assert model_catalog.normalize_control(provider_id, model_id, raw) == expected


def test_registry_blocks_retired_or_unavailable_models_before_adapter_use():
    catalog = ModelCatalog.from_mapping(
        {
            "version": 1,
            "verified_at": "2026-07-29",
            "providers": {
                "fake": {
                    "models": [
                        {
                            "id": "stable-model",
                            "display_name": "Stable",
                            "lifecycle": "stable",
                            "thinking_control": None,
                        },
                        {
                            "id": "retired-model",
                            "display_name": "Retired",
                            "lifecycle": "retired",
                            "thinking_control": None,
                        },
                    ]
                }
            },
        }
    )
    registry = ProviderRegistry(model_catalog=catalog)
    registry.register(_registry_definition(), _Adapter())

    assert registry.snapshot("fake", "stable-model").model_id == "stable-model"
    with pytest.raises(ModelUnavailableError, match="retired-model"):
        registry.snapshot("fake", "retired-model")

    catalog.reconcile("fake", ["unverified-live-model"])
    with pytest.raises(ModelUnavailableError, match="stable-model"):
        registry.snapshot("fake", "stable-model")
    # Discovered-but-unknown on non-custom providers stay visible, not callable.
    assert catalog.public_model("fake", "unverified-live-model")["callable"] is False
    with pytest.raises(ModelUnavailableError, match="unverified-live-model"):
        registry.snapshot("fake", "unverified-live-model")
    assert catalog.public_model("fake", "missing-unknown")["lifecycle"] == (
        "unavailable"
    )
    with pytest.raises(ModelUnavailableError, match="missing-unknown"):
        registry.snapshot("fake", "missing-unknown")


def test_provider_metadata_exposes_default_model_control_schema_and_no_kimi(app_client):
    response = app_client.get("/api/providers")

    assert response.status_code == 200
    rows = response.json()
    assert all(row["id"] != "kimi" for row in rows)
    by_id = {row["id"]: row for row in rows}
    flash = by_id["deepseek"]["default_model_capability"]
    assert by_id["deepseek"]["catalog_verified_at"] == "2026-09-12"
    assert by_id["deepseek"]["preferred_model_id"] == "deepseek-flash"
    assert flash["id"] == "deepseek-flash"
    assert flash["display_name"] == "DeepSeek Flash"
    assert flash["context_window"] is None
    assert flash["max_output_tokens"] is None
    assert flash["disclosed_version"] == "DeepSeek-V4.1-Flash"
    assert flash["thinking_control"]["native"] is True
    assert [row["value"] for row in flash["thinking_control"]["options"]] == [
        "high",
        "max",
    ]
    assert flash["thinking_control"]["aliases"]["xhigh"] == "max"
    assert flash["thinking_control"]["request_mode"] == "reasoning_effort"
    assert "https://www.deepseek.com/en/news/deepseek-v4-1-flash/" in flash["evidence"]
    assert "https://api-docs.deepseek.com/" in flash["evidence"]
    assert by_id["openai"]["catalog_version"] == 1
    assert by_id["openai"]["default_model_capability"]["thinking_control"][
        "options"
    ] == [
        {"value": "none", "label": "NONE"},
        {"value": "low", "label": "LOW"},
        {"value": "medium", "label": "MEDIUM"},
        {"value": "high", "label": "HIGH"},
        {"value": "xhigh", "label": "XHIGH"},
        {"value": "max", "label": "MAX"},
    ]


def test_models_endpoint_keeps_legacy_ids_and_adds_reconciled_catalog(
    app_client,
    monkeypatch,
):
    from app.routers import providers
    from app.services.provider_runtime import deepseek_service

    providers.provider_model_cache.clear("deepseek")

    async def discover(*, api_key=None):
        return [
            {
                "id": "deepseek-v4-flash",
                "context_window": 1_500_000,
            },
            {"id": "deepseek-next", "owned_by": "deepseek"},
        ]

    monkeypatch.setattr(deepseek_service, "list_models", discover)
    try:
        response = app_client.get("/api/providers/deepseek/models?refresh=true")

        assert response.status_code == 200
        payload = response.json()
        assert payload["models"] == ["deepseek-next", "deepseek-v4-flash"]
        assert payload["catalog_version"] == 1
        assert payload["catalog_verified_at"] == "2026-09-12"
        assert payload["discovery_status"] == "live"
        assert payload["preferred_model_id"] == "deepseek-flash"
        by_id = {row["id"]: row for row in payload["catalog"]}
        assert by_id["deepseek-flash"]["lifecycle"] == "unavailable"
        assert by_id["deepseek-v4-pro"]["lifecycle"] == "unavailable"
        assert by_id["deepseek-v4-flash"]["context_window"] == 1_500_000
        assert by_id["deepseek-v4-flash"]["compatibility_of"] == "deepseek-flash"
        assert by_id["deepseek-next"]["lifecycle"] == "unverified"
        assert by_id["deepseek-next"]["callable"] is False
        assert by_id["deepseek-next"]["live_metadata"]["owned_by"] == "deepseek"
        assert by_id["deepseek-next"]["thinking_control"] is None
        assert "source_message_ids" not in json.dumps(payload)
    finally:
        model_catalog.clear_discovery("deepseek")


def test_websocket_auth_accepts_catalog_value_and_drops_unknown_control(
    app_client,
    monkeypatch,
):
    from app.routers import chat_ws

    store = InMemoryCredentialStore()
    store.set("openai", "test-openai-secret")
    monkeypatch.setattr(chat_ws, "credential_store", store)

    with app_client.websocket_connect("/ws/chat?session_id=q41-openai") as websocket:
        websocket.send_json(
            {
                "type": "auth",
                "client": "desktop",
                "protocol_version": 2,
                "conversation_mode": "draft",
                "provider_id": "openai",
                "model": "gpt-5.6-luna",
                "reasoning_effort": "max",
            }
        )
        websocket.send_json({"type": "auth"})
        assert websocket.receive_json()["type"] == "session.ready"
        assert websocket.receive_json()["type"] == "error"
    assert chat_ws.sessions["q41-openai"].reasoning_effort == "max"

    with app_client.websocket_connect("/ws/chat?session_id=q41-custom") as websocket:
        websocket.send_json(
            {
                "type": "auth",
                "client": "desktop",
                "protocol_version": 2,
                "conversation_mode": "draft",
                "provider_id": "custom",
                "model": "local-model",
                "reasoning_effort": "high",
            }
        )
        websocket.send_json({"type": "auth"})
        assert websocket.receive_json()["type"] == "session.ready"
        assert websocket.receive_json()["type"] == "error"
    assert chat_ws.sessions["q41-custom"].reasoning_effort is None


def test_config_update_echoes_normalized_reasoning_effort(app_client, monkeypatch):
    """P1-2: alias inputs must surface as the effective catalog value."""
    from app.routers import chat_ws

    store = InMemoryCredentialStore()
    store.set("deepseek", "test-deepseek-secret")
    monkeypatch.setattr(chat_ws, "credential_store", store)

    with app_client.websocket_connect(
        "/ws/chat?session_id=q41-effort-echo"
    ) as websocket:
        websocket.send_json(
            {
                "type": "auth",
                "client": "desktop",
                "protocol_version": 2,
                "conversation_mode": "draft",
                "provider_id": "deepseek",
                "model": "deepseek-v4-flash",
                "reasoning_effort": "high",
            }
        )
        assert websocket.receive_json()["type"] == "session.ready"
        websocket.send_json(
            {
                "type": "config_update",
                "reasoning_effort": "xhigh",
            }
        )
        applied = websocket.receive_json()
        assert applied == {
            "type": "config_applied",
            "reasoning_effort": "max",
        }
    assert chat_ws.sessions["q41-effort-echo"].reasoning_effort == "max"


def test_config_update_accepts_catalog_model_and_rejects_unknown(
    app_client,
    monkeypatch,
):
    from app.routers import chat_ws

    store = InMemoryCredentialStore()
    store.set("deepseek", "test-deepseek-secret")
    monkeypatch.setattr(chat_ws, "credential_store", store)
    session_id = "q41-config-model"

    with app_client.websocket_connect(
        f"/ws/chat?session_id={session_id}"
    ) as websocket:
        websocket.send_json(
            {
                "type": "auth",
                "client": "desktop",
                "protocol_version": 2,
                "conversation_mode": "draft",
                "provider_id": "deepseek",
                "model": "deepseek-v4-flash",
            }
        )
        assert websocket.receive_json()["type"] == "session.ready"
        websocket.send_json(
            {
                "type": "config_update",
                "model": "deepseek-v4-pro",
            }
        )
        websocket.send_json(
            {
                "type": "config_update",
                "model": "deepseek-not-in-catalog",
            }
        )
        # Drain any frames; model rejection is silent (keep prior model).
        websocket.send_json({"type": "auth"})
        assert websocket.receive_json()["type"] == "error"

    assert chat_ws.sessions[session_id].model == "deepseek-v4-pro"


def test_websocket_blocks_model_that_disappeared_before_any_provider_call(
    app_client,
    monkeypatch,
):
    from app.routers import chat_ws

    session_id = "q41-disappeared-model"
    store = InMemoryCredentialStore()
    store.set("deepseek", "test-deepseek-secret")
    monkeypatch.setattr(chat_ws, "credential_store", store)
    model_catalog.clear_discovery("deepseek")

    async def must_not_generate(*args, **kwargs):
        raise AssertionError("provider generation must not run")

    monkeypatch.setattr(chat_ws, "get_clean_text_stream", must_not_generate)
    try:
        with app_client.websocket_connect(
            f"/ws/chat?session_id={session_id}&worldline=steins_gate"
        ) as websocket:
            websocket.send_json(
                {
                    "type": "auth",
                    "client": "desktop",
                    "protocol_version": 2,
                    "conversation_mode": "draft",
                    "provider_id": "deepseek",
                    "model": "deepseek-v4-flash",
                }
            )
            # Drain auth completion: second auth returns a stable error only after
            # the first auth finished (avoids racing reconcile before auth).
            websocket.send_json({"type": "auth"})
            assert websocket.receive_json()["type"] == "session.ready"
            auth_done = websocket.receive_json()
            assert auth_done["type"] == "error"
            assert "认证" in auth_done.get("message", "")

            model_catalog.reconcile("deepseek", ["deepseek-v4-pro"])
            websocket.send_json({"type": "chat.send", "content": "test"})

            frame = websocket.receive_json()
            assert frame["type"] == "error"
            assert frame["code"] == "model_unavailable"
            assert frame["recoverable"] is True
            assert chat_ws.sessions[session_id].is_busy is False
    finally:
        model_catalog.clear_discovery("deepseek")
