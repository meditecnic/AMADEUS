"""Packaged startup must fail before app imports; source runs warn without a token."""
import importlib.util
import logging
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest

ENTRY_PATH = Path(__file__).resolve().parents[2] / 'packaging/windows/backend_entry.py'


@pytest.fixture
def entry(monkeypatch):
    spec = importlib.util.spec_from_file_location('local_token_backend_entry', ENTRY_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module.sys, 'argv', [str(ENTRY_PATH)])
    return module


@pytest.mark.parametrize('self_check', [False, True])
@pytest.mark.parametrize('token', [None, ''])
def test_packaged_entry_requires_token_before_configuration_or_app_import(entry, monkeypatch, capsys, token, self_check):
    if self_check:
        monkeypatch.setattr(entry.sys, 'argv', [str(ENTRY_PATH), '--self-check'])
    if token is None:
        monkeypatch.delenv('AMADEUS_LOCAL_API_TOKEN', raising=False)
    else:
        monkeypatch.setenv('AMADEUS_LOCAL_API_TOKEN', token)
    configure = Mock(side_effect=AssertionError('must refuse before imports'))
    serve = AsyncMock(side_effect=AssertionError('must not serve'))
    monkeypatch.setattr(entry, 'configure_environment', configure)
    monkeypatch.setattr(entry, 'serve', serve)
    assert entry.main() != 0
    captured = capsys.readouterr()
    assert captured.out == ''
    assert captured.err == 'AMADEUS_LOCAL_API_TOKEN required for the packaged backend\n'
    configure.assert_not_called()
    serve.assert_not_called()


def test_packaged_entry_with_token_reaches_server(entry, monkeypatch, capsys):
    monkeypatch.setenv('AMADEUS_LOCAL_API_TOKEN', 'synthetic-entry-token')
    monkeypatch.delenv('AMADEUS_PARENT_PID', raising=False)
    monkeypatch.setenv('BACKEND_PORT', '8000')
    configure = Mock()
    thread = Mock()
    serve = AsyncMock(return_value=0)
    monkeypatch.setattr(entry, 'configure_environment', configure)
    monkeypatch.setattr(entry.threading, 'Thread', thread)
    monkeypatch.setattr(entry, 'serve', serve)
    assert entry.main() == 0
    configure.assert_called_once()
    serve.assert_awaited_once()
    assert serve.call_args.args[1] == 8000
    captured = capsys.readouterr()
    assert 'synthetic-entry-token' not in captured.out + captured.err


def test_source_startup_warns_when_local_authentication_is_disabled(monkeypatch, caplog, app_client):
    # app_client has entered the actual FastAPI lifespan without a token.
    assert any(record.levelno == logging.WARNING
               and 'HTTP and WebSocket authentication is disabled' in record.message
               and 'development' in record.message for record in caplog.get_records('setup'))
    assert app_client.get('/health').status_code == 200


def test_packaged_self_check_with_token_reaches_check(entry, monkeypatch):
    monkeypatch.setenv('AMADEUS_LOCAL_API_TOKEN', 'synthetic-check-token')
    monkeypatch.setattr(entry.sys, 'argv', [str(ENTRY_PATH), '--self-check'])
    configure = Mock()
    check = Mock(return_value=0)
    monkeypatch.setattr(entry, 'configure_environment', configure)
    monkeypatch.setattr(entry, 'self_check', check)
    assert entry.main() == 0
    configure.assert_called_once()
    check.assert_called_once()
