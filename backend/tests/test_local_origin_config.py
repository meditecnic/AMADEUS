"""Origins must match the current runtime and its configured frontend port."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
from fastapi.testclient import TestClient
from starlette.applications import Starlette
from starlette.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse
from starlette.routing import Route, WebSocketRoute
from starlette.websockets import WebSocketDisconnect

from app.security import local_transport as transport


@pytest.mark.parametrize('frozen,port,expected', [
    (True, 1420, {'http://tauri.localhost'}),
    (False, 1420, {'http://127.0.0.1:1420', 'http://localhost:1420'}),
    (False, 1422, {'http://127.0.0.1:1422', 'http://localhost:1422'}),
])
def test_exact_origin_set(frozen, port, expected):
    assert transport.local_origins(frozen, port) == expected


@pytest.fixture(params=[(False, 1420), (False, 1422), (True, 1420)])
def origin_client(request, monkeypatch):
    origins = transport.local_origins(*request.param)
    monkeypatch.setattr(transport, 'LOCAL_ORIGINS', origins)
    monkeypatch.setattr(transport, 'LOCAL_API_TOKEN', b'')
    calls = []

    async def write(request):
        calls.append(request.method)
        return JSONResponse({'ok': True})

    async def websocket(ws):
        if await transport.accept_local_websocket(ws):
            await ws.send_json({'ok': True})
            await ws.close()

    app = Starlette(routes=[Route('/write', write, methods=['POST']), WebSocketRoute('/ws', websocket)])
    app.add_middleware(transport.HTTPOriginGuardMiddleware)
    app.add_middleware(CORSMiddleware, allow_origins=list(origins), allow_methods=['*'])
    with TestClient(app) as client:
        yield client, origins, calls


@pytest.mark.parametrize('origin', [
    'http://localhost', 'http://127.0.0.1', 'tauri://localhost',
    'https://tauri.localhost', 'http://127.0.0.1:1424', 'http://127.0.0.1:8000',
])
def test_removed_origins_rejected_by_http_ws_and_cors(origin_client, origin):
    client, _, calls = origin_client
    headers = {'origin': origin}
    assert client.post('/write', headers=headers).status_code == 403
    assert calls == []
    with client.websocket_connect('/ws', headers=headers) as ws:
        with pytest.raises(WebSocketDisconnect) as rejected:
            ws.receive_json()
        assert rejected.value.code == 4403
    preflight = client.options('/write', headers={**headers, 'access-control-request-method': 'POST'})
    assert 'access-control-allow-origin' not in preflight.headers


def test_only_current_runtime_origins_work(origin_client):
    client, origins, calls = origin_client
    candidates = {'http://tauri.localhost', 'http://127.0.0.1:1420', 'http://localhost:1420',
                  'http://127.0.0.1:1422', 'http://localhost:1422'}
    for origin in candidates:
        headers = {'origin': origin}
        allowed = origin in origins
        assert client.post('/write', headers=headers).status_code == (200 if allowed else 403)
        with client.websocket_connect('/ws', headers=headers) as ws:
            if allowed:
                assert ws.receive_json() == {'ok': True}
            else:
                with pytest.raises(WebSocketDisconnect) as rejected:
                    ws.receive_json()
                assert rejected.value.code == 4403
        preflight = client.options('/write', headers={**headers, 'access-control-request-method': 'POST'})
        assert preflight.headers.get('access-control-allow-origin') == (origin if allowed else None)
    assert calls == ['POST'] * len(origins)
    assert client.post('/write').status_code == 200


@pytest.mark.parametrize('value', [None, '1420', '1422', '1', '65535',
                                       ' 8 ', '+5', '1_0', '-1', '65536', '', '0', '8\n'])
@pytest.mark.parametrize('frozen', [False, True])
def test_frontend_port_import_validation(tmp_path, value, frozen):
    env = dict(os.environ, AMADEUS_DATA_DIR=str(tmp_path / 'data'), PYTHON_DOTENV_DISABLED='1',
               FRONTEND_PORT='1499')
    env.pop('AMADEUS_DB_PATH', None)
    env.pop('AMADEUS_FRONTEND_PORT', None)
    if value is not None:
        env['AMADEUS_FRONTEND_PORT'] = value
    code = ('import sys, json; sys.frozen = ' + repr(frozen) + '; '
            'from app import config; from app.security.local_transport import LOCAL_ORIGINS; '
            'print(json.dumps([config.FRONTEND_PORT, sorted(LOCAL_ORIGINS)]))')
    result = subprocess.run([sys.executable, '-c', code], env=env,
                            cwd=Path(__file__).resolve().parents[1],
                            capture_output=True, text=True, encoding='utf-8', timeout=20)
    if value in (None, '1420', '1422', '1', '65535'):
        assert result.returncode == 0, result.stderr
        port = int(value) if value is not None else 1420
        assert json.loads(result.stdout) == [port, sorted(transport.local_origins(frozen, port))]
    else:
        assert result.returncode != 0
        assert 'ValueError: AMADEUS_FRONTEND_PORT' in result.stderr
