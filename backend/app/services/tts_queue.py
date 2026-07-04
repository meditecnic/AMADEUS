import os
import httpx
import asyncio
from app import config
from pathlib import Path

class TTSServiceError(Exception):
    """Custom exception raised when TTS synthesis fails or times out."""
    pass

def _build_emotion_ref_map():
    """Build 9-emotion reference audio map from config, validating file existence.

    All reference clips are RMS-normalized (≈0.15, -16.5 dBFS) and peak-limited
    (-1 dBFS) for consistent TTS output across emotions.
    """
    entries = [
        ("neutral", config.REF_AUDIO_NEUTRAL, "あなたに会いに来たの、岡部倫太郎さん。あ、じゃなくて…"),
        ("tsundere", config.REF_AUDIO_TSUNDERE, "別に、確信があって言ったわけじゃない。単に…"),
        ("embarrassed", config.REF_AUDIO_EMBARRASSED, "私が言ったのは、親子で仲が良くて羨ましいなって…"),
        ("intellectual", config.REF_AUDIO_INTELLECTUAL, "超重力による無限圧縮、およびカーブラックホール内の特異点通過に耐えられなかったと思われる。"),
        ("happy", config.REF_AUDIO_HAPPY, "大好きな鳳凰院凶真からお前は男だったと言われて…"),
        ("surprised", config.REF_AUDIO_SURPRISED, "そうなのかな… 携帯の電波なんて…"),
        ("annoyed", config.REF_AUDIO_ANNOYED, "私は、あんたの言葉が冗談なのか本気なのか見極められるほど付き合いが長くない。"),
        ("disappointed", config.REF_AUDIO_DISAPPOINTED, "まったく… ノルアドレナリンが過剰分泌されてる気分よ。"),
        ("sad", config.REF_AUDIO_SAD, "あんたが死んだら、まゆりも助けられなくなると思いなさい。"),
    ]
    ref_map = {}
    for emotion, path, prompt_text in entries:
        if not Path(path).exists():
            print(f"[WARN] TTS reference audio not found for '{emotion}': {path}")
            print(f"[WARN] Set AMADEUS_REF_{emotion.upper()} env var to a valid WAV file path")
            ref_map[emotion] = None
        else:
            ref_map[emotion] = {
                "ref_audio_path": path,
                "prompt_text": prompt_text,
            }
    return ref_map

EMOTION_REF_MAP = _build_emotion_ref_map()


class TTSQueueManager:
    def __init__(self):
        self.sovits_url = config.SOVITS_URL
        self.timeout = config.TTS_TIMEOUT
        self.semaphore = asyncio.Semaphore(1)  # Concurrency limit for GPU API
        self.client = None                     # Async client managed by lifespan
        self.is_online = True                  # Kept for backward compatibility

        self.emotion_ref_map = EMOTION_REF_MAP

    async def init_client(self):
        """Initialize the HTTPX AsyncClient if it's closed or not created yet."""
        if self.client is None or self.client.is_closed:
            self.client = httpx.AsyncClient(timeout=self.timeout)

    async def close_client(self):
        """Close the HTTPX AsyncClient."""
        if self.client and not self.client.is_closed:
            await self.client.aclose()

    async def _async_tts_call(self, text: str, ref_config: dict, sovits_url: str = None) -> bytes:
        """
        Asynchronous HTTP request to GPT-SoVITS API.
        Attempts POST with JSON, POST Form, and GET with Query parameters.
        """
        payload = {
            "text": text,
            "text_lang": "ja",
            "ref_audio_path": ref_config["ref_audio_path"],
            "prompt_text": ref_config["prompt_text"],
            "prompt_lang": "ja",
            "media_type": "wav",
            "top_k": 5,
            "top_p": 0.8,
            "temperature": 0.4,
            "speed": 1.0,
        }

        base_url = sovits_url or self.sovits_url or "http://localhost:9880"
        urls_to_try = [
            base_url.rstrip("/") + "/tts",
        ]

        last_exception = None
        for url in urls_to_try:
            try:
                # 1. Try POST JSON
                response = await self.client.post(url, json=payload)
                if response.status_code == 200:
                    return response.content

                # 2. Try POST Form
                response = await self.client.post(url, data=payload)
                if response.status_code == 200:
                    return response.content

                # 3. Try GET Query parameters
                response = await self.client.get(url, params=payload)
                if response.status_code == 200:
                    return response.content
            except asyncio.CancelledError:
                raise  # Always raise CancelledError upward to stop execution immediately
            except Exception as e:
                last_exception = e
                continue

        if last_exception:
            raise last_exception
        raise RuntimeError("GPT-SoVITS API returned non-200 status on all endpoints")

    async def synthesize_async(self, text: str, emotion_tag: str, sovits_url: str = None) -> bytes:
        """
        Synthesizes speech asynchronously using a semaphore to limit concurrency.
        Raises TTSServiceError on failure.
        """
        ref_config = self.emotion_ref_map.get(emotion_tag)
        if ref_config is None:
            raise TTSServiceError(f"No valid reference audio configured for emotion '{emotion_tag}' — check AMADEUS_REF_{emotion_tag.upper()} env var")

        await self.init_client()

        async with self.semaphore:
            try:
                audio_bytes = await asyncio.wait_for(
                    self._async_tts_call(text, ref_config, sovits_url=sovits_url),
                    timeout=self.timeout
                )
                return audio_bytes
            except asyncio.TimeoutError:
                print(f"[TTS Timeout] Synthesis timed out ({self.timeout}s) for text (len={len(text)})")
                raise TTSServiceError(f"TTS synthesis timed out ({self.timeout}s) for text (len={len(text)})")
            except asyncio.CancelledError:
                print(f"[TTS Cancelled] Task cancelled for text (len={len(text)})")
                raise
            except Exception as e:
                print(f"[TTS Error] Synthesis failed: {e}")
                raise TTSServiceError(f"TTS synthesis failed: {e}")

# Singleton manager
tts_manager = TTSQueueManager()
