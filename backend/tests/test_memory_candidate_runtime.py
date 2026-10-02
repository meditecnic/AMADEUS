"""B1: closed-loop runtime on a real stage-A complete candidate.

A synthetic five-DB snapshot is built with the existing preview +
``build_memory_candidate`` path. The published candidate directory is then
the live ``AMADEUS_DATA_DIR``. New messages travel SessionState → processor
→ durable enqueue → ``process_due_jobs_once`` (injected fake
memory_completion) → Memory query API → the next real chat processor turn.

No ``app.main`` import, no full lifespan, no real providers, no real data.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import time
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.db import (
    get_data_root,
    get_db,
    init_db,
    reset_initialization_cache,
    _resolve_path,
)
from app.services.memory import EmbeddingAdapter, memory_service
from app.services.memory_v11 import candidate as cand
from app.services.memory_v11 import jobs as jobs_mod
from app.services.memory_v11.contracts import AUTO_STABLE_CONFIDENCE
from app.services.memory_v11.repository import create_stable_fact
from test_memory_candidate_builder import (
    BACKEND_ROOT,
    BETA,
    SG,
    _db_paths,
    _make_preview,
    _make_snapshot,
    _open,
)
from test_memory_v11_chat_entry import (
    REPLY_JA,
    _ProviderCapture,
    _done,
    _errors,
    _install_fake_provider,
    _make_session,
    _prepare_conversation,
    _record_legacy_extraction,
    _run_turn,
    _suppress_auto_title,
)

OWNER = "ownerA"
OKABE_SENTINEL = "B1RT_OKABE_ONLY"
OWNER_B_SENTINEL = "他人事实"
BETA_SENTINEL = "β事实"
IMPORTED_FACT = "喜欢看动漫"
SESAME_MARKER = "B1RT_SESAME"
DELETE_MARKER = "B1RT_DELETE"
REVISED_MARKER = "B1RT_SESAME_V2"
FORGET_MARKER = "B1RT_FORGET_WAIT"
D30_TEXT = "password: hunter2"
D30_FRAGMENT = "hunter2"
SCOPE = {
    "session_id": OWNER,
    "worldline": SG,
    "identity_mode": "self",
}


def _unit_vector(seed: str) -> list[float]:
    values: list[float] = []
    for block in range(12):
        digest = hashlib.sha256(f"{seed}:{block}".encode("utf-8")).digest()
        values.extend(int(byte) - 127.5 for byte in digest)
    norm = math.sqrt(sum(v * v for v in values)) or 1.0
    return [v / norm for v in values]


class _OfflineEmbedder(EmbeddingAdapter):
    """Fixed fake embedding — lexical retrieval is what this file proves."""

    def __init__(self) -> None:
        super().__init__(model_name="b1-offline-deterministic-384", dimensions=384)
        self.encode_passage_calls = 0
        self.encode_query_calls = 0

    async def encode_passage(self, text: str) -> list[float]:
        self.encode_passage_calls += 1
        return _unit_vector(f"passage:{text}")

    async def encode_query(self, text: str) -> list[float]:
        self.encode_query_calls += 1
        return _unit_vector(f"query:{text}")


def _core_facts_block(system_prompt: str) -> str:
    start = system_prompt.find("CORE FACTS")
    assert start >= 0, "system prompt has no CORE FACTS section"
    rest = system_prompt[start:]
    end = rest.find("\nEPISODIC MEMORY")
    return rest if end < 0 else rest[:end]


def _memory_asgi_app() -> FastAPI:
    """Minimal ASGI app: real Memory + conversation routers, no lifespan."""
    from app.routers import conversations, system_api

    app = FastAPI(title="b1-memory-runtime")
    app.include_router(system_api.router)
    app.include_router(conversations.router)
    return app


def _fail_external(name: str):
    async def _inner(*args, **kwargs):
        raise AssertionError(f"unreplaced external call: {name}")

    return _inner


def _completion_payload(source_message_id: int, marker: str) -> dict:
    """Meet the live CREATE threshold; do not lower AUTO_STABLE_CONFIDENCE."""
    assert AUTO_STABLE_CONFIDENCE == 0.85
    confidence = 0.96
    assert confidence >= AUTO_STABLE_CONFIDENCE
    return {
        "observations": [
            {
                "observation_ref": "o1",
                "source_message_ids": [int(source_message_id)],
                "display_text": f"{marker} 长期偏好",
                "semantic": {
                    "subject": "user",
                    "predicate": "likes",
                    "object": {"name": marker},
                },
                "evidence_kind": "direct_user",
                "memory_class": "stable_candidate",
                "confidence": confidence,
                "topic_label": "偏好",
            }
        ],
        "operations": [
            {
                "op": "CREATE",
                "observation_ref": "o1",
                "target_fact_id": None,
                "expected_version": None,
                "fact_text": f"{marker} 长期偏好",
                "reason_code": "direct_durable_statement",
            }
        ],
    }


def _bind_candidate(monkeypatch, candidate: Path, embedder: _OfflineEmbedder) -> None:
    monkeypatch.delenv("AMADEUS_DB_PATH", raising=False)
    monkeypatch.setenv("AMADEUS_DATA_DIR", str(candidate))
    monkeypatch.setenv("AMADEUS_MEMORY_MODE", "v11")
    reset_initialization_cache()
    monkeypatch.setattr(memory_service, "embedder", embedder)
    monkeypatch.setattr(
        jobs_mod, "default_memory_completion", _fail_external("default_memory_completion")
    )
    monkeypatch.setattr(
        "app.routers.chat_ws.execute_tool_call", _fail_external("execute_tool_call")
    )
    from app.services.tts_queue import tts_manager

    monkeypatch.setattr(tts_manager, "synthesize_async", _fail_external("tts.synthesize_async"))
    monkeypatch.setattr(
        tts_manager, "synthesize_and_queue", _fail_external("tts.synthesize_and_queue")
    )


async def _publish_candidate(tmp_path: Path) -> Path:
    snapshot = _make_snapshot(tmp_path)
    preview_path = _make_preview(snapshot, tmp_path / "inputs")
    output = tmp_path / "candidate"
    result = cand.build_memory_candidate(
        snapshot_dir=snapshot, preview_path=preview_path, output_dir=output
    )
    assert result["ok"] is True, result
    assert (output / "manifest.json").is_file()
    return output


async def _wait_pending_jobs(
    session_id: str, *, min_count: int, timeout: float = 5.0
) -> list[dict]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        db = await get_db(SG, "memory")
        try:
            rows = await (
                await db.execute(
                    """SELECT job_id, source_message_id, conversation_id,
                              identity_mode, state
                         FROM memory_ingest_jobs
                        WHERE session_id=? AND state IN ('pending','processing')
                        ORDER BY created_at ASC""",
                    (session_id,),
                )
            ).fetchall()
        finally:
            await db.close()
        items = [dict(r) for r in rows]
        if len(items) >= min_count:
            return items
        await asyncio.sleep(0.05)
    raise AssertionError(
        f"expected >= {min_count} pending jobs for {session_id}; timed out"
    )


def _last_user_source_id(session, content: str) -> int:
    for msg in reversed(getattr(session, "history", []) or []):
        if msg.get("role") != "user":
            continue
        if content not in str(msg.get("content") or ""):
            continue
        mid = msg.get("id")
        if isinstance(mid, int) and not isinstance(mid, bool) and mid > 0:
            return int(mid)
    raise AssertionError(f"no durable user source_message_id for {content!r}")


async def _find_job_for_source(
    *,
    session_id: str,
    conversation_id: str,
    identity_mode: str,
    source_message_id: int,
    worldline: str = SG,
) -> dict | None:
    db = await get_db(worldline, "memory")
    try:
        row = await (
            await db.execute(
                """SELECT job_id, source_message_id, conversation_id,
                          identity_mode, state, session_id
                     FROM memory_ingest_jobs
                    WHERE session_id=? AND conversation_id=?
                      AND identity_mode=? AND source_message_id=?
                    ORDER BY created_at DESC""",
                (
                    session_id,
                    str(conversation_id),
                    identity_mode,
                    int(source_message_id),
                ),
            )
        ).fetchone()
    finally:
        await db.close()
    return dict(row) if row is not None else None


async def _wait_job_for_source(
    *,
    session_id: str,
    conversation_id: str,
    identity_mode: str,
    source_message_id: int,
    worldline: str = SG,
    timeout: float = 5.0,
) -> dict:
    """Wait until THIS source cursor is enqueued. Other pending jobs do not count."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        row = await _find_job_for_source(
            session_id=session_id,
            conversation_id=conversation_id,
            identity_mode=identity_mode,
            source_message_id=source_message_id,
            worldline=worldline,
        )
        if row is not None:
            return row
        await asyncio.sleep(0.05)
    raise AssertionError(
        f"job for source_message_id={source_message_id} "
        f"session={session_id} conv={conversation_id} "
        f"identity={identity_mode} not enqueued before timeout"
    )


def _assert_projection_has_worker_fact(
    projection: dict,
    fact_id: str,
    *,
    label_fragment: str,
    expected_query: str | None = None,
) -> dict:
    """Accept only the worker-produced fact node, not a query-string echo."""
    fact_id = str(fact_id)
    pid = f"fact:{fact_id}"
    if expected_query is not None:
        echoed = (projection.get("criteria") or {}).get("query")
        assert echoed == expected_query, (
            f"criteria.query echo mismatch: {echoed!r} != {expected_query!r}"
        )
    nodes = [
        n
        for n in (projection.get("nodes") or [])
        if n.get("kind") == "fact" and str(n.get("fact_id")) == fact_id
    ]
    assert len(nodes) == 1, (
        f"expected exactly one fact node for {fact_id}, got {nodes!r} "
        f"in result_ids={projection.get('result_ids')}"
    )
    node = nodes[0]
    assert str(node.get("projection_id")) == pid
    # Fact nodes use `label` as the primary visible label; hub uses label_primary.
    primary = node.get("label_primary")
    if primary is None:
        primary = node.get("label")
    assert label_fragment in str(primary or ""), (
        f"worker fact label missing {label_fragment!r}: {node}"
    )
    result_ids = list(projection.get("result_ids") or [])
    assert pid in result_ids, (
        f"{pid} not in result_ids={result_ids}"
    )
    return node


def _assert_d30_job_completed_without_extraction(
    *,
    worker_results: list[dict],
    job_id: str,
    source_message_id: int,
    completion_source_ids: list[int],
    db_row: dict | None,
) -> None:
    job_id = str(job_id)
    source_message_id = int(source_message_id)
    matched = [
        r
        for r in worker_results
        if str(r.get("job_id")) == job_id
        and int(r.get("source_message_id") or 0) == source_message_id
    ]
    assert matched, (
        f"worker results missing job_id={job_id} source_message_id={source_message_id}: "
        f"{worker_results}"
    )
    assert matched[0]["state"] == "completed", matched[0]
    assert db_row is not None, f"db row missing for {job_id}"
    assert str(db_row.get("job_id")) == job_id
    assert int(db_row.get("source_message_id") or 0) == source_message_id
    assert db_row.get("state") == "completed"
    assert completion_source_ids.count(source_message_id) == 0, (
        f"memory_completion called for D30 source {source_message_id}: "
        f"{completion_source_ids}"
    )


def _job_rows(candidate: Path, *, session_id: str | None = None) -> list[dict]:
    conn = _open(_db_paths(candidate)["sg_memory"])
    try:
        sql = "SELECT job_id, state, retryable, last_error_code, source_message_id FROM memory_ingest_jobs"
        params: tuple = ()
        if session_id is not None:
            sql += " WHERE session_id=?"
            params = (session_id,)
        return [dict(r) for r in conn.execute(sql, params)]
    finally:
        conn.close()


def _receipt_count(candidate: Path) -> int:
    conn = _open(_db_paths(candidate)["sg_memory"])
    try:
        return int(
            conn.execute("SELECT COUNT(*) FROM memory_ingest_receipts").fetchone()[0]
        )
    finally:
        conn.close()


def _fact_texts(candidate: Path, session_id: str, identity: str = "self") -> list[str]:
    conn = _open(_db_paths(candidate)["sg_memory"])
    try:
        rows = conn.execute(
            """SELECT v.display_text FROM stable_facts f
               JOIN stable_fact_versions v
                 ON v.fact_id=f.fact_id AND v.version_no=f.active_version
              WHERE f.session_id=? AND f.identity_mode=? AND f.state='active'""",
            (session_id, identity),
        )
        return [str(r[0]) for r in rows]
    finally:
        conn.close()


@pytest.fixture
async def candidate_runtime(tmp_path, monkeypatch):
    candidate = await _publish_candidate(tmp_path)
    embedder = _OfflineEmbedder()
    _bind_candidate(monkeypatch, candidate, embedder)
    await init_db()

    root = get_data_root()
    assert root == candidate.resolve()
    assert Path(_resolve_path(SG, "memory")) == (
        candidate / "worldlines" / "sg" / "memory.sqlite3"
    ).resolve()
    import app

    assert Path(app.__file__).resolve() == BACKEND_ROOT / "app" / "__init__.py"

    await create_stable_fact(
        session_id=OWNER,
        worldline=SG,
        identity_mode="okabe",
        display_text=f"{OKABE_SENTINEL} 岡部側の事実",
        semantic_json={"subject": "user", "relation": "likes", "object": "okabe_only"},
    )

    capture = _ProviderCapture(reply=REPLY_JA)
    _install_fake_provider(monkeypatch, capture)
    _suppress_auto_title(monkeypatch)
    legacy_calls = _record_legacy_extraction(monkeypatch)
    yield {
        "candidate": candidate,
        "capture": capture,
        "embedder": embedder,
        "legacy_calls": legacy_calls,
        "receipts_before": _receipt_count(candidate),
    }
    reset_initialization_cache()


async def _api_client():
    transport = ASGITransport(app=_memory_asgi_app())
    async with AsyncClient(transport=transport, base_url="http://localhost") as client:
        yield client


async def _facts_list(client: AsyncClient, *, query: str | None = None) -> dict:
    params = dict(SCOPE)
    if query:
        params["query"] = query
    resp = await client.get("/api/memory/facts", params=params)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _projection(client: AsyncClient, *, query: str | None = None) -> dict:
    params = {
        **SCOPE,
        "view": "overview",
    }
    if query:
        params["query"] = query
    resp = await client.get("/api/memory/graph/projection", params=params)
    assert resp.status_code == 200, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# 1. Candidate → new message → worker → API → next chat.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_candidate_new_message_reaches_api_and_next_chat(candidate_runtime):
    candidate = candidate_runtime["candidate"]
    capture = candidate_runtime["capture"]
    freeze_calls = {"n": 0}

    async def freeze_spy(**kwargs):
        freeze_calls["n"] += 1
        return {"observations": [], "operations": []}

    frozen_before = await jobs_mod.process_due_jobs_once(
        lease_owner="b1-freeze", memory_completion=freeze_spy
    )
    assert frozen_before == []
    assert freeze_calls["n"] == 0
    frozen_ids = {"j-pending", "j-processing", "j-failed-retry"}
    frozen_rows = {r["job_id"]: r for r in _job_rows(candidate)}
    for job_id in frozen_ids:
        assert frozen_rows[job_id]["state"] == "cancelled"
        assert int(frozen_rows[job_id]["retryable"]) == 0
        assert frozen_rows[job_id]["last_error_code"] == "candidate_build_freeze"

    session = _make_session(OWNER, identity_mode="self")
    await _prepare_conversation(session, identity_mode="self")
    conv_id = session.conversation_id
    assert conv_id

    events = await _run_turn(session, "动漫")
    assert _done(events) and not _errors(events), events
    imported_prompt = capture.last_system_prompt
    imported_block = _core_facts_block(imported_prompt)
    assert IMPORTED_FACT in imported_block
    for forbidden in (OKABE_SENTINEL, OWNER_B_SENTINEL, BETA_SENTINEL):
        assert forbidden not in json.dumps(capture.last_call["messages"], ensure_ascii=False)
    assert candidate_runtime["legacy_calls"] == []

    sesame_text = f"我长期喜欢黑芝麻汤圆，这是稳定偏好。标记 {SESAME_MARKER}。"
    delete_text = f"另外我还长期收集胶片相机。标记 {DELETE_MARKER}。"
    events = await _run_turn(session, sesame_text)
    assert _done(events) and not _errors(events), events
    events = await _run_turn(session, delete_text)
    assert _done(events) and not _errors(events), events

    # Three processor turns (imported probe + two new statements) each enqueue
    # via create_task; wait for all three before the worker tick.
    pending = await _wait_pending_jobs(OWNER, min_count=3)
    jobs_for_conv = [j for j in pending if str(j["conversation_id"]) == str(conv_id)]
    assert len(jobs_for_conv) >= 3
    for row in jobs_for_conv:
        assert str(row["identity_mode"]) == "self"
        assert int(row["source_message_id"]) > 0

    completion_calls: list[dict] = []

    async def structured_completion(**kwargs):
        completion_calls.append(kwargs)
        text = str(kwargs.get("user_text") or "")
        assert D30_FRAGMENT not in text
        sid = int(kwargs["source_message_id"])
        assert kwargs["session_id"] == OWNER
        assert kwargs["worldline"] == SG
        assert kwargs["identity_mode"] == "self"
        assert str(kwargs["conversation_id"]) == str(conv_id)
        if SESAME_MARKER in text:
            return _completion_payload(sid, SESAME_MARKER)
        if DELETE_MARKER in text:
            return _completion_payload(sid, DELETE_MARKER)
        return {"observations": [], "operations": []}

    results = await jobs_mod.process_due_jobs_once(
        lease_owner="b1-worker",
        memory_completion=structured_completion,
        worldlines=(SG, BETA),
    )
    assert results
    assert all(r["state"] == "completed" for r in results)
    sesame_calls = [c for c in completion_calls if SESAME_MARKER in str(c.get("user_text") or "")]
    delete_calls = [c for c in completion_calls if DELETE_MARKER in str(c.get("user_text") or "")]
    assert sesame_calls and delete_calls, completion_calls
    texts = _fact_texts(candidate, OWNER)
    assert any(SESAME_MARKER in t for t in texts)
    assert any(DELETE_MARKER in t for t in texts)
    assert IMPORTED_FACT in "".join(texts)

    replay = await jobs_mod.process_due_jobs_once(
        lease_owner="b1-replay",
        memory_completion=structured_completion,
        worldlines=(SG, BETA),
    )
    assert replay == [] or all(r["state"] in {"completed", "cancelled"} for r in replay)
    sesame_count = sum(1 for t in _fact_texts(candidate, OWNER) if SESAME_MARKER in t)
    assert sesame_count == 1
    assert sum(1 for t in _fact_texts(candidate, OWNER) if DELETE_MARKER in t) == 1

    async for client in _api_client():
        listed = await _facts_list(client, query=SESAME_MARKER)
        sesame_cards = [
            f for f in listed["facts"] if SESAME_MARKER in str(f.get("display_text") or "")
        ]
        assert sesame_cards, listed
        sesame_fact = sesame_cards[0]
        sesame_id = str(sesame_fact["fact_id"])
        listed_del = await _facts_list(client, query=DELETE_MARKER)
        delete_cards = [
            f for f in listed_del["facts"] if DELETE_MARKER in str(f.get("display_text") or "")
        ]
        assert delete_cards, listed_del
        delete_id = str(delete_cards[0]["fact_id"])
        proj = await _projection(client, query=SESAME_MARKER)
        fact_node = _assert_projection_has_worker_fact(
            proj,
            sesame_id,
            label_fragment=SESAME_MARKER,
            expected_query=SESAME_MARKER,
        )
        print(
            f"[B1] sesame_fact_id={sesame_id} "
            f"projection_id={fact_node['projection_id']} "
            f"label={fact_node.get('label')}",
            flush=True,
        )
        stripped = json.loads(json.dumps(proj))
        stripped["nodes"] = [
            n
            for n in stripped.get("nodes") or []
            if not (
                n.get("kind") == "fact" and str(n.get("fact_id")) == sesame_id
            )
        ]
        assert (stripped.get("criteria") or {}).get("query") == SESAME_MARKER
        with pytest.raises(AssertionError):
            _assert_projection_has_worker_fact(
                stripped,
                sesame_id,
                label_fragment=SESAME_MARKER,
                expected_query=SESAME_MARKER,
            )
        for forbidden in (OKABE_SENTINEL, OWNER_B_SENTINEL, BETA_SENTINEL):
            listed_self = await _facts_list(client)
            blob = json.dumps(listed_self, ensure_ascii=False)
            assert forbidden not in blob

        capture.chat_calls.clear()
        events = await _run_turn(session, f"{SESAME_MARKER} {DELETE_MARKER} 长期偏好")
        assert _done(events) and not _errors(events), events
        memory_block = _core_facts_block(capture.last_system_prompt)
        assert SESAME_MARKER in memory_block, memory_block
        assert DELETE_MARKER in memory_block
        envelope = json.dumps(capture.last_call["messages"], ensure_ascii=False)
        for forbidden in (OKABE_SENTINEL, OWNER_B_SENTINEL, BETA_SENTINEL):
            assert forbidden not in envelope

        patched = await client.patch(
            f"/api/memory/facts/{sesame_id}",
            params=SCOPE,
            json={
                "display_text": f"{REVISED_MARKER} 改成更明确的芝麻偏好",
                "expected_version": int(sesame_fact["active_version"]),
            },
        )
        assert patched.status_code == 200, patched.text
        deleted = await client.delete(
            f"/api/memory/facts/{delete_id}",
            params={**SCOPE, "expected_version": str(delete_cards[0]["active_version"])},
        )
        assert deleted.status_code == 200, deleted.text

        after_edit = await _facts_list(client, query=REVISED_MARKER)
        assert any(
            REVISED_MARKER in str(f.get("display_text") or "") for f in after_edit["facts"]
        )
        gone = await _facts_list(client, query=DELETE_MARKER)
        assert not any(
            DELETE_MARKER in str(f.get("display_text") or "") and f.get("fact_id") == delete_id
            for f in gone["facts"]
        )
        details_gone = await client.get(
            f"/api/memory/facts/{delete_id}/details", params=SCOPE
        )
        assert details_gone.status_code == 404, details_gone.text

        capture.chat_calls.clear()
        events = await _run_turn(session, f"{REVISED_MARKER} {SESAME_MARKER}")
        assert _done(events) and not _errors(events), events
        next_block = _core_facts_block(capture.last_system_prompt)
        assert REVISED_MARKER in next_block
        assert DELETE_MARKER not in next_block

    assert not any(DELETE_MARKER in t for t in _fact_texts(candidate, OWNER))
    for job_id in frozen_ids:
        row = {r["job_id"]: r for r in _job_rows(candidate)}[job_id]
        assert row["state"] == "cancelled"
        assert int(row["retryable"]) == 0
    assert _receipt_count(candidate) >= candidate_runtime["receipts_before"]
    conn = _open(_db_paths(candidate)["sg_memory"])
    try:
        preserved = conn.execute(
            """SELECT receipt_kind FROM memory_ingest_receipts
                WHERE session_id=? AND source_message_id=55""",
            (OWNER,),
        ).fetchone()
        assert preserved is not None
        assert str(preserved[0]) == "provider_empty"
    finally:
        conn.close()
    assert freeze_calls["n"] == 0
    assert all(
        kwargs["session_id"] == OWNER and kwargs["identity_mode"] == "self"
        for kwargs in sesame_calls + delete_calls
    )


# ---------------------------------------------------------------------------
# 2. Forget during fake extraction wait must not write back.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_forget_during_fake_extraction_does_not_write_back(candidate_runtime):
    capture = candidate_runtime["capture"]
    candidate = candidate_runtime["candidate"]
    session = _make_session(OWNER, identity_mode="self")
    await _prepare_conversation(session, identity_mode="self")
    conv_id = session.conversation_id
    from app.routers.chat_ws import sessions

    sessions[OWNER] = session
    try:
        events = await _run_turn(
            session, f"请记住这件事，标记 {FORGET_MARKER}，我长期养了一只名叫琥珀的猫。"
        )
        assert _done(events) and not _errors(events), events
        pending = await _wait_pending_jobs(OWNER, min_count=1)
        assert any(str(j["conversation_id"]) == str(conv_id) for j in pending)

        entered = asyncio.Event()
        release = asyncio.Event()
        completion_calls: list[dict] = []

        async def hanging_completion(**kwargs):
            completion_calls.append(kwargs)
            entered.set()
            await release.wait()
            return _completion_payload(int(kwargs["source_message_id"]), FORGET_MARKER)

        worker = asyncio.create_task(
            jobs_mod.process_due_jobs_once(
                lease_owner="b1-forget-wait",
                memory_completion=hanging_completion,
                worldlines=(SG,),
            )
        )
        await asyncio.wait_for(entered.wait(), timeout=5.0)
        async for client in _api_client():
            resp = await client.post(
                f"/api/conversations/{conv_id}/forget",
                params={"session_id": OWNER, "worldline": SG},
                json={"forget_long_term": True},
            )
            assert resp.status_code == 200, resp.text
        release.set()
        results = await asyncio.wait_for(worker, timeout=10.0)
        assert results
        assert all(r["state"] in {"cancelled", "completed"} for r in results)
        texts = _fact_texts(candidate, OWNER)
        assert not any(FORGET_MARKER in t for t in texts)

        async for client in _api_client():
            listed = await _facts_list(client, query=FORGET_MARKER)
            assert not any(
                FORGET_MARKER in str(f.get("display_text") or "") for f in listed["facts"]
            )

        capture.chat_calls.clear()
        session2 = _make_session(OWNER, identity_mode="self")
        await _prepare_conversation(session2, identity_mode="self")
        events = await _run_turn(session2, FORGET_MARKER)
        assert _done(events) and not _errors(events), events
        assert FORGET_MARKER not in _core_facts_block(capture.last_system_prompt)
        assert len(completion_calls) == 1
    finally:
        sessions.pop(OWNER, None)


# ---------------------------------------------------------------------------
# 3. Adjacent isolation + D30 on the same live candidate.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_isolation_sentinels_and_d30_never_leave_candidate(candidate_runtime):
    capture = candidate_runtime["capture"]
    candidate = candidate_runtime["candidate"]
    session = _make_session(OWNER, identity_mode="self")
    await _prepare_conversation(session, identity_mode="self")

    events = await _run_turn(session, "动漫")
    assert _done(events) and not _errors(events), events
    envelope = json.dumps(capture.last_call["messages"], ensure_ascii=False)
    assert IMPORTED_FACT in _core_facts_block(capture.last_system_prompt)
    for forbidden in (OKABE_SENTINEL, OWNER_B_SENTINEL, BETA_SENTINEL):
        assert forbidden not in envelope

    events = await _run_turn(session, D30_TEXT)
    assert _done(events) and not _errors(events), events
    d30_source_id = _last_user_source_id(session, D30_TEXT)
    conv_id = session.conversation_id
    d30_job = await _wait_job_for_source(
        session_id=OWNER,
        conversation_id=str(conv_id),
        identity_mode="self",
        source_message_id=d30_source_id,
    )
    d30_job_id = str(d30_job["job_id"])
    print(
        f"[B1] d30_source_message_id={d30_source_id} job_id={d30_job_id}",
        flush=True,
    )

    completion_source_ids: list[int] = []

    async def d30_completion(**kwargs):
        sid = int(kwargs["source_message_id"])
        completion_source_ids.append(sid)
        text = str(kwargs.get("user_text") or "")
        if D30_FRAGMENT in text or "password:" in text.lower():
            raise AssertionError("D30 blocked text reached memory_completion")
        return {"observations": [], "operations": []}

    results = await jobs_mod.process_due_jobs_once(
        lease_owner="b1-d30",
        memory_completion=d30_completion,
        worldlines=(SG, BETA),
    )
    db_row = await _find_job_for_source(
        session_id=OWNER,
        conversation_id=str(conv_id),
        identity_mode="self",
        source_message_id=d30_source_id,
    )
    _assert_d30_job_completed_without_extraction(
        worker_results=results,
        job_id=d30_job_id,
        source_message_id=d30_source_id,
        completion_source_ids=completion_source_ids,
        db_row=db_row,
    )
    conn = _open(_db_paths(candidate)["sg_memory"])
    try:
        blob = json.dumps(
            [
                [tuple(r) for r in conn.execute("SELECT display_text FROM stable_fact_versions")],
                [tuple(r) for r in conn.execute("SELECT display_text FROM memory_observations")],
            ],
            ensure_ascii=False,
        )
        assert D30_FRAGMENT not in blob
    finally:
        conn.close()

    beta_session = _make_session("ownerBeta", identity_mode="okabe")
    beta_session.worldline = BETA
    await _prepare_conversation(beta_session, identity_mode="okabe")
    capture.chat_calls.clear()
    events = await _run_turn(session, "动漫")
    assert _done(events) and not _errors(events), events
    owner_a_prompt = json.dumps(capture.last_call["messages"], ensure_ascii=False)
    assert BETA_SENTINEL not in owner_a_prompt
    assert OWNER_B_SENTINEL not in owner_a_prompt
    assert OKABE_SENTINEL not in owner_a_prompt
    assert beta_session.worldline == BETA


# ---------------------------------------------------------------------------
# Assertion boundaries: missing projection node / job not yet processed.
# ---------------------------------------------------------------------------


def test_projection_assertion_fails_when_target_fact_node_is_removed():
    """Query echo alone must not pass; the worker fact node has to be present."""
    fact_id = "worker-fact-id"
    pid = f"fact:{fact_id}"
    envelope = {
        "criteria": {"query": SESAME_MARKER},
        "result_ids": [pid, "fact:other"],
        "nodes": [
            {
                "kind": "continuity_hub",
                "projection_id": "hub",
                "label_primary": "AMADEUS",
                "label_secondary": "SOUL",
            },
            {
                "kind": "fact",
                "projection_id": pid,
                "fact_id": fact_id,
                "label": f"{SESAME_MARKER} 长期偏好",
            },
        ],
    }
    _assert_projection_has_worker_fact(
        envelope, fact_id, label_fragment=SESAME_MARKER, expected_query=SESAME_MARKER
    )
    stripped = json.loads(json.dumps(envelope))
    stripped["nodes"] = [
        n for n in stripped["nodes"] if str(n.get("fact_id")) != fact_id
    ]
    assert stripped["criteria"]["query"] == SESAME_MARKER
    with pytest.raises(AssertionError):
        _assert_projection_has_worker_fact(
            stripped,
            fact_id,
            label_fragment=SESAME_MARKER,
            expected_query=SESAME_MARKER,
        )


def test_d30_assertion_fails_when_target_job_is_still_pending():
    job_id = "d30-job"
    source_message_id = 9090
    with pytest.raises(AssertionError):
        _assert_d30_job_completed_without_extraction(
            worker_results=[
                {
                    "job_id": "ordinary",
                    "source_message_id": 1,
                    "state": "completed",
                }
            ],
            job_id=job_id,
            source_message_id=source_message_id,
            completion_source_ids=[],
            db_row={
                "job_id": job_id,
                "source_message_id": source_message_id,
                "state": "pending",
            },
        )


@pytest.mark.asyncio
async def test_d30_wait_continues_while_only_unrelated_job_is_pending(candidate_runtime):
    session = _make_session(OWNER, identity_mode="self")
    await _prepare_conversation(session, identity_mode="self")
    conv_id = str(session.conversation_id)
    events = await _run_turn(session, "动漫")
    assert _done(events) and not _errors(events), events
    ordinary_mid = _last_user_source_id(session, "动漫")
    ordinary = await _wait_job_for_source(
        session_id=OWNER,
        conversation_id=conv_id,
        identity_mode="self",
        source_message_id=ordinary_mid,
    )
    assert int(ordinary["source_message_id"]) == ordinary_mid

    target_mid = 424242
    wait_task = asyncio.create_task(
        _wait_job_for_source(
            session_id=OWNER,
            conversation_id=conv_id,
            identity_mode="self",
            source_message_id=target_mid,
            timeout=5.0,
        )
    )
    done, _pending = await asyncio.wait({wait_task}, timeout=0.35)
    assert wait_task not in done, (
        "wait returned before the target source was enqueued; "
        "an unrelated pending job must not satisfy the D30 wait"
    )

    await jobs_mod.enqueue_memory_job(
        session_id=OWNER,
        worldline=SG,
        conversation_id=conv_id,
        identity_mode="self",
        source_message_id=target_mid,
        provider_id="deepseek",
        model_id="deepseek-chat",
    )
    found = await asyncio.wait_for(wait_task, timeout=5.0)
    assert int(found["source_message_id"]) == target_mid
    assert str(found["job_id"])
    print(
        f"[B1] delayed-target source={target_mid} job_id={found['job_id']}",
        flush=True,
    )
