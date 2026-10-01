"""Write-only provider credential storage backed by Windows Credential Manager."""

from __future__ import annotations

import ctypes
import os
import re
import sys
import tempfile
from ctypes import wintypes
from pathlib import Path
from typing import Protocol


# Built-in ids (deepseek, custom, …) plus multi-profile `custom:<uuid>`.
_PROVIDER_ID = re.compile(
    r"^(?:"
    r"[a-z0-9][a-z0-9._-]{0,63}"
    r"|custom:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
    r")$",
    re.IGNORECASE,
)
_ENV_KEY = re.compile(r"^[A-Z_][A-Z0-9_]*$")


def _target(provider_id: str) -> str:
    if not _PROVIDER_ID.fullmatch(provider_id):
        raise ValueError("invalid provider id")
    # Windows Credential Manager target names cannot contain ':'; map to safe form.
    safe_id = provider_id.replace(":", "/")
    return f"Amadeus/provider/{safe_id}"


def scrub_dotenv_key(path: Path, key: str) -> bool:
    """Atomically remove one plaintext secret assignment from a dotenv file."""
    if not _ENV_KEY.fullmatch(key):
        raise ValueError("invalid environment variable name")
    if not path.exists():
        return False

    original = path.read_text(encoding="utf-8")
    assignment = re.compile(rf"^\s*(?:export\s+)?{re.escape(key)}\s*=")
    retained = [
        line
        for line in original.splitlines(keepends=True)
        if not assignment.match(line)
    ]
    updated = "".join(retained)
    if updated == original:
        return False

    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            newline="",
            delete=False,
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
        ) as temporary:
            temporary.write(updated)
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary_name = temporary.name
        os.replace(temporary_name, path)
    finally:
        if temporary_name and os.path.exists(temporary_name):
            os.unlink(temporary_name)
    return True


class CredentialStore(Protocol):
    def set(self, provider_id: str, secret: str) -> None: ...
    def get(self, provider_id: str) -> str | None: ...
    def delete(self, provider_id: str) -> None: ...


class InMemoryCredentialStore:
    """Non-persistent test store; never used as the Windows production backend."""

    def __init__(self) -> None:
        self._values: dict[str, str] = {}

    def set(self, provider_id: str, secret: str) -> None:
        _target(provider_id)
        if not secret:
            raise ValueError("credential must not be empty")
        self._values[provider_id] = secret

    def get(self, provider_id: str) -> str | None:
        _target(provider_id)
        return self._values.get(provider_id)

    def delete(self, provider_id: str) -> None:
        _target(provider_id)
        self._values.pop(provider_id, None)


if sys.platform == "win32":
    class _CREDENTIALW(ctypes.Structure):
        _fields_ = [
            ("Flags", wintypes.DWORD),
            ("Type", wintypes.DWORD),
            ("TargetName", wintypes.LPWSTR),
            ("Comment", wintypes.LPWSTR),
            ("LastWritten", wintypes.FILETIME),
            ("CredentialBlobSize", wintypes.DWORD),
            ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
            ("Persist", wintypes.DWORD),
            ("AttributeCount", wintypes.DWORD),
            ("Attributes", ctypes.c_void_p),
            ("TargetAlias", wintypes.LPWSTR),
            ("UserName", wintypes.LPWSTR),
        ]


class WindowsCredentialStore:
    CRED_TYPE_GENERIC = 1
    CRED_PERSIST_LOCAL_MACHINE = 2
    ERROR_NOT_FOUND = 1168

    def __init__(self) -> None:
        if sys.platform != "win32":
            raise RuntimeError("Windows Credential Manager is only available on Windows")
        self._advapi = ctypes.WinDLL("Advapi32.dll", use_last_error=True)
        self._advapi.CredWriteW.argtypes = [ctypes.POINTER(_CREDENTIALW), wintypes.DWORD]
        self._advapi.CredWriteW.restype = wintypes.BOOL
        credential_pointer = ctypes.POINTER(_CREDENTIALW)
        self._advapi.CredReadW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.POINTER(credential_pointer),
        ]
        self._advapi.CredReadW.restype = wintypes.BOOL
        self._advapi.CredDeleteW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
        ]
        self._advapi.CredDeleteW.restype = wintypes.BOOL
        self._advapi.CredFree.argtypes = [ctypes.c_void_p]
        self._advapi.CredFree.restype = None

    def set(self, provider_id: str, secret: str) -> None:
        target = _target(provider_id)
        if not secret:
            raise ValueError("credential must not be empty")
        encoded = secret.encode("utf-16-le")
        blob = (ctypes.c_ubyte * len(encoded)).from_buffer_copy(encoded)
        credential = _CREDENTIALW()
        credential.Type = self.CRED_TYPE_GENERIC
        credential.TargetName = target
        credential.CredentialBlobSize = len(encoded)
        credential.CredentialBlob = ctypes.cast(blob, ctypes.POINTER(ctypes.c_ubyte))
        credential.Persist = self.CRED_PERSIST_LOCAL_MACHINE
        credential.UserName = provider_id
        if not self._advapi.CredWriteW(ctypes.byref(credential), 0):
            raise ctypes.WinError(ctypes.get_last_error())

    def get(self, provider_id: str) -> str | None:
        target = _target(provider_id)
        pointer = ctypes.POINTER(_CREDENTIALW)()
        if not self._advapi.CredReadW(
            target,
            self.CRED_TYPE_GENERIC,
            0,
            ctypes.byref(pointer),
        ):
            error = ctypes.get_last_error()
            if error == self.ERROR_NOT_FOUND:
                return None
            raise ctypes.WinError(error)
        try:
            credential = pointer.contents
            raw = ctypes.string_at(
                credential.CredentialBlob,
                credential.CredentialBlobSize,
            )
            return raw.decode("utf-16-le")
        finally:
            self._advapi.CredFree(pointer)

    def delete(self, provider_id: str) -> None:
        target = _target(provider_id)
        if self._advapi.CredDeleteW(target, self.CRED_TYPE_GENERIC, 0):
            return
        error = ctypes.get_last_error()
        if error != self.ERROR_NOT_FOUND:
            raise ctypes.WinError(error)


def _select_credential_store() -> CredentialStore:
    """Bind the process-wide store once, at import.

    ``AMADEUS_CREDENTIAL_BACKEND=memory`` selects the non-persistent store
    before Windows Credential Manager is constructed. Changing DATA_DIR
    alone does not isolate system credentials.
    """
    backend = (os.environ.get("AMADEUS_CREDENTIAL_BACKEND") or "").strip().lower()
    if backend in {"memory", "inmemory", "in-memory"}:
        return InMemoryCredentialStore()
    if sys.platform == "win32":
        return WindowsCredentialStore()
    return InMemoryCredentialStore()


credential_store: CredentialStore = _select_credential_store()
WINDOWS_CREDENTIAL_STORE_CONSTRUCTED = (
    type(credential_store).__name__ == "WindowsCredentialStore"
)
