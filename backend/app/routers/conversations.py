"""Durable, worldline-scoped conversation HTTP API."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Response, status
from pydantic import BaseModel, Field

from app import models
from app.db import normalize_worldline
from app.services.conversations import (
    ConversationNotFound,
    DefaultConversationError,
    conversation_service,
)
from app.services.provider_runtime import provider_registry


router = APIRouter(prefix="/api/conversations", tags=["conversations"])


class ConversationCreate(BaseModel):
    session_id: str = Field(min_length=1, max_length=200)
    worldline: str = "steins_gate"
    title: str | None = Field(default=None, max_length=200)
    provider_id: str | None = None
    model_id: str | None = Field(default=None, max_length=200)


class ConversationPatch(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    is_pinned: bool | None = None
    provider_id: str | None = None
    model_id: str | None = Field(default=None, max_length=200)


class ConversationForget(BaseModel):
    forget_long_term: bool = False


def _not_found(exc: ConversationNotFound) -> HTTPException:
    return HTTPException(status_code=404, detail={"code": "conversation_not_found"})


def _provider(provider_id: str):
    try:
        return provider_registry.snapshot(provider_id)
    except KeyError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "unknown_provider"},
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
        row["is_selected"] = row["id"] == selected["id"]
    return rows


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_conversation(request: ConversationCreate):
    wl = normalize_worldline(request.worldline)
    provider_id = request.provider_id or "deepseek"
    provider = _provider(provider_id)
    return await conversation_service.create(
        request.session_id,
        wl,
        title=request.title,
        provider_id=provider_id,
        model_id=request.model_id or provider.model_id,
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
    if "title" in changes and not str(changes["title"] or "").strip():
        raise HTTPException(status_code=422, detail={"code": "invalid_title"})
    if "provider_id" in changes or "model_id" in changes:
        provider_id = str(
            changes.get("provider_id") or current.get("provider_id") or "deepseek"
        )
        provider = _provider(provider_id)
        changes["provider_id"] = provider_id
        if not changes.get("model_id"):
            changes["model_id"] = provider.model_id
    return await conversation_service.update(
        conversation_id,
        session_id,
        wl,
        changes,
    )


@router.delete("/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_conversation(
    conversation_id: str,
    session_id: str,
    worldline: str = "steins_gate",
):
    try:
        await conversation_service.delete(
            conversation_id,
            session_id,
            normalize_worldline(worldline),
        )
    except ConversationNotFound as exc:
        raise _not_found(exc) from exc
    except DefaultConversationError as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": "default_conversation"},
        ) from exc
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
        return await conversation_service.forget(
            conversation_id,
            session_id,
            normalize_worldline(worldline),
            forget_long_term=request.forget_long_term,
        )
    except ConversationNotFound as exc:
        raise _not_found(exc) from exc
