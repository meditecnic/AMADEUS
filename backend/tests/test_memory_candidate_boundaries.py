"""Independent regression boundaries retained from the candidate repair review."""
from argparse import Namespace
import os
from pathlib import Path
import runpy
import sqlite3

import pytest

from test_memory_candidate_builder import (
    SCRIPT, _db_paths, _make_preview, _make_snapshot, _run_cli, cand,
)


@pytest.mark.parametrize("name,with_parent", [
    ("memory_vec", False),
    ("memory_vec_info", False),
    ("memory_vec_vector_chunks999", False),
    ("memory_vec_vector_chunks99", True),
])
def test_ordinary_tables_cannot_impersonate_vec_family(tmp_path, name, with_parent):
    snapshot = _make_snapshot(tmp_path)
    conn = sqlite3.connect(_db_paths(snapshot)["sg_memory"])
    try:
        if with_parent:
            import sqlite_vec
            conn.enable_load_extension(True)
            sqlite_vec.load(conn)
            conn.execute("CREATE VIRTUAL TABLE memory_vec USING vec0(embedding float[384])")
        conn.execute(f'CREATE TABLE "{name}"(id INTEGER PRIMARY KEY, payload TEXT)')
        conn.execute(f'INSERT INTO "{name}" VALUES(1, ?)', ("ordinary business data",))
        conn.commit()
    finally:
        conn.close()
    preview = _make_preview(snapshot, tmp_path / "inputs")
    output = tmp_path / "candidate"
    with pytest.raises(cand.CandidateBuildError) as error:
        cand.build_memory_candidate(snapshot_dir=snapshot, preview_path=preview, output_dir=output)
    assert error.value.code == "unknown_table"
    assert not output.exists()


@pytest.mark.parametrize("failure_stage", ["initial", "after_backup"])
def test_unreadable_wal_never_means_unchanged(tmp_path, monkeypatch, failure_stage):
    snapshot = _make_snapshot(tmp_path)
    control = _db_paths(snapshot)["control"]
    holder = sqlite3.connect(control)
    holder.execute("PRAGMA journal_mode=WAL")
    holder.execute("PRAGMA wal_autocheckpoint=0")
    preview = _make_preview(snapshot, tmp_path / "inputs")
    output = tmp_path / "candidate"
    fail_stat = failure_stage == "initial"
    original_stat = Path.stat
    original_backup = cand._backup_databases

    def denied_wal_stat(path, *args, **kwargs):
        if fail_stat and str(path) == str(control) + "-wal":
            raise PermissionError(13, "synthetic WAL metadata denial", str(path))
        return original_stat(path, *args, **kwargs)

    def backup_then_commit(*args, **kwargs):
        nonlocal fail_stat
        original_backup(*args, **kwargs)
        holder.execute("INSERT INTO schema_meta(key,value) VALUES('late_commit','1')")
        holder.commit()
        fail_stat = True

    monkeypatch.setattr(Path, "stat", denied_wal_stat)
    monkeypatch.setattr(cand, "_backup_databases", backup_then_commit)
    try:
        with pytest.raises(cand.CandidateBuildError) as error:
            cand.build_memory_candidate(snapshot_dir=snapshot, preview_path=preview, output_dir=output)
        assert error.value.code == "source_unreadable"
        assert not output.exists()
        if failure_stage == "after_backup":
            import json
            records = list(tmp_path.glob(".candidate.staging-*/INCOMPLETE.json"))
            assert len(records) == 1
            assert json.loads(records[0].read_text())["code"] == "source_unreadable"
    finally:
        holder.close()


@pytest.mark.parametrize("location", ["live", "snapshot", "outside"])
def test_preview_hardlinks_are_rejected_before_json_read(tmp_path, location):
    snapshot = _make_snapshot(tmp_path)
    fake_appdata = tmp_path / "fake-appdata"
    root = {"live": fake_appdata / "Amadeus", "snapshot": snapshot, "outside": tmp_path / "other"}[location]
    root.mkdir(parents=True, exist_ok=True)
    protected = root / "review.json"
    protected.write_text("not valid JSON", encoding="utf-8")
    alias = tmp_path / "external-review.json"
    os.link(protected, alias)
    output = tmp_path / "candidate"
    result = _run_cli("--snapshot", str(snapshot), "--preview", str(alias), "--out", str(output),
                      env_extra={"APPDATA": str(fake_appdata)})
    assert result.returncode == 2, result.stdout + result.stderr
    assert "preview_path_forbidden" in result.stderr
    assert "invalid_preview" not in result.stderr
    assert not output.exists()


def test_standalone_preview_validation_does_not_enumerate_protected_trees(tmp_path, monkeypatch):
    snapshot = _make_snapshot(tmp_path)
    preview = _make_preview(snapshot, tmp_path / "inputs")
    fake_appdata = tmp_path / "fake-appdata"
    (fake_appdata / "Amadeus").mkdir(parents=True)
    monkeypatch.setenv("APPDATA", str(fake_appdata))
    monkeypatch.delenv("AMADEUS_DATA_DIR", raising=False)
    monkeypatch.delenv("AMADEUS_DB_PATH", raising=False)
    entry = runpy.run_path(str(SCRIPT))["_entry_validation"]

    def forbidden_scan(*args, **kwargs):
        raise AssertionError("preview validation must not enumerate protected trees")

    monkeypatch.setattr(os, "scandir", forbidden_scan)
    entry(Namespace(snapshot=str(snapshot), preview=str(preview), out=str(tmp_path / "candidate")))
