import pytest
import json
import asyncio
from unittest.mock import patch, MagicMock, AsyncMock
from app.domain.segments import AudioErrorCode
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
async def test_v2pro_request_uses_production_anti_repetition_tuning():
    tts = TTSQueueManager()

    class FakeResponse:
        status_code = 200
        content = b"RIFF....WAVE"
        text = ""

    mock_client = MagicMock()
    mock_client.post = AsyncMock(return_value=FakeResponse())
    tts.client = mock_client

    await tts._async_tts_call(
        "ち、違うんだからね！",
        {"ref_audio_path": "/fake/embarrassed.wav", "prompt_text": "prompt"},
    )

    payload = mock_client.post.await_args.kwargs["json"]
    assert payload["repetition_penalty"] == 1.5
    assert payload["top_k"] == 10
    assert payload["top_p"] == 0.9
    assert payload["temperature"] == 0.6
    assert payload["speed_factor"] == 1.0
    assert payload["text_split_method"] == "cut5"
    assert payload["batch_size"] == 1


@pytest.mark.asyncio
async def test_queue_worker_prepares_text_and_delivers_real_audio_payload():
    tts = TTSQueueManager()
    tts.emotion_ref_map = {
        "embarrassed": {
            "ref_audio_path": "/fake/embarrassed.wav",
            "prompt_text": "prompt",
        }
    }

    class FakeResponse:
        status_code = 200
        content = b"RIFF....WAVE"
        text = ""

    mock_client = MagicMock()
    mock_client.is_closed = False
    mock_client.post = AsyncMock(return_value=FakeResponse())
    tts.client = mock_client
    delivered = []

    async def capture(seq, payload):
        delivered.append((seq, payload))

    tts.reset_sequence("queue-worker", capture)
    await tts._synthesize_worker(
        "queue-worker",
        0,
        " **ち、違うんだからね！** ",
        "embarrassed",
    )

    assert mock_client.post.await_args.kwargs["json"]["text"] == "ち、違うんだからね！"
    assert delivered[0][1]["audio"].startswith(b"RIFF")
    assert delivered[0][1]["error"] is None


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
async def test_json_validation_failure_never_falls_back_to_form_or_get():
    tts = TTSQueueManager()

    class FakeErrorResponse:
        status_code = 400
        text = '{"message":"tts failed","Exception":"请输入有效文本"}'
        content = text.encode("utf-8")

    mock_client = MagicMock()
    mock_client.is_closed = False
    mock_client.post = AsyncMock(return_value=FakeErrorResponse())
    mock_client.get = AsyncMock()
    tts.client = mock_client

    with pytest.raises(TTSServiceError) as exc:
        await tts._async_tts_call(
            "テスト",
            {"ref_audio_path": "/fake/neutral.wav", "prompt_text": "prompt"},
        )

    assert exc.value.code is AudioErrorCode.EMPTY_TEXT
    assert mock_client.post.await_count == 1
    assert mock_client.post.await_args.kwargs.get("json") is not None
    assert "data" not in mock_client.post.await_args.kwargs
    mock_client.get.assert_not_awaited()


@pytest.mark.asyncio
async def test_endpoint_contract_failure_is_sanitized_and_not_retried():
    tts = TTSQueueManager()

    class FakeErrorResponse:
        status_code = 405
        text = "internal sidecar path and stack trace"
        content = text.encode("utf-8")

    mock_client = MagicMock()
    mock_client.is_closed = False
    mock_client.post = AsyncMock(return_value=FakeErrorResponse())
    mock_client.get = AsyncMock()
    tts.client = mock_client

    with pytest.raises(TTSServiceError) as exc:
        await tts._async_tts_call(
            "テスト",
            {"ref_audio_path": "/fake/neutral.wav", "prompt_text": "prompt"},
        )

    assert exc.value.code is AudioErrorCode.SYNTHESIS_FAILED
    assert exc.value.diagnostic == "endpoint_contract_mismatch"
    assert "stack trace" not in str(exc.value)
    assert mock_client.post.await_count == 1
    mock_client.get.assert_not_awaited()


@pytest.mark.asyncio
async def test_empty_text_is_rejected_before_any_http_request():
    tts = TTSQueueManager()
    tts.emotion_ref_map = {
        "neutral": {"ref_audio_path": "/fake/neutral.wav", "prompt_text": "prompt"}
    }
    mock_client = MagicMock()
    mock_client.is_closed = False
    mock_client.post = AsyncMock()
    tts.client = mock_client

    with pytest.raises(TTSServiceError) as exc:
        await tts.synthesize_async("   ", "neutral")

    assert exc.value.code is AudioErrorCode.EMPTY_TEXT
    mock_client.post.assert_not_awaited()


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


def test_embarrassed_reference_prompt_matches_supplied_recording():
    entry = EMOTION_REF_MAP["embarrassed"]
    assert entry["prompt_text"] == "別に確信があって言ったわけじゃない。単に…"
    assert entry["ref_audio_path"].endswith("ref_embarrassed.wav")


@pytest.mark.asyncio
async def test_legacy_dispatch_merges_short_fragment_without_duplicating_history(
    monkeypatch,
):
    from app.routers import chat_ws

    class FakeTTSManager:
        def __init__(self):
            self.callback = None
            self.calls = []

        def reset_sequence(self, _key, callback):
            self.callback = callback

        async def synthesize_and_queue(self, _key, text, emotion, sovits_url=None):
            self.calls.append(text)
            await self.callback(0, {
                "audio": b"wav",
                "text": text,
                "emotion": emotion,
            })

        async def queue_silent(self, _key, text, emotion, reason):
            await self.callback(0, {
                "audio": None,
                "text": text,
                "emotion": emotion,
                "error": reason,
            })

        async def gather_pending(self, _key):
            return None

    class FakeSession:
        tts_key = "legacy-short-fragment"
        enable_tts = True
        sovits_url = "http://127.0.0.1:9880"
        client_type = "desktop"

    fake_tts = FakeTTSManager()
    monkeypatch.setattr(chat_ws, "tts_manager", fake_tts)

    text = "て。違うんだからね！"
    returned = await chat_ws._dispatch_tts_sentences(
        FakeSession(),
        text,
        "embarrassed",
        asyncio.Queue(),
        1,
    )

    assert returned == text
    assert fake_tts.calls == [text]
