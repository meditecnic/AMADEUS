from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.routers import chat_ws
from app.services.conversations import conversation_service
from app.services.soul_engine import soul_engine


@pytest.mark.parametrize('loader', ['load_from_db', 'enter_draft'])
@pytest.mark.parametrize('change', ['provider', 'key', 'unchanged'])
async def test_reload_resets_backoff_only_when_credentials_change(monkeypatch, loader, change):
    session = chat_ws.SessionState(str(uuid4()))
    session.api_key = 'test-provider-credential'
    session.compression_failures, session.compression_skip_turns = 3, 4
    selected = {
        'id': 'conversation', 'provider_id': 'glm' if change == 'provider' else 'deepseek',
        'model_id': 'test-model', 'identity_mode': 'self',
    }
    chat_ws.credential_store.set('glm', 'test-provider-credential')
    if change == 'key':
        chat_ws.credential_store.set('deepseek', 'changed-test-credential')
    monkeypatch.setattr(type(conversation_service), 'get_selected', AsyncMock(return_value=selected))
    monkeypatch.setattr(chat_ws.models, 'get_conversation_content_epoch', AsyncMock(return_value=0))
    monkeypatch.setattr(chat_ws.models, 'get_recent_messages', AsyncMock(return_value=[]))
    monkeypatch.setattr(chat_ws.models, 'get_latest_summary', AsyncMock(return_value=''))
    monkeypatch.setattr(type(soul_engine), 'restore_emotion', AsyncMock())
    await getattr(session, loader)()
    assert (session.compression_failures, session.compression_skip_turns) == (
        (3, 4) if change == 'unchanged' else (0, 0)
    )


@pytest.mark.parametrize('change', ['provider', 'key', 'unchanged'])
def test_auth_direct_credentials_reset_backoff(app_client, monkeypatch, change):
    session = chat_ws.SessionState(str(uuid4()))
    session.api_key = 'old-test-credential' if change == 'key' else 'test-provider-credential'
    session.compression_failures, session.compression_skip_turns = 3, 4
    chat_ws.sessions[session.session_id] = session
    # Keep loaders out of the way: exercise the direct auth assignments.
    monkeypatch.setattr(chat_ws.SessionState, 'enter_draft', AsyncMock())
    chat_ws.credential_store.set('glm', 'test-provider-credential')
    try:
        with app_client.websocket_connect(f'/ws/chat?session_id={session.session_id}') as ws:
            ws.send_json({'type': 'auth', 'protocol_version': 2, 'conversation_mode': 'draft',
                          'provider_id': 'glm' if change == 'provider' else 'deepseek'})
            assert ws.receive_json()['type'] == 'session.ready'
            assert (session.compression_failures, session.compression_skip_turns) == (
                (3, 4) if change == 'unchanged' else (0, 0)
            )
    finally:
        chat_ws.sessions.pop(session.session_id, None)
