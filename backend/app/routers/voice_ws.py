from __future__ import annotations

import asyncio
import base64
import io
import time
import wave

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app import config
from app.db import normalize_worldline
from app.services.credentials import credential_store
from app.services.provider_runtime import provider_registry
from app.services.session_manager import session_coordinator
from app.services.soul_engine import soul_engine
from app.services.speech import speech_service

router = APIRouter()


async def _send_voice_payload(websocket: WebSocket, payload: dict) -> None:
    if payload.get("type") == "voice_audio_format":
        await websocket.send_json({"type": "audio.format", "channels": payload["channels"], "sample_rate": payload["sample_rate"], "sample_width": payload["sample_width"]})
        return
    if payload.get("type") == "voice_audio_pcm":
        await websocket.send_bytes(payload["data"])
        return
    if payload.get("type") == "text_chunk":
        await websocket.send_json({"type": "llm.text", "turn_id": payload.get("turn_id"), "content": payload.get("content", ""), "emotion": payload.get("emotion", "neutral")})
        return
    if payload.get("type") == "audio_chunk" and payload.get("data"):
        data = base64.b64decode(payload["data"])
        try:
            with wave.open(io.BytesIO(data), "rb") as wav:
                fmt = {"type": "audio.format", "channels": wav.getnchannels(), "sample_rate": wav.getframerate(), "sample_width": wav.getsampwidth()}
                pcm = wav.readframes(wav.getnframes())
            await websocket.send_json(fmt)
            await websocket.send_bytes(pcm)
        except wave.Error:
            await websocket.send_json({"type": "voice.error", "message": "TTS returned invalid WAV data"})
        return
    await websocket.send_json(payload)


@router.websocket("/ws/voice")
async def websocket_voice_endpoint(websocket: WebSocket):
    await websocket.accept()
    session_id = websocket.query_params.get("session_id", "default")
    worldline = normalize_worldline(websocket.query_params.get("worldline", "steins_gate"))
    from app.routers.chat_ws import SessionState, processor_loop, sessions, sessions_creation_lock
    async with sessions_creation_lock:
        session = sessions.setdefault(session_id, SessionState(session_id))
    session.worldline = worldline
    session.client_type = "voice"
    await session.load_from_db()
    await session_coordinator.acquire_lease(session_id, worldline, websocket)
    buffer = bytearray()
    sample_rate = 16000
    channels = 1
    last_speech_at: float | None = None
    authenticated = False
    send_queue: asyncio.Queue = asyncio.Queue()

    async def writer():
        while True:
            item = await send_queue.get()
            if item is None:
                return
            epoch, payload = item
            if epoch == session.current_epoch:
                await _send_voice_payload(websocket, payload)

    async def commit(reason: str):
        nonlocal buffer, last_speech_at
        pcm = bytes(buffer)
        buffer.clear()
        last_speech_at = None
        minimum_bytes = int(sample_rate * channels * 2 * config.VAD_MIN_SPEECH_MS / 1000)
        if len(pcm) < minimum_bytes:
            await websocket.send_json({"type": "voice.discarded", "reason": "too_short"})
            return
        await websocket.send_json({"type": "stt.status", "state": "transcribing", "reason": reason})
        transcription_history_epoch = session.history_epoch
        try:
            text, emotion, confidence = await speech_service.transcribe_pcm(pcm, sample_rate, channels)
        except Exception as exc:
            await websocket.send_json({"type": "voice.error", "stage": "stt", "message": str(exc), "degraded": True})
            return
        if session.erasure_pending or session.history_epoch != transcription_history_epoch:
            await websocket.send_json({'type':'voice.discarded', 'reason':'context_changed'})
            return
        await soul_engine.apply_speech_emotion(
            session_id,
            worldline,
            emotion,
            confidence,
            identity_mode=getattr(session, "identity_mode", "okabe"),
        )
        await websocket.send_json({"type": "stt.final", "content": text, "emotion": emotion, "confidence": confidence})
        if text and not session.is_busy and not session.erasure_pending:
            # id filled after chat processor save_message (Memory Ingest v2).
            session.append_message({"role": "user", "content": text, "id": None})
            session.is_busy = True
            session.active_task = asyncio.create_task(processor_loop(session, text, send_queue, session.current_epoch, session.history_epoch))

    async def vad_monitor():
        nonlocal last_speech_at
        while True:
            await asyncio.sleep(0.05)
            if buffer and last_speech_at is not None and (time.monotonic() - last_speech_at) * 1000 >= config.VAD_SILENCE_MS:
                await commit("vad_silence")

    writer_task = asyncio.create_task(writer())
    monitor_task = asyncio.create_task(vad_monitor())
    try:
        while True:
            message = await websocket.receive()
            if message.get("type") == "websocket.disconnect":
                break
            if message.get("bytes") is not None:
                if not authenticated:
                    await websocket.send_json({"type": "voice.error", "message": "authentication required"})
                    continue
                chunk = message["bytes"]
                buffer.extend(chunk)
                if await speech_service.has_speech(chunk, sample_rate, config.VAD_THRESHOLD):
                    last_speech_at = time.monotonic()
                if len(buffer) >= sample_rate * channels * 2 * config.VAD_MAX_UTTERANCE_S:
                    await commit("max_duration")
                continue
            data = message.get("text")
            if not data:
                continue
            import json
            frame = json.loads(data)
            kind = frame.get("type")
            if kind == "auth":
                api_key = credential_store.get(session.provider_id)
                provider_snapshot = provider_registry.snapshot(
                    session.provider_id,
                    session.model,
                )
                if provider_snapshot.credential_required and not api_key:
                    await websocket.send_json({"type": "voice.error", "message": "API key required"})
                    continue
                session.api_key = api_key or ""
                authenticated = True
                await websocket.send_json({"type": "voice.ready", "worldline": worldline, "vad_silence_ms": config.VAD_SILENCE_MS})
            elif kind == "voice.start":
                sample_rate = int(frame.get("sample_rate", 16000))
                channels = int(frame.get("channels", 1))
                buffer.clear()
                last_speech_at = None
            elif kind == "voice.commit":
                await commit("manual_commit")
            elif kind == "voice.cancel":
                buffer.clear()
                last_speech_at = None
                await websocket.send_json({"type": "voice.cancelled"})
            elif kind == "ping":
                await websocket.send_json({"type": "pong"})
    except WebSocketDisconnect:
        pass
    finally:
        writer_task.cancel()
        monitor_task.cancel()
        await asyncio.gather(writer_task, monitor_task, return_exceptions=True)
        await session_coordinator.release_lease(session_id, worldline, websocket)
