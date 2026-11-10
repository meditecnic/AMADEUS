import asyncio

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask
import sqlite3
from typing import Literal

from app.services import backups, data_relocation

router = APIRouter(prefix="/api/data")


async def _call(function, *args):
    try:
        return await data_relocation.run_in_thread(function, *args)
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
    range: Literal["month", "conversation", "all"] = "month",
    conversation_id: str | None = None,
    tz: int = Query(0, ge=-840, le=840),
):
    from app.services.usage_report import usage_report

    if range == "conversation" and not (conversation_id or "").strip():
        raise HTTPException(422, detail={"code": "conversation_id_required"})
    try:
        return await asyncio.to_thread(usage_report, range, conversation_id, tz)
    except (sqlite3.Error, OSError):
        raise HTTPException(500, detail={"code": "usage_unavailable"}) from None


@router.get("/export")
async def export_data(
    kind: Literal["conversations", "memory"],
    format: Literal["md", "json"] = "json",
    worldline: Literal["sg", "beta", "all"] = "all",
    conversation_id: str | None = None,
):
    from app.services.data_export import PreparedExport

    if kind == "memory" and (format != "json" or conversation_id is not None):
        raise HTTPException(422, detail={"code": "invalid_export_scope"})
    if conversation_id is not None and not conversation_id.strip():
        raise HTTPException(422, detail={"code": "invalid_conversation_id"})
    export = PreparedExport(kind, format, worldline, conversation_id)
    try:
        await _call(export.prepare)
    except LookupError:
        raise HTTPException(404, detail={"code": "conversation_not_found"}) from None
    except sqlite3.Error:
        raise HTTPException(500, detail={"code": "export_unavailable"}) from None
    return StreamingResponse(export.stream(), media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{export.filename}"',
                 "Cache-Control": "no-store"},
        background=BackgroundTask(export.close))


@router.delete("/restore")
async def cancel_restore():
    return await _call(backups.cancel_restore)


@router.post("/restore-result/ack")
async def acknowledge_restore_result():
    from app.services.data_startup import acknowledge_restore_result
    return await _call(acknowledge_restore_result)
