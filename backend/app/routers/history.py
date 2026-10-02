from fastapi import APIRouter, HTTPException
from app import models
from app.services.conversations import ConversationNotFound

router = APIRouter()

@router.get("/api/history/{session_id}")
async def get_history(session_id: str, worldline: str = "steins_gate"):
    from app.db import normalize_worldline
    wl = normalize_worldline(worldline)
    try:
        msgs = await models.get_session_messages(session_id, worldline=wl)
        summary = await models.get_latest_summary(session_id, worldline=wl)
    except ConversationNotFound as exc:
        raise HTTPException(status_code=404, detail='conversation not found') from exc
    return {
        "session_id": session_id,
        "worldline": wl,
        "summary": summary,
        "messages": msgs
    }
