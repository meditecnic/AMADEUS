"""Session actors, connection leases and active cancellation."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from app.db import get_db, normalize_worldline


async def cancel_inflight(session: Any) -> int:
    cancelled = 0
    task = getattr(session, "active_task", None)
    if task is not None and not task.done():
        task.cancel()
        cancelled += 1
        await asyncio.gather(task, return_exceptions=True)
    streams = list(getattr(session, "active_streams", set()))
    for stream in streams:
        closer = getattr(stream, "aclose", None)
        if closer:
            try:
                await closer()
            except Exception:
                pass
    getattr(session, "active_streams", set()).clear()
    session.active_task = None
    session.is_busy = False
    return cancelled


class SessionCoordinator:
    def __init__(self):
        self._leases: dict[tuple[str, str], Any] = {}
        self._lock = asyncio.Lock()

    async def acquire_lease(self, session_id: str, worldline: str, websocket: Any) -> None:
        key = (session_id, normalize_worldline(worldline))
        old_connections = []
        async with self._lock:
            for existing_key, connection in list(self._leases.items()):
                if existing_key[0] == session_id and connection is not websocket:
                    old_connections.append(connection)
                    self._leases.pop(existing_key, None)
            self._leases[key] = websocket
        for old in old_connections:
            try:
                await old.close(code=4409, reason="session connection replaced")
            except TypeError:  # compatibility with simple test doubles
                await old.close(code=4409)
            except Exception:
                pass

    async def release_lease(self, session_id: str, worldline: str, websocket: Any) -> None:
        key = (session_id, normalize_worldline(worldline))
        async with self._lock:
            if self._leases.get(key) is websocket:
                self._leases.pop(key, None)

    async def get_current(self, session_id: str) -> tuple[str, int]:
        db = await get_db("steins_gate", "control")
        try:
            cur = await db.execute("SELECT worldline,revision FROM session_worldlines WHERE session_id=?", (session_id,))
            row = await cur.fetchone()
            return (row["worldline"], row["revision"]) if row else ("steins_gate", 0)
        finally:
            await db.close()

    async def persist_current(self, session_id: str, worldline: str, revision: int) -> None:
        db = await get_db("steins_gate", "control")
        try:
            await db.execute(
                """INSERT INTO session_worldlines(session_id,worldline,revision,updated_at_utc) VALUES(?,?,?,?)
                   ON CONFLICT(session_id) DO UPDATE SET worldline=excluded.worldline,revision=excluded.revision,updated_at_utc=excluded.updated_at_utc""",
                (session_id, normalize_worldline(worldline), revision, datetime.now(timezone.utc).isoformat()),
            )
            await db.commit()
        finally:
            await db.close()

    async def switch(self, session: Any, target_worldline: str) -> dict[str, object]:
        target = normalize_worldline(target_worldline)
        cancelled = await cancel_inflight(session)
        session.revision = max(getattr(session, "revision", 0), getattr(session, "current_epoch", 0)) + 1
        session.current_epoch = session.revision
        session.worldline = target
        session.history_epoch += 1
        session.history = []
        session.memory_summary = ""
        await self.persist_current(session.session_id, target, session.revision)
        await session.load_from_db()
        return {"session_id": session.session_id, "worldline": target, "revision": session.revision, "cancelled_tasks": cancelled, "available": True}


session_coordinator = SessionCoordinator()
