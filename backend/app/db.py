"""Worldline-isolated SQLite storage and idempotent startup migration."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

import aiosqlite

try:
    import portalocker
except ImportError:  # surfaced as a readiness/startup error, never silently ignored
    portalocker = None

Worldline = Literal["steins_gate", "beta"]
DatabaseKind = Literal["history", "memory", "control"]
SelectorAdmission = Literal["pending", "admitted", "manual"]

EMBEDDING_MODEL = "intfloat/multilingual-e5-small"
EMBEDDING_DIMENSION = 384
# v4: conversations.identity_mode (okabe|self); existing rows backfilled to okabe.
# v5: emotion_states keyed by (session_id, identity_mode); existing rows → okabe (U-M2).
# v6: core_facts / episodic_memories scoped by identity_mode; existing rows → okabe (U-M3).
# v7: conversations.identity_acknowledged (0|1); existing rows backfilled to 0 (IDENTITY-ACK-01).
# v8: control-only shared_user_facts (Q18 foundation); worldline stores bump meta only.
# v9: core_facts.is_dismissed (M3 soft-hide pending okabe candidates; audit retained).
# v10: provider_configurations multi-profile fields (display_name/enabled/created_at).
# v11: Memory observation–fact pipeline (additive tables; legacy core_facts retained).
SCHEMA_VERSION = 11

DEFAULT_IDENTITY_MODE = "okabe"
IDENTITY_MODES = frozenset({"okabe", "self"})

_initialized_paths: set[str] = set()
_init_lock = asyncio.Lock()
_migration_state: dict[str, object] = {"status": "not_started", "error": None}


def normalize_worldline(value: str | None) -> Worldline:
    aliases = {"sg": "steins_gate", "steins;gate": "steins_gate", "steins_gate": "steins_gate", "beta": "beta", "β": "beta"}
    try:
        return aliases[(value or "steins_gate").strip().lower()]  # type: ignore[return-value]
    except KeyError as exc:
        raise ValueError(f"unsupported worldline: {value!r}") from exc


def normalize_identity_mode(value: str | None) -> str:
    """Return okabe|self; None/empty → product default okabe (Q20-A)."""
    if value is None or str(value).strip() == "":
        return DEFAULT_IDENTITY_MODE
    cleaned = str(value).strip().lower()
    if cleaned not in IDENTITY_MODES:
        raise ValueError(f"unsupported identity_mode: {value!r}")
    return cleaned


def get_data_root() -> Path:
    override = os.environ.get("AMADEUS_DATA_DIR")
    if override:
        return Path(override).expanduser().resolve()
    appdata = os.environ.get("APPDATA")
    if appdata:
        return (Path(appdata) / "Amadeus").resolve()
    return (Path.home() / ".amadeus").resolve()


def default_conversation_id(session_id: str, worldline: str) -> str:
    """Return the stable compatibility conversation id for a session/worldline."""
    wl = normalize_worldline(worldline)
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"amadeus:{wl}:{session_id}:default"))


def _resolve_path(worldline: str = "steins_gate", kind: DatabaseKind = "history") -> str:
    """Resolve DB paths; AMADEUS_DB_PATH remains compatible with existing tests."""
    wl = normalize_worldline(worldline)
    legacy_override = os.environ.get("AMADEUS_DB_PATH")
    if legacy_override:
        base = Path(legacy_override).expanduser().resolve()
        if kind == "history" and wl == "steins_gate":
            return str(base)
        suffix = "beta" if wl == "beta" else "sg"
        if kind == "history":
            return str(base.with_name(f"{base.stem}.{suffix}{base.suffix or '.sqlite3'}"))
        return str(base.with_name(f"{base.stem}.{suffix}.{kind}{base.suffix or '.sqlite3'}"))

    root = get_data_root()
    if kind == "control":
        return str(root / "control.sqlite3")
    namespace = "sg" if wl == "steins_gate" else "beta"
    return str(root / "worldlines" / namespace / f"{kind}.sqlite3")


async def get_db(worldline: str = "steins_gate", kind: DatabaseKind = "history") -> aiosqlite.Connection:
    path = Path(_resolve_path(worldline, kind))
    path.parent.mkdir(parents=True, exist_ok=True)
    pending_connection = aiosqlite.connect(str(path), timeout=30.0)
    try:
        db = await pending_connection
    except BaseException:
        # aiosqlite queues its worker-stop sentinel when the initial await is
        # cancelled, but does not wait for the thread. Join only on this
        # exceptional path so the worker cannot call a closed event loop.
        worker = getattr(pending_connection, "_thread", None)
        if worker is not None and worker.is_alive():
            worker.join(timeout=5.0)
        raise
    try:
        db.row_factory = aiosqlite.Row
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("PRAGMA foreign_keys=ON")
        await db.execute("PRAGMA busy_timeout=30000")
        return db
    except BaseException:
        # A caller can be cancelled while the connection is still being
        # configured, before it receives the handle and establishes its own
        # try/finally. Close here so aiosqlite's worker never outlives the loop.
        await db.close()
        raise


HISTORY_SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    title TEXT NOT NULL,
    title_source TEXT NOT NULL DEFAULT 'auto' CHECK(title_source IN ('auto','manual')),
    is_default INTEGER NOT NULL DEFAULT 0 CHECK(is_default IN (0,1)),
    is_pinned INTEGER NOT NULL DEFAULT 0 CHECK(is_pinned IN (0,1)),
    provider_id TEXT,
    model_id TEXT,
    identity_mode TEXT NOT NULL DEFAULT 'okabe' CHECK(identity_mode IN ('okabe','self')),
    identity_acknowledged INTEGER NOT NULL DEFAULT 0 CHECK(identity_acknowledged IN (0,1)),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    last_active_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX IF NOT EXISTS idx_conversations_session_active
    ON conversations(session_id, is_pinned DESC, last_active_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS idx_conversations_one_default
    ON conversations(session_id) WHERE is_default=1;
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK(role IN ('system','user','assistant','tool')),
    content TEXT NOT NULL,
    translation TEXT,
    turn_id TEXT,
    revision INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, id);
CREATE INDEX IF NOT EXISTS idx_messages_conversation ON messages(conversation_id, id);
CREATE TABLE IF NOT EXISTS memory_summaries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    summary TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
-- Kept independently of conversation deletion so stale tasks cannot reuse an epoch.
CREATE TABLE IF NOT EXISTS conversation_content_epochs (
    session_id TEXT NOT NULL,
    conversation_id TEXT NOT NULL,
    epoch INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY(session_id, conversation_id)
);
CREATE TABLE IF NOT EXISTS conversation_erasure_commits (
    request_id TEXT PRIMARY KEY
);
CREATE INDEX IF NOT EXISTS idx_summary_session ON memory_summaries(session_id, id DESC);
CREATE INDEX IF NOT EXISTS idx_summary_conversation ON memory_summaries(conversation_id, id DESC);
CREATE TRIGGER IF NOT EXISTS messages_require_conversation_insert
BEFORE INSERT ON messages WHEN new.conversation_id IS NULL BEGIN
  SELECT RAISE(ABORT, 'messages.conversation_id is required');
END;
CREATE TRIGGER IF NOT EXISTS messages_require_conversation_update
BEFORE UPDATE OF conversation_id ON messages WHEN new.conversation_id IS NULL BEGIN
  SELECT RAISE(ABORT, 'messages.conversation_id is required');
END;
CREATE TRIGGER IF NOT EXISTS summaries_require_conversation_insert
BEFORE INSERT ON memory_summaries WHEN new.conversation_id IS NULL BEGIN
  SELECT RAISE(ABORT, 'memory_summaries.conversation_id is required');
END;
CREATE TRIGGER IF NOT EXISTS summaries_require_conversation_update
BEFORE UPDATE OF conversation_id ON memory_summaries WHEN new.conversation_id IS NULL BEGIN
  SELECT RAISE(ABORT, 'memory_summaries.conversation_id is required');
END;
CREATE TRIGGER IF NOT EXISTS messages_touch_conversation
AFTER INSERT ON messages BEGIN
  UPDATE conversations
     SET updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now'),
         last_active_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')
   WHERE id=new.conversation_id;
END;
"""

MEMORY_SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS episodic_memories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    identity_mode TEXT NOT NULL DEFAULT 'okabe' CHECK(identity_mode IN ('okabe','self')),
    content TEXT NOT NULL,
    confidence REAL NOT NULL CHECK(confidence BETWEEN 0 AND 1),
    importance REAL NOT NULL CHECK(importance BETWEEN 0 AND 1),
    source_message_ids TEXT NOT NULL,
    created_at TEXT NOT NULL,
    last_referenced_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_episode_session ON episodic_memories(session_id, identity_mode, id DESC);
CREATE VIRTUAL TABLE IF NOT EXISTS episodic_fts USING fts5(content, content='episodic_memories', content_rowid='id');
CREATE TRIGGER IF NOT EXISTS episodic_ai AFTER INSERT ON episodic_memories BEGIN
  INSERT INTO episodic_fts(rowid, content) VALUES (new.id, new.content);
END;
CREATE TRIGGER IF NOT EXISTS episodic_ad AFTER DELETE ON episodic_memories BEGIN
  INSERT INTO episodic_fts(episodic_fts, rowid, content) VALUES('delete', old.id, old.content);
END;
CREATE TABLE IF NOT EXISTS core_facts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    identity_mode TEXT NOT NULL DEFAULT 'okabe' CHECK(identity_mode IN ('okabe','self')),
    fact_key TEXT NOT NULL,
    fact_value TEXT NOT NULL,
    confidence REAL NOT NULL CHECK(confidence BETWEEN 0 AND 1),
    importance REAL NOT NULL CHECK(importance BETWEEN 0 AND 1),
    is_current INTEGER NOT NULL DEFAULT 1 CHECK(is_current IN (0,1)),
    is_pinned INTEGER NOT NULL DEFAULT 0 CHECK(is_pinned IN (0,1)),
    is_dismissed INTEGER NOT NULL DEFAULT 0 CHECK(is_dismissed IN (0,1)),
    source_message_ids TEXT NOT NULL,
    superseded_by INTEGER REFERENCES core_facts(id),
    created_at TEXT NOT NULL,
    last_referenced_at TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_current_fact ON core_facts(session_id, identity_mode, fact_key) WHERE is_current=1;
CREATE INDEX IF NOT EXISTS idx_core_fact_hot ON core_facts(session_id, identity_mode, is_current, is_pinned, importance DESC);
CREATE INDEX IF NOT EXISTS idx_core_fact_dismissed ON core_facts(session_id, identity_mode, is_current, is_dismissed);
CREATE TABLE IF NOT EXISTS memory_embeddings (
    memory_type TEXT NOT NULL CHECK(memory_type IN ('episodic','core')),
    memory_id INTEGER NOT NULL,
    model TEXT NOT NULL,
    dimensions INTEGER NOT NULL CHECK(dimensions=384),
    vector BLOB NOT NULL,
    PRIMARY KEY(memory_type, memory_id)
);
CREATE TABLE IF NOT EXISTS emotion_states (
    session_id TEXT NOT NULL,
    identity_mode TEXT NOT NULL DEFAULT 'okabe' CHECK(identity_mode IN ('okabe','self')),
    trust REAL NOT NULL,
    attachment REAL NOT NULL,
    irritation REAL NOT NULL,
    anxiety REAL NOT NULL,
    jealousy REAL NOT NULL,
    volatility REAL NOT NULL,
    updated_at_utc TEXT NOT NULL,
    PRIMARY KEY(session_id, identity_mode)
);
"""

# MEMORY-V11 additive structures (observation / stable fact / job / tombstone).
# Applied by MEMORY_SCHEMA (fresh install) and _migrate_v10_to_v11 (upgrade).
MEMORY_V11_DDL = """
CREATE TABLE IF NOT EXISTS memory_ingest_jobs (
    job_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    conversation_id TEXT NOT NULL,
    identity_mode TEXT NOT NULL CHECK(identity_mode IN ('self','okabe')),
    source_message_id INTEGER NOT NULL,
    pipeline_version TEXT NOT NULL,
    provider_id TEXT NOT NULL,
    model_id TEXT NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('pending','processing','completed','failed','cancelled')),
    attempt_count INTEGER NOT NULL DEFAULT 0,
    -- S4-ARCHIVE Gate 2: persisted retry-eligibility authority. 1 = the
    -- manual retry seam may move this failed row to pending; 0 = permanent
    -- failure (never retried). COMPAT SEMANTICS: rows that predate this
    -- column could not record failure provenance, so migration defaults
    -- them to 1 — explicitly documented, never inferred from error text.
    retryable INTEGER NOT NULL DEFAULT 1,
    next_attempt_at TEXT,
    lease_owner TEXT,
    lease_expires_at TEXT,
    last_error_code TEXT,
    last_error_hash TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_memory_ingest_jobs_source_cursor
    ON memory_ingest_jobs(
        session_id, conversation_id, identity_mode, source_message_id, pipeline_version
    );
CREATE INDEX IF NOT EXISTS idx_memory_ingest_jobs_state
    ON memory_ingest_jobs(state, next_attempt_at);

-- Durable, non-content intent across the separate history/memory databases.
-- Retained source ids also prevent in-flight extraction from reviving erased history.
CREATE TABLE IF NOT EXISTS conversation_erasure_requests (
    request_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    identity_mode TEXT NOT NULL,
    conversation_id TEXT NOT NULL,
    action TEXT NOT NULL CHECK(action IN ('forget','delete')),
    forget_long_term INTEGER NOT NULL,
    source_message_ids TEXT NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('pending','completed')),
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_conversation_erasure_scope
    ON conversation_erasure_requests(session_id, identity_mode, conversation_id);

CREATE TABLE IF NOT EXISTS memory_observations (
    observation_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    identity_mode TEXT NOT NULL CHECK(identity_mode IN ('self','okabe')),
    display_text TEXT,
    semantic_json TEXT,
    semantic_fingerprint TEXT,
    evidence_kind TEXT NOT NULL CHECK(
        evidence_kind IN ('direct_user','inferred_user','contextual_user','legacy_import')
    ),
    memory_class TEXT NOT NULL CHECK(
        memory_class IN ('stable_candidate','episodic','none')
    ),
    confidence REAL,
    topic_label_proposal TEXT,
    status TEXT NOT NULL CHECK(
        status IN ('candidate','attached','rejected','ignored','deleted','expired')
    ),
    extractor_version TEXT,
    reanalysis_count INTEGER NOT NULL DEFAULT 0 CHECK(reanalysis_count BETWEEN 0 AND 1),
    expires_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_memory_observations_scope_status
    ON memory_observations(session_id, identity_mode, status);
CREATE INDEX IF NOT EXISTS idx_memory_observations_fingerprint
    ON memory_observations(semantic_fingerprint);

CREATE TABLE IF NOT EXISTS memory_observation_sources (
    observation_id TEXT NOT NULL REFERENCES memory_observations(observation_id),
    source_message_id INTEGER NOT NULL,
    conversation_id TEXT NOT NULL,
    source_role TEXT NOT NULL CHECK(source_role = 'user'),
    source_fingerprint TEXT NOT NULL,
    source_created_at TEXT NOT NULL,
    source_state TEXT NOT NULL CHECK(source_state IN ('present','deleted')),
    excerpt TEXT,
    PRIMARY KEY(observation_id, source_message_id)
);

-- Topic identity is fully scoped: (topic_id, session_id, identity_mode).
-- Aliases and stable_facts must FK that composite so topics cannot attach across users/identities.
CREATE TABLE IF NOT EXISTS memory_topics (
    topic_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    identity_mode TEXT NOT NULL CHECK(identity_mode IN ('self','okabe')),
    normalized_label TEXT NOT NULL,
    display_label TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(topic_id, session_id, identity_mode)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_memory_topics_scope_label
    ON memory_topics(session_id, identity_mode, normalized_label);

CREATE TABLE IF NOT EXISTS memory_topic_aliases (
    topic_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    identity_mode TEXT NOT NULL CHECK(identity_mode IN ('self','okabe')),
    normalized_alias TEXT NOT NULL,
    PRIMARY KEY(session_id, identity_mode, normalized_alias),
    FOREIGN KEY(topic_id, session_id, identity_mode)
        REFERENCES memory_topics(topic_id, session_id, identity_mode)
);

CREATE TABLE IF NOT EXISTS stable_facts (
    fact_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    identity_mode TEXT NOT NULL CHECK(identity_mode IN ('self','okabe')),
    topic_id TEXT,
    state TEXT NOT NULL CHECK(state IN ('active','deleted')),
    active_version INTEGER,
    is_pinned INTEGER NOT NULL DEFAULT 0 CHECK(is_pinned IN (0,1)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    deleted_at TEXT,
    FOREIGN KEY(topic_id, session_id, identity_mode)
        REFERENCES memory_topics(topic_id, session_id, identity_mode)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_stable_facts_scope_id
    ON stable_facts(fact_id, session_id, identity_mode);
CREATE INDEX IF NOT EXISTS idx_stable_facts_scope_state
    ON stable_facts(session_id, identity_mode, state);

CREATE TABLE IF NOT EXISTS stable_fact_versions (
    fact_id TEXT NOT NULL REFERENCES stable_facts(fact_id),
    version_no INTEGER NOT NULL,
    display_text TEXT NOT NULL,
    semantic_json TEXT NOT NULL,
    semantic_fingerprint TEXT NOT NULL,
    confidence REAL NOT NULL CHECK(confidence BETWEEN 0 AND 1),
    change_kind TEXT NOT NULL CHECK(
        change_kind IN ('create','refine','supersede','user_edit','legacy_import')
    ),
    previous_version INTEGER,
    valid_from TEXT NOT NULL,
    invalid_at TEXT,
    created_by_job_id TEXT,
    PRIMARY KEY(fact_id, version_no)
);
-- Exactly one open (invalid_at IS NULL) version per fact.
CREATE UNIQUE INDEX IF NOT EXISTS idx_stable_fact_versions_one_open
    ON stable_fact_versions(fact_id) WHERE invalid_at IS NULL;

CREATE TABLE IF NOT EXISTS stable_fact_evidence (
    fact_id TEXT NOT NULL,
    version_no INTEGER NOT NULL,
    observation_id TEXT NOT NULL REFERENCES memory_observations(observation_id),
    evidence_role TEXT NOT NULL CHECK(evidence_role IN ('primary','supporting')),
    PRIMARY KEY(fact_id, version_no, observation_id),
    FOREIGN KEY(fact_id, version_no)
        REFERENCES stable_fact_versions(fact_id, version_no)
);

CREATE TABLE IF NOT EXISTS memory_tombstones (
    tombstone_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    identity_mode TEXT NOT NULL CHECK(identity_mode IN ('self','okabe')),
    fact_id TEXT NOT NULL,
    source_fingerprint TEXT,
    semantic_fingerprint TEXT,
    reason TEXT,
    deleted_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_memory_tombstones_scope
    ON memory_tombstones(session_id, identity_mode, fact_id);

CREATE TABLE IF NOT EXISTS memory_consolidation_proposals (
    proposal_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    identity_mode TEXT NOT NULL CHECK(identity_mode IN ('self','okabe')),
    survivor_fact_ids TEXT NOT NULL,
    duplicate_fact_ids TEXT NOT NULL,
    reason_codes TEXT NOT NULL,
    preview_json TEXT NOT NULL,
    snapshot_hash TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('open','applied','cancelled')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_memory_consolidation_scope_status
    ON memory_consolidation_proposals(session_id, identity_mode, status);

CREATE TABLE IF NOT EXISTS stable_fact_version_embeddings (
    fact_id TEXT NOT NULL,
    version_no INTEGER NOT NULL,
    model TEXT NOT NULL,
    dimensions INTEGER NOT NULL CHECK(dimensions=384),
    vector BLOB NOT NULL,
    PRIMARY KEY(fact_id, version_no),
    FOREIGN KEY(fact_id, version_no)
        REFERENCES stable_fact_versions(fact_id, version_no)
);

-- MEMORY-V11-S3C: first-class experiences (plan §4.10). Worldline stays
-- physical-DB isolation; identity_mode is the in-DB scope, same as stable_facts.
CREATE TABLE IF NOT EXISTS experiences (
    experience_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    conversation_id TEXT NOT NULL,
    identity_mode TEXT NOT NULL CHECK(identity_mode IN ('self','okabe')),
    observation_id TEXT NOT NULL UNIQUE REFERENCES memory_observations(observation_id),
    display_text TEXT NOT NULL,
    semantic_json TEXT NOT NULL,
    semantic_fingerprint TEXT NOT NULL,
    confidence REAL NOT NULL CHECK(confidence BETWEEN 0 AND 1),
    status TEXT NOT NULL CHECK(
        status IN ('active','deleted','expired','pending_source_delete')
    ),
    expires_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    deleted_at TEXT,
    is_pinned INTEGER NOT NULL DEFAULT 0 CHECK(is_pinned IN (0,1))
);
CREATE INDEX IF NOT EXISTS idx_experiences_scope_status
    ON experiences(session_id, identity_mode, status);
CREATE INDEX IF NOT EXISTS idx_experiences_fingerprint
    ON experiences(semantic_fingerprint);
CREATE INDEX IF NOT EXISTS idx_experiences_expires
    ON experiences(expires_at);

-- MEMORY-V11-S3F-1 (D28): durable non-semantic ingest receipts. Processing
-- infrastructure, not Memory content: no user text / excerpt / fingerprint /
-- provider response. Receipt identity mirrors the ingest job source cursor;
-- pipeline_version is mandatory so a receipt never suppresses a future
-- pipeline's deliberate reprocessing. Worldline stays physical DB isolation.
CREATE TABLE IF NOT EXISTS memory_ingest_receipts (
    session_id TEXT NOT NULL,
    conversation_id TEXT NOT NULL,
    identity_mode TEXT NOT NULL CHECK(identity_mode IN ('self','okabe')),
    source_message_id INTEGER NOT NULL,
    pipeline_version TEXT NOT NULL,
    receipt_kind TEXT NOT NULL CHECK(
        receipt_kind IN ('provider_empty','provider_nonempty')
    ),
    created_at TEXT NOT NULL,
    PRIMARY KEY(
        session_id,
        conversation_id,
        identity_mode,
        source_message_id,
        pipeline_version
    )
);
"""

MEMORY_SCHEMA = MEMORY_SCHEMA + MEMORY_V11_DDL

# Q18 foundation: cross-worldline shared stable user facts live only in
# control.sqlite3. origin_identity_mode is constrained to 'self' — okabe
# role-play must never populate the real-user profile. Superseded rows are
# kept (not deleted) for a minimal audit chain.
# Table and index DDL stay as separate statements so the v7→v8 migration can
# run them inside one explicit transaction (executescript would auto-commit).
SHARED_USER_FACTS_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS shared_user_facts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_session_id TEXT NOT NULL,
    fact_key TEXT NOT NULL,
    fact_value TEXT NOT NULL,
    confidence REAL NOT NULL CHECK(confidence BETWEEN 0 AND 1),
    importance REAL NOT NULL CHECK(importance BETWEEN 0 AND 1),
    is_current INTEGER NOT NULL DEFAULT 1 CHECK(is_current IN (0,1)),
    is_pinned INTEGER NOT NULL DEFAULT 0 CHECK(is_pinned IN (0,1)),
    origin_worldline TEXT NOT NULL CHECK(origin_worldline IN ('steins_gate','beta')),
    origin_conversation_id TEXT NOT NULL,
    origin_identity_mode TEXT NOT NULL CHECK(origin_identity_mode='self'),
    source_message_ids TEXT NOT NULL,
    created_at TEXT NOT NULL,
    superseded_by INTEGER REFERENCES shared_user_facts(id)
)
"""
SHARED_USER_FACTS_INDEX_DDL = (
    """CREATE UNIQUE INDEX IF NOT EXISTS idx_shared_current_fact
    ON shared_user_facts(owner_session_id, fact_key) WHERE is_current=1""",
    """CREATE INDEX IF NOT EXISTS idx_shared_fact_hot
    ON shared_user_facts(owner_session_id, is_current, is_pinned, importance DESC)""",
)
SHARED_USER_FACTS_DDL = (
    SHARED_USER_FACTS_TABLE_DDL.rstrip().rstrip(";") + ";\n"
    + ";\n".join(SHARED_USER_FACTS_INDEX_DDL) + ";\n"
)

# Columns any healthy control store must expose on shared_user_facts.
SHARED_USER_FACTS_REQUIRED_COLUMNS = frozenset({
    "id", "owner_session_id", "fact_key", "fact_value", "confidence",
    "importance", "is_current", "is_pinned", "origin_worldline",
    "origin_conversation_id", "origin_identity_mode", "source_message_ids",
    "created_at", "superseded_by",
})
SHARED_USER_FACTS_REQUIRED_INDEXES = frozenset({
    "idx_shared_current_fact", "idx_shared_fact_hot",
})

CONTROL_SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS session_worldlines (
    session_id TEXT PRIMARY KEY,
    worldline TEXT NOT NULL CHECK(worldline IN ('steins_gate','beta')),
    revision INTEGER NOT NULL DEFAULT 0,
    updated_at_utc TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS session_conversation_selections (
    session_id TEXT NOT NULL,
    worldline TEXT NOT NULL CHECK(worldline IN ('steins_gate','beta')),
    conversation_id TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL,
    PRIMARY KEY(session_id, worldline)
);
CREATE TABLE IF NOT EXISTS search_usage (
    day_utc TEXT PRIMARY KEY,
    tavily_credits INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS provider_configurations (
    provider_id TEXT PRIMARY KEY,
    base_url TEXT NOT NULL,
    default_model TEXT NOT NULL,
    display_name TEXT NOT NULL DEFAULT '',
    enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0,1)),
    selector_admission TEXT NOT NULL DEFAULT 'admitted'
        CHECK(selector_admission IN ('pending','admitted','manual')),
    created_at_utc TEXT NOT NULL DEFAULT '',
    updated_at_utc TEXT NOT NULL
);
""" + SHARED_USER_FACTS_DDL


def _read_schema_version(connection: sqlite3.Connection) -> int | None:
    connection.execute("CREATE TABLE IF NOT EXISTS schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    row = connection.execute("SELECT value FROM schema_meta WHERE key='schema_version'").fetchone()
    return int(row[0]) if row else None


def _column_exists(connection: sqlite3.Connection, table: str, column: str) -> bool:
    return any(row[1] == column for row in connection.execute(f"PRAGMA table_info({table})"))


def _prepare_history_v2_ddl(connection: sqlite3.Connection) -> None:
    """Commit the additive DDL stage before transactional data backfill."""
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS conversations (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            title TEXT NOT NULL,
            title_source TEXT NOT NULL DEFAULT 'auto' CHECK(title_source IN ('auto','manual')),
            is_default INTEGER NOT NULL DEFAULT 0 CHECK(is_default IN (0,1)),
            is_pinned INTEGER NOT NULL DEFAULT 0 CHECK(is_pinned IN (0,1)),
            provider_id TEXT,
            model_id TEXT,
            created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
            updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
            last_active_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
        );
        """
    )
    if not _column_exists(connection, "messages", "conversation_id"):
        connection.execute(
            "ALTER TABLE messages ADD COLUMN conversation_id TEXT REFERENCES conversations(id) ON DELETE CASCADE"
        )
    if not _column_exists(connection, "memory_summaries", "conversation_id"):
        connection.execute(
            "ALTER TABLE memory_summaries ADD COLUMN conversation_id TEXT REFERENCES conversations(id) ON DELETE CASCADE"
        )
    connection.commit()


def _backfill_history_v2(connection: sqlite3.Connection, worldline: str) -> None:
    """Backfill legacy rows atomically; schema DDL is intentionally separate."""
    try:
        connection.execute("BEGIN IMMEDIATE")
        rows = connection.execute(
            """SELECT session_id FROM messages
               UNION
               SELECT session_id FROM memory_summaries
               ORDER BY session_id"""
        ).fetchall()
        for (session_id,) in rows:
            conversation_id = default_conversation_id(session_id, worldline)
            connection.execute(
                """INSERT OR IGNORE INTO conversations(
                       id,session_id,title,title_source,is_default,is_pinned
                   ) VALUES(?,?,?,'auto',1,0)""",
                (conversation_id, session_id, "Default Conversation"),
            )
            connection.execute(
                "UPDATE messages SET conversation_id=? WHERE session_id=? AND conversation_id IS NULL",
                (conversation_id, session_id),
            )
            connection.execute(
                "UPDATE memory_summaries SET conversation_id=? WHERE session_id=? AND conversation_id IS NULL",
                (conversation_id, session_id),
            )

        missing_messages = connection.execute(
            "SELECT COUNT(*) FROM messages WHERE conversation_id IS NULL"
        ).fetchone()[0]
        missing_summaries = connection.execute(
            "SELECT COUNT(*) FROM memory_summaries WHERE conversation_id IS NULL"
        ).fetchone()[0]
        if missing_messages or missing_summaries:
            raise RuntimeError(
                f"conversation backfill incomplete: messages={missing_messages}, summaries={missing_summaries}"
            )
        connection.execute(
            "INSERT OR REPLACE INTO schema_meta(key,value) VALUES('schema_version',?)",
            ("2",),
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise


def _migrate_v1_to_v2(connection: sqlite3.Connection, kind: DatabaseKind, worldline: str) -> None:
    if kind == "history":
        _prepare_history_v2_ddl(connection)
        _backfill_history_v2(connection, worldline)
        return
    connection.execute(
        "INSERT OR REPLACE INTO schema_meta(key,value) VALUES('schema_version',?)",
        ("2",),
    )
    connection.commit()


def _migrate_v2_to_v3(connection: sqlite3.Connection, kind: DatabaseKind) -> None:
    """Add persisted assistant translations without rewriting existing rows."""
    if kind == "history" and not _column_exists(connection, "messages", "translation"):
        connection.execute("ALTER TABLE messages ADD COLUMN translation TEXT")
    connection.execute(
        "INSERT OR REPLACE INTO schema_meta(key,value) VALUES('schema_version','3')"
    )
    connection.commit()


def _prepare_history_v4_ddl(connection: sqlite3.Connection) -> None:
    """Additive DDL: identity_mode column with okabe default (existing rows get default)."""
    if not _column_exists(connection, "conversations", "identity_mode"):
        connection.execute(
            "ALTER TABLE conversations ADD COLUMN identity_mode TEXT NOT NULL DEFAULT 'okabe'"
        )
    connection.commit()


def _backfill_history_v4(connection: sqlite3.Connection) -> None:
    """Normalize identity_mode to okabe|self; fail closed with full rollback."""
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """UPDATE conversations
                   SET identity_mode='okabe'
                 WHERE identity_mode IS NULL
                    OR identity_mode NOT IN ('okabe','self')"""
        )
        bad = connection.execute(
            """SELECT COUNT(*) FROM conversations
                WHERE identity_mode IS NULL OR identity_mode NOT IN ('okabe','self')"""
        ).fetchone()[0]
        if bad:
            raise RuntimeError(
                f"identity_mode backfill incomplete: invalid_rows={bad}"
            )
        connection.execute(
            "INSERT OR REPLACE INTO schema_meta(key,value) VALUES('schema_version',?)",
            ("4",),
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise


def _migrate_v3_to_v4(connection: sqlite3.Connection, kind: DatabaseKind) -> None:
    """Conversation identity_mode (Q20-A default / Q21 immutable session field)."""
    if kind == "history":
        _prepare_history_v4_ddl(connection)
        _backfill_history_v4(connection)
        return
    connection.execute(
        "INSERT OR REPLACE INTO schema_meta(key,value) VALUES('schema_version',?)",
        ("4",),
    )
    connection.commit()


def _prepare_memory_v5_emotion_rebuild(connection: sqlite3.Connection) -> None:
    """Rebuild emotion_states with composite PK (session_id, identity_mode); backfill okabe.

    Additive intent with SQLite PK change: rebuild in a transaction; existing rows
    map to identity_mode=okabe (U-M2). self is never cloned from okabe (Q25).
    """
    exists = connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='emotion_states'"
    ).fetchone()
    if not exists:
        return
    # Already on v5 shape?
    columns = {
        row[1]: row
        for row in connection.execute("PRAGMA table_info(emotion_states)")
    }
    pk_cols = [
        name
        for name, row in sorted(
            ((name, row) for name, row in columns.items() if row[5] > 0),
            key=lambda item: item[1][5],
        )
    ]
    if "identity_mode" in columns and pk_cols == ["session_id", "identity_mode"]:
        return

    connection.execute(
        """CREATE TABLE emotion_states_v5 (
               session_id TEXT NOT NULL,
               identity_mode TEXT NOT NULL DEFAULT 'okabe'
                   CHECK(identity_mode IN ('okabe','self')),
               trust REAL NOT NULL,
               attachment REAL NOT NULL,
               irritation REAL NOT NULL,
               anxiety REAL NOT NULL,
               jealousy REAL NOT NULL,
               volatility REAL NOT NULL,
               updated_at_utc TEXT NOT NULL,
               PRIMARY KEY(session_id, identity_mode)
           )"""
    )
    if "identity_mode" in columns:
        connection.execute(
            """INSERT INTO emotion_states_v5(
                   session_id, identity_mode, trust, attachment, irritation,
                   anxiety, jealousy, volatility, updated_at_utc
               )
               SELECT session_id,
                      CASE
                          WHEN identity_mode IN ('okabe','self') THEN identity_mode
                          ELSE 'okabe'
                      END,
                      trust, attachment, irritation, anxiety, jealousy,
                      volatility, updated_at_utc
                 FROM emotion_states"""
        )
    else:
        connection.execute(
            """INSERT INTO emotion_states_v5(
                   session_id, identity_mode, trust, attachment, irritation,
                   anxiety, jealousy, volatility, updated_at_utc
               )
               SELECT session_id, 'okabe', trust, attachment, irritation,
                      anxiety, jealousy, volatility, updated_at_utc
                 FROM emotion_states"""
        )
    connection.execute("DROP TABLE emotion_states")
    connection.execute("ALTER TABLE emotion_states_v5 RENAME TO emotion_states")


def _backfill_memory_v5(connection: sqlite3.Connection) -> None:
    """Validate emotion_states modes; fail closed with full rollback."""
    try:
        connection.execute("BEGIN IMMEDIATE")
        _prepare_memory_v5_emotion_rebuild(connection)
        if connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='emotion_states'"
        ).fetchone():
            connection.execute(
                """UPDATE emotion_states
                       SET identity_mode='okabe'
                     WHERE identity_mode IS NULL
                        OR identity_mode NOT IN ('okabe','self')"""
            )
            bad = connection.execute(
                """SELECT COUNT(*) FROM emotion_states
                    WHERE identity_mode IS NULL
                       OR identity_mode NOT IN ('okabe','self')"""
            ).fetchone()[0]
            if bad:
                raise RuntimeError(
                    f"emotion_states identity_mode backfill incomplete: invalid_rows={bad}"
                )
        connection.execute(
            "INSERT OR REPLACE INTO schema_meta(key,value) VALUES('schema_version',?)",
            ("5",),
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise


def _migrate_v4_to_v5(connection: sqlite3.Connection, kind: DatabaseKind) -> None:
    """emotion_states (session, identity_mode) isolation (Q25 / U-M2=okabe)."""
    if kind == "memory":
        _backfill_memory_v5(connection)
        return
    connection.execute(
        "INSERT OR REPLACE INTO schema_meta(key,value) VALUES('schema_version',?)",
        ("5",),
    )
    connection.commit()


def _prepare_memory_v6_identity_scope(connection: sqlite3.Connection) -> None:
    """Additive DDL: identity_mode on episodic/core memories; mode-scoped unique fact key.

    Existing rows receive DEFAULT okabe (U-M3). self is never cloned (Q23/Q25).
    """
    if connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='episodic_memories'"
    ).fetchone():
        if not _column_exists(connection, "episodic_memories", "identity_mode"):
            connection.execute(
                "ALTER TABLE episodic_memories ADD COLUMN identity_mode TEXT NOT NULL DEFAULT 'okabe'"
            )
        connection.execute("DROP INDEX IF EXISTS idx_episode_session")
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_episode_session "
            "ON episodic_memories(session_id, identity_mode, id DESC)"
        )

    if connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='core_facts'"
    ).fetchone():
        if not _column_exists(connection, "core_facts", "identity_mode"):
            connection.execute(
                "ALTER TABLE core_facts ADD COLUMN identity_mode TEXT NOT NULL DEFAULT 'okabe'"
            )
        connection.execute("DROP INDEX IF EXISTS idx_current_fact")
        connection.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_current_fact "
            "ON core_facts(session_id, identity_mode, fact_key) WHERE is_current=1"
        )
        connection.execute("DROP INDEX IF EXISTS idx_core_fact_hot")
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_core_fact_hot "
            "ON core_facts(session_id, identity_mode, is_current, is_pinned, importance DESC)"
        )


def _backfill_memory_v6(connection: sqlite3.Connection) -> None:
    """Normalize memory identity_mode to okabe|self; fail closed with full rollback."""
    try:
        connection.execute("BEGIN IMMEDIATE")
        _prepare_memory_v6_identity_scope(connection)
        for table in ("episodic_memories", "core_facts"):
            if not connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                (table,),
            ).fetchone():
                continue
            if not _column_exists(connection, table, "identity_mode"):
                continue
            connection.execute(
                f"""UPDATE {table}
                       SET identity_mode='okabe'
                     WHERE identity_mode IS NULL
                        OR identity_mode NOT IN ('okabe','self')"""
            )
            bad = connection.execute(
                f"""SELECT COUNT(*) FROM {table}
                    WHERE identity_mode IS NULL
                       OR identity_mode NOT IN ('okabe','self')"""
            ).fetchone()[0]
            if bad:
                raise RuntimeError(
                    f"{table} identity_mode backfill incomplete: invalid_rows={bad}"
                )
        connection.execute(
            "INSERT OR REPLACE INTO schema_meta(key,value) VALUES('schema_version',?)",
            ("6",),
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise


def _migrate_v5_to_v6(connection: sqlite3.Connection, kind: DatabaseKind) -> None:
    """Memory extraction scope by identity_mode (Q23 / U-M3=okabe)."""
    if kind == "memory":
        _backfill_memory_v6(connection)
        return
    connection.execute(
        "INSERT OR REPLACE INTO schema_meta(key,value) VALUES('schema_version',?)",
        ("6",),
    )
    connection.commit()


def _migrate_v6_to_v7(connection: sqlite3.Connection, kind: DatabaseKind) -> None:
    """IDENTITY-ACK-01: conversation-scoped identity_acknowledged flag.

    Additive column; legacy rows backfill to 0 (never explained yet). The flag
    lives on the conversation row only — no global or memory-system state.
    """
    if kind == "history":
        if not _column_exists(connection, "conversations", "identity_acknowledged"):
            connection.execute(
                "ALTER TABLE conversations "
                "ADD COLUMN identity_acknowledged INTEGER NOT NULL DEFAULT 0"
            )
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """UPDATE conversations
                       SET identity_acknowledged=0
                     WHERE identity_acknowledged IS NULL
                        OR identity_acknowledged NOT IN (0,1)"""
            )
            connection.execute(
                "INSERT OR REPLACE INTO schema_meta(key,value) VALUES('schema_version',?)",
                ("7",),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        return
    connection.execute(
        "INSERT OR REPLACE INTO schema_meta(key,value) VALUES('schema_version',?)",
        ("7",),
    )
    connection.commit()


def _migrate_v7_to_v8(connection: sqlite3.Connection, kind: DatabaseKind) -> None:
    """Q18 foundation: shared_user_facts in control.sqlite3 only.

    control creates the table, both indexes and the version bump inside one
    BEGIN IMMEDIATE transaction — a failed migration rolls everything back.
    history/memory perform a meta-only version bump (no DDL).
    """
    try:
        connection.execute("BEGIN IMMEDIATE")
        if kind == "control":
            connection.execute(SHARED_USER_FACTS_TABLE_DDL)
            for index_ddl in SHARED_USER_FACTS_INDEX_DDL:
                connection.execute(index_ddl)
        connection.execute(
            "INSERT OR REPLACE INTO schema_meta(key,value) VALUES('schema_version',?)",
            ("8",),
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise


def _migrate_v8_to_v9(connection: sqlite3.Connection, kind: DatabaseKind) -> None:
    """M3: soft-dismiss flag on core_facts (hide from ledger/prompt; keep row)."""
    try:
        connection.execute("BEGIN IMMEDIATE")
        if kind == "memory":
            if connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='core_facts'"
            ).fetchone():
                if not _column_exists(connection, "core_facts", "is_dismissed"):
                    connection.execute(
                        "ALTER TABLE core_facts "
                        "ADD COLUMN is_dismissed INTEGER NOT NULL DEFAULT 0"
                    )
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS idx_core_fact_dismissed "
                    "ON core_facts(session_id, identity_mode, is_current, is_dismissed)"
                )
        connection.execute(
            "INSERT OR REPLACE INTO schema_meta(key,value) VALUES('schema_version',?)",
            ("9",),
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise


def _migrate_v9_to_v10(connection: sqlite3.Connection, kind: DatabaseKind) -> None:
    """Q41 Slice B: multi OpenAI-compatible profiles on control DB (atomic)."""
    try:
        connection.execute("BEGIN IMMEDIATE")
        if kind == "control":
            if connection.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name='provider_configurations'"
            ).fetchone():
                if not _column_exists(connection, "provider_configurations", "display_name"):
                    connection.execute(
                        "ALTER TABLE provider_configurations "
                        "ADD COLUMN display_name TEXT NOT NULL DEFAULT ''"
                    )
                if not _column_exists(connection, "provider_configurations", "enabled"):
                    connection.execute(
                        "ALTER TABLE provider_configurations "
                        "ADD COLUMN enabled INTEGER NOT NULL DEFAULT 1"
                    )
                if not _column_exists(connection, "provider_configurations", "created_at_utc"):
                    connection.execute(
                        "ALTER TABLE provider_configurations "
                        "ADD COLUMN created_at_utc TEXT NOT NULL DEFAULT ''"
                    )
                now = datetime.now(timezone.utc).isoformat()
                rows = connection.execute(
                    "SELECT provider_id, updated_at_utc, display_name, created_at_utc "
                    "FROM provider_configurations"
                ).fetchall()
                for provider_id, updated_at, display_name, created_at in rows:
                    fallback_name = (
                        "OpenAI-compatible"
                        if provider_id == "custom"
                        else str(provider_id)
                    )
                    connection.execute(
                        """UPDATE provider_configurations
                           SET display_name=?,
                               enabled=COALESCE(enabled, 1),
                               created_at_utc=?
                           WHERE provider_id=?""",
                        (
                            (display_name or "").strip() or fallback_name,
                            (created_at or "").strip() or (updated_at or now),
                            provider_id,
                        ),
                    )
        connection.execute(
            "INSERT OR REPLACE INTO schema_meta(key,value) VALUES('schema_version',?)",
            ("10",),
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise


def _ensure_experiences_is_pinned(connection: sqlite3.Connection) -> None:
    """M3: experiences.is_pinned NOT NULL DEFAULT 0 with existing-row backfill."""
    exists = connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='experiences'"
    ).fetchone()
    if exists is None:
        return
    columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(experiences)")}
    if "is_pinned" in columns:
        connection.execute(
            "UPDATE experiences SET is_pinned=0 WHERE is_pinned IS NULL"
        )
        return
    connection.execute(
        "ALTER TABLE experiences ADD COLUMN is_pinned INTEGER NOT NULL DEFAULT 0"
    )


def _ensure_memory_ingest_jobs_retryable(connection: sqlite3.Connection) -> None:
    """S4-ARCHIVE Gate 2: memory_ingest_jobs.retryable NOT NULL DEFAULT 1.

    Persisted retry-eligibility authority for the Archive failed-jobs surface.
    COMPAT SEMANTICS (explicit): rows written before this column could not
    record failure provenance (permanent vs auto-retry exhaustion), so the
    migration defaults them to 1 — the retry seam remains open for them,
    exactly as it was before the column existed. New terminal failures are
    stamped by the ingest pipeline (jobs._transition_owned).
    """
    exists = connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='memory_ingest_jobs'"
    ).fetchone()
    if exists is None:
        return
    columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(memory_ingest_jobs)")}
    if "retryable" in columns:
        return
    connection.execute(
        "ALTER TABLE memory_ingest_jobs ADD COLUMN retryable INTEGER NOT NULL DEFAULT 1"
    )


def _split_sql_statements(script: str) -> list[str]:
    """Split a DDL script into statements without using executescript (avoids auto-commit)."""
    statements: list[str] = []
    buf: list[str] = []
    for raw_line in script.splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()
        if not stripped or stripped.startswith("--"):
            continue
        buf.append(line)
        if stripped.endswith(";"):
            sql = "\n".join(buf).strip().rstrip(";").strip()
            if sql:
                statements.append(sql)
            buf = []
    tail = "\n".join(buf).strip().rstrip(";").strip()
    if tail:
        statements.append(tail)
    return statements


def _migrate_v10_to_v11(
    connection: sqlite3.Connection,
    kind: DatabaseKind,
    worldline: str = "steins_gate",
) -> None:
    """MEMORY-V11: additive observation/fact/job tables on memory DBs; bump all kinds to 11.

    - memory: non-overwrite backup, then transactional DDL (legacy core_facts retained).
    - history/control: schema_version bump only (no shared row deletion in S1).
    Failures roll back DDL and leave schema_version at 10 with no partial v11 tables.
    """
    if kind == "memory":
        # Backup before any DDL so a later failure never loses the pre-migration file.
        from app.services.memory_v11.migration import backup_memory_connection

        backup_memory_connection(connection, worldline=worldline, schema_label="v10")

    try:
        connection.execute("BEGIN IMMEDIATE")
        if kind == "memory":
            # Do not use executescript here — it auto-COMMITs and breaks atomic rollback.
            for statement in _split_sql_statements(MEMORY_V11_DDL):
                connection.execute(statement)
        connection.execute(
            "INSERT OR REPLACE INTO schema_meta(key,value) VALUES('schema_version',?)",
            ("11",),
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise


async def _initialize_one(worldline: str, kind: DatabaseKind, schema: str) -> None:
    path = _resolve_path(worldline, kind)
    if path in _initialized_paths:
        return
    await asyncio.to_thread(_initialize_sync, path, schema, kind, worldline)
    _initialized_paths.add(path)


def _initialize_vec0(path: str) -> None:
    """Create the dimension-locked vec0 table when sqlite-vec is installed."""
    try:
        import sqlite_vec
    except ImportError:
        return
    connection = sqlite3.connect(path)
    try:
        connection.enable_load_extension(True)
        sqlite_vec.load(connection)
        connection.execute("CREATE VIRTUAL TABLE IF NOT EXISTS episodic_vec USING vec0(embedding float[384])")
        connection.execute("CREATE VIRTUAL TABLE IF NOT EXISTS core_vec USING vec0(embedding float[384])")
        connection.commit()
    finally:
        connection.close()


def _ensure_provider_selector_admission(connection: sqlite3.Connection) -> None:
    columns = {row[1] for row in connection.execute("PRAGMA table_info(provider_configurations)")}
    if "selector_admission" not in columns:
        connection.execute(
            """ALTER TABLE provider_configurations ADD COLUMN
               selector_admission TEXT NOT NULL DEFAULT 'admitted'
               CHECK(selector_admission IN ('pending','admitted','manual'))"""
        )


def _initialize_sync(path: str, schema: str, kind: DatabaseKind, worldline: str = "steins_gate") -> None:
    db_path = Path(path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=30.0)
    try:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        current = _read_schema_version(connection)
        if current is not None and current > SCHEMA_VERSION:
            raise RuntimeError(f"unsupported {kind} schema version at {path}: {current}")
        if current == 1:
            _migrate_v1_to_v2(connection, kind, worldline)
            current = 2
        if current == 2:
            _migrate_v2_to_v3(connection, kind)
            current = 3
        if current == 3:
            _migrate_v3_to_v4(connection, kind)
            current = 4
        if current == 4:
            _migrate_v4_to_v5(connection, kind)
            current = 5
        if current == 5:
            _migrate_v5_to_v6(connection, kind)
            current = 6
        if current == 6:
            _migrate_v6_to_v7(connection, kind)
            current = 7
        if current == 7:
            _migrate_v7_to_v8(connection, kind)
            current = 8
        if current == 8:
            _migrate_v8_to_v9(connection, kind)
            current = 9
        if current == 9:
            _migrate_v9_to_v10(connection, kind)
            current = 10
        if current == 10:
            _migrate_v10_to_v11(connection, kind, worldline)
            current = 11
        if current not in (None, SCHEMA_VERSION):
            raise RuntimeError(f"unsupported {kind} schema version at {path}: {current}")

        connection.executescript(schema)
        if kind == "control":
            _ensure_provider_selector_admission(connection)
        if kind == "memory":
            _ensure_experiences_is_pinned(connection)
            _ensure_memory_ingest_jobs_retryable(connection)
        connection.execute(
            "INSERT OR REPLACE INTO schema_meta(key,value) VALUES('schema_version',?)",
            (str(SCHEMA_VERSION),),
        )
        if kind == "memory":
            model = connection.execute("SELECT value FROM schema_meta WHERE key='embedding_model'").fetchone()
            dimension = connection.execute("SELECT value FROM schema_meta WHERE key='embedding_dimension'").fetchone()
            if model and model[0] != EMBEDDING_MODEL:
                raise RuntimeError(f"embedding model mismatch at {path}: {model[0]}")
            if dimension and int(dimension[0]) != EMBEDDING_DIMENSION:
                raise RuntimeError(f"embedding dimension mismatch at {path}: {dimension[0]}")
            connection.execute("INSERT OR IGNORE INTO schema_meta(key,value) VALUES('embedding_model',?)", (EMBEDDING_MODEL,))
            connection.execute("INSERT OR IGNORE INTO schema_meta(key,value) VALUES('embedding_dimension',?)", (str(EMBEDDING_DIMENSION),))
        connection.commit()
    finally:
        connection.close()
    if kind == "memory":
        _initialize_vec0(path)


def _run_migration_locked() -> None:
    if portalocker is None:
        raise RuntimeError("portalocker is required for safe database migration")
    override = os.environ.get("AMADEUS_DB_PATH")
    root = Path(override).expanduser().resolve().parent if override else get_data_root()
    root.mkdir(parents=True, exist_ok=True)
    lock_path = root / ".migration.lock"
    marker = root / f".migration-v{SCHEMA_VERSION}.complete"
    with portalocker.Lock(str(lock_path), mode="a+", timeout=30, flags=portalocker.LOCK_EX | portalocker.LOCK_NB) as lock_file:
        if not marker.exists():
            legacy = root / "amadeus.db"
            if not override and legacy.exists():
                stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                shutil.copy2(legacy, root / f"amadeus.legacy.{stamp}.db")
            _initialize_sync(_resolve_path("steins_gate", "control"), CONTROL_SCHEMA, "control", "steins_gate")
            for worldline in ("steins_gate", "beta"):
                _initialize_sync(_resolve_path(worldline, "history"), HISTORY_SCHEMA, "history", worldline)
                _initialize_sync(_resolve_path(worldline, "memory"), MEMORY_SCHEMA, "memory", worldline)
            marker.write_text(json.dumps({"schema_version": SCHEMA_VERSION, "completed_at_utc": datetime.now(timezone.utc).isoformat()}), encoding="utf-8")
            with marker.open("r+b") as handle:
                handle.flush()
                os.fsync(handle.fileno())
        lock_file.flush()
        os.fsync(lock_file.fileno())


async def init_db() -> None:
    global _migration_state
    async with _init_lock:
        try:
            _migration_state = {"status": "running", "error": None}
            await asyncio.to_thread(_run_migration_locked)
            await _initialize_one("steins_gate", "control", CONTROL_SCHEMA)
            for wl in ("steins_gate", "beta"):
                await _initialize_one(wl, "history", HISTORY_SCHEMA)
                await _initialize_one(wl, "memory", MEMORY_SCHEMA)
                await asyncio.to_thread(_initialize_vec0, _resolve_path(wl, "memory"))
            _migration_state = {"status": "ready", "error": None}
        except Exception as exc:
            _migration_state = {"status": "error", "error": str(exc)}
            raise


def _provider_configuration_row(row) -> dict[str, object]:
    payload = dict(row)
    payload["enabled"] = bool(int(payload.get("enabled") or 0))
    payload["configuration_version"] = payload["updated_at_utc"]
    return payload


async def get_provider_configuration(provider_id: str) -> dict[str, object] | None:
    db = await get_db("steins_gate", "control")
    try:
        cur = await db.execute(
            """SELECT provider_id, base_url, default_model, display_name,
                      enabled, selector_admission, created_at_utc, updated_at_utc
               FROM provider_configurations
               WHERE provider_id=?""",
            (provider_id,),
        )
        row = await cur.fetchone()
        return _provider_configuration_row(row) if row else None
    finally:
        await db.close()


async def list_provider_configurations(
    *,
    enabled_only: bool = False,
    include_disabled: bool = True,
) -> list[dict[str, object]]:
    db = await get_db("steins_gate", "control")
    try:
        if enabled_only:
            cur = await db.execute(
                """SELECT provider_id, base_url, default_model, display_name,
                          enabled, selector_admission, created_at_utc, updated_at_utc
                   FROM provider_configurations
                   WHERE enabled=1
                   ORDER BY created_at_utc ASC, provider_id ASC"""
            )
        elif include_disabled:
            cur = await db.execute(
                """SELECT provider_id, base_url, default_model, display_name,
                          enabled, selector_admission, created_at_utc, updated_at_utc
                   FROM provider_configurations
                   ORDER BY enabled DESC, created_at_utc ASC, provider_id ASC"""
            )
        else:
            cur = await db.execute(
                """SELECT provider_id, base_url, default_model, display_name,
                          enabled, selector_admission, created_at_utc, updated_at_utc
                   FROM provider_configurations
                   WHERE enabled=1
                   ORDER BY created_at_utc ASC, provider_id ASC"""
            )
        rows = await cur.fetchall()
        return [_provider_configuration_row(row) for row in rows]
    finally:
        await db.close()


def _next_provider_configuration_version(previous: str | None = None) -> str:
    now = datetime.now(timezone.utc)
    if previous:
        try:
            prior = datetime.fromisoformat(previous).astimezone(timezone.utc)
        except ValueError:
            pass
        else:
            now = max(now, prior + timedelta(microseconds=1))
    return now.isoformat()


async def save_provider_configuration(
    provider_id: str,
    *,
    base_url: str,
    default_model: str,
    display_name: str | None = None,
    enabled: bool = True,
    initial_admission: SelectorAdmission = "admitted",
) -> dict[str, object]:
    """Persist a profile row. Success is declared after COMMIT using known values.

    A post-commit re-read must never be required to decide whether the write
    landed — that path can mis-classify a committed row as failure.
    """
    fallback_name = (
        "OpenAI-compatible" if provider_id == "custom" else provider_id
    )
    name = (display_name or "").strip() or fallback_name
    db = await get_db("steins_gate", "control")
    try:
        await db.execute("BEGIN IMMEDIATE")
        cur = await db.execute(
            "SELECT created_at_utc, updated_at_utc, selector_admission FROM provider_configurations WHERE provider_id=?",
            (provider_id,),
        )
        existing = await cur.fetchone()
        now = _next_provider_configuration_version(existing["updated_at_utc"] if existing else None)
        admission = existing["selector_admission"] if existing else initial_admission
        created_at = (
            str(existing["created_at_utc"] or now)
            if existing is not None
            else now
        )
        await db.execute(
            """INSERT INTO provider_configurations(
                   provider_id,base_url,default_model,display_name,
                   enabled,selector_admission,created_at_utc,updated_at_utc
               ) VALUES(?,?,?,?,?,?,?,?)
               ON CONFLICT(provider_id) DO UPDATE SET
                   base_url=excluded.base_url,
                   default_model=excluded.default_model,
                   display_name=excluded.display_name,
                   enabled=excluded.enabled,
                   updated_at_utc=excluded.updated_at_utc""",
            (
                provider_id,
                base_url,
                default_model,
                name,
                1 if enabled else 0,
                admission,
                created_at,
                now,
            ),
        )
        await db.commit()
        return {
            "provider_id": provider_id,
            "base_url": base_url,
            "default_model": default_model,
            "display_name": name,
            "enabled": bool(enabled),
            "selector_admission": admission,
            "configuration_version": now,
            "created_at_utc": created_at,
            "updated_at_utc": now,
        }
    finally:
        await db.close()


async def soft_disable_provider_configuration(
    provider_id: str,
    *,
    base_url: str | None = None,
    default_model: str | None = None,
    display_name: str | None = None,
) -> dict[str, object]:
    """Persist enabled=0. Creates a disabled stub row when the profile had no DB row yet.

    Return value is built from pre-commit known fields after a successful COMMIT.
    """
    existing = await get_provider_configuration(provider_id)
    now = _next_provider_configuration_version(existing["updated_at_utc"] if existing else None)
    if existing is None:
        if not base_url or not default_model:
            raise ValueError(
                f"cannot soft-disable missing profile {provider_id!r} without configuration"
            )
        return await save_provider_configuration(
            provider_id,
            base_url=base_url,
            default_model=default_model,
            display_name=display_name,
            enabled=False,
        )
    effective_base = str(base_url or existing["base_url"])
    effective_model = str(default_model or existing["default_model"])
    effective_name = (
        (display_name or "").strip()
        or str(existing.get("display_name") or "")
        or provider_id
    )
    created_at = str(existing.get("created_at_utc") or now)
    db = await get_db("steins_gate", "control")
    try:
        await db.execute(
            """UPDATE provider_configurations
               SET enabled=0,
                   base_url=?,
                   default_model=?,
                   display_name=?,
                   updated_at_utc=?
               WHERE provider_id=?""",
            (effective_base, effective_model, effective_name, now, provider_id),
        )
        await db.commit()
        return {
            "provider_id": provider_id,
            "base_url": effective_base,
            "default_model": effective_model,
            "display_name": effective_name,
            "enabled": False,
            "selector_admission": existing["selector_admission"],
            "configuration_version": now,
            "created_at_utc": created_at,
            "updated_at_utc": now,
        }
    finally:
        await db.close()


async def update_provider_selector_admission(
    provider_id: str, admission: SelectorAdmission, expected_configuration_version: str,
) -> dict[str, object] | None:
    """仅在配置版本仍匹配且接入未停用时保存准入值。"""
    db = await get_db("steins_gate", "control")
    try:
        cur = await db.execute(
            """UPDATE provider_configurations SET selector_admission=?
               WHERE provider_id=? AND enabled=1 AND updated_at_utc=?
               RETURNING *""",
            (admission, provider_id, expected_configuration_version),
        )
        row = await cur.fetchone()
        await cur.close()
        await db.commit()
        return _provider_configuration_row(row) if row else None
    finally:
        await db.close()


async def touch_provider_configuration_version(provider_id: str) -> None:
    """密钥写入前推进配置版本，阻止旧检查结果准入。"""
    db = await get_db("steins_gate", "control")
    try:
        await db.execute("BEGIN IMMEDIATE")
        cur = await db.execute(
            "SELECT updated_at_utc FROM provider_configurations WHERE provider_id=?",
            (provider_id,),
        )
        row = await cur.fetchone()
        if row:
            await db.execute(
                "UPDATE provider_configurations SET updated_at_utc=? WHERE provider_id=?",
                (_next_provider_configuration_version(row["updated_at_utc"]), provider_id),
            )
        await db.commit()
    finally:
        await db.close()


PROVIDER_CONFIGURATIONS_REQUIRED_COLUMNS = frozenset({
    "provider_id",
    "base_url",
    "default_model",
    "display_name",
    "enabled",
    "selector_admission",
    "created_at_utc",
    "updated_at_utc",
})


async def validate_schema() -> dict[str, object]:
    result: dict[str, object] = {"ok": True, "databases": {}, "migration": dict(_migration_state)}
    for wl in ("steins_gate", "beta"):
        for kind in ("history", "memory"):
            db = await get_db(wl, kind)
            try:
                cur = await db.execute("SELECT value FROM schema_meta WHERE key='schema_version'")
                row = await cur.fetchone()
                ok = bool(row and int(row[0]) == SCHEMA_VERSION)
                if kind == "memory":
                    cur = await db.execute("SELECT value FROM schema_meta WHERE key='embedding_dimension'")
                    dim = await cur.fetchone()
                    ok = ok and bool(dim and int(dim[0]) == EMBEDDING_DIMENSION)
                result["databases"][f"{wl}:{kind}"] = ok  # type: ignore[index]
                result["ok"] = bool(result["ok"] and ok)
            finally:
                await db.close()
    # The control store is a single worldline-agnostic database; validate it
    # exactly once outside the worldline loop (Q18 foundation). A partially
    # migrated control schema (missing columns/indexes) must fail validation.
    db = await get_db("steins_gate", "control")
    try:
        cur = await db.execute("SELECT value FROM schema_meta WHERE key='schema_version'")
        row = await cur.fetchone()
        ok = bool(row and int(row[0]) == SCHEMA_VERSION)
        cur = await db.execute("PRAGMA table_info(shared_user_facts)")
        columns = {info[1] for info in await cur.fetchall()}
        ok = ok and SHARED_USER_FACTS_REQUIRED_COLUMNS <= columns
        cur = await db.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='shared_user_facts'"
        )
        indexes = {info[0] for info in await cur.fetchall()}
        ok = ok and SHARED_USER_FACTS_REQUIRED_INDEXES <= indexes
        cur = await db.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name='provider_configurations'"
        )
        has_provider_table = bool(await cur.fetchone())
        ok = ok and has_provider_table
        if has_provider_table:
            cur = await db.execute("PRAGMA table_info(provider_configurations)")
            provider_columns = {info[1] for info in await cur.fetchall()}
            ok = ok and PROVIDER_CONFIGURATIONS_REQUIRED_COLUMNS <= provider_columns
        result["databases"]["control"] = ok  # type: ignore[index]
        result["ok"] = bool(result["ok"] and ok)
    finally:
        await db.close()
    return result


def reset_initialization_cache() -> None:
    """Test helper for environment-path changes."""
    _initialized_paths.clear()
