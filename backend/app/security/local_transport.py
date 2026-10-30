"""Local browser origins and opt-in remote sidecar addresses."""
import hmac
import ipaddress
import os

import httpx
from fastapi import WebSocket
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app import config


# Consume the launcher secret before lifespan starts any child processes.
LOCAL_API_TOKEN = os.environ.pop('AMADEUS_LOCAL_API_TOKEN', '').encode('utf-8')
LOCAL_WS_PROTOCOL = 'amadeus.local.v1'


class LocalAPITokenMiddleware:
    """Authenticate local HTTP requests before routing or reading the body."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope['type'] == 'http' and LOCAL_API_TOKEN:
            asset_read = (scope['method'] in {'GET', 'HEAD'}
                          and scope['path'].startswith('/api/asset-pack/'))
            if not asset_read:
                authorization = [value for name, value in scope['headers']
                                 if name.lower() == b'authorization']
                if (len(authorization) != 1
                        or not authorization[0].startswith(b'Bearer ')
                        or not hmac.compare_digest(authorization[0][7:], LOCAL_API_TOKEN)):
                    response = JSONResponse(
                        {'detail': {'code': 'local_auth_required'}}, status_code=401,
                        headers={'WWW-Authenticate': 'Bearer'},
                    )
                    await response(scope, receive, send)
                    return
        await self.app(scope, receive, send)


LOCAL_ORIGINS = frozenset({
    'http://localhost', 'http://127.0.0.1',
    'tauri://localhost', 'http://tauri.localhost', 'https://tauri.localhost',
    *(f'http://{host}:{port}' for host in ('localhost', '127.0.0.1')
      for port in (config.FRONTEND_PORT, config.BACKEND_PORT, 1420, 1422, 1424)),
})


class HTTPOriginGuardMiddleware:
    """Reject browser writes from untrusted origins before any route side effects."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope['type'] == 'http' and scope['method'] in {'POST', 'PUT', 'PATCH', 'DELETE'}:
            # Read raw headers so duplicate Origin fields cannot be collapsed.
            origins = [value.decode('latin-1') for name, value in scope['headers']
                       if name.lower() == b'origin']
            if origins and (len(origins) != 1 or origins[0] not in LOCAL_ORIGINS):
                response = JSONResponse({'detail': {'code': 'origin_not_allowed'}}, status_code=403)
                await response(scope, receive, send)
                return
        # Pure ASGI forwarding preserves streaming and non-HTTP scopes unchanged.
        await self.app(scope, receive, send)


def _enabled(name: str) -> bool:
    return os.getenv(name, '').strip().lower() in {'1', 'true', 'yes', 'on'}


async def accept_local_websocket(websocket: WebSocket) -> bool:
    """Complete the proxy handshake, then reject before session side effects."""
    protocols = websocket.scope.get('subprotocols', [])
    await websocket.accept(subprotocol=LOCAL_WS_PROTOCOL if LOCAL_WS_PROTOCOL in protocols else None)
    origins = websocket.headers.getlist('origin')
    if (len(origins) == 1 and origins[0] in LOCAL_ORIGINS) or (
        not origins and _enabled('AMADEUS_ALLOW_MISSING_WS_ORIGIN')
    ):
        if LOCAL_API_TOKEN and (LOCAL_WS_PROTOCOL not in protocols or not any(
            hmac.compare_digest(protocol.encode('utf-8'), b'amadeus.token.' + LOCAL_API_TOKEN)
            for protocol in protocols
        )):
            await websocket.close(code=4401, reason='local auth required')
            return False
        return True
    await websocket.close(code=4403, reason='origin not allowed')
    return False


def sovits_tts_url(value: str) -> str:
    """Validate the final address with the parser used by the HTTP client."""
    if any(ord(char) <= 32 for char in value):
        raise ValueError('invalid sidecar URL')
    try:
        url = httpx.URL(value)
        if (url.scheme not in {'http', 'https'} or not url.host or url.userinfo
                or url.query or url.fragment
                or (url.port is not None and not 1 <= url.port <= 65535)):
            raise ValueError('invalid sidecar URL')
        try:
            local = ipaddress.ip_address(url.host).is_loopback
        except ValueError:
            local = url.host == 'localhost'
        if not local and not _enabled('AMADEUS_ALLOW_REMOTE_SOVITS'):
            raise ValueError('sidecar must use loopback')
    except httpx.InvalidURL as exc:
        raise ValueError('invalid sidecar URL') from exc
    return str(url).rstrip('/') + '/tts'
