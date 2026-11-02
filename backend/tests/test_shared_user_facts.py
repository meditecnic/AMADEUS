"""Q18-SHARED-FACTS-FOUNDATION-01: shared_user_facts schema and migration.

The shared-fact writer and reader are gone; the control-store table, its
migration and its DB-level CHECK constraints remain.
"""
from __future__ import annotations

import sqlite3

import pytest

from app import db as db_module
from app.db import (
    init_db,
    reset_initialization_cache,
    validate_schema,
)


@pytest.fixture
async def isolated_store(tmp_path, monkeypatch):
    monkeypatch.delenv("AMADEUS_DB_PATH", raising=False)
    monkeypatch.setenv("AMADEUS_DATA_DIR", str(tmp_path))
    reset_initialization_cache()
    await init_db()
    return tmp_path


def _control_connection() -> sqlite3.Connection:
    connection = sqlite3.connect(db_module._resolve_path("steins_gate", "control"))
    connection.row_factory = sqlite3.Row
    return connection


def _shared_rows() -> list[sqlite3.Row]:
    connection = _control_connection()
    try:
        return connection.execute(
            "SELECT * FROM shared_user_facts ORDER BY id"
        ).fetchall()
    finally:
        connection.close()


# ---------------------------------------------------------------------------
# A. Schema / migration
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fresh_control_store_has_shared_user_facts_table(isolated_store):
    connection = _control_connection()
    try:
        table = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='shared_user_facts'"
        ).fetchone()
        version = connection.execute(
            "SELECT value FROM schema_meta WHERE key='schema_version'"
        ).fetchone()[0]
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(shared_user_facts)")
        }
    finally:
        connection.close()
    assert table is not None
    assert version == str(db_module.SCHEMA_VERSION)
    assert {
        "id", "owner_session_id", "fact_key", "fact_value", "confidence",
        "importance", "is_current", "is_pinned", "origin_worldline",
        "origin_conversation_id", "origin_identity_mode", "source_message_ids",
        "created_at", "superseded_by",
    } <= columns


@pytest.mark.asyncio
async def test_validate_schema_reports_control_once_outside_worldline_loop(isolated_store):
    result = await validate_schema()
    assert result["ok"] is True
    databases = result["databases"]
    assert databases["control"] is True
    # control is a single store: exactly one key, never worldline-prefixed.
    assert set(databases.keys()) == {
        "steins_gate:history", "steins_gate:memory",
        "beta:history", "beta:memory", "control",
    }


@pytest.mark.asyncio
async def test_validate_schema_flags_incomplete_control_schema(isolated_store):
    """A control store missing a required index must fail validation."""
    connection = _control_connection()
    try:
        connection.execute("DROP INDEX idx_shared_fact_hot")
        connection.commit()
    finally:
        connection.close()
    result = await validate_schema()
    assert result["databases"]["control"] is False
    assert result["ok"] is False


def test_migration_v7_to_v8_is_atomic_on_failure(tmp_path):
    """Table, indexes and version bump share one transaction: all or nothing."""
    control_path = tmp_path / "control_v7.sqlite3"
    connection = sqlite3.connect(control_path)
    try:
        connection.executescript(
            """
            CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            INSERT INTO schema_meta(key,value) VALUES('schema_version','7');
            -- Same-name object forces the second index DDL to fail mid-migration.
            CREATE TABLE idx_shared_fact_hot (x INTEGER);
            """
        )
        connection.commit()

        with pytest.raises(sqlite3.OperationalError):
            db_module._migrate_v7_to_v8(connection, "control")

        has_table = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='shared_user_facts'"
        ).fetchone()
        version = connection.execute(
            "SELECT value FROM schema_meta WHERE key='schema_version'"
        ).fetchone()[0]
    finally:
        connection.close()
    assert has_table is None, "failed migration must roll the new table back"
    assert version == "7", "failed migration must not advance schema_version"


@pytest.mark.asyncio
async def test_legacy_v7_stores_migrate_to_v8_preserving_data(tmp_path, monkeypatch):
    monkeypatch.delenv("AMADEUS_DB_PATH", raising=False)
    monkeypatch.setenv("AMADEUS_DATA_DIR", str(tmp_path))

    # Legacy v7 control: real v7 tables (no shared_user_facts) + live data.
    control_path = tmp_path / "control.sqlite3"
    connection = sqlite3.connect(control_path)
    try:
        connection.executescript(
            """
            CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            INSERT INTO schema_meta(key,value) VALUES('schema_version','7');
            CREATE TABLE session_worldlines (
                session_id TEXT PRIMARY KEY,
                worldline TEXT NOT NULL CHECK(worldline IN ('steins_gate','beta')),
                revision INTEGER NOT NULL DEFAULT 0,
                updated_at_utc TEXT NOT NULL
            );
            INSERT INTO session_worldlines(session_id,worldline,revision,updated_at_utc)
            VALUES ('legacy-user','beta',3,'2026-07-20T00:00:00+00:00');
            """
        )
        connection.commit()
    finally:
        connection.close()

    # Legacy v7 history/memory: v7 and v8 share the same DDL; only meta advances.
    history_path = tmp_path / "worldlines" / "sg" / "history.sqlite3"
    history_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(history_path)
    try:
        connection.executescript(db_module.HISTORY_SCHEMA)
        connection.execute(
            "INSERT OR REPLACE INTO schema_meta(key,value) VALUES('schema_version','7')"
        )
        connection.execute(
            "INSERT INTO conversations(id,session_id,title,is_default) VALUES('c-1','legacy-user','t',1)"
        )
        connection.execute(
            "INSERT INTO messages(session_id,conversation_id,role,content) VALUES('legacy-user','c-1','user','古いメッセージ')"
        )
        connection.commit()
    finally:
        connection.close()

    memory_path = tmp_path / "worldlines" / "sg" / "memory.sqlite3"
    connection = sqlite3.connect(memory_path)
    try:
        connection.executescript(db_module.MEMORY_SCHEMA)
        connection.execute(
            "INSERT OR REPLACE INTO schema_meta(key,value) VALUES('schema_version','7')"
        )
        connection.execute(
            """INSERT INTO core_facts(
                   session_id,identity_mode,fact_key,fact_value,confidence,importance,
                   is_current,is_pinned,source_message_ids,created_at
               ) VALUES('legacy-user','self','hobby','読書',0.9,0.6,1,0,'[1]','2026-07-20T00:00:00+00:00')"""
        )
        connection.commit()
    finally:
        connection.close()

    reset_initialization_cache()
    await init_db()

    for path in (control_path, history_path, memory_path):
        connection = sqlite3.connect(path)
        try:
            version = connection.execute(
                "SELECT value FROM schema_meta WHERE key='schema_version'"
            ).fetchone()[0]
            has_shared = connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='shared_user_facts'"
            ).fetchone()
        finally:
            connection.close()
        assert version == str(db_module.SCHEMA_VERSION), path
        # Only control gains the table; worldline stores complete a meta-only bump.
        assert (has_shared is not None) == (path == control_path), path

    connection = sqlite3.connect(control_path)
    try:
        row = connection.execute(
            "SELECT worldline,revision FROM session_worldlines WHERE session_id='legacy-user'"
        ).fetchone()
    finally:
        connection.close()
    assert row == ("beta", 3)

    connection = sqlite3.connect(history_path)
    try:
        content = connection.execute("SELECT content FROM messages").fetchone()[0]
    finally:
        connection.close()
    assert content == "古いメッセージ"

    connection = sqlite3.connect(memory_path)
    try:
        fact = connection.execute("SELECT fact_value FROM core_facts").fetchone()[0]
    finally:
        connection.close()
    assert fact == "読書"


def test_amadeus_db_path_compat_still_resolves_control_via_existing_rule(tmp_path, monkeypatch):
    """Regression: the legacy AMADEUS_DB_PATH override rule stays untouched."""
    base = tmp_path / "compat.sqlite3"
    monkeypatch.setenv("AMADEUS_DB_PATH", str(base))
    resolved = db_module._resolve_path("steins_gate", "control")
    assert resolved == str(base.with_name("compat.sg.control.sqlite3"))


@pytest.mark.asyncio
async def test_direct_sql_insert_violating_checks_is_rejected_by_sqlite(isolated_store):
    """DB-level defense in depth: CHECK constraints hold even past the service."""
    base_row = (
        "raw-owner", "k", "v", 0.9, 0.7, 1, 0,
        "steins_gate", "c-1", "self", "[1]", "2026-07-26T00:00:00+00:00",
    )
    insert_sql = """INSERT INTO shared_user_facts(
            owner_session_id,fact_key,fact_value,confidence,importance,
            is_current,is_pinned,origin_worldline,origin_conversation_id,
            origin_identity_mode,source_message_ids,created_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)"""
    connection = _control_connection()
    try:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                insert_sql,
                base_row[:9] + ("okabe",) + base_row[10:],
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                insert_sql,
                base_row[:7] + ("gamma",) + base_row[8:],
            )
    finally:
        connection.close()
    assert _shared_rows() == []
