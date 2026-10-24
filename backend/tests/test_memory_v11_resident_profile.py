"""Dispatch 55: v11 resident profile (CORE FACTS every turn) + supplement.

Acceptance map (dispatch section 6) is noted per test as [A<n>].
"""
from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.db import get_db, init_db, reset_initialization_cache
from app.services.memory_v11.recall import (
    INJECTED_FACTS_LOOKUP_UNAVAILABLE_NOTICE,
    NO_USABLE_FACTS_NOTICE,
    MemoryScope,
    RecallLimits,
    RecallRequest,
    invalid_result,
    load_resident_fact_candidates,
    recall_stable_facts,
    shared_bigram_count,
    structured_score,
    unavailable_result,
    unsupported_result,
)

WORLDLINE = "steins_gate"


@pytest.fixture
async def isolated_store(tmp_path, monkeypatch):
    monkeypatch.delenv("AMADEUS_DB_PATH", raising=False)
    monkeypatch.setenv("AMADEUS_DATA_DIR", str(tmp_path))
    reset_initialization_cache()
    await init_db()
    return tmp_path


def _fp(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


async def _seed_observation(db, *, session_id: str, identity_mode: str, text: str) -> str:
    observation_id = str(uuid4())
    now = "2026-08-01T00:00:00+00:00"
    await db.execute(
        """INSERT INTO memory_observations(
               observation_id, session_id, identity_mode, display_text,
               semantic_json, semantic_fingerprint, evidence_kind, memory_class,
               confidence, topic_label_proposal, status, extractor_version,
               reanalysis_count, expires_at, created_at, updated_at
           ) VALUES(?,?,?,?, '{}', ?, 'direct_user', 'stable_candidate', 0.9, NULL,
                    'attached', 'memory-v11-1', 0, NULL, ?, ?)""",
        (observation_id, session_id, identity_mode, text, _fp(text + observation_id), now, now),
    )
    return observation_id


async def seed_fact(
    *,
    session_id: str,
    text: str,
    identity_mode: str = "self",
    pinned: bool = False,
    confidence: float = 0.9,
    change_kind: str = "create",
    fingerprint: str | None = None,
    created_at: str = "2026-08-01T00:00:00+00:00",
    updated_at: str | None = None,
    evidence: list[list[str]] | None = None,
    prior_kinds: tuple[str, ...] = (),
    state: str = "active",
    fact_id: str | None = None,
) -> str:
    """Insert one stable fact with full control of rank signals.

    ``prior_kinds`` adds closed earlier versions (oldest first) before the
    active version. ``evidence`` is one list of observation keys per version
    (prior versions first, then the active one); equal keys reuse the same
    observation_id so cross-version duplicates can be tested.
    """
    fact_id = fact_id or str(uuid4())
    updated_at = updated_at or created_at
    kinds = list(prior_kinds) + [change_kind]
    db = await get_db(WORLDLINE, "memory")
    try:
        await db.execute(
            """INSERT INTO stable_facts(
                   fact_id, session_id, identity_mode, topic_id, state,
                   active_version, is_pinned, created_at, updated_at, deleted_at
               ) VALUES(?,?,?,NULL,?,?,?,?,?,NULL)""",
            (fact_id, session_id, identity_mode, state, len(kinds), 1 if pinned else 0, created_at, updated_at),
        )
        for index, kind in enumerate(kinds, start=1):
            active = index == len(kinds)
            await db.execute(
                """INSERT INTO stable_fact_versions(
                       fact_id, version_no, display_text, semantic_json,
                       semantic_fingerprint, confidence, change_kind,
                       previous_version, valid_from, invalid_at, created_by_job_id
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,NULL)""",
                (
                    fact_id,
                    index,
                    text if active else f"{text} (v{index})",
                    json.dumps({"text": text}, ensure_ascii=False),
                    fingerprint if fingerprint is not None else _fp(f"{fact_id}:{text}"),
                    confidence,
                    kind,
                    index - 1 if index > 1 else None,
                    created_at,
                    None if active else created_at,
                ),
            )
        observation_ids: dict[str, str] = {}
        for index, keys in enumerate(evidence or [], start=1):
            for key in keys:
                if key not in observation_ids:
                    observation_ids[key] = await _seed_observation(
                        db, session_id=session_id, identity_mode=identity_mode, text=key
                    )
                await db.execute(
                    """INSERT INTO stable_fact_evidence(
                           fact_id, version_no, observation_id, evidence_role
                       ) VALUES(?,?,?, 'supporting')""",
                    (fact_id, index, observation_ids[key]),
                )
        await db.commit()
    finally:
        await db.close()
    return fact_id


def _scope(session_id: str, identity_mode: str = "self") -> MemoryScope:
    return MemoryScope(session_id=session_id, worldline=WORLDLINE, identity_mode=identity_mode)


class SpyEmbedder:
    """E5 stand-in: records every encode_query call."""

    model_name = "spy-e5"
    dimensions = 4

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def encode_query(self, text: str) -> list[float]:
        self.calls.append(text)
        return [1.0, 0.0, 0.0, 0.0]


# ---------------------------------------------------------------------------
# recall.py: optional exclusion / lexical-only / admission knobs
# ---------------------------------------------------------------------------


def test_shared_bigram_count_uses_structured_score_folding():
    # NFKC + casefold: fullwidth "ＴＥＡ" folds to "tea".
    assert shared_bigram_count(need="ＴＥＡが好き", display="tea time") == 2  # te, ea
    assert shared_bigram_count(need="紅茶", display="紅茶が好き") == 1
    assert shared_bigram_count(need="", display="anything") == 0
    # structured_score is unchanged by the helper refactor.
    assert structured_score(need="紅茶", display="紅茶が好き", semantic_json=None, topic_id=None) == pytest.approx(2.6)


@pytest.mark.asyncio
async def test_recall_exclude_fact_ids_default_and_explicit(isolated_store):
    """[A6] exclusion drops given ids and same-fingerprint duplicates only."""
    sid = f"rp-excl-{uuid4()}"
    keep = await seed_fact(session_id=sid, text="紅茶はアールグレイが好き")
    drop = await seed_fact(session_id=sid, text="紅茶はダージリンも飲む", fingerprint="fp-dup")
    dup = await seed_fact(session_id=sid, text="紅茶ならダージリン", fingerprint="fp-dup")
    request = RecallRequest(needs=("紅茶",))

    baseline = await recall_stable_facts(scope=_scope(sid), request=request, lexical_only=True)
    base_ids = {item["fact_id"] for item in baseline.ranked_items}
    assert keep in base_ids and (drop in base_ids or dup in base_ids)

    result = await recall_stable_facts(
        scope=_scope(sid), request=request, lexical_only=True, exclude_fact_ids=[drop]
    )
    ids = {item["fact_id"] for item in result.ranked_items}
    assert ids == {keep}
    assert result.diagnostics["excluded_count"] == 2


@pytest.mark.asyncio
async def test_recall_lexical_only_never_touches_embedder(isolated_store, monkeypatch):
    """[A5] lexical_only never resolves the default embedder nor encodes."""
    import app.services.memory_v11.recall as recall_module

    sid = f"rp-lex-{uuid4()}"
    await seed_fact(session_id=sid, text="紅茶はアールグレイが好き")

    def forbidden(_embedder):
        raise AssertionError("lexical_only must not resolve an embedder")

    monkeypatch.setattr(recall_module, "_resolve_embedder", forbidden)
    spy = SpyEmbedder()
    result = await recall_stable_facts(
        scope=_scope(sid),
        request=RecallRequest(needs=("紅茶",)),
        embedder=spy,
        lexical_only=True,
    )
    assert spy.calls == []
    assert result.status == "ok"
    assert result.diagnostics["degraded"] is False
    assert list(result.records.values()) == ["紅茶はアールグレイが好き"]


@pytest.mark.asyncio
async def test_recall_min_shared_bigrams_gates_admission_even_with_vectors(isolated_store, monkeypatch):
    """[A5][D] E5 can rank admitted facts but never admits on its own."""
    import struct

    import app.services.memory_v11.recall as recall_module

    sid = f"rp-gate-{uuid4()}"
    two = await seed_fact(session_id=sid, text="毎朝紅茶を飲む")       # 紅茶, 茶を ... shares 2+
    one = await seed_fact(session_id=sid, text="紅白歌合戦が好き")      # shares only 紅? no: 紅白 != 紅茶
    vec_only = await seed_fact(session_id=sid, text="コーヒー派ではない")  # no shared bigram

    vector = struct.pack("4f", 1.0, 0.0, 0.0, 0.0)

    async def all_embeddings(*, worldline, fact_versions):
        return {key: {"model": "spy-e5", "dimensions": 4, "vector": vector} for key in fact_versions}

    monkeypatch.setattr(recall_module, "_load_version_embeddings", all_embeddings)
    spy = SpyEmbedder()
    need = "紅茶を飲む話"
    assert shared_bigram_count(need=need, display="毎朝紅茶を飲む") >= 2
    assert shared_bigram_count(need=need, display="紅白歌合戦が好き") < 2
    assert shared_bigram_count(need=need, display="コーヒー派ではない") == 0

    gated = await recall_stable_facts(
        scope=_scope(sid),
        request=RecallRequest(needs=(need,)),
        embedder=spy,
        min_shared_bigrams=2,
    )
    assert spy.calls == [need]
    assert {item["fact_id"] for item in gated.ranked_items} == {two}

    ungated = await recall_stable_facts(
        scope=_scope(sid), request=RecallRequest(needs=(need,)), embedder=spy
    )
    # Without the gate the vector pool admits everything (existing behavior).
    assert {item["fact_id"] for item in ungated.ranked_items} >= {two, vec_only}
    del one


def test_notice_variants_for_injected_turns():
    """[A8] notice wording switches only when facts were injected."""
    for factory in (invalid_result, unavailable_result, unsupported_result):
        plain = factory("x")
        assert NO_USABLE_FACTS_NOTICE in plain.block
        assert plain.facts_injected is False
        injected = factory("x", facts_injected=True)
        assert INJECTED_FACTS_LOOKUP_UNAVAILABLE_NOTICE in injected.block
        assert "No usable stable facts" not in injected.block
        assert injected.facts_injected is True
        assert injected.block.startswith("MEMORY CANDIDATES\n")


# ---------------------------------------------------------------------------
# Resident candidate loader: scope + rank signals
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resident_loader_scope_and_observation_counts(isolated_store):
    """[A7] distinct observations across versions; legacy_import counts >= 2."""
    sid = f"rp-load-{uuid4()}"
    # Same observation "o1" attached to v1 and v2 -> counted once; o2 new -> 2.
    multi = await seed_fact(
        session_id=sid,
        text="猫を二匹飼っている",
        prior_kinds=("create",),
        change_kind="refine",
        evidence=[["o1"], ["o1", "o2"]],
    )
    legacy = await seed_fact(
        session_id=sid, text="出身は大阪", prior_kinds=("legacy_import",), change_kind="user_edit"
    )
    legacy_many = await seed_fact(
        session_id=sid,
        text="眼鏡をかけている",
        change_kind="legacy_import",
        evidence=[["a", "b", "c"]],
    )
    await seed_fact(session_id=sid, text="削除済み", state="deleted")
    await seed_fact(session_id=sid, text="岡部モードの事実", identity_mode="okabe")
    await seed_fact(session_id=f"{sid}-other", text="他人の事実")

    rows = {row["fact_id"]: row for row in await load_resident_fact_candidates(_scope(sid))}
    assert set(rows) == {multi, legacy, legacy_many}
    assert rows[multi]["observation_count"] == 2
    assert rows[legacy]["observation_count"] == 2  # no evidence, legacy floor
    assert rows[legacy]["change_kind"] == "user_edit"
    assert rows[legacy_many]["observation_count"] == 3
    assert rows[multi]["version_no"] == 2
    assert rows[multi]["display_text"] == "猫を二匹飼っている"
