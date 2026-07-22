"""Incremental sentence boundaries and ordered per-turn segment processing."""

from __future__ import annotations

import asyncio
import inspect
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Awaitable, Callable, Iterable


class AudioErrorCode(StrEnum):
    EMPTY_TEXT = "empty_text"
    ACTION_ONLY = "action_only"
    TOO_SHORT = "too_short"
    USER_DISABLED = "user_disabled"
    VOICE_STOPPED = "voice_stopped"
    TIMEOUT = "timeout"
    RATE_LIMITED = "rate_limited"
    SERVICE_UNAVAILABLE = "service_unavailable"
    SYNTHESIS_FAILED = "synthesis_failed"


@dataclass(frozen=True, slots=True)
class PreparedTTSText:
    text: str
    error: AudioErrorCode | None = None


_NON_SPEECH_RE = re.compile(
    r"[\s。！\uff01？\uff1f!?,.\uff0c\u3001\"'`「」『』()（）\-—_+=\[\]{}<>#~*]"
)


def clean_text_for_tts(text: str) -> str:
    """Remove stage directions while preserving the spoken Japanese text."""
    cleaned = re.sub(r"\(.*?\)", "", text)
    cleaned = re.sub(r"（.*?）", "", cleaned)
    cleaned = re.sub(r"\[.*?\]", "", cleaned)
    cleaned = re.sub(r"\*.*?\*", "", cleaned)
    return cleaned.strip()


def meaningful_character_count(text: str) -> int:
    return len(_NON_SPEECH_RE.sub("", text))


def is_meaningful_sentence(text: str) -> bool:
    return meaningful_character_count(text) > 0


def prepare_tts_text(text: str) -> PreparedTTSText:
    if not text or not text.strip():
        return PreparedTTSText("", AudioErrorCode.EMPTY_TEXT)
    cleaned = clean_text_for_tts(text)
    if not cleaned or not is_meaningful_sentence(cleaned):
        return PreparedTTSText(cleaned, AudioErrorCode.ACTION_ONLY)
    if meaningful_character_count(cleaned) < 2:
        return PreparedTTSText(cleaned, AudioErrorCode.TOO_SHORT)
    return PreparedTTSText(cleaned)


@dataclass(frozen=True, slots=True)
class Segment:
    id: int
    text: str
    emotion: str = "neutral"


@dataclass(frozen=True, slots=True)
class TurnIdentity:
    conversation_id: str
    turn_id: str
    generation: int


class TransientSegmentError(RuntimeError):
    """A segment operation that is safe to retry within the same turn."""


class _VoiceStopped(RuntimeError):
    pass


class IncrementalJapaneseSegmenter:
    _OPEN_TO_CLOSE = {
        "「": "」",
        "『": "』",
        "（": "）",
        "(": ")",
        "【": "】",
        "[": "]",
        "“": "”",
        "‘": "’",
    }
    _CLOSERS = set(_OPEN_TO_CLOSE.values())
    _TERMINATORS = set("。！？!?\n")

    def __init__(self) -> None:
        self._pending = ""
        self._pending_emotions: list[str] = []
        self._short_candidate: tuple[str, str] | None = None
        self._next_id = 0

    @property
    def pending(self) -> str:
        return self._pending

    def feed(self, chunk: str, emotion: str = "neutral") -> list[Segment]:
        if chunk:
            self._pending += chunk
            self._pending_emotions.extend([emotion] * len(chunk))
        return self._drain(final=False)

    def finish(self) -> list[Segment]:
        segments = self._drain(final=True)
        self._pending = ""
        self._pending_emotions = []
        return segments

    def _drain(self, *, final: bool) -> list[Segment]:
        emitted: list[Segment] = []
        while self._pending:
            boundary = self._find_boundary(self._pending)
            if boundary is None:
                break
            raw = self._pending[:boundary]
            text = raw.strip()
            leading = len(raw) - len(raw.lstrip())
            emotion = (
                self._pending_emotions[leading]
                if text and leading < len(self._pending_emotions)
                else "neutral"
            )
            self._pending = self._pending[boundary:]
            self._pending_emotions = self._pending_emotions[boundary:]
            if text:
                self._accept_candidate(emitted, text, emotion)

        if final:
            raw_tail = self._pending
            tail = raw_tail.strip()
            leading = len(raw_tail) - len(raw_tail.lstrip())
            emotion = (
                self._pending_emotions[leading]
                if tail and leading < len(self._pending_emotions)
                else "neutral"
            )
            self._pending = ""
            self._pending_emotions = []
            if tail:
                self._accept_candidate(emitted, tail, emotion)
            if self._short_candidate is not None:
                short_text, short_emotion = self._short_candidate
                self._short_candidate = None
                emitted.append(Segment(self._next_id, short_text, short_emotion))
                self._next_id += 1
        return emitted

    def _accept_candidate(
        self,
        emitted: list[Segment],
        text: str,
        emotion: str,
    ) -> None:
        if self._short_candidate is not None:
            short_text, short_emotion = self._short_candidate
            self._short_candidate = None
            text = short_text + text
            emotion = short_emotion
        elif prepare_tts_text(text).error is AudioErrorCode.TOO_SHORT:
            self._short_candidate = (text, emotion)
            return
        emitted.append(Segment(self._next_id, text, emotion))
        self._next_id += 1

    def _find_boundary(self, text: str) -> int | None:
        stack: list[str] = []
        i = 0
        while i < len(text):
            char = text[i]
            expected_close = self._OPEN_TO_CLOSE.get(char)
            if expected_close:
                stack.append(expected_close)
                i += 1
                continue
            if char in self._CLOSERS:
                if stack and stack[-1] == char:
                    stack.pop()
                i += 1
                continue

            end = None
            if char in self._TERMINATORS:
                end = i + 1
                while end < len(text) and text[end] in self._TERMINATORS:
                    end += 1
            elif char == "…":
                end = i
                while end < len(text) and text[end] == "…":
                    end += 1
                if end - i < 2:
                    i = end
                    continue
            elif char == ".":
                end = i
                while end < len(text) and text[end] == ".":
                    end += 1
                if end - i < 3:
                    i = end
                    continue

            if end is None:
                i += 1
                continue

            remaining_stack = list(stack)
            while end < len(text) and text[end] in self._CLOSERS:
                closer = text[end]
                if remaining_stack and remaining_stack[-1] == closer:
                    remaining_stack.pop()
                end += 1
            if not remaining_stack:
                return end
            i = end
        return None


@dataclass(slots=True)
class _SegmentResult:
    segment: Segment
    translation: str
    translation_error: str | None
    audio: bytes | None
    audio_error: str | None


EventEmitter = Callable[[dict], object]
TextOperation = Callable[[str], Awaitable[str]]
AudioOperation = Callable[[str, str], Awaitable[bytes]]
TtsObserver = Callable[[TurnIdentity, Segment, bytes | None, str | None], object]
TurnObserver = Any


class SegmentPipeline:
    """Process segments concurrently while committing their events in order."""

    def __init__(
        self,
        identity: TurnIdentity,
        *,
        emit: EventEmitter,
        translate: TextOperation,
        synthesize: AudioOperation,
        tts_concurrency: int = 2,
        tts_retries: int = 1,
        translation_retries: int = 1,
        translation_timeout: float = 10.0,
        tts_observer: TtsObserver | None = None,
        turn_observer: TurnObserver | None = None,
    ) -> None:
        self.identity = identity
        self._emit_callback = emit
        self._translate = translate
        self._synthesize = synthesize
        self._tts_observer = tts_observer
        self._turn_observer = turn_observer
        self._tts_semaphore = asyncio.Semaphore(max(1, tts_concurrency))
        self._tts_retries = max(0, tts_retries)
        self._translation_retries = max(0, translation_retries)
        if translation_timeout <= 0:
            raise ValueError("translation timeout must be positive")
        self._translation_timeout = translation_timeout
        self._buffer: dict[int, _SegmentResult] = {}
        self._committed_translations: list[str] = []
        self._translation_error_count = 0
        self._next_segment_id = 0
        self._submit_lock = asyncio.Lock()
        self._segment_tasks: set[asyncio.Task] = set()
        self._voice_tasks: set[asyncio.Task] = set()
        self._voice_stopped = False
        self._cancelled = False
        self._cancel_event_sent = False
        self._complete_event_sent = False
        self._running = False

    @property
    def translation_text(self) -> str:
        """Return translations in the exact order committed to the client."""
        return "".join(self._committed_translations)

    @property
    def has_translation_errors(self) -> bool:
        """Whether any committed segment exhausted its translation attempts."""
        return self._translation_error_count > 0

    async def run(self, segments: Iterable[Segment]) -> None:
        await self.start()
        items = list(segments)
        if items:
            self._next_segment_id = min(segment.id for segment in items)
        for segment in items:
            self.submit(segment)
        await self.finish(segment_count=len(items))

    async def start(self) -> None:
        if self._running:
            raise RuntimeError("segment pipeline can only run once")
        self._running = True
        await self._emit(self._event("turn.started"))
        if self._turn_observer is not None:
            try:
                self._turn_observer.turn_started(self.identity)
            except Exception:
                pass

    def submit(self, segment: Segment) -> None:
        if not self._running:
            raise RuntimeError("segment pipeline has not started")
        if self._cancelled:
            return
        self._segment_tasks.add(asyncio.create_task(self._process(segment)))

    async def finish(
        self,
        *,
        segment_count: int | None = None,
        emit_completed: bool = True,
    ) -> None:
        if self._segment_tasks:
            await asyncio.gather(*self._segment_tasks, return_exceptions=True)
        completed_count = segment_count
        if completed_count is None:
            completed_count = self._next_segment_id
        self._segment_tasks.clear()
        if emit_completed:
            await self.complete(segment_count=completed_count)

    async def complete(self, *, segment_count: int) -> None:
        if self._cancelled or self._complete_event_sent:
            return
        self._complete_event_sent = True
        await self._emit(self._event("turn.completed", segment_count=segment_count))
        if self._turn_observer is not None:
            try:
                self._turn_observer.turn_terminal(self.identity, "succeeded")
            except Exception:
                pass

    async def stop_voice(self) -> None:
        self._voice_stopped = True
        tasks = list(self._voice_tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def cancel(self, *, reason: str | None = "user_turn_cancel") -> None:
        if self._cancelled:
            return
        self._cancelled = True
        tasks = [*self._segment_tasks, *self._voice_tasks]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if not self._cancel_event_sent:
            self._cancel_event_sent = True
            await self._emit(self._event("turn.cancelled"))
            if self._turn_observer is not None and reason is not None:
                try:
                    self._turn_observer.turn_terminal(self.identity, "cancelled", reason)
                except Exception:
                    pass

    async def _process(self, segment: Segment) -> None:
        translation_task = asyncio.create_task(self._translate_safe(segment.text))
        voice_task = asyncio.create_task(self._synthesize_safe(segment))
        self._voice_tasks.add(voice_task)
        try:
            translation, translation_error = await translation_task
            try:
                audio, audio_error = await voice_task
            except asyncio.CancelledError:
                if self._cancelled:
                    raise
                audio, audio_error = None, "voice_stopped"
        except asyncio.CancelledError:
            translation_task.cancel()
            voice_task.cancel()
            await asyncio.gather(translation_task, voice_task, return_exceptions=True)
            raise
        finally:
            self._voice_tasks.discard(voice_task)

        await self._submit(
            _SegmentResult(
                segment=segment,
                translation=translation,
                translation_error=translation_error,
                audio=audio,
                audio_error=audio_error,
            )
        )

    async def _translate_safe(self, text: str) -> tuple[str, str | None]:
        attempts = self._translation_retries + 1
        last_error: Exception | None = None
        for attempt in range(attempts):
            try:
                translated = await asyncio.wait_for(
                    self._translate(text),
                    timeout=self._translation_timeout,
                )
                if not translated or not translated.strip():
                    return "", "translation_empty"
                return translated.strip(), None
            except asyncio.CancelledError:
                raise
            except asyncio.TimeoutError as exc:
                last_error = exc
            except Exception as exc:
                last_error = exc
                if not self._is_retryable_translation_error(exc):
                    break

            if attempt + 1 < attempts:
                await asyncio.sleep(0.15 * (attempt + 1))

        if isinstance(last_error, asyncio.TimeoutError):
            return "", "translation_timeout"
        return "", "translation_unavailable"

    @staticmethod
    def _is_retryable_translation_error(error: Exception) -> bool:
        if isinstance(error, (TransientSegmentError, TimeoutError, ConnectionError, OSError)):
            return True
        status_code = getattr(error, "status_code", None)
        return status_code in {408, 425, 429} or (
            isinstance(status_code, int) and status_code >= 500
        )

    async def _synthesize_safe(self, segment: Segment) -> tuple[bytes | None, str | None]:
        if self._voice_stopped:
            return None, "voice_stopped"
        attempts = self._tts_retries + 1
        for attempt in range(attempts):
            try:
                async with self._tts_semaphore:
                    if self._voice_stopped:
                        raise _VoiceStopped()
                    return await self._synthesize(segment.text, segment.emotion), None
            except asyncio.CancelledError:
                raise
            except _VoiceStopped:
                return None, "voice_stopped"
            except TransientSegmentError as exc:
                if attempt + 1 >= attempts:
                    return None, str(exc) or "transient_error"
            except Exception as exc:
                code = getattr(exc, "code", None)
                return None, getattr(code, "value", None) or str(exc) or "service_error"
        return None, "service_error"

    async def _submit(self, result: _SegmentResult) -> None:
        if self._cancelled:
            return
        async with self._submit_lock:
            if self._cancelled:
                return
            self._buffer[result.segment.id] = result
            while self._next_segment_id in self._buffer:
                current = self._buffer.pop(self._next_segment_id)
                ready = self._event(
                    "segment.ready",
                    segment_id=current.segment.id,
                    ja=current.segment.text,
                    zh=current.translation,
                    emotion=current.segment.emotion,
                )
                if current.translation_error:
                    ready["translation_error"] = current.translation_error
                    self._translation_error_count += 1
                await self._emit(ready)
                if self._turn_observer is not None:
                    try:
                        self._turn_observer.segment_committed(self.identity, current.segment)
                    except Exception:
                        pass
                if self._tts_observer is not None:
                    try:
                        observer_error = current.audio_error
                        if observer_error is None and self._voice_stopped and current.audio is None:
                            observer_error = "voice_stopped"
                        self._tts_observer(
                            self.identity,
                            current.segment,
                            current.audio,
                            observer_error,
                        )
                    except Exception:
                        # The observer is a strictly best-effort side channel.
                        # Its owner is responsible for recording this failure.
                        pass
                self._committed_translations.append(current.translation)
                if current.audio is not None and not self._voice_stopped:
                    await self._emit(
                        self._event(
                            "segment.audio",
                            segment_id=current.segment.id,
                            data=current.audio,
                            emotion=current.segment.emotion,
                        )
                    )
                else:
                    await self._emit(
                        self._event(
                            "segment.audio_error",
                            segment_id=current.segment.id,
                            reason=current.audio_error or "voice_stopped",
                            emotion=current.segment.emotion,
                        )
                    )
                self._next_segment_id += 1

    def _event(self, event_type: str, **payload) -> dict:
        return {
            "type": event_type,
            "conversation_id": self.identity.conversation_id,
            "turn_id": self.identity.turn_id,
            "generation": self.identity.generation,
            **payload,
        }

    async def _emit(self, event: dict) -> None:
        result = self._emit_callback(event)
        if inspect.isawaitable(result):
            await result
