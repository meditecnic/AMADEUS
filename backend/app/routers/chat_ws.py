import re
import base64
import asyncio
import starlette
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from app import config
from app.services.deepseek import DeepSeekService
from app.services.tts_queue import tts_manager
from app import models
from app.db import init_db

import copy
from app.services.tts_queue import TTSServiceError
from contextlib import aclosing
from app.security.guard import contains_leak

router = APIRouter()
deepseek_service = DeepSeekService()

class SessionState:
    def __init__(self, session_id: str):
        self.session_id = session_id
        self._history = []
        self.memory_summary = ""
        self.lock = asyncio.Lock()
        self.compressing = False
        
        self.temperature = 0.8
        self.model = config.DEEPSEEK_MODEL
        self.reasoning_effort = None
        self.system_prompt = None
        self.api_key = None
        self.sovits_url = None
        self.enable_tts = True
        self.worldline = "steins_gate"
        self.client_type = "mobile"
        
        self.active_task = None
        self.is_busy = False
        
        self.current_epoch = 0
        self.history_epoch = 0
        self.last_assistant_emotion = "neutral"
        self.consecutive_no_emo_tags = 0

    async def load_from_db(self):
        await init_db()
        self._history = await models.get_recent_messages(self.session_id, limit=6)
        self.memory_summary = await models.get_latest_summary(self.session_id)

    @property
    def history(self):
        return copy.deepcopy(self._history)

    @history.setter
    def history(self, value):
        self._history = copy.deepcopy(value)

    def append_message(self, message: dict):
        self._history.append(copy.deepcopy(message))

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

def get_session_allowed_emotions(session: "SessionState") -> set:
    if session.client_type == "desktop":
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



async def compress_and_update_history(session_id: str, current_history: list):
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
            new_summary = await deepseek_service.compress_history(to_compress, api_key=session_data.api_key, old_summary=session_data.memory_summary)
            if new_summary:
                await models.save_memory_summary(session_id, new_summary)
                async with session_data.lock:
                    session_data.memory_summary = new_summary
                    if len(session_data.history) >= N:
                        session_data.history = session_data.history[N:]
                    else:
                        session_data.history = []
                    print(f"[History Compression] Session {session_id}: Compression successful. Summary: {new_summary}")
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
    from app.services.search import web_search
    if name == "web_search":
        try:
            args = json.loads(arguments_str)
            query = args.get("query", "")
        except Exception:
            query = arguments_str
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, web_search, query)
    else:
        return f"Error: Tool {name} not found."

async def get_clean_text_stream(
    active_history: list,
    memory_summary: str,
    session: SessionState,
    tools: list = None,
    tool_choice: str = None,
    default_emotion: str = "neutral"
):
    """
    Get text or tool calls stream with leak retries.
    Returns: (final_text, final_emotion, merged_tool_calls, emo_tag_found)
    """
    MAX_ATTEMPTS = 3
    _logged_content = False
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

        stream_gen = deepseek_service.get_chat_stream(
            active_history=active_history,
            memory_summary=memory_summary,
            api_key=session.api_key,
            system_prompt=eff_system_prompt,
            temperature=session.temperature,
            tools=tools,
            tool_choice=tool_choice,
            model=session.model,
            reasoning_effort=session.reasoning_effort
        )
        
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
                        if not _logged_content:
                            print(f"[RAW CONTENT] '{content[:80]}'", flush=True)
                            _logged_content = True
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
                                    emo_buffer = ""
                                    in_emo_tag = False
                                elif len(emo_buffer) > 20:
                                    text_buffer += emo_buffer
                                    emo_buffer = ""
                                    in_emo_tag = False
                            else:
                                if char == '[' and not text_buffer.strip():
                                    in_emo_tag = True
                                    emo_buffer = "["
                                else:
                                    text_buffer += char
            if emo_buffer:
                text_buffer += emo_buffer
        except asyncio.CancelledError:
            raise
            
        if merged_tool_calls:
            return "", "neutral", merged_tool_calls, False
            
        if contains_leak(text_buffer):
            print(f"[Guard] attempt {attempt + 1}/{MAX_ATTEMPTS} 露見検出、破棄して再試行: {text_buffer[:50]}")
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
    
    active_history = session.history
    memory_summary = session.memory_summary
    
    try:
        await send_queue.put((epoch, {
            "type": "status",
            "dsk": "thinking",
            "state": "thinking",
            "tts": "idle"
        }))
        
        await models.save_message(session.session_id, "user", user_msg)
        
        from app.agent_tools import AMADEUS_TOOLS
        tools_to_use = None
        tool_choice_to_use = None
        if session.client_type == "desktop":
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
            default_emotion=session.last_assistant_emotion
        )
        print(f"[DEBUG] processor_loop: final_emotion='{final_emotion}' has_tool_call={bool(merged_tool_calls)} emo_tag_found={emo_tag_found}", flush=True)
        
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
                default_emotion=session.last_assistant_emotion
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

        full_ja_text = await _dispatch_tts_sentences(session, final_text, final_emotion, send_queue, epoch)
        
        history_content = f"[EMO:{final_emotion}] {full_ja_text}" if full_ja_text.strip() else full_ja_text
        
        if session.history_epoch == start_history_epoch:
            session.append_message({"role": "assistant", "content": history_content})
            if full_ja_text.strip():
                await models.save_message(session.session_id, "assistant", history_content)
        
        if full_ja_text.strip():
            translation = await deepseek_service.translate_to_zh(full_ja_text, api_key=session.api_key)
            await send_queue.put((epoch, {
                "type": "translation",
                "content": translation
            }))
            
        await send_queue.put((epoch, {
            "type": "status",
            "dsk": "idle",
            "state": "done",
            "tts": "idle"
        }))
        
        if len(session.history) // 2 > config.HISTORY_COMPRESS_THRESHOLD and not session.compressing:
            session.compressing = True
            asyncio.create_task(compress_and_update_history(session.session_id, session.history))
            
    except asyncio.CancelledError:
        tts_manager.cancel_pending(session.session_id)
        raise
    except Exception as e:
        print(f"[Processor Error] Session {session.session_id}: {e}")
        await send_queue.put((epoch, {"type": "error", "message": f"Server error: {str(e)}"}))
    finally:
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

    tts_manager.reset_sequence(session.session_id, tts_send_callback)

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
            print(f"[DEBUG] sender_task processing sentence {sentence_id}: emotion='{emotion}' text_preview='{text[:50]}' client_type='{session.client_type}'", flush=True)

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
                        await tts_manager.synthesize_and_queue(session.session_id, tts_text, final_emotion, sovits_url=session.sovits_url)
                    else:
                        reason = "user_disabled" if not session.enable_tts else "action_only"
                        await tts_manager.queue_silent(session.session_id, forced, final_emotion, reason)
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
                await tts_manager.synthesize_and_queue(session.session_id, tts_text, final_emotion, sovits_url=session.sovits_url)
            else:
                reason = "user_disabled" if not session.enable_tts else "action_only"
                await tts_manager.queue_silent(session.session_id, text, final_emotion, reason)

        if tts_fragment_buffer:
            text = tts_fragment_buffer
            tts_fragment_buffer = ""
            full_ja_text_parts.append(text)
            tts_text = clean_text_for_tts(text)
            is_action_only = not is_meaningful_sentence(tts_text)
            clean_len = len(re.sub(r'[\s。！？!?,.、…\-—]', '', tts_text))
            if clean_len < 2:
                await tts_manager.queue_silent(session.session_id, text, final_emotion, "too_short")
            elif session.enable_tts and not is_action_only:
                if not tts_status_sent:
                    await send_queue.put((epoch, {
                        "type": "status",
                        "dsk": "speaking",
                        "state": "speaking",
                        "tts": "synthesizing"
                    }))
                    tts_status_sent = True
                await tts_manager.synthesize_and_queue(session.session_id, tts_text, final_emotion, sovits_url=session.sovits_url)
            else:
                reason = "user_disabled" if not session.enable_tts else "action_only"
                await tts_manager.queue_silent(session.session_id, text, final_emotion, reason)

        await tts_manager.gather_pending(session.session_id)
        await sentence_queue.put(None)
        await t2

    except Exception as e:
        t2.cancel()
        await sentence_queue.put(e)
        await asyncio.gather(t2, return_exceptions=True)
        raise e

    return "".join(full_ja_text_parts)


async def run_tts_pipeline(session: SessionState, text: str, emotion: str, send_queue: asyncio.Queue, epoch: int):
    session.is_busy = True
    try:
        await send_queue.put((epoch, {"type": "status", "dsk": "speaking", "state": "speaking", "tts": "synthesizing"}))
        await _dispatch_tts_sentences(session, text, emotion, send_queue, epoch)
        await send_queue.put((epoch, {"type": "translation", "content": ""}))
        await send_queue.put((epoch, {"type": "status", "dsk": "idle", "state": "done", "tts": "idle"}))
    except asyncio.CancelledError:
        tts_manager.cancel_pending(session.session_id)
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
                api_key = data.get("api_key") or config.DEEPSEEK_API_KEY
                if not api_key:
                    await send_queue.put((
                        session.current_epoch,
                        {
                            "type": "error",
                            "message": "缺失 API Key。请打开设置保存 DeepSeek API Key 后重新连接。",
                        },
                    ))
                    continue
                
                # Store credentials in session
                session.api_key = api_key
                if "sovits_url" in data:
                    session.sovits_url = data["sovits_url"]
                
                from app.security.prompt import get_rendered_system_prompt, SYSTEM_PROMPT_BASE
                if "worldline" in data:
                    wl = data["worldline"]
                    if wl in ("steins_gate", "beta"):
                        session.worldline = wl
                
                if "client" in data:
                    session.client_type = data["client"]
                
                base_prompt = data.get("system_prompt") or SYSTEM_PROMPT_BASE
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
                await session.load_from_db()
                continue
            
            # Already authenticated, check duplicate auth
            if frame_type == "auth":
                await send_queue.put((session.current_epoch, {"type": "error", "message": "已完成认证，请勿重复操作"}))
                continue
                
            # Handle ping frames (keep-alive)
            if frame_type == "ping":
                continue
                
            if frame_type == "clear_history":
                try:
                    await models.clear_session_data(session.session_id)
                except Exception as e:
                    await send_queue.put((session.current_epoch, {"type": "error", "message": f"物理清除历史记录失败: {str(e)}"}))
                    continue
                
                session.history = []
                session.history_epoch += 1
                session.current_epoch += 1
                
                if session.active_task and not session.active_task.done():
                    session.active_task.cancel()
                    
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
                    new_key = data["api_key"]
                    if new_key and new_key.strip():
                        session.api_key = new_key.strip()
                    
                if "sovits_url" in data:
                    session.sovits_url = data["sovits_url"]
                
                from app.security.prompt import get_rendered_system_prompt, SYSTEM_PROMPT_BASE
                needs_reset = False
                if "system_prompt" in data:
                    base_prompt = data["system_prompt"]
                    session.system_prompt = get_rendered_system_prompt(base_prompt, session.worldline, session.client_type)
                    needs_reset = True
                if "worldline" in data:
                    new_wl = data["worldline"]
                    if new_wl in ("steins_gate", "beta") and session.worldline != new_wl:
                        session.worldline = new_wl
                        current_prompt = session.system_prompt or SYSTEM_PROMPT_BASE
                        if "【現在世界線：" in current_prompt:
                            base_prompt = current_prompt.split("【現在世界線：")[0].strip()
                        else:
                            base_prompt = current_prompt
                        session.system_prompt = get_rendered_system_prompt(base_prompt, new_wl, session.client_type)
                        needs_reset = True
                
                if needs_reset:
                    session.history = []
                    session.history_epoch += 1
                    session.current_epoch += 1
                    if session.active_task and not session.active_task.done():
                        session.active_task.cancel()
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
                if session.active_task and not session.active_task.done():
                    session.active_task.cancel()
                tts_manager.cancel_pending(session.session_id)
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

            elif frame_type == "chat":
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
