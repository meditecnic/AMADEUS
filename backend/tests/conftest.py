from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def isolated_provider_credentials(monkeypatch):
    """Keep tests out of the user's real Windows Credential Manager."""
    from app.routers import chat_ws, providers, voice_ws
    from app.services import credentials
    from app.services.credentials import InMemoryCredentialStore

    store = InMemoryCredentialStore()
    store.set("deepseek", "test-provider-credential")
    monkeypatch.setattr(credentials, "credential_store", store)
    monkeypatch.setattr(chat_ws, "credential_store", store)
    monkeypatch.setattr(voice_ws, "credential_store", store)
    monkeypatch.setattr(providers, "credential_store", store)
    providers.provider_model_cache.clear()
    return store


@pytest.fixture
def app_client(isolated_provider_credentials, tmp_path, monkeypatch):
    """Run the ASGI app in a managed portal without launching a TTS sidecar."""
    from app.main import app
    from app.db import reset_initialization_cache

    monkeypatch.delenv('AMADEUS_DB_PATH', raising=False)
    monkeypatch.setenv('AMADEUS_DATA_DIR', str(tmp_path))
    reset_initialization_cache()

    with patch(
        "app.main.sidecar_supervisor.ensure_available",
        new=AsyncMock(return_value=SimpleNamespace(url=None)),
    ), patch(
        "app.main.sidecar_supervisor.close",
        new=AsyncMock(),
    ):
        with TestClient(app, base_url="http://localhost", headers={"host": "localhost", "origin": "http://localhost:1420"}) as client:
            yield client
    reset_initialization_cache()


@pytest.fixture(autouse=True)
def isolated_speech_detector(monkeypatch):
    """Endpoint tests use synthetic speech detection without model downloads."""
    monkeypatch.setattr("app.services.speech.speech_service.has_speech", AsyncMock(return_value=True))
