"""Inspection, validation, atomic installation, and removal of optional asset packs."""

from __future__ import annotations

import base64
from datetime import datetime, timezone
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import shutil
import stat
import sys
import tempfile
import threading
from typing import Any
import uuid
import zipfile

from app import db
from app.security.passcode import atomic_json
from app.services import data_startup

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
OGG_MAGIC = b"OggS"
ALLOWED_EXTENSIONS = frozenset({".png", ".ogg", ".json", ".md"})
OS_JUNK_NAMES = frozenset({".ds_store", "thumbs.db", "desktop.ini"})

MAX_UPLOAD_BYTES = 512 * 1024 * 1024
MAX_JSON_BODY_BYTES = 64 * 1024
MAX_ZIP_ENTRIES = 500
MAX_DIR_ENTRIES = 1000
MAX_ENTRY_UNCOMPRESSED_BYTES = 64 * 1024 * 1024
MAX_TOTAL_UNCOMPRESSED_BYTES = 512 * 1024 * 1024
MIN_RATIO_CHECK_BYTES = 100 * 1024
MAX_COMPRESSION_RATIO = 100
MAX_MANIFEST_BYTES = 1 * 1024 * 1024
MAX_PREVIEW_BYTES = 16 * 1024 * 1024
STREAM_CHUNK_BYTES = 64 * 1024

_REPARSE_ATTR = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
_DRIVE_PREFIX_RE = re.compile(r"^[A-Za-z]:")
_WINDOWS_RESERVED_NAMES = frozenset(
    {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
    }
)
_WINDOWS_ILLEGAL_CHARS = set('<>:"|?*')
_MANAGED_TEMP_PREFIXES = (".del-asset-pack-", ".tmp-asset-pack-")
_MANAGED_OLD_PREFIX = ".old-asset-pack-"

EMOTION_PREVIEWS: tuple[tuple[str, str], ...] = (
    ("neutral", "assets/images/kurisu_normal1.png"),
    ("tsundere", "assets/images/kurisu_sided_angry1.png"),
    ("embarrassed", "assets/images/kurisu_sided_blush1.png"),
    ("intellectual", "assets/images/kurisu_sided_thinking1.png"),
    ("happy", "assets/images/kurisu_happy1.png"),
    ("surprised", "assets/images/kurisu_sided_surprised1.png"),
    ("annoyed", "assets/images/kurisu_annoyed1.png"),
    ("disappointed", "assets/images/kurisu_disappointed1.png"),
    ("sad", "assets/images/kurisu_sad1.png"),
)

PORTRAIT_FILES: tuple[str, ...] = (
    *(
        f"assets/images/{stem}{frame}.png"
        for stem in (
            "kurisu_normal",
            "kurisu_sided_angry",
            "kurisu_sided_blush",
            "kurisu_sided_thinking",
            "kurisu_happy",
            "kurisu_sided_surprised",
            "kurisu_annoyed",
            "kurisu_disappointed",
            "kurisu_sad",
        )
        for frame in (1, 2, 3)
    ),
    "assets/images/kurisu_eyes_closed1.png",
    "assets/images/kurisu_sided_eyes_closed1.png",
)

BGM_FILES: tuple[str, ...] = (
    "audio/bgm/Amadeus.ogg",
    "audio/bgm/Future of spirit.ogg",
    "audio/bgm/Logical theme.ogg",
    "audio/bgm/Messenger from zero.ogg",
    "audio/bgm/Messenger main theme.ogg",
)

REQUIRED_FILES: tuple[str, ...] = (*PORTRAIT_FILES, *BGM_FILES)
REQUIRED_FILE_SET = frozenset(REQUIRED_FILES)
PREVIEW_PATH_TO_EMOTION = {path: emotion for emotion, path in EMOTION_PREVIEWS}

_install_lock = threading.Lock()


class AssetPackError(RuntimeError):
    """Structured error surfaced as HTTPException by asset pack management routes."""

    def __init__(
        self,
        code: str,
        message: str,
        status: int = 422,
        extra: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.status = status
        self.extra = extra or {}


def _is_link(path: Path) -> bool:
    info = path.lstat()
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & _REPARSE_ATTR)


def _path_exists_or_link(path: Path) -> bool:
    try:
        path.lstat()
        return True
    except OSError:
        return False


def _ensure_no_ancestor_links(
    path: Path,
    *,
    code: str = "unsafe_source_path",
    status: int = 422,
) -> None:
    for part in (path, *path.parents):
        if _path_exists_or_link(part) and _is_link(part):
            raise AssetPackError(code, f"Path must not traverse symbolic links or junctions: {part}", status)


def _make_writable_non_link(target: Path) -> None:
    if not _path_exists_or_link(target) or _is_link(target):
        raise PermissionError(f"Refusing to modify permissions on link or missing path: {target}")
    info = target.lstat()
    mode = (stat.S_IWRITE | stat.S_IREAD | stat.S_IEXEC) if stat.S_ISDIR(info.st_mode) else (stat.S_IWRITE | stat.S_IREAD)
    os.chmod(target, mode)


def _rmtree_managed(path: Path) -> None:
    if not _path_exists_or_link(path):
        return
    if _is_link(path):
        raise OSError(f"Refusing to remove linked path: {path}")

    def _on_exc(func: Any, func_path: str, exc: BaseException) -> None:
        if not isinstance(exc, PermissionError):
            raise exc
        target = Path(func_path)
        if not _path_exists_or_link(target) or _is_link(target):
            raise exc
        _make_writable_non_link(target)
        if func is os.scandir or getattr(func, "__name__", "") in {"scandir", "listdir"}:
            return
        try:
            func(func_path)
        except PermissionError:
            parent = target.parent
            if parent != target and (parent == path or path in parent.parents) and _path_exists_or_link(parent) and not _is_link(parent):
                _make_writable_non_link(parent)
                func(func_path)
            else:
                raise

    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=_on_exc)
    else:
        def _on_error(func: Any, func_path: str, exc_info: tuple[Any, BaseException, Any]) -> None:
            _on_exc(func, func_path, exc_info[1])

        shutil.rmtree(path, onerror=_on_error)


def _rmtree_managed_best_effort(path: Path) -> None:
    try:
        _rmtree_managed(path)
    except OSError:
        pass


def _sweep_managed_temps(pack_parent: Path, *, include_old: bool = False) -> None:
    if not _path_exists_or_link(pack_parent) or _is_link(pack_parent) or not pack_parent.is_dir():
        return
    prefixes = (*_MANAGED_TEMP_PREFIXES, _MANAGED_OLD_PREFIX) if include_old else _MANAGED_TEMP_PREFIXES
    try:
        with os.scandir(pack_parent) as entries:
            candidates = list(entries)
    except OSError:
        return
    for entry in candidates:
        if not entry.name.startswith(prefixes):
            continue
        try:
            info = entry.stat(follow_symlinks=False)
        except OSError:
            continue
        if entry.is_symlink() or stat.S_ISLNK(info.st_mode) or (getattr(info, "st_file_attributes", 0) & _REPARSE_ATTR):
            continue
        entry_path = Path(entry.path)
        if stat.S_ISDIR(info.st_mode):
            _rmtree_managed_best_effort(entry_path)
        else:
            try:
                os.unlink(entry_path)
            except PermissionError:
                try:
                    _make_writable_non_link(entry_path)
                    os.unlink(entry_path)
                except OSError:
                    pass
            except OSError:
                pass


def _restore_orphan_old_pack(pack_parent: Path, target_dir: Path) -> None:
    if (
        _path_exists_or_link(target_dir)
        or not _path_exists_or_link(pack_parent)
        or _is_link(pack_parent)
        or not pack_parent.is_dir()
    ):
        return
    try:
        with os.scandir(pack_parent) as entries:
            scanned = list(entries)
    except OSError:
        return
    candidates: list[tuple[bool, int, Path]] = []
    for entry in scanned:
        if not entry.name.startswith(_MANAGED_OLD_PREFIX):
            continue
        try:
            info = entry.stat(follow_symlinks=False)
        except OSError:
            continue
        if (
            entry.is_symlink()
            or stat.S_ISLNK(info.st_mode)
            or (getattr(info, "st_file_attributes", 0) & _REPARSE_ATTR)
            or not stat.S_ISDIR(info.st_mode)
        ):
            continue
        candidate_path = Path(entry.path)
        manifest_path = candidate_path / "manifest.json"
        has_manifest = (
            _path_exists_or_link(manifest_path)
            and not _is_link(manifest_path)
            and manifest_path.is_file()
        )
        candidates.append((has_manifest, getattr(info, "st_mtime_ns", 0), candidate_path))
    candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
    for _, _, candidate_path in candidates:
        try:
            os.replace(candidate_path, target_dir)
            return
        except OSError:
            continue


def _snapshot_tree_paths(root: Path) -> frozenset[str] | None:
    if not _path_exists_or_link(root) or _is_link(root):
        return None
    if not root.is_dir():
        return frozenset({""})
    paths: set[str] = set()
    stack = [root]
    try:
        while stack:
            current = stack.pop()
            with os.scandir(current) as entries:
                for entry in entries:
                    entry_path = Path(entry.path)
                    paths.add(entry_path.relative_to(root).as_posix())
                    info = entry.stat(follow_symlinks=False)
                    if (
                        not entry.is_symlink()
                        and not stat.S_ISLNK(info.st_mode)
                        and not (getattr(info, "st_file_attributes", 0) & _REPARSE_ATTR)
                        and stat.S_ISDIR(info.st_mode)
                    ):
                        stack.append(entry_path)
    except OSError:
        return None
    return frozenset(paths)


def _env_override() -> Path | None:
    raw = (os.environ.get("AMADEUS_ASSET_PACK_DIR") or "").strip()
    if not raw:
        return None
    return Path(raw).expanduser().resolve()


def _pack_root() -> tuple[Path, bool, str]:
    override = _env_override()
    if override is not None:
        return override, False, "env_override"
    return (db.get_data_root() / "asset-pack" / "amadeus-original").resolve(), True, "data_dir"


def _require_data_available() -> None:
    if not data_startup.available():
        raise AssetPackError("data_unavailable", "Data directory is unavailable", 409)


def _walk_pack_sizes(root: Path) -> int:
    if not root.is_dir() or _is_link(root):
        return 0
    total = 0
    stack = [root]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    try:
                        info = entry.stat(follow_symlinks=False)
                    except OSError:
                        continue
                    if entry.is_symlink() or (getattr(info, "st_file_attributes", 0) & _REPARSE_ATTR):
                        continue
                    if stat.S_ISDIR(info.st_mode):
                        stack.append(Path(entry.path))
                    elif stat.S_ISREG(info.st_mode):
                        total += info.st_size
        except OSError:
            continue
    return total


def _read_manifest_metadata(root: Path) -> tuple[bool, str | None, str | None]:
    manifest_path = root / "manifest.json"
    if not _path_exists_or_link(manifest_path) or _is_link(manifest_path) or not manifest_path.is_file():
        return False, None, None
    try:
        raw = manifest_path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, ValueError, UnicodeDecodeError):
        return True, None, None
    if not isinstance(data, dict):
        return True, None, None
    if data.get("installed") is False:
        return False, None, None
    raw_name = data.get("name")
    name = raw_name.strip() if isinstance(raw_name, str) and raw_name.strip() else None
    raw_version = data.get("version")
    if isinstance(raw_version, str) and raw_version.strip():
        version: str | None = raw_version.strip()
    elif isinstance(raw_version, (int, float)) and not isinstance(raw_version, bool):
        version = str(raw_version)
    else:
        version = None
    return True, name, version


def get_status() -> dict[str, Any]:
    """Return current asset pack installation status."""
    override = _env_override()
    if override is None:
        _require_data_available()
    root, movable, source = _pack_root()

    root_valid = root.is_dir() and not _is_link(root)
    manifest_present, manifest_name, manifest_version = _read_manifest_metadata(root) if root_valid else (False, None, None)

    present_files: set[str] = set()
    if root_valid:
        for rel in REQUIRED_FILES:
            candidate = root / rel
            if not _path_exists_or_link(candidate):
                continue
            # Check intermediate directories under root for links as well.
            if any(_is_link(parent) for parent in candidate.parents if parent != root and parent.is_relative_to(root)):
                continue
            try:
                info = candidate.lstat()
            except OSError:
                continue
            if not _is_link(candidate) and stat.S_ISREG(info.st_mode) and info.st_size > 0:
                present_files.add(rel)

    missing_files = [rel for rel in REQUIRED_FILES if rel not in present_files]
    installed = root_valid and (manifest_present or len(present_files) > 0)
    complete = installed and len(missing_files) == 0
    size_bytes = _walk_pack_sizes(root) if installed else 0

    if not installed:
        name = None
        version = None
    else:
        name = manifest_name or "amadeus-original"
        version = manifest_version

    return {
        "installed": installed,
        "complete": complete,
        "movable": movable,
        "path": str(root),
        "source": source,
        "name": name,
        "version": version,
        "size_bytes": size_bytes,
        "required_count": len(REQUIRED_FILES),
        "present_count": len(present_files),
        "missing_files": missing_files,
    }


def _is_os_junk(rel_path: str) -> bool:
    parts = rel_path.split("/")
    if parts[0] == "__MACOSX":
        return True
    name = parts[-1].lower()
    return name in OS_JUNK_NAMES or name.startswith("._")


def _strip_wrapper_paths(file_paths: list[str], dir_paths: list[str]) -> tuple[str | None, dict[str, str]]:
    non_junk_files = [p for p in file_paths if not _is_os_junk(p)]
    non_junk_dirs = [p for p in dir_paths if not _is_os_junk(p)]
    all_non_junk = non_junk_files + non_junk_dirs
    if not all_non_junk:
        return None, {p: p for p in file_paths}

    if any(p == "assets" or p == "audio" or p.startswith(("assets/", "audio/")) for p in all_non_junk):
        return None, {p: p for p in file_paths}

    if any("/" not in p for p in non_junk_files):
        return None, {p: p for p in file_paths}

    tops = {p.split("/", 1)[0] for p in all_non_junk}
    if len(tops) == 1:
        top = next(iter(tops))
        has_expected_child = any(
            p == f"{top}/assets" or p == f"{top}/audio" or p.startswith((f"{top}/assets/", f"{top}/audio/"))
            for p in all_non_junk
        )
        if has_expected_child:
            prefix = f"{top}/"
            mapping = {
                p: p[len(prefix):] if p.startswith(prefix) else p
                for p in file_paths
            }
            return top, mapping

    return None, {p: p for p in file_paths}


def _validate_magic(rel_path: str, prefix: bytes) -> str | None:
    ext = Path(rel_path).suffix.lower()
    if ext == ".png" and not prefix.startswith(PNG_MAGIC):
        return "Invalid PNG signature"
    if ext == ".ogg" and not prefix.startswith(OGG_MAGIC):
        return "Invalid OGG signature"
    return None


def _parse_manifest_bytes(raw_bytes: bytes) -> tuple[dict[str, Any] | None, str | None]:
    try:
        decoded = raw_bytes.decode("utf-8")
    except UnicodeDecodeError:
        return None, "manifest.json must be valid UTF-8"
    try:
        parsed = json.loads(decoded)
    except ValueError:
        return None, "manifest.json is not valid JSON"
    if not isinstance(parsed, dict):
        return None, "manifest.json must be a JSON object"
    return parsed, None


def _finalize_inspection(
    *,
    source_type: str,
    stripped_root: str | None,
    manifest_data: dict[str, Any] | None,
    manifest_error: str | None,
    present_files: set[str],
    invalid_files: list[dict[str, str]],
    skipped_files: list[str],
    total_bytes: int,
    preview_bytes: dict[str, bytes],
) -> dict[str, Any]:
    missing_files = [rel for rel in REQUIRED_FILES if rel not in present_files]
    found_count = len(REQUIRED_FILES) - len(missing_files)

    errors: list[str] = []
    warnings: list[str] = []

    if manifest_error is not None:
        invalid_files.append({"path": "manifest.json", "reason": manifest_error})
        errors.append(manifest_error)

    if missing_files:
        errors.append(f"Missing {len(missing_files)} required asset file(s)")
    if any(item["path"] != "manifest.json" for item in invalid_files):
        bad_count = sum(1 for item in invalid_files if item["path"] != "manifest.json")
        errors.append(f"{bad_count} file(s) failed format header validation")

    for skipped in sorted(skipped_files):
        warnings.append(f"Skipped unsupported file type: {skipped}")

    if manifest_data is not None:
        listed = manifest_data.get("files")
        if isinstance(listed, list):
            missing_listed = [
                f for f in listed
                if isinstance(f, str)
                and f.replace("\\", "/").lstrip("/") not in present_files
                and f.replace("\\", "/").lstrip("/").lower() != "manifest.json"
            ]
            if missing_listed:
                warnings.append(f"manifest.json lists {len(missing_listed)} absent file(s)")

    name = stripped_root or "amadeus-original"
    version = "1.0"
    if manifest_data is not None:
        raw_name = manifest_data.get("name")
        if isinstance(raw_name, str) and raw_name.strip():
            name = raw_name.strip()
        raw_version = manifest_data.get("version")
        if isinstance(raw_version, str) and raw_version.strip():
            version = raw_version.strip()
        elif isinstance(raw_version, (int, float)) and not isinstance(raw_version, bool):
            version = str(raw_version)

    previews: list[dict[str, str]] = []
    invalid_paths = {item["path"] for item in invalid_files}
    for emotion, rel_path in EMOTION_PREVIEWS:
        if rel_path in preview_bytes and rel_path not in invalid_paths:
            encoded = base64.b64encode(preview_bytes[rel_path]).decode("ascii")
            previews.append({
                "emotion": emotion,
                "path": rel_path,
                "data_url": f"data:image/png;base64,{encoded}",
            })

    valid = len(missing_files) == 0 and len(invalid_files) == 0 and len(errors) == 0

    return {
        "valid": valid,
        "source_type": source_type,
        "stripped_root": stripped_root,
        "name": name,
        "version": version,
        "required_count": len(REQUIRED_FILES),
        "found_count": found_count,
        "missing_files": missing_files,
        "invalid_files": invalid_files,
        "errors": errors,
        "warnings": warnings,
        "total_bytes": total_bytes,
        "previews": previews,
    }


def _validate_portable_segment(seg: str) -> str | None:
    if not seg or seg in {".", ".."}:
        return "empty or dot path segment"
    if seg.endswith((".", " ")):
        return "trailing dot or space in path segment"
    for ch in seg:
        if ord(ch) < 0x20 or ord(ch) == 0x7F:
            return "control character in path segment"
        if ch in _WINDOWS_ILLEGAL_CHARS or ch in ("/", "\\"):
            return "illegal filename character in path segment"
    stem = seg.rstrip(". ").split(".", 1)[0].strip().upper()
    if stem in _WINDOWS_RESERVED_NAMES:
        return "reserved Windows device name"
    return None


def _validate_zip_entry_name(raw_name: str, *, is_dir: bool) -> str:
    if not raw_name or "\x00" in raw_name:
        raise AssetPackError("unsafe_archive", "Archive contains an invalid entry name", 422)
    if raw_name.startswith(("/", "\\")) or PureWindowsPath(raw_name).drive or _DRIVE_PREFIX_RE.match(raw_name):
        raise AssetPackError("unsafe_archive", f"Archive contains unsafe absolute or drive path: {raw_name}", 422)

    normalized = raw_name.replace("\\", "/")
    if is_dir:
        normalized = normalized.rstrip("/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    if not normalized or normalized == ".":
        return ""

    parts = [part for part in normalized.split("/") if part != ""]
    if not parts or any(part in {".", ".."} for part in parts):
        raise AssetPackError("unsafe_archive", f"Archive contains unsafe path traversal: {raw_name}", 422)
    for part in parts:
        reason = _validate_portable_segment(part)
        if reason is not None:
            raise AssetPackError("unsafe_archive", f"Archive contains unsafe path segment ({reason}): {raw_name}", 422)

    posix = PurePosixPath(*parts)
    if posix.is_absolute():
        raise AssetPackError("unsafe_archive", f"Archive contains unsafe absolute path: {raw_name}", 422)
    return posix.as_posix()


def _validate_zip_info_metadata(info: zipfile.ZipInfo) -> None:
    if info.flag_bits & 0x1:
        raise AssetPackError("unsafe_archive", f"Encrypted archive entries are not supported: {info.filename}", 422)

    mode = info.external_attr >> 16
    file_type = stat.S_IFMT(mode)
    if stat.S_ISLNK(mode):
        raise AssetPackError("unsafe_archive", f"Archive contains symbolic link: {info.filename}", 422)
    if mode != 0 and file_type not in (0, stat.S_IFREG, stat.S_IFDIR):
        raise AssetPackError("unsafe_archive", f"Archive contains unsupported special entry: {info.filename}", 422)
    if (info.external_attr & _REPARSE_ATTR) and info.create_system in (0, 10, 11, 14):
        raise AssetPackError("unsafe_archive", f"Archive contains Windows reparse point: {info.filename}", 422)

    if info.file_size > MAX_ENTRY_UNCOMPRESSED_BYTES:
        raise AssetPackError("unsafe_archive", f"Archive entry exceeds size limit: {info.filename}", 422)
    if not info.is_dir():
        if info.file_size > 0 and info.compress_size <= 0:
            raise AssetPackError("unsafe_archive", f"Archive entry has invalid compressed size: {info.filename}", 422)
        if info.file_size > MIN_RATIO_CHECK_BYTES and (info.file_size / info.compress_size) > MAX_COMPRESSION_RATIO:
            raise AssetPackError("unsafe_archive", f"Archive entry exceeds compression ratio limit: {info.filename}", 422)


def _check_path_collisions(rel_paths: list[str]) -> None:
    seen_lower: set[str] = set()
    dir_prefixes: set[str] = set()
    file_set: set[str] = set()

    for rel in rel_paths:
        lower = rel.lower()
        if lower in seen_lower:
            raise AssetPackError("unsafe_archive", f"Archive contains duplicate or case-conflicting path: {rel}", 422)
        seen_lower.add(lower)
        file_set.add(lower)
        parts = lower.split("/")
        for idx in range(1, len(parts)):
            dir_prefixes.add("/".join(parts[:idx]))

    if file_set & dir_prefixes:
        raise AssetPackError("unsafe_archive", "Archive contains file/directory path collision", 422)


def _inspect_zip_internal(zip_path: Path) -> tuple[dict[str, Any], list[tuple[zipfile.ZipInfo, str]]]:
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            infos = zf.infolist()
            if len(infos) > MAX_ZIP_ENTRIES:
                raise AssetPackError("unsafe_archive", "Archive contains too many entries", 422)

            declared_total = 0
            raw_files: list[tuple[zipfile.ZipInfo, str]] = []
            raw_dirs: list[str] = []

            for info in infos:
                _validate_zip_info_metadata(info)
                norm = _validate_zip_entry_name(info.filename, is_dir=info.is_dir())
                if not norm:
                    continue
                if info.is_dir():
                    raw_dirs.append(norm)
                else:
                    declared_total += info.file_size
                    if declared_total > MAX_TOTAL_UNCOMPRESSED_BYTES:
                        raise AssetPackError("unsafe_archive", "Archive exceeds maximum total uncompressed size", 422)
                    raw_files.append((info, norm))

            stripped_root, path_map = _strip_wrapper_paths(
                [norm for _, norm in raw_files],
                raw_dirs,
            )

            non_junk_mapped: list[tuple[zipfile.ZipInfo, str]] = []
            for info, orig in raw_files:
                if _is_os_junk(orig):
                    continue
                mapped = path_map[orig]
                if not mapped or _is_os_junk(mapped):
                    continue
                non_junk_mapped.append((info, mapped))

            _check_path_collisions([mapped for _, mapped in non_junk_mapped])

            actual_total = 0
            total_eligible_bytes = 0
            present_files: set[str] = set()
            invalid_files: list[dict[str, str]] = []
            skipped_files: list[str] = []
            preview_bytes: dict[str, bytes] = {}
            manifest_data: dict[str, Any] | None = None
            manifest_error: str | None = None
            install_entries: list[tuple[zipfile.ZipInfo, str]] = []

            for info, rel in non_junk_mapped:
                ext = Path(rel).suffix.lower()
                is_preview = rel in PREVIEW_PATH_TO_EMOTION
                is_manifest = rel.lower() == "manifest.json"
                capture_bytes = is_manifest or is_preview

                entry_bytes = 0
                prefix = bytearray()
                captured = bytearray()

                try:
                    with zf.open(info, "r") as stream:
                        while True:
                            chunk = stream.read(STREAM_CHUNK_BYTES)
                            if not chunk:
                                break
                            entry_bytes += len(chunk)
                            actual_total += len(chunk)
                            if entry_bytes > MAX_ENTRY_UNCOMPRESSED_BYTES:
                                raise AssetPackError("unsafe_archive", f"Archive entry exceeds size limit: {info.filename}", 422)
                            if actual_total > MAX_TOTAL_UNCOMPRESSED_BYTES:
                                raise AssetPackError("unsafe_archive", "Archive exceeds maximum total uncompressed size", 422)
                            if entry_bytes > MIN_RATIO_CHECK_BYTES and (
                                info.compress_size <= 0 or (entry_bytes / info.compress_size) > MAX_COMPRESSION_RATIO
                            ):
                                raise AssetPackError("unsafe_archive", f"Archive entry exceeds compression ratio limit: {info.filename}", 422)
                            if len(prefix) < 16:
                                prefix.extend(chunk[: 16 - len(prefix)])
                            if capture_bytes:
                                limit = MAX_MANIFEST_BYTES if is_manifest else MAX_PREVIEW_BYTES
                                if len(captured) + len(chunk) <= limit:
                                    captured.extend(chunk)
                                else:
                                    capture_bytes = False
                                    captured.clear()
                except NotImplementedError as exc:
                    raise AssetPackError("invalid_archive", f"Unsupported zip compression method: {exc}", 422) from exc
                except (zipfile.BadZipFile, RuntimeError, EOFError) as exc:
                    raise AssetPackError("invalid_archive", f"Corrupted zip archive: {exc}", 422) from exc

                if ext not in ALLOWED_EXTENSIONS:
                    skipped_files.append(rel)
                    continue

                total_eligible_bytes += entry_bytes
                present_files.add(rel)

                if is_manifest:
                    if entry_bytes > MAX_MANIFEST_BYTES:
                        manifest_error = "manifest.json exceeds size limit"
                    else:
                        manifest_data, manifest_error = _parse_manifest_bytes(bytes(captured))
                    continue

                reason = _validate_magic(rel, bytes(prefix))
                if reason is not None:
                    invalid_files.append({"path": rel, "reason": reason})
                    continue

                if is_preview and len(captured) == entry_bytes:
                    preview_bytes[rel] = bytes(captured)

                install_entries.append((info, rel))

            result = _finalize_inspection(
                source_type="zip",
                stripped_root=stripped_root,
                manifest_data=manifest_data,
                manifest_error=manifest_error,
                present_files=present_files,
                invalid_files=invalid_files,
                skipped_files=skipped_files,
                total_bytes=total_eligible_bytes,
                preview_bytes=preview_bytes,
            )
            return result, install_entries
    except (zipfile.BadZipFile, OSError) as exc:
        if isinstance(exc, AssetPackError):
            raise
        raise AssetPackError("invalid_archive", f"Cannot open zip archive: {exc}", 422) from exc


def _inspect_directory_internal(source_dir: Path) -> tuple[dict[str, Any], list[tuple[Path, str]]]:
    _ensure_no_ancestor_links(source_dir, code="unsafe_source_path")

    raw_files: list[tuple[Path, str, int]] = []
    raw_dirs: list[str] = []
    entry_count = 0
    total_scanned_bytes = 0

    stack = [source_dir]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    entry_count += 1
                    if entry_count > MAX_DIR_ENTRIES:
                        raise AssetPackError("unsafe_source_path", "Source directory contains too many entries", 422)
                    entry_path = Path(entry.path)
                    try:
                        info = entry.stat(follow_symlinks=False)
                    except OSError as exc:
                        raise AssetPackError("unsafe_source_path", f"Cannot stat entry: {entry.name}", 422) from exc
                    if entry.is_symlink() or stat.S_ISLNK(info.st_mode) or (getattr(info, "st_file_attributes", 0) & _REPARSE_ATTR):
                        raise AssetPackError("unsafe_source_path", f"Symbolic links and junctions are not allowed: {entry.name}", 422)

                    rel = entry_path.relative_to(source_dir).as_posix()
                    for part in rel.split("/"):
                        reason = _validate_portable_segment(part)
                        if reason is not None:
                            raise AssetPackError(
                                "unsafe_source_path",
                                f"Source directory contains unsafe path segment ({reason}): {rel}",
                                422,
                            )
                    if stat.S_ISDIR(info.st_mode):
                        raw_dirs.append(rel)
                        stack.append(entry_path)
                    elif stat.S_ISREG(info.st_mode):
                        if info.st_size > MAX_ENTRY_UNCOMPRESSED_BYTES:
                            raise AssetPackError("unsafe_source_path", f"File exceeds maximum entry size: {rel}", 422)
                        total_scanned_bytes += info.st_size
                        if total_scanned_bytes > MAX_TOTAL_UNCOMPRESSED_BYTES:
                            raise AssetPackError("unsafe_source_path", "Directory exceeds maximum total size", 422)
                        raw_files.append((entry_path, rel, info.st_size))
                    else:
                        raise AssetPackError("unsafe_source_path", f"Unsupported special file in directory: {rel}", 422)
        except OSError as exc:
            if isinstance(exc, AssetPackError):
                raise
            raise AssetPackError("source_not_found", f"Cannot read directory: {exc}", 422) from exc

    stripped_root, path_map = _strip_wrapper_paths(
        [rel for _, rel, _ in raw_files],
        raw_dirs,
    )

    non_junk: list[tuple[Path, str, int]] = []
    for disk_path, orig, size in raw_files:
        if _is_os_junk(orig):
            continue
        mapped = path_map[orig]
        if not mapped or _is_os_junk(mapped):
            continue
        non_junk.append((disk_path, mapped, size))

    _check_path_collisions([mapped for _, mapped, _ in non_junk])

    total_eligible_bytes = 0
    present_files: set[str] = set()
    invalid_files: list[dict[str, str]] = []
    skipped_files: list[str] = []
    preview_bytes: dict[str, bytes] = {}
    manifest_data: dict[str, Any] | None = None
    manifest_error: str | None = None
    install_entries: list[tuple[Path, str]] = []

    for disk_path, rel, size in non_junk:
        ext = Path(rel).suffix.lower()
        if ext not in ALLOWED_EXTENSIONS:
            skipped_files.append(rel)
            continue

        total_eligible_bytes += size
        present_files.add(rel)

        if rel.lower() == "manifest.json":
            if size > MAX_MANIFEST_BYTES:
                manifest_error = "manifest.json exceeds size limit"
            else:
                try:
                    manifest_data, manifest_error = _parse_manifest_bytes(disk_path.read_bytes())
                except OSError as exc:
                    manifest_error = f"Cannot read manifest.json: {exc}"
            continue

        try:
            with disk_path.open("rb") as handle:
                prefix = handle.read(16)
        except OSError as exc:
            invalid_files.append({"path": rel, "reason": f"Cannot read file: {exc}"})
            continue

        reason = _validate_magic(rel, prefix)
        if reason is not None:
            invalid_files.append({"path": rel, "reason": reason})
            continue

        if rel in PREVIEW_PATH_TO_EMOTION and size <= MAX_PREVIEW_BYTES:
            try:
                preview_bytes[rel] = disk_path.read_bytes()
            except OSError as exc:
                invalid_files.append({"path": rel, "reason": f"Cannot read preview image: {exc}"})
                continue

        install_entries.append((disk_path, rel))

    result = _finalize_inspection(
        source_type="directory",
        stripped_root=stripped_root,
        manifest_data=manifest_data,
        manifest_error=manifest_error,
        present_files=present_files,
        invalid_files=invalid_files,
        skipped_files=skipped_files,
        total_bytes=total_eligible_bytes,
        preview_bytes=preview_bytes,
    )
    return result, install_entries


def _resolve_source_path(raw_path: str) -> Path:
    cleaned = raw_path.strip()
    if not cleaned or "\x00" in cleaned:
        raise AssetPackError("invalid_request", "Source path must be non-empty", 422)
    path = Path(cleaned)
    if not path.is_absolute():
        raise AssetPackError("path_not_absolute", "Source path must be an absolute path", 422)
    if not _path_exists_or_link(path):
        raise AssetPackError("source_not_found", f"Source path does not exist: {cleaned}", 422)
    _ensure_no_ancestor_links(path, code="unsafe_source_path")
    return path.resolve()


def inspect_path(raw_path: str) -> dict[str, Any]:
    """Inspect a local directory or zip archive path without modifying installed data."""
    _require_data_available()
    source = _resolve_source_path(raw_path)
    if source.is_dir():
        result, _ = _inspect_directory_internal(source)
        return result
    if source.is_file():
        if source.stat().st_size > MAX_UPLOAD_BYTES:
            raise AssetPackError("payload_too_large", "Archive file exceeds maximum allowed size", 413)
        result, _ = _inspect_zip_internal(source)
        return result
    raise AssetPackError("unsafe_source_path", "Source path must be a regular file or directory", 422)


def inspect_zip_file(zip_path: Path) -> dict[str, Any]:
    """Inspect an uploaded zip archive file without modifying installed data."""
    _require_data_available()
    if not zip_path.is_file():
        raise AssetPackError("source_not_found", "Uploaded archive file not found", 422)
    if zip_path.stat().st_size > MAX_UPLOAD_BYTES:
        raise AssetPackError("payload_too_large", "Uploaded archive exceeds size limit", 413)
    result, _ = _inspect_zip_internal(zip_path)
    return result


def _verify_staged_dir(staging_dir: Path, expected_files: list[str]) -> None:
    manifest_path = staging_dir / "manifest.json"
    if not manifest_path.is_file() or _is_link(manifest_path):
        raise AssetPackError("asset_pack_install_failed", "Staged manifest.json is missing", 500)
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError) as exc:
        raise AssetPackError("asset_pack_install_failed", f"Staged manifest.json invalid: {exc}", 500) from exc

    if manifest.get("format") != 1 or manifest.get("files") != sorted(expected_files):
        raise AssetPackError("asset_pack_install_failed", "Staged manifest contents mismatch", 500)

    for rel in expected_files:
        staged = staging_dir / rel
        if not staged.is_file() or _is_link(staged):
            raise AssetPackError("asset_pack_install_failed", f"Staged file missing: {rel}", 500)
        if rel in REQUIRED_FILE_SET:
            with staged.open("rb") as handle:
                header = handle.read(16)
            reason = _validate_magic(rel, header)
            if reason is not None:
                raise AssetPackError("asset_pack_install_failed", f"Staged file invalid ({rel}): {reason}", 500)


def _stage_and_swap(
    *,
    inspect_result: dict[str, Any],
    writer_fn,
) -> dict[str, Any]:
    data_root = db.get_data_root()
    _ensure_no_ancestor_links(data_root, code="unsafe_target_path", status=409)
    pack_parent = data_root / "asset-pack"
    if _path_exists_or_link(pack_parent) and _is_link(pack_parent):
        raise AssetPackError("unsafe_target_path", "Asset pack parent directory cannot be a link", 409)
    target_dir = pack_parent / "amadeus-original"
    if _path_exists_or_link(target_dir) and _is_link(target_dir):
        raise AssetPackError("unsafe_target_path", "Asset pack target directory cannot be a link", 409)

    pack_parent.mkdir(parents=True, exist_ok=True)
    if not _path_exists_or_link(target_dir):
        _restore_orphan_old_pack(pack_parent, target_dir)
    _sweep_managed_temps(pack_parent, include_old=False)
    staging_dir = Path(tempfile.mkdtemp(prefix=".tmp-asset-pack-", dir=pack_parent))
    backup_dir: Path | None = None
    swapped_old = False
    swapped_new = False

    try:
        installed_files = writer_fn(staging_dir)
        manifest = {
            "format": 1,
            "name": inspect_result["name"],
            "version": inspect_result["version"],
            "installed_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "files": sorted(installed_files),
        }
        atomic_json(staging_dir / "manifest.json", manifest)
        _verify_staged_dir(staging_dir, installed_files)

        if target_dir.exists():
            backup_dir = pack_parent / f".old-asset-pack-{uuid.uuid4().hex}"
            os.replace(target_dir, backup_dir)
            swapped_old = True

        os.replace(staging_dir, target_dir)
        swapped_new = True
    except AssetPackError:
        if swapped_old and not swapped_new and backup_dir is not None and backup_dir.exists() and not target_dir.exists():
            try:
                os.replace(backup_dir, target_dir)
            except OSError:
                pass
        raise
    except Exception as exc:
        if swapped_old and not swapped_new and backup_dir is not None and backup_dir.exists() and not target_dir.exists():
            try:
                os.replace(backup_dir, target_dir)
            except OSError:
                pass
        raise AssetPackError("asset_pack_install_failed", f"Failed to install asset pack: {exc}", 500) from exc
    finally:
        if _path_exists_or_link(staging_dir):
            _rmtree_managed_best_effort(staging_dir)
        if swapped_new:
            if backup_dir is not None and _path_exists_or_link(backup_dir):
                _rmtree_managed_best_effort(backup_dir)
            _sweep_managed_temps(pack_parent, include_old=True)

    return get_status()


def _acquire_lock_or_raise() -> None:
    if not _install_lock.acquire(blocking=False):
        raise AssetPackError("asset_pack_busy", "Another asset pack install or uninstall is in progress", 409)


def install_from_path(raw_path: str) -> dict[str, Any]:
    """Validate and atomically install an asset pack from a local directory or zip path."""
    _require_data_available()
    if _env_override() is not None:
        raise AssetPackError(
            "asset_pack_not_movable",
            "Cannot install asset pack while AMADEUS_ASSET_PACK_DIR is set",
            409,
        )
    _acquire_lock_or_raise()
    try:
        source = _resolve_source_path(raw_path)
        if source.is_dir():
            result, entries = _inspect_directory_internal(source)
            if not result["valid"]:
                raise AssetPackError(
                    "invalid_asset_pack",
                    "Asset pack is incomplete or invalid",
                    422,
                    extra={"inspect": result},
                )

            def write_from_dir(staging_dir: Path) -> list[str]:
                copied: list[str] = []
                staging_resolved = staging_dir.resolve()
                for disk_path, rel in entries:
                    if _is_link(disk_path):
                        raise AssetPackError("unsafe_source_path", f"Linked source file rejected: {rel}", 422)
                    dest = (staging_dir / rel).resolve()
                    if not dest.is_relative_to(staging_resolved):
                        raise AssetPackError("unsafe_source_path", f"Target escapes staging: {rel}", 422)
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    if _path_exists_or_link(dest):
                        raise AssetPackError("unsafe_source_path", f"Duplicate staged target path: {rel}", 422)
                    try:
                        with disk_path.open("rb") as src, dest.open("xb") as out:
                            shutil.copyfileobj(src, out, length=STREAM_CHUNK_BYTES)
                    except FileExistsError as exc:
                        raise AssetPackError("unsafe_source_path", f"Duplicate staged target path: {rel}", 422) from exc
                    copied.append(rel)
                return copied

            return _stage_and_swap(inspect_result=result, writer_fn=write_from_dir)

        if source.is_file():
            if source.stat().st_size > MAX_UPLOAD_BYTES:
                raise AssetPackError("payload_too_large", "Archive file exceeds maximum allowed size", 413)
            return _install_zip_locked(source)

        raise AssetPackError("unsafe_source_path", "Source path must be a regular file or directory", 422)
    finally:
        _install_lock.release()


def _install_zip_locked(zip_path: Path) -> dict[str, Any]:
    result, entries = _inspect_zip_internal(zip_path)
    if not result["valid"]:
        raise AssetPackError(
            "invalid_asset_pack",
            "Asset pack is incomplete or invalid",
            422,
            extra={"inspect": result},
        )

    def write_from_zip(staging_dir: Path) -> list[str]:
        copied: list[str] = []
        staging_resolved = staging_dir.resolve()
        actual_total = 0
        with zipfile.ZipFile(zip_path, "r") as zf:
            for info, rel in entries:
                dest = (staging_dir / rel).resolve()
                if not dest.is_relative_to(staging_resolved):
                    raise AssetPackError("unsafe_archive", f"Archive entry escapes staging: {rel}", 422)
                dest.parent.mkdir(parents=True, exist_ok=True)
                if _path_exists_or_link(dest):
                    raise AssetPackError("unsafe_archive", f"Archive entry collides in staging: {rel}", 422)
                entry_bytes = 0
                try:
                    with zf.open(info, "r") as src, dest.open("xb") as dst:
                        while True:
                            chunk = src.read(STREAM_CHUNK_BYTES)
                            if not chunk:
                                break
                            entry_bytes += len(chunk)
                            actual_total += len(chunk)
                            if entry_bytes > MAX_ENTRY_UNCOMPRESSED_BYTES or actual_total > MAX_TOTAL_UNCOMPRESSED_BYTES:
                                raise AssetPackError("unsafe_archive", f"Archive entry exceeds size limit: {info.filename}", 422)
                            if entry_bytes > MIN_RATIO_CHECK_BYTES and (
                                info.compress_size <= 0 or (entry_bytes / info.compress_size) > MAX_COMPRESSION_RATIO
                            ):
                                raise AssetPackError("unsafe_archive", f"Archive entry exceeds compression ratio limit: {info.filename}", 422)
                            dst.write(chunk)
                except FileExistsError as exc:
                    raise AssetPackError("unsafe_archive", f"Archive entry collides in staging: {rel}", 422) from exc
                copied.append(rel)
        return copied

    return _stage_and_swap(inspect_result=result, writer_fn=write_from_zip)


def install_from_zip_file(zip_path: Path) -> dict[str, Any]:
    """Validate and atomically install an asset pack from a closed zip file."""
    _require_data_available()
    if _env_override() is not None:
        raise AssetPackError(
            "asset_pack_not_movable",
            "Cannot install asset pack while AMADEUS_ASSET_PACK_DIR is set",
            409,
        )
    if not zip_path.is_file():
        raise AssetPackError("source_not_found", "Uploaded archive file not found", 422)
    if zip_path.stat().st_size > MAX_UPLOAD_BYTES:
        raise AssetPackError("payload_too_large", "Uploaded archive exceeds size limit", 413)
    _acquire_lock_or_raise()
    try:
        return _install_zip_locked(zip_path)
    finally:
        _install_lock.release()


def uninstall_pack() -> dict[str, Any]:
    """Remove the installed asset pack from the managed data directory."""
    _require_data_available()
    if _env_override() is not None:
        raise AssetPackError(
            "asset_pack_not_movable",
            "Cannot remove asset pack while AMADEUS_ASSET_PACK_DIR is set",
            409,
        )
    _acquire_lock_or_raise()
    try:
        data_root = db.get_data_root()
        _ensure_no_ancestor_links(data_root, code="unsafe_target_path", status=409)
        pack_parent = data_root / "asset-pack"
        target_dir = pack_parent / "amadeus-original"
        if _path_exists_or_link(pack_parent) and _is_link(pack_parent):
            raise AssetPackError("unsafe_target_path", "Asset pack parent directory cannot be a link", 409)
        _sweep_managed_temps(pack_parent, include_old=False)
        if _path_exists_or_link(target_dir):
            if _is_link(target_dir):
                raise AssetPackError("unsafe_target_path", "Asset pack target directory cannot be a link", 409)
            before_snapshot = _snapshot_tree_paths(target_dir)
            trash_dir = pack_parent / f".del-asset-pack-{uuid.uuid4().hex}"
            try:
                os.replace(target_dir, trash_dir)
            except OSError as exc:
                raise AssetPackError("asset_pack_remove_failed", f"Failed to remove asset pack: {exc}", 500) from exc
            try:
                _rmtree_managed(trash_dir)
            except OSError as exc:
                if (
                    not _path_exists_or_link(target_dir)
                    and _path_exists_or_link(trash_dir)
                    and not _is_link(trash_dir)
                    and before_snapshot is not None
                    and _snapshot_tree_paths(trash_dir) == before_snapshot
                ):
                    try:
                        os.replace(trash_dir, target_dir)
                    except OSError:
                        pass
                raise AssetPackError("asset_pack_remove_failed", f"Failed to remove asset pack: {exc}", 500) from exc
        _sweep_managed_temps(pack_parent, include_old=True)
        if pack_parent.is_dir():
            try:
                pack_parent.rmdir()
            except OSError:
                pass
        return get_status()
    finally:
        _install_lock.release()
