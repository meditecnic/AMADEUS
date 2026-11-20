"""Contract tests for the tiers exposed in the catalog and sent over HTTP."""

import asyncio
from contextlib import asynccontextmanager
import json
from uuid import uuid4

from fastapi import HTTPException
import httpx
import pytest

from app.routers import chat_ws, providers
from app.services import deepseek
from app.services.model_catalog import ModelCatalog, model_catalog
from app.services.provider_adapters import GeminiAdapter, OpenAICompatibleAdapter, OpenAIResponsesAdapter
from app.services.provider_registry import ProviderCapabilities, ProviderSnapshot, ProviderTask
from app.services.provider_runtime import (
    configure_custom_provider,
    custom_provider_settings,
    deepseek_service,
    provider_registry,
    unregister_custom_provider,
)


DEEPSEEK = ("thinking_and_effort", ("disabled", "low", "high", "max"), "low", {
    "none": "disabled", "minimal": "low", "medium": "high", "xhigh": "high", "ultra": "max",
})
GLM_52 = ("thinking_and_effort", ("disabled", "high", "max"), "max", {
    "none": "disabled", "minimal": "disabled", "low": "high", "medium": "high", "xhigh": "max",
})
LUNA = ("openai_reasoning_effort", ("none", "low", "medium", "high", "xhigh", "max"), "medium", {
    "disabled": "none", "minimal": "low",
})
GEMINI = ("gemini_thinking_level", ("minimal", "low", "medium", "high"), "medium", {
    "none": "minimal", "disabled": "minimal", "xhigh": "high", "max": "high",
})
LOWEST_ALIASES = {"none": "low", "minimal": "low", "disabled": "low"}
KIMI = ("openai_reasoning_effort", ("low", "high", "max"), "max", {
    **LOWEST_ALIASES, "medium": "high", "xhigh": "max",
})
GLM_53 = ("thinking_and_effort", *KIMI[1:])
GROK = ("openai_reasoning_effort", ("low", "medium", "high", "xhigh"), "high", {
    **LOWEST_ALIASES, "max": "xhigh",
})
GPT_62 = ("openai_reasoning_effort", ("low", "medium", "high", "xhigh", "max"), "medium", LOWEST_ALIASES)

DEEPSEEK_MODELS = ("deepseek-flash", "deepseek-v4-flash", "deepseek-v4-pro")
CUSTOM_ID = "custom:25600000-0000-4000-8000-000000000001"
CUSTOM_CASES = [
    (url, model, spec)
    for urls, models, spec in [
        (("https://api.moonshot.cn/v1", "https://api.moonshot.ai/v1",
          "https://api.kimi.com/coding/v1", "https://api.kimi.ai/coding/v1"),
         ("kimi-k3.1-pro", "kimi-k3.1-pro-highspeed"), KIMI),
        (("https://api.z.ai/api/paas/v4", "https://open.bigmodel.cn/api/paas/v4"), ("glm-5.2",), GLM_52),
        (("https://api.z.ai/api/paas/v4", "https://open.bigmodel.cn/api/paas/v4"),
         ("glm-5.3", "glm-5.3-flash", "glm-5.3-flashx"), GLM_53),
        (("https://api.x.ai/v1",), ("grok-4.7",), GROK),
        (("https://api.openai.com/v1",), ("gpt-6.2-sol", "gpt-6.2-astra"), GPT_62),
        (("https://api.openai.com/v1",), ("gpt-5.6-luna", "gpt-6-luna"), LUNA),
        (("https://api.deepseek.com", "https://api.deepseek.com/v1"), DEEPSEEK_MODELS, DEEPSEEK),
    ]
    for url in urls
    for model in models
]
BUILTIN_CASES = [
    *(("deepseek", None, model, DEEPSEEK) for model in DEEPSEEK_MODELS),
    ("glm", None, "glm-5.2", GLM_52),
    ("openai", None, "gpt-5.6-luna", ("reasoning_effort", *LUNA[1:])),
    ("gemini", None, "gemini-3.6-flash", GEMINI),
    ("gemini", None, "gemini-3.5-flash", GEMINI),
]
MESSAGES = [{"role": "user", "content": "検証するわ。"}]
AUXILIARY_STREAM_CASES = [
    ("deepseek", None, "deepseek-flash", DEEPSEEK),
    ("glm", None, "glm-5.2", GLM_52),
    ("openai", None, "gpt-5.6-luna", ("reasoning_effort", *LUNA[1:])),
    ("gemini", None, "gemini-3.6-flash", GEMINI),
    (CUSTOM_ID, "https://api.moonshot.cn/v1", "kimi-k3.1-pro", KIMI),
    (CUSTOM_ID, "https://api.z.ai/api/paas/v4", "glm-5.3", GLM_53),
    (CUSTOM_ID, "https://api.x.ai/v1", "grok-4.7", GROK),
    (CUSTOM_ID, "https://api.openai.com/v1", "gpt-6.2-sol", GPT_62),
    (CUSTOM_ID, "https://api.openai.com/v1", "gpt-5.6-luna", LUNA),
    (CUSTOM_ID, "https://api.openai.com/v1", "legacy-model", None),
    (CUSTOM_ID, "https://openrouter.ai/api/v1", "gpt-6.2-sol", None),
]


@pytest.fixture
def outbound(monkeypatch):
    captured = []

    def handle(request):
        payload = json.loads(request.content)
        captured.append((request.url, payload))
        reply = MESSAGES[0]["content"]
        if request.url.path.endswith(":streamGenerateContent"):
            event = {"candidates": [{"content": {"parts": [{"text": reply}]}}]}
        elif request.url.path.endswith("/responses"):
            event = {"type": "response.output_text.delta", "delta": reply}
        else:
            event = {"choices": [{"delta": {"content": reply}}]}
        if payload.get("stream") or request.url.path.endswith(":streamGenerateContent"):
            return httpx.Response(200, text=f"data: {json.dumps(event)}\n\ndata: [DONE]\n\n")
        raw = '{"ok": true}'
        return httpx.Response(200, json={
            "choices": [{"message": {"content": raw}}],
            "output_text": raw,
            "candidates": [{"content": {"parts": [{"text": raw}]}}],
        })

    transport = httpx.MockTransport(handle)

    @asynccontextmanager
    async def deepseek_client(owner, client_type, *, timeout):
        async with httpx.AsyncClient(transport=transport, timeout=timeout) as client:
            yield client, {}

    monkeypatch.setattr(deepseek, "model_http_client", deepseek_client)

    def build(provider, url, model):
        args = {"message_builder": deepseek_service.build_persona_messages, "transport": transport}
        if provider == "deepseek":
            return deepseek.DeepSeekService()
        if provider == "openai":
            return OpenAIResponsesAdapter(**args)
        if provider == "gemini":
            return GeminiAdapter(**args)
        return OpenAICompatibleAdapter(
            provider_id=provider,
            base_url=url or "https://open.bigmodel.cn/api/paas/v4",
            default_model=model,
            **args,
        )

    return captured, build


async def stream(adapter, model, effort=None, **options):
    async for _ in adapter.get_chat_stream(
        MESSAGES, api_key="test-key", model=model, reasoning_effort=effort, **options,
    ):
        pass


def assert_fields(payload, mode, tier, temperature):
    expected = {}
    if mode == "thinking_and_effort":
        expected["thinking"] = {"type": "disabled" if tier == "disabled" else "enabled"}
        if tier != "disabled":
            expected["reasoning_effort"] = tier
    elif mode == "openai_reasoning_effort":
        expected["reasoning_effort"] = tier
    elif mode == "reasoning_effort":
        expected["reasoning"] = {"effort": tier}
    elif mode == "gemini_thinking_level":
        expected["thinkingConfig"] = {"thinkingLevel": tier}
    if temperature is not None:
        expected["temperature"] = temperature
    keys = ("temperature", "thinking", "reasoning_effort", "reasoning", "thinkingConfig")
    actual = {key: payload[key] for key in keys if key in payload}
    if mode == "gemini_thinking_level":
        assert actual == {}
        actual = {key: value for key, value in payload["generationConfig"].items() if key in keys}
    assert actual == expected


@pytest.fixture(params=[
    pytest.param((provider, url, model, spec), id=f"{provider}-{url or 'builtin'}-{model}")
    for provider, url, model, spec in AUXILIARY_STREAM_CASES
])
def auxiliary_stream_case(request, outbound):
    provider, url, model, spec = request.param
    captured, build = outbound
    snapshot = ProviderSnapshot(
        provider_id=provider,
        model_id=model,
        capabilities=ProviderCapabilities(tasks=frozenset({ProviderTask.CHAT})),
        adapter=build(provider, url, model),
    )
    return captured, snapshot, spec


def assert_stream_minimum(payload, spec, temperature):
    if spec is None:
        assert_fields(payload, None, None, temperature)
        return
    mode, tiers, _, _ = spec
    lowest = tiers[0]
    retains_temperature = lowest == "disabled" or (mode == "openai_reasoning_effort" and lowest == "none")
    assert_fields(payload, mode, lowest, temperature if retains_temperature else None)


def test_chat_check_stream_uses_protocol_minimum_and_token_limit(
    app_client, auxiliary_stream_case, isolated_provider_credentials, monkeypatch,
):
    captured, snapshot, spec = auxiliary_stream_case
    provider = snapshot.provider_id
    isolated_provider_credentials.set(provider, "test-key")
    monkeypatch.setattr(providers.provider_registry, "snapshot", lambda *_args, **_kwargs: snapshot)
    response = app_client.post(
        f"/api/providers/{provider}/chat-check", json={"model_id": snapshot.model_id},
    )
    assert response.status_code == 200, response.json()
    (request_url, payload), = captured
    assert_stream_minimum(payload, spec, 0.75)
    if spec in (GPT_62, LUNA):
        assert str(request_url) == "https://api.openai.com/v1/chat/completions"
        assert payload["max_completion_tokens"] == 512
        assert "max_tokens" not in payload
    elif provider == "openai":
        assert request_url.path.endswith("/responses")
        assert payload["max_output_tokens"] == 512
    elif provider == "gemini":
        assert payload["generationConfig"]["maxOutputTokens"] == 512
    else:
        assert payload["max_tokens"] == 512
        assert "max_completion_tokens" not in payload


@pytest.mark.asyncio
async def test_japanese_renderer_stream_uses_protocol_minimum(auxiliary_stream_case):
    captured, snapshot, spec = auxiliary_stream_case
    session = chat_ws.SessionState("thinking-protocol-renderer")
    session.api_key = "test-key"
    session.provider_id = snapshot.provider_id
    session.model = snapshot.model_id
    rendered = await chat_ws._render_foreign_speech_to_japanese(
        provider=snapshot.adapter,
        provider_snapshot=None if snapshot.provider_id == "deepseek" else snapshot,
        session=session,
        source_text="This result checks out.",
        user_request="Check the result.",
    )
    assert rendered == MESSAGES[0]["content"]
    (_, payload), = captured
    assert_stream_minimum(payload, spec, 0.0)


@pytest.mark.asyncio
@pytest.mark.parametrize(("provider", "url", "model", "spec"), [
    pytest.param(provider, url, model, spec, id=f"{provider}-{url or 'builtin'}-{model}")
    for provider, url, model, spec in [
        *BUILTIN_CASES,
        *((CUSTOM_ID, url, model, spec) for url, model, spec in CUSTOM_CASES),
    ]
])
async def test_every_catalog_tier_matches_wire_protocol(outbound, provider, url, model, spec):
    captured, build = outbound
    adapter = build(provider, url, model)
    mode, tiers, default, aliases = spec
    control = model_catalog.public_model(provider, model, base_url=url)["thinking_control"]
    assert control["request_mode"] == mode
    assert control["default"] == default
    assert control["aliases"] == aliases
    assert control["options"] == [
        {"value": tier, "label": "OFF" if tier in {"disabled", "none"} else tier.upper()}
        for tier in tiers
    ]

    for requested, effective in [
        *((option["value"], option["value"]) for option in control["options"]),
        *aliases.items(),
        (None, default),
    ]:
        assert model_catalog.normalize_control(provider, model, requested, url) == effective
        await stream(adapter, model, requested)
        request_url, payload = captured[-1]
        if provider == "gemini":
            assert request_url.path == "/v1beta/models/gemini-3.6-flash:streamGenerateContent"
        else:
            assert payload["model"] == model
        retains_temperature = effective == "disabled" or (mode == "openai_reasoning_effort" and effective == "none")
        assert_fields(payload, mode, effective, 0.75 if retains_temperature else None)

    if spec is DEEPSEEK:
        tools = [{"type": "function", "function": {"name": "lookup"}}]
        await stream(adapter, model, "max", tools=tools, tool_choice="auto")
        assert captured[-1][1]["tools"] == tools
        assert_fields(captured[-1][1], mode, "disabled", 0.75)

    captured.clear()
    await adapter.complete_json(MESSAGES, api_key="test-key", model=model)
    await adapter.translate_to_zh("検証するわ。", api_key="test-key", model=model)
    await adapter.compress_history(MESSAGES, api_key="test-key", model=model)
    assert len(captured) == 3
    for (request_url, payload), temperature in zip(captured, (0.1, 0.3, 0.3), strict=True):
        if provider == "gemini":
            assert request_url.path == "/v1beta/models/gemini-3.6-flash:generateContent"
        keeps_temperature = tiers[0] == "disabled" or (mode == "openai_reasoning_effort" and tiers[0] == "none")
        assert_fields(payload, mode, tiers[0], temperature if keeps_temperature else None)


@pytest.mark.asyncio
@pytest.mark.parametrize(("url", "model"), [
    *[(url, model)
      for url in ("https://openrouter.ai/api/v1", "http://127.0.0.1:1234/v1")
      for model in ("kimi-k3.1-pro", "glm-5.3", "grok-4.7")],
    ("https://api.moonshot.cn/v2", "kimi-k3.1-pro"),
    ("https://api.moonshot.cn/v1", "glm-5.3"),
    ("https://api.moonshot.cn.example/v1", "kimi-k3.1-pro"),
    ("http://api.moonshot.cn/v1", "kimi-k3.1-pro"),
])
async def test_unverified_endpoint_model_pairs_do_not_inject_thinking(outbound, url, model):
    captured, build = outbound
    adapter = build(CUSTOM_ID, url, model)
    assert model_catalog.public_model(CUSTOM_ID, model, base_url=url)["thinking_control"] is None
    await stream(adapter, model, "high", temperature=0.75)
    await adapter.complete_json(MESSAGES, api_key="test-key", model=model, reasoning_effort="max")
    assert len(captured) == 2
    assert_fields(captured[0][1], None, None, 0.75)
    assert_fields(captured[1][1], None, None, 0.1)


def test_gemini_legacy_id_remains_callable_after_discovering_new_id():
    catalog = ModelCatalog.from_mapping(model_catalog.manifest)
    rows = {row["id"]: row for row in catalog.reconcile("gemini", ["gemini-3.6-flash"])}
    assert catalog.preferred_id("gemini") == "gemini-3.6-flash"
    legacy = rows["gemini-3.5-flash"]
    assert legacy["lifecycle"] == "deprecated"
    assert legacy["request_id"] == legacy["compatibility_of"] == "gemini-3.6-flash"
    assert legacy["thinking_control"] == rows["gemini-3.6-flash"]["thinking_control"]
    catalog.ensure_callable("gemini", "gemini-3.5-flash")


def test_manifest_rejects_removed_prompt_depth_mode():
    manifest = model_catalog.manifest
    manifest["providers"]["deepseek"]["models"][0]["thinking_control"]["request_mode"] = "prompt_depth"
    with pytest.raises(ValueError, match="unsupported thinking_control request_mode"):
        ModelCatalog.from_mapping(manifest)


@pytest.fixture
def custom_profile():
    yield CUSTOM_ID
    unregister_custom_provider(CUSTOM_ID)
    providers.provider_model_cache.clear(CUSTOM_ID)
    model_catalog.clear_discovery(CUSTOM_ID)


def test_legacy_custom_singleton_exposes_verified_controls(app_client):
    original = custom_provider_settings()
    try:
        response = app_client.patch("/api/providers/custom/configuration", json={
            "base_url": "https://api.deepseek.com/v1/",
            "default_model": "deepseek-flash",
        })
        assert response.is_success, response.json()
        row = next(row for row in app_client.get("/api/providers").json() if row["id"] == "custom")
        control = row["default_model_capability"]["thinking_control"]
        assert control["default"] == "low"
        assert row["default_model_capability"] in row["catalog"]
    finally:
        configure_custom_provider(**original)


def test_custom_profile_uses_same_endpoint_for_listing_and_websocket(app_client, monkeypatch, custom_profile):
    model = "kimi-k3.1-pro"

    async def discover(*, api_key=None):
        return [model]

    monkeypatch.setattr(providers, "new_custom_provider_id", lambda: custom_profile)
    created = app_client.post("/api/providers/custom-profiles", json={
        "display_name": "Test Moonshot",
        "base_url": " https://API.MOONSHOT.CN/v1/chat/completions/ ",
        "default_model": model,
    })
    assert created.is_success, created.json()
    provider_id = created.json()["provider_id"]

    def read_surfaces():
        snapshot = provider_registry.snapshot(provider_id, allow_unavailable=True)
        monkeypatch.setattr(snapshot.adapter, "list_models", discover)
        items = app_client.get("/api/providers").json()
        default = next(row for row in items if row["id"] == provider_id)
        discovered = app_client.get(f"/api/providers/{provider_id}/models")
        assert discovered.is_success, discovered.json()
        row = next(row for row in discovered.json()["catalog"] if row["id"] == model)
        assert default["default_model_capability"]["thinking_control"] == row["thinking_control"]
        return default, row

    default, row = read_surfaces()
    assert default["base_url"] == row["thinking_base_url"] == "https://api.moonshot.cn/v1"
    assert row["thinking_control"]["default"] == "max"
    session_id = str(uuid4())
    with app_client.websocket_connect(f"/ws/chat?session_id={session_id}") as ws:
        ws.send_json({
            "type": "auth", "client": "desktop", "protocol_version": 2,
            "conversation_mode": "draft", "provider_id": provider_id, "model": model,
            "reasoning_effort": "none",
        })
        assert ws.receive_json()["type"] == "session.ready"
        assert chat_ws.sessions[session_id].reasoning_effort == "low"
        ws.send_json({"type": "config_update", "reasoning_effort": "xhigh"})
        assert ws.receive_json() == {"type": "config_applied", "reasoning_effort": "max"}

        changed = app_client.patch(f"/api/providers/custom-profiles/{provider_id}", json={
            "base_url": "https://openrouter.ai/api/v1",
        })
        assert changed.is_success, changed.json()
        assert providers.provider_model_cache.peek(provider_id) is None
        default, row = read_surfaces()
        assert default["base_url"] == "https://openrouter.ai/api/v1"
        assert row["thinking_control"] is None
        ws.send_json({"type": "config_update", "reasoning_effort": "max"})
        assert ws.receive_json() == {"type": "config_applied", "reasoning_effort": None}


@pytest.mark.asyncio
async def test_old_discovery_cannot_refill_cache_after_url_change(monkeypatch, custom_profile):
    model = "kimi-k3.1-pro"
    enter = asyncio.Event()
    release = asyncio.Event()
    configure_custom_provider(
        provider_id=custom_profile, base_url="https://api.moonshot.cn/v1", default_model=model,
    )
    old = provider_registry.snapshot(custom_profile).adapter

    async def discover(*, api_key=None):
        enter.set()
        await release.wait()
        return [model]

    monkeypatch.setattr(old, "list_models", discover)
    obsolete = asyncio.create_task(providers.list_provider_models(custom_profile))
    await enter.wait()
    configure_custom_provider(
        provider_id=custom_profile, base_url="https://openrouter.ai/api/v1", default_model=model,
    )
    release.set()
    with pytest.raises(HTTPException) as error:
        await obsolete
    assert error.value.status_code == 409
    assert providers.provider_model_cache.peek(custom_profile) is None
