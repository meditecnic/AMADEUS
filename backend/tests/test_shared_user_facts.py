"""Q18-SHARED-FACTS-FOUNDATION-01: cross-worldline shared stable user facts.

Frozen semantics under test:
- shared = local user profile keyed by the durable session_id (control.sqlite3
  only); never a cloud account, never cross-device sync.
- Only explicit identity_mode=self writes may enter the shared store; okabe is
  fail-closed on both write and read.
- Automatic extraction keeps writing worldline-local memory only.
- Local core fact with the same fact_key wins; the shared value is suppressed.
- Minimal auditable provenance is mandatory and verified against the origin
  worldline history DB before any control write.
"""
from __future__ import annotations

import asyncio
import json
import sqlite3

import pytest

from app import db as db_module
from app import models
from app.db import (
    default_conversation_id,
    init_db,
    reset_initialization_cache,
    validate_schema,
)
from app.services.conversations import conversation_service
from app.services.memory import (
    CoreFactCandidate,
    ExtractionResult,
    memory_service,
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


def _candidate(key: str = "favorite_drink", value: str = "コーヒー", ids: list[int] | None = None) -> CoreFactCandidate:
    return CoreFactCandidate(
        fact_key=key,
        fact_value=value,
        confidence=0.9,
        importance=0.7,
        # None means "default sample"; an explicit empty list must NOT be
        # masked here — it has to reach validation and fail closed.
        source_message_ids=[1] if ids is None else ids,
    )


async def _seed_self_turn(
    session_id: str,
    worldline: str,
    texts: list[str],
    roles: list[str] | None = None,
) -> tuple[str, list[int]]:
    """Create a REAL persisted self conversation and write messages into it.

    Positive shared-fact paths must come from a conversation whose stored
    identity_mode is 'self' — never from the default (okabe) conversation.
    """
    conversation = await conversation_service.create(
        session_id, worldline, identity_mode="self"
    )
    conversation_id = str(conversation["id"])
    role_list = roles or ["user"] * len(texts)
    message_ids = [
        await models.save_message(
            session_id, role, text, worldline=worldline, conversation_id=conversation_id
        )
        for role, text in zip(role_list, texts)
    ]
    return conversation_id, message_ids


async def _seed_okabe_turn(session_id: str, worldline: str, texts: list[str]) -> tuple[str, list[int]]:
    """Attack-path fixture: the default conversation persists identity_mode=okabe."""
    message_ids = [
        await models.save_message(session_id, "user", text, worldline=worldline)
        for text in texts
    ]
    return default_conversation_id(session_id, worldline), message_ids


async def _write_shared(
    owner: str,
    conversation_id: str,
    message_ids: list[int],
    *,
    key: str = "favorite_drink",
    value: str = "コーヒー",
    worldline: str = "steins_gate",
    mode: str = "self",
) -> int:
    return await memory_service.upsert_shared_user_fact(
        owner,
        _candidate(key=key, value=value, ids=message_ids),
        origin_worldline=worldline,
        origin_conversation_id=conversation_id,
        origin_identity_mode=mode,
    )


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


# ---------------------------------------------------------------------------
# B. Read/write matrix
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_self_shared_fact_visible_across_worldlines_without_beta_mirror(isolated_store):
    owner = "q18-owner"
    cid, mids = await _seed_self_turn(owner, "steins_gate", ["私はコーヒーが好き。"])
    await _write_shared(owner, cid, mids)

    beta_view = await memory_service.select_core_facts(
        owner, "beta", "favorite_drink", identity_mode="self"
    )
    assert any("コーヒー" in item.content for item in beta_view)
    shared_items = [item for item in beta_view if "コーヒー" in item.content]
    assert all(item.id < 0 for item in shared_items), "shared rows must use negative ids"

    # No mirror row may appear in the beta worldline memory store.
    beta_memory = sqlite3.connect(db_module._resolve_path("beta", "memory"))
    try:
        count = beta_memory.execute("SELECT COUNT(*) FROM core_facts").fetchone()[0]
    finally:
        beta_memory.close()
    assert count == 0


@pytest.mark.asyncio
async def test_okabe_read_and_write_are_fail_closed(isolated_store):
    owner = "q18-okabe"
    cid, mids = await _seed_self_turn(owner, "steins_gate", ["コーヒーが好き。"])
    await _write_shared(owner, cid, mids)

    for worldline in ("steins_gate", "beta"):
        okabe_view = await memory_service.select_core_facts(
            owner, worldline, "favorite_drink", identity_mode="okabe"
        )
        assert not any("コーヒー" in item.content for item in okabe_view), worldline

    # okabe writes are rejected regardless of the origin worldline.
    with pytest.raises(ValueError):
        await _write_shared(owner, cid, mids, key="hobby", value="読書", mode="okabe")
    beta_cid, beta_mids = await _seed_self_turn(owner, "beta", ["βでの発言。"])
    with pytest.raises(ValueError):
        await _write_shared(
            owner, beta_cid, beta_mids,
            key="hobby", value="読書", worldline="beta", mode="okabe",
        )
    assert not any(row["fact_key"] == "hobby" for row in _shared_rows())


@pytest.mark.asyncio
async def test_blank_owner_session_id_is_fail_closed(isolated_store):
    """An empty/whitespace owner must never create a shared profile."""
    for owner in ("", "   "):
        cid, mids = await _seed_okabe_turn(owner, "steins_gate", ["空のオーナー。"])
        with pytest.raises(ValueError):
            await _write_shared(owner, cid, mids)
    assert _shared_rows() == []


@pytest.mark.asyncio
async def test_self_beta_shared_write_is_visible_in_self_sg(isolated_store):
    """Matrix completion: β/self explicit write → SG/self read."""
    owner = "q18-beta-origin"
    beta_cid, beta_mids = await _seed_self_turn(owner, "beta", ["紅茶が好き。"])
    await _write_shared(
        owner, beta_cid, beta_mids, value="紅茶", worldline="beta"
    )

    sg_view = await memory_service.select_core_facts(
        owner, "steins_gate", "favorite_drink", identity_mode="self"
    )
    assert any("紅茶" in item.content for item in sg_view)
    rows = _shared_rows()
    assert rows[0]["origin_worldline"] == "beta"


@pytest.mark.asyncio
async def test_local_and_shared_ids_coexist_without_collision(isolated_store):
    """Same raw row id on both stores: shared surfaces as -1, local as +1."""
    owner = "q18-idspace"
    cid, mids = await _seed_self_turn(owner, "steins_gate", ["コーヒーが好き。"])
    shared_id = await _write_shared(owner, cid, mids, key="fact_a", value="コーヒー")
    local_id = await memory_service.upsert_core_fact(
        owner, "beta", _candidate(key="fact_b", value="読書", ids=[1]),
        identity_mode="self",
    )
    # Both stores really assigned their first autoincrement id.
    assert shared_id == 1 and local_id == 1

    merged = await memory_service.select_core_facts(
        owner, "beta", "fact", identity_mode="self"
    )
    by_key = {item.content.split(":")[0]: item for item in merged}
    assert "fact_a" in by_key and "fact_b" in by_key
    assert by_key["fact_a"].id == -1  # shared → negative control-row id
    assert by_key["fact_b"].id == 1   # local → positive memory-row id
    ids = [item.id for item in merged]
    assert len(ids) == len(set(ids))


@pytest.mark.asyncio
async def test_local_fact_overrides_shared_same_key_and_merged_ids_unique(isolated_store):
    owner = "q18-priority"
    cid, mids = await _seed_self_turn(owner, "steins_gate", ["コーヒーが好き。"])
    await _write_shared(owner, cid, mids)

    # β/self writes a local fact under the same key: β sees local only.
    await memory_service.upsert_core_fact(
        owner,
        "beta",
        _candidate(value="紅茶", ids=[1]),
        identity_mode="self",
    )
    beta_view = await memory_service.select_core_facts(
        owner, "beta", "favorite_drink", identity_mode="self"
    )
    drink_items = [item for item in beta_view if "favorite_drink" in item.content]
    assert len(drink_items) == 1
    assert "紅茶" in drink_items[0].content
    assert "コーヒー" not in " ".join(item.content for item in beta_view)

    # SG/self still resolves to the shared value.
    sg_view = await memory_service.select_core_facts(
        owner, "steins_gate", "favorite_drink", identity_mode="self"
    )
    assert any("コーヒー" in item.content for item in sg_view)

    # Merged ids stay unique ints (local positive, shared negative).
    ids = [item.id for item in beta_view]
    assert len(ids) == len(set(ids))
    assert all(isinstance(item_id, int) for item_id in ids)


@pytest.mark.asyncio
async def test_shared_same_key_supersession_keeps_audit_chain(isolated_store):
    owner = "q18-supersede"
    cid, mids = await _seed_self_turn(
        owner, "steins_gate", ["コーヒーが好き。", "やっぱり紅茶が好き。"]
    )
    first = await _write_shared(owner, cid, [mids[0]], value="コーヒー")
    second = await _write_shared(owner, cid, [mids[1]], value="紅茶")

    rows = {row["id"]: row for row in _shared_rows()}
    assert rows[first]["is_current"] == 0
    assert rows[first]["superseded_by"] == second
    assert rows[second]["is_current"] == 1
    assert rows[second]["superseded_by"] is None
    # Old provenance stays auditable after supersession — including the raw
    # JSON evidence list, byte for byte.
    assert rows[first]["origin_worldline"] == "steins_gate"
    assert rows[first]["origin_conversation_id"] == cid
    assert rows[first]["origin_identity_mode"] == "self"
    assert rows[first]["source_message_ids"] == json.dumps([mids[0]])
    assert rows[second]["source_message_ids"] == json.dumps([mids[1]])


# ---------------------------------------------------------------------------
# C. Provenance verification
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_provenance_verification_is_fail_closed(isolated_store):
    owner = "q18-prov"
    other = "q18-intruder"
    cid, mids = await _seed_self_turn(owner, "steins_gate", ["コーヒーが好き。"])
    other_cid, other_mids = await _seed_self_turn(other, "steins_gate", ["別人の発言。"])
    beta_cid, beta_mids = await _seed_self_turn(owner, "beta", ["β線の発言。"])

    cases = [
        # forged / nonexistent message id
        {"conversation_id": cid, "message_ids": [999999]},
        # message from another session's conversation
        {"conversation_id": cid, "message_ids": other_mids},
        # conversation not owned by the writer
        {"conversation_id": other_cid, "message_ids": other_mids},
        # nonexistent conversation
        {"conversation_id": "no-such-conversation", "message_ids": mids},
        # cross-worldline (declared origin has no such data): SG evidence
        # declared as beta origin fails inside the beta history DB.
        {"conversation_id": cid, "message_ids": mids, "worldline": "beta"},
        # cross-worldline (real β conversation + β message declared as SG
        # origin): must fail in the SG history DB — no cross-line fallback.
        {"conversation_id": beta_cid, "message_ids": beta_mids, "worldline": "steins_gate"},
        # non-positive id
        {"conversation_id": cid, "message_ids": [0]},
        {"conversation_id": cid, "message_ids": [-5]},
        # duplicated evidence ids
        {"conversation_id": cid, "message_ids": [mids[0], mids[0]]},
    ]
    for case in cases:
        with pytest.raises(ValueError):
            await _write_shared(
                owner,
                case["conversation_id"],
                case["message_ids"],
                worldline=case.get("worldline", "steins_gate"),
            )
    assert _shared_rows() == []

    # Sanity: the honest write with the same session still succeeds afterwards.
    await _write_shared(owner, cid, mids)
    assert len(_shared_rows()) == 1


@pytest.mark.asyncio
async def test_real_okabe_conversation_cannot_be_promoted_by_claiming_self(isolated_store):
    """Q23 P0: the caller's origin_identity_mode claim never overrides the
    persisted conversation identity. Evidence from a real okabe conversation
    must stay out of the shared profile even when the caller claims self."""
    owner = "q18-claim-attack"
    cid, mids = await _seed_okabe_turn(owner, "steins_gate", ["私はコーヒーが好き。"])
    with pytest.raises(ValueError):
        await _write_shared(owner, cid, mids, mode="self")
    assert _shared_rows() == []


@pytest.mark.asyncio
async def test_persisted_self_conversation_requires_self_claim(isolated_store):
    """Claim/persisted consistency: a self conversation with an okabe claim fails."""
    owner = "q18-claim-mismatch"
    cid, mids = await _seed_self_turn(owner, "steins_gate", ["コーヒーが好き。"])
    with pytest.raises(ValueError):
        await _write_shared(owner, cid, mids, mode="okabe")
    assert _shared_rows() == []


@pytest.mark.asyncio
async def test_assistant_message_cannot_source_shared_user_fact(isolated_store):
    """Shared facts are user-owned: Amadeus's own words are not evidence."""
    owner = "q18-assistant-src"
    cid, mids = await _seed_self_turn(
        owner, "steins_gate", ["[EMO:neutral] コーヒーが好きなのね。"], roles=["assistant"]
    )
    with pytest.raises(ValueError):
        await _write_shared(owner, cid, mids)
    assert _shared_rows() == []


@pytest.mark.asyncio
async def test_mixed_roles_cannot_source_shared_user_fact(isolated_store):
    """user + assistant mixed evidence is rejected as a whole."""
    owner = "q18-mixed-src"
    cid, mids = await _seed_self_turn(
        owner,
        "steins_gate",
        ["コーヒーが好き。", "[EMO:neutral] 覚えたわ。"],
        roles=["user", "assistant"],
    )
    with pytest.raises(ValueError):
        await _write_shared(owner, cid, mids)
    assert _shared_rows() == []


@pytest.mark.asyncio
async def test_empty_source_evidence_is_fail_closed(isolated_store):
    """An explicit empty evidence list must fail at candidate validation."""
    with pytest.raises(ValueError):
        CoreFactCandidate(
            fact_key="favorite_drink",
            fact_value="コーヒー",
            confidence=0.9,
            importance=0.7,
            source_message_ids=[],
        )
    assert _shared_rows() == []


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


# ---------------------------------------------------------------------------
# D. Quiet Memory boundaries
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_auto_extraction_never_touches_shared_store(isolated_store):
    owner = "q18-extract"
    extraction = ExtractionResult(
        working_summary="",
        episodic=[],
        core_facts=[_candidate(value="コーヒー", ids=[1])],
    )
    await memory_service.apply_extraction(
        owner, "steins_gate", extraction, identity_mode="self"
    )
    assert _shared_rows() == []
    local = await memory_service.select_core_facts(
        owner, "steins_gate", "favorite_drink", identity_mode="self"
    )
    assert any("コーヒー" in item.content for item in local)


@pytest.mark.asyncio
async def test_local_forget_leaves_shared_store_intact(isolated_store):
    owner = "q18-forget"
    cid, mids = await _seed_self_turn(owner, "steins_gate", ["コーヒーが好き。"])
    await _write_shared(owner, cid, mids)
    await memory_service.upsert_core_fact(
        owner, "steins_gate", _candidate(key="hobby", value="読書", ids=[1]),
        identity_mode="self",
    )

    await memory_service.forget(owner, "steins_gate", scope="all")

    assert len(_shared_rows()) == 1  # shared store untouched by local forget
    local = await memory_service.select_core_facts(
        owner, "steins_gate", "hobby", identity_mode="self"
    )
    assert not any("読書" in item.content for item in local)


# ---------------------------------------------------------------------------
# E. Prompt compilation via the existing call chain
# ---------------------------------------------------------------------------

class _FakeSession:
    def __init__(self, session_id: str, worldline: str, identity_mode: str):
        self.session_id = session_id
        self.worldline = worldline
        self.identity_mode = identity_mode
        self.base_system_prompt = ""
        self.system_prompt = ""
        self.memory_summary = ""
        self.history: list[dict[str, str]] = []
        self.self_name = ""
        self.identity_acknowledged = False


@pytest.mark.asyncio
async def test_prompt_beta_self_includes_shared_but_beta_okabe_excludes(isolated_store):
    from app.services.prompt_compiler import compile_for_session

    owner = "q18-prompt"
    cid, mids = await _seed_self_turn(owner, "steins_gate", ["コーヒーが好き。"])
    await _write_shared(owner, cid, mids)

    self_prompt = await compile_for_session(
        _FakeSession(owner, "beta", "self"), "飲み物の話をしよう"
    )
    assert "コーヒー" in self_prompt

    okabe_prompt = await compile_for_session(
        _FakeSession(owner, "beta", "okabe"), "飲み物の話をしよう"
    )
    assert "コーヒー" not in okabe_prompt


# ---------------------------------------------------------------------------
# F. Concurrency / adjacent writes
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_concurrent_same_key_writes_leave_single_current_chain(isolated_store):
    owner = "q18-race"
    cid, mids = await _seed_self_turn(
        owner, "steins_gate", ["コーヒーが好き。", "紅茶も好き。"]
    )
    results = await asyncio.gather(
        _write_shared(owner, cid, [mids[0]], value="コーヒー"),
        _write_shared(owner, cid, [mids[1]], value="紅茶"),
        return_exceptions=True,
    )
    # Both adjacent writes must complete — a lock failure may not hide here.
    assert all(isinstance(result, int) for result in results), results

    rows = {row["id"]: row for row in _shared_rows()}
    assert len(rows) == 2
    current = [row for row in rows.values() if row["is_current"] == 1]
    assert len(current) == 1
    # Do not assume which value wins; the chain itself must stay complete.
    assert current[0]["superseded_by"] is None
    superseded = [row for row in rows.values() if row["is_current"] == 0]
    assert len(superseded) == 1
    assert superseded[0]["superseded_by"] == current[0]["id"]
    assert superseded[0]["fact_key"] == current[0]["fact_key"]
