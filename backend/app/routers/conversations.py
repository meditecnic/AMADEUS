"""Durable, worldline-scoped conversation HTTP API."""

from __future__ import annotations
from contextlib import asynccontextmanager

from fastapi import APIRouter, HTTPException, Response, status
from pydantic import BaseModel, Field

from app import models
from app.db import normalize_worldline
from app.db import normalize_identity_mode
from app.services.conversations import (
    ConversationNotFound,
    IdentityModeImmutableError,
    conversation_service,
)
from app.services.model_catalog import ModelUnavailableError
from app.services.provider_runtime import provider_registry


router = APIRouter(prefix="/api/conversations", tags=["conversations"])


@asynccontextmanager
async def _erasure_runtime(conversation_id: str, session_id: str, worldline: str, *, deleting: bool = False):
    await conversation_service.require_owned(conversation_id, session_id, worldline)
    from app.routers.chat_ws import sessions
    from app.services.session_manager import cancel_inflight
    session = sessions.get(session_id)
    if session is None or session.worldline != worldline or session.conversation_id != conversation_id:
        yield
        return
    if getattr(session, 'erasure_pending', False):
        raise HTTPException(status_code=409, detail={'code':'conversation_erasure_pending'})
    session.erasure_pending = True
    session.history_epoch += 1
    session.current_epoch += 1
    session.revision = session.current_epoch
    try:
        if session.active_segment_pipeline is not None:
            await session.active_segment_pipeline.cancel(reason='superseded_generation')
        await cancel_inflight(session)
        session.history = []
        session.memory_summary = ''
        yield
        if deleting and session.worldline == worldline and session.conversation_id == conversation_id:
            await session.load_from_db()
        if not deleting and session.worldline == worldline and session.conversation_id == conversation_id:
            session.content_epoch = await models.get_conversation_content_epoch(
                session_id, worldline=worldline, conversation_id=conversation_id,
            )
    finally:
        session.erasure_pending = False


class ConversationCreate(BaseModel):
    session_id: str = Field(min_length=1, max_length=200)
    worldline: str = "steins_gate"
    title: str | None = Field(default=None, max_length=200)
    provider_id: str | None = None
    model_id: str | None = Field(default=None, max_length=200)
    # Q20-A default okabe when omitted; only okabe|self accepted.
    identity_mode: str | None = Field(default=None, max_length=32)


class ConversationPatch(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    is_pinned: bool | None = None
    provider_id: str | None = None
    model_id: str | None = Field(default=None, max_length=200)
    # Present only so clients that send it get an explicit immutable error (Q21).
    identity_mode: str | None = Field(default=None, max_length=32)


class ConversationForget(BaseModel):
    forget_long_term: bool = False


def _not_found(exc: ConversationNotFound) -> HTTPException:
    return HTTPException(status_code=404, detail={"code": "conversation_not_found"})


def _provider(provider_id: str, model_id: str | None = None):
    try:
        return provider_registry.snapshot(provider_id, model_id)
    except KeyError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "unknown_provider"},
        ) from exc
    except ModelUnavailableError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "model_unavailable"},
        ) from exc


@router.get("")
async def list_conversations(
    session_id: str,
    worldline: str = "steins_gate",
    query: str | None = None,
):
    wl = normalize_worldline(worldline)
    rows = await conversation_service.list_for_session(
        session_id,
        wl,
        query=query,
    )
    selected = await conversation_service.get_selected(session_id, wl)
    for row in rows:
        row["is_selected"] = selected is not None and row["id"] == selected["id"]
    return rows


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_conversation(request: ConversationCreate):
    wl = normalize_worldline(request.worldline)
    provider_id = request.provider_id or "deepseek"
    provider = _provider(provider_id, request.model_id)
    try:
        identity_mode = normalize_identity_mode(request.identity_mode)
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "invalid_identity_mode"},
        ) from exc
    return await conversation_service.create(
        request.session_id,
        wl,
        title=request.title,
        provider_id=provider_id,
        model_id=provider.model_id,
        identity_mode=identity_mode,
    )


@router.get("/{conversation_id}")
async def get_conversation(
    conversation_id: str,
    session_id: str,
    worldline: str = "steins_gate",
):
    try:
        return await conversation_service.require_owned(
            conversation_id,
            session_id,
            normalize_worldline(worldline),
        )
    except ConversationNotFound as exc:
        raise _not_found(exc) from exc


@router.patch("/{conversation_id}")
async def patch_conversation(
    conversation_id: str,
    request: ConversationPatch,
    session_id: str,
    worldline: str = "steins_gate",
):
    wl = normalize_worldline(worldline)
    try:
        current = await conversation_service.require_owned(
            conversation_id, session_id, wl
        )
    except ConversationNotFound as exc:
        raise _not_found(exc) from exc
    changes = request.model_dump(exclude_unset=True)
    if "identity_mode" in changes:
        raise HTTPException(
            status_code=422,
            detail={"code": "identity_mode_immutable"},
        )
    if "title" in changes and not str(changes["title"] or "").strip():
        raise HTTPException(status_code=422, detail={"code": "invalid_title"})
    if "provider_id" in changes or "model_id" in changes:
        provider_id = str(
            changes.get("provider_id") or current.get("provider_id") or "deepseek"
        )
        provider = _provider(provider_id, changes.get("model_id"))
        changes["provider_id"] = provider_id
        changes["model_id"] = provider.model_id
    try:
        return await conversation_service.update(
            conversation_id,
            session_id,
            wl,
            changes,
        )
    except IdentityModeImmutableError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "identity_mode_immutable"},
        ) from exc


@router.delete("/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_conversation(
    conversation_id: str,
    session_id: str,
    worldline: str = "steins_gate",
):
    try:
        async with _erasure_runtime(conversation_id, session_id, normalize_worldline(worldline), deleting=True):
            await conversation_service.delete(conversation_id, session_id, normalize_worldline(worldline))
    except ConversationNotFound as exc:
        raise _not_found(exc) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{conversation_id}/messages")
async def get_conversation_messages(
    conversation_id: str,
    session_id: str,
    worldline: str = "steins_gate",
):
    wl = normalize_worldline(worldline)
    try:
        await conversation_service.require_owned(conversation_id, session_id, wl)
    except ConversationNotFound as exc:
        raise _not_found(exc) from exc
    return await models.get_session_messages(
        session_id,
        worldline=wl,
        conversation_id=conversation_id,
    )


@router.post("/{conversation_id}/forget")
async def forget_conversation(
    conversation_id: str,
    request: ConversationForget,
    session_id: str,
    worldline: str = "steins_gate",
):
    try:
        async with _erasure_runtime(conversation_id, session_id, normalize_worldline(worldline)):
            return await conversation_service.forget(
                conversation_id, session_id, normalize_worldline(worldline),
                forget_long_term=request.forget_long_term,
            )
    except ConversationNotFound as exc:
        raise _not_found(exc) from exc
