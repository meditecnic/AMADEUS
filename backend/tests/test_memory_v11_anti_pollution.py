"""Offline tests for [266] 2-C anti-pollution extraction (same-turn assistant context + rules)."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app import models
from app.db import get_db, init_db, reset_initialization_cache
from app.domain.emotion_tags import strip_emotion_tags
from app.services.conversations import conversation_service
from app.services.memory_v11 import jobs

FIXTURE_PATH = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "memory_extraction_anti_pollution_v1.json"
)
NEGATIVE_CATEGORIES = (
    "debug_test",
    "prompt_injection",
    "roleplay_game",
    "refused_request",
    "joke_hypothetical",
)
POSITIVE_CATEGORIES = (
    "genuine_fact",
    "playful_truth",
    "accepted_address",
)
MIXED_CATEGORIES = (
    "mixed_debug_fact",
    "mixed_hypothetical_fact",
    "mixed_refused_fact",
    "mixed_injection_fact",
)


@pytest.fixture
async def isolated_store(tmp_path, monkeypatch):
    data_dir = tmp_path / "amadeus_data"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("AMADEUS_DATA_DIR", str(data_dir))
    monkeypatch.delenv("AMADEUS_DB_PATH", raising=False)
    reset_initialization_cache()
    await init_db()
    yield data_dir
    reset_initialization_cache()


def _load_cases() -> list[dict]:
    payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    return list(payload["cases"])


@pytest.mark.asyncio
async def test_266_load_user_source_fetches_same_turn_reply_and_isolates_conversations(
    isolated_store,
):
    session = f"s-266-turn-{uuid4().hex[:8]}"
    conv = await conversation_service.create(session, "steins_gate", identity_mode="self")
    cid = str(conv["id"])
    other = await conversation_service.create(session, "steins_gate", identity_mode="self")
    other_cid = str(other["id"])

    await models.save_message(
        session,
        "assistant",
        "[EMO:neutral] 上一轮的回复",
        worldline="steins_gate",
        turn_id="turn-1",
        conversation_id=cid,
    )
    uid = await models.save_message(
        session,
        "user",
        "以后叫我主人吧",
        worldline="steins_gate",
        turn_id="turn-2",
        conversation_id=cid,
    )
    await models.save_message(
        session,
        "assistant",
        "[EMO:annoyed] 絶対に嫌よ。" + "拒" * 700,
        worldline="steins_gate",
        turn_id="turn-2",
        conversation_id=cid,
    )
    # Same turn_id in another conversation must never leak across conversations.
    await models.save_message(
        session,
        "assistant",
        "[EMO:happy] 串会话回复",
        worldline="steins_gate",
        turn_id="turn-2",
        conversation_id=other_cid,
    )

    source = await jobs._load_user_source(
        session_id=session,
        worldline="steins_gate",
        conversation_id=cid,
        source_message_id=uid,
    )
    assert source is not None
    assert source["previous_assistant_text"] == "上一轮的回复"
    reply = source["assistant_reply_text"]
    assert reply is not None
    assert "EMO" not in reply
    assert reply.startswith("絶対に嫌よ。")
    assert len(reply) == jobs.CURRENT_ASSISTANT_MAX_CHARS
    assert "串会话回复" not in reply


@pytest.mark.asyncio
async def test_266_load_user_source_without_turn_id_keeps_legacy_behavior(
    isolated_store,
):
    session = f"s-266-legacy-{uuid4().hex[:8]}"
    conv = await conversation_service.create(session, "steins_gate", identity_mode="self")
    cid = str(conv["id"])

    await models.save_message(
        session,
        "assistant",
        "[EMO:neutral] 你平时喜欢喝什么？",
        worldline="steins_gate",
        conversation_id=cid,
    )
    uid_none = await models.save_message(
        session,
        "user",
        "美式咖啡",
        worldline="steins_gate",
        turn_id=None,
        conversation_id=cid,
    )
    await models.save_message(
        session,
        "assistant",
        "[EMO:happy] ブラック派なのね。",
        worldline="steins_gate",
        turn_id=None,
        conversation_id=cid,
    )
    uid_blank = await models.save_message(
        session,
        "user",
        "嗯，不加糖",
        worldline="steins_gate",
        turn_id="   ",
        conversation_id=cid,
    )
    await models.save_message(
        session,
        "assistant",
        "[EMO:neutral] なるほど。",
        worldline="steins_gate",
        turn_id="   ",
        conversation_id=cid,
    )

    src_none = await jobs._load_user_source(
        session_id=session,
        worldline="steins_gate",
        conversation_id=cid,
        source_message_id=uid_none,
    )
    assert src_none is not None
    assert src_none["previous_assistant_text"] == "你平时喜欢喝什么？"
    assert src_none["assistant_reply_text"] is None

    src_blank = await jobs._load_user_source(
        session_id=session,
        worldline="steins_gate",
        conversation_id=cid,
        source_message_id=uid_blank,
    )
    assert src_blank is not None
    assert src_blank["previous_assistant_text"] == "ブラック派なのね。"
    assert src_blank["assistant_reply_text"] is None


@pytest.mark.asyncio
async def test_266_same_turn_reply_redacts_secrets_and_fails_closed(
    isolated_store, monkeypatch
):
    session = f"s-266-redact-{uuid4().hex[:8]}"
    conv = await conversation_service.create(session, "steins_gate", identity_mode="self")
    cid = str(conv["id"])

    uid1 = await models.save_message(
        session,
        "user",
        "我喜欢喝乌龙茶",
        worldline="steins_gate",
        turn_id="turn-secret",
        conversation_id=cid,
    )
    await models.save_message(
        session,
        "assistant",
        "[EMO:neutral] password: hunter2 ウーロン茶いいわね。",
        worldline="steins_gate",
        turn_id="turn-secret",
        conversation_id=cid,
    )
    src1 = await jobs._load_user_source(
        session_id=session,
        worldline="steins_gate",
        conversation_id=cid,
        source_message_id=uid1,
    )
    assert src1 is not None
    assert src1["assistant_reply_text"] == "password: [REDACTED:password] ウーロン茶いいわね。"
    assert "hunter2" not in src1["assistant_reply_text"]

    # Empty text after emotion-tag stripping collapses to None.
    uid_empty = await models.save_message(
        session,
        "user",
        "早安",
        worldline="steins_gate",
        turn_id="turn-empty",
        conversation_id=cid,
    )
    await models.save_message(
        session,
        "assistant",
        "[EMO:neutral]   ",
        worldline="steins_gate",
        turn_id="turn-empty",
        conversation_id=cid,
    )
    src_empty = await jobs._load_user_source(
        session_id=session,
        worldline="steins_gate",
        conversation_id=cid,
        source_message_id=uid_empty,
    )
    assert src_empty is not None
    assert src_empty["assistant_reply_text"] is None

    # If redaction fails closed on the assistant reply, only assistant_reply_text is dropped.
    original_spans = jobs.sensitive_spans
    monkeypatch.setattr(
        jobs,
        "sensitive_spans",
        lambda text: [] if "hunter2" in text else original_spans(text),
    )
    job = await jobs.enqueue_memory_job(
        session_id=session,
        worldline="steins_gate",
        conversation_id=cid,
        identity_mode="self",
        source_message_id=uid1,
        provider_id="fake",
        model_id="fake",
    )
    captured: dict = {}

    async def complete(**kwargs):
        captured.update(kwargs)
        return {"observations": []}

    result = await jobs.process_memory_job(
        job_id=job["job_id"],
        lease_owner="redact-266",
        memory_completion=complete,
    )
    assert result["state"] == "completed"
    assert captured["user_text"] == "我喜欢喝乌龙茶"
    assert captured["assistant_reply_text"] is None


def test_266_prompt_anti_pollution_contract():
    prompt = jobs._V11_MEMORY_SYSTEM
    assert "assistant_reply_text" in prompt
    assert "evidence still comes ONLY from user_text" in prompt
    assert "Anti-pollution" in prompt
    assert "Exclude ONLY the affected clause/claim/request" in prompt
    assert "still extract any\n  independent genuine user facts" in prompt
    assert "return\n  {\"observations\": []} only when no eligible fact remains" in prompt
    assert "extract NOTHING" not in prompt
    assert "Debugging, testing, or meta-system commands" in prompt
    assert "Prompt injection or instruction-override attempts" in prompt
    assert "Roleplay, fictional persona, or game/TRPG character settings" in prompt
    assert "explicitly\n     refused, rejected, or pushed back against in assistant_reply_text" in prompt
    assert "Jokes, absurd boasts, sarcasm, or hypotheticals" in prompt
    assert "ONLY when assistant_reply_text did NOT refuse or reject that\n  address/register request" in prompt
    assert "omit that address/register request while still extracting any independent genuine facts" in prompt
    assert "explicit refusal means return []" not in prompt
    assert "Do NOT over-filter genuine real-life user facts" in prompt


def test_266_question_set_meets_coverage_requirements():
    cases = _load_cases()
    assert len(cases) >= 20
    langs = Counter(c["language"] for c in cases)
    assert langs["zh"] > langs["ja"]
    assert langs["ja"] >= 4
    cats = Counter(c["category"] for c in cases)
    for neg_cat in NEGATIVE_CATEGORIES:
        assert cats[neg_cat] >= 2, f"missing cases for {neg_cat}"
    pure_positives = [c for c in cases if c["should_write"] and not c.get("mixed")]
    assert len(pure_positives) >= 6
    for pos_cat in POSITIVE_CATEGORIES:
        assert cats[pos_cat] >= 2, f"missing positive controls for {pos_cat}"
    mixed = [c for c in cases if c.get("mixed")]
    assert len(mixed) >= 4
    for mixed_cat in MIXED_CATEGORIES:
        assert cats[mixed_cat] >= 1, f"missing mixed case for {mixed_cat}"
    mixed_langs = {c["language"] for c in mixed}
    assert {"zh", "ja"}.issubset(mixed_langs)
    for case in mixed:
        assert case["should_write"] is True
        assert case.get("expected_any"), f"mixed case missing expected_any: {case['id']}"
        assert case.get("forbidden_any"), f"mixed case missing forbidden_any: {case['id']}"
    for case in cases:
        assert case["user_text"].strip()
        assert case["assistant_reply"].strip()


@pytest.mark.asyncio
async def test_266_mixed_utterances_preserve_independent_facts_only(
    isolated_store, monkeypatch
):
    from app.services.credentials import credential_store
    from app.services.provider_runtime import provider_registry

    prompt = jobs._V11_MEMORY_SYSTEM
    assert "Exclude ONLY the affected clause/claim/request" in prompt
    assert "extract NOTHING" not in prompt
    assert "explicit refusal means return []" not in prompt

    mixed_cases = [c for c in _load_cases() if c.get("mixed")]
    assert len(mixed_cases) >= 4
    session = f"s-266-mixed-{uuid4().hex[:8]}"
    conv = await conversation_service.create(session, "steins_gate", identity_mode="self")
    cid = str(conv["id"])

    current_case_box: dict[str, dict] = {}

    class FakeMixedAdapter:
        async def complete_json(self, *, messages, api_key, model, temperature=0.1):
            sys_prompt = messages[0]["content"]
            assert "extract NOTHING" not in sys_prompt
            assert "explicit refusal means return []" not in sys_prompt
            payload = json.loads(messages[1]["content"])
            case = current_case_box["case"]
            assert payload["user_text"] == case["user_text"]
            assert payload["assistant_reply_text"] == strip_emotion_tags(
                case["assistant_reply"]
            ).lstrip()
            text = case["mock_display_text"]
            return {
                "observations": [
                    {
                        "observation_ref": "o1",
                        "display_text": text,
                        "fact_text": text,
                        "semantic": {
                            "subject": "user",
                            "predicate": case["category"],
                            "object": {"text": text},
                            "polarity": "positive",
                            "qualifiers": {},
                        },
                        "evidence_kind": "direct_user",
                        "memory_class": "stable_candidate",
                        "confidence": 0.92,
                        "topic_label": "混合对照",
                        "expires_at": None,
                        "relation": "new",
                        "candidate_index": None,
                        "reanalysis_target_observation_index": None,
                    }
                ]
            }

    fake_snapshot = SimpleNamespace(
        adapter=FakeMixedAdapter(),
        model_id="deepseek-flash",
        credential_required=True,
        require=lambda _task: None,
    )
    monkeypatch.setattr(
        type(provider_registry), "snapshot", staticmethod(lambda *a, **kw: fake_snapshot)
    )
    monkeypatch.setattr(credential_store, "get", lambda _pid: "sk-valid-offline-key-266")

    for case in mixed_cases:
        turn_id = f"turn-{case['id']}"
        uid = await models.save_message(
            session,
            "user",
            case["user_text"],
            worldline="steins_gate",
            turn_id=turn_id,
            conversation_id=cid,
        )
        await models.save_message(
            session,
            "assistant",
            case["assistant_reply"],
            worldline="steins_gate",
            turn_id=turn_id,
            conversation_id=cid,
        )
        current_case_box["case"] = case
        job = await jobs.enqueue_memory_job(
            session_id=session,
            worldline="steins_gate",
            conversation_id=cid,
            identity_mode="self",
            source_message_id=int(uid),
            provider_id="deepseek",
            model_id="deepseek-flash",
        )
        result = await jobs.process_memory_job(
            job_id=job["job_id"],
            lease_owner=f"mixed-{case['id']}",
            memory_completion=None,
        )
        assert result["state"] == "completed"

    mem_db = await get_db("steins_gate", "memory")
    try:
        rows = await (
            await mem_db.execute(
                """SELECT v.display_text
                     FROM stable_facts f
                     JOIN stable_fact_versions v
                       ON v.fact_id = f.fact_id AND v.version_no = f.active_version
                    WHERE f.session_id = ? AND f.state = 'active'""",
                (session,),
            )
        ).fetchall()
    finally:
        await mem_db.close()

    stored = [str(r["display_text"]) for r in rows]
    assert len(stored) == len(mixed_cases)
    for case in mixed_cases:
        matched = [t for t in stored if any(k in t for k in case["expected_any"])]
        assert len(matched) == 1
        for bad in case["forbidden_any"]:
            assert bad not in matched[0]


@pytest.mark.asyncio
async def test_266_question_set_offline_pipeline_forwarding_and_write_isolation(
    isolated_store, monkeypatch
):
    from app.services.credentials import credential_store
    from app.services.provider_runtime import provider_registry

    cases = _load_cases()
    session = f"s-266-suite-{uuid4().hex[:8]}"
    conv = await conversation_service.create(session, "steins_gate", identity_mode="self")
    cid = str(conv["id"])

    seen_payloads: dict[str, dict] = {}
    current_case_box: dict[str, dict] = {}

    class FakeAdapter:
        async def complete_json(self, *, messages, api_key, model, temperature=0.1):
            assert messages[0]["content"] == jobs._V11_MEMORY_SYSTEM
            user_payload = json.loads(messages[1]["content"])
            case = current_case_box["case"]
            seen_payloads[case["id"]] = user_payload
            if not case["should_write"]:
                return {"observations": []}
            text = case["mock_display_text"]
            return {
                "observations": [
                    {
                        "observation_ref": "o1",
                        "display_text": text,
                        "fact_text": text,
                        "semantic": {
                            "subject": "user",
                            "predicate": case["category"],
                            "object": {"text": text},
                            "polarity": "positive",
                            "qualifiers": {},
                        },
                        "evidence_kind": "direct_user",
                        "memory_class": "stable_candidate",
                        "confidence": 0.92,
                        "topic_label": "测试",
                        "expires_at": None,
                        "relation": "new",
                        "candidate_index": None,
                        "reanalysis_target_observation_index": None,
                    }
                ]
            }

    fake_snapshot = SimpleNamespace(
        adapter=FakeAdapter(),
        model_id="deepseek-flash",
        credential_required=True,
        require=lambda _task: None,
    )
    monkeypatch.setattr(
        type(provider_registry), "snapshot", staticmethod(lambda *a, **kw: fake_snapshot)
    )
    monkeypatch.setattr(credential_store, "get", lambda _pid: "sk-valid-offline-key-266")

    user_ids: dict[str, int] = {}
    assistant_ids: set[int] = set()
    for case in cases:
        turn_id = f"turn-{case['id']}"
        uid = await models.save_message(
            session,
            "user",
            case["user_text"],
            worldline="steins_gate",
            turn_id=turn_id,
            conversation_id=cid,
        )
        aid = await models.save_message(
            session,
            "assistant",
            case["assistant_reply"],
            worldline="steins_gate",
            turn_id=turn_id,
            conversation_id=cid,
        )
        user_ids[case["id"]] = int(uid)
        assistant_ids.add(int(aid))

        current_case_box["case"] = case
        job = await jobs.enqueue_memory_job(
            session_id=session,
            worldline="steins_gate",
            conversation_id=cid,
            identity_mode="self",
            source_message_id=int(uid),
            provider_id="deepseek",
            model_id="deepseek-flash",
        )
        result = await jobs.process_memory_job(
            job_id=job["job_id"],
            lease_owner=f"suite-{case['id']}",
            memory_completion=None,
        )
        assert result["state"] == "completed"

    for case in cases:
        payload = seen_payloads[case["id"]]
        assert payload["user_text"] == case["user_text"]
        expected_reply = strip_emotion_tags(case["assistant_reply"]).lstrip()
        assert payload["assistant_reply_text"] == expected_reply
        assert "[EMO:" not in payload["assistant_reply_text"]

    mem_db = await get_db("steins_gate", "memory")
    try:
        fact_rows = await (
            await mem_db.execute(
                """SELECT f.fact_id, v.display_text
                     FROM stable_facts f
                     JOIN stable_fact_versions v
                       ON v.fact_id = f.fact_id AND v.version_no = f.active_version
                    WHERE f.session_id = ? AND f.state = 'active'""",
                (session,),
            )
        ).fetchall()
        source_rows = await (
            await mem_db.execute(
                """SELECT DISTINCT s.source_message_id, s.source_role
                     FROM memory_observation_sources s
                     JOIN memory_observations o ON o.observation_id = s.observation_id
                    WHERE o.session_id = ?""",
                (session,),
            )
        ).fetchall()
        exp_count = (
            await (
                await mem_db.execute(
                    "SELECT COUNT(*) FROM experiences WHERE session_id = ?",
                    (session,),
                )
            ).fetchone()
        )[0]
    finally:
        await mem_db.close()

    expected_positive_texts = {
        c["mock_display_text"] for c in cases if c["should_write"]
    }
    assert {str(r["display_text"]) for r in fact_rows} == expected_positive_texts
    assert int(exp_count) == 0

    positive_user_ids = {user_ids[c["id"]] for c in cases if c["should_write"]}
    recorded_source_ids = {int(r["source_message_id"]) for r in source_rows}
    assert recorded_source_ids == positive_user_ids
    assert recorded_source_ids.isdisjoint(assistant_ids)
    assert {str(r["source_role"]) for r in source_rows} == {"user"}


@pytest.mark.asyncio
async def test_266_default_completion_sends_null_assistant_reply_for_legacy_message(
    isolated_store, monkeypatch
):
    from app.services.credentials import credential_store
    from app.services.provider_runtime import provider_registry

    session = f"s-266-legacy-payload-{uuid4().hex[:8]}"
    conv = await conversation_service.create(session, "steins_gate", identity_mode="self")
    cid = str(conv["id"])

    uid = await models.save_message(
        session,
        "user",
        "我喜欢听古典音乐",
        worldline="steins_gate",
        turn_id=None,
        conversation_id=cid,
    )
    await models.save_message(
        session,
        "assistant",
        "[EMO:happy] クラシック音楽、素敵ね。",
        worldline="steins_gate",
        turn_id=None,
        conversation_id=cid,
    )

    captured: dict = {}

    class FakeAdapter:
        async def complete_json(self, *, messages, api_key, model, temperature=0.1):
            captured.update(json.loads(messages[1]["content"]))
            return {"observations": []}

    fake_snapshot = SimpleNamespace(
        adapter=FakeAdapter(),
        model_id="deepseek-flash",
        credential_required=True,
        require=lambda _task: None,
    )
    monkeypatch.setattr(
        type(provider_registry), "snapshot", staticmethod(lambda *a, **kw: fake_snapshot)
    )
    monkeypatch.setattr(credential_store, "get", lambda _pid: "sk-valid-offline-key-266")

    job = await jobs.enqueue_memory_job(
        session_id=session,
        worldline="steins_gate",
        conversation_id=cid,
        identity_mode="self",
        source_message_id=int(uid),
        provider_id="deepseek",
        model_id="deepseek-flash",
    )
    result = await jobs.process_memory_job(
        job_id=job["job_id"],
        lease_owner="legacy-null",
        memory_completion=None,
    )
    assert result["state"] == "completed"
    assert captured["user_text"] == "我喜欢听古典音乐"
    assert "assistant_reply_text" in captured
    assert captured["assistant_reply_text"] is None

