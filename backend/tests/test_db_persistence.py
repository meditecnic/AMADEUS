import sys
import os
import pytest
from fastapi.testclient import TestClient

# Ensure backend root is in python path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db import init_db, get_db
from app.models import save_message, get_recent_messages, save_memory_summary, get_latest_summary
from app.routers.chat_ws import SessionState
import app.main as main_mod

@pytest.mark.asyncio
async def test_database_persistence_roundtrip(tmp_path, monkeypatch):
    # Override database path to use temporary folder for isolated tests
    db_file = tmp_path / "test_amadeus.db"
    monkeypatch.setenv("AMADEUS_DB_PATH", str(db_file))
    
    # Initialize DB
    await init_db()
    
    # Save user and assistant messages
    sid = "test-session-1"
    await save_message(sid, "user", "Hello Kurisu")
    await save_message(sid, "assistant", "What is it?")
    
    # Retrieve messages
    recent = await get_recent_messages(sid, limit=10)
    assert len(recent) == 2
    assert recent[0]["role"] == "user"
    assert recent[0]["content"] == "Hello Kurisu"
    assert recent[1]["role"] == "assistant"
    assert recent[1]["content"] == "What is it?"
    
    # Save and retrieve summary
    await save_memory_summary(sid, "The user greeted Kurisu.")
    summary = await get_latest_summary(sid)
    assert summary == "The user greeted Kurisu."

@pytest.mark.asyncio
async def test_session_state_load_from_db(tmp_path, monkeypatch):
    db_file = tmp_path / "test_amadeus_restore.db"
    monkeypatch.setenv("AMADEUS_DB_PATH", str(db_file))
    
    await init_db()
    sid = "test-session-2"
    await save_message(sid, "user", "Past Message")
    await save_message(sid, "assistant", "Ok")
    await save_memory_summary(sid, "Past memory summary")
    
    # Instantiate a clean session state simulating application startup
    session = SessionState(sid)
    await session.load_from_db()
    
    assert len(session.history) == 2
    assert session.history[0]["content"] == "Past Message"
    assert session.memory_summary == "Past memory summary"

@pytest.mark.asyncio
async def test_history_rest_endpoint(tmp_path, monkeypatch):
    db_file = tmp_path / "test_amadeus_rest.db"
    monkeypatch.setenv("AMADEUS_DB_PATH", str(db_file))
    
    await init_db()
    await save_message("s1", "user", "REST Test User")
    await save_message("s1", "assistant", "REST Test Assistant")
    await save_memory_summary("s1", "REST Summary")
    
    client = TestClient(main_mod.app)
    r = client.get("/api/history/s1")
    assert r.status_code == 200
    data = r.json()
    assert data["session_id"] == "s1"
    assert data["summary"] == "REST Summary"
    assert len(data["messages"]) == 2
    assert data["messages"][0]["content"] == "REST Test User"
