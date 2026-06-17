import os
import httpx
import asyncio
from app import config

class TTSQueueManager:
    def __init__(self):
        self.sovits_url = config.SOVITS_URL
        self.timeout = 10.0  # Strict 10s timeout
        self.semaphore = asyncio.Semaphore(1)  # Concurrency limit for GPU API
        self.client = None                     # Async client managed by lifespan

        # Initialize reference audio mapping
        self.emotion_ref_map = {
            "neutral": {
                "ref_audio_path": os.path.normpath(os.path.join(config.ASSETS_DIR, "audio/hello.ogg")),
                "prompt_text": "こんにちは。"
            },
            "tsundere": {
                "ref_audio_path": os.path.normpath(os.path.join(config.ASSETS_DIR, "audio/pervert_confirmed.ogg")),
                "prompt_text": "変態確定ね。"
            },
            "embarrassed": {
                "ref_audio_path": os.path.normpath(os.path.join(config.ASSETS_DIR, "audio/senpai_please_dont_tell.ogg")),
                "prompt_text": "先輩、誰にも言わないでください……"
            },
            "intellectual": {
                "ref_audio_path": os.path.normpath(os.path.join(config.ASSETS_DIR, "audio/humans_software.ogg")),
                "prompt_text": "人間はソフトウェアみたいなものよ。"
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

    async def _async_tts_call(self, text: str, ref_config: dict) -> bytes:
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

        urls_to_try = [
            self.sovits_url.rstrip("/"),
            self.sovits_url.rstrip("/") + "/tts",
            self.sovits_url.rstrip("/") + "/"
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

    async def synthesize_async(self, text: str, emotion_tag: str) -> bytes:
        """
        Synthesizes speech asynchronously using a semaphore to limit concurrency.
        Provides fallback and 10s timeout, returning None on failure.
        """
        ref_config = self.emotion_ref_map.get(emotion_tag)
        if not ref_config:
            ref_config = self.emotion_ref_map["neutral"]

        await self.init_client()

        async with self.semaphore:
            try:
                audio_bytes = await asyncio.wait_for(
                    self._async_tts_call(text, ref_config),
                    timeout=self.timeout
                )
                return audio_bytes
            except asyncio.TimeoutError:
                print(f"[TTS Timeout] Synthesis timed out (10s) for text: {text}")
                return None
            except asyncio.CancelledError:
                print(f"[TTS Cancelled] Task cancelled for text: {text}")
                raise
            except Exception as e:
                print(f"[TTS Error] Synthesis failed: {e}")
                return None

# Singleton manager
tts_manager = TTSQueueManager()
