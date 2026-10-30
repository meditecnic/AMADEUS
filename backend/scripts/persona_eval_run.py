"""Offline persona evaluation through compile_for_session/get_clean_text_stream.

From backend: python scripts/persona_eval_run.py --run-dir /tmp/persona-run
--judge-pack RUN creates independent, manually scored judge files.
--merge-judgements RUN A.json B.json requires judges from different families.
--compare OLD NEW writes persona-comparison.{json,md} in NEW.
Fake replies are constant plumbing evidence, never persona-quality evidence.
Real HTTP uses B5's authorization/key/official-URL gate and --cap-cny (<=10).
The cap covers one run directory only. Interrupted executions are not reissued.
Tools use local no-result web stubs. Product history compression runs synchronously.
No app lifespan, real search, TTS, subtitle or memory-ingest jobs are started.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from contextlib import ExitStack, contextmanager, nullcontext
import hashlib
import json
import os
from pathlib import Path
import re
import statistics
import subprocess
import sys
from unittest.mock import patch

import httpx
import portalocker

SCRIPT = Path(__file__).resolve()
BACKEND = SCRIPT.parents[1]
sys.path.insert(0, str(SCRIPT.parent))
sys.path.insert(0, str(BACKEND))
import memory_recall_b5_real_run as b5  # noqa: E402

QUESTION_SET = BACKEND / 'tests/fixtures/persona_eval_v1.json'
BIBLE = BACKEND / 'characters/persona_bible_v1.json'
REGRESSIONS = BACKEND / 'characters/persona_regressions_v1.json'
Refused = b5.Refused
read_json, write_json = b5.read_json, b5.write_json
CHECKS = ['japanese', 'single_legal_emo', 'self_address', 'fact_original_terms']
EMO_RE = re.compile(r'\[EMO:([^\]]*)\]')
MAX_OUTPUT_TOKENS = 8000
JUDGE_FAMILIES = {'openai', 'anthropic', 'google', 'moonshot', 'deepseek'}
# Keep the unwrapped factory for Capture's explicitly gated real HTTP boundary.
HTTP_CLIENT = httpx.AsyncClient


@contextmanager
def fake_guard():
    """B5's socket/HTTP guard plus refusal of child-process network escapes."""
    def refused(*_args, **_kwargs):
        raise Refused('subprocess_forbidden_in_fake')

    with b5.fake_network_guard(), ExitStack() as stack:
        stack.enter_context(patch.object(subprocess, 'Popen', refused))
        for name in ('system', 'posix_spawn', 'posix_spawnp', 'fork', 'forkpty'):
            if hasattr(os, name):
                stack.enter_context(patch.object(os, name, refused))
        yield


def inputs(case: dict, qset: dict) -> list[str]:
    return qset['dialogues'][case['dialogue_id']] if 'dialogue_id' in case else [case['input']]


def validate_questions(qset: dict) -> None:
    if qset['version'] != 'persona-eval-v1':
        raise Refused('unknown_question_set')
    cases = qset['cases']
    if len({c['id'] for c in cases}) != len(cases):
        raise Refused('duplicate_case')
    originals = {c['id']: c for c in read_json(REGRESSIONS)['cases']}
    imported = [c for c in cases if 'source_id' in c]
    if Counter(c['source_id'] for c in imported) != Counter({key: 1 for key in originals}):
        raise Refused('regressions_missing_or_duplicated')
    for case in cases:
        if case.get('source_id'):
            original = originals[case['source_id']]
            if any(case.get(key) != value for key, value in original.items()):
                raise Refused('regression_text_changed')
        if case['identity_mode'] not in {'self', 'okabe'} or case['worldline'] not in {'beta', 'steins_gate'}:
            raise Refused('invalid_persona_scope')
        if case['mechanical_checks'] != CHECKS:
            raise Refused('invalid_mechanical_checks')
        for key in ('required_traits', 'forbidden_traits'):
            if not case[key] or not all(isinstance(t, str) and t.strip() for t in case[key]):
                raise Refused('invalid_judge_checks')
        lines = inputs(case, qset)
        if not lines or not all(isinstance(line, str) and line.strip() for line in lines):
            raise Refused('invalid_inputs')
        if 'dialogue_id' in case:
            probes = case['probe_turns']
            if len(lines) != 60 or probes != [1, 30, 60] or len({lines[n-1] for n in probes}) != 1:
                raise Refused('invalid_drift_dialogue')
        for fact in case.get('resident_facts', []):
            if not isinstance(fact['text'], str) or not fact['text'].strip() or not all(
                    isinstance(term, str) and term for term in fact['original_terms']):
                raise Refused('invalid_resident_fact')


def make_plan(*, transport='fake', base_url=b5.BUILTIN_DEEPSEEK_BASE_URL,
              cap_cny=None, model='deepseek-flash') -> dict:
    qset = read_json(QUESTION_SET)
    validate_questions(qset)
    return {'version': 1, 'transport': transport, 'base_url': b5.canonical_base_url(base_url),
            'cap_cny': cap_cny, 'model': model,
            'memory_mode': (os.environ.get('AMADEUS_MEMORY_MODE') or 'legacy').strip().lower(),
            'question_sha256': b5.sha256(QUESTION_SET), 'script_sha256': b5.sha256(SCRIPT),
            'bible_sha256': b5.sha256(BIBLE), 'question_set': qset, 'bible': read_json(BIBLE),
            'compression_threshold': int(os.environ.get('HISTORY_COMPRESS_THRESHOLD', '15')),
            'intentional_product_differences': [
                'Add max_tokens=8000 to every request for bounded budget reservations.',
                'Add stream_options.include_usage=true to streaming requests for accounting.',
                'Await product compression synchronously; product schedules it in the background.',
                'Web tools/direct search return a fixed local no-results stub; no real search.',
                'v11 unsupported tool plans are refused; product degrades and continues (not reached with DeepSeek).',
                'Collect desktop text only; no subtitle, TTS or background memory extraction.',
            ],
            'executions': [{'id': c['id']} for c in qset['cases']]}


def mechanical_score(case: dict, body: str, raw_output: str, allowed: set[str]) -> dict:
    from app.domain.segments import IncrementalJapaneseSegmenter, is_ignorable_non_speech_segment
    from app.routers.chat_ws import _is_publishable_japanese

    segmenter = IncrementalJapaneseSegmenter()
    parts = [s.text for s in [*segmenter.feed(body), *segmenter.finish()]
             if not is_ignorable_non_speech_segment(s.text)]
    tags = EMO_RE.findall(raw_output)
    legal_emo = (len(tags) == 1 and tags[0] in allowed
                 and raw_output.lstrip().startswith(f'[EMO:{tags[0]}]')
                 and raw_output.count('[EMO') == 1)
    # A conservative address flag, not a semantic entity recognizer. Ordinary
    # third-person references and explicit denials are left to the judges.
    address = []
    if case['identity_mode'] == 'self':
        for hit in re.finditer(r'(?:岡部(?:倫太郎)?|冈部(?:伦太郎)?|凶真|オカリン)', body):
            left, right = body[:hit.start()], body[hit.end():]
            if re.match(r'(?:さん|君|くん)?[、，,！!？?。\s]|$', right):
                if not left.endswith(('は', 'の', 'と', '「', '『')):
                    address.append(hit.group())
    leaked = sorted({term for fact in case.get('resident_facts', [])
                     for term in fact['original_terms'] if term in body})
    return {'japanese': bool(parts) and all(_is_publishable_japanese(p) for p in parts),
            'single_legal_emo': legal_emo, 'self_address': address, 'fact_original_terms': leaked}


def fake_response(payload: dict, kind: str) -> str:
    if kind == 'compression':
        return json.dumps({'choices': [{'message': {'content': '日常の話題について会話を続けている。'},
                                        'finish_reason': 'stop'}],
                           'usage': {'prompt_tokens': b5.estimate_input_tokens(payload),
                                     'completion_tokens': 32, 'prompt_cache_hit_tokens': 0}}, ensure_ascii=False)
    body = b5.fake_response(payload, 'persona')
    ending = 'data: ' + json.dumps({'choices': [{'delta': {}, 'finish_reason': 'stop'}]}) + '\n\n'
    return body if '"finish_reason"' in body else body.replace('data: [DONE]', ending + 'data: [DONE]')


class Capture:
    def __init__(self, plan: dict, directory: Path, turn: int):
        self.plan, self.directory, self.turn = plan, directory, turn
        self.phase = 'first'
        self.history_messages = 0
        self.requests: list[dict] = []
        self.refusal: Exception | None = None
        self.budget = b5.Budget(directory.parents[1] / 'budget.json', plan['cap_cny']) if plan['transport'] == 'real' else None

    def raise_if_refused(self):
        if self.refusal:
            raise self.refusal

    async def handle(self, request: httpx.Request) -> httpx.Response:
        self.raise_if_refused()
        try:
            return await self._handle(request)
        except Exception as exc:
            if self.budget and self.budget.amounts()['retained_reserve_cny'] != '0':
                exc = b5.AccountingUncertain('request_failed_reservation_retained')
            self.refusal = exc
            raise exc from None
        finally:
            write_json(self.directory / f'turn-{self.turn:02d}-requests.json', self.requests)

    async def _handle(self, request: httpx.Request) -> httpx.Response:
        b5.check_real_gate(self.plan['transport'], self.plan['base_url'], self.plan['cap_cny'])
        if request.method != 'POST' or str(request.url) != self.plan['base_url'] + '/chat/completions':
            raise Refused('unexpected_model_destination')
        payload = json.loads(request.content)
        kind = 'compression' if self.phase == 'compression' else b5.request_kind(payload, 'main')
        attempt = 1 + sum(r['kind'] == kind and r['phase'] == self.phase for r in self.requests)
        limit = 1 if kind == 'compression' else b5.MAX_RENDER_ATTEMPTS if kind == 'japanese_rewrite' else b5.MAX_MAIN_ATTEMPTS
        if attempt > limit:
            raise Refused('extra_model_request_blocked')
        # Preserve DeepSeekService/build_chat_payload's exact product envelope;
        # only these two billing controls deliberately differ (see plan.json).
        payload['max_tokens'] = MAX_OUTPUT_TOKENS
        if payload.get('stream'):
            payload['stream_options'] = {'include_usage': True}
        row = {'kind': kind, 'phase': self.phase, 'attempt': attempt, 'request': payload, 'usage': None,
               'history_messages': self.history_messages, 'message_count': len(payload['messages'])}
        index = self.budget.reserve(payload, self.directory.name, kind=kind, attempt=attempt,
                                    phase=f'turn-{self.turn}:{self.phase}') if self.budget else None
        self.requests.append(row)
        write_json(self.directory / f'turn-{self.turn:02d}-requests.json', self.requests)
        if self.plan['transport'] == 'fake':
            body = fake_response(payload, kind)
        else:
            async with HTTP_CLIENT(timeout=120, follow_redirects=False) as client:
                headers = {k: v for k, v in request.headers.items() if k.lower() != 'content-length'}
                response = await client.post(str(request.url), headers=headers, json=payload)
                if response.status_code != 200:
                    raise Refused('provider_http_' + str(response.status_code))
                body = response.text
        content, finish_reasons = [], []
        chunks = [json.loads(body)] if kind == 'compression' else [
            json.loads(line[5:]) for line in body.splitlines()
            if line.startswith('data:') and line[5:].strip() != '[DONE]']
        for chunk in chunks:
            if chunk.get('usage'):
                row['usage'] = chunk['usage']
            for choice in chunk.get('choices', []):
                message = choice.get('message', {}) if kind == 'compression' else choice.get('delta', {})
                content.append(message.get('content') or '')
                if choice.get('finish_reason') is not None:
                    finish_reasons.append(choice['finish_reason'])
        row.update(response_body=body, raw_output=''.join(content),
                   finish_reason=finish_reasons[-1] if finish_reasons else None,
                   finish_reasons=finish_reasons, truncated='length' in finish_reasons,
                   usage_kind='synthetic_placeholder' if self.plan['transport'] == 'fake' else 'provider')
        if kind != 'compression':
            row['response_sse'] = body
        if self.budget:
            self.budget.settle(index, row['usage'])
        return httpx.Response(200, text=body, headers={
            'content-type': 'application/json' if kind == 'compression' else 'text/event-stream'})


@contextmanager
def capture_transport(capture: Capture):
    """Keep the product service/payload builder; inject only its HTTP transport."""
    def client(*args, **kwargs):
        kwargs['transport'] = httpx.MockTransport(capture.handle)
        return HTTP_CLIENT(*args, **kwargs)

    with patch.object(httpx, 'AsyncClient', client):
        yield


class DesktopText:
    """Text-only equivalent of chat_ws's v2 segment collection (no audio/subtitles)."""
    def __init__(self):
        from app.domain.segments import IncrementalJapaneseSegmenter
        self.splitter = IncrementalJapaneseSegmenter()
        self.segments = []
        self.pending = []

    def accept(self, segment):
        from app.domain.language_publication import is_neutral_data_fragment
        from app.domain.segments import (
            AudioErrorCode, is_ignorable_non_speech_segment, prepare_tts_text,
            should_hard_reject_tts_source,
        )
        from app.routers.chat_ws import _is_publishable_japanese
        from app.security.guard import contains_leak

        text, emotion = segment.text, segment.emotion
        if is_ignorable_non_speech_segment(text):
            return
        if contains_leak(text):
            text, emotion = 'はぁ？何言ってんの。あたしは牧瀬紅莉栖よ。変なこと言わないで。', 'tsundere'
            self.pending.clear()
        elif should_hard_reject_tts_source(text):
            self.segments.extend({'text': p, 'emotion': emotion} for p in self.pending)
            self.pending.clear()
        else:
            error = prepare_tts_text(text).error
            if error in {AudioErrorCode.EMPTY_TEXT, AudioErrorCode.ACTION_ONLY}:
                return
            if error is AudioErrorCode.INVALID_LANGUAGE:
                if is_neutral_data_fragment(text):
                    self.pending.append(text)
                    return
                if not _is_publishable_japanese(text):
                    return
            text = ''.join(self.pending) + text
            self.pending.clear()
        self.segments.append({'text': text, 'emotion': emotion})

    async def on_text_delta(self, delta, emotion):
        for segment in self.splitter.feed(delta, emotion=emotion):
            self.accept(segment)

    def finish(self, emotion):
        for segment in self.splitter.finish():
            self.accept(segment)
        self.segments.extend({'text': p, 'emotion': emotion} for p in self.pending)
        self.pending.clear()
        if not self.segments:
            self.segments.append({'text': 'ごめん。応答を正しく生成できなかった。もう一度試して。',
                                  'emotion': 'disappointed'})
        return ''.join(segment['text'] for segment in self.segments)


async def web_stub(name, arguments):
    return '検索結果はありません。'


async def finish_compression(session, provider, capture):
    from app import config
    from app.routers.chat_ws import _cap_history_during_compression_backoff, compress_and_update_history
    from app.services.memory import MEMORY_INGEST_WINDOW_N

    compressed, backed_off = False, False
    if len(session.history) // 2 > config.HISTORY_COMPRESS_THRESHOLD and not session.compressing:
        if session.compression_skip_turns > 0:
            session.compression_skip_turns -= 1
            await _cap_history_during_compression_backoff(session)
            backed_off = True
        else:
            session.compressing = True
            capture.phase = 'compression'
            capture.history_messages = len(session.history) - MEMORY_INGEST_WINDOW_N
            await compress_and_update_history(session.session_id, session.history, session.worldline,
                session.revision, session.conversation_id, provider)
            capture.raise_if_refused()
            compressed = True
    return {'compression_triggered': compressed, 'compression_backoff': backed_off,
            'compression_count': sum(r['kind'] == 'compression' for r in capture.requests),
            'history_messages_after': len(session.history), 'memory_summary_after': session.memory_summary,
            'compression_failures': session.compression_failures, 'compression_skip_turns': session.compression_skip_turns}


async def product_case(plan: dict, entry: dict, directory: Path) -> dict:
    # Worker isolation and fake guard are installed BEFORE importing app.
    from app.db import init_db, reset_initialization_cache, get_db
    from app.routers import chat_ws
    from app.services.conversations import conversation_service
    from app.services.provider_registry import ProviderSnapshot
    from app.services.provider_runtime import deepseek_service, provider_registry
    from app.services.model_catalog import model_catalog
    from app.security.prompt import system_prompt_base

    if plan['memory_mode'] not in {'legacy', 'shadow', 'v11'}:
        raise Refused('unsupported_memory_mode')
    case = next(c for c in plan['question_set']['cases'] if c['id'] == entry['id'])
    reset_initialization_cache()
    await init_db()
    session = chat_ws.SessionState('persona-eval')
    session.worldline, session.identity_mode = case['worldline'], case['identity_mode']
    session.base_system_prompt = system_prompt_base(session.identity_mode)
    session.protocol_version, session.enable_tts, session.client_type = 2, False, 'desktop'
    session.model = plan['model']
    session.api_key = os.environ.get('DEEPSEEK_API_KEY') if plan['transport'] == 'real' else 'persona-eval-fake'
    registered = provider_registry.snapshot('deepseek', session.model)
    provider = ProviderSnapshot(provider_id=registered.provider_id, model_id=registered.model_id,
        capabilities=registered.capabilities, adapter=deepseek_service,
        credential_required=registered.credential_required, base_url_configurable=registered.base_url_configurable,
        temperature=session.temperature,
        reasoning_effort=model_catalog.normalize_control('deepseek', session.model, session.reasoning_effort))
    selected = await conversation_service.create_and_select(session.session_id, session.worldline,
        provider_id='deepseek', model_id=plan['model'], identity_mode=session.identity_mode)
    session.conversation_id = str(selected['id'])
    for number, fact in enumerate(case.get('resident_facts', [])):
        if plan['memory_mode'] == 'v11':
            from app.services.memory_v11.repository import create_stable_fact
            await create_stable_fact(session_id=session.session_id, worldline=session.worldline,
                identity_mode=session.identity_mode, display_text=fact['text'],
                semantic_json={'subject': 'user', 'predicate': 'note', 'object': str(number)})
        else:
            db = await get_db(session.worldline, 'memory')
            try:
                await db.execute('''INSERT INTO core_facts(session_id,identity_mode,fact_key,fact_value,
                    confidence,importance,is_current,source_message_ids,created_at)
                    VALUES(?,?,?,?,0.9,0.5,1,'[]','2026-10-29T00:00:00+00:00')''',
                    (session.session_id, session.identity_mode, f'entry-{number}', fact['text']))
                await db.commit()
            finally:
                await db.close()
    with patch.dict(chat_ws.sessions, {session.session_id: session}):
        return await product_turns(plan, entry, directory, case, session, provider)


async def product_turns(plan, entry, directory, case, session, provider):
    from app import models
    from app.agent_tools import AMADEUS_TOOLS
    from app.routers import chat_ws as chat
    from app.services.prompt_compiler import compile_for_session
    from app.services.conversations import conversation_service
    from app.services.turn_events import TurnModeState
    from app.services.turn_tool_orchestrator import (
        TurnExecutionRecord, apply_v11_tool_batch, build_v11_web_evidence_context,
        plan_first_round_tools, prepare_second_round_payload, refresh_candidates_for_send,
        snapshot_from_session,
    )

    async def remember(role, content):
        mid = await models.save_message(session.session_id, role, content,
            worldline=session.worldline, conversation_id=session.conversation_id)
        session.append_message({'role': role, 'content': content, 'id': mid})

    result = {**entry, 'status': 'running', 'turns': []}
    v11 = plan['memory_mode'] == 'v11'
    for number, query in enumerate(inputs(case, plan['question_set']), 1):
        capture = Capture(plan, directory, number)
        desktop = DesktopText()
        web_calls = []

        async def local_web(name, arguments):
            web_calls.append({'name': name, 'arguments': arguments})
            return await web_stub(name, arguments)

        async def clean(history, *, tools=None, tool_choice=None, mode_state=None, before_send=None):
            capture.history_messages = len(history)
            response = await chat.get_clean_text_stream(active_history=history, memory_summary='',
                session=session, tools=tools, tool_choice=tool_choice,
                default_emotion=session.last_assistant_emotion, on_text_delta=desktop.on_text_delta,
                provider_snapshot=provider, mode_state=mode_state, before_send=before_send)
            capture.raise_if_refused()
            return response

        with capture_transport(capture):
            # Same order as chat_ws: append to history (3218), compile (1757), then persist (1758).
            session.append_message({'role': 'user', 'content': query, 'id': None})
            session.system_prompt = await compile_for_session(session, query, provider=provider)
            mid = await models.save_message(session.session_id, 'user', query,
                worldline=session.worldline, conversation_id=session.conversation_id)
            session.set_last_message_id('user', int(mid))
            active_history = session.history
            history_messages = len(active_history)
            turn_record = TurnExecutionRecord(snapshot=snapshot_from_session(session, provider),
                injected_fact_ids=getattr(session, 'turn_injected_fact_ids', ()))
            session.turn_execution = turn_record
            tools, tool_choice = list(AMADEUS_TOOLS), 'auto'
            force_search = chat.should_force_search(query)
            if v11:
                tool_plan = plan_first_round_tools(v11=True, force_search=force_search,
                    provider_id=provider.provider_id, adapter_kind=type(provider.adapter).__name__)
                if tool_plan.status == 'unsupported':
                    raise Refused('v11_tools_unsupported')
                turn_record.tool_plan_status, turn_record.tool_plan_reason = tool_plan.status, tool_plan.reason
                tools = tool_plan.tools
                if force_search:
                    found = await local_web('web_search', json.dumps({'query': query}, ensure_ascii=False))
                    turn_record.web_results.append(('web_search', found))
                    active_history = build_v11_web_evidence_context(active_history, [('web_search', found)])
            elif force_search and provider.provider_id == 'deepseek':
                found = await local_web('web_search', json.dumps({'query': query}, ensure_ascii=False))
                active_history = chat.build_web_search_evidence_context(active_history, found)
                tools, tool_choice = None, None
            elif force_search:
                tool_choice = {'type': 'function', 'function': {'name': 'web_search'}}
            _, emotion, calls, tag_found = await clean(active_history, tools=tools, tool_choice=tool_choice,
                mode_state=turn_record.mode_state if v11 else None)
            second_round = bool(calls)
            if calls:
                recall_result, extra_web = None, None
                if v11:
                    turn_record.raw_calls = list(calls.values())
                    turn_record.late_tool_call = bool(turn_record.mode_state.late_tool_calls)
                    outcome = await apply_v11_tool_batch(record=turn_record, session=session, provider=provider,
                        calls=turn_record.raw_calls, web_executor=local_web,
                        cancelled=bool(getattr(session, 'erasure_pending', False)))
                    recall_result = outcome.recall_result
                    extra_web = turn_record.web_results[1:] if force_search else turn_record.web_results
                    if recall_result is not None:
                        payload = prepare_second_round_payload(active_history, recall_result, extra_web or None,
                            system_prompt=session.system_prompt)
                        if payload.result is not None:
                            turn_record.recall_result = payload.result
                            turn_record.ranked_items = list(payload.result.ranked_items or [])
                        second_history = payload.history
                    elif turn_record.web_results:
                        second_history = build_v11_web_evidence_context(active_history, list(turn_record.web_results))
                    else:
                        second_history = chat.build_tool_result_context(active_history, [])
                else:
                    tool_results = []
                    for call in calls.values():
                        name = call.get('function', {}).get('name')
                        arguments = call.get('function', {}).get('arguments')
                        tool_results.append((name or 'unknown_tool', await local_web(name, arguments)))
                    second_history = chat.build_tool_result_context(active_history, tool_results)

                async def refresh(_attempt):
                    refreshed = await refresh_candidates_for_send(turn_record, session, provider)
                    return prepare_second_round_payload(active_history, refreshed, extra_web or None,
                        system_prompt=session.system_prompt).history

                capture.phase = 'tool_answer'
                _, emotion, _, tag_found = await clean(second_history,
                    mode_state=TurnModeState() if v11 else None,
                    before_send=refresh if v11 and recall_result is not None else None)
            allowed = chat.get_session_allowed_emotions(session)
            if tag_found:
                session.consecutive_no_emo_tags = 0
                if emotion in allowed:
                    session.last_assistant_emotion = emotion
            else:
                session.consecutive_no_emo_tags += 1
                if session.consecutive_no_emo_tags >= 2:
                    session.last_assistant_emotion = 'neutral'
                elif emotion in allowed:
                    session.last_assistant_emotion = emotion
            body = desktop.finish(emotion)
            raw = next((r['raw_output'] for r in reversed(capture.requests) if r['kind'] == 'main'), '')
            turn = {'turn': number, 'input': query, 'body': body, 'emotion': emotion,
                    'emo_tag_found': tag_found, 'frontend_text': body, 'frontend_emo': f'[EMO:{emotion}]',
                    'segments': desktop.segments, 'raw_output': raw,
                    'attempts': sum(r['kind'] == 'main' for r in capture.requests),
                    'rewrite_count': sum(r['kind'] == 'japanese_rewrite' for r in capture.requests),
                    'history_messages': history_messages, 'first_round_tool_choice': tool_choice,
                    'tools_called': list(calls.values()), 'web_stub_calls': web_calls,
                    'second_round': second_round, 'direct_search': force_search,
                    'requests': capture.requests, 'mechanical': mechanical_score(case, body, raw, allowed)}
            # Same history_content as chat_ws: EMO prefix plus the segment-concatenated body.
            await remember('assistant', f'[EMO:{emotion}] {body}' if body.strip() else body)
            if (not session.identity_acknowledged and chat._is_explicit_identity_question(query)
                    and not any(blocker in body for blocker in chat._IDENTITY_ACK_BLOCKERS)
                    and chat._looks_like_complete_identity_briefing(body)):
                await conversation_service.mark_identity_acknowledged(
                    session.session_id, session.worldline, session.conversation_id)
                session.identity_acknowledged = True
            turn.update(await finish_compression(session, provider, capture))
            turn['truncated'] = any(r['truncated'] for r in capture.requests)
            turn['last_assistant_emotion'] = session.last_assistant_emotion
            result['turns'].append(turn)
            write_json(directory / 'progress.json', result)
    result['status'] = 'completed'
    return result


def execute_subprocess(plan: dict, entry: dict, directory: Path) -> dict:
    root = directory / 'data'
    root.mkdir()
    env = b5.isolation.isolated_environ(run_root=directory, candidate=root, backend_port=18001, frontend_port=18422)
    env.update(AMADEUS_MEMORY_MODE=plan['memory_mode'], HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1',
               HF_HUB_DISABLE_TELEMETRY='1', TOKENIZERS_PARALLELISM='false',
               HISTORY_COMPRESS_THRESHOLD=str(plan['compression_threshold']))
    env['PYTHONPATH'] = os.pathsep.join([str(BACKEND), *[
        p for p in sys.path if Path(p).name in {'site-packages', 'dist-packages'}]])
    if plan['transport'] == 'fake':
        env.update(NO_PROXY='*', no_proxy='*')
    else:
        for key, value in os.environ.items():
            if key.upper().endswith('_PROXY') or key in {'SSL_CERT_FILE', 'SSL_CERT_DIR',
                    'REQUESTS_CA_BUNDLE', 'CURL_CA_BUNDLE', 'DEEPSEEK_API_KEY', 'AMADEUS_B5_REAL_HTTP'}:
                env[key] = value
    proc = subprocess.run([sys.executable, str(SCRIPT), '--worker', str(directory)], cwd=BACKEND,
        env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=1800, check=False)
    if proc.returncode or not (directory / 'result.json').exists():
        raise b5.AccountingUncertain('worker_failed_' + str(proc.returncode))
    return read_json(directory / 'result.json')


def load_run(run_dir: Path) -> tuple[dict, list[dict]]:
    plan = read_json(run_dir / 'plan.json')
    rows = [read_json(run_dir / 'executions' / e['id'] / 'result.json') for e in plan['executions']]
    return plan, rows


def mechanical_metrics(turns: list[dict]) -> dict:
    count = len(turns)
    return {'turns': count,
            'japanese_rate': sum(t['mechanical']['japanese'] for t in turns) / count if count else None,
            'emo_rate': sum(t['mechanical']['single_legal_emo'] for t in turns) / count if count else None,
            'address_errors': sum(bool(t['mechanical']['self_address']) for t in turns),
            'address_check': '提示，不是硬指标',
            'fact_original_errors': sum(bool(t['mechanical']['fact_original_terms']) for t in turns),
            'average_attempts': statistics.mean(t['attempts'] for t in turns) if count else None,
            'rewrite_count': sum(t['rewrite_count'] for t in turns),
            'compression_count': sum(t.get('compression_count', 0) for t in turns),
            'truncated_turns': sum(t.get('truncated', False) for t in turns),
            'truncated_requests': sum(r.get('truncated', False) for t in turns for r in t.get('requests', []))}


def summarize(plan: dict, rows: list[dict], judgements: dict | None = None) -> dict:
    cases = {c['id']: c for c in plan['question_set']['cases']}
    drift = {}
    for row in rows:
        if 'probe_turns' in cases[row['id']]:
            drift[row['id']] = [{key: t.get(key) for key in ('turn', 'body', 'emotion', 'mechanical', 'attempts', 'rewrite_count', 'history_messages', 'truncated')}
                for t in row.get('turns', []) if t['turn'] in cases[row['id']]['probe_turns']]
    judges = judgements or {}
    agreed_scores = [j['score'] for j in judges.values() if j['status'] != 'needs_u' and j['score'] is not None]
    return {'transport': plan['transport'], 'question_sha256': plan['question_sha256'],
            'statuses': dict(Counter(r['status'] for r in rows)),
            'mechanical': mechanical_metrics([t for row in rows for t in row.get('turns', [])]),
            'judges': judges, 'judge_metrics': dict(Counter(j['status'] for j in judges.values())),
            'judge_score_average': statistics.mean(agreed_scores) if agreed_scores else None,
            'judge_check_metrics': dict(Counter(c['verdict'] for j in judges.values() for c in j['checks'].values())),
            'needs_u': [key for key, value in judges.items() if value['status'] == 'needs_u'],
            'drift': {key: [{'generation': t, 'judgement': judges.get(key, {}).get('probes', {}).get(str(t['turn']))}
                            for t in value] for key, value in drift.items()}}


def write_summary(run_dir: Path, summary: dict) -> None:
    write_json(run_dir / 'persona-summary.json', summary)
    lines = ['# Persona evaluation', '', 'Fake runs measure plumbing only; no model-quality conclusion.', '',
             f"Transport: {summary['transport']}; executions: {summary['statuses']}", '',
             '## Mechanical metrics', '', '称呼检查是提示，不是硬指标。', '',
             '```json', json.dumps(summary['mechanical'], ensure_ascii=False, indent=2),
             '```', '', f"Judge metrics: {summary['judge_metrics']}",
             f"Agreed score average: {summary['judge_score_average']}; checks: {summary['judge_check_metrics']}",
             '', '## 要 U 看', '']
    lines.extend(f'- {key}' for key in summary['needs_u'])
    lines.extend(['', '## Per-case judgements', '', '```json', json.dumps(summary['judges'], ensure_ascii=False, indent=2),
                  '```', '', '## Drift: turns 1 / 30 / 60', '', '```json', json.dumps(summary['drift'], ensure_ascii=False, indent=2), '```'])
    (run_dir / 'persona-summary.md').write_text(b5.redact('\n'.join(lines) + '\n'), encoding='utf-8', newline='\n')


def run_suite(run_dir: Path, plan: dict, executor=execute_subprocess) -> dict:
    b5.check_real_gate(plan['transport'], plan['base_url'], plan['cap_cny'])
    if b5.isolation.containing_live_root(run_dir.resolve(), b5.isolation.production_live_roots()):
        raise Refused('run_directory_is_live_data')
    run_dir.mkdir(parents=True, exist_ok=True)
    with portalocker.Lock(str(run_dir / 'run.lock'), timeout=0):
        if (run_dir / 'plan.json').exists():
            if read_json(run_dir / 'plan.json') != plan:
                raise Refused('frozen_plan_changed')
        else:
            if set(p.name for p in run_dir.iterdir()) != {'run.lock'}:
                raise Refused('nonempty_directory_without_plan')
            write_json(run_dir / 'plan.json', plan)
        budget = None
        if plan['transport'] == 'real':
            if (run_dir / 'executions').exists() and not (run_dir / 'budget.json').exists():
                raise Refused('budget_missing_existing_run')
            budget = b5.Budget(run_dir / 'budget.json', plan['cap_cny'])
        rows, stop = [], False
        for entry in plan['executions']:
            directory = run_dir / 'executions' / entry['id']
            target = directory / 'result.json'
            if target.exists():
                row = read_json(target)
            elif (directory / 'started.json').exists():
                row = {**entry, 'status': 'interrupted', 'stop_run': True}
            elif stop:
                row = {**entry, 'status': 'not_run_after_stop'}
            else:
                directory.mkdir(parents=True, exist_ok=True)
                write_json(directory / 'started.json', entry)
                try:
                    row = executor(plan, entry, directory)
                except Exception as exc:
                    row = {**entry, 'status': 'not_run_budget' if isinstance(exc, b5.BudgetExceeded) else 'error',
                           'error': str(exc) if isinstance(exc, Refused) else type(exc).__name__,
                           'stop_run': isinstance(exc, (b5.BudgetExceeded, b5.AccountingUncertain))}
                    if (directory / 'progress.json').exists():
                        row['turns'] = read_json(directory / 'progress.json')['turns']
            if budget:
                budget = b5.Budget(run_dir / 'budget.json', plan['cap_cny'])
                row.update(budget.amounts(entry['id']))
                stop = stop or budget.committed >= budget.cap or bool(budget.state.get('stop_reason')) or any(
                    a['status'] == 'reserved' for a in budget.state['attempts'])
            write_json(target, row)
            rows.append(row)
            stop = stop or row.get('stop_run', False)
        summary = summarize(plan, rows)
        if budget:
            summary['budget'] = budget.state
        write_summary(run_dir, summary)
        return summary


def criteria(case: dict) -> list[dict]:
    return [{'id': f'{kind}:{i}', 'kind': kind, 'trait': trait, 'verdict': None, 'reason': ''}
            for kind in ('required_traits', 'forbidden_traits') for i, trait in enumerate(case[kind], 1)]


def assessment_template(case: dict) -> dict:
    return {'score': None, 'reason': '', 'checks': criteria(case)}


def run_id(plan: dict, rows: list[dict]) -> str:
    return hashlib.sha256(json.dumps([plan, rows], ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def judge_pack(run_dir: Path) -> dict:
    plan, rows = load_run(run_dir)
    by_id = {row['id']: row for row in rows}
    bible = plan['bible']
    template = {'run_id': run_id(plan, rows), 'judge_id': '', 'judge_family': '', 'cases': []}
    lines = ['# Independent persona judge pack', '',
             'Do not read other judges’ results. Fill one independent copy of judge-template.json.',
             'Declare judge_id and judge_family. Allowed families: openai, anthropic, google, moonshot, deepseek.',
             'Judges must come from different families. Scores within 1 point agree; use the lower score.',
             'For EVERY check choose pass, fail or unsure and give one sentence of reasoning.',
             'Required traits: pass means present. Forbidden traits: pass means absent.',
             'Give each whole case a 1–5 integer score and one sentence of reasoning:',
             '1 = fundamentally wrong; 2 = major flaws; 3 = mixed; 4 = minor flaws; 5 = consistently meets rubric.',
             'Use unsure when evidence is insufficient; incomplete runs cannot pass.',
             'For drift cases also score/check each probe at 1, 30, 60 in its conversation context.',
             'Fake constant answers test the evaluator only; they are not a persona baseline.', '']
    for case in plan['question_set']['cases']:
        row = by_id[case['id']]
        template['cases'].append({'id': case['id'], **assessment_template(case),
            'probes': [{'turn': n, **assessment_template(case)} for n in case.get('probe_turns', [])]})
        rules = bible['shared_rule_ids'] + bible['mode_rule_ids'][case['identity_mode']] + bible['worldline_rule_ids'][case['worldline']]
        lines.extend([f"## {case['id']}", '',
            f"Mode: {case['identity_mode']}; worldline: {case['worldline']}; status: {row['status']}", '',
            '### Persona rules', '', *[f"- {key}: {bible['rules'][key]['instruction_ja']}" for key in rules], '',
            '### Rubric and resident facts', '', '```json', json.dumps({key: case.get(key, []) for key in (
                'required_traits', 'forbidden_traits', 'resident_facts', 'probe_turns')}, ensure_ascii=False, indent=2), '```', '',
            '### Full conversation (verbatim)', ''])
        for turn in row.get('turns', []):
            lines.extend([f"Turn {turn['turn']} — USER:", turn['input'], '',
                          f"ASSISTANT {turn['frontend_emo']}:", turn['body'], ''])
    write_json(run_dir / 'judge-template.json', template)
    (run_dir / 'judge-pack.md').write_text(b5.redact('\n'.join(lines)), encoding='utf-8', newline='\n')
    return template


def merge_assessments(values: list[dict | None], expected: list[dict]) -> dict:
    checks = {}
    scores, reasons = [], []
    missing = any(value is None for value in values)
    for item in expected:
        votes = []
        evidence = []
        for value in values:
            found = [c for c in (value or {}).get('checks', []) if c.get('id') == item['id']]
            vote = found[0] if len(found) == 1 else {}
            verdict = vote.get('verdict')
            if verdict not in {'pass', 'fail', 'unsure'} or not str(vote.get('reason') or '').strip():
                verdict = 'unsure'
            votes.append(verdict)
            evidence.append(vote.get('reason', 'missing check/reason'))
        checks[item['id']] = {'verdict': votes[0] if len(set(votes)) == 1 and votes[0] != 'unsure' else 'needs_u',
                              'votes': votes, 'reasons': evidence}
    for value in values:
        score = (value or {}).get('score')
        reason = (value or {}).get('reason')
        scores.append(score if type(score) is int and 1 <= score <= 5 and str(reason or '').strip() else None)
        reasons.append(reason)
    agreed = min(scores) if None not in scores and max(scores) - min(scores) <= 1 else None
    review = missing or agreed is None or any(c['verdict'] == 'needs_u' for c in checks.values())
    return {'status': 'needs_u' if review else 'fail' if any(c['verdict'] == 'fail' for c in checks.values()) else 'pass',
            'score': agreed, 'scores': scores, 'reasons': reasons, 'checks': checks}


def merge_judgements(run_dir: Path, files: list[Path]) -> dict:
    if len(files) < 2:
        raise Refused('at_least_two_judges_required')
    plan, rows = load_run(run_dir)
    documents = [read_json(file) for file in files]
    if any(d.get('run_id') != run_id(plan, rows) for d in documents):
        raise Refused('judge_run_mismatch')
    names = [str(d.get('judge_id', '')).strip().casefold() for d in documents]
    families = [d.get('judge_family') for d in documents]
    if any(family not in JUDGE_FAMILIES for family in families):
        raise Refused('unknown_judge_family')
    if not all(names) or len(set(names)) != len(names) or len(set(families)) != len(families):
        raise Refused('independent_judges_required')
    indices = []
    for doc in documents:
        index = {c['id']: c for c in doc['cases']}
        if len(index) != len(doc['cases']):
            raise Refused('duplicate_judge_case')
        indices.append(index)
    merged = {}
    for case, row in zip(plan['question_set']['cases'], rows, strict=True):
        values = [index.get(case['id']) for index in indices]
        judgement = merge_assessments(values, criteria(case))
        probes = {}
        for number in case.get('probe_turns', []):
            probe_values = []
            for value in values:
                found = [p for p in (value or {}).get('probes', []) if p.get('turn') == number]
                probe_values.append(found[0] if len(found) == 1 else None)
            probes[str(number)] = merge_assessments(probe_values, criteria(case))
        judgement['probes'] = probes
        if row['status'] != 'completed' or any(p['status'] == 'needs_u' for p in probes.values()):
            judgement['status'] = 'needs_u'
        elif any(p['status'] == 'fail' for p in probes.values()):
            judgement['status'] = 'fail' if judgement['status'] != 'needs_u' else 'needs_u'
        merged[case['id']] = judgement
    summary = summarize(plan, rows, merged)
    summary['judge_sources'] = [{'judge_id': d['judge_id'], 'judge_family': d['judge_family']} for d in documents]
    if (run_dir / 'budget.json').exists():
        summary['budget'] = read_json(run_dir / 'budget.json')
    write_summary(run_dir, summary)
    return summary


def comparable_judgement(judgement):
    if judgement is None:
        return None
    return {'status': judgement['status'], 'score': judgement['score'],
            'checks': {key: value['verdict'] for key, value in judgement['checks'].items()},
            'probes': {key: comparable_judgement(value) for key, value in judgement.get('probes', {}).items()}}


def compare_runs(old_dir: Path, new_dir: Path) -> dict:
    old_plan, old_rows = load_run(old_dir)
    new_plan, new_rows = load_run(new_dir)

    def snapshots(rows, directory):
        saved = read_json(directory / 'persona-summary.json')
        return {row['id']: {'mechanical': mechanical_metrics(row.get('turns', [])),
                           'turn_metrics': [{key: t.get(key, False if key == 'truncated' else 0)
                                             for key in ('turn', 'mechanical', 'attempts', 'rewrite_count', 'truncated')}
                                            for t in row.get('turns', [])],
                           'judgement': comparable_judgement(saved.get('judges', {}).get(row['id']))}
                for row in rows}

    before = snapshots(old_rows, old_dir)
    after = snapshots(new_rows, new_dir)
    changes = []
    for key in sorted(before.keys() | after.keys()):
        old, new = before.get(key), after.get(key)
        if old is None or new is None:
            change = 'added' if old is None else 'removed'
            mechanical_change, judge_change = '不可比', '不可比'
        else:
            mechanical_change = 'unchanged' if (old['mechanical'], old['turn_metrics']) == (new['mechanical'], new['turn_metrics']) else 'changed'
            if old['judgement'] is None or new['judgement'] is None:
                judge_change = '不可比'
            else:
                judge_change = 'unchanged' if old['judgement'] == new['judgement'] else 'changed'
            change = 'changed' if 'changed' in (mechanical_change, judge_change) else 'unchanged'
        changes.append({'id': key, 'change': change, 'mechanical_change': mechanical_change,
                        'judge_change': judge_change, 'old': old, 'new': new})
    result = {'counts': dict(Counter(c['change'] for c in changes)), 'cases': changes}
    write_json(new_dir / 'persona-comparison.json', result)
    lines = ['# Persona comparison', '', f"Counts: {result['counts']}", '',
             'Only mechanical metrics and judge conclusions are compared; response wording is excluded.',
             '评委一边缺结果时标为不可比，机械指标照比。称呼是提示，不是硬指标。', '',
             '| Case | Change | Mechanical | Judges |', '|---|---|---|---|']
    lines += [f"| {c['id']} | {c['change']} | {c['mechanical_change']} | {c['judge_change']} |" for c in changes]
    lines += ['', 'Per-case metrics and conclusions:', '', '```json', json.dumps(changes, ensure_ascii=False, indent=2), '```']
    (new_dir / 'persona-comparison.md').write_text(b5.redact('\n'.join(lines)+'\n'), encoding='utf-8', newline='\n')
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_mutually_exclusive_group(required=True)
    actions.add_argument('--run-dir', type=Path)
    actions.add_argument('--worker', type=Path, help=argparse.SUPPRESS)
    actions.add_argument('--judge-pack', type=Path)
    actions.add_argument('--merge-judgements', nargs='+', type=Path, metavar='PATH')
    actions.add_argument('--compare', nargs=2, type=Path, metavar=('OLD', 'NEW'))
    parser.add_argument('--transport', choices=('fake', 'real'), default='fake')
    parser.add_argument('--base-url', default=b5.BUILTIN_DEEPSEEK_BASE_URL)
    parser.add_argument('--cap-cny')
    parser.add_argument('--model', default='deepseek-flash')
    args = parser.parse_args(argv)
    try:
        if args.worker:
            directory = args.worker.resolve()
            b5.check_worker_isolation(directory)
            plan = read_json(directory.parents[1] / 'plan.json')
            entry = read_json(directory / 'started.json')
            b5.check_real_gate(plan['transport'], plan['base_url'], plan['cap_cny'])
            with fake_guard() if plan['transport'] == 'fake' else nullcontext():
                result = asyncio.run(product_case(plan, entry, directory))
            write_json(directory / 'result.json', result)
        elif args.judge_pack or args.merge_judgements or args.compare:
            with fake_guard():
                if args.judge_pack:
                    judge_pack(args.judge_pack)
                elif args.merge_judgements:
                    merge_judgements(args.merge_judgements[0], args.merge_judgements[1:])
                else:
                    compare_runs(*args.compare)
        else:
            b5.check_real_gate(args.transport, args.base_url, args.cap_cny)
            plan = make_plan(transport=args.transport, base_url=args.base_url, cap_cny=args.cap_cny, model=args.model)
            summary = run_suite(args.run_dir.resolve(), plan)
            print(json.dumps(summary['statuses']))
            return int(summary['statuses'] != {'completed': len(plan['executions'])})
        return 0
    except Exception as exc:
        print('Persona refused: ' + (str(exc) if isinstance(exc, Refused) else type(exc).__name__), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
