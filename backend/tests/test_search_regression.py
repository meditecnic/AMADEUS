import pytest
import json
import asyncio
from unittest.mock import patch, MagicMock, AsyncMock
from app.services.search import (
    SearchEvidenceError,
    SearchResponse,
    SearchService,
    prepare_search_query,
    web_search,
    _extract_city,
)
from app.routers import chat_ws as _chat_ws_mod
from app.routers.chat_ws import (
    build_tool_result_context,
    build_web_search_evidence_context,
    execute_tool_call,
    get_clean_text_stream,
    SessionState,
    should_force_search,
    _ANSWER_LANGUAGE_HARD_REQUIREMENT,
    _history_has_external_evidence,
)


@pytest.fixture(autouse=True)
def _clean_sessions():
    """Ensure each test starts with an empty sessions dict (in-place).

    IMPORTANT: use .clear() — never reassign chat_ws.sessions = {}.
    Reassignment replaces the module-level dict object, breaking other
    test modules that captured the original reference via
    ``from app.routers.chat_ws import sessions``.
    """
    _chat_ws_mod.sessions.clear()
    yield
    _chat_ws_mod.sessions.clear()


# --- Unit tests for search.py ---

def test_extract_city_strips_weather_keywords():
    assert _extract_city("今天北京天气怎么样") == "北京"
    assert _extract_city("東京の天気") == "東京"
    assert _extract_city("Akihabara weather today") == "Akihabara"

def test_extract_city_defaults_to_akihabara():
    assert _extract_city("今天天气怎么样") == "Akihabara"

def test_should_force_search_detects_weather():
    assert should_force_search("今天天气怎么样")
    assert should_force_search("東京の天気")
    assert should_force_search("What's the weather in Akihabara?")

def test_should_force_search_detects_news():
    assert should_force_search("最新新闻")
    assert should_force_search("Search for Steins;Gate")

def test_should_force_search_ignores_normal_chat():
    assert not should_force_search("助手じゃない")
    assert not should_force_search("你好吗")


def test_should_force_search_detects_explicit_web_search_phrase():
    assert should_force_search("请你web search一下今年的世界杯冠军是哪个国家队")


def test_web_search_evidence_context_requires_japanese_answer_body():
    history = [{"role": "user", "content": "请你web search一下今年的世界杯冠军"}]
    injected = build_web_search_evidence_context(
        history,
        "[source: tavily]\nSpain won the 2026 FIFA World Cup.",
    )
    assert len(injected) == 2
    content = injected[-1]["content"]
    assert "WEB SEARCH RESULT" in content
    assert "Spain won" in content
    assert "ANSWER LANGUAGE — HARD REQUIREMENT" in content
    assert "自然な日本語" in content
    assert _ANSWER_LANGUAGE_HARD_REQUIREMENT.split("\n")[0] in content
    assert _history_has_external_evidence(injected)


def test_tool_result_context_requires_japanese_answer_body():
    history = [{"role": "user", "content": "search weather"}]
    injected = build_tool_result_context(
        history,
        [("web_search", "[source: tavily]\nrain in Tokyo")],
    )
    content = injected[-1]["content"]
    assert "TOOL RESULTS" in content
    assert "ANSWER LANGUAGE — HARD REQUIREMENT" in content
    assert _history_has_external_evidence(injected)


@pytest.mark.asyncio
async def test_post_search_chinese_draft_retries_to_japanese_without_full_recovery():
    """WEB-SEARCH-LANG-01: Chinese body after evidence must not end as bare recovery."""

    class _SequencedStreamProvider:
        def __init__(self, outputs: list[str]):
            self.outputs = outputs
            self.call_count = 0
            self.prompts: list[str] = []
            self.temperatures: list[float] = []

        async def get_chat_stream(self, **kwargs):
            self.prompts.append(kwargs.get("system_prompt") or "")
            self.temperatures.append(float(kwargs.get("temperature") or 0))
            output = self.outputs[min(self.call_count, len(self.outputs) - 1)]
            self.call_count += 1
            midpoint = max(1, len(output) // 2)
            yield {"content": output[:midpoint], "tool_calls": None}
            yield {"content": output[midpoint:], "tool_calls": None}

    provider = _SequencedStreamProvider(
        [
            "[EMO:intellectual] 根据搜索结果，2026年世界杯冠军是西班牙。",
            "[EMO:intellectual] 検索結果を見る限り、2026年ワールドカップ優勝はスペインと出てるわ。",
        ]
    )
    session = SessionState("web-search-lang")
    session.api_key = "test-key"
    session.system_prompt = "日本語で答えること。"
    session.temperature = 0.4
    history = build_web_search_evidence_context(
        [{"role": "user", "content": "请你web search一下今年的世界杯冠军是哪个国家队"}],
        "[source: tavily]\nSpain won the 2026 FIFA World Cup.",
    )
    published: list[str] = []

    with patch("app.routers.chat_ws.deepseek_service", provider):
        text, emotion, _, found = await get_clean_text_stream(
            active_history=history,
            memory_summary="",
            session=session,
            on_text_delta=lambda delta, _emotion: published.append(delta),
        )

    visible = "".join(published)
    assert provider.call_count == 2
    assert any("中国語または英語" in prompt or "自然な日本語" in prompt for prompt in provider.prompts[1:])
    assert provider.temperatures[1] == 0.0
    assert "スペイン" in visible
    assert "根据搜索结果" not in visible
    assert "日本語の応答を正しく生成できなかった" not in visible
    assert "もう一度試して" not in text
    assert text.strip() == "検索結果を見る限り、2026年ワールドカップ優勝はスペインと出てるわ。"
    assert emotion == "intellectual"
    assert found is True


def test_search_response_requires_real_evidence():
    assert SearchResponse(
        query="世界杯冠军",
        source="duckduckgo",
        evidence="Title: FIFA\nURL: https://example.test\nContent: verified result",
        degraded_reason="tavily_key_missing",
    ).failure_code is None
    assert SearchResponse(
        query="世界杯冠军",
        source="duckduckgo",
        evidence="No results.",
        degraded_reason="tavily_key_missing",
    ).failure_code == "no_results"
    assert SearchResponse(
        query="世界杯冠军",
        source="none",
        evidence="Search unavailable: local path must not leak",
        degraded_reason="duckduckgo_error",
    ).failure_code == "unavailable"


def test_current_world_cup_query_is_resolved_to_official_current_result():
    from datetime import datetime

    prepared = prepare_search_query(
        "请你web search一下今年的世界杯冠军是哪个国家队",
        now=datetime(2026, 7, 22),
    )
    assert "2026 FIFA World Cup" in prepared
    assert "winner" in prepared
    assert "site:fifa.com" in prepared
    assert prepare_search_query("历届世界杯冠军", now=datetime(2026, 7, 22)) == "历届世界杯冠军"


@pytest.mark.asyncio
async def test_keyless_search_fails_closed_without_duckduckgo(tmp_path, monkeypatch):
    """No paid key → unavailable; DuckDuckGo degradation path is removed."""
    monkeypatch.setenv("AMADEUS_DB_PATH", str(tmp_path / "query-plan.db"))
    from app.db import init_db
    await init_db()

    service = SearchService()
    service.configured_key = lambda provider: ""  # type: ignore[method-assign]
    service._consume_search_credit = AsyncMock(return_value=(True, None))
    service._tavily = AsyncMock()
    service._firecrawl = AsyncMock()

    response = await service.search("请你web search一下今年的世界杯冠军是哪个国家队")

    assert response.source == "none"
    assert response.failure_code == "unavailable"
    assert "tavily_key_missing" in (response.degraded_reason or "") or "firecrawl" in (
        response.degraded_reason or ""
    ) or "no_paid" in (response.degraded_reason or "") or "key_missing" in (
        response.degraded_reason or ""
    )
    service._tavily.assert_not_awaited()
    service._firecrawl.assert_not_awaited()


@pytest.mark.asyncio
async def test_paid_provider_failure_does_not_call_duckduckgo(tmp_path, monkeypatch):
    monkeypatch.setenv("AMADEUS_DB_PATH", str(tmp_path / "query-fail.db"))
    from app.db import init_db
    await init_db()

    service = SearchService()
    service.configured_key = lambda provider: "tvly-x" if provider == "tavily" else ""  # type: ignore[method-assign]
    service.get_active_provider = lambda: "tavily"  # type: ignore[method-assign]
    service._consume_search_credit = AsyncMock(return_value=(True, None))
    service._tavily = AsyncMock(side_effect=RuntimeError("upstream down"))
    service._firecrawl = AsyncMock()

    with patch("app.services.search.DDGS") as mock_ddgs:
        response = await service.search("秋葉原 天気")

    assert response.source == "none"
    assert response.failure_code == "unavailable"
    # After tavily fails, inactive firecrawl is key_missing — still no DDG.
    assert response.degraded_reason in {
        "tavily_error:RuntimeError",
        "firecrawl_key_missing",
    }
    mock_ddgs.assert_not_called()
    service._firecrawl.assert_not_awaited()


@pytest.mark.asyncio
async def test_execute_tool_call_rejects_empty_search_evidence():
    response = SearchResponse(
        query="今年的世界杯冠军",
        source="duckduckgo",
        evidence="No results.",
        degraded_reason="tavily_key_missing",
    )
    with patch(
        "app.services.search.search_service.search",
        new_callable=AsyncMock,
        return_value=response,
    ):
        with pytest.raises(SearchEvidenceError) as caught:
            await execute_tool_call(
                "web_search",
                json.dumps({"query": "今年的世界杯冠军"}, ensure_ascii=False),
            )

    assert caught.value.code == "no_results"
    assert "local path" not in str(caught.value)

@pytest.mark.asyncio
async def test_weather_search_uses_wttrin(tmp_path, monkeypatch):
    monkeypatch.setenv("AMADEUS_DB_PATH", str(tmp_path / "test.db"))
    from app.db import init_db
    await init_db()

    fake_json = {
        "current_condition": [{"temp_C": "24", "weatherDesc": [{"value": "Sunny"}]}],
        "weather": [
            {"date": "2026-06-29", "maxtempC": "26", "mintempC": "20",
             "hourly": [{"weatherDesc": [{"value": "Clear"}]}]}
        ]
    }

    class FakeResponse:
        status_code = 200
        def json(self): return fake_json

    with patch("app.services.search.httpx.get", return_value=FakeResponse()):
        result = web_search("今天北京天气怎么样")
        assert "北京" in result
        assert "24" in result
        assert "Sunny" in result or "Clear" in result

@pytest.mark.asyncio
async def test_general_search_uses_ddgs(tmp_path, monkeypatch):
    monkeypatch.setenv("AMADEUS_DB_PATH", str(tmp_path / "test.db"))
    from app.db import init_db
    await init_db()

    fake_result = [{"title": "Steins;Gate", "body": "A visual novel about time travel."}]
    fake_ddgs = MagicMock()
    fake_ddgs.__enter__ = MagicMock(return_value=fake_ddgs)
    fake_ddgs.__exit__ = MagicMock(return_value=False)
    fake_ddgs.text = MagicMock(return_value=fake_result)

    with patch("app.services.search.DDGS", return_value=fake_ddgs):
        result = web_search("Steins;Gate story")
        assert "Steins;Gate" in result
        assert "time travel" in result

# --- Integration test for chat_ws tool-call flow ---

@pytest.mark.asyncio
async def test_processor_loop_triggers_web_search_for_weather(tmp_path, monkeypatch):
    """
    Verify that a weather question causes the backend to emit a web_search notice
    and call execute_tool_call (mocked) rather than returning plain text.
    """
    monkeypatch.setenv("AMADEUS_DB_PATH", str(tmp_path / "flow.db"))
    from app.db import init_db
    await init_db()

    from app.routers import chat_ws
    from app.routers.chat_ws import SessionState, processor_loop

    chat_ws.sessions.clear()
    session = SessionState("search-test")
    session.api_key = "sk-test"
    session.model = "deepseek-v4-pro"
    session.system_prompt = "You are Kurisu. Always use web_search for weather."
    session.enable_tts = False
    session.temperature = 0.8
    chat_ws.sessions["search-test"] = session

    send_queue = asyncio.Queue()
    epoch = 1
    start_history_epoch = session.history_epoch
    stream_calls = 0

    async def fake_clean_stream(
        *,
        active_history,
        memory_summary,
        session,
        tools,
            tool_choice,
            default_emotion="neutral",
            on_text_delta=None,
            provider_snapshot=None,
        ):
        nonlocal stream_calls
        stream_calls += 1
        # First call: simulate model emitting a web_search tool call
        if tools is not None:
            assert tools is not None
            return "", "neutral", {
                0: {
                    "id": "call_123",
                    "type": "function",
                    "function": {
                        "name": "web_search",
                        "arguments": json.dumps({"query": "北京 天气"})
                    }
                }
            }, True
        else:
            # Second call after tool result
            assert tools is None
            assert tool_choice is None
            assert active_history[-1]["role"] == "system"
            assert "WEB SEARCH RESULT" in active_history[-1]["content"]
            return "[EMO:neutral] 北京は晴れ、24度よ。", "neutral", {}, True

    sent_statuses = []
    original_put = send_queue.put
    async def capturing_put(item):
        sent_statuses.append(item)
        await original_put(item)

    with patch.object(chat_ws, "get_clean_text_stream", side_effect=fake_clean_stream), \
         patch.object(chat_ws, "execute_tool_call", new_callable=AsyncMock, return_value="地点: 北京\n当前实况: 晴, 24°C") as mock_exec, \
         patch.object(send_queue, "put", side_effect=capturing_put), \
         patch.object(chat_ws.deepseek_service, "translate_to_zh", new_callable=AsyncMock, return_value="北京晴，24度。"):
        await processor_loop(session, "今天北京天气怎么样", send_queue, epoch, start_history_epoch)

    # Verify execute_tool_call was invoked for web_search
    assert mock_exec.called
    call_args = mock_exec.call_args
    assert call_args[0][0] == "web_search"

    # Verify a status frame with tool=web_search was emitted
    tool_statuses = [p for _, p in sent_statuses if p.get("type") == "status" and p.get("tool") == "web_search"]
    assert len(tool_statuses) >= 1


@pytest.mark.asyncio
async def test_processor_does_not_generate_answer_without_search_evidence(tmp_path, monkeypatch):
    monkeypatch.setenv("AMADEUS_DB_PATH", str(tmp_path / "no-evidence.db"))
    from app.db import init_db
    await init_db()

    from app.routers import chat_ws
    from app.routers.chat_ws import SessionState, processor_loop

    chat_ws.sessions.clear()
    session = SessionState("search-no-evidence")
    session.api_key = "sk-test"
    session.model = "deepseek-v4-pro"
    session.system_prompt = "日本語で答えること。"
    session.enable_tts = False
    chat_ws.sessions[session.session_id] = session
    send_queue = asyncio.Queue()
    model_stream = AsyncMock()

    with patch.object(
        chat_ws,
        "execute_tool_call",
        new_callable=AsyncMock,
        side_effect=SearchEvidenceError("no_results"),
    ), patch.object(chat_ws, "get_clean_text_stream", model_stream):
        await processor_loop(
            session,
            "请你web search一下今年的世界杯冠军是哪个国家队",
            send_queue,
            1,
            session.history_epoch,
        )

    model_stream.assert_not_awaited()
    payloads = []
    while not send_queue.empty():
        _, payload = await send_queue.get()
        payloads.append(payload)
    errors = [payload for payload in payloads if payload.get("type") == "error"]
    assert errors == [{
        "type": "error",
        "code": "web_search_no_results",
        "recoverable": True,
        "message": "Web Search 没有返回可验证的结果，本轮未生成答案。",
    }]
