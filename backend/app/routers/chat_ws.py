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
        self.system_prompt = None
        self.api_key = None
        self.sovits_url = None
        self.enable_tts = True
        
        self.active_task = None
        self.is_busy = False
        
        self.current_epoch = 0
        self.history_epoch = 0

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
ALLOWED_EMOTIONS = {"neutral", "tsundere", "embarrassed", "intellectual"}

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
    
    # Take a deep copy snapshot of history and memory summary for LLM call
    active_history = session.history
    memory_summary = session.memory_summary
    
    sentence_queue = asyncio.Queue()
    sentence_id_counter = 0
    full_ja_text_parts = []
    speaking_status_sent = False
    
    # Store reference to running tts tasks to cancel them if this processor gets cancelled
    tts_tasks = []
    
    async def stream_and_segment():
        nonlocal sentence_id_counter

        # 两段式 + 重试：先纯流式收字拼成完整文本并判 leak（不分配 sentence_id、不起 TTS），
        # leak 则 aclose 丢弃整轮、带强化语重试（最多 3 次）；干净后再切句/起 TTS/入队。
        # 这样重试不会与上一轮已发的句子叠加（因为上一轮根本没入队）。
        MAX_ATTEMPTS = 3
        final_text = ""
        final_emotion = "neutral"

        for attempt in range(MAX_ATTEMPTS):
            emo_buffer = ""
            in_emo_tag = False
            last_emotion = "neutral"
            text_buffer = ""
            leaked = False

            # 重试时给 system_prompt 追加一段强化纠偏语（首attempt不加）
            eff_system_prompt = session.system_prompt
            if attempt > 0:
                eff_system_prompt = (session.system_prompt or "") + (
                    "\n【直前の出力でAI・アシスタント・言語モデル等の露見語が検出された。"
                    "絶対に紅莉栖としてのみ振る舞い、二度とそのような語を使わないこと。】"
                )

            stream_gen = deepseek_service.get_chat_stream(
                active_history=active_history,
                memory_summary=memory_summary,
                api_key=session.api_key,
                system_prompt=eff_system_prompt,
                temperature=session.temperature
            )
            try:
                async with aclosing(stream_gen) as stream:
                    async for chunk in stream:
                        for char in chunk:
                            if in_emo_tag:
                                emo_buffer += char
                                if char == ']':
                                    match = EMO_TAG_PATTERN.match(emo_buffer)
                                    if match:
                                        emotion = match.group(1)
                                        last_emotion = emotion if emotion in ALLOWED_EMOTIONS else "neutral"
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
                    # 流结束后把残留 emo_buffer 归并进正文
                    if emo_buffer:
                        text_buffer += emo_buffer
            except asyncio.CancelledError:
                raise

            # 对完整文本做 leak 判定（完整判定比逐句判定更稳：避免半句触发误判/漏判）
            if contains_leak(text_buffer):
                print(f"[Guard] attempt {attempt + 1}/{MAX_ATTEMPTS} 露見検出、破棄して再試行: {text_buffer[:50]}")
                leaked = True
                if attempt < MAX_ATTEMPTS - 1:
                    continue  # 还有重试机会 → 重新走一轮
                # 重试用尽仍 leak → 兜底：用红莉栖式冷淡反驳句，绝不下发露馅原文
                final_text = "はぁ？何言ってんの。あたしは牧瀬紅莉栖よ。変なこと言わないで。"
                final_emotion = "tsundere"
                break

            # 干净 → 采纳
            final_text = text_buffer
            final_emotion = last_emotion
            leaked = False
            break

        # 第二段：对最终干净文本切句、起 TTS、入队（只在确认干净后执行一次）
        try:
            remaining = final_text
            while True:
                sentences, remaining = extract_sentences_from_buffer(remaining, is_end=(remaining == ""))
                for s in sentences:
                    text = s.strip()
                    if not text or not is_meaningful_sentence(text):
                        continue
                    full_ja_text_parts.append(text)

                    tts_task = None
                    if session.enable_tts:
                        tts_task = asyncio.create_task(
                            tts_manager.synthesize_async(text, final_emotion, sovits_url=session.sovits_url)
                        )
                        tts_tasks.append(tts_task)

                    await sentence_queue.put({
                        "sentence_id": sentence_id_counter,
                        "text": text,
                        "emotion": final_emotion,
                        "tts_task": tts_task
                    })
                    sentence_id_counter += 1
                if not sentences:
                    break
        except asyncio.CancelledError:
            raise
        except Exception as e:
            await sentence_queue.put(e)
            return

        await sentence_queue.put(None)

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
            text = item["text"]
            emotion = item["emotion"]
            tts_task = item["tts_task"]
            
            # Check enable_tts before await
            if not session.enable_tts:
                if tts_task and not tts_task.done():
                    tts_task.cancel()
                
                await send_queue.put((epoch, {
                    "type": "text_chunk",
                    "sentence_id": sentence_id,
                    "content": text,
                    "emotion": emotion,
                    "audio_degraded": True,
                    "degraded_reason": "user_disabled"
                }))
                if not speaking_status_sent:
                    await send_queue.put((epoch, {
                        "type": "status",
                        "state": "speaking"
                    }))
                    speaking_status_sent = True
                sentence_queue.task_done()
                continue
            
            try:
                audio_bytes = await tts_task if tts_task else None
                if audio_bytes is None:
                    raise TTSServiceError("TTS synthesis returned None or was skipped")
            except Exception as e:
                # TTS degradation fallback
                if not isinstance(e, TTSServiceError) and not isinstance(e, asyncio.CancelledError):
                    e = TTSServiceError(str(e))
                if isinstance(e, asyncio.CancelledError):
                    if tts_task:
                        tts_task.cancel()
                    raise
                
                reason = "user_disabled" if not session.enable_tts else "service_error"
                await send_queue.put((epoch, {
                    "type": "text_chunk",
                    "sentence_id": sentence_id,
                    "content": text,
                    "emotion": emotion,
                    "audio_degraded": True,
                    "degraded_reason": reason
                }))
                if not speaking_status_sent:
                    await send_queue.put((epoch, {
                        "type": "status",
                        "state": "speaking"
                    }))
                    speaking_status_sent = True
                sentence_queue.task_done()
                continue
                
            # Check enable_tts again after await, before sending audio_chunk
            if not session.enable_tts:
                await send_queue.put((epoch, {
                    "type": "text_chunk",
                    "sentence_id": sentence_id,
                    "content": text,
                    "emotion": emotion,
                    "audio_degraded": True,
                    "degraded_reason": "user_disabled"
                }))
                if not speaking_status_sent:
                    await send_queue.put((epoch, {
                        "type": "status",
                        "state": "speaking"
                    }))
                    speaking_status_sent = True
                sentence_queue.task_done()
                continue

            # Successful TTS synthesis
            audio_base64 = base64.b64encode(audio_bytes).decode('utf-8')
            
            await send_queue.put((epoch, {
                "type": "text_chunk",
                "sentence_id": sentence_id,
                "content": text,
                "emotion": emotion
            }))
            
            if not speaking_status_sent:
                await send_queue.put((epoch, {
                    "type": "status",
                    "state": "speaking"
                }))
                speaking_status_sent = True
                
            await send_queue.put((epoch, {
                "type": "audio_chunk",
                "sentence_id": sentence_id,
                "data": audio_base64
            }))
            sentence_queue.task_done()

    # Main execution inside Processor
    try:
        # Initial status thinking
        await send_queue.put((epoch, {"type": "status", "state": "thinking"}))
        
        # Persist user message to SQLite
        await models.save_message(session.session_id, "user", user_msg)
        
        t1 = asyncio.create_task(stream_and_segment())
        t2 = asyncio.create_task(sender_task())
        
        try:
            await asyncio.gather(t1, t2)
        except Exception as e:
            t1.cancel()
            t2.cancel()
            await asyncio.gather(t1, t2, return_exceptions=True)
            raise e
            
        full_ja_text = "".join(full_ja_text_parts)
        
        if session.history_epoch == start_history_epoch:
            session.append_message({"role": "assistant", "content": full_ja_text})
            if full_ja_text.strip():
                await models.save_message(session.session_id, "assistant", full_ja_text)
        
        if full_ja_text.strip():
            translation = await deepseek_service.translate_to_zh(full_ja_text, api_key=session.api_key)
            await send_queue.put((epoch, {
                "type": "translation",
                "content": translation
            }))
            
        await send_queue.put((epoch, {"type": "status", "state": "done"}))
        
        # History compression threshold check
        if len(session.history) // 2 > config.HISTORY_COMPRESS_THRESHOLD and not session.compressing:
            session.compressing = True
            asyncio.create_task(compress_and_update_history(session.session_id, session.history))
            
    except asyncio.CancelledError:
        for task in tts_tasks:
            if not task.done():
                task.cancel()
        raise
    except Exception as e:
        print(f"[Processor Error] Session {session.session_id}: {e}")
        await send_queue.put((epoch, {"type": "error", "message": f"Server error: {str(e)}"}))
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
                if "system_prompt" in data:
                    session.system_prompt = data["system_prompt"]
                if "temperature" in data:
                    try:
                        temp = float(data["temperature"])
                        session.temperature = max(0.0, min(2.0, temp))
                    except (ValueError, TypeError):
                        pass
                if "enable_tts" in data:
                    session.enable_tts = bool(data["enable_tts"])
                    
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
                
            if frame_type == "config_update":
                if "api_key" in data:
                    await send_queue.put((session.current_epoch, {"type": "error", "message": "连接存活期间禁止更新 API Key"}))
                    continue
                    
                if "sovits_url" in data:
                    session.sovits_url = data["sovits_url"]
                if "system_prompt" in data:
                    session.system_prompt = data["system_prompt"]
                    session.history = []
                    session.history_epoch += 1
                    session.current_epoch += 1
                    if session.active_task and not session.active_task.done():
                        session.active_task.cancel()
                    # Push done status to indicate end of cancelled state
                    await send_queue.put((session.current_epoch, {"type": "status", "state": "done"}))
                if "temperature" in data:
                    try:
                        temp = float(data["temperature"])
                        session.temperature = max(0.0, min(2.0, temp))
                    except (ValueError, TypeError):
                        pass
                if "enable_tts" in data:
                    session.enable_tts = bool(data["enable_tts"])
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
