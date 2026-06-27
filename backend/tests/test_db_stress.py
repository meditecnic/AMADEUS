import sys
import os
import pytest
import asyncio
import sqlite3
import aiosqlite

# Ensure backend root is in python path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db import init_db, get_db
from app.models import (
    save_message,
    get_recent_messages,
    get_session_messages,
    save_memory_summary,
    get_latest_summary
)

@pytest.mark.asyncio
async def test_db_high_concurrency_writes(tmp_path, monkeypatch):
    """
    Stress test SQLite under high concurrency.
    Spawn 100 concurrent write operations (messages and summaries) using separate tasks.
    Verify that SQLite (in WAL mode) handles concurrency without throwing locking/operational errors.
    """
    db_file = tmp_path / "test_amadeus_stress.db"
    monkeypatch.setenv("AMADEUS_DB_PATH", str(db_file))
    
    await init_db()
    
    session_id = "stress-session-concurrency"
    num_writes = 100
    
    async def write_task(index: int):
        # Alternate between saving message and saving summary
        if index % 2 == 0:
            await save_message(session_id, "user", f"Message content {index}")
        else:
            await save_memory_summary(session_id, f"Summary content {index}")

    # Spawn all tasks concurrently
    tasks = [write_task(i) for i in range(num_writes)]
    
    # Run concurrently and ensure no exception is raised
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    errors = [r for r in results if isinstance(r, Exception)]
    assert len(errors) == 0, f"Encountered {len(errors)} errors during concurrent write: {errors}"
    
    # Read back to ensure all 50 messages and 50 summaries were written successfully
    messages = await get_session_messages(session_id)
    assert len(messages) == 50, f"Expected 50 messages, got {len(messages)}"
    
    # Check summaries directly via connection (since get_latest_summary only returns 1)
    db = await get_db()
    try:
        cur = await db.execute(
            "SELECT COUNT(*) as count FROM memory_summaries WHERE session_id=?",
            (session_id,),
        )
        row = await cur.fetchone()
        assert row["count"] == 50, f"Expected 50 summaries, got {row['count']}"
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_db_concurrency_consistency(tmp_path, monkeypatch):
    """
    Verify that concurrent writes do not lead to data corruption or loss.
    All written content must be retrievable and matches the sent payloads.
    """
    db_file = tmp_path / "test_amadeus_consistency.db"
    monkeypatch.setenv("AMADEUS_DB_PATH", str(db_file))
    
    await init_db()
    
    session_id = "consistency-session"
    num_writes = 50
    payloads = [f"Unique Payload #{i}" for i in range(num_writes)]
    
    async def write_msg(payload: str):
        await save_message(session_id, "user", payload)
        
    # Execute concurrently
    await asyncio.gather(*(write_msg(p) for p in payloads))
    
    # Retrieve messages
    messages = await get_session_messages(session_id)
    assert len(messages) == num_writes
    
    retrieved_contents = {m["content"] for m in messages}
    expected_contents = set(payloads)
    
    assert retrieved_contents == expected_contents, "Retrieved data set does not match the original payloads."


@pytest.mark.asyncio
async def test_db_sql_injection_prevention(tmp_path, monkeypatch):
    """
    Verify parameter safety. Injection payloads in session_id should be treated as literal strings,
    not SQL command execution.
    """
    db_file = tmp_path / "test_amadeus_sql_inj.db"
    monkeypatch.setenv("AMADEUS_DB_PATH", str(db_file))
    
    await init_db()
    
    normal_session = "normal_session"
    malicious_session = "normal_session' OR '1'='1"
    
    # Save messages for both
    await save_message(normal_session, "user", "Normal message")
    await save_message(malicious_session, "user", "Malicious message")
    
    # Query normal session. It should only return 1 message.
    # If injection occurred, it might return both messages.
    normal_msgs = await get_session_messages(normal_session)
    assert len(normal_msgs) == 1
    assert normal_msgs[0]["content"] == "Normal message"
    
    # Query malicious session
    malicious_msgs = await get_session_messages(malicious_session)
    assert len(malicious_msgs) == 1
    assert malicious_msgs[0]["content"] == "Malicious message"


@pytest.mark.asyncio
async def test_db_boundary_inputs(tmp_path, monkeypatch):
    """
    Test boundary cases: empty string session IDs, extremely long content, and special characters.
    """
    db_file = tmp_path / "test_amadeus_boundary.db"
    monkeypatch.setenv("AMADEUS_DB_PATH", str(db_file))
    
    await init_db()
    
    # 1. Empty string session_id
    await save_message("", "user", "Empty session ID content")
    empty_msgs = await get_session_messages("")
    assert len(empty_msgs) == 1
    assert empty_msgs[0]["content"] == "Empty session ID content"
    
    # 2. Extremely long string (100KB)
    long_content = "A" * 100000
    await save_message("long-session", "user", long_content)
    long_msgs = await get_session_messages("long-session")
    assert len(long_msgs) == 1
    assert len(long_msgs[0]["content"]) == 100000
    
    # 3. Special characters (emojis, control characters, quotes, unicode characters)
    special_content = "Kurisu \n\t ' \" \\ % _ \u3053\u3093\u306b\u3061\u306f 🧪🚀"
    await save_message("special-session", "assistant", special_content)
    special_msgs = await get_session_messages("special-session")
    assert len(special_msgs) == 1
    assert special_msgs[0]["content"] == special_content


@pytest.mark.asyncio
async def test_db_null_constraints(tmp_path, monkeypatch):
    """
    Test passing invalid Python types such as None to non-nullable fields.
    SQLite should raise IntegrityError.
    """
    db_file = tmp_path / "test_amadeus_null.db"
    monkeypatch.setenv("AMADEUS_DB_PATH", str(db_file))
    
    await init_db()
    
    # Attempting to save a message with None session_id, role, or content
    with pytest.raises((sqlite3.IntegrityError, aiosqlite.IntegrityError)):
        await save_message(None, "user", "Some content")
        
    with pytest.raises((sqlite3.IntegrityError, aiosqlite.IntegrityError)):
        await save_message("session-null", None, "Some content")
        
    with pytest.raises((sqlite3.IntegrityError, aiosqlite.IntegrityError)):
        await save_message("session-null", "user", None)
