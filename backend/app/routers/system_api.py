from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr
from fastapi import APIRouter, HTTPException, Query

from app.db import normalize_worldline, validate_schema
from app.services.memory import SharedFactPromotionError, memory_service
from app.services.session_manager import session_coordinator
from app.services.soul_engine import soul_engine
from app.services.diagnostics import diagnostic_runtime
from app.services.credentials import credential_store
from app.services.search import (
    SEARCH_CREDENTIAL_BY_PROVIDER,
    SEARCH_PROVIDERS,
    normalize_search_provider,
    search_service,
)

router = APIRouter()

SearchProviderLiteral = Literal["tavily", "firecrawl"]


class SwitchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str = "default"
    worldline: str


class ForgetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str = "default"
    worldline: str = "steins_gate"
    scope: str = "all"


class SearchCredentialUpdate(BaseModel):
    """Write-only search credential update for one provider.

    Blank api_key preserves the existing secret (industry standard: never echo keys).
    """

    model_config = ConfigDict(extra="forbid")
    provider: SearchProviderLiteral = "tavily"
    api_key: SecretStr = Field(default=SecretStr(""))


class SearchSettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    active_provider: SearchProviderLiteral


@router.get("/api/search/settings")
async def search_settings():
    return search_service.settings_snapshot()


@router.patch("/api/search/settings")
async def patch_search_settings(update: SearchSettingsUpdate):
    active = search_service.set_active_provider(update.active_provider)
    search_service.credentials_changed()
    snapshot = search_service.settings_snapshot()
    snapshot["active_provider"] = active
    snapshot["provider"] = active
    return snapshot


@router.put("/api/search/credential")
async def save_search_credential(update: SearchCredentialUpdate):
    provider = normalize_search_provider(update.provider)
    cred_id = SEARCH_CREDENTIAL_BY_PROVIDER[provider]
    secret = update.api_key.get_secret_value().strip()
    if secret:
        credential_store.set(cred_id, secret)
        # First successful key for a provider can become active if none preferred.
        if not search_service.provider_configured(search_service.get_active_provider()):
            search_service.set_active_provider(provider)
        search_service.credentials_changed()
    snapshot = search_service.settings_snapshot()
    return {
        "provider": provider,
        "active_provider": snapshot["active_provider"],
        "configured": search_service.provider_configured(provider),
        "providers": snapshot["providers"],
    }


@router.delete("/api/search/credential")
async def delete_search_credential(
    provider: SearchProviderLiteral = Query("tavily"),
):
    normalized = normalize_search_provider(provider)
    cred_id = SEARCH_CREDENTIAL_BY_PROVIDER[normalized]
    credential_store.delete(cred_id)
    # If active provider lost its key, prefer any still-configured paid source.
    if search_service.get_active_provider() == normalized:
        for candidate in SEARCH_PROVIDERS:
            if candidate != normalized and search_service.provider_configured(candidate):
                search_service.set_active_provider(candidate)
                break
    search_service.credentials_changed()
    snapshot = search_service.settings_snapshot()
    return {
        "provider": normalized,
        "active_provider": snapshot["active_provider"],
        "configured": False,
        "providers": snapshot["providers"],
    }


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


@router.get("/api/memory/facts")
async def memory_facts_ledger(
    session_id: str = Query("default"),
    worldline: str = Query("steins_gate"),
):
    """Gate 7A Memory Ledger: worldline self-local facts + all shared facts."""
    try:
        wl = normalize_worldline(worldline)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return await memory_service.list_memory_facts_for_ledger(session_id, wl)


@router.post("/api/memory/facts/{local_fact_id}/promote")
async def promote_memory_fact(
    local_fact_id: int,
    session_id: str = Query("default"),
    worldline: str = Query("steins_gate"),
):
    """Gate 7A explicit promotion. No request body is accepted: the server
    re-reads and re-verifies everything from the persistent stores."""
    try:
        wl = normalize_worldline(worldline)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    try:
        shared_id, changed = await memory_service.promote_local_self_fact(
            session_id, wl, local_fact_id
        )
    except SharedFactPromotionError as exc:
        status = 404 if exc.code == "fact_not_found" else 409
        raise HTTPException(status_code=status, detail=exc.code) from exc
    return {"id": shared_id, "scope": "shared", "changed": changed}


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
