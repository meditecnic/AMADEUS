"""M2: Memory quality gate — thresholds, throttle, prefer-empty.

Frozen semantics under test:
- Hard thresholds: confidence >= 0.7 AND importance >= 0.65 to persist.
- Per-turn cap: at most 2 core facts after filtering.
- Rhythm: intent trigger OR N=3 turns since last core extraction.
- Greeting/chitchat → no core extraction.
- Only user evidence counts; assistant-only → rejected.
- self and okabe share the same thresholds.
"""
from __future__ import annotations

import pytest

from app import db as db_module
from app.db import init_db, reset_initialization_cache
from app.services.memory import (
    CoreFactCandidate,
    ExtractionResult,
    EpisodicCandidate,
    MemoryService,
    _has_memory_intent,
    _CORE_CONFIDENCE_THRESHOLD,
    _CORE_IMPORTANCE_THRESHOLD,
    _MAX_CORE_PER_TURN,
    _N_TURNS_CORE_THROTTLE,
    memory_service,
)


@pytest.fixture
async def isolated_store(tmp_path, monkeypatch):
    monkeypatch.delenv("AMADEUS_DB_PATH", raising=False)
    monkeypatch.setenv("AMADEUS_DATA_DIR", str(tmp_path))
    reset_initialization_cache()
    await init_db()
    return tmp_path


# ---------------------------------------------------------------------------
# Intent detection
# ---------------------------------------------------------------------------

class TestMemoryIntentDetection:
    def test_chinese_intent(self):
        assert _has_memory_intent("请记住我喜欢咖啡") is True
        assert _has_memory_intent("我叫小明") is True
        assert _has_memory_intent("我的爱好是读书") is True

    def test_japanese_intent(self):
        assert _has_memory_intent("覚えておいて") is True
        assert _has_memory_intent("私の名前は紅莉栖") is True

    def test_english_intent(self):
        assert _has_memory_intent("remember my name is John") is True
        assert _has_memory_intent("I like coffee") is True

    def test_no_intent(self):
        assert _has_memory_intent("今天天气不错") is False
        assert _has_memory_intent("你好") is False
        assert _has_memory_intent("谢谢") is False
        assert _has_memory_intent("再见") is False

    def test_empty(self):
        assert _has_memory_intent("") is False


# ---------------------------------------------------------------------------
# Quality gate thresholds
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_high_quality_core_fact_persisted(isolated_store):
    """Core fact meeting both thresholds is persisted."""
    owner = "m2-high"
    extraction = ExtractionResult(
        working_summary="",
        episodic=[],
        core_facts=[
            CoreFactCandidate(
                fact_key="favorite_drink",
                fact_value="咖啡",
                confidence=0.9,
                importance=0.8,
                source_message_ids=[1],
            ),
        ],
    )
    await memory_service.apply_extraction(owner, "steins_gate", extraction, identity_mode="self")
    facts = await memory_service.select_core_facts(owner, "steins_gate", "drink", identity_mode="self")
    assert len(facts) == 1
    assert "咖啡" in facts[0].content


@pytest.mark.asyncio
async def test_low_confidence_core_fact_rejected(isolated_store):
    """Core fact below confidence threshold is silently dropped."""
    owner = "m2-low-conf"
    extraction = ExtractionResult(
        working_summary="",
        episodic=[],
        core_facts=[
            CoreFactCandidate(
                fact_key="guess",
                fact_value="maybe",
                confidence=0.5,  # below 0.7
                importance=0.8,
                source_message_ids=[1],
            ),
        ],
    )
    await memory_service.apply_extraction(owner, "steins_gate", extraction, identity_mode="self")
    facts = await memory_service.select_core_facts(owner, "steins_gate", "guess", identity_mode="self")
    assert len(facts) == 0


@pytest.mark.asyncio
async def test_low_importance_core_fact_rejected(isolated_store):
    """Core fact below importance threshold is silently dropped."""
    owner = "m2-low-imp"
    extraction = ExtractionResult(
        working_summary="",
        episodic=[],
        core_facts=[
            CoreFactCandidate(
                fact_key="trivia",
                fact_value="something",
                confidence=0.9,
                importance=0.3,  # below 0.65
                source_message_ids=[1],
            ),
        ],
    )
    await memory_service.apply_extraction(owner, "steins_gate", extraction, identity_mode="self")
    facts = await memory_service.select_core_facts(owner, "steins_gate", "trivia", identity_mode="self")
    assert len(facts) == 0


@pytest.mark.asyncio
async def test_boundary_threshold_values_accepted(isolated_store):
    """Core fact at exact threshold boundaries (0.7 / 0.65) is accepted."""
    owner = "m2-boundary"
    extraction = ExtractionResult(
        working_summary="",
        episodic=[],
        core_facts=[
            CoreFactCandidate(
                fact_key="boundary_fact",
                fact_value="exact",
                confidence=0.7,  # exact threshold
                importance=0.65,  # exact threshold
                source_message_ids=[1],
            ),
        ],
    )
    await memory_service.apply_extraction(owner, "steins_gate", extraction, identity_mode="self")
    facts = await memory_service.select_core_facts(owner, "steins_gate", "boundary", identity_mode="self")
    assert len(facts) == 1


# ---------------------------------------------------------------------------
# Per-turn cap
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_per_turn_core_cap(isolated_store):
    """More than _MAX_CORE_PER_TURN qualified facts → only first 2 persisted."""
    owner = "m2-cap"
    extraction = ExtractionResult(
        working_summary="",
        episodic=[],
        core_facts=[
            CoreFactCandidate(fact_key=f"fact_{i}", fact_value=f"val_{i}",
                              confidence=0.9, importance=0.8, source_message_ids=[1])
            for i in range(5)
        ],
    )
    await memory_service.apply_extraction(owner, "steins_gate", extraction, identity_mode="self")
    facts = await memory_service.select_core_facts(owner, "steins_gate", "fact", identity_mode="self")
    assert len(facts) <= _MAX_CORE_PER_TURN


# ---------------------------------------------------------------------------
# Rhythm control (skip_core)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_skip_core_prevents_persistence(isolated_store):
    """When skip_core=True, no core facts are persisted even if qualified."""
    owner = "m2-skip"
    extraction = ExtractionResult(
        working_summary="test summary",
        episodic=[],
        core_facts=[
            CoreFactCandidate(
                fact_key="should_skip",
                fact_value="skipped",
                confidence=0.9,
                importance=0.8,
                source_message_ids=[1],
            ),
        ],
    )
    await memory_service.apply_extraction(
        owner, "steins_gate", extraction, identity_mode="self", skip_core=True
    )
    facts = await memory_service.select_core_facts(owner, "steins_gate", "skip", identity_mode="self")
    assert len(facts) == 0


@pytest.mark.asyncio
async def test_skip_core_still_allows_episodic(isolated_store):
    """When skip_core=True, episodic memories are still persisted."""
    owner = "m2-skip-ep"
    extraction = ExtractionResult(
        working_summary="",
        episodic=[
            EpisodicCandidate(
                content="一起去喝了咖啡",
                confidence=0.8,
                importance=0.6,
                source_message_ids=[1],
            ),
        ],
        core_facts=[
            CoreFactCandidate(
                fact_key="drink",
                fact_value="咖啡",
                confidence=0.9,
                importance=0.8,
                source_message_ids=[1],
            ),
        ],
    )
    await memory_service.apply_extraction(
        owner, "steins_gate", extraction, identity_mode="self", skip_core=True
    )
    # Episodic should be persisted (direct DB check — no embedding/FTS needed)
    from app.db import get_db
    db = await get_db("steins_gate", "memory")
    try:
        cur = await db.execute(
            "SELECT COUNT(*) FROM episodic_memories WHERE session_id=? AND identity_mode='self'",
            (owner,),
        )
        row = await cur.fetchone()
        assert row[0] >= 1  # episodic found
    finally:
        await db.close()
    # Core should NOT be persisted
    facts = await memory_service.select_core_facts(owner, "steins_gate", "drink", identity_mode="self")
    assert len(facts) == 0


# ---------------------------------------------------------------------------
# Okabe same thresholds
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_okabe_same_thresholds(isolated_store):
    """Okabe mode uses same thresholds as self."""
    owner = "m2-okabe"
    # High quality → persisted in okabe
    extraction = ExtractionResult(
        working_summary="",
        episodic=[],
        core_facts=[
            CoreFactCandidate(
                fact_key="okabe_drink",
                fact_value="Dr Pepper",
                confidence=0.9,
                importance=0.8,
                source_message_ids=[1],
            ),
        ],
    )
    await memory_service.apply_extraction(owner, "steins_gate", extraction, identity_mode="okabe")
    facts = await memory_service.select_core_facts(owner, "steins_gate", "drink", identity_mode="okabe")
    assert len(facts) == 1

    # Low quality → rejected in okabe too
    owner2 = "m2-okabe-low"
    extraction2 = ExtractionResult(
        working_summary="",
        episodic=[],
        core_facts=[
            CoreFactCandidate(
                fact_key="okabe_low",
                fact_value="maybe",
                confidence=0.5,
                importance=0.8,
                source_message_ids=[1],
            ),
        ],
    )
    await memory_service.apply_extraction(owner2, "steins_gate", extraction2, identity_mode="okabe")
    facts2 = await memory_service.select_core_facts(owner2, "steins_gate", "low", identity_mode="okabe")
    assert len(facts2) == 0


# ---------------------------------------------------------------------------
# Constants sanity
# ---------------------------------------------------------------------------

def test_threshold_constants():
    assert _CORE_CONFIDENCE_THRESHOLD == 0.7
    assert _CORE_IMPORTANCE_THRESHOLD == 0.65
    assert _MAX_CORE_PER_TURN == 2
    assert _N_TURNS_CORE_THROTTLE == 3


# ---------------------------------------------------------------------------
# N=3 rhythm integration
# ---------------------------------------------------------------------------

def test_n3_rhythm_constants():
    """N=3 throttle constant is correctly set."""
    assert _N_TURNS_CORE_THROTTLE == 3


@pytest.mark.asyncio
async def test_skip_core_false_when_turns_reached(isolated_store):
    """When turns_since_last_core >= N=3 and no intent, skip_core should be False."""
    # Simulate: no intent, but 3 turns elapsed
    user_text = "今天天气不错"  # no intent
    assert _has_memory_intent(user_text) is False
    turns = 3
    skip_core = not (_has_memory_intent(user_text) or turns >= _N_TURNS_CORE_THROTTLE)
    assert skip_core is False  # should NOT skip — N=3 reached


@pytest.mark.asyncio
async def test_skip_core_true_when_under_threshold(isolated_store):
    """When turns_since_last_core < N=3 and no intent, skip_core should be True."""
    user_text = "今天天气不错"
    turns = 2
    skip_core = not (_has_memory_intent(user_text) or turns >= _N_TURNS_CORE_THROTTLE)
    assert skip_core is True  # should skip — under threshold


@pytest.mark.asyncio
async def test_intent_overrides_turn_counter(isolated_store):
    """Intent keyword should trigger core even at turn 1."""
    user_text = "请记住我喜欢咖啡"
    turns = 1
    skip_core = not (_has_memory_intent(user_text) or turns >= _N_TURNS_CORE_THROTTLE)
    assert skip_core is False  # should NOT skip — intent detected


# ---------------------------------------------------------------------------
# User evidence validation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_empty_source_message_ids_rejected(isolated_store):
    """CoreFactCandidate with empty source_message_ids cannot be constructed."""
    # Pydantic enforces min_length=1 on source_message_ids.
    with pytest.raises(Exception):
        CoreFactCandidate(
            fact_key="no_source",
            fact_value="phantom",
            confidence=0.9,
            importance=0.8,
            source_message_ids=[],
        )


@pytest.mark.asyncio
async def test_invalid_source_message_ids_rejected(isolated_store):
    """Core facts with non-positive source_message_ids are filtered in apply_extraction."""
    owner = "m2-invalid-src"
    # Construct with valid ids first (Pydantic requires min_length=1)
    extraction = ExtractionResult(
        working_summary="",
        episodic=[],
        core_facts=[
            CoreFactCandidate(
                fact_key="valid_fact",
                fact_value="real",
                confidence=0.9,
                importance=0.8,
                source_message_ids=[1],
            ),
        ],
    )
    # Manually corrupt source_message_ids to simulate bad data
    extraction.core_facts[0].source_message_ids = [0, -1]  # non-positive
    await memory_service.apply_extraction(owner, "steins_gate", extraction, identity_mode="self")
    facts = await memory_service.select_core_facts(owner, "steins_gate", "valid", identity_mode="self")
    assert len(facts) == 0  # rejected by defensive check
