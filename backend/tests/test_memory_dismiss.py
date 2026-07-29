"""M3: soft-dismiss okabe candidates — hide from ledger and prompt, keep audit row."""
from __future__ import annotations

import pytest

from app.db import init_db, reset_initialization_cache
from app import models
from app.db import default_conversation_id
from app.services.memory import (
    CoreFactCandidate,
    DismissError,
    memory_service,
)


@pytest.fixture
async def isolated_store(tmp_path, monkeypatch):
    monkeypatch.delenv("AMADEUS_DB_PATH", raising=False)
    monkeypatch.setenv("AMADEUS_DATA_DIR", str(tmp_path))
    reset_initialization_cache()
    await init_db()
    return tmp_path


async def _seed_okabe_fact(owner: str, worldline: str, key: str, value: str) -> int:
    mids = [
        await models.save_message(owner, "user", f"关于{value}", worldline=worldline)
    ]
    return await memory_service.upsert_core_fact(
        owner,
        worldline,
        CoreFactCandidate(
            fact_key=key,
            fact_value=value,
            confidence=0.9,
            importance=0.8,
            source_message_ids=mids,
        ),
        identity_mode="okabe",
    )


@pytest.mark.asyncio
async def test_dismiss_hides_from_ledger(isolated_store):
    owner = "m3-ledger"
    wl = "steins_gate"
    fid = await _seed_okabe_fact(owner, wl, "hobby", "读书")
    snap = await memory_service.list_memory_facts_for_ledger(owner, wl)
    assert any(int(x["id"]) == fid for x in snap["okabe"])

    fact_id, changed = await memory_service.dismiss_okabe_fact(owner, wl, fid)
    assert fact_id == fid and changed is True

    snap2 = await memory_service.list_memory_facts_for_ledger(owner, wl)
    assert all(int(x["id"]) != fid for x in snap2["okabe"])


@pytest.mark.asyncio
async def test_dismiss_excluded_from_select_core_facts(isolated_store):
    owner = "m3-select"
    wl = "steins_gate"
    fid = await _seed_okabe_fact(owner, wl, "favorite_drink", "雪碧")
    before = await memory_service.select_core_facts(
        owner, wl, "雪碧", identity_mode="okabe"
    )
    assert any(r.id == fid for r in before)

    await memory_service.dismiss_okabe_fact(owner, wl, fid)
    after = await memory_service.select_core_facts(
        owner, wl, "雪碧", identity_mode="okabe"
    )
    assert all(r.id != fid for r in after)


@pytest.mark.asyncio
async def test_dismiss_idempotent(isolated_store):
    owner = "m3-idemp"
    wl = "steins_gate"
    fid = await _seed_okabe_fact(owner, wl, "name", "测试")
    _, c1 = await memory_service.dismiss_okabe_fact(owner, wl, fid)
    _, c2 = await memory_service.dismiss_okabe_fact(owner, wl, fid)
    assert c1 is True and c2 is False


@pytest.mark.asyncio
async def test_dismiss_rejects_self_fact(isolated_store):
    owner = "m3-self"
    wl = "steins_gate"
    mids = [await models.save_message(owner, "user", "我喜欢咖啡", worldline=wl)]
    # ensure self conversation path not required for upsert_core_fact
    fid = await memory_service.upsert_core_fact(
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
    with pytest.raises(DismissError) as exc:
        await memory_service.dismiss_okabe_fact(owner, wl, fid)
    assert exc.value.code == "not_okabe_fact"


@pytest.mark.asyncio
async def test_bulk_dismiss(isolated_store):
    owner = "m3-bulk"
    wl = "steins_gate"
    a = await _seed_okabe_fact(owner, wl, "hobby", "A")
    b = await _seed_okabe_fact(owner, wl, "location", "B")
    result = await memory_service.dismiss_okabe_facts_bulk(owner, wl, [a, b, 99999])
    assert set(result["dismissed"]) == {a, b}
    assert 99999 in result["skipped"]
    snap = await memory_service.list_memory_facts_for_ledger(owner, wl)
    assert snap["okabe"] == []


@pytest.mark.asyncio
async def test_reclassify_blocked_after_dismiss(isolated_store):
    owner = "m3-reclass"
    wl = "steins_gate"
    fid = await _seed_okabe_fact(owner, wl, "nickname", "克里斯蒂娜")
    await memory_service.dismiss_okabe_fact(owner, wl, fid)
    from app.services.memory import ReclassifyError

    with pytest.raises(ReclassifyError) as exc:
        await memory_service.reclassify_okabe_fact_to_self(
            owner, wl, fid, confirmed=True
        )
    assert exc.value.code == "fact_dismissed"
