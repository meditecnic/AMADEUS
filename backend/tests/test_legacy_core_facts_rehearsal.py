"""Synthetic-data tests for the offline legacy core-fact migration rehearsal.

Task memory-migration-glm-20260908 acceptance coverage (revision 1):

- qualified user sources import with correct scope/provenance/confidence;
  the same numeric source id in another worldline does not confuse scopes;
- dismissed/historical, empty/bad/missing sources, assistant sources, and
  owner/identity/conversation mismatches all produce explicit skips;
- D30 hard privacy blocks (existing classifier) skip the item, keep the raw
  hit text out of preview/detail/hints, and never import it;
- repeat apply does not grow; same text across scopes is not merged; facts
  deleted or revised in the target are never re-imported/overwritten;
- an injected write failure rolls the whole worldline DB back, keeps the
  committed other-worldline mapping and reports NO uncommitted ids; retry works;
- stale previews, tampered preview content, overlapping paths, wrong-source
  targets, non-target directories and incomplete snapshots are rejected;
  the source DB files are never modified;
- apply consumes ONE fixed read view: values mutated in the source after the
  view is read are never imported, and only previewed content lands;
- CLI entry refuses env overrides, live APPDATA / ~/.amadeus paths, preview
  outputs inside the source, outputs over existing files, and outputs that
  alias into the source via a junction;
- actual scoped retrieval on the rehearsal target returns only this import
  (demo v11 facts, legacy episodes, experiences, shared facts, other
  owner/identity/worldline scopes stay out);
- preview reports per-scope pin counts against the existing limit constant.

All fixtures are synthetic. No real APPDATA data, no external model calls.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from app.db import CONTROL_SCHEMA, HISTORY_SCHEMA, MEMORY_SCHEMA
from app.services.memory_v11 import migration as mig
from scripts import legacy_core_facts_rehearsal as rehearsal_cli

BACKEND_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = BACKEND_ROOT / "scripts" / "legacy_core_facts_rehearsal.py"

SG = "steins_gate"
BETA = "beta"


# ---------------------------------------------------------------------------
# Invocation isolation guards (the pytest process itself must be isolated).
# ---------------------------------------------------------------------------


def test_app_module_resolves_to_this_worktree():
    import app

    assert Path(app.__file__).resolve() == BACKEND_ROOT / "app" / "__init__.py"


def test_process_environment_is_isolated_from_live_store():
    assert not (os.environ.get("AMADEUS_DB_PATH") or "").strip(), (
        "AMADEUS_DB_PATH must not be set for this test process"
    )
    data_dir = os.environ.get("AMADEUS_DATA_DIR")
    appdata = os.environ.get("APPDATA")
    if data_dir and appdata:
        live_root = (Path(appdata) / "Amadeus").resolve()
        resolved = Path(data_dir).resolve()
        assert live_root not in resolved.parents and resolved != live_root, (
            f"AMADEUS_DATA_DIR={resolved} points into the live store"
        )


# ---------------------------------------------------------------------------
# Synthetic source snapshot helpers.
# ---------------------------------------------------------------------------


def _open(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def _namespace(worldline: str) -> str:
    return "sg" if worldline == SG else "beta"


def _create_snapshot(root: Path) -> Path:
    """Create BOTH worldline snapshots (a worldline with no data stays empty).

    Revision 1 (C ruling): the tool requires the complete four-DB snapshot;
    tests exercising one worldline's logic keep the other one empty.
    """
    src = root / "snapshot"
    for wl in (SG, BETA):
        ns_dir = src / "worldlines" / _namespace(wl)
        ns_dir.mkdir(parents=True)
        for name, schema in (
            ("history.sqlite3", HISTORY_SCHEMA),
            ("memory.sqlite3", MEMORY_SCHEMA),
        ):
            conn = _open(ns_dir / name)
            try:
                conn.executescript(schema)
                conn.execute(
                    "INSERT OR REPLACE INTO schema_meta(key,value) "
                    "VALUES('schema_version','11')"
                )
                conn.commit()
            finally:
                conn.close()
    return src


def _history(src: Path, worldline: str) -> sqlite3.Connection:
    return _open(src / "worldlines" / _namespace(worldline) / "history.sqlite3")


def _memory(src: Path, worldline: str) -> sqlite3.Connection:
    return _open(src / "worldlines" / _namespace(worldline) / "memory.sqlite3")


def _target_memory(target: Path, worldline: str) -> sqlite3.Connection:
    return _open(target / "worldlines" / _namespace(worldline) / "memory.sqlite3")


def _add_conversation(
    src: Path, worldline: str, *, conv_id: str, session_id: str, identity_mode: str
) -> None:
    conn = _history(src, worldline)
    try:
        conn.execute(
            """INSERT INTO conversations(
                   id,session_id,title,title_source,is_default,identity_mode
               ) VALUES(?,?,?,'auto',0,?)""",
            (conv_id, session_id, "Conversation", identity_mode),
        )
        conn.commit()
    finally:
        conn.close()


def _add_message(
    src: Path,
    worldline: str,
    *,
    message_id: int,
    session_id: str,
    conversation_id: str,
    role: str = "user",
    content: str,
    created_at: str = "2026-08-01T00:00:00+00:00",
) -> int:
    conn = _history(src, worldline)
    try:
        conn.execute(
            """INSERT INTO messages(
                   id,session_id,conversation_id,role,content,created_at
               ) VALUES(?,?,?,?,?,?)""",
            (message_id, session_id, conversation_id, role, content, created_at),
        )
        conn.commit()
    finally:
        conn.close()
    return message_id


def _add_core_fact(
    src: Path,
    worldline: str,
    *,
    session_id: str,
    fact_key: str,
    fact_value: str,
    identity_mode: str = "self",
    confidence: float = 0.9,
    importance: float = 0.85,
    is_current: int = 1,
    is_pinned: int = 0,
    is_dismissed: int = 0,
    source_message_ids,
    created_at: str = "2026-08-02T00:00:00+00:00",
) -> int:
    raw_sources = (
        source_message_ids
        if isinstance(source_message_ids, str)
        else json.dumps(source_message_ids)
    )
    conn = _memory(src, worldline)
    try:
        cur = conn.execute(
            """INSERT INTO core_facts(
                   session_id,identity_mode,fact_key,fact_value,confidence,
                   importance,is_current,is_pinned,is_dismissed,
                   source_message_ids,created_at
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (
                session_id,
                identity_mode,
                fact_key,
                fact_value,
                confidence,
                importance,
                is_current,
                is_pinned,
                is_dismissed,
                raw_sources,
                created_at,
            ),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def _sqlite_state(src: Path) -> dict[str, str]:
    state: dict[str, str] = {}
    for path in sorted(src.rglob("*")):
        if path.is_file() and path.suffix == ".sqlite3":
            state[str(path.relative_to(src)).replace("\\", "/")] = (
                hashlib.sha256(path.read_bytes()).hexdigest()
            )
    return state


def _preview(src: Path) -> dict:
    return mig.build_legacy_core_facts_preview(src)


def _apply(src: Path, target: Path, preview: dict) -> dict:
    return mig.apply_legacy_core_facts(
        source_dir=src, target_dir=target, preview=preview
    )


def _base_source(tmp_path: Path) -> Path:
    """SG + BETA snapshot: one planned fact per worldline, same numeric id 101."""
    src = _create_snapshot(tmp_path)
    # SG history
    _add_conversation(src, SG, conv_id="conv-a-self", session_id="ownerA", identity_mode="self")
    _add_message(src, SG, message_id=101, session_id="ownerA", conversation_id="conv-a-self",
                 content="私はアニメが好きです。")
    _add_message(src, SG, message_id=102, session_id="ownerA", conversation_id="conv-a-self",
                 role="assistant", content="そうですか。")
    # BETA history: SAME numeric message id, different owner/content/worldline
    _add_conversation(src, BETA, conv_id="conv-beta", session_id="ownerBeta", identity_mode="okabe")
    _add_message(src, BETA, message_id=101, session_id="ownerBeta", conversation_id="conv-beta",
                 content="ベータ世界線の内容です。")
    # Legacy core facts
    _add_core_fact(src, SG, session_id="ownerA", fact_key="likes_anime",
                   fact_value="喜欢看动漫", identity_mode="self", confidence=0.9,
                   importance=0.85, source_message_ids=[101])
    _add_core_fact(src, BETA, session_id="ownerBeta", fact_key="beta_fact",
                   fact_value="β世界线事实", identity_mode="okabe", confidence=0.8,
                   importance=0.7, source_message_ids=[101])
    return src


# ---------------------------------------------------------------------------
# 1. Qualified import: mapping, scope, provenance, worldline isolation.
# ---------------------------------------------------------------------------


def test_planned_import_mapping_scope_and_provenance(tmp_path):
    src = _base_source(tmp_path)
    before = _sqlite_state(src)

    preview = _preview(src)
    planned = [i for i in preview["items"] if i["decision"] == "planned"]
    assert len(planned) == 2
    assert preview["summary"]["planned"] == 2

    target = tmp_path / "target"
    result = _apply(src, target, preview)
    assert result["ok"] is True
    assert result["worldlines"][SG]["imported"] == 1
    assert result["worldlines"][BETA]["imported"] == 1

    # Source untouched (main DB files byte-identical).
    assert _sqlite_state(src) == before

    # Target manifest binds the source snapshot.
    manifest = json.loads((target / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["kind"] == mig.TARGET_KIND
    assert manifest["source_id"] == preview["source"]["source_id"]
    assert sorted(manifest["worldlines"]) == [BETA, SG]

    # SG import: mapping / provenance / confidence / scope.
    conn = _target_memory(target, SG)
    try:
        fact = conn.execute(
            "SELECT * FROM stable_facts WHERE session_id='ownerA'"
        ).fetchone()
        assert fact is not None and fact["identity_mode"] == "self"
        assert fact["state"] == "active" and fact["active_version"] == 1
        version = conn.execute(
            "SELECT * FROM stable_fact_versions WHERE fact_id=?", (fact["fact_id"],)
        ).fetchone()
        assert version["display_text"] == "likes_anime: 喜欢看动漫"
        assert version["change_kind"] == "legacy_import"
        assert abs(version["confidence"] - 0.9) < 1e-9
        observation = conn.execute(
            "SELECT * FROM memory_observations WHERE observation_id IN "
            "(SELECT observation_id FROM stable_fact_evidence WHERE fact_id=?)",
            (fact["fact_id"],),
        ).fetchone()
        assert observation["evidence_kind"] == "legacy_import"
        assert observation["status"] == "attached"
        assert observation["memory_class"] == "stable_candidate"
        assert observation["extractor_version"] == mig.MIGRATION_TOOL_VERSION
        source_row = conn.execute(
            "SELECT * FROM memory_observation_sources WHERE observation_id=?",
            (observation["observation_id"],),
        ).fetchone()
        assert source_row["source_message_id"] == 101
        assert source_row["source_role"] == "user"
        assert source_row["source_state"] == "present"
        assert source_row["excerpt"] == "私はアニメが好きです。"
        assert source_row["source_fingerprint"] == hashlib.sha256(
            "ownerA:101:私はアニメが好きです。".encode("utf-8")
        ).hexdigest()
        import_record = conn.execute(
            "SELECT * FROM legacy_core_fact_imports WHERE fact_id=?", (fact["fact_id"],)
        ).fetchone()
        assert import_record["source_id"] == preview["source"]["source_id"]
        assert import_record["worldline"] == SG
        assert import_record["legacy_row_id"] == 1
        # Legacy table is NOT wholesale-copied into the target.
        assert conn.execute("SELECT COUNT(*) FROM core_facts").fetchone()[0] == 0
        sg_fingerprint = source_row["source_fingerprint"]
    finally:
        conn.close()

    # BETA import: same numeric source id 101 resolved in its own worldline.
    conn = _target_memory(target, BETA)
    try:
        fact = conn.execute(
            "SELECT * FROM stable_facts WHERE session_id='ownerBeta'"
        ).fetchone()
        assert fact is not None and fact["identity_mode"] == "okabe"
        source_row = conn.execute(
            """SELECT s.* FROM memory_observation_sources s
               JOIN stable_fact_evidence e ON e.observation_id=s.observation_id
               WHERE e.fact_id=?""",
            (fact["fact_id"],),
        ).fetchone()
        assert source_row["source_message_id"] == 101
        assert source_row["source_fingerprint"] == hashlib.sha256(
            "ownerBeta:101:ベータ世界線の内容です。".encode("utf-8")
        ).hexdigest()
        assert source_row["source_fingerprint"] != sg_fingerprint
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 2. Explicit skip reasons; source never modified.
# ---------------------------------------------------------------------------


def test_skip_reasons_are_explicit_and_source_untouched(tmp_path):
    src = _create_snapshot(tmp_path)
    _add_conversation(src, SG, conv_id="conv-self", session_id="ownerA", identity_mode="self")
    _add_conversation(src, SG, conv_id="conv-other-owner", session_id="ownerB", identity_mode="self")
    _add_conversation(src, SG, conv_id="conv-okabe", session_id="ownerA", identity_mode="okabe")
    _add_message(src, SG, message_id=101, session_id="ownerA", conversation_id="conv-self",
                 content="内容一")
    _add_message(src, SG, message_id=102, session_id="ownerA", conversation_id="conv-self",
                 role="assistant", content="助手回复")
    _add_message(src, SG, message_id=103, session_id="ownerB", conversation_id="conv-other-owner",
                 content="别人家的消息")
    _add_message(src, SG, message_id=104, session_id="ownerA", conversation_id="conv-okabe",
                 content="身份不同的消息")
    # Message in a conversation owned by another session.
    _add_message(src, SG, message_id=105, session_id="ownerB", conversation_id="conv-self",
                 content="借用他人会话")

    cases = {
        "k_dismissed": ("ownerA", "self", [101], {"is_dismissed": 1}, "dismissed"),
        "k_historical": ("ownerA", "self", [101], {"is_current": 0}, "not_current"),
        "k_empty_src": ("ownerA", "self", "[]", {}, "source_empty"),
        "k_bad_src": ("ownerA", "self", "not-json", {}, "source_malformed"),
        "k_missing": ("ownerA", "self", [999], {}, "source_missing"),
        "k_assistant": ("ownerA", "self", [102], {}, "source_not_user"),
        "k_owner": ("ownerA", "self", [103], {}, "owner_mismatch"),
        "k_conv_owner": ("ownerB", "self", [105], {}, "conversation_owner_mismatch"),
        "k_identity": ("ownerA", "self", [104], {}, "identity_mismatch"),
        "k_planned": ("ownerA", "self", [101], {}, None),
    }
    for key, (session, mode, sources, extra, _reason) in cases.items():
        _add_core_fact(
            src, SG, session_id=session, fact_key=key, fact_value=f"{key}的值",
            identity_mode=mode, source_message_ids=sources, **extra,
        )

    before = _sqlite_state(src)
    preview = _preview(src)
    assert _sqlite_state(src) == before, "preview must not modify source DB files"

    decisions = {i["fact_key"]: i for i in preview["items"]}
    for key, (session, mode, sources, extra, reason) in cases.items():
        item = decisions[key]
        if reason is None:
            assert item["decision"] == "planned", (key, item)
        else:
            assert item["decision"] == "skip", (key, item)
            assert item["reason"] == reason, (key, item)

    target = tmp_path / "target"
    result = _apply(src, target, preview)
    assert result["ok"] is True
    assert result["worldlines"][SG]["imported"] == 1
    assert _sqlite_state(src) == before, "apply must not modify source DB files"


# ---------------------------------------------------------------------------
# 3. Repeat apply is idempotent.
# ---------------------------------------------------------------------------


def test_repeat_apply_does_not_grow(tmp_path):
    src = _base_source(tmp_path)
    preview = _preview(src)
    target = tmp_path / "target"

    first = _apply(src, target, preview)
    assert first["ok"] is True
    second = _apply(src, target, preview)
    assert second["ok"] is True
    for wl in (SG, BETA):
        assert second["worldlines"][wl]["imported"] == 0
        assert second["worldlines"][wl]["already_imported"] == 1

    for wl in (SG, BETA):
        conn = _target_memory(target, wl)
        try:
            for table in (
                "stable_facts",
                "stable_fact_versions",
                "stable_fact_evidence",
                "memory_observations",
                "memory_observation_sources",
                "legacy_core_fact_imports",
            ):
                assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 1
        finally:
            conn.close()
    manifest = json.loads((target / "manifest.json").read_text(encoding="utf-8"))
    assert len(manifest["runs"]) == 2


# ---------------------------------------------------------------------------
# 4. Same text across scopes is not merged; duplicates shown, not merged.
# ---------------------------------------------------------------------------


def test_same_text_across_scopes_is_not_merged(tmp_path):
    src = _create_snapshot(tmp_path)
    _add_conversation(src, SG, conv_id="c-self-a", session_id="ownerA", identity_mode="self")
    _add_conversation(src, SG, conv_id="c-okabe-a", session_id="ownerA", identity_mode="okabe")
    _add_conversation(src, SG, conv_id="c-self-b", session_id="ownerB", identity_mode="self")
    _add_message(src, SG, message_id=201, session_id="ownerA", conversation_id="c-self-a",
                 content="同文来源一")
    _add_message(src, SG, message_id=202, session_id="ownerA", conversation_id="c-okabe-a",
                 content="同文来源二")
    _add_message(src, SG, message_id=203, session_id="ownerB", conversation_id="c-self-b",
                 content="同文来源三")

    _add_core_fact(src, SG, session_id="ownerA", fact_key="a1", fact_value="同文",
                   identity_mode="self", source_message_ids=[201])
    _add_core_fact(src, SG, session_id="ownerA", fact_key="a2", fact_value="同文",
                   identity_mode="okabe", source_message_ids=[202])
    _add_core_fact(src, SG, session_id="ownerB", fact_key="a3", fact_value="同文",
                   identity_mode="self", source_message_ids=[203])
    _add_core_fact(src, SG, session_id="ownerA", fact_key="a5", fact_value="同文",
                   identity_mode="self", source_message_ids=[201])

    preview = _preview(src)
    assert preview["summary"]["planned"] == 4
    # Same-scope duplicate shown, not merged.
    assert len(preview["duplicate_hints"]) == 1
    hint = preview["duplicate_hints"][0]
    assert (hint["worldline"], hint["session_id"], hint["identity_mode"]) == (SG, "ownerA", "self")
    assert sorted(hint["legacy_row_ids"]) == [1, 4]

    target = tmp_path / "target"
    result = _apply(src, target, preview)
    assert result["ok"] is True
    conn = _target_memory(target, SG)
    try:
        facts = conn.execute("SELECT * FROM stable_facts").fetchall()
        assert len(facts) == 4
        scopes = {(r["session_id"], r["identity_mode"]) for r in facts}
        assert scopes == {("ownerA", "self"), ("ownerA", "okabe"), ("ownerB", "self")}
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM stable_facts WHERE session_id='ownerA' "
                "AND identity_mode='self'"
            ).fetchone()[0]
            == 2
        )
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 5. Deleted / revised imported facts are not revived by re-apply.
# ---------------------------------------------------------------------------


@pytest.fixture
def use_data_dir(monkeypatch):
    from app.db import reset_initialization_cache

    def _use(root: Path) -> None:
        monkeypatch.setenv("AMADEUS_DATA_DIR", str(root))
        reset_initialization_cache()

    yield _use
    reset_initialization_cache()


class _StubEmbedder:
    model_name = "intfloat/multilingual-e5-small"
    dimensions = 384

    async def encode_passage(self, text: str) -> list[float]:
        return [0.0] * 384


async def test_deleted_and_revised_facts_are_not_revived(tmp_path, use_data_dir):
    from app.services.memory_v11.facts import delete_fact, user_edit_fact

    src = _create_snapshot(tmp_path)
    _add_conversation(src, SG, conv_id="c1", session_id="ownerA", identity_mode="self")
    _add_message(src, SG, message_id=301, session_id="ownerA", conversation_id="c1",
                 content="用户说喜欢看动漫")
    _add_message(src, SG, message_id=302, session_id="ownerA", conversation_id="c1",
                 content="用户说喜欢喝咖啡")
    _add_core_fact(src, SG, session_id="ownerA", fact_key="likes_anime",
                   fact_value="喜欢看动漫", source_message_ids=[301])
    _add_core_fact(src, SG, session_id="ownerA", fact_key="likes_coffee",
                   fact_value="喜欢喝咖啡", source_message_ids=[302])

    preview = _preview(src)
    target = tmp_path / "target"
    result = _apply(src, target, preview)
    assert result["ok"] is True
    by_legacy = {f["legacy_row_id"]: f for f in result["imported_facts"]}
    assert len(by_legacy) == 2

    use_data_dir(target)
    await delete_fact(
        session_id="ownerA", worldline=SG, identity_mode="self",
        fact_id=by_legacy[1]["fact_id"], expected_version=1,
    )
    await user_edit_fact(
        session_id="ownerA", worldline=SG, identity_mode="self",
        fact_id=by_legacy[2]["fact_id"], display_text="likes_coffee: 特别喜欢喝咖啡",
        expected_version=1, embedder=_StubEmbedder(),
    )

    # Re-apply the same preview: no revival, no overwrite, no growth.
    again = _apply(src, target, preview)
    assert again["ok"] is True
    assert again["worldlines"][SG]["already_imported"] == 2
    assert again["worldlines"][SG]["imported"] == 0

    conn = _target_memory(target, SG)
    try:
        deleted = conn.execute(
            "SELECT * FROM stable_facts WHERE fact_id=?", (by_legacy[1]["fact_id"],)
        ).fetchone()
        assert deleted["state"] == "deleted"
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM stable_fact_versions WHERE fact_id=?",
                (by_legacy[1]["fact_id"],),
            ).fetchone()[0]
            == 0
        )
        revised = conn.execute(
            "SELECT * FROM stable_facts WHERE fact_id=?", (by_legacy[2]["fact_id"],)
        ).fetchone()
        assert revised["state"] == "active"
        assert revised["active_version"] == 2
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM stable_fact_versions WHERE fact_id=?",
                (by_legacy[2]["fact_id"],),
            ).fetchone()[0]
            == 2
        )
        assert conn.execute("SELECT COUNT(*) FROM stable_facts").fetchone()[0] == 2
        assert (
            conn.execute("SELECT COUNT(*) FROM legacy_core_fact_imports").fetchone()[0] == 2
        )
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 6. Injected write failure: full rollback per worldline DB; retry succeeds.
# ---------------------------------------------------------------------------


def test_failure_rolls_back_worldline_and_retry_succeeds(tmp_path):
    src = _create_snapshot(tmp_path)
    # SG: 3 planned facts (the 2nd write will fail).
    _add_conversation(src, SG, conv_id="c1", session_id="ownerA", identity_mode="self")
    for mid in (401, 402, 403):
        _add_message(src, SG, message_id=mid, session_id="ownerA",
                     conversation_id="c1", content=f"消息{mid}")
        _add_core_fact(src, SG, session_id="ownerA", fact_key=f"k{mid}",
                       fact_value=f"事实{mid}", source_message_ids=[mid])
    # BETA: 1 planned fact (must stay committed when SG fails).
    _add_conversation(src, BETA, conv_id="cb", session_id="ownerB", identity_mode="self")
    _add_message(src, BETA, message_id=401, session_id="ownerB",
                 conversation_id="cb", content="β消息")
    _add_core_fact(src, BETA, session_id="ownerB", fact_key="k_beta",
                   fact_value="β事实", source_message_ids=[401])

    preview = _preview(src)
    assert preview["summary"]["planned"] == 4
    target = tmp_path / "target"

    original = mig._write_imported_fact
    calls = {"n": 0}

    def _flaky(*args, **kwargs):
        if kwargs.get("worldline") == SG:
            calls["n"] += 1
            if calls["n"] >= 2:
                raise RuntimeError("injected import failure")
        return original(*args, **kwargs)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(mig, "_write_imported_fact", _flaky)
        result = _apply(src, target, preview)

    assert result["ok"] is False
    assert result["worldlines"][SG]["status"] == "failed"
    assert result["worldlines"][SG]["rolled_back"] is True
    # Committed β survives…
    assert result["worldlines"][BETA]["status"] == "committed"
    assert result["worldlines"][BETA]["imported"] == 1
    # …and the failed SG worldline contributes NOTHING to the mapping:
    # only the committed β import is reported, no uncommitted SG ids.
    assert [f["worldline"] for f in result["imported_facts"]] == [BETA]
    assert result["imported_facts"][0]["display_text"] == "k_beta: β事实"

    conn = _target_memory(target, SG)
    try:
        for table in (
            "stable_facts",
            "stable_fact_versions",
            "stable_fact_evidence",
            "memory_observations",
            "memory_observation_sources",
            "legacy_core_fact_imports",
        ):
            assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0, table
    finally:
        conn.close()
    conn = _target_memory(target, BETA)
    try:
        assert conn.execute("SELECT COUNT(*) FROM stable_facts").fetchone()[0] == 1
        assert (
            conn.execute("SELECT COUNT(*) FROM legacy_core_fact_imports").fetchone()[0]
            == 1
        )
    finally:
        conn.close()

    # Retry after the failure is repaired: everything imports cleanly and
    # β is recognized as already imported.
    retry = _apply(src, target, preview)
    assert retry["ok"] is True
    assert retry["worldlines"][SG]["imported"] == 3
    assert retry["worldlines"][BETA]["already_imported"] == 1
    conn = _target_memory(target, SG)
    try:
        assert conn.execute("SELECT COUNT(*) FROM stable_facts").fetchone()[0] == 3
        assert (
            conn.execute("SELECT COUNT(*) FROM legacy_core_fact_imports").fetchone()[0]
            == 3
        )
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 7. Stale preview / tampered preview / path overlap rejection.
# ---------------------------------------------------------------------------


def test_stale_preview_is_rejected(tmp_path):
    src = _base_source(tmp_path)
    preview = _preview(src)
    target = tmp_path / "target"

    _add_core_fact(src, SG, session_id="ownerA", fact_key="new_fact",
                   fact_value="新事实", source_message_ids=[101])
    with pytest.raises(mig.MigrationRehearsalError) as excinfo:
        _apply(src, target, preview)
    assert excinfo.value.code == "preview_stale"
    assert not target.exists()


def test_overlapping_paths_are_rejected(tmp_path):
    src = _base_source(tmp_path)
    preview = _preview(src)
    for target in (src, src / "inner", src.parent):
        with pytest.raises(mig.MigrationRehearsalError) as excinfo:
            _apply(src, target, preview)
        assert excinfo.value.code == "overlapping_paths"


def test_tampered_preview_is_rejected(tmp_path):
    src = _base_source(tmp_path)
    preview = _preview(src)
    tampered = json.loads(json.dumps(preview))
    tampered["items"] = [
        item for item in tampered["items"] if item["decision"] != "planned"
    ] + [
        {
            "worldline": SG,
            "legacy_row_id": 999,
            "session_id": "ownerA",
            "identity_mode": "self",
            "decision": "planned",
        }
    ]
    with pytest.raises(mig.MigrationRehearsalError) as excinfo:
        _apply(src, tmp_path / "target", tampered)
    assert excinfo.value.code == "preview_mismatch"


def test_tampered_preview_content_is_rejected(tmp_path):
    """Editing a reviewed planned value in the preview file must not apply.

    The semantic comparison covers the full reviewed plan (key/value/
    confidence/pins/provenance), not just row ids — a modified display
    content cannot ride along under an unchanged planned set.
    """
    src = _base_source(tmp_path)
    preview = _preview(src)
    tampered = json.loads(json.dumps(preview))
    for item in tampered["items"]:
        if item["decision"] == "planned" and item.get("fact_key") == "likes_anime":
            item["fact_value"] = "tampered value"
    with pytest.raises(mig.MigrationRehearsalError) as excinfo:
        _apply(src, tmp_path / "target", tampered)
    assert excinfo.value.code == "preview_mismatch"


def test_apply_uses_the_previewed_fixed_view(tmp_path):
    """Source mutations after the fixed read must never be imported.

    Simulates a source change mid-apply (after the view is read, before the
    writes): the imported values must be exactly the previewed ones, never
    the un-previewed new values (C reproduction #3).
    """
    src = _base_source(tmp_path)
    preview = _preview(src)
    target = tmp_path / "target"

    original = mig._write_imported_fact
    mutated = {"done": False}

    def _mutate_then_write(conn, **kwargs):
        if not mutated["done"]:
            mutated["done"] = True
            db = _memory(src, SG)
            try:
                db.execute(
                    "UPDATE core_facts SET fact_value='updated synthetic value' "
                    "WHERE fact_key='likes_anime'"
                )
                db.commit()
            finally:
                db.close()
        return original(conn, **kwargs)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(mig, "_write_imported_fact", _mutate_then_write)
        result = _apply(src, target, preview)

    assert result["ok"] is True
    sg_displays = [
        f["display_text"] for f in result["imported_facts"] if f["worldline"] == SG
    ]
    assert sg_displays == ["likes_anime: 喜欢看动漫"]
    assert not any("updated synthetic value" in f["display_text"] for f in result["imported_facts"])
    conn = _target_memory(target, SG)
    try:
        texts = [
            str(r[0])
            for r in conn.execute("SELECT display_text FROM stable_fact_versions")
        ]
        assert texts == ["likes_anime: 喜欢看动漫"]
        assert not any("updated synthetic value" in t for t in texts)
    finally:
        conn.close()


def test_incomplete_snapshot_missing_worldline_is_rejected(tmp_path):
    src = _create_snapshot(tmp_path)
    shutil.rmtree(src / "worldlines" / "beta")
    with pytest.raises(mig.MigrationRehearsalError) as excinfo:
        _preview(src)
    assert excinfo.value.code == "invalid_source_layout"


# ---------------------------------------------------------------------------
# 8. Target binding and rejection of non-target directories.
# ---------------------------------------------------------------------------


def test_target_is_bound_to_its_source(tmp_path):
    src = _base_source(tmp_path)
    preview = _preview(src)
    target = tmp_path / "target"
    assert _apply(src, target, preview)["ok"] is True

    # A different source snapshot cannot reuse the same target.
    other_src = _base_source(tmp_path / "other")
    _add_core_fact(other_src, SG, session_id="ownerA", fact_key="extra",
                   fact_value="额外事实", source_message_ids=[101])
    other_preview = _preview(other_src)
    assert other_preview["source"]["source_id"] != preview["source"]["source_id"]
    with pytest.raises(mig.MigrationRehearsalError) as excinfo:
        _apply(other_src, target, other_preview)
    assert excinfo.value.code == "target_source_mismatch"

    # Non-empty directory without a manifest is not a rehearsal target.
    stray = tmp_path / "stray"
    stray.mkdir()
    (stray / "not-a-manifest.txt").write_text("stray", encoding="utf-8")
    with pytest.raises(mig.MigrationRehearsalError) as excinfo:
        _apply(src, stray, preview)
    assert excinfo.value.code == "not_a_rehearsal_target"


# ---------------------------------------------------------------------------
# 9. Erasure / tombstone signals are respected.
# ---------------------------------------------------------------------------


def test_erasure_and_tombstone_signals(tmp_path):
    src = _create_snapshot(tmp_path)
    _add_conversation(src, SG, conv_id="c1", session_id="ownerA", identity_mode="self")
    _add_conversation(src, SG, conv_id="c2", session_id="ownerA", identity_mode="self")
    _add_message(src, SG, message_id=201, session_id="ownerA",
                 conversation_id="c1", content="忘れてほしい内容")
    _add_message(src, SG, message_id=202, session_id="ownerA",
                 conversation_id="c1", content="保持内容")
    _add_message(src, SG, message_id=203, session_id="ownerA",
                 conversation_id="c2", content="墓碑内容")
    conn = _memory(src, SG)
    try:
        conn.execute(
            """INSERT INTO conversation_erasure_requests(
                   request_id,session_id,identity_mode,conversation_id,action,
                   forget_long_term,source_message_ids,state,created_at
               ) VALUES('r1','ownerA','self','c1','forget',1,'[201]',
                        'completed','2026-08-03T00:00:00+00:00')"""
        )
        conn.execute(
            """INSERT INTO conversation_erasure_requests(
                   request_id,session_id,identity_mode,conversation_id,action,
                   forget_long_term,source_message_ids,state,created_at
               ) VALUES('r2','ownerA','self','c1','delete',0,'[202]',
                        'completed','2026-08-03T00:00:00+00:00')"""
        )
        tombstone_fp = hashlib.sha256(
            "ownerA:203:墓碑内容".encode("utf-8")
        ).hexdigest()
        conn.execute(
            """INSERT INTO memory_tombstones(
                   tombstone_id,session_id,identity_mode,fact_id,
                   source_fingerprint,semantic_fingerprint,reason,deleted_at
               ) VALUES('t1','ownerA','self','legacy-999',?,NULL,
                        'user_delete','2026-08-04T00:00:00+00:00')""",
            (tombstone_fp,),
        )
        conn.commit()
    finally:
        conn.close()

    _add_core_fact(src, SG, session_id="ownerA", fact_key="k_forget",
                   fact_value="将被遗忘", source_message_ids=[201])
    _add_core_fact(src, SG, session_id="ownerA", fact_key="k_retain",
                   fact_value="保持派生", source_message_ids=[202])
    _add_core_fact(src, SG, session_id="ownerA", fact_key="k_tomb",
                   fact_value="墓碑", source_message_ids=[203])

    preview = _preview(src)
    decisions = {i["fact_key"]: i for i in preview["items"]}
    assert (decisions["k_forget"]["decision"], decisions["k_forget"]["reason"]) == (
        "skip",
        "erasure_barrier",
    )
    assert (decisions["k_retain"]["decision"], decisions["k_retain"]["reason"]) == (
        "defer",
        "erasure_conflict",
    )
    assert (decisions["k_tomb"]["decision"], decisions["k_tomb"]["reason"]) == (
        "skip",
        "tombstone_source_deleted",
    )
    assert preview["summary"]["planned"] == 0


# ---------------------------------------------------------------------------
# 9b. D30 hard privacy blocks (existing classifier, no new categories).
# ---------------------------------------------------------------------------


def test_d30_blocks_sensitive_fact_and_sensitive_source(tmp_path):
    from app.services.memory_v11.privacy import classify_memory_sensitive_text

    secret_value = "password: synthetic-only-123!"
    secret_source = "my password is synthetic-only-123"
    assert classify_memory_sensitive_text(secret_value)  # sanity: hard hit

    src = _create_snapshot(tmp_path)
    _add_conversation(src, SG, conv_id="c1", session_id="ownerA", identity_mode="self")
    _add_message(src, SG, message_id=601, session_id="ownerA", conversation_id="c1",
                 content="普通内容一")
    _add_message(src, SG, message_id=602, session_id="ownerA", conversation_id="c1",
                 content=secret_source)
    # Fact body itself hits D30.
    _add_core_fact(src, SG, session_id="ownerA", fact_key="k_sensitive_fact",
                   fact_value=secret_value, source_message_ids=[601])
    # Ordinary fact body, but the source message content hits D30.
    _add_core_fact(src, SG, session_id="ownerA", fact_key="k_ordinary",
                   fact_value="普通事实", source_message_ids=[602])
    # Fully ordinary control fact.
    _add_core_fact(src, SG, session_id="ownerA", fact_key="k_normal",
                   fact_value="正常事实", source_message_ids=[601])

    before = _sqlite_state(src)
    preview = _preview(src)
    assert _sqlite_state(src) == before

    # The raw hit text never appears anywhere in the preview payload.
    raw = json.dumps(preview, ensure_ascii=False)
    assert "synthetic-only-123" not in raw
    assert secret_source not in raw

    by_row = {i["legacy_row_id"]: i for i in preview["items"]}
    # Fact-level hit: value and key redacted, only classes + locating ids kept.
    sensitive_fact = by_row[1]
    assert (sensitive_fact["decision"], sensitive_fact["reason"]) == (
        "skip",
        "sensitive_fact_blocked",
    )
    assert sensitive_fact["fact_value"] is None
    assert sensitive_fact["fact_key"] is None
    assert "sensitive_classes=password" in (sensitive_fact["detail"] or "")
    # Source-level hit with an ordinary fact body: value may be shown, the
    # matched source text never is; only classes + source ids are reported.
    sensitive_source = by_row[2]
    assert (sensitive_source["decision"], sensitive_source["reason"]) == (
        "skip",
        "sensitive_source_blocked",
    )
    assert sensitive_source["fact_value"] == "普通事实"
    assert "sensitive_classes=password" in (sensitive_source["detail"] or "")
    assert "602" in (sensitive_source["detail"] or "")
    # Control fact stays planned; no duplicate hints leak the blocked text.
    assert (by_row[3]["decision"], by_row[3]["reason"]) == ("planned", None)
    assert preview["duplicate_hints"] == []
    assert preview["summary"]["planned"] == 1

    target = tmp_path / "target"
    result = _apply(src, target, preview)
    assert result["ok"] is True
    assert [f["display_text"] for f in result["imported_facts"]] == [
        "k_normal: 正常事实"
    ]
    conn = _target_memory(target, SG)
    try:
        everything = json.dumps(
            [
                [str(r[0]) for r in conn.execute("SELECT display_text FROM stable_fact_versions").fetchall()],
                [str(r[0]) for r in conn.execute("SELECT excerpt FROM memory_observation_sources").fetchall()],
            ],
            ensure_ascii=False,
        )
        assert "synthetic-only-123" not in everything
        # Only the control fact landed (1 fact, 1 observation, 1 source row).
        assert conn.execute("SELECT COUNT(*) FROM stable_facts").fetchone()[0] == 1
        assert (
            conn.execute("SELECT COUNT(*) FROM memory_observations").fetchone()[0]
            == 1
        )
    finally:
        conn.close()
    assert _sqlite_state(src) == before


# ---------------------------------------------------------------------------
# 9c. Pin audit (C ruling: report only, original is_pinned kept).
# ---------------------------------------------------------------------------


def test_preview_reports_pin_audit_and_limit_violation(tmp_path):
    src = _create_snapshot(tmp_path)
    _add_conversation(src, SG, conv_id="c1", session_id="ownerA", identity_mode="self")
    for i in range(9):
        _add_message(src, SG, message_id=700 + i, session_id="ownerA",
                     conversation_id="c1", content=f"内容{i}")
        _add_core_fact(src, SG, session_id="ownerA", fact_key=f"pin_k{i}",
                       fact_value=f"置顶事实{i}", source_message_ids=[700 + i],
                       is_pinned=1)

    preview = _preview(src)
    audit = preview["pin_audit"]
    assert audit["pin_limit_per_scope"] == 8
    assert audit["any_exceeds_limit"] is True
    entry = next(
        e for e in audit["scopes"] if e["worldline"] == SG and e["session_id"] == "ownerA"
    )
    assert entry["planned_pinned"] == 9
    assert entry["exceeds_pin_limit"] is True
    assert "NOT eligible for production cutover" in audit["note"]

    # Original pins are preserved on import (no auto-unpin, no winner pick).
    target = tmp_path / "target"
    result = _apply(src, target, preview)
    assert result["ok"] is True
    conn = _target_memory(target, SG)
    try:
        assert (
            conn.execute("SELECT COUNT(*) FROM stable_facts WHERE is_pinned=1")
            .fetchone()[0]
            == 9
        )
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 10. Scoped retrieval isolation on the rehearsal target.
# ---------------------------------------------------------------------------


async def test_scoped_retrieval_isolation(tmp_path, use_data_dir):
    from app.services.memory_v11.retrieval import select_stable_fact_candidates
    from app.services.memory_v11.repository import create_stable_fact

    src = _create_snapshot(tmp_path)
    _add_conversation(src, SG, conv_id="c-self", session_id="ownerA", identity_mode="self")
    _add_message(src, SG, message_id=501, session_id="ownerA", conversation_id="c-self",
                 content="用户说喜欢看动漫")
    _add_core_fact(src, SG, session_id="ownerA", fact_key="likes_anime",
                   fact_value="喜欢看动漫", source_message_ids=[501])
    # Records that must never enter candidates or the target.
    _add_core_fact(src, SG, session_id="ownerA", fact_key="dismissed_old",
                   fact_value="已否定的旧事实", source_message_ids=[501], is_dismissed=1)
    _add_core_fact(src, SG, session_id="ownerA", fact_key="historical_old",
                   fact_value="历史旧事实", source_message_ids=[501], is_current=0)
    # BETA: same owner text in a different worldline.
    _add_conversation(src, BETA, conv_id="c-beta", session_id="ownerA", identity_mode="self")
    _add_message(src, BETA, message_id=501, session_id="ownerA", conversation_id="c-beta",
                 content="用户说喜欢看动漫")
    _add_core_fact(src, BETA, session_id="ownerA", fact_key="likes_anime",
                   fact_value="喜欢看动漫", source_message_ids=[501])

    # v11 demo fact + legacy episode + v11 experience + shared fact in source.
    use_data_dir(src)
    await create_stable_fact(
        session_id="ownerA", worldline=SG, identity_mode="self",
        display_text="v11-density 演示事实",
        semantic_json={"object": {"demo": "v11-density"}}, confidence=0.5,
    )
    conn = _memory(src, SG)
    try:
        conn.execute(
            """INSERT INTO episodic_memories(
                   session_id,identity_mode,content,confidence,importance,
                   source_message_ids,created_at
               ) VALUES('ownerA','self','旧经历内容',0.9,0.9,'[501]',
                        '2026-08-01T00:00:00+00:00')"""
        )
        conn.execute(
            """INSERT INTO memory_observations(
                   observation_id,session_id,identity_mode,display_text,
                   semantic_json,semantic_fingerprint,evidence_kind,memory_class,
                   confidence,status,created_at,updated_at
               ) VALUES('obs-exp','ownerA','self','旧经历观察','{}','fp',
                        'legacy_import','episodic',0.9,'attached',
                        '2026-08-01T00:00:00+00:00','2026-08-01T00:00:00+00:00')"""
        )
        conn.execute(
            """INSERT INTO experiences(
                   experience_id,session_id,conversation_id,identity_mode,
                   observation_id,display_text,semantic_json,semantic_fingerprint,
                   confidence,status,created_at,updated_at
               ) VALUES('exp-1','ownerA','c-self','self','obs-exp',
                        '旧经历','{}','fp-exp',0.9,'active',
                        '2026-08-01T00:00:00+00:00','2026-08-01T00:00:00+00:00')"""
        )
        conn.commit()
    finally:
        conn.close()
    control = _open(src / "control.sqlite3")
    try:
        control.executescript(CONTROL_SCHEMA)
        control.execute(
            """INSERT INTO shared_user_facts(
                   owner_session_id,fact_key,fact_value,confidence,importance,
                   is_current,is_pinned,origin_worldline,origin_conversation_id,
                   origin_identity_mode,source_message_ids,created_at
               ) VALUES('ownerA','shared_key','共享事实',0.9,0.8,1,0,
                        'steins_gate','c-self','self','[501]',
                        '2026-08-01T00:00:00+00:00')"""
        )
        control.commit()
    finally:
        control.close()

    preview = _preview(src)
    planned_keys = {
        (i["worldline"], i["fact_key"]) for i in preview["items"] if i["decision"] == "planned"
    }
    assert planned_keys == {(SG, "likes_anime"), (BETA, "likes_anime")}

    target = tmp_path / "target"
    result = _apply(src, target, preview)
    assert result["ok"] is True

    # Target contains ONLY the allowed imports.
    conn = _target_memory(target, SG)
    try:
        counts = {
            table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in (
                "stable_facts",
                "stable_fact_versions",
                "stable_fact_evidence",
                "memory_observations",
                "experiences",
                "episodic_memories",
                "core_facts",
            )
        }
        assert counts == {
            "stable_facts": 1,
            "stable_fact_versions": 1,
            "stable_fact_evidence": 1,
            "memory_observations": 1,
            "experiences": 0,
            "episodic_memories": 0,
            "core_facts": 0,
        }
        texts = [
            str(r[0])
            for r in conn.execute("SELECT display_text FROM stable_fact_versions").fetchall()
        ]
        assert texts == ["likes_anime: 喜欢看动漫"]
    finally:
        conn.close()
    assert not (target / "control.sqlite3").exists()

    # Actual scoped retrieval on the target (lexical; no embeddings stored).
    use_data_dir(target)
    candidates = await select_stable_fact_candidates(
        session_id="ownerA", worldline=SG, identity_mode="self", query="动漫"
    )
    assert [c["display_text"] for c in candidates] == ["likes_anime: 喜欢看动漫"]

    # Other owner / identity scopes see nothing.
    assert await select_stable_fact_candidates(
        session_id="ownerB", worldline=SG, identity_mode="self", query="动漫"
    ) == []
    assert await select_stable_fact_candidates(
        session_id="ownerA", worldline=SG, identity_mode="okabe", query="动漫"
    ) == []
    # BETA scope returns its own import, not the SG one.
    beta_candidates = await select_stable_fact_candidates(
        session_id="ownerA", worldline=BETA, identity_mode="self", query="动漫"
    )
    assert [c["display_text"] for c in beta_candidates] == ["likes_anime: 喜欢看动漫"]
    beta_conn = _target_memory(target, BETA)
    try:
        beta_fact_id = beta_conn.execute("SELECT fact_id FROM stable_facts").fetchone()[0]
    finally:
        beta_conn.close()
    sg_conn = _target_memory(target, SG)
    try:
        sg_fact_id = sg_conn.execute("SELECT fact_id FROM stable_facts").fetchone()[0]
    finally:
        sg_conn.close()
    assert beta_fact_id != sg_fact_id

    # Disapproved legacy records never entered the target at all.
    everything = json.dumps(
        [c["display_text"] for c in candidates]
        + [c["display_text"] for c in beta_candidates],
        ensure_ascii=False,
    )
    for forbidden in ("已否定的旧事实", "历史旧事实", "v11-density", "旧经历", "共享事实"):
        assert forbidden not in everything


# ---------------------------------------------------------------------------
# 11. CLI entry: path/env validation, preview/apply, stale rejection.
# ---------------------------------------------------------------------------


def _run_cli(*argv: str, env_extra: dict[str, str] | None = None):
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in ("AMADEUS_DATA_DIR", "AMADEUS_DB_PATH")
    }
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [sys.executable, str(SCRIPT), *argv],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(BACKEND_ROOT),
    )


def test_cli_refuses_env_overrides(tmp_path):
    src = _base_source(tmp_path)
    result = _run_cli(
        "preview", "--source", str(src), "--out", str(tmp_path / "p.json"),
        env_extra={"AMADEUS_DATA_DIR": str(tmp_path)},
    )
    assert result.returncode == 2
    assert "env_override_present" in result.stderr


def test_cli_preview_apply_and_stale_rejection(tmp_path):
    src = _base_source(tmp_path)
    preview_path = tmp_path / "preview.json"
    target = tmp_path / "target"

    preview = _run_cli(
        "preview", "--source", str(src), "--out", str(preview_path)
    )
    assert preview.returncode == 0, preview.stderr
    payload = json.loads(preview_path.read_text(encoding="utf-8"))
    assert payload["kind"] == mig.PREVIEW_KIND
    assert payload["summary"]["planned"] == 2
    assert "excerpt" not in json.dumps(payload)  # no source excerpts in preview

    apply_run = _run_cli(
        "apply", "--source", str(src), "--target", str(target),
        "--preview", str(preview_path),
    )
    assert apply_run.returncode == 0, apply_run.stderr
    result = json.loads(apply_run.stdout)
    assert result["ok"] is True
    assert result["worldlines"][SG]["imported"] == 1
    assert result["worldlines"][BETA]["imported"] == 1

    again = _run_cli(
        "apply", "--source", str(src), "--target", str(target),
        "--preview", str(preview_path),
    )
    assert again.returncode == 0, again.stderr
    assert json.loads(again.stdout)["worldlines"][SG]["already_imported"] == 1

    # Stale source: the old preview must be refused.
    _add_core_fact(src, SG, session_id="ownerA", fact_key="late_fact",
                   fact_value="迟到事实", source_message_ids=[101])
    stale = _run_cli(
        "apply", "--source", str(src), "--target", tmp_path / "target2",
        "--preview", str(preview_path),
    )
    assert stale.returncode == 2
    assert "preview_stale" in stale.stderr


def test_cli_refuses_live_appdata_paths(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    live_root = tmp_path / "Amadeus"
    inner_source = live_root / "snapshot"
    inner_source.mkdir(parents=True)
    # A source placed inside the (fake) live Amadeus data root must be
    # refused at the entry, before any app import or source validation.
    result = _run_cli(
        "preview", "--source", str(inner_source), "--out", str(tmp_path / "p.json")
    )
    assert result.returncode == 2
    assert "live_data_path" in result.stderr


def test_cli_refuses_paths_in_default_home_amadeus_without_appdata(tmp_path):
    """APPDATA absent must still cover app's ~/.amadeus fallback root."""
    fake_home = tmp_path / "fakehome"
    inner = fake_home / ".amadeus" / "snapshot"
    inner.mkdir(parents=True)
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in ("AMADEUS_DATA_DIR", "AMADEUS_DB_PATH", "APPDATA")
    }
    env["USERPROFILE"] = str(fake_home)  # controls Path.home() on Windows
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "preview", "--source", str(inner),
         "--out", str(tmp_path / "p.json")],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(BACKEND_ROOT),
    )
    assert result.returncode == 2
    assert "live_data_path" in result.stderr


# ---------------------------------------------------------------------------
# 12. CLI preview-output path protection (C reproduction #1: --out overwrite).
# ---------------------------------------------------------------------------


def test_cli_refuses_preview_output_inside_source(tmp_path):
    src = _base_source(tmp_path)
    before = _sqlite_state(src)
    result = _run_cli(
        "preview", "--source", str(src), "--out", str(src / "preview.json")
    )
    assert result.returncode == 2
    assert "output_path_forbidden" in result.stderr
    assert not (src / "preview.json").exists()
    assert _sqlite_state(src) == before


def test_cli_refuses_preview_output_over_existing_file(tmp_path):
    src = _base_source(tmp_path)
    out = tmp_path / "preview.json"
    out.write_text("existing notes", encoding="utf-8")
    result = _run_cli("preview", "--source", str(src), "--out", str(out))
    assert result.returncode == 2
    assert "output_exists" in result.stderr
    assert out.read_text(encoding="utf-8") == "existing notes"


def test_cli_refuses_preview_output_pointing_at_source_db(tmp_path):
    src = _base_source(tmp_path)
    db = src / "worldlines" / "sg" / "history.sqlite3"
    before = db.read_bytes()
    result = _run_cli("preview", "--source", str(src), "--out", str(db))
    assert result.returncode == 2
    assert db.read_bytes() == before


def test_cli_refuses_preview_output_via_junction_into_source(tmp_path):
    """A junction aliasing the source cannot smuggle the output inside it."""
    if os.name != "nt":
        pytest.skip("junction aliasing requires Windows")
    real = _base_source(tmp_path / "real")
    link = tmp_path / "link-src"
    created = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(real)],
        capture_output=True,
        text=True,
    )
    if created.returncode != 0:
        pytest.skip(f"junction creation unavailable: {created.stderr.strip()}")
    before = _sqlite_state(real)
    result = _run_cli(
        "preview", "--source", str(link), "--out", str(link / "preview.json")
    )
    assert result.returncode == 2
    assert "output_path_forbidden" in result.stderr
    assert not (real / "preview.json").exists()
    assert _sqlite_state(real) == before


def test_cli_refuses_source_database_escaping_via_internal_junction(tmp_path):
    if os.name != "nt":
        pytest.skip("junction aliasing requires Windows")
    src = _base_source(tmp_path / "source")
    escaped_sg = tmp_path / "escaped-sg"
    shutil.move(str(src / "worldlines" / "sg"), str(escaped_sg))
    link = src / "worldlines" / "sg"
    created = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(escaped_sg)],
        capture_output=True,
        text=True,
    )
    if created.returncode != 0:
        pytest.skip(f"junction creation unavailable: {created.stderr.strip()}")

    result = _run_cli(
        "preview", "--source", str(src), "--out", str(tmp_path / "p.json")
    )
    assert result.returncode == 2
    assert "path_escape" in result.stderr
    assert not (tmp_path / "p.json").exists()


def test_cli_refuses_target_database_escaping_via_internal_junction(tmp_path):
    if os.name != "nt":
        pytest.skip("junction aliasing requires Windows")
    src = _base_source(tmp_path / "source")
    target = tmp_path / "target"
    (target / "worldlines").mkdir(parents=True)
    escaped_sg = tmp_path / "escaped-target-sg"
    escaped_sg.mkdir()
    created = subprocess.run(
        [
            "cmd", "/c", "mklink", "/J",
            str(target / "worldlines" / "sg"), str(escaped_sg),
        ],
        capture_output=True,
        text=True,
    )
    if created.returncode != 0:
        pytest.skip(f"junction creation unavailable: {created.stderr.strip()}")
    preview = tmp_path / "preview.json"
    preview.write_text("{}", encoding="utf-8")

    result = _run_cli(
        "apply", "--source", str(src), "--target", str(target),
        "--preview", str(preview),
    )
    assert result.returncode == 2
    assert "path_escape" in result.stderr
    assert list(escaped_sg.iterdir()) == []


def test_cli_refuses_existing_source_target_file_alias(tmp_path):
    src = _base_source(tmp_path / "source")
    target_db = tmp_path / "target" / "worldlines" / "sg" / "memory.sqlite3"
    target_db.parent.mkdir(parents=True)
    try:
        os.link(src / "worldlines" / "sg" / "memory.sqlite3", target_db)
    except OSError as exc:
        pytest.skip(f"hard-link creation unavailable: {exc}")
    preview = tmp_path / "preview.json"
    preview.write_text("{}", encoding="utf-8")

    result = _run_cli(
        "apply", "--source", str(src),
        "--target", str(tmp_path / "target"), "--preview", str(preview),
    )
    assert result.returncode == 2
    assert "overlapping_paths" in result.stderr


def test_cli_refuses_apply_preview_inside_owned_or_live_roots(tmp_path):
    src = _base_source(tmp_path / "source")
    target = tmp_path / "target"

    source_preview = src / "review.json"
    source_preview.write_text("{}", encoding="utf-8")
    source_result = _run_cli(
        "apply", "--source", str(src), "--target", str(target),
        "--preview", str(source_preview),
    )
    assert source_result.returncode == 2
    assert "preview_path_forbidden" in source_result.stderr

    target.mkdir()
    target_preview = target / "review.json"
    target_preview.write_text("{}", encoding="utf-8")
    target_result = _run_cli(
        "apply", "--source", str(src), "--target", str(target),
        "--preview", str(target_preview),
    )
    assert target_result.returncode == 2
    assert "preview_path_forbidden" in target_result.stderr

    live_preview = tmp_path / "appdata" / "Amadeus" / "review.json"
    live_preview.parent.mkdir(parents=True)
    live_preview.write_text("{}", encoding="utf-8")
    live_result = _run_cli(
        "apply", "--source", str(src), "--target", str(tmp_path / "target2"),
        "--preview", str(live_preview),
        env_extra={"APPDATA": str(tmp_path / "appdata")},
    )
    assert live_result.returncode == 2
    assert "preview_path_forbidden" in live_result.stderr


def test_cli_resolves_live_data_root_before_comparison(tmp_path):
    if os.name != "nt":
        pytest.skip("junction aliasing requires Windows")
    appdata = tmp_path / "appdata"
    appdata.mkdir()
    real_live = tmp_path / "real-live"
    source = real_live / "snapshot"
    source.mkdir(parents=True)
    created = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(appdata / "Amadeus"), str(real_live)],
        capture_output=True,
        text=True,
    )
    if created.returncode != 0:
        pytest.skip(f"junction creation unavailable: {created.stderr.strip()}")

    result = _run_cli(
        "preview", "--source", str(source), "--out", str(tmp_path / "p.json"),
        env_extra={"APPDATA": str(appdata)},
    )
    assert result.returncode == 2
    assert "live_data_path" in result.stderr


def test_preview_output_race_uses_exclusive_create(tmp_path, monkeypatch, capsys):
    out = tmp_path / "preview.json"

    def race_output_into_place(_source: str) -> dict:
        out.write_text("created by another process", encoding="utf-8")
        return {}

    monkeypatch.setattr(mig, "build_legacy_core_facts_preview", race_output_into_place)
    args = rehearsal_cli._build_parser().parse_args(
        ["preview", "--source", str(tmp_path / "source"), "--out", str(out)]
    )
    with pytest.raises(SystemExit) as excinfo:
        rehearsal_cli._run_preview(args)

    assert excinfo.value.code == 2
    assert "output_exists" in capsys.readouterr().err
    assert out.read_text(encoding="utf-8") == "created by another process"
