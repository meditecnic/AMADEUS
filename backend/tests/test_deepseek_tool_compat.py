import pytest

from app.services import deepseek
from app.services.deepseek import DeepSeekService, build_chat_payload
from app.routers.chat_ws import build_tool_result_context


def test_v4_pro_tool_routing_disables_thinking_and_keeps_forced_choice():
    forced_search = {
        "type": "function",
        "function": {"name": "web_search"},
    }
    payload = build_chat_payload(
        model="deepseek-v4-pro",
        messages=[{"role": "user", "content": "search the web"}],
        temperature=0.8,
        tools=[{"type": "function", "function": {"name": "web_search"}}],
        tool_choice=forced_search,
        reasoning_effort="xhigh",
    )

    assert payload["thinking"] == {"type": "disabled"}
    assert payload["tool_choice"] == forced_search
    assert "reasoning_effort" not in payload


def test_v4_pro_final_answer_keeps_thinking_without_tool_parameters():
    payload = build_chat_payload(
        model="deepseek-v4-pro",
        messages=[{"role": "user", "content": "answer from the search result"}],
        temperature=0.8,
        tools=None,
        tool_choice=None,
        reasoning_effort="high",
    )

    assert payload["thinking"] == {"type": "enabled"}
    assert payload["reasoning_effort"] == "high"
    assert "tools" not in payload
    assert "tool_choice" not in payload


def test_v4_flash_auto_tool_routing_preserves_native_thinking():
    payload = build_chat_payload(
        model="deepseek-v4-flash",
        messages=[{"role": "user", "content": "search the web"}],
        temperature=0.8,
        tools=[{"type": "function", "function": {"name": "web_search"}}],
        tool_choice="auto",
        reasoning_effort="high",
    )

    assert payload["thinking"] == {"type": "enabled"}
    assert payload["reasoning_effort"] == "high"
    assert "tool_choice" not in payload


def test_v4_flash_final_answer_uses_native_thinking_effort():
    payload = build_chat_payload(
        model="deepseek-v4-flash",
        messages=[{"role": "user", "content": "answer carefully"}],
        temperature=0.0,
        tools=None,
        tool_choice=None,
        reasoning_effort="max",
    )

    assert payload["thinking"] == {"type": "enabled"}
    assert payload["reasoning_effort"] == "max"


@pytest.mark.asyncio
async def test_v4_flash_no_longer_injects_prompt_depth(monkeypatch):
    captured: dict = {}

    class _Response:
        status_code = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def aiter_lines(self):
            yield "data: [DONE]"

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def stream(self, method, url, *, json, headers):
            captured["payload"] = json
            return _Response()

    monkeypatch.setattr(deepseek.httpx, "AsyncClient", lambda **_kwargs: _Client())
    service = DeepSeekService()
    original_system = "SYSTEM PERSONA"

    chunks = [
        chunk
        async for chunk in service.get_chat_stream(
            [],
            api_key="test-secret",
            system_prompt=original_system,
            model="deepseek-v4-flash",
            reasoning_effort="max",
        )
    ]

    assert chunks == []
    system_message = captured["payload"]["messages"][0]["content"]
    assert original_system in system_message
    assert "思考深度" not in system_message
    assert captured["payload"]["thinking"] == {"type": "enabled"}
    assert captured["payload"]["reasoning_effort"] == "max"


def test_tool_results_do_not_fabricate_reasoning_dependent_messages():
    context = build_tool_result_context(
        [{"role": "user", "content": "What changed?"}],
        [("web_search", "A factual result")],
    )

    assert [message["role"] for message in context] == ["user", "system"]
    assert "UNTRUSTED DATA" in context[-1]["content"]
    assert "A factual result" in context[-1]["content"]
    assert all("tool_calls" not in message for message in context)
    assert all("reasoning_content" not in message for message in context)
