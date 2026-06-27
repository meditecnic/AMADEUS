import os
import httpx
import asyncio
from app import config

class TTSServiceError(Exception):
    """Custom exception raised when TTS synthesis fails or times out."""
    pass

class TTSQueueManager:
    def __init__(self):
        self.sovits_url = config.SOVITS_URL
        self.timeout = 10.0  # Strict 10s timeout
        self.semaphore = asyncio.Semaphore(1)  # Concurrency limit for GPU API
        self.client = None                     # Async client managed by lifespan

        # Initialize reference audio mapping
        self.emotion_ref_map = {
            "neutral": {
                "ref_audio_path": r"<LOCAL_PATH> 无奈.wav",
                "prompt_text": "全く。ノルアドレナリンが過剰分泌されてる気分よ"
            },
            "tsundere": {
                "ref_audio_path": r"<LOCAL_PATH> 害羞.wav",
                "prompt_text": "私が言ったのは、親子で仲が良くて、羨ましいってこと"
            },
            "embarrassed": {
                "ref_audio_path": r"<LOCAL_PATH> 害羞.wav",
                "prompt_text": "私が言ったのは、親子で仲が良くて、羨ましいってこと"
            },
            "intellectual": {
                "ref_audio_path": r"<LOCAL_PATH> 无奈.wav",
                "prompt_text": "全く。ノルアドレナリンが過剰分泌されてる気分よ"
            }
        }

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
            "text_language": "ja",
            "refer_wav_path": ref_config["ref_audio_path"],
            "prompt_language": "ja",
            "format": "wav"
        }

        base_url = sovits_url or self.sovits_url or "http://localhost:9880"
        urls_to_try = [
            base_url.rstrip("/"),
            base_url.rstrip("/") + "/tts",
            base_url.rstrip("/") + "/"
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
        Provides fallback and 10s timeout, raising TTSServiceError on failure.
        """
        ref_config = self.emotion_ref_map.get(emotion_tag)
        if not ref_config:
            ref_config = self.emotion_ref_map["neutral"]

        await self.init_client()

        async with self.semaphore:
            try:
                audio_bytes = await asyncio.wait_for(
                    self._async_tts_call(text, ref_config, sovits_url=sovits_url),
                    timeout=self.timeout
                )
                return audio_bytes
            except asyncio.TimeoutError:
                print(f"[TTS Timeout] Synthesis timed out (10s) for text: {text}")
                raise TTSServiceError(f"TTS synthesis timed out (10s) for text: {text}")
            except asyncio.CancelledError:
                print(f"[TTS Cancelled] Task cancelled for text: {text}")
                raise
            except Exception as e:
                print(f"[TTS Error] Synthesis failed: {e}")
                raise TTSServiceError(f"TTS synthesis failed: {e}")

# Singleton manager
tts_manager = TTSQueueManager()
