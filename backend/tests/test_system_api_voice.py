from __future__ import annotations

from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.db import reset_initialization_cache
from app.main import app
from app.routers.chat_ws import sessions


def test_worldline_memory_and_dependency_apis(tmp_path, monkeypatch):
    monkeypatch.delenv("AMADEUS_DB_PATH", raising=False)
    monkeypatch.setenv("AMADEUS_DATA_DIR", str(tmp_path))
    reset_initialization_cache()
    sessions.clear()
    with TestClient(app) as client:
        switched = client.post("/api/worldline/switch", json={"session_id": "okabe", "worldline": "beta"})
        assert switched.status_code == 200
        assert switched.json()["worldline"] == "beta"
        current = client.get("/api/worldline/current", params={"session_id": "okabe"}).json()
        assert current["worldline"] == "beta"
        memory = client.get("/api/memory/status", params={"session_id": "okabe", "worldline": "beta"}).json()
        assert memory["episodic_count"] == 0
        dependencies = client.get("/health/dependencies").json()
        assert dependencies["status"] == "ready"
        assert dependencies["dependencies"]["database"]["ok"] is True


def test_voice_manual_commit_bypasses_vad_wait(tmp_path, monkeypatch):
    monkeypatch.delenv("AMADEUS_DB_PATH", raising=False)
    monkeypatch.setenv("AMADEUS_DATA_DIR", str(tmp_path))
    reset_initialization_cache()
    sessions.clear()

    async def fake_processor(session, text, queue, epoch, history_epoch):
        session.is_busy = False
        await queue.put((epoch, {"type": "status", "dsk": "idle", "state": "done", "tts": "idle"}))

    with patch("app.services.speech.speech_service.transcribe_pcm", new=AsyncMock(return_value=("冈部，听得见。", "NEUTRAL", .9))), \
         patch("app.routers.chat_ws.processor_loop", side_effect=fake_processor):
        with TestClient(app) as client:
            with client.websocket_connect("/ws/voice?session_id=voice-test&worldline=steins_gate") as websocket:
                websocket.send_json({"type": "auth", "api_key": "sk-test-placeholder-for-voice"})
                assert websocket.receive_json()["type"] == "voice.ready"
                websocket.send_json({"type": "voice.start", "sample_rate": 16000, "channels": 1})
                websocket.send_bytes((10000).to_bytes(2, "little", signed=True) * 4000)
                websocket.send_json({"type": "voice.commit"})
                status = websocket.receive_json()
                final = websocket.receive_json()
                assert status == {"type": "stt.status", "state": "transcribing", "reason": "manual_commit"}
                assert final["type"] == "stt.final"
                assert final["content"] == "冈部，听得见。"
