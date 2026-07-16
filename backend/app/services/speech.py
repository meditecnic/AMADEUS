"""Lazy SenseVoice adapter and lightweight PCM endpoint detection helpers."""

from __future__ import annotations

import asyncio
import os
import re
import tempfile
import wave
from array import array
from pathlib import Path


class SpeechService:
    EMOTION_PATTERN = re.compile(r"<(HAPPY|SAD|ANGRY|NEUTRAL|FEARFUL|DISGUSTED|SURPRISED)>", re.I)

    def __init__(self):
        self._model = None
        self._vad_model = None
        self.model_name = os.getenv("SENSEVOICE_MODEL", "iic/SenseVoiceSmall")

    @staticmethod
    def pcm_has_speech(pcm: bytes, threshold: float = 0.012) -> bool:
        if len(pcm) < 2:
            return False
        samples = array("h")
        samples.frombytes(pcm[: len(pcm) - len(pcm) % 2])
        if not samples:
            return False
        rms = (sum(float(sample) * sample for sample in samples) / len(samples)) ** 0.5 / 32768.0
        return rms >= threshold

    async def has_speech(self, pcm: bytes, sample_rate: int = 16000, threshold: float = 0.5) -> bool:
        """Use Silero VAD when available; retain RMS as a degraded fallback."""
        try:
            return await asyncio.to_thread(self._silero_has_speech, pcm, sample_rate, threshold)
        except (ImportError, RuntimeError, ValueError):
            return self.pcm_has_speech(pcm)

    def _silero_has_speech(self, pcm: bytes, sample_rate: int, threshold: float) -> bool:
        import torch
        from silero_vad import load_silero_vad
        if self._vad_model is None:
            self._vad_model = load_silero_vad()
        samples = array("h")
        samples.frombytes(pcm[: len(pcm) - len(pcm) % 2])
        if not samples:
            return False

        # Silero's JIT model accepts exactly 512 samples at 16 kHz (or 256 at
        # 8 kHz). Browser/WebSocket PCM chunks are arbitrary-sized, so feed
        # the model fixed-size windows and zero-pad the final window.
        frame_size = 512 if sample_rate == 16000 else 256 if sample_rate == 8000 else 0
        if not frame_size:
            return self.pcm_has_speech(pcm)
        self._vad_model.reset_states()
        values = list(samples)
        for offset in range(0, len(values), frame_size):
            frame = values[offset : offset + frame_size]
            if len(frame) < frame_size:
                frame.extend([0] * (frame_size - len(frame)))
            tensor = torch.tensor(frame, dtype=torch.float32) / 32768.0
            probability = float(self._vad_model(tensor, sample_rate).item())
            if probability >= threshold:
                return True
        return False

    async def transcribe_pcm(self, pcm: bytes, sample_rate: int = 16000, channels: int = 1) -> tuple[str, str, float]:
        return await asyncio.to_thread(self._transcribe_sync, pcm, sample_rate, channels)

    def _load(self):
        if self._model is None:
            from funasr import AutoModel
            self._model = AutoModel(model=self.model_name, trust_remote_code=True, device="cuda:0" if os.getenv("CUDA_VISIBLE_DEVICES", "") != "" else "cpu")
        return self._model

    def _transcribe_sync(self, pcm: bytes, sample_rate: int, channels: int) -> tuple[str, str, float]:
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as temp:
            path = Path(temp.name)
        try:
            with wave.open(str(path), "wb") as wav:
                wav.setnchannels(channels)
                wav.setsampwidth(2)
                wav.setframerate(sample_rate)
                wav.writeframes(pcm)
            result = self._load().generate(input=str(path), cache={}, language="auto", use_itn=True)
            raw = str(result[0].get("text", "")) if result else ""
            match = self.EMOTION_PATTERN.search(raw)
            emotion = match.group(1).upper() if match else "NEUTRAL"
            text = re.sub(r"<[^>]+>", "", raw).strip()
            return text, emotion, 0.85 if match else 0.5
        finally:
            path.unlink(missing_ok=True)

    def readiness(self) -> dict[str, object]:
        try:
            import funasr  # noqa: F401
            try:
                import silero_vad  # noqa: F401
                vad = "silero"
            except ImportError:
                vad = "rms_fallback"
            return {"ok": True, "model": self.model_name, "loaded": self._model is not None, "download_on_first_use": True, "vad": vad}
        except ImportError:
            return {"ok": False, "degraded": True, "model": self.model_name, "error": "funasr/SenseVoice is not installed"}


speech_service = SpeechService()
