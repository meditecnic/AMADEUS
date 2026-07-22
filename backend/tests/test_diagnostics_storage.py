"""Real-SQLite tests for the isolated diagnostic side channel."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3

import pytest

from app.services.diagnostics import (
    DIAGNOSTIC_EXPORT_EXCLUSIONS,
    DIAGNOSTIC_STORAGE_VERSION,
    DiagnosticStore,
)


def _event(seq: int, *, turn_id: str = "turn-1", observed_at: str | None = None):
    return {
        "schema_version": 1,
        "event_id": f"evt-{seq}",
        "turn_id": turn_id,
        "segment_id": "segment-1",
        "stage": "tts",
        "observed_at": observed_at or datetime.now(timezone.utc).isoformat(),
        "source": "server",
        "status": "started",
        "error_summary": None,
        "reason_code": None,
        "attempt_no": 1,
        "server_seq": seq,
    }


def _store(tmp_path: Path, *, now: datetime | None = None) -> DiagnosticStore:
    root = tmp_path / "app-data"
    control = tmp_path / "control.sqlite3"
    return DiagnosticStore(root, control_path=control, clock=lambda: now or datetime.now(timezone.utc))


def test_store_is_isolated_under_app_data_and_uses_wal(tmp_path):
    store = _store(tmp_path)
    store.initialize()
    assert store.path == tmp_path / "app-data" / "diagnostics.sqlite3"
    assert store.path != store.control_path
    assert store.path.is_file()
    assert store.journal_mode() == "wal"


def test_v1_database_is_incrementally_migrated_without_rewriting_history(tmp_path):
    store = _store(tmp_path)
    store.data_root.mkdir(parents=True)
    with sqlite3.connect(store.path) as connection:
        connection.executescript(
            """
            PRAGMA user_version=1;
            CREATE TABLE diagnostic_events (
                row_id INTEGER PRIMARY KEY AUTOINCREMENT,
                schema_version INTEGER NOT NULL,
                event_id TEXT NOT NULL UNIQUE,
                turn_id TEXT NOT NULL,
                segment_id TEXT,
                stage TEXT,
                observed_at TEXT NOT NULL,
                source TEXT NOT NULL,
                status TEXT NOT NULL,
                error_summary TEXT,
                reason_code TEXT,
                attempt_no INTEGER NOT NULL,
                server_seq INTEGER
            );
            INSERT INTO diagnostic_events(
                schema_version,event_id,turn_id,segment_id,stage,observed_at,
                source,status,error_summary,reason_code,attempt_no,server_seq
            ) VALUES (
                1,'legacy','legacy-turn','segment-1','tts',
                '2026-07-21T00:00:00+00:00','server','failed',
                'synthesis_failed',NULL,1,1
            );
            """
        )

    store.initialize()

    with sqlite3.connect(store.path) as connection:
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(diagnostic_events)")
        }
        legacy = connection.execute(
            "SELECT schema_version,event_id,source_hash,source_length,language,audio_bytes "
            "FROM diagnostic_events WHERE event_id='legacy'"
        ).fetchone()
    assert version == DIAGNOSTIC_STORAGE_VERSION == 2
    assert {"source_hash", "source_length", "language", "audio_bytes"} <= columns
    assert legacy == (1, "legacy", None, None, None, None)


def test_v1_migration_rolls_back_all_columns_when_an_alter_fails(tmp_path):
    path = tmp_path / "diagnostics.sqlite3"
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        PRAGMA user_version=1;
        CREATE TABLE diagnostic_events (
            row_id INTEGER PRIMARY KEY AUTOINCREMENT,
            schema_version INTEGER NOT NULL,
            event_id TEXT NOT NULL UNIQUE,
            turn_id TEXT NOT NULL,
            segment_id TEXT,
            stage TEXT,
            observed_at TEXT NOT NULL,
            source TEXT NOT NULL,
            status TEXT NOT NULL,
            error_summary TEXT,
            reason_code TEXT,
            attempt_no INTEGER NOT NULL,
            server_seq INTEGER
        );
        """
    )
    alter_count = 0

    def deny_second_alter(action, _arg1, _arg2, _database, _trigger):
        nonlocal alter_count
        if action == sqlite3.SQLITE_ALTER_TABLE:
            alter_count += 1
            if alter_count == 2:
                return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    connection.set_authorizer(deny_second_alter)
    with pytest.raises(sqlite3.DatabaseError):
        DiagnosticStore._migrate_storage(connection)
    connection.set_authorizer(None)
    columns = {row[1] for row in connection.execute("PRAGMA table_info(diagnostic_events)")}
    version = connection.execute("PRAGMA user_version").fetchone()[0]
    connection.close()

    assert not {"source_hash", "source_length", "language", "audio_bytes"} & columns
    assert version == 1


def test_v2_events_round_trip_observation_fields(tmp_path):
    store = _store(tmp_path)
    store.initialize()
    event = _event(1)
    event.update(
        schema_version=2,
        status="succeeded",
        source_hash="0123456789abcdef",
        source_length=8,
        language="ja",
        audio_bytes=4096,
    )
    assert store.append_event(event) is True
    row = store.read_events("turn-1")[0]
    assert row["source_hash"] == "0123456789abcdef"
    assert row["source_length"] == 8
    assert row["language"] == "ja"
    assert row["audio_bytes"] == 4096


def test_startup_probe_is_a_real_transaction_and_restores_ready(tmp_path):
    store = _store(tmp_path)
    store.initialize()
    store.record_failure("write_failed")
    assert store.startup_probe() is True
    assert store.health()["status"] == "ready"
    assert store.health()["cumulative_failures"] == 1
    assert store.health()["consecutive_failures"] == 0
    assert store.read_events("__diagnostic_probe__") == []


def test_locked_startup_probe_enters_degraded_and_counts_failure(tmp_path):
    store = _store(tmp_path)
    store.initialize()
    lock = sqlite3.connect(store.path, timeout=0)
    lock.execute("BEGIN EXCLUSIVE")
    try:
        assert store.startup_probe() is False
    finally:
        lock.rollback()
        lock.close()
    state = store.health()
    assert state["status"] == "degraded"
    assert state["consecutive_failures"] == 1
    assert state["cumulative_failures"] == 1


def test_real_locked_database_is_best_effort_and_opens_circuit(tmp_path):
    store = _store(tmp_path)
    store.initialize()
    lock = sqlite3.connect(store.path, timeout=0)
    lock.execute("BEGIN EXCLUSIVE")
    try:
        assert store.append_event(_event(1)) is False
        assert store.append_event(_event(2)) is False
        assert store.append_event(_event(3)) is False
        assert store.health()["status"] == "degraded"
        assert store.health()["consecutive_failures"] == 3
        attempts = store.write_attempts
        assert store.append_event(_event(4)) is False
        assert store.write_attempts == attempts
    finally:
        lock.rollback()
        lock.close()


def test_success_resets_consecutive_failures_before_circuit_opens(tmp_path):
    store = _store(tmp_path)
    store.initialize()
    store.record_failure("write_failed")
    assert store.health()["consecutive_failures"] == 1
    assert store.append_event(_event(1)) is True
    assert store.health()["consecutive_failures"] == 0
    assert store.health()["cumulative_failures"] == 1


def test_sweep_uses_injected_clock_and_deletes_only_expired_rows(tmp_path):
    now = datetime(2026, 7, 21, tzinfo=timezone.utc)
    store = _store(tmp_path, now=now)
    store.initialize()
    store.append_event(_event(1, turn_id="old", observed_at=(now - timedelta(days=8)).isoformat()))
    store.append_event(_event(2, turn_id="new", observed_at=(now - timedelta(days=6)).isoformat()))
    assert store.sweep() == 1
    assert store.read_events("old") == []
    assert len(store.read_events("new")) == 1


def test_sweep_deactivates_expired_verbose_session_and_clears_text(tmp_path):
    clock = [datetime(2026, 7, 21, tzinfo=timezone.utc)]
    store = DiagnosticStore(
        tmp_path / "app-data",
        control_path=tmp_path / "control.sqlite3",
        clock=lambda: clock[0],
    )
    store.initialize()
    store.enable_verbose("session-1")
    assert store.capture_verbose("session-1", "temporary text") is True

    clock[0] += timedelta(hours=25)
    store.sweep()

    assert store.read_verbose("session-1") == []
    assert store.capture_verbose("session-1", "must not persist") is False


def test_storage_replaces_the_200th_regular_event_with_overflow_and_seals(tmp_path):
    store = _store(tmp_path)
    store.initialize()
    for seq in range(1, 200):
        assert store.append_event(_event(seq)) is True
    assert store.append_event(_event(200)) is False
    rows = store.read_events("turn-1")
    assert len(rows) == 200
    assert rows[-1]["status"] == "overflow"
    attempts = store.write_attempts
    assert store.append_event(_event(201)) is False
    assert store.write_attempts == attempts


def test_turn_limit_and_overflow_seal_survive_store_restart(tmp_path):
    first = _store(tmp_path)
    first.initialize()
    for seq in range(1, 200):
        assert first.append_event(_event(seq)) is True

    restarted = _store(tmp_path)
    restarted.initialize()
    assert restarted.append_event(_event(200)) is False
    rows = restarted.read_events("turn-1")
    assert len(rows) == 200
    assert rows[-1]["status"] == "overflow"

    sealed_restart = _store(tmp_path)
    sealed_restart.initialize()
    assert sealed_restart.append_event(_event(201)) is False
    assert len(sealed_restart.read_events("turn-1")) == 200


def test_server_events_read_in_monotonic_sequence_after_out_of_order_writes(tmp_path):
    store = _store(tmp_path)
    store.initialize()
    assert store.append_event(_event(2)) is True
    assert store.append_event(_event(1)) is True
    assert [row["server_seq"] for row in store.read_events("turn-1")] == [1, 2]


def test_concurrent_best_effort_writes_are_serialized_without_self_degrading(tmp_path):
    store = _store(tmp_path)
    store.initialize()
    with ThreadPoolExecutor(max_workers=12) as pool:
        results = list(pool.map(store.append_event, [_event(seq) for seq in range(1, 41)]))
    assert results == [True] * 40
    assert len(store.read_events("turn-1")) == 40
    assert store.health()["consecutive_failures"] == 0


def test_verbose_lifecycle_clear_and_export_exclusion(tmp_path):
    now = datetime(2026, 7, 21, tzinfo=timezone.utc)
    store = _store(tmp_path, now=now)
    store.initialize()
    store.enable_verbose("session-1")
    assert store.capture_verbose("session-1", "complete local diagnostic text") is True
    assert store.read_verbose("session-1") == ["complete local diagnostic text"]
    assert not store.has_foreign_keys("diagnostic_verbose")
    store.record_failure("write_failed", force_degraded=True)
    store.clear_all()
    assert store.read_verbose("session-1") == []
    assert store.health()["cumulative_failures"] == 0
    assert store.health()["status"] == "ready"
    assert "diagnostics.sqlite3" in DIAGNOSTIC_EXPORT_EXCLUSIONS
    assert "diagnostic_events" in DIAGNOSTIC_EXPORT_EXCLUSIONS
    assert "diagnostic_verbose" in DIAGNOSTIC_EXPORT_EXCLUSIONS


def test_verbose_capture_expires_after_24_hours_and_clears_text(tmp_path):
    clock = [datetime(2026, 7, 21, tzinfo=timezone.utc)]
    store = DiagnosticStore(
        tmp_path / "app-data",
        control_path=tmp_path / "control.sqlite3",
        clock=lambda: clock[0],
    )
    store.initialize()
    store.enable_verbose("session-1")
    assert store.capture_verbose("session-1", "temporary text") is True
    clock[0] += timedelta(hours=25)
    assert store.capture_verbose("session-1", "must not persist") is False
    assert store.read_verbose("session-1") == []


def test_failure_history_persists_outside_diagnostics_database(tmp_path):
    first = _store(tmp_path)
    first.initialize()
    for _ in range(3):
        first.record_failure("write_failed")
    assert first.health()["status"] == "degraded"

    restarted = _store(tmp_path)
    restarted.initialize()
    assert restarted.health()["status"] == "degraded"
    assert restarted.health()["cumulative_failures"] == 3
    assert restarted.health()["consecutive_failures"] == 0
    assert restarted.startup_probe() is True
    assert restarted.health()["status"] == "ready"
    assert restarted.health()["cumulative_failures"] == 3
