"""Soft-delete established local / shared memory facts (audit retained)."""
from __future__ import annotations

import pytest

from app import models
from app.db import init_db, reset_initialization_cache
from app.services.memory import (
    CoreFactCandidate,
    MemoryDeleteError,
    memory_service,
)


@pytest.fixture
async def isolated_store(tmp_path, monkeypatch):
    monkeypatch.delenv("AMADEUS_DB_PATH", raising=False)
    monkeypatch.setenv("AMADEUS_DATA_DIR", str(tmp_path))
    reset_initialization_cache()
    await init_db()
    return tmp_path


async def _seed_self_fact(owner: str, wl: str, key: str, value: str) -> int:
    mids = [await models.save_message(owner, "user", f"我是{value}", worldline=wl)]
    # Force self conversation for provenance if needed for promote tests later
    return await memory_service.upsert_core_fact(
        owner,
        wl,
        CoreFactCandidate(
            fact_key=key,
            fact_value=value,
            confidence=0.9,
            importance=0.8,
            source_message_ids=mids,
        ),
        identity_mode="self",
    )


@pytest.mark.asyncio
async def test_delete_local_hides_from_ledger_and_prompt(isolated_store):
    owner = "del-local"
    wl = "steins_gate"
    fid = await _seed_self_fact(owner, wl, "hobby", "读书")
    snap = await memory_service.list_memory_facts_for_ledger(owner, wl)
    assert any(int(x["id"]) == fid for x in snap["local"])
    before = await memory_service.select_core_facts(owner, wl, "读书", identity_mode="self")
    assert any(r.id == fid for r in before)

    rid, changed = await memory_service.delete_local_self_fact(owner, wl, fid)
    assert rid == fid and changed is True

    snap2 = await memory_service.list_memory_facts_for_ledger(owner, wl)
    assert all(int(x["id"]) != fid for x in snap2["local"])
    after = await memory_service.select_core_facts(owner, wl, "读书", identity_mode="self")
    assert all(r.id != fid for r in after)


@pytest.mark.asyncio
async def test_delete_local_idempotent(isolated_store):
    owner = "del-idemp"
    wl = "steins_gate"
    fid = await _seed_self_fact(owner, wl, "name", "测试")
    _, c1 = await memory_service.delete_local_self_fact(owner, wl, fid)
    _, c2 = await memory_service.delete_local_self_fact(owner, wl, fid)
    assert c1 is True and c2 is False


@pytest.mark.asyncio
async def test_delete_local_rejects_okabe(isolated_store):
    owner = "del-okabe"
    wl = "steins_gate"
    mids = [await models.save_message(owner, "user", "冈部设定", worldline=wl)]
    fid = await memory_service.upsert_core_fact(
        owner,
        wl,
        CoreFactCandidate(
            fact_key="hobby",
            fact_value="实验",
            confidence=0.9,
            importance=0.8,
            source_message_ids=mids,
        ),
        identity_mode="okabe",
    )
    with pytest.raises(MemoryDeleteError) as exc:
        await memory_service.delete_local_self_fact(owner, wl, fid)
    assert exc.value.code == "wrong_scope"


@pytest.mark.asyncio
async def test_delete_shared_hides_and_keeps_local(isolated_store):
    owner = "del-shared"
    wl = "steins_gate"
    # Seed self conversation + messages for shared provenance
    from app.services.conversations import conversation_service
    from app.db import normalize_identity_mode

    # Create self-mode conversation by creating via API-like path
    conv = await conversation_service.create(
        owner, wl, title="self", identity_mode="self"
    )
    cid = str(conv["id"])
    mid = await models.save_message(
        owner, "user", "我喜欢阿西莫夫", worldline=wl, conversation_id=cid
    )
    local_id = await memory_service.upsert_core_fact(
        owner,
        wl,
        CoreFactCandidate(
            fact_key="favorite_author",
            fact_value="Asimov",
            confidence=0.9,
            importance=0.8,
            source_message_ids=[mid],
        ),
        identity_mode="self",
    )
    shared_id = await memory_service.upsert_shared_user_fact(
        owner_session_id=owner,
        candidate=CoreFactCandidate(
            fact_key="favorite_author",
            fact_value="Asimov",
            confidence=0.9,
            importance=0.8,
            source_message_ids=[mid],
        ),
        origin_worldline=wl,
        origin_conversation_id=cid,
        origin_identity_mode="self",
    )

    snap = await memory_service.list_memory_facts_for_ledger(owner, wl)
    assert any(int(x["id"]) == shared_id for x in snap["shared"])
    assert any(int(x["id"]) == local_id for x in snap["local"])

    rid, changed = await memory_service.delete_shared_fact(owner, shared_id)
    assert rid == shared_id and changed is True

    snap2 = await memory_service.list_memory_facts_for_ledger(owner, wl)
    assert all(int(x["id"]) != shared_id for x in snap2["shared"])
    # Local copy remains
    assert any(int(x["id"]) == local_id for x in snap2["local"])


@pytest.mark.asyncio
async def test_delete_local_reclassified_does_not_resurrect_okabe_pending(isolated_store):
    """Deleting a self copy that came from okabe reclassify must not re-open 待确认."""
    owner = "del-no-resurrect"
    wl = "steins_gate"
    mids = [
        await models.save_message(owner, "user", "我喜欢咖啡", worldline=wl)
    ]
    okabe_id = await memory_service.upsert_core_fact(
        owner,
        wl,
        CoreFactCandidate(
            fact_key="favorite_drink",
            fact_value="咖啡",
            confidence=0.9,
            importance=0.8,
            source_message_ids=mids,
        ),
        identity_mode="okabe",
    )
    self_id = await memory_service.upsert_core_fact(
        owner,
        wl,
        CoreFactCandidate(
            fact_key="favorite_drink",
            fact_value="咖啡",
            confidence=0.9,
            importance=0.8,
            source_message_ids=mids,
        ),
        identity_mode="self",
    )
    snap = await memory_service.list_memory_facts_for_ledger(owner, wl)
    okabe_item = next(x for x in snap["okabe"] if int(x["id"]) == okabe_id)
    assert okabe_item["already_reclassified"] is True

    await memory_service.delete_local_self_fact(owner, wl, self_id)
    snap2 = await memory_service.list_memory_facts_for_ledger(owner, wl)
    # Self gone from local list
    assert all(int(x["id"]) != self_id for x in snap2["local"])
    # Okabe source either hidden (dismissed) or still marked already_reclassified
    pending = [x for x in snap2["okabe"] if not x["already_reclassified"]]
    assert all(int(x["id"]) != okabe_id for x in pending)
    assert all(int(x["id"]) != okabe_id for x in snap2["okabe"]) or any(
        int(x["id"]) == okabe_id and x["already_reclassified"] for x in snap2["okabe"]
    )


@pytest.mark.asyncio
async def test_delete_local_does_not_remove_shared(isolated_store):
    owner = "del-local-keep-shared"
    wl = "steins_gate"
    from app.services.conversations import conversation_service

    conv = await conversation_service.create(
        owner, wl, title="self2", identity_mode="self"
    )
    cid = str(conv["id"])
    mid = await models.save_message(
        owner, "user", "我喜欢雪碧", worldline=wl, conversation_id=cid
    )
    local_id = await memory_service.upsert_core_fact(
        owner,
        wl,
        CoreFactCandidate(
            fact_key="favorite_drink",
            fact_value="雪碧",
            confidence=0.9,
            importance=0.8,
            source_message_ids=[mid],
        ),
        identity_mode="self",
    )
    shared_id = await memory_service.upsert_shared_user_fact(
        owner_session_id=owner,
        candidate=CoreFactCandidate(
            fact_key="favorite_drink",
            fact_value="雪碧",
            confidence=0.9,
            importance=0.8,
            source_message_ids=[mid],
        ),
        origin_worldline=wl,
        origin_conversation_id=cid,
        origin_identity_mode="self",
    )
    await memory_service.delete_local_self_fact(owner, wl, local_id)
    snap = await memory_service.list_memory_facts_for_ledger(owner, wl)
    assert all(int(x["id"]) != local_id for x in snap["local"])
    assert any(int(x["id"]) == shared_id for x in snap["shared"])
