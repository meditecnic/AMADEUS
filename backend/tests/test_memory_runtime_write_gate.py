"""Memory #2: v11 writes always succeed; job retry is not gated."""
from __future__ import annotations

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


@pytest.mark.parametrize("method,path,body", WRITES)
def test_v11_writes_change_the_database(isolated_client, tmp_path, method, path, body):
    owner = "write-gate"
    ids = {
        "fact": _seed_fact(session_id=owner, display_text="original"),
        "topic": _seed_topic(owner, "self", "original topic"),
        "experience": _seed_experience(session_id=owner, display_text="original experience"),
        "observation": _seed_candidate_observation(session_id=owner, display_text="candidate"),
    }
    url = "/api/memory/" + path.format(**ids)
    params = {"session_id": owner, "worldline": "steins_gate", "identity_mode": "self", "expected_version": "1"}
    before = _database_snapshot(tmp_path)
    response = isolated_client.request(method, url, params=params, json=body)
    assert response.status_code == 200, response.text
    assert _database_snapshot(tmp_path) != before


def test_job_retry_is_not_gated(isolated_client, monkeypatch):
    retry = AsyncMock(return_value={
        "job_id": "job", "state": "pending", "session_id": "owner", "identity_mode": "self",
    })
    monkeypatch.setattr("app.services.memory_v11.jobs.retry_memory_job", retry)
    response = isolated_client.post("/api/memory/jobs/job/retry", params={
        "session_id": "owner", "worldline": "beta", "identity_mode": "self",
    })
    assert response.status_code == 200, response.text
    retry.assert_awaited_once_with(job_id="job", session_id="owner", worldline="beta", identity_mode="self")
