"""Process-local admission barrier: drain admitted work before copying data."""
import asyncio
import json

from starlette.responses import JSONResponse

from app import db

_root = None
_event = None
_tasks = set()
pause_background = None
resume_background = None


def reset():
    global _root, _event
    _root, _event = str(db.get_data_root()), asyncio.Event()
    _tasks.clear()


def relocating():
    return _root == str(db.get_data_root()) and _event is not None and _event.is_set()


def begin():
    if _root != str(db.get_data_root()) or _event is None:
        reset()
    _event.set()


def abort():
    _event.clear()


async def drain(request_task):
    current = request_task
    pending = [task for task in _tasks if task is not current and not task.done()]
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


class RelocationMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] not in {'http', 'websocket'}:
            return await self.app(scope, receive, send)
        if scope['type'] == 'http' and relocating():
            await JSONResponse({'detail': {'code': 'data_relocating'}}, status_code=503)(scope, receive, send)
            return
        # Save this event: aborted relocation must not re-open an already drained socket.
        event = _event
        closed = False
        accepted = False

        async def ws_send(message):
            nonlocal accepted, closed
            if not closed:
                await send(message)
                accepted |= message['type'] == 'websocket.accept'
                closed |= message['type'] == 'websocket.close'

        async def ws_receive():
            nonlocal closed
            if event is None or not accepted:
                return await receive()
            incoming = asyncio.create_task(receive())
            stop = asyncio.create_task(event.wait())
            try:
                await asyncio.wait({incoming, stop}, return_when=asyncio.FIRST_COMPLETED)
                if event.is_set():
                    if not closed:
                        kind = 'error' if scope['path'] == '/ws/chat' else 'voice.error'
                        await send({'type': 'websocket.send', 'text': json.dumps({'type': kind, 'message': 'Server error'})})
                        await send({'type': 'websocket.close', 'code': 1000})
                        closed = True
                    return {'type': 'websocket.disconnect', 'code': 1000}
                return incoming.result()
            finally:
                for waiter in (incoming, stop):
                    if not waiter.done():
                        waiter.cancel()
                await asyncio.gather(incoming, stop, return_exceptions=True)
        task = asyncio.create_task(self.app(scope, ws_receive if scope['type'] == 'websocket' else receive,
                                            ws_send if scope['type'] == 'websocket' else send))
        _tasks.add(task)
        def finished(done):
            _tasks.discard(done)
            if not done.cancelled():
                done.exception()
        task.add_done_callback(finished)
        await asyncio.shield(task)
