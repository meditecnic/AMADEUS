from __future__ import annotations

import asyncio
import json
from weakref import ref

import httpx
import pytest

from app.services import provider_adapters
from app.services.deepseek import DeepSeekService
from app.services.provider_adapters import (
    GeminiAdapter,
    OpenAICompatibleAdapter,
    OpenAIResponsesAdapter,
)
from app.services.provider_runtime import deepseek_service, provider_registry


def test_model_pool_reuses_client_across_requests_but_not_across_event_loops(monkeypatch):
    clients = []
    requests = []

    def respond(request):
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": "offline-model"}]})
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"ok": true}'}}]})

    class OfflineClient(httpx.AsyncClient):
        def __init__(self, **kwargs):
            super().__init__(transport=httpx.MockTransport(respond), **kwargs)
            clients.append(self)

    monkeypatch.setattr(provider_adapters, "_HTTP_CLIENT_TYPE", OfflineClient)
    monkeypatch.setattr(httpx, "AsyncClient", OfflineClient)
    adapters = [
        DeepSeekService(),
        OpenAICompatibleAdapter(
            provider_id="fake-compatible", base_url="https://compatible.example/v1",
            default_model="offline-model", message_builder=deepseek_service.build_persona_messages,
        ),
    ]

    async def run_turns():
        for adapter in adapters:
            await adapter.list_models(api_key="offline")
            result = await adapter.complete_json([{"role": "user", "content": "test"}], api_key="offline")
            assert result == {"ok": True}

    asyncio.run(run_turns())
    assert len(clients) == 2
    assert all(not client.is_closed for client in clients)
    assert [item.extensions["timeout"]["read"] for item in requests] == [20, 30, 20, 30]

    async def run_next_loop():
        await run_turns()
        await provider_adapters.close_model_http_clients()
        assert all(client.is_closed for client in clients[2:])
        await run_turns()
        await provider_adapters.close_model_http_clients()

    asyncio.run(run_next_loop())
    assert len(clients) == 6
    assert all(client.is_closed for client in clients[2:])


async def test_model_pool_does_not_retain_a_replaced_adapter(monkeypatch):
    class OfflineClient(httpx.AsyncClient):
        def __init__(self, **kwargs):
            transport = httpx.MockTransport(
                lambda request: httpx.Response(200, json={"data": [{"id": "offline-model"}]})
            )
            super().__init__(transport=transport, **kwargs)

    monkeypatch.setattr(provider_adapters, "_HTTP_CLIENT_TYPE", OfflineClient)
    monkeypatch.setattr(httpx, "AsyncClient", OfflineClient)

    async def discover_models():
        adapter = OpenAICompatibleAdapter(
            provider_id="custom:replace", base_url="https://offline-check.invalid/v1",
            default_model="offline-model", message_builder=deepseek_service.build_persona_messages,
        )
        assert await adapter.list_models(api_key="offline") == [{"id": "offline-model"}]
        return ref(adapter)

    old_adapter = await discover_models()
    try:
        assert old_adapter() is None
    finally:
        await provider_adapters.close_model_http_clients()


@pytest.mark.asyncio
async def test_openai_compatible_stream_preserves_canonical_persona_and_tools():
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            text=(
                'data: {"choices":[{"delta":{"content":"[EMO:neutral] テスト"}}]}\n\n'
                'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call-1","type":"function","function":{"name":"search_web","arguments":"{}"}}]}}]}\n\n'
                "data: [DONE]\n\n"
            ),
            headers={"content-type": "text/event-stream"},
        )

    adapter = OpenAICompatibleAdapter(
        provider_id="fake-compatible",
        base_url="https://compatible.example/v1",
        default_model="fake-chat",
        message_builder=deepseek_service.build_persona_messages,
        transport=httpx.MockTransport(handler),
    )

    chunks = [
        chunk
        async for chunk in adapter.get_chat_stream(
            active_history=[{"role": "user", "content": "你好"}],
            api_key="provider-secret",
            system_prompt="SYSTEM",
            tools=[{"type": "function", "function": {"name": "search_web"}}],
            tool_choice="auto",
        )
    ]

    payload = json.loads(requests[0].content)
    assert requests[0].url.path == "/v1/chat/completions"
    assert "[EMO:neutral|" in payload["messages"][0]["content"]
    assert payload["messages"][-1]["content"] == "你好"
    assert payload["tools"][0]["function"]["name"] == "search_web"
    assert chunks[0]["content"] == "[EMO:neutral] テスト"
    assert chunks[1]["tool_calls"][0]["function"]["name"] == "search_web"


@pytest.mark.asyncio
async def test_openai_compatible_isolated_stream_bypasses_persona_envelope():
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, text="data: [DONE]\n\n")

    adapter = OpenAICompatibleAdapter(
        provider_id="fake-compatible",
        base_url="https://compatible.example/v1",
        default_model="fake-chat",
        message_builder=deepseek_service.build_persona_messages,
        transport=httpx.MockTransport(handler),
    )
    history = [{"role": "user", "content": "SOURCE JSON"}]
    _ = [
        chunk
        async for chunk in adapter.get_chat_stream(
            active_history=history,
            memory_summary="SECRET MEMORY",
            api_key="provider-secret",
            system_prompt="ISOLATED RENDERER",
            isolated=True,
        )
    ]

    payload = json.loads(requests[0].content)
    assert payload["messages"] == [
        {"role": "system", "content": "ISOLATED RENDERER"},
        *history,
    ]


@pytest.mark.asyncio
async def test_openai_compatible_structured_output_and_model_listing():
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/models"):
            return httpx.Response(
                200,
                json={"data": [{"id": "model-z"}, {"id": "model-a"}]},
            )
        payload = json.loads(request.content)
        assert payload["response_format"] == {"type": "json_object"}
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": '{"ok": true}'}}]},
        )

    adapter = OpenAICompatibleAdapter(
        provider_id="fake-compatible",
        base_url="https://compatible.example/v1/",
        default_model="fake-chat",
        message_builder=deepseek_service.build_persona_messages,
        transport=httpx.MockTransport(handler),
    )

    result = await adapter.complete_json(
        [{"role": "user", "content": "Return JSON"}],
        api_key="provider-secret",
    )
    models = await adapter.list_models(api_key="provider-secret")

    assert result == {"ok": True}
    assert models == [{"id": "model-z"}, {"id": "model-a"}]


def test_glm_and_custom_are_registered_without_kimi():
    definitions = {row.id: row for row in provider_registry.list_definitions()}

    assert set(definitions) == {"deepseek", "glm", "custom", "openai", "gemini"}
    assert "kimi" not in definitions
    assert definitions["glm"].default_model == "glm-5.2"
    assert definitions["custom"].base_url_configurable is True
    assert definitions["custom"].credential_required is False
    assert definitions["glm"].capabilities.tools is True


@pytest.mark.asyncio
async def test_optional_credential_compatible_adapter_omits_authorization_header():
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"data": [{"id": "local-model"}]})

    adapter = OpenAICompatibleAdapter(
        provider_id="local",
        base_url="http://127.0.0.1:1234/v1",
        default_model="local-model",
        message_builder=deepseek_service.build_persona_messages,
        credential_required=False,
        transport=httpx.MockTransport(handler),
    )

    assert await adapter.list_models(api_key=None) == [{"id": "local-model"}]
    assert "authorization" not in requests[0].headers


@pytest.mark.asyncio
async def test_openai_responses_stream_uses_instructions_and_delta_events():
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            text=(
                'data: {"type":"response.output_text.delta","delta":"[EMO:intellectual] 仮説を検証するわ。"}\n\n'
                'data: {"type":"response.completed"}\n\n'
            ),
            headers={"content-type": "text/event-stream"},
        )

    adapter = OpenAIResponsesAdapter(
        message_builder=deepseek_service.build_persona_messages,
        transport=httpx.MockTransport(handler),
    )
    chunks = [
        chunk
        async for chunk in adapter.get_chat_stream(
            active_history=[{"role": "user", "content": "验证这个假设"}],
            api_key="openai-secret",
            model="gpt-5.6-luna",
            reasoning_effort="high",
        )
    ]

    payload = json.loads(requests[0].content)
    assert requests[0].url.path == "/v1/responses"
    assert "[EMO:neutral|" in payload["instructions"]
    assert payload["input"][-1]["content"] == "验证这个假设"
    assert payload["reasoning"] == {"effort": "high"}
    assert chunks == [
        {"content": "[EMO:intellectual] 仮説を検証するわ。", "tool_calls": None}
    ]


@pytest.mark.asyncio
async def test_openai_responses_isolated_stream_bypasses_persona_envelope():
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            text='data: {"type":"response.completed"}\n\n',
            headers={"content-type": "text/event-stream"},
        )

    adapter = OpenAIResponsesAdapter(
        message_builder=deepseek_service.build_persona_messages,
        transport=httpx.MockTransport(handler),
    )
    _ = [
        chunk
        async for chunk in adapter.get_chat_stream(
            active_history=[{"role": "user", "content": "SOURCE JSON"}],
            memory_summary="SECRET MEMORY",
            api_key="openai-secret",
            system_prompt="ISOLATED RENDERER",
            isolated=True,
        )
    ]

    payload = json.loads(requests[0].content)
    assert payload["instructions"] == "ISOLATED RENDERER"
    assert payload["input"] == [{"role": "user", "content": "SOURCE JSON"}]


@pytest.mark.asyncio
async def test_gemini_stream_maps_persona_roles_and_native_parts():
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            text=(
                'data: {"candidates":[{"content":{"parts":[{"text":"[EMO:neutral] データを確認したわ。"}]}}]}\n\n'
            ),
            headers={"content-type": "text/event-stream"},
        )

    adapter = GeminiAdapter(
        message_builder=deepseek_service.build_persona_messages,
        transport=httpx.MockTransport(handler),
    )
    chunks = [
        chunk
        async for chunk in adapter.get_chat_stream(
            active_history=[{"role": "user", "content": "检查数据"}],
            api_key="gemini-secret",
            model="gemini-3.5-flash",
            reasoning_effort="low",
        )
    ]

    payload = json.loads(requests[0].content)
    assert requests[0].url.path.endswith(
        "/models/gemini-3.5-flash:streamGenerateContent"
    )
    assert "[EMO:neutral|" in payload["systemInstruction"]["parts"][0]["text"]
    assert payload["contents"][-1] == {
        "role": "user",
        "parts": [{"text": "检查数据"}],
    }
    assert payload["generationConfig"]["thinkingConfig"] == {
        "thinkingLevel": "low"
    }
    assert chunks[0]["content"] == "[EMO:neutral] データを確認したわ。"
    assert chunks[0]["tool_calls"] is None


@pytest.mark.asyncio
async def test_gemini_same_event_text_and_function_call_stay_one_chunk():
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            text=(
                'data: {"candidates":[{"content":{"parts":['
                '{"text":"前言"},'
                '{"functionCall":{"name":"recall_memory","args":{"needs":["pet"]}}}'
                ']}}]}\n\n'
            ),
            headers={"content-type": "text/event-stream"},
        )

    adapter = GeminiAdapter(
        message_builder=deepseek_service.build_persona_messages,
        transport=httpx.MockTransport(handler),
    )
    chunks = [
        chunk
        async for chunk in adapter.get_chat_stream(
            active_history=[{"role": "user", "content": "宠物"}],
            api_key="gemini-secret",
            model="gemini-3.5-flash",
        )
    ]
    assert len(chunks) == 1
    assert chunks[0]["content"] == "前言"
    assert chunks[0]["tool_calls"][0]["function"]["name"] == "recall_memory"
    assert chunks[0]["tool_calls"][0]["id"].startswith("gemini-")


@pytest.mark.asyncio
async def test_gemini_isolated_stream_bypasses_persona_envelope():
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            text='data: {"candidates":[]}\n\n',
            headers={"content-type": "text/event-stream"},
        )

    adapter = GeminiAdapter(
        message_builder=deepseek_service.build_persona_messages,
        transport=httpx.MockTransport(handler),
    )
    _ = [
        chunk
        async for chunk in adapter.get_chat_stream(
            active_history=[{"role": "user", "content": "SOURCE JSON"}],
            memory_summary="SECRET MEMORY",
            api_key="gemini-secret",
            system_prompt="ISOLATED RENDERER",
            isolated=True,
        )
    ]

    payload = json.loads(requests[0].content)
    assert payload["systemInstruction"] == {
        "parts": [{"text": "ISOLATED RENDERER"}]
    }
    assert payload["contents"] == [
        {"role": "user", "parts": [{"text": "SOURCE JSON"}]}
    ]


def test_openai_and_gemini_native_providers_are_registered():
    definitions = {row.id: row for row in provider_registry.list_definitions()}

    assert definitions["openai"].default_model == "gpt-5.6-luna"
    assert "reasoning_effort" in definitions["openai"].capabilities.parameters
    assert definitions["gemini"].default_model == "gemini-3.5-flash"
    assert definitions["gemini"].capabilities.model_discovery is True


@pytest.mark.asyncio
async def test_glm_native_thinking_payload_is_model_controlled():
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            text=(
                'data: {"choices":[{"delta":{"content":"[EMO:neutral] 検証するわ。"}}]}\n\n'
                "data: [DONE]\n\n"
            ),
            headers={"content-type": "text/event-stream"},
        )

    adapter = OpenAICompatibleAdapter(
        provider_id="glm",
        base_url="https://open.bigmodel.cn/api/paas/v4",
        default_model="glm-5.2",
        message_builder=deepseek_service.build_persona_messages,
        thinking_request_mode="reasoning_effort",
        transport=httpx.MockTransport(handler),
    )

    _ = [
        chunk
        async for chunk in adapter.get_chat_stream(
            active_history=[{"role": "user", "content": "验证"}],
            api_key="glm-secret",
            model="glm-5.2",
            reasoning_effort="minimal",
        )
    ]

    payload = json.loads(requests[0].content)
    assert payload["thinking"] == {"type": "disabled"}
    assert payload["reasoning_effort"] == "minimal"


@pytest.mark.asyncio
async def test_custom_compatible_provider_never_guesses_reasoning_parameters():
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            text="data: [DONE]\n\n",
            headers={"content-type": "text/event-stream"},
        )

    adapter = OpenAICompatibleAdapter(
        provider_id="custom",
        base_url="http://127.0.0.1:1234/v1",
        default_model="unknown-local",
        message_builder=deepseek_service.build_persona_messages,
        credential_required=False,
        transport=httpx.MockTransport(handler),
    )

    _ = [
        chunk
        async for chunk in adapter.get_chat_stream(
            active_history=[{"role": "user", "content": "验证"}],
            api_key=None,
            reasoning_effort="high",
        )
    ]

    payload = json.loads(requests[0].content)
    assert "thinking" not in payload
    assert "reasoning_effort" not in payload


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("provider_id", "model", "kwargs", "thinking_fields"),
    [
        ("glm", "glm-5.2", {}, {"thinking": {"type": "disabled"}, "reasoning_effort": "none"}),
        (
            "glm", "glm-5.2", {"reasoning_effort": "low"},
            {"thinking": {"type": "enabled"}, "reasoning_effort": "low"},
        ),
        ("deepseek", "deepseek-flash", {}, {"thinking": {"type": "disabled"}}),
        ("glm", "uncatalogued", {}, {}),
        ("custom:00000000-0000-0000-0000-000000000001", "glm-5.2", {}, {}),
        (
            "custom:00000000-0000-0000-0000-000000000001", "glm-5.2",
            {"reasoning_effort": "low"}, {},
        ),
    ],
)
async def test_compatible_auxiliary_thinking_follows_the_catalog(
    provider_id, model, kwargs, thinking_fields
):
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(
            200, json={"choices": [{"message": {"content": '{"ok": true}'}}]}
        )

    adapter = OpenAICompatibleAdapter(
        provider_id=provider_id,
        base_url="https://compatible.example/v1",
        default_model="local-model",
        message_builder=deepseek_service.build_persona_messages,
        thinking_request_mode="reasoning_effort",
        transport=httpx.MockTransport(handler),
    )
    result = await adapter.complete_json(
        [{"role": "user", "content": "Return JSON"}],
        api_key="test-key", model=model, **kwargs,
    )

    assert result == {"ok": True}
    (payload,) = requests
    assert {
        key: payload[key]
        for key in ("thinking", "reasoning_effort", "reasoning") if key in payload
    } == thinking_fields


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("adapter_type", "response", "config_field", "effort_field", "default_effort"),
    [
        (OpenAIResponsesAdapter, {"output_text": '{"ok": true}'}, "reasoning", "effort", "none"),
        (
            GeminiAdapter,
            {"candidates": [{"content": {"parts": [{"text": '{"ok": true}'}]}}]},
            "thinkingConfig", "thinkingLevel", "minimal",
        ),
    ],
)
async def test_native_auxiliary_thinking_defaults_and_overrides(
    adapter_type, response, config_field, effort_field, default_effort
):
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json=response)

    adapter = adapter_type(
        message_builder=deepseek_service.build_persona_messages,
        transport=httpx.MockTransport(handler),
    )
    await adapter.translate_to_zh("こんにちは", api_key="test-key")

    def thinking_config(payload):
        return payload.get("generationConfig", payload)[config_field]

    assert thinking_config(requests[0]) == {effort_field: default_effort}
    result = await adapter.complete_json(
        [{"role": "user", "content": "Return JSON"}],
        api_key="test-key", reasoning_effort="low",
    )
    assert result == {"ok": True}
    assert thinking_config(requests[1]) == {effort_field: "low"}


# ---------------------------------------------------------------------------
# MEMORY-V11-S3E-3: D38 identity-aware compression construction.
# ---------------------------------------------------------------------------


def test_compression_messages_identity_self_vs_okabe():
    from app.services.provider_adapters import _compression_messages

    history = [{"role": "user", "content": "こんにちは"}]
    self_msgs = _compression_messages(history, "", "self")
    sys_self = self_msgs[0]["content"]
    assert "利用者を岡部と決めつけない" in sys_self
    assert "ユーザーは岡部と呼ぶ" not in sys_self
    assert "NO_WORKING_CONTEXT" in sys_self
    assert "紅莉栖の会話メモ" in sys_self
    assert "比較中" not in sys_self

    okabe_msgs = _compression_messages(history, "", "okabe")
    assert "ユーザーは岡部と呼ぶ" in okabe_msgs[0]["content"]

    with_old = _compression_messages(history, "比較中、候補Bの確認待ち", "self")
    user_content = with_old[1]["content"]
    assert "比較中、候補Bの確認待ち" in user_content
    assert "これまでの作業コンテキスト" in user_content


@pytest.mark.asyncio
async def test_openai_compatible_compression_uses_d38_identity_contract():
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "比較中、候補Bの確認待ち"}}]},
        )

    adapter = OpenAICompatibleAdapter(
        provider_id="fake-compatible",
        base_url="https://compatible.example/v1",
        default_model="fake-chat",
        message_builder=deepseek_service.build_persona_messages,
        transport=httpx.MockTransport(handler),
    )
    result = await adapter.compress_history(
        history=[{"role": "user", "content": "こんにちは"}],
        api_key="provider-secret",
        old_summary="",
        model="fake-chat",
        identity_mode="self",
    )
    assert result == "比較中、候補Bの確認待ち"
    payload = json.loads(requests[0].content)
    system = payload["messages"][0]["content"]
    assert "利用者を岡部と決めつけない" in system
    assert "ユーザーは岡部と呼ぶ" not in system
    assert "NO_WORKING_CONTEXT" in system
    assert "紅莉栖の会話メモ" in system
