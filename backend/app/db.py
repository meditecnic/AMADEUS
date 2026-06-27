import os
import aiosqlite

_initialized_paths = set()

def _resolve_path() -> str:
    # Use environment variable override for testing, otherwise default to APPDATA on Windows
    override = os.environ.get("AMADEUS_DB_PATH")
    if override:
        return override
    appdata = os.environ.get("APPDATA")
    if appdata:
        d = os.path.join(appdata, "Amadeus")
        os.makedirs(d, exist_ok=True)
        return os.path.join(d, "amadeus.db")
    # Fallback for non-Windows platforms
    home = os.path.expanduser("~")
    d = os.path.join(home, ".amadeus")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "amadeus.db")

async def get_db() -> aiosqlite.Connection:
    db = await aiosqlite.connect(_resolve_path())
    db.row_factory = aiosqlite.Row
    await db.execute("PRAGMA journal_mode=WAL")
    return db

async def init_db():
    db_path = _resolve_path()
    if db_path in _initialized_paths:
        return
    db = await get_db()
    try:
        await db.executescript("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, id);
        
        CREATE TABLE IF NOT EXISTS memory_summaries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            summary TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_summary_session ON memory_summaries(session_id, id DESC);
        """)
        await db.commit()
    finally:
        await db.close()
    _initialized_paths.add(db_path)
