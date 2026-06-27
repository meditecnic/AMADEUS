from app.db import get_db

async def save_message(session_id: str, role: str, content: str):
    db = await get_db()
    try:
        await db.execute(
            "INSERT INTO messages(session_id, role, content) VALUES(?,?,?)",
            (session_id, role, content),
        )
        await db.commit()
    finally:
        await db.close()

async def get_recent_messages(session_id: str, limit: int = 6):
    db = await get_db()
    try:
        cur = await db.execute(
            "SELECT role, content FROM messages WHERE session_id=? ORDER BY id DESC LIMIT ?",
            (session_id, limit),
        )
        rows = await cur.fetchall()
        return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]
    finally:
        await db.close()

async def get_session_messages(session_id: str):
    db = await get_db()
    try:
        cur = await db.execute(
            "SELECT role, content, created_at FROM messages WHERE session_id=? ORDER BY id",
            (session_id,),
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]
    finally:
        await db.close()

async def save_memory_summary(session_id: str, summary: str):
    db = await get_db()
    try:
        await db.execute(
            "INSERT INTO memory_summaries(session_id, summary) VALUES(?,?)",
            (session_id, summary),
        )
        await db.commit()
    finally:
        await db.close()

async def get_latest_summary(session_id: str) -> str:
    db = await get_db()
    try:
        cur = await db.execute(
            "SELECT summary FROM memory_summaries WHERE session_id=? ORDER BY id DESC LIMIT 1",
            (session_id,),
        )
        row = await cur.fetchone()
        return row["summary"] if row else ""
    finally:
        await db.close()
