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
    assert target_text in prompt, (
        "vector candidate signal unused: target fact (zero lexical overlap, "
        "strongest same-scope vector candidate in "
        "stable_fact_version_embeddings) was dropped from the v11 prompt"
    )


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
