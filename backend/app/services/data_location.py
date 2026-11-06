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
