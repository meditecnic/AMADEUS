import os
import shutil
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# User-directory isolation (audit P0).
#
# Some paths are resolved at import time (for example
# app.config.DEFAULT_REF_AUDIO_DIR and speech_service._cache_dir both call
# get_data_root() while the module is imported). Test modules import app code
# during collection, before any fixture runs, so the environment must already
# point at a throwaway location when this conftest is imported. This module is
# loaded by pytest before any test module is collected.
# ---------------------------------------------------------------------------
_ISOLATED_ENV_KEYS = (
    "AMADEUS_DATA_DIR",
    "HOME",
    "USERPROFILE",  # Path.home() on Windows
    "APPDATA",  # get_data_root() fallback on Windows
    "LOCALAPPDATA",
)
_ORIGINAL_ENV = {key: os.environ.get(key) for key in _ISOLATED_ENV_KEYS}
_SESSION_ROOT = Path(tempfile.mkdtemp(prefix="amadeus-tests-"))
_SESSION_HOME = _SESSION_ROOT / "home"
_SESSION_ENV = {
    "AMADEUS_DATA_DIR": str(_SESSION_ROOT / "data"),
    "HOME": str(_SESSION_HOME),
    "USERPROFILE": str(_SESSION_HOME),
    "APPDATA": str(_SESSION_HOME / "AppData" / "Roaming"),
    "LOCALAPPDATA": str(_SESSION_HOME / "AppData" / "Local"),
}
for _value in _SESSION_ENV.values():
    Path(_value).mkdir(parents=True, exist_ok=True)
os.environ.update(_SESSION_ENV)


def pytest_unconfigure(config):
    for key, value in _ORIGINAL_ENV.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
    shutil.rmtree(_SESSION_ROOT, ignore_errors=True)


@pytest.fixture(autouse=True)
def isolated_user_dirs(tmp_path_factory):
    """Point the data root and every home-directory variable at fresh temp dirs.

    Defined first so it is set up before any fixture that requests
    ``monkeypatch`` and torn down after it. It uses its own MonkeyPatch so a
    test calling ``monkeypatch.undo()`` cannot remove this isolation.
    """
    data_dir = tmp_path_factory.mktemp("amadeus-data")
    home_dir = tmp_path_factory.mktemp("home")
    env = {
        "AMADEUS_DATA_DIR": str(data_dir),
        "HOME": str(home_dir),
        "USERPROFILE": str(home_dir),
        "APPDATA": str(home_dir / "AppData" / "Roaming"),
        "LOCALAPPDATA": str(home_dir / "AppData" / "Local"),
    }
    mp = pytest.MonkeyPatch()
    try:
        for key, value in env.items():
            mp.setenv(key, value)
        yield data_dir
    finally:
        mp.undo()


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
