"""Incremental sentence boundaries and ordered per-turn segment processing."""

from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass
from typing import Awaitable, Callable, Iterable


@dataclass(frozen=True, slots=True)
class Segment:
    id: int
    text: str


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
        self._next_id = 0

    @property
    def pending(self) -> str:
        return self._pending

    def feed(self, chunk: str) -> list[Segment]:
        if chunk:
            self._pending += chunk
        return self._drain(final=False)

    def finish(self) -> list[Segment]:
        segments = self._drain(final=True)
        self._pending = ""
        return segments

    def _drain(self, *, final: bool) -> list[Segment]:
        emitted: list[Segment] = []
        while self._pending:
            boundary = self._find_boundary(self._pending)
            if boundary is None:
                break
            text = self._pending[:boundary].strip()
            self._pending = self._pending[boundary:]
            if text:
                emitted.append(Segment(self._next_id, text))
                self._next_id += 1

        if final:
            tail = self._pending.strip()
            self._pending = ""
            if tail:
                emitted.append(Segment(self._next_id, tail))
                self._next_id += 1
        return emitted

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
AudioOperation = Callable[[str], Awaitable[bytes]]


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
    ) -> None:
        self.identity = identity
        self._emit_callback = emit
        self._translate = translate
        self._synthesize = synthesize
        self._tts_semaphore = asyncio.Semaphore(max(1, tts_concurrency))
        self._tts_retries = max(0, tts_retries)
        self._buffer: dict[int, _SegmentResult] = {}
        self._committed_translations: list[str] = []
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

    async def stop_voice(self) -> None:
        self._voice_stopped = True
        tasks = list(self._voice_tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def cancel(self) -> None:
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

    async def _process(self, segment: Segment) -> None:
        translation_task = asyncio.create_task(self._translate_safe(segment.text))
        voice_task = asyncio.create_task(self._synthesize_safe(segment.text))
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
        try:
            return await self._translate(text), None
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            return "", str(exc)

    async def _synthesize_safe(self, text: str) -> tuple[bytes | None, str | None]:
        if self._voice_stopped:
            return None, "voice_stopped"
        attempts = self._tts_retries + 1
        for attempt in range(attempts):
            try:
                async with self._tts_semaphore:
                    if self._voice_stopped:
                        raise _VoiceStopped()
                    return await self._synthesize(text), None
            except asyncio.CancelledError:
                raise
            except _VoiceStopped:
                return None, "voice_stopped"
            except TransientSegmentError as exc:
                if attempt + 1 >= attempts:
                    return None, str(exc) or "transient_error"
            except Exception as exc:
                return None, str(exc) or "service_error"
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
                )
                if current.translation_error:
                    ready["translation_error"] = current.translation_error
                await self._emit(ready)
                self._committed_translations.append(current.translation)
                if current.audio is not None and not self._voice_stopped:
                    await self._emit(
                        self._event(
                            "segment.audio",
                            segment_id=current.segment.id,
                            data=current.audio,
                        )
                    )
                else:
                    await self._emit(
                        self._event(
                            "segment.audio_error",
                            segment_id=current.segment.id,
                            reason=current.audio_error or "voice_stopped",
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
