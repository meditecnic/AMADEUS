"""Optional launcher authentication covers every route before side effects."""

import os
import re
import secrets
import subprocess
import sys
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import httpx
import pytest
from starlette.websockets import WebSocketDisconnect

from app.main import app
from app.security import local_transport


REQUIRED = {'detail': {'code': 'local_auth_required'}}


@pytest.fixture(autouse=True)
def isolated_model_discovery(monkeypatch):
    from app.services.model_catalog import model_catalog

    monkeypatch.setattr(model_catalog, '_discovered', {})
    monkeypatch.setattr(model_catalog, '_discovered_metadata', {})


@pytest.fixture
def local_token(monkeypatch):
    token = secrets.token_urlsafe(32)
    monkeypatch.setattr(local_transport, 'LOCAL_API_TOKEN', token.encode())
    return token


@pytest.fixture
def token_client(local_token, app_client):
    return app_client


def _http_routes(routes):
    for route in routes:
        if hasattr(route, 'original_router'):
            yield from _http_routes(route.original_router.routes)
        else:
            yield route


def test_every_http_route_requires_token(token_client):
    checked = set()
    for route in _http_routes(app.routes):
        for method in getattr(route, 'methods', ()):
            if method in {'GET', 'HEAD'} and route.path.startswith('/api/asset-pack/'):
                continue
            path = re.sub(r'\{[^}]+\}', '1', route.path)
            response = token_client.request(method, path)
            assert response.status_code == 401, (method, path, response.status_code)
            if method != 'HEAD':
                assert response.json() == REQUIRED
            checked.add((method, route.path))
    assert {('GET', '/health'), ('GET', '/docs'), ('GET', '/openapi.json'),
            ('GET', '/api/conversations'), ('POST', '/api/conversations')} <= checked


def test_unknown_paths_and_methods_require_token(token_client):
    for path in ('/health', '/__missing'):
        for method in ('GET', 'HEAD', 'POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS', 'TRACE', 'CONNECT'):
            assert token_client.request(method, path).status_code == 401


@pytest.mark.parametrize('headers', [
    {}, {'authorization': 'Bearer wrong'}, {'authorization': 'Basic wrong'},
    [('authorization', 'Bearer wrong'), ('authorization', 'Bearer wrong')],
])
def test_invalid_http_auth_is_generic(token_client, headers):
    response = token_client.get('/health', headers=headers)
    assert response.status_code == 401
    assert response.json() == REQUIRED
    assert response.headers['www-authenticate'] == 'Bearer'


def test_duplicate_correct_tokens_rejected(token_client, local_token):
    response = token_client.get('/health', headers=[
        ('authorization', f'Bearer {local_token}'),
        ('authorization', f'Bearer {local_token}'),
    ])
    assert response.status_code == 401
    assert response.json() == REQUIRED


def test_correct_http_token_and_origin_guard(token_client, local_token):
    headers = {'authorization': f'Bearer {local_token}'}
    assert token_client.get('/health', headers=headers).json() == {'status': 'ok'}
    response = token_client.post('/api/settings', json={}, headers={
        **headers, 'origin': 'https://evil.example',
    })
    assert response.status_code == 403


def test_preflight_only_is_exempt(token_client):
    response = token_client.options('/api/conversations', headers={
        'origin': 'http://localhost:1420', 'access-control-request-method': 'POST',
        'access-control-request-headers': 'authorization',
    })
    assert response.status_code == 200
    assert response.headers['access-control-allow-origin'] == 'http://localhost:1420'
    assert token_client.options('/health').status_code == 401
    assert token_client.options('/health', headers={
        'access-control-request-method': 'GET', 'origin': '',
    }).status_code == 400
    assert token_client.options('/health', headers={
        'access-control-request-method': 'GET', 'origin': 'https://evil.example',
    }).status_code == 400
    del token_client.headers['origin']
    assert token_client.options('/health', headers={
        'access-control-request-method': 'GET',
    }).status_code == 401


def test_asset_reads_keep_host_guard(token_client, tmp_path, monkeypatch):
    monkeypatch.setenv('AMADEUS_ASSET_PACK_DIR', str(tmp_path))
    (tmp_path / 'example.md').write_text('synthetic asset', encoding='utf-8')
    response = token_client.get('/api/asset-pack/example.md')
    assert response.status_code == 200
    assert response.text == 'synthetic asset'
    # FastAPI's GET-only asset route currently answers HEAD with 405, not 401.
    assert token_client.head('/api/asset-pack/example.md').status_code == 405
    for method in ('GET', 'HEAD'):
        assert token_client.request(method, '/api/asset-pack/example.md', headers={
            'host': 'evil.example',
        }).status_code == 400
    assert token_client.post('/api/asset-pack/example.md').status_code == 401
    assert token_client.get('/api/asset-pack').status_code == 401
    assert token_client.get('/api/asset-pack-other/example.md').status_code == 401


def test_http_rejection_prevents_database_network_and_process_effects(token_client, local_token, monkeypatch):
    from app.db import get_db
    from app.services.conversations import conversation_service

    owner = 'token-effects-' + uuid4().hex

    async def count_conversations():
        db = await get_db('steins_gate', 'history')
        try:
            row = await (await db.execute(
                'SELECT count(*) FROM conversations WHERE session_id=?', (owner,),
            )).fetchone()
            return row[0]
        finally:
            await db.close()

    assert token_client.portal.call(count_conversations) == 0
    create = AsyncMock(wraps=conversation_service.create)
    monkeypatch.setattr(conversation_service, 'create', create)
    outbound = AsyncMock(return_value=httpx.Response(
        200, json={'data': [{'id': 'deepseek-flash'}, {'id': 'deepseek-v4-flash'}]},
        request=httpx.Request('GET', 'https://provider.example/models'),
    ))
    monkeypatch.setattr(httpx.AsyncClient, 'send', outbound)
    sidecar = AsyncMock()
    monkeypatch.setattr('app.main.sidecar_supervisor.ensure_available', sidecar)
    spawn = MagicMock(side_effect=AssertionError('unauthorized request spawned process'))
    monkeypatch.setattr(subprocess, 'Popen', spawn)
    params = {'session_id': owner}
    for method, path, kwargs in [
        ('GET', '/api/conversations', {'params': params}),
        ('GET', '/api/providers/deepseek/models', {'params': {'refresh': 'true'}}),
        ('GET', '/health/dependencies', {}),
        ('POST', '/api/conversations', {'json': params}),
    ]:
        assert token_client.request(method, path, **kwargs).status_code == 401
    assert token_client.portal.call(count_conversations) == 0
    create.assert_not_awaited()
    outbound.assert_not_awaited()
    sidecar.assert_not_awaited()
    spawn.assert_not_called()
    headers = {'authorization': f'Bearer {local_token}'}
    assert token_client.get('/api/conversations', params=params, headers=headers).status_code == 200
    assert token_client.portal.call(count_conversations) == 1
    assert token_client.get('/api/providers/deepseek/models', params={'refresh': 'true'}, headers=headers).status_code == 200
    outbound.assert_awaited_once()
    assert token_client.post('/api/conversations', json={
        **params, 'model_id': 'deepseek-v4-flash',
    }, headers=headers).status_code == 201
    create.assert_awaited()


@pytest.mark.parametrize('endpoint', ['chat', 'voice'])
@pytest.mark.parametrize('offered', ['missing', 'wrong', 'no-version'])
def test_ws_requires_token_before_session_or_database(token_client, local_token, monkeypatch, endpoint, offered):
    from app.routers.chat_ws import sessions

    protocols = {'missing': [], 'wrong': ['amadeus.local.v1', 'amadeus.token.wrong'],
                 'no-version': [f'amadeus.token.{local_token}']}[offered]
    sid = 'token-reject-' + uuid4().hex
    with monkeypatch.context() as mp:
        session = MagicMock(side_effect=AssertionError('unauthorized session'))
        db = AsyncMock(side_effect=AssertionError('unauthorized database read'))
        lease = AsyncMock(side_effect=AssertionError('unauthorized lease'))
        mp.setattr('app.routers.chat_ws.SessionState', session)
        mp.setattr('app.db.get_db', db)
        mp.setattr('app.models.get_db', db)
        mp.setattr('app.routers.voice_ws.session_coordinator.acquire_lease', lease)
        with token_client.websocket_connect(f'/ws/{endpoint}?session_id={sid}', subprotocols=protocols) as ws:
            with pytest.raises(WebSocketDisconnect) as rejected:
                ws.receive_json()
        assert rejected.value.code == 4401
        assert sid not in sessions
        session.assert_not_called()
        db.assert_not_awaited()
        lease.assert_not_awaited()


@pytest.mark.parametrize('endpoint', ['chat', 'voice'])
def test_ws_origin_still_rejected_first(token_client, endpoint):
    with token_client.websocket_connect(f'/ws/{endpoint}', headers={'origin': 'https://evil.example'}) as ws:
        with pytest.raises(WebSocketDisconnect) as rejected:
            ws.receive_json()
    assert rejected.value.code == 4403


@pytest.mark.parametrize('endpoint', ['chat', 'voice'])
def test_ws_echoes_public_protocol_and_exchanges_messages(token_client, local_token, endpoint, monkeypatch):
    monkeypatch.setattr('app.config.DEEPSEEK_MODEL', 'deepseek-v4-flash')
    with token_client.websocket_connect(f'/ws/{endpoint}?session_id={uuid4().hex}', subprotocols=[
        'amadeus.local.v1', f'amadeus.token.{local_token}',
    ]) as ws:
        assert ws.accepted_subprotocol == 'amadeus.local.v1'
        ws.send_json({'type': 'auth', 'protocol_version': 2, 'conversation_mode': 'draft',
                      'model': 'deepseek-v4-flash', 'enable_tts': False})
        assert ws.receive_json()['type'] == ('session.ready' if endpoint == 'chat' else 'voice.ready')
        ws.send_json({'type': 'voice.stop' if endpoint == 'chat' else 'ping'})
        assert ws.receive_json()['type'] == ('status' if endpoint == 'chat' else 'pong')


@pytest.mark.parametrize('configured', [False, True])
def test_startup_consumes_environment_and_never_logs_token(tmp_path, configured):
    token = secrets.token_urlsafe(32)
    env = {**os.environ, 'AMADEUS_LOCAL_API_TOKEN': token if configured else '',
           'AMADEUS_DATA_DIR': str(tmp_path / 'data')}
    env.pop('AMADEUS_DB_PATH', None)
    code = '''
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient
from app.main import app
assert 'AMADEUS_LOCAL_API_TOKEN' not in os.environ
with patch('app.main.sidecar_supervisor.ensure_available', AsyncMock(return_value=SimpleNamespace(url=None))), patch('app.main.sidecar_supervisor.close', AsyncMock()):
    with TestClient(app, base_url='http://localhost') as client:
        assert client.get('/health').status_code == EXPECTED
'''.replace('EXPECTED', '401' if configured else '200')
    result = subprocess.run([sys.executable, '-c', code], env=env, capture_output=True, text=True,
                            encoding='utf-8', timeout=30)
    assert result.returncode == 0, result.stderr
    assert token not in result.stdout + result.stderr


def test_rejections_never_disclose_token(token_client, local_token, caplog, capsys):
    response = token_client.get('/health', headers={'authorization': f'Basic {local_token}'})
    assert response.json() == REQUIRED
    with token_client.websocket_connect('/ws/chat', subprotocols=[f'amadeus.token.{local_token}']) as ws:
        with pytest.raises(WebSocketDisconnect) as rejected:
            ws.receive_json()
    assert rejected.value.code == 4401
    output = capsys.readouterr()
    assert local_token not in response.text + output.out + output.err + caplog.text + str(rejected.value)


@pytest.mark.parametrize('protocols', [[], ['amadeus.local.v1', 'amadeus.token.ignored']])
def test_unconfigured_preserves_http_and_ws(app_client, monkeypatch, protocols):
    monkeypatch.setattr(local_transport, 'LOCAL_API_TOKEN', b'')
    assert app_client.get('/health', headers={'authorization': 'Basic ignored'}).status_code == 200
    with app_client.websocket_connect('/ws/chat?session_id=' + uuid4().hex, subprotocols=protocols) as ws:
        assert ws.accepted_subprotocol == ('amadeus.local.v1' if protocols else None)
        ws.send_json({'type': 'auth', 'protocol_version': 2, 'conversation_mode': 'draft',
                      'model': 'deepseek-v4-flash', 'enable_tts': False})
        assert ws.receive_json()['type'] == 'session.ready'


async def test_http_rejects_before_reading_body(local_token):
    downstream = AsyncMock()
    receive = AsyncMock(side_effect=AssertionError('unauthorized body read'))
    send = AsyncMock()
    await local_transport.LocalAPITokenMiddleware(downstream)({
        'type': 'http', 'method': 'POST', 'path': '/api/conversations', 'headers': [],
    }, receive, send)
    downstream.assert_not_awaited()
    receive.assert_not_awaited()
    assert send.await_args_list[0].args[0]['status'] == 401
