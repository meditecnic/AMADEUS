"""MEMORY-V11 S0 red contracts: observation → fact reconciliation (R01–R13).

Fixed import surface only:
  app.services.memory_v11.reconciler
  app.services.memory_v11.contracts
  app.services.memory_v11.repository

No fallback to legacy memory.service. apply path must go through validated
reconciler — raw operations are not a public write bypass.
"""
from __future__ import annotations

import hashlib
import importlib
import math
import struct
from typing import Any
from uuid import uuid4

import pytest

from app.db import get_db, init_db, reset_initialization_cache
from app.services.memory import CoreFactCandidate, EmbeddingAdapter, memory_service


def _reconciler():
    return importlib.import_module("app.services.memory_v11.reconciler")


def _contracts():
    return importlib.import_module("app.services.memory_v11.contracts")


def _repository():
    return importlib.import_module("app.services.memory_v11.repository")


def _unit_vector(seed: str) -> list[float]:
    values: list[float] = []
    for block in range(12):
        digest = hashlib.sha256(f"{seed}:{block}".encode("utf-8")).digest()
        values.extend(int(byte) - 127.5 for byte in digest)
    norm = math.sqrt(sum(v * v for v in values))
    return [v / norm for v in values]


class _DeterministicEmbedder(EmbeddingAdapter):
    """Offline passage/query stub — no SentenceTransformer, no network."""

    def __init__(
        self,
        *,
        model_name: str = "test-deterministic-384",
        fail_after: int | None = None,
        fail_always: bool = False,
    ):
        super().__init__(model_name=model_name, dimensions=384)
        self.encode_passage_calls = 0
        self.fail_after = fail_after
        self.fail_always = fail_always

    async def encode_passage(self, text: str) -> list[float]:
        self.encode_passage_calls += 1
        if self.fail_always:
            raise RuntimeError("stub encode_passage forced failure")
        if self.fail_after is not None and self.encode_passage_calls > self.fail_after:
            raise RuntimeError("stub encode_passage batch failure")
        return _unit_vector(f"passage:{text}")

    async def encode_query(self, text: str) -> list[float]:
        return _unit_vector(f"query:{text}")


@pytest.fixture
async def isolated_store(tmp_path, monkeypatch):
    monkeypatch.delenv("AMADEUS_DB_PATH", raising=False)
    monkeypatch.setenv("AMADEUS_DATA_DIR", str(tmp_path))
    reset_initialization_cache()
    await init_db()
    # Production CREATE/REFINE/SUPERSEDE now require passage embeddings.
    # Keep neighborhood tests offline with a deterministic EmbeddingAdapter stub.
    monkeypatch.setattr(memory_service, "embedder", _DeterministicEmbedder())
    return tmp_path


def _observation(
    *,
    ref: str,
    display_text: str,
    semantic: dict[str, Any],
    source_message_ids: list[int],
    evidence_kind: str = "direct_user",
    memory_class: str = "stable_candidate",
    confidence: float = 0.94,
) -> dict[str, Any]:
    return {
        "observation_ref": ref,
        "source_message_ids": source_message_ids,
        "display_text": display_text,
        "semantic": semantic,
        "evidence_kind": evidence_kind,
        "memory_class": memory_class,
        "confidence": confidence,
        "topic_label": "动漫",
        "expires_at": None,
    }


def _source_evidence(
    *,
    source_message_id: int,
    conversation_id: str,
    role: str = "user",
    fingerprint: str,
    excerpt: str,
) -> dict[str, Any]:
    return {
        "source_message_id": source_message_id,
        "conversation_id": conversation_id,
        "source_role": role,
        "source_fingerprint": fingerprint,
        "source_created_at": "2026-08-01T00:00:00+00:00",
        "excerpt": excerpt,
    }


async def _apply_validated(
    *,
    session_id: str,
    worldline: str,
    identity_mode: str,
    observations: list[dict[str, Any]],
    operations: list[dict[str, Any]],
    source_evidence: list[dict[str, Any]],
    candidate_allowlist: list[str],
    reanalysis_allowlist: list[str] | None = None,
) -> Any:
    """Call the validated reconciler entry — never a raw unvalidated writer."""
    recon = _reconciler()
    apply_fn = getattr(recon, "apply_validated_memory_operations", None)
    assert callable(apply_fn), (
        "reconciler.apply_validated_memory_operations missing "
        "(must validate; must not expose raw write bypass)"
    )
    # Public raw apply that skips validation must not exist.
    assert not hasattr(recon, "apply_raw_operations"), (
        "raw apply_raw_operations is forbidden as a public write entry"
    )
    return await apply_fn(
        session_id=session_id,
        worldline=worldline,
        identity_mode=identity_mode,
        observations=observations,
        operations=operations,
        source_evidence=source_evidence,
        candidate_allowlist=candidate_allowlist,
        reanalysis_allowlist=reanalysis_allowlist,
    )


@pytest.mark.asyncio
async def test_s0_legacy_key_drift_still_duplicates_today(isolated_store):
    """Defect baseline (green on legacy): exact fact_key upsert still duplicates."""
    owner = "v11-key-drift"
    await memory_service.upsert_core_fact(
        owner,
        "steins_gate",
        CoreFactCandidate(
            fact_key="likes_anime",
            fact_value="喜欢看动漫",
            confidence=0.95,
            importance=0.9,
            source_message_ids=[4151],
        ),
        identity_mode="self",
    )
    await memory_service.upsert_core_fact(
        owner,
        "steins_gate",
        CoreFactCandidate(
            fact_key="like_anime",
            fact_value="喜欢看动漫",
            confidence=0.95,
            importance=0.9,
            source_message_ids=[4151],
        ),
        identity_mode="self",
    )
    facts = await memory_service.select_core_facts(
        owner, "steins_gate", query="动漫", limit=24, identity_mode="self"
    )
    anime_rows = [f for f in facts if "喜欢看动漫" in f.content]
    assert len(anime_rows) >= 2


@pytest.mark.asyncio
async def test_s0_v11_key_drift_attaches_with_allowlist_and_evidence(isolated_store):
    """R01/R02: CREATE then ATTACH same claim; target must be on allowlist + expected_version."""
    source_id = 4151
    conv = "conv-r02"
    fp = "fp-likes-anime-4151"
    obs_create = _observation(
        ref="o1",
        display_text="喜欢看动漫",
        semantic={
            "subject": "user",
            "predicate": "likes",
            "object": {"kind": "medium", "name": "anime"},
            "polarity": "positive",
            "qualifiers": {},
        },
        source_message_ids=[source_id],
    )
    create_result = await _apply_validated(
        session_id="v11-r02",
        worldline="steins_gate",
        identity_mode="self",
        observations=[obs_create],
        operations=[
            {
                "op": "CREATE",
                "observation_ref": "o1",
                "target_fact_id": None,
                "expected_version": None,
                "fact_text": "喜欢看动漫",
                "reason_code": "direct_durable_statement",
            }
        ],
        source_evidence=[
            _source_evidence(
                source_message_id=source_id,
                conversation_id=conv,
                fingerprint=fp,
                excerpt="我喜欢看动漫",
            )
        ],
        candidate_allowlist=[],
    )
    created = (
        create_result["created_facts"]
        if isinstance(create_result, dict)
        else create_result.created_facts
    )
    assert created, "CREATE must return created_facts"
    first = created[0]
    fact_id = first["fact_id"] if isinstance(first, dict) else first.fact_id
    if isinstance(first, dict):
        assert "version_no" in first, f"CREATE must return version_no: {first}"
        active_version = first["version_no"]
    else:
        assert hasattr(first, "version_no"), f"CREATE must return version_no: {first!r}"
        active_version = first.version_no
    assert isinstance(active_version, int), (
        f"CREATE version_no must be int, got {type(active_version)!r}: {active_version!r}"
    )
    assert active_version >= 1

    obs_attach = _observation(
        ref="o2",
        display_text="喜欢看动漫",
        semantic={
            "subject": "user",
            "predicate": "likes",
            "object": {"kind": "medium", "name": "anime"},
            "polarity": "positive",
            "qualifiers": {},
        },
        source_message_ids=[source_id],
    )
    attach_result = await _apply_validated(
        session_id="v11-r02",
        worldline="steins_gate",
        identity_mode="self",
        observations=[obs_attach],
        operations=[
            {
                "op": "ATTACH",
                "observation_ref": "o2",
                "target_fact_id": fact_id,
                "expected_version": active_version,
                "fact_text": "喜欢看动漫",
                "reason_code": "equivalent_evidence",
            }
        ],
        source_evidence=[
            _source_evidence(
                source_message_id=source_id,
                conversation_id=conv,
                fingerprint=fp,
                excerpt="我喜欢看动漫",
            )
        ],
        candidate_allowlist=[fact_id],
    )
    active = (
        attach_result["active_facts"]
        if isinstance(attach_result, dict)
        else attach_result.active_facts
    )
    texts = [
        item["display_text"] if isinstance(item, dict) else item.display_text
        for item in active
    ]
    assert texts.count("喜欢看动漫") == 1


@pytest.mark.asyncio
async def test_s0_v11_three_atomic_anime_facts_reject_recommendation(isolated_store):
    """R03/R04: three atomic facts; conversational-act REJECT."""
    source_id = 4153
    conv = "conv-r03"
    observations = [
        _observation(
            ref="o1",
            display_text="喜欢看动漫",
            semantic={
                "subject": "user",
                "predicate": "likes",
                "object": {"name": "anime"},
            },
            source_message_ids=[source_id],
        ),
        _observation(
            ref="o2",
            display_text="偏好搞笑类型的动漫",
            semantic={
                "subject": "user",
                "predicate": "prefers_genre",
                "object": {"name": "comedy_anime"},
            },
            source_message_ids=[source_id],
        ),
        _observation(
            ref="o3",
            display_text="喜欢《为美好的世界献上祝福》，认为它很搞笑",
            semantic={
                "subject": "user",
                "predicate": "likes_work",
                "object": {"name": "konosuba"},
            },
            source_message_ids=[source_id],
        ),
        _observation(
            ref="o4",
            display_text="推荐过《为美好的世界献上祝福》",
            semantic={
                "subject": "user",
                "predicate": "recommended",
                "object": {"name": "konosuba"},
            },
            source_message_ids=[source_id],
            memory_class="none",
            confidence=0.5,
        ),
    ]
    ops = [
        {
            "op": "CREATE",
            "observation_ref": "o1",
            "target_fact_id": None,
            "expected_version": None,
            "fact_text": "喜欢看动漫",
            "reason_code": "direct_durable_statement",
        },
        {
            "op": "CREATE",
            "observation_ref": "o2",
            "target_fact_id": None,
            "expected_version": None,
            "fact_text": "偏好搞笑类型的动漫",
            "reason_code": "direct_durable_statement",
        },
        {
            "op": "CREATE",
            "observation_ref": "o3",
            "target_fact_id": None,
            "expected_version": None,
            "fact_text": "喜欢《为美好的世界献上祝福》，认为它很搞笑",
            "reason_code": "direct_durable_statement",
        },
        {
            "op": "REJECT",
            "observation_ref": "o4",
            "target_fact_id": None,
            "expected_version": None,
            "fact_text": "推荐过《为美好的世界献上祝福》",
            "reason_code": "conversational_act",
        },
    ]
    result = await _apply_validated(
        session_id="v11-r03",
        worldline="steins_gate",
        identity_mode="self",
        observations=observations,
        operations=ops,
        source_evidence=[
            _source_evidence(
                source_message_id=source_id,
                conversation_id=conv,
                fingerprint="fp-anime-4153",
                excerpt="动漫偏好对话",
            )
        ],
        candidate_allowlist=[],
    )
    active = result["active_facts"] if isinstance(result, dict) else result.active_facts
    texts = [
        item["display_text"] if isinstance(item, dict) else item.display_text
        for item in active
    ]
    assert len(texts) == 3
    assert any("搞笑" in t and "动漫" in t for t in texts)
    assert any("为美好的世界献上祝福" in t for t in texts)
    assert not any("推荐过" in t for t in texts)


@pytest.mark.asyncio
async def test_s0_v11_identity_and_worldline_isolation(isolated_store):
    """R05: okabe fruit only in okabe+SG; not self; not beta."""
    source_id = 9001
    result = await _apply_validated(
        session_id="v11-r05",
        worldline="steins_gate",
        identity_mode="okabe",
        observations=[
            _observation(
                ref="o1",
                display_text="喜欢吃水果",
                semantic={
                    "subject": "user",
                    "predicate": "likes",
                    "object": {"name": "fruit"},
                },
                source_message_ids=[source_id],
            )
        ],
        operations=[
            {
                "op": "CREATE",
                "observation_ref": "o1",
                "target_fact_id": None,
                "expected_version": None,
                "fact_text": "喜欢吃水果",
                "reason_code": "direct_durable_statement",
            }
        ],
        source_evidence=[
            _source_evidence(
                source_message_id=source_id,
                conversation_id="conv-fruit",
                fingerprint="fp-fruit-9001",
                excerpt="我喜欢吃水果",
            )
        ],
        candidate_allowlist=[],
    )
    repo = _repository()
    list_fn = repo.list_active_stable_facts

    async def texts(identity_mode: str, worldline: str) -> list[str]:
        rows = await list_fn(
            session_id="v11-r05",
            worldline=worldline,
            identity_mode=identity_mode,
        )
        out = []
        for item in rows:
            if isinstance(item, dict):
                out.append(str(item.get("display_text") or ""))
            else:
                out.append(str(getattr(item, "display_text", "")))
        return out

    assert any("水果" in t for t in await texts("okabe", "steins_gate"))
    assert not any("水果" in t for t in await texts("self", "steins_gate"))
    assert not any("水果" in t for t in await texts("okabe", "beta"))
    assert result is not None


@pytest.mark.asyncio
async def test_s0_assistant_source_rejected_with_stable_error_code(isolated_store):
    """R06/D31: assistant role cannot establish user evidence — stable error type/code."""
    contracts = _contracts()
    error_type = getattr(contracts, "MemoryValidationError", None)
    assert error_type is not None, "contracts.MemoryValidationError missing"

    with pytest.raises(error_type) as exc_info:
        await _apply_validated(
            session_id="v11-r06",
            worldline="steins_gate",
            identity_mode="self",
            observations=[
                _observation(
                    ref="o1",
                    display_text="用户喜欢咖啡",
                    semantic={
                        "subject": "user",
                        "predicate": "likes",
                        "object": {"name": "coffee"},
                    },
                    source_message_ids=[1],
                    evidence_kind="direct_user",
                )
            ],
            operations=[
                {
                    "op": "CREATE",
                    "observation_ref": "o1",
                    "target_fact_id": None,
                    "expected_version": None,
                    "fact_text": "用户喜欢咖啡",
                    "reason_code": "direct_durable_statement",
                }
            ],
            source_evidence=[
                _source_evidence(
                    source_message_id=1,
                    conversation_id="conv-asst",
                    role="assistant",
                    fingerprint="fp-asst-1",
                    excerpt="你喜欢咖啡",
                )
            ],
            candidate_allowlist=[],
        )
    err = exc_info.value
    code = getattr(err, "code", None) or getattr(err, "error_code", None)
    if code is None and hasattr(err, "args") and err.args:
        # Allow structured first arg dict.
        first = err.args[0]
        code = first.get("code") if isinstance(first, dict) else first
    assert code in {
        "assistant_source_forbidden",
        "source_role_not_user",
        "non_user_evidence",
    }, f"unstable or missing error code: {code!r} err={err!r}"


@pytest.mark.asyncio
async def test_s0_target_outside_allowlist_fail_closed(isolated_store):
    """R10: target_fact_id not on candidate allowlist → stable validation error."""
    contracts = _contracts()
    error_type = getattr(contracts, "MemoryValidationError", None)
    assert error_type is not None

    with pytest.raises(error_type) as exc_info:
        await _apply_validated(
            session_id="v11-r10",
            worldline="steins_gate",
            identity_mode="self",
            observations=[
                _observation(
                    ref="o1",
                    display_text="喜欢茶",
                    semantic={
                        "subject": "user",
                        "predicate": "likes",
                        "object": {"name": "tea"},
                    },
                    source_message_ids=[2],
                )
            ],
            operations=[
                {
                    "op": "ATTACH",
                    "observation_ref": "o1",
                    "target_fact_id": "not-on-allowlist",
                    "expected_version": 1,
                    "fact_text": "喜欢茶",
                    "reason_code": "equivalent_evidence",
                }
            ],
            source_evidence=[
                _source_evidence(
                    source_message_id=2,
                    conversation_id="conv-r10",
                    fingerprint="fp-tea-2",
                    excerpt="我喜欢茶",
                )
            ],
            candidate_allowlist=[],  # empty — target not allowed
        )
    code = getattr(exc_info.value, "code", None) or getattr(
        exc_info.value, "error_code", None
    )
    assert code in {
        "target_not_in_allowlist",
        "allowlist_violation",
        "invalid_target_fact_id",
    }


@pytest.mark.asyncio
async def test_s2_second_operation_failure_rolls_back_entire_batch(isolated_store):
    """P1: if a later operation fails, earlier CREATE/observation must not remain."""
    from app.db import get_db

    recon = _reconciler()
    session_id = "s-batch-atomic"
    source_id = 7001
    observations = [
        _observation(
            ref="o1",
            display_text="喜欢猫",
            semantic={"subject": "user", "predicate": "likes", "object": {"name": "cat"}},
            source_message_ids=[source_id],
        ),
        _observation(
            ref="o2",
            display_text="喜欢狗",
            semantic={"subject": "user", "predicate": "likes", "object": {"name": "dog"}},
            source_message_ids=[source_id],
        ),
    ]
    operations = [
        {
            "op": "CREATE",
            "observation_ref": "o1",
            "target_fact_id": None,
            "expected_version": None,
            "fact_text": "喜欢猫",
            "reason_code": "direct_durable_statement",
        },
        {
            "op": "ATTACH",
            "observation_ref": "o2",
            "target_fact_id": "missing-target",
            "expected_version": 1,
            "fact_text": "喜欢狗",
            "reason_code": "equivalent_evidence",
        },
    ]
    with pytest.raises(Exception):
        await recon.apply_validated_memory_operations(
            session_id=session_id,
            worldline="steins_gate",
            identity_mode="self",
            observations=observations,
            operations=operations,
            source_evidence=[
                _source_evidence(
                    source_message_id=source_id,
                    conversation_id="conv-atomic",
                    fingerprint="fp-atomic",
                    excerpt="猫和狗",
                )
            ],
            candidate_allowlist=[],  # ATTACH target not allowed → fail after CREATE pre-validate
        )

    # Pre-validate rejects before write when allowlist fails; also cover mid-batch:
    # CREATE succeeds validation, second op fails at apply-time expected_version.
    # Seed one fact then batch CREATE + ATTACH with stale expected_version.
    first = await recon.apply_validated_memory_operations(
        session_id=session_id,
        worldline="steins_gate",
        identity_mode="self",
        observations=[
            _observation(
                ref="seed",
                display_text="喜欢鱼",
                semantic={
                    "subject": "user",
                    "predicate": "likes",
                    "object": {"name": "fish"},
                },
                source_message_ids=[8001],
            )
        ],
        operations=[
            {
                "op": "CREATE",
                "observation_ref": "seed",
                "target_fact_id": None,
                "expected_version": None,
                "fact_text": "喜欢鱼",
                "reason_code": "direct_durable_statement",
            }
        ],
        source_evidence=[
            _source_evidence(
                source_message_id=8001,
                conversation_id="conv-seed",
                fingerprint="fp-seed",
                excerpt="鱼",
            )
        ],
        candidate_allowlist=[],
    )
    fact_id = first["created_facts"][0]["fact_id"]
    before_facts = len(first["active_facts"])

    with pytest.raises(Exception):
        await recon.apply_validated_memory_operations(
            session_id=session_id,
            worldline="steins_gate",
            identity_mode="self",
            observations=[
                _observation(
                    ref="n1",
                    display_text="喜欢鸟",
                    semantic={
                        "subject": "user",
                        "predicate": "likes",
                        "object": {"name": "bird"},
                    },
                    source_message_ids=[8002],
                ),
                _observation(
                    ref="n2",
                    display_text="喜欢鱼细化",
                    semantic={
                        "subject": "user",
                        "predicate": "likes",
                        "object": {"name": "fish"},
                    },
                    source_message_ids=[8002],
                ),
            ],
            operations=[
                {
                    "op": "CREATE",
                    "observation_ref": "n1",
                    "target_fact_id": None,
                    "expected_version": None,
                    "fact_text": "喜欢鸟",
                    "reason_code": "direct_durable_statement",
                },
                {
                    "op": "ATTACH",
                    "observation_ref": "n2",
                    "target_fact_id": fact_id,
                    "expected_version": 99,  # stale → fail after CREATE in same txn
                    "fact_text": "喜欢鱼",
                    "reason_code": "equivalent_evidence",
                },
            ],
            source_evidence=[
                _source_evidence(
                    source_message_id=8002,
                    conversation_id="conv-batch2",
                    fingerprint="fp-batch2",
                    excerpt="鸟和鱼",
                )
            ],
            candidate_allowlist=[fact_id],
        )

    db = await get_db("steins_gate", "memory")
    try:
        facts = await (
            await db.execute(
                "SELECT COUNT(*) AS n FROM stable_facts WHERE session_id=?",
                (session_id,),
            )
        ).fetchone()
        obs = await (
            await db.execute(
                "SELECT COUNT(*) AS n FROM memory_observations WHERE session_id=?",
                (session_id,),
            )
        ).fetchone()
        # Only the successful seed CREATE remains; rolled-back batch leaves nothing new.
        assert int(facts["n"]) == 1
        assert int(obs["n"]) == 1
    finally:
        await db.close()
    assert before_facts == 1


# ---------------------------------------------------------------------------
# MEMORY-V11-S3B-2: stable-fact version embedding lifecycle
# ---------------------------------------------------------------------------


async def _count_embeddings(fact_id: str | None = None) -> int:
    db = await get_db("steins_gate", "memory")
    try:
        if fact_id is None:
            cur = await db.execute(
                "SELECT COUNT(*) AS n FROM stable_fact_version_embeddings"
            )
        else:
            cur = await db.execute(
                "SELECT COUNT(*) AS n FROM stable_fact_version_embeddings WHERE fact_id=?",
                (fact_id,),
            )
        row = await cur.fetchone()
        return int(row["n"])
    finally:
        await db.close()


async def _embedding_row(fact_id: str, version_no: int) -> dict[str, Any] | None:
    db = await get_db("steins_gate", "memory")
    try:
        cur = await db.execute(
            """SELECT fact_id, version_no, model, dimensions, vector
                 FROM stable_fact_version_embeddings
                WHERE fact_id=? AND version_no=?""",
            (fact_id, version_no),
        )
        row = await cur.fetchone()
        if row is None:
            return None
        return {
            "fact_id": row["fact_id"],
            "version_no": int(row["version_no"]),
            "model": row["model"],
            "dimensions": int(row["dimensions"]),
            "vector": bytes(row["vector"]),
        }
    finally:
        await db.close()


async def _session_residue_counts(session_id: str) -> dict[str, int]:
    db = await get_db("steins_gate", "memory")
    try:
        facts = await (
            await db.execute(
                "SELECT COUNT(*) AS n FROM stable_facts WHERE session_id=?",
                (session_id,),
            )
        ).fetchone()
        versions = await (
            await db.execute(
                """SELECT COUNT(*) AS n FROM stable_fact_versions v
                     JOIN stable_facts f ON f.fact_id=v.fact_id
                    WHERE f.session_id=?""",
                (session_id,),
            )
        ).fetchone()
        obs = await (
            await db.execute(
                "SELECT COUNT(*) AS n FROM memory_observations WHERE session_id=?",
                (session_id,),
            )
        ).fetchone()
        evidence = await (
            await db.execute(
                """SELECT COUNT(*) AS n FROM stable_fact_evidence e
                     JOIN stable_facts f ON f.fact_id=e.fact_id
                    WHERE f.session_id=?""",
                (session_id,),
            )
        ).fetchone()
        vectors = await (
            await db.execute(
                """SELECT COUNT(*) AS n FROM stable_fact_version_embeddings e
                     JOIN stable_facts f ON f.fact_id=e.fact_id
                    WHERE f.session_id=?""",
                (session_id,),
            )
        ).fetchone()
        return {
            "facts": int(facts["n"]),
            "versions": int(versions["n"]),
            "observations": int(obs["n"]),
            "evidence": int(evidence["n"]),
            "vectors": int(vectors["n"]),
        }
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_s3b2_create_writes_matching_embedding(isolated_store, monkeypatch):
    """A: CREATE commits fact/version/vector together with model/dim contract."""
    embedder = _DeterministicEmbedder()
    monkeypatch.setattr(memory_service, "embedder", embedder)
    session_id = f"s3b2-create-{uuid4().hex[:8]}"
    text = "好きなアニメはこのすば"
    result = await _apply_validated(
        session_id=session_id,
        worldline="steins_gate",
        identity_mode="self",
        observations=[
            _observation(
                ref="o1",
                display_text=text,
                semantic={"subject": "user", "relation": "likes", "object": "konosuba"},
                source_message_ids=[9001],
            )
        ],
        operations=[
            {
                "op": "CREATE",
                "observation_ref": "o1",
                "target_fact_id": None,
                "expected_version": None,
                "fact_text": text,
                "reason_code": "direct_durable_statement",
            }
        ],
        source_evidence=[
            _source_evidence(
                source_message_id=9001,
                conversation_id="conv-s3b2-c",
                fingerprint="fp-s3b2-c",
                excerpt=text,
            )
        ],
        candidate_allowlist=[],
    )
    fact_id = result["created_facts"][0]["fact_id"]
    row = await _embedding_row(fact_id, 1)
    assert row is not None, "CREATE must write stable_fact_version_embeddings(v1)"
    assert row["model"] == embedder.model_name
    assert row["dimensions"] == 384
    expected = struct.pack(f"{384}f", *_unit_vector(f"passage:{text}"))
    assert row["vector"] == expected
    assert embedder.encode_passage_calls == 1


@pytest.mark.asyncio
async def test_s3b2_refine_replaces_vector(isolated_store, monkeypatch):
    """B: REFINE invalidates v1, promotes v2, swaps embedding rows."""
    embedder = _DeterministicEmbedder()
    monkeypatch.setattr(memory_service, "embedder", embedder)
    session_id = f"s3b2-refine-{uuid4().hex[:8]}"
    text_v1 = "好きな飲み物はコーヒー"
    create = await _apply_validated(
        session_id=session_id,
        worldline="steins_gate",
        identity_mode="self",
        observations=[
            _observation(
                ref="o1",
                display_text=text_v1,
                semantic={"subject": "user", "relation": "drinks", "object": "coffee"},
                source_message_ids=[9101],
            )
        ],
        operations=[
            {
                "op": "CREATE",
                "observation_ref": "o1",
                "fact_text": text_v1,
                "reason_code": "direct_durable_statement",
            }
        ],
        source_evidence=[
            _source_evidence(
                source_message_id=9101,
                conversation_id="conv-s3b2-r",
                fingerprint="fp-s3b2-r1",
                excerpt=text_v1,
            )
        ],
        candidate_allowlist=[],
    )
    fact_id = create["created_facts"][0]["fact_id"]
    assert await _embedding_row(fact_id, 1) is not None

    text_v2 = "好きな飲み物は紅茶"
    calls_before = embedder.encode_passage_calls
    await _apply_validated(
        session_id=session_id,
        worldline="steins_gate",
        identity_mode="self",
        observations=[
            _observation(
                ref="o2",
                display_text=text_v2,
                semantic={"subject": "user", "relation": "drinks", "object": "tea"},
                source_message_ids=[9102],
            )
        ],
        operations=[
            {
                "op": "REFINE",
                "observation_ref": "o2",
                "target_fact_id": fact_id,
                "expected_version": 1,
                "fact_text": text_v2,
                "reason_code": "compatible_refinement",
            }
        ],
        source_evidence=[
            _source_evidence(
                source_message_id=9102,
                conversation_id="conv-s3b2-r",
                fingerprint="fp-s3b2-r2",
                excerpt=text_v2,
            )
        ],
        candidate_allowlist=[fact_id],
    )
    assert embedder.encode_passage_calls == calls_before + 1
    assert await _embedding_row(fact_id, 1) is None, "old v1 vector must be deleted"
    row_v2 = await _embedding_row(fact_id, 2)
    assert row_v2 is not None
    assert row_v2["vector"] == struct.pack(
        f"{384}f", *_unit_vector(f"passage:{text_v2}")
    )
    db = await get_db("steins_gate", "memory")
    try:
        cur = await db.execute(
            "SELECT active_version FROM stable_facts WHERE fact_id=?",
            (fact_id,),
        )
        active = int((await cur.fetchone())["active_version"])
        cur = await db.execute(
            """SELECT invalid_at FROM stable_fact_versions
                WHERE fact_id=? AND version_no=1""",
            (fact_id,),
        )
        invalid_at = (await cur.fetchone())["invalid_at"]
    finally:
        await db.close()
    assert active == 2
    assert invalid_at is not None


@pytest.mark.asyncio
async def test_s3b2_supersede_replaces_vector(isolated_store, monkeypatch):
    """C: SUPERSEDE same vector lifecycle as REFINE."""
    embedder = _DeterministicEmbedder()
    monkeypatch.setattr(memory_service, "embedder", embedder)
    session_id = f"s3b2-super-{uuid4().hex[:8]}"
    text_v1 = "猫が好き"
    create = await _apply_validated(
        session_id=session_id,
        worldline="steins_gate",
        identity_mode="self",
        observations=[
            _observation(
                ref="o1",
                display_text=text_v1,
                semantic={"subject": "user", "relation": "likes", "object": "cat"},
                source_message_ids=[9201],
            )
        ],
        operations=[
            {
                "op": "CREATE",
                "observation_ref": "o1",
                "fact_text": text_v1,
                "reason_code": "direct_durable_statement",
            }
        ],
        source_evidence=[
            _source_evidence(
                source_message_id=9201,
                conversation_id="conv-s3b2-s",
                fingerprint="fp-s3b2-s1",
                excerpt=text_v1,
            )
        ],
        candidate_allowlist=[],
    )
    fact_id = create["created_facts"][0]["fact_id"]
    text_v2 = "犬が好き"
    await _apply_validated(
        session_id=session_id,
        worldline="steins_gate",
        identity_mode="self",
        observations=[
            _observation(
                ref="o2",
                display_text=text_v2,
                semantic={"subject": "user", "relation": "likes", "object": "dog"},
                source_message_ids=[9202],
            )
        ],
        operations=[
            {
                "op": "SUPERSEDE",
                "observation_ref": "o2",
                "target_fact_id": fact_id,
                "expected_version": 1,
                "fact_text": text_v2,
                "reason_code": "preference_change",
            }
        ],
        source_evidence=[
            _source_evidence(
                source_message_id=9202,
                conversation_id="conv-s3b2-s",
                fingerprint="fp-s3b2-s2",
                excerpt=text_v2,
            )
        ],
        candidate_allowlist=[fact_id],
    )
    assert await _embedding_row(fact_id, 1) is None
    assert await _embedding_row(fact_id, 2) is not None
    db = await get_db("steins_gate", "memory")
    try:
        cur = await db.execute(
            "SELECT active_version FROM stable_facts WHERE fact_id=?",
            (fact_id,),
        )
        assert int((await cur.fetchone())["active_version"]) == 2
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_s3b2_create_embedding_failure_is_atomic(isolated_store, monkeypatch):
    """D: encode failure leaves zero fact/version/observation/evidence/vector residue."""
    embedder = _DeterministicEmbedder(fail_always=True)
    monkeypatch.setattr(memory_service, "embedder", embedder)
    contracts = _contracts()
    session_id = f"s3b2-fail-create-{uuid4().hex[:8]}"
    with pytest.raises(contracts.MemoryValidationError) as exc_info:
        await _apply_validated(
            session_id=session_id,
            worldline="steins_gate",
            identity_mode="self",
            observations=[
                _observation(
                    ref="o1",
                    display_text="失敗する記憶",
                    semantic={"subject": "user", "relation": "x", "object": "y"},
                    source_message_ids=[9301],
                )
            ],
            operations=[
                {
                    "op": "CREATE",
                    "observation_ref": "o1",
                    "fact_text": "失敗する記憶",
                    "reason_code": "direct_durable_statement",
                }
            ],
            source_evidence=[
                _source_evidence(
                    source_message_id=9301,
                    conversation_id="conv-s3b2-fail",
                    fingerprint="fp-s3b2-fail",
                    excerpt="失敗する記憶",
                )
            ],
            candidate_allowlist=[],
        )
    assert exc_info.value.code == "embedding_failed"
    residue = await _session_residue_counts(session_id)
    assert residue == {
        "facts": 0,
        "versions": 0,
        "observations": 0,
        "evidence": 0,
        "vectors": 0,
    }


@pytest.mark.asyncio
async def test_s3b2_refine_embedding_failure_preserves_old_truth(
    isolated_store, monkeypatch
):
    """E: REFINE encode failure keeps v1 active/open and v1 vector intact."""
    embedder = _DeterministicEmbedder()
    monkeypatch.setattr(memory_service, "embedder", embedder)
    session_id = f"s3b2-fail-refine-{uuid4().hex[:8]}"
    text_v1 = "元の事実"
    create = await _apply_validated(
        session_id=session_id,
        worldline="steins_gate",
        identity_mode="self",
        observations=[
            _observation(
                ref="o1",
                display_text=text_v1,
                semantic={"subject": "user", "relation": "base", "object": "v1"},
                source_message_ids=[9401],
            )
        ],
        operations=[
            {
                "op": "CREATE",
                "observation_ref": "o1",
                "fact_text": text_v1,
                "reason_code": "direct_durable_statement",
            }
        ],
        source_evidence=[
            _source_evidence(
                source_message_id=9401,
                conversation_id="conv-s3b2-fr",
                fingerprint="fp-s3b2-fr1",
                excerpt=text_v1,
            )
        ],
        candidate_allowlist=[],
    )
    fact_id = create["created_facts"][0]["fact_id"]
    v1_before = await _embedding_row(fact_id, 1)
    assert v1_before is not None

    failing = _DeterministicEmbedder(fail_always=True)
    monkeypatch.setattr(memory_service, "embedder", failing)
    contracts = _contracts()
    with pytest.raises(contracts.MemoryValidationError) as exc_info:
        await _apply_validated(
            session_id=session_id,
            worldline="steins_gate",
            identity_mode="self",
            observations=[
                _observation(
                    ref="o2",
                    display_text="新しい事実",
                    semantic={"subject": "user", "relation": "base", "object": "v2"},
                    source_message_ids=[9402],
                )
            ],
            operations=[
                {
                    "op": "REFINE",
                    "observation_ref": "o2",
                    "target_fact_id": fact_id,
                    "expected_version": 1,
                    "fact_text": "新しい事実",
                    "reason_code": "compatible_refinement",
                }
            ],
            source_evidence=[
                _source_evidence(
                    source_message_id=9402,
                    conversation_id="conv-s3b2-fr",
                    fingerprint="fp-s3b2-fr2",
                    excerpt="新しい事実",
                )
            ],
            candidate_allowlist=[fact_id],
        )
    assert exc_info.value.code == "embedding_failed"
    v1_after = await _embedding_row(fact_id, 1)
    assert v1_after is not None
    assert v1_after["vector"] == v1_before["vector"]
    assert await _embedding_row(fact_id, 2) is None
    db = await get_db("steins_gate", "memory")
    try:
        cur = await db.execute(
            "SELECT active_version, state FROM stable_facts WHERE fact_id=?",
            (fact_id,),
        )
        row = await cur.fetchone()
        assert int(row["active_version"]) == 1
        assert row["state"] == "active"
        cur = await db.execute(
            """SELECT invalid_at FROM stable_fact_versions
                WHERE fact_id=? AND version_no=1""",
            (fact_id,),
        )
        assert (await cur.fetchone())["invalid_at"] is None
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_s3b2_attach_does_not_reindex(isolated_store, monkeypatch):
    """F: ATTACH does not call encode_passage or mutate the current vector."""
    embedder = _DeterministicEmbedder()
    monkeypatch.setattr(memory_service, "embedder", embedder)
    session_id = f"s3b2-attach-{uuid4().hex[:8]}"
    text = "添付される事実"
    create = await _apply_validated(
        session_id=session_id,
        worldline="steins_gate",
        identity_mode="self",
        observations=[
            _observation(
                ref="o1",
                display_text=text,
                semantic={"subject": "user", "relation": "likes", "object": "x"},
                source_message_ids=[9501],
            )
        ],
        operations=[
            {
                "op": "CREATE",
                "observation_ref": "o1",
                "fact_text": text,
                "reason_code": "direct_durable_statement",
            }
        ],
        source_evidence=[
            _source_evidence(
                source_message_id=9501,
                conversation_id="conv-s3b2-a",
                fingerprint="fp-s3b2-a1",
                excerpt=text,
            )
        ],
        candidate_allowlist=[],
    )
    fact_id = create["created_facts"][0]["fact_id"]
    before = await _embedding_row(fact_id, 1)
    calls_before = embedder.encode_passage_calls
    await _apply_validated(
        session_id=session_id,
        worldline="steins_gate",
        identity_mode="self",
        observations=[
            _observation(
                ref="o2",
                display_text=text,
                semantic={"subject": "user", "relation": "likes", "object": "x"},
                source_message_ids=[9502],
            )
        ],
        operations=[
            {
                "op": "ATTACH",
                "observation_ref": "o2",
                "target_fact_id": fact_id,
                "expected_version": 1,
                "fact_text": text,
                "reason_code": "supporting_evidence",
            }
        ],
        source_evidence=[
            _source_evidence(
                source_message_id=9502,
                conversation_id="conv-s3b2-a",
                fingerprint="fp-s3b2-a2",
                excerpt=text,
            )
        ],
        candidate_allowlist=[fact_id],
    )
    assert embedder.encode_passage_calls == calls_before
    after = await _embedding_row(fact_id, 1)
    assert after is not None and after["vector"] == before["vector"]
    assert await _count_embeddings(fact_id) == 1


@pytest.mark.asyncio
async def test_s3b2_replay_does_not_reembed(isolated_store, monkeypatch):
    """G: successful apply then same-source replay does not re-encode."""
    embedder = _DeterministicEmbedder()
    monkeypatch.setattr(memory_service, "embedder", embedder)
    session_id = f"s3b2-replay-{uuid4().hex[:8]}"
    text = "再生される事実"
    kwargs = dict(
        session_id=session_id,
        worldline="steins_gate",
        identity_mode="self",
        observations=[
            _observation(
                ref="o1",
                display_text=text,
                semantic={"subject": "user", "relation": "likes", "object": "replay"},
                source_message_ids=[9601],
            )
        ],
        operations=[
            {
                "op": "CREATE",
                "observation_ref": "o1",
                "fact_text": text,
                "reason_code": "direct_durable_statement",
            }
        ],
        source_evidence=[
            _source_evidence(
                source_message_id=9601,
                conversation_id="conv-s3b2-rp",
                fingerprint="fp-s3b2-rp",
                excerpt=text,
            )
        ],
        candidate_allowlist=[],
    )
    first = await _apply_validated(**kwargs)
    assert first.get("replay") is False
    calls_after_first = embedder.encode_passage_calls
    assert calls_after_first == 1
    second = await _apply_validated(**kwargs)
    assert second.get("replay") is True
    assert embedder.encode_passage_calls == calls_after_first
    assert await _count_embeddings(first["created_facts"][0]["fact_id"]) == 1


@pytest.mark.asyncio
async def test_s3b2_batch_embedding_failure_is_atomic(isolated_store, monkeypatch):
    """H: multi-CREATE batch — one encode failure leaves no partial writes."""
    embedder = _DeterministicEmbedder(fail_after=1)
    monkeypatch.setattr(memory_service, "embedder", embedder)
    contracts = _contracts()
    session_id = f"s3b2-batch-{uuid4().hex[:8]}"
    with pytest.raises(contracts.MemoryValidationError) as exc_info:
        await _apply_validated(
            session_id=session_id,
            worldline="steins_gate",
            identity_mode="self",
            observations=[
                _observation(
                    ref="o1",
                    display_text="バッチA",
                    semantic={"subject": "user", "relation": "a", "object": "1"},
                    source_message_ids=[9701],
                ),
                _observation(
                    ref="o2",
                    display_text="バッチB",
                    semantic={"subject": "user", "relation": "b", "object": "2"},
                    source_message_ids=[9702],
                ),
            ],
            operations=[
                {
                    "op": "CREATE",
                    "observation_ref": "o1",
                    "fact_text": "バッチA",
                    "reason_code": "direct_durable_statement",
                },
                {
                    "op": "CREATE",
                    "observation_ref": "o2",
                    "fact_text": "バッチB",
                    "reason_code": "direct_durable_statement",
                },
            ],
            source_evidence=[
                _source_evidence(
                    source_message_id=9701,
                    conversation_id="conv-s3b2-b",
                    fingerprint="fp-s3b2-b1",
                    excerpt="バッチA",
                ),
                _source_evidence(
                    source_message_id=9702,
                    conversation_id="conv-s3b2-b",
                    fingerprint="fp-s3b2-b2",
                    excerpt="バッチB",
                ),
            ],
            candidate_allowlist=[],
        )
    assert exc_info.value.code == "embedding_failed"
    residue = await _session_residue_counts(session_id)
    assert residue == {
        "facts": 0,
        "versions": 0,
        "observations": 0,
        "evidence": 0,
        "vectors": 0,
    }
    assert embedder.encode_passage_calls == 2


class _NonFiniteEmbedder(EmbeddingAdapter):
    """Returns a full-dim vector with one non-finite component (offline)."""

    def __init__(self, bad_value: float):
        super().__init__(model_name="test-deterministic-384", dimensions=384)
        self.bad_value = float(bad_value)
        self.encode_passage_calls = 0

    async def encode_passage(self, text: str) -> list[float]:
        self.encode_passage_calls += 1
        vector = _unit_vector(f"passage:{text}")
        vector[0] = self.bad_value
        return vector

    async def encode_query(self, text: str) -> list[float]:
        return _unit_vector(f"query:{text}")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad_value",
    [float("nan"), float("inf"), float("-inf")],
    ids=["nan", "pos_inf", "neg_inf"],
)
async def test_s3b2_nonfinite_vector_rejected_atomically(
    isolated_store, monkeypatch, bad_value
):
    """Non-finite passage vectors fail closed with zero CREATE residue."""
    embedder = _NonFiniteEmbedder(bad_value)
    monkeypatch.setattr(memory_service, "embedder", embedder)
    contracts = _contracts()
    session_id = f"s3b2-nonfinite-{uuid4().hex[:8]}"
    with pytest.raises(contracts.MemoryValidationError) as exc_info:
        await _apply_validated(
            session_id=session_id,
            worldline="steins_gate",
            identity_mode="self",
            observations=[
                _observation(
                    ref="o1",
                    display_text="非有限ベクトル",
                    semantic={"subject": "user", "relation": "x", "object": "nf"},
                    source_message_ids=[9801],
                )
            ],
            operations=[
                {
                    "op": "CREATE",
                    "observation_ref": "o1",
                    "fact_text": "非有限ベクトル",
                    "reason_code": "direct_durable_statement",
                }
            ],
            source_evidence=[
                _source_evidence(
                    source_message_id=9801,
                    conversation_id="conv-s3b2-nf",
                    fingerprint="fp-s3b2-nf",
                    excerpt="非有限ベクトル",
                )
            ],
            candidate_allowlist=[],
        )
    assert exc_info.value.code == "invalid_embedding_vector"
    assert embedder.encode_passage_calls == 1
    residue = await _session_residue_counts(session_id)
    assert residue == {
        "facts": 0,
        "versions": 0,
        "observations": 0,
        "evidence": 0,
        "vectors": 0,
    }


@pytest.mark.asyncio
async def test_s3b2_refine_txn_insert_embedding_failure_rolls_back(
    isolated_store, monkeypatch
):
    """REFINE: insert_version_embedding failure after BEGIN rolls back fully.

    Proves failure is in the write transaction (prepare already succeeded),
    not encode_passage: v1 stays active/open with unchanged embedding.
    """
    embedder = _DeterministicEmbedder()
    monkeypatch.setattr(memory_service, "embedder", embedder)
    session_id = f"s3b2-txn-ins-{uuid4().hex[:8]}"
    text_v1 = "事務内失敗の元事実"
    create = await _apply_validated(
        session_id=session_id,
        worldline="steins_gate",
        identity_mode="self",
        observations=[
            _observation(
                ref="o1",
                display_text=text_v1,
                semantic={"subject": "user", "relation": "base", "object": "txn1"},
                source_message_ids=[9901],
            )
        ],
        operations=[
            {
                "op": "CREATE",
                "observation_ref": "o1",
                "fact_text": text_v1,
                "reason_code": "direct_durable_statement",
            }
        ],
        source_evidence=[
            _source_evidence(
                source_message_id=9901,
                conversation_id="conv-s3b2-txn",
                fingerprint="fp-s3b2-txn1",
                excerpt=text_v1,
            )
        ],
        candidate_allowlist=[],
    )
    fact_id = create["created_facts"][0]["fact_id"]
    v1_before = await _embedding_row(fact_id, 1)
    assert v1_before is not None
    obs_before = (await _session_residue_counts(session_id))["observations"]
    evidence_before = (await _session_residue_counts(session_id))["evidence"]

    recon = _reconciler()
    insert_calls = {"n": 0}
    prepare_ok = {"n": 0}
    real_prepare = recon.prepare_passage_embedding

    async def tracking_prepare(text, *, embedder=None):
        prepared = await real_prepare(text, embedder=embedder)
        prepare_ok["n"] += 1
        return prepared

    async def boom_insert(db, *, fact_id, version_no, prepared):
        insert_calls["n"] += 1
        raise RuntimeError("forced insert_version_embedding failure inside txn")

    monkeypatch.setattr(recon, "prepare_passage_embedding", tracking_prepare)
    monkeypatch.setattr(recon, "insert_version_embedding", boom_insert)

    with pytest.raises(RuntimeError, match="forced insert_version_embedding"):
        await _apply_validated(
            session_id=session_id,
            worldline="steins_gate",
            identity_mode="self",
            observations=[
                _observation(
                    ref="o2",
                    display_text="事務内失敗の新事実",
                    semantic={"subject": "user", "relation": "base", "object": "txn2"},
                    source_message_ids=[9902],
                )
            ],
            operations=[
                {
                    "op": "REFINE",
                    "observation_ref": "o2",
                    "target_fact_id": fact_id,
                    "expected_version": 1,
                    "fact_text": "事務内失敗の新事実",
                    "reason_code": "compatible_refinement",
                }
            ],
            source_evidence=[
                _source_evidence(
                    source_message_id=9902,
                    conversation_id="conv-s3b2-txn",
                    fingerprint="fp-s3b2-txn2",
                    excerpt="事務内失敗の新事実",
                )
            ],
            candidate_allowlist=[fact_id],
        )

    assert prepare_ok["n"] == 1, "preparation must succeed before write txn"
    assert insert_calls["n"] == 1, "failure must occur at insert_version_embedding"

    v1_after = await _embedding_row(fact_id, 1)
    assert v1_after is not None
    assert v1_after["vector"] == v1_before["vector"]
    assert await _embedding_row(fact_id, 2) is None

    db = await get_db("steins_gate", "memory")
    try:
        cur = await db.execute(
            "SELECT active_version, state FROM stable_facts WHERE fact_id=?",
            (fact_id,),
        )
        row = await cur.fetchone()
        assert int(row["active_version"]) == 1
        assert row["state"] == "active"
        cur = await db.execute(
            """SELECT invalid_at FROM stable_fact_versions
                WHERE fact_id=? AND version_no=1""",
            (fact_id,),
        )
        assert (await cur.fetchone())["invalid_at"] is None
        cur = await db.execute(
            """SELECT COUNT(*) AS n FROM stable_fact_versions
                WHERE fact_id=? AND version_no=2""",
            (fact_id,),
        )
        assert int((await cur.fetchone())["n"]) == 0
    finally:
        await db.close()

    residue = await _session_residue_counts(session_id)
    assert residue["observations"] == obs_before
    assert residue["evidence"] == evidence_before
    assert residue["versions"] == 1
    assert residue["vectors"] == 1

# ---------------------------------------------------------------------------
# MEMORY-V11-S3C-1: episodic observations route to first-class experiences.
# memory_class=episodic + CREATE -> experience (never a stable fact/vector).
# ---------------------------------------------------------------------------


async def _experience_rows(
    *, worldline: str = "steins_gate", session_id: str | None = None
) -> list[dict[str, Any]]:
    db = await get_db(worldline, "memory")
    try:
        if session_id is None:
            cur = await db.execute(
                "SELECT * FROM experiences ORDER BY experience_id"
            )
        else:
            cur = await db.execute(
                "SELECT * FROM experiences WHERE session_id=? ORDER BY experience_id",
                (session_id,),
            )
        rows = await cur.fetchall()
        return [dict(row) for row in rows]
    finally:
        await db.close()


async def _count_rows(
    *, worldline: str = "steins_gate", sql: str, params: tuple[Any, ...] = ()
) -> int:
    db = await get_db(worldline, "memory")
    try:
        cur = await db.execute(sql, params)
        return int((await cur.fetchone())["n"])
    finally:
        await db.close()


async def _attached_observation_count(session_id: str) -> int:
    return await _count_rows(
        sql="""SELECT COUNT(*) AS n FROM memory_observations
                WHERE session_id=? AND status='attached'""",
        params=(session_id,),
    )


async def _source_rows_for_session(session_id: str) -> int:
    return await _count_rows(
        sql="""SELECT COUNT(*) AS n FROM memory_observation_sources s
                JOIN memory_observations o ON o.observation_id=s.observation_id
               WHERE o.session_id=?""",
        params=(session_id,),
    )


def _canonical_fingerprint_hex(semantic: dict[str, Any]) -> str:
    import json as _json

    return hashlib.sha256(
        _json.dumps(
            semantic, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()


@pytest.mark.asyncio
async def test_s3c1_episodic_create_stores_experience_only(isolated_store):
    """C: episodic CREATE -> exactly one experience, zero stable facts/vectors."""
    session_id = f"s3c1-ep-{uuid4().hex[:8]}"
    text = "先週秋葉原でフィギュアを買った"
    semantic = {
        "subject": "user",
        "predicate": "visited",
        "object": {"place": "akihabara"},
    }
    expires_at = "2026-09-01T00:00:00+00:00"
    obs = _observation(
        ref="o1",
        display_text=text,
        semantic=semantic,
        source_message_ids=[7001],
        memory_class="episodic",
        confidence=0.9,
    )
    obs["expires_at"] = expires_at
    result = await _apply_validated(
        session_id=session_id,
        worldline="steins_gate",
        identity_mode="self",
        observations=[obs],
        operations=[
            {
                "op": "CREATE",
                "observation_ref": "o1",
                "target_fact_id": None,
                "expected_version": None,
                "fact_text": text,
                "reason_code": "contextual_event",
            }
        ],
        source_evidence=[
            _source_evidence(
                source_message_id=7001,
                conversation_id="conv-ep-1",
                fingerprint="fp-ep-1",
                excerpt=text,
            )
        ],
        candidate_allowlist=[],
    )
    assert result["created_facts"] == []
    exps = await _experience_rows(session_id=session_id)
    assert len(exps) == 1, "episodic CREATE must store exactly one experience"
    exp = exps[0]
    assert exp["conversation_id"] == "conv-ep-1"
    assert exp["identity_mode"] == "self"
    assert exp["status"] == "active"
    assert exp["expires_at"] == expires_at
    assert exp["semantic_fingerprint"] == _canonical_fingerprint_hex(semantic)
    assert exp["deleted_at"] is None
    assert await _attached_observation_count(session_id) == 1
    assert await _source_rows_for_session(session_id) == 1
    assert await _count_rows(
        sql="SELECT COUNT(*) AS n FROM stable_facts WHERE session_id=?",
        params=(session_id,),
    ) == 0, "episodic CREATE must not create stable facts"
    assert await _count_rows(
        sql="""SELECT COUNT(*) AS n FROM stable_fact_version_embeddings e
                JOIN stable_facts f ON f.fact_id=e.fact_id
               WHERE f.session_id=?""",
        params=(session_id,),
    ) == 0, "episodic CREATE must not create stable fact embeddings"
    assert await _count_rows(
        sql="""SELECT COUNT(*) AS n FROM stable_fact_versions v
                JOIN stable_facts f ON f.fact_id=v.fact_id
               WHERE f.session_id=?""",
        params=(session_id,),
    ) == 0


@pytest.mark.asyncio
async def test_s3c1_stable_create_regression_no_experience(isolated_store):
    """D: stable_candidate CREATE still writes fact+vector, zero experience."""
    session_id = f"s3c1-stable-{uuid4().hex[:8]}"
    text = "コーヒーが好き"
    result = await _apply_validated(
        session_id=session_id,
        worldline="steins_gate",
        identity_mode="self",
        observations=[
            _observation(
                ref="o1",
                display_text=text,
                semantic={"subject": "user", "relation": "likes", "object": "coffee"},
                source_message_ids=[7101],
            )
        ],
        operations=[
            {
                "op": "CREATE",
                "observation_ref": "o1",
                "target_fact_id": None,
                "expected_version": None,
                "fact_text": text,
                "reason_code": "direct_durable_statement",
            }
        ],
        source_evidence=[
            _source_evidence(
                source_message_id=7101,
                conversation_id="conv-st-1",
                fingerprint="fp-st-1",
                excerpt=text,
            )
        ],
        candidate_allowlist=[],
    )
    assert len(result["created_facts"]) == 1
    fact_id = result["created_facts"][0]["fact_id"]
    assert await _count_rows(
        sql="""SELECT COUNT(*) AS n FROM stable_fact_version_embeddings
                WHERE fact_id=? AND version_no=1""",
        params=(fact_id,),
    ) == 1, "stable CREATE must still write its v1 vector"
    assert await _experience_rows(session_id=session_id) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("op_name", ["ATTACH", "REFINE", "SUPERSEDE"])
async def test_s3c1_episodic_target_operation_rejected(
    isolated_store, op_name
):
    """E: episodic observations must never mutate a real selected stable fact."""
    session_id = f"s3c1-reject-{uuid4().hex[:8]}"
    text = "毎朝ジョギングする"
    seed = await _apply_validated(
        session_id=session_id,
        worldline="steins_gate",
        identity_mode="self",
        observations=[
            _observation(
                ref="s1",
                display_text=text,
                semantic={"subject": "user", "relation": "habit", "object": "jog"},
                source_message_ids=[7201],
            )
        ],
        operations=[
            {
                "op": "CREATE",
                "observation_ref": "s1",
                "fact_text": text,
                "reason_code": "direct_durable_statement",
            }
        ],
        source_evidence=[
            _source_evidence(
                source_message_id=7201,
                conversation_id="conv-rj-1",
                fingerprint="fp-rj-1",
                excerpt=text,
            )
        ],
        candidate_allowlist=[],
    )
    fact_id = seed["created_facts"][0]["fact_id"]

    with pytest.raises(_contracts().MemoryValidationError) as exc_info:
        await _apply_validated(
            session_id=session_id,
            worldline="steins_gate",
            identity_mode="self",
            observations=[
                _observation(
                    ref="o1",
                    display_text="台風でジョギングを休んだ",
                    semantic={
                        "subject": "user",
                        "predicate": "skipped",
                        "object": {"event": "typhoon"},
                    },
                    source_message_ids=[7202],
                    memory_class="episodic",
                    confidence=0.8,
                )
            ],
            operations=[
                {
                    "op": op_name,
                    "observation_ref": "o1",
                    "target_fact_id": fact_id,
                    "expected_version": 1,
                    "fact_text": "台風でジョギングを休んだ",
                    "reason_code": "episodic_attempt",
                }
            ],
            source_evidence=[
                _source_evidence(
                    source_message_id=7202,
                    conversation_id="conv-rj-2",
                    fingerprint="fp-rj-2",
                    excerpt="台風でジョギングを休んだ",
                )
            ],
            candidate_allowlist=[fact_id],
        )
    assert exc_info.value.code == "episodic_target_operation_forbidden"

    db = await get_db("steins_gate", "memory")
    try:
        cur = await db.execute(
            "SELECT active_version, state FROM stable_facts WHERE fact_id=?",
            (fact_id,),
        )
        row = await cur.fetchone()
        assert int(row["active_version"]) == 1
        assert row["state"] == "active"
        cur = await db.execute(
            """SELECT invalid_at FROM stable_fact_versions
                WHERE fact_id=? AND version_no=1""",
            (fact_id,),
        )
        assert (await cur.fetchone())["invalid_at"] is None
    finally:
        await db.close()
    assert await _experience_rows(session_id=session_id) == []
    residue = await _session_residue_counts(session_id)
    assert residue["observations"] == 1, "no partial observation from rejected batch"
    assert residue["evidence"] == 1, "no partial evidence from rejected batch"


@pytest.mark.asyncio
async def test_s3c1_episodic_reject_stores_rejected_observation(isolated_store):
    """F: episodic + REJECT -> rejected observation only, no experience."""
    session_id = f"s3c1-rej-{uuid4().hex[:8]}"
    text = "昨日は雨だった"
    await _apply_validated(
        session_id=session_id,
        worldline="steins_gate",
        identity_mode="self",
        observations=[
            _observation(
                ref="o1",
                display_text=text,
                semantic={"subject": "user", "predicate": "weather", "object": "rain"},
                source_message_ids=[7301],
                memory_class="episodic",
                confidence=0.6,
            )
        ],
        operations=[
            {
                "op": "REJECT",
                "observation_ref": "o1",
                "fact_text": text,
                "reason_code": "noise",
            }
        ],
        source_evidence=[
            _source_evidence(
                source_message_id=7301,
                conversation_id="conv-rej-1",
                fingerprint="fp-rej-1",
                excerpt=text,
            )
        ],
        candidate_allowlist=[],
    )
    assert await _experience_rows(session_id=session_id) == []
    assert await _count_rows(
        sql="""SELECT COUNT(*) AS n FROM memory_observations
                WHERE session_id=? AND status='rejected'""",
        params=(session_id,),
    ) == 1
    assert await _count_rows(
        sql="SELECT COUNT(*) AS n FROM stable_facts WHERE session_id=?",
        params=(session_id,),
    ) == 0


@pytest.mark.asyncio
async def test_s3c1_episodic_replay_idempotent(isolated_store):
    """G: same-source replay adds no second experience/observation/source."""
    session_id = f"s3c1-replay-{uuid4().hex[:8]}"
    text = "先週京都に行った"
    kwargs = dict(
        session_id=session_id,
        worldline="steins_gate",
        identity_mode="self",
        observations=[
            _observation(
                ref="o1",
                display_text=text,
                semantic={"subject": "user", "predicate": "visited", "object": "kyoto"},
                source_message_ids=[7401],
                memory_class="episodic",
                confidence=0.9,
            )
        ],
        operations=[
            {
                "op": "CREATE",
                "observation_ref": "o1",
                "fact_text": text,
                "reason_code": "contextual_event",
            }
        ],
        source_evidence=[
            _source_evidence(
                source_message_id=7401,
                conversation_id="conv-rp-1",
                fingerprint="fp-rp-1",
                excerpt=text,
            )
        ],
        candidate_allowlist=[],
    )
    first = await _apply_validated(**kwargs)
    assert first.get("replay") is False
    second = await _apply_validated(**kwargs)
    assert second.get("replay") is True
    assert len(await _experience_rows(session_id=session_id)) == 1
    assert await _attached_observation_count(session_id) == 1
    assert await _source_rows_for_session(session_id) == 1


@pytest.mark.asyncio
async def test_s3c1_experience_insert_failure_rolls_back_all(isolated_store):
    """H: experience INSERT failure rolls back observation + source too."""
    session_id = f"s3c1-txn-{uuid4().hex[:8]}"
    text = "花火大会に行った"
    recon = _reconciler()
    original = recon.insert_experience

    async def boom_insert(*args: Any, **kwargs: Any):
        raise RuntimeError("forced experience insert failure inside txn")

    recon.insert_experience = boom_insert
    try:
        with pytest.raises(RuntimeError, match="forced experience insert"):
            await _apply_validated(
                session_id=session_id,
                worldline="steins_gate",
                identity_mode="self",
                observations=[
                    _observation(
                        ref="o1",
                        display_text=text,
                        semantic={
                            "subject": "user",
                            "predicate": "attended",
                            "object": "fireworks",
                        },
                        source_message_ids=[7501],
                        memory_class="episodic",
                        confidence=0.9,
                    )
                ],
                operations=[
                    {
                        "op": "CREATE",
                        "observation_ref": "o1",
                        "fact_text": text,
                        "reason_code": "contextual_event",
                    }
                ],
                source_evidence=[
                    _source_evidence(
                        source_message_id=7501,
                        conversation_id="conv-tx-1",
                        fingerprint="fp-tx-1",
                        excerpt=text,
                    )
                ],
                candidate_allowlist=[],
            )
    finally:
        recon.insert_experience = original

    residue = await _session_residue_counts(session_id)
    assert residue == {
        "facts": 0,
        "versions": 0,
        "observations": 0,
        "evidence": 0,
        "vectors": 0,
    }, "experience INSERT failure must roll back observation + sources"
    assert await _experience_rows(session_id=session_id) == []


@pytest.mark.asyncio
async def test_s3c1_worldline_identity_isolation(isolated_store):
    """I: self/okabe and SG/beta experiences never cross boundaries."""
    session_id = f"s3c1-iso-{uuid4().hex[:8]}"
    text_self = "self: 鎌倉の寺を巡った"
    text_okabe = "okabe: ラボで電話レンジを試した"
    await _apply_validated(
        session_id=session_id,
        worldline="steins_gate",
        identity_mode="self",
        observations=[
            _observation(
                ref="o1",
                display_text=text_self,
                semantic={"subject": "user", "predicate": "visited", "object": "kamakura"},
                source_message_ids=[7601],
                memory_class="episodic",
                confidence=0.9,
            )
        ],
        operations=[{"op": "CREATE", "observation_ref": "o1", "fact_text": text_self,
                     "reason_code": "contextual_event"}],
        source_evidence=[_source_evidence(source_message_id=7601,
                                          conversation_id="conv-iso-1",
                                          fingerprint="fp-iso-1", excerpt=text_self)],
        candidate_allowlist=[],
    )
    await _apply_validated(
        session_id=session_id,
        worldline="steins_gate",
        identity_mode="okabe",
        observations=[
            _observation(
                ref="o2",
                display_text=text_okabe,
                semantic={"subject": "okabe", "predicate": "tried", "object": "phonewave"},
                source_message_ids=[7602],
                memory_class="episodic",
                confidence=0.9,
            )
        ],
        operations=[{"op": "CREATE", "observation_ref": "o2", "fact_text": text_okabe,
                     "reason_code": "contextual_event"}],
        source_evidence=[_source_evidence(source_message_id=7602,
                                          conversation_id="conv-iso-2",
                                          fingerprint="fp-iso-2", excerpt=text_okabe)],
        candidate_allowlist=[],
    )
    rows = await _experience_rows(session_id=session_id)
    assert len(rows) == 2
    assert {r["identity_mode"] for r in rows} == {"self", "okabe"}
    assert await _count_rows(
        worldline="beta",
        sql="SELECT COUNT(*) AS n FROM experiences WHERE session_id=?",
        params=(session_id,),
    ) == 0, "SG experiences must never appear in the beta physical DB"

# ---------------------------------------------------------------------------
# MEMORY-V11-S3D-2: CREATE-only automatic topic materialization (R1-R7 + E).
# ---------------------------------------------------------------------------


async def _topic_rows(session_id: str, identity_mode: str) -> list[dict]:
    db = await get_db("steins_gate", "memory")
    try:
        rows = await (
            await db.execute(
                """SELECT topic_id, normalized_label, display_label
                     FROM memory_topics
                    WHERE session_id=? AND identity_mode=?
                    ORDER BY topic_id""",
                (session_id, identity_mode),
            )
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        await db.close()


async def _fact_topic_id(fact_id: str) -> str | None:
    db = await get_db("steins_gate", "memory")
    try:
        row = await (
            await db.execute(
                "SELECT topic_id FROM stable_facts WHERE fact_id=?", (fact_id,)
            )
        ).fetchone()
        assert row is not None
        return row["topic_id"]
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_s3d2_r1_stable_create_materializes_topic(isolated_store):
    """R1: stable CREATE with topic_label → memory_topics row + fact.topic_id."""
    session_id = f"s3d2-r1-{uuid4().hex[:8]}"
    result = await _apply_validated(
        session_id=session_id,
        worldline="steins_gate",
        identity_mode="self",
        observations=[
            _observation(
                ref="o1",
                display_text="喜欢看动漫",
                semantic={"subject": "user", "predicate": "likes", "object": "anime"},
                source_message_ids=[3101],
            )
        ],
        operations=[
            {
                "op": "CREATE",
                "observation_ref": "o1",
                "target_fact_id": None,
                "expected_version": None,
                "fact_text": "喜欢看动漫",
                "reason_code": "direct_durable_statement",
            }
        ],
        source_evidence=[
            _source_evidence(
                source_message_id=3101,
                conversation_id="conv-s3d2-r1",
                fingerprint="fp-s3d2-r1",
                excerpt="喜欢看动漫",
            )
        ],
        candidate_allowlist=[],
    )
    fact_id = result["created_facts"][0]["fact_id"]
    topics = await _topic_rows(session_id, "self")
    assert len(topics) == 1, (
        "stable CREATE with topic_label must materialize one memory_topics row"
    )
    assert topics[0]["normalized_label"] == "动漫"
    assert topics[0]["display_label"] == "动漫"
    assert await _fact_topic_id(fact_id) == topics[0]["topic_id"]


@pytest.mark.asyncio
async def test_s3d2_r2_normalized_equivalent_labels_reuse_topic(isolated_store):
    """R2: '动漫' then '动漫。' resolve to the SAME topic_id."""
    session_id = f"s3d2-r2-{uuid4().hex[:8]}"
    first = await _apply_validated(
        session_id=session_id,
        worldline="steins_gate",
        identity_mode="self",
        observations=[
            _observation(
                ref="o1",
                display_text="喜欢看动漫",
                semantic={"subject": "user", "predicate": "likes", "object": "anime"},
                source_message_ids=[3201],
            )
        ],
        operations=[
            {"op": "CREATE", "observation_ref": "o1", "fact_text": "喜欢看动漫",
             "reason_code": "direct_durable_statement"}
        ],
        source_evidence=[
            _source_evidence(
                source_message_id=3201, conversation_id="conv-s3d2-r2",
                fingerprint="fp-s3d2-r2", excerpt="喜欢看动漫",
            )
        ],
        candidate_allowlist=[],
    )
    obs2 = _observation(
        ref="o2",
        display_text="偏好搞笑类型的动漫",
        semantic={"subject": "user", "predicate": "prefers_genre", "object": "comedy"},
        source_message_ids=[3202],
    )
    obs2["topic_label"] = "动漫。"
    second = await _apply_validated(
        session_id=session_id,
        worldline="steins_gate",
        identity_mode="self",
        observations=[obs2],
        operations=[
            {"op": "CREATE", "observation_ref": "o2", "fact_text": "偏好搞笑类型的动漫",
             "reason_code": "direct_durable_statement"}
        ],
        source_evidence=[
            _source_evidence(
                source_message_id=3202, conversation_id="conv-s3d2-r2",
                fingerprint="fp-s3d2-r2b", excerpt="偏好搞笑类型的动漫",
            )
        ],
        candidate_allowlist=[],
    )
    topics = await _topic_rows(session_id, "self")
    assert len(topics) == 1, "normalized-equivalent labels must reuse one topic"
    assert await _fact_topic_id(first["created_facts"][0]["fact_id"]) == topics[0]["topic_id"]
    assert await _fact_topic_id(second["created_facts"][0]["fact_id"]) == topics[0]["topic_id"]


@pytest.mark.asyncio
async def test_s3d2_r3_same_label_identity_isolated(isolated_store):
    """R3: same normalized label under self vs okabe → distinct topic rows."""
    session_id = f"s3d2-r3-{uuid4().hex[:8]}"
    await _apply_validated(
        session_id=session_id,
        worldline="steins_gate",
        identity_mode="self",
        observations=[
            _observation(
                ref="o1", display_text="喜欢看动漫",
                semantic={"subject": "user", "predicate": "likes", "object": "anime"},
                source_message_ids=[3301],
            )
        ],
        operations=[
            {"op": "CREATE", "observation_ref": "o1", "fact_text": "喜欢看动漫",
             "reason_code": "direct_durable_statement"}
        ],
        source_evidence=[
            _source_evidence(
                source_message_id=3301, conversation_id="conv-s3d2-r3",
                fingerprint="fp-s3d2-r3a", excerpt="喜欢看动漫",
            )
        ],
        candidate_allowlist=[],
    )
    okabe = await _apply_validated(
        session_id=session_id,
        worldline="steins_gate",
        identity_mode="okabe",
        observations=[
            _observation(
                ref="o1", display_text="喜欢吃水果",
                semantic={"subject": "user", "predicate": "likes", "object": "fruit"},
                source_message_ids=[3302],
            )
        ],
        operations=[
            {"op": "CREATE", "observation_ref": "o1", "fact_text": "喜欢吃水果",
             "reason_code": "direct_durable_statement"}
        ],
        source_evidence=[
            _source_evidence(
                source_message_id=3302, conversation_id="conv-s3d2-r3",
                fingerprint="fp-s3d2-r3b", excerpt="喜欢吃水果",
            )
        ],
        candidate_allowlist=[],
    )
    topics_self = await _topic_rows(session_id, "self")
    topics_okabe = await _topic_rows(session_id, "okabe")
    assert len(topics_self) == 1 and len(topics_okabe) == 1
    assert topics_self[0]["topic_id"] != topics_okabe[0]["topic_id"], (
        "identity_mode must isolate topic scope"
    )
    assert await _fact_topic_id(okabe["created_facts"][0]["fact_id"]) == topics_okabe[0]["topic_id"]


@pytest.mark.asyncio
async def test_s3d2_r4_candidate_only_no_topic(isolated_store):
    """R4: candidate-only CREATE must not create a topic."""
    session_id = f"s3d2-r4-{uuid4().hex[:8]}"
    await _apply_validated(
        session_id=session_id,
        worldline="steins_gate",
        identity_mode="self",
        observations=[
            _observation(
                ref="o1", display_text="可能喜欢咖啡",
                semantic={"subject": "user", "predicate": "maybe", "object": "coffee"},
                source_message_ids=[3401],
                confidence=0.4,
            )
        ],
        operations=[
            {"op": "CREATE", "observation_ref": "o1", "fact_text": "可能喜欢咖啡",
             "reason_code": "low_confidence"}
        ],
        source_evidence=[
            _source_evidence(
                source_message_id=3401, conversation_id="conv-s3d2-r4",
                fingerprint="fp-s3d2-r4", excerpt="可能喜欢咖啡",
            )
        ],
        candidate_allowlist=[],
    )
    assert await _topic_rows(session_id, "self") == [], (
        "candidate-only CREATE must not materialize a topic"
    )


@pytest.mark.asyncio
async def test_s3d2_r5_episodic_no_topic(isolated_store):
    """R5: episodic CREATE must not create a topic."""
    session_id = f"s3d2-r5-{uuid4().hex[:8]}"
    await _apply_validated(
        session_id=session_id,
        worldline="steins_gate",
        identity_mode="self",
        observations=[
            _observation(
                ref="o1", display_text="上周去了秋叶原",
                semantic={"subject": "user", "predicate": "went", "object": "akiba"},
                source_message_ids=[3501],
                memory_class="episodic",
            )
        ],
        operations=[
            {"op": "CREATE", "observation_ref": "o1", "fact_text": "上周去了秋叶原",
             "reason_code": "contextual_event"}
        ],
        source_evidence=[
            _source_evidence(
                source_message_id=3501, conversation_id="conv-s3d2-r5",
                fingerprint="fp-s3d2-r5", excerpt="上周去了秋叶原",
            )
        ],
        candidate_allowlist=[],
    )
    assert await _topic_rows(session_id, "self") == [], (
        "episodic CREATE must not materialize a topic"
    )


@pytest.mark.asyncio
async def test_s3d2_r6_reject_no_topic(isolated_store):
    """R6: REJECT must not create a topic."""
    session_id = f"s3d2-r6-{uuid4().hex[:8]}"
    await _apply_validated(
        session_id=session_id,
        worldline="steins_gate",
        identity_mode="self",
        observations=[
            _observation(
                ref="o1", display_text="推荐过动漫",
                semantic={"subject": "user", "predicate": "recommended", "object": "anime"},
                source_message_ids=[3601],
                confidence=0.5,
            )
        ],
        operations=[
            {"op": "REJECT", "observation_ref": "o1", "fact_text": "推荐过动漫",
             "reason_code": "conversational_act"}
        ],
        source_evidence=[
            _source_evidence(
                source_message_id=3601, conversation_id="conv-s3d2-r6",
                fingerprint="fp-s3d2-r6", excerpt="推荐过动漫",
            )
        ],
        candidate_allowlist=[],
    )
    assert await _topic_rows(session_id, "self") == [], "REJECT must not materialize a topic"


@pytest.mark.asyncio
async def test_s3d2_r7_failed_batch_rolls_back_topic(isolated_store, monkeypatch):
    """R7: failure after topic creation rolls back topic + whole fact batch."""
    from app.services.memory_v11 import reconciler as recon_mod

    async def boom(*args, **kwargs):
        raise RuntimeError("injected insert_version_embedding failure")

    monkeypatch.setattr(recon_mod, "insert_version_embedding", boom)
    session_id = f"s3d2-r7-{uuid4().hex[:8]}"
    with pytest.raises(Exception):
        await _apply_validated(
            session_id=session_id,
            worldline="steins_gate",
            identity_mode="self",
            observations=[
                _observation(
                    ref="o1", display_text="喜欢看动漫",
                    semantic={"subject": "user", "predicate": "likes", "object": "anime"},
                    source_message_ids=[3701],
                )
            ],
            operations=[
                {"op": "CREATE", "observation_ref": "o1", "fact_text": "喜欢看动漫",
                 "reason_code": "direct_durable_statement"}
            ],
            source_evidence=[
                _source_evidence(
                    source_message_id=3701, conversation_id="conv-s3d2-r7",
                    fingerprint="fp-s3d2-r7", excerpt="喜欢看动漫",
                )
            ],
            candidate_allowlist=[],
        )
    assert await _topic_rows(session_id, "self") == [], (
        "failed batch must roll back the newly created topic (no orphan)"
    )
    db = await get_db("steins_gate", "memory")
    try:
        n = await (
            await db.execute(
                "SELECT COUNT(*) AS n FROM stable_facts WHERE session_id=?",
                (session_id,),
            )
        ).fetchone()
        assert int(n["n"]) == 0
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_s3d2_e_dual_match_conflict_fails_closed(isolated_store):
    """E: canonical=A + alias=B for one token → topic_conflict, never a pick."""
    contracts = _contracts()
    from app.services.memory_v11.topics import resolve_or_create_topic

    session_id = f"s3d2-e-{uuid4().hex[:8]}"
    db = await get_db("steins_gate", "memory")
    try:
        now = "2026-08-01T00:00:00+00:00"
        await db.execute("BEGIN IMMEDIATE")
        await db.execute(
            """INSERT INTO memory_topics(
                   topic_id, session_id, identity_mode, normalized_label,
                   display_label, created_at, updated_at
               ) VALUES('t-a', ?, 'self', 'anime', 'anime', ?, ?)""",
            (session_id, now, now),
        )
        await db.execute(
            """INSERT INTO memory_topics(
                   topic_id, session_id, identity_mode, normalized_label,
                   display_label, created_at, updated_at
               ) VALUES('t-b', ?, 'self', 'other', 'other', ?, ?)""",
            (session_id, now, now),
        )
        await db.execute(
            """INSERT INTO memory_topic_aliases(
                   session_id, identity_mode, normalized_alias, topic_id
               ) VALUES(?, 'self', 'anime', 't-b')""",
            (session_id,),
        )
        with pytest.raises(contracts.MemoryValidationError) as exc_info:
            await resolve_or_create_topic(
                db,
                session_id=session_id,
                identity_mode="self",
                display_label="anime",
                normalized_label="anime",
            )
        assert exc_info.value.code == "topic_conflict"
        await db.rollback()
    finally:
        await db.close()


# ---------------------------------------------------------------------------
# MEMORY-V11-S3D-4: D18 bounded contextual reanalysis — reconciler surface.
# ---------------------------------------------------------------------------


async def _d18_candidate(
    session_id: str,
    *,
    source_message_id: int,
    display_text: str = "在看一部动漫",
    confidence: float = 0.4,
) -> str:
    """Real reconciler low-confidence CREATE → candidate observation id."""
    result = await _apply_validated(
        session_id=session_id,
        worldline="steins_gate",
        identity_mode="self",
        observations=[
            _observation(
                ref="o1",
                display_text=display_text,
                semantic={"subject": "user", "predicate": "watching", "object": "anime"},
                source_message_ids=[source_message_id],
                confidence=confidence,
            )
        ],
        operations=[
            {"op": "CREATE", "observation_ref": "o1", "fact_text": display_text,
             "reason_code": "low_confidence"}
        ],
        source_evidence=[
            _source_evidence(
                source_message_id=source_message_id,
                conversation_id=f"conv-d18-{source_message_id}",
                fingerprint=f"fp-d18-{source_message_id}",
                excerpt=display_text,
            )
        ],
        candidate_allowlist=[],
    )
    db = await get_db("steins_gate", "memory")
    try:
        row = await (
            await db.execute(
                """SELECT observation_id FROM memory_observations
                    WHERE session_id=? AND display_text=? AND status='candidate'
                    ORDER BY created_at DESC LIMIT 1""",
                (session_id, display_text),
            )
        ).fetchone()
        assert row is not None
        return str(row["observation_id"])
    finally:
        await db.close()


def _d18_resolve_ops(
    *,
    op: str,
    new_text: str,
    source_message_id: int,
    target_id: str,
    confidence: float = 0.96,
    memory_class: str = "stable_candidate",
    expires_at: str | None = None,
    semantic: dict | None = None,
    fact_target: str | None = None,
    expected_version: int | None = None,
    reanalysis_allowlist: list[str] | None = None,
    op_override: dict | None = None,
) -> dict:
    obs = {
        "observation_ref": "r1",
        "reanalysis_target_observation_id": target_id,
        "source_message_ids": [source_message_id],
        "display_text": new_text,
        "semantic": semantic
        or {"subject": "user", "predicate": "resolved", "object": new_text[:12]},
        "evidence_kind": "direct_user",
        "memory_class": memory_class,
        "confidence": confidence,
        "topic_label": None,
        "expires_at": expires_at,
    }
    op_dict = {
        "op": op,
        "observation_ref": "r1",
        "fact_text": new_text,
        "reason_code": "d18",
    }
    if op_override is not None:
        op_dict = op_override
    elif fact_target is not None:
        op_dict["target_fact_id"] = fact_target
        op_dict["expected_version"] = expected_version
    return {
        "observations": [obs],
        "operations": [op_dict],
        "source_evidence": [
            _source_evidence(
                source_message_id=source_message_id,
                conversation_id=f"conv-d18-{source_message_id}",
                fingerprint=f"fp-d18-{source_message_id}",
                excerpt=new_text,
            )
        ],
        "candidate_allowlist": [fact_target] if fact_target else [],
        "reanalysis_allowlist": reanalysis_allowlist if reanalysis_allowlist is not None else [target_id],
    }


@pytest.mark.asyncio
async def test_s3d4_d16_target_not_allowed(isolated_store):
    """Target outside frozen reanalysis_allowlist → fail closed, zero mutation."""
    contracts = _contracts()
    session_id = f"s3d4-d16-{uuid4().hex[:8]}"
    target_id = await _d18_candidate(session_id, source_message_id=8101)
    kwargs = _d18_resolve_ops(
        op="CREATE", new_text="解析后", source_message_id=8102, target_id=target_id,
        reanalysis_allowlist=[],  # frozen allowlist does NOT contain the target
    )
    with pytest.raises(contracts.MemoryValidationError) as exc_info:
        await _apply_validated(session_id=session_id, worldline="steins_gate",
                               identity_mode="self", **kwargs)
    assert exc_info.value.code == "reanalysis_target_not_allowed"
    db = await get_db("steins_gate", "memory")
    try:
        row = await (await db.execute(
            "SELECT status, reanalysis_count FROM memory_observations WHERE observation_id=?",
            (target_id,),
        )).fetchone()
    finally:
        await db.close()
    assert row["status"] == "candidate" and int(row["reanalysis_count"]) == 0


@pytest.mark.asyncio
async def test_s3d4_d17_target_reused(isolated_store):
    """Same target referenced by two output observations → fail closed."""
    contracts = _contracts()
    session_id = f"s3d4-d17-{uuid4().hex[:8]}"
    target_id = await _d18_candidate(session_id, source_message_id=8111)
    kwargs = _d18_resolve_ops(
        op="CREATE", new_text="解析后", source_message_id=8112, target_id=target_id,
    )
    second_obs = dict(kwargs["observations"][0])
    second_obs["observation_ref"] = "r2"
    kwargs["observations"].append(second_obs)
    kwargs["operations"].append(
        {"op": "REJECT", "observation_ref": "r2", "fact_text": "解析后", "reason_code": "d18"}
    )
    with pytest.raises(contracts.MemoryValidationError) as exc_info:
        await _apply_validated(session_id=session_id, worldline="steins_gate",
                               identity_mode="self", **kwargs)
    assert exc_info.value.code == "reanalysis_target_reused"


@pytest.mark.asyncio
async def test_s3d4_d17b_two_distinct_targets_rejected(isolated_store):
    """Two DIFFERENT targets in one message → fail closed (single-target rule)."""
    contracts = _contracts()
    session_id = f"s3d4-d17b-{uuid4().hex[:8]}"
    t1 = await _d18_candidate(session_id, source_message_id=8121, display_text="候选一")
    t2 = await _d18_candidate(session_id, source_message_id=8122, display_text="候选二")
    obs1 = {
        "observation_ref": "r1", "reanalysis_target_observation_id": t1,
        "source_message_ids": [8123], "display_text": "解析一",
        "semantic": {"subject": "user", "predicate": "a", "object": "x"},
        "evidence_kind": "direct_user", "memory_class": "stable_candidate",
        "confidence": 0.96, "topic_label": None, "expires_at": None,
    }
    obs2 = {
        "observation_ref": "r2", "reanalysis_target_observation_id": t2,
        "source_message_ids": [8123], "display_text": "解析二",
        "semantic": {"subject": "user", "predicate": "b", "object": "y"},
        "evidence_kind": "direct_user", "memory_class": "stable_candidate",
        "confidence": 0.96, "topic_label": None, "expires_at": None,
    }
    with pytest.raises(contracts.MemoryValidationError) as exc_info:
        await _apply_validated(
            session_id=session_id, worldline="steins_gate", identity_mode="self",
            observations=[obs1, obs2],
            operations=[
                {"op": "CREATE", "observation_ref": "r1", "fact_text": "解析一", "reason_code": "d18"},
                {"op": "CREATE", "observation_ref": "r2", "fact_text": "解析二", "reason_code": "d18"},
            ],
            source_evidence=[
                _source_evidence(source_message_id=8123, conversation_id="conv-8123",
                                 fingerprint="fp-8123", excerpt="解析")
            ],
            candidate_allowlist=[],
            reanalysis_allowlist=[t1, t2],
        )
    assert exc_info.value.code == "reanalysis_target_reused"


@pytest.mark.asyncio
async def test_s3d4_d18_target_unconsumed(isolated_store):
    """Targeted observation with NO consuming operation → fail closed."""
    contracts = _contracts()
    session_id = f"s3d4-d18-{uuid4().hex[:8]}"
    target_id = await _d18_candidate(session_id, source_message_id=8131)
    kwargs = _d18_resolve_ops(
        op="CREATE", new_text="解析后", source_message_id=8132, target_id=target_id,
    )
    kwargs["operations"] = []  # target marked resolved but no operation consumes it
    with pytest.raises(contracts.MemoryValidationError) as exc_info:
        await _apply_validated(session_id=session_id, worldline="steins_gate",
                               identity_mode="self", **kwargs)
    assert exc_info.value.code == "reanalysis_target_unconsumed"


@pytest.mark.asyncio
async def test_s3d4_d19_no_target_ordinary_unchanged(isolated_store):
    """Context offered but model does not target → old observation untouched."""
    session_id = f"s3d4-d19-{uuid4().hex[:8]}"
    target_id = await _d18_candidate(session_id, source_message_id=8141)
    result = await _apply_validated(
        session_id=session_id, worldline="steins_gate", identity_mode="self",
        observations=[
            _observation(
                ref="o1", display_text="新话题",
                semantic={"subject": "user", "predicate": "new", "object": "topic"},
                source_message_ids=[8142],
            )
        ],
        operations=[
            {"op": "CREATE", "observation_ref": "o1", "fact_text": "新话题",
             "reason_code": "direct_durable_statement"}
        ],
        source_evidence=[
            _source_evidence(source_message_id=8142, conversation_id="conv-8142",
                             fingerprint="fp-8142", excerpt="新话题")
        ],
        candidate_allowlist=[],
        reanalysis_allowlist=[target_id],
    )
    assert len(result["created_facts"]) == 1
    db = await get_db("steins_gate", "memory")
    try:
        row = await (await db.execute(
            "SELECT status, reanalysis_count FROM memory_observations WHERE observation_id=?",
            (target_id,),
        )).fetchone()
    finally:
        await db.close()
    assert row["status"] == "candidate" and int(row["reanalysis_count"]) == 0, (
        "merely offered observations must not be mutated"
    )


@pytest.mark.asyncio
async def test_s3d4_d20_legacy_caller_without_allowlist(isolated_store):
    """Direct callers without reanalysis_allowlist keep working (backward compat)."""
    session_id = f"s3d4-d20-{uuid4().hex[:8]}"
    result = await _apply_validated(
        session_id=session_id, worldline="steins_gate", identity_mode="self",
        observations=[
            _observation(
                ref="o1", display_text="普通事实",
                semantic={"subject": "user", "predicate": "x", "object": "y"},
                source_message_ids=[8151],
            )
        ],
        operations=[
            {"op": "CREATE", "observation_ref": "o1", "fact_text": "普通事实",
             "reason_code": "direct_durable_statement"}
        ],
        source_evidence=[
            _source_evidence(source_message_id=8151, conversation_id="conv-8151",
                             fingerprint="fp-8151", excerpt="普通事实")
        ],
        candidate_allowlist=[],
    )
    assert len(result["created_facts"]) == 1
    db = await get_db("steins_gate", "memory")
    try:
        row = await (await db.execute(
            "SELECT reanalysis_count FROM memory_observations WHERE session_id=?",
            (session_id,),
        )).fetchone()
    finally:
        await db.close()
    assert int(row["reanalysis_count"]) == 0


@pytest.mark.asyncio
async def test_s3d4_d21_23_same_row_dual_source_count(isolated_store):
    """D21/D22/D23: same observation_id; old+current sources; count 0→1."""
    session_id = f"s3d4-d21-{uuid4().hex[:8]}"
    target_id = await _d18_candidate(session_id, source_message_id=8161)
    kwargs = _d18_resolve_ops(
        op="CREATE", new_text="解析后", source_message_id=8162, target_id=target_id,
    )
    await _apply_validated(session_id=session_id, worldline="steins_gate",
                           identity_mode="self", **kwargs)
    db = await get_db("steins_gate", "memory")
    try:
        row = await (await db.execute(
            "SELECT status, reanalysis_count FROM memory_observations WHERE observation_id=?",
            (target_id,),
        )).fetchone()
        assert row is not None
        assert int(row["reanalysis_count"]) == 1
        assert str(row["status"]) == "attached"
        sources = await (await db.execute(
            """SELECT source_message_id FROM memory_observation_sources
                WHERE observation_id=? ORDER BY source_message_id""",
            (target_id,),
        )).fetchall()
        assert [int(s["source_message_id"]) for s in sources] == [8161, 8162]
        n = await (await db.execute(
            """SELECT COUNT(*) AS n FROM memory_observations
                WHERE session_id=? AND display_text='解析后'""",
            (session_id,),
        )).fetchone()
        assert int(n["n"]) == 1, "no parallel observation row"
        evidence = await (await db.execute(
            "SELECT COUNT(*) AS n FROM stable_fact_evidence WHERE observation_id=?",
            (target_id,),
        )).fetchone()
        assert int(evidence["n"]) == 1, "resolved observation is primary evidence"
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_s3d4_d24_fingerprint_recomputed(isolated_store):
    """Semantic fingerprint follows the resolved content, never stays stale."""
    session_id = f"s3d4-d24-{uuid4().hex[:8]}"
    target_id = await _d18_candidate(session_id, source_message_id=8171)
    db = await get_db("steins_gate", "memory")
    try:
        old_fp = (await (await db.execute(
            "SELECT semantic_fingerprint FROM memory_observations WHERE observation_id=?",
            (target_id,),
        )).fetchone())["semantic_fingerprint"]
    finally:
        await db.close()
    kwargs = _d18_resolve_ops(
        op="CREATE", new_text="解析后内容", source_message_id=8172, target_id=target_id,
        semantic={"subject": "user", "predicate": "likes_work", "object": "konosuba"},
    )
    await _apply_validated(session_id=session_id, worldline="steins_gate",
                           identity_mode="self", **kwargs)
    db = await get_db("steins_gate", "memory")
    try:
        new_fp = (await (await db.execute(
            "SELECT semantic_fingerprint FROM memory_observations WHERE observation_id=?",
            (target_id,),
        )).fetchone())["semantic_fingerprint"]
    finally:
        await db.close()
    assert new_fp != old_fp, "fingerprint must be recomputed from resolved content"


@pytest.mark.asyncio
async def test_s3d4_d25_expires_at_applied_including_null_clear(isolated_store):
    """Model expires_at applied exactly; explicit null clears the old expiry."""
    session_id = f"s3d4-d25-{uuid4().hex[:8]}"
    target_id = await _d18_candidate(session_id, source_message_id=8181)
    db = await get_db("steins_gate", "memory")
    try:
        await db.execute(
            "UPDATE memory_observations SET expires_at=? WHERE observation_id=?",
            ("2025-01-01T00:00:00+00:00", target_id),
        )
        await db.commit()
    finally:
        await db.close()
    kwargs = _d18_resolve_ops(
        op="CREATE", new_text="解析后", source_message_id=8182, target_id=target_id,
        expires_at=None,  # explicit null clears the old expiry
    )
    await _apply_validated(session_id=session_id, worldline="steins_gate",
                           identity_mode="self", **kwargs)
    db = await get_db("steins_gate", "memory")
    try:
        row = await (await db.execute(
            "SELECT expires_at FROM memory_observations WHERE observation_id=?",
            (target_id,),
        )).fetchone()
    finally:
        await db.close()
    assert row["expires_at"] is None, "null expires_at must clear the old expiry"


@pytest.mark.asyncio
async def test_s3d4_d26_forged_old_source_rejected(isolated_store):
    """Output listing the OLD source id (not current evidence) → fail closed."""
    contracts = _contracts()
    session_id = f"s3d4-d26-{uuid4().hex[:8]}"
    target_id = await _d18_candidate(session_id, source_message_id=8191)
    kwargs = _d18_resolve_ops(
        op="CREATE", new_text="解析后", source_message_id=8192, target_id=target_id,
    )
    kwargs["observations"][0]["source_message_ids"] = [8191]  # forged OLD source
    with pytest.raises(contracts.MemoryValidationError) as exc_info:
        await _apply_validated(session_id=session_id, worldline="steins_gate",
                               identity_mode="self", **kwargs)
    assert exc_info.value.code == "unknown_source_message_id"
    db = await get_db("steins_gate", "memory")
    try:
        row = await (await db.execute(
            "SELECT status, reanalysis_count FROM memory_observations WHERE observation_id=?",
            (target_id,),
        )).fetchone()
    finally:
        await db.close()
    assert row["status"] == "candidate" and int(row["reanalysis_count"]) == 0


@pytest.mark.asyncio
async def test_s3d4_d27_reject_target(isolated_store):
    session_id = f"s3d4-d27-{uuid4().hex[:8]}"
    target_id = await _d18_candidate(session_id, source_message_id=8201)
    kwargs = _d18_resolve_ops(
        op="REJECT", new_text="拒绝此解析", source_message_id=8202, target_id=target_id,
    )
    await _apply_validated(session_id=session_id, worldline="steins_gate",
                           identity_mode="self", **kwargs)
    db = await get_db("steins_gate", "memory")
    try:
        row = await (await db.execute(
            "SELECT status, reanalysis_count FROM memory_observations WHERE observation_id=?",
            (target_id,),
        )).fetchone()
        facts = await (await db.execute(
            "SELECT COUNT(*) AS n FROM stable_facts WHERE session_id=?", (session_id,)
        )).fetchone()
    finally:
        await db.close()
    assert row["status"] == "rejected" and int(row["reanalysis_count"]) == 1
    assert int(facts["n"]) == 0


@pytest.mark.asyncio
async def test_s3d4_d28_candidate_create_target(isolated_store):
    """Low-confidence CREATE on target → stays candidate, count=1, no D18 again."""
    session_id = f"s3d4-d28-{uuid4().hex[:8]}"
    target_id = await _d18_candidate(session_id, source_message_id=8211)
    kwargs = _d18_resolve_ops(
        op="CREATE", new_text="仍低置信", source_message_id=8212, target_id=target_id,
        confidence=0.4,
    )
    await _apply_validated(session_id=session_id, worldline="steins_gate",
                           identity_mode="self", **kwargs)
    db = await get_db("steins_gate", "memory")
    try:
        row = await (await db.execute(
            "SELECT status, reanalysis_count FROM memory_observations WHERE observation_id=?",
            (target_id,),
        )).fetchone()
    finally:
        await db.close()
    assert row["status"] == "candidate" and int(row["reanalysis_count"]) == 1


@pytest.mark.asyncio
async def test_s3d4_d29_stable_create_target(isolated_store):
    """High-confidence CREATE on target → attached + fact + vector + evidence."""
    session_id = f"s3d4-d29-{uuid4().hex[:8]}"
    target_id = await _d18_candidate(session_id, source_message_id=8221)
    kwargs = _d18_resolve_ops(
        op="CREATE", new_text="喜欢看动漫", source_message_id=8222, target_id=target_id,
        confidence=0.96,
    )
    await _apply_validated(session_id=session_id, worldline="steins_gate",
                           identity_mode="self", **kwargs)
    db = await get_db("steins_gate", "memory")
    try:
        row = await (await db.execute(
            "SELECT status, reanalysis_count FROM memory_observations WHERE observation_id=?",
            (target_id,),
        )).fetchone()
        fact = await (await db.execute(
            """SELECT f.fact_id FROM stable_facts f
                 JOIN stable_fact_evidence e ON e.fact_id = f.fact_id
                WHERE e.observation_id=?""",
            (target_id,),
        )).fetchone()
        vector = await (await db.execute(
            """SELECT COUNT(*) AS n FROM stable_fact_version_embeddings e
                 JOIN stable_facts f ON f.fact_id = e.fact_id
                WHERE f.fact_id=?""",
            (fact["fact_id"],),
        )).fetchone()
    finally:
        await db.close()
    assert row["status"] == "attached" and int(row["reanalysis_count"]) == 1
    assert fact is not None
    assert int(vector["n"]) == 1


@pytest.mark.asyncio
async def test_s3d4_d30_episodic_target(isolated_store):
    """Episodic CREATE on target → attached + Experience with SAME observation_id."""
    session_id = f"s3d4-d30-{uuid4().hex[:8]}"
    target_id = await _d18_candidate(session_id, source_message_id=8231)
    kwargs = _d18_resolve_ops(
        op="CREATE", new_text="上周去了秋叶原", source_message_id=8232,
        target_id=target_id, memory_class="episodic",
    )
    await _apply_validated(session_id=session_id, worldline="steins_gate",
                           identity_mode="self", **kwargs)
    db = await get_db("steins_gate", "memory")
    try:
        row = await (await db.execute(
            "SELECT status, reanalysis_count, memory_class FROM memory_observations WHERE observation_id=?",
            (target_id,),
        )).fetchone()
        exp = await (await db.execute(
            "SELECT observation_id FROM experiences WHERE observation_id=?",
            (target_id,),
        )).fetchone()
    finally:
        await db.close()
    assert row["status"] == "attached" and int(row["reanalysis_count"]) == 1
    assert row["memory_class"] == "episodic"
    assert exp is not None, "experience must reference the SAME observation_id"
    assert str(exp["observation_id"]) == target_id


@pytest.mark.asyncio
async def test_s3d4_d31_attach_target(isolated_store):
    """ATTACH on target → attached + supporting evidence."""
    session_id = f"s3d4-d31-{uuid4().hex[:8]}"
    target_id = await _d18_candidate(session_id, source_message_id=8241)
    fact_result = await _apply_validated(
        session_id=session_id, worldline="steins_gate", identity_mode="self",
        observations=[
            _observation(
                ref="seed", display_text="喜欢看动漫",
                semantic={"subject": "user", "predicate": "likes", "object": "anime"},
                source_message_ids=[8243],
            )
        ],
        operations=[
            {"op": "CREATE", "observation_ref": "seed", "fact_text": "喜欢看动漫",
             "reason_code": "direct_durable_statement"}
        ],
        source_evidence=[
            _source_evidence(source_message_id=8243, conversation_id="conv-8243",
                             fingerprint="fp-8243", excerpt="喜欢看动漫")
        ],
        candidate_allowlist=[],
    )
    fact_id = fact_result["created_facts"][0]["fact_id"]
    kwargs = _d18_resolve_ops(
        op="ATTACH", new_text="喜欢看动漫", source_message_id=8242, target_id=target_id,
        fact_target=fact_id, expected_version=1,
    )
    await _apply_validated(session_id=session_id, worldline="steins_gate",
                           identity_mode="self", **kwargs)
    db = await get_db("steins_gate", "memory")
    try:
        row = await (await db.execute(
            "SELECT status, reanalysis_count FROM memory_observations WHERE observation_id=?",
            (target_id,),
        )).fetchone()
        evidence = await (await db.execute(
            """SELECT evidence_role FROM stable_fact_evidence
                WHERE observation_id=? AND fact_id=?""",
            (target_id, fact_id),
        )).fetchone()
    finally:
        await db.close()
    assert row["status"] == "attached" and int(row["reanalysis_count"]) == 1
    assert evidence is not None and str(evidence["evidence_role"]) == "supporting"


@pytest.mark.asyncio
async def test_s3d4_d32_refine_target(isolated_store):
    """REFINE on target → attached + new fact version + primary evidence."""
    session_id = f"s3d4-d32-{uuid4().hex[:8]}"
    target_id = await _d18_candidate(session_id, source_message_id=8251)
    fact_result = await _apply_validated(
        session_id=session_id, worldline="steins_gate", identity_mode="self",
        observations=[
            _observation(
                ref="seed", display_text="喜欢咖啡",
                semantic={"subject": "user", "predicate": "likes", "object": "coffee"},
                source_message_ids=[8253],
            )
        ],
        operations=[
            {"op": "CREATE", "observation_ref": "seed", "fact_text": "喜欢咖啡",
             "reason_code": "direct_durable_statement"}
        ],
        source_evidence=[
            _source_evidence(source_message_id=8253, conversation_id="conv-8253",
                             fingerprint="fp-8253", excerpt="喜欢咖啡")
        ],
        candidate_allowlist=[],
    )
    fact_id = fact_result["created_facts"][0]["fact_id"]
    kwargs = _d18_resolve_ops(
        op="REFINE", new_text="喜欢黑咖啡", source_message_id=8252, target_id=target_id,
        fact_target=fact_id, expected_version=1,
    )
    await _apply_validated(session_id=session_id, worldline="steins_gate",
                           identity_mode="self", **kwargs)
    db = await get_db("steins_gate", "memory")
    try:
        row = await (await db.execute(
            "SELECT status, reanalysis_count FROM memory_observations WHERE observation_id=?",
            (target_id,),
        )).fetchone()
        active = await (await db.execute(
            "SELECT active_version FROM stable_facts WHERE fact_id=?", (fact_id,)
        )).fetchone()
        evidence = await (await db.execute(
            """SELECT evidence_role FROM stable_fact_evidence
                WHERE observation_id=? AND fact_id=? AND version_no=2""",
            (target_id, fact_id),
        )).fetchone()
    finally:
        await db.close()
    assert row["status"] == "attached" and int(row["reanalysis_count"]) == 1
    assert int(active["active_version"]) == 2
    assert evidence is not None and str(evidence["evidence_role"]) == "primary"


@pytest.mark.asyncio
async def test_s3d4_d33_supersede_target(isolated_store):
    """SUPERSEDE on target → attached + new fact version + primary evidence."""
    session_id = f"s3d4-d33-{uuid4().hex[:8]}"
    target_id = await _d18_candidate(session_id, source_message_id=8261)
    fact_result = await _apply_validated(
        session_id=session_id, worldline="steins_gate", identity_mode="self",
        observations=[
            _observation(
                ref="seed", display_text="喜欢咖啡",
                semantic={"subject": "user", "predicate": "likes", "object": "coffee"},
                source_message_ids=[8263],
            )
        ],
        operations=[
            {"op": "CREATE", "observation_ref": "seed", "fact_text": "喜欢咖啡",
             "reason_code": "direct_durable_statement"}
        ],
        source_evidence=[
            _source_evidence(source_message_id=8263, conversation_id="conv-8263",
                             fingerprint="fp-8263", excerpt="喜欢咖啡")
        ],
        candidate_allowlist=[],
    )
    fact_id = fact_result["created_facts"][0]["fact_id"]
    kwargs = _d18_resolve_ops(
        op="SUPERSEDE", new_text="现在更喜欢茶", source_message_id=8262, target_id=target_id,
        fact_target=fact_id, expected_version=1,
    )
    await _apply_validated(session_id=session_id, worldline="steins_gate",
                           identity_mode="self", **kwargs)
    db = await get_db("steins_gate", "memory")
    try:
        row = await (await db.execute(
            "SELECT status, reanalysis_count FROM memory_observations WHERE observation_id=?",
            (target_id,),
        )).fetchone()
        active = await (await db.execute(
            "SELECT active_version FROM stable_facts WHERE fact_id=?", (fact_id,)
        )).fetchone()
        evidence = await (await db.execute(
            """SELECT evidence_role FROM stable_fact_evidence
                WHERE observation_id=? AND fact_id=? AND version_no=2""",
            (target_id, fact_id),
        )).fetchone()
    finally:
        await db.close()
    assert row["status"] == "attached" and int(row["reanalysis_count"]) == 1
    assert int(active["active_version"]) == 2
    assert evidence is not None and str(evidence["evidence_role"]) == "primary"


@pytest.mark.asyncio
async def test_s3d4_d34_stale_target_fails_closed(isolated_store):
    """Racing target already resolved elsewhere → stale, whole batch rollback."""
    contracts = _contracts()
    session_id = f"s3d4-d34-{uuid4().hex[:8]}"
    target_id = await _d18_candidate(session_id, source_message_id=8271)
    db = await get_db("steins_gate", "memory")
    try:
        await db.execute(
            "UPDATE memory_observations SET reanalysis_count=1 WHERE observation_id=?",
            (target_id,),
        )
        await db.commit()
    finally:
        await db.close()
    kwargs = _d18_resolve_ops(
        op="CREATE", new_text="解析后", source_message_id=8272, target_id=target_id,
    )
    with pytest.raises(contracts.MemoryValidationError) as exc_info:
        await _apply_validated(session_id=session_id, worldline="steins_gate",
                               identity_mode="self", **kwargs)
    assert exc_info.value.code == "reanalysis_target_stale"
    db = await get_db("steins_gate", "memory")
    try:
        row = await (await db.execute(
            "SELECT reanalysis_count, display_text FROM memory_observations WHERE observation_id=?",
            (target_id,),
        )).fetchone()
        facts = await (await db.execute(
            "SELECT COUNT(*) AS n FROM stable_facts WHERE session_id=?", (session_id,)
        )).fetchone()
    finally:
        await db.close()
    assert int(row["reanalysis_count"]) == 1
    assert "在看一部动漫" in str(row["display_text"])
    assert int(facts["n"]) == 0


@pytest.mark.asyncio
async def test_s3d4_d35_evidence_conflict(isolated_store):
    """Candidate illegally backing stable fact evidence → fail closed."""
    contracts = _contracts()
    session_id = f"s3d4-d35-{uuid4().hex[:8]}"
    target_id = await _d18_candidate(session_id, source_message_id=8281)
    fact_result = await _apply_validated(
        session_id=session_id, worldline="steins_gate", identity_mode="self",
        observations=[
            _observation(
                ref="seed", display_text="独立事实",
                semantic={"subject": "user", "predicate": "x", "object": "y"},
                source_message_ids=[8283],
            )
        ],
        operations=[
            {"op": "CREATE", "observation_ref": "seed", "fact_text": "独立事实",
             "reason_code": "direct_durable_statement"}
        ],
        source_evidence=[
            _source_evidence(source_message_id=8283, conversation_id="conv-8283",
                             fingerprint="fp-8283", excerpt="独立事实")
        ],
        candidate_allowlist=[],
    )
    fact_id = fact_result["created_facts"][0]["fact_id"]
    db = await get_db("steins_gate", "memory")
    try:
        await db.execute(
            """INSERT INTO stable_fact_evidence(
                   fact_id, version_no, observation_id, evidence_role
               ) VALUES(?,1,?,'supporting')""",
            (fact_id, target_id),
        )
        await db.commit()
    finally:
        await db.close()
    kwargs = _d18_resolve_ops(
        op="CREATE", new_text="解析后", source_message_id=8282, target_id=target_id,
    )
    with pytest.raises(contracts.MemoryValidationError) as exc_info:
        await _apply_validated(session_id=session_id, worldline="steins_gate",
                               identity_mode="self", **kwargs)
    assert exc_info.value.code == "reanalysis_target_conflict"
    db = await get_db("steins_gate", "memory")
    try:
        row = await (await db.execute(
            "SELECT status, reanalysis_count FROM memory_observations WHERE observation_id=?",
            (target_id,),
        )).fetchone()
    finally:
        await db.close()
    assert row["status"] == "candidate" and int(row["reanalysis_count"]) == 0


@pytest.mark.asyncio
async def test_s3d4_d36_experience_conflict(isolated_store):
    """Candidate illegally backing an experience → fail closed."""
    contracts = _contracts()
    session_id = f"s3d4-d36-{uuid4().hex[:8]}"
    target_id = await _d18_candidate(session_id, source_message_id=8291)
    db = await get_db("steins_gate", "memory")
    try:
        await db.execute(
            """INSERT INTO experiences(
                   experience_id, session_id, conversation_id, identity_mode,
                   observation_id, display_text, semantic_json, semantic_fingerprint,
                   confidence, status, expires_at, created_at, updated_at, deleted_at
               ) VALUES(?,?, 'conv-corrupt', 'self', ?, '非法经历', '{}', 'fp-x',
                        0.9, 'active', NULL, '2026-08-01T00:00:00+00:00',
                        '2026-08-01T00:00:00+00:00', NULL)""",
            (str(uuid4()), session_id, target_id),
        )
        await db.commit()
    finally:
        await db.close()
    kwargs = _d18_resolve_ops(
        op="CREATE", new_text="解析后", source_message_id=8292, target_id=target_id,
    )
    with pytest.raises(contracts.MemoryValidationError) as exc_info:
        await _apply_validated(session_id=session_id, worldline="steins_gate",
                               identity_mode="self", **kwargs)
    assert exc_info.value.code == "reanalysis_target_conflict"
    db = await get_db("steins_gate", "memory")
    try:
        row = await (await db.execute(
            "SELECT status, reanalysis_count FROM memory_observations WHERE observation_id=?",
            (target_id,),
        )).fetchone()
    finally:
        await db.close()
    assert row["status"] == "candidate" and int(row["reanalysis_count"]) == 0


# ---------------------------------------------------------------------------
# MEMORY-V11-S3D-4 MICRO-REWORK: missing/null target fields + crash replay.
# ---------------------------------------------------------------------------


def _target_obs(
    target_id: str,
    new_text: str,
    source_message_id: int,
    **overrides,
) -> dict:
    obs = {
        "observation_ref": "r1",
        "reanalysis_target_observation_id": target_id,
        "source_message_ids": [source_message_id],
        "display_text": new_text,
        "semantic": {"subject": "user", "predicate": "resolved", "object": new_text[:12]},
        "evidence_kind": "direct_user",
        "memory_class": "stable_candidate",
        "confidence": 0.96,
        "topic_label": None,
        "expires_at": None,
    }
    obs.update(overrides)
    return obs


def _target_kwargs(
    *, session_id: str, obs: dict, source_message_id: int, fact_text: str | None = None
) -> dict:
    text = fact_text if fact_text is not None else obs.get("display_text", "解析后")
    return {
        "observations": [obs],
        "operations": [
            # Server-side fact_text independent of the observation payload so a
            # missing observation field is the ONLY defect under test (D44).
            {"op": "CREATE", "observation_ref": obs["observation_ref"],
             "fact_text": text, "reason_code": "d18"}
        ],
        "source_evidence": [
            _source_evidence(
                source_message_id=source_message_id,
                conversation_id=f"conv-d18-{source_message_id}",
                fingerprint=f"fp-d18-{source_message_id}",
                excerpt=text,
            )
        ],
        "candidate_allowlist": [],
        "reanalysis_allowlist": [obs["reanalysis_target_observation_id"]],
    }


@pytest.mark.asyncio
async def test_s3d4_d39_expires_absent_preserves(isolated_store):
    """P1-A: target output OMITS expires_at → old expiry preserved."""
    session_id = f"s3d4-d39-{uuid4().hex[:8]}"
    target_id = await _d18_candidate(session_id, source_message_id=8401)
    db = await get_db("steins_gate", "memory")
    try:
        await db.execute(
            "UPDATE memory_observations SET expires_at=? WHERE observation_id=?",
            ("2030-01-01T00:00:00+00:00", target_id),
        )
        await db.commit()
    finally:
        await db.close()
    obs = _target_obs(target_id, "解析后", 8402)
    obs.pop("expires_at")  # ABSENT
    await _apply_validated(session_id=session_id, worldline="steins_gate",
                           identity_mode="self", **_target_kwargs(session_id=session_id, obs=obs, source_message_id=8402))
    db = await get_db("steins_gate", "memory")
    try:
        row = await (await db.execute(
            "SELECT expires_at FROM memory_observations WHERE observation_id=?", (target_id,)
        )).fetchone()
    finally:
        await db.close()
    assert str(row["expires_at"]) == "2030-01-01T00:00:00+00:00", (
        "absent expires_at must preserve the existing expiry"
    )


@pytest.mark.asyncio
async def test_s3d4_d41_expires_value_replaces(isolated_store):
    """P1-A: target output expires_at=<value> → replaced."""
    session_id = f"s3d4-d41-{uuid4().hex[:8]}"
    target_id = await _d18_candidate(session_id, source_message_id=8411)
    db = await get_db("steins_gate", "memory")
    try:
        await db.execute(
            "UPDATE memory_observations SET expires_at=? WHERE observation_id=?",
            ("2030-01-01T00:00:00+00:00", target_id),
        )
        await db.commit()
    finally:
        await db.close()
    obs = _target_obs(
        target_id, "解析后", 8412, expires_at="2031-02-03T00:00:00+00:00"
    )
    await _apply_validated(session_id=session_id, worldline="steins_gate",
                           identity_mode="self", **_target_kwargs(session_id=session_id, obs=obs, source_message_id=8412))
    db = await get_db("steins_gate", "memory")
    try:
        row = await (await db.execute(
            "SELECT expires_at FROM memory_observations WHERE observation_id=?", (target_id,)
        )).fetchone()
    finally:
        await db.close()
    assert str(row["expires_at"]) == "2031-02-03T00:00:00+00:00"


@pytest.mark.asyncio
async def test_s3d4_d42_topic_absent_preserves(isolated_store):
    """P1-A: target output OMITS topic_label → old proposal preserved."""
    session_id = f"s3d4-d42-{uuid4().hex[:8]}"
    target_id = await _d18_candidate(session_id, source_message_id=8421)
    db = await get_db("steins_gate", "memory")
    try:
        await db.execute(
            "UPDATE memory_observations SET topic_label_proposal='动漫' WHERE observation_id=?",
            (target_id,),
        )
        await db.commit()
    finally:
        await db.close()
    obs = _target_obs(target_id, "解析后", 8422)
    obs.pop("topic_label")  # ABSENT
    await _apply_validated(session_id=session_id, worldline="steins_gate",
                           identity_mode="self", **_target_kwargs(session_id=session_id, obs=obs, source_message_id=8422))
    db = await get_db("steins_gate", "memory")
    try:
        row = await (await db.execute(
            "SELECT topic_label_proposal FROM memory_observations WHERE observation_id=?",
            (target_id,),
        )).fetchone()
    finally:
        await db.close()
    assert str(row["topic_label_proposal"]) == "动漫", (
        "absent topic_label must preserve the existing proposal"
    )


@pytest.mark.asyncio
async def test_s3d4_d43_topic_null_clears(isolated_store):
    """P1-A: target output topic_label=null → proposal cleared."""
    session_id = f"s3d4-d43-{uuid4().hex[:8]}"
    target_id = await _d18_candidate(session_id, source_message_id=8431)
    db = await get_db("steins_gate", "memory")
    try:
        await db.execute(
            "UPDATE memory_observations SET topic_label_proposal='动漫' WHERE observation_id=?",
            (target_id,),
        )
        await db.commit()
    finally:
        await db.close()
    obs = _target_obs(target_id, "解析后", 8432, topic_label=None)
    await _apply_validated(session_id=session_id, worldline="steins_gate",
                           identity_mode="self", **_target_kwargs(session_id=session_id, obs=obs, source_message_id=8432))
    db = await get_db("steins_gate", "memory")
    try:
        row = await (await db.execute(
            "SELECT topic_label_proposal FROM memory_observations WHERE observation_id=?",
            (target_id,),
        )).fetchone()
    finally:
        await db.close()
    assert row["topic_label_proposal"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field",
    ["display_text", "semantic", "evidence_kind", "memory_class", "confidence",
     "source_message_ids"],
)
async def test_s3d4_d44_incomplete_core_fields_fail_closed(isolated_store, field):
    """P1-A: missing required target field → reanalysis_target_incomplete, zero mutation."""
    contracts = _contracts()
    session_id = f"s3d4-d44-{field}-{uuid4().hex[:8]}"
    target_id = await _d18_candidate(session_id, source_message_id=8441)
    db = await get_db("steins_gate", "memory")
    try:
        before = dict((await (await db.execute(
            """SELECT display_text, semantic_json, semantic_fingerprint, evidence_kind,
                      memory_class, confidence, topic_label_proposal, expires_at,
                      status, reanalysis_count
                 FROM memory_observations WHERE observation_id=?""",
            (target_id,),
        )).fetchone()))
    finally:
        await db.close()

    obs = _target_obs(target_id, "解析后", 8442)
    obs.pop(field)
    with pytest.raises(contracts.MemoryValidationError) as exc_info:
        await _apply_validated(
            session_id=session_id, worldline="steins_gate", identity_mode="self",
            **_target_kwargs(session_id=session_id, obs=obs, source_message_id=8442),
        )
    assert exc_info.value.code == "reanalysis_target_incomplete"

    db = await get_db("steins_gate", "memory")
    try:
        after = dict((await (await db.execute(
            """SELECT display_text, semantic_json, semantic_fingerprint, evidence_kind,
                      memory_class, confidence, topic_label_proposal, expires_at,
                      status, reanalysis_count
                 FROM memory_observations WHERE observation_id=?""",
            (target_id,),
        )).fetchone()))
        sources_n = await (await db.execute(
            "SELECT COUNT(*) AS n FROM memory_observation_sources WHERE observation_id=?",
            (target_id,),
        )).fetchone()
        facts_n = await (await db.execute(
            "SELECT COUNT(*) AS n FROM stable_facts WHERE session_id=?", (session_id,)
        )).fetchone()
        exps_n = await (await db.execute(
            "SELECT COUNT(*) AS n FROM experiences WHERE session_id=?", (session_id,)
        )).fetchone()
    finally:
        await db.close()
    for key, value in before.items():
        assert after[key] == value, f"field {key} must be untouched"
    assert int(sources_n["n"]) == 1, "sources unchanged"
    assert int(facts_n["n"]) == 0, "no fact side effect"
    assert int(exps_n["n"]) == 0, "no experience side effect"


# ---------------------------------------------------------------------------
# MEMORY-V11-S3D-4 HOTFIX: episodic expiry alignment (observation authoritative).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode, expected",
    [
        ("absent", "2030-01-01T00:00:00+00:00"),  # A: ABSENT → old observation expiry
        ("null", None),  # B: explicit null → clear BOTH
    ],
    ids=["absent-preserves", "null-clears"],
)
async def test_s3d4_d45_episodic_target_expiry_alignment(
    isolated_store, mode, expected
):
    """Episodic D18 target: experience.expires_at == effective observation expiry."""
    from app.services.memory_v11.retrieval import select_experience_candidates

    session_id = f"s3d4-d45-{uuid4().hex[:8]}"
    target_id = await _d18_candidate(session_id, source_message_id=8501)
    db = await get_db("steins_gate", "memory")
    try:
        await db.execute(
            "UPDATE memory_observations SET expires_at=? WHERE observation_id=?",
            ("2030-01-01T00:00:00+00:00", target_id),
        )
        await db.commit()
    finally:
        await db.close()

    obs = _target_obs(target_id, "上周去了秋叶原", 8502, memory_class="episodic")
    if mode == "absent":
        obs.pop("expires_at")  # ABSENT → old value preserved
    else:
        obs["expires_at"] = None  # explicit null → clear

    await _apply_validated(
        session_id=session_id, worldline="steins_gate", identity_mode="self",
        **_target_kwargs(session_id=session_id, obs=obs, source_message_id=8502),
    )

    db = await get_db("steins_gate", "memory")
    try:
        obs_row = await (await db.execute(
            """SELECT status, reanalysis_count, expires_at FROM memory_observations
                WHERE observation_id=?""",
            (target_id,),
        )).fetchone()
        exp_row = await (await db.execute(
            """SELECT observation_id, expires_at FROM experiences
                WHERE observation_id=?""",
            (target_id,),
        )).fetchone()
    finally:
        await db.close()
    assert str(obs_row["status"]) == "attached"
    assert int(obs_row["reanalysis_count"]) == 1
    assert str(exp_row["observation_id"]) == target_id, "experience keeps SAME observation"
    if expected is None:
        assert obs_row["expires_at"] is None
        assert exp_row["expires_at"] is None
    else:
        assert str(obs_row["expires_at"]) == expected
        assert str(exp_row["expires_at"]) == expected, (
            "experience.expires_at must equal the effective observation expiry"
        )
    # The linked Experience must be retrievable under the aligned expiry.
    retrieved = await select_experience_candidates(
        session_id=session_id, worldline="steins_gate", identity_mode="self",
        query="秋叶原", limit=16, token_budget=1400,
    )
    if expected is None:
        assert retrieved, "null expiry must remain retrievable"
    else:
        assert retrieved, "future expiry must remain retrievable"


@pytest.mark.asyncio
async def test_s3d4_d46_episodic_target_expiry_replaced(isolated_store):
    """C: targeted episodic output replaces expiry on BOTH linked rows."""
    session_id = f"s3d4-d46-{uuid4().hex[:8]}"
    target_id = await _d18_candidate(session_id, source_message_id=8511)
    db = await get_db("steins_gate", "memory")
    try:
        await db.execute(
            "UPDATE memory_observations SET expires_at=? WHERE observation_id=?",
            ("2030-01-01T00:00:00+00:00", target_id),
        )
        await db.commit()
    finally:
        await db.close()
    obs = _target_obs(
        target_id, "上周去了咖啡厅", 8512,
        memory_class="episodic", expires_at="2031-02-03T00:00:00+00:00",
    )
    await _apply_validated(
        session_id=session_id, worldline="steins_gate", identity_mode="self",
        **_target_kwargs(session_id=session_id, obs=obs, source_message_id=8512),
    )
    db = await get_db("steins_gate", "memory")
    try:
        obs_row = await (await db.execute(
            "SELECT expires_at FROM memory_observations WHERE observation_id=?",
            (target_id,),
        )).fetchone()
        exp_row = await (await db.execute(
            "SELECT expires_at FROM experiences WHERE observation_id=?",
            (target_id,),
        )).fetchone()
    finally:
        await db.close()
    assert str(obs_row["expires_at"]) == "2031-02-03T00:00:00+00:00"
    assert str(exp_row["expires_at"]) == "2031-02-03T00:00:00+00:00"


@pytest.mark.asyncio
async def test_s3d4_d47_past_expiry_inherited_blocks_prompt_retrieval(isolated_store):
    """Product consequence: past expiry inherited → NOT prompt-eligible."""
    from app.services.memory_v11.retrieval import select_experience_candidates

    session_id = f"s3d4-d47-{uuid4().hex[:8]}"
    target_id = await _d18_candidate(session_id, source_message_id=8521)
    db = await get_db("steins_gate", "memory")
    try:
        await db.execute(
            "UPDATE memory_observations SET expires_at=? WHERE observation_id=?",
            ("2020-01-01T00:00:00+00:00", target_id),
        )
        await db.commit()
    finally:
        await db.close()
    obs = _target_obs(target_id, "很久以前的活动", 8522, memory_class="episodic")
    obs.pop("expires_at")  # ABSENT → old past expiry preserved
    await _apply_validated(
        session_id=session_id, worldline="steins_gate", identity_mode="self",
        **_target_kwargs(session_id=session_id, obs=obs, source_message_id=8522),
    )
    db = await get_db("steins_gate", "memory")
    try:
        exp_row = await (await db.execute(
            "SELECT expires_at FROM experiences WHERE observation_id=?",
            (target_id,),
        )).fetchone()
    finally:
        await db.close()
    assert str(exp_row["expires_at"]) == "2020-01-01T00:00:00+00:00", (
        "time-bound item must stay time-bound after reanalysis"
    )
    retrieved = await select_experience_candidates(
        session_id=session_id, worldline="steins_gate", identity_mode="self",
        query="活动", limit=16, token_budget=1400,
    )
    assert retrieved == [], (
        "a past-expiry experience must never be prompt-eligible"
    )


# ---------------------------------------------------------------------------
# MEMORY-V11-S3E-1: truth mutation gates.
#   P1-A: ATTACH/REFINE/SUPERSEDE require an explicit positive int
#         expected_version (missing / bool / string / float / <=0 rejected).
#   P1-B: REFINE/SUPERSEDE may only rewrite stable truth from the same
#         evidence authority as an automatic stable CREATE.
# ---------------------------------------------------------------------------


async def _s3e1_seed_fact(
    session_id: str, *, text: str = "喜欢喝黑咖啡", source_message_id: int = 8601
) -> dict[str, Any]:
    """Direct stable CREATE → active fact v1 (strong evidence, 0.94)."""
    result = await _apply_validated(
        session_id=session_id,
        worldline="steins_gate",
        identity_mode="self",
        observations=[
            _observation(
                ref="seed",
                display_text=text,
                semantic={"subject": "user", "predicate": "likes", "object": "coffee"},
                source_message_ids=[source_message_id],
            )
        ],
        operations=[
            {"op": "CREATE", "observation_ref": "seed", "fact_text": text,
             "reason_code": "direct_durable_statement"}
        ],
        source_evidence=[
            _source_evidence(
                source_message_id=source_message_id,
                conversation_id=f"conv-s3e1-{source_message_id}",
                fingerprint=f"fp-s3e1-{source_message_id}",
                excerpt=text,
            )
        ],
        candidate_allowlist=[],
    )
    return result["created_facts"][0]


def _s3e1_obs(
    ref: str,
    text: str,
    source_message_id: int,
    *,
    evidence_kind: str = "direct_user",
    memory_class: str = "stable_candidate",
    confidence: float = 0.94,
) -> dict[str, Any]:
    return _observation(
        ref=ref,
        display_text=text,
        semantic={"subject": "user", "predicate": "likes", "object": text[:8]},
        source_message_ids=[source_message_id],
        evidence_kind=evidence_kind,
        memory_class=memory_class,
        confidence=confidence,
    )


async def _s3e1_fact_state(fact_id: str) -> dict[str, Any]:
    """Persistent truth snapshot for zero-mutation assertions."""
    db = await get_db("steins_gate", "memory")
    try:
        fact = await (await db.execute(
            "SELECT active_version, state FROM stable_facts WHERE fact_id=?",
            (fact_id,),
        )).fetchone()
        v1 = await (await db.execute(
            """SELECT invalid_at FROM stable_fact_versions
                WHERE fact_id=? AND version_no=1""",
            (fact_id,),
        )).fetchone()
        out: dict[str, Any] = {
            "active_version": int(fact["active_version"]),
            "state": str(fact["state"]),
            "v1_invalid_at": v1["invalid_at"],
        }
        for key, sql in (
            (
                "versions",
                "SELECT COUNT(*) AS n FROM stable_fact_versions WHERE fact_id=?",
            ),
            (
                "evidence",
                "SELECT COUNT(*) AS n FROM stable_fact_evidence WHERE fact_id=?",
            ),
            (
                "embeddings",
                "SELECT COUNT(*) AS n FROM stable_fact_version_embeddings "
                "WHERE fact_id=?",
            ),
        ):
            out[key] = int(
                (await (await db.execute(sql, (fact_id,))).fetchone())["n"]
            )
        return out
    finally:
        await db.close()


async def _s3e1_attempt(
    session_id: str,
    *,
    op_name: str,
    fact_id: str,
    obs: dict[str, Any],
    expected_version: Any = "__absent__",
    source_message_id: int = 8602,
) -> Any:
    """Run one op through the real production seam; returns raised error info."""
    op = {
        "op": op_name,
        "observation_ref": obs["observation_ref"],
        "target_fact_id": fact_id,
        "fact_text": obs["display_text"],
        "reason_code": "s3e1",
    }
    if expected_version != "__absent__":
        op["expected_version"] = expected_version
    with pytest.raises(_contracts().MemoryValidationError) as exc_info:
        await _apply_validated(
            session_id=session_id,
            worldline="steins_gate",
            identity_mode="self",
            observations=[obs],
            operations=[op],
            source_evidence=[
                _source_evidence(
                    source_message_id=source_message_id,
                    conversation_id=f"conv-s3e1-{source_message_id}",
                    fingerprint=f"fp-s3e1-{source_message_id}",
                    excerpt=obs["display_text"],
                )
            ],
            candidate_allowlist=[fact_id],
        )
    return exc_info


@pytest.mark.asyncio
@pytest.mark.parametrize("op_name", ["ATTACH", "REFINE", "SUPERSEDE"])
@pytest.mark.parametrize(
    "ev", [pytest.param("__absent__", id="absent"), pytest.param(None, id="none")]
)
async def test_s3e1_e1_e3_missing_expected_version(
    isolated_store, monkeypatch, op_name, ev
):
    """P1-A: key ABSENT and explicit None are both MISSING → zero mutation.

    The previous dangerous omitted-version path silently skipped version checks;
    it must now fail earlier as missing_expected_version.
    """
    embedder = _DeterministicEmbedder()
    monkeypatch.setattr(memory_service, "embedder", embedder)
    session_id = f"s3e1-e-{uuid4().hex[:8]}"
    fact = await _s3e1_seed_fact(session_id)
    fact_id = fact["fact_id"]
    before = await _s3e1_fact_state(fact_id)
    obs = _s3e1_obs("o1", "喜欢喝黑咖啡", 8602)
    calls_before = embedder.encode_passage_calls
    exc_info = await _s3e1_attempt(
        session_id, op_name=op_name, fact_id=fact_id, obs=obs,
        expected_version=ev, source_message_id=8602,
    )
    assert exc_info.value.code == "missing_expected_version"
    assert embedder.encode_passage_calls == calls_before, (
        "missing expected_version must fail BEFORE encode_passage"
    )
    assert await _s3e1_fact_state(fact_id) == before, "zero truth mutation"
    assert await _count_rows(
        sql="SELECT COUNT(*) AS n FROM memory_observations WHERE session_id=?",
        params=(session_id,),
    ) == 1, "no observation residue from rejected batch"


@pytest.mark.asyncio
@pytest.mark.parametrize("op_name", ["ATTACH", "REFINE", "SUPERSEDE"])
@pytest.mark.parametrize(
    "bad",
    [True, False, 0, -1, "1", 1.0],
    ids=["true", "false", "zero", "negative", "string", "float"],
)
async def test_s3e1_e_invalid_expected_version(isolated_store, monkeypatch, op_name, bad):
    """P1-A: bool / string / float / zero / negative versions are invalid."""
    embedder = _DeterministicEmbedder()
    monkeypatch.setattr(memory_service, "embedder", embedder)
    session_id = f"s3e1-ev-{uuid4().hex[:8]}"
    fact = await _s3e1_seed_fact(session_id)
    fact_id = fact["fact_id"]
    before = await _s3e1_fact_state(fact_id)
    obs = _s3e1_obs("o1", "喜欢喝黑咖啡", 8603)
    calls_before = embedder.encode_passage_calls
    exc_info = await _s3e1_attempt(
        session_id, op_name=op_name, fact_id=fact_id, obs=obs,
        expected_version=bad, source_message_id=8603,
    )
    assert exc_info.value.code == "invalid_expected_version"
    assert embedder.encode_passage_calls == calls_before, (
        "invalid expected_version must fail BEFORE encode_passage"
    )
    assert await _s3e1_fact_state(fact_id) == before, "zero truth mutation"
    assert await _count_rows(
        sql="SELECT COUNT(*) AS n FROM memory_observations WHERE session_id=?",
        params=(session_id,),
    ) == 1, "no observation residue from rejected batch"


@pytest.mark.asyncio
async def test_s3e1_t1_stale_version_snapshot_rejected(isolated_store, monkeypatch):
    """Reviewer race: expected_version=1 vs active v2 → mismatch, no v3/evidence."""
    embedder = _DeterministicEmbedder()
    monkeypatch.setattr(memory_service, "embedder", embedder)
    session_id = f"s3e1-t1-{uuid4().hex[:8]}"
    fact = await _s3e1_seed_fact(session_id)
    fact_id = fact["fact_id"]
    obs_v2 = _s3e1_obs("v2", "喜欢喝冰黑咖啡", 8604)
    await _apply_validated(
        session_id=session_id, worldline="steins_gate", identity_mode="self",
        observations=[obs_v2],
        operations=[
            {"op": "REFINE", "observation_ref": "v2", "target_fact_id": fact_id,
             "expected_version": 1, "fact_text": "喜欢喝冰黑咖啡",
             "reason_code": "compatible_refinement"}
        ],
        source_evidence=[
            _source_evidence(source_message_id=8604, conversation_id="conv-s3e1-8604",
                             fingerprint="fp-s3e1-8604", excerpt="喜欢喝冰黑咖啡")
        ],
        candidate_allowlist=[fact_id],
    )
    assert (await _s3e1_fact_state(fact_id))["active_version"] == 2
    before = await _s3e1_fact_state(fact_id)
    for op_name, new_text in (
        ("SUPERSEDE", "现在更喜欢茶"),
        ("REFINE", "喜欢喝热美式"),
        ("ATTACH", "喜欢喝冰黑咖啡"),
    ):
        obs = _s3e1_obs("o1", new_text, 8605)
        exc_info = await _s3e1_attempt(
            session_id, op_name=op_name, fact_id=fact_id, obs=obs,
            expected_version=1, source_message_id=8605,
        )
        assert exc_info.value.code == "expected_version_mismatch", (
            f"stale {op_name} expected_version=1 vs active=2 must mismatch"
        )
        assert await _s3e1_fact_state(fact_id) == before, (
            f"{op_name} stale snapshot must leave zero mutation"
        )
    assert await _count_rows(
        sql="SELECT COUNT(*) AS n FROM memory_observations WHERE session_id=?",
        params=(session_id,),
    ) == 2, "only seed + v2 observations exist; rejected attempts add none"


@pytest.mark.asyncio
@pytest.mark.parametrize("op_name", ["SUPERSEDE", "REFINE"])
async def test_s3e1_m1_m2_inferred_weak_evidence_rejected(
    isolated_store, monkeypatch, op_name
):
    """inferred_user @ 0.20 can never rewrite stable truth."""
    embedder = _DeterministicEmbedder()
    monkeypatch.setattr(memory_service, "embedder", embedder)
    session_id = f"s3e1-m1-{uuid4().hex[:8]}"
    fact = await _s3e1_seed_fact(session_id)
    fact_id = fact["fact_id"]
    before = await _s3e1_fact_state(fact_id)
    obs = _s3e1_obs(
        "o1", "喜欢喝黑咖啡", 8606,
        evidence_kind="inferred_user", confidence=0.20,
    )
    calls_before = embedder.encode_passage_calls
    exc_info = await _s3e1_attempt(
        session_id, op_name=op_name, fact_id=fact_id, obs=obs,
        expected_version=1, source_message_id=8606,
    )
    assert exc_info.value.code == "fact_mutation_not_eligible"
    assert embedder.encode_passage_calls == calls_before, (
        "ineligible mutation must fail BEFORE encode_passage"
    )
    assert await _s3e1_fact_state(fact_id) == before, (
        "old active fact must remain untouched"
    )
    assert await _count_rows(
        sql="SELECT COUNT(*) AS n FROM memory_observations WHERE session_id=?",
        params=(session_id,),
    ) == 1, "no observation residue from rejected batch"


@pytest.mark.asyncio
@pytest.mark.parametrize("op_name", ["SUPERSEDE", "REFINE"])
async def test_s3e1_m3_m4_low_confidence_rejected(
    isolated_store, monkeypatch, op_name
):
    """direct_user @ 0.50 < AUTO_STABLE_CONFIDENCE → reject."""
    embedder = _DeterministicEmbedder()
    monkeypatch.setattr(memory_service, "embedder", embedder)
    session_id = f"s3e1-m3-{uuid4().hex[:8]}"
    fact = await _s3e1_seed_fact(session_id)
    fact_id = fact["fact_id"]
    before = await _s3e1_fact_state(fact_id)
    obs = _s3e1_obs("o1", "喜欢喝黑咖啡", 8607, confidence=0.50)
    calls_before = embedder.encode_passage_calls
    exc_info = await _s3e1_attempt(
        session_id, op_name=op_name, fact_id=fact_id, obs=obs,
        expected_version=1, source_message_id=8607,
    )
    assert exc_info.value.code == "fact_mutation_not_eligible"
    assert embedder.encode_passage_calls == calls_before
    assert await _s3e1_fact_state(fact_id) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("op_name", ["SUPERSEDE", "REFINE"])
async def test_s3e1_m_threshold_boundary_rejected(
    isolated_store, monkeypatch, op_name
):
    """confidence 0.84 with AUTO_STABLE_CONFIDENCE==0.85 → reject.

    The shared predicate uses the authoritative threshold; no second constant.
    """
    contracts = _contracts()
    assert float(contracts.AUTO_STABLE_CONFIDENCE) == 0.85
    embedder = _DeterministicEmbedder()
    monkeypatch.setattr(memory_service, "embedder", embedder)
    session_id = f"s3e1-mt-{uuid4().hex[:8]}"
    fact = await _s3e1_seed_fact(session_id)
    fact_id = fact["fact_id"]
    before = await _s3e1_fact_state(fact_id)
    obs = _s3e1_obs("o1", "喜欢喝黑咖啡", 8608, confidence=0.84)
    exc_info = await _s3e1_attempt(
        session_id, op_name=op_name, fact_id=fact_id, obs=obs,
        expected_version=1, source_message_id=8608,
    )
    assert exc_info.value.code == "fact_mutation_not_eligible"
    assert await _s3e1_fact_state(fact_id) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("op_name", ["SUPERSEDE", "REFINE"])
async def test_s3e1_m5_non_candidate_class_rejected(
    isolated_store, monkeypatch, op_name
):
    """memory_class != stable_candidate → fact_mutation_not_eligible.

    Episodic keeps its existing stronger prohibition
    (episodic_target_operation_forbidden, covered by test_s3c1_*).
    """
    embedder = _DeterministicEmbedder()
    monkeypatch.setattr(memory_service, "embedder", embedder)
    session_id = f"s3e1-m5-{uuid4().hex[:8]}"
    fact = await _s3e1_seed_fact(session_id)
    fact_id = fact["fact_id"]
    before = await _s3e1_fact_state(fact_id)
    obs = _s3e1_obs("o1", "喜欢喝黑咖啡", 8609, memory_class="transient")
    exc_info = await _s3e1_attempt(
        session_id, op_name=op_name, fact_id=fact_id, obs=obs,
        expected_version=1, source_message_id=8609,
    )
    assert exc_info.value.code == "fact_mutation_not_eligible"
    assert await _s3e1_fact_state(fact_id) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("op_name", ["SUPERSEDE", "REFINE"])
async def test_s3e1_m6_m7_strong_mutation_controls(
    isolated_store, monkeypatch, op_name
):
    """Control: direct_user + stable_candidate + 0.94 + correct version → v2."""
    embedder = _DeterministicEmbedder()
    monkeypatch.setattr(memory_service, "embedder", embedder)
    session_id = f"s3e1-m6-{uuid4().hex[:8]}"
    fact = await _s3e1_seed_fact(session_id)
    fact_id = fact["fact_id"]
    obs = _s3e1_obs("o1", "喜欢喝冰黑咖啡", 8610)
    await _apply_validated(
        session_id=session_id, worldline="steins_gate", identity_mode="self",
        observations=[obs],
        operations=[
            {"op": op_name, "observation_ref": "o1", "target_fact_id": fact_id,
             "expected_version": 1, "fact_text": "喜欢喝冰黑咖啡",
             "reason_code": "compatible_refinement"}
        ],
        source_evidence=[
            _source_evidence(source_message_id=8610, conversation_id="conv-s3e1-8610",
                             fingerprint="fp-s3e1-8610", excerpt="喜欢喝冰黑咖啡")
        ],
        candidate_allowlist=[fact_id],
    )
    state = await _s3e1_fact_state(fact_id)
    assert state["active_version"] == 2, "valid mutation must advance version"
    assert state["versions"] == 2, "v1 + v2"
    assert state["evidence"] == 2, "v1 primary + v2 primary"
    assert state["embeddings"] == 1, "v1 vector replaced by v2 vector"
    assert state["v1_invalid_at"] is not None, "v1 closed by v2"


@pytest.mark.asyncio
async def test_s3e1_a1_attach_weak_evidence_allowed(isolated_store):
    """ATTACH is intentionally different: inferred 0.20 + correct version links.

    Fact text/version stay untouched; exactly one supporting evidence link
    lands on the expected active version.
    """
    session_id = f"s3e1-a1-{uuid4().hex[:8]}"
    fact = await _s3e1_seed_fact(session_id)
    fact_id = fact["fact_id"]
    obs = _s3e1_obs(
        "o1", "喜欢喝黑咖啡", 8611,
        evidence_kind="inferred_user", confidence=0.20,
    )
    await _apply_validated(
        session_id=session_id, worldline="steins_gate", identity_mode="self",
        observations=[obs],
        operations=[
            {"op": "ATTACH", "observation_ref": "o1", "target_fact_id": fact_id,
             "expected_version": 1, "fact_text": "喜欢喝黑咖啡",
             "reason_code": "equivalent_evidence"}
        ],
        source_evidence=[
            _source_evidence(source_message_id=8611, conversation_id="conv-s3e1-8611",
                             fingerprint="fp-s3e1-8611", excerpt="喜欢喝黑咖啡")
        ],
        candidate_allowlist=[fact_id],
    )
    state = await _s3e1_fact_state(fact_id)
    assert state["active_version"] == 1, "ATTACH must not advance the version"
    assert state["versions"] == 1, "ATTACH must not open a version"
    assert state["evidence"] == 2, "seed primary + one supporting link"
    assert state["v1_invalid_at"] is None
    # supporting link must exist for the new observation at v1
    assert await _count_rows(
        sql="""SELECT COUNT(*) AS n FROM stable_fact_evidence
                WHERE fact_id=? AND version_no=1 AND evidence_role='supporting'""",
        params=(fact_id,),
    ) == 1, "exactly one supporting evidence link on expected active version"
    assert await _attached_observation_count(session_id) == 2, (
        "seed + attached weak observation"
    )


@pytest.mark.asyncio
async def test_s3e1_a2_attach_stale_version_no_evidence(isolated_store):
    """ATTACH with stale expected_version → mismatch, zero evidence added."""
    session_id = f"s3e1-a2-{uuid4().hex[:8]}"
    fact = await _s3e1_seed_fact(session_id)
    fact_id = fact["fact_id"]
    before = await _s3e1_fact_state(fact_id)
    obs = _s3e1_obs(
        "o1", "喜欢喝黑咖啡", 8612,
        evidence_kind="inferred_user", confidence=0.20,
    )
    exc_info = await _s3e1_attempt(
        session_id, op_name="ATTACH", fact_id=fact_id, obs=obs,
        expected_version=2, source_message_id=8612,
    )
    assert exc_info.value.code == "expected_version_mismatch"
    assert await _s3e1_fact_state(fact_id) == before, "no evidence added"
    assert await _count_rows(
        sql="SELECT COUNT(*) AS n FROM memory_observations WHERE session_id=?",
        params=(session_id,),
    ) == 1, "no observation residue from rejected batch"
