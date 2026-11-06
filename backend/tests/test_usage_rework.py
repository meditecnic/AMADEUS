import json

import httpx
import pytest

from app.services import model_usage as accounting
from app.services.deepseek import DeepSeekService
from app.services.provider_adapters import GeminiAdapter, OpenAICompatibleAdapter, OpenAIResponsesAdapter
from test_model_usage import rows, usage_store  # noqa: F401


def adapter_for(kind, handler, monkeypatch):
    transport = httpx.MockTransport(handler)
    builder = lambda history, *args: history
    if kind == 'compatible':
        return OpenAICompatibleAdapter(provider_id='custom', base_url='https://example.invalid',
            default_model='test-model', message_builder=builder, transport=transport)
    if kind == 'responses':
        return OpenAIResponsesAdapter(message_builder=builder, transport=transport)
    if kind == 'gemini':
        return GeminiAdapter(message_builder=builder, transport=transport)
    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kwargs: original(transport=transport, **kwargs))
    return DeepSeekService()


def stream_event(kind, *, usage=None, text='reply'):
    if kind == 'responses':
        return ({'type': 'response.completed', 'response': {'usage': usage}} if usage is not None
                else {'type': 'response.output_text.delta', 'delta': text})
    if kind == 'gemini':
        return {'candidates': [{'content': {'parts': [{'text': text}]}}],
                **({'usageMetadata': usage} if usage is not None else {})}
    return {'choices': [{'delta': {'content': text}}], **({'usage': usage} if usage is not None else {})}


@pytest.mark.parametrize('kind', ['compatible', 'responses', 'deepseek'])
@pytest.mark.parametrize('details', [['malformed'], 'malformed', 23, True])
@pytest.mark.asyncio
async def test_malformed_usage_never_interrupts_stream(kind, details, usage_store, monkeypatch):
    counts = {'prompt_tokens': 100, 'completion_tokens': 20, 'prompt_tokens_details': details}
    if kind == 'responses':
        counts = {'input_tokens': 100, 'output_tokens': 20, 'input_tokens_details': details}
    events = [stream_event(kind), stream_event(kind, usage=counts), stream_event(kind, text='after')]
    def handler(request):
        return httpx.Response(200, text=''.join('data: '+json.dumps(e)+'\n\n' for e in events)+'data: [DONE]\n\n')
    adapter = adapter_for(kind, handler, monkeypatch)
    result = [chunk async for chunk in adapter.get_chat_stream([], api_key='test-key')]
    assert ''.join(chunk.get('content') or '' for chunk in result).endswith('after')
    record, = rows(usage_store)
    assert record['reported'] == 0
    assert record['input_tokens'] is record['output_tokens'] is record['cached_tokens'] is None


@pytest.mark.parametrize('kind', ['compatible', 'responses', 'gemini', 'deepseek'])
@pytest.mark.parametrize('usage', ['wrong type', [100], {'prompt_tokens': [], 'completion_tokens': 'twenty'},
                                   {'promptTokenCount': {}, 'candidatesTokenCount': []}])
@pytest.mark.asyncio
async def test_bad_counts_in_all_provider_streams_keep_reply(kind, usage, usage_store, monkeypatch):
    events = [stream_event(kind, usage=usage), stream_event(kind, text='completed')]
    adapter = adapter_for(kind, lambda request: httpx.Response(200, text=''.join(
        'data: '+json.dumps(e)+'\n\n' for e in events)+'data: [DONE]\n\n'), monkeypatch)
    result = [chunk async for chunk in adapter.get_chat_stream([], api_key='test-key')]
    assert result[-1]['content'] == 'completed'
    assert rows(usage_store)[0]['reported'] == 0


def test_parser_exception_clears_partial_counts_and_is_sticky():
    usage = accounting.Usage('custom', 'model')
    usage.observe({'usage': {'prompt_tokens': 10, 'completion_tokens': 2}})
    usage.observe({'usage': {'prompt_tokens': 20, 'completion_tokens': 3, 'prompt_tokens_details': 'bad'}})
    usage.observe({'usage': {'prompt_tokens': 30, 'completion_tokens': 4}})
    assert usage.input is usage.output is usage.cached is None


class InterruptedStream(httpx.AsyncByteStream):
    def __init__(self, first):
        self.first = first

    async def __aiter__(self):
        if self.first:
            yield self.first
        raise httpx.ReadError('synthetic interrupted body')


@pytest.mark.parametrize('kind', ['compatible', 'responses', 'gemini', 'deepseek'])
@pytest.mark.parametrize('method', ['stream', 'json', 'translation', 'compression'])
@pytest.mark.parametrize('failure', ['401', 'connect'])
@pytest.mark.asyncio
async def test_rejected_or_unconnected_requests_are_not_unreported(kind, method, failure, usage_store, monkeypatch):
    def handler(request):
        if failure == '401':
            return httpx.Response(401, json={'error': 'rejected'})
        raise httpx.ConnectError('synthetic connect failure', request=request)
    adapter = adapter_for(kind, handler, monkeypatch)
    # DeepSeek's existing compression intentionally returns '' on upstream error;
    # the other entry points keep their existing exception contracts.
    if kind == 'deepseek' and method == 'compression':
        assert await adapter.compress_history([], api_key='test-key') == ''
    else:
        with pytest.raises(Exception):
            if method == 'stream':
                _ = [chunk async for chunk in adapter.get_chat_stream([], api_key='test-key')]
            elif method == 'json':
                await adapter.complete_json([], api_key='test-key')
            elif method == 'translation':
                await adapter.translate_to_zh('hello', api_key='test-key')
            else:
                await adapter.compress_history([], api_key='test-key')
    assert rows(usage_store) == []


@pytest.mark.parametrize('kind', ['compatible', 'responses', 'gemini', 'deepseek'])
@pytest.mark.parametrize('has_body', [False, True])
@pytest.mark.asyncio
async def test_stream_body_start_controls_truncated_call_accounting(kind, has_body, usage_store, monkeypatch):
    first = ('data: '+json.dumps(stream_event(kind))+'\n\n').encode('utf-8') if has_body else b''
    adapter = adapter_for(kind, lambda req: httpx.Response(200, stream=InterruptedStream(first)), monkeypatch)
    chunks = []
    with pytest.raises(httpx.ReadError):
        async for chunk in adapter.get_chat_stream([], api_key='test-key'):
            chunks.append(chunk)
    if has_body:
        assert chunks[0]['content'] == 'reply'
        record, = rows(usage_store)
        assert record['reported'] == 0 and record['input_tokens'] is None
    else:
        assert rows(usage_store) == []


@pytest.mark.parametrize('kind', ['compatible', 'responses', 'gemini', 'deepseek'])
@pytest.mark.asyncio
async def test_empty_200_stream_does_not_create_a_call(kind, usage_store, monkeypatch):
    adapter = adapter_for(kind, lambda req: httpx.Response(200, content=b''), monkeypatch)
    assert [chunk async for chunk in adapter.get_chat_stream([], api_key='test-key')] == []
    assert rows(usage_store) == []
