import re
import base64
import asyncio
from uuid import uuid4
import starlette
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from app import config
from app.services.provider_registry import ProviderSnapshot, ProviderTask
from app.services.provider_runtime import deepseek_service, provider_registry
from app.services.credentials import credential_store
from app.services.tts_queue import tts_manager
from app import models
from app.db import init_db
from app.services.prompt_compiler import compile_for_session
from app.services.session_manager import cancel_inflight, session_coordinator
from app.domain.segments import (
    IncrementalJapaneseSegmenter,
    Segment,
    SegmentPipeline,
    TransientSegmentError,
    TurnIdentity,
)

import copy
from app.services.tts_queue import TTSServiceError
from contextlib import aclosing
from app.security.guard import contains_leak

router = APIRouter()


class SessionState:
    def __init__(self, session_id: str):
        self.session_id = session_id
        self.conversation_id = None
        self._history = []
        self.memory_summary = ""
        self.lock = asyncio.Lock()
        self.compressing = False
        
        self.temperature = 0.8
        self.provider_id = "deepseek"
        self.model = config.DEEPSEEK_MODEL
        self.reasoning_effort = None
        self.system_prompt = None
        self.base_system_prompt = None
        self.api_key = None
        self.sovits_url = None
        self.enable_tts = True
        self.worldline = "steins_gate"
        self.client_type = "mobile"
        self.protocol_version = 1
        
        self.active_task = None
        self.is_busy = False
        
        self.current_epoch = 0
        self.revision = 0
        self.active_turn_id = None
        self.voice_stopped_turn_id = None
        self.active_streams = set()
        self.active_segment_pipeline = None
        self.history_epoch = 0
        self.last_assistant_emotion = "neutral"
        self.consecutive_no_emo_tags = 0

    async def load_from_db(self):
        await init_db()
        from app.services.conversations import conversation_service

        selected = await conversation_service.get_selected(self.session_id, self.worldline)
        self.conversation_id = str(selected["id"])
        self.provider_id = str(selected.get("provider_id") or "deepseek")
        self.model = str(selected.get("model_id") or self.model)
        self.api_key = credential_store.get(self.provider_id)
        self._history = await models.get_recent_messages(
            self.session_id,
            limit=6,
            worldline=self.worldline,
            conversation_id=self.conversation_id,
        )
        self.memory_summary = await models.get_latest_summary(
            self.session_id,
            worldline=self.worldline,
            conversation_id=self.conversation_id,
        )
        from app.services.soul_engine import soul_engine
        await soul_engine.restore_emotion(self.session_id, self.worldline)

    @property
    def history(self):
        return copy.deepcopy(self._history)

    @history.setter
    def history(self, value):
        self._history = copy.deepcopy(value)

    def append_message(self, message: dict):
        self._history.append(copy.deepcopy(message))

    @property
    def tts_key(self) -> str:
        return f"{self.session_id}:{self.worldline}:{self.conversation_id or 'default'}:{self.revision}"

# In-memory session store: { session_id: SessionState }
sessions = {}
sessions_creation_lock = asyncio.Lock()

# Regex patterns
EMO_TAG_PATTERN = re.compile(r'^\[(?:E?MO)[:=]([a-zA-Z0-9_]+)\]\s*')
MOBILE_ALLOWED_EMOTIONS = {"neutral", "tsundere", "embarrassed", "intellectual"}
DESKTOP_ALLOWED_EMOTIONS = {
    "neutral", "tsundere", "embarrassed", "intellectual",
    "happy", "surprised", "annoyed", "disappointed", "sad"
}
ALLOWED_EMOTIONS = DESKTOP_ALLOWED_EMOTIONS

def get_session_allowed_emotions(session: "SessionState") -> set:
    if session.client_type in ("desktop", "voice"):
        return DESKTOP_ALLOWED_EMOTIONS
    return MOBILE_ALLOWED_EMOTIONS

def is_meaningful_sentence(text: str) -> bool:
    """Filter out empty sentences containing only punctuation and whitespace."""
    clean = re.sub(r'[\s。！\uff01？\uff1f!?,.\uff0c\u3001\"\'`「」『』()（）\-—_+=\[\]{}<>#~*]', '', text)
    return len(clean) > 0

def extract_sentences_from_buffer(buffer: str, is_end: bool = False):
    """
    Split the text buffer into sentences by Japanese punctuation or newlines, 
    preserving the delimiters.
    """
    sentences = []
    delimiters = {'。', '！', '？', '\n'}
    start = 0
    for i, char in enumerate(buffer):
        if char in delimiters:
            sentences.append(buffer[start:i+1])
            start = i + 1
    
    remaining = buffer[start:]
    if is_end and remaining.strip():
        sentences.append(remaining)
        remaining = ""
        
    return sentences, remaining


def _supported_kwargs(callable_obj, values: dict) -> dict:
    import inspect

    signature = inspect.signature(callable_obj)
    if any(
        parameter.kind == inspect.Parameter.VAR_KEYWORD
        for parameter in signature.parameters.values()
    ):
        return values
    return {name: value for name, value in values.items() if name in signature.parameters}


async def translate_with_provider(
    provider_snapshot: ProviderSnapshot,
    text: str,
    api_key: str | None,
) -> str:
    provider_snapshot.require(ProviderTask.TRANSLATION)
    method = provider_snapshot.adapter.translate_to_zh
    kwargs = _supported_kwargs(
        method,
        {
            "text": text,
            "api_key": api_key,
            "model": provider_snapshot.model_id,
        },
    )
    return await method(**kwargs)



async def compress_and_update_history(
    session_id: str,
    current_history: list,
    worldline: str = "steins_gate",
    revision: int = 0,
    conversation_id: str | None = None,
    provider_snapshot: ProviderSnapshot | None = None,
):
    """
    Asynchronously compress older conversation history and update session state safely.
    Keeps the last 3 rounds (6 messages) of history.
    Preserves any new messages added during the compression network call.
    """
    keep_count = 6
    if len(current_history) <= keep_count:
        session_data = sessions.get(session_id)
        if session_data:
            async with session_data.lock:
                session_data.compressing = False
        return
        
    to_compress = current_history[:-keep_count]
    N = len(to_compress)
    
    print(f"[History Compression] Session {session_id}: Compressing {N} messages...")
    new_summary = None
    try:
        session_data = sessions.get(session_id)
        if session_data:
            if provider_snapshot is None:
                registered = provider_registry.snapshot(
                    session_data.provider_id,
                    session_data.model,
                )
                provider_snapshot = ProviderSnapshot(
                    registered.provider_id,
                    registered.model_id,
                    registered.capabilities,
                    deepseek_service if registered.provider_id == "deepseek" else registered.adapter,
                )
            provider_snapshot.require(ProviderTask.COMPRESSION)
            method = provider_snapshot.adapter.compress_history
            kwargs = _supported_kwargs(
                method,
                {
                    "history": to_compress,
                    "api_key": session_data.api_key,
                    "old_summary": session_data.memory_summary,
                    "model": provider_snapshot.model_id,
                },
            )
            new_summary = await method(**kwargs)
            if new_summary:
                await models.save_memory_summary(
                    session_id,
                    new_summary,
                    worldline=worldline,
                    conversation_id=conversation_id,
                )
                async with session_data.lock:
                    if (
                        session_data.worldline != worldline
                        or session_data.revision != revision
                        or getattr(session_data, "conversation_id", None) != conversation_id
                    ):
                        return
                    session_data.memory_summary = new_summary
                    if len(session_data.history) >= N:
                        session_data.history = session_data.history[N:]
                    else:
                        session_data.history = []
                    print(
                        f"[History Compression] Session {session_id}: "
                        f"compression successful ({len(new_summary)} chars)"
                    )
    except Exception as e:
        print(f"[History Compression Error] Session {session_id}: {e}")
    finally:
        session_data = sessions.get(session_id)
        if session_data:
            async with session_data.lock:
                session_data.compressing = False

def clean_text_for_tts(text: str) -> str:
    """
    Remove action descriptions in brackets, e.g., (ため息をつく), （ため息をつく）, [ため息], etc.
    """
    cleaned = re.sub(r'\(.*?\)', '', text)
    cleaned = re.sub(r'（.*?）', '', cleaned)
    cleaned = re.sub(r'\[.*?\]', '', cleaned)
    cleaned = re.sub(r'\*.*?\*', '', cleaned)
    return cleaned.strip()

async def execute_tool_call(name: str, arguments_str: str) -> str:
    import json
    from app.services.search import search_service
    if name == "web_search":
        try:
            args = json.loads(arguments_str)
            query = args.get("query", "")
        except Exception:
            query = arguments_str
        return (await search_service.search(query)).render()
    else:
        return f"Error: Tool {name} not found."

async def get_clean_text_stream(
    active_history: list,
    memory_summary: str,
    session: SessionState,
    tools: list = None,
    tool_choice: str = None,
    default_emotion: str = "neutral",
    on_text_delta=None,
    provider_snapshot: ProviderSnapshot | None = None,
):
    """
    Get text or tool calls stream with leak retries.
    Returns: (final_text, final_emotion, merged_tool_calls, emo_tag_found)
    """
    MAX_ATTEMPTS = 3
    for attempt in range(MAX_ATTEMPTS):
        emo_buffer = ""
        in_emo_tag = False
        emo_tag_found = False
        allowed = get_session_allowed_emotions(session)
        last_emotion = default_emotion if default_emotion in allowed else "neutral"
        text_buffer = ""
        merged_tool_calls = {}
        
        eff_system_prompt = session.system_prompt
        if attempt > 0:
            eff_system_prompt = (session.system_prompt or "") + (
                "\n【直前の出力でAI・アシスタント・言語モデル等の露見語が検出された。"
                "絶対に紅莉栖としてのみ振る舞い、二度とそのような語を使わないこと。】"
            )
            
        from datetime import datetime
        time_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        time_supplement = f"\n【現在時刻：{time_str}】"
        eff_system_prompt = (eff_system_prompt or "") + time_supplement

        import inspect
        stream_kwargs = {
            "active_history": active_history,
            "memory_summary": memory_summary,
            "api_key": session.api_key,
            "system_prompt": eff_system_prompt,
            "temperature": (
                provider_snapshot.temperature
                if provider_snapshot is not None and provider_snapshot.temperature is not None
                else session.temperature
            ),
            "tools": tools,
            "tool_choice": tool_choice,
            "model": session.model,
            "reasoning_effort": (
                provider_snapshot.reasoning_effort
                if provider_snapshot is not None and provider_snapshot.reasoning_effort is not None
                else session.reasoning_effort
            ),
        }
        provider = provider_snapshot.adapter if provider_snapshot is not None else deepseek_service
        stream_kwargs["model"] = provider_snapshot.model_id if provider_snapshot is not None else session.model
        signature = inspect.signature(provider.get_chat_stream)
        accepts_kwargs = any(parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in signature.parameters.values())
        if not accepts_kwargs:
            stream_kwargs = {key: value for key, value in stream_kwargs.items() if key in signature.parameters}
        stream_gen = provider.get_chat_stream(**stream_kwargs)
        session.active_streams.add(stream_gen)
        
        try:
            async with aclosing(stream_gen) as stream:
                async for chunk in stream:
                    content = chunk.get("content")
                    tool_calls = chunk.get("tool_calls")
                    
                    if tool_calls:
                        for tc in tool_calls:
                            idx = tc.get("index", 0)
                            if idx not in merged_tool_calls:
                                merged_tool_calls[idx] = {
                                    "id": tc.get("id"),
                                    "type": tc.get("type"),
                                    "function": {
                                        "name": tc.get("function", {}).get("name"),
                                        "arguments": tc.get("function", {}).get("arguments", "")
                                    }
                                }
                            else:
                                target = merged_tool_calls[idx]
                                if tc.get("id"):
                                    target["id"] = tc.get("id")
                                if tc.get("type"):
                                    target["type"] = tc.get("type")
                                if tc.get("function", {}).get("name"):
                                    target["function"]["name"] = tc.get("function", {}).get("name")
                                if tc.get("function", {}).get("arguments"):
                                    target["function"]["arguments"] += tc.get("function", {}).get("arguments")
                                    
                    if content:
                        delta_text = ""
                        for char in content:
                            if in_emo_tag:
                                emo_buffer += char
                                if char == ']':
                                    match = EMO_TAG_PATTERN.match(emo_buffer)
                                    if match:
                                        emotion = match.group(1)
                                        allowed_emotions_for_log = allowed
                                        last_emotion = emotion if emotion in allowed else "neutral"
                                        print(f"[DEBUG] EMO tag found: '{emotion}' → last_emotion='{last_emotion}' (allowed: {len(allowed_emotions_for_log)} emotions)", flush=True)
                                        emo_tag_found = True
                                    else:
                                        text_buffer += emo_buffer
                                        delta_text += emo_buffer
                                    emo_buffer = ""
                                    in_emo_tag = False
                                elif len(emo_buffer) > 20:
                                    text_buffer += emo_buffer
                                    delta_text += emo_buffer
                                    emo_buffer = ""
                                    in_emo_tag = False
                            else:
                                if char == '[' and not text_buffer.strip():
                                    in_emo_tag = True
                                    emo_buffer = "["
                                else:
                                    text_buffer += char
                                    delta_text += char
                        if delta_text and on_text_delta is not None:
                            callback_result = on_text_delta(delta_text, last_emotion)
                            if asyncio.iscoroutine(callback_result):
                                await callback_result
            if emo_buffer:
                text_buffer += emo_buffer
                if on_text_delta is not None:
                    callback_result = on_text_delta(emo_buffer, last_emotion)
                    if asyncio.iscoroutine(callback_result):
                        await callback_result
        except asyncio.CancelledError:
            raise
        finally:
            session.active_streams.discard(stream_gen)
            
        if merged_tool_calls:
            return "", "neutral", merged_tool_calls, False
            
        if contains_leak(text_buffer):
            print(
                f"[Guard] attempt {attempt + 1}/{MAX_ATTEMPTS} "
                f"rejected leaked output ({len(text_buffer)} chars)"
            )
            if on_text_delta is not None:
                return text_buffer, last_emotion, {}, emo_tag_found
            if attempt < MAX_ATTEMPTS - 1:
                continue
            return "はぁ？何言ってんの。あたしは牧瀬紅莉栖よ。変なこと言わないで。", "tsundere", {}, False
        print(f"[DEBUG] get_clean_text_stream returning: emotion='{last_emotion}' emo_tag_found={emo_tag_found} text_len={len(text_buffer)}", flush=True)
        return text_buffer, last_emotion, merged_tool_calls, emo_tag_found

def should_force_search(user_msg: str) -> bool:
    import re
    patterns = [
        # Chinese
        r"天气", r"气温", r"温度", r"降水", r"下雨", r"下雪", r"台风", r"今天.*怎么样",
        r"新闻", r"资讯", r"实时", r"最新", r"search", r"查一下", r"查询", r"查查",
        # Japanese
        r"天気", r"気温", r"降水", r"天候", r"ニュース", r"調べる", r"検索",
        # English
        r"\bweather\b", r"\bforecast\b", r"\btemperature\b", r"\bnews\b",
        r"\bsearch\b", r"\blook up\b", r"\bcurrent events\b",
    ]
    return any(re.search(p, user_msg, re.IGNORECASE) for p in patterns)

async def processor_loop(
    session: SessionState, 
    user_msg: str, 
    send_queue: asyncio.Queue, 
    epoch: int, 
    start_history_epoch: int
):
    """
    Processor Coroutine (In-flight Task):
    Executes deepseek chat streaming, sentence extraction, launches TTS tasks, and queues results.
    """
    session.is_busy = True
    turn_id = session.active_turn_id or str(uuid4())
    session.active_turn_id = turn_id

    if session.conversation_id is None:
        from app.services.conversations import conversation_service

        selected = await conversation_service.get_selected(session.session_id, session.worldline)
        session.conversation_id = str(selected["id"])
    conversation_id = session.conversation_id
    registered_provider = provider_registry.snapshot(session.provider_id, session.model)
    turn_provider = ProviderSnapshot(
        provider_id=registered_provider.provider_id,
        model_id=registered_provider.model_id,
        capabilities=registered_provider.capabilities,
        adapter=(
            deepseek_service
            if registered_provider.provider_id == "deepseek"
            else registered_provider.adapter
        ),
        credential_required=registered_provider.credential_required,
        base_url_configurable=registered_provider.base_url_configurable,
        temperature=session.temperature,
        reasoning_effort=session.reasoning_effort,
    )
    turn_provider.require(ProviderTask.CHAT)
    segment_pipeline = None
    segment_count = 0
    history_translation = ""
    segment_event_context = {"emotion": "neutral"}
    streaming_splitter = None
    streamed_segments = []
    
    active_history = session.history
    memory_summary = session.memory_summary
    
    try:
        if session.protocol_version >= 2:
            async def emit_segment_event(event: dict):
                payload = dict(event)
                if payload["type"].startswith("segment."):
                    payload["emotion"] = segment_event_context["emotion"]
                if isinstance(payload.get("data"), bytes):
                    payload["data"] = base64.b64encode(payload["data"]).decode("utf-8")
                await send_queue.put((epoch, payload))

            async def translate_segment(text: str) -> str:
                return await translate_with_provider(turn_provider, text, session.api_key)

            async def synthesize_segment(text: str) -> bytes:
                cleaned = clean_text_for_tts(text)
                if not session.enable_tts:
                    raise RuntimeError("user_disabled")
                if not is_meaningful_sentence(cleaned):
                    raise RuntimeError("action_only")
                try:
                    return await tts_manager.synthesize_async(
                        cleaned,
                        segment_event_context["emotion"],
                        sovits_url=session.sovits_url,
                    )
                except TTSServiceError as exc:
                    message = str(exc).lower()
                    if any(token in message for token in ("timeout", "429", "500", "502", "503", "504", "network")):
                        raise TransientSegmentError(str(exc)) from exc
                    raise

            segment_pipeline = SegmentPipeline(
                TurnIdentity(conversation_id, turn_id, session.revision),
                emit=emit_segment_event,
                translate=translate_segment,
                synthesize=synthesize_segment,
                tts_concurrency=2,
                tts_retries=1,
                translation_retries=1,
                translation_timeout=6.0,
            )
            session.active_segment_pipeline = segment_pipeline
            await segment_pipeline.start()
            if session.voice_stopped_turn_id == turn_id:
                await segment_pipeline.stop_voice()
            streaming_splitter = IncrementalJapaneseSegmenter()

            async def submit_text_delta(delta: str, emotion: str):
                segment_event_context["emotion"] = emotion
                for segment in streaming_splitter.feed(delta):
                    delivered = segment
                    if contains_leak(segment.text):
                        delivered = Segment(
                            segment.id,
                            "はぁ？何言ってんの。あたしは牧瀬紅莉栖よ。変なこと言わないで。",
                        )
                        segment_event_context["emotion"] = "tsundere"
                    streamed_segments.append(delivered)
                    segment_pipeline.submit(delivered)

        await send_queue.put((epoch, {
            "type": "status",
            "dsk": "thinking",
            "state": "thinking",
            "tts": "idle"
        }))
        
        session.system_prompt = await compile_for_session(session, user_msg)
        user_message_id = await models.save_message(
            session.session_id,
            "user",
            user_msg,
            worldline=session.worldline,
            turn_id=turn_id,
            revision=session.revision,
            conversation_id=conversation_id,
        )
        
        from app.agent_tools import AMADEUS_TOOLS
        tools_to_use = AMADEUS_TOOLS
        tool_choice_to_use = "auto"
        if should_force_search(user_msg):
            tool_choice_to_use = {"type": "function", "function": {"name": "web_search"}}
        
        final_text, final_emotion, merged_tool_calls, emo_tag_found = await get_clean_text_stream(
            active_history=active_history,
            memory_summary=memory_summary,
            session=session,
            tools=tools_to_use,
            tool_choice=tool_choice_to_use,
            default_emotion=session.last_assistant_emotion,
            on_text_delta=submit_text_delta if session.protocol_version >= 2 else None,
            provider_snapshot=turn_provider,
        )
        print(f"[DEBUG] processor_loop: final_emotion='{final_emotion}' has_tool_call={bool(merged_tool_calls)} emo_tag_found={emo_tag_found}", flush=True)
        segment_event_context["emotion"] = final_emotion
        
        if merged_tool_calls:
            await send_queue.put((epoch, {
                "type": "status",
                "dsk": "thinking",
                "state": "thinking",
                "tts": "idle",
                "notice": "Amadeus web_search tool invoking...",
                "tool": "web_search"
            }))
            
            tool_responses = []
            for tc in merged_tool_calls.values():
                name = tc.get("function", {}).get("name")
                args = tc.get("function", {}).get("arguments")
                result = await execute_tool_call(name, args)
                tool_responses.append({
                    "role": "tool",
                    "tool_call_id": tc.get("id"),
                    "name": name,
                    "content": result
                })
                
            assistant_tool_call_msg = {
                "role": "assistant",
                "content": None,
                "tool_calls": list(merged_tool_calls.values())
            }
            
            tool_context = list(active_history) + [assistant_tool_call_msg] + tool_responses
            
            final_text, final_emotion, _, emo_tag_found = await get_clean_text_stream(
                active_history=tool_context,
                memory_summary=memory_summary,
                session=session,
                tools=AMADEUS_TOOLS,
                tool_choice="none",
                default_emotion=session.last_assistant_emotion,
                on_text_delta=submit_text_delta if session.protocol_version >= 2 else None,
                provider_snapshot=turn_provider,
            )
            
        # Persist detected emotion for next turn fallback, with decay to neutral
        # if the model forgets the EMO tag for multiple consecutive turns.
        allowed = get_session_allowed_emotions(session)
        if emo_tag_found:
            session.consecutive_no_emo_tags = 0
            if final_emotion in allowed:
                session.last_assistant_emotion = final_emotion
        else:
            session.consecutive_no_emo_tags += 1
            if session.consecutive_no_emo_tags >= 2:
                session.last_assistant_emotion = "neutral"
            elif final_emotion in allowed:
                session.last_assistant_emotion = final_emotion

        if session.protocol_version >= 2 and segment_pipeline is not None:
            for segment in streaming_splitter.finish():
                delivered = segment
                if contains_leak(segment.text):
                    delivered = Segment(
                        segment.id,
                        "はぁ？何言ってんの。あたしは牧瀬紅莉栖よ。変なこと言わないで。",
                    )
                    segment_event_context["emotion"] = "tsundere"
                streamed_segments.append(delivered)
                segment_pipeline.submit(delivered)
            segment_count = len(streamed_segments)
            await segment_pipeline.finish(segment_count=segment_count, emit_completed=False)
            full_ja_text = "".join(segment.text for segment in streamed_segments)
            history_translation = segment_pipeline.translation_text
        elif session.client_type == "voice":
            full_ja_text = await _dispatch_streaming_voice_tts(session, final_text, final_emotion, send_queue, epoch)
        else:
            full_ja_text = await _dispatch_tts_sentences(session, final_text, final_emotion, send_queue, epoch)
        
        history_content = f"[EMO:{final_emotion}] {full_ja_text}" if full_ja_text.strip() else full_ja_text

        if full_ja_text.strip() and session.protocol_version < 2:
            history_translation = await translate_with_provider(
                turn_provider,
                full_ja_text,
                session.api_key,
            )
        
        if session.history_epoch == start_history_epoch and session.conversation_id == conversation_id:
            session.append_message({"role": "assistant", "content": history_content})
            if full_ja_text.strip():
                assistant_message_id = await models.save_message(
                    session.session_id,
                    "assistant",
                    history_content,
                    worldline=session.worldline,
                    turn_id=turn_id,
                    revision=session.revision,
                    conversation_id=conversation_id,
                    translation=history_translation or None,
                )
                from app.services.memory import memory_service
                memory_service.schedule_turn_extraction(
                    session_id=session.session_id,
                    worldline=session.worldline,
                    conversation_id=conversation_id,
                    user_text=user_msg,
                    assistant_text=history_content,
                    source_message_ids=[user_message_id, assistant_message_id],
                    api_key=session.api_key,
                    provider_snapshot=turn_provider,
                )
                from app.services.conversations import conversation_service

                conversation_service.schedule_auto_title(
                    conversation_id,
                    session.session_id,
                    session.worldline,
                    user_text=user_msg,
                    assistant_text=history_content,
                    api_key=session.api_key,
                    provider_snapshot=turn_provider,
                )
        
        if history_translation and session.protocol_version < 2:
            await send_queue.put((epoch, {
                "type": "translation",
                "content": history_translation
            }))
            
        await send_queue.put((epoch, {
            "type": "status",
            "dsk": "idle",
            "state": "done",
            "tts": "idle"
        }))

        if segment_pipeline is not None:
            await segment_pipeline.complete(segment_count=segment_count)
        
        if len(session.history) // 2 > config.HISTORY_COMPRESS_THRESHOLD and not session.compressing:
            session.compressing = True
            asyncio.create_task(
                compress_and_update_history(
                    session.session_id,
                    session.history,
                    session.worldline,
                    session.revision,
                    conversation_id,
                    turn_provider,
                )
            )
            
    except asyncio.CancelledError:
        if segment_pipeline is not None:
            await segment_pipeline.cancel()
        tts_manager.cancel_pending(session.tts_key)
        raise
    except Exception as e:
        if segment_pipeline is not None:
            await segment_pipeline.cancel()
        print(f"[Processor Error] Session {session.session_id}: {e}")
        await send_queue.put((epoch, {"type": "error", "message": f"Server error: {str(e)}"}))
    finally:
        if session.active_segment_pipeline is segment_pipeline:
            session.active_segment_pipeline = None
        session.is_busy = False


async def _dispatch_tts_sentences(
    session: SessionState,
    final_text: str,
    final_emotion: str,
    send_queue: asyncio.Queue,
    epoch: int,
) -> str:
    sentence_queue = asyncio.Queue()
    full_ja_text_parts = []

    async def tts_send_callback(seq: int, payload):
        await sentence_queue.put({
            "sentence_id": seq,
            "payload": payload
        })

    tts_manager.reset_sequence(session.tts_key, tts_send_callback)

    speaking_status_sent = False

    async def sender_task():
        nonlocal speaking_status_sent
        while True:
            item = await sentence_queue.get()
            if item is None:
                sentence_queue.task_done()
                break

            if isinstance(item, Exception):
                raise item

            sentence_id = item["sentence_id"]
            payload = item.get("payload", {})
            audio_bytes = payload.get("audio")
            error = payload.get("error")
            text = payload.get("text", "")
            emotion = payload.get("emotion", "neutral")
            print(
                f"[DEBUG] sender_task sentence={sentence_id} emotion='{emotion}' "
                f"text_chars={len(text)} client_type='{session.client_type}'",
                flush=True,
            )

            if not speaking_status_sent:
                await send_queue.put((epoch, {
                    "type": "status",
                    "dsk": "speaking",
                    "state": "speaking"
                }))
                speaking_status_sent = True

            if audio_bytes is None:
                reason = "user_disabled" if not session.enable_tts else (error or "service_error")
                await send_queue.put((epoch, {
                    "type": "text_chunk",
                    "sentence_id": sentence_id,
                    "content": text,
                    "emotion": emotion,
                    "audio_degraded": True,
                    "degraded_reason": reason
                }))
                sentence_queue.task_done()
                continue

            audio_base64 = base64.b64encode(audio_bytes).decode('utf-8')

            await send_queue.put((epoch, {
                "type": "text_chunk",
                "sentence_id": sentence_id,
                "content": text,
                "emotion": emotion
            }))

            await send_queue.put((epoch, {
                "type": "audio_chunk",
                "sentence_id": sentence_id,
                "data": audio_base64
            }))
            sentence_queue.task_done()

    t2 = asyncio.create_task(sender_task())

    try:
        sentences, _ = extract_sentences_from_buffer(final_text, is_end=True)
        tts_fragment_buffer = ""
        tts_status_sent = False

        for s in sentences:
            text = s.strip()
            if not text or not is_meaningful_sentence(text):
                continue

            if len(text) < 3:
                if len(tts_fragment_buffer) > 15:
                    forced = tts_fragment_buffer
                    tts_fragment_buffer = text
                    full_ja_text_parts.append(forced)
                    tts_text = clean_text_for_tts(forced)
                    is_action_only = not is_meaningful_sentence(tts_text)
                    if session.enable_tts and not is_action_only:
                        if not tts_status_sent:
                            await send_queue.put((epoch, {
                                "type": "status",
                                "dsk": "speaking",
                                "state": "speaking",
                                "tts": "synthesizing"
                            }))
                            tts_status_sent = True
                        await tts_manager.synthesize_and_queue(session.tts_key, tts_text, final_emotion, sovits_url=session.sovits_url)
                    else:
                        reason = "user_disabled" if not session.enable_tts else "action_only"
                        await tts_manager.queue_silent(session.tts_key, forced, final_emotion, reason)
                    continue
                else:
                    tts_fragment_buffer += text
                    full_ja_text_parts.append(text)
                    continue

            if tts_fragment_buffer:
                text = tts_fragment_buffer + text
                tts_fragment_buffer = ""

            full_ja_text_parts.append(text)
            tts_text = clean_text_for_tts(text)
            is_action_only = not is_meaningful_sentence(tts_text)

            if session.enable_tts and not is_action_only:
                if not tts_status_sent:
                    await send_queue.put((epoch, {
                        "type": "status",
                        "dsk": "speaking",
                        "state": "speaking",
                        "tts": "synthesizing"
                    }))
                    tts_status_sent = True
                await tts_manager.synthesize_and_queue(session.tts_key, tts_text, final_emotion, sovits_url=session.sovits_url)
            else:
                reason = "user_disabled" if not session.enable_tts else "action_only"
                await tts_manager.queue_silent(session.tts_key, text, final_emotion, reason)

        if tts_fragment_buffer:
            text = tts_fragment_buffer
            tts_fragment_buffer = ""
            full_ja_text_parts.append(text)
            tts_text = clean_text_for_tts(text)
            is_action_only = not is_meaningful_sentence(tts_text)
            clean_len = len(re.sub(r'[\s。！？!?,.、…\-—]', '', tts_text))
            if clean_len < 2:
                await tts_manager.queue_silent(session.tts_key, text, final_emotion, "too_short")
            elif session.enable_tts and not is_action_only:
                if not tts_status_sent:
                    await send_queue.put((epoch, {
                        "type": "status",
                        "dsk": "speaking",
                        "state": "speaking",
                        "tts": "synthesizing"
                    }))
                    tts_status_sent = True
                await tts_manager.synthesize_and_queue(session.tts_key, tts_text, final_emotion, sovits_url=session.sovits_url)
            else:
                reason = "user_disabled" if not session.enable_tts else "action_only"
                await tts_manager.queue_silent(session.tts_key, text, final_emotion, reason)

        await tts_manager.gather_pending(session.tts_key)
        await sentence_queue.put(None)
        await t2

    except Exception as e:
        t2.cancel()
        await sentence_queue.put(e)
        await asyncio.gather(t2, return_exceptions=True)
        raise e

    return "".join(full_ja_text_parts)


async def _dispatch_streaming_voice_tts(session: SessionState, final_text: str, emotion: str, send_queue: asyncio.Queue, epoch: int) -> str:
    sentences, _ = extract_sentences_from_buffer(final_text, is_end=True)
    delivered = []
    for sentence_id, text in enumerate(sentences or [final_text]):
        text = text.strip()
        if not text:
            continue
        delivered.append(text)
        cleaned = clean_text_for_tts(text)
        if not session.enable_tts or not is_meaningful_sentence(cleaned):
            reason = "user_disabled" if not session.enable_tts else "action_only"
            await send_queue.put((epoch, {"type": "text_chunk", "sentence_id": sentence_id, "content": text, "emotion": emotion, "audio_degraded": True, "degraded_reason": reason}))
            continue
        await send_queue.put((epoch, {"type": "text_chunk", "sentence_id": sentence_id, "content": text, "emotion": emotion}))
        stream = tts_manager.stream_pcm(cleaned, emotion, sovits_url=session.sovits_url)
        session.active_streams.add(stream)
        format_sent = False
        try:
            async with aclosing(stream) as chunks:
                async for audio_format, pcm in chunks:
                    if not format_sent and audio_format is not None:
                        await send_queue.put((epoch, {"type": "voice_audio_format", "channels": audio_format.channels, "sample_rate": audio_format.sample_rate, "sample_width": audio_format.sample_width}))
                        format_sent = True
                    await send_queue.put((epoch, {"type": "voice_audio_pcm", "data": pcm}))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await send_queue.put((epoch, {"type": "voice.audio_degraded", "sentence_id": sentence_id, "reason": str(exc)}))
        finally:
            session.active_streams.discard(stream)
    return "".join(delivered)


async def run_tts_pipeline(session: SessionState, text: str, emotion: str, send_queue: asyncio.Queue, epoch: int):
    session.is_busy = True
    try:
        await send_queue.put((epoch, {"type": "status", "dsk": "speaking", "state": "speaking", "tts": "synthesizing"}))
        await _dispatch_tts_sentences(session, text, emotion, send_queue, epoch)
        await send_queue.put((epoch, {"type": "translation", "content": ""}))
        await send_queue.put((epoch, {"type": "status", "dsk": "idle", "state": "done", "tts": "idle"}))
    except asyncio.CancelledError:
        tts_manager.cancel_pending(session.tts_key)
        raise
    except Exception as e:
        print(f"[TTS Debug Error] {e}")
        await send_queue.put((epoch, {"type": "error", "message": f"TTS debug error: {e}"}))
    finally:
        session.is_busy = False


async def writer_loop(send_queue: asyncio.Queue, session: SessionState, websocket: WebSocket):
    try:
        while True:
            msg = await send_queue.get()
            if msg is None:
                send_queue.task_done()
                break
            
            # Unpack msg
            msg_epoch, payload = msg
            if msg_epoch == session.current_epoch:
                await websocket.send_json(payload)
                
            send_queue.task_done()
    except WebSocketDisconnect:
        pass
    except Exception as e:
        print(f"[Writer Exception] Session {session.session_id}: {e}")

@router.websocket("/ws/chat")
async def websocket_chat_endpoint(websocket: WebSocket):
    # 1. Handshake Origin Check (CSWSH Defense)
    # Disabled to allow local network access (e.g. 192.168.x.x) through Vite proxy.
    # Vite's http-proxy hangs the websocket if we reject it here, causing a 3s timeout.

    await websocket.accept()
    
    # 2. Thread-safe retrieval or initialization of session data
    session_id = websocket.query_params.get("session_id", "default")
    async with sessions_creation_lock:
        if session_id not in sessions:
            sessions[session_id] = SessionState(session_id)
        
    session = sessions[session_id]
    
    # Increment current epoch on new connection to invalidate previous inflight messages
    session.current_epoch += 1
    session.revision = session.current_epoch
    
    # Connection-level send_queue
    send_queue = asyncio.Queue()
    
    # 3. Writer Coroutine: reads send_queue and sends to client
    writer_task = asyncio.create_task(writer_loop(send_queue, session, websocket))
    
    authenticated = False
    
    try:
        # 4. Reader Coroutine (Main Loop): reads WebSocket frames and routes
        while True:
            try:
                data = await websocket.receive_json()
            except WebSocketDisconnect:
                break
            except Exception as e:
                await send_queue.put((session.current_epoch, {"type": "error", "message": f"数据格式无效: {str(e)}"}))
                continue
            
            frame_type = data.get("type")
            
            # Auth logic
            if not authenticated:
                if frame_type != "auth":
                    await send_queue.put((
                        session.current_epoch,
                        {
                            "type": "error",
                            "message": "未认证。请打开设置并配置您的 API Key。",
                        },
                    ))
                    continue
                if "worldline" in data:
                    wl = data["worldline"]
                    if wl in ("steins_gate", "beta"):
                        session.worldline = wl
                requested_conversation_id = str(
                    data.get("conversation_id") or ""
                ).strip()
                if requested_conversation_id:
                    from app.services.conversations import (
                        ConversationNotFound,
                        conversation_service,
                    )
                    try:
                        await conversation_service.select(
                            session.session_id,
                            session.worldline,
                            requested_conversation_id,
                        )
                    except ConversationNotFound:
                        await send_queue.put((
                            session.current_epoch,
                            {
                                "type": "error",
                                "message": "请求的会话不存在或不属于当前世界线。",
                            },
                        ))
                        continue
                await session.load_from_db()
                api_key = credential_store.get(session.provider_id)
                provider_snapshot = provider_registry.snapshot(
                    session.provider_id,
                    session.model,
                )
                if provider_snapshot.credential_required and not api_key:
                    await send_queue.put((
                        session.current_epoch,
                        {
                            "type": "error",
                            "message": (
                                f"缺失 {session.provider_id} API Key。"
                                "请打开设置保存供应商凭据后重新连接。"
                            ),
                        },
                    ))
                    continue
                # Store credentials in session
                session.api_key = api_key or ""
                if "sovits_url" in data:
                    session.sovits_url = data["sovits_url"]
                
                from app.security.prompt import get_rendered_system_prompt, SYSTEM_PROMPT_BASE
                if "client" in data:
                    session.client_type = data["client"]
                requested_protocol = data.get("protocol_version", data.get("protocol", 1))
                session.protocol_version = 2 if requested_protocol in (2, "2", "v2") else 1
                
                base_prompt = data.get("system_prompt") or SYSTEM_PROMPT_BASE
                session.base_system_prompt = base_prompt
                session.system_prompt = get_rendered_system_prompt(base_prompt, session.worldline, session.client_type)

                if "temperature" in data:
                    try:
                        temp = float(data["temperature"])
                        session.temperature = max(0.0, min(2.0, temp))
                    except (ValueError, TypeError):
                        pass
                if "enable_tts" in data:
                    session.enable_tts = bool(data["enable_tts"])
                if "model" in data:
                    m = data["model"]
                    if m in ("deepseek-v4-flash", "deepseek-v4-pro"):
                        session.model = m
                if "reasoning_effort" in data:
                    effort = data["reasoning_effort"]
                    if effort in ("high", "xhigh"):
                        session.reasoning_effort = effort
                    
                authenticated = True
                await session_coordinator.persist_current(session.session_id, session.worldline, session.revision)
                await session_coordinator.acquire_lease(session.session_id, session.worldline, websocket)
                continue
            
            # Already authenticated, check duplicate auth
            if frame_type == "auth":
                await send_queue.put((session.current_epoch, {"type": "error", "message": "已完成认证，请勿重复操作"}))
                continue
                
            # Handle ping frames (keep-alive)
            if frame_type == "ping":
                continue

            if frame_type == "voice.stop":
                session.voice_stopped_turn_id = session.active_turn_id
                pipeline = session.active_segment_pipeline
                if pipeline is not None:
                    await pipeline.stop_voice()
                tts_manager.cancel_pending(session.tts_key)
                await send_queue.put((session.current_epoch, {
                    "type": "status",
                    "dsk": "thinking" if session.is_busy else "idle",
                    "state": "voice_stopped",
                    "tts": "idle",
                }))
                continue

            if frame_type == "turn.cancel":
                pipeline = session.active_segment_pipeline
                if pipeline is not None:
                    await pipeline.cancel()
                elif session.protocol_version >= 2 and session.active_turn_id:
                    await send_queue.put((session.current_epoch, {
                        "type": "turn.cancelled",
                        "conversation_id": session.conversation_id,
                        "turn_id": session.active_turn_id,
                        "generation": session.revision,
                    }))
                await cancel_inflight(session)
                tts_manager.cancel_pending(session.tts_key)
                await send_queue.put((session.current_epoch, {
                    "type": "status",
                    "dsk": "idle",
                    "state": "cancelled",
                    "tts": "idle",
                }))
                continue

            if frame_type == "conversation.switch":
                conversation_id = str(data.get("conversation_id") or "").strip()
                if not conversation_id:
                    await send_queue.put((session.current_epoch, {
                        "type": "error",
                        "message": "conversation.switch requires conversation_id",
                    }))
                    continue
                pipeline = session.active_segment_pipeline
                if pipeline is not None:
                    await pipeline.cancel()
                await cancel_inflight(session)
                from app.services.conversations import ConversationNotFound, conversation_service
                try:
                    await conversation_service.select(
                        session.session_id,
                        session.worldline,
                        conversation_id,
                    )
                except ConversationNotFound as exc:
                    await send_queue.put((session.current_epoch, {
                        "type": "error",
                        "message": str(exc),
                    }))
                    continue
                session.history_epoch += 1
                session.current_epoch += 1
                session.revision = session.current_epoch
                await session.load_from_db()
                await send_queue.put((session.current_epoch, {
                    "type": "status",
                    "dsk": "idle",
                    "state": "conversation_switched",
                    "conversation_id": session.conversation_id,
                }))
                continue
                
            if frame_type == "clear_history":
                try:
                    await models.clear_session_data(session.session_id, worldline=session.worldline)
                except Exception as e:
                    await send_queue.put((session.current_epoch, {"type": "error", "message": f"物理清除历史记录失败: {str(e)}"}))
                    continue
                
                session.history = []
                session.history_epoch += 1
                session.current_epoch += 1
                
                await cancel_inflight(session)
                    
                await send_queue.put((session.current_epoch, {
                    "type": "status",
                    "dsk": "idle",
                    "state": "done",
                    "tts": "idle",
                    "notice": "History memory successfully cleared physically."
                }))
                continue
                
            if frame_type == "config_update":
                if "api_key" in data:
                    # Credentials are write-only through the local provider API.
                    # Never retain secrets received in a long-lived WS frame.
                    pass
                    
                if "sovits_url" in data:
                    session.sovits_url = data["sovits_url"]
                
                from app.security.prompt import get_rendered_system_prompt, SYSTEM_PROMPT_BASE
                needs_reset = False
                if "system_prompt" in data:
                    base_prompt = data["system_prompt"]
                    session.base_system_prompt = base_prompt
                    session.system_prompt = get_rendered_system_prompt(base_prompt, session.worldline, session.client_type)
                    needs_reset = True
                if "worldline" in data:
                    new_wl = data["worldline"]
                    if new_wl in ("steins_gate", "beta") and session.worldline != new_wl:
                        old_wl = session.worldline
                        await session_coordinator.release_lease(session.session_id, old_wl, websocket)
                        await session_coordinator.switch(session, new_wl)
                        base_prompt = session.base_system_prompt or SYSTEM_PROMPT_BASE
                        session.system_prompt = get_rendered_system_prompt(base_prompt, new_wl, session.client_type)
                        await session_coordinator.acquire_lease(session.session_id, new_wl, websocket)
                        needs_reset = False
                
                if needs_reset:
                    session.history = []
                    session.history_epoch += 1
                    session.current_epoch += 1
                    await cancel_inflight(session)
                    await send_queue.put((session.current_epoch, {"type": "status", "dsk": "idle", "state": "done"}))
                    
                if "temperature" in data:
                    try:
                        temp = float(data["temperature"])
                        session.temperature = max(0.0, min(2.0, temp))
                    except (ValueError, TypeError):
                        pass
                if "enable_tts" in data:
                    session.enable_tts = bool(data["enable_tts"])
                if "model" in data:
                    m = data["model"]
                    if m in ("deepseek-v4-flash", "deepseek-v4-pro"):
                        session.model = m
                if "reasoning_effort" in data:
                    effort = data["reasoning_effort"]
                    if effort in ("high", "xhigh"):
                        session.reasoning_effort = effort
                continue
                
            elif frame_type == "interrupt-signal":
                await cancel_inflight(session)
                tts_manager.cancel_pending(session.tts_key)
                session.current_epoch += 1
                await send_queue.put((session.current_epoch, {
                    "type": "text_chunk",
                    "content": "[Interrupted by user]",
                    "emotion": "neutral",
                    "audio_degraded": True,
                    "degraded_reason": "interrupted"
                }))
                await send_queue.put((session.current_epoch, {
                    "type": "status",
                    "dsk": "idle",
                    "state": "done",
                    "tts": "idle"
                }))
                continue

            elif frame_type == "tts_debug":
                if "content" not in data or "emotion" not in data:
                    await send_queue.put((session.current_epoch, {"type": "error", "message": "tts_debug 需要 content 和 emotion 字段"}))
                    continue
                emotion = data.get("emotion", "neutral")
                content = data.get("content", "").strip()
                if not content:
                    continue
                allowed = get_session_allowed_emotions(session)
                if emotion not in allowed:
                    await send_queue.put((session.current_epoch, {"type": "error", "message": f"未知情绪: {emotion}"}))
                    continue
                if session.is_busy:
                    await send_queue.put((session.current_epoch, {"type": "error", "message": "系统正在处理中，请稍后再试"}))
                    continue
                session.is_busy = True
                session.active_task = asyncio.create_task(
                    run_tts_pipeline(session, content, emotion, send_queue, session.current_epoch)
                )
                continue

            elif frame_type in ("chat", "chat.send"):
                if "message" in data:
                    await send_queue.put((session.current_epoch, {"type": "error", "message": "禁止使用已废弃的 'message' 字段"}))
                    continue
                if "content" not in data:
                    await send_queue.put((session.current_epoch, {"type": "error", "message": "聊天消息中缺失 'content' 字段"}))
                    continue
                    
                content = data.get("content", "").strip()
                if not content:
                    await send_queue.put((session.current_epoch, {"type": "error", "message": "发送的内容不能为空"}))
                    continue
                    
                # Request serialization check
                if session.is_busy:
                    await send_queue.put((session.current_epoch, {"type": "error", "message": "系统正在处理中，请稍后再试"}))
                    continue
                
                session.is_busy = True
                session.active_turn_id = str(uuid4())
                session.voice_stopped_turn_id = None
                session.append_message({"role": "user", "content": content})
                session.active_task = asyncio.create_task(
                    processor_loop(session, content, send_queue, session.current_epoch, session.history_epoch)
                )
                
            else:
                await send_queue.put((session.current_epoch, {"type": "error", "message": f"Unknown frame type: {frame_type}"}))
                
    except Exception as e:
        print(f"[Reader Exception] Session {session_id}: {e}")
        try:
            await send_queue.put((session.current_epoch, {"type": "error", "message": f"Server error: {str(e)}"}))
        except Exception:
            pass
    finally:
        # Cancel running Processor and Writer tasks on disconnect
        tasks_to_gather = [writer_task]
        writer_task.cancel()
        if session.active_task:
            if not session.active_task.done():
                session.active_task.cancel()
            tasks_to_gather.append(session.active_task)
        # Clean up session state active task reference if done
        await asyncio.gather(*tasks_to_gather, return_exceptions=True)
        await session_coordinator.release_lease(session.session_id, session.worldline, websocket)
