"""Live migration integration tests use isolated SQLite and the fake E5 fixture."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3

import pytest

from app import db as db_module
from app.services.conversation_erasure import request_erasure
from app.services.memory_v11 import migration as mig, repository as repo
from app.services.memory_v11.facts import delete_fact, user_edit_fact
from scripts import legacy_memory_migrate as cli

SG = "steins_gate"
NOW = "2026-10-27T00:00:00+00:00"


@pytest.fixture
async def store(tmp_path, monkeypatch):
    monkeypatch.delenv("AMADEUS_DB_PATH", raising=False)
    monkeypatch.setenv("AMADEUS_DATA_DIR", str(tmp_path))
    db_module.reset_initialization_cache()
    await db_module.init_db()
    return tmp_path


def connection(wl=SG, kind="memory"):
    conn = sqlite3.connect(db_module._resolve_path(wl, kind))
    conn.row_factory = sqlite3.Row
    return conn


def source(mid=1, *, wl=SG, owner="owner", mode="self", content="ordinary source"):
    conv = f"c-{owner}-{mode}-{mid}"
    with connection(wl, "history") as conn:
        conn.execute("INSERT INTO conversations(id,session_id,identity_mode,title) VALUES(?,?,?,'test')", (conv, owner, mode))
        conn.execute("INSERT INTO messages(id,session_id,conversation_id,role,content,created_at) VALUES(?,?,?,'user',?,?)",
                     (mid, owner, conv, content, NOW))
    return conv


def core(mid=1, *, wl=SG, owner="owner", mode="self", key="likes_anime", value="喜欢动漫", **fields):
    row = dict(session_id=owner, identity_mode=mode, fact_key=key, fact_value=value,
               confidence=0.9, importance=0.8, is_current=1, is_pinned=0, is_dismissed=0,
               source_message_ids=json.dumps([mid]), created_at=NOW)
    row.update(fields)
    with connection(wl) as conn:
        return conn.execute(f"INSERT INTO core_facts({','.join(row)}) VALUES({','.join('?' for _ in row)})", tuple(row.values())).lastrowid


def shared(mid=1, *, wl=SG, owner="owner", key="likes_anime", value="喜欢动漫", **fields):
    row = dict(owner_session_id=owner, fact_key=key, fact_value=value, confidence=0.9,
               importance=0.8, is_current=1, is_pinned=0, origin_worldline=wl,
               origin_conversation_id=f"c-{owner}-self-{mid}", origin_identity_mode="self",
               source_message_ids=json.dumps([mid]), created_at=NOW)
    row.update(fields)
    with connection(SG, "control") as conn:
        return conn.execute(f"INSERT INTO shared_user_facts({','.join(row)}) VALUES({','.join('?' for _ in row)})", tuple(row.values())).lastrowid


def erasure(mid, *, wl=SG, forget=True, owner="owner", mode="self"):
    with connection(wl) as conn:
        conn.execute("INSERT INTO conversation_erasure_requests VALUES(?,?,?,?, 'delete',?,?, 'completed',?)",
                     (f"r-{mid}-{int(forget)}", owner, mode, f"c-{owner}-{mode}-{mid}", int(forget), json.dumps([mid]), NOW))


def counts(wl=SG):
    with connection(wl) as conn:
        return {table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in ("stable_facts", "stable_fact_versions", "stable_fact_evidence",
                              "memory_observations", "memory_observation_sources", "stable_fact_version_embeddings")}


def old_hashes():
    result = {}
    for wl, kind, tables in ((SG, "memory", ("core_facts", "episodic_memories")),
                             ("beta", "memory", ("core_facts", "episodic_memories")),
                             (SG, "control", ("shared_user_facts",))):
        with connection(wl, kind) as conn:
            for table in tables:
                result[(wl, table)] = hashlib.sha256(json.dumps(
                    [list(r) for r in conn.execute(f"SELECT * FROM {table} ORDER BY id")],
                    sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return result


@pytest.mark.parametrize("memory_mode", ["legacy", "shadow", "v11"])
async def test_locations_and_old_tables_unchanged(store, monkeypatch, memory_mode):
    monkeypatch.setenv("AMADEUS_MEMORY_MODE", memory_mode)
    for wl in (SG, "beta"):
        for mid, mode in ((1, "self"), (2, "okabe")):
            source(mid, wl=wl, mode=mode)
            core(mid, wl=wl, mode=mode, key=f"{wl}_{mode}")
        source(3, wl=wl)
        shared(3, wl=wl, key="shared-" + wl)
        with connection(wl) as conn:
            conn.execute("INSERT INTO episodic_memories(session_id,identity_mode,content,confidence,importance,source_message_ids,created_at) VALUES('owner','self','untouched',0.9,0.8,'[1]',?)", (NOW,))
    before = old_hashes()
    report = await mig.apply_live_migration(preview=mig.build_live_migration_preview())
    assert old_hashes() == before
    assert len(report["reconciliation"]) == 6
    for wl in (SG, "beta"):
        for mode, expected in (("self", 2), ("okabe", 1)):
            rows = await repo.list_active_stable_facts(session_id="owner", worldline=wl, identity_mode=mode)
            assert len(rows) == expected
            assert all(r["topic_id"] is None for r in rows)
        with connection(wl) as conn:
            assert conn.execute("SELECT COUNT(*) FROM memory_topics").fetchone()[0] == 0
            assert conn.execute("SELECT COUNT(*) FROM stable_fact_version_embeddings").fetchone()[0] == 3
    before_counts = (counts(), counts("beta"))
    await mig.apply_live_migration(preview=mig.build_live_migration_preview())
    assert (counts(), counts("beta")) == before_counts
    assert old_hashes() == before


@pytest.mark.parametrize("reason,fields", [
    ("dismissed", {"is_dismissed": 1}), ("not_current", {"is_current": 0}),
    ("sensitive_fact_blocked", {"value": "密码是 Password123!"}),
    ("sensitive_source_blocked", {}), ("erasure_barrier", {}),
    ("tombstone_source_deleted", {}), ("source_missing", {}),
])
async def test_skip_barriers(store, reason, fields):
    if reason != "source_missing":
        source(content="密码是 Password123!" if reason == "sensitive_source_blocked" else "ordinary source")
    core(**fields)
    if reason == "erasure_barrier":
        erasure(1)
    if reason == "tombstone_source_deleted":
        fp = mig._source_fingerprint("owner", 1, "ordinary source")
        with connection() as conn:
            conn.execute("INSERT INTO memory_tombstones VALUES('t','owner','self','old',?,NULL,'delete',?)", (fp, NOW))
    for _ in range(2):
        report = await mig.apply_live_migration(preview=mig.build_live_migration_preview())
        assert report["reconciliation"][0]["skipped"] == {reason: 1}
        assert not any(counts().values())


@pytest.mark.parametrize("survives", [True, False])
async def test_retained_erasure_deleted_provenance_no_source_body(store, survives):
    original = "原始消息不能进入v11 TEST_ONLY"
    if survives:
        source(content=original)
    core()
    erasure(1, forget=False)
    await mig.import_legacy_core_facts(session_id="owner", worldline=SG)
    with connection() as conn:
        row = conn.execute("SELECT * FROM memory_observation_sources").fetchone()
        assert row["source_state"] == "deleted"
        assert row["excerpt"] is None
        assert row["conversation_id"] == "c-owner-self-1"
        assert row["source_created_at"] == NOW
        for (table,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():
            if table in ("core_facts", "episodic_memories") or table.startswith(("sqlite_", "core_vec", "episodic_vec")):
                continue
            assert original not in repr([tuple(r) for r in conn.execute(f'SELECT * FROM "{table}"')])


async def test_missing_sources_forget_wins_and_tombstone_without_content_is_not_bypassed(store):
    core()
    erasure(1, forget=False)
    erasure(1, forget=True)
    assert mig.build_live_migration_preview()["items"][0]["reason"] == "erasure_barrier"
    with connection() as conn:
        conn.execute("DELETE FROM conversation_erasure_requests WHERE forget_long_term=1")
        conn.execute("INSERT INTO memory_tombstones VALUES('t','owner','self','old','irreversible-content-hash',NULL,'delete',?)", (NOW,))
    assert mig.build_live_migration_preview()["items"][0]["reason"] == "tombstone_source_unverifiable"
    with connection() as conn:
        conn.execute("INSERT INTO memory_deleted_sources VALUES('owner','self','c-owner-self-1',1,'old',?)", (NOW,))
    assert mig.build_live_migration_preview()["items"][0]["reason"] == "tombstone_source_deleted"
    with connection() as conn:
        conn.execute("UPDATE memory_deleted_sources SET source_message_id=2")
    # Identifiable, unrelated tombstones do not block retained source 1.
    assert mig.build_live_migration_preview()["items"][0]["decision"] == "planned"


async def test_deleted_and_edited_imports_stay_authoritative(store):
    for mid in (1, 2):
        source(mid)
        core(mid, key=f"key-{mid}")
    rows = await mig.import_legacy_core_facts(session_id="owner", worldline=SG)
    await delete_fact(session_id="owner", worldline=SG, identity_mode="self", fact_id=rows[0]["fact_id"], expected_version=1)
    await user_edit_fact(session_id="owner", worldline=SG, identity_mode="self", fact_id=rows[1]["fact_id"], expected_version=1, display_text="用户改写")
    before = counts()
    rerun = await mig.import_legacy_core_facts(session_id="owner", worldline=SG)
    assert {r["fact_id"] for r in rows} == {r["fact_id"] for r in rerun}
    assert counts() == before
    active = await repo.list_active_stable_facts(session_id="owner", worldline=SG, identity_mode="self")
    assert len(active) == 1 and active[0]["display_text"] == "用户改写"


async def test_pin_limit_existing_residents_priority_and_shared_local_alias(store):
    for i in range(2):
        await repo.create_stable_fact(session_id="owner", worldline=SG, identity_mode="self", display_text=f"existing {i}", semantic_json={"existing": i}, is_pinned=True)
    for mid in range(1, 11):
        source(mid)
        core(mid, key=f"key-{mid}", is_pinned=1, importance=mid / 10)
    shared(10, key="key-10", is_pinned=1, importance=1)
    report = await mig.apply_live_migration(preview=mig.build_live_migration_preview())
    rows = await repo.list_active_stable_facts(session_id="owner", worldline=SG, identity_mode="self")
    assert len(rows) == 12
    assert sum(r["is_pinned"] for r in rows) == 8
    assert {r["display_text"].split(":")[0] for r in rows if r["is_pinned"] and ":" in r["display_text"]} == {f"key-{i}" for i in range(5, 11)}
    assert sum(i.get("pin_reason") == "pin_limit_unpinned" for i in report["items"]) == 4
    assert next(s for s in report["reconciliation"] if s["source_category"] == "shared")["merged_into_local"] == 1


async def test_shared_alias_merges_evidence_and_preserves_shared_rows(store):
    source(1)
    source(2)
    core(1, value=" TEA ")
    shared(2, value="tea")
    before = old_hashes()
    report = await mig.apply_live_migration(preview=mig.build_live_migration_preview())
    assert old_hashes() == before
    assert len({i["fact_id"] for i in report["items"]}) == 1
    assert counts()["stable_fact_evidence"] == 2
    assert counts()["stable_fact_version_embeddings"] == 1
    for mid in (1, 2):
        await request_erasure(session_id="owner", identity_mode="self", worldline=SG,
                              conversation_id=f"c-owner-self-{mid}", action="delete", forget_long_term=True)
        assert len(await repo.list_active_stable_facts(session_id="owner", worldline=SG, identity_mode="self")) == (1 if mid == 1 else 0)


async def test_unexplained_outcome_rolls_back_all_worldlines(store, monkeypatch):
    for wl in (SG, "beta"):
        source(wl=wl)
        core(wl=wl)
    original = mig._reconcile_live

    def corrupt(items, *, preview=False):
        if not preview:
            items[-1]["outcome"] = "unknown"
        return original(items, preview=preview)

    monkeypatch.setattr(mig, "_reconcile_live", corrupt)
    with pytest.raises(mig.MigrationRehearsalError, match="reconciliation_failed"):
        await mig.apply_live_migration(preview=mig.build_live_migration_preview())
    for wl in (SG, "beta"):
        assert not any(counts(wl).values())
        with connection(wl) as conn:
            assert not mig._table_exists(conn, "legacy_core_fact_imports")


async def test_unknown_classification_rejected(store, monkeypatch):
    source()
    core()
    original = mig._evaluate_fact_row

    def corrupt(*args):
        item = original(*args)
        item["decision"] = "unknown"
        return item

    monkeypatch.setattr(mig, "_evaluate_fact_row", corrupt)
    with pytest.raises(mig.MigrationRehearsalError, match="reconciliation_failed"):
        await mig.import_legacy_core_facts(session_id="owner", worldline=SG)
    assert not any(counts().values())


async def test_embedding_failure_and_changes_during_encoding_do_not_write(store, monkeypatch):
    from app.services.memory_v11 import indexing
    from app.services.memory_v11.contracts import MemoryValidationError

    source()
    core()
    original = indexing.prepare_passage_embedding

    async def failed(*args, **kwargs):
        raise MemoryValidationError("embedding_failed")

    monkeypatch.setattr(indexing, "prepare_passage_embedding", failed)
    with pytest.raises(MemoryValidationError, match="embedding_failed"):
        await mig.import_legacy_core_facts(session_id="owner", worldline=SG)
    assert not any(counts().values())

    async def changed(*args, **kwargs):
        prepared = await original(*args, **kwargs)
        with connection() as conn:
            conn.execute("UPDATE core_facts SET fact_value='changed'")
        return prepared

    monkeypatch.setattr(indexing, "prepare_passage_embedding", changed)
    with pytest.raises(mig.MigrationRehearsalError, match="preview_stale"):
        await mig.import_legacy_core_facts(session_id="owner", worldline=SG)
    assert not any(counts().values())


async def seed_duplicates():
    for mid in (1, 2):
        source(mid)
        core(mid, key=f"different-key-{mid}")
    return await mig.import_legacy_core_facts(session_id="owner", worldline=SG)


async def test_consolidation_preview_apply_evidence_and_conversation_forgetting(store):
    imported = await seed_duplicates()
    before = counts()
    proposal = await mig.preview_consolidation(session_id="owner", worldline=SG, identity_mode="self")
    assert counts() == before
    assert (await mig.preview_consolidation(session_id="owner", worldline=SG, identity_mode="self"))["proposal_id"] == proposal["proposal_id"]
    await mig.apply_consolidation(proposal_id=proposal["proposal_id"], worldline=SG)
    assert counts()["stable_fact_evidence"] == 2
    assert counts()["stable_fact_versions"] == 1
    rerun = await mig.import_legacy_core_facts(session_id="owner", worldline=SG)
    assert len(rerun) == len(imported) == 2
    assert len({r["fact_id"] for r in rerun}) == 1
    assert len(await repo.list_active_stable_facts(session_id="owner", worldline=SG, identity_mode="self")) == 1
    with connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM memory_tombstones").fetchone()[0] == 0
        assert conn.execute("SELECT status FROM memory_consolidation_proposals").fetchone()[0] == "applied"
    for mid in (1, 2):
        await request_erasure(session_id="owner", identity_mode="self", worldline=SG,
                              conversation_id=f"c-owner-self-{mid}", action="delete", forget_long_term=True)
        assert len(await repo.list_active_stable_facts(session_id="owner", worldline=SG, identity_mode="self")) == (1 if mid == 1 else 0)
    assert counts()["stable_fact_versions"] == 0
    with connection() as conn:
        assert not conn.execute("SELECT 1 FROM memory_observations WHERE display_text IS NOT NULL").fetchone()


@pytest.mark.parametrize("change", ["edit", "evidence", "pin"])
async def test_stale_consolidation_and_cancel(store, change):
    rows = await seed_duplicates()
    proposal = await mig.preview_consolidation(session_id="owner", worldline=SG, identity_mode="self")
    if change == "edit":
        await user_edit_fact(session_id="owner", worldline=SG, identity_mode="self", fact_id=rows[0]["fact_id"], expected_version=1, display_text="changed")
    else:
        with connection() as conn:
            conn.execute("UPDATE memory_observation_sources SET source_state='deleted',excerpt=NULL" if change == "evidence" else "UPDATE stable_facts SET is_pinned=1")
    before = counts()
    with pytest.raises(mig.MigrationRehearsalError, match="snapshot_stale"):
        await mig.apply_consolidation(proposal_id=proposal["proposal_id"], worldline=SG)
    assert counts() == before
    await mig.cancel_consolidation(proposal_id=proposal["proposal_id"], worldline=SG)
    assert counts() == before
    with connection() as conn:
        assert conn.execute("SELECT status FROM memory_consolidation_proposals").fetchone()[0] == "cancelled"


async def test_consolidation_ranking_and_native_facts(store):
    rows = await seed_duplicates()
    await user_edit_fact(session_id="owner", worldline=SG, identity_mode="self", fact_id=rows[0]["fact_id"], expected_version=1, display_text="same text")
    native = await repo.create_stable_fact(session_id="owner", worldline=SG, identity_mode="self", display_text=" SAME TEXT ", semantic_json={"native": True})
    proposal = await mig.preview_consolidation(session_id="owner", worldline=SG, identity_mode="self")
    assert proposal["groups"] == [[rows[0]["fact_id"], native["fact_id"]]]
    await mig.cancel_consolidation(proposal_id=proposal["proposal_id"], worldline=SG)
    with connection() as conn:
        conn.execute("UPDATE stable_facts SET is_pinned=1 WHERE fact_id=?", (native["fact_id"],))
    proposal = await mig.preview_consolidation(session_id="owner", worldline=SG, identity_mode="self")
    assert proposal["groups"] == [[native["fact_id"], rows[0]["fact_id"]]]


def test_cli_requires_data_dir_and_rejects_db_override(store, monkeypatch, tmp_path):
    with pytest.raises(SystemExit):
        cli.main(["preview", "--output", str(tmp_path / "report.json")])
    monkeypatch.setenv("AMADEUS_DB_PATH", "")
    with pytest.raises(SystemExit):
        cli.main(["preview", "--data-dir", str(store), "--output", str(tmp_path / "report.json")])


def test_cli_backups_before_writes_no_text_stdout_exclusive_reports(store, monkeypatch, capsys):
    source()
    core(value="TEST FACT MUST NOT REACH STDOUT")
    preview, result = store / "preview.json", store / "apply.json"
    args = ["--data-dir", str(store)]
    assert cli.main(["preview", *args, "--output", str(preview)]) == 0
    before = counts()
    assert cli.main(["preview", *args, "--output", str(preview)]) == 1
    assert counts() == before
    original = repo.create_stable_fact_from_legacy

    async def check_backups(*a, **kw):
        assert os.environ["HF_HUB_OFFLINE"] == "1"
        backups = list(store.rglob("*.backup.sqlite3"))
        assert len(backups) == 3
        for path in backups:
            with sqlite3.connect(path) as conn:
                assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        return await original(*a, **kw)

    monkeypatch.setattr(repo, "create_stable_fact_from_legacy", check_backups)
    assert cli.main(["apply", *args, "--preview", str(preview), "--output", str(result)]) == 0
    report = json.loads(result.read_text())
    assert len(report["backups"]) == 3
    assert report["reconciliation"][0] == dict(worldline=SG, session_id="owner", identity_mode="self", source_category="core", total=1, migrated=1, merged_into_local=0, skipped={}, deferred={}, unexplained=0)
    assert "TEST FACT MUST NOT REACH STDOUT" not in capsys.readouterr().out


def test_cli_stale_preview_refuses_writes(store):
    source()
    core()
    preview, report = store / "preview.json", store / "apply.json"
    args = ["--data-dir", str(store)]
    assert cli.main(["preview", *args, "--output", str(preview)]) == 0
    with connection() as conn:
        conn.execute("UPDATE core_facts SET fact_value='changed'")
    assert cli.main(["apply", *args, "--preview", str(preview), "--output", str(report)]) == 1
    assert not any(counts().values())
    assert not list(store.rglob("*.backup.sqlite3"))


async def test_pin_priority_confidence_then_row_id(store):
    for mid in range(1, 11):
        source(mid)
        core(mid, key=f"key-{mid}", is_pinned=1, importance=0.8,
             confidence=0.95 if mid >= 9 else 0.9)
    rows = await mig.import_legacy_core_facts(session_id="owner", worldline=SG)
    assert {r["legacy_row_id"] for r in rows if r["is_pinned"]} == {1, 2, 3, 4, 5, 6, 9, 10}


async def test_native_semantic_duplicate_transitive_group_and_scope_isolation(store):
    for mode, owner in (("self", "owner"), ("okabe", "owner"), ("self", "other")):
        for text in ("first text", "second text"):
            await repo.create_stable_fact(session_id=owner, worldline=SG, identity_mode=mode,
                                          display_text=text, semantic_json={"same": True})
    proposal = await mig.preview_consolidation(session_id="owner", worldline=SG, identity_mode="self")
    assert len(proposal["groups"]) == 1 and len(proposal["groups"][0]) == 2
    await mig.apply_consolidation(proposal_id=proposal["proposal_id"], worldline=SG)
    for mode, owner, expected in (("self", "owner", 1), ("okabe", "owner", 2), ("self", "other", 2)):
        assert len(await repo.list_active_stable_facts(session_id=owner, worldline=SG, identity_mode=mode)) == expected


def test_cli_consolidation_commands(store):
    for mid in (1, 2):
        source(mid)
        core(mid, key=f"key-{mid}")
    args = ["--data-dir", str(store)]
    preview = store / "preview.json"
    assert cli.main(["preview", *args, "--output", str(preview)]) == 0
    assert cli.main(["apply", *args, "--preview", str(preview), "--output", str(store / "apply.json")]) == 0
    proposal_file = store / "proposal.json"
    scope = ["--worldline", SG]
    assert cli.main(["consolidate-preview", *args, *scope, "--session-id", "owner", "--identity-mode", "self", "--output", str(proposal_file)]) == 0
    proposal_id = json.loads(proposal_file.read_text())["proposal_id"]
    assert cli.main(["consolidate-cancel", *args, *scope, "--proposal", proposal_id, "--output", str(store / "cancel.json")]) == 0
    proposal_file = store / "proposal2.json"
    assert cli.main(["consolidate-preview", *args, *scope, "--session-id", "owner", "--identity-mode", "self", "--output", str(proposal_file)]) == 0
    proposal_id = json.loads(proposal_file.read_text())["proposal_id"]
    assert cli.main(["consolidate-apply", *args, *scope, "--proposal", proposal_id, "--output", str(store / "merged.json")]) == 0
    with connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM stable_facts WHERE state='active'").fetchone()[0] == 1


async def test_shared_copy_after_local_v11_delete_or_edit_is_not_recreated(store):
    for mid in (1, 2, 3, 4):
        source(mid)
    core(1, key="deleted")
    core(2, key="edited")
    rows = await mig.import_legacy_core_facts(session_id="owner", worldline=SG)
    await delete_fact(session_id="owner", worldline=SG, identity_mode="self", fact_id=rows[0]["fact_id"], expected_version=1)
    await user_edit_fact(session_id="owner", worldline=SG, identity_mode="self", fact_id=rows[1]["fact_id"], expected_version=1, display_text="my new text")
    shared(3, key="deleted")
    shared(4, key="edited")
    report = await mig.import_shared_user_facts(owner_session_id="owner")
    assert all(i["outcome"] == "merged_into_local" for i in report["items"])
    active = await repo.list_active_stable_facts(session_id="owner", worldline=SG, identity_mode="self")
    assert len(active) == 1 and active[0]["display_text"] == "my new text"
    before = counts()
    await mig.import_shared_user_facts(owner_session_id="owner")
    assert counts() == before


async def test_later_forget_removes_fact_imported_from_deleted_retained_source(store):
    core()
    erasure(1, forget=False)
    await mig.import_legacy_core_facts(session_id="owner", worldline=SG)
    assert counts()["stable_facts"] == 1
    await request_erasure(session_id="owner", identity_mode="self", worldline=SG,
                          conversation_id="c-owner-self-1", action="forget", forget_long_term=True)
    assert not await repo.list_active_stable_facts(session_id="owner", worldline=SG, identity_mode="self")


def test_cli_standalone_stdout_is_only_counts(store):
    import subprocess
    import sys

    source()
    core()
    result = subprocess.run([sys.executable, cli.__file__, "preview", "--data-dir", str(store),
                             "--output", str(store / "subprocess.json")], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    summary = json.loads(result.stdout)
    assert summary["total"] == 1
    assert "喜欢动漫" not in result.stdout
