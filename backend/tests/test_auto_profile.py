"""Memory ingest window contract and the chat session message-id backfill."""
from __future__ import annotations

import pytest

from app.db import init_db, reset_initialization_cache
from app.services.memory import MEMORY_INGEST_WINDOW_N, memory_service


@pytest.fixture
async def isolated_store(tmp_path, monkeypatch):
    from unittest.mock import AsyncMock
    from app.services.memory import EMBEDDING_DIMENSION

    monkeypatch.setattr(memory_service.embedder, "encode_query", AsyncMock(return_value=[0.0] * EMBEDDING_DIMENSION))
    monkeypatch.setattr(memory_service.embedder, "encode_passage", AsyncMock(return_value=[0.0] * EMBEDDING_DIMENSION))
    monkeypatch.delenv("AMADEUS_DB_PATH", raising=False)
    monkeypatch.setenv("AMADEUS_DATA_DIR", str(tmp_path))
    reset_initialization_cache()
    await init_db()
    return tmp_path


def test_ingest_window_constant_matches_contract():
    assert MEMORY_INGEST_WINDOW_N == 6


@pytest.mark.asyncio
async def test_session_set_last_message_id_backfills_newest_row(isolated_store):
    from app.routers.chat_ws import SessionState

    session = SessionState("sess-window")
    session.append_message({"role": "user", "content": "q1", "id": 1})
    session.append_message({"role": "assistant", "content": "a1", "id": 2})
    session.append_message({"role": "user", "content": "塞拉斯"})
    session.set_last_message_id("user", 3)
    history = session.history
    assert len(history) == 3
    assert history[-1]["id"] == 3
    assert history[-1]["content"] == "塞拉斯"
