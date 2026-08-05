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
    session_id: str = Query("default"),
    worldline: str = Query("steins_gate"),
    identity_mode: str | None = Query(None),
):
    """Memory status.

    With explicit ``identity_mode`` → v11 scoped job aggregates (S3a).
    Without → legacy Quiet Ingest counts (existing Ledger clients).
    """
    try:
        wl = normalize_worldline(worldline)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if identity_mode is not None and str(identity_mode).strip() != "":
        from app.services.memory_v11.contracts import MemoryValidationError
        from app.services.memory_v11.repository import job_status_summary

        try:
            mode = normalize_identity_mode(identity_mode)
            return await job_status_summary(
                session_id=session_id,
                worldline=wl,
                identity_mode=mode,
            )
        except MemoryValidationError as exc:
            raise HTTPException(
                status_code=422,
                detail={"code": exc.code, "message": str(exc)},
            ) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    try:
        return await memory_service.status(session_id, wl)
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
    session_id: str = Query("default"),
    worldline: str = Query("steins_gate"),
    identity_mode: str | None = Query(None),
    query: str | None = Query(None),
    topic_id: str | None = Query(None),
    pinned_only: bool = Query(False),
):
    """Memory facts list.

    With explicit ``identity_mode`` → v11 scoped active stable facts (S3a).
    Without → legacy Gate 7A Ledger (local/shared/okabe) for existing UI.
    """
    try:
        wl = normalize_worldline(worldline)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if identity_mode is not None and str(identity_mode).strip() != "":
        from app.services.memory_v11.contracts import MemoryValidationError
        from app.services.memory_v11.repository import list_active_stable_facts

        try:
            mode = normalize_identity_mode(identity_mode)
            facts = await list_active_stable_facts(
                session_id=session_id,
                worldline=wl,
                identity_mode=mode,
                query=query,
                topic_id=topic_id,
                pinned_only=pinned_only,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except MemoryValidationError as exc:
            raise HTTPException(
                status_code=422,
                detail={"code": exc.code, "message": str(exc)},
            ) from exc
        return {
            "facts": facts,
            "scope": {
                "session_id": session_id,
                "worldline": wl,
                "identity_mode": mode,
            },
        }
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
    }
)


def _memory_error_response(exc: MemoryValidationError) -> HTTPException:
    code = exc.code
    if code == "fact_not_found":
        return HTTPException(
            status_code=404, detail={"code": code, "message": str(exc)}
        )
    if code == "expected_version_mismatch":
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
    """Canonical 422 envelopes for scope normalization (never bare strings)."""
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
            detail={"code": "invalid_identity_mode", "message": "invalid identity_mode"},
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


@router.patch("/api/memory/facts/{fact_id}")
async def patch_memory_fact(
    fact_id: str,
    request: Request,
    session_id: str | None = Query(None),
    worldline: str = Query("steins_gate"),
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
    worldline: str = Query("steins_gate"),
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
    worldline: str = Query("steins_gate"),
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
