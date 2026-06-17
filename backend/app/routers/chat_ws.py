import re
import base64
import asyncio
import starlette
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from app import config
from app.services.deepseek import DeepSeekService
from app.services.tts_queue import tts_manager

router = APIRouter()
deepseek_service = DeepSeekService()

# In-memory session store: { session_id: { "history": [], "memory_summary": "", "lock": asyncio.Lock() } }
sessions = {}
sessions_creation_lock = asyncio.Lock()

# Regex patterns
EMO_TAG_PATTERN = re.compile(r'^\[EMO:([a-zA-Z0-9_]+)\]\s*')
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

def parse_sentence_emotion(sentence: str, last_emotion: str) -> tuple:
    """
    Extract the [EMO:xxx] tag from the sentence beginning, stripping it, 
    inheriting last emotion if missing, and defaulting to 'neutral' if unsupported.
    """
    clean_sentence = sentence.strip()
    if not clean_sentence:
        return "", last_emotion, last_emotion
    
    match = EMO_TAG_PATTERN.match(clean_sentence)
    if match:
        emotion = match.group(1)
        text = EMO_TAG_PATTERN.sub('', clean_sentence)
    else:
        emotion = last_emotion
        text = clean_sentence
    
    text = text.strip()
    if emotion not in ALLOWED_EMOTIONS:
        emotion = "neutral"
        
    return text, emotion, emotion

async def compress_and_update_history(session_id: str, current_history: list):
    """
    Asynchronously compress older conversation history and update session state safely.
    Keeps the last 3 rounds (6 messages) of history.
    Uses slice-based updating history[N:] to preserve any new messages added during the compression network call.
    """
    keep_count = 6
    if len(current_history) <= keep_count:
        session_data = sessions.get(session_id)
        if session_data:
            async with session_data["lock"]:
                session_data["compressing"] = False
        return
        
    to_compress = current_history[:-keep_count]
    N = len(to_compress)
    
    print(f"[History Compression] Session {session_id}: Compressing {N} messages...")
    new_summary = None
    try:
        new_summary = await deepseek_service.compress_history(to_compress)
        if new_summary:
            session_data = sessions.get(session_id)
            if session_data:
                async with session_data["lock"]:
                    session_data["memory_summary"] = new_summary
                    if len(session_data["history"]) >= N:
                        session_data["history"] = session_data["history"][N:]
                    else:
                        session_data["history"] = []
                    print(f"[History Compression] Session {session_id}: Compression successful. Summary: {new_summary}")
    except Exception as e:
        print(f"[History Compression Error] Session {session_id}: {e}")
    finally:
        session_data = sessions.get(session_id)
        if session_data:
            async with session_data["lock"]:
                session_data["compressing"] = False

@router.websocket("/ws/chat")
async def websocket_chat_endpoint(websocket: WebSocket):
    # 1. Handshake Origin Check (CSWSH Defense)
    origin = websocket.headers.get("origin")
    allowed_origins = [
        "http://localhost",
        f"http://localhost:{config.FRONTEND_PORT}",
        "http://127.0.0.1",
        f"http://127.0.0.1:{config.FRONTEND_PORT}",
    ]
    if origin and origin not in allowed_origins:
        await websocket.close(code=1008)  # Policy Violation
        return

    await websocket.accept()
    
    # 2. Thread-safe retrieval or initialization of session data
    session_id = websocket.query_params.get("session_id", "default")
    async with sessions_creation_lock:
        if session_id not in sessions:
            sessions[session_id] = {
                "history": [],
                "memory_summary": "",
                "lock": asyncio.Lock(),
                "compressing": False
            }
        
    session_data = sessions[session_id]
    active_tts_tasks = []
    
    try:
        while True:
            # Expected incoming JSON format: { "message": "ユーザーの入力" }
            try:
                data = await websocket.receive_json()
            except WebSocketDisconnect:
                raise
            except Exception as e:
                await websocket.send_json({"type": "error", "message": f"Invalid input format: {str(e)}"})
                continue
                
            user_msg = data.get("message", "").strip()
            if not user_msg:
                continue
                
            # Clean up completed tasks from previous rounds
            active_tts_tasks = [t for t in active_tts_tasks if not t.done()]
                
            # Acquire pessimistic lock per session to prevent race conditions during interaction
            async with session_data["lock"]:
                active_history = session_data["history"]
                active_history.append({"role": "user", "content": user_msg})
                
                # Send initial 'thinking' status
                await websocket.send_json({"type": "status", "state": "thinking"})
                
                # Setup pipelines and concurrency
                sentence_queue = asyncio.Queue()
                sentence_id_counter = 0
                full_ja_text_parts = []
                speaking_status_sent = False
                
                # Read latest memory summary
                memory_summary = session_data["memory_summary"]

                async def stream_and_segment():
                    nonlocal sentence_id_counter
                    buffer = ""
                    last_emotion = "neutral"
                    
                    try:
                        async for chunk in deepseek_service.get_chat_stream(active_history, memory_summary):
                            # Proactively abort if client disconnects
                            if websocket.client_state == starlette.websockets.WebSocketState.DISCONNECTED:
                                break
                            
                            buffer += chunk
                            sentences, buffer = extract_sentences_from_buffer(buffer, is_end=False)
                            for s in sentences:
                                text, emotion, last_emotion = parse_sentence_emotion(s, last_emotion)
                                if not text or not is_meaningful_sentence(text):
                                    continue
                                full_ja_text_parts.append(text)
                                
                                # Launch TTS task immediately in background
                                tts_task = asyncio.create_task(tts_manager.synthesize_async(text, emotion))
                                active_tts_tasks.append(tts_task)
                                await sentence_queue.put({
                                    "sentence_id": sentence_id_counter,
                                    "text": text,
                                    "emotion": emotion,
                                    "tts_task": tts_task
                                })
                                sentence_id_counter += 1
                                
                        if websocket.client_state != starlette.websockets.WebSocketState.DISCONNECTED:
                            # Clean up remaining buffer
                            sentences, _ = extract_sentences_from_buffer(buffer, is_end=True)
                            for s in sentences:
                                text, emotion, last_emotion = parse_sentence_emotion(s, last_emotion)
                                if not text or not is_meaningful_sentence(text):
                                    continue
                                full_ja_text_parts.append(text)
                                
                                tts_task = asyncio.create_task(tts_manager.synthesize_async(text, emotion))
                                active_tts_tasks.append(tts_task)
                                await sentence_queue.put({
                                    "sentence_id": sentence_id_counter,
                                    "text": text,
                                    "emotion": emotion,
                                    "tts_task": tts_task
                                })
                                sentence_id_counter += 1
                    except asyncio.CancelledError:
                        raise
                    except Exception as e:
                        # Put error signal/exception to queue so sender knows it failed
                        await sentence_queue.put(e)
                        return
                        
                    # Sentinel to end consumer loop
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
                        
                        # Wait for TTS inference
                        try:
                            audio_bytes = await tts_task
                        except asyncio.CancelledError:
                            tts_task.cancel()  # Propagate cancellation to the TTS coroutine
                            raise
                        
                        if audio_bytes is not None:
                            # Successful TTS: encode to base64
                            audio_base64 = base64.b64encode(audio_bytes).decode('utf-8')
                            
                            # 1. Send text_chunk
                            await websocket.send_json({
                                "type": "text_chunk",
                                "sentence_id": sentence_id,
                                "content": text,
                                "emotion": emotion
                            })
                            
                            # 2. Update status: speaking before sending first audio_chunk
                            if not speaking_status_sent:
                                await websocket.send_json({
                                    "type": "status",
                                    "state": "speaking"
                                })
                                speaking_status_sent = True
                                
                            # 3. Send audio_chunk
                            await websocket.send_json({
                                "type": "audio_chunk",
                                "sentence_id": sentence_id,
                                "data": audio_base64
                            })
                        else:
                            # Degraded TTS: send text_chunk with audio_degraded = True, skip audio_chunk
                            await websocket.send_json({
                                "type": "text_chunk",
                                "sentence_id": sentence_id,
                                "content": text,
                                "emotion": emotion,
                                "audio_degraded": True
                            })
                            
                            # Update status: speaking on first text_chunk if degraded
                            if not speaking_status_sent:
                                await websocket.send_json({
                                    "type": "status",
                                    "state": "speaking"
                                })
                                speaking_status_sent = True
                                
                        sentence_queue.task_done()

                # Start streaming and sending concurrently
                t1 = asyncio.create_task(stream_and_segment())
                t2 = asyncio.create_task(sender_task())
                
                try:
                    await asyncio.gather(t1, t2)
                except Exception as e:
                    t1.cancel()
                    t2.cancel()
                    # Wait for tasks to handle cancellation and release resources
                    await asyncio.gather(t1, t2, return_exceptions=True)
                    raise e
                
                # Build full Japanese clean text
                full_ja_text = "".join(full_ja_text_parts)
                
                # Append response to history
                active_history.append({"role": "assistant", "content": full_ja_text})
                
                # Run translation and send
                if full_ja_text.strip():
                    translation = await deepseek_service.translate_to_zh(full_ja_text)
                    await websocket.send_json({
                        "type": "translation",
                        "content": translation
                    })
                
                # Finally send 'done' status
                await websocket.send_json({"type": "status", "state": "done"})
                
                # Trigger background history compression if threshold exceeded
                if len(active_history) // 2 > config.HISTORY_COMPRESS_THRESHOLD and not session_data.get("compressing"):
                    session_data["compressing"] = True
                    asyncio.create_task(compress_and_update_history(session_id, active_history))
                    
    except WebSocketDisconnect:
        print(f"[WebSocket] Connection disconnected for session {session_id}")
    except Exception as e:
        print(f"[WebSocket Error] Exception: {e}")
        try:
            await websocket.send_json({"type": "error", "message": f"Server error: {str(e)}"})
        except Exception:
            pass
    finally:
        # Cancel all unfinished active TTS tasks when the client disconnects or an error occurs
        for task in active_tts_tasks:
            if not task.done():
                task.cancel()
