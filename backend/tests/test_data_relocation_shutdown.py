"""Cancellation must reach ASGI handlers; relocation waits for actual thread work."""
import asyncio
from uuid import uuid4
import json

import pytest

from app import main, models
from app.routers import chat_ws
from app.services import data_relocation
from test_turn_deadline import fake_providers


@pytest.mark.timeout(8)
async def test_lifespan_shutdown_cancels_open_chat_and_slow_turn(monkeypatch, fake_providers):
    monkeypatch.setattr(chat_ws, 'sessions', {})
    before = asyncio.all_tasks()
    slow_started, stream_closed = asyncio.Event(), asyncio.Event()
    handler_cancelled, handler_finished = asyncio.Event(), asyncio.Event()
    handler_tasks = []
    incoming, outgoing = asyncio.Queue(), asyncio.Queue()
    session_id = str(uuid4())
    connection = None

    async def slow_reply(**kwargs):
        try:
            slow_started.set()
            await asyncio.Event().wait()
            yield {}  # Keep this a streaming provider without producing a reply.
        finally:
            stream_closed.set()

    monkeypatch.setitem(vars(chat_ws.deepseek_service), 'get_chat_stream', slow_reply)

    async def handler(scope, receive, send):
        handler_tasks.append(asyncio.current_task())
        try:
            await chat_ws.router(scope, receive, send)
        except asyncio.CancelledError:
            handler_cancelled.set()
            raise
        finally:
            handler_finished.set()

    async def frame():
        while True:
            message = await asyncio.wait_for(outgoing.get(), 2)
            if message['type'] == 'websocket.send':
                return json.loads(message['text'])

    async def send_frame(payload):
        await incoming.put({'type': 'websocket.receive', 'text': json.dumps(payload)})

    scope = {'type': 'websocket', 'path': '/ws/chat', 'scheme': 'ws', 'root_path': '',
             'query_string': f'session_id={session_id}'.encode('ascii'),
             'headers': [(b'host', b'localhost'), (b'origin', b'http://localhost:1420')],
             'subprotocols': [], 'server': ('localhost', 80), 'client': ('127.0.0.1', 1), 'app': main.app}
    try:
        async with main.lifespan(main.app):
            await incoming.put({'type': 'websocket.connect'})
            connection = asyncio.create_task(data_relocation.RelocationMiddleware(handler)(
                scope, incoming.get, outgoing.put))
            assert (await asyncio.wait_for(outgoing.get(), 2))['type'] == 'websocket.accept'
            await send_frame({'type': 'auth', 'protocol_version': 2, 'client': 'desktop',
                              'conversation_mode': 'draft', 'enable_tts': False})
            assert (await frame())['type'] == 'session.ready'
            await send_frame({'type': 'chat', 'content': 'hello'})
            await asyncio.wait_for(slow_started.wait(), 2)
            session = chat_ws.sessions[session_id]
            assert session.is_busy
            # Server shutdown cancels the still-open connection before exiting lifespan.
            shutdown_started = asyncio.get_running_loop().time()
            connection.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(connection, 2)
        assert asyncio.get_running_loop().time() - shutdown_started < 3
        assert handler_cancelled.is_set() and handler_finished.is_set()
        assert handler_tasks == [connection]
        assert stream_closed.is_set()
        assert session.active_task.done() and not session.is_busy
        assert not session.active_streams and session.active_segment_pipeline is None
        assert not data_relocation._tasks
        assert not (asyncio.all_tasks() - before), 'shutdown left background tasks alive'
        rows = await models.get_recent_messages(
            session_id, worldline=session.worldline, conversation_id=session.conversation_id)
        assert chat_ws._ABORTED_TURN_JA in str(rows)
    finally:
        # Also clean a deliberately reintroduced shielding bug during mutation checks.
        remaining = [task for task in {connection, *handler_tasks} if task is not None and not task.done()]
        for task in remaining:
            task.cancel()
        if remaining:
            await asyncio.gather(*remaining, return_exceptions=True)
