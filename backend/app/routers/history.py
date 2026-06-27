from fastapi import APIRouter
from app import models

router = APIRouter()

@router.get("/api/history/{session_id}")
async def get_history(session_id: str):
    msgs = await models.get_session_messages(session_id)
    summary = await models.get_latest_summary(session_id)
    return {
        "session_id": session_id,
        "summary": summary,
        "messages": msgs
    }
