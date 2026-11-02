from uuid import uuid4

import pytest

from app.routers import chat_ws


@pytest.mark.parametrize('key_changed', [False, True])
def test_voice_auth_resets_backoff_only_when_key_changes(app_client, key_changed):
    session_id = f'voice-key-{uuid4()}'
    with app_client.websocket_connect(f'/ws/voice?session_id={session_id}') as voice:
        # The pong proves the initial load has finished, before auth reads the key again.
        voice.send_json({'type': 'ping'})
        assert voice.receive_json() == {'type': 'pong'}
        session = chat_ws.sessions[session_id]
        session.compression_failures, session.compression_skip_turns = 3, 4
        if key_changed:
            chat_ws.credential_store.set('deepseek', 'changed-test-credential')
        voice.send_json({'type': 'auth'})
        assert voice.receive_json()['type'] == 'voice.ready'
        assert session.api_key == chat_ws.credential_store.get('deepseek')
        assert (session.compression_failures, session.compression_skip_turns) == (
            (0, 0) if key_changed else (3, 4)
        )
