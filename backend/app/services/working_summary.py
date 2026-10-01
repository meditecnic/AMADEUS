"""D38 working-summary storage + generation normalization (S3E-3).

Working summary = TEMPORARY conversation continuity ONLY. It is never a
second authority for durable stable user facts, profile/preferences, or
long-term episodic memory.

This module owns:
- the versioned storage encoding boundary (DB representation),
- the generated-output normalizer (sentinel / success-empty / failure),
- the fail-closed decoder (legacy unversioned or unknown markers → "").

Pure / local / deterministic. No network, no model, no DB.
"""

from __future__ import annotations

# Frozen storage marker: versioned rows generated under the D38 contract.
# The exact spelling is frozen by tests. Runtime representation is ALWAYS the
# decoded plain payload; this marker must never leak through model APIs.
_STORAGE_PREFIX = "AMADEUS_WORKING_SUMMARY_D38_V1\n"

# Frozen generated-output sentinel: compression/extraction succeeded but there
# is no short-term continuity worth retaining.
SENTINEL = "NO_WORKING_CONTEXT"


def encode_working_summary(text: str) -> str:
    """DB representation: versioned encoded text.

    The reserved storage marker is an internal trust token: it is never valid
    runtime/user-facing content. Input containing it is REJECTED (fail closed)
    instead of being silently certified, stripped, or double-encoded — a caller
    must never be able to forge the D38 storage representation through the
    public save boundary.
    """
    value = str(text or "")
    if _STORAGE_PREFIX in value:
        raise ValueError("working_summary_reserved_storage_marker")
    return _STORAGE_PREFIX + value


def decode_working_summary(stored: str | None) -> str:
    """Runtime representation: plain decoded text, fail closed.

    Recognized current prefix → decoded payload.
    Legacy unversioned text / unknown/future marker / None / malformed → "".
    The newest row is authoritative; never scan backward past an unsupported
    newest row.
    """
    if not isinstance(stored, str):
        return ""
    if stored.startswith(_STORAGE_PREFIX):
        return stored[len(_STORAGE_PREFIX) :]
    return ""


def normalize_generated_working_summary(raw: object) -> tuple[bool, str]:
    """Normalize generated output: (success, summary).

    success=True means "generated under the D38 contract":
    - sentinel "NO_WORKING_CONTEXT" → (True, "") — successful empty, durable.
    - non-empty plain text → (True, stripped text).
    failure (False, ""):
    - raw is not a string / empty / whitespace only / provider exception
      (the caller converts exceptions to failure before reaching here).
    - reserved storage marker appears in the output — such output is invalid
      and must never become runtime summary content.
    """
    if not isinstance(raw, str):
        return False, ""
    if _STORAGE_PREFIX in raw:
        return False, ""
    stripped = raw.strip()
    if stripped == SENTINEL:
        return True, ""
    if not stripped:
        return False, ""
    return True, stripped
