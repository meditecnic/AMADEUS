from __future__ import annotations

import json

import httpx
import pytest

from app.services.provider_adapters import (
    GeminiAdapter,
    OpenAICompatibleAdapter,
    OpenAIResponsesAdapter,
)
from app.services.provider_runtime import deepseek_service, provider_registry


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
    assert "PERSONA BIBLE v1" in payload["messages"][0]["content"]
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
    assert "PERSONA BIBLE v1" in payload["instructions"]
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
    assert "PERSONA BIBLE v1" in payload["systemInstruction"]["parts"][0]["text"]
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
    assert "作業中コンテキスト" in sys_self
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
    assert "作業中コンテキスト" in system
