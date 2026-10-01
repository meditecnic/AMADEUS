"""Application provider registry and built-in adapter registration."""

from __future__ import annotations

import os
import re
import uuid
from urllib.parse import urlsplit, urlunsplit

from app.services.deepseek import DeepSeekService
from app.services.model_catalog import is_user_custom_provider, model_catalog
from app.services.provider_adapters import (
    GeminiAdapter,
    OpenAICompatibleAdapter,
    OpenAIResponsesAdapter,
)
from app.services.provider_registry import (
    ProviderCapabilities,
    ProviderDefinition,
    ProviderRegistry,
    ProviderTask,
)


deepseek_service = DeepSeekService()
provider_registry = ProviderRegistry(model_catalog=model_catalog)
provider_registry.register(
    ProviderDefinition(
        id="deepseek",
        display_name="DeepSeek",
        default_model=deepseek_service.model,
        capabilities=ProviderCapabilities(
            tasks=frozenset(
                {
                    ProviderTask.CHAT,
                    ProviderTask.TRANSLATION,
                    ProviderTask.MEMORY,
                    ProviderTask.COMPRESSION,
                    ProviderTask.TITLE,
                    ProviderTask.MODELS,
                }
            ),
            parameters=frozenset(
                {"temperature", "tools", "tool_choice", "reasoning_effort"}
            ),
            structured_output=True,
            tools=True,
            model_discovery=True,
        ),
    ),
    deepseek_service,
)


_CUSTOM_PROFILE_ID_RE = re.compile(
    r"^custom:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


def _compatible_capabilities(*, reasoning: bool = False) -> ProviderCapabilities:
    parameters = {"temperature", "tools", "tool_choice"}
    if reasoning:
        parameters.add("reasoning_effort")
    return ProviderCapabilities(
        tasks=frozenset(
            {
                ProviderTask.CHAT,
                ProviderTask.TRANSLATION,
                ProviderTask.MEMORY,
                ProviderTask.COMPRESSION,
                ProviderTask.TITLE,
                ProviderTask.MODELS,
            }
        ),
        parameters=frozenset(parameters),
        structured_output=True,
        tools=True,
        model_discovery=True,
    )


def normalize_custom_base_url(value: str) -> str:
    raw = value.strip().rstrip("/")
    parsed = urlsplit(raw)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("custom base URL must be a credential-free HTTP(S) endpoint")
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", ""))


def normalize_custom_display_name(value: str) -> str:
    name = (value or "").strip()
    if not name or len(name) > 80:
        raise ValueError("custom display name must be 1–80 characters")
    return name


def normalize_custom_default_model(value: str) -> str:
    model = (value or "").strip()
    if not model or len(model) > 200:
        raise ValueError("custom default model is required")
    return model


def new_custom_provider_id() -> str:
    return f"custom:{uuid.uuid4()}"


def is_custom_profile_id(provider_id: str) -> bool:
    return provider_id == "custom" or bool(_CUSTOM_PROFILE_ID_RE.match(provider_id))


def _register_compatible(
    *,
    provider_id: str,
    display_name: str,
    base_url: str,
    default_model: str,
    base_url_configurable: bool = False,
    credential_required: bool = True,
    thinking_request_mode: str | None = None,
) -> None:
    adapter = OpenAICompatibleAdapter(
        provider_id=provider_id,
        base_url=base_url,
        default_model=default_model,
        message_builder=deepseek_service.build_persona_messages,
        credential_required=credential_required,
        thinking_request_mode=thinking_request_mode,
    )
    provider_registry.register(
        ProviderDefinition(
            id=provider_id,
            display_name=display_name,
            default_model=default_model,
            capabilities=_compatible_capabilities(
                reasoning=thinking_request_mode is not None
            ),
            base_url_configurable=base_url_configurable,
            credential_required=credential_required,
        ),
        adapter,
    )


_register_compatible(
    provider_id="glm",
    display_name="GLM",
    base_url="https://open.bigmodel.cn/api/paas/v4",
    default_model="glm-5.2",
    thinking_request_mode="reasoning_effort",
)
_register_compatible(
    provider_id="custom",
    display_name="OpenAI-compatible",
    base_url=normalize_custom_base_url(os.getenv(
        "AMADEUS_CUSTOM_PROVIDER_BASE_URL",
        "http://127.0.0.1:1234/v1",
    )),
    default_model=os.getenv("AMADEUS_CUSTOM_PROVIDER_MODEL", "local-model"),
    base_url_configurable=True,
    credential_required=False,
)


def custom_provider_settings(provider_id: str = "custom") -> dict[str, str]:
    snapshot = provider_registry.snapshot(provider_id, allow_unavailable=True)
    display_name = next(
        (
            definition.display_name
            for definition in provider_registry.list_definitions()
            if definition.id == provider_id
        ),
        provider_id,
    )
    return {
        "base_url": str(snapshot.adapter.base_url),
        "default_model": snapshot.model_id,
        "display_name": display_name,
    }


def prepare_custom_provider_settings(
    *,
    base_url: str,
    default_model: str,
    display_name: str | None = None,
    provider_id: str = "custom",
) -> dict[str, str]:
    """Normalize inputs without mutating the live registry (no ghost config)."""
    if not is_custom_profile_id(provider_id):
        raise ValueError("invalid custom provider id")
    normalized_url = normalize_custom_base_url(base_url)
    normalized_model = normalize_custom_default_model(default_model)
    if provider_id == "custom":
        name = normalize_custom_display_name(display_name or "OpenAI-compatible")
    else:
        name = normalize_custom_display_name(
            display_name or f"OpenAI-compatible {provider_id.split(':', 1)[-1][:8]}"
        )
    return {
        "provider_id": provider_id,
        "base_url": normalized_url,
        "default_model": normalized_model,
        "display_name": name,
    }


def configure_custom_provider(
    *,
    base_url: str,
    default_model: str,
    display_name: str | None = None,
    provider_id: str = "custom",
) -> dict[str, str]:
    """Register or replace one OpenAI-compatible profile in the live registry."""
    settings = prepare_custom_provider_settings(
        provider_id=provider_id,
        base_url=base_url,
        default_model=default_model,
        display_name=display_name,
    )
    adapter = OpenAICompatibleAdapter(
        provider_id=settings["provider_id"],
        base_url=settings["base_url"],
        default_model=settings["default_model"],
        message_builder=deepseek_service.build_persona_messages,
        credential_required=False,
    )
    definition = ProviderDefinition(
        id=settings["provider_id"],
        display_name=settings["display_name"],
        default_model=settings["default_model"],
        capabilities=_compatible_capabilities(),
        base_url_configurable=True,
        credential_required=False,
    )
    if any(item.id == settings["provider_id"] for item in provider_registry.list_definitions()):
        provider_registry.replace(definition, adapter)
    else:
        provider_registry.upsert(definition, adapter)
    return settings


def unregister_custom_provider(provider_id: str) -> None:
    """Remove a custom profile from the live registry. Never re-registers defaults."""
    if is_custom_profile_id(provider_id):
        provider_registry.unregister(provider_id)


def ensure_default_custom_singleton() -> None:
    """Bootstrap-only: register legacy custom when no persisted custom row exists."""
    if any(item.id == "custom" for item in provider_registry.list_definitions()):
        return
    configure_custom_provider(
        provider_id="custom",
        base_url=os.getenv(
            "AMADEUS_CUSTOM_PROVIDER_BASE_URL",
            "http://127.0.0.1:1234/v1",
        ),
        default_model=os.getenv("AMADEUS_CUSTOM_PROVIDER_MODEL", "local-model"),
        display_name="OpenAI-compatible",
    )


async def restore_custom_provider_configuration() -> None:
    from app.db import list_provider_configurations

    rows = await list_provider_configurations(include_disabled=True)
    enabled_ids: set[str] = set()
    has_custom_row = False
    for row in rows:
        provider_id = str(row["provider_id"])
        if not is_user_custom_provider(provider_id):
            continue
        if provider_id == "custom":
            has_custom_row = True
        if not bool(row.get("enabled")):
            continue
        configure_custom_provider(
            provider_id=provider_id,
            base_url=str(row["base_url"]),
            default_model=str(row["default_model"]),
            display_name=str(row.get("display_name") or "") or None,
        )
        enabled_ids.add(provider_id)

    # Drop any custom runtime entries that are disabled or absent from enabled set.
    for definition in list(provider_registry.list_definitions()):
        if (
            is_user_custom_provider(definition.id)
            and definition.id not in enabled_ids
        ):
            provider_registry.unregister(definition.id)

    # Compatibility: only when no persisted `custom` row exists at all.
    if not has_custom_row:
        ensure_default_custom_singleton()


openai_adapter = OpenAIResponsesAdapter(
    message_builder=deepseek_service.build_persona_messages
)
provider_registry.register(
    ProviderDefinition(
        id="openai",
        display_name="OpenAI",
        default_model=openai_adapter.model,
        capabilities=ProviderCapabilities(
            tasks=frozenset(
                {
                    ProviderTask.CHAT,
                    ProviderTask.TRANSLATION,
                    ProviderTask.MEMORY,
                    ProviderTask.COMPRESSION,
                    ProviderTask.TITLE,
                    ProviderTask.MODELS,
                }
            ),
            parameters=frozenset(
                {"tools", "tool_choice", "reasoning_effort"}
            ),
            structured_output=True,
            tools=True,
            model_discovery=True,
        ),
    ),
    openai_adapter,
)

gemini_adapter = GeminiAdapter(message_builder=deepseek_service.build_persona_messages)
provider_registry.register(
    ProviderDefinition(
        id="gemini",
        display_name="Gemini",
        default_model=gemini_adapter.model,
        capabilities=ProviderCapabilities(
            tasks=frozenset(
                {
                    ProviderTask.CHAT,
                    ProviderTask.TRANSLATION,
                    ProviderTask.MEMORY,
                    ProviderTask.COMPRESSION,
                    ProviderTask.TITLE,
                    ProviderTask.MODELS,
                }
            ),
            parameters=frozenset(
                {"temperature", "tools", "tool_choice", "reasoning_effort"}
            ),
            structured_output=True,
            tools=True,
            model_discovery=True,
        ),
    ),
    gemini_adapter,
)
