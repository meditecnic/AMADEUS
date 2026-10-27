"""Memory v11 schema migration helpers.

S1 backup primitives (live-path, worldline-scoped) plus the offline legacy
core-fact migration rehearsal engine (explicit-path preview/apply).

The rehearsal engine (task memory-migration-glm-20260908, revision 1) is
deliberately separate from the live-path helpers above:

- Input is an operator-specified snapshot directory with BOTH worldline
  snapshots (sg + beta history/memory), opened SQLite mode=ro with
  ``PRAGMA query_only=ON`` inside one read transaction per DB; the live
  APPDATA store is never discovered or connected.
- Input is consumed through ONE fixed read view per apply/preview call: the
  source identity digest, the item decisions and the imported write content
  all derive from that same view, so apply can prove it imported exactly the
  previewed input. This is a fixed-offline-snapshot guarantee only — no
  cross-database transactional atomicity is claimed.
- D30 hard privacy blocks (privacy.classify_memory_sensitive_text) apply to
  the importable fact text and the FULL source message contents; hit items
  are skipped and never carry their raw text into preview/detail/hints.
- Output is a freshly initialized v11 rehearsal target directory bound to
  one source snapshot via a minimal manifest; qualified legacy core_facts
  are imported with legacy_import semantics (never direct_user). The target
  is memory-only: it cannot run the full app or replace the live data dir.
- Explicit live migration and deterministic consolidation are implemented below;
  no automatic startup hook or legacy-table writes are installed.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Final

from app.db import _resolve_path, normalize_worldline
from app.services.memory_v11.contracts import MAX_PINNED_FACTS_PER_SCOPE
from app.services.memory_v11.privacy import classify_memory_sensitive_text


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


# ---------------------------------------------------------------------------
# Offline legacy core-fact migration rehearsal (explicit-path preview/apply).
#
# Minimal scope (task memory-migration-glm-20260908): a read-only preview of
# which legacy core_facts qualify, and an apply that imports the qualified
# rows into a freshly initialized v11 rehearsal target directory. The source
# snapshot is never modified; the live store is never touched; no external
# model or embedding API is called (retrieval on the target is lexical-only).
#
# Source snapshot layout (operator-supplied isolated copy):
#   <source>/worldlines/sg/{history,memory}.sqlite3
#   <source>/worldlines/beta/{history,memory}.sqlite3
#
# Target layout (created and owned by this tool only):
#   <target>/manifest.json
#   <target>/worldlines/<ns>/memory.sqlite3
# ---------------------------------------------------------------------------

MIGRATION_TOOL_VERSION: Final[str] = "legacy-core-facts-rehearsal/1"
PREVIEW_KIND: Final[str] = "legacy_core_facts_preview"
TARGET_KIND: Final[str] = "legacy_core_facts_rehearsal_target"

_WORLDLINE_NAMESPACES: Final[dict[str, str]] = {
    "steins_gate": "sg",
    "beta": "beta",
}

_LEGACY_IMPORTS_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS legacy_core_fact_imports (
    import_key TEXT PRIMARY KEY,
    source_id TEXT NOT NULL,
    worldline TEXT NOT NULL,
    session_id TEXT NOT NULL,
    identity_mode TEXT NOT NULL,
    legacy_row_id INTEGER NOT NULL,
    fact_id TEXT NOT NULL,
    observation_id TEXT NOT NULL,
    imported_at TEXT NOT NULL
)
"""

_LEGACY_IMPORTS_INDEX_DDL = (
    "CREATE INDEX IF NOT EXISTS idx_legacy_core_fact_imports_scope "
    "ON legacy_core_fact_imports(session_id, identity_mode, legacy_row_id)"
)

_CORE_FACTS_COLUMNS: Final[tuple[str, ...]] = (
    "id", "session_id", "identity_mode", "fact_key", "fact_value",
    "confidence", "importance", "is_current", "is_pinned", "is_dismissed",
    "source_message_ids", "superseded_by", "created_at",
)
_ERASURE_REQUESTS_COLUMNS: Final[tuple[str, ...]] = (
    "request_id", "session_id", "identity_mode", "conversation_id", "action",
    "forget_long_term", "source_message_ids", "state", "created_at",
)
_TOMBSTONES_COLUMNS: Final[tuple[str, ...]] = (
    "tombstone_id", "session_id", "identity_mode", "fact_id",
    "source_fingerprint", "semantic_fingerprint", "reason", "deleted_at",
)
_CONVERSATIONS_COLUMNS: Final[tuple[str, ...]] = ("id", "session_id", "identity_mode")
_MESSAGES_COLUMNS: Final[tuple[str, ...]] = (
    "id", "session_id", "conversation_id", "role", "content", "created_at",
)


class MigrationRehearsalError(Exception):
    """Fail-closed rehearsal error with a stable machine code."""

    def __init__(self, code: str, message: str = "") -> None:
        self.code = code
        super().__init__(message or code)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect_readonly(path: Path) -> sqlite3.Connection:
    uri = path.resolve().as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=30.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn


def _connect_writable(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return row is not None


def _schema_version(conn: sqlite3.Connection) -> str | None:
    if not _table_exists(conn, "schema_meta"):
        return None
    row = conn.execute(
        "SELECT value FROM schema_meta WHERE key='schema_version'"
    ).fetchone()
    return str(row[0]) if row is not None else None


def _source_fingerprint(session_id: str, message_id: int, content: str) -> str:
    """Same formula as the v11 ingest pipeline (jobs.process_memory_job)."""
    raw = f"{session_id}:{int(message_id)}:{content}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def discover_source_snapshot(source_dir: str | Path) -> dict[str, dict[str, Path]]:
    """Validate and return the explicit source snapshot layout (read-only use).

    Revision 1 (C ruling): the rehearsal entry requires the complete snapshot —
    BOTH worldline namespaces with their history and memory databases. A
    missing worldline is rejected instead of silently skipped. Tests that
    only exercise one worldline's logic create the other one empty.
    """
    root = Path(source_dir).expanduser().resolve()
    if not root.is_dir():
        raise MigrationRehearsalError(
            "source_not_found", f"source directory missing: {root}"
        )
    worldlines_dir = root / "worldlines"
    if not worldlines_dir.is_dir():
        raise MigrationRehearsalError(
            "invalid_source_layout", f"missing worldlines/ under {root}"
        )
    found: dict[str, dict[str, Path]] = {}
    for wl, ns in _WORLDLINE_NAMESPACES.items():
        ns_dir = worldlines_dir / ns
        memory = ns_dir / "memory.sqlite3"
        history = ns_dir / "history.sqlite3"
        if not ns_dir.is_dir() or not memory.is_file() or not history.is_file():
            raise MigrationRehearsalError(
                "invalid_source_layout",
                f"incomplete source snapshot: worldline {wl} under {ns_dir} "
                "must provide history.sqlite3 and memory.sqlite3 "
                "(both worldline snapshots are required)",
            )
        found[wl] = {"memory": memory, "history": history}
    return found


def _rows_to_digest_lists(rows: list[sqlite3.Row]) -> list[list[Any]]:
    out: list[list[Any]] = []
    for row in rows:
        values: list[Any] = []
        for value in tuple(row):
            if isinstance(value, bytes):
                values.append(value.hex())
            else:
                values.append(value)
        out.append(values)
    return out


def _read_source_view(source_dir: str | Path) -> dict[str, dict[str, Any]]:
    """ONE fixed read of everything this tool consumes from the snapshot.

    Per worldline, each database is read inside a single read transaction
    (mode=ro + query_only), so the digest inputs, the item decisions and
    the content later written into the target all come from the same view.
    The two databases of one worldline (and the two worldlines) are read as
    sequential snapshots: this is a fixed-offline-snapshot guarantee, not a
    cross-database transaction.
    """
    found = discover_source_snapshot(source_dir)
    view: dict[str, dict[str, Any]] = {}
    for wl in sorted(found):
        mem = _connect_readonly(found[wl]["memory"])
        his = _connect_readonly(found[wl]["history"])
        try:
            mem.execute("BEGIN")
            his.execute("BEGIN")
            try:
                if not _table_exists(mem, "core_facts"):
                    raise MigrationRehearsalError(
                        "invalid_source_layout",
                        f"memory snapshot for {wl} lacks core_facts: {found[wl]['memory']}",
                    )
                part: dict[str, Any] = {
                    "memory_schema_version": _schema_version(mem),
                    "history_schema_version": _schema_version(his),
                    "core_facts": mem.execute(
                        f"SELECT {','.join(_CORE_FACTS_COLUMNS)} FROM core_facts ORDER BY id"
                    ).fetchall(),
                    "conversations": his.execute(
                        f"SELECT {','.join(_CONVERSATIONS_COLUMNS)} FROM conversations ORDER BY 1"
                    ).fetchall(),
                    "messages": his.execute(
                        f"SELECT {','.join(_MESSAGES_COLUMNS)} FROM messages ORDER BY 1"
                    ).fetchall(),
                }
                part["erasure_requests"] = (
                    mem.execute(
                        f"SELECT {','.join(_ERASURE_REQUESTS_COLUMNS)} "
                        "FROM conversation_erasure_requests ORDER BY 1"
                    ).fetchall()
                    if _table_exists(mem, "conversation_erasure_requests")
                    else []
                )
                part["deleted_sources"] = (
                    mem.execute("SELECT * FROM memory_deleted_sources ORDER BY 1,2,3,4,5").fetchall()
                    if _table_exists(mem, "memory_deleted_sources") else []
                )
                part["tombstones"] = (
                    mem.execute(
                        f"SELECT {','.join(_TOMBSTONES_COLUMNS)} "
                        "FROM memory_tombstones ORDER BY 1"
                    ).fetchall()
                    if _table_exists(mem, "memory_tombstones")
                    else []
                )
            finally:
                # End the read-only snapshot transactions (nothing to undo).
                for conn in (mem, his):
                    try:
                        conn.execute("ROLLBACK")
                    except sqlite3.Error:  # pragma: no cover - defensive
                        pass
            view[wl] = part
        finally:
            mem.close()
            his.close()
    return view


def _source_id_from_view(view: dict[str, dict[str, Any]]) -> str:
    """Content digest over exactly the tables this tool reads.

    Any change to core_facts, erasure requests, tombstones, conversations or
    messages (the migration-relevant input) produces a different identity.
    Computed FROM the fixed view, never from a second read.
    """
    payload: dict[str, Any] = {}
    for wl, part in view.items():
        payload[wl] = {
            "memory_schema_version": part["memory_schema_version"],
            "history_schema_version": part["history_schema_version"],
            "core_facts": _rows_to_digest_lists(part["core_facts"]),
            "erasure_requests": _rows_to_digest_lists(part["erasure_requests"]),
            "tombstones": _rows_to_digest_lists(part["tombstones"]),
            "deleted_sources": _rows_to_digest_lists(part.get("deleted_sources", [])),
            "conversations": _rows_to_digest_lists(part["conversations"]),
            "messages": _rows_to_digest_lists(part["messages"]),
        }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def compute_source_identity(source_dir: str | Path) -> str:
    """Identity of one fixed read view of the snapshot (single read pass)."""
    return _source_id_from_view(_read_source_view(source_dir))


def _parse_legacy_source_ids(raw: Any) -> list[int] | None:
    """Strict parse of legacy source_message_ids JSON.

    Returns the de-duplicated ordered list of positive ints, or None when the
    value is unusable (missing/empty/malformed). Legacy rows always store
    ``json.dumps(list[int])``, so anything else is treated as malformed.
    """
    if raw is None:
        return None
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(parsed, list) or not parsed:
        return None
    ids: list[int] = []
    for value in parsed:
        if isinstance(value, bool) or not isinstance(value, int):
            return None
        if value <= 0:
            return None
        if value not in ids:
            ids.append(value)
    return ids


def _validate_fact_sources(
    row: sqlite3.Row,
    source_ids: list[int],
    messages: dict[int, sqlite3.Row],
    conversations: dict[str, sqlite3.Row],
) -> tuple[list[dict[str, Any]] | None, str | None, str | None, frozenset[str]]:
    """Validate every declared source; all must pass.

    Returns (source_details, reason, detail, source_sensitive_classes).
    source_details is None when any source fails; reason is the primary
    failure code. The FULL content of each source message is classified
    with the existing D30 hard-block rules; any hit class is reported (class
    names only, never the matched text) so the caller can block the import.
    """
    session_id = str(row["session_id"])
    failures: list[str] = []
    details: list[dict[str, Any]] = []
    sensitive: set[str] = set()
    for mid in source_ids:
        message = messages.get(int(mid))
        if message is None:
            failures.append(f"{mid}:source_missing")
            continue
        if str(message["role"]) != "user":
            failures.append(f"{mid}:source_not_user")
            continue
        if str(message["session_id"]) != session_id:
            failures.append(f"{mid}:owner_mismatch")
            continue
        conv = conversations.get(str(message["conversation_id"]))
        if conv is None:
            failures.append(f"{mid}:conversation_missing")
            continue
        if str(conv["session_id"]) != session_id:
            failures.append(f"{mid}:conversation_owner_mismatch")
            continue
        if str(conv["identity_mode"]) != str(row["identity_mode"]):
            failures.append(f"{mid}:identity_mismatch")
            continue
        content = str(message["content"] or "")
        sensitive |= classify_memory_sensitive_text(content)
        details.append(
            {
                "message_id": int(mid),
                "conversation_id": str(message["conversation_id"]),
                "created_at": message["created_at"],
                "fingerprint": _source_fingerprint(session_id, int(mid), content),
                "excerpt": content[:200],
            }
        )
    if failures:
        first_reason = failures[0].split(":", 1)[1]
        return None, first_reason, "; ".join(failures), frozenset(sensitive)
    return details, None, None, frozenset(sensitive)


def _covering_requests(mid, message, requests):
    """Ids survive history deletion; conversation matching is delete-only."""
    return [r for r in requests if
            mid in (_parse_legacy_source_ids(r["source_message_ids"]) or [])
            or (message is not None and r["action"] == "delete"
                and str(message["conversation_id"]) == str(r["conversation_id"]))]



def _evaluate_fact_row(
    worldline: str,
    row: sqlite3.Row,
    messages: dict[int, sqlite3.Row],
    conversations: dict[str, sqlite3.Row],
    erasure_requests_by_scope: dict[tuple[str, str], list[sqlite3.Row]],
    tombstone_fingerprints_by_scope: dict[tuple[str, str], set[str]],
) -> dict[str, Any]:
    session_id = str(row["session_id"] or "").strip()
    identity_mode = str(row["identity_mode"] or "").strip()
    fact_key = str(row["fact_key"] or "").strip()
    fact_value = str(row["fact_value"] or "").strip()
    item: dict[str, Any] = {
        "worldline": worldline,
        "legacy_row_id": int(row["id"]),
        "session_id": session_id,
        "identity_mode": identity_mode,
        "fact_key": fact_key,
        "fact_value": fact_value,
        "confidence": float(row["confidence"] or 0.0),
        "importance": float(row["importance"] or 0.0),
        "is_pinned": bool(int(row["is_pinned"] or 0)),
        "source_message_ids": [],
        "decision": None,
        "reason": None,
        "detail": None,
        "sources": None,
    }

    def _decide(decision: str, reason: str, detail: str | None = None) -> dict[str, Any]:
        item["decision"] = decision
        item["reason"] = reason
        item["detail"] = detail
        return item

    # D30 hard privacy block on the importable fact text FIRST, so a hit item
    # is redacted in preview/detail regardless of any other decision reason.
    sensitive_fact = classify_memory_sensitive_text(f"{fact_key}: {fact_value}")
    if sensitive_fact:
        item["fact_key"] = None
        item["fact_value"] = None
        return _decide(
            "skip",
            "sensitive_fact_blocked",
            f"sensitive_classes={','.join(sorted(sensitive_fact))}",
        )

    if not session_id:
        return _decide("skip", "invalid_owner")
    if identity_mode not in ("okabe", "self"):
        return _decide("skip", "invalid_identity_mode")
    if not fact_key or not fact_value:
        return _decide("skip", "empty_fact_content")
    if int(row["is_dismissed"] or 0) == 1:
        return _decide("skip", "dismissed")
    if int(row["is_current"] or 0) != 1:
        return _decide("skip", "not_current")

    raw_sources = row["source_message_ids"]
    if raw_sources is None or str(raw_sources).strip() in ("", "[]"):
        return _decide("skip", "source_empty")
    source_ids = _parse_legacy_source_ids(raw_sources)
    if source_ids is None:
        return _decide("skip", "source_malformed", f"source_message_ids={raw_sources!r}")
    item["source_message_ids"] = source_ids

    # Resolve durable erasure intent BEFORE requiring surviving history.
    requests = erasure_requests_by_scope.get((session_id, identity_mode), [])
    retained = {}
    for mid in source_ids:
        covering = _covering_requests(mid, messages.get(mid), requests)
        if any(int(r["forget_long_term"] or 0) == 1 for r in covering):
            return _decide("skip", "erasure_barrier")
        if covering:
            retained[mid] = sorted(covering, key=lambda r: (r["created_at"], r["request_id"]))[-1]
    # Validate surviving sources, including their full privacy classification.
    remaining = [mid for mid in source_ids if mid in messages or mid not in retained]
    source_details, reason, detail, source_sensitive = _validate_fact_sources(
        row, remaining, messages, conversations
    )
    if source_details is None:
        return _decide("skip", reason, detail)
    by_id = {d["message_id"]: d for d in source_details}
    for mid, request in retained.items():
        if mid not in by_id:
            # Domain separation: never pretend an empty string was the original
            # message. Missing-content hashes cannot identify old content hashes.
            marker = f"legacy-deleted:{session_id}:{identity_mode}:{mid}:{request['request_id']}"
            by_id[mid] = dict(message_id=mid, conversation_id=request["conversation_id"],
                             created_at=request["created_at"],
                             fingerprint=hashlib.sha256(marker.encode()).hexdigest(),
                             content_missing=True)
        by_id[mid].update(source_state="deleted", excerpt=None)
    source_details = [by_id[mid] for mid in source_ids]
    item["sources"] = source_details

    fingerprints = {str(d["fingerprint"]) for d in source_details}
    tombstoned = tombstone_fingerprints_by_scope.get((session_id, identity_mode), set())
    if fingerprints & tombstoned:
        return _decide("skip", "tombstone_source_deleted")

    if any(f"message_id:{mid}" in tombstoned for mid in source_ids):
        return _decide("skip", "tombstone_source_deleted")
    if "unverifiable" in tombstoned and any(d.get("content_missing") for d in source_details):
        # A content hash is irreversible. An unrelated-looking source tombstone
        # cannot safely be ruled out after the history is gone; require review.
        return _decide("defer", "tombstone_source_unverifiable")

    # D30 hard privacy block on the FULL source message contents. The fact
    # body itself may be ordinary ("来源本身命中而事实正文普通" case) — only the
    # classes and source ids are reported, never the matched source text.
    if source_sensitive:
        return _decide(
            "skip",
            "sensitive_source_blocked",
            "sensitive_classes="
            + ",".join(sorted(source_sensitive))
            + "; source_message_ids="
            + ",".join(str(int(d["message_id"])) for d in source_details),
        )

    if all(d.get("source_state", "present") != "deleted"
           and not str(d["excerpt"] or "").strip() for d in source_details):
        return _decide("defer", "empty_source_content")

    return _decide("planned", None)


def _duplicate_hints(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Same fact_value within one scope: shown, never merged (task §6)."""
    groups: dict[tuple[str, str, str, str], list[int]] = defaultdict(list)
    for item in items:
        if item["decision"] != "planned":
            continue
        key = (
            item["worldline"],
            item["session_id"],
            item["identity_mode"],
            item["fact_value"].strip().casefold(),
        )
        groups[key].append(int(item["legacy_row_id"]))
    hints: list[dict[str, Any]] = []
    for (wl, session_id, mode, value), row_ids in sorted(groups.items()):
        if len(row_ids) > 1:
            hints.append(
                {
                    "worldline": wl,
                    "session_id": session_id,
                    "identity_mode": mode,
                    "fact_value": value,
                    "legacy_row_ids": sorted(row_ids),
                    "note": "same fact_value within one scope; shown only, not merged",
                }
            )
    return hints


def _tombstone_scope_fingerprints(part):
    scopes = defaultdict(set)
    deleted = defaultdict(set)
    for row in part.get("deleted_sources", []):
        deleted[(row["session_id"], row["identity_mode"], row["fact_id"])].add(row["source_message_id"])
    for row in part["tombstones"]:
        scope = (row["session_id"], row["identity_mode"])
        ids = deleted.get((*scope, row["fact_id"]), set())
        if row["source_fingerprint"]:
            scopes[scope].add(row["source_fingerprint"])
            if not ids:
                scopes[scope].add("unverifiable")
        scopes[scope].update(f"message_id:{mid}" for mid in ids)
    return scopes


def _evaluate_view(view: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Classify every legacy core fact row from ONE fixed read view."""
    items: list[dict[str, Any]] = []
    worldlines_meta: dict[str, Any] = {}
    for wl in sorted(view):
        part = view[wl]
        messages = {int(r["id"]): r for r in part["messages"]}
        conversations = {str(r["id"]): r for r in part["conversations"]}
        erasure_requests_by_scope: dict[tuple[str, str], list[sqlite3.Row]] = defaultdict(list)
        for r in part["erasure_requests"]:
            erasure_requests_by_scope[(str(r["session_id"]), str(r["identity_mode"]))].append(r)
        tombstone_fps = _tombstone_scope_fingerprints(part)
        for row in part["core_facts"]:
            items.append(
                _evaluate_fact_row(
                    wl, row, messages, conversations, erasure_requests_by_scope, tombstone_fps
                )
            )
        worldlines_meta[wl] = {
            "memory_schema_version": part["memory_schema_version"],
            "history_schema_version": part["history_schema_version"],
            "core_facts_total": len(part["core_facts"]),
            "messages_total": len(messages),
            "erasure_requests_total": len(part["erasure_requests"]),
            "tombstones_total": sum(len(v) for v in tombstone_fps.values()),
        }

    by_reason: dict[str, int] = defaultdict(int)
    by_worldline: dict[str, dict[str, int]] = {}
    for item in items:
        key = item["reason"] or item["decision"]
        by_reason[key] += 1
        wl_stats = by_worldline.setdefault(
            item["worldline"], {"planned": 0, "skip": 0, "defer": 0}
        )
        wl_stats[item["decision"]] += 1
    summary = {
        "planned": sum(1 for i in items if i["decision"] == "planned"),
        "skip": sum(1 for i in items if i["decision"] == "skip"),
        "defer": sum(1 for i in items if i["decision"] == "defer"),
        "by_reason": dict(sorted(by_reason.items())),
        "by_worldline": by_worldline,
    }
    return {
        "items": items,
        "worldlines": worldlines_meta,
        "summary": summary,
        "duplicate_hints": _duplicate_hints(items),
        "pin_audit": _pin_audit(items),
    }


def evaluate_legacy_core_facts(source_dir: str | Path) -> dict[str, Any]:
    """Read-only classification of every legacy core fact row (one fixed view)."""
    return _evaluate_view(_read_source_view(source_dir))


def _pin_audit(items: list[dict[str, Any]]) -> dict[str, Any]:
    """Per-scope planned pin counts against the existing pin-limit constant.

    C ruling (revision 1): imported facts keep their original is_pinned; the
    audit only REPORTS. A scope above the limit is explicitly NOT eligible
    for production cutover under the current pin policy — a rehearsal pass
    cannot be used to skip that product constraint.
    """
    scopes: dict[tuple[str, str, str], int] = defaultdict(int)
    for item in items:
        if item["decision"] == "planned" and item["is_pinned"]:
            scopes[
                (item["worldline"], item["session_id"], item["identity_mode"])
            ] += 1
    entries: list[dict[str, Any]] = []
    for (wl, session_id, mode), count in sorted(scopes.items()):
        entries.append(
            {
                "worldline": wl,
                "session_id": session_id,
                "identity_mode": mode,
                "planned_pinned": count,
                "exceeds_pin_limit": count > MAX_PINNED_FACTS_PER_SCOPE,
            }
        )
    any_exceeds = any(entry["exceeds_pin_limit"] for entry in entries)
    audit: dict[str, Any] = {
        "pin_limit_per_scope": MAX_PINNED_FACTS_PER_SCOPE,
        "scopes": entries,
        "any_exceeds_limit": any_exceeds,
        "note": "imported facts keep their original is_pinned; pins are not auto-adjusted",
    }
    if any_exceeds:
        audit["note"] += (
            "; at least one scope exceeds the pin limit — NOT eligible for "
            "production cutover under the current pin policy"
        )
    return audit


def build_legacy_core_facts_preview(source_dir: str | Path) -> dict[str, Any]:
    """Pure preview report; writes nothing to the source (or anywhere else).

    The report, its source identity and the content a later apply would write
    all derive from ONE fixed read view of the snapshot.
    """
    root = Path(source_dir).expanduser().resolve()
    view = _read_source_view(root)
    evaluation = _evaluate_view(view)
    source_id = _source_id_from_view(view)
    items: list[dict[str, Any]] = []
    for item in evaluation["items"]:
        slim = dict(item)
        slim["sources"] = [
            {k: v for k, v in source.items() if k != "excerpt"}
            for source in (item.get("sources") or [])
        ]
        items.append(slim)
    return {
        "kind": PREVIEW_KIND,
        "tool_version": MIGRATION_TOOL_VERSION,
        "generated_at": _utc_now(),
        "source": {
            "path": str(root),
            "source_id": source_id,
            "worldlines": evaluation["worldlines"],
        },
        "items": items,
        "duplicate_hints": evaluation["duplicate_hints"],
        "pin_audit": evaluation["pin_audit"],
        "summary": evaluation["summary"],
    }


def _validate_source_target_disjoint(source_root: Path, target_root: Path) -> None:
    if (
        source_root == target_root
        or source_root in target_root.parents
        or target_root in source_root.parents
    ):
        raise MigrationRehearsalError(
            "overlapping_paths",
            f"source and target must be distinct, non-nested paths "
            f"(source={source_root}, target={target_root})",
        )


def _import_key(source_id: str, worldline: str, item: dict[str, Any]) -> str:
    raw = (
        f"{source_id}|{worldline}|{item['session_id']}|{item['identity_mode']}|"
        f"{int(item['legacy_row_id'])}"
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _write_imported_fact(
    conn: sqlite3.Connection,
    *,
    source_id: str,
    worldline: str,
    item: dict[str, Any],
    now: str,
    existing_fact_id: str | None = None,
) -> tuple[str, str]:
    """Insert observation + stable fact v1 + evidence + idempotency record.

    Runs inside the caller's BEGIN IMMEDIATE transaction; mirrors the
    reconciler CREATE shape (minus embeddings — no external calls here) with
    legacy_import semantics.
    """
    fact_id = existing_fact_id or str(uuid.uuid4())
    observation_id = str(uuid.uuid4())
    display_text = f"{item['fact_key']}: {item['fact_value']}".strip()
    semantic = {
        "subject": "user",
        "predicate": item["fact_key"],
        "object": {"text": item["fact_value"]},
        "qualifiers": {"source": "legacy_core_fact_import"},
    }
    from app.services.memory_v11.repository import _canonical_fingerprint

    semantic_blob = json.dumps(semantic, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    fingerprint = _canonical_fingerprint(semantic)
    confidence = float(item["confidence"])
    conn.execute(
        """INSERT INTO memory_observations(
               observation_id, session_id, identity_mode, display_text,
               semantic_json, semantic_fingerprint, evidence_kind, memory_class,
               confidence, topic_label_proposal, status, extractor_version,
               reanalysis_count, expires_at, created_at, updated_at
           ) VALUES(?,?,?,?,?,?,?,?,?,NULL,'attached',?,0,NULL,?,?)""",
        (
            observation_id,
            item["session_id"],
            item["identity_mode"],
            display_text,
            semantic_blob,
            fingerprint,
            "legacy_import",
            "stable_candidate",
            confidence,
            MIGRATION_TOOL_VERSION,
            now,
            now,
        ),
    )
    for source in item["sources"]:
        conn.execute(
            """INSERT INTO memory_observation_sources(
                   observation_id, source_message_id, conversation_id,
                   source_role, source_fingerprint, source_created_at,
                   source_state, excerpt
               ) VALUES(?,?,?, 'user', ?, ?, ?, ?)""",
            (
                observation_id,
                int(source["message_id"]),
                str(source["conversation_id"]),
                str(source["fingerprint"]),
                str(source["created_at"] or now),
                source.get("source_state", "present"),
                str(source["excerpt"] or "")[:200] or None,
            ),
        )
    if existing_fact_id is None:
        conn.execute(
            """INSERT INTO stable_facts(
                   fact_id, session_id, identity_mode, topic_id, state,
                   active_version, is_pinned, created_at, updated_at, deleted_at
               ) VALUES(?,?,?,NULL,'active',1,?,?,?,NULL)""",
            (
                fact_id,
                item["session_id"],
                item["identity_mode"],
                1 if item["is_pinned"] else 0,
                now,
                now,
            ),
        )
        conn.execute(
            """INSERT INTO stable_fact_versions(
                   fact_id, version_no, display_text, semantic_json,
                   semantic_fingerprint, confidence, change_kind,
                   previous_version, valid_from, invalid_at, created_by_job_id
               ) VALUES(?,1,?,?,?,?, 'legacy_import', NULL, ?, NULL, NULL)""",
            (fact_id, display_text, semantic_blob, fingerprint, confidence, now),
        )
    version = conn.execute("SELECT active_version FROM stable_facts WHERE fact_id=?", (fact_id,)).fetchone()[0]
    conn.execute(
        """INSERT INTO stable_fact_evidence(fact_id, version_no, observation_id, evidence_role)
           VALUES(?, ?, ?, ?)""",
        (fact_id, version, observation_id, "supporting" if existing_fact_id else "primary"),
    )
    conn.execute(
        """INSERT INTO legacy_core_fact_imports(
               import_key, source_id, worldline, session_id, identity_mode,
               legacy_row_id, fact_id, observation_id, imported_at
           ) VALUES(?,?,?,?,?,?,?,?,?)""",
        (
            _import_key(source_id, worldline, item),
            source_id,
            worldline,
            item["session_id"],
            item["identity_mode"],
            int(item["legacy_row_id"]),
            fact_id,
            observation_id,
            now,
        ),
    )
    return fact_id, observation_id


def _write_manifest_atomic(path: Path, payload: dict[str, Any]) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _init_target_memory_db(db_path: Path, worldline: str) -> None:
    from app.db import MEMORY_SCHEMA, _initialize_sync, _initialize_vec0

    db_path.parent.mkdir(parents=True, exist_ok=True)
    _initialize_sync(str(db_path), MEMORY_SCHEMA, "memory", worldline)
    conn = _connect_writable(db_path)
    try:
        conn.execute(_LEGACY_IMPORTS_TABLE_DDL)
        conn.execute(_LEGACY_IMPORTS_INDEX_DDL)
        conn.commit()
    finally:
        conn.close()
    try:
        _initialize_vec0(str(db_path))
    except Exception:
        # vec0 tables are optional rehearsal infrastructure; the import does
        # not depend on them (retrieval falls back to lexical matching).
        pass


def _prepare_rehearsal_target(
    target_root: Path, worldlines: list[str], source_id: str, source_root: Path
) -> None:
    from app.db import SCHEMA_VERSION

    manifest_path = target_root / "manifest.json"
    if target_root.exists():
        if not target_root.is_dir():
            raise MigrationRehearsalError(
                "invalid_target", f"target path is not a directory: {target_root}"
            )
        if any(target_root.iterdir()) and not manifest_path.is_file():
            raise MigrationRehearsalError(
                "not_a_rehearsal_target",
                f"non-empty target without manifest.json: {target_root}",
            )
    else:
        target_root.mkdir(parents=True)

    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise MigrationRehearsalError(
                "not_a_rehearsal_target", f"unreadable manifest: {exc}"
            ) from exc
        if (
            manifest.get("kind") != TARGET_KIND
            or manifest.get("tool_version") != MIGRATION_TOOL_VERSION
        ):
            raise MigrationRehearsalError(
                "not_a_rehearsal_target",
                "manifest does not describe a rehearsal target of this tool",
            )
        if manifest.get("source_id") != source_id:
            raise MigrationRehearsalError(
                "target_source_mismatch",
                "target is bound to a different source snapshot "
                f"(manifest source_id={manifest.get('source_id')})",
            )
        if int(manifest.get("app_schema_version") or -1) != int(SCHEMA_VERSION):
            raise MigrationRehearsalError(
                "target_schema_mismatch",
                f"target app schema {manifest.get('app_schema_version')} != "
                f"current {SCHEMA_VERSION}",
            )
        if sorted(manifest.get("worldlines") or []) != sorted(worldlines):
            raise MigrationRehearsalError(
                "target_source_mismatch",
                "target worldlines differ from the source snapshot",
            )
        for wl in worldlines:
            db_path = target_root / "worldlines" / _WORLDLINE_NAMESPACES[wl] / "memory.sqlite3"
            if not db_path.is_file():
                _init_target_memory_db(db_path, wl)
        return

    # Fresh target: the manifest is the claim marker written FIRST, so an
    # interrupted init is resumable (the branch above re-initializes missing
    # worldline DBs on the next run).
    _write_manifest_atomic(
        manifest_path,
        {
            "kind": TARGET_KIND,
            "tool_version": MIGRATION_TOOL_VERSION,
            "created_at": _utc_now(),
            "source_id": source_id,
            "source_path": str(source_root),
            "app_schema_version": int(SCHEMA_VERSION),
            "worldlines": list(worldlines),
            "runs": [],
        },
    )
    for wl in worldlines:
        db_path = target_root / "worldlines" / _WORLDLINE_NAMESPACES[wl] / "memory.sqlite3"
        _init_target_memory_db(db_path, wl)


def _append_target_run(target_root: Path, run: dict[str, Any]) -> None:
    manifest_path = target_root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.setdefault("runs", []).append(run)
    _write_manifest_atomic(manifest_path, manifest)


def apply_legacy_core_facts(
    *,
    source_dir: str | Path,
    target_dir: str | Path,
    preview: dict[str, Any],
) -> dict[str, Any]:
    """Import the preview's planned legacy core facts into the rehearsal target.

    Guarantees:
    - consumes the source through ONE fixed read view: the source identity,
      the item decisions and the content written into the target all derive
      from that same view, so apply imports exactly the previewed input
      (fixed-offline-snapshot semantics; no cross-database atomicity);
    - rejects a preview whose source content changed since it was generated,
      and a preview whose reviewed plan content was modified (semantic
      comparison of the planned items; generated_at and other non-semantic
      metadata are ignored);
    - one transaction per target physical DB: a failure leaves no partial
      fact/version/evidence/observation or idempotency record, and a failed
      DB contributes NOTHING to imported_facts — only committed mappings
      are returned;
    - repeat runs against the same target are idempotent (import records);
      facts deleted or revised in the target are never re-imported;
    - cross-worldline atomicity is NOT claimed; each DB reports its own status.
    """
    source_root = Path(source_dir).expanduser().resolve()
    target_root = Path(target_dir).expanduser().resolve()
    _validate_source_target_disjoint(source_root, target_root)

    if (
        not isinstance(preview, dict)
        or preview.get("kind") != PREVIEW_KIND
        or preview.get("tool_version") != MIGRATION_TOOL_VERSION
    ):
        raise MigrationRehearsalError(
            "invalid_preview",
            "preview payload is not a legacy core-facts preview of this tool version",
        )

    # ONE fixed read view: digest, decisions and write content share it.
    view = _read_source_view(source_root)
    source_id = _source_id_from_view(view)
    if preview.get("source", {}).get("source_id") != source_id:
        raise MigrationRehearsalError(
            "preview_stale",
            "source content changed since the preview was generated "
            f"(preview source_id={preview.get('source', {}).get('source_id')}, "
            f"current source_id={source_id})",
        )

    evaluation = _evaluate_view(view)

    def _planned_semantics(item: dict[str, Any]) -> str:
        sources = [
            {k: v for k, v in (s or {}).items() if k != "excerpt"}
            for s in (item.get("sources") or [])
        ]
        payload = {
            "worldline": item.get("worldline"),
            "legacy_row_id": item.get("legacy_row_id"),
            "session_id": item.get("session_id"),
            "identity_mode": item.get("identity_mode"),
            "fact_key": item.get("fact_key"),
            "fact_value": item.get("fact_value"),
            "confidence": item.get("confidence"),
            "importance": item.get("importance"),
            "is_pinned": bool(item.get("is_pinned")),
            "source_message_ids": item.get("source_message_ids"),
            "sources": sources,
        }
        return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    planned = [i for i in evaluation["items"] if i["decision"] == "planned"]
    preview_planned = [
        i for i in (preview.get("items") or []) if i.get("decision") == "planned"
    ]
    if sorted(_planned_semantics(i) for i in planned) != sorted(
        _planned_semantics(i) for i in preview_planned
    ):
        raise MigrationRehearsalError(
            "preview_mismatch",
            "preview planned set does not semantically match the reviewed "
            "plan for the current source (content or provenance differs)",
        )

    worldlines = sorted(evaluation["worldlines"])
    _prepare_rehearsal_target(target_root, worldlines, source_id, source_root)

    now = _utc_now()
    result: dict[str, Any] = {
        "ok": True,
        "tool_version": MIGRATION_TOOL_VERSION,
        "source_id": source_id,
        "target": str(target_root),
        "worldlines": {},
        "imported_facts": [],
    }
    for wl in worldlines:
        db_path = target_root / "worldlines" / _WORLDLINE_NAMESPACES[wl] / "memory.sqlite3"
        planned_wl = [i for i in planned if i["worldline"] == wl]
        conn = _connect_writable(db_path)
        already = 0
        committed_imports: list[dict[str, Any]] = []
        try:
            conn.execute("BEGIN IMMEDIATE")
            try:
                for item in planned_wl:
                    key = _import_key(source_id, wl, item)
                    exists = conn.execute(
                        "SELECT 1 FROM legacy_core_fact_imports WHERE import_key=?",
                        (key,),
                    ).fetchone()
                    if exists is not None:
                        already += 1
                        continue
                    fact_id, observation_id = _write_imported_fact(
                        conn, source_id=source_id, worldline=wl, item=item, now=now
                    )
                    committed_imports.append(
                        {
                            "worldline": wl,
                            "legacy_row_id": int(item["legacy_row_id"]),
                            "session_id": item["session_id"],
                            "identity_mode": item["identity_mode"],
                            "fact_id": fact_id,
                            "observation_id": observation_id,
                            "display_text": f"{item['fact_key']}: {item['fact_value']}".strip(),
                        }
                    )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        except Exception as exc:
            # Failed DB: the transaction was rolled back, so NOTHING from
            # this worldline enters imported_facts (no uncommitted IDs).
            result["ok"] = False
            result["worldlines"][wl] = {
                "status": "failed",
                "planned": len(planned_wl),
                "error": f"{type(exc).__name__}: {exc}",
                "rolled_back": True,
            }
        else:
            result["worldlines"][wl] = {
                "status": "committed",
                "planned": len(planned_wl),
                "imported": len(committed_imports),
                "already_imported": already,
            }
            result["imported_facts"].extend(committed_imports)
        finally:
            conn.close()

    _append_target_run(
        target_root,
        {
            "applied_at": now,
            "source_id": source_id,
            "ok": bool(result["ok"]),
            "worldlines": result["worldlines"],
        },
    )
    return result


# ---------------------------------------------------------------------------
# Explicit live import. The application never calls these on startup.
# ---------------------------------------------------------------------------
LIVE_PREVIEW_KIND = "legacy_memory_live_preview/1"
_LIVE_SOURCE_IDS = {"core": "live/core/1", "shared": "live/shared/1"}


def _read_live_view(memory_connections=None):
    """Fixed read transactions; a stopped app is required for CLI operations.

    During apply, memory reads use the already-held BEGIN IMMEDIATE locks.
    History/control are read-only and never initialized or migrated here.
    """
    view = {}
    for wl in _WORLDLINE_NAMESPACES:
        mem = (memory_connections or {}).get(wl)
        owned = mem is None
        if owned:
            mem = _connect_readonly(Path(_resolve_path(wl, "memory")))
            mem.execute("BEGIN")
        his = _connect_readonly(Path(_resolve_path(wl, "history")))
        try:
            his.execute("BEGIN")
            part = dict(memory_schema_version=_schema_version(mem),
                        history_schema_version=_schema_version(his))
            for key, conn, table, columns in (
                ("core_facts", mem, "core_facts", _CORE_FACTS_COLUMNS),
                ("erasure_requests", mem, "conversation_erasure_requests", _ERASURE_REQUESTS_COLUMNS),
                ("tombstones", mem, "memory_tombstones", _TOMBSTONES_COLUMNS),
                ("conversations", his, "conversations", _CONVERSATIONS_COLUMNS),
                ("messages", his, "messages", _MESSAGES_COLUMNS),
            ):
                part[key] = conn.execute(
                    f"SELECT {','.join(columns)} FROM {table} ORDER BY 1"
                ).fetchall()
            part["deleted_sources"] = (
                mem.execute("SELECT * FROM memory_deleted_sources ORDER BY 1,2,3,4,5").fetchall()
                if _table_exists(mem, "memory_deleted_sources") else []
            )
            part["imports"] = (mem.execute("SELECT * FROM legacy_core_fact_imports ORDER BY import_key").fetchall()
                               if _table_exists(mem, "legacy_core_fact_imports") else [])
            view[wl] = part
        finally:
            his.close()
            if owned:
                mem.close()
    control = _connect_readonly(Path(_resolve_path("steins_gate", "control")))
    try:
        control.execute("BEGIN")
        shared = control.execute("SELECT * FROM shared_user_facts ORDER BY id").fetchall()
    finally:
        control.close()
    return view, shared


def _live_source_identity(view, shared):
    return hashlib.sha256((_source_id_from_view(view) + json.dumps(
        _rows_to_digest_lists(shared), ensure_ascii=False, separators=(",", ":")
    )).encode()).hexdigest()


def _live_items(view, shared, *, session_id=None, include_core=True, include_shared=True):
    """Both sources use the rehearsal's one classifier (no parallel rules)."""
    items = []
    for wl, part in view.items():
        rows = []
        if include_core:
            rows.extend(("core", r) for r in part["core_facts"]
                        if session_id is None or r["session_id"] == session_id)
        if include_shared:
            for r in shared:
                if session_id is not None and r["owner_session_id"] != session_id:
                    continue
                if normalize_worldline(r["origin_worldline"]) != wl:
                    continue
                row = dict(r)
                row.update(session_id=r["owner_session_id"], identity_mode="self", is_dismissed=0)
                rows.append(("shared", row))
        requests = defaultdict(list)
        tombstones = _tombstone_scope_fingerprints(part)
        for r in part["erasure_requests"]:
            requests[(r["session_id"], r["identity_mode"])].append(r)
        messages = {r["id"]: r for r in part["messages"]}
        conversations = {r["id"]: r for r in part["conversations"]}
        for category, row in rows:
            item = _evaluate_fact_row(wl, row, messages, conversations, requests, tombstones)
            item["source_category"] = category
            item["source_id"] = _LIVE_SOURCE_IDS[category]
            item["import_key"] = _import_key(item["source_id"], wl, item)
            items.append(item)
    return items


def _slim_live_items(items):
    return [{k: v for k, v in item.items() if k != "sources"} for item in items]


def build_live_migration_preview():
    """Read-only; the CLI writes the returned report with exclusive creation."""
    view, shared = _read_live_view()
    items = _live_items(view, shared)
    return dict(kind=LIVE_PREVIEW_KIND, source_id=_live_source_identity(view, shared),
                items=_slim_live_items(items), reconciliation=_reconcile_live(items, preview=True))


def _reconcile_live(items, *, preview=False):
    scopes = {}
    for item in items:
        key = (item["worldline"], item["session_id"], item["identity_mode"], item["source_category"])
        stats = scopes.setdefault(key, dict(total=0, migrated=0, merged_into_local=0,
                                            skipped={}, deferred={}, unexplained=0))
        stats["total"] += 1
        outcome = item.get("outcome", item["decision"] if preview else None)
        if outcome in ("migrated", "already_imported", "planned"):
            stats["migrated"] += 1
        elif outcome == "merged_into_local":
            stats["merged_into_local"] += 1
        elif outcome in ("skip", "defer") and item.get("reason"):
            bucket = stats["skipped" if outcome == "skip" else "deferred"]
            bucket[item["reason"]] = bucket.get(item["reason"], 0) + 1
        else:
            stats["unexplained"] += 1
    result = []
    for (wl, session, mode, category), stats in sorted(scopes.items()):
        accounted = (stats["migrated"] + stats["merged_into_local"]
                     + sum(stats["skipped"].values()) + sum(stats["deferred"].values()))
        if stats["unexplained"] or accounted != stats["total"]:
            raise MigrationRehearsalError("reconciliation_failed")
        result.append(dict(worldline=wl, session_id=session, identity_mode=mode,
                           source_category=category, **stats))
    return result


def _value_key(item):
    return (item["session_id"], item["identity_mode"], item["fact_key"],
            str(item["fact_value"]).strip().casefold())


def _local_import_match(conn, item):
    """Match the read-only legacy row, so v11 edits/deletes stay authoritative."""
    rows = conn.execute(
        """SELECT l.fact_id,l.observation_id,c.fact_key,c.fact_value,f.state
           FROM legacy_core_fact_imports l
           JOIN core_facts c ON c.id=l.legacy_row_id AND c.session_id=l.session_id AND c.identity_mode=l.identity_mode
           JOIN stable_facts f ON f.fact_id=l.fact_id
           WHERE l.source_id=? AND l.session_id=? AND l.identity_mode=? AND c.fact_key=?
           ORDER BY l.legacy_row_id""",
        (_LIVE_SOURCE_IDS["core"], item["session_id"], item["identity_mode"], item["fact_key"]),
    ).fetchall()
    for row in rows:
        if (row["fact_key"] == item["fact_key"]
                and str(row["fact_value"]).strip().casefold()
                == str(item["fact_value"]).strip().casefold()):
            return row
    return None


async def _import_live(*, session_id=None, worldlines=None, include_core=True,
                       include_shared=True, preview=None):
    from app.services.memory_v11 import repository
    from app.services.memory_v11.indexing import prepare_passage_embedding

    selected = set(worldlines or _WORLDLINE_NAMESPACES)
    view, shared = _read_live_view()
    source_id = _live_source_identity(view, shared)
    items = [i for i in _live_items(view, shared, session_id=session_id,
                                  include_core=include_core, include_shared=include_shared)
             if i["worldline"] in selected]
    if preview is not None:
        if preview.get("kind") != LIVE_PREVIEW_KIND:
            raise MigrationRehearsalError("invalid_preview")
        if preview.get("source_id") != source_id:
            raise MigrationRehearsalError("preview_stale")
        if preview.get("items") != _slim_live_items(items):
            raise MigrationRehearsalError("preview_mismatch")
    # Reject unaccountable decisions before any write (also checked under locks).
    _reconcile_live(items, preview=True)
    existing = {r["import_key"] for wl in selected for r in view[wl]["imports"]}
    prepared = {}
    for item in items:
        if item["decision"] == "planned" and item["import_key"] not in existing:
            prepared[item["import_key"]] = await prepare_passage_embedding(
                f"{item['fact_key']}: {item['fact_value']}".strip())
    conns = {}
    try:
        # Hold every selected memory lock until all scopes have reconciled.
        # Exceptions roll back all open DBs. SQLite WAL commits across files
        # are not crash-atomic; offline backups are the recovery boundary.
        for wl in sorted(selected):
            conn = _connect_writable(Path(_resolve_path(wl, "memory")))
            conns[wl] = conn
            conn.execute("BEGIN IMMEDIATE")
        current_view, current_shared = _read_live_view(conns)
        if _live_source_identity(current_view, current_shared) != source_id:
            raise MigrationRehearsalError("preview_stale")
        items = [i for i in _live_items(current_view, current_shared, session_id=session_id,
                                       include_core=include_core, include_shared=include_shared)
                 if i["worldline"] in selected]
        _reconcile_live(items, preview=True)
        for wl, conn in conns.items():
            conn.execute(_LEGACY_IMPORTS_TABLE_DDL)
            conn.execute(_LEGACY_IMPORTS_INDEX_DDL)
            pending = [i for i in items if i["worldline"] == wl]
            # Old local rows precede promoted copies, regardless of row id.
            pending.sort(key=lambda i: (i["source_category"] != "core", i["legacy_row_id"]))
            new_local_keys = {_value_key(i) for i in pending
                              if i["source_category"] == "core" and i["decision"] == "planned"}
            pin_candidates = sorted(
                [i for i in pending if i["decision"] == "planned" and i["is_pinned"]
                 and i["import_key"] not in existing
                 and not (i["source_category"] == "shared" and
                          (_value_key(i) in new_local_keys or _local_import_match(conn, i)))],
                key=lambda i: (-i["importance"], -i["confidence"], i["legacy_row_id"], i["source_category"]),
            )
            pin_slots = {}
            for item in pin_candidates:
                scope = (item["session_id"], item["identity_mode"])
                if scope not in pin_slots:
                    used = conn.execute(
                        "SELECT COUNT(*) FROM stable_facts WHERE session_id=? AND identity_mode=? AND state='active' AND is_pinned=1",
                        scope,
                    ).fetchone()[0]
                    pin_slots[scope] = max(0, MAX_PINNED_FACTS_PER_SCOPE - used)
                if pin_slots[scope] > 0:
                    pin_slots[scope] -= 1
                else:
                    item.update(is_pinned=False, pin_reason="pin_limit_unpinned")
            for item in pending:
                recorded = conn.execute(
                    "SELECT fact_id,observation_id FROM legacy_core_fact_imports WHERE import_key=?",
                    (item["import_key"],),
                ).fetchone()
                if recorded is not None:
                    item.update(dict(recorded), outcome="already_imported")
                    if item["source_category"] == "shared":
                        local = conn.execute(
                            "SELECT 1 FROM legacy_core_fact_imports WHERE source_id=? AND fact_id=?",
                            (_LIVE_SOURCE_IDS["core"], recorded["fact_id"]),
                        ).fetchone()
                        if local:
                            item["outcome"] = "merged_into_local"
                    continue
                if item["decision"] != "planned":
                    item["outcome"] = item["decision"]
                    continue
                match = _local_import_match(conn, item) if item["source_category"] == "shared" else None
                now = _utc_now()
                if match is not None and match["state"] == "deleted":
                    # Do not recreate content the user has already deleted.
                    fact_id, observation_id = match["fact_id"], match["observation_id"]
                    conn.execute(
                        "INSERT INTO legacy_core_fact_imports VALUES(?,?,?,?,?,?,?,?,?)",
                        (item["import_key"], item["source_id"], wl, item["session_id"],
                         item["identity_mode"], item["legacy_row_id"], fact_id, observation_id, now),
                    )
                else:
                    fact_id, observation_id = await repository.create_stable_fact_from_legacy(
                        conn, source_id=item["source_id"], worldline=wl, item=item, now=now,
                        prepared=prepared.get(item["import_key"]),
                        existing_fact_id=match["fact_id"] if match else None,
                    )
                item.update(fact_id=fact_id, observation_id=observation_id,
                            outcome="merged_into_local" if match else "migrated")
        reconciliation = _reconcile_live(items)
        for conn in conns.values():
            conn.commit()
        return dict(items=_slim_live_items(items), reconciliation=reconciliation)
    except BaseException:
        for conn in conns.values():
            conn.rollback()
        raise
    finally:
        for conn in conns.values():
            conn.close()


async def import_legacy_core_facts(*, session_id: str, worldline: str):
    result = await _import_live(session_id=session_id, worldlines=[normalize_worldline(worldline)],
                                include_shared=False)
    return [i for i in result["items"] if i.get("fact_id")]


async def import_shared_user_facts(*, owner_session_id: str):
    return await _import_live(session_id=owner_session_id, include_core=False)


async def apply_live_migration(*, preview):
    return await _import_live(preview=preview)


# Deterministic, operator-confirmed consolidation; no provider calls.
def _consolidation_snapshot(conn, session_id, identity_mode):
    params = (session_id, identity_mode)
    scope = "SELECT fact_id FROM stable_facts WHERE session_id=? AND identity_mode=?"
    observations = "SELECT observation_id FROM memory_observations WHERE session_id=? AND identity_mode=?"
    payload = {}
    for name, where in (
        ("stable_facts", "session_id=? AND identity_mode=?"),
        ("stable_fact_versions", f"fact_id IN ({scope})"),
        ("stable_fact_evidence", f"fact_id IN ({scope})"),
        ("stable_fact_version_embeddings", f"fact_id IN ({scope})"),
        ("memory_observations", "session_id=? AND identity_mode=?"),
        ("memory_observation_sources", f"observation_id IN ({observations})"),
    ):
        rows = conn.execute(f"SELECT * FROM {name} WHERE {where} ORDER BY 1,2,3", params).fetchall()
        payload[name] = _rows_to_digest_lists(rows)
    rows = conn.execute(
        """SELECT f.*,v.display_text,v.semantic_json,v.semantic_fingerprint,v.confidence,v.change_kind,
           (SELECT COUNT(DISTINCT observation_id) FROM stable_fact_evidence e WHERE e.fact_id=f.fact_id) observation_count,
           EXISTS(SELECT 1 FROM stable_fact_versions h WHERE h.fact_id=f.fact_id AND h.change_kind='legacy_import') has_legacy_import
           FROM stable_facts f JOIN stable_fact_versions v ON v.fact_id=f.fact_id AND v.version_no=f.active_version
           WHERE f.session_id=? AND f.identity_mode=? AND f.state='active' AND v.invalid_at IS NULL
           ORDER BY f.fact_id""", params,
    ).fetchall()
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest(), [dict(r) for r in rows]


def _consolidation_groups(rows):
    from app.services.prompt_compiler import resident_rank_key

    parents = list(range(len(rows)))

    def root(i):
        while parents[i] != i:
            parents[i] = parents[parents[i]]
            i = parents[i]
        return i

    seen = {}
    for index, row in enumerate(rows):
        keys = [("semantic", row["semantic_fingerprint"]),
                ("display", row["display_text"].strip().casefold())]
        if row["has_legacy_import"]:
            value = json.loads(row["semantic_json"]).get("object", {}).get("text")
            if isinstance(value, str) and value.strip():
                keys.append(("legacy_value", value.strip().casefold()))
        for key in keys:
            if not key[1]:
                continue
            if key in seen:
                parents[root(index)] = root(seen[key])
            seen[key] = index
    groups = defaultdict(list)
    for index, row in enumerate(rows):
        groups[root(index)].append(row)
    return [[r["fact_id"] for r in sorted(group, key=resident_rank_key)]
            for group in groups.values() if len(group) > 1]


async def preview_consolidation(*, session_id: str, worldline: str, identity_mode: str):
    from app.db import normalize_identity_mode
    from app.services.memory_v11.repository import _require_session_id

    session_id = _require_session_id(session_id)
    identity_mode = normalize_identity_mode(identity_mode)
    conn = _connect_writable(Path(_resolve_path(normalize_worldline(worldline), "memory")))
    try:
        conn.execute("BEGIN IMMEDIATE")
        existing = conn.execute(
            "SELECT * FROM memory_consolidation_proposals WHERE session_id=? AND identity_mode=? AND status='open'",
            (session_id, identity_mode),
        ).fetchone()
        if existing:
            return dict(existing)
        snapshot_hash, rows = _consolidation_snapshot(conn, session_id, identity_mode)
        groups = _consolidation_groups(rows)
        if not groups:
            return None
        proposal_id = str(uuid.uuid4())
        now = _utc_now()
        conn.execute(
            """INSERT INTO memory_consolidation_proposals VALUES(?,?,?,?,?,?,?,?, 'open',?,?)""",
            (proposal_id, session_id, identity_mode, json.dumps([g[0] for g in groups]),
             json.dumps([fid for g in groups for fid in g[1:]]), json.dumps(["deterministic_duplicate"]),
             json.dumps(dict(groups=groups)), snapshot_hash, now, now),
        )
        conn.commit()
        return dict(proposal_id=proposal_id, status="open", snapshot_hash=snapshot_hash, groups=groups)
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


async def _finish_consolidation(*, proposal_id, worldline, apply):
    conn = _connect_writable(Path(_resolve_path(normalize_worldline(worldline), "memory")))
    try:
        conn.execute("BEGIN IMMEDIATE")
        proposal = conn.execute("SELECT * FROM memory_consolidation_proposals WHERE proposal_id=?", (proposal_id,)).fetchone()
        if proposal is None or proposal["status"] != "open":
            raise MigrationRehearsalError("proposal_not_open")
        now = _utc_now()
        if apply:
            snapshot_hash, rows = _consolidation_snapshot(conn, proposal["session_id"], proposal["identity_mode"])
            groups = _consolidation_groups(rows)
            if (snapshot_hash != proposal["snapshot_hash"]
                    or groups != json.loads(proposal["preview_json"])["groups"]):
                raise MigrationRehearsalError("snapshot_stale")
            for survivor, *duplicates in groups:
                version = conn.execute("SELECT active_version FROM stable_facts WHERE fact_id=?", (survivor,)).fetchone()[0]
                for duplicate in duplicates:
                    # All historical evidence matters to conversation erasure,
                    # not just evidence attached to the currently active version.
                    conn.execute(
                        """INSERT OR IGNORE INTO stable_fact_evidence
                           SELECT ?,?,observation_id,'supporting' FROM stable_fact_evidence WHERE fact_id=?""",
                        (survivor, version, duplicate),
                    )
                    conn.execute("DELETE FROM stable_fact_evidence WHERE fact_id=?", (duplicate,))
                    conn.execute("DELETE FROM stable_fact_version_embeddings WHERE fact_id=?", (duplicate,))
                    # Remove retired content as well: later conversation erasure
                    # only visits active facts, so archived duplicate versions
                    # must not survive behind an already-deleted shell.
                    conn.execute("DELETE FROM stable_fact_versions WHERE fact_id=?", (duplicate,))
                    if _table_exists(conn, "legacy_core_fact_imports"):
                        conn.execute("UPDATE legacy_core_fact_imports SET fact_id=? WHERE fact_id=?",
                                     (survivor, duplicate))
                    conn.execute(
                        """UPDATE stable_facts SET state='deleted',active_version=NULL,is_pinned=0,
                           topic_id=NULL,deleted_at=?,updated_at=? WHERE fact_id=?""", (now, now, duplicate),
                    )
                conn.execute("UPDATE stable_facts SET updated_at=? WHERE fact_id=?", (now, survivor))
        status = "applied" if apply else "cancelled"
        conn.execute("UPDATE memory_consolidation_proposals SET status=?,updated_at=? WHERE proposal_id=?",
                     (status, now, proposal_id))
        conn.commit()
        return dict(proposal_id=proposal_id, status=status)
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


async def apply_consolidation(*, proposal_id: str, worldline: str):
    return await _finish_consolidation(proposal_id=proposal_id, worldline=worldline, apply=True)


async def cancel_consolidation(*, proposal_id: str, worldline: str):
    return await _finish_consolidation(proposal_id=proposal_id, worldline=worldline, apply=False)


def backup_live_migration_databases():
    """Three SQLite backups, exclusively reserved names; never copy WAL files."""
    backups = []
    for wl, kind in (("steins_gate", "memory"), ("beta", "memory"), ("steins_gate", "control")):
        source_path = Path(_resolve_path(wl, kind))
        destination_path = source_path.with_name(
            f"{kind}.{wl}.{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.{uuid.uuid4().hex}.backup.sqlite3")
        # Exclusive creation removes even the unlikely concurrent name race.
        with destination_path.open("xb"):
            pass
        source = _connect_readonly(source_path)
        destination = sqlite3.connect(str(destination_path))
        try:
            source.backup(destination)
            destination.commit()
        finally:
            destination.close()
            source.close()
        backups.append(str(destination_path))
    return backups
