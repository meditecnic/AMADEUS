"""MEMORY-V11 S0 red contracts: canonical REST (§8) and conversation delete (D26).

No pytest.skip on conversation fixtures — create real conversations.
Legacy shared delete path is POST /api/memory/shared-facts/{id}/delete.
"""
from __future__ import annotations

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


def test_s0_canonical_status_returns_job_counts(client: TestClient):
    """D24: status includes pending/processing/failed aggregates; no secrets."""
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
    """P05: GET /api/memory/facts returns scoped cards (items|facts), not local/shared/okabe ledger."""
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
