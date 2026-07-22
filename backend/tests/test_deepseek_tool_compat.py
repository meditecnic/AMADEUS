from app.services.deepseek import build_chat_payload
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


def test_v4_flash_tool_routing_also_disables_default_thinking():
    payload = build_chat_payload(
        model="deepseek-v4-flash",
        messages=[{"role": "user", "content": "search the web"}],
        temperature=0.8,
        tools=[{"type": "function", "function": {"name": "web_search"}}],
        tool_choice="auto",
        reasoning_effort="high",
    )

    assert payload["thinking"] == {"type": "disabled"}
    assert payload["tool_choice"] == "auto"


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
