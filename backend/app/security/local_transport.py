"""Local browser origins and opt-in remote sidecar addresses."""
import ipaddress
import os

import httpx
from fastapi import WebSocket

from app import config


LOCAL_ORIGINS = frozenset({
    'http://localhost', 'http://127.0.0.1',
    'tauri://localhost', 'http://tauri.localhost', 'https://tauri.localhost',
    *(f'http://{host}:{port}' for host in ('localhost', '127.0.0.1')
      for port in (config.FRONTEND_PORT, config.BACKEND_PORT, 1420, 1422, 1424)),
})


def _enabled(name: str) -> bool:
    return os.getenv(name, '').strip().lower() in {'1', 'true', 'yes', 'on'}


async def accept_local_websocket(websocket: WebSocket) -> bool:
    """Complete the proxy handshake, then reject before session side effects."""
    await websocket.accept()
    origins = websocket.headers.getlist('origin')
    if (len(origins) == 1 and origins[0] in LOCAL_ORIGINS) or (
        not origins and _enabled('AMADEUS_ALLOW_MISSING_WS_ORIGIN')
    ):
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
