"""Application provider registry and built-in adapter registration."""

from __future__ import annotations

import os
from urllib.parse import urlsplit, urlunsplit

from app.services.deepseek import DeepSeekService
from app.services.model_catalog import model_catalog
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


def custom_provider_settings() -> dict[str, str]:
    snapshot = provider_registry.snapshot("custom", allow_unavailable=True)
    return {
        "base_url": str(snapshot.adapter.base_url),
        "default_model": snapshot.model_id,
    }


def configure_custom_provider(*, base_url: str, default_model: str) -> dict[str, str]:
    normalized_url = normalize_custom_base_url(base_url)
    normalized_model = default_model.strip()
    if not normalized_model or len(normalized_model) > 200:
        raise ValueError("custom default model is required")
    adapter = OpenAICompatibleAdapter(
        provider_id="custom",
        base_url=normalized_url,
        default_model=normalized_model,
        message_builder=deepseek_service.build_persona_messages,
        credential_required=False,
    )
    provider_registry.replace(
        ProviderDefinition(
            id="custom",
            display_name="OpenAI-compatible",
            default_model=normalized_model,
            capabilities=_compatible_capabilities(),
            base_url_configurable=True,
            credential_required=False,
        ),
        adapter,
    )
    return {"base_url": normalized_url, "default_model": normalized_model}


async def restore_custom_provider_configuration() -> None:
    from app.db import get_provider_configuration

    saved = await get_provider_configuration("custom")
    if saved:
        configure_custom_provider(**saved)

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
