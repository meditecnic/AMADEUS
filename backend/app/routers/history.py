from fastapi import APIRouter
from app import models

router = APIRouter()

@router.get("/api/history/{session_id}")
async def get_history(session_id: str, worldline: str = "steins_gate"):
    from app.db import normalize_worldline
    wl = normalize_worldline(worldline)
    msgs = await models.get_session_messages(session_id, worldline=wl)
    summary = await models.get_latest_summary(session_id, worldline=wl)
    return {
        "session_id": session_id,
        "worldline": wl,
        "summary": summary,
        "messages": msgs
    }
