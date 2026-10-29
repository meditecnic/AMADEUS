"""Select credential backends without constructing a real Windows store."""

import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from dotenv import dotenv_values

from app.services import credentials


@pytest.mark.parametrize("backend, platform, frozen, windows", [
    pytest.param(None, "win32", False, False, id="source-win32-unset"),
    pytest.param("", "win32", False, False, id="source-win32-empty"),
    pytest.param(" \t", "win32", False, False, id="source-win32-blank"),
    pytest.param("unknown", "win32", False, False, id="source-win32-unknown"),
    pytest.param("windows", "win32", False, True, id="source-win32-opt-in"),
    pytest.param(" WINDOWS ", "win32", False, True, id="source-win32-normalized-opt-in"),
    pytest.param(None, "win32", True, True, id="frozen-win32-unset"),
    pytest.param("", "win32", True, True, id="frozen-win32-empty"),
    pytest.param("unknown", "win32", True, True, id="frozen-win32-unknown"),
    pytest.param("windows", "win32", True, True, id="frozen-win32-opt-in"),
    pytest.param("memory", "win32", False, False, id="source-win32-memory"),
    pytest.param("memory", "win32", True, False, id="frozen-win32-memory"),
    pytest.param("inmemory", "win32", True, False, id="frozen-win32-inmemory"),
    pytest.param("in-memory", "win32", True, False, id="frozen-win32-in-memory"),
    pytest.param(" MEMORY ", "win32", True, False, id="frozen-win32-normalized-memory"),
    pytest.param(None, "linux", False, False, id="source-linux-unset"),
    pytest.param("windows", "linux", False, False, id="source-linux-windows"),
    pytest.param(None, "linux", True, False, id="frozen-linux-unset"),
    pytest.param("windows", "linux", True, False, id="frozen-linux-windows"),
])
def test_credential_store_selection(monkeypatch, backend, platform, frozen, windows):
    if backend is None:
        monkeypatch.delenv("AMADEUS_CREDENTIAL_BACKEND", raising=False)
    else:
        monkeypatch.setenv("AMADEUS_CREDENTIAL_BACKEND", backend)
    runtime = SimpleNamespace(platform=platform)
    if frozen:
        runtime.frozen = True
    monkeypatch.setattr(credentials, "sys", runtime)
    windows_factory = Mock(return_value=object())
    monkeypatch.setattr(credentials, "WindowsCredentialStore", windows_factory)

    store = credentials._select_credential_store()

    if windows:
        assert store is windows_factory.return_value
        windows_factory.assert_called_once_with()
    else:
        assert isinstance(store, credentials.InMemoryCredentialStore)
        windows_factory.assert_not_called()


@pytest.mark.parametrize("persistent", [False, True], ids=["memory-restart", "persistent"])
async def test_lifespan_migrates_dotenv_key(monkeypatch, tmp_path, persistent):
    from app import config, main
    from app.services.provider_runtime import deepseek_service

    env_file = tmp_path / ".env"
    secret = "synthetic-legacy-provider-key"
    original = (
        "# synthetic configuration\r\n"
        f"DEEPSEEK_API_KEY={secret}\r\n"
        "DEEPSEEK_MODEL=deepseek-chat\r\n"
    ).encode("utf-8")
    env_file.write_bytes(original)
    monkeypatch.setattr(config, "env_path", env_file)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)

    class StopAfterCredentialMigration(Exception):
        pass

    # Exercise real startup migration, stopping before database/sidecar startup.
    monkeypatch.setattr(main, "init_db", AsyncMock(side_effect=StopAfterCredentialMigration))

    for _ in range(1 if persistent else 2):
        if persistent:
            values = {}
            store = Mock(spec=credentials.CredentialStore)
            store.get.side_effect = values.get
            store.set.side_effect = values.__setitem__
        else:
            store = credentials.InMemoryCredentialStore()
        monkeypatch.setattr(credentials, "credential_store", store)

        # Reload only this temporary dotenv, as config would on a fresh process.
        legacy_key = dotenv_values(env_file)["DEEPSEEK_API_KEY"]
        assert legacy_key == secret
        monkeypatch.setenv("DEEPSEEK_API_KEY", legacy_key)
        monkeypatch.setattr(config, "DEEPSEEK_API_KEY", legacy_key)
        monkeypatch.setattr(deepseek_service, "api_key", legacy_key)

        with pytest.raises(StopAfterCredentialMigration):
            async with main.lifespan(main.app):
                pytest.fail("startup should stop immediately after credential migration")

        assert store.get("deepseek") == secret
        assert "DEEPSEEK_API_KEY" not in os.environ
        assert config.DEEPSEEK_API_KEY == ""
        assert deepseek_service.api_key == ""
        if persistent:
            assert dotenv_values(env_file) == {"DEEPSEEK_MODEL": "deepseek-chat"}
        else:
            assert env_file.read_bytes() == original
