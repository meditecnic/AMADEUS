"""Per-message WAV voice cache with admission-checked atomic archival and 1 GB LRU eviction."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import re
import time
import uuid
from functools import partial
from pathlib import Path
from typing import Any
from weakref import WeakValueDictionary

from app import db
from app.domain.segments import (
    AudioErrorCode,
    IncrementalJapaneseSegmenter,
    prepare_tts_text,
)
from app.services.conversation_admission import conversation_admission
from app.services.conversation_erasure import history_erasure_cursor
from app.services.data_location import is_link
from app.services.data_relocation import submit_thread_work

logger = logging.getLogger(__name__)

# Fixed 1 GiB cap decided by U; no user-facing config knob.
MAX_VOICE_CACHE_BYTES = 1024 * 1024 * 1024
_MANIFEST_NAME, _TMP_PREFIX = "manifest.json", ".tmp-"
_LEADING_EMO_RE = re.compile(r"^\s*\[EMO:([a-z_]+)\]\s*", re.IGNORECASE)
_ANY_EMO_RE = re.compile(r"\[EMO:[^\]\r\n]{1,24}\]", re.IGNORECASE)
_VALID_EMOTIONS = frozenset(
    {
        "neutral",
        "tsundere",
        "embarrassed",
        "intellectual",
        "happy",
        "surprised",
        "annoyed",
        "disappointed",
        "sad",
    }
)
_message_locks: WeakValueDictionary[tuple[str, int], asyncio.Lock] = (
    WeakValueDictionary()
)


class ReplayError(Exception):
    def __init__(self, code: str, status_code: int, reason: str | None = None) -> None:
        super().__init__(code)
        self.code, self.status_code, self.reason = code, status_code, reason


def voice_cache_root() -> Path:
    return db.get_data_root() / "voice_cache"


def _safe_conversation_dir(conversation_id: str) -> Path:
    if (
        not conversation_id
        or "/" in conversation_id
        or "\\" in conversation_id
        or ".." in conversation_id
    ):
        raise ValueError("invalid conversation_id for voice cache")
    root = voice_cache_root().resolve()
    candidate = root / conversation_id
    if _linked(candidate):
        raise ValueError("linked conversation cache directory")
    target = candidate.resolve()
    if not target.is_relative_to(root) or target == root:
        raise ValueError("conversation_id escapes voice cache root")
    return target


def message_cache_dir(conversation_id: str, message_id: int) -> Path:
    if (
        not isinstance(message_id, int)
        or isinstance(message_id, bool)
        or message_id <= 0
    ):
        raise ValueError("invalid message_id for voice cache")
    return _safe_conversation_dir(conversation_id) / str(message_id)


def _linked(path: Path) -> bool:
    try:
        return is_link(path)
    except FileNotFoundError:
        return False


def _cache_children(path: Path):
    try:
        with os.scandir(path) as entries:
            for entry in entries:
                child = Path(entry.path)
                try:
                    if not is_link(child):
                        yield child
                except FileNotFoundError:
                    continue
    except FileNotFoundError:
        return


def _read_manifest(entry_dir: Path) -> dict[str, Any] | None:
    manifest_path = entry_dir / _MANIFEST_NAME
    if _linked(entry_dir) or _linked(manifest_path) or not manifest_path.is_file():
        return None
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("source") not in {
        "original",
        "synthesized",
    }:
        return None
    segments = data.get("segments")
    if (
        not isinstance(segments, list)
        or not segments
        or not all(
            isinstance(n, str) and n.endswith(".wav") and "/" not in n and "\\" not in n
            for n in segments
        )
    ):
        return None
    for name in segments:
        wav_path = entry_dir / name
        if _linked(wav_path) or not wav_path.is_file() or wav_path.stat().st_size <= 0:
            return None
    return data


def _write_manifest_atomic(entry_dir: Path, manifest: dict[str, Any]) -> None:
    tmp = entry_dir / f".manifest-{uuid.uuid4().hex}.tmp"
    try:
        tmp.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, entry_dir / _MANIFEST_NAME)
    except OSError:
        tmp.unlink(missing_ok=True)
        raise


def _remove_tree_best_effort(path: Path) -> bool:
    try:
        if is_link(path):
            return False
        if path.is_dir():
            removed = True
            for child in path.iterdir():
                removed = _remove_tree_best_effort(child) and removed
            if not removed:
                return False
            path.rmdir()
        else:
            path.unlink()
        return True
    except FileNotFoundError:
        return True
    except OSError as exc:
        winerror = getattr(exc, "winerror", None)
        logger.warning(
            "[VoiceCache] could not remove %s (%s%s); deferred to startup sweep",
            path.name,
            type(exc).__name__,
            f", winerror={winerror}" if winerror is not None else "",
        )
        return False


def _prune_empty_dir(path: Path) -> None:
    try:
        if not _linked(path) and path.is_dir() and not any(path.iterdir()):
            path.rmdir()
    except OSError:
        pass


def _stage_entry_dir(tmp_dir: Path, segments: list[bytes], source: str) -> None:
    tmp_dir.parent.mkdir(parents=True, exist_ok=True)
    tmp_dir.mkdir(parents=False, exist_ok=False)
    try:
        names: list[str] = []
        for idx, wav_bytes in enumerate(segments):
            name = f"{idx:03d}.wav"
            with (tmp_dir / name).open("wb") as handle:
                handle.write(wav_bytes)
                handle.flush()
            names.append(name)
        manifest = {
            "version": 1,
            "source": source,
            "segments": names,
            "last_played_at": time.time(),
        }
        (tmp_dir / _MANIFEST_NAME).write_text(
            json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
        )
    except BaseException:
        _remove_tree_best_effort(tmp_dir)
        raise


async def _message_exists(
    worldline: str, session_id: str, conversation_id: str, message_id: int
) -> bool:
    conn = await db.get_db(worldline, "history")
    try:
        row = await (
            await conn.execute(
                "SELECT 1 FROM messages WHERE id=? AND session_id=? AND conversation_id=? AND role='assistant'",
                (message_id, session_id, conversation_id),
            )
        ).fetchone()
        return row is not None
    finally:
        await conn.close()


def enforce_cache_limit(
    *, protect: tuple[str, int] | None = None, max_bytes: int | None = None
) -> int:
    limit = MAX_VOICE_CACHE_BYTES if max_bytes is None else max_bytes
    root = voice_cache_root()
    if not root.is_dir():
        return 0
    entries: list[tuple[float, int, str, int, Path]] = []
    total_bytes = 0
    for conv in _cache_children(root):
        if not conv.is_dir() or conv.name.startswith(_TMP_PREFIX):
            continue
        for msg in _cache_children(conv):
            if not msg.is_dir() or not msg.name.isascii() or not msg.name.isdecimal():
                continue
            try:
                size = sum(
                    child.stat().st_size
                    for child in _cache_children(msg)
                    if child.is_file()
                )
                manifest = _read_manifest(msg)
                last_played = manifest.get("last_played_at") if manifest else None
                ts = (
                    float(last_played)
                    if isinstance(last_played, (int, float))
                    else msg.stat().st_mtime
                )
            except FileNotFoundError:
                continue
            total_bytes += size
            entries.append((ts, size, conv.name, int(msg.name), msg))
    if total_bytes <= limit:
        return 0
    entries.sort(key=lambda item: (item[0], item[2], item[3]))
    evicted = 0
    for _, size, conv_id, msg_id, msg_path in entries:
        if total_bytes <= limit:
            break
        if protect is not None and (conv_id, msg_id) == protect:
            continue
        if not _linked(msg_path.parent) and _remove_tree_best_effort(msg_path):
            total_bytes -= size
            evicted += 1
            _prune_empty_dir(msg_path.parent)
    return evicted


class TurnAudioRecorder:
    """Record foreground WAV segments for turns whose audio is fully sent."""

    def __init__(self, *, foreground: bool = True) -> None:
        self.foreground = foreground
        self._segments: list[tuple[int, bytes]] = []
        self._incomplete = not foreground

    def on_event(self, event: dict[str, Any]) -> None:
        if self._incomplete:
            return
        et = event.get("type")
        if et == "segment.audio":
            data = event.get("data")
            if isinstance(data, bytes) and data:
                self._segments.append(
                    (int(event.get("segment_id", len(self._segments))), data)
                )
            else:
                self._incomplete = True
        elif et == "segment.audio_error":
            self._incomplete = True

    def complete_segments(
        self, *, voice_stopped: bool = False, tts_enabled: bool = True
    ) -> list[bytes]:
        if self._incomplete or voice_stopped or not tts_enabled or not self._segments:
            return []
        return [data for _, data in sorted(self._segments, key=lambda item: item[0])]


async def _finish_file_operation(function, *args):
    work = submit_thread_work(function, *args)
    try:
        return await asyncio.shield(work)
    except asyncio.CancelledError:
        # The thread can still be using the temp directory or the admission lock.
        while not work.done():
            try:
                await asyncio.wait({work})
            except asyncio.CancelledError:
                continue
        raise


async def archive_audio(
    *,
    worldline: str,
    session_id: str,
    conversation_id: str,
    message_id: int,
    expected_erasure: int,
    segments: list[bytes],
    source: str,
) -> bool:
    """Write temp audio files off-lock, then verify message + erasure cursor inside admission lock before renaming."""
    if not segments:
        return False
    wl = db.normalize_worldline(worldline)
    conv_dir, target_dir = (
        _safe_conversation_dir(conversation_id),
        message_cache_dir(conversation_id, message_id),
    )
    tmp_dir = conv_dir / f"{_TMP_PREFIX}{message_id}-{uuid.uuid4().hex}"
    published = False
    try:
        await _finish_file_operation(_stage_entry_dir, tmp_dir, segments, source)
        async with conversation_admission(wl, session_id, conversation_id):
            cursor = await history_erasure_cursor(wl, session_id, conversation_id)
            if cursor != expected_erasure or not await _message_exists(
                wl, session_id, conversation_id, message_id
            ):
                return False
            if _linked(target_dir):
                return False
            if target_dir.exists():
                if _read_manifest(target_dir) is not None:
                    return True
                if not await _finish_file_operation(
                    _remove_tree_best_effort, target_dir
                ):
                    return False
            await _finish_file_operation(os.replace, tmp_dir, target_dir)
            published = True
    finally:
        if not published:
            await _finish_file_operation(_remove_tree_best_effort, tmp_dir)
    submit_thread_work(
        partial(enforce_cache_limit, protect=(conversation_id, message_id))
    )
    return True


def _load_cached_sync(
    conversation_id: str, message_id: int
) -> tuple[str, list[bytes]] | None:
    entry_dir = message_cache_dir(conversation_id, message_id)
    try:
        manifest = _read_manifest(entry_dir)
    except FileNotFoundError:
        return None
    if manifest is None:
        return None
    try:
        segments = [(entry_dir / name).read_bytes() for name in manifest["segments"]]
    except OSError:
        return None
    manifest["last_played_at"] = time.time()
    try:
        _write_manifest_atomic(entry_dir, manifest)
    except OSError:
        pass
    return str(manifest["source"]), segments


async def load_cached_audio(
    conversation_id: str, message_id: int
) -> tuple[str, list[bytes]] | None:
    return await asyncio.to_thread(_load_cached_sync, conversation_id, message_id)


def _remove_audio_sync(conversation_id: str, message_ids: list[int]) -> int:
    if not message_ids:
        return 0
    try:
        conv_dir = _safe_conversation_dir(conversation_id)
    except ValueError:
        return 0
    if not conv_dir.is_dir():
        return 0
    removed = sum(
        1
        for mid in message_ids
        if isinstance(mid, int)
        and not isinstance(mid, bool)
        and mid > 0
        and (conv_dir / str(mid)).exists()
        and _remove_tree_best_effort(conv_dir / str(mid))
    )
    _prune_empty_dir(conv_dir)
    return removed


async def remove_conversation_audio(
    conversation_id: str, message_ids: list[int]
) -> int:
    return await asyncio.to_thread(_remove_audio_sync, conversation_id, message_ids)


async def cleanup_orphan_voice_cache() -> dict[str, int]:
    """Remove voice_cache entries whose messages no longer exist in any worldline history DB."""
    root = voice_cache_root()
    if not root.is_dir():
        return {"removed_entries": 0, "removed_temp": 0}
    live: set[tuple[str, int]] = set()
    for wl in ("steins_gate", "beta"):
        conn = await db.get_db(wl, "history")
        try:
            rows = await (
                await conn.execute("SELECT conversation_id, id FROM messages")
            ).fetchall()
            live.update((str(r["conversation_id"]), int(r["id"])) for r in rows)
        finally:
            await conn.close()

    def _sweep() -> dict[str, int]:
        removed_entries = removed_temp = 0
        for conv in _cache_children(root):
            if conv.name.startswith(_TMP_PREFIX) or not conv.is_dir():
                removed_temp += int(_remove_tree_best_effort(conv))
                continue
            for child in _cache_children(conv):
                if child.name.startswith(_TMP_PREFIX):
                    removed_temp += int(_remove_tree_best_effort(child))
                elif (
                    not child.is_dir()
                    or not child.name.isascii()
                    or not child.name.isdecimal()
                    or (conv.name, int(child.name)) not in live
                    or _read_manifest(child) is None
                ):
                    removed_entries += int(_remove_tree_best_effort(child))
            _prune_empty_dir(conv)
        return {"removed_entries": removed_entries, "removed_temp": removed_temp}

    return await asyncio.to_thread(_sweep)


def _split_replay_chunks(raw_content: str) -> tuple[str, list[str]]:
    match = _LEADING_EMO_RE.match(raw_content or "")
    emotion = (
        match.group(1).lower()
        if match and match.group(1).lower() in _VALID_EMOTIONS
        else "neutral"
    )
    text = _ANY_EMO_RE.sub("", raw_content or "").strip()
    prepared = prepare_tts_text(text)
    if prepared.error is None:
        return emotion, [prepared.text]
    if prepared.error is not AudioErrorCode.TEXT_TOO_LONG:
        raise ReplayError("replay_unplayable", 422, reason=prepared.error.value)
    splitter = IncrementalJapaneseSegmenter()
    parts = [s.text for s in splitter.feed(text)] + [s.text for s in splitter.finish()]
    chunks = [p.text for part in parts if (p := prepare_tts_text(part)).error is None]
    if not chunks:
        raise ReplayError(
            "replay_unplayable", 422, reason=AudioErrorCode.ACTION_ONLY.value
        )
    return emotion, chunks


async def replay_message_audio(
    *, worldline: str, session_id: str, conversation_id: str, message_id: int
) -> dict[str, Any]:
    """Return cached audio or synthesize once behind foreground TTS, archive safely, and return base64 WAVs."""
    from app.services.tts_queue import TTSServiceError, tts_manager
    from app.services.tts_sidecar import sidecar_supervisor

    wl = db.normalize_worldline(worldline)
    conn = await db.get_db(wl, "history")
    try:
        row = await (
            await conn.execute(
                "SELECT id, role, content FROM messages WHERE id=? AND session_id=? AND conversation_id=?",
                (message_id, session_id, conversation_id),
            )
        ).fetchone()
    finally:
        await conn.close()
    if row is None or row["role"] != "assistant":
        raise ReplayError("message_not_found", 404)

    expected_erasure = await history_erasure_cursor(wl, session_id, conversation_id)
    async with _message_locks.setdefault((conversation_id, message_id), asyncio.Lock()):
        cached = await load_cached_audio(conversation_id, message_id)
        if cached is not None:
            source, wavs = cached
            return {
                "message_id": message_id,
                "source": source,
                "cached": True,
                "segments": [base64.b64encode(w).decode("ascii") for w in wavs],
            }
        emotion, chunks = _split_replay_chunks(str(row["content"] or ""))
        sovits_url = sidecar_supervisor.state.url or tts_manager.sovits_url
        try:
            synthesized = [
                await tts_manager.synthesize_replay_async(
                    c, emotion, sovits_url=sovits_url
                )
                for c in chunks
            ]
        except TTSServiceError as exc:
            if exc.code in {
                AudioErrorCode.EMPTY_TEXT,
                AudioErrorCode.ACTION_ONLY,
                AudioErrorCode.TOO_SHORT,
                AudioErrorCode.INVALID_LANGUAGE,
            }:
                raise ReplayError(
                    "replay_unplayable", 422, reason=exc.code.value
                ) from exc
            raise ReplayError("tts_unavailable", 503, reason=exc.code.value) from exc
        if not await archive_audio(
            worldline=wl,
            session_id=session_id,
            conversation_id=conversation_id,
            message_id=message_id,
            expected_erasure=expected_erasure,
            segments=synthesized,
            source="synthesized",
        ):
            raise ReplayError("message_erased", 409)
        return {
            "message_id": message_id,
            "source": "synthesized",
            "cached": False,
            "segments": [base64.b64encode(w).decode("ascii") for w in synthesized],
        }
