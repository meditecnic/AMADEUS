"""Offline contracts for the ephemeral memory WS notifications."""
import asyncio
from uuid import uuid4

import pytest

from app import models
from app.db import get_db
from app.routers import chat_ws
from app.services.conversations import conversation_service
from app.services.memory_v11 import events, jobs
from test_memory_v11_jobs import isolated_store  # noqa: F401


async def drain():
    while events._pending:
        await asyncio.gather(*events._pending)


async def make_job(session="messages192", turn="turn192"):
    conv = await conversation_service.create(session, "steins_gate", identity_mode="self")
    mid = await models.save_message(session, "user", "我喜欢抹茶", worldline="steins_gate",
                                    conversation_id=conv["id"], turn_id=turn)
    return await jobs.enqueue_memory_job(
        session_id=session, worldline="steins_gate", identity_mode="self",
        conversation_id=conv["id"], source_message_id=mid, pipeline_version="memory-v11-1",
        provider_id="deepseek", model_id="deepseek-chat",
    )


async def create_completion(**kwargs):
    return {
        "observations": [
            {"observation_ref": str(i), "display_text": text,
             "semantic": {"subject": "user", "predicate": "likes", "object": {"name": text}},
             "evidence_kind": "direct_user", "memory_class": memory_class, "confidence": 0.97}
            for i, (text, memory_class) in enumerate([
                ("喜欢抹茶", "stable_candidate"), ("今天喝了抹茶", "episodic")])
        ],
        "operations": [{"op": "CREATE", "observation_ref": str(i),
                        "target_fact_id": None, "expected_version": None,
                        "reason_code": "direct_durable_statement"} for i in range(2)],
    }


async def test_recorded_job_once_current_chart_metadata(isolated_store):
    job = await make_job()
    received, foreign = [], []
    off = events.subscribe((job["session_id"], "steins_gate", "self"), received.append)
    foreign_off = [events.subscribe(scope, foreign.append) for scope in [
        ("other", "steins_gate", "self"), (job["session_id"], "beta", "self"),
        (job["session_id"], "steins_gate", "okabe")]]
    try:
        result = await jobs.process_memory_job(job_id=job["job_id"], memory_completion=create_completion)
        assert result["state"] == "completed"
        await drain()
        assert len(received) == 1
        frame = received[0]
        assert frame == {"type": "memory.recorded", "turn_id": "turn192",
                         "conversation_id": job["conversation_id"],
                         "source_message_id": job["source_message_id"], "changes": frame["changes"]}
        assert [(c["kind"], c["change"], c["label"]) for c in frame["changes"]] == [
            ("fact", "create", "喜欢抹茶"), ("experience", "create", "今天喝了抹茶")]
        for c in frame["changes"]:
            db = await get_db("steins_gate", "memory")
            try:
                table, key = ("stable_facts", "fact_id") if c["kind"] == "fact" else ("experiences", "experience_id")
                row = await (await db.execute(f"SELECT created_at FROM {table} WHERE {key}=?", (c["id"],))).fetchone()
                assert c["created_at"] == row["created_at"]
            finally:
                await db.close()
        await jobs.process_memory_job(job_id=job["job_id"], memory_completion=create_completion)
        events.publish_recorded(result, {"replay": True, "changes": frame["changes"]})
        await drain()
        assert len(received) == 1 and not foreign
    finally:
        off()
        for unsubscribe in foreign_off:
            unsubscribe()


@pytest.mark.parametrize("case", ["empty", "attach", "failed", "missing", "no-turn", "forgotten", "disconnected"])
async def test_recorded_suppressed_and_disconnect_does_not_fail_job(isolated_store, case):
    job = await make_job(turn=None if case == "no-turn" else "turn192")
    received = []
    off = events.subscribe((job["session_id"], "steins_gate", "self"), received.append)

    async def complete(**kwargs):
        if case == "failed":
            raise RuntimeError("private provider details")
        if case == "empty":
            return {"observations": [], "operations": []}
        if case == "forgotten":
            from app.services.conversation_erasure import request_erasure
            await request_erasure(session_id=job["session_id"], identity_mode="self", worldline="steins_gate",
                                  conversation_id=job["conversation_id"], action="delete", forget_long_term=True)
        return await create_completion(**kwargs)

    try:
        if case == "missing":
            db = await get_db("steins_gate", "history")
            await db.execute("DELETE FROM messages WHERE id=?", (job["source_message_id"],))
            await db.commit()
            await db.close()
        if case == "disconnected":
            off()
        result = await jobs.process_memory_job(job_id=job["job_id"], memory_completion=complete)
        if case == "attach":
            # Check the filter independently of reconciliation's ATTACH tests.
            await drain()
            received.clear()
            events.publish_recorded(result, {"changes": [{"kind": "fact", "id": "x", "change": "attach"}]})
        await drain()
        assert received == []
        assert result["state"] == ({"missing": "cancelled", "forgotten": "cancelled", "failed": "pending"}.get(case, "completed"))
    finally:
        off()


async def test_subscriber_failure_is_type_only_and_job_stays_completed(isolated_store, caplog):
    job = await make_job()

    def broken(payload):
        raise RuntimeError("private detail")

    off = events.subscribe((job["session_id"], "steins_gate", "self"), broken)
    try:
        result = await jobs.process_memory_job(job_id=job["job_id"], memory_completion=create_completion)
        await drain()
        assert result["state"] == "completed"
        assert "RuntimeError" in caplog.text and "private detail" not in caplog.text
    finally:
        off()


@pytest.mark.parametrize("protocol", [1, 2])
def test_ws_subscription_scope_conversation_and_cleanup(app_client, protocol):
    session_id = f"messages192-{uuid4()}"
    conv = app_client.post("/api/conversations", json={
        "session_id": session_id, "worldline": "steins_gate", "identity_mode": "self"}).json()
    other = app_client.post("/api/conversations", json={
        "session_id": session_id, "worldline": "steins_gate", "identity_mode": "okabe"}).json()
    scope = (session_id, "steins_gate", "self")
    with app_client.websocket_connect(f"/ws/chat?session_id={session_id}") as ws:
        ws.send_json({"type": "auth", "protocol_version": protocol, "conversation_id": conv["id"]})
        assert ws.receive_json()["type"] == "session.ready"
        assert bool(events._subscribers.get(scope)) == (protocol == 2)

        def send(conversation):
            for receive in tuple(events._subscribers.get(scope, ())):
                receive({"type": "memory.recorded", "conversation_id": conversation,
                         "turn_id": "old-turn", "source_message_id": 123, "changes": []})

        app_client.portal.call(send, other["id"])
        app_client.portal.call(send, conv["id"])
        ws.send_json({"type": "unknown192"})
        if protocol == 2:
            assert ws.receive_json()["type"] == "memory.recorded"
        assert ws.receive_json()["type"] == "error"
        ws.send_json({"type": "conversation.switch", "conversation_id": other["id"]})
        assert ws.receive_json()["type"] == "conversation.switched"
        assert scope not in events._subscribers
        if protocol == 2:
            assert (session_id, "steins_gate", "okabe") in events._subscribers
        ws.send_json({"type": "worldline.switch", "target_worldline": "beta"})
        assert ws.receive_json()["type"] == "worldline.switched"
        assert not any(s[:2] == (session_id, "steins_gate") for s in events._subscribers)
    assert not any(s[0] == session_id for s in events._subscribers)


async def test_writer_rechecks_current_conversation():
    from unittest.mock import AsyncMock
    session = chat_ws.SessionState("writer192")
    session.protocol_version = 2
    session.conversation_id = "now"
    queue = asyncio.Queue()
    queue.put_nowait((session.current_epoch, {"type": "memory.recorded", "conversation_id": "old"}))
    queue.put_nowait(None)
    ws = AsyncMock()
    await chat_ws.writer_loop(queue, session, ws)
    ws.send_json.assert_not_awaited()
