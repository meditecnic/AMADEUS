import asyncio

from fastapi import APIRouter, HTTPException, Query
import sqlite3
from typing import Literal

from app.services import backups

router = APIRouter(prefix="/api/data")


async def _call(function, *args):
    try:
        return await asyncio.to_thread(function, *args)
    except backups.BackupError as exc:
        raise HTTPException(exc.status, detail={"code": exc.code, "message": str(exc)}) from exc
    except OSError as exc:
        raise HTTPException(500, detail={"code": "data_io_failed", "message": str(exc)}) from exc


@router.get("/status")
async def data_status():
    from app.services.data_startup import current_status

    return {**current_status(), "backups": await _call(backups.list_backups)}


@router.get("/backups")
async def get_backups():
    return await _call(backups.list_backups)


@router.post("/backups")
async def create_backup():
    from app.routers.chat_ws import sessions

    if any(s.active_task is not None and not s.active_task.done() for s in sessions.values()):
        raise HTTPException(409, detail={"code": "conversation_active"})
    return await _call(backups.create_backup)


@router.post("/backups/{backup_id}/restore")
async def restore_backup(backup_id: str):
    return await _call(backups.schedule_restore, backup_id)


@router.delete("/backups/{backup_id}")
async def delete_backup(backup_id: str):
    return await _call(backups.delete_backup, backup_id)


@router.get("/usage")
async def get_usage(
    range: Literal["month", "session", "all"] = "month",
    session_id: str | None = None,
    tz: int = Query(0, ge=-840, le=840),
):
    from app.services.usage_report import usage_report

    if range == "session" and not (session_id or "").strip():
        raise HTTPException(422, detail={"code": "session_id_required"})
    try:
        return await asyncio.to_thread(usage_report, range, session_id, tz)
    except (sqlite3.Error, OSError):
        raise HTTPException(500, detail={"code": "usage_unavailable"}) from None
