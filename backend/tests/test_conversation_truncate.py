"""Integration contracts for conversation rollback."""
import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from io import BytesIO
from threading import Event
from types import SimpleNamespace
from unittest.mock import AsyncMock
from zipfile import ZipFile

import pytest
from test_memory_conversation_erasure import _change, _details, _message, _recall, _source
from test_memory_v11_api import _db_execute, _db_rows, _facts_scope, _s3d2_obs, _seed_fact_via_reconciler
from test_memory_v11_api import isolated_client as isolated_client

from app import models


def truncate(client, owner, conversation, cutoff, last=None):
    return client.post(
        f"/api/conversations/{conversation}/truncate", params={"session_id": owner},
        json={"from_message_id": cutoff, "expected_last_message_id": last or cutoff},
    )


def save(owner, conversation, text, role="user"):
    return asyncio.run(models.save_message(owner, role, text, conversation_id=conversation))


def test_truncate_keeps_prefix_and_history_only_memory(isolated_client):
    client, owner = isolated_client, "truncate-prefix"
    conversation, _, fact, _ = _source(client, owner, "Remembered before clearing history")
    path = f"/api/conversations/{conversation}"
    params = {"session_id": owner}
    assert client.post(f"{path}/forget", params=params, json={"forget_long_term": False}).status_code == 200
    ids = [
        asyncio.run(models.save_message(owner, role, text, conversation_id=conversation))
        for role, text in (("user", "keep"), ("user", "cut"), ("assistant", "cut reply"))
    ]
    response = truncate(client, owner, conversation, ids[1], ids[-1])
    assert response.status_code == 200, response.text
    assert response.json()["messages_deleted"] == 2
    assert [row["content"] for row in client.get(f"{path}/messages", params=params).json()] == ["keep"]
    assert _details(client, owner, fact)["fact"]["display_text"] == "Remembered before clearing history"
    assert _db_rows(
        "SELECT cutoff_message_id,source_message_ids FROM conversation_erasure_requests WHERE cutoff_message_id IS NOT NULL",
    ) == [{"cutoff_message_id": ids[1], "source_message_ids": json.dumps(ids[1:])}]


@pytest.mark.parametrize("case", ["exclusive", "current", "restore", "pinned", "edited", "support", "unavailable"])
def test_truncate_recomputes_facts(isolated_client, monkeypatch, case):
    client, owner = isolated_client, f"facts-{case}"
    conversation, _, fact, _ = _source(client, owner, "I run every week")
    cutoff = save(owner, conversation, "I run at the lighthouse")
    if case == "exclusive":
        fact, _ = _seed_fact_via_reconciler(session_id=owner, display_text="I run at the lighthouse",
                                           source_message_id=cutoff, conversation_id=conversation)
    else:
        _change(owner, fact, 1, conversation, cutoff, "I run at the lighthouse")
    if case in ("current", "support"):
        other, last = _message(client, owner, "I run on weekends")
        _change(owner, fact, 2, other, last, "I run on weekends", op="REFINE" if case == "current" else "ATTACH")
    if case == "pinned":
        assert client.patch(f"/api/memory/facts/{fact}/presentation", params=_facts_scope(owner),
                            json={"is_pinned": True}).status_code == 200
    if case == "edited":
        assert client.patch(f"/api/memory/facts/{fact}", params=_facts_scope(owner),
                            json={"expected_version": 2, "display_text": "edited"}).status_code == 200
    if case == "unavailable":
        from app.services.memory import memory_service
        monkeypatch.setattr(memory_service.embedder, "encode_cached_passage", AsyncMock(side_effect=RuntimeError))
    before = _details(client, owner, fact)
    assert truncate(client, owner, conversation, cutoff).status_code == 200
    if case == "exclusive":
        assert client.get(f"/api/memory/facts/{fact}/details", params=_facts_scope(owner)).status_code == 404
        return
    details = _details(client, owner, fact)
    if case in ("pinned", "edited"):
        assert details["versions"] == before["versions"]
        return
    assert details["fact"]["active_version"] == 3
    assert [row["version_no"] for row in details["versions"]] == [1, 3]
    assert "lighthouse" not in str(details)
    assert details["fact"]["display_text"] == ("I run on weekends" if case == "current" else "I run every week")
    assert details["versions"][-1]["restored_from_version"] == (None if case == "current" else 1)
    exported = client.get("/api/data/export?kind=memory&worldline=sg")
    with ZipFile(BytesIO(exported.content)) as archive:
        assert "lighthouse" not in archive.read("sg/memory.json").decode("utf-8")
    vectors = _db_rows("SELECT version_no FROM stable_fact_version_embeddings WHERE fact_id=?", (fact,))
    assert vectors == ([] if case == "unavailable" else [{"version_no": 3}])
    if case in ("support", "unavailable"):
        assert "I run every week" in _recall(owner, "run every week")
        assert "lighthouse" not in _recall(owner, "lighthouse")


@pytest.mark.parametrize("state", ["written", "pending", "deleted"])
@pytest.mark.parametrize("position", ["before", "across", "after"])
def test_diary_windows_at_cutoff(isolated_client, state, position):
    from app.services.memory_v11 import diaries

    client, owner = isolated_client, "diary-cutoff"
    conversation, _ = _message(client, owner, "first")
    ids = [save(owner, conversation, str(n)) for n in range(6)]
    first, last = {"before": ids[0:2], "across": ids[1:4:2], "after": ids[4:6]}[position]
    scope = _facts_scope(owner)
    window = dict(**scope, conversation_id=conversation, first_message_id=first, last_message_id=last,
                  due_at=datetime(2099, 1, 1, tzinfo=timezone.utc))
    diary_id = asyncio.run(diaries.register_diary_window(**window))
    assert diary_id
    body = dict(body_ja="日記", body_zh="日记", verified_phrases=[])
    if state == "written":
        assert asyncio.run(diaries.commit_diary(**scope, diary_id=diary_id, **body))
    _db_execute(
        """INSERT INTO memory_ingest_jobs(job_id,session_id,identity_mode,conversation_id,source_message_id,
                    pipeline_version,provider_id,model_id,state,created_at,updated_at,next_attempt_at)
           VALUES(?,?,'self',?,?,'diary-v1','test','test','pending','now','now','2099-01-01')""",
        (diary_id, owner, conversation, first),
    )
    if state == "deleted":
        asyncio.run(diaries.delete_diary(**scope, diary_id=diary_id))
    assert truncate(client, owner, conversation, ids[2], ids[-1]).status_code == 200
    rows = _db_rows("SELECT last_message_id,state,body_zh FROM memory_diaries WHERE diary_id=?", (diary_id,))
    retained = state == "written" and position == "before" or state == "deleted" and position != "after"
    assert _db_rows("SELECT state FROM memory_ingest_jobs WHERE job_id=?", (diary_id,)) == [
        {"state": "pending" if state == "written" and retained else "cancelled"}
    ]
    assert rows == ([{"last_message_id": ids[1] if position == "across" else last,
                      "state": "discarded" if state == "deleted" else "written",
                      "body_zh": None if state == "deleted" else "日记"}] if retained else [])
    assert not asyncio.run(diaries.commit_diary(**scope, diary_id=diary_id, **body))
    if position == "before" and state == "pending":
        new_id = asyncio.run(diaries.register_diary_window(**window))
        assert new_id and new_id != diary_id
        assert asyncio.run(diaries.commit_diary(**scope, diary_id=new_id, **body))
    if position == "across" and state == "deleted":
        assert asyncio.run(diaries.register_diary_window(**{**window, "last_message_id": ids[1]})) is None


@pytest.mark.parametrize("source_cut,outcome", [(False, "content"), (False, "empty"), (False, "error"), (True, "content")])
def test_late_extraction_respects_cutoff(isolated_client, monkeypatch, source_cut, outcome):
    from app.services.memory_v11 import jobs

    client, owner = isolated_client, "truncate-extraction"
    conversation, prefix = _message(client, owner, "kept user")
    cutoff = save(owner, conversation, "cut user")
    message = cutoff if source_cut else prefix
    monkeypatch.setattr(jobs, "process_due_jobs_once", AsyncMock())

    async def create_job():
        return await jobs.enqueue_memory_job(
            **_facts_scope(owner), conversation_id=conversation, source_message_id=message,
            provider_id="test", model_id="test",
        )

    def result(text):
        return {
            "observations": [_s3d2_obs(ref="o1", display_text=text, source_message_id=message)],
            "operations": [{"op": "CREATE", "observation_ref": "o1", "fact_text": text,
                            "reason_code": "direct_durable_statement"}],
        }

    async def stale_completion(**kwargs):
        response = await asyncio.to_thread(truncate, client, owner, conversation, cutoff)
        assert response.status_code == 200, response.text
        if outcome == "error":
            raise OSError("provider interrupted")
        return {"observations": [], "operations": []} if outcome == "empty" else result("stale details")

    job_id = asyncio.run(create_job())["job_id"]
    def run(completion):
        return asyncio.run(jobs.process_memory_job(
            job_id=job_id, worldline="steins_gate", memory_completion=completion,
        ))
    job = run(stale_completion)
    assert job["state"] == ("cancelled" if source_cut else "pending")
    assert not _db_rows("SELECT * FROM memory_ingest_receipts")
    assert not _db_rows("SELECT * FROM stable_facts")
    if not source_cut:
        assert job["attempt_count"] == 0
        fresh = run(AsyncMock(return_value=result("fresh prefix")))
        assert (fresh["state"], fresh["attempt_count"]) == ("completed", 1)
        assert _db_rows("SELECT display_text FROM stable_fact_versions") == [{"display_text": "fresh prefix"}]


def test_recovery_replays_only_history(isolated_client, monkeypatch):
    from app.services import conversation_erasure as erasure

    client, owner = isolated_client, "truncate-recovery"
    conversation, cutoff, fact, _ = _source(client, owner, "erase this")
    commit_history = erasure._erase_history
    monkeypatch.setattr(erasure, "_erase_history", AsyncMock(side_effect=RuntimeError("history interrupted")))
    with pytest.raises(RuntimeError, match="history interrupted"):
        truncate(client, owner, conversation, cutoff)
    assert len(asyncio.run(models.get_session_messages(owner, conversation_id=conversation))) == 1
    assert client.get(f"/api/memory/facts/{fact}/details", params=_facts_scope(owner)).status_code == 404
    monkeypatch.setattr(erasure, "_erase_history", commit_history)
    monkeypatch.setattr(erasure, "_commit_erasure_memory", AsyncMock(side_effect=AssertionError))
    asyncio.run(erasure.resume_erasures(worldline="steins_gate"))
    new = save(owner, conversation, "new history")
    asyncio.run(erasure.resume_erasures(worldline="steins_gate"))
    assert [row["id"] for row in asyncio.run(models.get_recent_messages(owner, conversation_id=conversation))] == [new]


def test_truncate_conflicts_and_scope(isolated_client):
    from app.routers.chat_ws import SessionState, sessions
    from app.services.conversations import conversation_service

    client, owner = isolated_client, "truncate-conflict"
    conversation, cutoff = _message(client, owner, "cut me")
    reply = save(owner, conversation, "reply", "assistant")
    other = asyncio.run(conversation_service.create(owner, "steins_gate", identity_mode="okabe"))["id"]
    other_message = save(owner, other, "other identity")
    beta = asyncio.run(conversation_service.create(owner, "beta", identity_mode="self"))["id"]
    asyncio.run(models.save_message(owner, "user", "beta only", "beta", conversation_id=beta))
    session = SessionState(owner)
    session.conversation_id = conversation
    session.is_busy = True
    sessions[owner] = session
    try:
        busy = truncate(client, owner, conversation, cutoff, reply)
        assert busy.status_code == 409 and busy.json()["detail"]["code"] == "conversation_busy"
        session.is_busy = False
        changed = truncate(client, owner, conversation, cutoff)
        assert changed.status_code == 409 and changed.json()["detail"]["code"] == "conversation_changed"
        assert truncate(client, owner, conversation, reply).status_code == 422
        assert truncate(client, owner, conversation, other_message, reply).status_code == 422
        assert truncate(client, "distinct-owner", conversation, cutoff, reply).status_code == 404
        assert not _db_rows("SELECT * FROM conversation_erasure_requests")
        assert truncate(client, owner, conversation, cutoff, reply).status_code == 200
        assert session.last_assistant_emotion == "neutral" and session.history == []
        for cid, worldline, kept in [(other, "steins_gate", "other identity"), (beta, "beta", "beta only")]:
            assert [m["content"] for m in asyncio.run(models.get_session_messages(owner, worldline, cid))] == [kept]
    finally:
        sessions.pop(owner, None)


def test_late_title_summary_and_reply_cannot_write(isolated_client, isolated_provider_credentials, monkeypatch):
    from app.services.conversations import conversation_service

    client, owner = isolated_client, "truncate-late-writers"
    isolated_provider_credentials.set("deepseek", "test-credential")
    params = {"session_id": owner}
    created = client.post("/api/conversations", json=params)
    assert created.status_code == 201, created.text
    cid = created.json()["id"]
    prefix = save(owner, cid, "kept user")
    prefix_reply = save(owner, cid, "kept reply", "assistant")
    cutoff = save(owner, cid, "cut old details")
    scheduled = []
    monkeypatch.setattr(conversation_service, "schedule_auto_title", lambda *args, **kwargs: scheduled.append(kwargs))

    async def stale_title(**kwargs):
        response = await asyncio.to_thread(truncate, client, owner, cid, cutoff)
        assert response.status_code == 200, response.text
        return {"title": "stale title"}

    snapshot = SimpleNamespace(require=lambda _task: None, model_id="test",
                               adapter=SimpleNamespace(complete_json=stale_title))

    async def run_writers():
        epoch = await models.get_conversation_content_epoch(owner, conversation_id=cid)
        record = await conversation_service.generate_auto_title(
            cid, owner, "steins_gate", user_text="cut old details", assistant_text="", api_key="test",
            provider_snapshot=snapshot, expected_epoch=epoch,
        )
        assert record["title"] == "New Conversation"
        assert not await models.save_memory_summary(owner, "stale summary", conversation_id=cid, expected_epoch=epoch)
        assert await models.save_message(owner, "assistant", "stale reply", conversation_id=cid, expected_epoch=epoch) is None

    asyncio.run(run_writers())
    kwargs, = scheduled
    assert (kwargs["user_text"], kwargs["assistant_text"], kwargs["expected_epoch"]) == ("kept user", "kept reply", 1)
    assert [m["content"] for m in asyncio.run(models.get_session_messages(owner, conversation_id=cid))] == [
        "kept user", "kept reply"
    ]
    assert not asyncio.run(models.get_latest_summary(owner, conversation_id=cid))
    assert truncate(client, owner, cid, prefix, prefix_reply).status_code == 200
    assert len(scheduled) == 1


def test_websocket_waits_for_truncate_and_uses_reloaded_prompt(
    isolated_client, isolated_provider_credentials, monkeypatch,
):
    from app.routers import chat_ws
    from app.services import conversation_erasure as erasure
    from app.services.conversations import conversation_service
    from app.services.memory_v11 import jobs
    from test_emotion_per_conversation import _ScriptedDeepSeek, _SilentTTS

    client, owner, other_owner = isolated_client, "truncate-live", "truncate-other"
    isolated_provider_credentials.set("deepseek", "test-credential")
    cid, _, fact, _ = _source(client, owner, "I run every week")
    save(owner, cid, "[EMO:sad] かなしいわ。", "assistant")
    cutoff = save(owner, cid, "cut the secret lighthouse")
    _change(owner, fact, 1, cid, cutoff, "cut the secret lighthouse")
    last = save(owner, cid, "[EMO:happy] 実験成功よ！", "assistant")
    other_cid, _ = _message(client, other_owner, "parallel conversation")
    asyncio.run(models.save_memory_summary(owner, "cut old summary", conversation_id=cid))
    monkeypatch.setattr(jobs, "process_due_jobs_once", AsyncMock())
    monkeypatch.setattr(conversation_service, "schedule_auto_title", lambda *a, **kw: None)
    monkeypatch.setattr(chat_ws, "tts_manager", _SilentTTS())
    captured = []

    class Provider(_ScriptedDeepSeek):
        async def get_chat_stream(self, active_history, **kwargs):
            captured.append(str((active_history, kwargs.get("system_prompt"))))
            async for part in super().get_chat_stream(active_history, **kwargs):
                yield part

    monkeypatch.setattr(chat_ws, "deepseek_service", Provider(["[EMO:sad] そうね。", "[EMO:sad] そうね。"]))
    paused, release, attempted, admitted = Event(), Event(), Event(), Event()
    commit = erasure._commit_erasure_memory
    admission = chat_ws.session_admission

    async def pause(request, *args, **kwargs):
        if request["cutoff_message_id"] is not None:
            paused.set()
            assert await asyncio.to_thread(release.wait, 10)
        return await commit(request, *args, **kwargs)

    @asynccontextmanager
    async def observe(session):
        if session.session_id == owner:
            attempted.set()
        async with admission(session):
            if session.session_id == owner:
                admitted.set()
            yield

    monkeypatch.setattr(erasure, "_commit_erasure_memory", pause)
    monkeypatch.setattr(chat_ws, "session_admission", observe)

    def auth(socket, conversation):
        socket.send_json({"type": "auth", "protocol_version": 2, "client": "desktop",
                          "conversation_mode": "history", "conversation_id": conversation})
        assert socket.receive_json()["type"] == "session.ready"

    def completed(socket):
        for _ in range(24):
            frame = socket.receive_json()
            assert frame["type"] != "error", frame
            if frame["type"] == "turn.completed":
                return
        pytest.fail("No completed turn")

    try:
        with client.websocket_connect(f"/ws/chat?session_id={owner}") as ws, \
                client.websocket_connect(f"/ws/chat?session_id={other_owner}") as other_ws, \
                ThreadPoolExecutor() as pool:
            auth(ws, cid)
            auth(other_ws, other_cid)
            pending = pool.submit(truncate, client, owner, cid, cutoff, last)
            try:
                assert paused.wait(5)
                ws.send_json({"type": "chat.send", "content": "next user message"})
                assert attempted.wait(5)
                other_ws.send_json({"type": "chat.send", "content": "parallel user message"})
                completed(other_ws)
                assert not admitted.is_set()
            finally:
                release.set()
            assert pending.result(timeout=10).status_code == 200
            completed(ws)
            assert chat_ws.sessions[owner].last_assistant_emotion == "sad"
            assert chat_ws.sessions[owner].content_epoch == 1
        prompt, = [text for text in captured if "next user message" in text]
        assert "かなしいわ。" in prompt
        assert "実験成功よ！" not in prompt and "cut old summary" not in prompt and "lighthouse" not in prompt
        rows = asyncio.run(models.get_session_messages(owner, conversation_id=cid))
        assert "next user message" in [row["content"] for row in rows]
        clean = client.post(f"/api/conversations/{cid}/forget", params={"session_id": owner}, json={})
        assert clean.status_code == 200 and chat_ws.sessions[owner].last_assistant_emotion == "neutral"
    finally:
        chat_ws.sessions.pop(owner, None)
        chat_ws.sessions.pop(other_owner, None)


def test_truncate_does_not_reuse_fact_version_numbers(isolated_client):
    from app.services.memory_v11.contracts import MemoryValidationError

    client, owner = isolated_client, "truncate-aba"
    cid, _, fact, _ = _source(client, owner, "I run every week")
    cutoff = save(owner, cid, "I run at the lighthouse")
    _change(owner, fact, 1, cid, cutoff, "I run at the lighthouse")
    delayed, delayed_source = _message(client, owner, "I run at dawn")
    assert truncate(client, owner, cid, cutoff).status_code == 200
    restored = _details(client, owner, fact)["fact"]["active_version"]
    later, later_source = _message(client, owner, "I run at midday")
    _change(owner, fact, restored, later, later_source, "I run at midday")
    with pytest.raises(MemoryValidationError) as rejected:
        _change(owner, fact, 2, delayed, delayed_source, "I run at dawn")
    assert rejected.value.code == "expected_version_mismatch"
    assert _details(client, owner, fact)["fact"]["active_version"] == 4


def test_preparation_error_preserves_history_and_memory(isolated_client, monkeypatch):
    from app.services.memory_v11 import indexing

    client, owner = isolated_client, "truncate-preflight"
    cid, _, fact, _ = _source(client, owner, "I run every week")
    cutoff = save(owner, cid, "I run at the lighthouse")
    _change(owner, fact, 1, cid, cutoff, "I run at the lighthouse")
    monkeypatch.setattr(indexing, "prepare_passage_embedding", AsyncMock(side_effect=OSError("preparation failure")))
    with pytest.raises(OSError, match="preparation failure"):
        truncate(client, owner, cid, cutoff)
    assert len(asyncio.run(models.get_session_messages(owner, conversation_id=cid))) == 2
    assert _details(client, owner, fact)["fact"]["active_version"] == 2
    assert not _db_rows("SELECT * FROM conversation_erasure_requests")
