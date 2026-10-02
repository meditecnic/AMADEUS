from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.main import app
from app.routers.chat_ws import sessions
from app.services.tts_queue import TTSQueueManager, TTSServiceError


@pytest.fixture(autouse=True)
def isolated_transport_app(tmp_path, monkeypatch):
    from app.db import reset_initialization_cache

    monkeypatch.delenv('AMADEUS_DB_PATH', raising=False)
    monkeypatch.setenv('AMADEUS_DATA_DIR', str(tmp_path))
    reset_initialization_cache()
    monkeypatch.setattr('app.main.sidecar_supervisor.ensure_available', AsyncMock(return_value=SimpleNamespace(url=None)))
    monkeypatch.setattr('app.main.sidecar_supervisor.close', AsyncMock())
    monkeypatch.setattr('app.main.search_service.start', AsyncMock())
    monkeypatch.setattr('app.main.search_service.close', AsyncMock())


@pytest.mark.parametrize('endpoint', ['chat', 'voice'])
@pytest.mark.parametrize('origin', [None, 'null', 'https://evil.example', 'http://localhost:1420.evil.example'])
def test_websocket_rejects_untrusted_origin_before_session(endpoint, origin, monkeypatch):
    monkeypatch.delenv('AMADEUS_ALLOW_MISSING_WS_ORIGIN', raising=False)
    sid = f'origin-reject-{uuid4().hex}'
    headers = {'host': 'localhost'} if origin is None else {'host': 'localhost', 'origin': origin}
    with TestClient(app, base_url='http://localhost') as client:
        with client.websocket_connect(f'/ws/{endpoint}?session_id={sid}', headers=headers) as ws:
            ws.send_json({'type': 'ping'})
            with pytest.raises(WebSocketDisconnect) as rejected:
                ws.receive_json()
    assert rejected.value.code == 4403
    assert sid not in sessions


@pytest.mark.parametrize('endpoint', ['chat', 'voice'])
@pytest.mark.parametrize('origin', ['http://localhost:1420', 'http://127.0.0.1:1422', 'http://localhost:1424', 'tauri://localhost', 'http://tauri.localhost', 'https://tauri.localhost'])
def test_websocket_accepts_local_origins(endpoint, origin):
    sid = f'origin-allow-{uuid4().hex}'
    with TestClient(app, base_url='http://localhost') as client:
        with client.websocket_connect(f'/ws/{endpoint}?session_id={sid}', headers={'host': 'localhost', 'origin': origin}):
            pass


def test_http_rejects_foreign_host():
    with TestClient(app, base_url='http://localhost') as client:
        response = client.get('/health', headers={'host': 'evil.example'})
    assert response.status_code == 400


@pytest.mark.parametrize('origin', [None, 'https://evil.example'])
def test_missing_origin_opt_in_does_not_allow_foreign_origin(origin, monkeypatch):
    monkeypatch.setenv('AMADEUS_ALLOW_MISSING_WS_ORIGIN', '1')
    headers = {'host': 'localhost'} if origin is None else {'host': 'localhost', 'origin': origin}
    with TestClient(app, base_url='http://localhost') as client:
        with client.websocket_connect(f'/ws/chat?session_id={uuid4().hex}', headers=headers) as ws:
            if origin is not None:
                with pytest.raises(WebSocketDisconnect) as rejected:
                    ws.receive_json()
                assert rejected.value.code == 4403


REF = {'ref_audio_path': 'synthetic.wav', 'prompt_text': 'こんにちは'}


@pytest.mark.parametrize('url', ['http://evil.example:9880', 'http://127.1:9880', 'http://2130706433:9880', 'file:///tmp/tts', 'http://user@localhost:9880', 'http://localhost:99999', 'http://localhost:9880?next=evil', 'http://localhost:9880#fragment'])
@pytest.mark.parametrize('streaming', [False, True])
async def test_tts_rejects_unsafe_urls_before_request(url, streaming, monkeypatch):
    monkeypatch.delenv('AMADEUS_ALLOW_REMOTE_SOVITS', raising=False)
    manager = TTSQueueManager()
    manager.emotion_ref_map = {'neutral': REF}
    manager.client = SimpleNamespace(is_closed=False, post=AsyncMock(return_value=SimpleNamespace(status_code=200, content=b'audio')), stream=MagicMock(side_effect=AssertionError('unexpected request')))
    with pytest.raises(TTSServiceError):
        if streaming:
            async for _ in manager.stream_pcm('こんにちは。', 'neutral', sovits_url=url):
                pass
        else:
            await manager._async_tts_call('こんにちは。', REF, sovits_url=url)
    manager.client.post.assert_not_awaited()
    manager.client.stream.assert_not_called()


@pytest.mark.parametrize('url,remote', [('http://localhost:9880', False), ('http://127.0.0.1:9881', False), ('http://[::1]:9880', False), ('https://tts.example:9880', True)])
async def test_tts_local_and_explicit_remote_control(url, remote, monkeypatch):
    monkeypatch.setenv('AMADEUS_ALLOW_REMOTE_SOVITS', '1' if remote else '0')
    manager = TTSQueueManager()
    manager.client = SimpleNamespace(post=AsyncMock(return_value=SimpleNamespace(status_code=200, content=b'audio')))
    assert await manager._async_tts_call('こんにちは。', REF, sovits_url=url) == b'audio'
    assert str(manager.client.post.call_args.args[0]) == url + '/tts'
