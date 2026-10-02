import asyncio
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app import models
from app.routers import chat_ws
from app.services.conversations import conversation_service
from app.services.memory import CoreFactCandidate, memory_service
from test_working_summary import isolated_store

SECRET = 'SYNTHETIC_DELETED_FACT_9834'


@pytest.mark.parametrize('action', ['forget', 'dismiss', 'delete_local'])
async def test_next_provider_prompt_drops_memory_and_working_summary(isolated_store, monkeypatch, action):
    mode = 'self' if action == 'delete_local' else 'okabe'
    owner = uuid4().hex
    conv = await conversation_service.create_and_select(owner, 'steins_gate', title='memory', identity_mode=mode)
    cid = str(conv['id'])
    monkeypatch.setattr(memory_service, '_try_index', AsyncMock())
    monkeypatch.setattr(memory_service.embedder, 'encode_query', AsyncMock(side_effect=RuntimeError('offline')))
    monkeypatch.setattr(memory_service, 'schedule_turn_extraction', lambda *args, **kwargs: None)
    mid = await models.save_message(owner, 'user', SECRET, conversation_id=cid)
    fact_id = await memory_service.upsert_core_fact(owner, 'steins_gate', CoreFactCandidate(fact_key='favorite_drink', fact_value=SECRET, confidence=.95, importance=.9, source_message_ids=[mid]), identity_mode=mode)
    await models.save_memory_summary(owner, SECRET, conversation_id=cid)
    session = chat_ws.SessionState(owner)
    session.conversation_id = cid
    session.identity_mode = mode
    session.memory_summary = SECRET
    session.enable_tts = False
    session.api_key = 'synthetic'
    session.history = [{'role': 'user', 'content': SECRET}]
    chat_ws.sessions[owner] = session
    old_epoch = await models.get_conversation_content_epoch(owner, conversation_id=cid)
    captures = []

    class Provider:
        async def get_chat_stream(self, **kwargs):
            captures.append(kwargs)
            yield {'content': '[EMO:neutral] こんにちは。', 'tool_calls': None}

        async def translate_to_zh(self, *args, **kwargs):
            return '你好。'

    monkeypatch.setattr(chat_ws, 'deepseek_service', Provider())
    try:
        if action == 'forget':
            await memory_service.forget(owner, 'steins_gate', 'core')
        elif action == 'dismiss':
            await memory_service.dismiss_okabe_fact(owner, 'steins_gate', fact_id)
        else:
            await memory_service.delete_local_self_fact(owner, 'steins_gate', fact_id)
        session.append_message({'role': 'user', 'content': 'こんにちは'})
        await chat_ws.processor_loop(session, 'こんにちは', asyncio.Queue(), session.current_epoch, session.history_epoch)
        assert captures
        prompt = captures[0]['system_prompt']
        assert SECRET not in prompt.split('RECENT DIALOGUE')[0]
        assert SECRET not in captures[0].get('memory_summary', '')
        assert not await models.save_memory_summary(owner, SECRET, conversation_id=cid, expected_epoch=old_epoch)
        assert await models.get_latest_summary(owner, conversation_id=cid) == ''
        assert session.memory_summary == ''
        assert (await memory_service.status(owner, 'steins_gate'))['current_fact_count'] == 0
        assert any(row['content'] == SECRET for row in await models.get_session_messages(owner, conversation_id=cid))
    finally:
        chat_ws.sessions.pop(owner, None)


async def test_dismiss_isolated_by_owner_worldline_and_identity(isolated_store, monkeypatch):
    monkeypatch.setattr(memory_service, '_try_index', AsyncMock())
    owner = uuid4().hex
    entries = []
    for sid, wl, mode in [(owner, 'steins_gate', 'okabe'), (owner, 'steins_gate', 'self'), (owner, 'beta', 'okabe'), ('other-'+owner, 'steins_gate', 'okabe')]:
        conv = await conversation_service.create_and_select(sid, wl, title='scope', identity_mode=mode)
        cid = str(conv['id'])
        await models.save_memory_summary(sid, SECRET, worldline=wl, conversation_id=cid)
        entries.append((sid, wl, mode, cid))
    mid = await models.save_message(owner, 'user', SECRET, conversation_id=entries[0][3])
    fact_id = await memory_service.upsert_core_fact(owner, 'steins_gate', CoreFactCandidate(fact_key='favorite_drink', fact_value=SECRET, confidence=.95, importance=.9, source_message_ids=[mid]), identity_mode='okabe')
    await memory_service.dismiss_okabe_fact(owner, 'steins_gate', fact_id)
    for sid, wl, mode, cid in entries:
        expected = '' if (sid, wl, mode) == (owner, 'steins_gate', 'okabe') else SECRET
        assert await models.get_latest_summary(sid, worldline=wl, conversation_id=cid) == expected
