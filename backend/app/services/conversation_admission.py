"""Process-local admission for conversation turns and history changes."""

import asyncio
from contextlib import asynccontextmanager
from weakref import WeakValueDictionary

from app.db import normalize_worldline

_locks = WeakValueDictionary()


def conversation_admission(worldline: str, session_id: str, conversation_id: str | None) -> asyncio.Lock:
    key = (normalize_worldline(worldline), session_id, conversation_id)
    return _locks.setdefault(key, asyncio.Lock())


@asynccontextmanager
async def session_admission(session):
    while True:
        key = (session.worldline, session.session_id, session.conversation_id)
        async with conversation_admission(*key):
            if key == (session.worldline, session.session_id, session.conversation_id):
                yield
                return
