import asyncio

import httpx
import pytest

from app.services import deepseek, provider_adapters
from app.services.provider_adapters import (
    GeminiAdapter,
    OpenAICompatibleAdapter,
    OpenAIResponsesAdapter,
    ProviderStallError,
)


class TimedSSE(httpx.AsyncByteStream):
    def __init__(self, lines, *, silent=False):
        self.lines = lines
        self.silent = silent
        self.closed = False

    async def __aiter__(self):
        for line in self.lines:
            await asyncio.sleep(0.005)
            yield (line + "\n").encode("utf-8")
        if self.silent:
            await asyncio.Event().wait()

    async def aclose(self):
        self.closed = True


@pytest.fixture(params=["deepseek", "compatible", "responses", "gemini"])
def streaming_adapter(request, monkeypatch):
    def make(stream):
        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, stream=stream)
        )
        builder = lambda *args, **kwargs: []
        if request.param == "deepseek":
            client = httpx.AsyncClient
            monkeypatch.setattr(
                deepseek.httpx, "AsyncClient",
                lambda **kwargs: client(transport=transport, **kwargs),
            )
            adapter = deepseek.DeepSeekService()
        elif request.param == "compatible":
            adapter = OpenAICompatibleAdapter(
                provider_id="test", base_url="https://test.invalid",
                default_model="test", message_builder=builder, transport=transport,
            )
        elif request.param == "responses":
            adapter = OpenAIResponsesAdapter(message_builder=builder, transport=transport)
        else:
            adapter = GeminiAdapter(message_builder=builder, transport=transport)
        return adapter

    monkeypatch.setattr(provider_adapters, "PROVIDER_STALL_TIMEOUT_SECONDS", 0.2)
    return make


async def collect(adapter):
    return [chunk async for chunk in adapter.get_chat_stream([], api_key="fake")]


@pytest.mark.parametrize("line", [": keep-alive", "", "event: ping", "data: "])
async def test_keepalives_do_not_extend_progress_budget(streaming_adapter, line):
    stream = TimedSSE([line] * 80)
    with pytest.raises(ProviderStallError):
        await asyncio.wait_for(collect(streaming_adapter(stream)), timeout=1)
    assert stream.closed


async def test_silent_stream_is_cancelled_and_closed(streaming_adapter):
    stream = TimedSSE([], silent=True)
    with pytest.raises(ProviderStallError):
        await asyncio.wait_for(collect(streaming_adapter(stream)), timeout=1)
    assert stream.closed


@pytest.mark.parametrize("line", [
    'data: {"choices":[{"delta":{"content":"テスト"}}]}',
    'data: {"choices":[{"delta":{"reasoning_content":"考え中"}}]}',
    'data: {"type":"response.reasoning_text.delta","delta":"thinking"}',
    'data: {"candidates":[{"content":{"parts":[{"text":"考え中","thought":true}]}}]}',
])
async def test_data_lines_advance_budget_even_without_visible_text(streaming_adapter, line):
    # Total duration exceeds the budget; comments between data cannot reset it.
    stream = TimedSSE([line, ": keep-alive", ""] * 25)
    await asyncio.wait_for(collect(streaming_adapter(stream)), timeout=2)
    assert stream.closed


async def test_done_is_a_progress_line(monkeypatch):
    # All adapters use this iterator before their existing DONE parsing.
    monkeypatch.setattr(provider_adapters, "PROVIDER_STALL_TIMEOUT_SECONDS", 0.2)
    stream = TimedSSE(["data: [DONE]", ": keep-alive"] * 25)
    response = httpx.Response(200, stream=stream)
    try:
        lines = [line async for line in provider_adapters.iter_provider_lines(response)]
    finally:
        await response.aclose()
    assert lines.count("data: [DONE]") == 25
