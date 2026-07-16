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
