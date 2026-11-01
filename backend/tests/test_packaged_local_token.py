"""Packaged startup must fail before app imports; source runs warn without a token."""
import importlib.util
import asyncio
import builtins
import json
import logging
import os
from pathlib import Path
import socket
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from starlette.applications import Starlette

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


@pytest.fixture
def port_runtime(entry, monkeypatch):
    """Run the actual socket/uvicorn startup with an empty app and no Windows watchers."""
    monkeypatch.setenv('AMADEUS_LOCAL_API_TOKEN', 'synthetic-port-token')
    monkeypatch.delenv('AMADEUS_PARENT_PID', raising=False)
    monkeypatch.setattr(entry, 'configure_environment', Mock())
    monkeypatch.setattr(entry, 'threading', SimpleNamespace(Event=entry.threading.Event, Thread=Mock()))
    monkeypatch.setitem(sys.modules, 'app.main', SimpleNamespace(app=Starlette()))
    actual_serve = entry.serve
    requested_ports = []
    ready = []
    state = {}

    async def serve(shutdown, port):
        state['shutdown'] = shutdown
        requested_ports.append(port)
        return await asyncio.wait_for(actual_serve(shutdown, port), timeout=5)

    def capture(*args, **kwargs):
        builtins.print(*args, **kwargs)
        if args and args[0].startswith('AMADEUS_BACKEND_READY '):
            payload = json.loads(args[0].split(' ', 1)[1])
            ready.append(payload)
            # READY is emitted only once uvicorn has begun listening.
            with socket.create_connection(('127.0.0.1', payload['port']), timeout=1):
                pass
            state['shutdown'].set()

    monkeypatch.setattr(entry, 'serve', serve)
    monkeypatch.setattr(entry, 'print', capture, raising=False)
    return requested_ports, ready


@pytest.mark.parametrize('value', [None, '0'])
def test_packaged_auto_port_publishes_actual_socket_and_version(entry, port_runtime, monkeypatch, capsys, value):
    if value is None:
        monkeypatch.delenv('BACKEND_PORT', raising=False)
    else:
        monkeypatch.setenv('BACKEND_PORT', value)
    assert entry.main() == 0
    requested, ready = port_runtime
    assert requested == [0]
    assert len(ready) == 1
    assert ready[0]['version'] == 1
    assert ready[0]['pid'] == os.getpid()
    assert 1 <= ready[0]['port'] <= 65535
    captured = capsys.readouterr()
    assert 'synthetic-port-token' not in captured.out + captured.err


def test_packaged_fixed_port_is_used(entry, port_runtime, monkeypatch):
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        port = probe.getsockname()[1]
    monkeypatch.setenv('BACKEND_PORT', str(port))
    assert entry.main() == 0
    requested, ready = port_runtime
    assert requested == [port]
    assert ready == [{'version': 1, 'port': port, 'pid': os.getpid()}]


def test_packaged_fixed_port_conflict_fails_without_ready(entry, port_runtime, monkeypatch, capsys):
    with socket.socket() as listener:
        if os.name == 'nt':
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        listener.bind(('127.0.0.1', 0))
        listener.listen()
        port = listener.getsockname()[1]
        monkeypatch.setenv('BACKEND_PORT', str(port))
        assert entry.main() != 0
    requested, ready = port_runtime
    assert requested == [port]
    assert ready == []
    captured = capsys.readouterr()
    assert 'AMADEUS_BACKEND_FAILED' in captured.out
    assert 'AMADEUS_BACKEND_READY' not in captured.out
    assert 'synthetic-port-token' not in captured.out + captured.err


@pytest.mark.parametrize('value', [' 8 ', '+5', '1_0', '-1', '65536', '', 'abc', '8\n'])
def test_packaged_invalid_port_fails(entry, port_runtime, monkeypatch, capsys, value):
    monkeypatch.setenv('BACKEND_PORT', value)
    assert entry.main() != 0
    assert port_runtime == ([], [])
    captured = capsys.readouterr()
    assert 'AMADEUS_BACKEND_FAILED' in captured.out
    assert 'AMADEUS_BACKEND_READY' not in captured.out
