from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr
from fastapi import APIRouter, HTTPException, Query, Request

from app.db import normalize_identity_mode, normalize_worldline, validate_schema
from app.services.memory import (
    DismissError,
    MemoryDeleteError,
    SharedFactPromotionError,
    ReclassifyError,
    memory_service,
)
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


class DismissBatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ids: list[int] = Field(default_factory=list, max_length=100)


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
async def memory_status(
    session_id: str | None = Query(None),
    worldline: str | None = Query(None),
    identity_mode: str | None = Query(None),
):
    """Memory status.

    With explicit ``identity_mode`` → v11 scoped job aggregates (S3a).
    Without → legacy Quiet Ingest counts (existing Ledger clients).
    """
    if identity_mode is not None:
        session_id = _require_session_id(session_id)
        wl, mode = _validate_memory_scope(worldline, identity_mode)
        from app.services.memory_v11.contracts import MemoryValidationError
        from app.services.memory_v11.repository import job_status_summary
        from app.services.memory_v11.jobs import memory_mode

        try:
            summary = await job_status_summary(
                session_id=session_id,
                worldline=wl,
                identity_mode=mode,
            )
            return {**summary, 'runtime_mode': memory_mode()}
        except MemoryValidationError as exc:
            raise HTTPException(
                status_code=422,
                detail={"code": exc.code, "message": str(exc)},
            ) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    legacy_session_id = "default" if session_id is None else session_id
    legacy_worldline = "steins_gate" if worldline is None else worldline
    try:
        wl = normalize_worldline(legacy_worldline)
        return await memory_service.status(legacy_session_id, wl)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/api/memory/jobs/{job_id}/retry")
async def retry_memory_job_endpoint(
    job_id: str,
    session_id: str = Query(...),
    worldline: str = Query("steins_gate"),
    identity_mode: str = Query(...),
):
    """Retry a failed v11 ingest job; requires full scope ownership match."""
    from app.services.memory_v11.jobs import retry_memory_job

    try:
        wl = normalize_worldline(worldline)
        mode = normalize_identity_mode(identity_mode)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    try:
        job = await retry_memory_job(
            job_id=job_id,
            session_id=session_id,
            worldline=wl,
            identity_mode=mode,
        )
    except KeyError as exc:
        raise HTTPException(
            status_code=404,
            detail={"code": "job_not_found", "message": str(exc)},
        ) from exc
    except PermissionError as exc:
        raise HTTPException(
            status_code=403,
            detail={"code": "scope_mismatch", "message": str(exc)},
        ) from exc
    return {
        "job_id": job["job_id"],
        "state": job["state"],
        "session_id": job["session_id"],
        "worldline": job.get("worldline", wl),
        "identity_mode": job["identity_mode"],
        "attempt_count": job.get("attempt_count", 0),
        "last_error_code": job.get("last_error_code"),
    }


@router.post("/api/memory/forget")
async def forget_memory(request: ForgetRequest):
    if request.scope not in {"all", "episodic", "core", "emotion"}:
        raise HTTPException(status_code=422, detail="scope must be all, episodic, core, or emotion")
    await memory_service.forget(request.session_id, normalize_worldline(request.worldline), request.scope)
    return {"status": "forgotten", "scope": request.scope, "worldline": normalize_worldline(request.worldline)}


@router.get("/api/memory/facts")
async def memory_facts_ledger(
    session_id: str | None = Query(None),
    worldline: str | None = Query(None),
    identity_mode: str | None = Query(None),
    query: str | None = Query(None),
    topic_id: str | None = Query(None),
    pinned_only: bool = Query(False),
    limit: str | None = Query(None),
    offset: str | None = Query(None),
):
    """Memory facts list.

    With explicit ``identity_mode`` → v11 scoped active stable facts (S3a) with
    bounded offset pagination (S3F-2B).
    Without → legacy Gate 7A Ledger (local/shared/okabe) for existing UI.
    """
    if identity_mode is not None:
        session_id = _require_session_id(session_id)
        wl, mode = _validate_memory_scope(worldline, identity_mode)
        from app.services.memory_v11.contracts import MemoryValidationError
        from app.services.memory_v11.repository import list_active_stable_facts_page

        try:
            lim = _parse_pagination_param("limit", limit, 20, 1, 100)
            off = _parse_pagination_param("offset", offset, 0, 0, None)
            page = await list_active_stable_facts_page(
                session_id=session_id,
                worldline=wl,
                identity_mode=mode,
                query=query,
                topic_id=topic_id,
                pinned_only=pinned_only,
                limit=lim,
                offset=off,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except MemoryValidationError as exc:
            raise HTTPException(
                status_code=422,
                detail={"code": exc.code, "message": str(exc)},
            ) from exc
        facts = list(page["facts"])
        total = int(page["total"])
        return {
            "facts": facts,
            "scope": {
                "session_id": session_id,
                "worldline": wl,
                "identity_mode": mode,
            },
            "pagination": {
                "limit": lim,
                "offset": off,
                "total": total,
                "has_more": off + len(facts) < total,
            },
        }
    legacy_session_id = "default" if session_id is None else session_id
    legacy_worldline = "steins_gate" if worldline is None else worldline
    try:
        wl = normalize_worldline(legacy_worldline)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return await memory_service.list_memory_facts_for_ledger(legacy_session_id, wl)


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


@router.post("/api/memory/facts/{okabe_fact_id}/reclassify")
async def reclassify_okabe_fact(
    okabe_fact_id: int,
    session_id: str = Query("default"),
    worldline: str = Query("steins_gate"),
    confirmed: bool = Query(False),
):
    """Gate 7B explicit reclassify: okabe → self local. Copy semantics."""
    try:
        wl = normalize_worldline(worldline)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    try:
        self_fact_id, changed = await memory_service.reclassify_okabe_fact_to_self(
            session_id, wl, okabe_fact_id, confirmed=confirmed
        )
    except ReclassifyError as exc:
        status = 404 if exc.code == "fact_not_found" else 409
        raise HTTPException(status_code=status, detail=exc.code) from exc
    return {"id": self_fact_id, "scope": "self", "changed": changed}


@router.post("/api/memory/facts/{okabe_fact_id}/dismiss")
async def dismiss_okabe_fact(
    okabe_fact_id: int,
    session_id: str = Query("default"),
    worldline: str = Query("steins_gate"),
):
    """M3 soft-dismiss: hide okabe candidate from ledger and prompt; keep audit row."""
    try:
        wl = normalize_worldline(worldline)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    try:
        fact_id, changed = await memory_service.dismiss_okabe_fact(
            session_id, wl, okabe_fact_id
        )
    except DismissError as exc:
        status = 404 if exc.code == "fact_not_found" else 409
        raise HTTPException(status_code=status, detail=exc.code) from exc
    return {"id": fact_id, "scope": "okabe", "changed": changed, "dismissed": True}


@router.post("/api/memory/facts/dismiss-batch")
async def dismiss_okabe_facts_batch(
    body: DismissBatchRequest,
    session_id: str = Query("default"),
    worldline: str = Query("steins_gate"),
):
    """M3 bulk soft-dismiss. Body: {\"ids\": [1,2,3]}."""
    try:
        wl = normalize_worldline(worldline)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    try:
        result = await memory_service.dismiss_okabe_facts_bulk(
            session_id, wl, list(body.ids)
        )
    except DismissError as exc:
        status = 404 if exc.code == "fact_not_found" else 409
        raise HTTPException(status_code=status, detail=exc.code) from exc
    return result


@router.post("/api/memory/facts/{local_fact_id}/delete-local")
async def delete_local_memory_fact(
    local_fact_id: int,
    session_id: str = Query("default"),
    worldline: str = Query("steins_gate"),
):
    """Soft-delete one self-local established fact. Does not touch shared."""
    try:
        wl = normalize_worldline(worldline)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    try:
        fact_id, changed = await memory_service.delete_local_self_fact(
            session_id, wl, local_fact_id
        )
    except MemoryDeleteError as exc:
        status = 404 if exc.code == "fact_not_found" else 409
        raise HTTPException(status_code=status, detail=exc.code) from exc
    return {"id": fact_id, "scope": "local", "changed": changed, "deleted": True}


@router.post("/api/memory/shared-facts/{shared_fact_id}/delete")
async def delete_shared_memory_fact(
    shared_fact_id: int,
    session_id: str = Query("default"),
):
    """Soft-delete one shared profile fact. Does not touch worldline-local copies."""
    try:
        fact_id, changed = await memory_service.delete_shared_fact(
            session_id, shared_fact_id
        )
    except MemoryDeleteError as exc:
        status = 404 if exc.code == "fact_not_found" else 409
        raise HTTPException(status_code=status, detail=exc.code) from exc
    return {"id": fact_id, "scope": "shared", "changed": changed, "deleted": True}


# Caller-input validation codes (everything else is server/indexing side and
# must never be reported as user validation).
_USER_VALIDATION_CODES = frozenset(
    {
        "empty_session_id",
        "empty_fact_id",
        "empty_display_text",
        "missing_expected_version",
        "invalid_expected_version",
        "missing_display_label",
        "invalid_display_label",
        "empty_display_label",
        "invalid_is_pinned",
        "invalid_topic_id",
        "empty_topic_id",
    }
)


def _memory_error_response(exc: MemoryValidationError) -> HTTPException:
    code = exc.code
    if code in {
        "fact_not_found",
        "topic_not_found",
        "experience_not_found",
        "observation_not_found",
    }:
        return HTTPException(
            status_code=404, detail={"code": code, "message": str(exc)}
        )
    if code in {
        "expected_version_mismatch",
        "pin_limit_reached",
        "topic_label_conflict",
        "experience_delete_pending",
        "observation_not_ignorable",
    }:
        return HTTPException(
            status_code=409, detail={"code": code, "message": str(exc)}
        )
    if code in _USER_VALIDATION_CODES:
        return HTTPException(
            status_code=422, detail={"code": code, "message": str(exc)}
        )
    # Indexing/server failures: stable non-2xx, no raw internals or provider
    # bodies in the response.
    return HTTPException(
        status_code=500,
        detail={"code": code, "message": "memory operation failed"},
    )


def _validate_memory_scope(
    worldline: str | None, identity_mode: str | None
) -> tuple[str, str]:
    """Canonical 422 envelopes for scope normalization (never bare strings).

    The v11 lifecycle/presentation/topic routes require EXPLICIT scope:
    blank/missing worldline or identity is a caller error, never a silent
    default.
    """
    if worldline is None or str(worldline).strip() == "":
        raise HTTPException(
            status_code=422,
            detail={"code": "missing_worldline", "message": "worldline is required"},
        )
    try:
        wl = normalize_worldline(worldline)
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail={"code": "invalid_worldline", "message": "invalid worldline"},
        ) from None
    if identity_mode is None or str(identity_mode).strip() == "":
        raise HTTPException(
            status_code=422,
            detail={"code": "missing_identity_mode", "message": "identity_mode is required"},
        )
    try:
        mode = normalize_identity_mode(identity_mode)
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail={"code": "invalid_identity_mode", "message": "invalid identity_mode"},
        ) from None
    return wl, mode


def _require_session_id(session_id: str | None) -> str:
    sid = (session_id or "").strip()
    if not sid:
        raise HTTPException(
            status_code=422,
            detail={"code": "empty_session_id", "message": "session_id is required"},
        )
    return sid


def _body_error(code: str, message: str) -> HTTPException:
    return HTTPException(status_code=422, detail={"code": code, "message": message})


def _parse_pagination_param(
    name: str,
    raw: str | None,
    default: int,
    minimum: int,
    maximum: int | None,
) -> int:
    """Deterministic local paging parse → stable invalid_<name> envelope."""
    if raw is None or str(raw).strip() == "":
        return default
    try:
        value = int(str(raw))
    except (TypeError, ValueError):
        raise _body_error(f"invalid_{name}", f"{name} must be an integer")
    if value < minimum:
        raise _body_error(f"invalid_{name}", f"{name} must be >= {minimum}")
    if maximum is not None and value > maximum:
        raise _body_error(f"invalid_{name}", f"{name} must be <= {maximum}")
    return value


@router.patch("/api/memory/facts/{fact_id}")
async def patch_memory_fact(
    fact_id: str,
    request: Request,
    session_id: str | None = Query(None),
    worldline: str | None = Query(None),
    identity_mode: str | None = Query(None),
):
    """v11 user edit (plan §7.1): new ``user_edit`` version on the same fact.

    Full explicit scope; stale expected_version → 409; no provider/LLM call
    (deterministic canonical semantic payload in this slice). Body parsing is
    local so every caller-validation failure returns the stable detail.code
    envelope instead of FastAPI's raw validation array.
    """
    from app.services.memory_v11.contracts import MemoryValidationError
    from app.services.memory_v11.facts import user_edit_fact

    wl, mode = _validate_memory_scope(worldline, identity_mode)
    sid = _require_session_id(session_id)

    try:
        raw = await request.json()
    except Exception:
        raise _body_error(
            "invalid_json_body", "request body must be valid JSON"
        ) from None
    if not isinstance(raw, dict):
        raise _body_error(
            "invalid_request_body", "request body must be a JSON object"
        ) from None
    extras = sorted(set(raw) - {"display_text", "expected_version"})
    if extras:
        raise _body_error(
            "extra_fields_forbidden", "unknown body fields are forbidden"
        )
    if "display_text" not in raw:
        raise _body_error("missing_display_text", "display_text is required")
    display_text = raw["display_text"]
    if not isinstance(display_text, str):
        raise _body_error("invalid_display_text", "display_text must be a string")
    text = display_text.strip()
    if not text:
        raise _body_error("empty_display_text", "display_text is required")
    if "expected_version" not in raw:
        raise _body_error("missing_expected_version", "expected_version is required")
    expected_version = raw["expected_version"]
    if isinstance(expected_version, bool) or not isinstance(expected_version, int):
        raise _body_error(
            "invalid_expected_version", "expected_version must be a positive int"
        )
    if expected_version <= 0:
        raise _body_error(
            "invalid_expected_version", "expected_version must be a positive int"
        )

    try:
        return await user_edit_fact(
            session_id=sid,
            worldline=wl,
            identity_mode=mode,
            fact_id=fact_id,
            display_text=text,
            expected_version=expected_version,
        )
    except MemoryValidationError as exc:
        raise _memory_error_response(exc) from exc
    except Exception:
        raise HTTPException(
            status_code=500,
            detail={
                "code": "memory_operation_failed",
                "message": "memory operation failed",
            },
        ) from None


@router.delete("/api/memory/facts/{fact_id}")
async def delete_memory_fact(
    fact_id: str,
    session_id: str | None = Query(None),
    worldline: str | None = Query(None),
    identity_mode: str | None = Query(None),
    expected_version: str | None = Query(None),
):
    """v11 delete (plan §7.2): tombstone + content erasure, idempotent repeat.

    expected_version is read as a raw string so malformed values map to the
    stable detail.code envelope instead of FastAPI's validation array.
    """
    from app.services.memory_v11.contracts import MemoryValidationError
    from app.services.memory_v11.facts import delete_fact

    wl, mode = _validate_memory_scope(worldline, identity_mode)
    sid = _require_session_id(session_id)

    raw_version = (expected_version or "").strip()
    if not raw_version:
        raise _body_error("missing_expected_version", "expected_version is required")
    try:
        version = int(raw_version)
    except ValueError:
        raise _body_error(
            "invalid_expected_version", "expected_version must be a positive int"
        ) from None
    if version <= 0:
        raise _body_error(
            "invalid_expected_version", "expected_version must be a positive int"
        )

    try:
        return await delete_fact(
            session_id=sid,
            worldline=wl,
            identity_mode=mode,
            fact_id=fact_id,
            expected_version=version,
        )
    except MemoryValidationError as exc:
        raise _memory_error_response(exc) from exc
    except Exception:
        raise HTTPException(
            status_code=500,
            detail={
                "code": "memory_operation_failed",
                "message": "memory operation failed",
            },
        ) from None


@router.get("/api/memory/facts/{fact_id}/details")
async def memory_fact_details(
    fact_id: str,
    session_id: str | None = Query(None),
    worldline: str | None = Query(None),
    identity_mode: str | None = Query(None),
):
    """v11 details (plan §8.2): active-fact version history + minimal provenance."""
    from app.services.memory_v11.contracts import MemoryValidationError
    from app.services.memory_v11.facts import fact_details

    wl, mode = _validate_memory_scope(worldline, identity_mode)
    sid = _require_session_id(session_id)

    try:
        return await fact_details(
            session_id=sid,
            worldline=wl,
            identity_mode=mode,
            fact_id=fact_id,
        )
    except MemoryValidationError as exc:
        raise _memory_error_response(exc) from exc
    except Exception:
        raise HTTPException(
            status_code=500,
            detail={
                "code": "memory_operation_failed",
                "message": "memory operation failed",
            },
        ) from None


@router.patch("/api/memory/facts/{fact_id}/presentation")
async def patch_memory_fact_presentation(
    fact_id: str,
    request: Request,
    session_id: str | None = Query(None),
    worldline: str | None = Query(None),
    identity_mode: str | None = Query(None),
):
    """v11 presentation (plan §7.3/§8.2): is_pinned + topic_id only, local."""
    from app.services.memory_v11.contracts import MemoryValidationError
    from app.services.memory_v11.facts import _UNSET, update_fact_presentation

    wl, mode = _validate_memory_scope(worldline, identity_mode)
    sid = _require_session_id(session_id)

    try:
        raw = await request.json()
    except Exception:
        raise _body_error(
            "invalid_json_body", "request body must be valid JSON"
        ) from None
    if not isinstance(raw, dict):
        raise _body_error(
            "invalid_request_body", "request body must be a JSON object"
        ) from None
    extras = sorted(set(raw) - {"is_pinned", "topic_id"})
    if extras:
        raise _body_error("extra_fields_forbidden", "unknown body fields are forbidden")
    if not raw:
        raise _body_error(
            "missing_presentation_fields", "at least one presentation field is required"
        )

    is_pinned: bool | None = None
    if "is_pinned" in raw:
        if not isinstance(raw["is_pinned"], bool):
            raise _body_error("invalid_is_pinned", "is_pinned must be a boolean")
        is_pinned = raw["is_pinned"]
    topic_id: str | None | object = _UNSET  # sentinel: field omitted
    if "topic_id" in raw:
        target = raw["topic_id"]
        if target is not None:
            if not isinstance(target, str):
                raise _body_error(
                    "invalid_topic_id", "topic_id must be a string or null"
                )
            if not str(target).strip():
                raise _body_error(
                    "invalid_topic_id", "topic_id must be a non-empty string or null"
                )
        topic_id = target

    try:
        result = await update_fact_presentation(
            session_id=sid,
            worldline=wl,
            identity_mode=mode,
            fact_id=fact_id,
            is_pinned=is_pinned,
            topic_id=topic_id,
        )
    except MemoryValidationError as exc:
        raise _memory_error_response(exc) from exc
    except Exception:
        raise HTTPException(
            status_code=500,
            detail={
                "code": "memory_operation_failed",
                "message": "memory operation failed",
            },
        ) from None
    return result


@router.get("/api/memory/graph/projection")
async def memory_graph_projection(request: Request):
    """Canonical Constellation Overview projection (S4-MEMORY-SPINE / G61)."""
    from app.services.memory_v11.projection import (
        ProjectionError,
        parse_projection_request,
        project_overview,
    )

    raw: dict[str, list[str]] = {}
    for key, value in request.query_params.multi_items():
        raw.setdefault(key, []).append(value)
    try:
        criteria = parse_projection_request(raw)
        return await project_overview(criteria)
    except ProjectionError as exc:
        status = 422
        if exc.code == "projection_failed":
            status = 500
        if exc.code == "projection_unavailable":
            status = 503
        raise HTTPException(
            status_code=status,
            detail={"code": exc.code, "message": str(exc)},
        ) from exc
    except Exception:
        raise HTTPException(
            status_code=500,
            detail={"code": "projection_failed", "message": "projection failed"},
        ) from None


@router.get("/api/memory/topics")
async def list_memory_topics(
    session_id: str | None = Query(None),
    worldline: str | None = Query(None),
    identity_mode: str | None = Query(None),
):
    """v11 topics list (plan §8.3): scoped, active-fact counts, local only."""
    from app.services.memory_v11.contracts import MemoryValidationError
    from app.services.memory_v11.topics import list_topics

    wl, mode = _validate_memory_scope(worldline, identity_mode)
    sid = _require_session_id(session_id)
    try:
        return await list_topics(
            session_id=sid,
            worldline=wl,
            identity_mode=mode,
        )
    except MemoryValidationError as exc:
        raise _memory_error_response(exc) from exc
    except Exception:
        raise HTTPException(
            status_code=500,
            detail={
                "code": "memory_operation_failed",
                "message": "memory operation failed",
            },
        ) from None


@router.patch("/api/memory/topics/{topic_id}")
async def patch_memory_topic(
    topic_id: str,
    request: Request,
    session_id: str | None = Query(None),
    worldline: str | None = Query(None),
    identity_mode: str | None = Query(None),
):
    """v11 topic rename (plan §8.3): display_label only, no merges, local."""
    from app.services.memory_v11.contracts import MemoryValidationError
    from app.services.memory_v11.topics import rename_topic

    wl, mode = _validate_memory_scope(worldline, identity_mode)
    sid = _require_session_id(session_id)

    try:
        raw = await request.json()
    except Exception:
        raise _body_error(
            "invalid_json_body", "request body must be valid JSON"
        ) from None
    if not isinstance(raw, dict):
        raise _body_error(
            "invalid_request_body", "request body must be a JSON object"
        ) from None
    extras = sorted(set(raw) - {"display_label"})
    if extras:
        raise _body_error("extra_fields_forbidden", "unknown body fields are forbidden")
    if "display_label" not in raw:
        raise _body_error("missing_display_label", "display_label is required")
    if not isinstance(raw["display_label"], str):
        raise _body_error("invalid_display_label", "display_label must be a string")

    try:
        return await rename_topic(
            session_id=sid,
            worldline=wl,
            identity_mode=mode,
            topic_id=topic_id,
            display_label=raw["display_label"],
        )
    except MemoryValidationError as exc:
        raise _memory_error_response(exc) from exc
    except Exception:
        raise HTTPException(
            status_code=500,
            detail={
                "code": "memory_operation_failed",
                "message": "memory operation failed",
            },
        ) from None


@router.get("/api/memory/experiences")
async def list_memory_experiences(
    session_id: str | None = Query(None),
    worldline: str | None = Query(None),
    identity_mode: str | None = Query(None),
    query: str | None = Query(None),
    pinned_only: bool = Query(False),
    limit: str | None = Query(None),
    offset: str | None = Query(None),
):
    """v11 experience browse (plan §8.3 / §10.2): active+expired history only.

    S4-ARCHIVE Gate 2 additive filters: literal ``query`` and ``pinned_only``
    with filter-then-page totals; ordering/admission otherwise unchanged.
    """
    from app.services.memory_v11.contracts import MemoryValidationError
    from app.services.memory_v11.experiences import list_experiences

    wl, mode = _validate_memory_scope(worldline, identity_mode)
    sid = _require_session_id(session_id)
    lim = _parse_pagination_param("limit", limit, 20, 1, 100)
    off = _parse_pagination_param("offset", offset, 0, 0, None)
    try:
        return await list_experiences(
            session_id=sid,
            worldline=wl,
            identity_mode=mode,
            query=query,
            pinned_only=pinned_only,
            limit=lim,
            offset=off,
        )
    except MemoryValidationError as exc:
        raise _memory_error_response(exc) from exc
    except Exception:
        raise HTTPException(
            status_code=500,
            detail={
                "code": "memory_operation_failed",
                "message": "memory operation failed",
            },
        ) from None


@router.get("/api/memory/experiences/{experience_id}/details")
async def memory_experience_details(
    experience_id: str,
    session_id: str | None = Query(None),
    worldline: str | None = Query(None),
    identity_mode: str | None = Query(None),
):
    """v11 experience details (plan §8.3): minimal provenance, local only."""
    from app.services.memory_v11.contracts import MemoryValidationError
    from app.services.memory_v11.experiences import experience_details

    wl, mode = _validate_memory_scope(worldline, identity_mode)
    sid = _require_session_id(session_id)
    try:
        return await experience_details(
            session_id=sid,
            worldline=wl,
            identity_mode=mode,
            experience_id=experience_id,
        )
    except MemoryValidationError as exc:
        raise _memory_error_response(exc) from exc
    except Exception:
        raise HTTPException(
            status_code=500,
            detail={
                "code": "memory_operation_failed",
                "message": "memory operation failed",
            },
        ) from None


@router.patch("/api/memory/experiences/{experience_id}/presentation")
async def patch_memory_experience_presentation(
    experience_id: str,
    request: Request,
    session_id: str | None = Query(None),
    worldline: str | None = Query(None),
    identity_mode: str | None = Query(None),
):
    """S4-ARCHIVE Gate 2: Experience presentation = ``is_pinned`` ONLY.

    No text editor, no version history, no other field: the body grammar is
    frozen to a single boolean so this seam can never grow into an edit
    surface without a new contract.
    """
    from app.services.memory_v11.contracts import MemoryValidationError
    from app.services.memory_v11.experiences import update_experience_presentation

    wl, mode = _validate_memory_scope(worldline, identity_mode)
    sid = _require_session_id(session_id)

    try:
        raw = await request.json()
    except Exception:
        raise _body_error(
            "invalid_json_body", "request body must be valid JSON"
        ) from None
    if not isinstance(raw, dict):
        raise _body_error(
            "invalid_request_body", "request body must be a JSON object"
        ) from None
    extras = sorted(set(raw) - {"is_pinned"})
    if extras:
        raise _body_error("extra_fields_forbidden", "unknown body fields are forbidden")
    if "is_pinned" not in raw:
        raise _body_error(
            "missing_presentation_fields", "is_pinned is required"
        )
    if not isinstance(raw["is_pinned"], bool):
        raise _body_error("invalid_is_pinned", "is_pinned must be a boolean")

    try:
        return await update_experience_presentation(
            session_id=sid,
            worldline=wl,
            identity_mode=mode,
            experience_id=experience_id,
            is_pinned=raw["is_pinned"],
        )
    except MemoryValidationError as exc:
        raise _memory_error_response(exc) from exc
    except Exception:
        raise HTTPException(
            status_code=500,
            detail={
                "code": "memory_operation_failed",
                "message": "memory operation failed",
            },
        ) from None


@router.delete("/api/memory/experiences/{experience_id}")
async def delete_memory_experience(
    experience_id: str,
    session_id: str | None = Query(None),
    worldline: str | None = Query(None),
    identity_mode: str | None = Query(None),
):
    """v11 experience delete (plan §8.3): real erasure, idempotent repeat."""
    from app.services.memory_v11.contracts import MemoryValidationError
    from app.services.memory_v11.experiences import delete_experience

    wl, mode = _validate_memory_scope(worldline, identity_mode)
    sid = _require_session_id(session_id)
    try:
        return await delete_experience(
            session_id=sid,
            worldline=wl,
            identity_mode=mode,
            experience_id=experience_id,
        )
    except MemoryValidationError as exc:
        raise _memory_error_response(exc) from exc
    except Exception:
        raise HTTPException(
            status_code=500,
            detail={
                "code": "memory_operation_failed",
                "message": "memory operation failed",
            },
        ) from None


@router.get("/api/memory/observations")
async def list_memory_observations(
    session_id: str | None = Query(None),
    worldline: str | None = Query(None),
    identity_mode: str | None = Query(None),
    limit: str | None = Query(None),
    offset: str | None = Query(None),
):
    """v11 observation diagnostics (plan §8.3): all statuses, local, bounded."""
    from app.services.memory_v11.contracts import MemoryValidationError
    from app.services.memory_v11.observations import list_observations

    wl, mode = _validate_memory_scope(worldline, identity_mode)
    sid = _require_session_id(session_id)
    lim = _parse_pagination_param("limit", limit, 20, 1, 100)
    off = _parse_pagination_param("offset", offset, 0, 0, None)
    try:
        return await list_observations(
            session_id=sid, worldline=wl, identity_mode=mode, limit=lim, offset=off
        )
    except MemoryValidationError as exc:
        raise _memory_error_response(exc) from exc
    except Exception:
        raise HTTPException(
            status_code=500,
            detail={
                "code": "memory_operation_failed",
                "message": "memory operation failed",
            },
        ) from None


@router.post("/api/memory/observations/{observation_id}/ignore")
async def ignore_memory_observation(
    observation_id: str,
    session_id: str | None = Query(None),
    worldline: str | None = Query(None),
    identity_mode: str | None = Query(None),
):
    """v11 observation ignore (plan §8.3): candidate → ignored, not promote."""
    from app.services.memory_v11.contracts import MemoryValidationError
    from app.services.memory_v11.observations import ignore_observation

    wl, mode = _validate_memory_scope(worldline, identity_mode)
    sid = _require_session_id(session_id)
    try:
        return await ignore_observation(
            session_id=sid,
            worldline=wl,
            identity_mode=mode,
            observation_id=observation_id,
        )
    except MemoryValidationError as exc:
        raise _memory_error_response(exc) from exc
    except Exception:
        raise HTTPException(
            status_code=500,
            detail={
                "code": "memory_operation_failed",
                "message": "memory operation failed",
            },
        ) from None


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
