from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from app import models
from app.db import init_db, reset_initialization_cache, validate_schema
from app.services.memory import CoreFactCandidate, EpisodicCandidate, memory_service, reciprocal_rank_fusion
from app.services.session_manager import session_coordinator
from app.services.soul_engine import SpeechEmotionMapper, soul_engine


@pytest.fixture
async def isolated_store(tmp_path, monkeypatch):
    monkeypatch.delenv("AMADEUS_DB_PATH", raising=False)
    monkeypatch.setenv("AMADEUS_DATA_DIR", str(tmp_path))
    reset_initialization_cache()
    await init_db()
    return tmp_path


@pytest.mark.asyncio
async def test_history_and_memory_are_physically_isolated(isolated_store):
    await models.save_message("okabe", "user", "SG only", worldline="steins_gate")
    await models.save_message("okabe", "user", "Beta only", worldline="beta")
    await memory_service.add_episode("okabe", "steins_gate", EpisodicCandidate(content="SG promise", confidence=.9, importance=.8, source_message_ids=[1]))
    await memory_service.add_episode("okabe", "beta", EpisodicCandidate(content="Beta event", confidence=.9, importance=.8, source_message_ids=[1]))

    sg = await models.get_session_messages("okabe", worldline="steins_gate")
    beta = await models.get_session_messages("okabe", worldline="beta")
    assert [row["content"] for row in sg] == ["SG only"]
    assert [row["content"] for row in beta] == ["Beta only"]
    assert (isolated_store / "worldlines" / "sg" / "history.sqlite3").exists()
    assert (isolated_store / "worldlines" / "beta" / "history.sqlite3").exists()
    assert (await validate_schema())["ok"] is True


@pytest.mark.asyncio
async def test_core_fact_supersession_keeps_one_hot_record(isolated_store):
    first = await memory_service.upsert_core_fact("okabe", "steins_gate", CoreFactCandidate(fact_key="favorite_drink", fact_value="coffee", confidence=.9, importance=.6, source_message_ids=[1]))
    second = await memory_service.upsert_core_fact("okabe", "steins_gate", CoreFactCandidate(fact_key="favorite_drink", fact_value="Dr Pepper", confidence=.95, importance=.8, source_message_ids=[2]))
    selected = await memory_service.select_core_facts("okabe", "steins_gate", "favorite drink")
    assert first != second
    assert len(selected) == 1
    assert "Dr Pepper" in selected[0].content


@pytest.mark.asyncio
async def test_phase2_s6_memory_isolated_by_identity_mode_no_auto_upgrade(isolated_store):
    """P2-S6 Q23: okabe facts invisible to self retrieval; no clone/upgrade into self."""
    from app.services.memory import ExtractionResult

    await memory_service.upsert_core_fact(
        "s6-user",
        "steins_gate",
        CoreFactCandidate(
            fact_key="okabe_drink",
            fact_value="Dr Pepper",
            confidence=0.95,
            importance=0.8,
            source_message_ids=[1],
        ),
        identity_mode="okabe",
    )
    await memory_service.add_episode(
        "s6-user",
        "steins_gate",
        EpisodicCandidate(
            content="Okabe promised to return the PhoneWave",
            confidence=0.9,
            importance=0.8,
            source_message_ids=[1],
        ),
        identity_mode="okabe",
    )

    # self lane starts blank — cannot see okabe-scoped memories
    self_facts = await memory_service.select_core_facts(
        "s6-user", "steins_gate", "Dr Pepper", identity_mode="self"
    )
    self_eps = await memory_service.lexical_search(
        "s6-user", "steins_gate", "PhoneWave", identity_mode="self"
    )
    assert self_facts == []
    assert self_eps == []

    # okabe lane still sees its own writes (default omit = okabe)
    okabe_facts = await memory_service.select_core_facts(
        "s6-user", "steins_gate", "Dr Pepper"
    )
    assert len(okabe_facts) == 1
    assert "Dr Pepper" in okabe_facts[0].content

    # Same fact_key may coexist per mode without superseding across modes
    await memory_service.upsert_core_fact(
        "s6-user",
        "steins_gate",
        CoreFactCandidate(
            fact_key="okabe_drink",
            fact_value="tea",
            confidence=0.9,
            importance=0.7,
            source_message_ids=[2],
        ),
        identity_mode="self",
    )
    okabe_after = await memory_service.select_core_facts(
        "s6-user", "steins_gate", "drink", identity_mode="okabe"
    )
    self_after = await memory_service.select_core_facts(
        "s6-user", "steins_gate", "drink", identity_mode="self"
    )
    assert any("Dr Pepper" in item.content for item in okabe_after)
    assert any("tea" in item.content for item in self_after)
    assert not any("Dr Pepper" in item.content for item in self_after)

    # apply_extraction under okabe must not land in self (negative upgrade path)
    extraction = ExtractionResult(
        working_summary="",
        episodic=[],
        core_facts=[
            CoreFactCandidate(
                fact_key="lab_member",
                fact_value="I am Okabe Rintaro",
                confidence=0.9,
                importance=0.9,
                source_message_ids=[3],
            )
        ],
    )
    await memory_service.apply_extraction(
        "s6-user", "steins_gate", extraction, identity_mode="okabe"
    )
    self_lab = await memory_service.select_core_facts(
        "s6-user", "steins_gate", "lab_member Rintaro", identity_mode="self"
    )
    okabe_lab = await memory_service.select_core_facts(
        "s6-user", "steins_gate", "lab_member Rintaro", identity_mode="okabe"
    )
    assert not any("lab_member" in item.content for item in self_lab)
    assert any("lab_member" in item.content and "Rintaro" in item.content for item in okabe_lab)


def test_rrf_merges_independent_rankings_in_python():
    scores = reciprocal_rank_fusion([1, 2, 3], [3, 2, 4])
    assert scores[2] > scores[1]
    assert scores[3] > scores[4]


@pytest.mark.asyncio
async def test_emotion_decay_uses_persisted_updated_at(isolated_store):
    start = datetime(2026, 7, 16, 0, 0, tzinfo=timezone.utc)
    raised = await soul_engine.apply_deltas("okabe", "steins_gate", {"irritation": .5}, now=start)
    restored = await soul_engine.restore_emotion("okabe", "steins_gate", now=start + timedelta(hours=1))
    baseline = soul_engine.profile("steins_gate").baselines.irritation
    assert raised.irritation > restored.irritation
    assert restored.irritation == pytest.approx(baseline, abs=.08)


@pytest.mark.asyncio
async def test_phase2_s5_emotion_states_isolated_by_identity_mode(isolated_store):
    """P2-S5 Q25: okabe and self emotion lanes do not merge; self starts from baseline."""
    start = datetime(2026, 7, 16, 12, 0, tzinfo=timezone.utc)
    baselines = soul_engine.profile("steins_gate").baselines

    raised = await soul_engine.apply_deltas(
        "s5-user",
        "steins_gate",
        {"trust": 0.4, "attachment": 0.35},
        now=start,
        identity_mode="okabe",
    )
    assert raised.trust > baselines.trust
    assert raised.attachment > baselines.attachment

    # Same session+worldline, self lane is independent blank baseline (not a copy).
    self_state = await soul_engine.restore_emotion(
        "s5-user",
        "steins_gate",
        now=start,
        identity_mode="self",
    )
    assert self_state.trust == pytest.approx(baselines.trust, abs=1e-6)
    assert self_state.attachment == pytest.approx(baselines.attachment, abs=1e-6)

    # Raising self must not pollute okabe.
    await soul_engine.apply_deltas(
        "s5-user",
        "steins_gate",
        {"irritation": 0.45},
        now=start,
        identity_mode="self",
    )
    okabe_again = await soul_engine.restore_emotion(
        "s5-user",
        "steins_gate",
        now=start,
        identity_mode="okabe",
    )
    assert okabe_again.trust == pytest.approx(raised.trust, abs=1e-6)
    assert okabe_again.irritation == pytest.approx(baselines.irritation, abs=0.05)

    # Default omit mode → okabe lane (backward compatible).
    defaulted = await soul_engine.restore_emotion("s5-user", "steins_gate", now=start)
    assert defaulted.trust == pytest.approx(raised.trust, abs=1e-6)

    # Same mode shares continuity across "new conversations" (session-scoped key).
    shared = await soul_engine.restore_emotion(
        "s5-user",
        "steins_gate",
        now=start,
        identity_mode="okabe",
    )
    assert shared.trust == pytest.approx(raised.trust, abs=1e-6)


def test_speech_emotion_scale_and_caps_are_worldline_specific():
    sg = SpeechEmotionMapper.map("ANGRY", 1.0, soul_engine.profile("steins_gate"))
    beta = SpeechEmotionMapper.map("ANGRY", 1.0, soul_engine.profile("beta"))
    assert sum(abs(v) for v in sg.values()) <= .12 + 1e-9
    assert sum(abs(v) for v in beta.values()) <= .18 + 1e-9
    assert beta["irritation"] > sg["irritation"]
    assert abs(sg["trust"]) <= .03 and abs(beta["trust"]) <= .03


@pytest.mark.asyncio
async def test_switch_cancels_active_task_before_revision_change(isolated_store):
    class FakeSession:
        def __init__(self):
            self.session_id = "okabe"
            self.worldline = "steins_gate"
            self.revision = 1
            self.current_epoch = 1
            self.history_epoch = 0
            self.history = [{"role": "user", "content": "old"}]
            self.memory_summary = "old"
            self.active_streams = set()
            self.is_busy = True
            self.active_task = asyncio.create_task(asyncio.sleep(60))

        async def load_from_db(self):
            self.history = await models.get_recent_messages(self.session_id, worldline=self.worldline)

    session = FakeSession()
    result = await session_coordinator.switch(session, "beta")
    assert result["cancelled_tasks"] == 1
    assert session.active_task is None
    assert session.worldline == "beta"
    assert session.revision == 2


@pytest.mark.asyncio
async def test_failed_worldline_load_restores_runtime_and_persisted_mapping(isolated_store):
    class FakeSession:
        def __init__(self):
            self.session_id = "rollback-user"
            self.worldline = "steins_gate"
            self.revision = 4
            self.current_epoch = 4
            self.history_epoch = 2
            self.history = [{"role": "user", "content": "old"}]
            self.memory_summary = "old summary"
            self.conversation_id = "sg-conversation"
            self.conversation_mode = "history"
            self.provider_id = "deepseek"
            self.model = "deepseek-v4-flash"
            self.api_key = ""
            self.last_assistant_emotion = "neutral"
            self.consecutive_no_emo_tags = 0
            self.active_streams = set()
            self.active_task = None
            self.is_busy = False

        async def enter_draft(self):
            self.provider_id = "changed-provider"
            raise RuntimeError("target database unavailable")

    session = FakeSession()
    await session_coordinator.persist_current(
        session.session_id,
        session.worldline,
        session.revision,
    )

    with pytest.raises(RuntimeError, match="target database unavailable"):
        await session_coordinator.switch(session, "beta", conversation_mode="draft")

    assert session.worldline == "steins_gate"
    assert session.conversation_id == "sg-conversation"
    assert session.history == [{"role": "user", "content": "old"}]
    assert session.memory_summary == "old summary"
    assert session.provider_id == "deepseek"
    assert session.revision == 5
    assert await session_coordinator.get_current(session.session_id) == ("steins_gate", 5)
