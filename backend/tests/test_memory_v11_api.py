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

from app.services.memory import EmbeddingAdapter


def _unit_vector(seed: str) -> list[float]:
    values: list[float] = []
    for block in range(12):
        digest = hashlib.sha256(f"{seed}:{block}".encode("utf-8")).digest()
        values.extend(int(byte) - 127.5 for byte in digest)
    norm = sum(v * v for v in values) ** 0.5
    return [v / norm for v in values]


class _ApiDeterministicEmbedder(EmbeddingAdapter):
    """Offline EmbeddingAdapter stub — no SentenceTransformer / network."""

    def __init__(self, *, model_name: str = "test-deterministic-384", fail_always: bool = False):
        super().__init__(model_name=model_name, dimensions=384)
        self.fail_always = fail_always
        self.encode_passage_calls = 0
        self.encode_query_calls = 0

    async def encode_passage(self, text: str) -> list[float]:
        self.encode_passage_calls += 1
        if self.fail_always:
            raise RuntimeError("forced encode_passage failure")
        return _unit_vector(f"passage:{text}")

    async def encode_query(self, text: str) -> list[float]:
        self.encode_query_calls += 1
        return _unit_vector(f"query:{text}")


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
def client(isolated_client: TestClient) -> TestClient:
    # Legacy compatibility tests can create/delete conversations too. Never
    # let this alias start the application against the user's data directory.
    return isolated_client


@pytest.fixture
def isolated_client(tmp_path, monkeypatch, isolated_provider_credentials):
    """TestClient with isolated AMADEUS_DATA_DIR (v11 schema + jobs).

    memory_service.embedder is stubbed with a deterministic offline embedder
    because the S3D-1 edit route follows the S3B-2 vector contract (passage
    encoding) and must never load a real SentenceTransformer model in tests.
    """
    from app.db import reset_initialization_cache
    from app.main import app
    from app.services.memory import memory_service

    monkeypatch.delenv("AMADEUS_DB_PATH", raising=False)
    monkeypatch.setenv("AMADEUS_DATA_DIR", str(tmp_path))
    reset_initialization_cache()
    monkeypatch.setattr(memory_service, "embedder", _ApiDeterministicEmbedder())
    with patch(
        "app.main.sidecar_supervisor.ensure_available",
        new=AsyncMock(return_value=SimpleNamespace(url=None)),
    ), patch(
        "app.main.sidecar_supervisor.close",
        new=AsyncMock(),
    ):
        with TestClient(app, base_url="http://localhost", headers={"host": "localhost", "origin": "http://localhost:1420"}) as client:
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
        from app.db import get_db
        db = await get_db('steins_gate', 'memory')
        try:
            await db.execute('UPDATE memory_ingest_jobs SET attempt_count=3 WHERE job_id=?', (job['job_id'],))
            await db.commit()
        finally:
            await db.close()
        await jobs_mod.mark_memory_job_failed(
            job_id=claimed["job_id"],
            error_code="timeout",
            permanent=False,
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


def test_s3f2c_explicit_scope_required_legacy_defaults_preserved(
    isolated_client: TestClient,
):
    """Shared status/facts routes distinguish explicit v11 scope from legacy calls."""
    routes = ("/api/memory/status", "/api/memory/facts")
    valid_session = _session("s3f2c-scope")
    cases = (
        ({"worldline": "steins_gate", "identity_mode": "self"}, "empty_session_id"),
        (
            {"session_id": "", "worldline": "steins_gate", "identity_mode": "self"},
            "empty_session_id",
        ),
        (
            {"session_id": "   ", "worldline": "steins_gate", "identity_mode": "self"},
            "empty_session_id",
        ),
        ({"session_id": valid_session, "identity_mode": "self"}, "missing_worldline"),
        (
            {"session_id": valid_session, "worldline": "", "identity_mode": "self"},
            "missing_worldline",
        ),
        (
            {"session_id": valid_session, "worldline": "   ", "identity_mode": "self"},
            "missing_worldline",
        ),
        (
            {
                "session_id": valid_session,
                "worldline": "steins_gate",
                "identity_mode": "",
            },
            "missing_identity_mode",
        ),
        (
            {
                "session_id": valid_session,
                "worldline": "bogus",
                "identity_mode": "self",
            },
            "invalid_worldline",
        ),
        (
            {
                "session_id": valid_session,
                "worldline": "steins_gate",
                "identity_mode": "bogus",
            },
            "invalid_identity_mode",
        ),
    )
    for route in routes:
        for params, expected_code in cases:
            response = isolated_client.get(route, params=params)
            assert response.status_code == 422, (route, params, response.text)
            assert response.json()["detail"]["code"] == expected_code, (route, params)

    legacy_status = isolated_client.get("/api/memory/status")
    assert legacy_status.status_code == 200, legacy_status.text
    status_body = legacy_status.json()
    assert status_body["session_id"] == "default"
    assert status_body["worldline"] == "steins_gate"
    assert "episodic_count" in status_body
    assert "pending_count" not in status_body

    legacy_facts = isolated_client.get("/api/memory/facts")
    assert legacy_facts.status_code == 200, legacy_facts.text
    facts_body = legacy_facts.json()
    assert "local" in facts_body or "shared" in facts_body, facts_body
    assert "facts" not in facts_body
    assert "pagination" not in facts_body


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
    *,
    session_id: str,
    identity_mode: str = "self",
    display_text: str,
    status: str = "active",
    expires_at: str | None = None,
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
                   ) VALUES(?,?,?,?,?,?,?,?,0.9,?,?,?,?,NULL)""",
                (
                    experience_id,
                    session_id,
                    "conv-s3d-exp",
                    identity_mode,
                    observation_id,
                    display_text,
                    json.dumps(semantic, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                    fingerprint,
                    status,
                    expires_at,
                    now,
                    now,
                ),
            )
            await db.commit()
            return experience_id
        finally:
            await db.close()

    return asyncio.run(_insert())


def _seed_observation(
    *,
    session_id: str,
    identity_mode: str = "self",
    display_text: str | None,
    status: str,
    confidence: float = 0.5,
) -> str:
    """Seed one memory_observations row in a given status (diagnostics tests)."""
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
                   ) VALUES(?,?,?,?,?,?, 'direct_user', 'stable_candidate', ?, NULL,
                            ?, 'memory-v11-1', 0, NULL, ?, ?)""",
                (
                    observation_id,
                    session_id,
                    identity_mode,
                    display_text,
                    "{}",
                    f"fp-{observation_id[:8]}",
                    confidence,
                    status,
                    now,
                    now,
                ),
            )
            await db.commit()
            return observation_id
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
    """§8.3: GET /api/memory/topics — scoped local list; no cross identity.

    Zero-active-count topics are hidden (product rule S3D-2), so the self
    topic carries one attached active fact.
    """
    session_id = _session("s3d-topics")
    topic_id = _seed_topic(session_id, "self", "动漫")
    _seed_fact(session_id=session_id, display_text="喜欢看动漫", topic_id=topic_id)
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


# ---------------------------------------------------------------------------
# MEMORY-V11-S3D-1: stable-fact lifecycle REST (plan §7.1 / §7.2 / §8.2).
# ---------------------------------------------------------------------------


def _seed_fact_with_vector(
    *, session_id: str, display_text: str, identity_mode: str = "self"
) -> str:
    """create_stable_fact + explicit v1 embedding row (offline deterministic)."""
    import asyncio
    import struct

    from app.db import get_db
    from app.services.memory import memory_service
    from app.services.memory_v11.repository import create_stable_fact

    async def _seed() -> str:
        row = await create_stable_fact(
            session_id=session_id,
            worldline="steins_gate",
            identity_mode=identity_mode,
            display_text=display_text,
            semantic_json={
                "subject": "user",
                "relation": "marker",
                "object": display_text[:16],
            },
        )
        fact_id = str(row["fact_id"])
        embedder = memory_service.embedder
        vector = await embedder.encode_passage(display_text)
        db = await get_db("steins_gate", "memory")
        try:
            await db.execute(
                """INSERT INTO stable_fact_version_embeddings(
                       fact_id, version_no, model, dimensions, vector
                   ) VALUES(?,1,?,?,?)""",
                (
                    fact_id,
                    embedder.model_name,
                    embedder.dimensions,
                    struct.pack(f"{embedder.dimensions}f", *vector),
                ),
            )
            await db.commit()
        finally:
            await db.close()
        return fact_id

    return asyncio.run(_seed())


def _seed_fact_via_reconciler(
    *,
    session_id: str,
    display_text: str,
    source_message_id: int,
    excerpt: str | None = None,
    topic_label: str | None = None,
    identity_mode: str = "self",
    conversation_id: str | None = None,
    pipeline_version: str | None = None,
) -> tuple[str, str]:
    """Public reconciler CREATE → (fact_id, observation_id) with vector + evidence."""
    import asyncio

    from app.db import get_db
    from app.services.memory_v11.reconciler import apply_validated_memory_operations

    async def _seed() -> tuple[str, str]:
        obs = {
            "observation_ref": "o1",
            "source_message_ids": [source_message_id],
            "display_text": display_text,
            "semantic": {
                "subject": "user",
                "predicate": "likes",
                "object": {"name": display_text[:12]},
            },
            "evidence_kind": "direct_user",
            "memory_class": "stable_candidate",
            "confidence": 0.96,
            "topic_label": topic_label,
            "expires_at": None,
        }
        if topic_label is None:
            obs.pop("topic_label")
        result = await apply_validated_memory_operations(
            session_id=session_id,
            worldline="steins_gate",
            identity_mode=identity_mode,
            observations=[obs],
            operations=[
                {
                    "op": "CREATE",
                    "observation_ref": "o1",
                    "target_fact_id": None,
                    "expected_version": None,
                    "fact_text": display_text,
                    "reason_code": "direct_durable_statement",
                }
            ],
            source_evidence=[
                {
                    "source_message_id": source_message_id,
                    "conversation_id": conversation_id or f"conv-s3d1-{source_message_id}",
                    "source_role": "user",
                    "source_fingerprint": f"fp-s3d1-{source_message_id}",
                    "source_created_at": "2026-08-01T00:00:00+00:00",
                    "excerpt": excerpt if excerpt is not None else display_text,
                }
            ],
            candidate_allowlist=[],
            processing_receipt={
                'conversation_id': conversation_id or f'conv-s3d1-{source_message_id}',
                'source_message_id': source_message_id,
                'pipeline_version': pipeline_version,
            } if pipeline_version else None,
        )
        fact_id = str(result["created_facts"][0]["fact_id"])
        db = await get_db("steins_gate", "memory")
        try:
            row = await (
                await db.execute(
                    """SELECT observation_id FROM stable_fact_evidence
                        WHERE fact_id=? LIMIT 1""",
                    (fact_id,),
                )
            ).fetchone()
            assert row is not None
            observation_id = str(row["observation_id"])
        finally:
            await db.close()
        return fact_id, observation_id

    return asyncio.run(_seed())


def _reconciler_apply(
    *,
    session_id: str,
    observations: list[dict],
    operations: list[dict],
    source_message_id: int | None = None,
    source_message_ids: list[int] | None = None,
    identity_mode: str = "self",
    candidate_allowlist: tuple = (),
) -> dict:
    """Generic direct reconciler apply via asyncio.run (offline embedder stub)."""
    import asyncio

    from app.services.memory_v11.reconciler import apply_validated_memory_operations

    source_ids = (
        list(source_message_ids)
        if source_message_ids is not None
        else ([source_message_id] if source_message_id is not None else [])
    )

    async def _run() -> dict:
        return await apply_validated_memory_operations(
            session_id=session_id,
            worldline="steins_gate",
            identity_mode=identity_mode,
            observations=observations,
            operations=operations,
            source_evidence=[
                {
                    "source_message_id": mid,
                    "conversation_id": f"conv-s3d2-{mid}",
                    "source_role": "user",
                    "source_fingerprint": f"fp-s3d2-{mid}",
                    "source_created_at": "2026-08-01T00:00:00+00:00",
                    "excerpt": "s3d2 excerpt",
                }
                for mid in source_ids
            ],
            candidate_allowlist=list(candidate_allowlist),
        )

    return asyncio.run(_run())


def _s3d3_episodic_obs(
    *,
    ref: str,
    display_text: str,
    source_message_ids: list[int],
) -> dict:
    """Episodic observation for real reconciler CREATE → experience."""
    return {
        "observation_ref": ref,
        "source_message_ids": source_message_ids,
        "display_text": display_text,
        "semantic": {"subject": "user", "predicate": "event", "object": display_text[:12]},
        "evidence_kind": "direct_user",
        "memory_class": "episodic",
        "confidence": 0.9,
        "topic_label": "事件",
        "expires_at": None,
    }


def _s3d2_obs(
    *,
    ref: str,
    display_text: str,
    source_message_id: int,
    topic_label: str | None = None,
    confidence: float = 0.96,
) -> dict:
    obs = {
        "observation_ref": ref,
        "source_message_ids": [source_message_id],
        "display_text": display_text,
        "semantic": {"subject": "user", "predicate": "marker", "object": display_text[:12]},
        "evidence_kind": "direct_user",
        "memory_class": "stable_candidate",
        "confidence": confidence,
        "topic_label": topic_label,
        "expires_at": None,
    }
    if topic_label is None:
        obs.pop("topic_label")
    return obs


def _db_rows(sql: str, params: tuple = ()) -> list[dict]:
    import asyncio

    from app.db import get_db

    async def _read() -> list[dict]:
        db = await get_db("steins_gate", "memory")
        try:
            rows = await (await db.execute(sql, params)).fetchall()
            return [dict(row) for row in rows]
        finally:
            await db.close()

    return asyncio.run(_read())


def _db_execute(sql: str, params: tuple = ()) -> None:
    import asyncio

    from app.db import get_db

    async def _run() -> None:
        db = await get_db("steins_gate", "memory")
        try:
            await db.execute(sql, params)
            await db.commit()
        finally:
            await db.close()

    asyncio.run(_run())


def _facts_scope(session_id: str) -> dict[str, str]:
    return {
        "session_id": session_id,
        "worldline": "steins_gate",
        "identity_mode": "self",
    }


# -- A. PATCH user edit -----------------------------------------------------


def test_s3d1_edit_version_lineage_old_version_in_details(
    isolated_client: TestClient,
):
    """§7.1: user_edit creates exactly one new version; old stays in details."""
    import asyncio

    from app.services.memory_v11.repository import create_stable_fact

    session_id = _session("s3d1-edit-lineage")

    async def _seed() -> str:
        row = await create_stable_fact(
            session_id=session_id,
            worldline="steins_gate",
            identity_mode="self",
            display_text="喜欢咖啡",
            semantic_json={"subject": "user", "relation": "likes", "object": "coffee"},
        )
        return str(row["fact_id"])

    fact_id = asyncio.run(_seed())
    response = isolated_client.patch(
        f"/api/memory/facts/{fact_id}",
        params=_facts_scope(session_id),
        json={"display_text": "喜欢黑咖啡", "expected_version": 1},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["fact_id"] == fact_id
    assert body["version_no"] == 2
    assert body["active_version"] == 2
    assert body["change_kind"] == "user_edit"

    listed = isolated_client.get("/api/memory/facts", params=_facts_scope(session_id))
    assert listed.status_code == 200, listed.text
    facts = listed.json()["facts"]
    assert len(facts) == 1, "edit must not create a new fact_id"
    assert facts[0]["display_text"] == "喜欢黑咖啡"
    assert facts[0]["fact_id"] == fact_id

    details = isolated_client.get(
        f"/api/memory/facts/{fact_id}/details", params=_facts_scope(session_id)
    )
    assert details.status_code == 200, details.text
    d = details.json()
    assert d["fact"]["fact_id"] == fact_id
    assert d["fact"]["active_version"] == 2
    assert d["fact"]["display_text"] == "喜欢黑咖啡"
    versions = d["versions"]
    assert [v["version_no"] for v in versions] == [1, 2]
    assert versions[0]["is_active"] is False
    assert versions[0]["invalid_at"] is not None
    assert versions[0]["display_text"] == "喜欢咖啡"
    assert versions[1]["is_active"] is True
    assert versions[1]["invalid_at"] is None
    assert versions[1]["previous_version"] == 1
    assert versions[1]["change_kind"] == "user_edit"
    assert versions[1]["display_text"] == "喜欢黑咖啡"


def test_s3d1_edit_wrong_identity_no_mutation(isolated_client: TestClient):
    session_id = _session("s3d1-edit-xident")
    fact_id = _seed_fact(session_id=session_id, display_text="喜欢茶")
    cross = isolated_client.patch(
        f"/api/memory/facts/{fact_id}",
        params={
            "session_id": session_id,
            "worldline": "steins_gate",
            "identity_mode": "okabe",
        },
        json={"display_text": "被篡改", "expected_version": 1},
    )
    assert cross.status_code == 404, cross.text
    assert cross.json()["detail"]["code"] == "fact_not_found"
    versions = _db_rows(
        """SELECT version_no, display_text FROM stable_fact_versions
            WHERE fact_id=?""",
        (fact_id,),
    )
    assert len(versions) == 1
    assert versions[0]["display_text"] == "喜欢茶"


def test_s3d1_edit_stale_expected_version_409_no_mutation(
    isolated_client: TestClient,
):
    session_id = _session("s3d1-edit-stale")
    fact_id = _seed_fact(session_id=session_id, display_text="喜欢咖啡")
    stale = isolated_client.patch(
        f"/api/memory/facts/{fact_id}",
        params=_facts_scope(session_id),
        json={"display_text": "喜欢白咖啡", "expected_version": 99},
    )
    assert stale.status_code == 409, stale.text
    assert stale.json()["detail"]["code"] == "expected_version_mismatch"
    rows = _db_rows(
        "SELECT version_no, invalid_at FROM stable_fact_versions WHERE fact_id=?",
        (fact_id,),
    )
    assert len(rows) == 1 and rows[0]["invalid_at"] is None


def test_s3d1_edit_empty_text_422_and_invalid_version(
    isolated_client: TestClient,
):
    session_id = _session("s3d1-edit-empty")
    fact_id = _seed_fact(session_id=session_id, display_text="喜欢咖啡")
    empty = isolated_client.patch(
        f"/api/memory/facts/{fact_id}",
        params=_facts_scope(session_id),
        json={"display_text": "   ", "expected_version": 1},
    )
    assert empty.status_code == 422, empty.text
    assert empty.json()["detail"]["code"] == "empty_display_text"
    bad_version = isolated_client.patch(
        f"/api/memory/facts/{fact_id}",
        params=_facts_scope(session_id),
        json={"display_text": "喜欢黑咖啡", "expected_version": -3},
    )
    assert bad_version.status_code == 422, bad_version.text
    assert bad_version.json()["detail"]["code"] == "invalid_expected_version"
    extra = isolated_client.patch(
        f"/api/memory/facts/{fact_id}",
        params=_facts_scope(session_id),
        json={"display_text": "喜欢黑咖啡", "expected_version": 1, "topic_id": "x"},
    )
    assert extra.status_code == 422, "extra fields must be forbidden"
    assert extra.json()["detail"]["code"] == "extra_fields_forbidden"


def test_s3d1_edit_vector_swap(isolated_client: TestClient):
    """§7.1/S3B-2: old vector removed, new vector present, both in one txn."""
    import struct

    from app.services.memory import memory_service

    session_id = _session("s3d1-edit-vec")
    text_v1 = "喜欢咖啡"
    text_v2 = "喜欢黑咖啡"
    fact_id = _seed_fact_with_vector(session_id=session_id, display_text=text_v1)
    embedder = memory_service.embedder
    assert embedder.encode_passage_calls == 1  # seeding only

    response = isolated_client.patch(
        f"/api/memory/facts/{fact_id}",
        params=_facts_scope(session_id),
        json={"display_text": text_v2, "expected_version": 1},
    )
    assert response.status_code == 200, response.text
    assert embedder.encode_passage_calls == 2, "exactly one new passage encode"

    rows = _db_rows(
        """SELECT version_no, model, dimensions, vector
             FROM stable_fact_version_embeddings WHERE fact_id=?""",
        (fact_id,),
    )
    assert [r["version_no"] for r in rows] == [2]
    expected_v2 = struct.pack(
        f"{embedder.dimensions}f", *_unit_vector(f"passage:{text_v2}")
    )
    assert rows[0]["vector"] == expected_v2
    assert rows[0]["model"] == embedder.model_name


def test_s3d1_edit_embedding_failure_leaves_everything(
    isolated_client: TestClient, monkeypatch
):
    """Embedding failure: 500 (stable, not user validation); fact fully intact."""
    from app.services.memory import memory_service

    session_id = _session("s3d1-edit-embfail")
    fact_id = _seed_fact_with_vector(session_id=session_id, display_text="喜欢咖啡")
    monkeypatch.setattr(memory_service, "embedder", _ApiDeterministicEmbedder(fail_always=True))
    response = isolated_client.patch(
        f"/api/memory/facts/{fact_id}",
        params=_facts_scope(session_id),
        json={"display_text": "喜欢黑咖啡", "expected_version": 1},
    )
    assert response.status_code == 500, response.text
    assert response.json()["detail"]["code"] == "embedding_failed"
    facts = _db_rows(
        "SELECT state, active_version FROM stable_facts WHERE fact_id=?", (fact_id,)
    )
    assert facts[0]["state"] == "active"
    assert facts[0]["active_version"] == 1
    versions = _db_rows(
        """SELECT version_no, invalid_at FROM stable_fact_versions WHERE fact_id=?""",
        (fact_id,),
    )
    assert len(versions) == 1 and versions[0]["invalid_at"] is None
    vectors = _db_rows(
        "SELECT version_no FROM stable_fact_version_embeddings WHERE fact_id=?",
        (fact_id,),
    )
    assert [v["version_no"] for v in vectors] == [1]


def test_s3d1_edit_zero_provider_calls(isolated_client: TestClient, monkeypatch):
    """PATCH must never touch provider registry / credential store / network."""
    import app.services.credentials as cred_mod
    import app.services.provider_runtime as pr_mod

    def boom(*args, **kwargs):
        raise AssertionError("provider must not be called during user edit")

    monkeypatch.setattr(pr_mod.provider_registry, "snapshot", boom)
    monkeypatch.setattr(cred_mod.credential_store, "get", boom)

    session_id = _session("s3d1-edit-noprov")
    fact_id = _seed_fact(session_id=session_id, display_text="喜欢咖啡")
    response = isolated_client.patch(
        f"/api/memory/facts/{fact_id}",
        params=_facts_scope(session_id),
        json={"display_text": "喜欢黑咖啡", "expected_version": 1},
    )
    assert response.status_code == 200, response.text


# -- A2. canonical validation envelopes (REWORK 01 P1) ------------------------


def _patch_code(
    client: TestClient, fact_id: str, session_id: str, body
) -> tuple[int, str]:
    response = client.patch(
        f"/api/memory/facts/{fact_id}", params=_facts_scope(session_id), json=body
    )
    return response.status_code, response.json().get("detail", {}).get("code")


def test_s3d1_edit_missing_expected_version_canonical_422(
    isolated_client: TestClient,
):
    session_id = _session("s3d1-env-missver")
    fact_id = _seed_fact(session_id=session_id, display_text="喜欢咖啡")
    status, code = _patch_code(
        isolated_client, fact_id, session_id, {"display_text": "喜欢黑咖啡"}
    )
    assert status == 422 and code == "missing_expected_version", (status, code)


def test_s3d1_edit_expected_version_non_int_canonical_422(
    isolated_client: TestClient,
):
    session_id = _session("s3d1-env-nonint")
    fact_id = _seed_fact(session_id=session_id, display_text="喜欢咖啡")
    status, code = _patch_code(
        isolated_client, fact_id, session_id,
        {"display_text": "喜欢黑咖啡", "expected_version": "abc"},
    )
    assert status == 422 and code == "invalid_expected_version", (status, code)


def test_s3d1_edit_expected_version_non_positive_canonical_422(
    isolated_client: TestClient,
):
    session_id = _session("s3d1-env-zerover")
    fact_id = _seed_fact(session_id=session_id, display_text="喜欢咖啡")
    status, code = _patch_code(
        isolated_client, fact_id, session_id,
        {"display_text": "喜欢黑咖啡", "expected_version": 0},
    )
    assert status == 422 and code == "invalid_expected_version", (status, code)


def test_s3d1_edit_missing_display_text_canonical_422(
    isolated_client: TestClient,
):
    session_id = _session("s3d1-env-misstext")
    fact_id = _seed_fact(session_id=session_id, display_text="喜欢咖啡")
    status, code = _patch_code(
        isolated_client, fact_id, session_id, {"expected_version": 1}
    )
    assert status == 422 and code == "missing_display_text", (status, code)


def test_s3d1_edit_invalid_display_text_type_canonical_422(
    isolated_client: TestClient,
):
    session_id = _session("s3d1-env-badtype")
    fact_id = _seed_fact(session_id=session_id, display_text="喜欢咖啡")
    status, code = _patch_code(
        isolated_client, fact_id, session_id,
        {"display_text": 123, "expected_version": 1},
    )
    assert status == 422 and code == "invalid_display_text", (status, code)


def test_s3d1_edit_malformed_json_canonical_422(isolated_client: TestClient):
    session_id = _session("s3d1-env-badjson")
    fact_id = _seed_fact(session_id=session_id, display_text="喜欢咖啡")
    response = isolated_client.patch(
        f"/api/memory/facts/{fact_id}",
        params=_facts_scope(session_id),
        content="{not valid json",
        headers={"content-type": "application/json"},
    )
    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "invalid_json_body"


def test_s3d1_edit_non_object_body_canonical_422(isolated_client: TestClient):
    session_id = _session("s3d1-env-nonobj")
    fact_id = _seed_fact(session_id=session_id, display_text="喜欢咖啡")
    response = isolated_client.patch(
        f"/api/memory/facts/{fact_id}",
        params=_facts_scope(session_id),
        content="[1, 2, 3]",
        headers={"content-type": "application/json"},
    )
    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "invalid_request_body"


def test_s3d1_delete_expected_version_non_int_canonical_422(
    isolated_client: TestClient,
):
    session_id = _session("s3d1-env-del-nonint")
    fact_id = _seed_fact(session_id=session_id, display_text="喜欢咖啡")
    response = isolated_client.delete(
        f"/api/memory/facts/{fact_id}",
        params={**_facts_scope(session_id), "expected_version": "abc"},
    )
    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "invalid_expected_version"


def test_s3d1_delete_expected_version_non_positive_canonical_422(
    isolated_client: TestClient,
):
    session_id = _session("s3d1-env-del-zero")
    fact_id = _seed_fact(session_id=session_id, display_text="喜欢咖啡")
    response = isolated_client.delete(
        f"/api/memory/facts/{fact_id}",
        params={**_facts_scope(session_id), "expected_version": "0"},
    )
    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "invalid_expected_version"


def test_s3d1_scope_invalid_identity_and_worldline_canonical_422(
    isolated_client: TestClient,
):
    """All three routes: invalid worldline/identity_mode → canonical envelopes."""
    session_id = _session("s3d1-env-scope")
    fact_id = _seed_fact(session_id=session_id, display_text="喜欢咖啡")
    body = {"display_text": "喜欢黑咖啡", "expected_version": 1}

    patch_bad_mode = isolated_client.patch(
        f"/api/memory/facts/{fact_id}",
        params={"session_id": session_id, "worldline": "steins_gate", "identity_mode": "invalid"},
        json=body,
    )
    assert patch_bad_mode.status_code == 422
    assert patch_bad_mode.json()["detail"]["code"] == "invalid_identity_mode"
    patch_bad_wl = isolated_client.patch(
        f"/api/memory/facts/{fact_id}",
        params={"session_id": session_id, "worldline": "bogus", "identity_mode": "self"},
        json=body,
    )
    assert patch_bad_wl.status_code == 422
    assert patch_bad_wl.json()["detail"]["code"] == "invalid_worldline"

    del_bad_mode = isolated_client.delete(
        f"/api/memory/facts/{fact_id}",
        params={"session_id": session_id, "worldline": "steins_gate", "identity_mode": "invalid", "expected_version": 1},
    )
    assert del_bad_mode.status_code == 422
    assert del_bad_mode.json()["detail"]["code"] == "invalid_identity_mode"
    del_bad_wl = isolated_client.delete(
        f"/api/memory/facts/{fact_id}",
        params={"session_id": session_id, "worldline": "bogus", "identity_mode": "self", "expected_version": 1},
    )
    assert del_bad_wl.status_code == 422
    assert del_bad_wl.json()["detail"]["code"] == "invalid_worldline"

    det_bad_mode = isolated_client.get(
        f"/api/memory/facts/{fact_id}/details",
        params={"session_id": session_id, "worldline": "steins_gate", "identity_mode": "invalid"},
    )
    assert det_bad_mode.status_code == 422
    assert det_bad_mode.json()["detail"]["code"] == "invalid_identity_mode"
    det_bad_wl = isolated_client.get(
        f"/api/memory/facts/{fact_id}/details",
        params={"session_id": session_id, "worldline": "bogus", "identity_mode": "self"},
    )
    assert det_bad_wl.status_code == 422
    assert det_bad_wl.json()["detail"]["code"] == "invalid_worldline"

    # S3D-2 frozen envelope: missing/blank identity_mode → missing_identity_mode.
    for missing_mode in (
        {"session_id": session_id, "worldline": "steins_gate"},
        {"session_id": session_id, "worldline": "steins_gate", "identity_mode": ""},
    ):
        patch_no_mode = isolated_client.patch(
            f"/api/memory/facts/{fact_id}", params=missing_mode, json=body
        )
        assert patch_no_mode.status_code == 422
        assert patch_no_mode.json()["detail"]["code"] == "missing_identity_mode"
        del_no_mode = isolated_client.delete(
            f"/api/memory/facts/{fact_id}",
            params={**missing_mode, "expected_version": 1},
        )
        assert del_no_mode.status_code == 422
        assert del_no_mode.json()["detail"]["code"] == "missing_identity_mode"
        det_no_mode = isolated_client.get(
            f"/api/memory/facts/{fact_id}/details", params=missing_mode
        )
        assert det_no_mode.status_code == 422
        assert det_no_mode.json()["detail"]["code"] == "missing_identity_mode"

    facts = _db_rows(
        "SELECT state, active_version FROM stable_facts WHERE fact_id=?", (fact_id,)
    )
    assert facts[0]["state"] == "active" and facts[0]["active_version"] == 1


def test_s3d1_edit_semantic_json_canonical_shape(isolated_client: TestClient):
    """P2: active user_edit version semantic_json is the canonical payload."""
    import asyncio

    from app.services.memory_v11.repository import create_stable_fact

    session_id = _session("s3d1-sem")

    async def _seed() -> str:
        row = await create_stable_fact(
            session_id=session_id,
            worldline="steins_gate",
            identity_mode="self",
            display_text="喜欢咖啡",
            semantic_json={"subject": "user", "relation": "likes", "object": "coffee"},
        )
        return str(row["fact_id"])

    fact_id = asyncio.run(_seed())
    response = isolated_client.patch(
        f"/api/memory/facts/{fact_id}",
        params=_facts_scope(session_id),
        json={"display_text": "喜欢黑咖啡", "expected_version": 1},
    )
    assert response.status_code == 200, response.text
    rows = _db_rows(
        """SELECT version_no, semantic_json FROM stable_fact_versions
            WHERE fact_id=? ORDER BY version_no ASC""",
        (fact_id,),
    )
    assert len(rows) == 2
    old_semantic = json.loads(rows[0]["semantic_json"])
    new_semantic = json.loads(rows[1]["semantic_json"])
    assert new_semantic == {
        "subject": "user",
        "predicate": "states",
        "object": {"text": "喜欢黑咖啡"},
        "qualifiers": {"source": "user_edit"},
    }, f"unexpected user_edit semantic_json: {new_semantic}"
    assert new_semantic != old_semantic


# -- POST-PUSH HOTFIX: zero-confidence preservation + explicit worldline ------


def test_s3d1_edit_preserves_zero_confidence(isolated_client: TestClient):
    """Hotfix: user_edit preserves the exact stored confidence (0.0 is legal)."""
    import asyncio

    from app.services.memory_v11.repository import create_stable_fact

    session_id = _session("s3d1-zero-conf")

    async def _seed() -> str:
        row = await create_stable_fact(
            session_id=session_id,
            worldline="steins_gate",
            identity_mode="self",
            display_text="低置信事实",
            semantic_json={"subject": "user", "relation": "x", "object": "y"},
            confidence=0.0,
        )
        return str(row["fact_id"])

    fact_id = asyncio.run(_seed())
    assert float(
        _db_rows(
            "SELECT confidence FROM stable_fact_versions WHERE fact_id=? AND version_no=1",
            (fact_id,),
        )[0]["confidence"]
    ) == 0.0

    response = isolated_client.patch(
        f"/api/memory/facts/{fact_id}",
        params=_facts_scope(session_id),
        json={"display_text": "更新后的低置信事实", "expected_version": 1},
    )
    assert response.status_code == 200, response.text
    rows = _db_rows(
        """SELECT version_no, confidence FROM stable_fact_versions
            WHERE fact_id=? ORDER BY version_no ASC""",
        (fact_id,),
    )
    assert len(rows) == 2
    assert float(rows[0]["confidence"]) == 0.0
    assert float(rows[1]["confidence"]) == 0.0, (
        f"user_edit must preserve exact confidence 0.0, got {rows[1]['confidence']}"
    )
    details = isolated_client.get(
        f"/api/memory/facts/{fact_id}/details", params=_facts_scope(session_id)
    )
    assert details.status_code == 200, details.text
    active = next(v for v in details.json()["versions"] if v["is_active"])
    assert float(active["confidence"]) == 0.0


def test_s3d1_scope_missing_worldline_422(isolated_client: TestClient):
    """Hotfix: the three lifecycle routes require an EXPLICIT worldline.

    Missing / blank → 422 missing_worldline; invalid → invalid_worldline.
    """
    session_id = _session("s3d1-missing-wl")
    fact_id = _seed_fact(session_id=session_id, display_text="喜欢咖啡")
    base = {"session_id": session_id, "identity_mode": "self"}
    body = {"display_text": "喜欢黑咖啡", "expected_version": 1}

    def _assert(code: str, response) -> None:
        assert response.status_code == 422, response.text
        assert response.json()["detail"]["code"] == code, response.text

    for wl_value in (None, ""):
        params = dict(base)
        if wl_value is not None:
            params["worldline"] = wl_value
        _assert(
            "missing_worldline",
            isolated_client.patch(
                f"/api/memory/facts/{fact_id}", params=params, json=body
            ),
        )
        _assert(
            "missing_worldline",
            isolated_client.delete(
                f"/api/memory/facts/{fact_id}",
                params={**params, "expected_version": 1},
            ),
        )
        _assert(
            "missing_worldline",
            isolated_client.get(f"/api/memory/facts/{fact_id}/details", params=params),
        )

    # Invalid value keeps the existing invalid_worldline envelope.
    bogus = {**base, "worldline": "bogus"}
    _assert(
        "invalid_worldline",
        isolated_client.patch(f"/api/memory/facts/{fact_id}", params=bogus, json=body),
    )
    _assert(
        "invalid_worldline",
        isolated_client.delete(
            f"/api/memory/facts/{fact_id}", params={**bogus, "expected_version": 1}
        ),
    )
    _assert(
        "invalid_worldline",
        isolated_client.get(f"/api/memory/facts/{fact_id}/details", params=bogus),
    )

    # The fact is untouched by all rejected requests.
    facts = _db_rows(
        "SELECT state, active_version FROM stable_facts WHERE fact_id=?", (fact_id,)
    )
    assert facts[0]["state"] == "active" and facts[0]["active_version"] == 1


# -- B. DELETE ---------------------------------------------------------------


def test_s3d1_delete_erases_versions_vectors_shell(isolated_client: TestClient):
    import asyncio
    from app import models

    session_id = _session("s3d1-del-erase")
    cid = _create_conversation(isolated_client, session_id, 'delete summary')
    asyncio.run(models.save_message(session_id, 'user', '喜欢茶', conversation_id=cid))
    asyncio.run(models.save_memory_summary(session_id, '喜欢茶', conversation_id=cid))
    old_epoch = asyncio.run(models.get_conversation_content_epoch(session_id, conversation_id=cid))
    fact_id = _seed_fact_with_vector(session_id=session_id, display_text="喜欢茶")
    response = isolated_client.delete(
        f"/api/memory/facts/{fact_id}",
        params={**_facts_scope(session_id), "expected_version": 1},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["state"] == "deleted"
    assert body["idempotent"] is False
    assert asyncio.run(models.get_latest_summary(session_id, conversation_id=cid)) == ''
    assert not asyncio.run(models.save_memory_summary(session_id, '喜欢茶', conversation_id=cid, expected_epoch=old_epoch))
    assert any(row['content'] == '喜欢茶' for row in asyncio.run(models.get_session_messages(session_id, conversation_id=cid)))

    shell = _db_rows(
        """SELECT state, active_version, is_pinned, topic_id, deleted_at
             FROM stable_facts WHERE fact_id=?""",
        (fact_id,),
    )
    assert shell[0]["state"] == "deleted"
    assert shell[0]["active_version"] is None
    assert shell[0]["is_pinned"] == 0
    assert shell[0]["topic_id"] is None
    assert shell[0]["deleted_at"] is not None
    assert len(_db_rows("SELECT * FROM stable_fact_versions WHERE fact_id=?", (fact_id,))) == 0
    assert len(_db_rows("SELECT * FROM stable_fact_version_embeddings WHERE fact_id=?", (fact_id,))) == 0
    assert len(_db_rows("SELECT * FROM stable_fact_evidence WHERE fact_id=?", (fact_id,))) == 0

    listed = isolated_client.get("/api/memory/facts", params=_facts_scope(session_id))
    assert listed.status_code == 200
    assert not any(f["fact_id"] == fact_id for f in listed.json()["facts"])
    details = isolated_client.get(
        f"/api/memory/facts/{fact_id}/details", params=_facts_scope(session_id)
    )
    assert details.status_code == 404


def test_s3d1_delete_tombstone_no_recoverable_text(isolated_client: TestClient):
    session_id = _session("s3d1-del-tomb")
    text = "喜欢咖啡"
    fact_id, observation_id = _seed_fact_via_reconciler(
        session_id=session_id, display_text=text, source_message_id=70001
    )
    fingerprint = _db_rows(
        "SELECT semantic_fingerprint FROM stable_fact_versions WHERE fact_id=?",
        (fact_id,),
    )[0]["semantic_fingerprint"]
    response = isolated_client.delete(
        f"/api/memory/facts/{fact_id}",
        params={**_facts_scope(session_id), "expected_version": 1},
    )
    assert response.status_code == 200, response.text
    tombstones = _db_rows(
        """SELECT * FROM memory_tombstones
            WHERE fact_id=? AND session_id=? ORDER BY tombstone_id""",
        (fact_id, session_id),
    )
    assert len(tombstones) >= 1
    assert all(t["reason"] == "user_delete" for t in tombstones)
    blob = str(tombstones)
    assert text not in blob, "tombstone must not carry recoverable fact text"
    fps = {t["semantic_fingerprint"] for t in tombstones} | {
        t["source_fingerprint"] for t in tombstones
    }
    assert fingerprint in fps, "semantic fingerprint tombstone must be recorded"
    assert "fp-s3d1-70001" in fps, "source fingerprint tombstone must be recorded"


def test_s3d1_delete_repeat_idempotent(isolated_client: TestClient):
    session_id = _session("s3d1-del-repeat")
    fact_id = _seed_fact(session_id=session_id, display_text="喜欢咖啡")
    first = isolated_client.delete(
        f"/api/memory/facts/{fact_id}",
        params={**_facts_scope(session_id), "expected_version": 1},
    )
    assert first.status_code == 200, first.text
    second = isolated_client.delete(
        f"/api/memory/facts/{fact_id}",
        params={**_facts_scope(session_id), "expected_version": 1},
    )
    assert second.status_code == 200, second.text
    body = second.json()
    assert body["idempotent"] is True
    assert body["state"] == "deleted"
    assert len(_db_rows("SELECT * FROM stable_fact_versions WHERE fact_id=?", (fact_id,))) == 0
    tombstones = _db_rows("SELECT * FROM memory_tombstones WHERE fact_id=?", (fact_id,))
    assert len(tombstones) == 1, "repeat delete must not duplicate tombstones"


def test_s3d1_delete_stale_expected_version_409_no_mutation(
    isolated_client: TestClient,
):
    session_id = _session("s3d1-del-stale")
    fact_id = _seed_fact_with_vector(session_id=session_id, display_text="喜欢咖啡")
    stale = isolated_client.delete(
        f"/api/memory/facts/{fact_id}",
        params={**_facts_scope(session_id), "expected_version": 99},
    )
    assert stale.status_code == 409, stale.text
    assert stale.json()["detail"]["code"] == "expected_version_mismatch"
    facts = _db_rows(
        "SELECT state, active_version FROM stable_facts WHERE fact_id=?", (fact_id,)
    )
    assert facts[0]["state"] == "active" and facts[0]["active_version"] == 1
    assert len(_db_rows("SELECT * FROM stable_fact_versions WHERE fact_id=?", (fact_id,))) == 1
    assert len(_db_rows("SELECT * FROM stable_fact_version_embeddings WHERE fact_id=?", (fact_id,))) == 1
    assert len(_db_rows("SELECT * FROM memory_tombstones WHERE fact_id=?", (fact_id,))) == 0


def test_s3d1_delete_missing_expected_version_422(isolated_client: TestClient):
    session_id = _session("s3d1-del-missingver")
    fact_id = _seed_fact(session_id=session_id, display_text="喜欢咖啡")
    response = isolated_client.delete(
        f"/api/memory/facts/{fact_id}", params=_facts_scope(session_id)
    )
    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "missing_expected_version"


def test_s3d1_delete_wrong_scope_404(isolated_client: TestClient):
    session_id = _session("s3d1-del-xscope")
    fact_id = _seed_fact(session_id=session_id, display_text="喜欢咖啡")
    for scope in (
        {"session_id": session_id, "worldline": "steins_gate", "identity_mode": "okabe"},
        {"session_id": "someone-else", "worldline": "steins_gate", "identity_mode": "self"},
        {"session_id": session_id, "worldline": "beta", "identity_mode": "self"},
    ):
        response = isolated_client.delete(
            f"/api/memory/facts/{fact_id}",
            params={**scope, "expected_version": 1},
        )
        assert response.status_code == 404, response.text
        assert response.json()["detail"]["code"] == "fact_not_found"
    facts = _db_rows(
        "SELECT state, active_version FROM stable_facts WHERE fact_id=?", (fact_id,)
    )
    assert facts[0]["state"] == "active" and facts[0]["active_version"] == 1


def test_s3d1_delete_exclusive_observation_scrubbed_receipt_retained(
    isolated_client: TestClient,
):
    session_id = _session("s3d1-del-excl")
    text = "喜欢咖啡"
    fact_id, observation_id = _seed_fact_via_reconciler(
        session_id=session_id, display_text=text, source_message_id=70002
    )
    response = isolated_client.delete(
        f"/api/memory/facts/{fact_id}",
        params={**_facts_scope(session_id), "expected_version": 1},
    )
    assert response.status_code == 200, response.text
    obs = _db_rows(
        """SELECT status, display_text, semantic_json, semantic_fingerprint,
                  topic_label_proposal FROM memory_observations
            WHERE observation_id=?""",
        (observation_id,),
    )
    assert obs[0]["status"] == "deleted"
    assert obs[0]["display_text"] is None
    assert obs[0]["semantic_json"] is None
    assert obs[0]["topic_label_proposal"] is None
    assert obs[0]["semantic_fingerprint"] is not None, "non-content hash retained"
    sources = _db_rows(
        """SELECT source_message_id, source_fingerprint, source_state, excerpt
             FROM memory_observation_sources WHERE observation_id=?""",
        (observation_id,),
    )
    assert len(sources) == 1, "source receipt must be retained for replay blocking"
    assert sources[0]["source_message_id"] == 70002
    assert sources[0]["source_state"] == "present"
    assert sources[0]["excerpt"] is None, "deleted fact must erase its exclusive source excerpt"


def test_s3d1_delete_shared_observation_kept(isolated_client: TestClient):
    session_id = _session("s3d1-del-shared")
    text_a = "喜欢动漫"
    text_b = "喜欢看番"
    fact_a, obs_a = _seed_fact_via_reconciler(
        session_id=session_id, display_text=text_a, source_message_id=70003
    )
    fact_b, obs_b = _seed_fact_via_reconciler(
        session_id=session_id, display_text=text_b, source_message_id=70004
    )
    # obs_a now supports fact B as well → shared.
    _db_execute(
        """INSERT INTO stable_fact_evidence(fact_id, version_no, observation_id, evidence_role)
           VALUES(?,1,?,'supporting')""",
        (fact_b, obs_a),
    )
    response = isolated_client.delete(
        f"/api/memory/facts/{fact_a}",
        params={**_facts_scope(session_id), "expected_version": 1},
    )
    assert response.status_code == 200, response.text
    obs = _db_rows(
        """SELECT status, display_text FROM memory_observations
            WHERE observation_id=?""",
        (obs_a,),
    )
    assert obs[0]["status"] == "attached", "shared observation must not be scrubbed"
    assert obs[0]["display_text"] == text_a
    details_b = isolated_client.get(
        f"/api/memory/facts/{fact_b}/details", params=_facts_scope(session_id)
    )
    assert details_b.status_code == 200, details_b.text
    provenance = details_b.json()["provenance"]
    assert any(p["observation_id"] == obs_a for p in provenance)
    assert any(p["observation_id"] == obs_b for p in provenance)
    assert len(_db_rows("SELECT * FROM stable_fact_evidence WHERE fact_id=?", (fact_a,))) == 0


def test_s3d1_delete_old_source_no_resurrect_new_source_relearns(
    isolated_client: TestClient, monkeypatch
):
    """D12: same historical source stays blocked; a NEW source can relearn."""
    import asyncio

    from app import models
    from app.services.conversations import conversation_service
    from app.services.memory_v11 import jobs as jobs_mod
    from app.services.memory_v11.reconciler import apply_validated_memory_operations

    session_id = _session("s3d1-del-resurrect")

    async def _save_user_message(title: str) -> tuple[int, str]:
        conv = await conversation_service.create(
            session_id, "steins_gate", title=title, identity_mode="self"
        )
        conversation_id = str(conv["id"])
        mid = await models.save_message(
            session_id, "user", "我喜欢咖啡",
            worldline="steins_gate", conversation_id=conversation_id,
        )
        return int(mid), conversation_id

    async def _learn_via_job(source_message_id: int, conversation_id: str) -> None:
        job = await jobs_mod.enqueue_memory_job(
            session_id=session_id, worldline="steins_gate",
            conversation_id=conversation_id, identity_mode="self",
            source_message_id=source_message_id, pipeline_version="memory-v11-1",
            provider_id="deepseek", model_id="deepseek-chat",
        )
        result = await jobs_mod.process_memory_job(
            job_id=job["job_id"], lease_owner="w-s3d1",
            memory_completion=_create_completion(source_message_id),
        )
        assert result["state"] == "completed"

    async def _apply_direct(source_message_id: int, conversation_id: str) -> dict:
        return await apply_validated_memory_operations(
            session_id=session_id,
            worldline="steins_gate",
            identity_mode="self",
            observations=[
                {
                    "observation_ref": "o1",
                    "source_message_ids": [source_message_id],
                    "display_text": "喜欢咖啡",
                    "semantic": {
                        "subject": "user",
                        "predicate": "likes",
                        "object": {"name": "coffee"},
                    },
                    "evidence_kind": "direct_user",
                    "memory_class": "stable_candidate",
                    "confidence": 0.96,
                    "topic_label": "饮食",
                    "expires_at": None,
                }
            ],
            operations=[
                {
                    "op": "CREATE",
                    "observation_ref": "o1",
                    "target_fact_id": None,
                    "expected_version": None,
                    "fact_text": "喜欢咖啡",
                    "reason_code": "direct_durable_statement",
                }
            ],
            source_evidence=[
                {
                    "source_message_id": source_message_id,
                    "conversation_id": conversation_id,
                    "source_role": "user",
                    "source_fingerprint": f"fp-{source_message_id}",
                    "source_created_at": "2026-08-01T00:00:00+00:00",
                    "excerpt": "我喜欢咖啡",
                }
            ],
            candidate_allowlist=[],
        )

    # Phase 1: real job pipeline learns from source mid1 (real source receipt).
    mid1, conv1 = asyncio.run(_save_user_message("learn"))
    asyncio.run(_learn_via_job(mid1, conv1))
    facts = _db_rows(
        "SELECT fact_id, state FROM stable_facts WHERE session_id=?", (session_id,)
    )
    assert len(facts) == 1 and facts[0]["state"] == "active"
    fact_id = facts[0]["fact_id"]

    response = isolated_client.delete(
        f"/api/memory/facts/{fact_id}",
        params={**_facts_scope(session_id), "expected_version": 1},
    )
    assert response.status_code == 200, response.text

    # Phase 2: replay the SAME historical source → receipt blocks resurrection.
    replay = asyncio.run(_apply_direct(mid1, conv1))
    assert replay.get("replay") is True, (
        "same historical source must be blocked by the retained source receipt (D12)"
    )
    active_after_replay = _db_rows(
        "SELECT fact_id FROM stable_facts WHERE session_id=? AND state='active'",
        (session_id,),
    )
    assert active_after_replay == [], "old source replay must not resurrect the fact"

    # Phase 3: a genuinely NEW source may establish the fact again (D03).
    mid2, conv2 = asyncio.run(_save_user_message("relearn"))
    relearned = asyncio.run(_apply_direct(mid2, conv2))
    assert relearned.get("replay") is False
    assert len(relearned.get("created_facts") or []) == 1
    active_after_new = _db_rows(
        "SELECT fact_id FROM stable_facts WHERE session_id=? AND state='active'",
        (session_id,),
    )
    assert len(active_after_new) == 1, "a genuinely new source may relearn (D03)"


# -- C. DETAILS --------------------------------------------------------------


def test_s3d1_details_ordered_versions_and_provenance(isolated_client: TestClient):
    session_id = _session("s3d1-details")
    text = "喜欢咖啡"
    fact_id, observation_id = _seed_fact_via_reconciler(
        session_id=session_id, display_text=text, source_message_id=70005,
        excerpt="我今天喜欢咖啡",
    )
    response = isolated_client.get(
        f"/api/memory/facts/{fact_id}/details", params=_facts_scope(session_id)
    )
    assert response.status_code == 200, response.text
    d = response.json()
    assert d["scope"] == _facts_scope(session_id)
    assert d["fact"]["fact_id"] == fact_id
    assert d["fact"]["display_text"] == text
    assert d["fact"]["active_version"] == 1
    assert len(d["versions"]) == 1
    assert d["versions"][0]["is_active"] is True
    assert d["provenance"][0]["observation_id"] == observation_id
    assert d["provenance"][0]["source_message_id"] == 70005
    assert d["provenance"][0]["conversation_id"] == "conv-s3d1-70005"
    assert d["provenance"][0]["source_state"] == "present"
    assert d["provenance"][0]["excerpt"] == "我今天喜欢咖啡"

    edited = isolated_client.patch(
        f"/api/memory/facts/{fact_id}",
        params=_facts_scope(session_id),
        json={"display_text": "喜欢黑咖啡", "expected_version": 1},
    )
    assert edited.status_code == 200, edited.text
    after = isolated_client.get(
        f"/api/memory/facts/{fact_id}/details", params=_facts_scope(session_id)
    )
    assert after.status_code == 200
    d2 = after.json()
    assert [v["version_no"] for v in d2["versions"]] == [1, 2]
    assert d2["versions"][1]["is_active"] is True
    assert d2["versions"][1]["previous_version"] == 1
    assert d2["versions"][0]["is_active"] is False
    assert d2["fact"]["display_text"] == "喜欢黑咖啡"
    prov_versions = {p["version_no"] for p in d2["provenance"]}
    assert prov_versions == {1}, "user-edit versions have no observation provenance"
    body_text = str(d2)
    assert "semantic_json" not in body_text
    assert "semantic_fingerprint" not in body_text
    assert "vector" not in body_text


def test_s3d1_details_source_deleted_forces_excerpt_null(
    isolated_client: TestClient,
):
    session_id = _session("s3d1-details-nexcerpt")
    fact_id, observation_id = _seed_fact_via_reconciler(
        session_id=session_id, display_text="喜欢咖啡", source_message_id=70006,
        excerpt="stale-content-that-must-not-leak",
    )
    _db_execute(
        """UPDATE memory_observation_sources SET source_state='deleted'
            WHERE observation_id=?""",
        (observation_id,),
    )
    response = isolated_client.get(
        f"/api/memory/facts/{fact_id}/details", params=_facts_scope(session_id)
    )
    assert response.status_code == 200, response.text
    prov = response.json()["provenance"]
    assert prov[0]["source_state"] == "deleted"
    assert prov[0]["excerpt"] is None, "deleted source must force excerpt=null"


def test_s3d1_details_cross_scope_and_deleted_404(isolated_client: TestClient):
    session_id = _session("s3d1-details-404")
    fact_id = _seed_fact(session_id=session_id, display_text="喜欢咖啡")
    for scope in (
        {"session_id": session_id, "worldline": "steins_gate", "identity_mode": "okabe"},
        {"session_id": "someone-else", "worldline": "steins_gate", "identity_mode": "self"},
        {"session_id": session_id, "worldline": "beta", "identity_mode": "self"},
    ):
        response = isolated_client.get(
            f"/api/memory/facts/{fact_id}/details", params=scope
        )
        assert response.status_code == 404, response.text
        assert response.json()["detail"]["code"] == "fact_not_found"
    missing = isolated_client.get(
        f"/api/memory/facts/{uuid4()}/details", params=_facts_scope(session_id)
    )
    assert missing.status_code == 404

    deleted = isolated_client.delete(
        f"/api/memory/facts/{fact_id}",
        params={**_facts_scope(session_id), "expected_version": 1},
    )
    assert deleted.status_code == 200, deleted.text
    after = isolated_client.get(
        f"/api/memory/facts/{fact_id}/details", params=_facts_scope(session_id)
    )
    assert after.status_code == 404, "deleted facts are not part of the details surface"


def test_s3d1_details_zero_provider_calls(isolated_client: TestClient, monkeypatch):
    import app.services.credentials as cred_mod
    import app.services.provider_runtime as pr_mod

    def boom(*args, **kwargs):
        raise AssertionError("provider must not be called during details")

    monkeypatch.setattr(pr_mod.provider_registry, "snapshot", boom)
    monkeypatch.setattr(cred_mod.credential_store, "get", boom)

    session_id = _session("s3d1-details-noprov")
    fact_id, _ = _seed_fact_via_reconciler(
        session_id=session_id, display_text="喜欢咖啡", source_message_id=70007
    )
    response = isolated_client.get(
        f"/api/memory/facts/{fact_id}/details", params=_facts_scope(session_id)
    )
    assert response.status_code == 200, response.text


# -- D. transaction rollback -------------------------------------------------


def test_s3d1_delete_mid_transaction_failure_rolls_back(
    isolated_client: TestClient, monkeypatch
):
    """Injected failure after tombstone insert must roll back EVERYTHING."""
    session_id = _session("s3d1-del-rollback")
    text = "喜欢咖啡"
    fact_id, observation_id = _seed_fact_via_reconciler(
        session_id=session_id, display_text=text, source_message_id=70008
    )

    async def boom(*args, **kwargs):
        raise RuntimeError("injected mid-transaction failure")

    monkeypatch.setattr(
        "app.services.memory_v11.facts.delete_version_embedding", boom
    )
    response = isolated_client.delete(
        f"/api/memory/facts/{fact_id}",
        params={**_facts_scope(session_id), "expected_version": 1},
    )
    assert response.status_code == 500, response.text
    assert response.json()["detail"]["code"] == "memory_operation_failed"

    facts = _db_rows(
        "SELECT state, active_version FROM stable_facts WHERE fact_id=?", (fact_id,)
    )
    assert facts[0]["state"] == "active" and facts[0]["active_version"] == 1
    assert len(_db_rows("SELECT * FROM stable_fact_versions WHERE fact_id=?", (fact_id,))) == 1
    assert len(_db_rows("SELECT * FROM stable_fact_version_embeddings WHERE fact_id=?", (fact_id,))) == 1
    assert len(_db_rows("SELECT * FROM stable_fact_evidence WHERE fact_id=?", (fact_id,))) == 1
    assert len(_db_rows("SELECT * FROM memory_tombstones WHERE fact_id=?", (fact_id,))) == 0
    obs = _db_rows(
        "SELECT status, display_text FROM memory_observations WHERE observation_id=?",
        (observation_id,),
    )
    assert obs[0]["status"] == "attached"
    assert obs[0]["display_text"] == text


def _create_completion(source_message_id: int):
    async def completion(**kwargs):
        sid = int(kwargs["source_message_id"])
        return {
            "observations": [
                {
                    "observation_ref": "o1",
                    "source_message_ids": [sid],
                    "display_text": "喜欢咖啡",
                    "semantic": {
                        "subject": "user",
                        "predicate": "likes",
                        "object": {"name": "coffee"},
                    },
                    "evidence_kind": "direct_user",
                    "memory_class": "stable_candidate",
                    "confidence": 0.96,
                    "topic_label": "饮食",
                    "expires_at": None,
                }
            ],
            "operations": [
                {
                    "op": "CREATE",
                    "observation_ref": "o1",
                    "target_fact_id": None,
                    "expected_version": None,
                    "fact_text": "喜欢咖啡",
                    "reason_code": "direct_durable_statement",
                }
            ],
        }

    return completion

# ---------------------------------------------------------------------------
# MEMORY-V11-S3D-2: topics + presentation foundation.
# ---------------------------------------------------------------------------


def _fact_topic_id_db(fact_id: str) -> str | None:
    rows = _db_rows("SELECT topic_id FROM stable_facts WHERE fact_id=?", (fact_id,))
    assert rows
    return rows[0]["topic_id"]


def _alias_rows(topic_id: str) -> list[dict]:
    return _db_rows(
        """SELECT session_id, identity_mode, normalized_alias
             FROM memory_topic_aliases WHERE topic_id=?
             ORDER BY normalized_alias""",
        (topic_id,),
    )


def _pin_facts(client: TestClient, session_id: str, fact_ids: list[str]) -> None:
    for fact_id in fact_ids:
        response = client.patch(
            f"/api/memory/facts/{fact_id}/presentation",
            params=_facts_scope(session_id),
            json={"is_pinned": True},
        )
        assert response.status_code == 200, response.text


# -- 13. post-create stickiness ---------------------------------------------


def test_s3d2_sticky_create_attach_preserves_topic(isolated_client: TestClient):
    session_id = _session("s3d2-sticky-attach")
    fact_id, _ = _seed_fact_via_reconciler(
        session_id=session_id, display_text="喜欢看动漫", source_message_id=4101,
        topic_label="动漫",
    )
    topic_before = _fact_topic_id_db(fact_id)
    assert topic_before is not None
    _reconciler_apply(
        session_id=session_id,
        observations=[_s3d2_obs(ref="o2", display_text="喜欢看动漫", source_message_id=4102, topic_label="游戏")],
        operations=[{"op": "ATTACH", "observation_ref": "o2", "target_fact_id": fact_id, "expected_version": 1, "fact_text": "喜欢看动漫", "reason_code": "equivalent_evidence"}],
        source_message_id=4102,
        candidate_allowlist=(fact_id,),
    )
    assert _fact_topic_id_db(fact_id) == topic_before, "ATTACH must preserve topic"


def test_s3d2_sticky_refine_preserves_topic(isolated_client: TestClient):
    session_id = _session("s3d2-sticky-refine")
    fact_id, _ = _seed_fact_via_reconciler(
        session_id=session_id, display_text="喜欢咖啡", source_message_id=4201,
        topic_label="饮食",
    )
    topic_before = _fact_topic_id_db(fact_id)
    _reconciler_apply(
        session_id=session_id,
        observations=[_s3d2_obs(ref="o2", display_text="喜欢黑咖啡", source_message_id=4202, topic_label="游戏")],
        operations=[{"op": "REFINE", "observation_ref": "o2", "target_fact_id": fact_id, "expected_version": 1, "fact_text": "喜欢黑咖啡", "reason_code": "compatible_refinement"}],
        source_message_id=4202,
        candidate_allowlist=(fact_id,),
    )
    assert _fact_topic_id_db(fact_id) == topic_before, "REFINE must preserve topic"


def test_s3d2_sticky_supersede_preserves_topic(isolated_client: TestClient):
    session_id = _session("s3d2-sticky-super")
    fact_id, _ = _seed_fact_via_reconciler(
        session_id=session_id, display_text="喜欢咖啡", source_message_id=4301,
        topic_label="饮食",
    )
    topic_before = _fact_topic_id_db(fact_id)
    _reconciler_apply(
        session_id=session_id,
        observations=[_s3d2_obs(ref="o2", display_text="现在更喜欢茶", source_message_id=4302, topic_label="游戏")],
        operations=[{"op": "SUPERSEDE", "observation_ref": "o2", "target_fact_id": fact_id, "expected_version": 1, "fact_text": "现在更喜欢茶", "reason_code": "preference_change"}],
        source_message_id=4302,
        candidate_allowlist=(fact_id,),
    )
    assert _fact_topic_id_db(fact_id) == topic_before, "SUPERSEDE must preserve topic"


def test_s3d2_sticky_null_stays_null_refine_and_supersede(isolated_client: TestClient):
    session_id = _session("s3d2-sticky-null")
    fact_id, _ = _seed_fact_via_reconciler(
        session_id=session_id, display_text="喜欢咖啡", source_message_id=4401,
        topic_label=None,
    )
    assert _fact_topic_id_db(fact_id) is None
    _reconciler_apply(
        session_id=session_id,
        observations=[_s3d2_obs(ref="o2", display_text="喜欢黑咖啡", source_message_id=4402, topic_label="动漫")],
        operations=[{"op": "REFINE", "observation_ref": "o2", "target_fact_id": fact_id, "expected_version": 1, "fact_text": "喜欢黑咖啡", "reason_code": "compatible_refinement"}],
        source_message_id=4402,
        candidate_allowlist=(fact_id,),
    )
    assert _fact_topic_id_db(fact_id) is None, "REFINE must never auto-fill NULL topic"
    _reconciler_apply(
        session_id=session_id,
        observations=[_s3d2_obs(ref="o3", display_text="现在更喜欢茶", source_message_id=4403, topic_label="动漫")],
        operations=[{"op": "SUPERSEDE", "observation_ref": "o3", "target_fact_id": fact_id, "expected_version": 2, "fact_text": "现在更喜欢茶", "reason_code": "preference_change"}],
        source_message_id=4403,
        candidate_allowlist=(fact_id,),
    )
    assert _fact_topic_id_db(fact_id) is None, "SUPERSEDE must never auto-fill NULL topic"


def test_s3d2_sticky_ungroup_survives_refine(isolated_client: TestClient):
    session_id = _session("s3d2-sticky-ungroup")
    fact_id, _ = _seed_fact_via_reconciler(
        session_id=session_id, display_text="喜欢看动漫", source_message_id=4501,
        topic_label="动漫",
    )
    topic_id = _fact_topic_id_db(fact_id)
    ungroup = isolated_client.patch(
        f"/api/memory/facts/{fact_id}/presentation",
        params=_facts_scope(session_id),
        json={"topic_id": None},
    )
    assert ungroup.status_code == 200, ungroup.text
    assert _fact_topic_id_db(fact_id) is None
    _reconciler_apply(
        session_id=session_id,
        observations=[_s3d2_obs(ref="o2", display_text="偏好搞笑动漫", source_message_id=4502, topic_label="动漫")],
        operations=[{"op": "REFINE", "observation_ref": "o2", "target_fact_id": fact_id, "expected_version": 1, "fact_text": "偏好搞笑动漫", "reason_code": "compatible_refinement"}],
        source_message_id=4502,
        candidate_allowlist=(fact_id,),
    )
    assert _fact_topic_id_db(fact_id) is None, (
        "explicit ungroup must survive later REFINE (topic_id NULL is sticky)"
    )
    assert topic_id is not None


# -- 14. alias / rename ------------------------------------------------------


def test_s3d2_rename_old_label_becomes_alias_facts_keep_topic(
    isolated_client: TestClient,
):
    session_id = _session("s3d2-rename")
    topic_id = _seed_topic(session_id, "self", "动漫")
    fact_id = _seed_fact(session_id=session_id, display_text="喜欢看动漫", topic_id=topic_id)
    response = isolated_client.patch(
        f"/api/memory/topics/{topic_id}",
        params=_facts_scope(session_id),
        json={"display_label": "动画"},
    )
    assert response.status_code == 200, response.text
    rows = _db_rows(
        """SELECT normalized_label, display_label FROM memory_topics
            WHERE topic_id=? AND session_id=? AND identity_mode='self'""",
        (topic_id, session_id),
    )
    assert rows[0]["normalized_label"] == "动画"
    assert rows[0]["display_label"] == "动画"
    aliases = _alias_rows(topic_id)
    assert [a["normalized_alias"] for a in aliases] == ["动漫"], (
        "old normalized label must become an alias of the SAME topic"
    )
    listed = isolated_client.get("/api/memory/facts", params=_facts_scope(session_id))
    card = next(f for f in listed.json()["facts"] if f["fact_id"] == fact_id)
    assert card["topic_id"] == topic_id, "rename must keep facts attached to the topic"


def test_s3d2_rename_back_to_own_alias(isolated_client: TestClient):
    session_id = _session("s3d2-rename-back")
    topic_id = _seed_topic(session_id, "self", "动漫")
    first = isolated_client.patch(
        f"/api/memory/topics/{topic_id}",
        params=_facts_scope(session_id),
        json={"display_label": "动画"},
    )
    assert first.status_code == 200, first.text
    back = isolated_client.patch(
        f"/api/memory/topics/{topic_id}",
        params=_facts_scope(session_id),
        json={"display_label": "动漫"},
    )
    assert back.status_code == 200, back.text
    rows = _db_rows(
        "SELECT normalized_label FROM memory_topics WHERE topic_id=?", (topic_id,)
    )
    assert rows[0]["normalized_label"] == "动漫"
    aliases = [a["normalized_alias"] for a in _alias_rows(topic_id)]
    assert "动画" in aliases, "old canonical must become alias after rename-back"
    assert "动漫" not in aliases, "own alias must not remain after becoming canonical"


def test_s3d2_rename_conflict_other_canonical_409(isolated_client: TestClient):
    session_id = _session("s3d2-rename-conflict")
    topic_a = _seed_topic(session_id, "self", "动漫")
    topic_b = _seed_topic(session_id, "self", "游戏")
    response = isolated_client.patch(
        f"/api/memory/topics/{topic_b}",
        params=_facts_scope(session_id),
        json={"display_label": "动漫"},
    )
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "topic_label_conflict"
    rows = _db_rows(
        "SELECT normalized_label FROM memory_topics WHERE topic_id=?", (topic_b,)
    )
    assert rows[0]["normalized_label"] == "游戏", "conflict must not mutate"
    assert _alias_rows(topic_a) == [] and _alias_rows(topic_b) == []


def test_s3d2_rename_conflict_other_alias_409(isolated_client: TestClient):
    session_id = _session("s3d2-rename-alias-conflict")
    topic_a = _seed_topic(session_id, "self", "动漫")
    first = isolated_client.patch(
        f"/api/memory/topics/{topic_a}",
        params=_facts_scope(session_id),
        json={"display_label": "动画"},
    )
    assert first.status_code == 200, first.text
    topic_b = _seed_topic(session_id, "self", "喜剧")
    response = isolated_client.patch(
        f"/api/memory/topics/{topic_b}",
        params=_facts_scope(session_id),
        json={"display_label": "动漫"},
    )
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "topic_label_conflict"
    rows = _db_rows(
        "SELECT normalized_label FROM memory_topics WHERE topic_id=?", (topic_b,)
    )
    assert rows[0]["normalized_label"] == "喜剧"


def test_s3d2_rename_alias_reused_by_new_create(isolated_client: TestClient):
    session_id = _session("s3d2-alias-reuse")
    topic_id = _seed_topic(session_id, "self", "动漫")
    renamed = isolated_client.patch(
        f"/api/memory/topics/{topic_id}",
        params=_facts_scope(session_id),
        json={"display_label": "动画"},
    )
    assert renamed.status_code == 200, renamed.text
    fact_id, _ = _seed_fact_via_reconciler(
        session_id=session_id, display_text="喜欢看动漫", source_message_id=4601,
        topic_label="动漫",
    )
    assert _fact_topic_id_db(fact_id) == topic_id, (
        "future model proposal using the old alias wording must reuse the topic"
    )


# -- 15. presentation / pin --------------------------------------------------


def test_s3d2_presentation_pin_move_ungroup(isolated_client: TestClient):
    session_id = _session("s3d2-present-basic")
    topic1 = _seed_topic(session_id, "self", "动漫")
    topic2 = _seed_topic(session_id, "self", "饮食")
    fact_id, _ = _seed_fact_via_reconciler(
        session_id=session_id, display_text="喜欢看动漫", source_message_id=4701,
        topic_label="动漫",
    )
    assert _fact_topic_id_db(fact_id) == topic1
    move = isolated_client.patch(
        f"/api/memory/facts/{fact_id}/presentation",
        params=_facts_scope(session_id),
        json={"is_pinned": True, "topic_id": topic2},
    )
    assert move.status_code == 200, move.text
    body = move.json()
    assert body["is_pinned"] is True and body["topic_id"] == topic2
    listed = isolated_client.get("/api/memory/facts", params=_facts_scope(session_id))
    card = next(f for f in listed.json()["facts"] if f["fact_id"] == fact_id)
    assert card["is_pinned"] is True and card["topic_id"] == topic2
    ungroup = isolated_client.patch(
        f"/api/memory/facts/{fact_id}/presentation",
        params=_facts_scope(session_id),
        json={"topic_id": None},
    )
    assert ungroup.status_code == 200, ungroup.text
    assert _fact_topic_id_db(fact_id) is None
    unpin = isolated_client.patch(
        f"/api/memory/facts/{fact_id}/presentation",
        params=_facts_scope(session_id),
        json={"is_pinned": False},
    )
    assert unpin.status_code == 200, unpin.text


def test_s3d2_presentation_cross_scope_topic_move_404(isolated_client: TestClient):
    session_id = _session("s3d2-present-xscope")
    fact_id, _ = _seed_fact_via_reconciler(
        session_id=session_id, display_text="喜欢看动漫", source_message_id=4801,
        topic_label="动漫",
    )
    other_topic = _seed_topic(session_id, "okabe", "冈部话题")
    cross_identity = isolated_client.patch(
        f"/api/memory/facts/{fact_id}/presentation",
        params={
            "session_id": session_id,
            "worldline": "steins_gate",
            "identity_mode": "self",
        },
        json={"topic_id": other_topic},
    )
    assert cross_identity.status_code == 404, cross_identity.text
    assert cross_identity.json()["detail"]["code"] == "topic_not_found"
    assert _fact_topic_id_db(fact_id) is not None
    assert _fact_topic_id_db(fact_id) != other_topic

    other_session = _session("s3d2-other-session")
    topic_other = _seed_topic(other_session, "self", "别人的话题")
    cross_session = isolated_client.patch(
        f"/api/memory/facts/{fact_id}/presentation",
        params={
            "session_id": session_id,
            "worldline": "steins_gate",
            "identity_mode": "self",
        },
        json={"topic_id": topic_other},
    )
    assert cross_session.status_code == 404, cross_session.text
    assert cross_session.json()["detail"]["code"] == "topic_not_found"

    cross_worldline = isolated_client.patch(
        f"/api/memory/facts/{fact_id}/presentation",
        params={
            "session_id": session_id,
            "worldline": "beta",
            "identity_mode": "self",
        },
        json={"topic_id": other_topic},
    )
    assert cross_worldline.status_code == 404, cross_worldline.text
    assert cross_worldline.json()["detail"]["code"] == "fact_not_found"


def test_s3d2_presentation_rejects_bad_bodies(isolated_client: TestClient):
    session_id = _session("s3d2-present-bad")
    fact_id = _seed_fact(session_id=session_id, display_text="喜欢咖啡")

    def _assert_code(body, code: str) -> None:
        response = isolated_client.patch(
            f"/api/memory/facts/{fact_id}/presentation",
            params=_facts_scope(session_id),
            json=body,
        )
        assert response.status_code == 422, response.text
        assert response.json()["detail"]["code"] == code, response.text

    _assert_code({}, "missing_presentation_fields")
    _assert_code({"display_text": "偷偷改"}, "extra_fields_forbidden")
    _assert_code({"is_pinned": "yes"}, "invalid_is_pinned")
    _assert_code({"is_pinned": None}, "invalid_is_pinned")
    _assert_code({"topic_id": 123}, "invalid_topic_id")
    _assert_code({"topic_id": "  "}, "invalid_topic_id")
    _assert_code({"expected_version": 1}, "extra_fields_forbidden")


def test_s3d2_presentation_no_versions_vectors_evidence(isolated_client: TestClient):
    session_id = _session("s3d2-present-pure")
    fact_id, _ = _seed_fact_via_reconciler(
        session_id=session_id, display_text="喜欢看动漫", source_message_id=4901,
        topic_label="动漫",
    )
    versions_before = _db_rows(
        "SELECT version_no, semantic_json FROM stable_fact_versions WHERE fact_id=?",
        (fact_id,),
    )
    vectors_before = _db_rows(
        "SELECT version_no FROM stable_fact_version_embeddings WHERE fact_id=?",
        (fact_id,),
    )
    evidence_before = _db_rows(
        "SELECT observation_id FROM stable_fact_evidence WHERE fact_id=?", (fact_id,)
    )
    response = isolated_client.patch(
        f"/api/memory/facts/{fact_id}/presentation",
        params=_facts_scope(session_id),
        json={"is_pinned": True},
    )
    assert response.status_code == 200, response.text
    versions_after = _db_rows(
        "SELECT version_no, semantic_json FROM stable_fact_versions WHERE fact_id=?",
        (fact_id,),
    )
    vectors_after = _db_rows(
        "SELECT version_no FROM stable_fact_version_embeddings WHERE fact_id=?",
        (fact_id,),
    )
    evidence_after = _db_rows(
        "SELECT observation_id FROM stable_fact_evidence WHERE fact_id=?", (fact_id,)
    )
    assert versions_before == versions_after
    assert vectors_before == vectors_after
    assert evidence_before == evidence_after


def test_s3d2_presentation_and_topics_zero_provider(
    isolated_client: TestClient, monkeypatch
):
    import app.services.credentials as cred_mod
    import app.services.provider_runtime as pr_mod

    def boom(*args, **kwargs):
        raise AssertionError("provider must not be called")

    monkeypatch.setattr(pr_mod.provider_registry, "snapshot", boom)
    monkeypatch.setattr(cred_mod.credential_store, "get", boom)

    session_id = _session("s3d2-noprov")
    fact_id, _ = _seed_fact_via_reconciler(
        session_id=session_id, display_text="喜欢看动漫", source_message_id=5001,
        topic_label="动漫",
    )
    presentation = isolated_client.patch(
        f"/api/memory/facts/{fact_id}/presentation",
        params=_facts_scope(session_id),
        json={"is_pinned": True},
    )
    assert presentation.status_code == 200, presentation.text
    listed = isolated_client.get("/api/memory/topics", params=_facts_scope(session_id))
    assert listed.status_code == 200, listed.text
    renamed = isolated_client.patch(
        f"/api/memory/topics/{listed.json()['topics'][0]['topic_id']}",
        params=_facts_scope(session_id),
        json={"display_label": "动画"},
    )
    assert renamed.status_code == 200, renamed.text


def test_s3d2_pin_cap_8_ninth_rejected(isolated_client: TestClient):
    session_id = _session("s3d2-pin-cap")
    fact_ids = [
        _seed_fact(session_id=session_id, display_text=f"事实{i}") for i in range(9)
    ]
    _pin_facts(isolated_client, session_id, fact_ids[:7])
    eighth = isolated_client.patch(
        f"/api/memory/facts/{fact_ids[7]}/presentation",
        params=_facts_scope(session_id),
        json={"is_pinned": True},
    )
    assert eighth.status_code == 200, eighth.text
    ninth = isolated_client.patch(
        f"/api/memory/facts/{fact_ids[8]}/presentation",
        params=_facts_scope(session_id),
        json={"is_pinned": True},
    )
    assert ninth.status_code == 409, ninth.text
    assert ninth.json()["detail"]["code"] == "pin_limit_reached"
    rows = _db_rows(
        """SELECT is_pinned FROM stable_facts
            WHERE session_id=? AND identity_mode='self' AND fact_id=?""",
        (session_id, fact_ids[8]),
    )
    assert rows[0]["is_pinned"] == 0, "rejected pin must not mutate"


def test_s3d2_pin_idempotent_and_unpin(isolated_client: TestClient):
    session_id = _session("s3d2-pin-idem")
    fact_id = _seed_fact(session_id=session_id, display_text="喜欢咖啡")
    first = isolated_client.patch(
        f"/api/memory/facts/{fact_id}/presentation",
        params=_facts_scope(session_id),
        json={"is_pinned": True},
    )
    assert first.status_code == 200, first.text
    again = isolated_client.patch(
        f"/api/memory/facts/{fact_id}/presentation",
        params=_facts_scope(session_id),
        json={"is_pinned": True},
    )
    assert again.status_code == 200, "already-pinned → true must stay idempotent"
    unpin = isolated_client.patch(
        f"/api/memory/facts/{fact_id}/presentation",
        params=_facts_scope(session_id),
        json={"is_pinned": False},
    )
    assert unpin.status_code == 200, unpin.text


def test_s3d2_pin_deleted_not_counted(isolated_client: TestClient):
    session_id = _session("s3d2-pin-deleted")
    fact_ids = [
        _seed_fact(session_id=session_id, display_text=f"事实{i}") for i in range(9)
    ]
    _pin_facts(isolated_client, session_id, fact_ids[:8])
    deleted = isolated_client.delete(
        f"/api/memory/facts/{fact_ids[0]}",
        params={**_facts_scope(session_id), "expected_version": 1},
    )
    assert deleted.status_code == 200, deleted.text
    ninth = isolated_client.patch(
        f"/api/memory/facts/{fact_ids[8]}/presentation",
        params=_facts_scope(session_id),
        json={"is_pinned": True},
    )
    assert ninth.status_code == 200, (
        f"deleted facts must not count toward the pin cap: {ninth.text}"
    )


def test_s3d2_pin_joint_rollback_topic_move(isolated_client: TestClient):
    session_id = _session("s3d2-pin-joint")
    topic1 = _seed_topic(session_id, "self", "动漫")
    topic2 = _seed_topic(session_id, "self", "饮食")
    fact_id, _ = _seed_fact_via_reconciler(
        session_id=session_id, display_text="喜欢看动漫", source_message_id=5101,
        topic_label="动漫",
    )
    assert _fact_topic_id_db(fact_id) == topic1
    others = [
        _seed_fact(session_id=session_id, display_text=f"填充{i}") for i in range(8)
    ]
    _pin_facts(isolated_client, session_id, others)
    joint = isolated_client.patch(
        f"/api/memory/facts/{fact_id}/presentation",
        params=_facts_scope(session_id),
        json={"topic_id": topic2, "is_pinned": True},
    )
    assert joint.status_code == 409, joint.text
    assert joint.json()["detail"]["code"] == "pin_limit_reached"
    assert _fact_topic_id_db(fact_id) == topic1, (
        "rejected pin must roll back the simultaneous topic move"
    )


# -- 16. topic list ----------------------------------------------------------


def test_s3d2_topics_list_counts_hides_zero_active_and_reuse(
    isolated_client: TestClient,
):
    session_id = _session("s3d2-list-counts")
    topic_id = _seed_topic(session_id, "self", "动漫")
    hidden_id = _seed_topic(session_id, "self", "空主题")
    f1 = _seed_fact(session_id=session_id, display_text="喜欢看动漫", topic_id=topic_id)
    f2 = _seed_fact(session_id=session_id, display_text="偏好搞笑动漫", topic_id=topic_id)
    deleted = isolated_client.delete(
        f"/api/memory/facts/{f2}",
        params={**_facts_scope(session_id), "expected_version": 1},
    )
    assert deleted.status_code == 200, deleted.text
    listed = isolated_client.get("/api/memory/topics", params=_facts_scope(session_id))
    assert listed.status_code == 200, listed.text
    topics = listed.json()["topics"]
    assert [t["topic_id"] for t in topics] == [topic_id]
    assert topics[0]["fact_count"] == 1, "deleted facts must be excluded from count"
    assert "aliases" not in str(listed.json())

    hidden_rows = _db_rows(
        "SELECT topic_id FROM memory_topics WHERE topic_id=?", (hidden_id,)
    )
    assert hidden_rows, "zero-active topic row must remain in DB"

    new_fact, _ = _seed_fact_via_reconciler(
        session_id=session_id, display_text="学习空主题", source_message_id=5201,
        topic_label="空主题",
    )
    assert _fact_topic_id_db(new_fact) == hidden_id, (
        "later CREATE with matching label must reuse the hidden topic"
    )
    after = isolated_client.get("/api/memory/topics", params=_facts_scope(session_id))
    visible = [t["topic_id"] for t in after.json()["topics"]]
    assert hidden_id in visible, "reused topic must become visible again"
    assert f1 != new_fact


def test_s3d2_topics_list_ordering_no_aliases(isolated_client: TestClient):
    session_id = _session("s3d2-list-order")
    t_b = _seed_topic(session_id, "self", "B动漫")
    t_a = _seed_topic(session_id, "self", "A游戏")
    _seed_fact(session_id=session_id, display_text="看B动漫", topic_id=t_b)
    _seed_fact(session_id=session_id, display_text="玩A游戏", topic_id=t_a)
    listed = isolated_client.get("/api/memory/topics", params=_facts_scope(session_id))
    assert listed.status_code == 200, listed.text
    topics = listed.json()["topics"]
    assert [t["topic_id"] for t in topics] == [t_a, t_b]
    assert set(topics[0].keys()) == {"topic_id", "display_label", "fact_count"}


# -- 17. scope envelopes for the 3 new routes --------------------------------


def test_s3d2_scope_envelopes_all_routes(isolated_client: TestClient):
    session_id = _session("s3d2-env")
    fact_id = _seed_fact(session_id=session_id, display_text="喜欢咖啡")
    topic_id = _seed_topic(session_id, "self", "动漫")

    cases = [
        (None, None, "missing_worldline"),
        ("", None, "missing_worldline"),
        ("bogus", None, "invalid_worldline"),
        ("steins_gate", None, "missing_identity_mode"),
        ("steins_gate", "", "missing_identity_mode"),
        ("steins_gate", "bogus", "invalid_identity_mode"),
    ]
    for worldline, identity_mode, expected in cases:
        params = {"session_id": session_id}
        if worldline is not None:
            params["worldline"] = worldline
        if identity_mode is not None:
            params["identity_mode"] = identity_mode

        presentation = isolated_client.patch(
            f"/api/memory/facts/{fact_id}/presentation",
            params=params,
            json={"is_pinned": True},
        )
        assert presentation.status_code == 422, presentation.text
        assert presentation.json()["detail"]["code"] == expected

        listed = isolated_client.get("/api/memory/topics", params=params)
        assert listed.status_code == 422, listed.text
        assert listed.json()["detail"]["code"] == expected

        renamed = isolated_client.patch(
            f"/api/memory/topics/{topic_id}", params=params, json={"display_label": "动画"}
        )
        assert renamed.status_code == 422, renamed.text
        assert renamed.json()["detail"]["code"] == expected

    for params in (
        {"worldline": "steins_gate", "identity_mode": "self"},
        {"worldline": "steins_gate", "identity_mode": "self", "session_id": ""},
    ):
        presentation = isolated_client.patch(
            f"/api/memory/facts/{fact_id}/presentation",
            params=params,
            json={"is_pinned": True},
        )
        assert presentation.status_code == 422
        assert presentation.json()["detail"]["code"] == "empty_session_id"
        listed = isolated_client.get("/api/memory/topics", params=params)
        assert listed.status_code == 422
        assert listed.json()["detail"]["code"] == "empty_session_id"
        renamed = isolated_client.patch(
            f"/api/memory/topics/{topic_id}", params=params, json={"display_label": "动画"}
        )
        assert renamed.status_code == 422
        assert renamed.json()["detail"]["code"] == "empty_session_id"


# ---------------------------------------------------------------------------
# MEMORY-V11-S3D-3: experience + observation REST/lifecycle.
# ---------------------------------------------------------------------------


def _create_episodic(
    *,
    session_id: str,
    display_text: str,
    source_message_ids: list[int],
    identity_mode: str = "self",
) -> tuple[str, str]:
    """Real reconciler episodic CREATE → (experience_id, observation_id)."""
    result = _reconciler_apply(
        session_id=session_id,
        identity_mode=identity_mode,
        observations=[
            _s3d3_episodic_obs(
                ref="o1", display_text=display_text, source_message_ids=source_message_ids
            )
        ],
        operations=[
            {
                "op": "CREATE",
                "observation_ref": "o1",
                "target_fact_id": None,
                "expected_version": None,
                "fact_text": display_text,
                "reason_code": "contextual_event",
            }
        ],
        source_message_ids=source_message_ids,
    )
    db_rows = _db_rows(
        """SELECT e.experience_id, e.observation_id FROM experiences e
            WHERE e.session_id=?""",
        (session_id,),
    )
    assert db_rows, "episodic CREATE must produce an experience"
    return str(db_rows[-1]["experience_id"]), str(db_rows[-1]["observation_id"])


def _exp_status(experience_id: str) -> dict:
    rows = _db_rows(
        """SELECT status, display_text, semantic_json, deleted_at,
                  semantic_fingerprint FROM experiences
            WHERE experience_id=?""",
        (experience_id,),
    )
    assert rows
    return rows[0]


# -- experience browse / details ---------------------------------------------


def test_s3d3_e1_real_create_then_list(isolated_client: TestClient):
    session_id = _session("s3d3-e1")
    _create_episodic(
        session_id=session_id, display_text="上周去了秋叶原", source_message_ids=[6101]
    )
    listed = isolated_client.get("/api/memory/experiences", params=_facts_scope(session_id))
    assert listed.status_code == 200, listed.text
    body = listed.json()
    assert body["scope"] == _facts_scope(session_id)
    assert len(body["experiences"]) == 1
    item = body["experiences"][0]
    assert item["display_text"] == "上周去了秋叶原"
    assert item["status"] == "active"
    assert item["is_expired"] is False
    assert body["pagination"] == {"limit": 20, "offset": 0, "total": 1, "has_more": False}
    assert "semantic_json" not in str(body) and "semantic_fingerprint" not in str(body)


def test_s3d3_e2_self_okabe_isolation(isolated_client: TestClient):
    session_id = _session("s3d3-e2")
    self_marker = f"E2_SELF_{uuid4().hex[:8]}"
    okabe_marker = f"E2_OKABE_{uuid4().hex[:8]}"
    _create_episodic(session_id=session_id, display_text=self_marker, source_message_ids=[6201])
    _create_episodic(
        session_id=session_id, identity_mode="okabe",
        display_text=okabe_marker, source_message_ids=[6202],
    )
    self_list = isolated_client.get("/api/memory/experiences", params=_facts_scope(session_id))
    assert self_list.status_code == 200
    assert self_marker in str(self_list.json())
    assert okabe_marker not in str(self_list.json())
    okabe_list = isolated_client.get(
        "/api/memory/experiences",
        params={
            "session_id": session_id, "worldline": "steins_gate", "identity_mode": "okabe",
        },
    )
    assert okabe_marker in str(okabe_list.json())
    assert self_marker not in str(okabe_list.json())


def test_s3d3_e3_worldline_isolation(isolated_client: TestClient):
    session_id = _session("s3d3-e3")
    marker = f"E3_SG_{uuid4().hex[:8]}"
    _create_episodic(session_id=session_id, display_text=marker, source_message_ids=[6301])
    beta_list = isolated_client.get(
        "/api/memory/experiences",
        params={"session_id": session_id, "worldline": "beta", "identity_mode": "self"},
    )
    assert beta_list.status_code == 200, beta_list.text
    assert marker not in str(beta_list.json())


def test_s3d3_e4_details_multi_source_provenance(isolated_client: TestClient):
    session_id = _session("s3d3-e4")
    experience_id, observation_id = _create_episodic(
        session_id=session_id, display_text="与朋友去了咖啡厅",
        source_message_ids=[6401, 6402],
    )
    details = isolated_client.get(
        f"/api/memory/experiences/{experience_id}/details",
        params=_facts_scope(session_id),
    )
    assert details.status_code == 200, details.text
    body = details.json()
    assert body["observation_id"] == observation_id
    prov = body["provenance"]
    assert [p["source_message_id"] for p in prov] == [6401, 6402], (
        "one observation may have multiple source rows, ordered by source id"
    )
    assert all(p["source_state"] == "present" for p in prov)
    assert all(p["excerpt"] == "s3d2 excerpt" for p in prov)
    assert "semantic_json" not in str(body) and "source_fingerprint" not in str(body)


def test_s3d3_e5_deleted_source_forces_excerpt_null(isolated_client: TestClient):
    session_id = _session("s3d3-e5")
    experience_id, observation_id = _create_episodic(
        session_id=session_id, display_text="去看电影了", source_message_ids=[6501]
    )
    _db_execute(
        """UPDATE memory_observation_sources SET source_state='deleted'
            WHERE observation_id=?""",
        (observation_id,),
    )
    details = isolated_client.get(
        f"/api/memory/experiences/{experience_id}/details",
        params=_facts_scope(session_id),
    )
    assert details.status_code == 200, details.text
    prov = details.json()["provenance"]
    assert prov[0]["source_state"] == "deleted"
    assert prov[0]["excerpt"] is None


def test_s3d3_e6_delete_scrubs_experience(isolated_client: TestClient):
    import json as _json

    session_id = _session("s3d3-e6")
    experience_id, _ = _create_episodic(
        session_id=session_id, display_text="秘密事件", source_message_ids=[6601]
    )
    response = isolated_client.delete(
        f"/api/memory/experiences/{experience_id}", params=_facts_scope(session_id)
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["state"] == "deleted" and body["idempotent"] is False
    shell = _exp_status(experience_id)
    assert shell["status"] == "deleted"
    assert shell["display_text"] == "", "recoverable narrative text must be scrubbed"
    assert shell["deleted_at"] is not None
    assert _json.loads(shell["semantic_json"]) == {}, (
        "semantic_json must remain syntactically valid JSON with no content"
    )


def test_s3d3_e7_delete_scrubs_linked_observation(isolated_client: TestClient):
    session_id = _session("s3d3-e7")
    experience_id, observation_id = _create_episodic(
        session_id=session_id, display_text="去了音乐会", source_message_ids=[6701]
    )
    response = isolated_client.delete(
        f"/api/memory/experiences/{experience_id}", params=_facts_scope(session_id)
    )
    assert response.status_code == 200, response.text
    obs = _db_rows(
        """SELECT status, display_text, semantic_json, topic_label_proposal,
                  semantic_fingerprint FROM memory_observations
            WHERE observation_id=?""",
        (observation_id,),
    )
    assert obs[0]["status"] == "deleted"
    assert obs[0]["display_text"] is None
    assert obs[0]["semantic_json"] is None
    assert obs[0]["topic_label_proposal"] is None
    assert obs[0]["semantic_fingerprint"] is not None, "non-content hash retained"


def test_s3d3_e8_delete_scrubs_sources_retains_receipts(
    isolated_client: TestClient,
):
    session_id = _session("s3d3-e8")
    experience_id, observation_id = _create_episodic(
        session_id=session_id, display_text="去看海了", source_message_ids=[6801]
    )
    response = isolated_client.delete(
        f"/api/memory/experiences/{experience_id}", params=_facts_scope(session_id)
    )
    assert response.status_code == 200, response.text
    sources = _db_rows(
        """SELECT source_message_id, conversation_id, source_fingerprint,
                  source_state, excerpt FROM memory_observation_sources
            WHERE observation_id=?""",
        (observation_id,),
    )
    assert len(sources) == 1
    assert sources[0]["source_message_id"] == 6801
    assert sources[0]["source_fingerprint"] == "fp-s3d2-6801"
    assert sources[0]["excerpt"] is None, "excerpt must be scrubbed"
    assert sources[0]["source_state"] == "present", (
        "source_state describes message provenance; delete must not change it"
    )


def test_s3d3_e9_repeated_delete_idempotent(isolated_client: TestClient):
    session_id = _session("s3d3-e9")
    experience_id, _ = _create_episodic(
        session_id=session_id, display_text="散步", source_message_ids=[6901]
    )
    first = isolated_client.delete(
        f"/api/memory/experiences/{experience_id}", params=_facts_scope(session_id)
    )
    assert first.status_code == 200, first.text
    second = isolated_client.delete(
        f"/api/memory/experiences/{experience_id}", params=_facts_scope(session_id)
    )
    assert second.status_code == 200, second.text
    assert second.json()["idempotent"] is True


def test_s3d3_e10_same_source_replay_cannot_resurrect(isolated_client: TestClient):
    """D12-style: old source receipt blocks recreation of a deleted experience."""
    session_id = _session("s3d3-e10")
    experience_id, _ = _create_episodic(
        session_id=session_id, display_text="去看演出", source_message_ids=[7001]
    )
    response = isolated_client.delete(
        f"/api/memory/experiences/{experience_id}", params=_facts_scope(session_id)
    )
    assert response.status_code == 200, response.text
    replay = _reconciler_apply(
        session_id=session_id,
        observations=[
            _s3d3_episodic_obs(ref="o1", display_text="去看演出", source_message_ids=[7001])
        ],
        operations=[
            {"op": "CREATE", "observation_ref": "o1", "fact_text": "去看演出",
             "reason_code": "contextual_event"}
        ],
        source_message_ids=[7001],
    )
    assert replay.get("replay") is True, "same old source must be blocked"
    rows = _db_rows(
        """SELECT status FROM experiences WHERE session_id=? AND status='active'""",
        (session_id,),
    )
    assert rows == [], "deleted experience must not resurrect"


def test_s3d3_e11_new_source_recreates(isolated_client: TestClient):
    session_id = _session("s3d3-e11")
    experience_id, _ = _create_episodic(
        session_id=session_id, display_text="去看演出", source_message_ids=[7101]
    )
    response = isolated_client.delete(
        f"/api/memory/experiences/{experience_id}", params=_facts_scope(session_id)
    )
    assert response.status_code == 200, response.text
    _reconciler_apply(
        session_id=session_id,
        observations=[
            _s3d3_episodic_obs(ref="o1", display_text="去看演出", source_message_ids=[7102])
        ],
        operations=[
            {"op": "CREATE", "observation_ref": "o1", "fact_text": "去看演出",
             "reason_code": "contextual_event"}
        ],
        source_message_ids=[7102],
    )
    active = _db_rows(
        """SELECT display_text FROM experiences
            WHERE session_id=? AND status='active'""",
        (session_id,),
    )
    assert len(active) == 1, "a genuinely new source may recreate the experience"


def test_s3d3_e12_delete_failure_rolls_back(isolated_client: TestClient, monkeypatch):
    session_id = _session("s3d3-e12")
    experience_id, observation_id = _create_episodic(
        session_id=session_id, display_text="远足", source_message_ids=[7201]
    )
    excerpt_before = _db_rows(
        "SELECT excerpt FROM memory_observation_sources WHERE observation_id=?",
        (observation_id,),
    )[0]["excerpt"]

    async def boom(*args, **kwargs):
        raise RuntimeError("injected mid-delete scrub failure")

    monkeypatch.setattr(
        "app.services.memory_v11.experiences._scrub_observation_sources", boom
    )
    response = isolated_client.delete(
        f"/api/memory/experiences/{experience_id}", params=_facts_scope(session_id)
    )
    assert response.status_code == 500, response.text
    assert response.json()["detail"]["code"] == "memory_operation_failed"

    shell = _exp_status(experience_id)
    assert shell["status"] == "active", "experience status must be preserved"
    assert shell["display_text"] == "远足"
    assert shell["deleted_at"] is None
    obs = _db_rows(
        """SELECT status, display_text, semantic_json FROM memory_observations
            WHERE observation_id=?""",
        (observation_id,),
    )
    assert obs[0]["status"] == "attached"
    assert obs[0]["display_text"] == "远足"
    sources = _db_rows(
        "SELECT excerpt FROM memory_observation_sources WHERE observation_id=?",
        (observation_id,),
    )
    assert sources[0]["excerpt"] == excerpt_before


def test_s3d3_e13_expired_status_visible(isolated_client: TestClient):
    session_id = _session("s3d3-e13")
    experience_id = _seed_experience(
        session_id=session_id, display_text="历史事件", status="expired"
    )
    listed = isolated_client.get("/api/memory/experiences", params=_facts_scope(session_id))
    assert listed.status_code == 200, listed.text
    items = listed.json()["experiences"]
    assert len(items) == 1
    assert items[0]["status"] == "expired" and items[0]["is_expired"] is True
    details = isolated_client.get(
        f"/api/memory/experiences/{experience_id}/details",
        params=_facts_scope(session_id),
    )
    assert details.status_code == 200, "expired history details remain accessible"
    assert details.json()["experience"]["is_expired"] is True


def test_s3d3_e14_active_past_expiry_is_expired_flag(isolated_client: TestClient):
    session_id = _session("s3d3-e14")
    experience_id = _seed_experience(
        session_id=session_id, display_text="过期但仍活跃",
        status="active", expires_at="2020-01-01T00:00:00+00:00",
    )
    listed = isolated_client.get("/api/memory/experiences", params=_facts_scope(session_id))
    assert listed.status_code == 200, listed.text
    item = listed.json()["experiences"][0]
    assert item["status"] == "active" and item["is_expired"] is True
    details = isolated_client.get(
        f"/api/memory/experiences/{experience_id}/details",
        params=_facts_scope(session_id),
    )
    assert details.json()["experience"]["is_expired"] is True


def test_s3d3_e15_deleted_hidden(isolated_client: TestClient):
    session_id = _session("s3d3-e15")
    experience_id = _seed_experience(
        session_id=session_id, display_text="已删除", status="deleted"
    )
    listed = isolated_client.get("/api/memory/experiences", params=_facts_scope(session_id))
    assert experience_id not in str(listed.json())
    details = isolated_client.get(
        f"/api/memory/experiences/{experience_id}/details",
        params=_facts_scope(session_id),
    )
    assert details.status_code == 404
    assert details.json()["detail"]["code"] == "experience_not_found"


def test_s3d3_e16_pending_source_delete_hidden(isolated_client: TestClient):
    session_id = _session("s3d3-e16")
    experience_id = _seed_experience(
        session_id=session_id, display_text="S4 预留", status="pending_source_delete"
    )
    listed = isolated_client.get("/api/memory/experiences", params=_facts_scope(session_id))
    assert listed.status_code == 200, listed.text
    assert experience_id not in str(listed.json())
    details = isolated_client.get(
        f"/api/memory/experiences/{experience_id}/details",
        params=_facts_scope(session_id),
    )
    assert details.status_code == 404
    assert details.json()["detail"]["code"] == "experience_not_found"


def test_s3d3_e17_delete_pending_conflict(isolated_client: TestClient):
    session_id = _session("s3d3-e17")
    experience_id = _seed_experience(
        session_id=session_id, display_text="S4 预留", status="pending_source_delete"
    )
    response = isolated_client.delete(
        f"/api/memory/experiences/{experience_id}", params=_facts_scope(session_id)
    )
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "experience_delete_pending"
    shell = _exp_status(experience_id)
    assert shell["status"] == "pending_source_delete", "conflict must not mutate"
    assert shell["display_text"] == "S4 预留"


def test_s3d3_e18_evidence_integrity_guard(isolated_client: TestClient):
    """Corrupt state: episodic observation linked to stable_fact_evidence."""
    session_id = _session("s3d3-e18")
    experience_id, observation_id = _create_episodic(
        session_id=session_id, display_text="非法证据事件", source_message_ids=[7301]
    )
    fact_id = _seed_fact(session_id=session_id, display_text="独立事实")
    _db_execute(
        """INSERT INTO stable_fact_evidence(
               fact_id, version_no, observation_id, evidence_role
           ) VALUES(?,1,?,'supporting')""",
        (fact_id, observation_id),
    )
    response = isolated_client.delete(
        f"/api/memory/experiences/{experience_id}", params=_facts_scope(session_id)
    )
    assert response.status_code == 500, response.text
    assert response.json()["detail"]["code"] == "experience_observation_conflict"
    assert "远足" not in response.text and "s3d2 excerpt" not in response.text
    shell = _exp_status(experience_id)
    assert shell["status"] == "active", "guard failure must not mutate experience"
    obs = _db_rows(
        "SELECT status, display_text FROM memory_observations WHERE observation_id=?",
        (observation_id,),
    )
    assert obs[0]["status"] == "attached" and obs[0]["display_text"] == "非法证据事件"
    sources = _db_rows(
        "SELECT excerpt FROM memory_observation_sources WHERE observation_id=?",
        (observation_id,),
    )
    assert sources[0]["excerpt"] == "s3d2 excerpt", "source excerpts must be untouched"


def test_s3d3_e19_semantic_json_parses_after_delete(isolated_client: TestClient):
    import json as _json

    session_id = _session("s3d3-e19")
    experience_id, _ = _create_episodic(
        session_id=session_id, display_text="删除后解析", source_message_ids=[7401]
    )
    response = isolated_client.delete(
        f"/api/memory/experiences/{experience_id}", params=_facts_scope(session_id)
    )
    assert response.status_code == 200, response.text
    shell = _exp_status(experience_id)
    parsed = _json.loads(shell["semantic_json"])
    assert parsed == {}
    assert shell["semantic_fingerprint"] is not None


# -- observation diagnostics / ignore ----------------------------------------


def test_s3d3_o1_scoped_diagnostics_list(isolated_client: TestClient):
    session_id = _session("s3d3-o1")
    marker = f"O1_OBS_{uuid4().hex[:8]}"
    _seed_candidate_observation(session_id=session_id, display_text=marker)
    _seed_candidate_observation(
        session_id=session_id, identity_mode="okabe", display_text="okabe 候选"
    )
    listed = isolated_client.get("/api/memory/observations", params=_facts_scope(session_id))
    assert listed.status_code == 200, listed.text
    body = listed.json()
    assert body["scope"] == _facts_scope(session_id)
    assert marker in str(body)
    assert "okabe 候选" not in str(body)


def test_s3d3_o2_all_statuses_represented(isolated_client: TestClient):
    session_id = _session("s3d3-o2")
    for status in ("candidate", "attached", "rejected", "ignored", "expired"):
        _seed_observation(session_id=session_id, display_text=f"状态-{status}", status=status)
    # Deleted observations were content-scrubbed by the delete lifecycle: they
    # must surface as display_text=null, never with reconstructed text.
    _seed_observation(session_id=session_id, display_text=None, status="deleted")
    listed = isolated_client.get("/api/memory/observations", params=_facts_scope(session_id))
    assert listed.status_code == 200, listed.text
    statuses = {o["status"] for o in listed.json()["observations"]}
    assert statuses == {"candidate", "attached", "rejected", "ignored", "deleted", "expired"}
    deleted_row = next(o for o in listed.json()["observations"] if o["status"] == "deleted")
    assert deleted_row["display_text"] is None, "scrubbed text must never resurface"


def test_s3d3_o3_pagination_deterministic_order(isolated_client: TestClient):
    session_id = _session("s3d3-o3")
    for i in range(3):
        _seed_candidate_observation(session_id=session_id, display_text=f"候选{i}")
    page1 = isolated_client.get(
        "/api/memory/observations",
        params={**_facts_scope(session_id), "limit": "2", "offset": "0"},
    )
    assert page1.status_code == 200, page1.text
    body = page1.json()
    assert len(body["observations"]) == 2
    assert body["pagination"] == {"limit": 2, "offset": 0, "total": 3, "has_more": True}
    page2 = isolated_client.get(
        "/api/memory/observations",
        params={**_facts_scope(session_id), "limit": "2", "offset": "2"},
    )
    assert page2.status_code == 200, page2.text
    body2 = page2.json()
    assert len(body2["observations"]) == 1
    assert body2["pagination"]["has_more"] is False
    all_ids = [o["observation_id"] for o in body["observations"] + body2["observations"]]
    assert len(set(all_ids)) == 3, "pages must not overlap"


def test_s3d3_o4_candidate_to_ignored(isolated_client: TestClient):
    session_id = _session("s3d3-o4")
    observation_id = _seed_candidate_observation(
        session_id=session_id, display_text="低置信候选"
    )
    response = isolated_client.post(
        f"/api/memory/observations/{observation_id}/ignore",
        params=_facts_scope(session_id),
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "ignored"
    status, _ = _obs_status(session_id, observation_id)
    assert status == "ignored"


def test_s3d3_o5_ignored_idempotent(isolated_client: TestClient):
    session_id = _session("s3d3-o5")
    observation_id = _seed_candidate_observation(
        session_id=session_id, display_text="重复忽略"
    )
    first = isolated_client.post(
        f"/api/memory/observations/{observation_id}/ignore",
        params=_facts_scope(session_id),
    )
    assert first.status_code == 200, first.text
    second = isolated_client.post(
        f"/api/memory/observations/{observation_id}/ignore",
        params=_facts_scope(session_id),
    )
    assert second.status_code == 200, second.text
    assert second.json()["idempotent"] is True


def test_s3d3_o6_non_candidate_statuses_409(isolated_client: TestClient):
    session_id = _session("s3d3-o6")
    for status in ("attached", "rejected", "deleted", "expired"):
        observation_id = _seed_observation(
            session_id=session_id, display_text=f"不可忽略-{status}", status=status
        )
        response = isolated_client.post(
            f"/api/memory/observations/{observation_id}/ignore",
            params=_facts_scope(session_id),
        )
        assert response.status_code == 409, response.text
        assert response.json()["detail"]["code"] == "observation_not_ignorable"
        after_status, _ = _obs_status(session_id, observation_id)
        assert after_status == status, "409 must not mutate"


def test_s3d3_o7_wrong_scope_404(isolated_client: TestClient):
    session_id = _session("s3d3-o7")
    observation_id = _seed_candidate_observation(
        session_id=session_id, display_text="属于 self"
    )
    for params in (
        {"session_id": session_id, "worldline": "steins_gate", "identity_mode": "okabe"},
        {"session_id": "someone-else", "worldline": "steins_gate", "identity_mode": "self"},
        {"session_id": session_id, "worldline": "beta", "identity_mode": "self"},
    ):
        response = isolated_client.post(
            f"/api/memory/observations/{observation_id}/ignore", params=params
        )
        assert response.status_code == 404, response.text
        assert response.json()["detail"]["code"] == "observation_not_found"
    status, _ = _obs_status(session_id, observation_id)
    assert status == "candidate"


def test_s3d3_o8_ignore_zero_side_effects(isolated_client: TestClient):
    session_id = _session("s3d3-o8")
    fact_id = _seed_fact(session_id=session_id, display_text="喜欢咖啡")
    topic_id = _seed_topic(session_id, "self", "动漫")
    _seed_experience(session_id=session_id, display_text="一个经历")
    observation_id = _seed_candidate_observation(
        session_id=session_id, display_text="仅候选"
    )
    before = {
        "facts": _db_rows("SELECT fact_id FROM stable_facts"),
        "versions": _db_rows("SELECT fact_id FROM stable_fact_versions"),
        "vectors": _db_rows("SELECT fact_id FROM stable_fact_version_embeddings"),
        "topics": _db_rows("SELECT topic_id FROM memory_topics"),
        "experiences": _db_rows("SELECT experience_id FROM experiences"),
        "sources": _db_rows("SELECT observation_id FROM memory_observation_sources"),
    }
    response = isolated_client.post(
        f"/api/memory/observations/{observation_id}/ignore",
        params=_facts_scope(session_id),
    )
    assert response.status_code == 200, response.text
    after = {
        "facts": _db_rows("SELECT fact_id FROM stable_facts"),
        "versions": _db_rows("SELECT fact_id FROM stable_fact_versions"),
        "vectors": _db_rows("SELECT fact_id FROM stable_fact_version_embeddings"),
        "topics": _db_rows("SELECT topic_id FROM memory_topics"),
        "experiences": _db_rows("SELECT experience_id FROM experiences"),
        "sources": _db_rows("SELECT observation_id FROM memory_observation_sources"),
    }
    assert before == after, "ignore must not touch facts/versions/vectors/topics/experiences/sources"
    assert fact_id and topic_id


def test_s3d3_o9_ignored_remains_visible_with_content(isolated_client: TestClient):
    session_id = _session("s3d3-o9")
    marker = f"O9_KEEP_{uuid4().hex[:8]}"
    observation_id = _seed_candidate_observation(
        session_id=session_id, display_text=marker
    )
    response = isolated_client.post(
        f"/api/memory/observations/{observation_id}/ignore",
        params=_facts_scope(session_id),
    )
    assert response.status_code == 200, response.text
    listed = isolated_client.get("/api/memory/observations", params=_facts_scope(session_id))
    assert listed.status_code == 200
    rows = [o for o in listed.json()["observations"] if o["observation_id"] == observation_id]
    assert len(rows) == 1, "ignored observation must remain visible"
    assert rows[0]["status"] == "ignored"
    assert rows[0]["display_text"] == marker, "diagnostic content must be retained"


def test_s3d3_o10_ignore_evidence_corruption_guard(isolated_client: TestClient):
    session_id = _session("s3d3-o10")
    observation_id = _seed_candidate_observation(
        session_id=session_id, display_text="被证据引用的候选"
    )
    fact_id = _seed_fact(session_id=session_id, display_text="独立事实")
    _db_execute(
        """INSERT INTO stable_fact_evidence(
               fact_id, version_no, observation_id, evidence_role
           ) VALUES(?,1,?,'supporting')""",
        (fact_id, observation_id),
    )
    response = isolated_client.post(
        f"/api/memory/observations/{observation_id}/ignore",
        params=_facts_scope(session_id),
    )
    assert response.status_code == 500, response.text
    assert response.json()["detail"]["code"] == "observation_state_conflict"
    status, _ = _obs_status(session_id, observation_id)
    assert status == "candidate", "guard failure must not mutate"


def test_s3d3_o11_ignore_experience_corruption_guard(isolated_client: TestClient):
    session_id = _session("s3d3-o11")
    observation_id = _seed_candidate_observation(
        session_id=session_id, display_text="被经历引用的候选"
    )
    _db_execute(
        """INSERT INTO experiences(
               experience_id, session_id, conversation_id, identity_mode,
               observation_id, display_text, semantic_json, semantic_fingerprint,
               confidence, status, expires_at, created_at, updated_at, deleted_at
           ) VALUES(?,?, 'conv-corrupt', 'self', ?, '非法经历', '{}', 'fp-x',
                    0.9, 'active', NULL, '2026-08-01T00:00:00+00:00',
                    '2026-08-01T00:00:00+00:00', NULL)""",
        (str(uuid4()), session_id, observation_id),
    )
    response = isolated_client.post(
        f"/api/memory/observations/{observation_id}/ignore",
        params=_facts_scope(session_id),
    )
    assert response.status_code == 500, response.text
    assert response.json()["detail"]["code"] == "observation_state_conflict"
    status, _ = _obs_status(session_id, observation_id)
    assert status == "candidate"


# -- S3D-3 REST envelopes ----------------------------------------------------


def test_s3d3_scope_envelopes_all_five_routes(isolated_client: TestClient):
    session_id = _session("s3d3-env")
    experience_id = _seed_experience(session_id=session_id, display_text="经历")
    observation_id = _seed_candidate_observation(
        session_id=session_id, display_text="候选"
    )
    cases = [
        (None, None, "missing_worldline"),
        ("", None, "missing_worldline"),
        ("bogus", None, "invalid_worldline"),
        ("steins_gate", None, "missing_identity_mode"),
        ("steins_gate", "", "missing_identity_mode"),
        ("steins_gate", "bogus", "invalid_identity_mode"),
    ]
    for worldline, identity_mode, expected in cases:
        params = {"session_id": session_id}
        if worldline is not None:
            params["worldline"] = worldline
        if identity_mode is not None:
            params["identity_mode"] = identity_mode
        for method, url in (
            ("GET", "/api/memory/experiences"),
            ("GET", f"/api/memory/experiences/{experience_id}/details"),
            ("DELETE", f"/api/memory/experiences/{experience_id}"),
            ("GET", "/api/memory/observations"),
            ("POST", f"/api/memory/observations/{observation_id}/ignore"),
        ):
            response = getattr(isolated_client, method.lower())(url, params=params)
            assert response.status_code == 422, (method, url, response.text)
            assert response.json()["detail"]["code"] == expected, (method, url)

    for params in (
        {"worldline": "steins_gate", "identity_mode": "self"},
        {"worldline": "steins_gate", "identity_mode": "self", "session_id": ""},
    ):
        for method, url in (
            ("GET", "/api/memory/experiences"),
            ("GET", f"/api/memory/experiences/{experience_id}/details"),
            ("DELETE", f"/api/memory/experiences/{experience_id}"),
            ("GET", "/api/memory/observations"),
            ("POST", f"/api/memory/observations/{observation_id}/ignore"),
        ):
            response = getattr(isolated_client, method.lower())(url, params=params)
            assert response.status_code == 422, (method, url)
            assert response.json()["detail"]["code"] == "empty_session_id"


def test_s3d3_pagination_envelopes(isolated_client: TestClient):
    session_id = _session("s3d3-paging")
    _seed_candidate_observation(session_id=session_id, display_text="候选")
    default_page = isolated_client.get(
        "/api/memory/observations", params=_facts_scope(session_id)
    )
    assert default_page.status_code == 200, default_page.text
    assert default_page.json()["pagination"]["limit"] == 20
    for bad in ("0", "101", "abc", "1.5", "-3"):
        response = isolated_client.get(
            "/api/memory/observations",
            params={**_facts_scope(session_id), "limit": bad},
        )
        assert response.status_code == 422, response.text
        assert response.json()["detail"]["code"] == "invalid_limit", bad
    for bad in ("-1", "abc"):
        response = isolated_client.get(
            "/api/memory/observations",
            params={**_facts_scope(session_id), "offset": bad},
        )
        assert response.status_code == 422, response.text
        assert response.json()["detail"]["code"] == "invalid_offset", bad
    ok_edges = isolated_client.get(
        "/api/memory/observations",
        params={**_facts_scope(session_id), "limit": "1", "offset": "0"},
    )
    assert ok_edges.status_code == 200, ok_edges.text
    assert ok_edges.json()["pagination"]["limit"] == 1


def test_s3d3_zero_provider_all_five_routes(
    isolated_client: TestClient, monkeypatch
):
    import app.services.credentials as cred_mod
    import app.services.provider_runtime as pr_mod

    def boom(*args, **kwargs):
        raise AssertionError("provider must not be called")

    monkeypatch.setattr(pr_mod.provider_registry, "snapshot", boom)
    monkeypatch.setattr(cred_mod.credential_store, "get", boom)

    session_id = _session("s3d3-noprov")
    experience_id, _ = _create_episodic(
        session_id=session_id, display_text="零依赖经历", source_message_ids=[7501]
    )
    observation_id = _seed_candidate_observation(
        session_id=session_id, display_text="零依赖候选"
    )
    assert isolated_client.get(
        "/api/memory/experiences", params=_facts_scope(session_id)
    ).status_code == 200
    assert isolated_client.get(
        f"/api/memory/experiences/{experience_id}/details", params=_facts_scope(session_id)
    ).status_code == 200
    assert isolated_client.delete(
        f"/api/memory/experiences/{experience_id}", params=_facts_scope(session_id)
    ).status_code == 200
    assert isolated_client.get(
        "/api/memory/observations", params=_facts_scope(session_id)
    ).status_code == 200
    assert isolated_client.post(
        f"/api/memory/observations/{observation_id}/ignore",
        params=_facts_scope(session_id),
    ).status_code == 200


def test_s3d3_o12_attached_with_real_evidence_409(isolated_client: TestClient):
    """REWORK P1: attached observation with REAL stable_fact_evidence is
    legitimate state → 409 observation_not_ignorable, never 500 corruption."""
    session_id = _session("s3d3-o12")
    fact_id, observation_id = _seed_fact_via_reconciler(
        session_id=session_id, display_text="喜欢咖啡", source_message_id=7601,
        topic_label="饮食",
    )
    attached = _db_rows(
        "SELECT status FROM memory_observations WHERE observation_id=?",
        (observation_id,),
    )
    assert attached[0]["status"] == "attached"
    evidence = _db_rows(
        "SELECT COUNT(*) AS n FROM stable_fact_evidence WHERE observation_id=?",
        (observation_id,),
    )
    assert int(evidence[0]["n"]) == 1, "attached observation must carry real evidence"
    versions_before = _db_rows(
        "SELECT version_no FROM stable_fact_versions WHERE fact_id=?", (fact_id,)
    )

    response = isolated_client.post(
        f"/api/memory/observations/{observation_id}/ignore",
        params=_facts_scope(session_id),
    )
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "observation_not_ignorable"

    status, _ = _obs_status(session_id, observation_id)
    assert status == "attached", "409 must not mutate the observation"
    evidence_after = _db_rows(
        "SELECT COUNT(*) AS n FROM stable_fact_evidence WHERE observation_id=?",
        (observation_id,),
    )
    assert int(evidence_after[0]["n"]) == 1, "evidence must survive"
    versions_after = _db_rows(
        "SELECT version_no FROM stable_fact_versions WHERE fact_id=?", (fact_id,)
    )
    assert versions_before == versions_after, "fact/version must be unchanged"
    facts = _db_rows(
        "SELECT state FROM stable_facts WHERE fact_id=?", (fact_id,)
    )
    assert facts[0]["state"] == "active"


# ---------------------------------------------------------------------------
# MEMORY-V11-S3F-2B: scoped stable-fact REST pagination.
# ---------------------------------------------------------------------------


def _seed_stable_facts(
    session_id: str, texts: list[str], *, identity_mode: str = "self"
) -> None:
    import asyncio

    from app.services.memory_v11.repository import create_stable_fact

    async def _seed() -> None:
        for text in texts:
            await create_stable_fact(
                session_id=session_id,
                worldline="steins_gate",
                identity_mode=identity_mode,
                display_text=text,
                semantic_json={"subject": "user", "predicate": "x", "object": text[:8]},
            )

    asyncio.run(_seed())


def _facts_params(session_id: str, **extra) -> dict:
    params = {
        "session_id": session_id,
        "worldline": "steins_gate",
        "identity_mode": "self",
    }
    params.update(extra)
    return params


def test_s3f2b_default_pagination_envelope(isolated_client: TestClient):
    """Explicit v11 default pagination = limit 20 / offset 0 with envelope."""
    session_id = _session("s3f2b-default")
    _seed_stable_facts(session_id, ["事实一", "事实二"])
    resp = isolated_client.get(
        "/api/memory/facts", params=_facts_params(session_id)
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "facts" in body and "scope" in body
    assert body["pagination"] == {
        "limit": 20,
        "offset": 0,
        "total": 2,
        "has_more": False,
    }
    assert sorted(f["display_text"] for f in body["facts"]) == ["事实一", "事实二"]


def test_s3f2b_limit_one_and_has_more(isolated_client: TestClient):
    session_id = _session("s3f2b-limit")
    _seed_stable_facts(session_id, [f"事实{i}" for i in range(5)])
    resp = isolated_client.get(
        "/api/memory/facts", params=_facts_params(session_id, limit="1")
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["facts"]) == 1
    assert body["pagination"] == {"limit": 1, "offset": 0, "total": 5, "has_more": True}


def test_s3f2b_offset_page_continuity(isolated_client: TestClient):
    session_id = _session("s3f2b-offset")
    _seed_stable_facts(session_id, [f"事实{i}" for i in range(5)])
    page1 = isolated_client.get(
        "/api/memory/facts", params=_facts_params(session_id, limit="2", offset="0")
    ).json()
    page2 = isolated_client.get(
        "/api/memory/facts", params=_facts_params(session_id, limit="2", offset="2")
    ).json()
    page3 = isolated_client.get(
        "/api/memory/facts", params=_facts_params(session_id, limit="2", offset="4")
    ).json()
    ids1 = {f["fact_id"] for f in page1["facts"]}
    ids2 = {f["fact_id"] for f in page2["facts"]}
    ids3 = {f["fact_id"] for f in page3["facts"]}
    assert len(page1["facts"]) == 2 and page1["pagination"]["has_more"] is True
    assert len(page2["facts"]) == 2 and page2["pagination"]["has_more"] is True
    assert len(page3["facts"]) == 1 and page3["pagination"]["has_more"] is False
    assert ids1.isdisjoint(ids2) and ids2.isdisjoint(ids3)


def test_s3f2b_query_filter_before_page(isolated_client: TestClient):
    """Query-before-page: page 1 contains only matches; total == matching count."""
    session_id = _session("s3f2b-query")
    # Seed non-matches first (newer → would fill an unfiltered first page).
    _seed_stable_facts(session_id, ["无关内容甲", "无关内容乙", "无关内容丙"])
    _seed_stable_facts(session_id, ["目标事实Z", "目标事实Y"])
    resp = isolated_client.get(
        "/api/memory/facts",
        params=_facts_params(session_id, query="目标", limit="1", offset="0"),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["facts"]) == 1
    assert "目标" in body["facts"][0]["display_text"]
    assert body["pagination"]["total"] == 2
    assert body["pagination"]["has_more"] is True


def test_s3f2b_query_literal_percent_not_wildcard(isolated_client: TestClient):
    """Caller % must not become a LIKE pattern operator."""
    session_id = _session("s3f2b-literal")
    _seed_stable_facts(session_id, ["进度100%完成", "普通事实"])
    resp = isolated_client.get(
        "/api/memory/facts",
        params=_facts_params(session_id, query="%", limit="10"),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["pagination"]["total"] == 1, body["pagination"]
    assert body["facts"][0]["display_text"] == "进度100%完成"


def test_s3f2b_topic_filter_total(isolated_client: TestClient):
    from app.services.memory_v11 import repository as repo

    session_id = _session("s3f2b-topic")
    _seed_stable_facts(session_id, [f"咖啡事实{i}" for i in range(5)])
    _seed_stable_facts(session_id, ["茶事实一", "茶事实二"])
    import asyncio

    async def _topic() -> str:
        from app.services.memory_v11.topics import resolve_or_create_topic
        from app.db import get_db

        db = await get_db("steins_gate", "memory")
        try:
            await db.execute("BEGIN IMMEDIATE")
            topic_id = await resolve_or_create_topic(
                db, session_id=session_id, identity_mode="self",
                display_label="茶", normalized_label="茶",
            )
            await db.execute(
                """UPDATE stable_facts
                    SET topic_id=?
                  WHERE session_id=? AND fact_id IN (
                        SELECT f.fact_id FROM stable_facts f
                        JOIN stable_fact_versions v
                          ON v.fact_id=f.fact_id
                         AND v.version_no=f.active_version
                       WHERE f.session_id=? AND v.display_text LIKE '茶%'
                  )""",
                (topic_id, session_id, session_id),
            )
            await db.commit()
            return topic_id
        finally:
            await db.close()

    topic_id = asyncio.run(_topic())
    resp = isolated_client.get(
        "/api/memory/facts",
        params=_facts_params(session_id, topic_id=topic_id, limit="1"),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["pagination"]["total"] == 2, "total must reflect the topic filter"


def test_s3f2b_pinned_filter_total(isolated_client: TestClient):
    session_id = _session("s3f2b-pinned")
    _seed_stable_facts(session_id, ["普通事实一", "普通事实二"])
    import asyncio

    async def _pin() -> None:
        from app.db import get_db

        db = await get_db("steins_gate", "memory")
        try:
            await db.execute(
                """UPDATE stable_facts SET is_pinned=1
                    WHERE fact_id=(SELECT fact_id FROM stable_facts
                                    WHERE session_id=? ORDER BY fact_id LIMIT 1)""",
                (session_id,),
            )
            await db.commit()
        finally:
            await db.close()

    asyncio.run(_pin())
    resp = isolated_client.get(
        "/api/memory/facts",
        params=_facts_params(session_id, pinned_only="true", limit="10"),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["pagination"]["total"] == 1
    assert all(f["is_pinned"] is True for f in body["facts"])


def test_s3f2b_deterministic_fact_id_tie_break(isolated_client: TestClient):
    """Same is_pinned + same updated_at → fact_id ASC order."""
    session_id = _session("s3f2b-tie")
    _seed_stable_facts(session_id, ["并列事实A", "并列事实B", "并列事实C"])
    import asyncio

    async def _equalize() -> None:
        from app.db import get_db

        db = await get_db("steins_gate", "memory")
        try:
            await db.execute(
                "UPDATE stable_facts SET updated_at=? WHERE session_id=?",
                ("2026-01-01T00:00:00+00:00", session_id),
            )
            await db.commit()
        finally:
            await db.close()

    asyncio.run(_equalize())
    resp = isolated_client.get(
        "/api/memory/facts",
        params=_facts_params(session_id, limit="10"),
    )
    assert resp.status_code == 200, resp.text
    ids = [f["fact_id"] for f in resp.json()["facts"]]
    assert ids == sorted(ids), "fact_id ASC tie-break required"


def test_s3f2b_deleted_fact_excluded(isolated_client: TestClient):
    session_id = _session("s3f2b-deleted")
    _seed_stable_facts(session_id, ["保留事实", "待删除事实"])
    import asyncio

    async def _delete() -> str:
        from app.db import get_db

        db = await get_db("steins_gate", "memory")
        try:
            row = await (
                await db.execute(
                    "SELECT fact_id FROM stable_facts WHERE session_id=? ORDER BY fact_id LIMIT 1",
                    (session_id,),
                )
            ).fetchone()
            deleted_id = str(row["fact_id"])
            await db.execute(
                "UPDATE stable_facts SET state='deleted' WHERE fact_id=?",
                (deleted_id,),
            )
            await db.commit()
            return deleted_id
        finally:
            await db.close()

    deleted_id = asyncio.run(_delete())
    resp = isolated_client.get(
        "/api/memory/facts", params=_facts_params(session_id, limit="10")
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["pagination"]["total"] == 1
    assert len(body["facts"]) == 1
    assert body["facts"][0]["fact_id"] != deleted_id, "deleted fact must be excluded"


def test_s3f2b_invalid_limit_offset_422(isolated_client: TestClient):
    session_id = _session("s3f2b-invalid")
    _seed_stable_facts(session_id, ["事实"])
    cases = [
        ("limit", "0", "invalid_limit"),
        ("limit", "101", "invalid_limit"),
        ("limit", "-1", "invalid_limit"),
        ("limit", "abc", "invalid_limit"),
        ("limit", "1.5", "invalid_limit"),
        ("offset", "-1", "invalid_offset"),
        ("offset", "abc", "invalid_offset"),
    ]
    for name, value, code in cases:
        resp = isolated_client.get(
            "/api/memory/facts",
            params=_facts_params(session_id, **{name: value}),
        )
        assert resp.status_code == 422, (name, value, resp.text)
        assert resp.json()["detail"]["code"] == code, (name, value)


def test_s3f2b_okabe_isolation_paginated(isolated_client: TestClient):
    session_id = _session("s3f2b-isolation")
    _seed_stable_facts(session_id, ["self 独有事实"], identity_mode="self")
    _seed_stable_facts(session_id, ["okabe 独有事实"], identity_mode="okabe")
    self_resp = isolated_client.get(
        "/api/memory/facts", params=_facts_params(session_id, limit="10")
    )
    okabe_resp = isolated_client.get(
        "/api/memory/facts",
        params={
            "session_id": session_id,
            "worldline": "steins_gate",
            "identity_mode": "okabe",
            "limit": "10",
        },
    )
    assert self_resp.json()["pagination"]["total"] == 1
    assert okabe_resp.json()["pagination"]["total"] == 1
    assert "okabe 独有事实" not in [
        f["display_text"] for f in self_resp.json()["facts"]
    ]
    assert "self 独有事实" not in [
        f["display_text"] for f in okabe_resp.json()["facts"]
    ]


def test_s3f2b_legacy_path_no_pagination_key(isolated_client: TestClient):
    session_id = _session("s3f2b-legacy")
    response = isolated_client.get(
        "/api/memory/facts",
        params={"session_id": session_id, "worldline": "steins_gate"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert "local" in body or "shared" in body, body
    assert "pagination" not in body, "legacy ledger must not gain a pagination key"
    assert "facts" not in body, "legacy ledger keeps its own shape"
