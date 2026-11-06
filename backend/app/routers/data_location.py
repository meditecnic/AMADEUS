"""Data-directory APIs; transport authentication is shared with all local APIs."""
from fastapi import APIRouter, HTTPException, Request

from pydantic import BaseModel, ConfigDict, StrictBool

from app.routers.data import _call
from app.services import data_location

router = APIRouter(prefix="/api/data")


@router.get("/location")
async def get_location():
    return await _call(data_location.location)


@router.get("/disk")
async def get_disk():
    return await _call(data_location.disk_usage)


class LocationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str


@router.post("/location/check")
async def check_location(request: LocationRequest):
    return await _call(data_location.check_target, request.path)


class MoveRequest(LocationRequest):
    acknowledge_warnings: StrictBool = False


@router.post("/location/move")
async def move_location(request: MoveRequest):
    import asyncio
    from app import db
    from app.routers.chat_ws import sessions
    from app.services import backups, data_relocation, data_startup

    if not data_location.location()["movable"]:
        raise HTTPException(409, detail={"code": "location_not_movable"})
    if not data_startup.available():
        raise HTTPException(409, detail={"code": "data_unavailable"})
    result = await _call(data_location.check_target, request.path)
    if not result["ok"]:
        raise HTTPException(422, detail=result)
    if result["warnings"] and request.acknowledge_warnings is not True:
        raise HTTPException(409, detail={"code": "warnings_not_acknowledged", **result})
    # No await between admission checks and closing the gate.
    if data_relocation.relocating():
        raise HTTPException(409, detail={"code": "data_relocating"})
    if any(s.active_task is not None and not s.active_task.done() for s in sessions.values()):
        raise HTTPException(409, detail={"code": "conversation_active"})
    if (db.get_data_root() / "restore-request.json").exists():
        raise HTTPException(409, detail={"code": "restore_pending"})
    if not backups._lock.acquire(blocking=False):
        raise HTTPException(409, detail={"code": "backup_busy"})
    backups._lock.release()
    data_relocation.begin()

    request_task = asyncio.current_task()

    async def relocate():
        try:
            await data_relocation.drain(request_task)
            if (db.get_data_root() / "restore-request.json").exists():
                raise HTTPException(409, detail={"code": "restore_pending"})
            return await _call(data_location.copy_and_switch, request.path)
        except Exception:
            data_relocation.abort()
            if data_relocation.resume_background is not None:
                await data_relocation.resume_background()
            raise
    # A disconnected requester must not reopen admission while the copy thread runs.
    return await asyncio.shield(relocate())


@router.delete("/location/previous")
async def delete_previous(request: Request):
    if request.query_params or await request.body():
        raise HTTPException(422, detail={"code": "invalid_delete_request"})
    return await _call(data_location.delete_previous)
