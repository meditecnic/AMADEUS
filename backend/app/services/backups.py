"""SQLite online snapshots and restore-before-opening startup protocol."""
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import shutil
import sqlite3
import tempfile
import threading
import time

from app import db
from app.security.passcode import atomic_json

REASONS = {"manual", "pre-upgrade", "weekly", "pre-restore"}
ID_PATTERN = re.compile(r"[0-9]{8}T[0-9]{12}Z-(manual|pre-upgrade|weekly|pre-restore)")
CONFIG_FILES = {"auth/passcode.json"}
_lock = threading.RLock()
upgrade_backup_id: str | None = None


class BackupError(RuntimeError):
    def __init__(self, code: str, detail: str, status: int = 500):
        super().__init__(detail)
        self.code, self.status = code, status


def _root() -> Path:
    return db.get_data_root()


def _local(base: Path, relative: str) -> Path:
    path = base / relative
    if Path(relative).is_absolute() or ".." in Path(relative).parts or "\\" in relative:
        raise BackupError("invalid_backup", "Invalid file path", 400)
    if not path.resolve().is_relative_to(base.resolve()) or any(
        part.is_symlink() for part in [path, *path.parents] if part != base.parent
    ):
        raise BackupError("invalid_backup", "Symbolic links are not supported", 400)
    return path


def database_files() -> set[str]:
    paths = {_root() / "diagnostics.sqlite3", _root() / "amadeus.db"}
    paths.add(Path(db._resolve_path("steins_gate", "control")))
    for worldline in ("steins_gate", "beta"):
        paths.update(Path(db._resolve_path(worldline, kind)) for kind in ("history", "memory"))
    return {path.relative_to(_root()).as_posix() for path in paths if path.is_relative_to(_root())}


def _directory(backup_id: str) -> Path:
    if not ID_PATTERN.fullmatch(backup_id):
        raise BackupError("invalid_backup_id", "Invalid backup id", 400)
    return _local(_root(), f"backups/{backup_id}")


def _check_stop(stop: threading.Event | None) -> None:
    if stop is not None and stop.is_set():
        raise BackupError("backup_stopped", "Weekly backup stopped during shutdown", 409)


def _hash(path: Path, stop: threading.Event | None = None) -> str:
    with path.open("rb") as handle:
        if stop is None:
            return hashlib.file_digest(handle, "sha256").hexdigest()
        digest = hashlib.sha256()
        while chunk := handle.read(1024 * 1024):
            _check_stop(stop)
            digest.update(chunk)
        _check_stop(stop)
        return digest.hexdigest()


def _schema(connection: sqlite3.Connection) -> int | None:
    if connection.execute("SELECT 1 FROM sqlite_master WHERE name='schema_meta'").fetchone():
        row = connection.execute("SELECT value FROM schema_meta WHERE key='schema_version'").fetchone()
        return int(row[0]) if row else None
    return connection.execute("PRAGMA user_version").fetchone()[0]


def _snapshot(source: Path, target: Path, stop: threading.Event | None = None) -> int | None:
    started = time.monotonic()

    def progress(*_):
        _check_stop(stop)
        if time.monotonic() - started > 30:
            raise BackupError("backup_busy", "Database remained busy for 30 seconds; retry later", 409)

    with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True, timeout=5)) as src:
        version = _schema(src)
        with closing(sqlite3.connect(target)) as dst:
            src.backup(dst, pages=256, progress=progress)
        return version


def load_manifest(backup_id: str, *, verify: bool = True) -> dict:
    directory = _directory(backup_id)
    if not directory.is_dir():
        raise BackupError("backup_not_found", "Backup does not exist", 404)
    try:
        manifest = json.loads(_local(directory, "manifest.json").read_text(encoding="utf-8"))
        if (manifest["format"] != 1 or manifest["id"] != backup_id
                or not (manifest["files"] or manifest.get("unreadable"))):
            raise ValueError("unsupported or empty manifest")
        seen = set()
        for item in manifest["files"]:
            relative = item["path"]
            if relative not in database_files() | CONFIG_FILES or relative in seen:
                raise ValueError("unexpected or duplicate file")
            seen.add(relative)
            path = _local(directory, relative)
            if relative in database_files() and (item["schema_version"] or 0) > db.SCHEMA_VERSION:
                raise ValueError("backup uses a newer database schema")
            if verify and (path.stat().st_size != item["size"] or _hash(path) != item["sha256"]):
                raise ValueError(f"checksum mismatch: {relative}")
        if verify and manifest.get("unreadable"):
            raise ValueError("snapshot contains unreadable databases; originals retained for rollback")
        return manifest
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise BackupError("invalid_backup", f"Backup validation failed: {exc}", 400) from exc


def list_backups() -> list[dict]:
    result = []
    base = _root() / "backups"
    for directory in sorted(base.iterdir(), reverse=True) if base.exists() else []:
        if not ID_PATTERN.fullmatch(directory.name) or not directory.is_dir():
            continue
        item = {"id": directory.name, "time": None, "reason": directory.name.split("Z-", 1)[1],
                "size": 0, "app_version": None, "can_restore": False}
        try:
            manifest = load_manifest(directory.name, verify=False)
            item.update(time=manifest["created_at"], app_version=manifest["app_version"],
                        size=sum(f["size"] for f in manifest["files"]))
            load_manifest(directory.name)
            item["can_restore"] = True
        except (BackupError, KeyError, TypeError) as exc:
            item["detail"] = str(exc)
        result.append(item)
    return result


def _prune(reason: str, protected: set[str]) -> None:
    if reason == "manual":
        return
    matches = [p for p in sorted((_root() / "backups").iterdir(), reverse=True)
               if ID_PATTERN.fullmatch(p.name) and p.name.endswith("-" + reason)]
    for path in matches[3:]:
        if path.name not in protected and not path.is_symlink():
            shutil.rmtree(path)


def create_backup(reason: str = "manual", *, salvage: bool = False, stop: threading.Event | None = None) -> dict:
    with _lock:
        _check_stop(stop)
        if reason not in REASONS:
            raise ValueError("unsupported backup reason")
        root = _root()
        root.mkdir(parents=True, exist_ok=True)
        files = sorted(name for name in database_files() | CONFIG_FILES if _local(root, name).exists())
        estimate = sum(_local(root, name).stat().st_size + (
            Path(str(root / name) + "-wal").stat().st_size
            if Path(str(root / name) + "-wal").exists() else 0) for name in files)
        needed = int(estimate * 1.2)
        if shutil.disk_usage(root).free < needed:
            raise BackupError("insufficient_space", f"Backup requires {needed} bytes of free space")
        base = _local(root, "backups")
        base.mkdir(exist_ok=True)
        now = datetime.now(timezone.utc)
        backup_id = now.strftime("%Y%m%dT%H%M%S%fZ") + "-" + reason
        staging = Path(tempfile.mkdtemp(prefix=".tmp-", dir=base))
        manifest = {"format": 1, "id": backup_id, "created_at": now.isoformat(),
                    "app_version": os.environ.get("AMADEUS_APP_VERSION") or "dev",
                    "reason": reason, "files": [], "unreadable": []}
        try:
            for relative in files:
                _check_stop(stop)
                source, target = _local(root, relative), staging / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                version = None
                if relative in database_files():
                    try:
                        version = _snapshot(source, target, stop)
                    except sqlite3.DatabaseError:
                        if not salvage:
                            raise
                        target.unlink(missing_ok=True)
                        manifest["unreadable"].append(relative)
                        continue
                else:
                    shutil.copyfile(source, target)
                manifest["files"].append({"path": relative, "schema_version": version,
                                          "size": target.stat().st_size, "sha256": _hash(target, stop)})
            atomic_json(staging / "manifest.json", manifest)
            _check_stop(stop)
            os.replace(staging, base / backup_id)
        except Exception as exc:
            shutil.rmtree(staging)
            if isinstance(exc, BackupError):
                raise
            raise BackupError("backup_failed", f"Backup failed: {exc}") from exc
        request = _root() / "restore-request.json"
        protected = set()
        if request.exists():
            pending = json.loads(request.read_text(encoding="utf-8"))
            protected.update({pending["id"], pending.get("rollback_id", "")})
        _prune(reason, protected)
        return manifest


def schedule_restore(backup_id: str) -> dict:
    with _lock:
        request_path = _root() / "restore-request.json"
        if request_path.exists() and json.loads(request_path.read_text(encoding="utf-8")).get("phase") == "applying":
            raise BackupError("restore_pending", "An interrupted rollback must finish first", 409)
        load_manifest(backup_id)
        atomic_json(request_path, {"id": backup_id, "phase": "pending"})
        return {"restart_required": True}


def delete_backup(backup_id: str) -> dict:
    with _lock:
        directory = _directory(backup_id)
        request = _root() / "restore-request.json"
        if request.exists():
            pending = json.loads(request.read_text(encoding="utf-8"))
            if backup_id in {pending["id"], pending.get("rollback_id")}:
                raise BackupError("restore_pending", "This backup is scheduled for restore or rollback", 409)
        if not directory.is_dir():
            raise BackupError("backup_not_found", "Backup does not exist", 404)
        shutil.rmtree(directory)
        return {"status": "success"}


def _restore_files(manifest: dict, source: Path) -> None:
    for item in manifest["files"]:
        relative = item["path"]
        target = _local(_root(), relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(_local(source, relative), target)
        if _hash(target) != item["sha256"]:
            raise BackupError("restore_checksum_failed", f"Restored checksum mismatch: {relative}")


def apply_pending_restore() -> str | None:
    """Run before opening ANY database. Persist a journal before moving files.

    Unreadable databases cannot use SQLite backup: retain their original bytes
    by renaming them into the pre-restore snapshot, never by copying a live DB.
    """
    with _lock:
        request_path = _root() / "restore-request.json"
        if not request_path.exists():
            return None
        try:
            request = json.loads(request_path.read_text(encoding="utf-8"))
            interrupted = request.get("phase") == "applying"
            if not interrupted:
                manifest = load_manifest(request["id"])
                rollback = create_backup("pre-restore", salvage=True)
                originals = sorted(database_files() | CONFIG_FILES)
                paths = [p for name in originals for p in (name, name + "-wal", name + "-shm")
                         if _local(_root(), p).exists()]
                request.update(phase="applying", rollback_id=rollback["id"], originals=paths)
                atomic_json(request_path, request)
            rollback_dir = _directory(request["rollback_id"]) / "originals"
            if interrupted:
                raise BackupError("restore_interrupted", "Previous restore was interrupted; rolling back")
            for relative in request["originals"]:
                target = _local(rollback_dir, relative)
                target.parent.mkdir(parents=True, exist_ok=True)
                os.replace(_local(_root(), relative), target)
            _restore_files(manifest, _directory(request["id"]))
            # Old migration markers must never suppress initialization of restored data.
            for marker in _root().glob(".migration-v*.complete"):
                marker.unlink()
            atomic_json(_root() / "restore-result.json", {"status": "ok", "detail": None})
            request_path.unlink()
            # Keep unreadable originals for possible manual salvage after recovery.
            # Once committed, cleanup failure must never start a partial rollback.
            try:
                for relative in request["originals"]:
                    if not any(relative == name or relative in {name + "-wal", name + "-shm"}
                               for name in rollback["unreadable"]):
                        _local(rollback_dir, relative).unlink(missing_ok=True)
                if not rollback["unreadable"]:
                    shutil.rmtree(rollback_dir)
            except OSError:
                logging.getLogger(__name__).warning("Restored successfully; old file cleanup deferred")
            return None
        except Exception as exc:
            detail = str(exc)
            try:
                if 'request' in locals() and request.get("phase") == "applying":
                    rollback_dir = _directory(request["rollback_id"]) / "originals"
                    # A file still at its original location was never moved; leave it alone.
                    for relative in database_files() | CONFIG_FILES:
                        saved = _local(rollback_dir, relative)
                        if saved.exists() or relative not in request["originals"]:
                            _local(_root(), relative).unlink(missing_ok=True)
                            for suffix in ("-wal", "-shm"):
                                _local(_root(), relative + suffix).unlink(missing_ok=True)
                    for relative in request["originals"]:
                        saved = _local(rollback_dir, relative)
                        if saved.exists():
                            target = _local(_root(), relative)
                            target.parent.mkdir(parents=True, exist_ok=True)
                            os.replace(saved, target)
                    shutil.rmtree(rollback_dir, ignore_errors=True)
                request_path.unlink(missing_ok=True)
            except Exception as rollback_error:
                detail += f"; rollback incomplete: {rollback_error}; retry on next startup"
            atomic_json(_root() / "restore-result.json", {"status": "restore_failed", "detail": detail})
            return detail


def existing_databases() -> list[Path]:
    return [_local(_root(), name) for name in sorted(database_files()) if _local(_root(), name).exists()]


def cleanup_staging() -> None:
    """Discard unpublished snapshots left by a terminated process, before DB use."""
    with _lock:
        for path in _local(_root(), "backups").glob(".tmp-*"):
            if path.is_dir() and not path.is_symlink():
                shutil.rmtree(path)


def backup_before_upgrade() -> None:
    """Called inside the migration lock, before any initialization writes."""
    global upgrade_backup_id
    upgrade_backup_id = None
    from app.services.diagnostics import DIAGNOSTIC_STORAGE_VERSION

    needs_upgrade = False
    for path in existing_databases():
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as connection:
            version = _schema(connection)
        target = DIAGNOSTIC_STORAGE_VERSION if path.name == "diagnostics.sqlite3" else db.SCHEMA_VERSION
        if version is None or version < target:
            needs_upgrade = True
    if needs_upgrade:
        upgrade_backup_id = create_backup("pre-upgrade")["id"]


def weekly_backup_if_due(stop: threading.Event | None = None) -> None:
    """Best effort background work; callers skip this on a fresh installation."""
    try:
        _check_stop(stop)
        base = _root() / "backups"
        dates = []
        for path in base.iterdir() if base.exists() else []:
            if ID_PATTERN.fullmatch(path.name) and not path.name.endswith("-manual"):
                try:
                    manifest = load_manifest(path.name, verify=False)
                    dates.append(datetime.fromisoformat(manifest["created_at"]))
                except (BackupError, ValueError, KeyError, TypeError):
                    continue
        if not dates or (datetime.now(timezone.utc) - max(dates)).total_seconds() >= 7 * 86400:
            create_backup("weekly", stop=stop)
    except BackupError as exc:
        if exc.code != "backup_stopped":
            logging.getLogger(__name__).warning("Weekly backup failed: %s", type(exc).__name__)
    except Exception as exc:
        logging.getLogger(__name__).warning("Weekly backup failed: %s", type(exc).__name__)
