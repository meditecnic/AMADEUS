"""Secret-free provider metadata and write-only credential endpoints."""

from __future__ import annotations

import asyncio
from contextlib import aclosing
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, SecretStr

from app.db import (
    SelectorAdmission,
    get_provider_configuration,
    list_provider_configurations,
    save_provider_configuration,
    soft_disable_provider_configuration,
    touch_provider_configuration_version,
    update_provider_selector_admission,
)
from app.services.credentials import credential_store
from app.services.model_catalog import (
    ModelUnavailableError,
    is_user_custom_provider,
    model_catalog,
)
from app.services.provider_adapters import ProviderRequestError
from app.services.provider_registry import ProviderModelCache, ProviderTask
from app.services.provider_runtime import (
    configure_custom_provider,
    is_custom_profile_id,
    new_custom_provider_id,
    prepare_custom_provider_settings,
    provider_registry,
    unregister_custom_provider,
)


router = APIRouter(prefix="/api/providers", tags=["providers"])
provider_model_cache = ProviderModelCache()


class CredentialUpdate(BaseModel):
    api_key: SecretStr


class ChatCheckRequest(BaseModel):
    model_id: str = Field(min_length=1, max_length=200)


class CustomProviderConfiguration(BaseModel):
    base_url: str
    default_model: str
    display_name: str | None = None


class CustomProfileCreate(BaseModel):
    display_name: str = Field(min_length=1, max_length=80)
    base_url: str
    default_model: str


class CustomProfileUpdate(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=80)
    base_url: str | None = None
    default_model: str | None = None
    selector_admission: SelectorAdmission | None = None
    expected_configuration_version: str | None = Field(default=None, min_length=1)


def _definition(provider_id: str):
    try:
        return provider_registry.snapshot(provider_id, allow_unavailable=True)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def _credential_configured(provider_id: str) -> bool:
    try:
        return credential_store.get(provider_id) is not None
    except ValueError:
        return False


def _provider_row(definition) -> dict[str, Any]:
    snapshot = provider_registry.snapshot(
        definition.id,
        allow_unavailable=True,
    )
    source = "user_config" if is_user_custom_provider(definition.id) else "builtin"
    return {
        "id": definition.id,
        "display_name": definition.display_name,
        "default_model": definition.default_model,
        "source": source,
        "catalog_version": model_catalog.version,
        "catalog_verified_at": model_catalog.verified_at,
        "default_model_capability": model_catalog.public_model(
            definition.id,
            definition.default_model,
        ),
        "preferred_model_id": model_catalog.preferred_id(definition.id),
        "catalog": model_catalog.list_provider(definition.id),
        "tasks": sorted(task.value for task in definition.capabilities.tasks),
        "parameters": sorted(definition.capabilities.parameters),
        "structured_output": definition.capabilities.structured_output,
        "tools": definition.capabilities.tools,
        "model_discovery": definition.capabilities.model_discovery,
        "base_url_configurable": definition.base_url_configurable,
        "configured": _credential_configured(definition.id),
        "credential_required": definition.credential_required,
        "base_url": (
            str(snapshot.adapter.base_url)
            if definition.base_url_configurable
            else None
        ),
        "enabled": True,
        "selector_admission": "admitted",
        "configuration_version": None,
    }


def _clear_runtime_surfaces(provider_id: str) -> None:
    """Unregister first so cache/catalog cleanup cannot block fail-closed revoke."""
    unregister_custom_provider(provider_id)
    try:
        provider_model_cache.clear(provider_id)
    except Exception:
        pass
    try:
        model_catalog.clear_discovery(provider_id)
    except Exception:
        pass


async def _activate_custom_profile(
    settings: dict[str, str], *, initial_admission: SelectorAdmission = "admitted",
) -> dict[str, object]:
    """Fail-closed two-phase: durable disabled → register runtime → durable enabled.

    On any failure after the first durable write, the row remains disabled and
    runtime is cleared. Compensation failures are not silently ignored into an
    enabled+unregistered ghost state.
    """
    provider_id = settings["provider_id"]
    await save_provider_configuration(
        provider_id,
        base_url=settings["base_url"],
        default_model=settings["default_model"],
        display_name=settings["display_name"],
        enabled=False,
        initial_admission=initial_admission,
    )
    try:
        configure_custom_provider(
            provider_id=provider_id,
            base_url=settings["base_url"],
            default_model=settings["default_model"],
            display_name=settings["display_name"],
        )
    except Exception as reg_exc:
        _clear_runtime_surfaces(provider_id)
        try:
            await soft_disable_provider_configuration(
                provider_id,
                base_url=settings["base_url"],
                default_model=settings["default_model"],
                display_name=settings["display_name"],
            )
        except Exception as compensate_exc:
            _clear_runtime_surfaces(provider_id)
            raise RuntimeError(
                f"provider_register_failed; durable disable compensate failed: {compensate_exc}"
            ) from compensate_exc
        raise reg_exc

    try:
        enabled = await save_provider_configuration(
            provider_id,
            base_url=settings["base_url"],
            default_model=settings["default_model"],
            display_name=settings["display_name"],
            enabled=True,
        )
    except Exception as enable_exc:
        _clear_runtime_surfaces(provider_id)
        try:
            await soft_disable_provider_configuration(
                provider_id,
                base_url=settings["base_url"],
                default_model=settings["default_model"],
                display_name=settings["display_name"],
            )
        except Exception as compensate_exc:
            _clear_runtime_surfaces(provider_id)
            raise RuntimeError(
                f"provider_enable_failed; durable disable compensate failed: {compensate_exc}"
            ) from compensate_exc
        raise enable_exc

    try:
        provider_model_cache.clear(provider_id)
    except Exception:
        pass
    try:
        model_catalog.clear_discovery(provider_id)
    except Exception:
        pass
    return enabled


@router.get("")
async def list_providers():
    rows = [_provider_row(definition) for definition in provider_registry.list_definitions()]
    # Surface soft-disabled custom profiles so the UI can mark historical targets.
    known_ids = {row["id"] for row in rows}
    by_id = {row["id"]: row for row in rows}
    for config in await list_provider_configurations(include_disabled=True):
        provider_id = str(config["provider_id"])
        if not is_user_custom_provider(provider_id):
            continue
        if provider_id in known_ids:
            by_id[provider_id].update({
                "selector_admission": config["selector_admission"],
                "configuration_version": config["configuration_version"],
            })
            continue
        if bool(config.get("enabled")):
            continue
        rows.append(
            {
                "id": provider_id,
                "display_name": str(config.get("display_name") or provider_id),
                "default_model": str(config.get("default_model") or ""),
                "source": "user_config",
                "catalog_version": model_catalog.version,
                "catalog_verified_at": model_catalog.verified_at,
                "default_model_capability": {
                    "id": str(config.get("default_model") or "unavailable"),
                    "display_name": str(config.get("default_model") or "unavailable"),
                    "lifecycle": "unavailable",
                    "callable": False,
                    "source": "dynamic",
                    "thinking_control": None,
                },
                "tasks": [],
                "parameters": [],
                "structured_output": False,
                "tools": False,
                "model_discovery": False,
                "base_url_configurable": True,
                "configured": _credential_configured(provider_id),
                "credential_required": False,
                "base_url": str(config.get("base_url") or ""),
                "enabled": False,
                "selector_admission": config["selector_admission"],
                "configuration_version": config["configuration_version"],
            }
        )
    return rows


def _custom_profile_response(saved: dict[str, object]) -> dict[str, object]:
    return {
        "provider_id": saved["provider_id"],
        "display_name": saved["display_name"],
        "base_url": saved["base_url"],
        "default_model": saved["default_model"],
        "enabled": bool(saved.get("enabled")),
        "created_at_utc": saved["created_at_utc"],
        "selector_admission": saved["selector_admission"],
        "configuration_version": saved["configuration_version"],
        "source": "user_config",
    }


@router.post("/custom-profiles")
async def create_custom_profile(body: CustomProfileCreate):
    provider_id = new_custom_provider_id()
    try:
        settings = prepare_custom_provider_settings(
            provider_id=provider_id,
            base_url=body.base_url,
            default_model=body.default_model,
            display_name=body.display_name,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "invalid_provider_configuration", "message": str(exc)},
        ) from exc
    try:
        saved = await _activate_custom_profile(settings, initial_admission="pending")
    except Exception as exc:
        code = (
            "provider_register_failed"
            if "register" in str(exc).lower() or "adapter" in str(exc).lower()
            else "provider_persist_failed"
        )
        if isinstance(exc, RuntimeError) and "register" in str(exc):
            code = "provider_register_failed"
        # Best-effort: ensure not left registered.
        _clear_runtime_surfaces(provider_id)
        raise HTTPException(
            status_code=500,
            detail={"code": code, "message": str(exc)},
        ) from exc
    return _custom_profile_response(saved)


@router.patch("/custom-profiles/{provider_id}")
async def update_custom_profile(provider_id: str, body: CustomProfileUpdate):
    if not is_custom_profile_id(provider_id):
        raise HTTPException(status_code=404, detail="unknown custom profile")
    existing = await get_provider_configuration(provider_id)
    if existing is not None and not bool(existing.get("enabled")):
        raise HTTPException(status_code=404, detail="custom profile is unavailable")
    if existing is None and provider_id != "custom":
        raise HTTPException(status_code=404, detail="custom profile is unavailable")

    admission_fields = {"selector_admission", "expected_configuration_version"}
    if body.model_fields_set & admission_fields:
        if (
            body.selector_admission is None
            or body.expected_configuration_version is None
            or body.model_fields_set - admission_fields
        ):
            raise HTTPException(status_code=422, detail={
                "code": "invalid_provider_configuration",
                "message": "准入更新须同时提供准入值和期望配置版本，配置字段须单独保存。",
            })
        saved = await update_provider_selector_admission(
            provider_id, body.selector_admission, body.expected_configuration_version,
        )
        if saved is None:
            raise HTTPException(status_code=409, detail={
                "code": "configuration_changed",
                "message": "接入配置已变化，请刷新后重新检查。",
            })
        return _custom_profile_response(saved)

    # Resolve previous values from DB first, then live registry only as fallback.
    try:
        if existing is not None:
            prior_base = str(existing["base_url"])
            prior_model = str(existing["default_model"])
            prior_name = str(existing.get("display_name") or "")
        else:
            current = provider_registry.snapshot(provider_id, allow_unavailable=True)
            prior_base = str(current.adapter.base_url)
            prior_model = current.model_id
            prior_name = next(
                (
                    definition.display_name
                    for definition in provider_registry.list_definitions()
                    if definition.id == provider_id
                ),
                "OpenAI-compatible",
            )
        settings = prepare_custom_provider_settings(
            provider_id=provider_id,
            base_url=body.base_url or prior_base,
            default_model=body.default_model or prior_model,
            display_name=body.display_name or prior_name,
        )
    except (KeyError, ValueError) as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "invalid_provider_configuration", "message": str(exc)},
        ) from exc

    try:
        saved = await _activate_custom_profile(settings)
    except Exception as exc:
        _clear_runtime_surfaces(provider_id)
        raise HTTPException(
            status_code=500,
            detail={"code": "provider_register_failed", "message": str(exc)},
        ) from exc
    return _custom_profile_response(saved)


@router.delete("/custom-profiles/{provider_id}")
async def delete_custom_profile(provider_id: str):
    """Soft-disable: persist enabled=0 first, then drop runtime, then credentials."""
    if not is_custom_profile_id(provider_id):
        raise HTTPException(status_code=404, detail="unknown custom profile")

    existing = await get_provider_configuration(provider_id)
    if existing is None and provider_id != "custom":
        raise HTTPException(status_code=404, detail="unknown custom profile")

    # Resolve defaults for stub insert when legacy custom has no DB row yet.
    base_url = None
    default_model = None
    display_name = None
    if existing is not None:
        base_url = str(existing["base_url"])
        default_model = str(existing["default_model"])
        display_name = str(existing.get("display_name") or "") or None
    else:
        try:
            snap = provider_registry.snapshot(provider_id, allow_unavailable=True)
            base_url = str(snap.adapter.base_url)
            default_model = snap.model_id
            display_name = next(
                (
                    definition.display_name
                    for definition in provider_registry.list_definitions()
                    if definition.id == provider_id
                ),
                "OpenAI-compatible",
            )
        except KeyError:
            base_url = "http://127.0.0.1:1234/v1"
            default_model = "local-model"
            display_name = "OpenAI-compatible"

    try:
        await soft_disable_provider_configuration(
            provider_id,
            base_url=base_url,
            default_model=default_model,
            display_name=display_name,
        )
    except Exception as exc:
        # Must not report success or leave DB enabled while claiming disabled.
        raise HTTPException(
            status_code=500,
            detail={"code": "provider_disable_failed", "message": str(exc)},
        ) from exc

    # Runtime becomes non-callable only after durable disable succeeds.
    _clear_runtime_surfaces(provider_id)

    configured = False
    try:
        credential_store.delete(provider_id)
        configured = credential_store.get(provider_id) is not None
    except Exception:
        # Profile remains non-callable; client may retry DELETE to clear credentials.
        try:
            configured = credential_store.get(provider_id) is not None
        except Exception:
            configured = True

    return {
        "provider_id": provider_id,
        "enabled": False,
        "configured": configured,
    }


@router.patch("/custom/configuration")
async def update_custom_provider_configuration(update: CustomProviderConfiguration):
    """Legacy singleton path — still writes the `custom` row."""
    try:
        settings = prepare_custom_provider_settings(
            base_url=update.base_url,
            default_model=update.default_model,
            display_name=update.display_name,
            provider_id="custom",
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "invalid_provider_configuration", "message": str(exc)},
        ) from exc
    try:
        saved = await _activate_custom_profile(settings)
    except Exception as exc:
        _clear_runtime_surfaces("custom")
        raise HTTPException(
            status_code=500,
            detail={"code": "provider_register_failed", "message": str(exc)},
        ) from exc
    return {
        "provider_id": "custom",
        "base_url": saved["base_url"],
        "default_model": saved["default_model"],
        "display_name": saved["display_name"],
        "enabled": bool(saved.get("enabled")),
        "created_at_utc": saved.get("created_at_utc"),
        "selector_admission": saved["selector_admission"],
        "configuration_version": saved["configuration_version"],
    }


@router.post("/{provider_id}/credentials")
async def save_provider_credential(provider_id: str, update: CredentialUpdate):
    _definition(provider_id)
    if is_custom_profile_id(provider_id):
        await touch_provider_configuration_version(provider_id)
    credential_store.set(provider_id, update.api_key.get_secret_value())
    provider_model_cache.clear(provider_id)
    model_catalog.clear_discovery(provider_id)
    return {"provider_id": provider_id, "configured": True}


@router.delete("/{provider_id}/credentials")
async def delete_provider_credential(provider_id: str):
    _definition(provider_id)
    if is_custom_profile_id(provider_id):
        await touch_provider_configuration_version(provider_id)
    credential_store.delete(provider_id)
    provider_model_cache.clear(provider_id)
    model_catalog.clear_discovery(provider_id)
    return {"provider_id": provider_id, "configured": False}


async def _discover_models(provider_id: str, *, refresh: bool):
    snapshot = _definition(provider_id)
    if (
        ProviderTask.MODELS not in snapshot.capabilities.tasks
        or not snapshot.capabilities.model_discovery
    ):
        raise HTTPException(
            status_code=400,
            detail={"code": "model_discovery_unsupported"},
        )
    secret = credential_store.get(provider_id)
    if snapshot.credential_required and not secret:
        raise HTTPException(
            status_code=409,
            detail={"code": "credential_missing"},
        )
    method = getattr(snapshot.adapter, "list_models", None)
    if method is None:
        raise HTTPException(
            status_code=500,
            detail={"code": "adapter_contract_error"},
        )

    async def load() -> list[str | dict[str, Any]]:
        return await method(api_key=secret)

    try:
        return await provider_model_cache.get(
            provider_id,
            load,
            refresh=refresh,
        )
    except HTTPException:
        raise
    except Exception as exc:
        stale = provider_model_cache.peek(provider_id)
        if stale is not None:
            return stale
        raise HTTPException(
            status_code=502,
            detail={
                "code": "provider_unavailable",
                "message": "Provider request failed",
            },
        ) from exc


@router.get("/{provider_id}/models")
async def list_provider_models(provider_id: str, refresh: bool = False):
    result = await _discover_models(provider_id, refresh=refresh)
    catalog = model_catalog.reconcile(
        provider_id,
        result.models,
        result.metadata,
    )
    id_only = bool(catalog) and all(
        row.get("display_source") == "request_id" for row in catalog
    )
    if result.stale:
        discovery_status = "failed_using_cache"
    elif result.cache_hit:
        discovery_status = "cache"
    else:
        discovery_status = "live"
    return {
        "provider_id": provider_id,
        "models": list(result.models),
        "catalog_version": model_catalog.version,
        "catalog_verified_at": model_catalog.verified_at,
        "catalog": catalog,
        "cache_hit": result.cache_hit,
        "discovery_status": discovery_status,
        "preferred_model_id": model_catalog.preferred_id(provider_id),
        "id_only": id_only,
    }


@router.post("/{provider_id}/test")
async def test_provider(provider_id: str):
    result = await _discover_models(provider_id, refresh=True)
    return {
        "provider_id": provider_id,
        "ok": True,
        "model_count": len(result.models),
    }


@router.post("/{provider_id}/chat-check")
async def check_selected_model_chat(provider_id: str, request: ChatCheckRequest):
    model_id = request.model_id.strip()
    if not model_id:
        raise HTTPException(status_code=422, detail={"code": "model_id_required"})
    try:
        snapshot = provider_registry.snapshot(provider_id, model_id).require(
            ProviderTask.CHAT
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail={"code": "provider_not_found"}) from exc
    except ModelUnavailableError as exc:
        raise HTTPException(status_code=409, detail={"code": "model_unavailable"}) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"code": "chat_unsupported"}) from exc

    secret = credential_store.get(provider_id)
    if snapshot.credential_required and not secret:
        raise HTTPException(status_code=409, detail={"code": "credential_missing"})

    answered = False
    try:
        stream = snapshot.adapter.get_chat_stream(
            active_history=[{"role": "user", "content": "Reply OK."}],
            api_key=secret,
            model=model_id,
            isolated=True,
            max_output_tokens=512,
        )
        async with asyncio.timeout(20):
            async with aclosing(stream):
                async for chunk in stream:
                    if str(chunk.get("content") or "").strip():
                        answered = True
                        break
    except (TimeoutError, httpx.TimeoutException) as exc:
        raise HTTPException(status_code=504, detail={"code": "chat_check_timeout"}) from exc
    except ProviderRequestError as exc:
        if exc.status_code in {401, 403}:
            raise HTTPException(status_code=401, detail={"code": "credential_rejected"}) from exc
        raise HTTPException(status_code=502, detail={"code": "provider_unavailable"}) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail={"code": "provider_unavailable"}) from exc

    if not answered:
        raise HTTPException(status_code=502, detail={"code": "empty_response"})
    try:
        current = provider_registry.snapshot(provider_id, model_id)
        unchanged = current.adapter is snapshot.adapter and credential_store.get(
            provider_id
        ) == secret
    except (KeyError, ModelUnavailableError):
        unchanged = False
    if not unchanged:
        raise HTTPException(status_code=409, detail={"code": "configuration_changed"})
    return {"provider_id": provider_id, "model_id": model_id, "ok": True}
