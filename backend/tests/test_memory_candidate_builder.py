"""Stage-A candidate builder tests (task memory-candidate-glm-20260909).

Synthetic five-DB snapshot + real rehearsal preview -> complete candidate
directory, per the C contract:

1. legacy states/vectors/FTS, history sequences (incl. high-water > max id)
   and control are preserved; v11 content is rebuilt exclusively from the
   donor import; jobs are frozen; old import ledger replaced by this build;
2. missing file / incompatible schema / pending erasure / commit mismatch /
   unknown table / stale preview are rejected, source unchanged;
3. frozen jobs are not schedulable under the REAL jobs API, retry does not
   reactivate, completion spy stays 0; pending-without-receipt froze fine
   and existing receipts are preserved verbatim;
4. the donor's imports are the only active v11 content — scope-strict via
   the REAL retrieval path; demo/unknown/old ledger never mix in;
5. mid-build failure publishes nothing (incomplete staging kept), rebuilds
   to two new paths have identical content sets/counts, existing outputs and
   alias paths are never overwritten;
6. the builder never imports app.main/provider_runtime/credentials and never
   calls lifespan/init_db (source-level guard; conftest imports pollute
   sys.modules so runtime introspection is not meaningful here).

All data is synthetic; no real APPDATA, credentials, or external models.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import struct
import subprocess
import sys
import time
from pathlib import Path

import pytest

from app.db import CONTROL_SCHEMA, HISTORY_SCHEMA, MEMORY_SCHEMA
from app.services.memory_v11 import candidate as cand
from app.services.memory_v11 import migration as mig

BACKEND_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = BACKEND_ROOT / "scripts" / "build_memory_candidate.py"

SG = "steins_gate"
BETA = "beta"
NOW = "2026-09-09T00:00:00+00:00"


# ---------------------------------------------------------------------------
# Synthetic five-DB snapshot helpers.
# ---------------------------------------------------------------------------


def _open(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def _db_paths(snapshot: Path) -> dict[str, Path]:
    return {
        "control": snapshot / "control.sqlite3",
        "sg_history": snapshot / "worldlines" / "sg" / "history.sqlite3",
        "sg_memory": snapshot / "worldlines" / "sg" / "memory.sqlite3",
        "beta_history": snapshot / "worldlines" / "beta" / "history.sqlite3",
        "beta_memory": snapshot / "worldlines" / "beta" / "memory.sqlite3",
    }


def _snapshot_state(snapshot: Path) -> dict[str, str]:
    return {
        name: hashlib.sha256(path.read_bytes()).hexdigest()
        for name, path in _db_paths(snapshot).items()
    }


def _make_snapshot(
    root: Path,
    *,
    pending_erasure: bool = False,
    completed_without_commit: bool = False,
    orphan_commit: bool = False,
    drop_receipts: bool = False,
    unknown_table: bool = False,
) -> Path:
    snapshot = root / "snapshot"
    paths = _db_paths(snapshot)
    for key, path in paths.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        schema = {
            "control": CONTROL_SCHEMA,
            "sg_history": HISTORY_SCHEMA,
            "beta_history": HISTORY_SCHEMA,
            "sg_memory": MEMORY_SCHEMA,
            "beta_memory": MEMORY_SCHEMA,
        }[key]
        conn = _open(path)
        try:
            conn.executescript(schema)
            conn.execute(
                "INSERT OR REPLACE INTO schema_meta(key,value) VALUES('schema_version','11')"
            )
            conn.commit()
        finally:
            conn.close()

    # --- control -----------------------------------------------------------
    conn = _open(paths["control"])
    try:
        conn.execute(
            "INSERT INTO session_worldlines(session_id,worldline,revision,updated_at_utc)"
            " VALUES('ownerA','steins_gate',1,?)",
            (NOW,),
        )
        conn.execute(
            "INSERT INTO shared_user_facts(owner_session_id,fact_key,fact_value,confidence,"
            "importance,is_current,is_pinned,origin_worldline,origin_conversation_id,"
            "origin_identity_mode,source_message_ids,created_at) VALUES("
            "'ownerA','shared_key','SHARED_MARKER 事实',0.9,0.8,1,0,'steins_gate',"
            "'conv-a-self','self','[101]',?)",
            (NOW,),
        )
        conn.commit()
    finally:
        conn.close()

    # --- history (both worldlines) ------------------------------------------
    for wl_ns, owner, conv, identity, message_id in (
        ("sg", "ownerA", "conv-a-self", "self", 101),
        ("sg", "ownerB", "conv-b-self", "self", 104),
        ("beta", "ownerBeta", "conv-beta", "okabe", 101),
    ):
        conn = _open(paths[f"{wl_ns}_history"])
        try:
            conn.execute(
                "INSERT INTO conversations(id,session_id,title,title_source,is_default,"
                "identity_mode) VALUES(?,?,?,'auto',0,?)",
                (conv, owner, "Conversation", identity),
            )
            conn.execute(
                "INSERT INTO messages(id,session_id,conversation_id,role,content)"
                " VALUES(?,?,?,'user',?)",
                (message_id, owner, conv, "私はアニメが好きです。"),
            )
            # Sequence high-water above the max surviving id: insert then delete.
            conn.execute(
                "INSERT INTO messages(id,session_id,conversation_id,role,content)"
                " VALUES(999,?,?,'user','temporary')",
                (owner, conv),
            )
            conn.execute("DELETE FROM messages WHERE id=999")
            conn.execute(
                "INSERT INTO memory_summaries(session_id,conversation_id,summary)"
                " VALUES(?,?,'要約')",
                (owner, conv),
            )
            # Legal: epoch row for an already-deleted conversation.
            conn.execute(
                "INSERT OR IGNORE INTO conversation_content_epochs(session_id,"
                "conversation_id,epoch) VALUES('deleted-owner','deleted-conv',3)"
            )
            conn.commit()
        finally:
            conn.close()

    # --- memory (SG rich, beta minimal) -------------------------------------
    _fill_memory_db(
        paths["sg_memory"],
        owner="ownerA",
        other_owner="ownerB",
        other_owner_source=104,
        conv_id="conv-a-self",
        identity="self",
        source_message_id=101,
        erasure_kwargs=dict(
            pending=pending_erasure,
            completed_without_commit=completed_without_commit,
            orphan_commit=orphan_commit,
        ),
        drop_receipts=drop_receipts,
        unknown_table=unknown_table,
        rich=True,
    )
    _fill_memory_db(
        paths["beta_memory"],
        owner="ownerBeta",
        other_owner=None,
        other_owner_source=None,
        conv_id="conv-beta",
        identity="okabe",
        source_message_id=101,
        planned_key="beta_fact",
        planned_value="β事实",
        erasure_kwargs=dict(),
        drop_receipts=False,
        unknown_table=False,
        rich=False,
    )
    return snapshot


def _fill_memory_db(
    db_path: Path,
    *,
    owner: str,
    other_owner: str | None,
    other_owner_source: int | None,
    conv_id: str,
    identity: str,
    source_message_id: int,
    erasure_kwargs: dict,
    drop_receipts: bool,
    unknown_table: bool,
    rich: bool,
    planned_key: str = "likes_anime",
    planned_value: str = "喜欢看动漫",
) -> None:
    conn = _open(db_path)
    try:
        # Legacy core facts: planned + dismissed + not-current (+ other owner).
        conn.execute(
            "INSERT INTO core_facts(session_id,identity_mode,fact_key,fact_value,confidence,"
            "importance,is_current,is_pinned,is_dismissed,source_message_ids,created_at)"
            " VALUES(?,?,?,?,0.9,0.85,1,0,0,?,?)",
            (owner, identity, planned_key, planned_value, json.dumps([source_message_id]), NOW),
        )
        conn.execute(
            "INSERT INTO core_facts(session_id,identity_mode,fact_key,fact_value,confidence,"
            "importance,is_current,is_pinned,is_dismissed,source_message_ids,created_at)"
            " VALUES(?,?,'dismissed_fact','已否定的旧事实',0.9,0.85,1,0,1,?,?)",
            (owner, identity, json.dumps([source_message_id]), NOW),
        )
        conn.execute(
            "INSERT INTO core_facts(session_id,identity_mode,fact_key,fact_value,confidence,"
            "importance,is_current,is_pinned,is_dismissed,source_message_ids,created_at)"
            " VALUES(?,?,'old_fact','历史旧事实',0.9,0.85,0,0,0,?,?)",
            (owner, identity, json.dumps([source_message_id]), NOW),
        )
        # Sequence high-water above surviving max id.
        conn.execute(
            "INSERT INTO core_facts(id,session_id,identity_mode,fact_key,fact_value,confidence,"
            "importance,is_current,is_pinned,is_dismissed,source_message_ids,created_at)"
            " VALUES(777,?,'self','temp','t',0.9,0.85,1,0,0,'[101]',?)",
            (owner, NOW),
        )
        conn.execute("DELETE FROM core_facts WHERE id=777")
        if other_owner:
            conn.execute(
                "INSERT INTO core_facts(session_id,identity_mode,fact_key,fact_value,confidence,"
                "importance,is_current,is_pinned,is_dismissed,source_message_ids,created_at)"
                " VALUES(?,?,'other_owner_fact','他人事实',0.9,0.85,1,0,0,?,?)",
                (other_owner, identity, json.dumps([other_owner_source]), NOW),
            )
        # Legacy episodic + FTS + embeddings + emotion.
        conn.execute(
            "INSERT INTO episodic_memories(session_id,identity_mode,content,confidence,"
            "importance,source_message_ids,created_at) VALUES(?,?,"
            "'ako-note-legacy-marker を整理した記録',0.9,0.7,'[101]',?)",
            (owner, identity, NOW),
        )
        conn.execute(
            "INSERT INTO memory_embeddings(memory_type,memory_id,model,dimensions,vector)"
            " VALUES('episodic',1,'intfloat/multilingual-e5-small',384,?)",
            (struct.pack("<384f", *([0.5] * 384)),),
        )
        conn.execute(
            "INSERT INTO emotion_states(session_id,identity_mode,trust,attachment,irritation,"
            "anxiety,jealousy,volatility,updated_at_utc) VALUES(?,?,0.6,0.5,0.1,0.1,0.1,"
            "0.1,?)",
            (owner, identity, NOW),
        )
        # Erasure metadata: completed request r1 with matching history commit.
        conn.execute(
            "INSERT INTO conversation_erasure_requests(request_id,session_id,identity_mode,"
            "conversation_id,action,forget_long_term,source_message_ids,state,created_at)"
            " VALUES('r1',?,?,?,'forget',1,'[102]','completed',?)",
            (owner, identity, conv_id, NOW),
        )
        if erasure_kwargs.get("pending"):
            conn.execute(
                "INSERT INTO conversation_erasure_requests(request_id,session_id,identity_mode,"
                "conversation_id,action,forget_long_term,source_message_ids,state,created_at)"
                " VALUES('r2',?,?,?,'delete',1,'[102]','pending',?)",
                (owner, identity, conv_id, NOW),
            )
        if erasure_kwargs.get("completed_without_commit"):
            conn.execute(
                "INSERT INTO conversation_erasure_requests(request_id,session_id,identity_mode,"
                "conversation_id,action,forget_long_term,source_message_ids,state,created_at)"
                " VALUES('r3',?,?,?,'delete',1,'[103]','completed',?)",
                (owner, identity, conv_id, NOW),
            )
        # Tombstone.
        conn.execute(
            "INSERT INTO memory_tombstones(tombstone_id,session_id,identity_mode,fact_id,"
            "source_fingerprint,semantic_fingerprint,reason,deleted_at) VALUES("
            "'t1',?,?, 'legacy-1','fp-old',NULL,'user_delete',?)",
            (owner, identity, NOW),
        )
        # Receipts (existing, must be preserved verbatim).
        if not drop_receipts:
            conn.execute(
                "INSERT INTO memory_ingest_receipts(session_id,conversation_id,identity_mode,"
                "source_message_id,pipeline_version,receipt_kind,created_at) VALUES("
                "?,?,'self',55,'memory-v11-1','provider_empty',?)",
                (owner, conv_id, NOW),
            )
        # Jobs across the lifecycle (pending WITHOUT receipt is normal).
        # Distinct source cursors: the jobs unique index covers them.
        jobs = [
            ("j-pending", "pending", 1, None, None, 7),
            ("j-processing", "processing", 1, "worker-x", NOW, 8),
            ("j-failed-retry", "failed", 1, None, None, 9),
            ("j-failed-perm", "failed", 0, None, None, 10),
            ("j-completed", "completed", 1, None, None, 42),
            ("j-cancelled", "cancelled", 1, None, None, 11),
        ]
        for job_id, state, retryable, lease_owner, lease_expires, source_id in jobs:
            conn.execute(
                "INSERT INTO memory_ingest_jobs(job_id,session_id,conversation_id,"
                "identity_mode,source_message_id,pipeline_version,provider_id,model_id,"
                "state,attempt_count,retryable,next_attempt_at,lease_owner,"
                "lease_expires_at,last_error_code,last_error_hash,created_at,updated_at,"
                "completed_at) VALUES(?,?,?,?,?, 'memory-v11-1','deepseek','m',?,1,?,?,?,?,"
                "NULL,NULL,?,?,NULL)",
                (
                    job_id, owner, conv_id, identity, source_id,
                    state, retryable, None, lease_owner, lease_expires, NOW, NOW,
                ),
            )
        if rich:
            # Demo v11 content that must NOT survive into the candidate.
            conn.execute(
                "INSERT INTO memory_observations(observation_id,session_id,identity_mode,"
                "display_text,semantic_json,semantic_fingerprint,evidence_kind,memory_class,"
                "confidence,status,extractor_version,reanalysis_count,expires_at,created_at,"
                "updated_at) VALUES('obs-demo',?,?,'DEMO_MARKER 演示密度事实','{}','fpd',"
                "'direct_user','stable_candidate',0.5,'attached','seed',0,NULL,?,?)",
                (owner, identity, NOW, NOW),
            )
            conn.execute(
                "INSERT INTO memory_observations(observation_id,session_id,identity_mode,"
                "display_text,semantic_json,semantic_fingerprint,evidence_kind,memory_class,"
                "confidence,status,extractor_version,reanalysis_count,expires_at,created_at,"
                "updated_at) VALUES('obs-exp',?,?,'旧经历','{}','fpe','direct_user',"
                "'episodic',0.9,'attached','seed',0,NULL,?,?)",
                (owner, identity, NOW, NOW),
            )
            conn.execute(
                "INSERT INTO stable_facts(fact_id,session_id,identity_mode,topic_id,state,"
                "active_version,is_pinned,created_at,updated_at,deleted_at) VALUES("
                "'fact-demo',?,?,NULL,'active',1,0,?,?,NULL)",
                (owner, identity, NOW, NOW),
            )
            conn.execute(
                "INSERT INTO stable_fact_versions(fact_id,version_no,display_text,"
                "semantic_json,semantic_fingerprint,confidence,change_kind,previous_version,"
                "valid_from,invalid_at,created_by_job_id) VALUES('fact-demo',1,"
                "'DEMO_MARKER 演示密度事实','{\"object\":{\"demo\":\"v11-density\"}}','fpv',"
                "0.5,'create',NULL,?,NULL,NULL)",
                (NOW,),
            )
            conn.execute(
                "INSERT INTO stable_fact_evidence(fact_id,version_no,observation_id,"
                "evidence_role) VALUES('fact-demo',1,'obs-demo','primary')"
            )
            conn.execute(
                "INSERT INTO experiences(experience_id,session_id,conversation_id,"
                "identity_mode,observation_id,display_text,semantic_json,"
                "semantic_fingerprint,confidence,status,expires_at,created_at,updated_at,"
                "deleted_at,is_pinned) VALUES('exp-old',?,?,?,?,'旧经历','{}','fpx',0.9,"
                "'active',NULL,?,?,NULL,0)",
                (owner, conv_id, identity, "obs-exp", NOW, NOW),
            )
            conn.execute(
                "INSERT INTO memory_topics(topic_id,session_id,identity_mode,normalized_label,"
                "display_label,created_at,updated_at) VALUES('topic-1',?,?, 'demo',"
                "'演示',?,?)",
                (owner, identity, NOW, NOW),
            )
            conn.execute(
                "INSERT INTO memory_topic_aliases(topic_id,session_id,identity_mode,"
                "normalized_alias) VALUES('topic-1',?,?,'演示别名')",
                (owner, identity),
            )
            conn.execute(
                "INSERT INTO memory_consolidation_proposals(proposal_id,session_id,"
                "identity_mode,survivor_fact_ids,duplicate_fact_ids,reason_codes,preview_json,"
                "snapshot_hash,status,created_at,updated_at) VALUES('prop-1',?,?, '[]',"
                "'[]','[]','{}','fph','open',?,?)",
                (owner, identity, NOW, NOW),
            )
            # Old import ledger (stale reference into unloaded facts).
            conn.execute(mig._LEGACY_IMPORTS_TABLE_DDL)
            conn.execute(mig._LEGACY_IMPORTS_INDEX_DDL)
            conn.execute(
                "INSERT INTO legacy_core_fact_imports(import_key,source_id,worldline,"
                "session_id,identity_mode,legacy_row_id,fact_id,observation_id,imported_at)"
                " VALUES('stale-key','stale-source','steins_gate',?,?,99,'fact-demo',"
                "'obs-demo',?)",
                (owner, identity, NOW),
            )
        if unknown_table:
            conn.execute("CREATE TABLE unknown_business_table(id INTEGER)")
        conn.commit()
    finally:
        conn.close()
    # vec0 tables (legacy index family) when sqlite_vec is available.
    try:
        import sqlite_vec  # noqa: F401

        conn = _open(db_path)
        try:
            conn.enable_load_extension(True)
            sqlite_vec.load(conn)
            for table in ("episodic_vec", "core_vec"):
                conn.execute(
                    f"CREATE VIRTUAL TABLE IF NOT EXISTS {table} "
                    "USING vec0(embedding float[384])"
                )
            conn.execute(
                "INSERT INTO episodic_vec(rowid, embedding) VALUES (1, ?)",
                (struct.pack("<384f", *([0.25] * 384)),),
            )
            conn.commit()
        finally:
            conn.close()
    except ImportError:
        pass
    # History-side erasure commit matching request r1 (and variants).
    history = db_path.parent / "history.sqlite3"
    conn = _open(history)
    try:
        conn.execute("INSERT INTO conversation_erasure_commits(request_id) VALUES('r1')")
        if erasure_kwargs.get("orphan_commit"):
            conn.execute(
                "INSERT INTO conversation_erasure_commits(request_id) VALUES('r-orphan')"
            )
        conn.commit()
    finally:
        conn.close()


def _make_preview(snapshot: Path, out_dir: Path) -> Path:
    preview = mig.build_legacy_core_facts_preview(snapshot)
    out_dir.mkdir(parents=True, exist_ok=True)
    preview_path = out_dir / "preview.json"
    preview_path.write_text(
        json.dumps(preview, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return preview_path


def _row_tuples(db_path: Path, table: str, order: str = "1") -> list[tuple]:
    conn = _open(db_path)
    try:
        return [tuple(r) for r in conn.execute(f"SELECT * FROM {table} ORDER BY {order}")]
    finally:
        conn.close()


def _count(db_path: Path, table: str) -> int:
    conn = _open(db_path)
    try:
        return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
    finally:
        conn.close()


def _sqlite_sequence(db_path: Path) -> dict[str, int]:
    conn = _open(db_path)
    try:
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='sqlite_sequence'"
        ).fetchone()
        if exists is None:
            return {}
        return {
            str(r[0]): int(r[1])
            for r in conn.execute("SELECT name, seq FROM sqlite_sequence")
        }
    finally:
        conn.close()


def _table_names(db_path: Path) -> set[str]:
    conn = _open(db_path)
    try:
        return {
            str(r[0])
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    finally:
        conn.close()


def _index_family_counts(db_path: Path) -> dict[str, int]:
    """Non-virtual (shadow) tables of the FTS/vec0 legacy index family."""
    conn = _open(db_path)
    try:
        counts: dict[str, int] = {}
        for name, sql_text in conn.execute(
            "SELECT name, sql FROM sqlite_master WHERE type='table'"
        ):
            table_name = str(name)
            if not table_name.startswith(
                ("episodic_fts", "episodic_vec", "core_vec", "memory_vec")
            ):
                continue
            if sql_text and str(sql_text).upper().startswith("CREATE VIRTUAL"):
                continue
            counts[table_name] = int(
                conn.execute(f'SELECT COUNT(*) FROM "{table_name}"').fetchone()[0]
            )
        return counts
    finally:
        conn.close()


def _run_cli(
    *argv: str,
    env_extra: dict[str, str] | None = None,
    drop_env: tuple[str, ...] = (),
):
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in ("AMADEUS_DATA_DIR", "AMADEUS_DB_PATH", *drop_env)
    }
    env["PYTHON_DOTENV_DISABLED"] = "1"
    env["PYTHONPATH"] = str(BACKEND_ROOT)
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [sys.executable, str(SCRIPT), *argv],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        cwd=str(BACKEND_ROOT),
    )


def test_app_module_resolves_to_this_worktree():
    import app

    assert Path(app.__file__).resolve() == BACKEND_ROOT / "app" / "__init__.py"


# ---------------------------------------------------------------------------
# 1. Full build: preservation + rebuild + freeze.
# ---------------------------------------------------------------------------


def test_candidate_build_preserves_legacy_and_rebuilds_v11(tmp_path):
    snapshot = _make_snapshot(tmp_path)
    preview_path = _make_preview(snapshot, tmp_path / "inputs")
    output = tmp_path / "candidate"
    before = _snapshot_state(snapshot)

    result = _run_cli(
        "--snapshot", str(snapshot), "--preview", str(preview_path), "--out", str(output)
    )
    assert result.returncode == 0, result.stderr

    paths = _db_paths(output)
    assert all(path.is_file() for path in paths.values())
    assert (output / "manifest.json").is_file()
    marker = output / ".migration-v11.complete"
    assert marker.is_file()
    assert json.loads(marker.read_text(encoding="utf-8"))["schema_version"] == 11
    # Source untouched.
    assert _snapshot_state(snapshot) == before

    # --- legacy preserved verbatim (memory) --------------------------------
    for table in (
        "core_facts",
        "episodic_memories",
        "memory_embeddings",
        "emotion_states",
        "conversation_erasure_requests",
        "memory_tombstones",
        "memory_ingest_receipts",
    ):
        assert _row_tuples(paths["sg_memory"], table) == _row_tuples(
            _db_paths(snapshot)["sg_memory"], table
        ), table
    # FTS still usable and matching the legacy content.
    conn = _open(paths["sg_memory"])
    try:
        hits = conn.execute(
            "SELECT rowid FROM episodic_fts WHERE episodic_fts MATCH 'marker'"
        ).fetchall()
        assert [int(r[0]) for r in hits] == [1]
    finally:
        conn.close()
    # vec0 index family tables preserved with identical shadow counts.
    assert _index_family_counts(paths["sg_memory"]) == _index_family_counts(
        _db_paths(snapshot)["sg_memory"]
    )

    # --- history/control preserved incl. sequence high-water ---------------
    for key in ("sg_history", "beta_history"):
        for table in (
            "conversations",
            "messages",
            "memory_summaries",
            "conversation_content_epochs",
            "conversation_erasure_commits",
        ):
            assert _row_tuples(paths[key], table) == _row_tuples(
                _db_paths(snapshot)[key], table
            ), (key, table)
        assert _sqlite_sequence(paths[key]) == _sqlite_sequence(_db_paths(snapshot)[key])
    for table in (
        "session_worldlines",
        "session_conversation_selections",
        "search_usage",
        "provider_configurations",
        "shared_user_facts",
    ):
        assert _row_tuples(paths["control"], table) == _row_tuples(
            _db_paths(snapshot)["control"], table
        ), table
    # High-water above the surviving max id really is preserved.
    assert _sqlite_sequence(paths["sg_history"]).get("messages", 0) >= 999

    # --- v11 content: ONLY the donor imports -------------------------------
    assert _count(paths["sg_memory"], "stable_facts") == 2
    assert _count(paths["sg_memory"], "stable_fact_versions") == 2
    assert _count(paths["sg_memory"], "stable_fact_evidence") == 2
    assert _count(paths["sg_memory"], "memory_observations") == 2
    assert _count(paths["sg_memory"], "memory_observation_sources") == 2
    assert _count(paths["beta_memory"], "stable_facts") == 1
    for db in (paths["sg_memory"], paths["beta_memory"]):
        assert _count(db, "experiences") == 0
        assert _count(db, "memory_topics") == 0
        assert _count(db, "memory_topic_aliases") == 0
        assert _count(db, "memory_consolidation_proposals") == 0
    # Demo marker is gone from every v11 content table; imports are legacy_import.
    conn = _open(paths["sg_memory"])
    try:
        everything = json.dumps(
            [
                [tuple(r) for r in conn.execute("SELECT * FROM stable_fact_versions")],
                [tuple(r) for r in conn.execute("SELECT * FROM memory_observations")],
                [tuple(r) for r in conn.execute("SELECT * FROM memory_observation_sources")],
            ],
            ensure_ascii=False,
            default=lambda v: v.hex() if isinstance(v, bytes) else str(v),
        )
        assert "DEMO_MARKER" not in everything
        kinds = {
            str(r[0])
            for r in conn.execute("SELECT change_kind FROM stable_fact_versions")
        }
        assert kinds == {"legacy_import"}
        # Old ledger replaced by THIS build's rows (no stale-key).
        ledger_keys = {
            str(r[0]) for r in conn.execute("SELECT import_key FROM legacy_core_fact_imports")
        }
        assert "stale-key" not in ledger_keys
        assert len(ledger_keys) == 2
    finally:
        conn.close()

    # --- jobs frozen --------------------------------------------------------
    conn = _open(paths["sg_memory"])
    try:
        frozen = {
            str(r["job_id"]): dict(r)
            for r in conn.execute("SELECT * FROM memory_ingest_jobs")
        }
    finally:
        conn.close()
    for job_id in ("j-pending", "j-processing", "j-failed-retry"):
        row = frozen[job_id]
        assert row["state"] == "cancelled", job_id
        assert int(row["retryable"]) == 0, job_id
        assert row["lease_owner"] is None and row["next_attempt_at"] is None
        assert row["last_error_code"] == "candidate_build_freeze"
    for job_id in ("j-failed-perm", "j-completed", "j-cancelled"):
        assert frozen[job_id]["state"] != "cancelled" or job_id == "j-cancelled"
        assert int(frozen[job_id]["retryable"]) == (0 if job_id == "j-failed-perm" else 1)

    # --- manifest ------------------------------------------------------------
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["kind"] == cand.CANDIDATE_KIND
    assert manifest["status"] == "valid"
    assert manifest["builder_version"] == cand.BUILDER_VERSION
    assert manifest["source"]["source_id"] == json.loads(
        preview_path.read_text(encoding="utf-8")
    )["source"]["source_id"]
    assert len(manifest["imported_facts"]) == 3
    assert manifest["jobs"]["steins_gate"]["before"]["pending"] == 1
    assert manifest["jobs"]["steins_gate"]["after"]["cancelled"] == 4
    assert manifest["erasure"]["steins_gate"]["matched"] is True
    assert manifest["erasure"]["steins_gate"]["pending"] == 0
    assert manifest["validation"]["ok"] is True
    not_loaded = manifest["not_loaded"]["items"]
    assert {item["reason"] for item in not_loaded} == {"dismissed", "not_current"}
    assert manifest["legacy_ledger"]["source_rows"] == 1
    assert manifest["legacy_ledger"]["candidate_rows"] == 3


# ---------------------------------------------------------------------------
# 2. Rejections (source stays unchanged, nothing published).
# ---------------------------------------------------------------------------


def _assert_rejected(tmp_path, *, code, snapshot_kwargs=None, mutate=None):
    """Build must be rejected with `code`; source unchanged; nothing published.

    `mutate` runs BEFORE the preview is generated so mutations that change
    the migration identity do not shadow the targeted check with
    preview_stale (identity is verified separately).
    """
    snapshot = _make_snapshot(tmp_path, **(snapshot_kwargs or {}))
    if mutate is not None:
        mutate(snapshot)
    preview_path = _make_preview(snapshot, tmp_path / "inputs")
    output = tmp_path / "candidate"
    before = _snapshot_state(snapshot)
    result = _run_cli(
        "--snapshot", str(snapshot), "--preview", str(preview_path), "--out", str(output)
    )
    assert result.returncode == 2, result.stdout + result.stderr
    assert f"[{code}]" in result.stderr, result.stderr
    assert not output.exists()
    assert _snapshot_state(snapshot) == before
    # Early rejections never created a staging tree.
    assert not list(tmp_path.glob(".candidate.staging-*"))
    return result


def test_reject_missing_memory_file(tmp_path):
    snapshot = _make_snapshot(tmp_path)
    preview_path = _make_preview(snapshot, tmp_path / "inputs")
    before = _snapshot_state(snapshot)
    (snapshot / "worldlines" / "beta" / "memory.sqlite3").unlink()
    output = tmp_path / "candidate"
    result = _run_cli(
        "--snapshot", str(snapshot), "--preview", str(preview_path), "--out", str(output)
    )
    assert result.returncode == 2
    assert "[invalid_source_layout]" in result.stderr
    assert not output.exists()
    after = {
        name: hashlib.sha256(path.read_bytes()).hexdigest()
        for name, path in _db_paths(snapshot).items()
        if path.is_file()
    }
    assert after == {k: v for k, v in before.items() if k != "beta_memory"}


def test_reject_pending_erasure(tmp_path):
    _assert_rejected(
        tmp_path, code="pending_erasure", snapshot_kwargs={"pending_erasure": True}
    )


def test_reject_completed_request_without_history_commit(tmp_path):
    _assert_rejected(
        tmp_path,
        code="missing_history_commit",
        snapshot_kwargs={"completed_without_commit": True},
    )


def test_reject_orphan_history_commit(tmp_path):
    def mutate(snapshot: Path):
        conn = _open(_db_paths(snapshot)["sg_history"])
        try:
            conn.execute(
                "INSERT INTO conversation_erasure_commits(request_id) VALUES('r-orphan')"
            )
            conn.commit()
        finally:
            conn.close()

    _assert_rejected(tmp_path, code="orphan_history_commit", mutate=mutate)


def test_reject_missing_receipts_table(tmp_path):
    def mutate(snapshot: Path):
        conn = _open(_db_paths(snapshot)["sg_memory"])
        try:
            conn.execute("DROP TABLE memory_ingest_receipts")
            conn.commit()
        finally:
            conn.close()

    _assert_rejected(tmp_path, code="schema_incompatible", mutate=mutate)


def test_reject_unknown_table(tmp_path):
    _assert_rejected(
        tmp_path, code="unknown_table", snapshot_kwargs={"unknown_table": True}
    )


def test_reject_stale_preview(tmp_path):
    snapshot = _make_snapshot(tmp_path)
    preview_path = _make_preview(snapshot, tmp_path / "inputs")
    conn = _open(_db_paths(snapshot)["sg_memory"])
    try:
        conn.execute(
            "INSERT INTO core_facts(session_id,identity_mode,fact_key,fact_value,confidence,"
            "importance,is_current,is_pinned,is_dismissed,source_message_ids,created_at)"
            " VALUES('ownerA','self','new_fact','新事实',0.9,0.85,1,0,0,'[101]',?)",
            (NOW,),
        )
        conn.commit()
    finally:
        conn.close()
    before = _snapshot_state(snapshot)
    output = tmp_path / "candidate"
    result = _run_cli(
        "--snapshot", str(snapshot), "--preview", str(preview_path), "--out", str(output)
    )
    assert result.returncode == 2
    assert "preview_stale" in result.stderr
    assert not output.exists()
    assert _snapshot_state(snapshot) == before


# ---------------------------------------------------------------------------
# 3. Frozen jobs under the REAL jobs API.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_frozen_jobs_not_schedulable_via_real_jobs_api(tmp_path, monkeypatch):
    from app.db import reset_initialization_cache
    from app.services.memory_v11 import jobs as jobs_module

    snapshot = _make_snapshot(tmp_path)
    preview_path = _make_preview(snapshot, tmp_path / "inputs")
    output = tmp_path / "candidate"
    result = cand.build_memory_candidate(
        snapshot_dir=snapshot, preview_path=preview_path, output_dir=output
    )
    assert result["ok"] is True

    monkeypatch.setenv("AMADEUS_DATA_DIR", str(output))
    reset_initialization_cache()

    assert await jobs_module.list_due_job_ids(worldline=SG) == []
    assert await jobs_module.list_due_job_ids(worldline=BETA) == []

    spy_calls = {"n": 0}

    async def spy_completion(**kwargs):
        spy_calls["n"] += 1
        return {"observations": [], "operations": []}

    processed = await jobs_module.process_due_jobs_once(
        lease_owner="probe", memory_completion=spy_completion
    )
    assert processed == []
    assert spy_calls["n"] == 0

    # Manual retry cannot reactivate a frozen (originally retryable) job.
    conn = _open(_db_paths(output)["sg_memory"])
    try:
        job = conn.execute(
            "SELECT session_id FROM memory_ingest_jobs WHERE job_id='j-failed-retry'"
        ).fetchone()
        session_id = str(job["session_id"])
    finally:
        conn.close()
    retried = await jobs_module.retry_memory_job(
        job_id="j-failed-retry", session_id=session_id, worldline=SG
    )
    assert retried["state"] == "cancelled"
    assert await jobs_module.list_due_job_ids(worldline=SG) == []
    reset_initialization_cache()


# ---------------------------------------------------------------------------
# 4. Candidate scope isolation via the REAL retrieval path.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_candidate_scope_isolation_via_real_retrieval(tmp_path, monkeypatch):
    from app.db import reset_initialization_cache
    from app.services.memory_v11.retrieval import select_stable_fact_candidates

    snapshot = _make_snapshot(tmp_path)
    preview_path = _make_preview(snapshot, tmp_path / "inputs")
    output = tmp_path / "candidate"
    assert cand.build_memory_candidate(
        snapshot_dir=snapshot, preview_path=preview_path, output_dir=output
    )["ok"]

    monkeypatch.setenv("AMADEUS_DATA_DIR", str(output))
    reset_initialization_cache()
    candidates = await select_stable_fact_candidates(
        session_id="ownerA", worldline=SG, identity_mode="self", query="动漫"
    )
    assert [c["display_text"] for c in candidates] == ["likes_anime: 喜欢看动漫"]
    assert all("DEMO_MARKER" not in c["display_text"] for c in candidates)
    assert await select_stable_fact_candidates(
        session_id="ownerA", worldline=SG, identity_mode="okabe", query="动漫"
    ) == []
    beta_candidates = await select_stable_fact_candidates(
        session_id="ownerBeta", worldline=BETA, identity_mode="okabe", query="事实"
    )
    assert [c["display_text"] for c in beta_candidates] == ["beta_fact: β事实"]
    reset_initialization_cache()


# ---------------------------------------------------------------------------
# 5. Failure semantics, determinism, output protection.
# ---------------------------------------------------------------------------


def test_failure_keeps_incomplete_staging_and_publishes_nothing(tmp_path, monkeypatch):
    snapshot = _make_snapshot(tmp_path)
    preview_path = _make_preview(snapshot, tmp_path / "inputs")
    output = tmp_path / "candidate"

    def boom(*args, **kwargs):
        raise RuntimeError("injected transfer failure")

    monkeypatch.setattr(cand, "_transfer_donor_content", boom)
    with pytest.raises(cand.CandidateBuildError) as excinfo:
        cand.build_memory_candidate(
            snapshot_dir=snapshot, preview_path=preview_path, output_dir=output
        )
    assert excinfo.value.code == "build_failed"
    assert not output.exists()
    stagings = list(tmp_path.glob(".candidate.staging-*"))
    assert len(stagings) == 1
    incomplete = json.loads((stagings[0] / "INCOMPLETE.json").read_text(encoding="utf-8"))
    assert incomplete["code"] == "build_failed"
    assert "injected transfer failure" in incomplete["message"]
    assert not list(tmp_path.glob(".candidate.donor-*"))
    monkeypatch.undo()
    # A retry to a fresh output succeeds (staging is per-build).
    output2 = tmp_path / "candidate2"
    assert cand.build_memory_candidate(
        snapshot_dir=snapshot, preview_path=preview_path, output_dir=output2
    )["ok"]


def test_rebuild_to_two_outputs_has_identical_content_sets(tmp_path):
    snapshot = _make_snapshot(tmp_path)
    preview_path = _make_preview(snapshot, tmp_path / "inputs")
    out1 = tmp_path / "candidate-a"
    out2 = tmp_path / "candidate-b"
    assert cand.build_memory_candidate(
        snapshot_dir=snapshot, preview_path=preview_path, output_dir=out1
    )["ok"]
    assert cand.build_memory_candidate(
        snapshot_dir=snapshot, preview_path=preview_path, output_dir=out2
    )["ok"]
    for key in ("sg_memory", "beta_memory"):
        tables = (
            "stable_facts",
            "stable_fact_versions",
            "stable_fact_evidence",
            "memory_observations",
            "memory_observation_sources",
            "legacy_core_fact_imports",
            "memory_ingest_jobs",
            "core_facts",
        )
        for table in tables:
            assert _count(_db_paths(out1)[key], table) == _count(_db_paths(out2)[key], table)
        texts1 = sorted(
            str(r[0])
            for r in _open(_db_paths(out1)[key]).execute(
                "SELECT display_text FROM stable_fact_versions"
            )
        )
        texts2 = sorted(
            str(r[0])
            for r in _open(_db_paths(out2)[key]).execute(
                "SELECT display_text FROM stable_fact_versions"
            )
        )
        assert texts1 == texts2
    ids1 = {
        str(r[0])
        for r in _open(_db_paths(out1)["sg_memory"]).execute(
            "SELECT fact_id FROM stable_facts"
        )
    }
    ids2 = {
        str(r[0])
        for r in _open(_db_paths(out2)["sg_memory"]).execute(
            "SELECT fact_id FROM stable_facts"
        )
    }
    assert len(ids1) == len(ids2)  # sets equal in size; UUIDs may differ


def test_cli_refuses_existing_output_and_env_and_alias(tmp_path):
    snapshot = _make_snapshot(tmp_path)
    preview_path = _make_preview(snapshot, tmp_path / "inputs")

    # Existing output is never overwritten.
    output = tmp_path / "candidate"
    output.mkdir()
    (output / "keep.txt").write_text("keep", encoding="utf-8")
    result = _run_cli(
        "--snapshot", str(snapshot), "--preview", str(preview_path), "--out", str(output)
    )
    assert result.returncode == 2
    assert "output_exists" in result.stderr
    assert (output / "keep.txt").read_text(encoding="utf-8") == "keep"

    # Env overrides refused.
    result = _run_cli(
        "--snapshot", str(snapshot), "--preview", str(preview_path),
        "--out", str(tmp_path / "c2"),
        env_extra={"AMADEUS_DATA_DIR": str(tmp_path)},
    )
    assert result.returncode == 2
    assert "env_override_present" in result.stderr

    # Output nested inside the snapshot refused.
    result = _run_cli(
        "--snapshot", str(snapshot), "--preview", str(preview_path),
        "--out", str(snapshot / "inner"),
    )
    assert result.returncode == 2
    assert "overlapping_paths" in result.stderr

    # Output aliased into the snapshot through a junction refused.
    if os.name == "nt":
        link = tmp_path / "link-out"
        created = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(snapshot)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if created.returncode == 0:
            result = _run_cli(
                "--snapshot", str(snapshot), "--preview", str(preview_path),
                "--out", str(link / "cand"),
            )
            assert result.returncode == 2
            assert "overlapping_paths" in result.stderr

    # Output inside a live APPDATA root refused.
    fake_appdata = tmp_path / "fake-appdata"
    (fake_appdata / "Amadeus").mkdir(parents=True)
    result = _run_cli(
        "--snapshot", str(snapshot), "--preview", str(preview_path),
        "--out", str(fake_appdata / "Amadeus" / "candidate"),
        env_extra={"APPDATA": str(fake_appdata)},
    )
    assert result.returncode == 2
    assert "output_path_forbidden" in result.stderr


# ---------------------------------------------------------------------------
# 6. No credentials / no app startup side effects (source-level guard).
# ---------------------------------------------------------------------------


def test_builder_sources_avoid_forbidden_imports():
    """Guard: the builder only uses side-effect-free app modules.

    The check is import-statement level (docstrings legitimately mention the
    forbidden modules to explain the discipline).
    """
    forbidden = ("app.main", "provider_runtime", "app.services.credentials", "init_db")
    for path in (
        BACKEND_ROOT / "app" / "services" / "memory_v11" / "candidate.py",
        BACKEND_ROOT / "scripts" / "build_memory_candidate.py",
    ):
        source = path.read_text(encoding="utf-8")
        for line in source.splitlines():
            stripped = line.strip()
            if not (stripped.startswith("import ") or stripped.startswith("from ")):
                continue
            for token in forbidden:
                assert token not in stripped, (path.name, stripped)


# ---------------------------------------------------------------------------
# Repair counterexamples (WAL stability, historical memory_vec, preview paths).
# ---------------------------------------------------------------------------


_WAL_COMMIT_SQL: dict[str, str] = {
    "control": "UPDATE session_worldlines SET revision = revision + 1",
    "sg_history": "UPDATE memory_summaries SET summary = summary || '-wal-commit'",
    "beta_history": "UPDATE memory_summaries SET summary = summary || '-wal-commit'",
    "sg_memory": "UPDATE emotion_states SET trust = MIN(1.0, trust + 0.01)",
    "beta_memory": "UPDATE emotion_states SET trust = MIN(1.0, trust + 0.01)",
}

_MEMORY_VEC_ROWID = 42
_MEMORY_VEC_VALUES = tuple(0.125 * ((i % 8) + 1) for i in range(384))


def _memory_vec_blob() -> bytes:
    return struct.pack("<384f", *_MEMORY_VEC_VALUES)


def _pin_wal(db_path: Path) -> sqlite3.Connection:
    """Hold a read transaction so a later COMMIT can stay uncheckpointed."""
    conn = sqlite3.connect(str(db_path.resolve()), isolation_level=None)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA wal_autocheckpoint=0")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("BEGIN")
    conn.execute("SELECT 1 FROM sqlite_master").fetchone()
    return conn


def _install_historical_memory_vec(db_path: Path) -> bytes:
    sqlite_vec = pytest.importorskip("sqlite_vec")
    blob = _memory_vec_blob()
    conn = _open(db_path)
    try:
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.execute("CREATE VIRTUAL TABLE memory_vec USING vec0(embedding float[384])")
        conn.execute(
            "INSERT INTO memory_vec(rowid, embedding) VALUES (?, ?)",
            (_MEMORY_VEC_ROWID, blob),
        )
        conn.commit()
    finally:
        conn.close()
    return blob


def _load_vec_conn(db_path: Path) -> sqlite3.Connection:
    sqlite_vec = pytest.importorskip("sqlite_vec")
    conn = _open(db_path)
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    return conn


@pytest.mark.parametrize("db_key", list(_WAL_COMMIT_SQL))
def test_wal_commit_during_build_refuses_publish(tmp_path, monkeypatch, db_key):
    """A WAL-only logical COMMIT on any of the five source DBs must fail closed.

    Anti-example: after backup, another connection updates the source and
    commits without checkpointing. The main ``.sqlite3`` SHA256 is unchanged,
    so a main-file-only digest would still publish an old candidate.
    """
    snapshot = _make_snapshot(tmp_path)
    preview_path = _make_preview(snapshot, tmp_path / "inputs")
    output = tmp_path / "candidate"
    db_path = _db_paths(snapshot)[db_key]
    pin = _pin_wal(db_path)
    try:
        main_before = hashlib.sha256(db_path.read_bytes()).hexdigest()
        original_backup = cand._backup_databases

        def backup_then_wal_commit(paths, staging):
            original_backup(paths, staging)
            writer = sqlite3.connect(str(db_path.resolve()), isolation_level=None)
            try:
                writer.execute("PRAGMA journal_mode=WAL")
                writer.execute("PRAGMA wal_autocheckpoint=0")
                writer.execute(_WAL_COMMIT_SQL[db_key])
            finally:
                writer.close()
            assert hashlib.sha256(db_path.read_bytes()).hexdigest() == main_before
            wal_path = Path(str(db_path) + "-wal")
            assert wal_path.is_file() and wal_path.stat().st_size > 0

        monkeypatch.setattr(cand, "_backup_databases", backup_then_wal_commit)
        with pytest.raises(cand.CandidateBuildError) as excinfo:
            cand.build_memory_candidate(
                snapshot_dir=snapshot, preview_path=preview_path, output_dir=output
            )
        assert excinfo.value.code == "source_changed"
        assert not output.exists()
        stagings = list(tmp_path.glob(".candidate.staging-*"))
        assert len(stagings) == 1
        incomplete = json.loads(
            (stagings[0] / "INCOMPLETE.json").read_text(encoding="utf-8")
        )
        assert incomplete["kind"] == cand.INCOMPLETE_KIND
        assert incomplete["code"] == "source_changed"
        assert hashlib.sha256(db_path.read_bytes()).hexdigest() == main_before
    finally:
        pin.close()


def test_wal_mode_sources_without_commit_still_publish(tmp_path):
    """WAL-mode snapshots with no logical COMMIT must still publish.

    Opening a read-only connection on a WAL database can create an empty
    ``-wal`` sidecar. Treating that as a source change would reject a
    stable input.
    """
    snapshot = _make_snapshot(tmp_path)
    for path in _db_paths(snapshot).values():
        conn = sqlite3.connect(str(path.resolve()), isolation_level=None)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA wal_autocheckpoint=0")
        finally:
            conn.close()
    preview_path = _make_preview(snapshot, tmp_path / "inputs")
    output = tmp_path / "candidate"
    before = _snapshot_state(snapshot)
    result = cand.build_memory_candidate(
        snapshot_dir=snapshot, preview_path=preview_path, output_dir=output
    )
    assert result["ok"] is True
    assert (output / "manifest.json").is_file()
    assert _snapshot_state(snapshot) == before


def test_historical_memory_vec_family_is_preserved_and_queryable(tmp_path):
    """Legal historical ``memory_vec`` + shadow tables must be kept, not unknown_table.

    Empty tables or equal shadow counts are not enough: a distinctive non-zero
    vector must survive whole-DB backup and remain readable/queryable.
    """
    snapshot = _make_snapshot(tmp_path)
    blob = _install_historical_memory_vec(_db_paths(snapshot)["sg_memory"])
    preview_path = _make_preview(snapshot, tmp_path / "inputs")
    output = tmp_path / "candidate"
    result = cand.build_memory_candidate(
        snapshot_dir=snapshot, preview_path=preview_path, output_dir=output
    )
    assert result["ok"] is True

    source_names = _table_names(_db_paths(snapshot)["sg_memory"])
    candidate_names = _table_names(_db_paths(output)["sg_memory"])
    family = {
        name
        for name in source_names
        if name == "memory_vec" or name.startswith("memory_vec_")
    }
    assert "memory_vec" in family
    assert any(name.startswith("memory_vec_") for name in family)
    assert family <= candidate_names

    conn = _load_vec_conn(_db_paths(output)["sg_memory"])
    try:
        stored = conn.execute(
            "SELECT embedding FROM memory_vec WHERE rowid=?",
            (_MEMORY_VEC_ROWID,),
        ).fetchone()
        assert stored is not None
        assert bytes(stored[0]) == blob
        hits = conn.execute(
            "SELECT rowid, distance FROM memory_vec WHERE embedding MATCH ? AND k = 1",
            (blob,),
        ).fetchall()
        assert [(int(row[0]), float(row[1])) for row in hits] == [
            (_MEMORY_VEC_ROWID, 0.0)
        ]
    finally:
        conn.close()


def test_unknown_table_near_memory_vec_name_still_rejected(tmp_path):
    def mutate(snapshot: Path):
        conn = _open(_db_paths(snapshot)["sg_memory"])
        try:
            conn.execute("CREATE TABLE memory_vectors_custom(id INTEGER)")
            conn.commit()
        finally:
            conn.close()

    _assert_rejected(tmp_path, code="unknown_table", mutate=mutate)


def test_unknown_table_using_memory_vec_prefix_still_rejected(tmp_path):
    """A business table named like a vec0 shadow must not ride the family prefix."""

    def mutate(snapshot: Path):
        conn = _open(_db_paths(snapshot)["sg_memory"])
        try:
            conn.execute("CREATE TABLE memory_vec_audit(id INTEGER, payload TEXT)")
            conn.execute("INSERT INTO memory_vec_audit VALUES (1, 'should-reject')")
            conn.commit()
        finally:
            conn.close()

    _assert_rejected(tmp_path, code="unknown_table", mutate=mutate)


def test_cli_refuses_preview_inside_fake_appdata(tmp_path):
    snapshot = _make_snapshot(tmp_path)
    preview_ok = _make_preview(snapshot, tmp_path / "inputs")
    fake_appdata = tmp_path / "fake-appdata"
    live_preview = fake_appdata / "Amadeus" / "preview.json"
    live_preview.parent.mkdir(parents=True)
    live_preview.write_bytes(preview_ok.read_bytes())
    output = tmp_path / "candidate"
    result = _run_cli(
        "--snapshot", str(snapshot),
        "--preview", str(live_preview),
        "--out", str(output),
        env_extra={"APPDATA": str(fake_appdata)},
    )
    assert result.returncode == 2, result.stdout + result.stderr
    assert "preview_path_forbidden" in result.stderr
    assert not output.exists()


def test_cli_refuses_preview_inside_fake_home_amadeus_without_appdata(tmp_path):
    snapshot = _make_snapshot(tmp_path)
    preview_ok = _make_preview(snapshot, tmp_path / "inputs")
    fake_home = tmp_path / "fake-home"
    live_preview = fake_home / ".amadeus" / "preview.json"
    live_preview.parent.mkdir(parents=True)
    live_preview.write_bytes(preview_ok.read_bytes())
    output = tmp_path / "candidate"
    result = _run_cli(
        "--snapshot", str(snapshot),
        "--preview", str(live_preview),
        "--out", str(output),
        env_extra={"USERPROFILE": str(fake_home)},
        drop_env=("APPDATA", "HOME"),
    )
    assert result.returncode == 2, result.stdout + result.stderr
    assert "preview_path_forbidden" in result.stderr
    assert not output.exists()


def test_cli_refuses_preview_inside_snapshot_or_output_tree(tmp_path):
    snapshot = _make_snapshot(tmp_path)
    preview_ok = _make_preview(snapshot, tmp_path / "inputs")

    inside_snapshot = snapshot / "review.json"
    inside_snapshot.write_bytes(preview_ok.read_bytes())
    output = tmp_path / "candidate"
    nested = _run_cli(
        "--snapshot", str(snapshot),
        "--preview", str(inside_snapshot),
        "--out", str(output),
    )
    assert nested.returncode == 2, nested.stdout + nested.stderr
    assert "preview_path_forbidden" in nested.stderr
    assert not output.exists()

    output_root = tmp_path / "candidate-out"
    output_root.mkdir()
    inside_output = output_root / "review.json"
    inside_output.write_bytes(preview_ok.read_bytes())
    overlapping = _run_cli(
        "--snapshot", str(snapshot),
        "--preview", str(inside_output),
        "--out", str(output_root),
    )
    assert overlapping.returncode == 2, overlapping.stdout + overlapping.stderr
    assert "output_exists" in overlapping.stderr or "preview_path_forbidden" in overlapping.stderr


def test_cli_refuses_preview_aliased_into_fake_live_root(tmp_path):
    if os.name != "nt":
        pytest.skip("junction aliasing requires Windows")
    snapshot = _make_snapshot(tmp_path)
    preview_ok = _make_preview(snapshot, tmp_path / "inputs")
    fake_appdata = tmp_path / "fake-appdata"
    live_dir = fake_appdata / "Amadeus"
    live_dir.mkdir(parents=True)
    (live_dir / "preview.json").write_bytes(preview_ok.read_bytes())
    alias = tmp_path / "alias-live"
    created = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(alias), str(live_dir)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if created.returncode != 0:
        pytest.skip(f"junction creation unavailable: {created.stderr.strip()}")
    output = tmp_path / "candidate"
    result = _run_cli(
        "--snapshot", str(snapshot),
        "--preview", str(alias / "preview.json"),
        "--out", str(output),
        env_extra={"APPDATA": str(fake_appdata)},
    )
    assert result.returncode == 2, result.stdout + result.stderr
    assert "preview_path_forbidden" in result.stderr
    assert not output.exists()


def test_cli_refuses_preview_hardlinked_to_protected_files(tmp_path):
    """File identity, not just resolved path names, must refuse protected files."""
    snapshot = _make_snapshot(tmp_path)
    preview_ok = _make_preview(snapshot, tmp_path / "inputs")
    outside = tmp_path / "outside"
    outside.mkdir()

    db_alias = outside / "from-source-db.json"
    try:
        os.link(_db_paths(snapshot)["control"], db_alias)
    except OSError as exc:
        pytest.skip(f"hard-link creation unavailable: {exc}")
    db_result = _run_cli(
        "--snapshot", str(snapshot),
        "--preview", str(db_alias),
        "--out", str(tmp_path / "cand-db"),
    )
    assert db_result.returncode == 2, db_result.stdout + db_result.stderr
    assert "preview_path_forbidden" in db_result.stderr
    assert not (tmp_path / "cand-db").exists()

    fake_appdata = tmp_path / "fake-appdata"
    live_preview = fake_appdata / "Amadeus" / "preview.json"
    live_preview.parent.mkdir(parents=True)
    live_preview.write_bytes(preview_ok.read_bytes())
    live_alias = outside / "from-live.json"
    os.link(live_preview, live_alias)
    live_result = _run_cli(
        "--snapshot", str(snapshot),
        "--preview", str(live_alias),
        "--out", str(tmp_path / "cand-live"),
        env_extra={"APPDATA": str(fake_appdata)},
    )
    assert live_result.returncode == 2, live_result.stdout + live_result.stderr
    assert "preview_path_forbidden" in live_result.stderr
    assert not (tmp_path / "cand-live").exists()

    inside = snapshot / "review.json"
    inside.write_bytes(preview_ok.read_bytes())
    snap_alias = outside / "from-snapshot.json"
    os.link(inside, snap_alias)
    snap_result = _run_cli(
        "--snapshot", str(snapshot),
        "--preview", str(snap_alias),
        "--out", str(tmp_path / "cand-snap"),
    )
    assert snap_result.returncode == 2, snap_result.stdout + snap_result.stderr
    assert "preview_path_forbidden" in snap_result.stderr
    assert not (tmp_path / "cand-snap").exists()
