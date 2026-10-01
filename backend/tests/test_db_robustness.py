import sys
import os
import pytest
import pytest_asyncio
import asyncio
import sqlite3
import threading
import aiosqlite
from unittest.mock import patch, AsyncMock
from fastapi import WebSocketDisconnect

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
from app.routers.chat_ws import sessions, websocket_chat_endpoint, SessionState

# Define MockWebSocket for websocket tests
class MockWebSocket:
    def __init__(self, session_id="default"):
        self.query_params = {"session_id": session_id}
        self.recv_queue = asyncio.Queue()
        self.send_queue = asyncio.Queue()
        self.accepted = False
        self.closed = False
        self.headers = {"origin": "http://localhost"}
        self.sent_messages = []

    async def accept(self):
        self.accepted = True

    async def receive_json(self):
        item = await self.recv_queue.get()
        if item is None:
            raise WebSocketDisconnect()
        return item

    async def send_json(self, data):
        self.sent_messages.append(data)
        await self.send_queue.put(data)

    async def close(self, code=1000):
        self.closed = True
        await self.recv_queue.put(None)


@pytest_asyncio.fixture(autouse=True)
async def setup_test_db(tmp_path, monkeypatch):
    """Isolate DB environment for each test and initialize schema."""
    db_file = tmp_path / "test_amadeus_robustness.db"
    monkeypatch.setenv("AMADEUS_DB_PATH", str(db_file))
    
    await init_db()
    
    yield db_file
    # Cleanup sessions store to ensure test isolation
    sessions.clear()


# -----------------------------------------------------------------
# 1. Connection lifecycle safety
# -----------------------------------------------------------------
@pytest.mark.asyncio
async def test_get_db_closes_partially_initialized_connection_when_cancelled(monkeypatch):
    """Cancellation during connection setup must not orphan an aiosqlite worker."""

    class BlockingConnection:
        def __init__(self):
            self.row_factory = None
            self.execute_started = asyncio.Event()
            self.closed = asyncio.Event()

        async def execute(self, _statement):
            self.execute_started.set()
            await asyncio.Future()

        async def close(self):
            self.closed.set()

    connection = BlockingConnection()
    monkeypatch.setattr(
        aiosqlite,
        "connect",
        AsyncMock(return_value=connection),
    )

    task = asyncio.create_task(get_db())
    await connection.execute_started.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert connection.closed.is_set()


@pytest.mark.asyncio
async def test_get_db_waits_for_cancelled_initial_connect_worker(monkeypatch):
    """Cancellation must not return while aiosqlite's worker is still alive."""

    real_aiosqlite_connect = aiosqlite.connect
    real_sqlite_connect = sqlite3.connect
    connect_started = threading.Event()
    release_connect = threading.Event()
    captured = {}

    def blocking_sqlite_connect(*args, **kwargs):
        connect_started.set()
        release_connect.wait(timeout=2.0)
        return real_sqlite_connect(*args, **kwargs)

    def captured_connect(*args, **kwargs):
        connection = real_aiosqlite_connect(*args, **kwargs)
        captured["connection"] = connection
        return connection

    monkeypatch.setattr(sqlite3, "connect", blocking_sqlite_connect)
    monkeypatch.setattr(aiosqlite, "connect", captured_connect)

    task = asyncio.create_task(get_db())
    assert await asyncio.to_thread(connect_started.wait, 1.0)
    task.cancel()
    release_timer = threading.Timer(0.05, release_connect.set)
    release_timer.start()

    try:
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not captured["connection"]._thread.is_alive()
    finally:
        release_connect.set()
        release_timer.join(timeout=1.0)
        captured["connection"]._thread.join(timeout=1.0)


@pytest.mark.asyncio
async def test_connection_lifecycle_no_leaks(setup_test_db):
    """
    Confirm database connections are created, used, and correctly released.
    Ensure zero file locks hung or file descriptor leaks on Windows.
    """
    db_file = setup_test_db
    session_id = "lifecycle-session"
    
    # Run high load of read/write operations
    async def task(i):
        await save_message(session_id, "user", f"msg {i}")
        await get_recent_messages(session_id, limit=5)
        await save_memory_summary(session_id, f"summary {i}")
        await get_latest_summary(session_id)
        
    await asyncio.gather(*(task(i) for i in range(50)))
    
    # Attempt to rename the database file. If it fails with PermissionError,
    # it means some connection handles are still holding a lock on the file.
    temp_rename = str(db_file) + ".rename"
    try:
        os.rename(db_file, temp_rename)
        os.rename(temp_rename, db_file)
        lock_freed = True
    except PermissionError as e:
        lock_freed = False
        print(f"File lock verification failed: {e}")
        
    assert lock_freed, "SQLite connection handle or file lock leak detected!"


# -----------------------------------------------------------------
# 2. High-frequency concurrent transaction processing
# -----------------------------------------------------------------
@pytest.mark.asyncio
async def test_high_frequency_concurrency_wal(setup_test_db):
    """
    Test WAL mode concurrency stability by firing massive concurrent user messages,
    assistant replies, and memory summary updates in parallel.
    """
    session_id = "concurrency-session"
    
    # We will run 150 tasks in parallel.
    # 50 writers for user messages, 50 writers for assistant messages, 50 writers for summaries.
    async def write_user(i):
        await save_message(session_id, "user", f"user message {i}")
        
    async def write_assistant(i):
        await save_message(session_id, "assistant", f"assistant message {i}")
        
    async def write_summary(i):
        await save_memory_summary(session_id, f"summary state {i}")

    tasks = []
    for i in range(50):
        tasks.append(write_user(i))
        tasks.append(write_assistant(i))
        tasks.append(write_summary(i))
        
    # Gather everything in parallel
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    errors = [r for r in results if isinstance(r, Exception)]
    assert len(errors) == 0, f"Encountered concurrency errors: {errors}"
    
    # Retrieve messages and summaries to verify integrity
    msgs = await get_session_messages(session_id)
    assert len(msgs) == 100, f"Expected 100 messages, got {len(msgs)}"
    
    # Check that we can read latest summary without error
    summary = await get_latest_summary(session_id)
    assert summary != ""


# -----------------------------------------------------------------
# 3. Clean cleanup on abnormal disconnection
# -----------------------------------------------------------------
@pytest.mark.asyncio
async def test_abnormal_disconnect_cleanup(setup_test_db):
    """
    Simulate client websocket disconnection/timeout, verify that the active_task
    and writer_task are cleanly cancelled, connections are closed, and resources are fully released.
    """
    session_id = "disconnect-session"
    ws = MockWebSocket(session_id=session_id)
    stream_started = asyncio.Event()
    stream_cancelled = asyncio.Event()
    
    async def mock_slow_chat_stream(self, *args, **kwargs):
        stream_started.set()
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            stream_cancelled.set()
            raise
        yield {"content": "Final segment", "tool_calls": None}
        
    async def mock_translate(self, text, api_key=None):
        return "translation"

    with patch("app.services.deepseek.DeepSeekService.get_chat_stream", mock_slow_chat_stream), \
         patch("app.services.deepseek.DeepSeekService.translate_to_zh", mock_translate), \
         patch("app.services.tts_queue.TTSQueueManager.synthesize_async", return_value=b"audio"):
         
        # Run websocket endpoint task
        endpoint_task = asyncio.create_task(websocket_chat_endpoint(ws))
        
        try:
            await ws.recv_queue.put({"type": "auth", "api_key": "test_key"})
            ready = await asyncio.wait_for(ws.send_queue.get(), timeout=5)
            assert ready["type"] == "session.ready"

            await ws.recv_queue.put({"type": "chat", "content": "Hello"})
            await asyncio.wait_for(stream_started.wait(), timeout=5)

            session_state = sessions.get(session_id)
            assert session_state is not None
            assert session_state.is_busy is True
            assert session_state.active_task is not None
            assert not session_state.active_task.done()
        finally:
            ws.closed = True
            await ws.recv_queue.put(None)
            await asyncio.wait_for(endpoint_task, timeout=5)
        
        # Verify tasks are cancelled and cleaned up
        assert session_state.is_busy is False
        assert session_state.active_task is None or session_state.active_task.done()
        assert stream_cancelled.is_set()
        
        # Verify database lock is free (all connections closed)
        db_file = setup_test_db
        temp_rename = str(db_file) + ".rename"
        try:
            os.rename(db_file, temp_rename)
            os.rename(temp_rename, db_file)
            lock_freed = True
        except PermissionError:
            lock_freed = False
        assert lock_freed, "Connections not closed during abnormal disconnect!"


# -----------------------------------------------------------------
# 4. System restart/interruption data consistency
# -----------------------------------------------------------------
@pytest.mark.asyncio
async def test_interrupted_writes_consistency(setup_test_db):
    """
    Verify that interrupting active writes or server crashes do not corrupt the database
    and that it recovers correctly.
    """
    db_file = setup_test_db
    session_id = "interrupted-session"
    from app.services.conversations import conversation_service

    conversation = await conversation_service.get_or_create_default(session_id, "steins_gate")
    conversation_id = conversation["id"]
    
    # Define a write that starts a transaction, inserts rows, sleeps, and commits
    async def interrupted_write():
        db = await get_db()
        try:
            await db.execute("BEGIN TRANSACTION")
            await db.execute(
                """INSERT INTO messages(session_id, conversation_id, role, content)
                   VALUES(?,?,?,?)""",
                (session_id, conversation_id, "user", "msg A")
            )
            # Sleep to allow cancellation in the middle
            await asyncio.sleep(2.0)
            await db.execute(
                """INSERT INTO messages(session_id, conversation_id, role, content)
                   VALUES(?,?,?,?)""",
                (session_id, conversation_id, "assistant", "msg B")
            )
            await db.commit()
        finally:
            await db.close()
            
    # Spawn and cancel write task mid-flight
    write_task = asyncio.create_task(interrupted_write())
    await asyncio.sleep(0.1) # Wait until it executes the first INSERT
    
    write_task.cancel()
    try:
        await write_task
    except asyncio.CancelledError:
        pass
        
    # Check that database recovered correctly and did not corrupt
    db = await get_db()
    try:
        # Run integrity check
        cur = await db.execute("PRAGMA integrity_check")
        row = await cur.fetchone()
        assert row[0] == "ok", f"Integrity check failed: {row[0]}"
        
        # Verify no partial/incomplete data is present (rolled back)
        cur = await db.execute("SELECT COUNT(*) as count FROM messages WHERE session_id=?", (session_id,))
        row = await cur.fetchone()
        assert row["count"] == 0, f"Transaction was not rolled back! Found {row['count']} rows"
        
        # Verify database is writeable and recovers
        await db.execute(
            """INSERT INTO messages(session_id, conversation_id, role, content)
               VALUES(?,?,?,?)""",
            (session_id, conversation_id, "user", "post-recovery msg")
        )
        await db.commit()
        
        cur = await db.execute("SELECT content FROM messages WHERE session_id=?", (session_id,))
        rows = await cur.fetchall()
        assert len(rows) == 1
        assert rows[0]["content"] == "post-recovery msg"
    finally:
        await db.close()
