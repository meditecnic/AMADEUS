"""In-flight generated summaries must not cross conversation erasure."""
import asyncio
from uuid import uuid4

import pytest

from app import models
from app.routers.chat_ws import SessionState, compress_and_update_history, sessions
from app.services.conversations import conversation_service
from app.services.memory import MemoryService
from app.services.provider_registry import ProviderCapabilities, ProviderSnapshot, ProviderTask
from test_working_summary import isolated_store, _HISTORY, _compress_snapshot, _raw_summary_rows


@pytest.mark.asyncio
async def test_processor_rejected_during_erasure_releases_busy_state(isolated_store):
    from app.routers.chat_ws import processor_loop
    session = SessionState('erasing-turn')
    session.erasure_pending = True
    session.is_busy = True
    queue = asyncio.Queue()
    await processor_loop(session, 'rejected input', queue, 0, 0)
    assert session.is_busy is False
    assert session.history == []
    _, event = await queue.get()
    assert event['type'] == 'error'


@pytest.mark.asyncio
@pytest.mark.parametrize("producer", ["compression", "legacy"])
async def test_paused_summary_cannot_repopulate_forgotten_conversation(isolated_store, producer):
    owner = f"summary-epoch-{uuid4()}"
    conv = await conversation_service.create_and_select(owner, "steins_gate", title="epoch")
    cid = str(conv["id"])
    started, release = asyncio.Event(), asyncio.Event()

    class Adapter:
        async def compress_history(self, **kwargs):
            started.set()
            await release.wait()
            return "候補の確認待ち"

        async def complete_json(self, **kwargs):
            started.set()
            await release.wait()
            return {"working_summary": "候補の確認待ち", "facts": []}

    session = SessionState(owner)
    session.conversation_id = cid
    session.history = list(_HISTORY)
    sessions[owner] = session
    adapter = Adapter()
    if producer == "compression":
        task = asyncio.create_task(compress_and_update_history(
            owner, session.history, conversation_id=cid,
            revision=session.revision, provider_snapshot=_compress_snapshot(adapter),
        ))
    else:
        snapshot = ProviderSnapshot("test", "test", ProviderCapabilities(
            tasks=frozenset({ProviderTask.MEMORY})), adapter)
        task = asyncio.create_task(MemoryService()._extract_turn(
            owner, "steins_gate", "test", "test", [], "", snapshot,
            conversation_id=cid,
        ))
    try:
        await asyncio.wait_for(started.wait(), 5)
        await conversation_service.forget(cid, owner, "steins_gate", forget_long_term=False)
        # Simulate the UI reload without helping the guard via a revision change.
        session.history = []
        session.memory_summary = ""
        release.set()
        await asyncio.wait_for(task, 5)
        assert await _raw_summary_rows(owner) == []
        assert session.memory_summary == ""
        # New-generation work remains usable after forgetting.
        epoch = await models.get_conversation_content_epoch(owner, conversation_id=cid)
        assert await models.save_memory_summary(owner, "新しい確認待ち", conversation_id=cid, expected_epoch=epoch)
    finally:
        release.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        sessions.pop(owner, None)
