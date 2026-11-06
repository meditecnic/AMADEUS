"""Startup data readiness, independent of database-backed services."""
from contextlib import closing
import asyncio
import json
import sqlite3

from app import db
from app.services import backups

_root_key: str | None = None
_state = {"status": "ok", "detail": None}
_available = True


def current_status() -> dict:
    return dict(_state) if _root_key == str(db.get_data_root()) else {"status": "ok", "detail": None}


def available() -> bool:
    return _available if _root_key == str(db.get_data_root()) else True


def check_databases() -> None:
    for path in backups.existing_databases():
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=5)) as connection:
            rows = connection.execute("PRAGMA quick_check").fetchall()
            if rows != [("ok",)]:
                raise sqlite3.DatabaseError(f"quick_check failed: {path.name}: {rows[:3]}")


async def initialize() -> tuple[bool, bool]:
    """Return (available, existing installation). Backup failures remain fatal."""
    global _root_key, _state, _available
    _root_key, _available = str(db.get_data_root()), False
    _state = {"status": "ok", "detail": None}
    await asyncio.to_thread(backups.cleanup_staging)
    await asyncio.to_thread(backups.apply_pending_restore)
    result_path = db.get_data_root() / "restore-result.json"
    if result_path.exists():
        try:
            _state = json.loads(result_path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            _state = {"status": "restore_failed", "detail": "Cannot read restore result"}
    # An incomplete rollback retains its journal; never open databases over it.
    if (db.get_data_root() / "restore-request.json").exists():
        return False, True
    had_databases = False
    backups.upgrade_backup_id = None
    try:
        had_databases = bool(await asyncio.to_thread(backups.existing_databases))
        await asyncio.to_thread(check_databases)
        await db.init_db()
    except backups.BackupError:
        raise  # No backup => no migration, startup exits nonzero.
    except Exception as exc:
        detail = str(exc)
        if backups.upgrade_backup_id:
            try:
                await asyncio.to_thread(backups.schedule_restore, backups.upgrade_backup_id)
                failure = await asyncio.to_thread(backups.apply_pending_restore)
                detail += f"; rollback failed: {failure}" if failure else "; restored pre-upgrade backup"
                db.reset_initialization_cache()
            except Exception as rollback_error:
                detail += f"; rollback failed: {rollback_error}"
        if _state["status"] != "restore_failed":
            _state["status"] = "corrupt"
        _state["detail"] = detail
        return False, had_databases
    _available = True
    return True, had_databases
