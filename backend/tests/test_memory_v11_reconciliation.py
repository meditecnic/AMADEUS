"""MEMORY-V11 S0 red contracts: observation → fact reconciliation (R01–R13).

Fixed import surface only:
  app.services.memory_v11.reconciler
  app.services.memory_v11.contracts
  app.services.memory_v11.repository

No fallback to legacy memory.service. apply path must go through validated
reconciler — raw operations are not a public write bypass.
"""
from __future__ import annotations

import importlib
from typing import Any

import pytest

from app.db import init_db, reset_initialization_cache
from app.services.memory import CoreFactCandidate, memory_service


def _reconciler():
    return importlib.import_module("app.services.memory_v11.reconciler")


def _contracts():
    return importlib.import_module("app.services.memory_v11.contracts")


def _repository():
    return importlib.import_module("app.services.memory_v11.repository")


@pytest.fixture
async def isolated_store(tmp_path, monkeypatch):
    monkeypatch.delenv("AMADEUS_DB_PATH", raising=False)
    monkeypatch.setenv("AMADEUS_DATA_DIR", str(tmp_path))
    reset_initialization_cache()
    await init_db()
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
    )


def test_s0_reconciler_module_exists():
    importlib.import_module("app.services.memory_v11.reconciler")
    importlib.import_module("app.services.memory_v11.contracts")
    importlib.import_module("app.services.memory_v11.repository")


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
