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
    """Missing or extra emotion keys change which reference audio can be selected."""
    expected = {"neutral", "tsundere", "embarrassed", "intellectual",
                "happy", "surprised", "annoyed", "disappointed", "sad"}
    assert set(EMOTION_REF_MAP) == expected


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


# ---------------------------------------------------------------------------
# TTS-CANONICAL-PRONUNCIATION-LEXICON-01: HTTP boundary conversion
# ---------------------------------------------------------------------------

LEXICON_SOURCE_SENTENCE = "牧瀬紅莉栖、鳳凰院凶真と岡部倫太郎。Amadeusと未来ガジェット研究所について話す。"
LEXICON_SPOKEN_TOKENS = ("マキセクリス", "ホウオウインキョウマ", "オカベリンタロウ", "アマデウス", "ミライガジェットケンキュウジョ")
LEXICON_CANONICAL_TOKENS = ("牧瀬紅莉栖", "鳳凰院凶真", "岡部倫太郎", "Amadeus", "未来ガジェット研究所")


@pytest.mark.asyncio
async def test_http_payload_text_uses_pronunciation_surface_direct_call():
    """_async_tts_call (shared by synthesize_async + queue) converts payload text only."""
    tts = TTSQueueManager()

    class FakeResponse:
        status_code = 200
        content = b"RIFF....WAVE"
        text = ""

    mock_client = MagicMock()
    mock_client.is_closed = False
    mock_client.post = AsyncMock(return_value=FakeResponse())
    tts.client = mock_client

    await tts._async_tts_call(
        LEXICON_SOURCE_SENTENCE,
        {"ref_audio_path": "/fake/neutral.wav", "prompt_text": "参照プロンプトはそのまま"},
    )

    payload = mock_client.post.await_args.kwargs["json"]
    for token in LEXICON_SPOKEN_TOKENS:
        assert token in payload["text"], token
    for token in LEXICON_CANONICAL_TOKENS:
        assert token not in payload["text"], token
    # Reference prompt text and generation params must stay untouched.
    assert payload["prompt_text"] == "参照プロンプトはそのまま"
    assert payload["text_lang"] == "ja"
    assert payload["repetition_penalty"] == 1.5


@pytest.mark.asyncio
async def test_quoted_chinese_from_user_uses_one_mixed_language_request():
    tts = TTSQueueManager()
    response = MagicMock(status_code=200, content=b"RIFF....WAVE")
    client = MagicMock()
    client.post = AsyncMock(return_value=response)
    tts.client = client
    ref = {"ref_audio_path": "/fake/neutral.wav", "prompt_text": "参照プロンプト"}

    for spoken, user, quoted in (
        ("中国語の「早上好」の音写でしょ。", "你说的是早上好的意思", "早上好"),
        ("その「喵」は余計よ。", "哈哈，猜对了喵", "喵"),
    ):
        await tts._async_tts_call(spoken, ref, quoted_source=user)
        payload = client.post.await_args.kwargs["json"]
        assert payload["text_lang"] == "auto"
        assert f"「\u200b{quoted}\u200b」" in payload["text"]

    await tts._async_tts_call("その「了解」は余計よ。", ref, quoted_source="今日は了解した")
    payload = client.post.await_args.kwargs["json"]
    assert payload["text_lang"] == "ja"
    assert "\u200b" not in payload["text"]


@pytest.mark.asyncio
async def test_queue_worker_converts_http_text_but_delivers_canonical_payload():
    """Queue path: sidecar sees spoken surface; client payload keeps canonical text."""
    tts = TTSQueueManager()
    tts.emotion_ref_map = {
        "neutral": {"ref_audio_path": "/fake/neutral.wav", "prompt_text": "prompt"}
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

    tts.reset_sequence("lexicon-queue", capture)
    await tts._synthesize_worker("lexicon-queue", 0, LEXICON_SOURCE_SENTENCE, "neutral")

    http_text = mock_client.post.await_args.kwargs["json"]["text"]
    for token in LEXICON_SPOKEN_TOKENS:
        assert token in http_text, token
    for token in LEXICON_CANONICAL_TOKENS:
        assert token not in http_text, token
    # The ordered-dispatch payload back to the client keeps the canonical text.
    assert delivered[0][1]["text"] == LEXICON_SOURCE_SENTENCE
    assert delivered[0][1]["error"] is None


@pytest.mark.asyncio
async def test_stream_pcm_payload_text_uses_pronunciation_surface():
    """stream_pcm builds its own payload and must not miss the conversion."""
    tts = TTSQueueManager()
    tts.emotion_ref_map = {
        "neutral": {"ref_audio_path": "/fake/neutral.wav", "prompt_text": "prompt"}
    }
    captured = {}

    class FakeStreamResponse:
        def raise_for_status(self):
            return None

        async def aiter_bytes(self):
            return
            yield b""

    class FakeStreamCM:
        async def __aenter__(self):
            return FakeStreamResponse()

        async def __aexit__(self, *args):
            return False

    def fake_stream(method, url, json=None, timeout=None):
        captured["json"] = json
        return FakeStreamCM()

    mock_client = MagicMock()
    mock_client.is_closed = False
    mock_client.stream = fake_stream
    tts.client = mock_client

    async for _ in tts.stream_pcm(LEXICON_SOURCE_SENTENCE, "neutral"):
        pass

    payload = captured["json"]
    for token in LEXICON_SPOKEN_TOKENS:
        assert token in payload["text"], token
    for token in LEXICON_CANONICAL_TOKENS:
        assert token not in payload["text"], token
    assert payload["streaming_mode"] == 2
    assert payload["prompt_text"] == "prompt"
