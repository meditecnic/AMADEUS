"""MEMORY-V11 S0 red contracts: legacy import, shared purge, consolidation, backup.

Fixed import surface only:
  app.services.memory_v11.migration
  app.services.memory_v11.repository
"""
from __future__ import annotations

import importlib
import sqlite3
from pathlib import Path

import pytest

from app import db as db_module
from app.db import init_db, reset_initialization_cache
from app.services.memory import CoreFactCandidate, memory_service


CONTENT_TABLES = (
    "stable_facts",
    "stable_fact_versions",
    "stable_fact_evidence",
    "memory_observations",
)


def _migration():
    return importlib.import_module("app.services.memory_v11.migration")


def _repository():
    return importlib.import_module("app.services.memory_v11.repository")


@pytest.fixture
async def isolated_store(tmp_path, monkeypatch):
    monkeypatch.delenv("AMADEUS_DB_PATH", raising=False)
    monkeypatch.setenv("AMADEUS_DATA_DIR", str(tmp_path))
    reset_initialization_cache()
    await init_db()
    return tmp_path


def _control_conn() -> sqlite3.Connection:
    connection = sqlite3.connect(db_module._resolve_path("steins_gate", "control"))
    connection.row_factory = sqlite3.Row
    return connection


def _memory_conn(worldline: str = "steins_gate") -> sqlite3.Connection:
    connection = sqlite3.connect(db_module._resolve_path(worldline, "memory"))
    connection.row_factory = sqlite3.Row
    return connection


def _count_table(connection: sqlite3.Connection, table: str) -> int:
    exists = connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    assert exists is not None, f"required table missing: {table}"
    return int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def _count_table_for_owner(
    connection: sqlite3.Connection, table: str, owner: str
) -> int:
    """Owner-scoped counts where session_id exists; else full table count for proposals."""
    cols = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
    if "session_id" in cols:
        return int(
            connection.execute(
                f"SELECT COUNT(*) FROM {table} WHERE session_id=?",
                (owner,),
            ).fetchone()[0]
        )
    return _count_table(connection, table)


def _snapshot_owner_content(owner: str, worldline: str = "steins_gate") -> dict[str, int]:
    connection = _memory_conn(worldline)
    try:
        snap = {
            table: _count_table_for_owner(connection, table, owner)
            for table in CONTENT_TABLES
        }
        snap["memory_consolidation_proposals"] = _count_table_for_owner(
            connection, "memory_consolidation_proposals", owner
        )
        return snap
    finally:
        connection.close()


@pytest.mark.asyncio
async def test_s0_legacy_import_idempotent_no_row_growth(isolated_store):
    """Second import: stable_facts/versions/evidence/observations counts must not increase."""
    owner = "v11-mig"
    await memory_service.upsert_core_fact(
        owner,
        "steins_gate",
        CoreFactCandidate(
            fact_key="likes_anime",
            fact_value="喜欢看动漫",
            confidence=0.9,
            importance=0.85,
            source_message_ids=[4151],
        ),
        identity_mode="self",
    )
    mig = _migration()
    first = await mig.import_legacy_core_facts(session_id=owner, worldline="steins_gate")
    after_first = _snapshot_owner_content(owner)
    assert any(after_first[t] > 0 for t in CONTENT_TABLES), after_first

    second = await mig.import_legacy_core_facts(session_id=owner, worldline="steins_gate")
    after_second = _snapshot_owner_content(owner)

    def ids(rows):
        out = []
        for item in rows:
            if isinstance(item, dict):
                out.append(item["fact_id"])
            else:
                out.append(item.fact_id)
        return sorted(out)

    assert ids(first) == ids(second)
    assert ids(first), "expected at least one imported fact"
    for table in CONTENT_TABLES:
        assert after_second[table] == after_first[table], (
            f"second import grew {table}: {after_first[table]} → {after_second[table]}"
        )


@pytest.mark.asyncio
async def test_s0_shared_row_seeded_then_purged_not_migrated(isolated_store):
    owner = "v11-shared-purge"
    # Module surface must exist before content snapshots (v11 tables).
    mig = _migration()

    control = _control_conn()
    try:
        control.execute(
            """INSERT INTO shared_user_facts(
                   owner_session_id,fact_key,fact_value,confidence,importance,
                   is_current,is_pinned,origin_worldline,origin_conversation_id,
                   origin_identity_mode,source_message_ids,created_at
               ) VALUES(?,?,?,?,?,1,0,?,?,?,?,?)""",
            (
                owner,
                "likes_fruit",
                "喜欢吃水果",
                0.9,
                0.8,
                "steins_gate",
                "conv-shared-seed",
                "self",
                "[1]",
                "2026-08-01T00:00:00+00:00",
            ),
        )
        control.commit()
        before = control.execute(
            "SELECT COUNT(*) FROM shared_user_facts WHERE owner_session_id=?",
            (owner,),
        ).fetchone()[0]
        assert before == 1
    finally:
        control.close()

    before_stable = _snapshot_owner_content(owner)
    await mig.purge_shared_user_facts(owner_session_id=owner)

    control = _control_conn()
    try:
        after = control.execute(
            "SELECT COUNT(*) FROM shared_user_facts WHERE owner_session_id=?",
            (owner,),
        ).fetchone()[0]
        assert after == 0
    finally:
        control.close()

    after_stable = _snapshot_owner_content(owner)
    for table in CONTENT_TABLES:
        assert after_stable[table] == before_stable[table], (
            f"purge migrated into {table}: {before_stable[table]} → {after_stable[table]}"
        )
    repo = _repository()
    rows = await repo.list_active_stable_facts(
        session_id=owner, worldline="steins_gate", identity_mode="self"
    )
    texts = [
        (r["display_text"] if isinstance(r, dict) else r.display_text) for r in rows
    ]
    assert not any("水果" in t for t in texts)


@pytest.mark.asyncio
async def test_s0_consolidation_preview_exactly_one_open_proposal(isolated_store):
    """Preview: content tables unchanged; proposal count exactly +1 with open + snapshot."""
    owner = "v11-consol"
    for key in ("likes_anime", "like_anime"):
        await memory_service.upsert_core_fact(
            owner,
            "steins_gate",
            CoreFactCandidate(
                fact_key=key,
                fact_value="喜欢看动漫",
                confidence=0.9,
                importance=0.85,
                source_message_ids=[4151],
            ),
            identity_mode="self",
        )
    mig = _migration()
    await mig.import_legacy_core_facts(session_id=owner, worldline="steins_gate")

    before = _snapshot_owner_content(owner)
    proposal = await mig.preview_consolidation(
        session_id=owner, worldline="steins_gate", identity_mode="self"
    )
    after = _snapshot_owner_content(owner)

    assert proposal is not None
    if isinstance(proposal, dict):
        proposal_id = proposal.get("proposal_id")
        status = proposal.get("status") or proposal.get("state")
        snapshot = proposal.get("snapshot_hash") or proposal.get("expected_snapshot")
    else:
        proposal_id = getattr(proposal, "proposal_id", None)
        status = getattr(proposal, "status", None) or getattr(proposal, "state", None)
        snapshot = getattr(proposal, "snapshot_hash", None) or getattr(
            proposal, "expected_snapshot", None
        )
    assert proposal_id, proposal
    assert status in {"open", "OPEN", "pending"}, f"proposal not open: {status!r}"
    assert snapshot, f"proposal missing snapshot: {proposal!r}"

    for table in CONTENT_TABLES:
        assert after[table] == before[table], (
            f"preview mutated {table}: {before[table]} → {after[table]}"
        )
    assert after["memory_consolidation_proposals"] == before[
        "memory_consolidation_proposals"
    ] + 1, (
        f"proposal count must be exactly +1: "
        f"{before['memory_consolidation_proposals']} → {after['memory_consolidation_proposals']}"
    )


@pytest.mark.asyncio
async def test_s0_backup_creates_openable_non_overwrite_file(isolated_store):
    mig = _migration()
    backup_fn = getattr(mig, "backup_memory_db_before_v11", None) or getattr(
        mig, "backup_worldline_memory_db", None
    )
    assert callable(backup_fn), "migration backup helper missing"

    first = backup_fn(worldline="steins_gate")
    first_path = Path(first if not isinstance(first, dict) else first["path"])
    assert first_path.is_file(), f"backup missing: {first_path}"
    first_stat = first_path.stat()
    first_mtime = first_stat.st_mtime_ns
    first_size = first_stat.st_size

    connection = sqlite3.connect(str(first_path))
    try:
        connection.execute("SELECT 1").fetchone()
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert tables, "backup DB has no tables"
    finally:
        connection.close()

    second = backup_fn(worldline="steins_gate")
    second_path = Path(second if not isinstance(second, dict) else second["path"])
    assert second_path.is_file()
    assert second_path != first_path, "second backup reused the same path (overwrite risk)"
    assert first_path.is_file()
    assert first_path.stat().st_mtime_ns == first_mtime
    assert first_path.stat().st_size == first_size


@pytest.mark.asyncio
async def test_s0_import_failure_rolls_back_owner_content(isolated_store, monkeypatch):
    """Injected failure: all owner content tables return exactly to before snapshot."""
    owner = "v11-mig-rollback"
    await memory_service.upsert_core_fact(
        owner,
        "steins_gate",
        CoreFactCandidate(
            fact_key="likes_anime",
            fact_value="喜欢看动漫",
            confidence=0.9,
            importance=0.85,
            source_message_ids=[4151],
        ),
        identity_mode="self",
    )
    await memory_service.upsert_core_fact(
        owner,
        "steins_gate",
        CoreFactCandidate(
            fact_key="likes_coffee",
            fact_value="喜欢咖啡",
            confidence=0.9,
            importance=0.85,
            source_message_ids=[4152],
        ),
        identity_mode="self",
    )

    mig = _migration()
    repo = _repository()
    before = _snapshot_owner_content(owner)

    original_create = getattr(repo, "create_stable_fact_from_legacy", None)
    assert callable(original_create), (
        "repository.create_stable_fact_from_legacy missing — required for rollback proof"
    )

    calls = {"n": 0}

    async def _boom(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] >= 2:
            raise RuntimeError("injected import failure")
        return await original_create(*args, **kwargs)

    monkeypatch.setattr(repo, "create_stable_fact_from_legacy", _boom)
    # Also patch the symbol as seen from migration module if it bound a local ref.
    if hasattr(mig, "create_stable_fact_from_legacy"):
        monkeypatch.setattr(mig, "create_stable_fact_from_legacy", _boom)
    if hasattr(mig, "repository"):
        monkeypatch.setattr(mig.repository, "create_stable_fact_from_legacy", _boom)

    with pytest.raises(RuntimeError, match="injected import failure"):
        await mig.import_legacy_core_facts(session_id=owner, worldline="steins_gate")

    after = _snapshot_owner_content(owner)
    for table in (*CONTENT_TABLES, "memory_consolidation_proposals"):
        assert after[table] == before[table], (
            f"import rollback left {table} changed for owner={owner}: "
            f"{before[table]} → {after[table]}"
        )
