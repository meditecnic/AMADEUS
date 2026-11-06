from fastapi import APIRouter, HTTPException

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
