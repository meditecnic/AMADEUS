"""Memory v11 stage-A candidate builder (task memory-candidate-glm-20260909).

Builds the COMPLETE offline candidate directory from an explicit five-DB
read-only snapshot plus an already-reviewed rehearsal preview:

    <snapshot>/control.sqlite3
    <snapshot>/worldlines/{sg,beta}/{history,memory}.sqlite3
    preview.json   (from scripts/legacy_core_facts_rehearsal.py preview)

Per the C contract (memory-candidate-contract-20260909):

- history/control keep their logical content and sequences unchanged;
- memory keeps schema_meta, ALL legacy content (core/episodic tables,
  memory_embeddings, the FTS/vec0 index families), emotion_states, erasure
  requests, tombstones and existing receipts;
- ALL ingest jobs are kept, but in-flight ones (pending/processing/failed
  with retryable=1) are frozen to cancelled/retryable=0 in the copy with a
  uniform build-freeze reason — a pending job without a receipt is a NORMAL
  lifecycle state and never blocks the build; receipts are never fabricated;
- the old v11 CONTENT tables are cleared in dependency order and rebuilt
  EXCLUSIVELY from a donor run of the existing preview/apply tool; the old
  legacy_core_fact_imports ledger is not carried over (it stays in the
  source snapshot);
- nothing is published until every validation passes: failure keeps a
  clearly-incomplete private staging tree with the reason; an existing
  output is never overwritten.

Side-effect discipline: this module never imports app.main /
provider_runtime / credentials and never calls lifespan/init_db; it uses
direct SQLite plus the side-effect-free schema/import primitives of the
existing migration tool. The source is only ever opened read-only and is
stability-checked (main + wal + journal sidecars) across the read/validate
window; the source is never checkpointed or otherwise written to obtain
that check.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import sqlite3
import stat
import uuid
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.db import SCHEMA_VERSION
from app.services.memory_v11 import migration as mig

BUILDER_VERSION: str = "memory-candidate-builder/1"
CANDIDATE_KIND: str = "memory_candidate_manifest"
INCOMPLETE_KIND: str = "memory_candidate_incomplete"
FREEZE_REASON: str = "candidate_build_freeze"

_WORLDLINE_NAMESPACES: dict[str, str] = {"steins_gate": "sg", "beta": "beta"}

_SNAPSHOT_FILES: dict[str, Path] = {
    "control": Path("control.sqlite3"),
    "sg_history": Path("worldlines") / "sg" / "history.sqlite3",
    "sg_memory": Path("worldlines") / "sg" / "memory.sqlite3",
    "beta_history": Path("worldlines") / "beta" / "history.sqlite3",
    "beta_memory": Path("worldlines") / "beta" / "memory.sqlite3",
}

# Memory tables preserved verbatim (plus the FTS/vec0 index families).
_MEMORY_PRESERVED_TABLES: tuple[str, ...] = (
    "core_facts",
    "episodic_memories",
    "memory_embeddings",
    "emotion_states",
    "conversation_erasure_requests",
    "memory_tombstones",
    "memory_ingest_receipts",
)
# Old v11 CONTENT tables, cleared in dependency order (children first).
_V11_CONTENT_CLEAR_ORDER: tuple[str, ...] = (
    "stable_fact_version_embeddings",
    "stable_fact_evidence",
    "experiences",
    "memory_observation_sources",
    "memory_observations",
    "stable_fact_versions",
    "stable_facts",
    "memory_topic_aliases",
    "memory_topics",
    "memory_consolidation_proposals",
    # Old import ledger: not loaded into the new content set (C contract #5);
    # the candidate ledger comes from this build's donor import.
    "legacy_core_fact_imports",
)
# Donor tables transferred into staging (insert order respects FKs).
_DONOR_TRANSFER_ORDER: tuple[str, ...] = (
    "memory_observations",
    "memory_observation_sources",
    "stable_facts",
    "stable_fact_versions",
    "stable_fact_evidence",
    "legacy_core_fact_imports",
)

_MEMORY_KNOWN_TABLES: frozenset[str] = frozenset(
    {
        "schema_meta", "episodic_memories", "episodic_fts", "core_facts",
        "memory_embeddings", "emotion_states", "memory_ingest_jobs",
        "conversation_erasure_requests", "memory_observations",
        "memory_observation_sources", "memory_topics", "memory_topic_aliases",
        "stable_facts", "stable_fact_versions", "stable_fact_evidence",
        "memory_tombstones", "memory_consolidation_proposals",
        "stable_fact_version_embeddings", "experiences", "memory_ingest_receipts",
        "legacy_core_fact_imports",
    }
)
_INDEX_FAMILY_PREFIXES: tuple[str, ...] = (
    "episodic_fts",
    "episodic_vec",
    "core_vec",
)
# The supported historical schema, not SQL supplied by the source snapshot.
_HISTORICAL_VEC0_DDL = "CREATE VIRTUAL TABLE memory_vec USING vec0(embedding float[384])"
_MEMORY_REQUIRED_TABLES: frozenset[str] = frozenset(
    {
        "schema_meta", "core_facts", "episodic_memories", "episodic_fts",
        "memory_embeddings", "emotion_states", "memory_ingest_jobs",
        "conversation_erasure_requests", "memory_observations",
        "memory_observation_sources", "memory_topics", "memory_topic_aliases",
        "stable_facts", "stable_fact_versions", "stable_fact_evidence",
        "memory_tombstones", "memory_consolidation_proposals",
        "stable_fact_version_embeddings", "experiences", "memory_ingest_receipts",
    }
)
_MEMORY_RECEIPT_COLUMNS: frozenset[str] = frozenset(
    {
        "session_id", "conversation_id", "identity_mode", "source_message_id",
        "pipeline_version", "receipt_kind", "created_at",
    }
)
_HISTORY_KNOWN_TABLES: frozenset[str] = frozenset(
    {
        "schema_meta", "conversations", "messages", "memory_summaries",
        "conversation_content_epochs", "conversation_erasure_commits",
    }
)
_CONTROL_KNOWN_TABLES: frozenset[str] = frozenset(
    {
        "schema_meta", "session_worldlines", "session_conversation_selections",
        "search_usage", "provider_configurations", "shared_user_facts",
    }
)


class CandidateBuildError(Exception):
    """Fail-closed builder error with a stable machine code."""

    def __init__(self, code: str, message: str = "") -> None:
        self.code = code
        super().__init__(message or code)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect_ro(path: Path) -> sqlite3.Connection:
    uri = path.resolve().as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=30.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn


def _connect_rw(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), timeout=30.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def _schema_version(conn: sqlite3.Connection) -> str | None:
    if not _table_exists(conn, "schema_meta"):
        return None
    row = conn.execute(
        "SELECT value FROM schema_meta WHERE key='schema_version'"
    ).fetchone()
    return str(row[0]) if row is not None else None


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _commit_sidecar_digest(path: Path) -> str | None:
    """Hash a WAL/journal sidecar that may contain committed frames.

    Missing and zero-byte files are equivalent: opening a WAL-mode database
    read-only can create an empty ``-wal`` without a COMMIT. Only non-empty
    sidecars are hashed. Never writes or checkpoints the source.
    """
    try:
        metadata = path.stat()
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(metadata.st_mode):
        raise OSError(f"source sidecar is not a regular file: {path}")
    if metadata.st_size == 0:
        return None
    return _sha256_file(path)


def _sqlite_sidecar_digests(db_path: Path) -> dict[str, str | None]:
    """Hashes of the main DB file plus commit-bearing sidecars.

    WAL-mode COMMITs are appended to ``<db>-wal`` and often leave the main
    file bytes unchanged until a checkpoint. ``-journal`` covers rollback
    mode. ``-shm`` is omitted: it is the WAL index/locks, not committed
    pages. Empty WAL/journal files are treated as absent so a read-only open
    that creates a 0-byte sidecar is not a logical change.
    """
    return {
        "main": _sha256_file(db_path),
        "wal": _commit_sidecar_digest(Path(str(db_path) + "-wal")),
        "journal": _commit_sidecar_digest(Path(str(db_path) + "-journal")),
    }


def _capture_source_stability(paths: dict[str, Path]) -> dict[str, dict[str, str | None]]:
    try:
        return {name: _sqlite_sidecar_digests(path) for name, path in paths.items()}
    except OSError as exc:
        raise CandidateBuildError(
            "source_unreadable", f"cannot verify source stability: {exc}"
        ) from exc


def _require_source_unchanged(
    before: dict[str, dict[str, str | None]], paths: dict[str, Path]
) -> None:
    after = _capture_source_stability(paths)
    if after == before:
        return
    changed = sorted(name for name in paths if after.get(name) != before.get(name))
    raise CandidateBuildError(
        "source_changed",
        "source snapshot committed new state during the build read/validate "
        f"window; refusing to publish (changed={changed})",
    )


def _table_names(conn: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }


def _historical_vec0_tables(conn: sqlite3.Connection) -> frozenset[str]:
    """Validate the real parent and exact shadow schema against a trusted reference.

    The reference is in memory; the source remains read-only. Never execute
    source-provided DDL or infer shadow membership from an arbitrary prefix.
    Unfamiliar legacy schemas fail closed instead of being silently accepted.
    """
    tables = dict(conn.execute("SELECT name, sql FROM sqlite_master WHERE type='table'"))
    parent_sql = tables.get("memory_vec") or ""
    if not re.fullmatch(
        r'CREATE\s+VIRTUAL\s+TABLE\s+(?:memory_vec|"memory_vec"|`memory_vec`|\[memory_vec\])'
        r'\s+USING\s+vec0\s*\(\s*embedding\s+float\s*\[\s*384\s*\]\s*\)',
        parent_sql.strip(), re.IGNORECASE,
    ):
        return frozenset()
    try:
        import sqlite_vec

        with closing(sqlite3.connect(":memory:")) as reference:
            reference.enable_load_extension(True)
            sqlite_vec.load(reference)
            reference.enable_load_extension(False)
            reference.execute(_HISTORICAL_VEC0_DDL)
            names = frozenset(
                str(row[0]) for row in reference.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'memory_vec%'"
                )
            )
            for name in names - {"memory_vec"}:
                expected = [tuple(row)[1:] for row in reference.execute(f'PRAGMA table_info("{name}")')]
                actual = [tuple(row)[1:] for row in conn.execute(f'PRAGMA table_info("{name}")')]
                if name not in tables or actual != expected:
                    raise CandidateBuildError("schema_incompatible", f"historical vec0 shadow schema mismatch: {name}")
            return names
    except (ImportError, sqlite3.Error) as exc:
        raise CandidateBuildError("schema_incompatible", f"cannot validate historical vec0 schema: {exc}") from exc


def _is_index_family_table(name: str, historical: frozenset[str] = frozenset()) -> bool:
    return name in historical or any(
        name == prefix or name.startswith(prefix + "_") for prefix in _INDEX_FAMILY_PREFIXES
    )


def _is_known_table(name: str, base: frozenset[str], historical: frozenset[str] = frozenset()) -> bool:
    if name.startswith("sqlite_"):
        return True
    if name in base:
        return True
    return _is_index_family_table(name, historical)


def _index_family_counts(conn: sqlite3.Connection) -> dict[str, int]:
    """Row counts for the legacy FTS/vec0 index family (shadow tables).

    Virtual-table rows (vec0 needs the extension module loaded) are skipped;
    their shadow tables carry the persisted data and prove the structure.
    """
    counts: dict[str, int] = {}
    historical = _historical_vec0_tables(conn)
    for name, sql_text in conn.execute(
        "SELECT name, sql FROM sqlite_master WHERE type='table'"
    ):
        table_name = str(name)
        if not _is_index_family_table(table_name, historical):
            continue
        if sql_text and str(sql_text).upper().startswith("CREATE VIRTUAL"):
            continue
        counts[table_name] = int(
            conn.execute(f'SELECT COUNT(*) FROM "{table_name}"').fetchone()[0]
        )
    return counts


def _sqlite_sequence(conn: sqlite3.Connection) -> dict[str, int]:
    if not _table_exists(conn, "sqlite_sequence"):
        return {}
    return {
        str(row[0]): int(row[1])
        for row in conn.execute("SELECT name, seq FROM sqlite_sequence")
    }


# ---------------------------------------------------------------------------
# Source validation (read-only, before anything is created).
# ---------------------------------------------------------------------------


def _validate_schema_and_tables(
    conn: sqlite3.Connection, known: frozenset[str], required: frozenset[str], db: str
) -> None:
    version = _schema_version(conn)
    if version != str(SCHEMA_VERSION):
        raise CandidateBuildError(
            "schema_incompatible",
            f"{db}: schema_version={version!r} != {SCHEMA_VERSION}",
        )
    tables = _table_names(conn)
    historical = _historical_vec0_tables(conn) if known == _MEMORY_KNOWN_TABLES else frozenset()
    unknown = sorted(
        name for name in tables if not _is_known_table(name, known, historical)
    )
    if unknown:
        raise CandidateBuildError(
            "unknown_table",
            f"{db}: unknown business table(s) {unknown}; refusing to silently "
            "drop or mis-preserve them",
        )
    missing = sorted(required - tables)
    if missing:
        raise CandidateBuildError(
            "schema_incompatible", f"{db}: required table(s) missing: {missing}"
        )


def _validate_receipt_columns(conn: sqlite3.Connection, db: str) -> None:
    columns = {
        str(row[1]) for row in conn.execute("PRAGMA table_info(memory_ingest_receipts)")
    }
    if not _MEMORY_RECEIPT_COLUMNS <= columns:
        raise CandidateBuildError(
            "schema_incompatible",
            f"{db}: memory_ingest_receipts columns {sorted(columns)} lack "
            f"{sorted(_MEMORY_RECEIPT_COLUMNS - columns)}",
        )


def _validate_erasure_consistency(memory: sqlite3.Connection, history: sqlite3.Connection, db: str) -> dict[str, Any]:
    """Erase-boundary checks from the C contract; returns stats for manifest."""
    pending = int(
        memory.execute(
            "SELECT COUNT(*) FROM conversation_erasure_requests WHERE state='pending'"
        ).fetchone()[0]
    )
    if pending:
        raise CandidateBuildError(
            "pending_erasure",
            f"{db}: {pending} pending conversation_erasure_requests; resolve or "
            "investigate before building (no auto-recovery is performed)",
        )
    request_states: dict[str, str] = {
        str(row["request_id"]): str(row["state"])
        for row in memory.execute(
            "SELECT request_id, state FROM conversation_erasure_requests"
        )
    }
    commits = [
        str(row["request_id"])
        for row in history.execute("SELECT request_id FROM conversation_erasure_commits")
    ]
    completed = {rid for rid, state in request_states.items() if state == "completed"}
    missing = sorted(completed - set(commits))
    if missing:
        raise CandidateBuildError(
            "missing_history_commit",
            f"{db}: completed erasure request(s) without history commit: {missing}",
        )
    orphan = sorted({c for c in commits if c not in request_states})
    if orphan:
        raise CandidateBuildError(
            "orphan_history_commit",
            f"{db}: history erasure commit(s) without a request: {orphan}",
        )
    pending_with_commit = sorted(
        {c for c in commits if request_states.get(c) == "pending"}
    )
    if pending_with_commit:
        raise CandidateBuildError(
            "erasure_state_mismatch",
            f"{db}: commit(s) exist for still-pending request(s): {pending_with_commit}",
        )
    return {
        "pending": pending,
        "completed": len(completed),
        "commits": len(commits),
        "matched": True,
        "orphan_commits": 0,
    }


def _snapshot_layout(snapshot_root: Path) -> dict[str, Path]:
    paths = {name: snapshot_root / relative for name, relative in _SNAPSHOT_FILES.items()}
    for name, path in paths.items():
        if not path.is_file():
            raise CandidateBuildError(
                "invalid_source_layout",
                f"incomplete five-DB snapshot: {name} missing at {path}",
            )
    return paths


def _validate_source(
    snapshot_root: Path, paths: dict[str, Path]
) -> dict[str, Any]:
    """Read-only source validation; returns the 'before' reference report."""
    report: dict[str, Any] = {"erasure": {}, "jobs_before": {}, "table_counts": {}}
    control = _connect_ro(paths["control"])
    try:
        _validate_schema_and_tables(
            control, _CONTROL_KNOWN_TABLES, _CONTROL_KNOWN_TABLES, "control"
        )
        report["table_counts"]["control"] = _all_table_counts(control, _CONTROL_KNOWN_TABLES)
    finally:
        control.close()
    for wl in ("steins_gate", "beta"):
        history = _connect_ro(paths[f"{'sg' if wl == 'steins_gate' else 'beta'}_history"])
        memory = _connect_ro(paths[f"{'sg' if wl == 'steins_gate' else 'beta'}_memory"])
        try:
            _validate_schema_and_tables(
                history, _HISTORY_KNOWN_TABLES, _HISTORY_KNOWN_TABLES, f"{wl}:history"
            )
            _validate_schema_and_tables(
                memory, _MEMORY_KNOWN_TABLES, _MEMORY_REQUIRED_TABLES, f"{wl}:memory"
            )
            _validate_receipt_columns(memory, f"{wl}:memory")
            report["erasure"][wl] = _validate_erasure_consistency(
                memory, history, f"{wl}"
            )
            report["jobs_before"][wl] = _job_state_counts(memory)
            report["table_counts"][f"{wl}:history"] = _all_table_counts(
                history, _HISTORY_KNOWN_TABLES
            )
            report["table_counts"][f"{wl}:memory_preserved"] = _all_table_counts(
                memory, frozenset(_MEMORY_PRESERVED_TABLES)
            )
            report["table_counts"][f"{wl}:memory_index_family"] = _index_family_counts(memory)
            # The old import ledger exists only if a rehearsal ever ran against
            # this store's own databases; it is optional in the source.
            report["table_counts"][f"{wl}:legacy_ledger_rows"] = (
                int(
                    memory.execute(
                        "SELECT COUNT(*) FROM legacy_core_fact_imports"
                    ).fetchone()[0]
                )
                if _table_exists(memory, "legacy_core_fact_imports")
                else 0
            )
        finally:
            history.close()
            memory.close()
    return report


def _all_table_counts(conn: sqlite3.Connection, tables: frozenset[str] | tuple[str, ...]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for table in sorted(tables):
        if _table_exists(conn, table):
            counts[table] = int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
    return counts


def _job_state_counts(conn: sqlite3.Connection) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in conn.execute(
        "SELECT state, COUNT(*) AS n FROM memory_ingest_jobs GROUP BY state"
    ):
        counts[str(row["state"])] = int(row["n"])
    return counts


# ---------------------------------------------------------------------------
# Staging steps.
# ---------------------------------------------------------------------------


def _backup_databases(paths: dict[str, Path], staging: Path) -> None:
    """Whole-DB SQLite backup: preserves legacy + FTS/vec0 internal structure."""
    for name, source_path in paths.items():
        destination_path = staging / _SNAPSHOT_FILES[name]
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        source = sqlite3.connect(source_path.resolve().as_uri() + "?mode=ro", uri=True)
        try:
            destination = sqlite3.connect(str(destination_path))
            try:
                source.backup(destination)
                destination.commit()
            finally:
                destination.close()
        finally:
            source.close()


def _freeze_jobs(staging: Path) -> dict[str, Any]:
    """Freeze in-flight jobs in the COPY; returns per-worldline report."""
    report: dict[str, Any] = {}
    for wl, ns in _WORLDLINE_NAMESPACES.items():
        path = staging / "worldlines" / ns / "memory.sqlite3"
        conn = _connect_rw(path)
        try:
            conn.execute("BEGIN IMMEDIATE")
            before = _job_state_counts(conn)
            frozen_rows = conn.execute(
                """SELECT job_id, session_id, conversation_id, identity_mode,
                          source_message_id, pipeline_version, state
                     FROM memory_ingest_jobs
                    WHERE state IN ('pending','processing')
                       OR (state='failed' AND retryable=1)
                    ORDER BY job_id"""
            ).fetchall()
            conn.execute(
                """UPDATE memory_ingest_jobs
                      SET state='cancelled',
                          retryable=0,
                          lease_owner=NULL,
                          lease_expires_at=NULL,
                          next_attempt_at=NULL,
                          last_error_code=?,
                          updated_at=?
                    WHERE state IN ('pending','processing')
                       OR (state='failed' AND retryable=1)""",
                (FREEZE_REASON, _utc_now()),
            )
            conn.commit()
            report[wl] = {
                "before": before,
                "after": _job_state_counts(conn),
                "frozen": [
                    {
                        "job_id": str(row["job_id"]),
                        "session_id": str(row["session_id"]),
                        "conversation_id": str(row["conversation_id"]),
                        "identity_mode": str(row["identity_mode"]),
                        "source_message_id": int(row["source_message_id"]),
                        "pipeline_version": str(row["pipeline_version"]),
                        "state_before": str(row["state"]),
                        "state_after": "cancelled",
                    }
                    for row in frozen_rows
                ],
            }
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
    return report


def _clear_v11_content(staging: Path) -> None:
    for ns in _WORLDLINE_NAMESPACES.values():
        path = staging / "worldlines" / ns / "memory.sqlite3"
        conn = _connect_rw(path)
        try:
            conn.execute("BEGIN IMMEDIATE")
            for table in _V11_CONTENT_CLEAR_ORDER:
                if _table_exists(conn, table):
                    conn.execute(f"DELETE FROM {table}")
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


def _transfer_donor_content(donor: Path, staging: Path) -> None:
    """Copy the donor's controlled v11 content + import ledger into staging."""
    for ns in _WORLDLINE_NAMESPACES.values():
        donor_db = donor / "worldlines" / ns / "memory.sqlite3"
        staging_db = staging / "worldlines" / ns / "memory.sqlite3"
        if not donor_db.is_file():
            continue
        source = _connect_ro(donor_db)
        target = _connect_rw(staging_db)
        try:
            rows_by_table: dict[str, list[tuple[Any, ...]]] = {}
            columns_by_table: dict[str, list[str]] = {}
            for table in _DONOR_TRANSFER_ORDER:
                if not _table_exists(source, table):
                    continue
                cursor = source.execute(f"SELECT * FROM {table}")
                columns_by_table[table] = [d[0] for d in cursor.description]
                rows_by_table[table] = [tuple(r) for r in cursor.fetchall()]
            target.execute("BEGIN IMMEDIATE")
            for table in _DONOR_TRANSFER_ORDER:
                rows = rows_by_table.get(table) or []
                if not rows:
                    continue
                columns = columns_by_table[table]
                if not _table_exists(target, table):
                    # The ledger is tool-specific and may be absent from the
                    # snapshot; create it with the tool's own DDL.
                    if table == "legacy_core_fact_imports":
                        target.execute(mig._LEGACY_IMPORTS_TABLE_DDL)
                        target.execute(mig._LEGACY_IMPORTS_INDEX_DDL)
                    else:
                        raise CandidateBuildError(
                            "validation_failed",
                            f"staging lacks required content table {table}",
                        )
                placeholders = ",".join("?" for _ in columns)
                target.executemany(
                    f"INSERT INTO {table} ({','.join(columns)}) VALUES ({placeholders})",
                    rows,
                )
            target.commit()
        except Exception:
            target.rollback()
            raise
        finally:
            source.close()
            target.close()


def _validate_candidate(
    staging: Path,
    donor: Path,
    source_report: dict[str, Any],
    source_paths: dict[str, Path],
) -> dict[str, Any]:
    """Full validation of the staged candidate before publishing."""
    checks: dict[str, Any] = {}

    def _fail(check: str, message: str) -> None:
        raise CandidateBuildError("validation_failed", f"{check}: {message}")

    # history/control preserved: counts + sequences.
    control = _connect_ro(staging / _SNAPSHOT_FILES["control"])
    try:
        if _schema_version(control) != str(SCHEMA_VERSION):
            _fail("schema_version", "control")
        counts = _all_table_counts(control, _CONTROL_KNOWN_TABLES)
        if counts != source_report["table_counts"]["control"]:
            _fail("control_tables_preserved", str(counts))
    finally:
        control.close()
    for wl, ns in _WORLDLINE_NAMESPACES.items():
        history = _connect_ro(staging / "worldlines" / ns / "history.sqlite3")
        memory = _connect_ro(staging / "worldlines" / ns / "memory.sqlite3")
        source_history = _connect_ro(
            source_paths[f"{'sg' if wl == 'steins_gate' else 'beta'}_history"]
        )
        source_memory = _connect_ro(
            source_paths[f"{'sg' if wl == 'steins_gate' else 'beta'}_memory"]
        )
        try:
            if _schema_version(history) != str(SCHEMA_VERSION) or _schema_version(memory) != str(SCHEMA_VERSION):
                _fail("schema_version", f"{wl}")
            history_counts = _all_table_counts(history, _HISTORY_KNOWN_TABLES)
            if history_counts != source_report["table_counts"][f"{wl}:history"]:
                _fail("history_tables_preserved", f"{wl}: {history_counts}")
            if _sqlite_sequence(history) != _sqlite_sequence(source_history):
                _fail("history_sequence_preserved", wl)
            preserved = _all_table_counts(memory, frozenset(_MEMORY_PRESERVED_TABLES))
            if preserved != source_report["table_counts"][f"{wl}:memory_preserved"]:
                _fail("memory_legacy_preserved", f"{wl}: {preserved}")
            if _index_family_counts(memory) != source_report["table_counts"][f"{wl}:memory_index_family"]:
                _fail("memory_index_family_preserved", wl)
            if _sqlite_sequence(memory) != _sqlite_sequence(source_memory):
                _fail("memory_sequence_preserved", wl)
            # Receipts preserved verbatim.
            source_receipts = [
                tuple(r) for r in source_memory.execute("SELECT * FROM memory_ingest_receipts ORDER BY 1")
            ]
            candidate_receipts = [
                tuple(r) for r in memory.execute("SELECT * FROM memory_ingest_receipts ORDER BY 1")
            ]
            if source_receipts != candidate_receipts:
                _fail("receipts_preserved", wl)
            # Erasure barriers preserved (protection tables untouched).
            for table in ("conversation_erasure_requests", "memory_tombstones"):
                source_rows = [tuple(r) for r in source_memory.execute(f"SELECT * FROM {table} ORDER BY 1")]
                candidate_rows = [tuple(r) for r in memory.execute(f"SELECT * FROM {table} ORDER BY 1")]
                if source_rows != candidate_rows:
                    _fail("erasure_barriers_preserved", f"{wl}:{table}")
            # Jobs frozen: nothing schedulable, frozen rows terminal.
            live = memory.execute(
                "SELECT COUNT(*) FROM memory_ingest_jobs WHERE state IN "
                "('pending','processing') OR (state='failed' AND retryable=1)"
            ).fetchone()[0]
            if int(live) != 0:
                _fail("jobs_frozen", f"{wl}: {live} schedulable job(s) remain")
            # v11 content equals the donor's import set exactly.
            donor_db = donor / "worldlines" / ns / "memory.sqlite3"
            if donor_db.is_file():
                donor_conn = _connect_ro(donor_db)
                try:
                    for table in (
                        "stable_facts",
                        "stable_fact_versions",
                        "stable_fact_evidence",
                        "memory_observations",
                        "memory_observation_sources",
                        "legacy_core_fact_imports",
                    ):
                        expected = int(donor_conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
                        actual = (
                            int(memory.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
                            if _table_exists(memory, table)
                            else 0
                        )
                        if actual != expected:
                            _fail("content_matches_donor", f"{wl}:{table} {actual}!={expected}")
                    donor_ids = {
                        str(r[0]) for r in donor_conn.execute("SELECT fact_id FROM stable_facts")
                    }
                    candidate_ids = {
                        str(r[0]) for r in memory.execute("SELECT fact_id FROM stable_facts")
                    }
                    if donor_ids != candidate_ids:
                        _fail("content_matches_donor", f"{wl}: fact_id sets differ")
                    kinds = {str(r[0]) for r in memory.execute("SELECT change_kind FROM stable_fact_versions")}
                    if kinds - {"legacy_import"}:
                        _fail("content_matches_donor", f"{wl}: unexpected change_kind {kinds}")
                finally:
                    donor_conn.close()
            else:
                for table in _DONOR_TRANSFER_ORDER:
                    if _table_exists(memory, table) and int(
                        memory.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                    ) != 0:
                        _fail("content_matches_donor", f"{wl}:{table} should be empty")
            # Legacy FTS still usable end-to-end.
            fts_ok = memory.execute(
                "SELECT COUNT(*) FROM episodic_fts"
            ).fetchone()[0]
            episodic = memory.execute("SELECT COUNT(*) FROM episodic_memories").fetchone()[0]
            if int(fts_ok) != int(episodic):
                _fail("legacy_fts_usable", f"{wl}: fts={fts_ok} episodic={episodic}")
        finally:
            history.close()
            memory.close()
            source_history.close()
            source_memory.close()

    checks["history_tables_preserved"] = True
    checks["control_tables_preserved"] = True
    checks["legacy_memory_preserved"] = True
    checks["legacy_fts_usable"] = True
    checks["legacy_index_family_preserved"] = True
    checks["sequences_preserved"] = True
    checks["jobs_frozen"] = True
    checks["receipts_preserved"] = True
    checks["erasure_barriers_preserved"] = True
    checks["content_matches_donor"] = True
    return checks


def _write_manifest(staging: Path, manifest: dict[str, Any]) -> None:
    path = staging / "manifest.json"
    tmp = staging / "manifest.json.tmp"
    tmp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _mark_incomplete(staging: Path, code: str, message: str, stage: str) -> None:
    try:
        (staging / "INCOMPLETE.json").write_text(
            json.dumps(
                {
                    "kind": INCOMPLETE_KIND,
                    "builder_version": BUILDER_VERSION,
                    "code": code,
                    "message": message,
                    "stage": stage,
                    "at": _utc_now(),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    except OSError:
        pass


def _remove_tree(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)


# ---------------------------------------------------------------------------
# Main entry.
# ---------------------------------------------------------------------------


def build_memory_candidate(
    *,
    snapshot_dir: str | Path,
    preview_path: str | Path,
    output_dir: str | Path,
) -> dict[str, Any]:
    """Build and publish the complete stage-A candidate directory.

    Guarantees (C contract):
    - explicit five-DB read-only snapshot + reviewed preview only; the
      preview must match the snapshot identity (preview_stale otherwise);
    - history/control/legacy/jobs/receipts/erasure metadata preserved as
      described in the module docstring; in-flight jobs frozen in the copy;
    - the v11 content comes EXCLUSIVELY from a fresh donor run of the
      existing preview/apply tool against the same source and plan;
    - a private unique staging tree is used and only published (renamed to
      the output) after full validation; failures keep an incomplete
      staging with the reason and never publish; existing outputs are
      refused; source stability is checked across the read/validate window
      using main + wal + journal sidecar hashes (not main-file SHA256 only).
    """
    snapshot_root = Path(snapshot_dir).expanduser().resolve()
    output_root = Path(output_dir).expanduser().resolve()
    preview_file = Path(preview_path).expanduser().resolve()

    if not snapshot_root.is_dir():
        raise CandidateBuildError("source_not_found", f"snapshot directory missing: {snapshot_root}")
    if output_root.exists():
        raise CandidateBuildError("output_exists", f"output already exists, never overwritten: {output_root}")
    if (
        snapshot_root == output_root
        or snapshot_root in output_root.parents
        or output_root in snapshot_root.parents
    ):
        raise CandidateBuildError(
            "overlapping_paths",
            f"snapshot and output must be distinct, non-nested paths "
            f"(snapshot={snapshot_root}, output={output_root})",
        )
    if not preview_file.is_file():
        raise CandidateBuildError("preview_file_missing", f"preview file not found: {preview_file}")

    try:
        preview = json.loads(preview_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise CandidateBuildError("invalid_preview", f"preview file is not valid JSON: {exc}") from exc
    if (
        not isinstance(preview, dict)
        or preview.get("kind") != mig.PREVIEW_KIND
        or preview.get("tool_version") != mig.MIGRATION_TOOL_VERSION
    ):
        raise CandidateBuildError(
            "invalid_preview",
            "preview payload is not a legacy core-facts preview of the expected tool version",
        )

    paths = _snapshot_layout(snapshot_root)

    # Capture committed-state fingerprints before the first source read.
    # Main-file SHA256 alone misses WAL-only COMMITs; sidecar hashes cover
    # that window through candidate validation and publish.
    stability_before = _capture_source_stability(paths)
    source_hashes = {name: stability_before[name]["main"] or "" for name in paths}
    source_id = mig.compute_source_identity(snapshot_root)
    if preview.get("source", {}).get("source_id") != source_id:
        raise CandidateBuildError(
            "preview_stale",
            "preview does not match the current snapshot identity "
            f"(preview={preview.get('source', {}).get('source_id')}, "
            f"snapshot={source_id})",
        )

    source_report = _validate_source(snapshot_root, paths)

    output_root.parent.mkdir(parents=True, exist_ok=True)
    staging = output_root.parent / f".{output_root.name}.staging-{uuid.uuid4().hex[:12]}"
    donor = output_root.parent / f".{output_root.name}.donor-{uuid.uuid4().hex[:12]}"
    staging.mkdir()
    stage = "init"
    try:
        try:
            stage = "backup"
            _backup_databases(paths, staging)

            stage = "freeze_jobs"
            jobs_report = _freeze_jobs(staging)

            stage = "clear_v11_content"
            _clear_v11_content(staging)

            stage = "donor_apply"
            donor_result = mig.apply_legacy_core_facts(
                source_dir=snapshot_root, target_dir=donor, preview=preview
            )
            if not donor_result.get("ok"):
                raise CandidateBuildError(
                    "donor_apply_failed",
                    f"donor import did not succeed: {donor_result.get('worldlines')}",
                )

            stage = "transfer_donor_content"
            _transfer_donor_content(donor, staging)

            stage = "validate"
            checks = _validate_candidate(staging, donor, source_report, paths)
            _require_source_unchanged(stability_before, paths)

            stage = "manifest"
            imported_facts = list(donor_result.get("imported_facts") or [])
            scopes: dict[str, dict[str, int]] = {}
            for fact in imported_facts:
                key = f"{fact['worldline']}|{fact['session_id']}|{fact['identity_mode']}"
                scopes.setdefault(key, 0)
                scopes[key] += 1
            preview_items = preview.get("items") or []
            manifest = {
                "kind": CANDIDATE_KIND,
                "builder_version": BUILDER_VERSION,
                "app_schema_version": int(SCHEMA_VERSION),
                "status": "valid",
                "built_at": _utc_now(),
                "source": {
                    "snapshot_path": str(snapshot_root),
                    "source_id": source_id,
                    "file_sha256": {
                        str(_SNAPSHOT_FILES[name]): digest
                        for name, digest in source_hashes.items()
                    },
                    "sidecar_sha256": {
                        str(_SNAPSHOT_FILES[name]): {
                            "wal": stability_before[name]["wal"],
                            "journal": stability_before[name]["journal"],
                        }
                        for name in source_hashes
                    },
                    "stability": "unchanged_before_and_after_build",
                    "stability_window": (
                        "from first source read through candidate validation "
                        "and publish; main + wal + journal sidecars"
                    ),
                },
                "plan": {
                    "preview_path": str(preview_file),
                    "preview_tool_version": mig.MIGRATION_TOOL_VERSION,
                    "planned": preview.get("summary", {}).get("planned"),
                    "skip": preview.get("summary", {}).get("skip"),
                    "defer": preview.get("summary", {}).get("defer"),
                    "by_reason": preview.get("summary", {}).get("by_reason", {}),
                },
                "scopes": [
                    {
                        "worldline": key.split("|")[0],
                        "session_id": key.split("|")[1],
                        "identity_mode": key.split("|")[2],
                        "imported": count,
                    }
                    for key, count in sorted(scopes.items())
                ],
                "imported_facts": imported_facts,
                "not_loaded": {
                    "note": "records remain in the source snapshot; not imported "
                    "into the candidate",
                    "source_snapshot": str(snapshot_root),
                    "items": [
                        {
                            "worldline": item.get("worldline"),
                            "legacy_row_id": item.get("legacy_row_id"),
                            "decision": item.get("decision"),
                            "reason": item.get("reason"),
                        }
                        for item in preview_items
                        if item.get("decision") != "planned"
                    ],
                },
                "table_strategies": {
                    "history_and_control": "preserved verbatim (whole-DB backup; "
                    "tables and sqlite_sequence high-water marks unchanged)",
                    "memory_legacy": "preserved verbatim: core_facts, episodic_memories, "
                    "memory_embeddings, episodic_fts/vec0 index families "
                    "(including historical memory_vec), emotion_states",
                    "memory_protection": "preserved verbatim: erasure requests, "
                    "tombstones, receipts",
                    "memory_ingest_jobs": "all rows kept; pending/processing/failed"
                    "(retryable=1) frozen to cancelled/retryable=0 with "
                    f"last_error_code={FREEZE_REASON}; no receipt fabricated",
                    "memory_v11_content": "cleared and rebuilt exclusively from the "
                    "donor import of the same reviewed plan",
                    "legacy_core_fact_imports": "old ledger not carried over; kept "
                    "only in the source snapshot; candidate rows come from this build",
                },
                "validation": {
                    "ok": True,
                    "checks": checks,
                    "erasure": source_report["erasure"],
                    "jobs": jobs_report,
                    "source_table_counts": source_report["table_counts"],
                },
                "erasure": source_report["erasure"],
                "jobs": jobs_report,
                "legacy_ledger": {
                    "source_rows": source_report["table_counts"].get(
                        "steins_gate:legacy_ledger_rows", 0
                    )
                    + source_report["table_counts"].get(
                        "beta:legacy_ledger_rows", 0
                    ),
                    "candidate_rows": len(imported_facts),
                    "note": "candidate ledger rows come from this build's donor "
                    "import; the old ledger stays in the source snapshot",
                },
            }
            _write_manifest(staging, manifest)

            stage = "candidate_marker"
            marker = staging / f".migration-v{SCHEMA_VERSION}.complete"
            marker.write_text(
                json.dumps(
                    {"schema_version": int(SCHEMA_VERSION), "completed_at_utc": _utc_now()}
                ),
                encoding="utf-8",
            )

            stage = "publish"
            _require_source_unchanged(stability_before, paths)
            _remove_tree(donor)
            try:
                staging.rename(output_root)
            except OSError as exc:
                raise CandidateBuildError(
                    "publish_failed", f"could not publish staging to output: {exc}"
                ) from exc
        except CandidateBuildError as exc:
            _mark_incomplete(staging, exc.code, str(exc), stage)
            raise
        except Exception as exc:
            _mark_incomplete(staging, "build_failed", f"{type(exc).__name__}: {exc}", stage)
            raise CandidateBuildError("build_failed", f"{type(exc).__name__}: {exc}") from exc
    finally:
        # The donor is a private temporary; staging is kept (complete or
        # explicitly incomplete) for inspection.
        if donor.exists():
            _remove_tree(donor)

    return {
        "ok": True,
        "builder_version": BUILDER_VERSION,
        "output": str(output_root),
        "source_id": source_id,
        "manifest_path": str(output_root / "manifest.json"),
        "imported_facts": donor_result.get("imported_facts") or [],
        "jobs_frozen": {
            wl: len(report["frozen"]) for wl, report in jobs_report.items()
        },
    }
