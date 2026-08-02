"""Memory v11 schema migration helpers (S1: backup primitive only).

Legacy import / consolidation / shared purge belong to S5 — not implemented here.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from app.db import _resolve_path, normalize_worldline


def backup_memory_db_before_v11(*, worldline: str) -> str:
    """Copy the worldline memory DB to a non-overwriting backup path.

    Filename includes original schema label (v10), worldline, and UTC stamp.
    Returns the absolute backup path as a string (test contract).
    """
    wl = normalize_worldline(worldline)
    src = Path(_resolve_path(wl, "memory"))
    if not src.is_file():
        raise FileNotFoundError(f"memory database missing for worldline={wl}: {src}")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    # Read current schema version for the filename when available.
    schema_label = "v10"
    try:
        probe = sqlite3.connect(str(src))
        try:
            row = probe.execute(
                "SELECT value FROM schema_meta WHERE key='schema_version'"
            ).fetchone()
            if row and str(row[0]).strip():
                schema_label = f"v{str(row[0]).strip()}"
        finally:
            probe.close()
    except sqlite3.Error:
        pass

    parent = src.parent
    parent.mkdir(parents=True, exist_ok=True)
    dest = parent / f"memory.{schema_label}.{wl}.{stamp}.backup.sqlite3"
    counter = 0
    while dest.exists():
        counter += 1
        dest = parent / f"memory.{schema_label}.{wl}.{stamp}.{counter}.backup.sqlite3"

    source = sqlite3.connect(str(src))
    try:
        destination = sqlite3.connect(str(dest))
        try:
            source.backup(destination)
            destination.commit()
        finally:
            destination.close()
    finally:
        source.close()

    return str(dest.resolve())


# Alias expected by S0 red contracts.
backup_worldline_memory_db = backup_memory_db_before_v11


def backup_memory_connection(
    connection: sqlite3.Connection,
    *,
    worldline: str,
    schema_label: str = "v10",
) -> str:
    """Online backup from an open connection (used inside v10→v11 migration)."""
    wl = normalize_worldline(worldline)
    src = Path(_resolve_path(wl, "memory"))
    parent = src.parent
    parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dest = parent / f"memory.{schema_label}.{wl}.{stamp}.backup.sqlite3"
    counter = 0
    while dest.exists():
        counter += 1
        dest = parent / f"memory.{schema_label}.{wl}.{stamp}.{counter}.backup.sqlite3"

    destination = sqlite3.connect(str(dest))
    try:
        connection.backup(destination)
        destination.commit()
    finally:
        destination.close()
    return str(dest.resolve())
