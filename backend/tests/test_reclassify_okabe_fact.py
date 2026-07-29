"""GATE7B-OKABE-RECLASSIFY-01: reclassify okabe facts to self scope.

Frozen semantics under test:
- An okabe-scope core fact can be reclassified to self scope.
- The original okabe fact is retained untouched.
- A new self-scope core fact is created with copied key/value/confidence/importance.
- source_message_ids are preserved exactly.
- Evidence chain (source messages) must survive validation.
- Zero LLM/Provider/TTS involvement.
"""
from __future__ import annotations

import asyncio
import json
import sqlite3

import pytest

from app import db as db_module
from app import models
from app.db import default_conversation_id, init_db, reset_initialization_cache
from app.services.conversations import conversation_service
from app.services.memory import (
    CoreFactCandidate,
    ReclassifyError,
    memory_service,
)


@pytest.fixture
async def isolated_store(tmp_path, monkeypatch):
    monkeypatch.delenv("AMADEUS_DB_PATH", raising=False)
    monkeypatch.setenv("AMADEUS_DATA_DIR", str(tmp_path))
    reset_initialization_cache()
    await init_db()
    return tmp_path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _seed_okabe_turn(
    session_id: str, worldline: str, texts: list[str]
) -> tuple[str, list[int]]:
    """The default conversation persists identity_mode=okabe."""
    message_ids = [
        await models.save_message(session_id, "user", text, worldline=worldline)
        for text in texts
    ]
    return default_conversation_id(session_id, worldline), message_ids


async def _seed_okabe_fact(
    owner: str,
    worldline: str,
    *,
    key: str = "favorite_drink",
    value: str = "コーヒー",
    pinned: bool = False,
) -> tuple[int, str, list[int]]:
    """Persist a real okabe conversation, user messages, and an okabe-scope core fact."""
    cid, mids = await _seed_okabe_turn(owner, worldline, [f"私は{value}が好き。"])
    fact_id = await memory_service.upsert_core_fact(
        owner,
        worldline,
        CoreFactCandidate(
            fact_key=key,
            fact_value=value,
            confidence=0.9,
            importance=0.7,
            source_message_ids=mids,
            is_pinned=pinned,
        ),
        identity_mode="okabe",
    )
    return fact_id, cid, mids


async def _seed_self_turn(
    session_id: str,
    worldline: str,
    texts: list[str],
    roles: list[str] | None = None,
) -> tuple[str, list[int]]:
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


async def _seed_self_fact(
    owner: str,
    worldline: str,
    *,
    key: str = "favorite_drink",
    value: str = "コーヒー",
) -> tuple[int, str, list[int]]:
    cid, mids = await _seed_self_turn(owner, worldline, [f"私は{value}が好き。"])
    fact_id = await memory_service.upsert_core_fact(
        owner,
        worldline,
        CoreFactCandidate(
            fact_key=key,
            fact_value=value,
            confidence=0.9,
            importance=0.7,
            source_message_ids=mids,
        ),
        identity_mode="self",
    )
    return fact_id, cid, mids


def _memory_rows(
    worldline: str, session_id: str, identity_mode: str
) -> list[sqlite3.Row]:
    connection = sqlite3.connect(db_module._resolve_path(worldline, "memory"))
    connection.row_factory = sqlite3.Row
    try:
        return connection.execute(
            "SELECT * FROM core_facts WHERE session_id=? AND identity_mode=? ORDER BY id",
            (session_id, identity_mode),
        ).fetchall()
    finally:
        connection.close()


def _memory_row_by_id(worldline: str, fact_id: int) -> sqlite3.Row | None:
    connection = sqlite3.connect(db_module._resolve_path(worldline, "memory"))
    connection.row_factory = sqlite3.Row
    try:
        return connection.execute(
            "SELECT * FROM core_facts WHERE id=?", (fact_id,)
        ).fetchone()
    finally:
        connection.close()


# ---------------------------------------------------------------------------
# T1: Happy path
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_reclassify_okabe_fact_to_self_happy_path(isolated_store):
    owner = "g7b-happy"
    wl = "steins_gate"
    okabe_fact_id, cid, mids = await _seed_okabe_fact(owner, wl)

    self_fact_id, changed = await memory_service.reclassify_okabe_fact_to_self(
        owner, wl, okabe_fact_id, confirmed=True
    )
    assert changed is True
    assert self_fact_id > 0

    rows = _memory_rows(wl, owner, "self")
    self_row = next(r for r in rows if r["id"] == self_fact_id)
    okabe_row = _memory_row_by_id(wl, okabe_fact_id)

    assert self_row["fact_key"] == okabe_row["fact_key"]
    assert self_row["fact_value"] == okabe_row["fact_value"]
    assert self_row["confidence"] == okabe_row["confidence"]
    assert self_row["importance"] == okabe_row["importance"]
    assert json.loads(self_row["source_message_ids"]) == json.loads(
        okabe_row["source_message_ids"]
    )


# ---------------------------------------------------------------------------
# T2: source_message_ids preserved exactly
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_reclassify_preserves_source_message_ids(isolated_store):
    owner = "g7b-src-ids"
    wl = "steins_gate"
    # Create okabe fact with multiple source messages
    cid, mids = await _seed_okabe_turn(
        owner, wl, ["私はコーヒーが好き。", "紅茶も好き。"]
    )
    okabe_fact_id = await memory_service.upsert_core_fact(
        owner,
        wl,
        CoreFactCandidate(
            fact_key="drink",
            fact_value="コーヒー",
            confidence=0.9,
            importance=0.7,
            source_message_ids=mids,
        ),
        identity_mode="okabe",
    )

    self_fact_id, _ = await memory_service.reclassify_okabe_fact_to_self(
        owner, wl, okabe_fact_id, confirmed=True
    )
    self_row = _memory_row_by_id(wl, self_fact_id)
    okabe_row = _memory_row_by_id(wl, okabe_fact_id)
    assert json.loads(self_row["source_message_ids"]) == json.loads(
        okabe_row["source_message_ids"]
    )
    assert json.loads(self_row["source_message_ids"]) == mids


# ---------------------------------------------------------------------------
# T3: Nonexistent fact
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_reclassify_nonexistent_fact_returns_not_found(isolated_store):
    owner = "g7b-noexist"
    wl = "steins_gate"
    with pytest.raises(ReclassifyError) as excinfo:
        await memory_service.reclassify_okabe_fact_to_self(
            owner, wl, 999999, confirmed=True
        )
    assert excinfo.value.code == "fact_not_found"


# ---------------------------------------------------------------------------
# T4: Self fact rejected
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_reclassify_self_fact_rejected(isolated_store):
    owner = "g7b-self-reject"
    wl = "steins_gate"
    self_fact_id, _, _ = await _seed_self_fact(owner, wl)
    with pytest.raises(ReclassifyError) as excinfo:
        await memory_service.reclassify_okabe_fact_to_self(
            owner, wl, self_fact_id, confirmed=True
        )
    assert excinfo.value.code == "not_okabe_fact"


# ---------------------------------------------------------------------------
# T5: Cross-session rejected
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_reclassify_cross_session_rejected(isolated_store):
    owner = "g7b-owner"
    stranger = "g7b-stranger"
    wl = "steins_gate"
    okabe_fact_id, _, _ = await _seed_okabe_fact(owner, wl)
    with pytest.raises(ReclassifyError) as excinfo:
        await memory_service.reclassify_okabe_fact_to_self(
            stranger, wl, okabe_fact_id, confirmed=True
        )
    assert excinfo.value.code == "fact_not_found"


# ---------------------------------------------------------------------------
# T5b: Wrong worldline rejected
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_reclassify_wrong_worldline_rejected(isolated_store):
    owner = "g7b-wl-reject"
    okabe_fact_id, _, _ = await _seed_okabe_fact(owner, "steins_gate")
    with pytest.raises(ReclassifyError) as excinfo:
        await memory_service.reclassify_okabe_fact_to_self(
            owner, "beta", okabe_fact_id, confirmed=True
        )
    assert excinfo.value.code == "fact_not_found"


# ---------------------------------------------------------------------------
# T6: Duplicate fact key supersedes
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_reclassify_duplicate_fact_key_supersedes(isolated_store):
    owner = "g7b-supersede"
    wl = "steins_gate"
    okabe_fact_id, _, _ = await _seed_okabe_fact(owner, wl, key="drink", value="コーヒー")

    # Pre-existing self fact with same key but different value
    old_self_id, _, _ = await _seed_self_fact(owner, wl, key="drink", value="水")

    new_self_id, changed = await memory_service.reclassify_okabe_fact_to_self(
        owner, wl, okabe_fact_id, confirmed=True
    )
    assert changed is True
    assert new_self_id != old_self_id

    old_row = _memory_row_by_id(wl, old_self_id)
    new_row = _memory_row_by_id(wl, new_self_id)
    assert old_row["is_current"] == 0
    assert old_row["superseded_by"] == new_self_id
    assert new_row["is_current"] == 1


# ---------------------------------------------------------------------------
# T7: Same fact reclassified twice returns existing
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_reclassify_same_fact_twice_returns_existing(isolated_store):
    owner = "g7b-idempotent"
    wl = "steins_gate"
    okabe_fact_id, _, _ = await _seed_okabe_fact(owner, wl)

    id1, changed1 = await memory_service.reclassify_okabe_fact_to_self(
        owner, wl, okabe_fact_id, confirmed=True
    )
    id2, changed2 = await memory_service.reclassify_okabe_fact_to_self(
        owner, wl, okabe_fact_id, confirmed=True
    )
    assert id1 == id2
    assert changed1 is True
    assert changed2 is False

    rows = _memory_rows(wl, owner, "self")
    current = [r for r in rows if r["is_current"] == 1]
    assert len(current) == 1
    # No supersede chain for same-value re-reclassify
    assert current[0]["superseded_by"] is None


# ---------------------------------------------------------------------------
# T7b: Concurrent reclassify same fact — single current
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_reclassify_concurrent_same_fact_single_current(isolated_store):
    owner = "g7b-race"
    wl = "steins_gate"
    okabe_fact_id, _, _ = await _seed_okabe_fact(owner, wl)

    results = await asyncio.gather(
        memory_service.reclassify_okabe_fact_to_self(owner, wl, okabe_fact_id, confirmed=True),
        memory_service.reclassify_okabe_fact_to_self(owner, wl, okabe_fact_id, confirmed=True),
        return_exceptions=True,
    )
    assert all(isinstance(r, tuple) for r in results), results
    ids = {r[0] for r in results}
    assert len(ids) == 1
    changed_vals = sorted(r[1] for r in results)
    assert changed_vals == [False, True]

    rows = _memory_rows(wl, owner, "self")
    current = [r for r in rows if r["is_current"] == 1]
    assert len(current) == 1
    # No dangling supersede chain
    for r in rows:
        sb = r["superseded_by"]
        if sb is not None:
            target = _memory_row_by_id(wl, sb)
            assert target is not None, f"superseded_by {sb} points to nonexistent row"


# ---------------------------------------------------------------------------
# T8: Okabe fact retained after reclassify
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_reclassify_okabe_fact_retained(isolated_store):
    owner = "g7b-retained"
    wl = "steins_gate"
    okabe_fact_id, _, _ = await _seed_okabe_fact(owner, wl)

    await memory_service.reclassify_okabe_fact_to_self(
        owner, wl, okabe_fact_id, confirmed=True
    )
    okabe_rows = _memory_rows(wl, owner, "okabe")
    okabe_row = next(r for r in okabe_rows if r["id"] == okabe_fact_id)
    assert okabe_row["is_current"] == 1


# ---------------------------------------------------------------------------
# T9: Requires confirmed flag
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_reclassify_requires_confirmed_flag(isolated_store):
    owner = "g7b-confirm"
    wl = "steins_gate"
    okabe_fact_id, _, _ = await _seed_okabe_fact(owner, wl)

    with pytest.raises(ReclassifyError) as excinfo:
        await memory_service.reclassify_okabe_fact_to_self(
            owner, wl, okabe_fact_id, confirmed=False
        )
    assert excinfo.value.code == "confirmation_required"


# ---------------------------------------------------------------------------
# T9b: Broken evidence chain rejected
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_reclassify_broken_evidence_chain_rejected(isolated_store):
    owner = "g7b-broken-ev"
    wl = "steins_gate"
    okabe_fact_id, _, mids = await _seed_okabe_fact(owner, wl)

    # Delete source message from history DB
    history = sqlite3.connect(db_module._resolve_path(wl, "history"))
    try:
        history.execute("DELETE FROM messages WHERE id=?", (mids[0],))
        history.commit()
    finally:
        history.close()

    with pytest.raises(ReclassifyError) as excinfo:
        await memory_service.reclassify_okabe_fact_to_self(
            owner, wl, okabe_fact_id, confirmed=True
        )
    assert excinfo.value.code == "evidence_chain_broken"


# ---------------------------------------------------------------------------
# T9c: Cross-session evidence rejected
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_reclassify_cross_session_evidence_rejected(isolated_store):
    owner = "g7b-cross-ev"
    wl = "steins_gate"
    okabe_fact_id, _, _ = await _seed_okabe_fact(owner, wl)

    # Create messages in another session to get valid message ids
    _, other_mids = await _seed_okabe_turn("other-session", wl, ["別人の発言。"])

    # Tamper: point source_message_ids to other session's messages
    memory_path = db_module._resolve_path(wl, "memory")
    conn = sqlite3.connect(memory_path)
    try:
        conn.execute(
            "UPDATE core_facts SET source_message_ids=? WHERE id=?",
            (json.dumps(other_mids), okabe_fact_id),
        )
        conn.commit()
    finally:
        conn.close()

    with pytest.raises(ReclassifyError) as excinfo:
        await memory_service.reclassify_okabe_fact_to_self(
            owner, wl, okabe_fact_id, confirmed=True
        )
    assert excinfo.value.code == "evidence_chain_broken"


# ---------------------------------------------------------------------------
# T9d: Invalid source_ids rejected
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_reclassify_invalid_source_ids_rejected(isolated_store):
    owner = "g7b-bad-ids"
    wl = "steins_gate"
    okabe_fact_id, _, mids = await _seed_okabe_fact(owner, wl)
    memory_path = db_module._resolve_path(wl, "memory")

    bad_payloads = [
        json.dumps([str(mids[0])]),   # JSON string, not int
        json.dumps([True]),           # bool
        json.dumps([float(mids[0])]), # float
        json.dumps([mids[0], mids[0]]),  # duplicate
        json.dumps([]),               # empty
    ]
    for payload in bad_payloads:
        conn = sqlite3.connect(memory_path)
        try:
            conn.execute(
                "UPDATE core_facts SET source_message_ids=? WHERE id=?",
                (payload, okabe_fact_id),
            )
            conn.commit()
        finally:
            conn.close()

        with pytest.raises(ReclassifyError) as excinfo:
            await memory_service.reclassify_okabe_fact_to_self(
                owner, wl, okabe_fact_id, confirmed=True
            )
        assert excinfo.value.code == "evidence_chain_broken", payload


# ---------------------------------------------------------------------------
# T9e: Non-user source rejected
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_reclassify_non_user_source_rejected(isolated_store):
    owner = "g7b-nonuser"
    wl = "steins_gate"

    # Create okabe conversation with assistant role message
    cid, mids = await _seed_okabe_turn(owner, wl, ["私はコーヒーが好き。"])
    # Also insert an assistant message to get its id
    asst_mid = await models.save_message(
        owner, "assistant", "[EMO:neutral] 覚えたわ。", worldline=wl
    )

    # Create okabe fact citing the assistant message
    okabe_fact_id = await memory_service.upsert_core_fact(
        owner,
        wl,
        CoreFactCandidate(
            fact_key="drink",
            fact_value="コーヒー",
            confidence=0.9,
            importance=0.7,
            source_message_ids=[asst_mid],
        ),
        identity_mode="okabe",
    )

    with pytest.raises(ReclassifyError) as excinfo:
        await memory_service.reclassify_okabe_fact_to_self(
            owner, wl, okabe_fact_id, confirmed=True
        )
    assert excinfo.value.code == "evidence_chain_broken"


# ---------------------------------------------------------------------------
# T9f: Multi-conversation source rejected
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_reclassify_multi_conversation_source_rejected(isolated_store):
    owner = "g7b-multi-conv"
    wl = "steins_gate"

    cid1, mids1 = await _seed_okabe_turn(owner, wl, ["コーヒーが好き。"])
    # Second okabe conversation — need to create a separate conversation
    conv2 = await conversation_service.create(
        owner, wl, identity_mode="okabe"
    )
    cid2 = str(conv2["id"])
    mid2 = await models.save_message(
        owner, "user", "紅茶も好き。", worldline=wl, conversation_id=cid2
    )

    okabe_fact_id = await memory_service.upsert_core_fact(
        owner,
        wl,
        CoreFactCandidate(
            fact_key="drink",
            fact_value="コーヒー",
            confidence=0.9,
            importance=0.7,
            source_message_ids=[mids1[0], mid2],
        ),
        identity_mode="okabe",
    )

    with pytest.raises(ReclassifyError) as excinfo:
        await memory_service.reclassify_okabe_fact_to_self(
            owner, wl, okabe_fact_id, confirmed=True
        )
    assert excinfo.value.code == "evidence_chain_broken"


# ---------------------------------------------------------------------------
# T9g: Non-okabe conversation source rejected
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_reclassify_non_okabe_conversation_rejected(isolated_store):
    owner = "g7b-nonokabe-conv"
    wl = "steins_gate"

    # Create a self conversation with user messages
    self_cid, self_mids = await _seed_self_turn(owner, wl, ["コーヒーが好き。"])

    # Create okabe fact (identity_mode="okabe") but source points to self conversation
    okabe_fact_id = await memory_service.upsert_core_fact(
        owner,
        wl,
        CoreFactCandidate(
            fact_key="drink",
            fact_value="コーヒー",
            confidence=0.9,
            importance=0.7,
            source_message_ids=self_mids,
        ),
        identity_mode="okabe",
    )

    with pytest.raises(ReclassifyError) as excinfo:
        await memory_service.reclassify_okabe_fact_to_self(
            owner, wl, okabe_fact_id, confirmed=True
        )
    assert excinfo.value.code == "evidence_chain_broken"


# ---------------------------------------------------------------------------
# T10: API list facts returns okabe candidates
# ---------------------------------------------------------------------------

def test_api_list_facts_returns_okabe_candidates(isolated_store, app_client):
    owner = "g7b-api-list"
    wl = "steins_gate"
    fact_id, _, _ = asyncio.run(_seed_okabe_fact(owner, wl))

    resp = app_client.get(
        "/api/memory/facts", params={"session_id": owner, "worldline": wl}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "okabe" in body
    okabe_ids = [item["id"] for item in body["okabe"]]
    assert fact_id in okabe_ids
    # source_message_ids must be redacted
    assert "source_message_ids" not in resp.text


# ---------------------------------------------------------------------------
# T11: API reclassify endpoint flow
# ---------------------------------------------------------------------------

def test_api_reclassify_endpoint_flow(isolated_store, app_client):
    owner = "g7b-api-reclass"
    wl = "steins_gate"
    fact_id, _, _ = asyncio.run(_seed_okabe_fact(owner, wl))

    resp = app_client.post(
        f"/api/memory/facts/{fact_id}/reclassify",
        params={"session_id": owner, "worldline": wl, "confirmed": "true"},
    )
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["changed"] is True
    assert isinstance(payload["id"], int)

    # Verify self facts now contain the reclassified fact
    listing = app_client.get(
        "/api/memory/facts", params={"session_id": owner, "worldline": wl}
    )
    assert listing.status_code == 200
    body = listing.json()
    self_ids = [item["id"] for item in body.get("self", body.get("local", []))]
    assert payload["id"] in self_ids


# ---------------------------------------------------------------------------
# T12: Zero provider calls
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_reclassify_zero_provider_calls(isolated_store, monkeypatch):
    def _boom(*args, **kwargs):
        raise AssertionError("reclassify must not touch Provider/LLM/TTS")

    monkeypatch.setattr(
        "app.services.provider_registry.ProviderSnapshot.require", _boom
    )
    from app.services.tts_queue import tts_manager

    monkeypatch.setattr(tts_manager, "synthesize_and_queue", _boom)
    monkeypatch.setattr(tts_manager, "synthesize_async", _boom)

    owner = "g7b-no-provider"
    wl = "steins_gate"
    okabe_fact_id, _, _ = await _seed_okabe_fact(owner, wl)

    self_fact_id, changed = await memory_service.reclassify_okabe_fact_to_self(
        owner, wl, okabe_fact_id, confirmed=True
    )
    assert changed is True and self_fact_id > 0


# ---------------------------------------------------------------------------
# P1 rework: strict source_message_ids type rules
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_strict_parse_rejects_bool_str_float_dup_empty(isolated_store):
    """_strict_parse_source_ids must never coerce via int()."""
    from app.services.memory import MemoryService
    parse = MemoryService._strict_parse_source_ids

    # Valid
    assert parse(json.dumps([1, 2, 3])) == [1, 2, 3]
    assert parse(json.dumps([5])) == [5]

    # Bool must be rejected (isinstance(True, int) is True in Python!)
    assert parse(json.dumps([True])) is None
    assert parse(json.dumps([1, True])) is None

    # String must be rejected
    assert parse(json.dumps(["1"])) is None
    assert parse(json.dumps([str(1)])) is None

    # Float must be rejected
    assert parse(json.dumps([1.0])) is None
    assert parse(json.dumps([float(1)])) is None

    # Duplicate must be rejected
    assert parse(json.dumps([1, 1])) is None

    # Empty list must be rejected
    assert parse(json.dumps([])) is None

    # Not a list
    assert parse(json.dumps({"id": 1})) is None
    assert parse(json.dumps("hello")) is None
    assert parse("not json") is None
    assert parse("") is None


@pytest.mark.asyncio
async def test_ledger_malformed_okabe_source_shows_as_not_reclassified(isolated_store):
    """Adversarial construction: self and okabe rows share all six fields;
    only the okabe row's source_message_ids is tampered to a JSON string list.
    Old int() coercion would match both to the same ID → false-positive
    already_reclassified=True. Strict parse must return False."""
    owner = "g7b-malformed-okabe"
    wl = "steins_gate"
    okabe_fact_id, _, mids = await _seed_okabe_fact(owner, wl)

    # Create self fact with same key/value — its source IDs differ from
    # okabe's mids (separate conversation), so we must align them explicitly.
    self_fact_id, _, _ = await _seed_self_fact(
        owner, wl, key="favorite_drink", value="コーヒー",
    )

    # Tamper both rows in one block:
    # - self row: set source to okabe's valid mids (align for exact-match)
    # - okabe row: set source to JSON string list (int("1") == 1 under old code)
    memory_path = db_module._resolve_path(wl, "memory")
    conn = sqlite3.connect(memory_path)
    try:
        conn.execute(
            "UPDATE core_facts SET source_message_ids=? WHERE id=?",
            (json.dumps(mids), self_fact_id),
        )
        conn.execute(
            "UPDATE core_facts SET source_message_ids=? WHERE id=?",
            (json.dumps([str(mids[0])]), okabe_fact_id),
        )
        conn.commit()
    finally:
        conn.close()

    ledger = await memory_service.list_memory_facts_for_ledger(owner, wl)
    okabe_entry = next(item for item in ledger["okabe"] if item["id"] == okabe_fact_id)
    # Strict parse: malformed okabe source → never match → False
    assert okabe_entry["already_reclassified"] is False


@pytest.mark.asyncio
async def test_malformed_self_row_does_not_block_legitimate_reclassify(isolated_store):
    """Adversarial construction: self and okabe rows share all six fields;
    only the self row's source_message_ids is tampered to a JSON string list.
    Old int() coercion would exact-match → changed=False (false negative).
    Strict parse must supersede → changed=True."""
    owner = "g7b-malformed-self"
    wl = "steins_gate"
    okabe_fact_id, _, mids = await _seed_okabe_fact(owner, wl)

    # Self fact with EXACTLY the same key/value/confidence/importance as okabe
    self_fact_id, _, _ = await _seed_self_fact(
        owner, wl, key="favorite_drink", value="コーヒー",
    )
    # Tamper self source to JSON string — int("1") == 1 under old code
    memory_path = db_module._resolve_path(wl, "memory")
    conn = sqlite3.connect(memory_path)
    try:
        conn.execute(
            "UPDATE core_facts SET source_message_ids=? WHERE id=?",
            (json.dumps([str(mids[0])]), self_fact_id),
        )
        conn.commit()
    finally:
        conn.close()

    # Reclassify the legitimate okabe fact — must go through changed=True
    new_self_id, changed = await memory_service.reclassify_okabe_fact_to_self(
        owner, wl, okabe_fact_id, confirmed=True
    )
    assert changed is True
    assert new_self_id != self_fact_id

    # Old malformed self row should be superseded
    old_row = _memory_row_by_id(wl, self_fact_id)
    assert old_row["is_current"] == 0
    assert old_row["superseded_by"] == new_self_id
