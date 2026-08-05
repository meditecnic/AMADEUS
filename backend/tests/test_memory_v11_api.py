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

import hashlib
import json
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


# ---------------------------------------------------------------------------
# MEMORY-V11-S3D-PROBE: remaining S3 REST closure contracts (plan §7 / §8).
#
# Permanent RED probes — these plan-required routes are NOT implemented yet.
# Tests only; no production code changes (evidence collection for the S3 gap
# audit). Missing registered surface today:
#   PATCH  /api/memory/facts/{fact_id}               (§7.1 user edit)
#   DELETE /api/memory/facts/{fact_id}               (§7.2 delete + tombstone)
#   PATCH  /api/memory/facts/{fact_id}/presentation  (§8.2 is_pinned/topic only)
#   GET    /api/memory/facts/{fact_id}/details       (§8.2 versions + provenance)
#   GET    /api/memory/topics / PATCH /api/memory/topics/{topic_id}    (§8.3)
#   GET    /api/memory/experiences + details + DELETE                   (§8.3)
#   GET    /api/memory/observations + .../ignore                        (§8.3)
#
# Coordinator decision points (NOT guessed here):
#   - response payload shapes beyond plan text ("versions + minimal provenance")
#   - exact stable codes for scope-mismatch / stale expected_version on the
#     new mutators (existing v11 routes use 403 scope_mismatch; P06 requires
#     the same stability, the concrete code is C's call)
# ---------------------------------------------------------------------------


def _seed_fact(
    *,
    session_id: str,
    identity_mode: str = "self",
    display_text: str,
    topic_id: str | None = None,
) -> str:
    import asyncio

    from app.services.memory_v11.repository import create_stable_fact

    async def _insert() -> str:
        row = await create_stable_fact(
            session_id=session_id,
            worldline="steins_gate",
            identity_mode=identity_mode,
            display_text=display_text,
            semantic_json={"subject": "user", "relation": "marker", "object": display_text[:16]},
            topic_id=topic_id,
        )
        return str(row["fact_id"])

    return asyncio.run(_insert())


def _seed_topic(session_id: str, identity_mode: str, label: str) -> str:
    import asyncio

    from app.db import get_db

    async def _insert() -> str:
        db = await get_db("steins_gate", "memory")
        try:
            topic_id = str(uuid4())
            now = "2026-08-01T00:00:00+00:00"
            await db.execute(
                """INSERT INTO memory_topics(
                       topic_id, session_id, identity_mode,
                       normalized_label, display_label, created_at, updated_at
                   ) VALUES(?,?,?,?,?,?,?)""",
                (topic_id, session_id, identity_mode, label, label, now, now),
            )
            await db.commit()
            return topic_id
        finally:
            await db.close()

    return asyncio.run(_insert())


def _seed_experience(
    *, session_id: str, identity_mode: str = "self", display_text: str
) -> str:
    """Seed one v11 experience with its observation row (source_state=present)."""
    import asyncio
    import json

    from app.db import get_db

    async def _insert() -> str:
        db = await get_db("steins_gate", "memory")
        try:
            observation_id = str(uuid4())
            experience_id = str(uuid4())
            now = "2026-08-01T00:00:00+00:00"
            semantic = {"subject": "user", "predicate": "event", "object": display_text[:24]}
            fingerprint = hashlib.sha256(
                json.dumps(
                    semantic, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                ).encode("utf-8")
            ).hexdigest()
            await db.execute(
                """INSERT INTO memory_observations(
                       observation_id, session_id, identity_mode, display_text,
                       semantic_json, semantic_fingerprint, evidence_kind, memory_class,
                       confidence, topic_label_proposal, status, extractor_version,
                       reanalysis_count, expires_at, created_at, updated_at
                   ) VALUES(?,?,?,?,?,?, 'direct_user', 'episodic', 0.9, NULL,
                            'attached', 'memory-v11-1', 0, NULL, ?, ?)""",
                (
                    observation_id,
                    session_id,
                    identity_mode,
                    display_text,
                    json.dumps(semantic, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                    fingerprint,
                    now,
                    now,
                ),
            )
            await db.execute(
                """INSERT INTO memory_observation_sources(
                       observation_id, source_message_id, conversation_id, source_role,
                       source_fingerprint, source_created_at, source_state, excerpt
                   ) VALUES(?,?,?, 'user', ?, ?, 'present', NULL)""",
                (
                    observation_id,
                    int(uuid4().int % 10**9),
                    "conv-s3d-exp",
                    f"fp-{observation_id[:8]}",
                    now,
                ),
            )
            await db.execute(
                """INSERT INTO experiences(
                       experience_id, session_id, conversation_id, identity_mode,
                       observation_id, display_text, semantic_json, semantic_fingerprint,
                       confidence, status, expires_at, created_at, updated_at, deleted_at
                   ) VALUES(?,?,?,?,?,?,?,?,0.9,'active',NULL,?,?,NULL)""",
                (
                    experience_id,
                    session_id,
                    "conv-s3d-exp",
                    identity_mode,
                    observation_id,
                    display_text,
                    json.dumps(semantic, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                    fingerprint,
                    now,
                    now,
                ),
            )
            await db.commit()
            return experience_id
        finally:
            await db.close()

    return asyncio.run(_insert())


def _seed_candidate_observation(
    *, session_id: str, identity_mode: str = "self", display_text: str
) -> str:
    """Seed one unresolved candidate observation (status='candidate', reanalysis_count=0)."""
    import asyncio

    from app.db import get_db

    async def _insert() -> str:
        db = await get_db("steins_gate", "memory")
        try:
            observation_id = str(uuid4())
            now = "2026-08-01T00:00:00+00:00"
            await db.execute(
                """INSERT INTO memory_observations(
                       observation_id, session_id, identity_mode, display_text,
                       semantic_json, semantic_fingerprint, evidence_kind, memory_class,
                       confidence, topic_label_proposal, status, extractor_version,
                       reanalysis_count, expires_at, created_at, updated_at
                   ) VALUES(?,?,?,?,?,?, 'direct_user', 'stable_candidate', 0.4, NULL,
                            'candidate', 'memory-v11-1', 0, NULL, ?, ?)""",
                (
                    observation_id,
                    session_id,
                    identity_mode,
                    display_text,
                    "{}",
                    f"fp-{observation_id[:8]}",
                    now,
                    now,
                ),
            )
            await db.commit()
            return observation_id
        finally:
            await db.close()

    return asyncio.run(_insert())


def _obs_status(session_id: str, observation_id: str) -> tuple[str, int]:
    import asyncio

    from app.db import get_db

    async def _read() -> tuple[str, int]:
        db = await get_db("steins_gate", "memory")
        try:
            row = await (
                await db.execute(
                    """SELECT status, reanalysis_count FROM memory_observations
                        WHERE observation_id=? AND session_id=?""",
                    (observation_id, session_id),
                )
            ).fetchone()
            assert row is not None
            return str(row["status"]), int(row["reanalysis_count"])
        finally:
            await db.close()

    return asyncio.run(_read())


# -- A. canonical stable-fact REST surface (plan §7 / §8.2) ------------------


def test_s3d_probe_fact_patch_user_edit_creates_version(isolated_client: TestClient):
    """§7.1: PATCH /api/memory/facts/{fact_id} — full scope + expected_version + new text.

    Success creates change_kind=user_edit version; the active card text changes;
    wrong identity must not mutate the target (P06 scope safety).
    """
    session_id = _session("s3d-patch")
    fact_id = _seed_fact(session_id=session_id, display_text="喜欢咖啡")
    response = isolated_client.patch(
        f"/api/memory/facts/{fact_id}",
        params={
            "session_id": session_id,
            "worldline": "steins_gate",
            "identity_mode": "self",
        },
        json={"display_text": "喜欢黑咖啡", "expected_version": 1},
    )
    assert response.status_code == 200, (
        f"PATCH fact user-edit route missing or broken: {response.status_code} "
        f"{response.text} (plan §7.1)"
    )
    listed = isolated_client.get(
        "/api/memory/facts",
        params={
            "session_id": session_id,
            "worldline": "steins_gate",
            "identity_mode": "self",
        },
    )
    assert listed.status_code == 200, listed.text
    texts = [f["display_text"] for f in listed.json()["facts"]]
    assert "喜欢黑咖啡" in texts
    assert "喜欢咖啡" not in texts

    cross = isolated_client.patch(
        f"/api/memory/facts/{fact_id}",
        params={
            "session_id": session_id,
            "worldline": "steins_gate",
            "identity_mode": "okabe",
        },
        json={"display_text": "被篡改", "expected_version": 2},
    )
    assert cross.status_code != 200, "okabe scope must not edit a self fact (P06)"


def test_s3d_probe_fact_delete_scoped_tombstone_idempotent(
    isolated_client: TestClient,
):
    """§7.2: DELETE /api/memory/facts/{fact_id} — scope + expected_version; tombstone.

    The fact disappears from the active list; repeated DELETE stays idempotent
    (not a 500); the fact must never be revived by later retrieval.
    """
    session_id = _session("s3d-del")
    fact_id = _seed_fact(session_id=session_id, display_text="喜欢茶")
    response = isolated_client.delete(
        f"/api/memory/facts/{fact_id}",
        params={
            "session_id": session_id,
            "worldline": "steins_gate",
            "identity_mode": "self",
            "expected_version": 1,
        },
    )
    assert response.status_code == 200, (
        f"DELETE fact route missing or broken: {response.status_code} "
        f"{response.text} (plan §7.2)"
    )
    listed = isolated_client.get(
        "/api/memory/facts",
        params={
            "session_id": session_id,
            "worldline": "steins_gate",
            "identity_mode": "self",
        },
    )
    assert listed.status_code == 200, listed.text
    assert not any(f["fact_id"] == fact_id for f in listed.json()["facts"]), (
        "deleted fact must not remain active"
    )
    again = isolated_client.delete(
        f"/api/memory/facts/{fact_id}",
        params={
            "session_id": session_id,
            "worldline": "steins_gate",
            "identity_mode": "self",
            "expected_version": 1,
        },
    )
    assert again.status_code == 200, "repeated DELETE must be idempotent (§7.2)"


def test_s3d_probe_fact_presentation_only_presentation_fields(
    isolated_client: TestClient,
):
    """§8.2/§7.3: PATCH .../presentation — is_pinned/topic_id only, local, no LLM.

    A presentation request must never change display_text/semantics, and a
    body carrying display_text must be rejected (P06).
    """
    session_id = _session("s3d-present")
    topic_id = _seed_topic(session_id, "self", "动漫")
    fact_id = _seed_fact(
        session_id=session_id, display_text="喜欢看动漫", topic_id=topic_id
    )
    response = isolated_client.patch(
        f"/api/memory/facts/{fact_id}/presentation",
        params={
            "session_id": session_id,
            "worldline": "steins_gate",
            "identity_mode": "self",
        },
        json={"is_pinned": True, "topic_id": topic_id},
    )
    assert response.status_code == 200, (
        f"presentation route missing or broken: {response.status_code} "
        f"{response.text} (plan §8.2)"
    )
    listed = isolated_client.get(
        "/api/memory/facts",
        params={
            "session_id": session_id,
            "worldline": "steins_gate",
            "identity_mode": "self",
        },
    )
    assert listed.status_code == 200, listed.text
    card = next(f for f in listed.json()["facts"] if f["fact_id"] == fact_id)
    assert card["is_pinned"] is True
    assert card["display_text"] == "喜欢看动漫"

    semantic = isolated_client.patch(
        f"/api/memory/facts/{fact_id}/presentation",
        params={
            "session_id": session_id,
            "worldline": "steins_gate",
            "identity_mode": "self",
        },
        json={"display_text": "偷偷改文本"},
    )
    assert semantic.status_code not in (200, 201), (
        "presentation must reject non-presentation fields (§8.2)"
    )


def test_s3d_probe_fact_details_returns_version_history(
    isolated_client: TestClient,
):
    """§8.2: GET /api/memory/facts/{fact_id}/details — versions + minimal provenance.

    Response shape beyond "versions are present" is a Coordinator decision
    point; the probe only requires version data to be returned.
    """
    session_id = _session("s3d-details")
    fact_id = _seed_fact(session_id=session_id, display_text="喜欢动漫")
    response = isolated_client.get(
        f"/api/memory/facts/{fact_id}/details",
        params={
            "session_id": session_id,
            "worldline": "steins_gate",
            "identity_mode": "self",
        },
    )
    assert response.status_code == 200, (
        f"fact details route missing or broken: {response.status_code} "
        f"{response.text} (plan §8.2)"
    )
    assert "version" in str(response.json()).lower(), (
        "details must include version history data"
    )
    cross = isolated_client.get(
        f"/api/memory/facts/{fact_id}/details",
        params={
            "session_id": session_id,
            "worldline": "steins_gate",
            "identity_mode": "okabe",
        },
    )
    assert cross.status_code != 200, "details must be scope-strict (P06)"


# -- B. topic / experience / observation REST (plan §8.3) --------------------


def test_s3d_probe_topics_list_scoped(isolated_client: TestClient):
    """§8.3: GET /api/memory/topics — scoped local list; no cross identity."""
    session_id = _session("s3d-topics")
    _seed_topic(session_id, "self", "动漫")
    _seed_topic(session_id, "okabe", "冈部话题")
    response = isolated_client.get(
        "/api/memory/topics",
        params={
            "session_id": session_id,
            "worldline": "steins_gate",
            "identity_mode": "self",
        },
    )
    assert response.status_code == 200, (
        f"topics list route missing or broken: {response.status_code} "
        f"{response.text} (plan §8.3)"
    )
    body = response.json()
    assert "动漫" in str(body)
    assert "冈部话题" not in str(body), "okabe topic must not leak into self scope"


def test_s3d_probe_topic_rename_presentation_only(isolated_client: TestClient):
    """§8.3: PATCH /api/memory/topics/{topic_id} — rename is presentation-only.

    Facts attached to the topic keep their topic_id; semantic/scope unchanged.
    """
    session_id = _session("s3d-topic-rename")
    topic_id = _seed_topic(session_id, "self", "动漫")
    _seed_fact(session_id=session_id, display_text="喜欢看动漫", topic_id=topic_id)
    response = isolated_client.patch(
        f"/api/memory/topics/{topic_id}",
        params={
            "session_id": session_id,
            "worldline": "steins_gate",
            "identity_mode": "self",
        },
        json={"display_label": "动画"},
    )
    assert response.status_code == 200, (
        f"topic rename route missing or broken: {response.status_code} "
        f"{response.text} (plan §8.3)"
    )
    listed = isolated_client.get(
        "/api/memory/facts",
        params={
            "session_id": session_id,
            "worldline": "steins_gate",
            "identity_mode": "self",
            "topic_id": topic_id,
        },
    )
    assert listed.status_code == 200, listed.text
    assert any(f["topic_id"] == topic_id for f in listed.json()["facts"]), (
        "rename must keep facts attached to the same topic_id (§4.4)"
    )


def test_s3d_probe_experiences_list_scoped(isolated_client: TestClient):
    """§8.3: GET /api/memory/experiences — same-scope active experiences only."""
    session_id = _session("s3d-exp-list")
    marker_self = f"EXP_SELF_{uuid4().hex[:8]}"
    marker_okabe = f"EXP_OKABE_{uuid4().hex[:8]}"
    _seed_experience(session_id=session_id, display_text=f"{marker_self} 去了秋叶原")
    _seed_experience(
        session_id=session_id, identity_mode="okabe", display_text=f"{marker_okabe} 买了一些装备"
    )
    response = isolated_client.get(
        "/api/memory/experiences",
        params={
            "session_id": session_id,
            "worldline": "steins_gate",
            "identity_mode": "self",
        },
    )
    assert response.status_code == 200, (
        f"experiences list route missing or broken: {response.status_code} "
        f"{response.text} (plan §8.3)"
    )
    body = response.json()
    assert marker_self in str(body)
    assert marker_okabe not in str(body), "cross-identity experience must not appear"


def test_s3d_probe_experience_details(isolated_client: TestClient):
    """§8.3: GET /api/memory/experiences/{id}/details — scoped detail."""
    session_id = _session("s3d-exp-details")
    experience_id = _seed_experience(session_id=session_id, display_text="本周去了图书馆")
    response = isolated_client.get(
        f"/api/memory/experiences/{experience_id}/details",
        params={
            "session_id": session_id,
            "worldline": "steins_gate",
            "identity_mode": "self",
        },
    )
    assert response.status_code == 200, (
        f"experience details route missing or broken: {response.status_code} "
        f"{response.text} (plan §8.3)"
    )


def test_s3d_probe_experience_delete_scoped(isolated_client: TestClient):
    """§8.3: DELETE /api/memory/experiences/{id} — scoped removal from active list."""
    session_id = _session("s3d-exp-del")
    experience_id = _seed_experience(session_id=session_id, display_text="本周去了秋叶原")
    response = isolated_client.delete(
        f"/api/memory/experiences/{experience_id}",
        params={
            "session_id": session_id,
            "worldline": "steins_gate",
            "identity_mode": "self",
        },
    )
    assert response.status_code == 200, (
        f"experience delete route missing or broken: {response.status_code} "
        f"{response.text} (plan §8.3)"
    )
    listed = isolated_client.get(
        "/api/memory/experiences",
        params={
            "session_id": session_id,
            "worldline": "steins_gate",
            "identity_mode": "self",
        },
    )
    assert listed.status_code == 200, listed.text
    assert experience_id not in str(listed.json())


def test_s3d_probe_observations_list_scoped(isolated_client: TestClient):
    """§8.3: GET /api/memory/observations — scoped diagnostics; no cross identity."""
    session_id = _session("s3d-obs")
    marker_self = f"OBS_SELF_{uuid4().hex[:8]}"
    marker_okabe = f"OBS_OKABE_{uuid4().hex[:8]}"
    _seed_candidate_observation(session_id=session_id, display_text=marker_self)
    _seed_candidate_observation(
        session_id=session_id, identity_mode="okabe", display_text=marker_okabe
    )
    response = isolated_client.get(
        "/api/memory/observations",
        params={
            "session_id": session_id,
            "worldline": "steins_gate",
            "identity_mode": "self",
        },
    )
    assert response.status_code == 200, (
        f"observations list route missing or broken: {response.status_code} "
        f"{response.text} (plan §8.3)"
    )
    body = response.json()
    assert marker_self in str(body)
    assert marker_okabe not in str(body), "cross-identity observation must not appear"


def test_s3d_probe_observation_ignore_blocks_not_promotes(
    isolated_client: TestClient,
):
    """§8.3: POST /api/memory/observations/{id}/ignore — blocks reconciliation only.

    ignore is not promote and not delete: the observation row survives.
    """
    session_id = _session("s3d-obs-ignore")
    observation_id = _seed_candidate_observation(
        session_id=session_id, display_text="低置信候选"
    )
    response = isolated_client.post(
        f"/api/memory/observations/{observation_id}/ignore",
        params={
            "session_id": session_id,
            "worldline": "steins_gate",
            "identity_mode": "self",
        },
    )
    assert response.status_code == 200, (
        f"observation ignore route missing or broken: {response.status_code} "
        f"{response.text} (plan §8.3)"
    )
    status, _ = _obs_status(session_id, observation_id)
    assert status == "ignored", "ignore must mark the observation, not delete it"
