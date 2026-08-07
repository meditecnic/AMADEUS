"""Stable contracts and error types for Memory v11 (S1+S2)."""

from __future__ import annotations

from enum import Enum
from typing import Final


PIPELINE_VERSION: Final[str] = "memory-v11-1"
MAX_PINNED_FACTS_PER_SCOPE: Final[int] = 8
# Conservative auto-activation threshold (shadow default).
AUTO_STABLE_CONFIDENCE: Final[float] = 0.85
MAX_AUTO_RETRIES: Final[int] = 3
LEASE_SECONDS: Final[int] = 120

# D18 bounded contextual reanalysis (S3D-4): recent same-conversation pool cap,
# completion context item cap, and whole-item token budget.
D18_UNRESOLVED_POOL_LIMIT: Final[int] = 16
D18_UNRESOLVED_CONTEXT_CAP: Final[int] = 3
D18_UNRESOLVED_TOKEN_BUDGET: Final[int] = 480

# S3F-2A: hard per-message provider output bounds. One user utterance normally
# produces few atomic claims (legacy extraction already frames facts[] as 0..8);
# D18 permits at most one contextual reanalysis target. Hard 8/8 caps prevent
# model-controlled local write amplification. No environment/provider-specific
# values.
MAX_MEMORY_OBSERVATIONS_PER_MESSAGE: Final[int] = 8
MAX_MEMORY_OPERATIONS_PER_MESSAGE: Final[int] = 8

IDENTITY_MODES: Final[frozenset[str]] = frozenset({"self", "okabe"})
FACT_STATES: Final[frozenset[str]] = frozenset({"active", "deleted"})
CHANGE_KINDS: Final[frozenset[str]] = frozenset(
    {"create", "refine", "supersede", "user_edit", "legacy_import"}
)
JOB_STATES: Final[frozenset[str]] = frozenset(
    {"pending", "processing", "completed", "failed", "cancelled"}
)
ALLOWED_OPS: Final[frozenset[str]] = frozenset(
    {"CREATE", "ATTACH", "REFINE", "SUPERSEDE", "REJECT"}
)


class MemoryJobErrorCode(str, Enum):
    """Stable job diagnostic codes — no user content."""

    PROVIDER_UNAVAILABLE = "provider_unavailable"
    INVALID_JSON = "invalid_json"
    VALIDATION_FAILED = "validation_failed"
    SOURCE_MISSING = "source_missing"
    SOURCE_NOT_USER = "source_not_user"
    LEASE_CONFLICT = "lease_conflict"
    TIMEOUT = "timeout"
    INTERNAL = "internal"


MEMORY_JOB_ERROR_CODES: Final[frozenset[str]] = frozenset(
    code.value for code in MemoryJobErrorCode
)


class MemoryValidationError(Exception):
    """Fail-closed validation / invariant error with a stable machine code."""

    def __init__(self, code: str, message: str = "") -> None:
        self.code = code
        self.error_code = code
        super().__init__(message or code)


class MemoryConstraintError(MemoryValidationError):
    """Schema or active-version constraint violation."""
