"""Shared fail-closed path/environment guards for the offline CLIs.

One protection rule set for every offline rehearsal/candidate CLI (task
memory-candidate-glm-20260909: extract instead of duplicating guards):

- AMADEUS_DB_PATH / AMADEUS_DATA_DIR overrides are refused at the entry,
  BEFORE any module with import side effects is loaded;
- every actual I/O position is deep-resolved (junction/symlink aware);
- no position may live inside a live data root: %APPDATA%/Amadeus or, when
  APPDATA is absent, the app's default ~/.amadeus fallback.

Pure helpers only — each CLI keeps its own exit-code handling.
"""
from __future__ import annotations

import os
from pathlib import Path

ENV_OVERRIDE_VARS: tuple[str, ...] = ("AMADEUS_DB_PATH", "AMADEUS_DATA_DIR")


def live_data_roots() -> list[Path]:
    """Formal data roots these tools must never read from or write into."""
    roots: list[Path] = []
    appdata = os.environ.get("APPDATA")
    if appdata:
        roots.append(deep_resolve(Path(appdata) / "Amadeus"))
    # app.db.get_data_root() fallback when APPDATA is absent.
    roots.append(deep_resolve(Path.home() / ".amadeus"))
    return roots


def deep_resolve(raw: Path) -> Path:
    """Absolute path with existing components fully resolved (junctions/symlinks)."""
    return raw.expanduser().resolve()


def env_override_present() -> str | None:
    """Return the first set override variable, or None when the env is clean."""
    for var in ENV_OVERRIDE_VARS:
        if (os.environ.get(var) or "").strip():
            return var
    return None


def containing_live_root(path: Path, roots: list[Path] | None = None) -> Path | None:
    """Return the live root containing `path` (or the root itself), else None."""
    resolved = deep_resolve(path)
    for root in roots if roots is not None else live_data_roots():
        if resolved == root or root in resolved.parents:
            return root
    return None


def paths_nested_or_equal(first: Path, second: Path) -> bool:
    """True when the two deep-resolved paths are equal or one contains the other."""
    a = deep_resolve(first)
    b = deep_resolve(second)
    return a == b or a in b.parents or b in a.parents
