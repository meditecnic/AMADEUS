"""S4-ARCHIVE Gate 2 RED-first probes: audited additive Archive seams.

Contract: .scratch/memory-orrery/issues/S4-ARCHIVE-memory-governance.md §2.3
Gap closure under audit (additive only; ordering/admission/scope unchanged):

1. Facts browse payload must add ``updated_at`` (parent contract field).
2. Experiences browse must expose ``is_pinned`` and accept literal ``query``
   plus ``pinned_only`` with filter-then-page totals; deleted and
   ``pending_source_delete`` rows stay hidden under every filter set.
3. One minimal exact-scope presentation mutation for Experience
   ``is_pinned`` only; no text editor, no version history invented.
4. v11 status exposes a bounded failed-jobs surface whose ``retryable``
   flag is server-owned (Archive never infers retry from error text).

These probes are written RED-first: they must fail against the Gate 1
baseline (e226165) and go green only under the Gate 2 minimal fix.
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


class _ArchiveDeterministicEmbedder(EmbeddingAdapter):
    """Offline EmbeddingAdapter stub — never loads a real model in tests."""

    def __init__(self) -> None:
        super().__init__(model_name="test-archive-384", dimensions=384)

    async def encode_passage(self, text: str) -> list[float]:
        return _unit_vector(f"passage:{text}")

    async def encode_query(self, text: str) -> list[float]:
        return _unit_vector(f"query:{text}")


@pytest.fixture
def isolated_client(tmp_path, monkeypatch, isolated_provider_credentials):
    from app.db import reset_initialization_cache
    from app.main import app
    from app.services.memory import memory_service

    monkeypatch.delenv("AMADEUS_DB_PATH", raising=False)
    monkeypatch.setenv("AMADEUS_DATA_DIR", str(tmp_path))
    reset_initialization_cache()
    monkeypatch.setattr(memory_service, "embedder", _ArchiveDeterministicEmbedder())
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


def _scope(session_id: str, identity_mode: str = "self", **extra) -> dict[str, str]:
    params: dict[str, str] = {
        "session_id": session_id,
        "worldline": "steins_gate",
        "identity_mode": identity_mode,
    }
    params.update(extra)
    return params


def _seed_fact(session_id: str, display_text: str, identity_mode: str = "self") -> str:
    import asyncio

    from app.services.memory_v11.repository import create_stable_fact

    async def _insert() -> str:
        row = await create_stable_fact(
            session_id=session_id,
            worldline="steins_gate",
            identity_mode=identity_mode,
            display_text=display_text,
            semantic_json={"subject": "user", "relation": "marker", "object": display_text[:16]},
        )
        return str(row["fact_id"])

    return asyncio.run(_insert())


def _seed_experience(
    *,
    session_id: str,
    identity_mode: str = "self",
    display_text: str,
    status: str = "active",
    is_pinned: bool = False,
) -> str:
    """Seed one v11 experience + observation row (source_state=present)."""
    import asyncio

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
                    "conv-archive-g2",
                    f"fp-{observation_id[:8]}",
                    now,
                ),
            )
            await db.execute(
                """INSERT INTO experiences(
                       experience_id, session_id, conversation_id, identity_mode,
                       observation_id, display_text, semantic_json, semantic_fingerprint,
                       confidence, status, expires_at, created_at, updated_at, deleted_at,
                       is_pinned
                   ) VALUES(?,?,?,?,?,?,?,?,0.9,?,?,?,?,NULL,?)""",
                (
                    experience_id,
                    session_id,
                    "conv-archive-g2",
                    identity_mode,
                    observation_id,
                    display_text,
                    json.dumps(semantic, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                    fingerprint,
                    status,
                    None,
                    now,
                    now,
                    1 if is_pinned else 0,
                ),
            )
            await db.commit()
            return experience_id
        finally:
            await db.close()

    return asyncio.run(_insert())


def _seed_failed_job(
    session_id: str,
    identity_mode: str = "self",
    *,
    attempt_count: int = 3,
    last_error_code: str = "provider_unavailable",
    updated_at: str = "2026-08-02T00:00:00+00:00",
) -> str:
    import asyncio

    from app.db import get_db

    async def _insert() -> str:
        db = await get_db("steins_gate", "memory")
        try:
            job_id = str(uuid4())
            await db.execute(
                """INSERT INTO memory_ingest_jobs(
                       job_id, session_id, conversation_id, identity_mode,
                       source_message_id, pipeline_version, provider_id, model_id,
                       state, attempt_count, last_error_code, created_at, updated_at
                   ) VALUES(?,?,?,?,?, 'memory-v11', 'test-provider', 'test-model',
                            'failed', ?, ?, ?, ?)""",
                (
                    job_id,
                    session_id,
                    "conv-archive-g2",
                    identity_mode,
                    int(uuid4().int % 10**9),
                    attempt_count,
                    last_error_code,
                    updated_at,
                    updated_at,
                ),
            )
            await db.commit()
            return job_id
        finally:
            await db.close()

    return asyncio.run(_insert())


# ---------------------------------------------------------------------------
# Gap 2: Facts browse adds updated_at (additive; ordering unchanged)
# ---------------------------------------------------------------------------


def test_gate2_facts_browse_rows_include_updated_at(isolated_client: TestClient):
    session_id = _session("g2-facts-updated")
    _seed_fact(session_id, "档案事实甲")
    resp = isolated_client.get("/api/memory/facts", params=_scope(session_id))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["facts"]) == 1
    row = body["facts"][0]
    assert "updated_at" in row, "parent contract requires updated_at in facts browse"
    assert isinstance(row["updated_at"], str) and row["updated_at"], row
    # Additive only: existing keys and ordering survive.
    assert row["fact_id"] and row["display_text"] == "档案事实甲"
    assert row["version_no"] == 1 and row["active_version"] == 1


# ---------------------------------------------------------------------------
# Gap 3+4: Experiences browse adds is_pinned / query / pinned_only
# ---------------------------------------------------------------------------


def test_gate2_experiences_browse_rows_include_is_pinned(isolated_client: TestClient):
    session_id = _session("g2-exp-pinned-field")
    _seed_experience(session_id=session_id, display_text="经历甲", is_pinned=True)
    _seed_experience(session_id=session_id, display_text="经历乙", is_pinned=False)
    resp = isolated_client.get("/api/memory/experiences", params=_scope(session_id))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["experiences"]) == 2
    pinned = {row["display_text"]: row["is_pinned"] for row in body["experiences"]}
    assert pinned == {"经历甲": True, "经历乙": False}, pinned


def test_gate2_experiences_query_filter_then_page_total(isolated_client: TestClient):
    session_id = _session("g2-exp-query")
    # Non-matches first (newer rows would otherwise fill an unfiltered page).
    _seed_experience(session_id=session_id, display_text="无关经历甲")
    _seed_experience(session_id=session_id, display_text="无关经历乙")
    _seed_experience(session_id=session_id, display_text="目标经历Z")
    _seed_experience(session_id=session_id, display_text="目标经历Y")
    resp = isolated_client.get(
        "/api/memory/experiences",
        params=_scope(session_id, **{"query": "目标", "limit": "1", "offset": "0"}),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["experiences"]) == 1
    assert "目标" in body["experiences"][0]["display_text"]
    assert body["pagination"]["total"] == 2, "total must reflect the query filter"
    assert body["pagination"]["has_more"] is True


def test_gate2_experiences_query_literal_percent_not_wildcard(isolated_client: TestClient):
    session_id = _session("g2-exp-literal")
    _seed_experience(session_id=session_id, display_text="进度100%完成")
    _seed_experience(session_id=session_id, display_text="普通经历")
    resp = isolated_client.get(
        "/api/memory/experiences",
        params=_scope(session_id, **{"query": "%", "limit": "10"}),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["pagination"]["total"] == 1, body["pagination"]
    assert body["experiences"][0]["display_text"] == "进度100%完成"


def test_gate2_experiences_pinned_only_filter_total(isolated_client: TestClient):
    session_id = _session("g2-exp-pinned-only")
    _seed_experience(session_id=session_id, display_text="置顶经历", is_pinned=True)
    _seed_experience(session_id=session_id, display_text="普通经历一")
    _seed_experience(session_id=session_id, display_text="普通经历二")
    resp = isolated_client.get(
        "/api/memory/experiences",
        params=_scope(session_id, **{"pinned_only": "true", "limit": "10"}),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["pagination"]["total"] == 1
    assert body["experiences"][0]["display_text"] == "置顶经历"
    assert body["experiences"][0]["is_pinned"] is True


def test_gate2_experiences_filters_hide_deleted_and_pending(isolated_client: TestClient):
    session_id = _session("g2-exp-hidden")
    _seed_experience(session_id=session_id, display_text="目标可见经历")
    _seed_experience(session_id=session_id, display_text="目标已删除经历", status="deleted")
    _seed_experience(
        session_id=session_id,
        display_text="目标待清理经历",
        status="pending_source_delete",
    )
    resp = isolated_client.get(
        "/api/memory/experiences",
        params=_scope(session_id, **{"query": "目标", "limit": "10"}),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["pagination"]["total"] == 1
    assert [row["display_text"] for row in body["experiences"]] == ["目标可见经历"]


def test_gate2_experience_details_include_is_pinned(isolated_client: TestClient):
    session_id = _session("g2-exp-details-pin")
    experience_id = _seed_experience(
        session_id=session_id, display_text="详情置顶经历", is_pinned=True
    )
    resp = isolated_client.get(
        f"/api/memory/experiences/{experience_id}/details", params=_scope(session_id)
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["experience"]["is_pinned"] is True


# ---------------------------------------------------------------------------
# Gap 3: Experience presentation mutation — is_pinned only
# ---------------------------------------------------------------------------


def test_gate2_experience_presentation_pin_toggle(isolated_client: TestClient):
    session_id = _session("g2-exp-mutation")
    experience_id = _seed_experience(session_id=session_id, display_text="待置顶经历")
    resp = isolated_client.patch(
        f"/api/memory/experiences/{experience_id}/presentation",
        params=_scope(session_id),
        json={"is_pinned": True},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["experience_id"] == experience_id
    assert body["is_pinned"] is True
    assert body["scope"] == {
        "session_id": session_id,
        "worldline": "steins_gate",
        "identity_mode": "self",
    }

    # Browse reflects the presentation change; text content is untouched.
    listed = isolated_client.get("/api/memory/experiences", params=_scope(session_id))
    rows = listed.json()["experiences"]
    assert len(rows) == 1
    assert rows[0]["is_pinned"] is True
    assert rows[0]["display_text"] == "待置顶经历"

    # Unpin again — presentation authority is reversible.
    unpin = isolated_client.patch(
        f"/api/memory/experiences/{experience_id}/presentation",
        params=_scope(session_id),
        json={"is_pinned": False},
    )
    assert unpin.status_code == 200, unpin.text
    assert unpin.json()["is_pinned"] is False


def test_gate2_experience_presentation_body_validation(isolated_client: TestClient):
    session_id = _session("g2-exp-mutation-body")
    experience_id = _seed_experience(session_id=session_id, display_text="校验经历")
    base = f"/api/memory/experiences/{experience_id}/presentation"

    extras = isolated_client.patch(
        base, params=_scope(session_id), json={"is_pinned": True, "display_text": "改文"}
    )
    assert extras.status_code == 422
    assert extras.json()["detail"]["code"] == "extra_fields_forbidden"

    empty = isolated_client.patch(base, params=_scope(session_id), json={})
    assert empty.status_code == 422
    assert empty.json()["detail"]["code"] == "missing_presentation_fields"

    invalid = isolated_client.patch(
        base, params=_scope(session_id), json={"is_pinned": "yes"}
    )
    assert invalid.status_code == 422
    assert invalid.json()["detail"]["code"] == "invalid_is_pinned"

    # No text editor is authorized: display_text never mutates via this seam.
    listed = isolated_client.get("/api/memory/experiences", params=_scope(session_id))
    assert listed.json()["experiences"][0]["display_text"] == "校验经历"
    assert listed.json()["experiences"][0]["is_pinned"] is False


def test_gate2_experience_presentation_scope_mismatch_404(isolated_client: TestClient):
    session_id = _session("g2-exp-mutation-scope")
    experience_id = _seed_experience(session_id=session_id, display_text="隔离经历")
    # Wrong identity_mode: 404 that never discloses cross-scope existence.
    resp = isolated_client.patch(
        f"/api/memory/experiences/{experience_id}/presentation",
        params=_scope(session_id, identity_mode="okabe"),
        json={"is_pinned": True},
    )
    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "experience_not_found"


def test_gate2_experience_presentation_deleted_404(isolated_client: TestClient):
    session_id = _session("g2-exp-mutation-deleted")
    experience_id = _seed_experience(
        session_id=session_id, display_text="已删经历", status="deleted"
    )
    resp = isolated_client.patch(
        f"/api/memory/experiences/{experience_id}/presentation",
        params=_scope(session_id),
        json={"is_pinned": True},
    )
    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "experience_not_found"


# ---------------------------------------------------------------------------
# Processing: bounded failed-jobs surface with server-owned retryable
# ---------------------------------------------------------------------------


def test_gate2_status_failed_jobs_exact_field_contract(isolated_client: TestClient):
    """Contract §16 C3: item fields ONLY job_id, last_error_code,
    attempt_count, updated_at, retryable (server-owned). ``state`` must
    never be exposed — the list is definitionally failed jobs."""
    session_id = _session("g2-status-failed")
    job_id = _seed_failed_job(session_id)
    resp = isolated_client.get("/api/memory/status", params=_scope(session_id))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["failed_count"] == 1
    failed_jobs = body.get("failed_jobs")
    assert isinstance(failed_jobs, list) and len(failed_jobs) == 1, body
    job = failed_jobs[0]
    assert job["job_id"] == job_id
    assert set(job) == {
        "job_id",
        "last_error_code",
        "attempt_count",
        "updated_at",
        "retryable",
    }, job
    assert "state" not in job
    assert isinstance(job["updated_at"], str) and job["updated_at"]
    assert job["attempt_count"] == 3
    assert job["last_error_code"] == "provider_unavailable"
    # Server-owned eligibility flag: Archive must never infer retryability
    # from error text.
    assert isinstance(job["retryable"], bool)


def test_gate2_status_failed_jobs_capped_at_eight_ordered(isolated_client: TestClient):
    """Contract §16 C3: max 8 items, ordered updated_at DESC, job_id ASC."""
    session_id = _session("g2-status-cap")
    seeded: list[tuple[str, str]] = []
    for index in range(8):
        stamp = f"2026-08-02T00:00:0{index:02d}+00:00"
        seeded.append((_seed_failed_job(session_id, updated_at=stamp), stamp))
    # Newest pair shares one timestamp: job_id ASC must break the tie.
    tie_stamp = "2026-08-02T00:00:08+00:00"
    seeded.append((_seed_failed_job(session_id, updated_at=tie_stamp), tie_stamp))
    seeded.append((_seed_failed_job(session_id, updated_at=tie_stamp), tie_stamp))

    resp = isolated_client.get("/api/memory/status", params=_scope(session_id))
    assert resp.status_code == 200, resp.text
    jobs = resp.json()["failed_jobs"]
    assert isinstance(jobs, list) and len(jobs) == 8, jobs

    by_id = sorted(seeded, key=lambda item: item[0])
    ordered = sorted(by_id, key=lambda item: item[1], reverse=True)
    expected_ids = [job_id for job_id, _ in ordered][:8]
    assert [job["job_id"] for job in jobs] == expected_ids

    stamp_by_id = dict(seeded)
    for job in jobs:
        assert job["updated_at"] == stamp_by_id[job["job_id"]]


def _fail_job_through_authority(
    session_id: str,
    *,
    permanent: bool,
    attempts: int = 1,
) -> str:
    """Drive a REAL job to terminal failure through the ingest pipeline
    authority (claim/fail), so the persisted retryable flag is exercised."""
    import asyncio

    from app.db import get_db
    from app.services.memory_v11 import jobs as memory_jobs

    async def _drive() -> str:
        job = await memory_jobs.enqueue_memory_job(
            session_id=session_id,
            worldline="steins_gate",
            conversation_id="conv-archive-g2",
            identity_mode="self",
            source_message_id=int(uuid4().int % 10**9),
            pipeline_version="memory-v11-1",
            provider_id="test-provider",
            model_id="test-model",
        )
        job_id = job.job_id if hasattr(job, "job_id") else job["job_id"]
        for _ in range(attempts):
            claimed = await memory_jobs.claim_memory_job(
                job_id=job_id, lease_owner="worker-g2",
            )
            assert claimed is not None
            await memory_jobs.mark_memory_job_failed(
                job_id=job_id,
                error_code="validation_failed" if permanent else "provider_unavailable",
                permanent=permanent,
            )
            if not permanent:
                db = await get_db("steins_gate", "memory")
                try:
                    await db.execute(
                        "UPDATE memory_ingest_jobs SET next_attempt_at=NULL WHERE job_id=?",
                        (job_id,),
                    )
                    await db.commit()
                finally:
                    await db.close()
        return job_id

    return asyncio.run(_drive())


def test_gate2_failed_jobs_retryable_from_persisted_authority(isolated_client: TestClient):
    """§16 C3 rework: ``retryable`` reads the PERSISTED per-job authority —
    permanent validation failure → false; exhausted transient failure → true.
    Never a hard-coded constant."""
    session_id = _session("g2-status-authority")
    _fail_job_through_authority(session_id, permanent=True)
    _fail_job_through_authority(session_id, permanent=False, attempts=3)

    resp = isolated_client.get("/api/memory/status", params=_scope(session_id))
    assert resp.status_code == 200, resp.text
    failed_jobs = resp.json()["failed_jobs"]
    assert len(failed_jobs) == 2, failed_jobs
    by_flag = {bool(job["retryable"]): job for job in failed_jobs}
    assert set(by_flag) == {True, False}, failed_jobs
    assert by_flag[False]["last_error_code"] == "validation_failed"
    assert by_flag[True]["last_error_code"] == "provider_unavailable"
