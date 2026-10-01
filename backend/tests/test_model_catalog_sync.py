from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.services.model_catalog import ModelCatalog, ModelUnavailableError, model_catalog
from app.services.provider_runtime import deepseek_service


FIXTURE = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "deepseek_list_models_ids_only.json"
)


def test_official_list_models_fixture_does_not_disclose_underlying_version():
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    ids = [row["id"] for row in payload["data"]]

    assert "deepseek-flash" in ids
    assert all("v4.1" not in row["id"].lower() for row in payload["data"])
    assert all(set(row) <= {"id", "object", "owned_by"} for row in payload["data"])


def test_discovery_adds_new_request_id_without_copying_old_token_limits():
    try:
        rows = model_catalog.reconcile(
            "deepseek",
            ["deepseek-flash", "deepseek-v4-pro"],
            json.loads(FIXTURE.read_text(encoding="utf-8"))["data"],
        )
        by_id = {row["id"]: row for row in rows}

        assert by_id["deepseek-flash"]["callable"] is True
        assert by_id["deepseek-flash"]["display_name"] == "DeepSeek Flash"
        assert by_id["deepseek-flash"]["display_source"] == "manifest"
        assert by_id["deepseek-flash"]["disclosed_version"] == "DeepSeek-V4.1-Flash"
        assert by_id["deepseek-flash"]["context_window"] is None
        assert by_id["deepseek-flash"]["max_output_tokens"] is None
        assert by_id["deepseek-flash"]["thinking_control"]["request_mode"] == (
            "reasoning_effort"
        )
        assert by_id["deepseek-v4-flash"]["lifecycle"] == "unavailable"
        assert by_id["deepseek-v4-flash"]["callable"] is False
        assert by_id["deepseek-v4-flash"]["id"] == "deepseek-v4-flash"
        assert by_id["deepseek-v4-flash"]["compatibility_of"] == "deepseek-flash"
        assert by_id["deepseek-v4-pro"]["lifecycle"] == "stable"
        assert by_id["deepseek-flash"]["live_metadata"]["owned_by"] == "deepseek"
        assert "v4.1" not in json.dumps(by_id["deepseek-flash"]["live_metadata"]).lower()
    finally:
        model_catalog.clear_discovery("deepseek")


def test_saved_alias_is_not_rewritten_when_preferred_id_appears():
    try:
        rows = model_catalog.reconcile(
            "deepseek",
            ["deepseek-flash", "deepseek-v4-flash"],
        )
        by_id = {row["id"]: row for row in rows}
        assert by_id["deepseek-v4-flash"]["id"] == "deepseek-v4-flash"
        assert by_id["deepseek-flash"]["id"] == "deepseek-flash"
        assert by_id["deepseek-v4-flash"]["callable"] is True
        model_catalog.ensure_callable("deepseek", "deepseek-v4-flash")
        model_catalog.ensure_callable("deepseek", "deepseek-flash")
    finally:
        model_catalog.clear_discovery("deepseek")


def test_unverified_builtin_does_not_inherit_thinking_or_version():
    try:
        rows = model_catalog.reconcile(
            "deepseek",
            ["deepseek-flash", "deepseek-next"],
            [
                {
                    "id": "deepseek-next",
                    "name": "Next Marketing Name",
                    "owned_by": "deepseek",
                }
            ],
        )
        row = next(item for item in rows if item["id"] == "deepseek-next")
        assert row["lifecycle"] == "unverified"
        assert row["callable"] is False
        assert row["thinking_control"] is None
        assert row["disclosed_version"] is None
        assert row["context_window"] is None
        assert row["display_name"] == "Next Marketing Name"
        assert row["display_source"] == "live_name"
        with pytest.raises(ModelUnavailableError):
            model_catalog.ensure_callable("deepseek", "deepseek-next")
    finally:
        model_catalog.clear_discovery("deepseek")


def test_custom_provider_does_not_receive_official_deepseek_alias_mapping():
    try:
        rows = model_catalog.reconcile(
            "custom",
            ["deepseek-flash", "local-model"],
        )
        by_id = {row["id"]: row for row in rows}
        flash = by_id["deepseek-flash"]
        assert flash["source"] == "dynamic"
        assert flash["callable"] is True
        assert flash["thinking_control"] is None
        assert flash["disclosed_version"] is None
        assert flash["display_name"] == "deepseek-flash"
        assert flash["compatibility_of"] is None
        model_catalog.ensure_callable("custom", "deepseek-flash")
    finally:
        model_catalog.clear_discovery("custom")


def test_disclosed_version_requires_evidence():
    with pytest.raises(ValueError, match="evidence"):
        ModelCatalog.from_mapping(
            {
                "version": 1,
                "verified_at": "2026-09-12",
                "providers": {
                    "fake": {
                        "models": [
                            {
                                "id": "x",
                                "display_name": "X",
                                "lifecycle": "stable",
                                "disclosed_version": "Secret Sauce",
                                "thinking_control": None,
                            }
                        ]
                    }
                },
            }
        )


def test_official_compatibility_cannot_be_attached_to_custom_manifest():
    with pytest.raises(ValueError, match="custom"):
        ModelCatalog.from_mapping(
            {
                "version": 1,
                "verified_at": "2026-09-12",
                "providers": {
                    "custom": {
                        "allow_undiscovered_models": True,
                        "models": [
                            {
                                "id": "local-model",
                                "display_name": "local-model",
                                "lifecycle": "unverified",
                                "thinking_control": None,
                            },
                            {
                                "id": "old-id",
                                "display_name": "old-id",
                                "lifecycle": "deprecated",
                                "compatibility_of": "local-model",
                                "compatibility_scope": "official_direct",
                                "thinking_control": None,
                            },
                        ]
                    }
                },
            }
        )


def test_models_endpoint_keeps_saved_alias_and_exposes_preferred_id(
    app_client,
    monkeypatch,
):
    from app.routers import providers

    providers.provider_model_cache.clear("deepseek")

    async def discover(*, api_key=None):
        return json.loads(FIXTURE.read_text(encoding="utf-8"))["data"]

    monkeypatch.setattr(deepseek_service, "list_models", discover)
    try:
        response = app_client.get("/api/providers/deepseek/models?refresh=true")
        assert response.status_code == 200
        payload = response.json()
        by_id = {row["id"]: row for row in payload["catalog"]}
        assert payload["preferred_model_id"] == "deepseek-flash"
        assert by_id["deepseek-flash"]["request_id"] == "deepseek-flash"
        assert by_id["deepseek-v4-flash"]["id"] == "deepseek-v4-flash"
        assert by_id["deepseek-v4-flash"]["lifecycle"] == "unavailable"
        assert by_id["deepseek-v4-flash"]["callable"] is False
        assert payload["models"] == ["deepseek-flash", "deepseek-v4-pro"]
    finally:
        model_catalog.clear_discovery("deepseek")
