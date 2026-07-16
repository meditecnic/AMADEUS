"""Secret-free provider metadata and write-only credential endpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, SecretStr

from app.db import save_provider_configuration
from app.services.credentials import credential_store
from app.services.provider_registry import ProviderModelCache, ProviderTask
from app.services.provider_runtime import provider_registry
from app.services.provider_runtime import configure_custom_provider


router = APIRouter(prefix="/api/providers", tags=["providers"])
provider_model_cache = ProviderModelCache()


class CredentialUpdate(BaseModel):
    api_key: SecretStr


class CustomProviderConfiguration(BaseModel):
    base_url: str
    default_model: str


def _definition(provider_id: str):
    try:
        return provider_registry.snapshot(provider_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("")
async def list_providers():
    rows = []
    for definition in provider_registry.list_definitions():
        snapshot = provider_registry.snapshot(definition.id)
        rows.append(
            {
                "id": definition.id,
                "display_name": definition.display_name,
                "default_model": definition.default_model,
                "tasks": sorted(task.value for task in definition.capabilities.tasks),
                "parameters": sorted(definition.capabilities.parameters),
                "structured_output": definition.capabilities.structured_output,
                "tools": definition.capabilities.tools,
                "model_discovery": definition.capabilities.model_discovery,
                "base_url_configurable": definition.base_url_configurable,
                "configured": credential_store.get(definition.id) is not None,
                "credential_required": definition.credential_required,
                "base_url": (
                    str(snapshot.adapter.base_url)
                    if definition.base_url_configurable
                    else None
                ),
            }
        )
    return rows


@router.patch("/custom/configuration")
async def update_custom_provider_configuration(update: CustomProviderConfiguration):
    try:
        settings = configure_custom_provider(
            base_url=update.base_url,
            default_model=update.default_model,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "invalid_provider_configuration", "message": str(exc)},
        ) from exc
    await save_provider_configuration("custom", **settings)
    provider_model_cache.clear("custom")
    return {"provider_id": "custom", **settings}


@router.post("/{provider_id}/credentials")
async def save_provider_credential(provider_id: str, update: CredentialUpdate):
    _definition(provider_id)
    credential_store.set(provider_id, update.api_key.get_secret_value())
    provider_model_cache.clear(provider_id)
    return {"provider_id": provider_id, "configured": True}


@router.delete("/{provider_id}/credentials")
async def delete_provider_credential(provider_id: str):
    _definition(provider_id)
    credential_store.delete(provider_id)
    provider_model_cache.clear(provider_id)
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

    async def load() -> list[str]:
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
    return {
        "provider_id": provider_id,
        "models": list(result.models),
        "cache_hit": result.cache_hit,
    }


@router.post("/{provider_id}/test")
async def test_provider(provider_id: str):
    result = await _discover_models(provider_id, refresh=True)
    return {
        "provider_id": provider_id,
        "ok": True,
        "model_count": len(result.models),
    }
