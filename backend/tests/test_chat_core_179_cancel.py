import asyncio
from unittest.mock import AsyncMock

from app.routers.chat_ws import SessionState
from app.services.session_manager import cancel_inflight


async def test_cancel_preserves_replacement_turn_and_its_streams():
    session = SessionState('cancel-replacement')
    started = asyncio.Event()
    replacement = None
    stream = AsyncMock()

    def replace():
        nonlocal replacement
        replacement = asyncio.create_task(asyncio.Event().wait())
        session.active_task = replacement
        session.active_task_connection_id = 'new-owner'
        session.is_busy = True
        session.active_streams = {stream}

    async def original():
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            asyncio.get_running_loop().call_soon(replace)

    old = asyncio.create_task(original())
    session.active_task = old
    session.active_task_connection_id = 'old-owner'
    session.is_busy = True
    await started.wait()
    try:
        assert await cancel_inflight(session) == 1
        assert old.cancelled()
        assert replacement is not None and not replacement.done()
        assert session.active_task is replacement
        assert session.active_task_connection_id == 'new-owner'
        assert session.is_busy
        assert session.active_streams == {stream}
        stream.aclose.assert_not_awaited()
    finally:
        if replacement is not None:
            replacement.cancel()
            await asyncio.gather(replacement, return_exceptions=True)
