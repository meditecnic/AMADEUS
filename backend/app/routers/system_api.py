from __future__ import annotations

from pydantic import BaseModel, ConfigDict
from fastapi import APIRouter, HTTPException, Query

from app.db import normalize_worldline, validate_schema
from app.services.memory import memory_service
from app.services.session_manager import session_coordinator
from app.services.soul_engine import soul_engine
from app.services.diagnostics import diagnostic_runtime

router = APIRouter()


class SwitchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str = "default"
    worldline: str


class ForgetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str = "default"
    worldline: str = "steins_gate"
    scope: str = "all"


@router.post("/api/worldline/switch")
async def switch_worldline(request: SwitchRequest):
    from app.routers.chat_ws import SessionState, sessions, sessions_creation_lock
    try:
        target = normalize_worldline(request.worldline)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    async with sessions_creation_lock:
        session = sessions.setdefault(request.session_id, SessionState(request.session_id))
    return await session_coordinator.switch(session, target)


@router.get("/api/worldline/current")
async def current_worldline(session_id: str = Query("default")):
    worldline, revision = await session_coordinator.get_current(session_id)
    return {"session_id": session_id, "worldline": worldline, "revision": revision}


@router.get("/api/memory/status")
async def memory_status(session_id: str = Query("default"), worldline: str = Query("steins_gate")):
    try:
        return await memory_service.status(session_id, normalize_worldline(worldline))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/api/memory/forget")
async def forget_memory(request: ForgetRequest):
    if request.scope not in {"all", "episodic", "core", "emotion"}:
        raise HTTPException(status_code=422, detail="scope must be all, episodic, core, or emotion")
    await memory_service.forget(request.session_id, normalize_worldline(request.worldline), request.scope)
    return {"status": "forgotten", "scope": request.scope, "worldline": normalize_worldline(request.worldline)}


@router.get("/health/dependencies")
async def dependencies_health():
    from app.services.search import search_service
    from app.services.speech import speech_service
    from app.services.tts_sidecar import sidecar_supervisor
    schema = await validate_schema()
    soul = soul_engine.readiness()
    dependencies = {
        "database": schema,
        "soul": soul,
        "embedding": memory_service.embedder.readiness(),
        "search": await search_service.health(),
        "stt": speech_service.readiness(),
        "tts": await sidecar_supervisor.health(),
        "diagnostics": diagnostic_runtime.public_health(),
    }
    required_ok = bool(schema.get("ok") and soul.get("ok"))
    if not required_ok:
        status = "not_ready"
    elif (
        not dependencies["tts"].get("ok")
        or dependencies["diagnostics"]["state"] != "ready"
    ):
        status = "degraded"
    else:
        status = "ready"
    return {"status": status, "dependencies": dependencies}
