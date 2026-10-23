"""Memory #2: v11 writes are gated; legacy deletes affect live recall."""
from __future__ import annotations

import asyncio
import sqlite3
from unittest.mock import AsyncMock

import pytest

from test_memory_v11_api import (
    _seed_candidate_observation,
    _seed_experience,
    _seed_fact,
    _seed_topic,
    isolated_client,
)


WRITES = [
    ("PATCH", "facts/{fact}", {"display_text": "edited", "expected_version": 1}),
    ("DELETE", "facts/{fact}", None),
    ("PATCH", "facts/{fact}/presentation", {"is_pinned": True}),
    ("PATCH", "topics/{topic}", {"display_label": "renamed"}),
    ("PATCH", "experiences/{experience}/presentation", {"is_pinned": True}),
    ("DELETE", "experiences/{experience}", None),
    ("POST", "observations/{observation}/ignore", None),
]


def _database_snapshot(root):
    snapshots = {}
    for path in root.rglob("*.sqlite3"):
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as db:
            snapshots[str(path.relative_to(root))] = db.serialize()
    assert snapshots
    return snapshots


@pytest.mark.parametrize("runtime_mode", ["legacy", "shadow", "v11"])
@pytest.mark.parametrize("method,path,body", WRITES)
def test_v11_write_gate_precedes_scope_body_and_database(
    isolated_client, monkeypatch, tmp_path, runtime_mode, method, path, body,
):
    from app.services.memory_v11 import experiences, facts, observations, topics

    owner = "write-gate"
    ids = {
        "fact": _seed_fact(session_id=owner, display_text="original"),
        "topic": _seed_topic(owner, "self", "original topic"),
        "experience": _seed_experience(session_id=owner, display_text="original experience"),
        "observation": _seed_candidate_observation(session_id=owner, display_text="candidate"),
    }
    monkeypatch.setenv("AMADEUS_MEMORY_MODE", runtime_mode)
    url = "/api/memory/" + path.format(**ids)
    params = {"session_id": owner, "worldline": "steins_gate", "identity_mode": "self", "expected_version": "1"}
    before = _database_snapshot(tmp_path)
    if runtime_mode == "v11":
        response = isolated_client.request(method, url, params=params, json=body)
        assert response.status_code == 200, response.text
        assert _database_snapshot(tmp_path) != before
        return

    # Import before patching: lazy route imports must never bind a fake get_db
    # permanently into a service module, even when a gate assertion fails.
    blocked = []
    with monkeypatch.context() as guard:
        for module, names in (
            (facts, ("user_edit_fact", "delete_fact", "update_fact_presentation")),
            (topics, ("rename_topic",)),
            (experiences, ("update_experience_presentation", "delete_experience")),
            (observations, ("ignore_observation",)),
        ):
            for name in (*names, "get_db"):
                stub = AsyncMock(side_effect=AssertionError(f"read-only request called {module.__name__}.{name}"))
                guard.setattr(module, name, stub)
                blocked.append(stub)
        # Valid operations and malformed/missing scope/body both hit the mode gate first.
        responses = [
            isolated_client.request(method, url, params=params, json=body),
            isolated_client.request(method, url, content=b"not JSON"),
        ]
    for response in responses:
        assert response.status_code == 409, response.text
        assert response.json()["detail"] == {
            "code": "v11_memory_read_only",
            "message": "v11 memory is read-only outside v11 mode",
        }
    for stub in blocked:
        stub.assert_not_called()
        stub.assert_not_awaited()
    assert _database_snapshot(tmp_path) == before


@pytest.mark.parametrize("runtime_mode", ["legacy", "shadow", "v11"])
def test_job_retry_is_not_gated(isolated_client, monkeypatch, runtime_mode):
    monkeypatch.setenv("AMADEUS_MEMORY_MODE", runtime_mode)
    retry = AsyncMock(return_value={
        "job_id": "job", "state": "pending", "session_id": "owner", "identity_mode": "self",
    })
    monkeypatch.setattr("app.services.memory_v11.jobs.retry_memory_job", retry)
    response = isolated_client.post("/api/memory/jobs/job/retry", params={
        "session_id": "owner", "worldline": "beta", "identity_mode": "self",
    })
    assert response.status_code == 200, response.text
    retry.assert_awaited_once_with(job_id="job", session_id="owner", worldline="beta", identity_mode="self")


async def _seed_legacy(source, worldline):
    from app import models
    from app.services.conversations import conversation_service
    from app.services.memory import CoreFactCandidate, memory_service

    owner = "legacy-delete"
    identity = "okabe" if source == "okabe" else "self"
    conversations = {}
    facts = {}
    for wl in ("steins_gate", "beta"):
        conv = await conversation_service.create(owner, wl, title="legacy", identity_mode=identity)
        cid = str(conv["id"])
        conversations[wl] = cid
        mid = await models.save_message(owner, "user", "keep this chat", worldline=wl, conversation_id=cid)
        candidate = CoreFactCandidate(fact_key="favorite_drink", fact_value=f"local {wl}",
                                      confidence=.95, importance=.9, source_message_ids=[mid])
        if source != "shared":
            facts[wl] = await memory_service.upsert_core_fact(owner, wl, candidate, identity_mode=identity)
        await models.save_memory_summary(owner, "stale summary", worldline=wl, conversation_id=cid)
        if wl == worldline and source != "okabe":
            shared_id = await memory_service.upsert_shared_user_fact(
                owner, CoreFactCandidate(fact_key="favorite_drink", fact_value="shared fallback",
                                         confidence=.95, importance=.9, source_message_ids=[mid]),
                origin_worldline=wl, origin_conversation_id=cid, origin_identity_mode="self",
            )
    return owner, identity, conversations, shared_id if source == "shared" else facts[worldline]


@pytest.mark.parametrize("runtime_mode", ["legacy", "shadow"])
@pytest.mark.parametrize("worldline", ["steins_gate", "beta"])
@pytest.mark.parametrize("source", ["local", "shared", "okabe"])
def test_legacy_delete_removes_live_recall_and_preserves_scope_and_chat(
    isolated_client, monkeypatch, runtime_mode, worldline, source,
):
    from app import models
    from app.services.memory import memory_service

    monkeypatch.setenv("AMADEUS_MEMORY_MODE", runtime_mode)
    owner, identity, conversations, fact_id = asyncio.run(_seed_legacy(source, worldline))

    async def read_state():
        return {
            wl: {
                "recall": [row.content for row in await memory_service.select_core_facts(owner, wl, "favorite_drink", identity_mode=identity)],
                "summary": await models.get_latest_summary(owner, worldline=wl, conversation_id=conversations[wl]),
                "chat": await models.get_session_messages(owner, worldline=wl, conversation_id=conversations[wl]),
            }
            for wl in ("steins_gate", "beta")
        }

    before = asyncio.run(read_state())
    target = "shared fallback" if source == "shared" else f"local {worldline}"
    assert any(target in text for text in before[worldline]["recall"])
    path = f"shared-facts/{fact_id}/delete" if source == "shared" else f"facts/{fact_id}/{'dismiss' if source == 'okabe' else 'delete-local'}"
    response = isolated_client.post(f"/api/memory/{path}", params={"session_id": owner, "worldline": worldline})
    assert response.status_code == 200, response.text
    after = asyncio.run(read_state())
    for wl in ("steins_gate", "beta"):
        assert after[wl]["chat"] == before[wl]["chat"]
        if wl == worldline or source == "shared":
            assert not any(target in text for text in after[wl]["recall"])
            assert after[wl]["summary"] == ""
        else:
            assert after[wl]["recall"] == before[wl]["recall"]
            assert after[wl]["summary"] == before[wl]["summary"]
    if source == "local":
        assert any("shared fallback" in text for text in after[worldline]["recall"])
    ledger = isolated_client.get("/api/memory/facts", params={"session_id": owner, "worldline": worldline}).json()
    assert all(row["id"] != fact_id for row in ledger[source])


@pytest.mark.parametrize("runtime_mode", ["legacy", "shadow"])
@pytest.mark.parametrize("path", ["facts/999999/delete-local", "shared-facts/999999/delete", "facts/999999/dismiss"])
def test_missing_legacy_fact_still_returns_404(isolated_client, monkeypatch, runtime_mode, path):
    monkeypatch.setenv("AMADEUS_MEMORY_MODE", runtime_mode)
    response = isolated_client.post(f"/api/memory/{path}", params={"session_id": "missing", "worldline": "beta"})
    assert response.status_code == 404
    assert response.json()["detail"] == "fact_not_found"


@pytest.mark.parametrize("runtime_mode", ["legacy", "shadow"])
@pytest.mark.parametrize("worldline", ["steins_gate", "beta"])
@pytest.mark.parametrize("source", ["local", "shared", "okabe"])
def test_legacy_delete_clears_session_summary_and_compiled_prompt(
    isolated_client, monkeypatch, runtime_mode, worldline, source,
):
    from app.routers.chat_ws import SessionState, sessions
    from app.services.prompt_compiler import compile_for_session

    monkeypatch.setenv("AMADEUS_MEMORY_MODE", runtime_mode)
    owner, identity, conversations, fact_id = asyncio.run(_seed_legacy(source, worldline))
    session = SessionState(owner)
    session.worldline = worldline
    session.identity_mode = identity
    session.conversation_id = conversations[worldline]
    session.base_system_prompt = "You are Amadeus Kurisu."
    target = "shared fallback" if source == "shared" else f"local {worldline}"
    session.memory_summary = f"stale summary: {target}"
    monkeypatch.setitem(sessions, owner, session)
    before = asyncio.run(compile_for_session(session, "favorite_drink"))
    assert target in before
    assert "stale summary" in before
    if source == "local":
        assert "shared fallback" not in before

    path = f"shared-facts/{fact_id}/delete" if source == "shared" else f"facts/{fact_id}/{'dismiss' if source == 'okabe' else 'delete-local'}"
    response = isolated_client.post(f"/api/memory/{path}", params={"session_id": owner, "worldline": worldline})
    assert response.status_code == 200, response.text
    assert session.memory_summary == ""
    after = asyncio.run(compile_for_session(session, "favorite_drink"))
    assert target not in after
    assert "stale summary" not in after
    if source == "local":
        assert "shared fallback" in after
