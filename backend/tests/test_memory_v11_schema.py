"""MEMORY-V11 S0 red contracts: schema v11 DDL, constraints, migration safety (B01/B02).

Fixed package surface:
  app.services.memory_v11.contracts
  app.services.memory_v11.repository
  (+ later jobs / reconciler / migration)

Fail until S1 lands SCHEMA_VERSION=11 and additive DDL. No production code here.
"""
from __future__ import annotations

import importlib
import sqlite3
from pathlib import Path

import pytest

from app import db as db_module
from app.db import init_db, reset_initialization_cache


V11_MEMORY_TABLES = (
    "memory_ingest_jobs",
    "memory_observations",
    "memory_observation_sources",
    "memory_topics",
    "memory_topic_aliases",
    "stable_facts",
    "stable_fact_versions",
    "stable_fact_evidence",
    "memory_tombstones",
    "memory_consolidation_proposals",
)

# Frozen minimal real v10 memory DDL — do NOT call db_module.MEMORY_SCHEMA (mutable).
# Captures the production v10 core surface required for migration tests.
FROZEN_V10_MEMORY_DDL = """
CREATE TABLE IF NOT EXISTS schema_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
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
CREATE INDEX IF NOT EXISTS idx_episode_session
    ON episodic_memories(session_id, identity_mode, id DESC);
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
CREATE UNIQUE INDEX IF NOT EXISTS idx_current_fact
    ON core_facts(session_id, identity_mode, fact_key) WHERE is_current=1;
CREATE INDEX IF NOT EXISTS idx_core_fact_hot
    ON core_facts(session_id, identity_mode, is_current, is_pinned, importance DESC);
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

INGEST_UNIQUE_COLUMNS = (
    "session_id",
    "conversation_id",
    "identity_mode",
    "source_message_id",
    "pipeline_version",
)

MEMORY_V11_MODULES = (
    "app.services.memory_v11",
    "app.services.memory_v11.contracts",
    "app.services.memory_v11.repository",
)


@pytest.fixture
async def isolated_store(tmp_path, monkeypatch):
    monkeypatch.delenv("AMADEUS_DB_PATH", raising=False)
    monkeypatch.setenv("AMADEUS_DATA_DIR", str(tmp_path))
    reset_initialization_cache()
    await init_db()
    return tmp_path


def _open_memory(worldline: str = "steins_gate") -> sqlite3.Connection:
    path = db_module._resolve_path(worldline, "memory")
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    return connection


def _table_names(connection: sqlite3.Connection) -> set[str]:
    return {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }


def _assert_no_v11_tables(connection: sqlite3.Connection, *, where: str) -> None:
    names = _table_names(connection)
    present = [t for t in V11_MEMORY_TABLES if t in names]
    assert not present, f"{where}: v11 tables must not exist yet: {present}"


def _unique_index_column_sets(connection: sqlite3.Connection, table: str) -> list[list[str]]:
    indexes = connection.execute(f"PRAGMA index_list('{table}')").fetchall()
    result: list[list[str]] = []
    for idx in indexes:
        name = idx[1]
        is_unique = int(idx[2]) == 1
        if not is_unique:
            continue
        cols = [
            row[2]
            for row in connection.execute(f"PRAGMA index_info('{name}')").fetchall()
        ]
        result.append(cols)
    return result


def _table_sql(connection: sqlite3.Connection, table: str) -> str:
    row = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    assert row is not None, f"table missing: {table}"
    return row[0] or ""


def _foreign_keys(connection: sqlite3.Connection, table: str) -> list[dict[str, object]]:
    rows = connection.execute(f"PRAGMA foreign_key_list('{table}')").fetchall()
    return [
        {"table": row[2], "from": row[3], "to": row[4]}
        for row in rows
    ]


def _seed_v10_memory_db(path: Path, *, session_id: str = "seed-v10") -> set[str]:
    """Create frozen v10 memory DB. Returns the table-name set after seed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(path))
    try:
        connection.executescript(FROZEN_V10_MEMORY_DDL)
        connection.execute(
            "INSERT OR REPLACE INTO schema_meta(key,value) VALUES('schema_version','10')"
        )
        connection.execute(
            "INSERT OR REPLACE INTO schema_meta(key,value) VALUES('embedding_model',?)",
            (db_module.EMBEDDING_MODEL,),
        )
        connection.execute(
            "INSERT OR REPLACE INTO schema_meta(key,value) VALUES('embedding_dimension',?)",
            (str(db_module.EMBEDDING_DIMENSION),),
        )
        connection.execute(
            """INSERT INTO core_facts(
                   session_id,identity_mode,fact_key,fact_value,confidence,importance,
                   is_current,is_pinned,is_dismissed,source_message_ids,created_at
               ) VALUES(?,?,?,?,?,?,1,0,0,?,?)""",
            (
                session_id,
                "self",
                "likes_anime",
                "喜欢看动漫",
                0.9,
                0.85,
                "[4151]",
                "2026-08-01T00:00:00+00:00",
            ),
        )
        connection.commit()
        _assert_no_v11_tables(connection, where=f"after frozen v10 seed ({path})")
        return _table_names(connection)
    finally:
        connection.close()


# ---------------------------------------------------------------------------
# Package / version surface
# ---------------------------------------------------------------------------


def test_s0_schema_version_constant_is_11():
    assert db_module.SCHEMA_VERSION == 11, (
        f"SCHEMA_VERSION is {db_module.SCHEMA_VERSION}; v11 not landed"
    )


def test_s0_memory_v11_fixed_module_catalog():
    missing = []
    for name in MEMORY_V11_MODULES:
        try:
            importlib.import_module(name)
        except ModuleNotFoundError:
            missing.append(name)
    assert not missing, f"fixed memory_v11 modules missing: {missing}"


# ---------------------------------------------------------------------------
# DDL presence + unique five-column cursor
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_s0_v11_tables_on_both_worldlines(isolated_store):
    assert db_module.SCHEMA_VERSION == 11
    for worldline in ("steins_gate", "beta"):
        connection = _open_memory(worldline)
        try:
            names = _table_names(connection)
        finally:
            connection.close()
        missing = [t for t in V11_MEMORY_TABLES if t not in names]
        assert not missing, f"{worldline} missing: {missing}"


@pytest.mark.asyncio
async def test_s0_ingest_jobs_unique_five_columns_via_pragma(isolated_store):
    assert db_module.SCHEMA_VERSION == 11
    connection = _open_memory("steins_gate")
    try:
        unique_sets = _unique_index_column_sets(connection, "memory_ingest_jobs")
    finally:
        connection.close()
    expected = list(INGEST_UNIQUE_COLUMNS)
    assert any(cols == expected for cols in unique_sets), (
        f"no unique index covering exactly {expected}; found {unique_sets}"
    )


# ---------------------------------------------------------------------------
# FK / CHECK / active-version (real rows — empty validate is insufficient)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_s0_stable_fact_versions_fk_and_check_ddl(isolated_store):
    """DDL-level FK/CHECK for stable facts and versions."""
    assert db_module.SCHEMA_VERSION == 11
    connection = _open_memory("steins_gate")
    try:
        fks = _foreign_keys(connection, "stable_fact_versions")
        assert any(
            fk["table"] == "stable_facts" and fk["from"] == "fact_id"
            for fk in fks
        ), f"stable_fact_versions missing FK to stable_facts.fact_id: {fks}"

        evidence_fks = _foreign_keys(connection, "stable_fact_evidence")
        assert any(fk["from"] == "fact_id" for fk in evidence_fks), evidence_fks
        assert any(fk["from"] == "observation_id" for fk in evidence_fks), evidence_fks

        ddl_facts = _table_sql(connection, "stable_facts").upper()
        assert "IDENTITY_MODE" in ddl_facts and "CHECK" in ddl_facts
        assert "SELF" in ddl_facts and "OKABE" in ddl_facts
        assert "ACTIVE" in ddl_facts and "DELETED" in ddl_facts

        ddl_versions = _table_sql(connection, "stable_fact_versions").upper()
        assert "CHANGE_KIND" in ddl_versions and "CHECK" in ddl_versions
        for kind in ("CREATE", "REFINE", "SUPERSEDE", "USER_EDIT", "LEGACY_IMPORT"):
            assert kind in ddl_versions, f"missing change_kind value {kind}"
    finally:
        connection.close()


@pytest.mark.asyncio
async def test_s0_active_version_rejects_mismatch_and_dual_open(isolated_store):
    """Create real fact/version rows; wrong active_version / dual open / mismatch must fail.

    Empty-DB validate() alone does not prove the invariant.
    """
    assert db_module.SCHEMA_VERSION == 11
    contracts = importlib.import_module("app.services.memory_v11.contracts")
    repo = importlib.import_module("app.services.memory_v11.repository")
    error_type = getattr(contracts, "MemoryValidationError", None) or getattr(
        contracts, "MemoryConstraintError", None
    )
    assert error_type is not None, "contracts.MemoryValidationError (or ConstraintError) missing"

    create_fn = getattr(repo, "create_stable_fact", None) or getattr(
        repo, "insert_stable_fact_with_version", None
    )
    assert callable(create_fn), "repository create_stable_fact helper missing"

    created = await create_fn(
        session_id="v11-active-ver",
        worldline="steins_gate",
        identity_mode="self",
        display_text="喜欢喝茶",
        semantic_json={"subject": "user", "predicate": "likes", "object": {"name": "tea"}},
        confidence=0.9,
        change_kind="create",
    )
    fact_id = created["fact_id"] if isinstance(created, dict) else created.fact_id
    version_no = created["version_no"] if isinstance(created, dict) else created.version_no
    assert isinstance(version_no, int) and version_no >= 1

    # Wrong active_version pointer must be rejected.
    set_active = getattr(repo, "set_active_version", None) or getattr(
        repo, "update_active_version", None
    )
    assert callable(set_active), "repository set_active_version missing"
    with pytest.raises(error_type) as wrong_exc:
        await set_active(
            session_id="v11-active-ver",
            worldline="steins_gate",
            identity_mode="self",
            fact_id=fact_id,
            active_version=version_no + 99,
        )
    code = getattr(wrong_exc.value, "code", None) or getattr(
        wrong_exc.value, "error_code", None
    )
    assert code in {
        "active_version_mismatch",
        "version_not_found",
        "invalid_active_version",
    }, f"unexpected code for wrong active_version: {code!r}"

    # Dual open: production must NOT expose insert_open_version_force; enforce via DDL.
    assert not hasattr(repo, "insert_open_version_force")
    assert not hasattr(repo, "create_version_without_closing_previous")
    connection = _open_memory("steins_gate")
    try:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """INSERT INTO stable_fact_versions(
                       fact_id, version_no, display_text, semantic_json,
                       semantic_fingerprint, confidence, change_kind,
                       previous_version, valid_from, invalid_at
                   ) VALUES(?,?,?,?,?,?,?,?,?,NULL)""",
                (
                    fact_id,
                    version_no + 1,
                    "dual open",
                    "{}",
                    "fp-dual",
                    0.9,
                    "refine",
                    version_no,
                    "2026-08-02T00:00:00+00:00",
                ),
            )
            connection.commit()
    finally:
        connection.close()

    validate = getattr(repo, "validate_active_version_invariants", None)
    assert callable(validate), "repository.validate_active_version_invariants missing"
    await validate(
        worldline="steins_gate",
        session_id="v11-active-ver",
        identity_mode="self",
    )


def _insert_topic(
    connection: sqlite3.Connection,
    *,
    topic_id: str,
    session_id: str,
    identity_mode: str,
    label: str = "动漫",
) -> None:
    connection.execute(
        """INSERT INTO memory_topics(
               topic_id, session_id, identity_mode, normalized_label,
               display_label, created_at, updated_at
           ) VALUES(?,?,?,?,?,?,?)""",
        (
            topic_id,
            session_id,
            identity_mode,
            label.casefold(),
            label,
            "2026-08-02T00:00:00+00:00",
            "2026-08-02T00:00:00+00:00",
        ),
    )
    connection.commit()


@pytest.mark.asyncio
async def test_s1_topic_same_scope_attach_succeeds(isolated_store):
    """Same (session_id, identity_mode) topic may attach to a new stable fact."""
    assert db_module.SCHEMA_VERSION == 11
    repo = importlib.import_module("app.services.memory_v11.repository")
    connection = _open_memory("steins_gate")
    try:
        _insert_topic(
            connection,
            topic_id="topic-self-anime",
            session_id="scope-a",
            identity_mode="self",
        )
    finally:
        connection.close()

    created = await repo.create_stable_fact(
        session_id="scope-a",
        worldline="steins_gate",
        identity_mode="self",
        display_text="喜欢看动漫",
        semantic_json={"subject": "user", "predicate": "likes", "object": {"name": "anime"}},
        topic_id="topic-self-anime",
    )
    assert created["fact_id"]
    assert created["topic_id"] == "topic-self-anime"
    assert created["version_no"] == 1


@pytest.mark.asyncio
async def test_s1_topic_cross_session_rejected_repo_and_sql_no_residue(isolated_store):
    """Cross-session topic attach fails at repository and direct SQL; zero fact/version rows."""
    assert db_module.SCHEMA_VERSION == 11
    contracts = importlib.import_module("app.services.memory_v11.contracts")
    repo = importlib.import_module("app.services.memory_v11.repository")
    connection = _open_memory("steins_gate")
    try:
        _insert_topic(
            connection,
            topic_id="topic-owner-a",
            session_id="owner-a",
            identity_mode="self",
        )
        facts_before = connection.execute("SELECT COUNT(*) FROM stable_facts").fetchone()[0]
        versions_before = connection.execute(
            "SELECT COUNT(*) FROM stable_fact_versions"
        ).fetchone()[0]
    finally:
        connection.close()

    with pytest.raises(contracts.MemoryValidationError) as exc_info:
        await repo.create_stable_fact(
            session_id="owner-b",
            worldline="steins_gate",
            identity_mode="self",
            display_text="跨用户应失败",
            semantic_json={"subject": "user", "predicate": "likes", "object": {"name": "x"}},
            topic_id="topic-owner-a",
        )
    assert exc_info.value.code == "topic_scope_mismatch"

    connection = _open_memory("steins_gate")
    try:
        # Direct SQL must also fail via composite FK.
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """INSERT INTO stable_facts(
                       fact_id, session_id, identity_mode, topic_id, state,
                       active_version, is_pinned, created_at, updated_at
                   ) VALUES(?,?,?,?, 'active', 1, 0, ?, ?)""",
                (
                    "fact-cross-session",
                    "owner-b",
                    "self",
                    "topic-owner-a",
                    "2026-08-02T00:00:00+00:00",
                    "2026-08-02T00:00:00+00:00",
                ),
            )
            connection.commit()
        connection.rollback()
        facts_after = connection.execute("SELECT COUNT(*) FROM stable_facts").fetchone()[0]
        versions_after = connection.execute(
            "SELECT COUNT(*) FROM stable_fact_versions"
        ).fetchone()[0]
        assert facts_after == facts_before
        assert versions_after == versions_before
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM stable_facts WHERE fact_id='fact-cross-session'"
            ).fetchone()[0]
            == 0
        )
    finally:
        connection.close()


@pytest.mark.asyncio
async def test_s1_topic_cross_identity_rejected_repo_and_sql_no_residue(isolated_store):
    """Cross-identity (self vs okabe) topic attach fails; no residual fact/version."""
    assert db_module.SCHEMA_VERSION == 11
    contracts = importlib.import_module("app.services.memory_v11.contracts")
    repo = importlib.import_module("app.services.memory_v11.repository")
    connection = _open_memory("steins_gate")
    try:
        _insert_topic(
            connection,
            topic_id="topic-okabe-only",
            session_id="same-user",
            identity_mode="okabe",
            label="角色",
        )
        facts_before = connection.execute("SELECT COUNT(*) FROM stable_facts").fetchone()[0]
        versions_before = connection.execute(
            "SELECT COUNT(*) FROM stable_fact_versions"
        ).fetchone()[0]
    finally:
        connection.close()

    with pytest.raises(contracts.MemoryValidationError) as exc_info:
        await repo.create_stable_fact(
            session_id="same-user",
            worldline="steins_gate",
            identity_mode="self",
            display_text="跨身份应失败",
            semantic_json={"subject": "user", "predicate": "likes", "object": {"name": "y"}},
            topic_id="topic-okabe-only",
        )
    assert exc_info.value.code == "topic_scope_mismatch"

    connection = _open_memory("steins_gate")
    try:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """INSERT INTO stable_facts(
                       fact_id, session_id, identity_mode, topic_id, state,
                       active_version, is_pinned, created_at, updated_at
                   ) VALUES(?,?,?,?, 'active', 1, 0, ?, ?)""",
                (
                    "fact-cross-identity",
                    "same-user",
                    "self",
                    "topic-okabe-only",
                    "2026-08-02T00:00:00+00:00",
                    "2026-08-02T00:00:00+00:00",
                ),
            )
            connection.commit()
        connection.rollback()
        facts_after = connection.execute("SELECT COUNT(*) FROM stable_facts").fetchone()[0]
        versions_after = connection.execute(
            "SELECT COUNT(*) FROM stable_fact_versions"
        ).fetchone()[0]
        assert facts_after == facts_before
        assert versions_after == versions_before
    finally:
        connection.close()


@pytest.mark.asyncio
async def test_s1_empty_session_id_rejected_zero_write(isolated_store):
    """Empty session_id fails closed with stable code and no fact/version rows."""
    assert db_module.SCHEMA_VERSION == 11
    contracts = importlib.import_module("app.services.memory_v11.contracts")
    repo = importlib.import_module("app.services.memory_v11.repository")
    connection = _open_memory("steins_gate")
    try:
        facts_before = connection.execute("SELECT COUNT(*) FROM stable_facts").fetchone()[0]
        versions_before = connection.execute(
            "SELECT COUNT(*) FROM stable_fact_versions"
        ).fetchone()[0]
    finally:
        connection.close()

    with pytest.raises(contracts.MemoryValidationError) as exc_info:
        await repo.create_stable_fact(
            session_id="   ",
            worldline="steins_gate",
            identity_mode="self",
            display_text="空 session 应失败",
            semantic_json={"subject": "user", "predicate": "likes", "object": {"name": "z"}},
        )
    assert exc_info.value.code == "empty_session_id"

    connection = _open_memory("steins_gate")
    try:
        assert connection.execute("SELECT COUNT(*) FROM stable_facts").fetchone()[0] == facts_before
        assert (
            connection.execute("SELECT COUNT(*) FROM stable_fact_versions").fetchone()[0]
            == versions_before
        )
    finally:
        connection.close()


@pytest.mark.asyncio
async def test_s1_topic_alias_composite_fk_rejects_cross_scope(isolated_store):
    """memory_topic_aliases composite FK blocks alias rows for another session/identity."""
    assert db_module.SCHEMA_VERSION == 11
    connection = _open_memory("steins_gate")
    try:
        _insert_topic(
            connection,
            topic_id="topic-alias-src",
            session_id="alias-owner",
            identity_mode="self",
        )
        # Same-scope alias OK.
        connection.execute(
            """INSERT INTO memory_topic_aliases(
                   topic_id, session_id, identity_mode, normalized_alias
               ) VALUES(?,?,?,?)""",
            ("topic-alias-src", "alias-owner", "self", "动画"),
        )
        connection.commit()
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """INSERT INTO memory_topic_aliases(
                       topic_id, session_id, identity_mode, normalized_alias
                   ) VALUES(?,?,?,?)""",
                ("topic-alias-src", "other-owner", "self", "动画作品"),
            )
            connection.commit()
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """INSERT INTO memory_topic_aliases(
                       topic_id, session_id, identity_mode, normalized_alias
                   ) VALUES(?,?,?,?)""",
                ("topic-alias-src", "alias-owner", "okabe", "角色动画"),
            )
            connection.commit()
    finally:
        connection.close()


@pytest.mark.asyncio
async def test_s0_observation_sources_fk_and_identity_check(isolated_store):
    assert db_module.SCHEMA_VERSION == 11
    connection = _open_memory("steins_gate")
    try:
        fks = _foreign_keys(connection, "memory_observation_sources")
        assert any(
            fk["table"] == "memory_observations" and fk["from"] == "observation_id"
            for fk in fks
        ), fks
        jobs_ddl = _table_sql(connection, "memory_ingest_jobs").upper()
        assert "CHECK" in jobs_ddl
        for state in ("PENDING", "PROCESSING", "COMPLETED", "FAILED", "CANCELLED"):
            assert state in jobs_ddl, f"job state {state} not constrained"
        for mode in ("SELF", "OKABE"):
            assert mode in jobs_ddl
    finally:
        connection.close()


@pytest.mark.asyncio
async def test_s0_tombstones_forbid_content_columns(isolated_store):
    assert db_module.SCHEMA_VERSION == 11
    connection = _open_memory("steins_gate")
    try:
        cols = {
            row[1]
            for row in connection.execute("PRAGMA table_info(memory_tombstones)")
        }
    finally:
        connection.close()
    forbidden = {"display_text", "fact_value", "excerpt", "embedding", "semantic_json"}
    assert not (forbidden & cols), f"tombstone content leak: {forbidden & cols}"


# ---------------------------------------------------------------------------
# Real v10 → v11 migration, rollback, idempotence, dual worldline
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_s0_v10_to_v11_migration_preserves_legacy_core_and_adds_tables(
    tmp_path, monkeypatch
):
    monkeypatch.delenv("AMADEUS_DB_PATH", raising=False)
    data_root = tmp_path / "v10_to_v11"
    monkeypatch.setenv("AMADEUS_DATA_DIR", str(data_root))
    reset_initialization_cache()

    for worldline in ("steins_gate", "beta"):
        target = Path(db_module._resolve_path(worldline, "memory"))
        _seed_v10_memory_db(target, session_id=f"seed-{worldline}")
        # Pre-migration: every v11 table absent.
        conn = sqlite3.connect(str(target))
        try:
            _assert_no_v11_tables(conn, where=f"pre-migrate {worldline}")
        finally:
            conn.close()

    assert db_module.SCHEMA_VERSION == 11, "S1 must set SCHEMA_VERSION=11 before migration runs"
    await init_db()

    for worldline in ("steins_gate", "beta"):
        connection = _open_memory(worldline)
        try:
            version = connection.execute(
                "SELECT value FROM schema_meta WHERE key='schema_version'"
            ).fetchone()[0]
            assert version == "11", f"{worldline} still at schema {version}"
            legacy = connection.execute(
                "SELECT COUNT(*) FROM core_facts WHERE session_id=?",
                (f"seed-{worldline}",),
            ).fetchone()[0]
            assert legacy == 1, f"{worldline} lost legacy core_facts"
            missing = [t for t in V11_MEMORY_TABLES if t not in _table_names(connection)]
            assert not missing, f"{worldline} missing after migrate: {missing}"
        finally:
            connection.close()


@pytest.mark.asyncio
async def test_s0_migration_injected_failure_leaves_no_v11_debris(
    tmp_path, monkeypatch
):
    """Injected failure: schema stays 10, table set identical, zero v11 tables."""
    monkeypatch.delenv("AMADEUS_DB_PATH", raising=False)
    data_root = tmp_path / "v11_fail"
    monkeypatch.setenv("AMADEUS_DATA_DIR", str(data_root))
    reset_initialization_cache()

    target = Path(db_module._resolve_path("steins_gate", "memory"))
    tables_before = _seed_v10_memory_db(target)

    assert db_module.SCHEMA_VERSION == 11

    migrate_fn_names = ("_migrate_v10_to_v11", "migrate_memory_v10_to_v11")
    original = None
    owner = None
    for mod_name in ("app.db", "app.services.memory_v11.migration"):
        try:
            mod = importlib.import_module(mod_name)
        except ModuleNotFoundError:
            continue
        for fn_name in migrate_fn_names:
            if hasattr(mod, fn_name):
                owner, original = mod, getattr(mod, fn_name)
                break
        if original is not None:
            break

    assert original is not None, "v10→v11 migrate function missing (S1)"

    def _boom(*args, **kwargs):
        raise RuntimeError("injected migration failure")

    monkeypatch.setattr(owner, original.__name__, _boom)
    reset_initialization_cache()
    with pytest.raises(RuntimeError, match="injected migration failure"):
        await init_db()

    connection = sqlite3.connect(str(target))
    try:
        version = connection.execute(
            "SELECT value FROM schema_meta WHERE key='schema_version'"
        ).fetchone()
        assert version is not None
        assert version[0] == "10", f"failed migration left schema_version={version[0]}"
        tables_after = _table_names(connection)
        assert tables_after == tables_before, (
            f"table set changed after failed migration:\n"
            f"  added={tables_after - tables_before}\n"
            f"  removed={tables_before - tables_after}"
        )
        _assert_no_v11_tables(connection, where="after injected migration failure")
    finally:
        connection.close()


@pytest.mark.asyncio
async def test_s0_migration_rerun_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.delenv("AMADEUS_DB_PATH", raising=False)
    data_root = tmp_path / "v11_idem"
    monkeypatch.setenv("AMADEUS_DATA_DIR", str(data_root))
    reset_initialization_cache()

    target = Path(db_module._resolve_path("steins_gate", "memory"))
    _seed_v10_memory_db(target, session_id="idem-seed")
    assert db_module.SCHEMA_VERSION == 11

    await init_db()
    connection = _open_memory("steins_gate")
    try:
        tables_first = _table_names(connection)
        core_first = connection.execute(
            "SELECT COUNT(*) FROM core_facts WHERE session_id='idem-seed'"
        ).fetchone()[0]
    finally:
        connection.close()

    reset_initialization_cache()
    await init_db()
    connection = _open_memory("steins_gate")
    try:
        tables_second = _table_names(connection)
        core_second = connection.execute(
            "SELECT COUNT(*) FROM core_facts WHERE session_id='idem-seed'"
        ).fetchone()[0]
        version = connection.execute(
            "SELECT value FROM schema_meta WHERE key='schema_version'"
        ).fetchone()[0]
    finally:
        connection.close()

    assert version == "11"
    assert tables_first == tables_second
    assert core_first == core_second == 1


@pytest.mark.asyncio
async def test_s0_dual_worldline_sequential_migration(tmp_path, monkeypatch):
    monkeypatch.delenv("AMADEUS_DB_PATH", raising=False)
    data_root = tmp_path / "v11_dual"
    monkeypatch.setenv("AMADEUS_DATA_DIR", str(data_root))
    reset_initialization_cache()

    for worldline in ("steins_gate", "beta"):
        path = Path(db_module._resolve_path(worldline, "memory"))
        _seed_v10_memory_db(path, session_id=f"dual-{worldline}")
        conn = sqlite3.connect(str(path))
        try:
            _assert_no_v11_tables(conn, where=f"pre-dual {worldline}")
        finally:
            conn.close()

    assert db_module.SCHEMA_VERSION == 11
    await init_db()

    for worldline in ("steins_gate", "beta"):
        connection = _open_memory(worldline)
        try:
            version = connection.execute(
                "SELECT value FROM schema_meta WHERE key='schema_version'"
            ).fetchone()[0]
            assert version == "11"
            count = connection.execute(
                "SELECT COUNT(*) FROM core_facts WHERE session_id=?",
                (f"dual-{worldline}",),
            ).fetchone()[0]
            assert count == 1
        finally:
            connection.close()
