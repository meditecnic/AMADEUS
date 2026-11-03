import asyncio
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app import main, models
from app.routers import chat_ws
from app.services.conversations import conversation_service
from app.services.memory import memory_service
import app.services.memory_v11.jobs as jobs


async def test_lifespan_drains_all_sessions_before_closing_services(monkeypatch):
    monkeypatch.setattr(chat_ws, 'sessions', {})
    stopped = []
    original_stop = jobs.stop_background_worker

    async def stop_worker():
        stopped.append('worker')
        await original_stop()

    monkeypatch.setattr(jobs, 'stop_background_worker', stop_worker)
    for service in (conversation_service, memory_service):
        original = type(service).close

        async def close(self, _original=original):
            stopped.append(type(self).__name__)
            await _original(self)

        monkeypatch.setattr(type(service), 'close', close)
    tasks = []
    sessions = []
    try:
        async with main.lifespan(main.app):
            for _ in range(2):
                session = chat_ws.SessionState(str(uuid4()))
                selected = await conversation_service.create_and_select(
                    session.session_id, session.worldline,
                    provider_id='deepseek', model_id=session.model,
                )
                session.conversation_id = selected['id']
                sessions.append(session)
                chat_ws.sessions[session.session_id] = session

                async def save_after_cancel(current=session):
                    try:
                        await asyncio.Event().wait()
                    finally:
                        async def persist():
                            await asyncio.sleep(0.03)
                            assert stopped == [], 'services stopped before the pending DB write'
                            await models.save_message(
                                current.session_id, 'assistant', '途中で止まった',
                                worldline=current.worldline, conversation_id=current.conversation_id,
                            )
                        tasks.append(chat_ws._schedule_background_task(current, persist()))

                session.active_task = asyncio.create_task(save_after_cancel())
                tasks.append(session.active_task)
                await asyncio.sleep(0)
        assert all(task.done() for task in tasks)
        for session in sessions:
            rows = await models.get_recent_messages(
                session.session_id, worldline=session.worldline, conversation_id=session.conversation_id,
            )
            assert [row['content'] for row in rows] == ['途中で止まった']
        assert stopped[0] == 'worker'
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


@pytest.mark.parametrize("active_cleanup_delay", [0.0, 0.2])
async def test_shutdown_has_one_global_grace_and_cancels_timed_out_tasks(monkeypatch, active_cleanup_delay):
    monkeypatch.setattr(chat_ws, 'sessions', {})
    tasks = []
    streams = []
    cancelled = []

    async def blocked(index):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.append(index)

    for index in range(3):
        session = chat_ws.SessionState(str(uuid4()))
        chat_ws.sessions[session.session_id] = session
        tasks.append(chat_ws._schedule_background_task(session, blocked(index)))
        stream = AsyncMock()
        session.active_streams.add(stream)
        streams.append(stream)
    async def active_turn():
        try:
            await asyncio.Event().wait()
        finally:
            await asyncio.sleep(active_cleanup_delay)

    session.active_task = asyncio.create_task(active_turn())
    tasks.append(session.active_task)
    await asyncio.sleep(0)
    started = asyncio.get_running_loop().time()
    try:
        await asyncio.wait_for(chat_ws.close_chat_sessions(), 1.2)
        elapsed = asyncio.get_running_loop().time() - started
        assert elapsed < 1.2
        assert sorted(cancelled) == [0, 1, 2]
        assert all(task.cancelled() for task in tasks)
        for stream in streams:
            stream.aclose.assert_awaited_once()
        assert all(not session.active_streams for session in chat_ws.sessions.values())
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


@pytest.mark.timeout(3)
async def test_shutdown_does_not_wait_forever_for_cancellation_cleanup(monkeypatch):
    session = chat_ws.SessionState(str(uuid4()))
    monkeypatch.setattr(chat_ws, 'sessions', {session.session_id: session})
    release = asyncio.Event()

    async def slow_cancel():
        try:
            await asyncio.Event().wait()
        finally:
            await release.wait()

    task = chat_ws._schedule_background_task(session, slow_cancel())
    await asyncio.sleep(0)
    started = asyncio.get_running_loop().time()
    try:
        await chat_ws.close_chat_sessions()
        assert asyncio.get_running_loop().time() - started < 1.2
        assert task.cancelling()
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
