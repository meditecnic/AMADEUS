"""Dispatch 185: chart snapshot and the ledger's additive resident marker."""

from __future__ import annotations

import asyncio
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.db import HISTORY_SCHEMA, MEMORY_SCHEMA, _resolve_path
from app.routers.system_api import router

SCOPE = {"session_id": "chart-session", "worldline": "steins_gate", "identity_mode": "self"}
FIRST = "2026-01-01T00:00:00Z"
LATER = "2026-02-01T00:00:00Z"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("AMADEUS_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("AMADEUS_DB_PATH", raising=False)
    for worldline in ("steins_gate", "beta"):
        for kind, schema in (("memory", MEMORY_SCHEMA), ("history", HISTORY_SCHEMA)):
            path = Path(_resolve_path(worldline, kind))
            path.parent.mkdir(parents=True, exist_ok=True)
            with sqlite3.connect(path) as db:
                db.executescript(schema)
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as test_client:
        yield test_client


def _memory(worldline="steins_gate"):
    db = sqlite3.connect(_resolve_path(worldline, "memory"))
    db.execute("PRAGMA foreign_keys=ON")
    return db


def _topic(db, tid, *, mode="self", session="chart-session", created=FIRST):
    db.execute(
        "INSERT INTO memory_topics VALUES(?,?,?,?,?,?,?)",
        (tid, session, mode, tid, f"topic {tid}", created, LATER),
    )


def _fact(db, fid, *, mode="self", session="chart-session", topic=None,
          created=FIRST, versions=1, pinned=False, state="active", invalid=False,
          text=None, fingerprint=None):
    db.execute(
        """INSERT INTO stable_facts VALUES(?,?,?,?,?,?,?,?,?,NULL)""",
        (fid, session, mode, topic, state, versions, int(pinned), created, LATER),
    )
    for version in range(1, versions + 1):
        db.execute(
            """INSERT INTO stable_fact_versions VALUES(?,?,?,?,?,?,?,?,?,?,NULL)""",
            (fid, version, text or f"认知 {fid} v{version}", "{}", fingerprint or fid,
             0.9, "create" if version == 1 else "user_edit",
             version - 1 if version > 1 else None,
             created if version == 1 else LATER,
             LATER if version < versions or invalid else None),
        )


def _observation(db, oid, *, mode="self", session="chart-session"):
    db.execute(
        """INSERT INTO memory_observations(
               observation_id,session_id,identity_mode,evidence_kind,memory_class,
               status,created_at,updated_at
           ) VALUES(?,?,?,'direct_user','stable_candidate','attached',?,?)""",
        (oid, session, mode, FIRST, FIRST),
    )


def _experience(db, eid, *, mode="self", session="chart-session", created=FIRST,
                status="active", expires=None, pinned=False):
    _observation(db, eid, mode=mode, session=session)
    db.execute(
        """INSERT INTO experiences VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,NULL,?)""",
        (eid, session, "conversation", mode, eid, f"片段 {eid}", "{}", eid,
         0.9, status, expires, created, LATER, int(pinned)),
    )


def _message(mid, created, *, mode="self", session="chart-session", worldline="steins_gate"):
    with sqlite3.connect(_resolve_path(worldline, "history")) as db:
        db.execute(
            """INSERT INTO conversations(id,session_id,title,identity_mode,created_at)
               VALUES(?,?,'chart conversation',?,?)""", (mid, session, mode, FIRST),
        )
        db.execute(
            """INSERT INTO messages(session_id,conversation_id,role,content,created_at)
               VALUES(?,?,'user','hello',?)""", (session, mid, created),
        )


def _chart(client, **scope):
    response = client.get("/api/memory/chart", params=SCOPE | scope)
    assert response.status_code == 200, response.text
    return response.json()


def test_chart_fields_lifecycle_scope_and_order(client):
    with _memory() as db:
        for tid in ("t-b", "t-a", "t-empty"):
            _topic(db, tid)
        for index in reversed(range(6)):
            _fact(db, f"f-{index}", topic="t-a" if index < 3 else "t-b",
                  created=FIRST if index < 2 else f"2026-01-0{index + 1}T00:00:00Z",
                  versions=3 if index == 0 else 1, pinned=index == 1)
        for oid in ("o1", "o2", "o3"):
            _observation(db, oid)
            db.execute("INSERT INTO stable_fact_evidence VALUES('f-0',1,?,'primary')", (oid,))
        # Same observation attached to two versions still counts only once.
        db.execute("INSERT INTO stable_fact_evidence VALUES('f-0',3,'o1','supporting')")
        _fact(db, "deleted", state="deleted", topic="t-a")
        _fact(db, "tombstoned", topic="t-b")
        db.execute("INSERT INTO memory_tombstones VALUES('tomb','chart-session','self','tombstoned',NULL,NULL,NULL,?)", (FIRST,))
        _fact(db, "invalid", invalid=True)
        _fact(db, "no-version")
        db.execute("UPDATE stable_facts SET active_version=NULL WHERE fact_id='no-version'")
        _experience(db, "e-live", pinned=True, expires="2999-01-01T00:00:00Z")
        _experience(db, "e-old", expires="2000-01-01T00:00:00Z")
        _experience(db, "e-status-expired", status="expired")
        _experience(db, "e-deleted", status="deleted")
        _experience(db, "e-pending", status="pending_source_delete")
        for mode, session in (("okabe", "chart-session"), ("self", "other-session")):
            _topic(db, f"other-{mode}", mode=mode, session=session)
            _fact(db, f"other-{mode}", mode=mode, session=session)
            _experience(db, f"other-{mode}", mode=mode, session=session)
    with _memory("beta") as db:
        _topic(db, "beta-topic")
        _fact(db, "beta-fact")
        _experience(db, "beta-experience")

    body = _chart(client)
    assert set(body) == {"scope", "generated_at", "origin_at", "topics", "facts", "experiences", "counts", "truncated"}
    assert body["scope"] == SCOPE
    generated = datetime.fromisoformat(body["generated_at"].replace("Z", "+00:00"))
    assert abs((datetime.now(timezone.utc) - generated).total_seconds()) < 10
    assert body["origin_at"] == FIRST
    assert body["counts"] == {"facts": 6, "experiences": 1, "topics": 3}
    assert body["truncated"] is False
    assert body["topics"] == [
        {"id": tid, "label": f"topic {tid}", "created_at": FIRST, "fact_count": count}
        for tid, count in (("t-a", 3), ("t-b", 3), ("t-empty", 0))
    ]
    assert body["facts"] == [
        {"id": f"f-{i}", "label": f"认知 f-{i} v{3 if i == 0 else 1}",
         "topic_id": "t-a" if i < 3 else "t-b",
         "created_at": FIRST if i < 2 else f"2026-01-0{i + 1}T00:00:00Z",
         "updated_at": LATER, "is_pinned": i == 1,
         "evidence_count": 3 if i == 0 else 0, "version_count": 3 if i == 0 else 1,
         "resident": True}
        for i in range(6)
    ]
    assert body["experiences"] == [{"id": "e-live", "label": "片段 e-live", "created_at": FIRST,
                                       "updated_at": LATER, "is_pinned": True,
                                       "expires_at": "2999-01-01T00:00:00Z"}]
    response = client.get("/api/memory/graph/projection", params=SCOPE | {"view": "overview"})
    assert response.status_code == 200, response.text
    assert {n["fact_id"] for n in response.json()["nodes"] if n["kind"] == "fact"} == {f"f-{i}" for i in range(6)}


@pytest.mark.parametrize("surface", ["chart", "facts"])
def test_resident_matches_chat_global_selection(client, surface):
    from app.services.memory_v11.recall import MemoryScope, load_resident_fact_candidates
    from app.services.prompt_compiler import prompt_compiler

    with _memory() as db:
        for index in range(40):
            _fact(db, f"f-{index:02}", pinned=index == 39, text=f"F{index}")
        _fact(db, "dedup", text="duplicate", fingerprint="f-00")
    candidates = asyncio.run(load_resident_fact_candidates(MemoryScope(**SCOPE)))
    selected = prompt_compiler.select_resident_profile(candidates, tool_capable=True)
    expected = {f["fact_id"] for f in selected.facts}
    assert len(expected) == 32
    assert selected.facts[0]["fact_id"] == "f-39"
    assert "f-32" not in expected
    if surface == "chart":
        actual = {f["id"]: f["resident"] for f in _chart(client)["facts"]}
    else:
        # A filtered page must still reflect selection over the entire scope.
        response = client.get("/api/memory/facts", params=SCOPE | {"limit": 100})
        assert response.status_code == 200
        actual = {f["fact_id"]: f["resident"] for f in response.json()["facts"]}
        filtered = client.get("/api/memory/facts", params=SCOPE | {"query": "F32", "limit": 1}).json()
        assert filtered["facts"][0]["resident"] is False
    assert actual == {str(c["fact_id"]): c["fact_id"] in expected for c in candidates}


@pytest.mark.parametrize("surface", ["chart", "facts"])
def test_resident_uses_token_budget_even_for_pinned(client, surface):
    from app.services.memory_v11.recall import MemoryScope, load_resident_fact_candidates
    from app.services.prompt_compiler import prompt_compiler

    with _memory() as db:
        _fact(db, "pinned-too-long", pinned=True, text="长" * 4000)
        _fact(db, "ordinary", text="short")
    candidates = asyncio.run(load_resident_fact_candidates(MemoryScope(**SCOPE)))
    expected = {f["fact_id"] for f in prompt_compiler.select_resident_profile(candidates, tool_capable=True).facts}
    assert not expected
    response = client.get(f"/api/memory/{surface}", params=SCOPE)
    assert response.status_code == 200
    assert all(f["resident"] is False for f in response.json()["facts"])


@pytest.mark.parametrize("source", ["messages", "fact", "experience", "empty"])
def test_origin_at_uses_scoped_messages_then_memory(client, source):
    _message("other-mode", "1999-01-01T00:00:00Z", mode="okabe")
    _message("other-session", "1999-01-01T00:00:00Z", session="other")
    _message("other-worldline", "1999-01-01T00:00:00Z", worldline="beta")
    expected = None
    with _memory() as db:
        _topic(db, "old-empty-topic", created="1990-01-01T00:00:00Z")
        if source in ("messages", "fact"):
            _fact(db, "first-fact", created=FIRST)
            _experience(db, "later-experience", created=LATER)
            expected = FIRST
        if source == "experience":
            _experience(db, "first-experience", created=FIRST)
            expected = FIRST
    if source == "messages":
        # Earliest message wins even when memory predates it. Compare instants,
        # including the app's existing mix of Z and +00:00 timestamps.
        _message("later-message", "2026-04-01T00:00:00Z")
        expected = "2026-03-01T00:00:00+00:00"
        _message("first-message", expected)
    assert _chart(client)["origin_at"] == expected


def test_chart_combined_cap_retains_latest_and_full_counts(client):
    with _memory() as db:
        _topic(db, "topic")
        _topic(db, "empty")
        for index in range(1001):
            _fact(db, f"f-{index:04}", topic="topic", created=FIRST if index == 0 else LATER)
        for index in range(1000):
            _experience(db, f"e-{index:04}", created=LATER)
    body = _chart(client)
    assert body["counts"] == {"facts": 1001, "experiences": 1000, "topics": 2}
    assert body["truncated"] is True
    assert len(body["facts"]) == len(body["experiences"]) == 1000
    assert body["facts"][0]["id"] == "f-0001"
    assert [f["id"] for f in body["facts"]] == sorted(f["id"] for f in body["facts"])
    assert [e["id"] for e in body["experiences"]] == sorted(e["id"] for e in body["experiences"])
    assert body["topics"][1]["fact_count"] == 1001
    assert body["origin_at"] == FIRST  # before truncation
    with _memory() as db:
        db.execute("UPDATE stable_facts SET state='deleted' WHERE fact_id='f-0000'")
    assert _chart(client)["truncated"] is False  # exactly 2000


@pytest.mark.parametrize("field,value,code", [
    ("session_id", None, "empty_session_id"), ("session_id", " ", "empty_session_id"),
    ("worldline", None, "missing_worldline"), ("worldline", "wrong", "invalid_worldline"),
    ("identity_mode", None, "missing_identity_mode"), ("identity_mode", "wrong", "invalid_identity_mode"),
])
def test_chart_validation_envelope(client, field, value, code):
    params = SCOPE | {field: value}
    response = client.get("/api/memory/chart", params={k: v for k, v in params.items() if v is not None})
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == code
    assert isinstance(response.json()["detail"]["message"], str)


@pytest.mark.parametrize("kind", ["memory", "history"])
@pytest.mark.parametrize("failure", ["open", "read"])
def test_chart_unavailable_store_is_503(client, monkeypatch, tmp_path, kind, failure):
    import aiosqlite

    from app.services.memory_v11 import chart

    path = Path(_resolve_path("steins_gate", kind))
    if failure == "open":
        # Windows cannot delete a store the app still holds open, so point the
        # chart at a directory instead: SQLite cannot open it as a database.
        path = tmp_path / f"blocked-{kind}"
        path.mkdir()
        resolve = chart._resolve_path
        monkeypatch.setattr(
            chart, "_resolve_path",
            lambda worldline, store: str(path) if store == kind else resolve(worldline, store),
        )
    else:
        connect = aiosqlite.connect

        async def failing_connect(database, *args, **kwargs):
            db = await connect(database, *args, **kwargs)
            if kind in str(database):
                async def fail(*args, **kwargs):
                    raise sqlite3.OperationalError("disk I/O error: private path")
                db.execute = fail
            return db

        monkeypatch.setattr(aiosqlite, "connect", failing_connect)
    response = client.get("/api/memory/chart", params=SCOPE)
    assert response.status_code == 503
    detail = response.json()["detail"]
    assert set(detail) == {"code", "message"}
    assert "private" not in response.text and str(path) not in response.text


def test_chart_opens_each_store_readonly_in_one_transaction(client, monkeypatch):
    import aiosqlite

    connect = aiosqlite.connect
    opened = []

    async def track_connect(database, *args, **kwargs):
        db = await connect(database, *args, **kwargs)
        statements = []
        await db.set_trace_callback(statements.append)
        opened.append((str(database), kwargs, statements))
        return db

    monkeypatch.setattr(aiosqlite, "connect", track_connect)
    _chart(client)
    assert len(opened) == 2
    for path, kwargs, statements in opened:
        assert "mode=ro" in path and kwargs["uri"] is True
        assert statements.count("BEGIN") == statements.count("COMMIT") == 1
        assert all(sql.lstrip().split()[0].upper() in {"SELECT", "PRAGMA", "BEGIN", "COMMIT"} for sql in statements)


def test_facts_created_and_resident_match_chart(client):
    with _memory() as db:
        for index in range(40):
            _fact(db, f"f-{index:02}", created=FIRST if index % 2 else LATER,
                  versions=2, pinned=index == 39, text=f"F{index}")
        _fact(db, "other-scope", mode="okabe")
    chart = {f["id"]: f for f in _chart(client)["facts"]}
    body = client.get("/api/memory/facts", params=SCOPE | {"limit": 100}).json()
    assert len(body["facts"]) == len(chart) == 40
    for fact in body["facts"]:
        expected = chart[fact["fact_id"]]
        assert fact["created_at"] == expected["created_at"]
        assert fact["resident"] is expected["resident"]
    assert body["resident_count"] == sum(f["resident"] for f in chart.values()) == 32
    assert body["resident_limit"] == 32
    assert body["resident_token_limit"] == 1200
    filtered = client.get("/api/memory/facts", params=SCOPE | {"query": "F32", "limit": 1}).json()
    assert filtered["resident_count"] == 32
    empty = client.get("/api/memory/facts", params=SCOPE | {"session_id": "empty"}).json()
    assert empty["resident_count"] == 0
