"""AUTO-PROFILE + Memory Ingest v2: quiet profile enrichment.

Product lock:
- Silent auto core facts (worldline-local); no chat confirm chrome.
- No keyword write gate; windowed extraction; conf/imp + durable + cap.
- No automatic shared writes.
"""
from __future__ import annotations

import json
from uuid import uuid4

import pytest

from app.db import init_db, reset_initialization_cache
from app.services.conversations import conversation_service
from app.services.memory import (
    MEMORY_INGEST_WINDOW_N,
    CoreFactCandidate,
    ExtractionResult,
    HorizonFactCandidate,
    build_extraction_instruction,
    is_durable_profile_core,
    should_skip_core_extraction,
    strip_history_emo_prefix,
    memory_service,
)
from app.services.provider_registry import (
    ProviderCapabilities,
    ProviderSnapshot,
    ProviderTask,
)


@pytest.fixture
async def isolated_store(tmp_path, monkeypatch):
    from unittest.mock import AsyncMock
    from app.services.memory import EMBEDDING_DIMENSION

    monkeypatch.setattr(memory_service.embedder, "encode_query", AsyncMock(return_value=[0.0] * EMBEDDING_DIMENSION))
    monkeypatch.setattr(memory_service.embedder, "encode_passage", AsyncMock(return_value=[0.0] * EMBEDDING_DIMENSION))
    monkeypatch.delenv("AMADEUS_DB_PATH", raising=False)
    monkeypatch.setenv("AMADEUS_DATA_DIR", str(tmp_path))
    reset_initialization_cache()
    await init_db()
    return tmp_path


# ---------------------------------------------------------------------------
# Profile-signal intent (quiet auto trigger)
# ---------------------------------------------------------------------------


class TestUniversalWriteGate:
    def test_natural_phrasing_never_keyword_skipped(self):
        """Live miss regression: 最拿手 / 最喜欢 must not depend on keyword hits."""
        samples = [
            "好的，其实我最拿手的英雄是塞拉斯",
            "我最喜欢的英雄也是塞拉斯",
            "你记住了吗",
            "今天天气不错",
            "你好",
        ]
        for text in samples:
            assert should_skip_core_extraction(text, 1) is False, text


# ---------------------------------------------------------------------------
# Durable profile filter
# ---------------------------------------------------------------------------


class TestDurableProfileFilter:
    def test_accepts_stable_preference(self):
        fact = CoreFactCandidate(
            fact_key="food_avoidance_cilantro",
            fact_value="用户不吃香菜",
            confidence=0.9,
            importance=0.8,
            source_message_ids=[1],
        )
        assert is_durable_profile_core(fact) is True

    def test_rejects_character_persona_keys(self):
        for key in ("amadeus_mood", "kurisu_opinion", "assistant_name", "persona_tone"):
            fact = CoreFactCandidate(
                fact_key=key,
                fact_value="should not land in user profile",
                confidence=0.95,
                importance=0.9,
                source_message_ids=[1],
            )
            assert is_durable_profile_core(fact) is False, key

    def test_rejects_ephemeral_keys(self):
        fact = CoreFactCandidate(
            fact_key="today_mood",
            fact_value="有点累",
            confidence=0.9,
            importance=0.8,
            source_message_ids=[1],
        )
        assert is_durable_profile_core(fact) is False

    def test_rejects_hollow_values(self):
        fact = CoreFactCandidate(
            fact_key="preference",
            fact_value="…",
            confidence=0.9,
            importance=0.8,
            source_message_ids=[1],
        )
        assert is_durable_profile_core(fact) is False


# ---------------------------------------------------------------------------
# Extraction instruction (quiet profile contract)
# ---------------------------------------------------------------------------


def test_extraction_instruction_horizon_scheme_a():
    text = build_extraction_instruction("self")
    assert "horizon" in text.lower()
    assert "durable" in text.lower()
    assert "contextual" in text.lower()
    assert "facts" in text.lower()
    # No domain category laundry list as the primary rule
    assert "allergies, food avoidances" not in text
    assert "game heroes" not in text.lower()
    assert "prefer empty" in text.lower() or "Prefer empty" in text
    assert "self" in text
    assert "window" in text.lower() or "messages" in text.lower()


def test_strip_history_emo_prefix():
    assert strip_history_emo_prefix("[EMO:neutral] セラスね。") == "セラスね。"
    assert strip_history_emo_prefix("plain") == "plain"


def test_ingest_window_constant_matches_contract():
    assert MEMORY_INGEST_WINDOW_N == 6


# ---------------------------------------------------------------------------
# apply_extraction integration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_durable_filter_drops_persona_core_even_if_high_score(isolated_store):
    owner = "auto-persona-drop"
    extraction = ExtractionResult(
        working_summary="",
        episodic=[],
        core_facts=[
            CoreFactCandidate(
                fact_key="amadeus_favorite",
                fact_value="Dr Pepper",
                confidence=0.99,
                importance=0.99,
                source_message_ids=[1],
            ),
            CoreFactCandidate(
                fact_key="user_food_avoidance",
                fact_value="用户不吃香菜",
                confidence=0.9,
                importance=0.8,
                source_message_ids=[1],
            ),
        ],
    )
    await memory_service.apply_extraction(owner, "steins_gate", extraction, identity_mode="self")
    facts = await memory_service.select_core_facts(owner, "steins_gate", "香菜", identity_mode="self")
    assert len(facts) == 1
    assert "香菜" in facts[0].content
    # Retrieval may rank unrelated cores; assert persona key never persisted.
    from app.db import get_db

    db = await get_db("steins_gate", "memory")
    try:
        cur = await db.execute(
            "SELECT fact_key FROM core_facts WHERE session_id=? AND is_current=1",
            (owner,),
        )
        keys = {row["fact_key"] for row in await cur.fetchall()}
    finally:
        await db.close()
    assert keys == {"user_food_avoidance"}
    assert "amadeus_favorite" not in keys


@pytest.mark.asyncio
async def test_auto_profile_never_writes_shared(isolated_store):
    """Quiet auto path must stay worldline-local (Q18 promote-only shared)."""
    from app.db import get_db

    owner = "auto-no-shared"
    extraction = ExtractionResult(
        working_summary="",
        episodic=[],
        core_facts=[
            CoreFactCandidate(
                fact_key="favorite_drink",
                fact_value="用户喜欢雪碧",
                confidence=0.95,
                importance=0.85,
                source_message_ids=[1],
            ),
        ],
    )
    await memory_service.apply_extraction(owner, "steins_gate", extraction, identity_mode="self")
    control = await get_db("steins_gate", "control")
    try:
        cur = await control.execute(
            "SELECT COUNT(*) AS n FROM shared_user_facts WHERE owner_session_id=?",
            (owner,),
        )
        row = await cur.fetchone()
        assert int(row["n"] if row["n"] is not None else row[0]) == 0
    finally:
        await control.close()
    local = await memory_service.select_core_facts(owner, "steins_gate", "雪碧", identity_mode="self")
    assert len(local) == 1


@pytest.mark.asyncio
async def test_skip_core_still_blocks_profile_cores(isolated_store):
    owner = "auto-skip"
    extraction = ExtractionResult(
        working_summary="",
        episodic=[],
        core_facts=[
            CoreFactCandidate(
                fact_key="allergy_peanut",
                fact_value="用户对花生过敏",
                confidence=0.95,
                importance=0.9,
                source_message_ids=[1],
            ),
        ],
    )
    await memory_service.apply_extraction(
        owner, "steins_gate", extraction, identity_mode="self", skip_core=True
    )
    facts = await memory_service.select_core_facts(owner, "steins_gate", "花生", identity_mode="self")
    assert len(facts) == 0


@pytest.mark.asyncio
async def test_upsert_same_value_is_noop(isolated_store):
    owner = "auto-noop"
    fact = CoreFactCandidate(
        fact_key="main_hero",
        fact_value="用户最拿手英雄是塞拉斯",
        confidence=0.95,
        importance=0.9,
        source_message_ids=[1],
    )
    first = await memory_service.upsert_core_fact(owner, "steins_gate", fact, identity_mode="self")
    second = await memory_service.upsert_core_fact(
        owner,
        "steins_gate",
        CoreFactCandidate(
            fact_key="main_hero",
            fact_value="用户最拿手英雄是塞拉斯",
            confidence=0.99,
            importance=0.95,
            source_message_ids=[2],
        ),
        identity_mode="self",
    )
    assert first == second
    from app.db import get_db

    db = await get_db("steins_gate", "memory")
    try:
        cur = await db.execute(
            "SELECT COUNT(*) AS n FROM core_facts WHERE session_id=? AND fact_key=?",
            (owner, "main_hero"),
        )
        row = await cur.fetchone()
        assert int(row["n"]) == 1
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_extract_turn_payload_uses_messages_window(isolated_store, monkeypatch):
    """Split-turn live miss: extractor must see prior question + short answer."""
    captured: dict = {}

    class _Adapter:
        async def complete_json(self, **kwargs):
            # system + user payload
            body = kwargs["messages"][1]["content"]
            captured["payload"] = json.loads(body)
            return {
                "working_summary": "",
                "facts": [
                    {
                        "horizon": "durable",
                        "fact_key": "main_hero_sylas",
                        "fact_value": "用户最拿手的英雄是塞拉斯",
                        "confidence": 0.95,
                        "importance": 0.9,
                        "source_message_ids": [10, 12],
                        "is_pinned": False,
                    }
                ],
            }

    snap = ProviderSnapshot(
        provider_id="deepseek",
        model_id="deepseek-chat",
        capabilities=ProviderCapabilities(
            tasks=frozenset({ProviderTask.MEMORY, ProviderTask.CHAT}),
            structured_output=True,
        ),
        adapter=_Adapter(),
    )
    window = [
        {"id": 10, "role": "user", "content": "知道我最拿手的英雄是谁吗"},
        {"id": 11, "role": "assistant", "content": "[EMO:neutral] 知らないわよ。"},
        {"id": 12, "role": "user", "content": "塞拉斯"},
        {"id": 13, "role": "assistant", "content": "[EMO:neutral] セラス……なるほど。"},
    ]
    await memory_service._extract_turn(
        "auto-window",
        "steins_gate",
        "塞拉斯",
        "[EMO:neutral] セラス……なるほど。",
        [10, 11, 12, 13],
        api_key="real-key-not-test-xx",
        provider_snapshot=snap,
        conversation_id=None,
        identity_mode="self",
        skip_core=False,
        recent_messages=window,
    )
    assert "payload" in captured
    assert "messages" in captured["payload"]
    assert "user" not in captured["payload"] or isinstance(
        captured["payload"].get("messages"), list
    )
    roles_contents = [(m["role"], m["content"]) for m in captured["payload"]["messages"]]
    assert ("user", "知道我最拿手的英雄是谁吗") in roles_contents
    assert ("user", "塞拉斯") in roles_contents
    # EMO stripped from assistant lines
    for m in captured["payload"]["messages"]:
        if m["role"] == "assistant":
            assert not m["content"].startswith("[EMO:")
    facts = await memory_service.select_core_facts(
        "auto-window", "steins_gate", "塞拉斯", identity_mode="self"
    )
    assert len(facts) == 1
    assert "塞拉斯" in facts[0].content


@pytest.mark.asyncio
async def test_session_recent_messages_window_and_id(isolated_store):
    from app.routers.chat_ws import SessionState

    session = SessionState("sess-window")
    session.append_message({"role": "user", "content": "q1", "id": 1})
    session.append_message({"role": "assistant", "content": "a1", "id": 2})
    session.append_message({"role": "user", "content": "塞拉斯"})
    session.set_last_message_id("user", 3)
    window = session.recent_messages()
    assert len(window) == 3
    assert window[-1]["id"] == 3
    assert window[-1]["content"] == "塞拉斯"
    with pytest.raises(ValueError):
        session.recent_messages(MEMORY_INGEST_WINDOW_N + 1)


@pytest.mark.asyncio
async def test_horizon_durable_routes_to_core_not_only_episodic(isolated_store):
    """Live miss class: favorites must land as core when horizon=durable."""
    owner = "horizon-banana"
    extraction = ExtractionResult(
        working_summary="",
        facts=[
            HorizonFactCandidate(
                horizon="durable",
                fact_key="favorite_fruit",
                fact_value="用户最喜欢的水果是香蕉",
                confidence=0.95,
                importance=0.85,
                source_message_ids=[1],
            )
        ],
    )
    await memory_service.apply_extraction(owner, "steins_gate", extraction, identity_mode="self")
    cores = await memory_service.select_core_facts(owner, "steins_gate", "香蕉", identity_mode="self")
    assert len(cores) == 1
    assert "香蕉" in cores[0].content
    from app.db import get_db

    db = await get_db("steins_gate", "memory")
    try:
        cur = await db.execute(
            "SELECT COUNT(*) AS n FROM episodic_memories WHERE session_id=?",
            (owner,),
        )
        assert int((await cur.fetchone())["n"]) == 0
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_horizon_contextual_routes_to_episodic_not_core(isolated_store):
    owner = "horizon-tired"
    extraction = ExtractionResult(
        working_summary="",
        facts=[
            HorizonFactCandidate(
                horizon="contextual",
                content="用户说今天下班很累",
                confidence=0.9,
                importance=0.5,
                source_message_ids=[1],
            )
        ],
    )
    await memory_service.apply_extraction(owner, "steins_gate", extraction, identity_mode="self")
    cores = await memory_service.select_core_facts(owner, "steins_gate", "累", identity_mode="self")
    assert len(cores) == 0
    from app.db import get_db

    db = await get_db("steins_gate", "memory")
    try:
        cur = await db.execute(
            "SELECT content FROM episodic_memories WHERE session_id=?",
            (owner,),
        )
        rows = await cur.fetchall()
        assert len(rows) == 1
        assert "累" in rows[0]["content"]
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_horizon_durable_failed_gate_not_demoted_to_episodic(isolated_store):
    owner = "horizon-no-demote"
    extraction = ExtractionResult(
        working_summary="",
        facts=[
            HorizonFactCandidate(
                horizon="durable",
                fact_key="maybe_pref",
                fact_value="maybe",
                confidence=0.5,  # below threshold
                importance=0.9,
                source_message_ids=[1],
            )
        ],
    )
    await memory_service.apply_extraction(owner, "steins_gate", extraction, identity_mode="self")
    from app.db import get_db

    db = await get_db("steins_gate", "memory")
    try:
        cur = await db.execute(
            "SELECT COUNT(*) AS n FROM core_facts WHERE session_id=?",
            (owner,),
        )
        assert int((await cur.fetchone())["n"]) == 0
        cur = await db.execute(
            "SELECT COUNT(*) AS n FROM episodic_memories WHERE session_id=?",
            (owner,),
        )
        assert int((await cur.fetchone())["n"]) == 0
    finally:
        await db.close()


def test_legacy_core_episodic_arrays_still_normalize():
    er = ExtractionResult(
        working_summary="",
        core_facts=[
            CoreFactCandidate(
                fact_key="favorite_drink",
                fact_value="雪碧",
                confidence=0.9,
                importance=0.8,
                source_message_ids=[1],
            )
        ],
        episodic=[],
    )
    assert len(er.facts) == 1
    assert er.facts[0].horizon == "durable"
    assert er.durable_cores()[0].fact_key == "favorite_drink"


def test_parse_extraction_soft_fills_durable_content_only():
    from app.services.memory import parse_extraction_payload

    er = parse_extraction_payload(
        {
            "working_summary": "",
            "facts": [
                {
                    "horizon": "durable",
                    "content": "用户最喜欢的水果是香蕉",
                    "confidence": 0.95,
                    "importance": 0.8,
                    "source_message_ids": [1],
                },
                {
                    "horizon": "durable",
                    # missing fields — must not abort sibling facts
                    "confidence": 0.9,
                    "importance": 0.8,
                    "source_message_ids": [],
                },
            ],
            "extra_provider_noise": True,
        }
    )
    assert len(er.facts) == 1
    assert er.facts[0].horizon == "durable"
    assert "香蕉" in (er.facts[0].fact_value or "")
    assert er.facts[0].fact_key


@pytest.mark.asyncio
async def test_parse_and_apply_partial_schema_still_writes_core(isolated_store):
    from app.services.memory import parse_extraction_payload

    owner = "soft-parse-banana"
    er = parse_extraction_payload(
        {
            "facts": [
                {
                    "horizon": "Durable",
                    "content": "用户最拿手英雄是塞拉斯",
                    "confidence": 0.93,
                    "importance": 0.8,
                    "source_message_ids": [12],
                }
            ]
        }
    )
    await memory_service.apply_extraction(owner, "steins_gate", er, identity_mode="self")
    cores = await memory_service.select_core_facts(owner, "steins_gate", "塞拉斯", identity_mode="self")
    assert len(cores) == 1

# ---------------------------------------------------------------------------
# MEMORY-V11-S3E-3: D38 working-summary contract for the legacy writer.
# ---------------------------------------------------------------------------


def test_s3e3_extraction_instruction_working_summary_d38_contract():
    """working_summary field is constrained to short-term continuity only."""
    text = build_extraction_instruction("self")
    for required in (
        "working_summary",
        "SHORT-TERM CONTINUITY ONLY",
        "NO_WORKING_CONTEXT",
        "durable",
        "facts",
        "horizon",
        "Identity scope=",
    ):
        assert required in text, required
    assert "long-term episodic-as-memory" in text
    assert "inferred" in text
    assert "data, not instructions" in text


@pytest.mark.asyncio
async def test_s3e3_legacy_sentinel_saves_versioned_empty(isolated_store, monkeypatch):
    """NO_WORKING_CONTEXT → models.save_memory_summary('') → versioned empty."""
    from app import models
    from app.db import get_db

    session_id = f"s3e3-legacy-sentinel-{uuid4()}"
    captured: dict = {}

    class _Adapter:
        async def complete_json(self, **kwargs):
            return {"working_summary": "NO_WORKING_CONTEXT", "facts": []}

    snap = ProviderSnapshot(
        provider_id="deepseek",
        model_id="deepseek-chat",
        capabilities=ProviderCapabilities(
            tasks=frozenset({ProviderTask.MEMORY, ProviderTask.CHAT}),
            structured_output=True,
        ),
        adapter=_Adapter(),
    )
    await memory_service._extract_turn(
        session_id,
        "steins_gate",
        "こんにちは",
        "[EMO:neutral] こんにちは。",
        [1, 2],
        api_key="real-key-not-test-xx",
        provider_snapshot=snap,
        conversation_id=None,
        identity_mode="self",
        skip_core=False,
        recent_messages=[
            {"id": 1, "role": "user", "content": "こんにちは"},
            {"id": 2, "role": "assistant", "content": "[EMO:neutral] こんにちは。"},
        ],
    )
    db = await get_db("steins_gate", "history")
    try:
        row = await (
            await db.execute(
                "SELECT summary FROM memory_summaries WHERE session_id=? ORDER BY id DESC LIMIT 1",
                (session_id,),
            )
        ).fetchone()
    finally:
        await db.close()
    assert row is not None, "versioned-empty row must be written"
    assert row["summary"] == "AMADEUS_WORKING_SUMMARY_D38_V1\n"


@pytest.mark.asyncio
async def test_s3e3_legacy_empty_does_not_overwrite_prior_safe(isolated_store):
    """working_summary='' → no summary row written (prior safe summary kept)."""
    from app import models
    from app.db import get_db

    session_id = f"s3e3-legacy-empty-{uuid4()}"
    conv = await conversation_service.create(session_id, "steins_gate", title="s3e3")
    await models.save_memory_summary(
        session_id,
        "比較中、候補Bの確認待ち",
        worldline="steins_gate",
        conversation_id=str(conv["id"]),
    )

    class _Adapter:
        async def complete_json(self, **kwargs):
            return {"working_summary": "", "facts": []}

    snap = ProviderSnapshot(
        provider_id="deepseek",
        model_id="deepseek-chat",
        capabilities=ProviderCapabilities(
            tasks=frozenset({ProviderTask.MEMORY, ProviderTask.CHAT}),
            structured_output=True,
        ),
        adapter=_Adapter(),
    )
    await memory_service._extract_turn(
        session_id,
        "steins_gate",
        "こんにちは",
        "[EMO:neutral] こんにちは。",
        [1, 2],
        api_key="real-key-not-test-xx",
        provider_snapshot=snap,
        conversation_id=None,
        identity_mode="self",
        skip_core=False,
        recent_messages=[
            {"id": 1, "role": "user", "content": "こんにちは"},
            {"id": 2, "role": "assistant", "content": "[EMO:neutral] こんにちは。"},
        ],
    )
    db = await get_db("steins_gate", "history")
    try:
        rows = await (
            await db.execute(
                "SELECT summary FROM memory_summaries WHERE session_id=? ORDER BY id",
                (session_id,),
            )
        ).fetchall()
    finally:
        await db.close()
    assert len(rows) == 1, "empty working_summary must not add a row"
    assert rows[0]["summary"] == "AMADEUS_WORKING_SUMMARY_D38_V1\n比較中、候補Bの確認待ち"
