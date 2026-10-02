"""Lazy local SenseVoice ONNX transcription and Silero endpoint detection."""

from __future__ import annotations

import asyncio
import hashlib
from array import array
import os
from pathlib import Path
import re
import shutil
import tarfile
import threading
import urllib.request

from app.db import get_data_root


MODEL_PACKAGE = 'sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17'
MODEL_URL = f'https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/{MODEL_PACKAGE}.tar.bz2'
VAD_URL = 'https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/silero_vad.onnx'
DOWNLOAD_SHA256 = {
    MODEL_URL: '7d1efa2138a65b0b488df37f8b89e3d91a60676e416f515b952358d83dfd347e',
    VAD_URL: '9e2449e1087496d8d4caba907f23e0bd3f78d91fa552479bb9c23ac09cbb1fd6',
}


def _download(url: str, destination: Path) -> None:
    expected = DOWNLOAD_SHA256[url]
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_suffix(destination.suffix + '.part')
    try:
        digest = hashlib.sha256()
        with urllib.request.urlopen(url, timeout=60) as response, partial.open('wb') as output:
            while chunk := response.read(1024 * 1024):
                output.write(chunk)
                digest.update(chunk)
        if digest.hexdigest() != expected:
            raise ValueError('speech model SHA-256 mismatch')
        partial.replace(destination)
    finally:
        partial.unlink(missing_ok=True)


class SpeechService:
    EMOTION_PATTERN = re.compile(r'<\|?(HAPPY|SAD|ANGRY|NEUTRAL|FEARFUL|DISGUSTED|SURPRISED)\|?>', re.I)

    def __init__(self):
        self._model = None
        self._lock = threading.RLock()
        self._model_override = os.getenv('SENSEVOICE_MODEL', '')
        self.model_name = self._model_override or MODEL_PACKAGE
        self._cache_dir = get_data_root() / 'models' / 'speech'

    @staticmethod
    def pcm_has_speech(pcm: bytes, threshold: float = 0.012) -> bool:
        if len(pcm) < 2:
            return False
        samples = array('h')
        samples.frombytes(pcm[:len(pcm) - len(pcm) % 2])
        rms = (sum(float(sample) * sample for sample in samples) / len(samples)) ** 0.5 / 32768.0
        return rms >= threshold

    async def has_speech(self, pcm: bytes, sample_rate: int = 16000, threshold: float = 0.5) -> bool:
        """Use local Silero VAD, falling back to RMS if its runtime is unavailable."""
        try:
            return await asyncio.to_thread(self._silero_has_speech, pcm, sample_rate, threshold)
        except (ImportError, OSError, RuntimeError, ValueError):
            return self.pcm_has_speech(pcm)

    def _silero_has_speech(self, pcm: bytes, sample_rate: int, threshold: float) -> bool:
        if not pcm or sample_rate != 16000:
            return self.pcm_has_speech(pcm)
        import numpy as np
        import sherpa_onnx

        with self._lock:
            override = os.getenv('AMADEUS_VAD_MODEL', '')
            path = Path(override).expanduser() if override else self._cache_dir / 'silero_vad.onnx'
            if not override and not path.is_file():
                _download(VAD_URL, path)
            if not path.is_file():
                raise FileNotFoundError(f'Silero VAD model not found: {path}')
            config = sherpa_onnx.VadModelConfig()
            config.silero_vad.model = str(path)
            config.silero_vad.threshold = threshold
            config.silero_vad.min_speech_duration = 0.0
            config.sample_rate = sample_rate
            vad = sherpa_onnx.VoiceActivityDetector(config, buffer_size_in_seconds=1)
            values = np.frombuffer(pcm[:len(pcm) - len(pcm) % 2], dtype='<i2').astype(np.float32) / 32768.0
            size = config.silero_vad.window_size
            for offset in range(0, len(values), size):
                frame = values[offset:offset + size]
                if len(frame) < size:
                    frame = np.pad(frame, (0, size - len(frame)))
                vad.accept_waveform(frame)
                if vad.is_speech_detected():
                    return True
            return False

    async def transcribe_pcm(self, pcm: bytes, sample_rate: int = 16000, channels: int = 1) -> tuple[str, str, float]:
        return await asyncio.to_thread(self._transcribe_sync, pcm, sample_rate, channels)

    def _model_paths(self) -> tuple[Path, Path]:
        if self._model_override:
            path = Path(self._model_override).expanduser()
            model = path if path.suffix == '.onnx' else path / 'model.int8.onnx'
            if path.is_dir() and not model.is_file():
                model = path / 'model.onnx'
            return model, Path(os.getenv('SENSEVOICE_TOKENS', str(model.parent / 'tokens.txt'))).expanduser()
        directory = self._cache_dir / MODEL_PACKAGE
        model, tokens = directory / 'model.int8.onnx', directory / 'tokens.txt'
        if not model.is_file() or not tokens.is_file():
            archive = self._cache_dir / f'{MODEL_PACKAGE}.tar.bz2'
            if not archive.is_file():
                _download(MODEL_URL, archive)
            directory.mkdir(parents=True, exist_ok=True)
            with tarfile.open(archive, 'r:bz2') as package:
                # Only named regular files are copied; archive paths are never extracted.
                for name in ('model.int8.onnx', 'tokens.txt', 'LICENSE', 'README.md'):
                    member = package.getmember(f'{MODEL_PACKAGE}/{name}')
                    if not member.isfile():
                        raise ValueError(f'Invalid SenseVoice model file: {name}')
                    destination = directory / name
                    partial = destination.with_suffix(destination.suffix + '.part')
                    try:
                        with package.extractfile(member) as source, partial.open('wb') as output:
                            shutil.copyfileobj(source, output)
                        partial.replace(destination)
                    finally:
                        partial.unlink(missing_ok=True)
        return model, tokens

    def _load(self):
        if self._model is None:
            import sherpa_onnx
            model, tokens = self._model_paths()
            for path in (model, tokens):
                if not path.is_file():
                    raise FileNotFoundError(f'SenseVoice model file not found: {path}')
            self._model = sherpa_onnx.OfflineRecognizer.from_sense_voice(
                model=str(model), tokens=str(tokens), num_threads=2, use_itn=True, provider='cpu')
        return self._model

    def _transcribe_sync(self, pcm: bytes, sample_rate: int, channels: int) -> tuple[str, str, float]:
        if sample_rate <= 0 or channels <= 0 or len(pcm) % (2 * channels):
            raise ValueError('Expected complete signed 16-bit PCM frames and a positive sample rate')
        if not pcm:
            return '', 'NEUTRAL', 0.5
        import numpy as np
        samples = np.frombuffer(pcm, dtype='<i2').astype(np.float32).reshape(-1, channels).mean(axis=1) / 32768.0
        with self._lock:
            model = self._load()
            stream = model.create_stream()
            stream.accept_waveform(sample_rate, samples)
            model.decode_stream(stream)
            raw = stream.result.text
            emotion_tag = getattr(stream.result, 'emotion', '')
        match = self.EMOTION_PATTERN.search(emotion_tag) or self.EMOTION_PATTERN.search(raw)
        emotion = match.group(1).upper() if match else 'NEUTRAL'
        return re.sub(r'<[^>]+>', '', raw).strip(), emotion, 0.85 if match else 0.5

    def readiness(self) -> dict[str, object]:
        try:
            import sherpa_onnx  # noqa: F401
            return {'ok': True, 'model': self.model_name, 'loaded': self._model is not None,
                    'download_on_first_use': True, 'vad': 'silero'}
        except (ImportError, OSError):
            return {'ok': False, 'degraded': True, 'model': self.model_name,
                    'error': 'sherpa-onnx/SenseVoice is not installed'}


speech_service = SpeechService()
