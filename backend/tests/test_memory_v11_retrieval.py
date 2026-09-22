"""MEMORY-V11-S3-GAP-PROBE R3: vector candidate participation (deterministic oracle).

Red until S3 consults stable_fact_version_embeddings in same-scope candidate
selection (plan §4.10 / §10.1). Test-only: no production code is modified.

The oracle is mathematically deterministic:
- vector evidence is produced through the EXISTING EmbeddingAdapter boundary
  (app.services.memory.EmbeddingAdapter), stubbed with an exact-phrase
  vocabulary and seeded hash unit vectors — no network, no model download;
- query vector and target fact vector are identical (cosine = 1.0); every
  filler fact vector is asserted to be far lower cosine;
- every filler carries a distinct canonical semantic fingerprint and a
  higher confidence than the target, so R2 fingerprint dedupe alone cannot
  collapse fillers and pull the target into the non-vector [:24] window;
- the target fact has zero lexical overlap with the query and the lowest
  confidence, so a lexical-only selection deterministically drops it.
"""
from __future__ import annotations

import hashlib
import json
import math
import struct
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.db import get_db, init_db, reset_initialization_cache
from app.services.memory import EmbeddingAdapter, memory_service
from app.services.memory_v11.repository import create_stable_fact
from app.services.prompt_compiler import compile_for_session


@pytest.fixture
async def isolated_store(tmp_path, monkeypatch):
    monkeypatch.delenv("AMADEUS_DB_PATH", raising=False)
    monkeypatch.setenv("AMADEUS_DATA_DIR", str(tmp_path))
    reset_initialization_cache()
    await init_db()
    return tmp_path


def _session_obj(session_id: str, *, identity_mode: str = "self") -> SimpleNamespace:
    return SimpleNamespace(
        session_id=session_id,
        worldline="steins_gate",
        identity_mode=identity_mode,
        base_system_prompt="You are Amadeus Kurisu.",
        system_prompt="You are Amadeus Kurisu.",
        memory_summary="",
        history=[],
        self_name="",
        identity_acknowledged=False,
    )


def _unit_vector(seed: str) -> list[float]:
    """Deterministic 384-dim unit vector from a seed string (no randomness)."""
    values: list[float] = []
    for block in range(12):
        digest = hashlib.sha256(f"{seed}:{block}".encode("utf-8")).digest()
        values.extend(int(byte) - 127.5 for byte in digest)
    norm = math.sqrt(sum(value * value for value in values))
    return [value / norm for value in values]


def _cosine(left: list[float], right: list[float]) -> float:
    return sum(a * b for a, b in zip(left, right))


# Must match seeded stable_fact_version_embeddings.model for vector scoring.
_TEST_EMBED_MODEL = "test-deterministic-384"


class _DeterministicEmbedder(EmbeddingAdapter):
    """Stub of the existing EmbeddingAdapter boundary: no model, no network.

    Exact-phrase vocabulary maps text to shared concept vectors; unknown text
    maps to a deterministic hash unit vector of the text itself.
    """

    def __init__(self, vocabulary: dict[str, str], *, model_name: str = _TEST_EMBED_MODEL):
        super().__init__(model_name=model_name, dimensions=384)
        self._vocabulary = dict(vocabulary)

    async def encode_passage(self, text: str) -> list[float]:
        return _unit_vector(self._vocabulary.get(text, text))

    async def encode_query(self, text: str) -> list[float]:
        return _unit_vector(self._vocabulary.get(text, text))


async def _compile_v11(session_id: str, user_message: str, monkeypatch) -> str:
    monkeypatch.setenv("AMADEUS_MEMORY_MODE", "v11")
    return await compile_for_session(_session_obj(session_id), user_message)


@pytest.mark.asyncio
async def test_r3_vector_candidate_participates_with_zero_lexical_overlap(
    isolated_store, monkeypatch
):
    """§4.10 / §10.1: core vector must be a real candidate signal.

    Given a deterministic query vector for which the target is the strongest
    same-scope vector candidate (cosine 1.0; fillers far lower), current v11
    prompt retrieval still drops the target because
    stable_fact_version_embeddings is never consulted.
    """
    session_id = f"vec-probe-{uuid4()}"
    query_text = "ペット"
    target_text = "犬を飼っている"
    filler_texts = [f"FILLER_{i:02d} study notes about topology" for i in range(29)]
    filler_semantics = [
        {"subject": "user", "relation": "study", "object": f"topic_{i:02d}"}
        for i in range(29)
    ]
    target_confidence = 0.5
    filler_confidence = 0.9

    # Vector evidence is produced through the existing embedding boundary.
    # Inject the same stub so production query encoding matches seeded vectors
    # (no real model / network).
    embedder = _DeterministicEmbedder(vocabulary={query_text: "pet", target_text: "pet"})
    monkeypatch.setattr(memory_service, "embedder", embedder)
    query_vector = await embedder.encode_query(query_text)
    target_vector = await embedder.encode_passage(target_text)
    filler_vectors = [
        await embedder.encode_passage(text)
        for text in filler_texts
    ]

    # Deterministic oracle: target must be clearly the strongest candidate,
    # fillers must be far below it. These pass on the seeded data itself.
    assert _cosine(query_vector, target_vector) >= 0.99, (
        "oracle: target vector must be clearly most similar to query vector"
    )
    for i, filler_vector in enumerate(filler_vectors):
        assert _cosine(query_vector, filler_vector) <= 0.25, (
            f"oracle: filler {i} must not rival the target's similarity"
        )
    for filler_text in filler_texts:
        assert query_text not in filler_text
    assert query_text not in target_text

    # Dedupe oracle: every filler must carry a distinct canonical semantic
    # fingerprint, so R2 fingerprint dedupe alone cannot collapse fillers and
    # let the target slip into the [:24] selection window. Canonical form is
    # the documented v11 contract: stable-key JSON serialization (plan §6.3).
    filler_fingerprints = {
        json.dumps(sem, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        for sem in filler_semantics
    }
    assert len(filler_fingerprints) == len(filler_semantics) == 29, (
        "oracle: every filler must have a distinct canonical semantic fingerprint"
    )
    assert len(filler_fingerprints) >= 24, (
        "oracle: at least 24 distinct-fingerprint fillers must remain after R2 "
        "dedupe so the target stays outside the non-vector [:24] window"
    )
    assert filler_confidence > target_confidence, (
        "oracle: fillers must outrank the target in non-vector selection"
    )

    # Same-scope facts: 29 fillers (high confidence) + target (lowest
    # confidence) so a lexical-only selection deterministically drops target.
    target = await create_stable_fact(
        session_id=session_id,
        worldline="steins_gate",
        identity_mode="self",
        display_text=target_text,
        semantic_json={"subject": "user", "relation": "has_pet", "object": "dog"},
        confidence=target_confidence,
    )
    filler_facts = []
    for filler_text, filler_semantic in zip(filler_texts, filler_semantics):
        filler_facts.append(
            await create_stable_fact(
                session_id=session_id,
                worldline="steins_gate",
                identity_mode="self",
                display_text=filler_text,
                semantic_json=filler_semantic,
                confidence=filler_confidence,
            )
        )

    # Seed stable_fact_version_embeddings through the same boundary.
    # model must equal embedder.model_name (fail-closed compatibility gate).
    assert embedder.model_name == _TEST_EMBED_MODEL
    db = await get_db("steins_gate", "memory")
    try:
        vector_rows = [(target["fact_id"], target_vector)] + [
            (fact["fact_id"], filler_vector)
            for fact, filler_vector in zip(filler_facts, filler_vectors)
        ]
        for fact_id, vector in vector_rows:
            await db.execute(
                """INSERT INTO stable_fact_version_embeddings(
                       fact_id, version_no, model, dimensions, vector
                   ) VALUES(?,?,?,?,?)""",
                (
                    fact_id,
                    1,
                    _TEST_EMBED_MODEL,
                    384,
                    struct.pack(f"{384}f", *vector),
                ),
            )
        await db.commit()
        cur = await db.execute("SELECT COUNT(*) FROM stable_fact_version_embeddings")
        seeded = (await cur.fetchone())[0]
    finally:
        await db.close()
    assert seeded == 30, "vector evidence must be present for every same-scope fact"

    prompt = await _compile_v11(session_id, query_text, monkeypatch)
    assert target_text not in prompt
    assert "\nCORE FACTS\n" not in prompt
    from app.services.memory_v11.recall import MemoryScope, RecallRequest, recall_stable_facts

    recalled = await recall_stable_facts(
        scope=MemoryScope(session_id=session_id, worldline="steins_gate", identity_mode="self"),
        request=RecallRequest(needs=(query_text,)),
        embedder=embedder,
    )
    assert target_text in recalled.records.values(), (
        "vector candidate signal unused: target fact (zero lexical overlap, "
        "strongest same-scope vector candidate) was dropped from recall"
    )
    from app.services.memory_v11.reconciliation_select import (
        select_reconciliation_candidates,
    )

    jobs_rows = await select_reconciliation_candidates(
        session_id=session_id,
        worldline="steins_gate",
        identity_mode="self",
        source_text=query_text,
        embedder=embedder,
    )
    assert any(row.get("display_text") == target_text for row in jobs_rows)


@pytest.mark.asyncio
async def test_r3_incompatible_embedding_model_is_ignored(
    isolated_store, monkeypatch
):
    """Stored vectors with a different model_name must not affect candidates.

    Same-scope target has a high-similarity BLOB under a foreign model id.
    Active embedder.model_name differs → vector signal fail-closed skipped;
    with zero lexical overlap the target stays out of the prompt.
    """
    session_id = f"vec-model-compat-{uuid4()}"
    query_text = "ペット"
    target_text = "犬を飼っている"
    foreign_model = "other-model-not-active"
    active_model = _TEST_EMBED_MODEL
    assert foreign_model != active_model

    embedder = _DeterministicEmbedder(
        vocabulary={query_text: "pet", target_text: "pet"},
        model_name=active_model,
    )
    monkeypatch.setattr(memory_service, "embedder", embedder)

    query_vector = await embedder.encode_query(query_text)
    target_vector = await embedder.encode_passage(target_text)
    assert _cosine(query_vector, target_vector) >= 0.99
    assert query_text not in target_text

    target = await create_stable_fact(
        session_id=session_id,
        worldline="steins_gate",
        identity_mode="self",
        display_text=target_text,
        semantic_json={"subject": "user", "relation": "has_pet", "object": "dog"},
        confidence=0.5,
    )
    # Fillers keep the non-vector top slots if anything were bulk-selected.
    for i in range(29):
        await create_stable_fact(
            session_id=session_id,
            worldline="steins_gate",
            identity_mode="self",
            display_text=f"FILLER_{i:02d} study notes about topology",
            semantic_json={
                "subject": "user",
                "relation": "study",
                "object": f"topic_{i:02d}",
            },
            confidence=0.9,
        )

    db = await get_db("steins_gate", "memory")
    try:
        await db.execute(
            """INSERT INTO stable_fact_version_embeddings(
                   fact_id, version_no, model, dimensions, vector
               ) VALUES(?,?,?,?,?)""",
            (
                target["fact_id"],
                1,
                foreign_model,
                384,
                struct.pack(f"{384}f", *target_vector),
            ),
        )
        await db.commit()
    finally:
        await db.close()

    prompt = await _compile_v11(session_id, query_text, monkeypatch)
    assert target_text not in prompt, (
        "incompatible embedding model influenced retrieval: target with "
        f"stored model={foreign_model!r} must not score under active "
        f"embedder.model_name={active_model!r}"
    )

# ---------------------------------------------------------------------------
# MEMORY-V11-S3C-2: first-class experience retrieval (plan §10.2 / §10.3).
# Local CJK-aware relevance, active + unexpired only, independent budget.
# ---------------------------------------------------------------------------


def _selector():
    import app.services.memory_v11.retrieval as mod

    return mod


def _semantic_fingerprint(semantic: dict) -> str:
    return hashlib.sha256(
        json.dumps(
            semantic, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()


async def _seed_experience(
    *,
    worldline: str = "steins_gate",
    session_id: str,
    identity_mode: str = "self",
    conversation_id: str = "conv-x",
    display_text: str,
    semantic: dict | None = None,
    confidence: float = 0.9,
    status: str = "active",
    expires_at: str | None = None,
    created_at: str = "2026-08-01T00:00:00+00:00",
    source_state: str = "present",
    excerpt: str | None = "excerpt",
) -> str:
    semantic = semantic or {
        "subject": "user",
        "predicate": "event",
        "object": display_text[:24],
    }
    db = await get_db(worldline, "memory")
    try:
        observation_id = str(uuid4())
        await db.execute(
            """INSERT INTO memory_observations(
                   observation_id, session_id, identity_mode, display_text,
                   semantic_json, semantic_fingerprint, evidence_kind, memory_class,
                   confidence, topic_label_proposal, status, extractor_version,
                   reanalysis_count, expires_at, created_at, updated_at
               ) VALUES(?,?,?,?,?,?, 'direct_user', 'episodic', ?, NULL, 'attached',
                        'memory-v11-1', 0, ?, ?, ?)""",
            (
                observation_id,
                session_id,
                identity_mode,
                display_text,
                json.dumps(semantic, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                _semantic_fingerprint(semantic),
                confidence,
                expires_at,
                created_at,
                created_at,
            ),
        )
        await db.execute(
            """INSERT INTO memory_observation_sources(
                   observation_id, source_message_id, conversation_id, source_role,
                   source_fingerprint, source_created_at, source_state, excerpt
               ) VALUES(?,?,?, 'user', ?, ?, ?, ?)""",
            (
                observation_id,
                int(uuid4().int % 10**9),
                conversation_id,
                f"fp-{observation_id[:8]}",
                created_at,
                source_state,
                excerpt,
            ),
        )
        experience_id = str(uuid4())
        await db.execute(
            """INSERT INTO experiences(
                   experience_id, session_id, conversation_id, identity_mode,
                   observation_id, display_text, semantic_json, semantic_fingerprint,
                   confidence, status, expires_at, created_at, updated_at, deleted_at
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,NULL)""",
            (
                experience_id,
                session_id,
                conversation_id,
                identity_mode,
                observation_id,
                display_text,
                json.dumps(semantic, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                _semantic_fingerprint(semantic),
                confidence,
                status,
                expires_at,
                created_at,
                created_at,
            ),
        )
        await db.commit()
        return experience_id
    finally:
        await db.close()


async def _select_experiences(
    session_id: str, query: str, *, worldline: str = "steins_gate", **kwargs
) -> list[dict]:
    selector = _selector()
    return await selector.select_experience_candidates(
        session_id=session_id,
        worldline=worldline,
        identity_mode="self",
        query=query,
        **kwargs,
    )


async def _count_experiences(*, worldline: str = "steins_gate") -> int:
    db = await get_db(worldline, "memory")
    try:
        cur = await db.execute("SELECT COUNT(*) AS n FROM experiences")
        return int((await cur.fetchone())["n"])
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_r5_experience_same_scope_relevance(isolated_store):
    """A: relevant self selected; irrelevant self + okabe excluded."""
    session_id = f"r5-scope-{uuid4()}"
    relevant_id = await _seed_experience(
        session_id=session_id,
        display_text="先週秋葉原で本を買った",
        conversation_id="conv-a",
    )
    await _seed_experience(
        session_id=session_id,
        display_text="数学の本を読んでいる",
        conversation_id="conv-b",
    )
    await _seed_experience(
        session_id=session_id,
        identity_mode="okabe",
        display_text="先週秋葉原で本を買った",
        conversation_id="conv-c",
    )
    rows = await _select_experiences(session_id, "秋葉原")
    ids = {r["experience_id"] for r in rows}
    assert ids == {relevant_id}


@pytest.mark.asyncio
async def test_r5_experience_worldline_isolation(isolated_store):
    """B: SG experiences never appear via the beta selector."""
    session_id = f"r5-wl-{uuid4()}"
    await _seed_experience(
        session_id=session_id, display_text="先週秋葉原で本を買った"
    )
    rows = await _select_experiences(session_id, "秋葉原", worldline="beta")
    assert rows == []


@pytest.mark.asyncio
async def test_r5_experience_expiry_behavior(isolated_store):
    """C: future enters; past/expired excluded; rows stay physically present."""
    session_id = f"r5-exp-{uuid4()}"
    future_id = await _seed_experience(
        session_id=session_id,
        display_text="先週秋葉原で本を買った",
        expires_at="2099-01-01T00:00:00+00:00",
    )
    await _seed_experience(
        session_id=session_id,
        display_text="先週秋葉原で本を買った",
        expires_at="2020-01-01T00:00:00+00:00",
    )
    await _seed_experience(
        session_id=session_id,
        display_text="先週秋葉原で本を買った",
        status="expired",
        expires_at=None,
    )
    rows = await _select_experiences(session_id, "秋葉原")
    assert [r["experience_id"] for r in rows] == [future_id]
    assert await _count_experiences() == 3, (
        "expiry filtering must be read behavior; rows stay in storage"
    )


@pytest.mark.asyncio
async def test_r5_experience_pending_source_delete_excluded(isolated_store):
    """D: status=pending_source_delete never enters retrieval."""
    session_id = f"r5-psd-{uuid4()}"
    active_id = await _seed_experience(
        session_id=session_id, display_text="先週秋葉原で本を買った"
    )
    await _seed_experience(
        session_id=session_id,
        display_text="先週秋葉原で本を買った",
        status="pending_source_delete",
    )
    rows = await _select_experiences(session_id, "秋葉原")
    assert [r["experience_id"] for r in rows] == [active_id]


@pytest.mark.asyncio
async def test_r5_experience_no_bulk_flood_recency_is_not_relevance(isolated_store):
    """E: 20 newer irrelevant experiences never flood; recency alone pulls nothing."""
    session_id = f"r5-flood-{uuid4()}"
    relevant_id = await _seed_experience(
        session_id=session_id,
        display_text="先週秋葉原で本を買った",
        created_at="2020-01-01T00:00:00+00:00",
    )
    for i in range(20):
        await _seed_experience(
            session_id=session_id,
            display_text=f"NOISE_{i:02d} 数学の本を読んでいる",
            created_at=f"2099-01-0{1 + i % 9}T00:00:00+00:00",
        )
    rows = await _select_experiences(session_id, "秋葉原")
    assert [r["experience_id"] for r in rows] == [relevant_id]


@pytest.mark.asyncio
async def test_r5_experience_cjk_relevance_without_whitespace(isolated_store):
    """F: Japanese/Chinese substring overlap matches without tokenization."""
    session_id = f"r5-cjk-{uuid4()}"
    ja_id = await _seed_experience(
        session_id=session_id, display_text="先週秋葉原で本を買った"
    )
    zh_id = await _seed_experience(
        session_id=session_id, display_text="昨天在书店买了一本科幻小说"
    )
    await _seed_experience(
        session_id=session_id, display_text="天体観測の本を読んだ"
    )
    ja = await _select_experiences(session_id, "秋葉原のこと")
    assert [r["experience_id"] for r in ja] == [ja_id]
    zh = await _select_experiences(session_id, "昨天买的书")
    assert [r["experience_id"] for r in zh] == [zh_id]
    none = await _select_experiences(session_id, "マラソン大会")
    assert none == []


@pytest.mark.asyncio
async def test_r5_experience_independent_budget_whole_items(isolated_store):
    """G: bounded count, whole display_text only, no fragments."""
    session_id = f"r5-budget-{uuid4()}"
    texts = [
        f"先週秋葉原で買った{item:03d}番目の本" + "。" * 120
        for item in range(25)
    ]
    for text in texts:
        await _seed_experience(session_id=session_id, display_text=text)
    rows = await _select_experiences(session_id, "秋葉原")
    assert len(rows) <= 16, "experience selector must have its own count bound"
    seeded_texts = set(texts)
    returned_texts = [r["display_text"] for r in rows]
    assert len(returned_texts) == len(set(returned_texts))
    assert all(text in seeded_texts for text in returned_texts), (
        "every returned experience must be a whole seeded display_text; "
        "no char-slicing and no fabricated fragments"
    )
    assert all(len(text) == len(texts[0]) for text in returned_texts)


@pytest.mark.asyncio
async def test_r5_experience_source_state_deleted_retained(isolated_store):
    """L: finalized retained memory (source_state=deleted) may still be retrieved."""
    session_id = f"r5-retained-{uuid4()}"
    retained_id = await _seed_experience(
        session_id=session_id,
        display_text="先週秋葉原で本を買った",
        source_state="deleted",
        excerpt=None,
    )
    rows = await _select_experiences(session_id, "秋葉原")
    assert [r["experience_id"] for r in rows] == [retained_id]

@pytest.mark.asyncio
async def test_r5_cjk_eligibility_positive_and_negative(isolated_store):
    """P1: weak single-bigram overlap must not grant CJK eligibility."""
    session_id = f"r5-gate-{uuid4()}"
    zh_pos_id = await _seed_experience(
        session_id=session_id, display_text="昨天在书店买了一本科幻小说"
    )
    await _seed_experience(session_id=session_id, display_text="昨天吃了拉面")
    await _seed_experience(session_id=session_id, display_text="书架需要整理")
    ja_pos_id = await _seed_experience(
        session_id=session_id, display_text="先週秋葉原で本を買った"
    )
    await _seed_experience(session_id=session_id, display_text="昨日病院に行った")

    zh = await _select_experiences(session_id, "昨天买的书")
    assert [r["experience_id"] for r in zh] == [zh_pos_id], (
        "weak shared temporal bigram must not grant eligibility"
    )
    ja = await _select_experiences(session_id, "秋葉原のこと")
    assert [r["experience_id"] for r in ja] == [ja_pos_id]
