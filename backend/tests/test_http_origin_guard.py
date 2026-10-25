"""Reject cross-site HTTP writes before routing, body reads, or side effects."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from starlette.middleware.cors import ALL_METHODS
from starlette.responses import JSONResponse
from starlette.routing import Route


WRITE_METHODS = ('POST', 'PUT', 'PATCH', 'DELETE')
ALLOWED_ORIGINS = ('http://tauri.localhost', 'http://127.0.0.1:1420')
FORBIDDEN = {'detail': {'code': 'origin_not_allowed'}}


@pytest.fixture
def http_client(app_client):
    # app_client supplies an allowed Origin by default; omission must be real.
    del app_client.headers['origin']
    return app_client


@pytest.fixture
def probe(http_client, monkeypatch):
    from app.main import app

    calls = []

    async def endpoint(request):
        calls.append(request.method)
        return JSONResponse({'ok': True})

    route = Route('/__origin_guard_probe', endpoint, methods=[*WRITE_METHODS, 'GET', 'HEAD', 'OPTIONS'])
    monkeypatch.setattr(app.router, 'routes', [*app.router.routes, route])
    return SimpleNamespace(client=http_client, calls=calls, path=route.path)


@pytest.mark.parametrize('method', WRITE_METHODS)
@pytest.mark.parametrize('origin', [None, *ALLOWED_ORIGINS])
def test_write_allows_local_and_missing_origin(probe, method, origin):
    headers = {} if origin is None else {'origin': origin}
    response = probe.client.request(method, probe.path, headers=headers)

    assert response.status_code == 200
    assert response.json() == {'ok': True}
    assert probe.calls == [method]
    if origin is not None:
        assert response.headers['access-control-allow-origin'] == origin
        assert response.headers['access-control-allow-credentials'] == 'true'


@pytest.mark.parametrize('method', WRITE_METHODS)
@pytest.mark.parametrize('headers', [
    [('origin', 'https://evil.example')],
    [('origin', 'null')],
    [('origin', '')],
    [('origin', ' http://tauri.localhost')],
    [('origin', 'http://127.0.0.1:1420.evil.example')],
    [('origin', 'http://tauri.localhost, https://evil.example')],
    [('origin', 'http://tauri.localhost https://evil.example')],
    [('origin', 'http://tauri.localhost'), ('origin', 'http://tauri.localhost')],
    [('origin', 'http://tauri.localhost'), ('origin', 'https://evil.example')],
    [('origin', 'https://evil.example'), ('origin', 'http://tauri.localhost')],
], ids=['evil', 'null', 'empty', 'whitespace', 'suffix', 'combined-comma',
        'combined-space', 'duplicate-local', 'local-then-evil', 'evil-then-local'])
def test_write_rejects_untrusted_origin_before_route(probe, method, headers):
    response = probe.client.request(method, probe.path, headers=headers)

    assert probe.calls == []
    assert response.status_code == 403
    assert response.json() == FORBIDDEN


@pytest.mark.parametrize('content_type', [
    'text/plain', 'application/x-www-form-urlencoded', 'multipart/form-data; boundary=probe',
])
def test_simple_post_content_types_cannot_bypass_guard(probe, content_type):
    response = probe.client.post(probe.path, content=b'ignored', headers={
        'origin': 'https://evil.example', 'content-type': content_type,
    })

    assert probe.calls == []
    assert response.status_code == 403
    assert response.json() == FORBIDDEN


@pytest.mark.parametrize('method', ['GET', 'HEAD', 'OPTIONS'])
def test_safe_methods_reach_route_with_foreign_origin(probe, method):
    response = probe.client.request(method, probe.path, headers={'origin': 'https://evil.example'})

    assert response.status_code == 200
    assert probe.calls == [method]
    assert 'access-control-allow-origin' not in response.headers


@pytest.mark.parametrize('origin', ALLOWED_ORIGINS)
def test_allowed_preflight_keeps_cors_headers(probe, origin):
    response = probe.client.options(probe.path, headers={
        'origin': origin,
        'access-control-request-method': 'POST',
        'access-control-request-headers': 'content-type,x-probe',
    })

    assert response.status_code == 200
    assert response.text == 'OK'
    assert response.headers['access-control-allow-origin'] == origin
    assert response.headers['access-control-allow-credentials'] == 'true'
    assert response.headers['access-control-allow-headers'] == 'content-type,x-probe'
    assert response.headers['access-control-allow-methods'] == ', '.join(ALL_METHODS)
    assert response.headers['access-control-max-age'] == '600'
    assert 'Origin' in response.headers['vary']
    assert probe.calls == []


@pytest.fixture
def self_fact(http_client):
    from app import models
    from app.services.conversations import conversation_service
    from app.services.memory import CoreFactCandidate, memory_service

    async def seed():
        owner = 'origin-guard-self'
        conversation = await conversation_service.create(
            owner, 'steins_gate', title='Origin guard', identity_mode='self',
        )
        mid = await models.save_message(
            owner, 'user', '我喜欢读书', conversation_id=str(conversation['id']),
        )
        fact_id = await memory_service.upsert_core_fact(
            owner, 'steins_gate', CoreFactCandidate(
                fact_key='hobby', fact_value='读书', confidence=.9,
                importance=.8, source_message_ids=[mid],
            ), identity_mode='self',
        )
        return owner, fact_id

    return http_client.portal.call(seed)


async def _dismissed(fact_id):
    from app.db import get_db

    db = await get_db('steins_gate', 'memory')
    try:
        row = await (await db.execute(
            'SELECT is_dismissed FROM core_facts WHERE id=?', (fact_id,),
        )).fetchone()
        return int(row['is_dismissed'])
    finally:
        await db.close()


@pytest.mark.parametrize('origin', ['https://evil.example', 'null'])
def test_delete_local_rejects_cross_site_without_mutation(http_client, self_fact, origin):
    owner, fact_id = self_fact
    assert http_client.portal.call(_dismissed, fact_id) == 0

    response = http_client.post(
        f'/api/memory/facts/{fact_id}/delete-local',
        params={'session_id': owner}, headers={'origin': origin},
    )

    assert http_client.portal.call(_dismissed, fact_id) == 0
    assert response.status_code == 403
    assert response.json() == FORBIDDEN


@pytest.mark.parametrize('origin', [None, *ALLOWED_ORIGINS])
def test_delete_local_allows_trusted_and_non_browser(http_client, self_fact, origin):
    owner, fact_id = self_fact
    assert http_client.portal.call(_dismissed, fact_id) == 0

    response = http_client.post(
        f'/api/memory/facts/{fact_id}/delete-local', params={'session_id': owner},
        headers={} if origin is None else {'origin': origin},
    )

    assert response.status_code == 200, response.text
    assert response.json() == {'id': fact_id, 'scope': 'local', 'changed': True, 'deleted': True}
    assert http_client.portal.call(_dismissed, fact_id) == 1


def test_provider_test_blocks_outbound_request(http_client, monkeypatch):
    outbound = AsyncMock(return_value=httpx.Response(
        200, json={'data': [{'id': 'deepseek-chat'}]},
        request=httpx.Request('GET', 'https://provider.example/models'),
    ))
    monkeypatch.setattr(httpx.AsyncClient, 'send', outbound)

    rejected = http_client.post('/api/providers/deepseek/test', headers={'origin': 'https://evil.example'})

    outbound.assert_not_awaited()
    assert rejected.status_code == 403
    assert rejected.json() == FORBIDDEN

    # Positive control exercises the real adapter path through the transport spy.
    allowed = http_client.post('/api/providers/deepseek/test', headers={'origin': ALLOWED_ORIGINS[0]})
    assert allowed.status_code == 200, allowed.text
    assert allowed.json() == {'provider_id': 'deepseek', 'ok': True, 'model_count': 1}
    outbound.assert_awaited_once()


async def test_guard_rejects_before_body_read():
    from app.security.local_transport import HTTPOriginGuardMiddleware

    downstream = AsyncMock()
    receive = AsyncMock(side_effect=AssertionError('rejected request body was read'))
    send = AsyncMock()
    scope = {'type': 'http', 'method': 'POST', 'headers': [(b'Origin', b'null')]}

    await HTTPOriginGuardMiddleware(downstream)(scope, receive, send)

    downstream.assert_not_awaited()
    receive.assert_not_awaited()
    assert send.await_args_list[0].args[0]['status'] == 403
    assert send.await_args_list[1].args[0]['body'] == b'{"detail":{"code":"origin_not_allowed"}}'


@pytest.mark.parametrize('scope_type', ['websocket', 'lifespan'])
async def test_guard_passes_non_http_unchanged(scope_type):
    from app.security.local_transport import HTTPOriginGuardMiddleware

    downstream, receive, send = AsyncMock(), AsyncMock(), AsyncMock()
    scope = {'type': scope_type}

    await HTTPOriginGuardMiddleware(downstream)(scope, receive, send)

    downstream.assert_awaited_once_with(scope, receive, send)
    receive.assert_not_awaited()
    send.assert_not_awaited()


async def test_guard_preserves_streaming_messages():
    from app.security.local_transport import HTTPOriginGuardMiddleware

    scope = {'type': 'http', 'method': 'POST', 'headers': [(b'origin', ALLOWED_ORIGINS[0].encode())]}
    receive = AsyncMock()
    sent = []

    async def send(message):
        sent.append(message)

    async def downstream(actual_scope, actual_receive, actual_send):
        assert (actual_scope, actual_receive, actual_send) == (scope, receive, send)
        await actual_send({'type': 'http.response.start', 'status': 200, 'headers': []})
        first = {'type': 'http.response.body', 'body': b'first', 'more_body': True}
        await actual_send(first)
        # The first chunk must arrive before downstream produces the next one.
        assert sent[-1] is first
        await actual_send({'type': 'http.response.body', 'body': b'last', 'more_body': False})

    await HTTPOriginGuardMiddleware(downstream)(scope, receive, send)

    assert [message.get('body') for message in sent[1:]] == [b'first', b'last']
    receive.assert_not_awaited()
