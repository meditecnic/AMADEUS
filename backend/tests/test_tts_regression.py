import pytest
import json
import asyncio
from unittest.mock import patch, MagicMock, AsyncMock
from app.services.tts_queue import TTSQueueManager, TTSServiceError, EMOTION_REF_MAP


@pytest.mark.asyncio
async def test_synthesize_async_returns_bytes(tmp_path, monkeypatch):
    monkeypatch.setenv("AMADEUS_DB_PATH", str(tmp_path / "test.db"))
    from app.db import init_db
    await init_db()

    tts = TTSQueueManager()
    tts.emotion_ref_map = {
        "neutral": {
            "ref_audio_path": "/fake/neutral.wav",
            "prompt_text": "test prompt"
        }
    }

    class FakeResponse:
        status_code = 200
        content = b"\x00\x01\x02WAV"

    mock_client = MagicMock()
    mock_client.is_closed = False
    mock_client.post = AsyncMock(return_value=FakeResponse())
    tts.client = mock_client

    result = await tts.synthesize_async("こんにちは", "neutral")
    assert result == b"\x00\x01\x02WAV"

    # Verify payload uses media_type, not format
    call_kwargs = mock_client.post.call_args
    assert call_kwargs is not None
    payload = call_kwargs[1].get("json") or call_kwargs[1].get("data") or {}
    assert "media_type" in payload
    assert payload["media_type"] == "wav"
    assert "format" not in payload
    assert "text_language" not in payload
    assert "refer_wav_path" not in payload
    assert "prompt_language" not in payload


@pytest.mark.asyncio
async def test_synthesize_async_raises_on_500(tmp_path, monkeypatch):
    monkeypatch.setenv("AMADEUS_DB_PATH", str(tmp_path / "test.db"))
    from app.db import init_db
    await init_db()

    tts = TTSQueueManager()
    tts.emotion_ref_map = {
        "neutral": {
            "ref_audio_path": "/fake/neutral.wav",
            "prompt_text": "test prompt"
        }
    }

    class FakeErrorResponse:
        status_code = 500

    mock_client = MagicMock()
    mock_client.is_closed = False
    mock_client.post = AsyncMock(return_value=FakeErrorResponse())
    mock_client.get = AsyncMock(return_value=FakeErrorResponse())
    tts.client = mock_client

    with pytest.raises(TTSServiceError) as exc:
        await tts.synthesize_async("テスト", "neutral")
    assert "synthesis failed" in str(exc.value).lower()


@pytest.mark.asyncio
async def test_synthesize_async_raises_on_missing_ref(tmp_path, monkeypatch):
    monkeypatch.setenv("AMADEUS_DB_PATH", str(tmp_path / "test.db"))
    from app.db import init_db
    await init_db()

    tts = TTSQueueManager()
    tts.emotion_ref_map = {
        "neutral": None
    }

    with pytest.raises(TTSServiceError) as exc:
        await tts.synthesize_async("テスト", "neutral")
    assert "reference audio" in str(exc.value).lower()


@pytest.mark.asyncio
async def test_synthesize_async_unknown_emotion_falls_to_config(tmp_path, monkeypatch):
    monkeypatch.setenv("AMADEUS_DB_PATH", str(tmp_path / "test.db"))
    from app.db import init_db
    await init_db()

    tts = TTSQueueManager()
    tts.emotion_ref_map = {"neutral": {"ref_audio_path": "/f/n.wav", "prompt_text": "p"}}

    with pytest.raises(TTSServiceError) as exc:
        await tts.synthesize_async("テスト", "nonexistent")
    assert "reference audio" in str(exc.value).lower()


def test_emotion_ref_map_built_9_emotions():
    """EMOTION_REF_MAP should always contain all 9 emotions, possibly as None."""
    expected = ("neutral", "tsundere", "embarrassed", "intellectual",
                "happy", "surprised", "annoyed", "disappointed", "sad")
    for emotion in expected:
        assert emotion in EMOTION_REF_MAP


def test_emotion_ref_map_count():
    """EMOTION_REF_MAP should contain exactly 9 emotions."""
    assert len(EMOTION_REF_MAP) == 9
