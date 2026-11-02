import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from starlette.datastructures import Headers
from starlette.websockets import WebSocketDisconnect, WebSocketState

from app.routers import chat_ws


@pytest.mark.parametrize('disconnected', [True, False])
async def test_reader_stops_after_disconnected_receive(monkeypatch, disconnected):
    ws = SimpleNamespace(
        query_params={'session_id': str(uuid4())},
        headers=Headers({'origin': 'http://localhost:1420'}),
        scope={'subprotocols': []}, accept=AsyncMock(),
        client_state=WebSocketState.DISCONNECTED if disconnected else WebSocketState.CONNECTED,
        application_state=WebSocketState.CONNECTED,
        receive_json=AsyncMock(side_effect=[
            ValueError('closed frame') if disconnected else RuntimeError(
                'Cannot call "receive" once a disconnect message has been received.'
            ),
            WebSocketDisconnect(),
        ]),
    )
    try:
        await chat_ws.websocket_chat_endpoint(ws)
        assert ws.receive_json.await_count == 1
    finally:
        chat_ws.sessions.pop(ws.query_params['session_id'], None)


async def test_bad_json_reports_error_and_keeps_reading(monkeypatch):
    frames = []

    async def writer(queue, session, websocket):
        while True:
            _, frame = await queue.get()
            frames.append(frame)

    async def receive():
        await asyncio.sleep(0)
        return next_frame()

    incoming = iter([json.JSONDecodeError('bad', '{', 0), {'type': 'ping'}, WebSocketDisconnect()])

    def next_frame():
        value = next(incoming)
        if isinstance(value, Exception):
            raise value
        return value

    ws = SimpleNamespace(
        query_params={'session_id': str(uuid4())},
        headers=Headers({'origin': 'http://localhost:1420'}),
        scope={'subprotocols': []}, accept=AsyncMock(),
        client_state=WebSocketState.CONNECTED, application_state=WebSocketState.CONNECTED,
        receive_json=AsyncMock(side_effect=receive),
    )
    monkeypatch.setattr(chat_ws, 'writer_loop', writer)
    try:
        await chat_ws.websocket_chat_endpoint(ws)
        assert ws.receive_json.await_count == 3
        assert frames[0] == {'type': 'error', 'message': '数据格式无效'}
        assert len(frames) == 2
    finally:
        chat_ws.sessions.pop(ws.query_params['session_id'], None)
