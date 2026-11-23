"""Per-message WAV voice cache with admission-checked atomic archival and 1 GB LRU eviction."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import re
import stat
import threading
import time
import uuid
from pathlib import Path
from typing import Any
from weakref import WeakValueDictionary

from app import db
from app.domain.segments import AudioErrorCode
from app.domain.segments import IncrementalJapaneseSegmenter
from app.domain.segments import prepare_tts_text
from app.services.conversation_admission import conversation_admission
from app.services.conversation_erasure import history_erasure_cursor
from app.services.data_location import is_link

logger = logging.getLogger(__name__)

# Fixed 1 GiB cap decided by U; no user-facing config knob.
MAX_VOICE_CACHE_BYTES = 1024 * 1024 * 1024
_MANIFEST_NAME = "manifest.json"
_TMP_PREFIX = ".tmp-"
_LEADING_EMO_RE = re.compile(r"^\s*\[EMO:([a-z_]+)\]\s*", re.IGNORECASE)
_ANY_EMO_RE = re.compile(r"\[EMO:[^\]\r\n]{1,24}\]", re.IGNORECASE)
_VALID_EMOTIONS = frozenset({
    "neutral",
    "tsundere",
    "embarrassed",
    "intellectual",
    "happy",
    "surprised",
    "annoyed",
    "disappointed",
    "sad",
})
_message_locks: WeakValueDictionary[tuple[str, int], asyncio.Lock] = WeakValueDictionary()
_background_evict_tasks: set[asyncio.Task[int]] = set()


class ReplayError(Exception):
    def __init__(self, code: str, status_code: int, reason: str | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.status_code = status_code
        self.reason = reason


def voice_cache_root() -> Path:
    return db.get_data_root() / "voice_cache"


def _is_reparse_or_link(path: Path) -> bool:
    """Detect symlinks and Windows NTFS junctions/reparse points via data_location.is_link."""
    try:
        return is_link(path)
    except FileNotFoundError:
        return False
    except OSError:
        return True


def _safe_conversation_dir(conversation_id: str) -> Path:
    if (
        not conversation_id
        or "/" in conversation_id
        or "\\" in conversation_id
        or ".." in conversation_id
    ):
        raise ValueError("invalid conversation_id for voice cache")
    root = voice_cache_root()
    candidate = root / conversation_id
    if _is_reparse_or_link(candidate):
        raise ValueError("conversation_id resolves to a link or reparse point")
    root_resolved = root.resolve()
    target_resolved = candidate.resolve()
    if not target_resolved.is_relative_to(root_resolved) or target_resolved == root_resolved:
        raise ValueError("conversation_id escapes voice cache root")
    return candidate


def message_cache_dir(conversation_id: str, message_id: int) -> Path:
    if not isinstance(message_id, int) or isinstance(message_id, bool) or message_id <= 0:
        raise ValueError("invalid message_id for voice cache")
    target = _safe_conversation_dir(conversation_id) / str(message_id)
    if _is_reparse_or_link(target):
        raise ValueError("message cache entry is a link or reparse point")
    return target


def _read_manifest(entry_dir: Path) -> dict[str, Any] | None:
    if _is_reparse_or_link(entry_dir):
        return None
    manifest_path = entry_dir / _MANIFEST_NAME
    if _is_reparse_or_link(manifest_path) or not manifest_path.is_file():
        return None
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("source") not in {"original", "synthesized"}:
        return None
    segments = data.get("segments")
    if not isinstance(segments, list) or not segments:
        return None
    for name in segments:
        if not isinstance(name, str) or not name.endswith(".wav") or "/" in name or "\\" in name:
            return None
        wav_path = entry_dir / name
        try:
            if _is_reparse_or_link(wav_path):
                return None
            info = wav_path.lstat()
        except OSError:
            return None
        if not stat.S_ISREG(info.st_mode) or info.st_size <= 0:
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
    """Remove regular files and real subdirectories without traversing or deleting links/junctions."""
    try:
        info = path.lstat()
    except FileNotFoundError:
        return True
    except OSError as exc:
        logger.warning(
            "[VoiceCache] could not inspect %s (%s); deferred to startup sweep",
            path.name,
            type(exc).__name__,
        )
        return False

    if _is_reparse_or_link(path):
        logger.warning("[VoiceCache] skipping link/reparse point during cleanup: %s", path.name)
        return False

    if not stat.S_ISDIR(info.st_mode):
        try:
            path.unlink(missing_ok=True)
            return True
        except OSError as exc:
            logger.warning(
                "[VoiceCache] could not remove %s (%s); deferred to startup sweep",
                path.name,
                type(exc).__name__,
            )
            return False

    complete = True
    try:
        with os.scandir(path) as entries:
            children = [Path(entry.path) for entry in entries]
    except FileNotFoundError:
        return True
    except OSError as exc:
        logger.warning(
            "[VoiceCache] could not scan %s (%s); deferred to startup sweep",
            path.name,
            type(exc).__name__,
        )
        return False

    for child in children:
        if not _remove_tree_best_effort(child):
            complete = False

    if not complete:
        return False

    try:
        path.rmdir()
        return True
    except FileNotFoundError:
        return True
    except OSError as exc:
        logger.warning(
            "[VoiceCache] could not remove %s (%s); deferred to startup sweep",
            path.name,
            type(exc).__name__,
        )
        return False


def _prune_empty_dir(path: Path) -> None:
    try:
        if _is_reparse_or_link(path):
            return
        if path.is_dir() and not any(path.iterdir()):
            path.rmdir()
    except OSError:
        pass


def _stage_entry_dir(
    conv_dir: Path,
    tmp_dir: Path,
    segments: list[bytes],
    source: str,
    cancel_event: threading.Event,
) -> Path:
    if cancel_event.is_set():
        return tmp_dir
    conv_dir.mkdir(parents=True, exist_ok=True)
    if _is_reparse_or_link(conv_dir):
        raise ValueError("conversation voice cache directory is a link or reparse point")
    tmp_dir.mkdir(parents=False, exist_ok=False)
    try:
        names: list[str] = []
        for idx, wav_bytes in enumerate(segments):
            if cancel_event.is_set():
                return tmp_dir
            name = f"{idx:03d}.wav"
            with (tmp_dir / name).open("wb") as handle:
                handle.write(wav_bytes)
                handle.flush()
            names.append(name)
        if cancel_event.is_set():
            return tmp_dir
        manifest = {
            "version": 1,
            "source": source,
            "segments": names,
            "last_played_at": time.time(),
        }
        (tmp_dir / _MANIFEST_NAME).write_text(
            json.dumps(manifest, ensure_ascii=False),
            encoding="utf-8",
        )
        return tmp_dir
    except BaseException:
        _remove_tree_best_effort(tmp_dir)
        raise


async def _await_task_uninterruptible(task: asyncio.Task[Any]) -> asyncio.CancelledError | None:
    """Wait until a thread-backed task finishes, returning any CancelledError to re-raise after cleanup."""
    cancelled: asyncio.CancelledError | None = None
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError as exc:
            cancelled = exc
        except BaseException:
            break
    return cancelled


async def _message_exists(
    worldline: str,
    session_id: str,
    conversation_id: str,
    message_id: int,
) -> bool:
    conn = await db.get_db(worldline, "history")
    try:
        cur = await conn.execute(
            "SELECT 1 FROM messages WHERE id=? AND session_id=? AND conversation_id=? AND role='assistant'",
            (message_id, session_id, conversation_id),
        )
        row = await cur.fetchone()
        return row is not None
    finally:
        await conn.close()


def _dir_regular_bytes(msg_path: Path) -> int | None:
    """Sum regular non-link file sizes inside one message directory, returning None if it vanished."""
    total = 0
    try:
        with os.scandir(msg_path) as items:
            for item in items:
                item_path = Path(item.path)
                if _is_reparse_or_link(item_path):
                    continue
                try:
                    info = item.stat(follow_symlinks=False)
                except FileNotFoundError:
                    continue
                if stat.S_ISREG(info.st_mode):
                    total += info.st_size
    except (FileNotFoundError, NotADirectoryError):
        return None
    except OSError:
        return None
    return total


def enforce_cache_limit(
    *,
    protect: tuple[str, int] | None = None,
    max_bytes: int | None = None,
) -> int:
    limit = MAX_VOICE_CACHE_BYTES if max_bytes is None else max_bytes
    root = voice_cache_root()
    if not root.is_dir():
        return 0
    scanned: list[tuple[int, str, int, Path]] = []
    total_bytes = 0
    try:
        with os.scandir(root) as conv_entries:
            conv_list = list(conv_entries)
    except OSError:
        return 0

    for conv in conv_list:
        conv_path = Path(conv.path)
        if conv.name.startswith(_TMP_PREFIX) or _is_reparse_or_link(conv_path):
            continue
        try:
            if not conv.is_dir(follow_symlinks=False):
                continue
            with os.scandir(conv_path) as msg_entries:
                msg_list = list(msg_entries)
        except (FileNotFoundError, NotADirectoryError, PermissionError):
            continue
        for msg in msg_list:
            msg_path = Path(msg.path)
            if (
                msg.name.startswith(_TMP_PREFIX)
                or not msg.name.isdigit()
                or _is_reparse_or_link(msg_path)
            ):
                continue
            try:
                if not msg.is_dir(follow_symlinks=False):
                    continue
            except (FileNotFoundError, OSError):
                continue
            size = _dir_regular_bytes(msg_path)
            if size is None:
                continue
            total_bytes += size
            scanned.append((size, conv.name, int(msg.name), msg_path))

    if total_bytes <= limit:
        return 0

    ranked: list[tuple[float, int, str, int, Path]] = []
    for size, conv_id, msg_id, msg_path in scanned:
        if _is_reparse_or_link(msg_path):
            continue
        manifest = _read_manifest(msg_path)
        if manifest and isinstance(manifest.get("last_played_at"), (int, float)):
            ts = float(manifest["last_played_at"])
        else:
            try:
                ts = msg_path.lstat().st_mtime
            except OSError:
                continue
        ranked.append((ts, size, conv_id, msg_id, msg_path))

    ranked.sort(key=lambda item: (item[0], item[2], item[3]))
    evicted = 0
    for _, size, conv_id, msg_id, msg_path in ranked:
        if total_bytes <= limit:
            break
        if protect is not None and (conv_id, msg_id) == protect:
            continue
        if _remove_tree_best_effort(msg_path):
            total_bytes -= size
            evicted += 1
            _prune_empty_dir(msg_path.parent)
    return evicted


def schedule_cache_eviction(*, protect: tuple[str, int] | None = None) -> asyncio.Task[int]:
    """Run LRU cache eviction off the turn.completed / replay response path."""

    async def _run() -> int:
        try:
            return await asyncio.to_thread(enforce_cache_limit, protect=protect)
        except Exception as exc:
            logger.warning("[VoiceCache] background eviction failed (%s)", type(exc).__name__)
            return 0

    task = asyncio.create_task(_run())
    _background_evict_tasks.add(task)
    task.add_done_callback(_background_evict_tasks.discard)
    return task


async def drain_background_evictions() -> None:
    loop = asyncio.get_running_loop()
    pending = [task for task in list(_background_evict_tasks) if task.get_loop() is loop]
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)


class TurnAudioRecorder:
    """In-memory per-turn staging for foreground completely-emitted segment.audio payloads."""

    def __init__(self, *, foreground: bool = True) -> None:
        self.foreground = foreground
        self._segments: list[tuple[int, bytes]] = []
        self._incomplete = not foreground

    def on_event(self, event: dict[str, Any]) -> None:
        if self._incomplete:
            return
        event_type = event.get("type")
        if event_type == "segment.audio":
            data = event.get("data")
            if isinstance(data, bytes) and data:
                segment_id = int(event.get("segment_id", len(self._segments)))
                self._segments.append((segment_id, data))
            else:
                self._incomplete = True
        elif event_type == "segment.audio_error":
            self._incomplete = True

    def complete_segments(
        self,
        *,
        voice_stopped: bool = False,
        tts_enabled: bool = True,
    ) -> list[bytes]:
        if self._incomplete or voice_stopped or not tts_enabled or not self._segments:
            return []
        ordered = sorted(self._segments, key=lambda item: item[0])
        return [data for _, data in ordered]


async def archive_audio(
    *,
    worldline: str,
    session_id: str,
    conversation_id: str,
    message_id: int,
    expected_erasure: int,
    segments: list[bytes],
    source: str,
    defer_eviction: bool = False,
) -> bool:
    """Write temp audio files off-lock, then verify message + erasure cursor inside admission lock before renaming."""
    if not segments:
        return False
    wl = db.normalize_worldline(worldline)
    conv_dir = _safe_conversation_dir(conversation_id)
    target_dir = message_cache_dir(conversation_id, message_id)
    tmp_dir = conv_dir / f"{_TMP_PREFIX}{message_id}-{uuid.uuid4().hex}"
    cancel_event = threading.Event()
    stage_task = asyncio.create_task(
        asyncio.to_thread(_stage_entry_dir, conv_dir, tmp_dir, segments, source, cancel_event)
    )
    published = False
    pending_cancel: asyncio.CancelledError | None = None
    try:
        try:
            await asyncio.shield(stage_task)
        except asyncio.CancelledError as exc:
            cancel_event.set()
            pending_cancel = exc
            nested_cancel = await _await_task_uninterruptible(stage_task)
            if nested_cancel is not None:
                pending_cancel = nested_cancel
            raise pending_cancel

        async with conversation_admission(wl, session_id, conversation_id):
            cursor = await history_erasure_cursor(wl, session_id, conversation_id)
            exists = await _message_exists(wl, session_id, conversation_id, message_id)
            if cursor != expected_erasure or not exists:
                return False
            if target_dir.exists():
                if _is_reparse_or_link(target_dir):
                    return False
                if _read_manifest(target_dir) is not None:
                    return True
                remove_task = asyncio.create_task(
                    asyncio.to_thread(_remove_tree_best_effort, target_dir)
                )
                nested_cancel = await _await_task_uninterruptible(remove_task)
                if nested_cancel is not None:
                    pending_cancel = nested_cancel
                    raise pending_cancel
            # Complete the final rename before releasing conversation_admission.
            replace_task = asyncio.create_task(
                asyncio.to_thread(os.replace, tmp_dir, target_dir)
            )
            nested_cancel = await _await_task_uninterruptible(replace_task)
            if replace_task.exception() is not None:
                raise replace_task.exception()
            published = True
            if nested_cancel is not None:
                pending_cancel = nested_cancel
    finally:
        if not published:
            cancel_event.set()
            if not stage_task.done():
                nested_cancel = await _await_task_uninterruptible(stage_task)
                if nested_cancel is not None and pending_cancel is None:
                    pending_cancel = nested_cancel
            def _cleanup_tmp() -> None:
                _remove_tree_best_effort(tmp_dir)
                _prune_empty_dir(conv_dir)

            cleanup_task = asyncio.create_task(asyncio.to_thread(_cleanup_tmp))
            await _await_task_uninterruptible(cleanup_task)

    if pending_cancel is not None:
        raise pending_cancel

    if defer_eviction:
        schedule_cache_eviction(protect=(conversation_id, message_id))
    else:
        await asyncio.to_thread(enforce_cache_limit, protect=(conversation_id, message_id))
    return True


def _load_cached_sync(conversation_id: str, message_id: int) -> tuple[str, list[bytes]] | None:
    try:
        entry_dir = message_cache_dir(conversation_id, message_id)
    except ValueError:
        return None
    manifest = _read_manifest(entry_dir)
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
    conversation_id: str,
    message_id: int,
) -> tuple[str, list[bytes]] | None:
    return await asyncio.to_thread(_load_cached_sync, conversation_id, message_id)


def _remove_audio_sync(conversation_id: str, message_ids: list[int]) -> int:
    if not message_ids:
        return 0
    try:
        conv_dir = _safe_conversation_dir(conversation_id)
    except ValueError:
        return 0
    if _is_reparse_or_link(conv_dir) or not conv_dir.is_dir():
        return 0
    removed = 0
    for mid in message_ids:
        if not isinstance(mid, int) or isinstance(mid, bool) or mid <= 0:
            continue
        target = conv_dir / str(mid)
        if _is_reparse_or_link(target):
            continue
        if target.exists() and _remove_tree_best_effort(target):
            removed += 1
    _prune_empty_dir(conv_dir)
    return removed


async def remove_conversation_audio(conversation_id: str, message_ids: list[int]) -> int:
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
            cur = await conn.execute("SELECT conversation_id, id FROM messages")
            rows = await cur.fetchall()
            for row in rows:
                live.add((str(row["conversation_id"]), int(row["id"])))
        finally:
            await conn.close()

    def _sweep() -> dict[str, int]:
        removed_entries = 0
        removed_temp = 0
        try:
            with os.scandir(root) as conv_entries:
                conv_list = list(conv_entries)
        except OSError:
            return {"removed_entries": 0, "removed_temp": 0}

        for conv in conv_list:
            conv_path = Path(conv.path)
            if _is_reparse_or_link(conv_path):
                continue
            try:
                is_dir = conv.is_dir(follow_symlinks=False)
            except OSError:
                continue
            if conv.name.startswith(_TMP_PREFIX) or not is_dir:
                removed_temp += int(_remove_tree_best_effort(conv_path))
                continue
            try:
                with os.scandir(conv_path) as child_entries:
                    children = list(child_entries)
            except (FileNotFoundError, NotADirectoryError, PermissionError):
                continue
            for child in children:
                child_path = Path(child.path)
                if _is_reparse_or_link(child_path):
                    continue
                try:
                    child_is_dir = child.is_dir(follow_symlinks=False)
                except OSError:
                    continue
                if child.name.startswith(_TMP_PREFIX):
                    removed_temp += int(_remove_tree_best_effort(child_path))
                elif (
                    not child_is_dir
                    or not child.name.isdigit()
                    or (conv.name, int(child.name)) not in live
                    or _read_manifest(child_path) is None
                ):
                    removed_entries += int(_remove_tree_best_effort(child_path))
            _prune_empty_dir(conv_path)
        return {"removed_entries": removed_entries, "removed_temp": removed_temp}

    return await asyncio.to_thread(_sweep)


def _split_replay_chunks(raw_content: str) -> tuple[str, list[str]]:
    match = _LEADING_EMO_RE.match(raw_content or "")
    if match and match.group(1).lower() in _VALID_EMOTIONS:
        emotion = match.group(1).lower()
    else:
        emotion = "neutral"
    text = _ANY_EMO_RE.sub("", raw_content or "").strip()
    prepared = prepare_tts_text(text)
    if prepared.error is None:
        return emotion, [prepared.text]
    if prepared.error is not AudioErrorCode.TEXT_TOO_LONG:
        raise ReplayError("replay_unplayable", 422, reason=prepared.error.value)
    splitter = IncrementalJapaneseSegmenter()
    parts = [seg.text for seg in splitter.feed(text)]
    parts.extend(seg.text for seg in splitter.finish())
    chunks: list[str] = []
    for part in parts:
        prepared_part = prepare_tts_text(part)
        if prepared_part.error is None:
            chunks.append(prepared_part.text)
    if not chunks:
        raise ReplayError("replay_unplayable", 422, reason=AudioErrorCode.ACTION_ONLY.value)
    return emotion, chunks


async def replay_message_audio(
    *,
    worldline: str,
    session_id: str,
    conversation_id: str,
    message_id: int,
) -> dict[str, Any]:
    """Return cached audio or synthesize once behind foreground TTS, archive safely, and return base64 WAVs."""
    from app.services.tts_queue import TTSServiceError
    from app.services.tts_queue import tts_manager
    from app.services.tts_sidecar import sidecar_supervisor

    wl = db.normalize_worldline(worldline)
    conn = await db.get_db(wl, "history")
    try:
        cur = await conn.execute(
            "SELECT id, role, content FROM messages WHERE id=? AND session_id=? AND conversation_id=?",
            (message_id, session_id, conversation_id),
        )
        row = await cur.fetchone()
    finally:
        await conn.close()
    if row is None or row["role"] != "assistant":
        raise ReplayError("message_not_found", 404)

    expected_erasure = await history_erasure_cursor(wl, session_id, conversation_id)
    lock = _message_locks.setdefault((conversation_id, message_id), asyncio.Lock())
    async with lock:
        cached = await load_cached_audio(conversation_id, message_id)
        if cached is not None:
            source, wavs = cached
            return {
                "message_id": message_id,
                "source": source,
                "cached": True,
                "segments": [base64.b64encode(wav).decode("ascii") for wav in wavs],
            }
        emotion, chunks = _split_replay_chunks(str(row["content"] or ""))
        sovits_url = sidecar_supervisor.state.url or tts_manager.sovits_url
        synthesized: list[bytes] = []
        try:
            for chunk in chunks:
                wav = await tts_manager.synthesize_replay_async(
                    chunk,
                    emotion,
                    sovits_url=sovits_url,
                )
                synthesized.append(wav)
        except TTSServiceError as exc:
            unplayable_codes = {
                AudioErrorCode.EMPTY_TEXT,
                AudioErrorCode.ACTION_ONLY,
                AudioErrorCode.TOO_SHORT,
                AudioErrorCode.INVALID_LANGUAGE,
            }
            if exc.code in unplayable_codes:
                raise ReplayError("replay_unplayable", 422, reason=exc.code.value) from exc
            raise ReplayError("tts_unavailable", 503, reason=exc.code.value) from exc
        archived = await archive_audio(
            worldline=wl,
            session_id=session_id,
            conversation_id=conversation_id,
            message_id=message_id,
            expected_erasure=expected_erasure,
            segments=synthesized,
            source="synthesized",
            defer_eviction=True,
        )
        if not archived:
            raise ReplayError("message_erased", 409)
        return {
            "message_id": message_id,
            "source": "synthesized",
            "cached": False,
            "segments": [base64.b64encode(wav).decode("ascii") for wav in synthesized],
        }
