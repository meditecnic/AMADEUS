"""Data location metadata and one-pass usage accounting (no link traversal)."""
import json
import os
from pathlib import Path
import stat

from app import db
from app.services.backups import BackupError

MARKER = "amadeus-data.json"


def location_file() -> Path | None:
    value = os.environ.get("AMADEUS_LOCATION_FILE")
    return Path(value) if value else None


def read_location() -> dict:
    path = location_file()
    if path is None or not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict) or not Path(value["path"]).is_absolute():
            raise ValueError("invalid location path")
        previous = value.get("previous_path")
        if previous is not None and (not isinstance(previous, str) or not Path(previous).is_absolute()):
            raise ValueError("invalid previous path")
        return value
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise BackupError("location_file_invalid", f"Cannot read location file: {exc}") from exc


def location() -> dict:
    file = location_file()
    record = read_location() if file else {}
    default = file.parent / "data" if file else Path(
        os.environ.get("LOCALAPPDATA", str(Path.home() / ".local/share"))
    ) / "com.amadeus.desktop/data"
    previous = record.get("previous_path")
    return {"path": str(db.get_data_root()), "default_path": str(default),
            "source": ("file" if record else "default") if file else "env",
            "movable": file is not None, "previous_path": previous,
            "previous_exists": Path(previous).exists() if previous else False}


def is_link(path: Path) -> bool:
    info = path.lstat()
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 0x400)


def walk_files(root: Path):
    """Yield relative regular files; skip all Windows reparse points as well."""
    stack = [root]
    while stack:
        directory = stack.pop()
        with os.scandir(directory) as entries:
            for entry in entries:
                info = entry.stat(follow_symlinks=False)
                if entry.is_symlink() or getattr(info, "st_file_attributes", 0) & 0x400:
                    continue
                path = Path(entry.path)
                if stat.S_ISDIR(info.st_mode):
                    stack.append(path)
                elif stat.S_ISREG(info.st_mode):
                    yield path.relative_to(root), info.st_size


def disk_usage() -> dict:
    categories = dict.fromkeys(("conversations_memory", "voice_models", "speech_models", "embedding_models",
                               "asset_pack", "pi_components", "logs", "backups", "other"), 0)
    prefixes = {"voice-pack": "voice_models", "asset-pack": "asset_pack", "pi-agent": "pi_components",
                "logs": "logs", "backups": "backups"}
    for relative, size in walk_files(db.get_data_root()):
        parts = relative.parts
        if relative.name.endswith((".sqlite3", ".sqlite3-wal", ".sqlite3-shm", ".db", ".db-wal", ".db-shm")) and parts[0] != "backups":
            category = "conversations_memory"
        elif parts[:2] == ("models", "speech"):
            category = "speech_models"
        else:
            category = prefixes.get(parts[0], "other")
        categories[category] += size
    return {"categories": categories, "total_bytes": sum(categories.values())}


def _volume(path: Path) -> tuple[int | None, str | None]:
    if os.name != "nt":
        return None, None
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetVolumePathNameW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
    kernel.GetDriveTypeW.argtypes = [wintypes.LPCWSTR]
    kernel.GetVolumeInformationW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD,
                                           ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.DWORD),
                                           ctypes.POINTER(wintypes.DWORD), wintypes.LPWSTR, wintypes.DWORD]
    volume, filesystem = ctypes.create_unicode_buffer(32768), ctypes.create_unicode_buffer(256)
    if not kernel.GetVolumePathNameW(str(path), volume, len(volume)):
        raise ctypes.WinError(ctypes.get_last_error())
    if not kernel.GetVolumeInformationW(volume.value, None, 0, None, None, None, filesystem, len(filesystem)):
        raise ctypes.WinError(ctypes.get_last_error())
    return kernel.GetDriveTypeW(volume.value), filesystem.value


def _sync_roots() -> list[Path]:
    roots = [Path(value) for key in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial")
             if (value := os.environ.get(key))]
    for key in ("APPDATA", "LOCALAPPDATA"):
        if not os.environ.get(key):
            continue
        try:
            info = json.loads((Path(os.environ[key]) / "Dropbox/info.json").read_text(encoding="utf-8"))
            roots.extend(Path(account["path"]) for account in info.values()
                         if isinstance(account, dict) and isinstance(account.get("path"), str))
        except (OSError, ValueError, TypeError, AttributeError):
            pass
    return roots


def check_target(raw: str) -> dict:
    import math
    import shutil
    import sys
    import tempfile

    result = {"ok": False, "errors": [], "warnings": [],
              "needed_bytes": math.ceil(disk_usage()["total_bytes"] * 1.2), "free_bytes": 0}

    def error(code, message):
        result["errors"].append({"code": code, "message": message})

    path = Path(raw)
    if not raw.strip() or not path.is_absolute():
        error("path_not_absolute", "Choose an absolute directory path")
        return result
    created = []
    try:
        target, current = path.resolve(), db.get_data_root()
        if target.is_relative_to(current) or current.is_relative_to(target):
            error("path_overlap", "Current and target directories must not contain each other")
        protected = [Path(sys.executable).parent] if getattr(sys, "frozen", False) else [Path(__file__).resolve().parents[3]]
        protected.extend(Path(v) for key in ("SystemRoot", "WINDIR", "ProgramFiles", "ProgramFiles(x86)",
                                             "ProgramW6432", "AMADEUS_INSTALL_DIR") if (v := os.environ.get(key)))
        if target == target.parent or any(target.is_relative_to(p.resolve()) for p in protected):
            error("system_directory", "System and application installation directories cannot hold user data")
        if any(p.exists() and is_link(p) for p in (path, *path.parents)):
            error("path_link", "Target path must not traverse symbolic links or junctions")
        if target.exists() and (not target.is_dir() or any(target.iterdir())):
            error("target_not_empty", "Target must be an empty directory or a new directory")
        if result["errors"]:
            return result
        cursor = target
        while not cursor.exists():
            created.append(cursor)
            cursor = cursor.parent
        target.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryFile(dir=target) as probe:
            probe.write(b"amadeus-write-check")
            probe.flush()
            os.fsync(probe.fileno())
        result["free_bytes"] = shutil.disk_usage(target).free
        if result["free_bytes"] < result["needed_bytes"]:
            error("insufficient_space", "Target needs 1.2 times the current data size")
        drive, filesystem = _volume(target)
        if filesystem and filesystem.upper() == "FAT32":
            error("filesystem_fat32", "FAT32 cannot store model files larger than 4 GB")
        warnings = result["warnings"]
        if any(target.is_relative_to(root.resolve()) for root in _sync_roots()):
            warnings.append({"code": "sync_directory", "message": "Sync software can corrupt SQLite databases"})
        if raw.startswith(("\\\\", "//")) or drive == 4:
            warnings.append({"code": "network_drive", "message": "Network drives can corrupt SQLite databases"})
        if drive == 2:
            warnings.append({"code": "removable_drive", "message": "Removing this drive makes your data unavailable"})
    except (OSError, ValueError, RuntimeError) as exc:
        error("path_unwritable", f"Cannot prepare target directory: {exc}")
    finally:
        for directory in created:  # leaf first; never remove pre-existing directories
            try:
                directory.rmdir()
            except OSError:
                pass
        result["ok"] = not result["errors"]
    return result
