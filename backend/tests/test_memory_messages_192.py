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


async def seeded_memories(owner):
    from app.services.memory_v11.repository import create_stable_fact
    job = await make_job(session=owner)
    await jobs.process_memory_job(job_id=job["job_id"], memory_completion=create_completion)
    db = await get_db("steins_gate", "memory")
    try:
        fact = await (await db.execute("SELECT fact_id FROM stable_facts WHERE session_id=?", (owner,))).fetchone()
        exp = await (await db.execute("SELECT experience_id FROM experiences WHERE session_id=?", (owner,))).fetchone()
    finally:
        await db.close()
    extra = [await create_stable_fact(session_id=owner, worldline="steins_gate", identity_mode="self",
                                     display_text=text, semantic_json={}) for text in ["常驻", "查到的认知"]]
    return fact["fact_id"], exp["experience_id"], extra[0]["fact_id"], extra[1]["fact_id"]


async def test_reference_order_dedup_residents_and_limit(isolated_store):
    from app.services.memory_v11.repository import create_stable_fact
    from app.services.prompt_compiler import CompiledPrompt
    owner = "ref192"
    supplement, exp, resident, recalled = await seeded_memories(owner)
    scope = (owner, "steins_gate", "self")
    compiled = CompiledPrompt("system", resident_fact_ids=(resident,),
                              supplement_fact_ids=(resident, supplement, supplement), experience_ids=(exp, exp))
    actual = await events.referenced_items_for_turn(scope, compiled, (resident, supplement, recalled, recalled))
    assert [(i["kind"], i["id"], i["via"]) for i in actual] == [
        ("fact", supplement, "supplement"), ("experience", exp, "episodic"), ("fact", recalled, "recall")]
    many = [await create_stable_fact(session_id=owner, worldline="steins_gate", identity_mode="self",
                                     display_text=f"fact{i}", semantic_json={}) for i in range(35)]
    compiled = CompiledPrompt("system", supplement_fact_ids=tuple(f["fact_id"] for f in many))
    actual = await events.referenced_items_for_turn(scope, compiled, (recalled,))
    assert [i["id"] for i in actual] == [f["fact_id"] for f in many[:32]]
    assert await events.referenced_items_for_turn(scope, CompiledPrompt("system"), ()) == []


async def test_experience_report_tracks_section_and_global_budget_without_prompt_ids(monkeypatch):
    from app.services import prompt_compiler as compiler
    from test_memory_v11_resident_profile import _inputs
    raw_ids = [f"raw-experience-{i}" for i in range(5)]
    rows = [{"experience_id": eid, "display_text": f"MARK{i} " + "旅行の記憶。" * 20}
            for i, eid in enumerate(raw_ids)]

    async def select(**kwargs):
        return rows

    monkeypatch.setattr("app.services.memory_v11.retrieval.select_experience_candidates", select)
    episodes = await compiler._select_v11_prompt_memories(
        session_id="offline", worldline="steins_gate", identity_mode="self", user_message="旅行")
    inputs = _inputs(None, episodic=episodes)
    roomy = compiler.PromptCompiler(token_counter=len, input_budget=100000)
    roomy.BUDGETS = {**roomy.BUDGETS, "episodic": len(roomy._format_episodic_memory(episodes[:2]))}
    section_limited = roomy.compile_with_report(inputs)
    assert section_limited.experience_ids == tuple(raw_ids[:2])
    assert all(eid not in section_limited.text for eid in raw_ids)
    for budget in [len(section_limited.text), len(section_limited.text) - 200, 1]:
        limited = compiler.PromptCompiler(token_counter=len, input_budget=budget)
        limited.BUDGETS = roomy.BUDGETS
        report = limited.compile_with_report(inputs)
        assert report.experience_ids == tuple(eid for i, eid in enumerate(raw_ids) if f"MARK{i}" in report.text)
        # The raw IDs are metadata only; rendering the old RetrievedMemory shape is identical.
        from dataclasses import replace
        from app.services.memory import RetrievedMemory
        old = [RetrievedMemory(**e.model_dump(exclude={"experience_id"})) for e in episodes]
        assert report.text == limited.compile(replace(inputs, episodic=old))


@pytest.mark.parametrize("case", ["supplement", "episodic", "recall", "all", "empty", "v1", "cancel", "unsent", "budget", "cancel-before-complete"])
async def test_processor_reference_publication_and_actual_second_request(isolated_store, monkeypatch, case):
    from unittest.mock import AsyncMock
    from app.services.prompt_compiler import CompiledPrompt
    from app.services import turn_tool_orchestrator as orchestrator
    from test_chat_ws_background_tasks import _new_session, _suppress_auto_title
    from test_memory_v11_resident_profile import _fake_supplement
    session = await _new_session()
    session.protocol_version = 1 if case == "v1" else 2
    supplement, exp, resident, recalled = await seeded_memories(session.session_id)
    compiled = CompiledPrompt("SYSTEM", resident_fact_ids=(resident,),
        supplement_fact_ids=(supplement,) if case in {"supplement", "all", "v1", "cancel-before-complete"} else (),
        experience_ids=(exp,) if case in {"episodic", "all"} else ())

    async def compile_prompt(*args, **kwargs):
        session.turn_compiled_prompt = compiled
        return compiled.text

    _suppress_auto_title(monkeypatch)
    monkeypatch.setattr(chat_ws, "compile_for_session", compile_prompt)
    monkeypatch.setattr(chat_ws, "translate_with_provider", AsyncMock(return_value="翻译"))
    monkeypatch.setattr("app.services.memory_v11.jobs.enqueue_memory_job", AsyncMock())
    recall = _fake_supplement([("m1", recalled, "茶" * (5000 if case == "budget" else 10)),
                              ("m2", resident, "常驻")], need="好み").result
    monkeypatch.setattr(orchestrator, "apply_v11_tool_batch", AsyncMock(return_value=orchestrator.ToolBatchOutcome(
        status="ok", recall_result=recall)))

    async def refresh(*args):
        if case == "unsent":
            raise RuntimeError("second request never sent")
        return recall

    monkeypatch.setattr(orchestrator, "refresh_candidates_for_send", refresh)
    if case == "budget":
        monkeypatch.setattr(chat_ws.config, "REQUEST_INPUT_BUDGET", 2000)
    if case == "cancel-before-complete":
        describe = events.referenced_items_for_turn

        async def cancel_during_describe(*args):
            items = await describe(*args)
            await session.active_segment_pipeline.cancel()
            return items

        monkeypatch.setattr(events, "referenced_items_for_turn", cancel_during_describe)
    captures = []
    wants_recall = case in {"recall", "all", "cancel", "unsent", "budget"}

    async def provider(self, **kwargs):
        captures.append(kwargs)
        if wants_recall and len(captures) == 1:
            yield {"content": None, "tool_calls": [{"index": 0, "id": "recall192", "type": "function",
                "function": {"name": "recall_memory", "arguments": '{"needs":["好み"]}'}}]}
        elif case == "cancel":
            raise asyncio.CancelledError()
        else:
            yield {"content": "そうね、覚えているわ。", "tool_calls": None}

    monkeypatch.setattr(type(chat_ws.deepseek_service), "get_chat_stream", provider)
    session.append_message({"role": "user", "content": "覚えてる？", "id": None})
    queue = asyncio.Queue()
    task = chat_ws.processor_loop(session, "覚えてる？", queue, session.current_epoch, session.history_epoch)
    if case == "cancel":
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        await task
    if session.background_tasks:
        await asyncio.gather(*session.background_tasks, return_exceptions=True)
    frames = [queue.get_nowait()[1] for _ in range(queue.qsize())]
    refs = [frame for frame in frames if frame["type"] == "memory.referenced"]
    expected = {"supplement": [supplement], "episodic": [exp], "recall": [recalled],
                "all": [supplement, exp, recalled]}.get(case, [])
    assert [i["id"] for frame in refs for i in frame["items"]] == expected
    if expected:
        assert len(refs) == 1
        index = frames.index(refs[0])
        assert frames[index + 1]["type"] == "turn.completed"
        assert any(f["type"] == "segment.ready" for f in frames[:index])
        completed = frames[index + 1]
        assert all(refs[0][key] == completed[key] for key in ["conversation_id", "turn_id", "generation"])
    if case == "unsent":
        assert len(captures) == 1 and not session.turn_execution.sent_recall_fact_ids
    if case == "budget":
        assert len(captures) == 2
        assert "茶" * 5000 not in str(captures[1]["active_history"])
        assert recalled not in session.turn_execution.sent_recall_fact_ids
