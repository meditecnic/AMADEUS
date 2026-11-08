"""Local-only support archives built from explicit metadata/log allowlists."""
from contextlib import closing
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import re
import sqlite3
import threading
from urllib.parse import urlsplit
from zipfile import ZipFile, ZIP_DEFLATED

from app import db
from app.security import local_transport
from app.services import backups, credentials, data_location, data_startup
from app.services.provider_runtime import provider_registry
from app.services.search import SEARCH_CREDENTIAL_BY_PROVIDER, search_service

LOG_NAMES = ("backend-stdout.log", "backend-stderr.log", "backend-startup.log")
MAX_LOG_BYTES = 5 * 1024 * 1024
_lock = threading.Lock()


def _credential(provider: str) -> str:
    try:
        return credentials.credential_store.get(provider) or ""
    except (OSError, ValueError):
        return ""


def _secrets() -> list[str]:
    ids = [d.id for d in provider_registry.list_definitions()]
    values = [_credential(p) for p in [*ids, *SEARCH_CREDENTIAL_BY_PROVIDER.values()]]
    values += [search_service.configured_key(p) for p in SEARCH_CREDENTIAL_BY_PROVIDER]
    values.append(local_transport.LOCAL_API_TOKEN.decode("utf-8"))
    return sorted({s for s in values if s}, key=len, reverse=True)


def redact(text: str, secrets: list[str]) -> str:
    for secret in secrets:
        text = text.replace(secret, "[REDACTED]")
    text = re.sub(r"(?i)\bBearer\s+[^\s\"',;]+", "Bearer [REDACTED]", text)
    text = re.sub(r"\b(?:sk-[A-Za-z0-9_-]{8,}|AIza[A-Za-z0-9_-]{20,}|[a-fA-F0-9]{64})\b", "[REDACTED]", text)
    text = re.sub(r'''(?i)(["']?(?:api[_-]?key|(?:local[_-]?)?(?:api[_-]?)?token|password|passcode|authorization)["']?\s*[:=]\s*)[^\r\n]+''', r"\1[REDACTED]", text)
    text = re.sub(r'''(?i)[a-z]:[\\/]+Users[\\/]+[^\\/\r\n"<>|]+''', "%USERPROFILE%", text)
    # URLs may contain credentials in userinfo, paths, or query parameters.
    text = re.sub(r'''https?://[^\s"'<>]+''', lambda m: _host(m[0]) or "[URL]", text)
    # Older builds logged response previews. Do not carry those into support bundles.
    text = re.sub(r"preview=.*", "preview=[REDACTED]", text)
    return text


def _host(url: str) -> str | None:
    try:
        return urlsplit(url).hostname
    except ValueError:
        return None


def _sanitize(value, secrets):
    if isinstance(value, str):
        return redact(value, secrets)
    if isinstance(value, list):
        return [_sanitize(v, secrets) for v in value]
    if isinstance(value, dict):
        return {k: _sanitize(v, secrets) for k, v in value.items()}
    return value


def _schema_versions() -> dict:
    result = {}
    for relative in sorted(backups.database_files()):
        path = db.get_data_root() / relative
        if not path.is_file() or data_location.is_link(path):
            result[relative] = None
            continue
        try:
            with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=0.2)) as connection:
                result[relative] = backups._schema(connection)
        except (sqlite3.Error, ValueError, OSError):
            result[relative] = "unreadable"
    return result


def _providers() -> list[dict]:
    result = []
    for definition in provider_registry.list_definitions():
        if definition.credential_required and not _credential(definition.id):
            continue
        snapshot = provider_registry.snapshot(definition.id, allow_unavailable=True)
        result.append({"kind": "openai-compatible" if definition.id.startswith("custom") else definition.id,
                       "model_id": snapshot.model_id,
                       "host": _host(str(getattr(snapshot.adapter, "base_url", "")))})
    return result


def _summary(settings: dict) -> dict:
    errors = {}
    status = data_startup.current_status()
    try:
        status["backups"] = backups.list_backups()
    except (OSError, ValueError, backups.BackupError):
        status["backups"] = []
        errors["backups"] = "unavailable"
    try:
        disk = data_location.disk_usage()
    except OSError:
        disk = None
        errors["disk_usage"] = "unavailable"
    asset_root = Path(os.environ.get("AMADEUS_ASSET_PACK_DIR") or db.get_data_root() / "asset-pack/amadeus-original")
    return {"app_version": os.environ.get("AMADEUS_APP_VERSION") or "dev",
            "windows_version": platform.win32_ver(), "os": platform.system(),
            "data_status": status, "schema_versions": _schema_versions(), "disk_usage": disk,
            "providers": _providers(), "voice_enabled": bool(settings.get("enable_tts", True)),
            "web_search_enabled": search_service.any_paid_provider_configured(),
            "asset_pack_installed": (asset_root / "manifest.json").is_file(), "errors": errors}


def create_bundle(settings: dict) -> dict:
    with _lock:
        root = db.get_data_root()
        directory = root / "diagnostics"
        if directory.exists() and data_location.is_link(directory):
            raise OSError("diagnostics directory cannot be a link")
        directory.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        destination = directory / f"amadeus-diagnostics-{timestamp}.zip"
        staging = destination.with_suffix(".tmp")
        secrets = _secrets()
        location = data_location.location_file()
        logs = (location.parent if location else root) / "logs"
        try:
            with ZipFile(staging, "w", ZIP_DEFLATED) as archive:
                summary = _sanitize(_summary(settings), secrets)
                archive.writestr("summary.json", json.dumps(summary, ensure_ascii=False, indent=2))
                if logs.is_dir() and not data_location.is_link(logs):
                    for name in LOG_NAMES:
                        count = 0
                        for suffix in ("", ".1", ".2", ".3"):
                            path = logs / (name + suffix)
                            if count == 3 or not path.is_file() or data_location.is_link(path):
                                continue
                            try:
                                with path.open("rb") as stream:
                                    content = stream.read(MAX_LOG_BYTES)
                            except OSError:
                                continue
                            archive.writestr("logs/" + path.name, redact(content.decode("utf-8", errors="replace"), secrets))
                            count += 1
            staging.replace(destination)
            packages = sorted(p for p in directory.glob("amadeus-diagnostics-*.zip")
                              if re.fullmatch(r"amadeus-diagnostics-\d{8}T\d{12}Z\.zip", p.name)
                              and p.is_file() and not data_location.is_link(p))
            for old in packages[:-5]:
                old.unlink()
            return {"path": str(destination), "size": destination.stat().st_size}
        finally:
            staging.unlink(missing_ok=True)
