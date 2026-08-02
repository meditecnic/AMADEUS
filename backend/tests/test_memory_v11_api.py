"""MEMORY-V11 REST contracts: S3a scoped read + job retry; S0 deferred markers.

S3a (must pass):
  - GET /api/memory/status with identity_mode → job aggregates
  - GET /api/memory/facts with identity_mode → scoped cards (no shared)
  - POST /api/memory/jobs/{id}/retry with scope ownership
  - no manual POST create-fact endpoint

Deferred (still red / xfail until later slices):
  - S6 legacy route removal
  - S4 conversation delete_derived_memory saga
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient


# Exact path templates as registered today; must leave production after cutover.
LEGACY_ROUTE_TEMPLATES = (
    ("POST", "/api/memory/facts/{local_fact_id}/promote"),
    ("POST", "/api/memory/facts/{okabe_fact_id}/reclassify"),
    ("POST", "/api/memory/facts/{okabe_fact_id}/dismiss"),
    ("POST", "/api/memory/facts/dismiss-batch"),
    ("POST", "/api/memory/facts/{local_fact_id}/delete-local"),
    ("POST", "/api/memory/shared-facts/{shared_fact_id}/delete"),
)


@pytest.fixture
def client(app_client: TestClient) -> TestClient:
    return app_client


@pytest.fixture
def isolated_client(tmp_path, monkeypatch, isolated_provider_credentials):
    """TestClient with isolated AMADEUS_DATA_DIR (v11 schema + jobs)."""
    from app.db import reset_initialization_cache
    from app.main import app

    monkeypatch.delenv("AMADEUS_DB_PATH", raising=False)
    monkeypatch.setenv("AMADEUS_DATA_DIR", str(tmp_path))
    reset_initialization_cache()
    with patch(
        "app.main.sidecar_supervisor.ensure_available",
        new=AsyncMock(return_value=SimpleNamespace(url=None)),
    ), patch(
        "app.main.sidecar_supervisor.close",
        new=AsyncMock(),
    ):
        with TestClient(app) as client:
            yield client
    reset_initialization_cache()


def _session(prefix: str) -> str:
    return f"{prefix}-{uuid4()}"


def _create_conversation(client: TestClient, session_id: str, title: str) -> str:
    created = client.post(
        "/api/conversations",
        json={
            "session_id": session_id,
            "worldline": "steins_gate",
            "title": title,
            "identity_mode": "self",
        },
    )
    assert created.status_code == 201, created.text
    body = created.json()
    conversation_id = body.get("id") or body.get("conversation_id")
    assert conversation_id, body
    return str(conversation_id)


def _registered_routes(client: TestClient) -> set[tuple[str, str]]:
    """Walk nested Starlette/FastAPI routers (_IncludedRouter) for full path table."""
    routes: set[tuple[str, str]] = set()

    def walk(nodes, prefix: str = "") -> None:
        for route in nodes:
            path = getattr(route, "path", None)
            methods = getattr(route, "methods", None)
            nested = getattr(route, "routes", None)
            if path and methods:
                full = prefix + path if prefix and not path.startswith(prefix) else path
                for method in methods:
                    if method in {"HEAD", "OPTIONS"}:
                        continue
                    routes.add((method.upper(), full))
            if nested is not None:
                mount = prefix
                mount_path = getattr(route, "path", None)
                if mount_path and mount_path not in ("", "/"):
                    mount = prefix + mount_path
                walk(nested, mount)

    walk(client.app.routes)
    # Also consult system_api router directly (authoritative Memory surface).
    from app.routers import system_api

    for route in system_api.router.routes:
        path = getattr(route, "path", None)
        methods = getattr(route, "methods", None) or set()
        if not path:
            continue
        for method in methods:
            if method in {"HEAD", "OPTIONS"}:
                continue
            routes.add((method.upper(), path))
    return routes


# ---------------------------------------------------------------------------
# S3a — must pass
# ---------------------------------------------------------------------------


def test_s0_canonical_status_returns_job_counts(client: TestClient):
    """D24 / S3a: status includes pending/processing/failed aggregates; no secrets."""
    response = client.get(
        "/api/memory/status",
        params={
            "session_id": _session("v11-status"),
            "worldline": "steins_gate",
            "identity_mode": "self",
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    for key in ("pending_count", "processing_count", "failed_count"):
        assert key in body, f"missing {key} in {body}"
    blob = str(body).lower()
    assert "password" not in blob
    assert "api_key" not in blob


def test_s0_canonical_facts_list_shape(client: TestClient):
    """P05 / S3a: GET /api/memory/facts returns scoped cards, not local/shared ledger."""
    response = client.get(
        "/api/memory/facts",
        params={
            "session_id": _session("v11-facts"),
            "worldline": "steins_gate",
            "identity_mode": "self",
            "query": "动漫",
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert "items" in body or "facts" in body, body
    assert "shared" not in body, "shared scope must leave product list surface"


def test_s0_no_manual_create_fact_endpoint(client: TestClient):
    """D39: POST /api/memory/facts must not create a fact without source message."""
    response = client.post(
        "/api/memory/facts",
        params={
            "session_id": _session("v11-manual"),
            "worldline": "steins_gate",
            "identity_mode": "self",
        },
        json={"display_text": "手动新增应被拒绝", "topic_label": "测试"},
    )
    assert response.status_code not in (200, 201), response.text


def test_s3a_status_counts_reflect_failed_jobs(isolated_client: TestClient):
    """Job aggregates are scoped and ignore other identity/session rows."""
    import asyncio

    from app.services.memory_v11 import jobs as jobs_mod

    session_id = _session("s3a-status")
    other_session = _session("s3a-other")

    async def _seed() -> str:
        job = await jobs_mod.enqueue_memory_job(
            session_id=session_id,
            worldline="steins_gate",
            conversation_id="c-status",
            identity_mode="self",
            source_message_id=1,
            provider_id="deepseek",
            model_id="deepseek-chat",
        )
        claimed = await jobs_mod.claim_memory_job(
            job_id=job["job_id"], lease_owner="tester", worldline="steins_gate"
        )
        assert claimed is not None
        await jobs_mod.mark_memory_job_failed(
            job_id=claimed["job_id"],
            error_code="provider_unavailable",
            permanent=True,
            worldline="steins_gate",
            lease_owner="tester",
        )
        await jobs_mod.enqueue_memory_job(
            session_id=other_session,
            worldline="steins_gate",
            conversation_id="c-other",
            identity_mode="self",
            source_message_id=1,
            provider_id="deepseek",
            model_id="deepseek-chat",
        )
        await jobs_mod.enqueue_memory_job(
            session_id=session_id,
            worldline="steins_gate",
            conversation_id="c-okabe",
            identity_mode="okabe",
            source_message_id=1,
            provider_id="deepseek",
            model_id="deepseek-chat",
        )
        return job["job_id"]

    asyncio.run(_seed())

    self_resp = isolated_client.get(
        "/api/memory/status",
        params={
            "session_id": session_id,
            "worldline": "steins_gate",
            "identity_mode": "self",
        },
    )
    assert self_resp.status_code == 200, self_resp.text
    body = self_resp.json()
    assert body["failed_count"] == 1
    assert body["pending_count"] == 0
    assert body["identity_mode"] == "self"

    okabe_resp = isolated_client.get(
        "/api/memory/status",
        params={
            "session_id": session_id,
            "worldline": "steins_gate",
            "identity_mode": "okabe",
        },
    )
    assert okabe_resp.status_code == 200, okabe_resp.text
    okabe_body = okabe_resp.json()
    assert okabe_body["pending_count"] == 1
    assert okabe_body["failed_count"] == 0


def test_s3a_facts_scope_isolation(isolated_client: TestClient):
    """Self facts never appear under okabe list and vice versa."""
    import asyncio

    from app.services.memory_v11.repository import create_stable_fact

    session_id = _session("s3a-facts")

    async def _seed() -> None:
        await create_stable_fact(
            session_id=session_id,
            worldline="steins_gate",
            identity_mode="self",
            display_text="喜欢看动漫",
            semantic_json={"subject": "user", "relation": "likes", "object": "anime"},
        )
        await create_stable_fact(
            session_id=session_id,
            worldline="steins_gate",
            identity_mode="okabe",
            display_text="喜欢吃水果",
            semantic_json={"subject": "user", "relation": "likes", "object": "fruit"},
        )

    asyncio.run(_seed())

    self_resp = isolated_client.get(
        "/api/memory/facts",
        params={
            "session_id": session_id,
            "worldline": "steins_gate",
            "identity_mode": "self",
        },
    )
    assert self_resp.status_code == 200, self_resp.text
    self_body = self_resp.json()
    texts = [f["display_text"] for f in self_body["facts"]]
    assert "喜欢看动漫" in texts
    assert "喜欢吃水果" not in texts
    assert "shared" not in self_body
    assert self_body["scope"]["identity_mode"] == "self"

    okabe_resp = isolated_client.get(
        "/api/memory/facts",
        params={
            "session_id": session_id,
            "worldline": "steins_gate",
            "identity_mode": "okabe",
            "query": "水果",
        },
    )
    assert okabe_resp.status_code == 200, okabe_resp.text
    okabe_texts = [f["display_text"] for f in okabe_resp.json()["facts"]]
    assert okabe_texts == ["喜欢吃水果"]


def test_s3a_job_retry_scope_and_idempotence(isolated_client: TestClient):
    """Retry only for matching scope failed jobs; wrong session is 403."""
    import asyncio

    from app.services.memory_v11 import jobs as jobs_mod

    session_id = _session("s3a-retry")

    async def _seed_failed() -> str:
        job = await jobs_mod.enqueue_memory_job(
            session_id=session_id,
            worldline="steins_gate",
            conversation_id="c-retry",
            identity_mode="self",
            source_message_id=99,
            provider_id="deepseek",
            model_id="deepseek-chat",
        )
        claimed = await jobs_mod.claim_memory_job(
            job_id=job["job_id"], lease_owner="tester", worldline="steins_gate"
        )
        assert claimed is not None
        await jobs_mod.mark_memory_job_failed(
            job_id=claimed["job_id"],
            error_code="timeout",
            permanent=True,
            worldline="steins_gate",
            lease_owner="tester",
        )
        return job["job_id"]

    job_id = asyncio.run(_seed_failed())

    forbidden = isolated_client.post(
        f"/api/memory/jobs/{job_id}/retry",
        params={
            "session_id": "other-session",
            "worldline": "steins_gate",
            "identity_mode": "self",
        },
    )
    assert forbidden.status_code == 403, forbidden.text
    assert forbidden.json()["detail"]["code"] == "scope_mismatch"

    wrong_mode = isolated_client.post(
        f"/api/memory/jobs/{job_id}/retry",
        params={
            "session_id": session_id,
            "worldline": "steins_gate",
            "identity_mode": "okabe",
        },
    )
    assert wrong_mode.status_code == 403, wrong_mode.text

    ok = isolated_client.post(
        f"/api/memory/jobs/{job_id}/retry",
        params={
            "session_id": session_id,
            "worldline": "steins_gate",
            "identity_mode": "self",
        },
    )
    assert ok.status_code == 200, ok.text
    body = ok.json()
    assert body["job_id"] == job_id
    assert body["state"] == "pending"

    again = isolated_client.post(
        f"/api/memory/jobs/{job_id}/retry",
        params={
            "session_id": session_id,
            "worldline": "steins_gate",
            "identity_mode": "self",
        },
    )
    assert again.status_code == 200, again.text
    assert again.json()["state"] == "pending"

    missing = isolated_client.post(
        f"/api/memory/jobs/{uuid4()}/retry",
        params={
            "session_id": session_id,
            "worldline": "steins_gate",
            "identity_mode": "self",
        },
    )
    assert missing.status_code == 404, missing.text


def test_s3a_legacy_facts_path_without_identity_mode(isolated_client: TestClient):
    """Omitting identity_mode keeps the legacy Ledger shape for existing UI."""
    session_id = _session("s3a-legacy-shape")
    response = isolated_client.get(
        "/api/memory/facts",
        params={"session_id": session_id, "worldline": "steins_gate"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    # Legacy Gate 7A shape uses local/shared (and often okabe).
    assert "local" in body or "shared" in body, body


# ---------------------------------------------------------------------------
# Deferred contracts — not S3a stop-gate
# ---------------------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason="S6: legacy product routes removed at cutover")
def test_s0_legacy_product_routes_must_exit(client: TestClient):
    """D05/D09/D57: promote/reclassify/dismiss/shared-delete leave the route table."""
    registered = _registered_routes(client)
    still_registered = [
        (method, path)
        for method, path in LEGACY_ROUTE_TEMPLATES
        if (method, path) in registered
    ]
    assert not still_registered, (
        f"legacy Memory product routes still registered: {still_registered}"
    )


@pytest.mark.xfail(strict=True, reason="S4: conversation delete_derived_memory saga")
def test_s0_conversation_delete_derived_memory_true(client: TestClient):
    """D26: top-level delete_derived_memory must be boolean True (no missing-field skip)."""
    session_id = _session("v11-del-true")
    conversation_id = _create_conversation(client, session_id, "v11-delete-true")
    response = client.delete(
        f"/api/conversations/{conversation_id}",
        params={
            "session_id": session_id,
            "worldline": "steins_gate",
            "delete_derived_memory": "true",
        },
    )
    assert response.status_code == 200, (
        f"expected 200 with derived-memory body, got {response.status_code}: {response.text}"
    )
    body = response.json()
    assert "delete_derived_memory" in body, (
        f"top-level delete_derived_memory required, body={body}"
    )
    assert body["delete_derived_memory"] is True, (
        f"expected delete_derived_memory is True, got {body['delete_derived_memory']!r}"
    )


@pytest.mark.xfail(strict=True, reason="S4: conversation delete_derived_memory saga")
def test_s0_conversation_delete_derived_memory_false(client: TestClient):
    """D26: top-level delete_derived_memory must be boolean False (no missing-field skip)."""
    session_id = _session("v11-del-false")
    conversation_id = _create_conversation(client, session_id, "v11-delete-false")
    response = client.delete(
        f"/api/conversations/{conversation_id}",
        params={
            "session_id": session_id,
            "worldline": "steins_gate",
            "delete_derived_memory": "false",
        },
    )
    assert response.status_code == 200, (
        f"expected 200 with derived-memory body, got {response.status_code}: {response.text}"
    )
    body = response.json()
    assert "delete_derived_memory" in body, (
        f"top-level delete_derived_memory required, body={body}"
    )
    assert body["delete_derived_memory"] is False, (
        f"expected delete_derived_memory is False, got {body['delete_derived_memory']!r}"
    )
