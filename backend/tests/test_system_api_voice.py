from __future__ import annotations

from unittest.mock import AsyncMock, patch
import pytest

from fastapi.testclient import TestClient

from app.db import reset_initialization_cache
from app.main import app
from app.routers.chat_ws import sessions


@pytest.mark.parametrize('frame', ['{', '[]', '{"type":"voice.start","sample_rate":-1}', '{"type":"voice.start","channels":3}'])
def test_voice_bad_frames_report_error_and_keep_connection(app_client, frame):
    with app_client.websocket_connect('/ws/voice?session_id=bad-voice-frame') as ws:
        ws.send_text(frame)
        assert ws.receive_json()['type'] == 'voice.error'
        ws.send_json({'type': 'ping'})
        assert ws.receive_json()['type'] == 'pong'


def test_worldline_memory_and_dependency_apis(tmp_path, monkeypatch):
    monkeypatch.delenv("AMADEUS_DB_PATH", raising=False)
    monkeypatch.setenv("AMADEUS_DATA_DIR", str(tmp_path))
    reset_initialization_cache()
    sessions.clear()
    with TestClient(app, base_url="http://localhost", headers={"host": "localhost", "origin": "http://localhost:1420"}) as client:
        switched = client.post("/api/worldline/switch", json={"session_id": "okabe", "worldline": "beta"})
        assert switched.status_code == 200
        assert switched.json()["worldline"] == "beta"
        current = client.get("/api/worldline/current", params={"session_id": "okabe"}).json()
        assert current["worldline"] == "beta"
        memory = client.get("/api/memory/status", params={"session_id": "okabe", "worldline": "beta"}).json()
        assert memory["episodic_count"] == 0
        dependencies = client.get("/health/dependencies").json()
        expected_status = (
            "ready" if dependencies["dependencies"]["tts"].get("ok") else "degraded"
        )
        assert dependencies["status"] == expected_status
        assert dependencies["dependencies"]["database"]["ok"] is True


def test_dependency_health_is_degraded_when_tts_is_explicitly_unavailable(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("AMADEUS_DATA_DIR", str(tmp_path))
    reset_initialization_cache()
    from app.services.tts_sidecar import sidecar_supervisor

    async def unavailable_tts():
        return {
            "ok": False,
            "status": "degraded",
            "url": None,
            "managed": False,
            "error": "no endpoint",
        }

    monkeypatch.setattr(sidecar_supervisor, "health", unavailable_tts)
    with TestClient(app, base_url="http://localhost", headers={"host": "localhost", "origin": "http://localhost:1420"}) as client:
        dependencies = client.get("/health/dependencies").json()

    assert dependencies["status"] == "degraded"
    assert dependencies["dependencies"]["database"]["ok"] is True
    assert dependencies["dependencies"]["soul"]["ok"] is True


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
        with TestClient(app, base_url="http://localhost", headers={"host": "localhost", "origin": "http://localhost:1420"}) as client:
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
