"""Process-local admission barrier: drain admitted work before copying data."""
import asyncio
import contextvars
import json

import anyio

from starlette.responses import JSONResponse

from app import db

_root = None
_event = None
_tasks = {}
_thread_work = set()
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


async def drain(request_task):
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
            # Receive in the caller task; only the relocation observer runs alongside it.
            async with anyio.create_task_group() as waiters:
                async def stop_on_relocation():
                    await event.wait()
                    waiters.cancel_scope.cancel()

                waiters.start_soon(stop_on_relocation)
                message = await receive()
                waiters.cancel_scope.cancel()
            if event.is_set():
                if not closed:
                    kind = 'error' if scope['path'] == '/ws/chat' else 'voice.error'
                    await send({'type': 'websocket.send', 'text': json.dumps({'type': kind, 'message': 'Server error'})})
                    await send({'type': 'websocket.close', 'code': 1000})
                    closed = True
                return {'type': 'websocket.disconnect', 'code': 1000}
            return message
        task = asyncio.current_task()
        finished = asyncio.get_running_loop().create_future()
        _tasks[task] = finished
        try:
            await self.app(scope, ws_receive if scope['type'] == 'websocket' else receive,
                           ws_send if scope['type'] == 'websocket' else send)
        finally:
            _tasks.pop(task, None)
            if not finished.done():
                finished.set_result(None)
