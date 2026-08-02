"""Stable contracts and error types for Memory v11 (S1)."""

from __future__ import annotations

from typing import Final


PIPELINE_VERSION: Final[str] = "memory-v11-1"
MAX_PINNED_FACTS_PER_SCOPE: Final[int] = 8

IDENTITY_MODES: Final[frozenset[str]] = frozenset({"self", "okabe"})
FACT_STATES: Final[frozenset[str]] = frozenset({"active", "deleted"})
CHANGE_KINDS: Final[frozenset[str]] = frozenset(
    {"create", "refine", "supersede", "user_edit", "legacy_import"}
)
JOB_STATES: Final[frozenset[str]] = frozenset(
    {"pending", "processing", "completed", "failed", "cancelled"}
)


class MemoryValidationError(Exception):
    """Fail-closed validation / invariant error with a stable machine code."""

    def __init__(self, code: str, message: str = "") -> None:
        self.code = code
        self.error_code = code
        super().__init__(message or code)


class MemoryConstraintError(MemoryValidationError):
    """Schema or active-version constraint violation."""
