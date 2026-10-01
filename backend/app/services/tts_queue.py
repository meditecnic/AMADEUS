import os
import io
import wave
import struct
import re
import httpx
import asyncio
from app import config
from app.domain.segments import (
    AudioErrorCode,
    prepare_tts_text,
    render_tts_pronunciation_surface,
)
from pathlib import Path
from typing import Dict, List, Optional, Callable

class TTSServiceError(Exception):
    """Sanitized synthesis failure with a stable client-facing code."""

    def __init__(
        self,
        code: AudioErrorCode | str,
        message: str | None = None,
        *,
        retryable: bool = False,
        status_code: int | None = None,
        diagnostic: str | None = None,
    ) -> None:
        if isinstance(code, AudioErrorCode):
            self.code = code
            safe_message = message or f"TTS synthesis failed: {code.value}"
        else:
            self.code = AudioErrorCode.SYNTHESIS_FAILED
            safe_message = message or code
        self.retryable = retryable
        self.status_code = status_code
        self.diagnostic = diagnostic
        super().__init__(safe_message)

def _build_emotion_ref_map():
    """Build 9-emotion reference audio map from config, validating file existence.

    Reference clips are RMS-normalized (≈0.15, -16.5 dBFS) and peak-limited
    (-1 dBFS) for consistent TTS output, except the embarrassed clip which uses
    the original unprocessed recording for natural synthesis of short utterances.
    """
    entries = [
        ("neutral", config.REF_AUDIO_NEUTRAL, "あなたに会いに来たの、岡部倫太郎さん。あ、じゃなくて…"),
        ("tsundere", config.REF_AUDIO_TSUNDERE, "別に、確信があって言ったわけじゃない。単に…"),
        ("embarrassed", config.REF_AUDIO_EMBARRASSED, "別に確信があって言ったわけじゃない。単に…"),
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

# Production V2Pro settings recovered from the embarrassed-emotion tuning
# matrix. In particular, repetition_penalty=1.5 prevents the model from
# extending short boundary fragments into an audible stray mora.
V2PRO_GENERATION_PARAMS = {
    "top_k": 10,
    "top_p": 0.9,
    "temperature": 0.6,
    "speed_factor": 1.0,
    "repetition_penalty": 1.5,
    "text_split_method": "cut5",
    "batch_size": 1,
}

_QUOTED_SPAN_RE = re.compile(r"「[^「」\r\n]{1,32}」|『[^『』\r\n]{1,32}』|“[^“”\r\n]{1,32}”")
_KANA_RE = re.compile(r"[\u3040-\u30ff]")
_HAN_RE = re.compile(r"[\u3400-\u9fff\uf900-\ufaff]")


def tts_speech_surface(text: str, quoted_source: str | None = None) -> tuple[str, str]:
    """Give Chinese words quoted from this turn's user text a speech-only language boundary."""
    if not quoted_source or _KANA_RE.search(quoted_source) or not _HAN_RE.search(quoted_source):
        return render_tts_pronunciation_surface(text), "ja"

    parts = []
    cursor = 0
    mixed = False
    for match in _QUOTED_SPAN_RE.finditer(text):
        quote = match.group()
        inner = quote[1:-1]
        if not _HAN_RE.search(inner) or inner.strip() not in quoted_source:
            continue
        parts.append(render_tts_pronunciation_surface(text[cursor:match.start()]))
        # Auto detection needs a boundary to classify a one-character Chinese quote.
        parts.append(f"{quote[0]}\u200b{inner}\u200b{quote[-1]}")
        cursor = match.end()
        mixed = True
    if not mixed:
        return render_tts_pronunciation_surface(text), "ja"
    parts.append(render_tts_pronunciation_surface(text[cursor:]))
    return "".join(parts), "auto"


def _peak_limit_wav(audio_bytes: bytes, threshold: int = 32000, target_peak: int = 30000) -> bytes:
    """对 16-bit PCM WAV 做下行 peak-limit：仅当 peak > threshold 时衰减到 target_peak，不放大。"""
    try:
        with wave.open(io.BytesIO(audio_bytes), 'rb') as wf:
            nch = wf.getnchannels()
            sw = wf.getsampwidth()
            fr = wf.getframerate()
            n = wf.getnframes()
            raw = wf.readframes(n)
        if sw != 2:
            return audio_bytes
        samples = struct.unpack('<' + 'h' * n, raw)
        peak = 0
        for s in samples:
            a = s if s >= 0 else -s
            if a > peak:
                peak = a
        if peak <= threshold:
            return audio_bytes
        scale = target_peak / peak
        out = []
        for s in samples:
            v = int(round(s * scale))
            if v > 32767:
                v = 32767
            elif v < -32768:
                v = -32768
            out.append(v)
        new_raw = struct.pack('<' + 'h' * n, *out)
        buf = io.BytesIO()
        with wave.open(buf, 'wb') as wf:
            wf.setnchannels(nch)
            wf.setsampwidth(sw)
            wf.setframerate(fr)
            wf.writeframes(new_raw)
        return buf.getvalue()
    except Exception as e:
        print(f"[TTS peak-limit] skipped: {e}", flush=True)
        return audio_bytes


class TTSQueueManager:
    def __init__(self):
        self.sovits_url = config.SOVITS_URL
        self.timeout = config.TTS_TIMEOUT
        self.semaphore = asyncio.Semaphore(3)  # 并行度提升到 3（GPU 显存允许范围内）
        self.client = None                     # Async client managed by lifespan
        self.is_online = True                  # Kept for backward compatibility

        self.emotion_ref_map = EMOTION_REF_MAP

        # OLV 风格序号缓冲：并行合成、有序投递、失败静默兜底（按 session 隔离）
        self._payload_queue: Dict[str, dict] = {}
        self._pending_tasks: Dict[str, List[asyncio.Task]] = {}
        self._send_callback: Dict[str, Optional[Callable]] = {}
        self._next_send_seq: Dict[str, int] = {}
        self._sequence_counter: Dict[str, int] = {}

    async def init_client(self):
        """Initialize the HTTPX AsyncClient if it's closed or not created yet."""
        if self.client is None or self.client.is_closed:
            # GPT-SoVITS is a loopback-only sidecar.  In environments with an
            # HTTP(S)_PROXY configured, honoring proxy variables can route
            # 127.0.0.1 through that proxy and turn a healthy local API into a
            # misleading 502 response.
            self.client = httpx.AsyncClient(timeout=self.timeout, trust_env=False)

    async def close_client(self):
        """Close the HTTPX AsyncClient."""
        if self.client and not self.client.is_closed:
            await self.client.aclose()

    async def _async_tts_call(
        self, text: str, ref_config: dict, sovits_url: str = None,
        quoted_source: str | None = None,
    ) -> bytes:
        """Call the GPT-SoVITS V2Pro JSON contract exactly once."""
        speech_text, text_lang = tts_speech_surface(text, quoted_source)
        payload = {
            # Pronunciation lexicon applies only to the sidecar-bound target
            # text; callers keep the canonical surface everywhere else.
            "text": speech_text,
            "text_lang": text_lang,
            "ref_audio_path": ref_config["ref_audio_path"],
            "prompt_text": ref_config["prompt_text"],
            "prompt_lang": "ja",
            "media_type": "wav",
            **V2PRO_GENERATION_PARAMS,
        }

        base_url = sovits_url or self.sovits_url or "http://localhost:9880"
        url = base_url.rstrip("/") + "/tts"
        try:
            response = await self.client.post(url, json=payload)
        except asyncio.CancelledError:
            raise
        except (httpx.TimeoutException, asyncio.TimeoutError) as exc:
            raise TTSServiceError(
                AudioErrorCode.TIMEOUT,
                retryable=True,
                diagnostic="sidecar_timeout",
            ) from exc
        except (httpx.RequestError, OSError) as exc:
            raise TTSServiceError(
                AudioErrorCode.SERVICE_UNAVAILABLE,
                retryable=True,
                diagnostic="sidecar_network_error",
            ) from exc

        status = response.status_code
        if status == 200:
            return response.content

        body = str(getattr(response, "text", "")).lower()
        if status in {400, 422}:
            empty_hint = any(
                token in body
                for token in ("请输入有效文本", "empty text", "text is empty", "valid text")
            )
            code = AudioErrorCode.EMPTY_TEXT if empty_hint else AudioErrorCode.SYNTHESIS_FAILED
            raise TTSServiceError(code, status_code=status, diagnostic="request_validation")
        if status in {404, 405}:
            raise TTSServiceError(
                AudioErrorCode.SYNTHESIS_FAILED,
                status_code=status,
                diagnostic="endpoint_contract_mismatch",
            )
        if status == 429:
            raise TTSServiceError(
                AudioErrorCode.RATE_LIMITED,
                retryable=True,
                status_code=status,
                diagnostic="sidecar_rate_limited",
            )
        if status >= 500:
            raise TTSServiceError(
                AudioErrorCode.SERVICE_UNAVAILABLE,
                retryable=True,
                status_code=status,
                diagnostic="sidecar_server_error",
            )
        raise TTSServiceError(
            AudioErrorCode.SYNTHESIS_FAILED,
            status_code=status,
            diagnostic="sidecar_http_error",
        )

    async def synthesize_async(
        self, text: str, emotion_tag: str, sovits_url: str = None,
        quoted_source: str | None = None,
    ) -> bytes:
        """
        [DEPRECATED] 保留作为备用接口。
        新调用点请使用 synthesize_and_queue（并行合成 + 有序投递 + 静默兜底）。

        Synthesizes speech asynchronously using a semaphore to limit concurrency.
        Raises TTSServiceError on failure.
        """
        prepared = prepare_tts_text(text)
        if prepared.error is not None:
            raise TTSServiceError(prepared.error)

        ref_config = self.emotion_ref_map.get(emotion_tag)
        if ref_config is None:
            raise TTSServiceError(f"No valid reference audio configured for emotion '{emotion_tag}' — check AMADEUS_REF_{emotion_tag.upper()} env var")

        await self.init_client()

        async with self.semaphore:
            try:
                audio_bytes = await asyncio.wait_for(
                    self._async_tts_call(
                        prepared.text, ref_config, sovits_url=sovits_url,
                        quoted_source=quoted_source,
                    ),
                    timeout=self.timeout
                )
                return audio_bytes
            except asyncio.TimeoutError as exc:
                print(f"[TTS Timeout] Synthesis timed out ({self.timeout}s) for text (len={len(text)})")
                raise TTSServiceError(
                    AudioErrorCode.TIMEOUT,
                    retryable=True,
                    diagnostic="manager_timeout",
                ) from exc
            except asyncio.CancelledError:
                print(f"[TTS Cancelled] Task cancelled for text (len={len(text)})")
                raise
            except TTSServiceError:
                raise
            except Exception as exc:
                print(f"[TTS Error] Synthesis failed ({type(exc).__name__})")
                raise TTSServiceError(
                    AudioErrorCode.SYNTHESIS_FAILED,
                    diagnostic="unexpected_manager_error",
                ) from exc

    async def stream_pcm(
        self, text: str, emotion_tag: str, sovits_url: str = None,
        quoted_source: str | None = None,
    ):
        """Yield (audio_format, pcm_chunk) from GPT-SoVITS streaming_mode=2."""
        from app.services.audio_stream import WavStreamDemuxer
        prepared = prepare_tts_text(text)
        if prepared.error is not None:
            raise TTSServiceError(prepared.error)

        ref_config = self.emotion_ref_map.get(emotion_tag)
        if ref_config is None:
            raise TTSServiceError(f"No valid reference audio configured for emotion '{emotion_tag}'")
        await self.init_client()
        speech_text, text_lang = tts_speech_surface(prepared.text, quoted_source)
        payload = {
            # Same sidecar-only pronunciation surface as _async_tts_call.
            "text": speech_text,
            "text_lang": text_lang,
            "ref_audio_path": ref_config["ref_audio_path"],
            "prompt_text": ref_config["prompt_text"],
            "prompt_lang": "ja",
            "media_type": "wav",
            "streaming_mode": 2,
            **V2PRO_GENERATION_PARAMS,
        }
        demuxer = WavStreamDemuxer()
        url = (sovits_url or self.sovits_url or "http://127.0.0.1:9880").rstrip("/") + "/tts"
        async with self.semaphore:
            async with self.client.stream("POST", url, json=payload, timeout=self.timeout) as response:
                response.raise_for_status()
                async for chunk in response.aiter_bytes():
                    for pcm in demuxer.feed(chunk):
                        yield demuxer.format, pcm

    # ------------------------------------------------------------------
    # OLV 风格序号缓冲队列：并行合成、有序投递、失败静默兜底
    # ------------------------------------------------------------------
    def reset_sequence(self, session_id: str, send_callback):
        """每轮对话开始时调用，重置序号并注入发送回调。传入 None 可清空缓冲。"""
        self._sequence_counter[session_id] = 0
        self._next_send_seq[session_id] = 0
        self._payload_queue[session_id] = {}
        self._send_callback[session_id] = send_callback

    def cancel_pending(self, session_id: str):
        """取消所有在途合成任务（用于中断）。"""
        tasks = self._pending_tasks.pop(session_id, [])
        for t in tasks:
            if not t.done():
                t.cancel()
        self._payload_queue.pop(session_id, None)
        self._next_send_seq.pop(session_id, None)
        self._sequence_counter.pop(session_id, None)
        self._send_callback.pop(session_id, None)

    async def gather_pending(self, session_id: str):
        """等待指定 session 的所有在途合成任务完成。"""
        tasks = self._pending_tasks.get(session_id, [])
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def _next_seq(self, session_id: str) -> int:
        seq = self._sequence_counter.get(session_id, 0)
        self._sequence_counter[session_id] = seq + 1
        return seq

    async def queue_silent(self, session_id: str, text: str, emotion: str, reason: str) -> int:
        """提交一个静默占位（action_only / user_disabled），通过同一有序缓冲投递，保证顺序。"""
        seq = self._next_seq(session_id)
        await self._ordered_dispatch(session_id, seq, {"audio": None, "error": reason, "text": text, "emotion": emotion})
        return seq

    async def _ordered_dispatch(self, session_id: str, seq: int, payload):
        """将完成的结果放入缓冲，按序号顺序通过 callback 发送。"""
        if session_id not in self._payload_queue:
            self._payload_queue[session_id] = {}
        self._payload_queue[session_id][seq] = payload
        cb = self._send_callback.get(session_id)
        while self._next_send_seq.get(session_id, 0) in self._payload_queue.get(session_id, {}):
            item = self._payload_queue[session_id].pop(self._next_send_seq[session_id])
            if cb is not None:
                try:
                    await cb(self._next_send_seq[session_id], item)
                except Exception as e:
                    print(f"[TTS Dispatch] callback error seq={self._next_send_seq[session_id]}: {e}", flush=True)
            self._next_send_seq[session_id] = self._next_send_seq.get(session_id, 0) + 1

    async def synthesize_and_queue(self, session_id: str, text: str, emotion_tag: str, sovits_url: str = None) -> int:
        """提交合成任务，立即返回序号。完成后自动有序投递。失败时投递 silent payload。"""
        seq = self._next_seq(session_id)
        task = asyncio.create_task(self._synthesize_worker(session_id, seq, text, emotion_tag, sovits_url))
        self._pending_tasks.setdefault(session_id, []).append(task)
        task.add_done_callback(lambda t: self._pending_tasks.get(session_id, []).remove(t) if t in self._pending_tasks.get(session_id, []) else None)
        return seq

    async def _synthesize_worker(self, session_id: str, seq: int, text: str, emotion_tag: str, sovits_url: str = None):
        prepared = prepare_tts_text(text)
        if prepared.error is not None:
            await self._ordered_dispatch(session_id, seq, {
                "audio": None,
                "error": prepared.error.value,
                "text": text,
                "emotion": emotion_tag,
            })
            return
        ref_config = self.emotion_ref_map.get(emotion_tag)
        if ref_config is None:
            await self._ordered_dispatch(session_id, seq, {"audio": None, "error": f"no_ref:{emotion_tag}", "text": text, "emotion": emotion_tag})
            return
        await self.init_client()
        try:
            async with self.semaphore:
                audio_bytes = await asyncio.wait_for(
                    self._async_tts_call(prepared.text, ref_config, sovits_url=sovits_url),
                    timeout=self.timeout
                )
            audio_bytes = _peak_limit_wav(audio_bytes)
            await self._ordered_dispatch(session_id, seq, {"audio": audio_bytes, "error": None, "text": text, "emotion": emotion_tag})
        except asyncio.CancelledError:
            await self._ordered_dispatch(session_id, seq, {"audio": None, "error": "cancelled", "text": text, "emotion": emotion_tag})
            raise
        except TTSServiceError as exc:
            print(f"[TTS Error] seq={seq} failed: {exc.code.value}", flush=True)
            await self._ordered_dispatch(session_id, seq, {
                "audio": None,
                "error": exc.code.value,
                "text": text,
                "emotion": emotion_tag,
            })
        except Exception as exc:
            print(f"[TTS Error] seq={seq} failed: {type(exc).__name__}", flush=True)
            await self._ordered_dispatch(session_id, seq, {
                "audio": None,
                "error": AudioErrorCode.SYNTHESIS_FAILED.value,
                "text": text,
                "emotion": emotion_tag,
            })

# Singleton manager
tts_manager = TTSQueueManager()
