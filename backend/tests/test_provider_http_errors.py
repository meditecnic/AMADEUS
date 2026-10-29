"""HTTP provider failures disclose only fixed text or safe provider status."""

from unittest.mock import AsyncMock, Mock

import pytest

from app.routers import providers
from app.services.provider_adapters import ProviderRequestError


@pytest.fixture(params=["create", "update", "disable", "legacy"])
def provider_failure_request(request, app_client, monkeypatch):
    operation = request.param
    profile_id = "custom:11111111-1111-1111-1111-111111111111"
    settings = {
        "base_url": "http://127.0.0.1:1234/v1",
        "default_model": "test-model",
        "display_name": "Test provider",
    }
    monkeypatch.setattr(
        providers, "get_provider_configuration",
        AsyncMock(return_value={**settings, "enabled": True}),
    )
    monkeypatch.setattr(providers, "_clear_runtime_surfaces", Mock())
    method, path = {
        "create": ("POST", "/custom-profiles"),
        "update": ("PATCH", f"/custom-profiles/{profile_id}"),
        "disable": ("DELETE", f"/custom-profiles/{profile_id}"),
        "legacy": ("PATCH", "/custom/configuration"),
    }[operation]

    def submit(error):
        target = (
            "soft_disable_provider_configuration"
            if operation == "disable" else "_activate_custom_profile"
        )
        monkeypatch.setattr(providers, target, AsyncMock(side_effect=error))
        kwargs = {} if operation == "disable" else {"json": settings}
        return app_client.request(method, f"/api/providers{path}", **kwargs)

    return operation, submit


@pytest.mark.parametrize("error_type", [RuntimeError, OSError])
def test_unexpected_provider_errors_are_private(
    provider_failure_request, error_type, caplog,
):
    operation, submit = provider_failure_request
    response = submit(error_type(
        "register secret-marker-123 https://internal.invalid/private\n"
        "C:\\private\\configuration.db 参数"
    ))
    code, message = (
        ("provider_disable_failed", "Provider disable failed")
        if operation == "disable"
        else ("provider_register_failed", "Provider registration failed")
    )

    assert response.status_code == 500
    assert "secret-marker-123" not in response.text
    assert response.json() == {"detail": {"code": code, "message": message}}
    records = [record for record in caplog.records if record.name == providers.__name__]
    assert len(records) == 1
    assert error_type.__name__ in records[0].getMessage()
    assert "secret-marker-123" not in caplog.text
    assert records[0].exc_info is None


@pytest.mark.parametrize("status", [401, 403])
def test_provider_request_error_preserves_safe_status(
    provider_failure_request, status,
):
    operation, submit = provider_failure_request
    error = ProviderRequestError("custom", status)
    response = submit(error)
    code = {
        "create": "provider_persist_failed",
        "update": "provider_register_failed",
        "disable": "provider_disable_failed",
        "legacy": "provider_register_failed",
    }[operation]

    assert response.status_code == 500
    assert str(status) in response.json()["detail"]["message"]
    assert response.json() == {"detail": {"code": code, "message": str(error)}}


@pytest.mark.parametrize("text, code, message", [
    ("REGISTER failed", "provider_register_failed", "Provider registration failed"),
    ("ADAPTER failed", "provider_register_failed", "Provider registration failed"),
    ("database locked", "provider_persist_failed", "Provider persistence failed"),
])
def test_create_error_classification_is_unchanged(
    app_client, monkeypatch, text, code, message,
):
    monkeypatch.setattr(
        providers, "_activate_custom_profile",
        AsyncMock(side_effect=RuntimeError(f"{text} secret-marker-123")),
    )
    response = app_client.post("/api/providers/custom-profiles", json={
        "display_name": "Test provider",
        "base_url": "http://127.0.0.1:1234/v1",
        "default_model": "test-model",
    })

    assert response.status_code == 500
    assert "secret-marker-123" not in response.text
    assert response.json() == {"detail": {"code": code, "message": message}}
