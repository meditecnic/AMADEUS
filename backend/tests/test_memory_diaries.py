import asyncio
import io
import json
import zipfile
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.db import get_db, init_db
from app.models import save_message, update_message_translation
from app.routers.system_api import router
from app.services.conversations import conversation_service

NOW = datetime(2026, 11, 20, tzinfo=timezone.utc)
SCOPE = dict(session_id="owner", worldline="steins_gate", identity_mode="self")
BODY = dict(body_ja="覚えている。", body_zh="我还记得。", verified_phrases=["覚えている", "还记得"])


async def source(**scope):
    await init_db()
    conversation = await conversation_service.create(**scope)
    cid = conversation["id"]
    ids = [await save_message(scope["session_id"], role, text, scope["worldline"], conversation_id=cid)
           for role, text in [("user", "one"), ("assistant", "two"), ("user", "three")]]
    return dict(**scope, conversation_id=cid), ids


@pytest.mark.asyncio
async def test_window_rejects_duplicate_and_overlap_and_is_due_from_new_connection():
    from app.services.memory_v11.diaries import list_due_diary_windows, register_diary_window

    scope, ids = await source(**SCOPE)
    window = dict(**scope, first_message_id=ids[0], last_message_id=ids[1], due_at=NOW)
    diary_id = await register_diary_window(**window)
    assert diary_id
    assert await register_diary_window(**window) is None
    assert await register_diary_window(**{**window, "first_message_id": ids[1], "last_message_id": ids[2]}) is None
    assert await list_due_diary_windows(worldline="steins_gate", now=NOW - timedelta(seconds=1)) == []
    [row] = await list_due_diary_windows(worldline="steins_gate", now=NOW)
    assert (row["diary_id"], row["source_count"], row["content_epoch"]) == (diary_id, 2, 0)
    assert await list_due_diary_windows(worldline="beta", now=NOW) == []


async def sql(kind, query, params=()):
    db = await get_db("steins_gate", kind)
    try:
        cursor = await db.execute(query, params)
        rows = await cursor.fetchall()
        await db.commit()
        return rows
    finally:
        await db.close()


async def registered():
    from app.services.memory_v11.diaries import register_diary_window

    conversation, ids = await source(**SCOPE)
    window = dict(**conversation, first_message_id=ids[0], last_message_id=ids[-1], due_at=NOW)
    diary_id = await register_diary_window(**window)
    assert diary_id
    return window, diary_id


@pytest.mark.asyncio
async def test_overlapping_registrations_are_serialized():
    from app.services.memory_v11.diaries import register_diary_window

    scope, (first, middle, last) = await source(**SCOPE)
    results = await asyncio.gather(
        register_diary_window(**scope, first_message_id=first, last_message_id=middle, due_at=NOW),
        register_diary_window(**scope, first_message_id=middle, last_message_id=last, due_at=NOW),
    )
    assert sum(result is not None for result in results) == 1


@pytest.mark.asyncio
async def test_translation_repair_preserves_registered_window():
    from app.services.memory_v11.diaries import commit_diary, list_due_diary_windows, load_diary_source

    window, diary_id = await registered()
    [due] = await list_due_diary_windows(worldline=SCOPE["worldline"], now=NOW)
    _, reply, _ = await load_diary_source(worldline=SCOPE["worldline"], window=due)
    translation = "修复后的字幕"
    assert await update_message_translation(
        reply["id"], SCOPE["session_id"], window["conversation_id"], translation, SCOPE["worldline"],
    )
    messages = await load_diary_source(worldline=SCOPE["worldline"], window=due)
    assert messages is not None
    assert messages[1]["translation"] == translation
    assert await commit_diary(**SCOPE, diary_id=diary_id, **BODY)


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["pending", "completed", "epoch", "content", "deleted"])
async def test_commit_discards_stale_source_or_deleted_window(change):
    from app.services.memory_v11.diaries import commit_diary, delete_diary, list_diaries, list_due_diary_windows

    window, diary_id = await registered()
    cid = window["conversation_id"]
    if change in {"pending", "completed"}:
        # An intent alone tests the memory-side guard while history is intact.
        await sql("memory", """INSERT INTO conversation_erasure_requests VALUES(
                  'erasure','owner','self',?,'forget',0,?,?,'now')""",
                  (cid, json.dumps([window["first_message_id"]]), change))
    elif change == "epoch":
        await sql("history", "INSERT INTO conversation_content_epochs VALUES('owner',?,1)", (cid,))
    elif change == "content":
        await sql("history", "UPDATE messages SET content='changed' WHERE id=?", (window["last_message_id"],))
    else:
        await delete_diary(**SCOPE, diary_id=diary_id)
    assert not await commit_diary(**SCOPE, diary_id=diary_id, **BODY)
    assert (await list_diaries(**SCOPE))["diaries"] == []
    assert await list_due_diary_windows(worldline="steins_gate", now=NOW) == []
    [row] = await sql("memory", "SELECT body_ja,body_zh,verified_phrases_json FROM memory_diaries")
    assert tuple(row) == (None, None, None)


async def link_diary_job(window, diary_id):
    await sql("memory", """INSERT INTO memory_ingest_jobs(job_id,session_id,identity_mode,conversation_id,
              source_message_id,pipeline_version,provider_id,model_id,state,created_at,updated_at)
              VALUES(?,'owner','self',?,?,'diary-v1','local','fixture','pending','now','now')""",
              (diary_id, window["conversation_id"], window["last_message_id"]))


@pytest.mark.asyncio
async def test_linked_job_uses_its_ingest_lease_and_diary_delete_cancels_it():
    from app.services.memory_v11.diaries import commit_diary, delete_diary, list_due_diary_windows
    from app.services.memory_v11.jobs import claim_memory_job, get_memory_job

    window, diary_id = await registered()
    await link_diary_job(window, diary_id)
    assert await list_due_diary_windows(worldline="steins_gate", now=NOW) == []
    assert not await commit_diary(**SCOPE, diary_id=diary_id, **BODY)
    assert await claim_memory_job(job_id=diary_id, worldline="steins_gate", lease_owner="writer")
    assert not await commit_diary(**SCOPE, diary_id=diary_id, lease_owner="other", **BODY)
    assert await commit_diary(**SCOPE, diary_id=diary_id, lease_owner="writer", **BODY)
    await delete_diary(**SCOPE, diary_id=diary_id)
    job = await get_memory_job(diary_id, worldline="steins_gate")
    assert (job["state"], job["retryable"], job["lease_owner"]) == ("cancelled", False, None)


@pytest.fixture
def memory_api():
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        yield client


@pytest.mark.asyncio
async def test_diary_api_scopes_reads_and_deletes_and_keeps_window(memory_api):
    from app.services.memory_v11.diaries import commit_diary, load_diary_source, list_due_diary_windows, register_diary_window

    window, diary_id = await registered()
    [due] = await list_due_diary_windows(worldline="steins_gate", now=NOW)
    messages = await load_diary_source(worldline="steins_gate", window=due)
    assert [message["content"] for message in messages] == ["one", "two", "three"]
    assert await commit_diary(**SCOPE, diary_id=diary_id, **BODY)
    path = "/api/memory/diaries"
    for other_scope in [{"identity_mode": "okabe"}, {"worldline": "beta"}, {"session_id": "other"}]:
        scope = {**SCOPE, **other_scope}
        assert memory_api.get(path, params=scope).json()["diaries"] == []
        assert memory_api.delete(f"{path}/{diary_id}", params=scope).status_code == 404
    [row] = memory_api.get(path, params=SCOPE).json()["diaries"]
    assert (row["diary_id"], row["body_ja"], row["body_zh"]) == (diary_id, BODY["body_ja"], BODY["body_zh"])
    assert json.loads(row["verified_phrases_json"]) == BODY["verified_phrases"]
    assert memory_api.delete(f"{path}/{diary_id}", params=SCOPE).status_code == 204
    assert memory_api.get(path, params=SCOPE).json()["diaries"] == []
    [deleted] = await sql("memory", "SELECT * FROM memory_diaries")
    assert deleted["deleted_at"] and deleted["source_count"] == 3
    assert deleted["body_ja"] is deleted["body_zh"] is deleted["verified_phrases_json"] is None
    assert await register_diary_window(**window) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("action,forget_long_term", [("delete", False), ("forget", True), ("forget", False)])
async def test_conversation_erasure_and_export(action, forget_long_term):
    from app.services.data_export import PreparedExport
    from app.services.memory_v11.diaries import commit_diary, list_diaries

    window, diary_id = await registered()
    assert await commit_diary(**SCOPE, diary_id=diary_id, **BODY)
    operation = getattr(conversation_service, action)
    await operation(window["conversation_id"], "owner", "steins_gate", forget_long_term=forget_long_term)
    retained = action == "forget" and not forget_long_term
    assert bool((await list_diaries(**SCOPE))["diaries"]) is retained
    archive = b"".join(PreparedExport("memory", "json", "sg", None).prepare().stream())
    with zipfile.ZipFile(io.BytesIO(archive)) as exported:
        diaries = json.loads(exported.read("sg/memory.json"))["diaries"]
    if retained:
        assert [(row["diary_id"], row["body_zh"]) for row in diaries] == [(diary_id, BODY["body_zh"])]
    else:
        assert diaries == []
        [row] = await sql("memory", "SELECT body_ja,body_zh,verified_phrases_json FROM memory_diaries")
        assert tuple(row) == (None, None, None)


@pytest.mark.asyncio
async def test_history_only_clear_discards_pending_window_and_cancels_linked_job():
    from app.services.memory_v11.diaries import commit_diary
    from app.services.memory_v11.jobs import claim_memory_job, get_memory_job

    window, diary_id = await registered()
    await link_diary_job(window, diary_id)
    worldline = SCOPE["worldline"]
    assert await claim_memory_job(job_id=diary_id, worldline=worldline, lease_owner="writer")

    await conversation_service.forget(window["conversation_id"], SCOPE["session_id"], worldline, forget_long_term=False)

    [diary] = await sql("memory", "SELECT state FROM memory_diaries WHERE diary_id=?", (diary_id,))
    assert diary["state"] == "discarded"
    job = await get_memory_job(diary_id, worldline=worldline)
    assert (job["state"], job["retryable"], job["lease_owner"], job["last_error_code"]) == (
        "cancelled", False, None, "source_missing",
    )
    assert not await commit_diary(**SCOPE, diary_id=diary_id, lease_owner="writer", **BODY)


@pytest.mark.asyncio
async def test_topics_keep_user_created_and_user_emptied_topics(memory_api):
    from app.services.memory_v11.facts import delete_fact

    await init_db()
    topics = "/api/memory/topics"
    created = memory_api.post(topics, params=SCOPE, json={"display_label": "  Reading  "})
    assert created.status_code == 200
    empty = created.json()["topic_id"]
    for topic in ("manual-move", "system-clear"):
        await sql("memory", """INSERT INTO memory_topics(topic_id,session_id,identity_mode,normalized_label,
                  display_label,created_at,updated_at) VALUES(?,'owner','self',?,?,'now','now')""", (topic, topic, topic))
        await sql("memory", """INSERT INTO stable_facts(fact_id,session_id,identity_mode,topic_id,state,
                  active_version,created_at,updated_at) VALUES(?,'owner','self',?,'active',1,'now','now')""", (topic, topic))
    blocked = memory_api.delete(f"{topics}/manual-move", params=SCOPE)
    assert (blocked.status_code, blocked.json()["detail"]["code"]) == (409, "topic_not_empty")
    moved = memory_api.patch("/api/memory/facts/manual-move/presentation", params=SCOPE, json={"topic_id": None})
    assert moved.status_code == 200
    await delete_fact(**SCOPE, fact_id="system-clear", expected_version=1)
    visible = memory_api.get(topics, params=SCOPE).json()["topics"]
    assert {row["topic_id"]: row["fact_count"] for row in visible} == {empty: 0, "manual-move": 0}
    assert memory_api.delete(f"{topics}/{empty}", params={**SCOPE, "identity_mode": "okabe"}).status_code == 404
    assert memory_api.delete(f"{topics}/{empty}", params=SCOPE).status_code == 204
    assert memory_api.delete(f"{topics}/manual-move", params=SCOPE).status_code == 204
    assert memory_api.get(topics, params=SCOPE).json()["topics"] == []


@pytest.mark.asyncio
async def test_fresh_install_has_v16_in_all_databases():
    await init_db()
    for worldline, kind in [("steins_gate", "control"), *(
        (wl, db_kind) for wl in ("steins_gate", "beta") for db_kind in ("history", "memory")
    )]:
        db = await get_db(worldline, kind)
        try:
            row = await (await db.execute("SELECT value FROM schema_meta WHERE key='schema_version'")).fetchone()
            assert row["value"] == "16"
        finally:
            await db.close()
