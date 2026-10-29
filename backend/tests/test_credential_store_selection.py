"""Select credential backends without constructing a real Windows store."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

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
