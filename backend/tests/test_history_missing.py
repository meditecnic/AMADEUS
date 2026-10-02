from unittest.mock import AsyncMock

from app.services.conversations import ConversationNotFound


def test_removed_default_conversation_history_returns_404(app_client, monkeypatch):
    monkeypatch.setattr('app.models.get_session_messages', AsyncMock(side_effect=ConversationNotFound('default conversation was removed')))
    response = app_client.get('/api/history/removed-default')
    assert response.status_code == 404
