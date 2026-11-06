"""Optional local startup ritual, not encryption or a security boundary.

Deleting <data>/auth/passcode.json resets it. Backoff only discourages casual
guessing and accidental retries; it is process-local and resets on restart.
"""
import asyncio
import hashlib
import hmac
import json
import os
import tempfile
from pathlib import Path

from fastapi import HTTPException

from app.db import get_data_root

PARAMETERS = {"n": 16384, "r": 8, "p": 1, "dklen": 32}
_lock = asyncio.Lock()
_failures = 0


def normalize_password(p: str) -> str:
    return p.lower().replace("ü", "u").replace("ue", "u").strip()


def passcode_path() -> Path:
    return get_data_root() / "auth" / "passcode.json"


def atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".tmp-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def read_passcode() -> tuple[dict | None, str | None]:
    try:
        record = json.loads(passcode_path().read_text(encoding="utf-8"))
        if (record["algorithm"] != "scrypt" or record["parameters"] != PARAMETERS
                or len(bytes.fromhex(record["salt"])) != 16
                or len(bytes.fromhex(record["hash"])) != 32):
            raise ValueError("invalid scrypt record")
        return record, None
    except FileNotFoundError:
        return None, None
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return None, f"passcode file unreadable ({type(exc).__name__})"


def state() -> dict:
    record, error = read_passcode()
    result = {"passcode_set": record is not None}
    if error:
        result["passcode_error"] = error
    return result


def _derive(value: str, salt: bytes) -> bytes:
    return hashlib.scrypt(value.encode("utf-8"), salt=salt, maxmem=64 * 1024 * 1024, **PARAMETERS)


async def _verify(record: dict | None, value: str | None, *, legacy: bool = False) -> None:
    global _failures
    if record is None:
        valid = not legacy or normalize_password(value or "") == "gott wurfelt nicht"
    else:
        valid = value is not None and hmac.compare_digest(
            await asyncio.to_thread(_derive, value, bytes.fromhex(record["salt"])),
            bytes.fromhex(record["hash"]),
        )
    if not valid:
        _failures += 1
        if _failures >= 5:
            await asyncio.sleep(min(_failures - 4, 30))
        raise HTTPException(401, detail="Invalid password" if legacy else {"code": "invalid_passcode"})
    _failures = 0


async def login(value: str | None, *, legacy: bool = False) -> dict:
    async with _lock:
        record, _ = await asyncio.to_thread(read_passcode)
        await _verify(record, value, legacy=legacy)
        result = {"status": "success"}
        if not legacy:
            result["easter_egg"] = normalize_password(value or "") == "gott wurfelt nicht"
        return result


async def change(current: str | None, new: str | None) -> dict:
    async with _lock:
        record, _ = await asyncio.to_thread(read_passcode)
        await _verify(record, current)
        try:
            if new is None:
                passcode_path().unlink(missing_ok=True)
            else:
                salt = os.urandom(16)
                digest = await asyncio.to_thread(_derive, new, salt)
                await asyncio.to_thread(atomic_json, passcode_path(), {
                    "algorithm": "scrypt", "parameters": PARAMETERS,
                    "salt": salt.hex(), "hash": digest.hex(),
                })
        except OSError as exc:
            raise HTTPException(500, detail={"code": "passcode_write_failed"}) from exc
        return {"status": "success"}
