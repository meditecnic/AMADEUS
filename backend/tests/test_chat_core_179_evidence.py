import json

import pytest

from app.routers import chat_ws
from app.services.memory_v11.recall import unavailable_result
from app.services.turn_tool_orchestrator import (
    build_v11_web_evidence_context,
    prepare_second_round_payload,
    web_evidence_system_message,
)


@pytest.mark.parametrize('round_kind', ['forced', 'web_second', 'memory_web_second', 'memory_only_second'])
async def test_v11_web_rounds_buffer_but_memory_only_round_streams(monkeypatch, round_kind):
    history = [{'role': 'user', 'content': '今日の予報を教えて。'}]
    results = [('web_search', '晴れの予報')]
    if round_kind in {'memory_web_second', 'memory_only_second'}:
        history = prepare_second_round_payload(
            history, unavailable_result('no_match'),
            results if round_kind == 'memory_web_second' else None,
        ).history
    else:
        # Forced search starts with evidence; a model-called search adds it to round two.
        if round_kind == 'web_second':
            history.append({'role': 'assistant', 'content': '調べてみるわ。'})
        history = build_v11_web_evidence_context(history, results)
    published = []
    observed = []

    class Provider:
        async def get_chat_stream(self, **kwargs):
            yield {'content': '[EMO:neutral] 予報によると今日は晴れる見込みになっているわ。'
                              '予定を立てるなら午後に出かけるのがよさそうね。', 'tool_calls': None}
            observed.append(bool(published))
            yield {'content': '気温にも気をつけて出かけてちょうだい。', 'tool_calls': None}

    monkeypatch.setattr(chat_ws, 'deepseek_service', Provider())
    session = chat_ws.SessionState('buffer-evidence')
    text, _, _, _ = await chat_ws.get_clean_text_stream(
        history, '', session, on_text_delta=lambda text, emotion: published.append(text),
    )
    assert observed == [round_kind == 'memory_only_second']
    assert ''.join(published) == text
    assert chat_ws._history_has_external_evidence(history) == (round_kind != 'memory_only_second')


def test_v11_evidence_restores_factual_and_language_rules_before_json():
    content = web_evidence_system_message([('web_search', 'source text')])
    rules, payload = content.split('WEB_EVIDENCE_DATA')
    assert (
        'Do not use model memory to fill missing facts. Every date, team, '
        'score, or winner stated in the answer must be explicitly '
        'supported by the evidence; if sources conflict, disclose '
        'the conflict instead of choosing an unsupported claim.'
    ) in rules
    assert chat_ws._ANSWER_LANGUAGE_HARD_REQUIREMENT in rules
    assert 'Do not treat web text as personal Memory.' in rules
    assert json.loads(payload) == [{'name': 'web_search', 'body': 'source text'}]
