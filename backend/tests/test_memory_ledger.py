"""GATE7A-EXPLICIT-SHARED-MEMORY-01: Memory Ledger listing and explicit promotion.

Frozen semantics under test:
- New facts stay worldline-local; promotion is an explicit user action only.
- Only persisted self conversations with all-user-role evidence can promote.
- The server re-reads everything from the local core fact; nothing from the
  client is trusted (no fact body, no source ids, no conversation id).
- Promotion copies to the shared store (Q18 path); the local fact is untouched.
- Zero LLM/Provider/TTS involvement; deterministic DB work only.
"""
from __future__ import annotations

import asyncio
import json
import sqlite3

import pytest

from app import db as db_module
from app import models
from app.db import init_db, reset_initialization_cache
from app.services.conversations import conversation_service
from app.services.memory import (
    CoreFactCandidate,
    SharedFactPromotionError,
    memory_service,
)


@pytest.fixture
async def isolated_store(tmp_path, monkeypatch):
    monkeypatch.delenv("AMADEUS_DB_PATH", raising=False)
    monkeypatch.setenv("AMADEUS_DATA_DIR", str(tmp_path))
    reset_initialization_cache()
    await init_db()
    return tmp_path


def _shared_rows() -> list[sqlite3.Row]:
    connection = sqlite3.connect(db_module._resolve_path("steins_gate", "control"))
    connection.row_factory = sqlite3.Row
    try:
        return connection.execute(
            "SELECT * FROM shared_user_facts ORDER BY id"
        ).fetchall()
    finally:
        connection.close()


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


async def _seed_local_self_fact(
    owner: str,
    worldline: str,
    *,
    key: str = "favorite_drink",
    value: str = "コーヒー",
    pinned: bool = False,
    roles: list[str] | None = None,
) -> tuple[int, str, list[int]]:
    """Persist a real self conversation and a local self core fact citing it."""
    cid, mids = await _seed_self_turn(
        owner, worldline, [f"私は{value}が好き。"], roles=roles
    )
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
        identity_mode="self",
    )
    return fact_id, cid, mids


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


# ---------------------------------------------------------------------------
# Promotion happy paths
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_promoted_sg_fact_visible_in_beta_retrieval_and_prompt(isolated_store):
    from app.services.prompt_compiler import compile_for_session

    owner = "g7-sg-to-beta"
    fact_id, _, _ = await _seed_local_self_fact(owner, "steins_gate")

    shared_id, changed = await memory_service.promote_local_self_fact(
        owner, "steins_gate", fact_id
    )
    assert changed is True and shared_id > 0

    beta_view = await memory_service.select_core_facts(
        owner, "beta", "favorite_drink", identity_mode="self"
    )
    assert any("コーヒー" in item.content for item in beta_view)
    prompt = await compile_for_session(
        _FakeSession(owner, "beta", "self"), "飲み物の話をしよう"
    )
    assert "コーヒー" in prompt
    # Promotion copies; the local fact stays current in its worldline.
    sg_local = await memory_service.select_core_facts(
        owner, "steins_gate", "favorite_drink", identity_mode="self"
    )
    assert any(item.id == fact_id for item in sg_local)


@pytest.mark.asyncio
async def test_promoted_beta_fact_visible_in_sg(isolated_store):
    owner = "g7-beta-to-sg"
    fact_id, _, _ = await _seed_local_self_fact(owner, "beta", value="紅茶")
    await memory_service.promote_local_self_fact(owner, "beta", fact_id)

    sg_view = await memory_service.select_core_facts(
        owner, "steins_gate", "favorite_drink", identity_mode="self"
    )
    assert any("紅茶" in item.content for item in sg_view)
    assert _shared_rows()[0]["origin_worldline"] == "beta"


@pytest.mark.asyncio
async def test_pinned_local_fact_promotes_as_pinned(isolated_store):
    owner = "g7-pinned"
    fact_id, _, _ = await _seed_local_self_fact(owner, "steins_gate", pinned=True)
    await memory_service.promote_local_self_fact(owner, "steins_gate", fact_id)
    rows = _shared_rows()
    assert rows[0]["is_pinned"] == 1


@pytest.mark.asyncio
async def test_promotion_makes_zero_provider_llm_tts_calls(isolated_store, monkeypatch):
    def _boom(*args, **kwargs):
        raise AssertionError("promotion must not touch Provider/LLM/TTS")

    monkeypatch.setattr(
        "app.services.provider_registry.ProviderSnapshot.require", _boom
    )
    from app.services.tts_queue import tts_manager

    monkeypatch.setattr(tts_manager, "synthesize_and_queue", _boom)
    monkeypatch.setattr(tts_manager, "synthesize_async", _boom)

    owner = "g7-no-llm"
    fact_id, _, _ = await _seed_local_self_fact(owner, "steins_gate")
    shared_id, changed = await memory_service.promote_local_self_fact(
        owner, "steins_gate", fact_id
    )
    assert changed is True and shared_id > 0


# ---------------------------------------------------------------------------
# Rejection matrix
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_promote_rejects_okabe_cross_session_cross_worldline_and_stale(isolated_store):
    owner = "g7-reject"
    stranger = "g7-stranger"

    # okabe-scope fact → identity_scope_violation
    ok_cid, ok_mids = await _seed_self_turn(owner, "steins_gate", ["岡部の話。"])
    okabe_fact = await memory_service.upsert_core_fact(
        owner, "steins_gate",
        CoreFactCandidate(fact_key="okabe_fact", fact_value="x", confidence=0.9,
                          importance=0.5, source_message_ids=ok_mids),
        identity_mode="okabe",
    )
    with pytest.raises(SharedFactPromotionError) as excinfo:
        await memory_service.promote_local_self_fact(owner, "steins_gate", okabe_fact)
    assert excinfo.value.code == "identity_scope_violation"

    # someone else's fact id → fact_not_found (ownership, no existence leak)
    their_fact, _, _ = await _seed_local_self_fact(stranger, "steins_gate", key="their_key")
    with pytest.raises(SharedFactPromotionError) as excinfo:
        await memory_service.promote_local_self_fact(owner, "steins_gate", their_fact)
    assert excinfo.value.code == "fact_not_found"

    # nonexistent id → fact_not_found
    with pytest.raises(SharedFactPromotionError) as excinfo:
        await memory_service.promote_local_self_fact(owner, "steins_gate", 999999)
    assert excinfo.value.code == "fact_not_found"

    # cross-worldline evidence: SG fact citing β message ids
    _, beta_mids = await _seed_self_turn(owner, "beta", ["β線での発言。"])
    cross_fact = await memory_service.upsert_core_fact(
        owner, "steins_gate",
        CoreFactCandidate(fact_key="cross_wl", fact_value="x", confidence=0.9,
                          importance=0.5, source_message_ids=[max(beta_mids) + 500]),
        identity_mode="self",
    )
    with pytest.raises(SharedFactPromotionError) as excinfo:
        await memory_service.promote_local_self_fact(owner, "steins_gate", cross_fact)
    assert excinfo.value.code == "source_evidence_unavailable"

    # stale (superseded) fact → stale_fact
    old_fact, _, _ = await _seed_local_self_fact(owner, "steins_gate", key="drink2", value="水")
    await _seed_local_self_fact(owner, "steins_gate", key="drink2", value="炭酸水")
    with pytest.raises(SharedFactPromotionError) as excinfo:
        await memory_service.promote_local_self_fact(owner, "steins_gate", old_fact)
    assert excinfo.value.code == "stale_fact"

    assert _shared_rows() == []


@pytest.mark.asyncio
async def test_promote_rejects_non_user_and_mixed_role_sources(isolated_store):
    owner = "g7-role"
    # assistant-only evidence
    assistant_fact, _, _ = await _seed_local_self_fact(
        owner, "steins_gate", key="a_fact", roles=["assistant"]
    )
    with pytest.raises(SharedFactPromotionError) as excinfo:
        await memory_service.promote_local_self_fact(owner, "steins_gate", assistant_fact)
    assert excinfo.value.code == "fact_not_promotable"

    # mixed user + assistant evidence
    cid, mids = await _seed_self_turn(
        owner, "steins_gate", ["コーヒーが好き。", "[EMO:neutral] 覚えたわ。"],
        roles=["user", "assistant"],
    )
    mixed_fact = await memory_service.upsert_core_fact(
        owner, "steins_gate",
        CoreFactCandidate(fact_key="m_fact", fact_value="コーヒー", confidence=0.9,
                          importance=0.5, source_message_ids=mids),
        identity_mode="self",
    )
    with pytest.raises(SharedFactPromotionError) as excinfo:
        await memory_service.promote_local_self_fact(owner, "steins_gate", mixed_fact)
    assert excinfo.value.code == "fact_not_promotable"
    assert _shared_rows() == []


@pytest.mark.asyncio
async def test_ledger_eligibility_enforces_q18_source_id_type_rules(isolated_store):
    """P1 rework: no silent int() coercion — the ledger and the promote path
    must fail closed on JSON strings, bools, floats, duplicates and empty
    lists, exactly like the Q18 provenance type rules."""
    owner = "g7-types"
    fact_id, _, mids = await _seed_local_self_fact(owner, "steins_gate")
    memory_path = db_module._resolve_path("steins_gate", "memory")

    bad_payloads = [
        json.dumps([str(mids[0])]),   # "1" — JSON string, not an int
        json.dumps([True]),           # bool must never coerce to 1
        json.dumps([float(mids[0])]), # 1.0 — float, not an actual int
        json.dumps([mids[0], mids[0]]),  # duplicated evidence
        json.dumps([]),               # empty evidence list
        json.dumps({"id": mids[0]}),  # not a JSON list at all
    ]
    for payload in bad_payloads:
        connection = sqlite3.connect(memory_path)
        try:
            connection.execute(
                "UPDATE core_facts SET source_message_ids=? WHERE id=?",
                (payload, fact_id),
            )
            connection.commit()
        finally:
            connection.close()

        ledger = await memory_service.list_memory_facts_for_ledger(owner, "steins_gate")
        entry = next(item for item in ledger["local"] if item["id"] == fact_id)
        assert entry["promotion_eligible"] is False, payload
        assert entry["promotion_reason"] == "source_evidence_unavailable", payload

        with pytest.raises(SharedFactPromotionError) as excinfo:
            await memory_service.promote_local_self_fact(owner, "steins_gate", fact_id)
        assert excinfo.value.code == "source_evidence_unavailable", payload

    assert _shared_rows() == []


@pytest.mark.asyncio
async def test_ledger_marks_already_shared_local_fact(isolated_store):
    """Display-bug fix: a local fact whose promotion would be a no-op reports
    already_shared=True so the UI can stop offering the share action."""
    owner = "g7-already"
    fact_id, _, _ = await _seed_local_self_fact(owner, "steins_gate")

    before = await memory_service.list_memory_facts_for_ledger(owner, "steins_gate")
    entry = next(item for item in before["local"] if item["id"] == fact_id)
    assert entry["already_shared"] is False

    await memory_service.promote_local_self_fact(owner, "steins_gate", fact_id)
    after = await memory_service.list_memory_facts_for_ledger(owner, "steins_gate")
    entry = next(item for item in after["local"] if item["id"] == fact_id)
    assert entry["already_shared"] is True
    # Evidence is still valid — only the "re-share is a no-op" state changed.
    assert entry["promotion_eligible"] is True
    assert entry["promotion_reason"] is None

    # Same key but the current shared value was superseded from β: the SG
    # local fact becomes shareable again (promoting would update shared).
    beta_fact, _, _ = await _seed_local_self_fact(owner, "beta", value="紅茶")
    await memory_service.promote_local_self_fact(owner, "beta", beta_fact)
    third = await memory_service.list_memory_facts_for_ledger(owner, "steins_gate")
    entry = next(item for item in third["local"] if item["id"] == fact_id)
    assert entry["already_shared"] is False


@pytest.mark.asyncio
async def test_deleted_source_marks_ineligible_and_promote_still_rejects(isolated_store):
    owner = "g7-deleted-src"
    fact_id, _, mids = await _seed_local_self_fact(owner, "steins_gate")
    history = sqlite3.connect(db_module._resolve_path("steins_gate", "history"))
    try:
        history.execute("DELETE FROM messages WHERE id=?", (mids[0],))
        history.commit()
    finally:
        history.close()

    ledger = await memory_service.list_memory_facts_for_ledger(owner, "steins_gate")
    entry = next(item for item in ledger["local"] if item["id"] == fact_id)
    assert entry["promotion_eligible"] is False
    assert entry["promotion_reason"] == "source_evidence_unavailable"

    # The submit path re-verifies regardless of what any UI showed (TOCTOU).
    with pytest.raises(SharedFactPromotionError) as excinfo:
        await memory_service.promote_local_self_fact(owner, "steins_gate", fact_id)
    assert excinfo.value.code == "source_evidence_unavailable"
    assert _shared_rows() == []


# ---------------------------------------------------------------------------
# Idempotency / supersession / concurrency
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_repeat_promotion_is_idempotent_without_new_audit_rows(isolated_store):
    owner = "g7-idempotent"
    fact_id, _, _ = await _seed_local_self_fact(owner, "steins_gate")

    first_id, first_changed = await memory_service.promote_local_self_fact(
        owner, "steins_gate", fact_id
    )
    second_id, second_changed = await memory_service.promote_local_self_fact(
        owner, "steins_gate", fact_id
    )
    assert first_changed is True
    assert second_changed is False
    assert second_id == first_id
    assert len(_shared_rows()) == 1  # no new audit row on a no-op


@pytest.mark.asyncio
async def test_same_key_different_value_promotion_supersedes(isolated_store):
    owner = "g7-supersede"
    first_fact, _, _ = await _seed_local_self_fact(owner, "steins_gate", value="コーヒー")
    first_id, _ = await memory_service.promote_local_self_fact(
        owner, "steins_gate", first_fact
    )
    second_fact, _, _ = await _seed_local_self_fact(owner, "steins_gate", value="紅茶")
    second_id, second_changed = await memory_service.promote_local_self_fact(
        owner, "steins_gate", second_fact
    )
    assert second_changed is True and second_id != first_id

    rows = {row["id"]: row for row in _shared_rows()}
    assert rows[first_id]["is_current"] == 0
    assert rows[first_id]["superseded_by"] == second_id
    assert rows[second_id]["is_current"] == 1


@pytest.mark.asyncio
async def test_concurrent_identical_promotions_both_succeed_single_current(isolated_store):
    owner = "g7-race-same"
    fact_id, _, _ = await _seed_local_self_fact(owner, "steins_gate")
    results = await asyncio.gather(
        memory_service.promote_local_self_fact(owner, "steins_gate", fact_id),
        memory_service.promote_local_self_fact(owner, "steins_gate", fact_id),
        return_exceptions=True,
    )
    assert all(isinstance(result, tuple) for result in results), results
    ids = {result[0] for result in results}
    assert len(ids) == 1
    rows = _shared_rows()
    assert len(rows) == 1 and rows[0]["is_current"] == 1


@pytest.mark.asyncio
async def test_concurrent_different_value_promotions_keep_chain_complete(isolated_store):
    owner = "g7-race-diff"
    fact_a, _, _ = await _seed_local_self_fact(owner, "steins_gate", value="コーヒー")
    # Same key, different value in the other worldline's local store.
    fact_b, _, _ = await _seed_local_self_fact(owner, "beta", value="紅茶")
    results = await asyncio.gather(
        memory_service.promote_local_self_fact(owner, "steins_gate", fact_a),
        memory_service.promote_local_self_fact(owner, "beta", fact_b),
        return_exceptions=True,
    )
    assert all(isinstance(result, tuple) for result in results), results
    rows = {row["id"]: row for row in _shared_rows()}
    current = [row for row in rows.values() if row["is_current"] == 1]
    assert len(current) == 1
    for row in rows.values():
        if row["is_current"] == 0:
            assert row["superseded_by"] in rows


@pytest.mark.asyncio
async def test_local_forget_after_promotion_keeps_shared(isolated_store):
    owner = "g7-forget"
    fact_id, _, _ = await _seed_local_self_fact(owner, "steins_gate")
    await memory_service.promote_local_self_fact(owner, "steins_gate", fact_id)
    await memory_service.forget(owner, "steins_gate", scope="all")
    assert len(_shared_rows()) == 1
    beta_view = await memory_service.select_core_facts(
        owner, "beta", "favorite_drink", identity_mode="self"
    )
    assert any("コーヒー" in item.content for item in beta_view)


# ---------------------------------------------------------------------------
# Ledger listing contract
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ledger_lists_self_local_and_shared_never_okabe_or_sources(isolated_store):
    owner = "g7-ledger"
    fact_id, _, _ = await _seed_local_self_fact(owner, "steins_gate")
    # okabe fact must never appear in the ledger.
    _, ok_mids = await _seed_self_turn(owner, "steins_gate", ["岡部として。"])
    await memory_service.upsert_core_fact(
        owner, "steins_gate",
        CoreFactCandidate(fact_key="okabe_secret", fact_value="lab", confidence=0.9,
                          importance=0.5, source_message_ids=ok_mids),
        identity_mode="okabe",
    )
    beta_fact, _, _ = await _seed_local_self_fact(owner, "beta", key="hobby", value="読書")
    await memory_service.promote_local_self_fact(owner, "beta", beta_fact)

    ledger = await memory_service.list_memory_facts_for_ledger(owner, "steins_gate")
    local_keys = {item["fact_key"] for item in ledger["local"]}
    assert local_keys == {"favorite_drink"}  # SG self-local only; no okabe, no β
    entry = ledger["local"][0]
    assert entry["promotion_eligible"] is True
    assert entry["promotion_reason"] is None
    shared_keys = {item["fact_key"] for item in ledger["shared"]}
    assert shared_keys == {"hobby"}  # all shared facts regardless of worldline
    assert ledger["shared"][0]["origin_worldline"] == "beta"

    # Minimal display model: never raw sources or message text.
    serialized = json.dumps(ledger, ensure_ascii=False, default=str)
    assert "source_message_ids" not in serialized
    assert "私は読書が好き。" not in serialized


@pytest.mark.asyncio
async def test_merged_read_order_and_local_override_still_hold(isolated_store):
    owner = "g7-order"
    fact_id, _, _ = await _seed_local_self_fact(owner, "steins_gate")
    await memory_service.promote_local_self_fact(owner, "steins_gate", fact_id)
    # β local same key overrides the shared value in β retrieval.
    _, beta_mids = await _seed_self_turn(owner, "beta", ["紅茶が好き。"])
    await memory_service.upsert_core_fact(
        owner, "beta",
        CoreFactCandidate(fact_key="favorite_drink", fact_value="紅茶", confidence=0.9,
                          importance=0.7, source_message_ids=beta_mids),
        identity_mode="self",
    )
    beta_view = await memory_service.select_core_facts(
        owner, "beta", "favorite_drink", identity_mode="self"
    )
    drink = [item for item in beta_view if "favorite_drink" in item.content]
    assert len(drink) == 1 and "紅茶" in drink[0].content
    sg_view = await memory_service.select_core_facts(
        owner, "steins_gate", "favorite_drink", identity_mode="self"
    )
    assert any("コーヒー" in item.content for item in sg_view)


# ---------------------------------------------------------------------------
# HTTP API
# ---------------------------------------------------------------------------

def test_api_ledger_and_promote_flow(isolated_store, app_client):
    owner = "g7-api"
    fact_id, _, _ = asyncio.run(_async_seed_for_api(owner))

    listing = app_client.get(
        "/api/memory/facts", params={"session_id": owner, "worldline": "steins_gate"}
    )
    assert listing.status_code == 200
    body = listing.json()
    assert {item["id"] for item in body["local"]} == {fact_id}
    assert "source_message_ids" not in listing.text
    assert "私はコーヒーが好き。" not in listing.text

    promoted = app_client.post(
        f"/api/memory/facts/{fact_id}/promote",
        params={"session_id": owner, "worldline": "steins_gate"},
    )
    assert promoted.status_code == 200
    payload = promoted.json()
    assert payload["scope"] == "shared"
    assert payload["changed"] is True
    assert isinstance(payload["id"], int)

    # Idempotent repeat via the API.
    repeat = app_client.post(
        f"/api/memory/facts/{fact_id}/promote",
        params={"session_id": owner, "worldline": "steins_gate"},
    )
    assert repeat.status_code == 200
    assert repeat.json() == {"id": payload["id"], "scope": "shared", "changed": False}

    refreshed = app_client.get(
        "/api/memory/facts", params={"session_id": owner, "worldline": "beta"}
    )
    assert {item["fact_key"] for item in refreshed.json()["shared"]} == {"favorite_drink"}


async def _async_seed_for_api(owner: str) -> tuple[int, str, list[int]]:
    return await _seed_local_self_fact(owner, "steins_gate")


def test_api_promote_stable_error_codes_and_ownership(isolated_store, app_client):
    owner = "g7-api-err"
    stranger = "g7-api-stranger"
    fact_id, _, mids = asyncio.run(_async_seed_for_api(owner))

    # Ownership: another session cannot promote my fact.
    stolen = app_client.post(
        f"/api/memory/facts/{fact_id}/promote",
        params={"session_id": stranger, "worldline": "steins_gate"},
    )
    assert stolen.status_code == 404
    assert stolen.json()["detail"] == "fact_not_found"

    # Deleted evidence → stable 409 code.
    history = sqlite3.connect(db_module._resolve_path("steins_gate", "history"))
    try:
        history.execute("DELETE FROM messages WHERE id=?", (mids[0],))
        history.commit()
    finally:
        history.close()
    unavailable = app_client.post(
        f"/api/memory/facts/{fact_id}/promote",
        params={"session_id": owner, "worldline": "steins_gate"},
    )
    assert unavailable.status_code == 409
    assert unavailable.json()["detail"] == "source_evidence_unavailable"

    assert _shared_rows() == []
