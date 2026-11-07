"""Process-local admission barrier: drain admitted work before copying data."""
import asyncio
import contextvars
from dataclasses import dataclass
import json

import anyio

from starlette.responses import JSONResponse
from starlette.types import Send

from app import db

_root = None
_event = None
_tasks = {}
_sockets = {}
_thread_work = set()
pause_background = None
resume_background = None


def reset():
    global _root, _event
    _root, _event = str(db.get_data_root()), asyncio.Event()
    _tasks.clear()
    _sockets.clear()


def relocating():
    return _root == str(db.get_data_root()) and _event is not None and _event.is_set()


def begin():
    if _root != str(db.get_data_root()) or _event is None:
        reset()
    _event.set()


def abort():
    _event.clear()


async def run_in_thread(function, *args):
    """Keep the executor future until work finishes, even if its caller cancels."""
    work = asyncio.get_running_loop().run_in_executor(None, contextvars.copy_context().run, function, *args)
    _thread_work.add(work)

    def finished(done):
        _thread_work.discard(done)
        if not done.cancelled():
            done.exception()

    work.add_done_callback(finished)
    # Cancelling asyncio.wait cancels only its waiter, not the actual executor work.
    await asyncio.wait({work})
    return work.result()


@dataclass
class _WebSocketConnection:
    send: Send
    error_type: str
    closed: bool = False

    async def close_for_relocation(self):
        if not self.closed:
            self.closed = True
            await self.send({'type': 'websocket.send', 'text': json.dumps({
                'type': self.error_type, 'message': 'Server error'})})
            await self.send({'type': 'websocket.close', 'code': 1000})


async def drain(request_task):
    for connection in list(_sockets.values()):
        try:
            await connection.close_for_relocation()
        except (OSError, RuntimeError, anyio.ClosedResourceError, anyio.BrokenResourceError):
            pass  # The peer disconnected while the close handshake was being sent.
    current = request_task
    # A server task can outlive the ASGI call (e.g. Starlette's WebSocket scope).
    pending = [finished for task, finished in _tasks.items() if task is not current]
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)
    from app.routers.chat_ws import sessions
    active = [session.active_task for session in sessions.values()
              if session.active_task is not None and not session.active_task.done()]
    if active:
        await asyncio.gather(*active, return_exceptions=True)
    pending = [task for session in sessions.values() for task in session.background_tasks if not task.done()]
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)
    from app.services.conversations import conversation_service
    if conversation_service._title_tasks:
        await asyncio.gather(*conversation_service._title_tasks, return_exceptions=True)
    # Voice disconnect cancels its VAD task; retain and drain its actual threads.
    from app.services.speech import speech_service
    await speech_service.drain_pending()
    if pause_background is not None:
        await pause_background()
    from app.services.diagnostics import diagnostic_runtime
    await diagnostic_runtime.drain_pending()
    while _thread_work:
        await asyncio.gather(*_thread_work, return_exceptions=True)


class RelocationMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] not in {'http', 'websocket'}:
            return await self.app(scope, receive, send)
        if scope['type'] == 'http' and relocating():
            await JSONResponse({'detail': {'code': 'data_relocating'}}, status_code=503)(scope, receive, send)
            return
        connection = (_WebSocketConnection(send, 'error' if scope['path'] == '/ws/chat' else 'voice.error')
                      if scope['type'] == 'websocket' else None)

        async def ws_send(message):
            if not connection.closed:
                if message['type'] == 'websocket.close':
                    connection.closed = True
                await send(message)
                if message['type'] == 'websocket.accept':
                    _sockets[task] = connection

        task = asyncio.current_task()
        finished = asyncio.get_running_loop().create_future()
        _tasks[task] = finished
        try:
            await self.app(scope, receive, ws_send if connection is not None else send)
        finally:
            _tasks.pop(task, None)
            _sockets.pop(task, None)
            if not finished.done():
                finished.set_result(None)
