"""MEMORY-V11 S3a: mode-gated prompt retrieval (P01 / P02)."""
from __future__ import annotations

import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.db import get_db, init_db, reset_initialization_cache
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


@pytest.mark.asyncio
async def test_s3a_shadow_mode_does_not_inject_v11_facts(isolated_store, monkeypatch):
    """P01: shadow writes stay off the prompt; retrieval remains legacy."""
    monkeypatch.setenv("AMADEUS_MEMORY_MODE", "shadow")
    session_id = f"prompt-shadow-{uuid4()}"
    secret_marker = f"V11_ONLY_SHADOW_{uuid4().hex[:8]}"
    await create_stable_fact(
        session_id=session_id,
        worldline="steins_gate",
        identity_mode="self",
        display_text=secret_marker,
        semantic_json={"subject": "user", "relation": "marker", "object": "shadow"},
    )
    prompt = await compile_for_session(_session_obj(session_id), "你好")
    assert secret_marker not in prompt


@pytest.mark.asyncio
async def test_s3a_legacy_mode_does_not_inject_v11_facts(isolated_store, monkeypatch):
    monkeypatch.setenv("AMADEUS_MEMORY_MODE", "legacy")
    session_id = f"prompt-legacy-{uuid4()}"
    marker = f"V11_ONLY_LEGACY_{uuid4().hex[:8]}"
    await create_stable_fact(
        session_id=session_id,
        worldline="steins_gate",
        identity_mode="self",
        display_text=marker,
        semantic_json={"subject": "user", "relation": "marker", "object": "legacy"},
    )
    prompt = await compile_for_session(_session_obj(session_id), "你好")
    assert marker not in prompt


@pytest.mark.asyncio
async def test_s3a_v11_mode_injects_same_scope_active_facts_only(
    isolated_store, monkeypatch
):
    """P02: v11 mode injects only active same-scope stable facts."""
    monkeypatch.setenv("AMADEUS_MEMORY_MODE", "v11")
    session_id = f"prompt-v11-{uuid4()}"
    self_marker = f"SELF_ANIME_{uuid4().hex[:8]}"
    okabe_marker = f"OKABE_FRUIT_{uuid4().hex[:8]}"
    await create_stable_fact(
        session_id=session_id,
        worldline="steins_gate",
        identity_mode="self",
        # Display text carries the query term so relevance-aware retrieval
        # can select the same-scope card (S3b does not bulk-inject zeros).
        display_text=f"{self_marker} 喜欢动漫",
        semantic_json={"subject": "user", "relation": "likes", "object": "anime"},
    )
    await create_stable_fact(
        session_id=session_id,
        worldline="steins_gate",
        identity_mode="okabe",
        display_text=f"{okabe_marker} 喜欢水果",
        semantic_json={"subject": "user", "relation": "likes", "object": "fruit"},
    )

    self_prompt = await compile_for_session(
        _session_obj(session_id, identity_mode="self"),
        "动漫",
    )
    assert self_marker not in self_prompt
    assert okabe_marker not in self_prompt
    assert "\nCORE FACTS\n" not in self_prompt
    assert "FACT_USE_CONTRACT" not in self_prompt

    okabe_prompt = await compile_for_session(
        _session_obj(session_id, identity_mode="okabe"),
        "水果",
    )
    assert okabe_marker not in okabe_prompt
    assert self_marker not in okabe_prompt
    assert "\nCORE FACTS\n" not in okabe_prompt


# ---------------------------------------------------------------------------
# MEMORY-V11-S3-GAP-PROBE R1: bounded, relevance-aware stable-fact budget.
# Red until S3 replaces bulk-pull + char-slice with a bounded selection.
# Behavioral only: assert on the final compiled prompt, never on internals.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_r1_irrelevant_facts_must_not_flood_prompt(isolated_store, monkeypatch):
    """P02/P03: obviously irrelevant facts must not bulk-enter the prompt."""
    monkeypatch.setenv("AMADEUS_MEMORY_MODE", "v11")
    session_id = f"r1-noise-{uuid4()}"
    markers = []
    for i in range(10):
        marker = f"IRR_{i:02d}"
        markers.append(marker)
        await create_stable_fact(
            session_id=session_id,
            worldline="steins_gate",
            identity_mode="self",
            display_text=f"{marker} 数学の本を読んでいる",
            semantic_json={"subject": "user", "relation": "study", "object": f"math_{i}"},
        )

    prompt = await compile_for_session(_session_obj(session_id), "zqx_nebula_9")
    for marker in markers:
        assert marker not in prompt, (
            f"irrelevant fact leaked into prompt ({marker}); no relevance "
            "threshold/budget is applied, everything under the count cap is injected"
        )


@pytest.mark.asyncio
async def test_r1_budget_selects_whole_facts_not_char_slices(
    isolated_store, monkeypatch
):
    """P03: the final injection is a bounded selection, not a char slice.

    The total display text far exceeds the core token budget, so a selection
    budget must drop whole facts; a char slice leaves truncated fragments in
    the prompt. Red while PromptCompiler._fit truncates mid-fact.
    """
    monkeypatch.setenv("AMADEUS_MEMORY_MODE", "v11")
    session_id = f"r1-budget-{uuid4()}"
    texts = []
    for i in range(30):
        text = f"MARK_{i:02d} " + "好" * 300
        texts.append(text)
        await create_stable_fact(
            session_id=session_id,
            worldline="steins_gate",
            identity_mode="self",
            display_text=text,
            semantic_json={"subject": "user", "relation": "marker", "object": f"m{i}"},
        )

    prompt = await compile_for_session(_session_obj(session_id), "你好")
    injected = [t for t in texts if t in prompt]
    assert len(injected) <= 24, "injection must be bounded by an explicit count budget"
    for text in texts:
        marker = text.split()[0]
        assert marker not in prompt or text in prompt, (
            f"char-sliced fact fragment in prompt: {marker!r} present but the "
            "fact body is truncated; budget must select whole facts, not slice text"
        )


# ---------------------------------------------------------------------------
# MEMORY-V11-S3-GAP-PROBE R2: final-injection dedupe by semantic fingerprint.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_r2_semantic_fingerprint_dedupe_before_injection(
    isolated_store, monkeypatch
):
    """P03: equal active semantic fingerprints must not double-occupy budget.

    Two fact_ids that canonicalize to the same fingerprint (the motivating
    likes_anime / like_anime drift) must inject at most one card. Red while
    the prompt surface injects every active row without fingerprint dedupe.
    """
    monkeypatch.setenv("AMADEUS_MEMORY_MODE", "v11")
    session_id = f"r2-fp-{uuid4()}"
    marker_a = f"KONO_A_{uuid4().hex[:8]}"
    marker_b = f"KONO_B_{uuid4().hex[:8]}"
    shared_semantic = {"subject": "user", "relation": "likes_anime", "object": "konosuba"}
    await create_stable_fact(
        session_id=session_id,
        worldline="steins_gate",
        identity_mode="self",
        display_text=f"{marker_a} 好きなアニメはこのすば",
        semantic_json=shared_semantic,
    )
    await create_stable_fact(
        session_id=session_id,
        worldline="steins_gate",
        identity_mode="self",
        display_text=f"{marker_b} このすばが好き",
        semantic_json=shared_semantic,
    )

    prompt = await compile_for_session(_session_obj(session_id), "このすば")
    assert not (marker_a in prompt and marker_b in prompt), (
        "same semantic fingerprint injected twice; final injection surface "
        "must dedupe by fingerprint before consuming budget"
    )

# ---------------------------------------------------------------------------
# MEMORY-V11-S3C-2: experience prompt integration (plan §10.2 / §10.3).
# ---------------------------------------------------------------------------


def _exp_fingerprint(semantic: dict) -> str:
    import hashlib

    return hashlib.sha256(
        json.dumps(
            semantic, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()


async def _seed_prompt_experience(
    *,
    session_id: str,
    identity_mode: str = "self",
    display_text: str,
    status: str = "active",
    expires_at: str | None = None,
    source_state: str = "present",
) -> str:
    semantic = {"subject": "user", "predicate": "event", "object": display_text[:24]}
    db = await get_db("steins_gate", "memory")
    try:
        observation_id = str(uuid4())
        now = "2026-08-01T00:00:00+00:00"
        await db.execute(
            """INSERT INTO memory_observations(
                   observation_id, session_id, identity_mode, display_text,
                   semantic_json, semantic_fingerprint, evidence_kind, memory_class,
                   confidence, topic_label_proposal, status, extractor_version,
                   reanalysis_count, expires_at, created_at, updated_at
               ) VALUES(?,?,?,?,?,?, 'direct_user', 'episodic', 0.9, NULL, 'attached',
                        'memory-v11-1', 0, ?, ?, ?)""",
            (
                observation_id,
                session_id,
                identity_mode,
                display_text,
                json.dumps(semantic, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                _exp_fingerprint(semantic),
                expires_at,
                now,
                now,
            ),
        )
        await db.execute(
            """INSERT INTO memory_observation_sources(
                   observation_id, source_message_id, conversation_id, source_role,
                   source_fingerprint, source_created_at, source_state, excerpt
               ) VALUES(?,?,?, 'user', ?, ?, ?, NULL)""",
            (
                observation_id,
                int(uuid4().int % 10**9),
                "conv-p",
                f"fp-{observation_id[:8]}",
                now,
                source_state,
            ),
        )
        experience_id = str(uuid4())
        await db.execute(
            """INSERT INTO experiences(
                   experience_id, session_id, conversation_id, identity_mode,
                   observation_id, display_text, semantic_json, semantic_fingerprint,
                   confidence, status, expires_at, created_at, updated_at, deleted_at
               ) VALUES(?,?,?,?,?,?,?,?,0.9,?,?,?,?,NULL)""",
            (
                experience_id,
                session_id,
                conversation_id := "conv-p",
                identity_mode,
                observation_id,
                display_text,
                json.dumps(semantic, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                _exp_fingerprint(semantic),
                status,
                expires_at,
                now,
                now,
            ),
        )
        await db.commit()
        return experience_id
    finally:
        await db.close()


def _compile_with_mode(session_id: str, message: str, mode: str, monkeypatch) -> str:
    monkeypatch.setenv("AMADEUS_MEMORY_MODE", mode)
    return compile_for_session(_session_obj(session_id), message)


@pytest.mark.asyncio
async def test_r5_v11_prompt_injects_both_sections(isolated_store, monkeypatch):
    """H: v11 prompt shows stable fact + experience in separate sections."""
    session_id = f"r5-both-{uuid4()}"
    fact_marker = f"ANIME_FACT_{uuid4().hex[:8]}"
    exp_marker = f"ANIME_EXP_{uuid4().hex[:8]}"
    await create_stable_fact(
        session_id=session_id,
        worldline="steins_gate",
        identity_mode="self",
        display_text=f"{fact_marker} 好きなアニメはこのすば",
        semantic_json={"subject": "user", "relation": "likes", "object": "anime"},
    )
    await _seed_prompt_experience(
        session_id=session_id,
        display_text=f"{exp_marker} このすばのイベントに行った",
    )
    monkeypatch.setenv("AMADEUS_MEMORY_MODE", "v11")
    prompt = await compile_for_session(_session_obj(session_id), "このすば")
    assert fact_marker not in prompt
    assert exp_marker in prompt
    assert "FACT_USE_CONTRACT" not in prompt
    assert "EXPERIENCE_USE_CONTRACT" in prompt
    assert "\nCORE FACTS\n" not in prompt
    assert "EPISODIC MEMORY" in prompt
    assert "WORKING CONTEXT" in prompt


@pytest.mark.asyncio
async def test_r5_v11_no_legacy_episodic_leak(isolated_store, monkeypatch):
    """I: v11 never reads legacy episodic_memories."""
    session_id = f"r5-legacy-{uuid4()}"
    legacy_marker = f"LEGACY_ONLY_{uuid4().hex[:8]}"
    v11_marker = f"V11_EXP_{uuid4().hex[:8]}"
    db = await get_db("steins_gate", "memory")
    try:
        await db.execute(
            """INSERT INTO episodic_memories(
                   session_id, identity_mode, content, confidence, importance,
                   source_message_ids, created_at
               ) VALUES(?, 'self', ?, 0.9, 0.5, '[]', ?)""",
            (session_id, legacy_marker, "2026-08-01T00:00:00+00:00"),
        )
        await db.commit()
    finally:
        await db.close()
    await _seed_prompt_experience(
        session_id=session_id,
        display_text=f"{v11_marker} このすばのイベントに行った",
    )
    monkeypatch.setenv("AMADEUS_MEMORY_MODE", "v11")
    prompt = await compile_for_session(_session_obj(session_id), "このすば")
    assert legacy_marker not in prompt
    assert v11_marker in prompt


@pytest.mark.asyncio
async def test_r5_shadow_and_legacy_gates_no_v11_experience_leak(
    isolated_store, monkeypatch
):
    """J: shadow/legacy modes never inject v11 experiences."""
    session_id = f"r5-gate-{uuid4()}"
    v11_marker = f"V11_ONLY_{uuid4().hex[:8]}"
    await _seed_prompt_experience(
        session_id=session_id,
        display_text=f"{v11_marker} このすばのイベントに行った",
    )
    for mode in ("shadow", "legacy"):
        prompt = await _compile_with_mode(session_id, "このすば", mode, monkeypatch)
        assert v11_marker not in prompt, f"{mode} must not leak v11 experiences"


@pytest.mark.asyncio
async def test_r5_experience_retrieval_failure_fail_soft(isolated_store, monkeypatch):
    """K: experience selector failure omits experiences; chat still compiles."""
    import app.services.memory_v11.retrieval as retrieval_module

    session_id = f"r5-soft-{uuid4()}"
    fact_marker = f"SOFT_FACT_{uuid4().hex[:8]}"
    exp_marker = f"SOFT_EXP_{uuid4().hex[:8]}"
    await create_stable_fact(
        session_id=session_id,
        worldline="steins_gate",
        identity_mode="self",
        display_text=f"{fact_marker} 好きなアニメはこのすば",
        semantic_json={"subject": "user", "relation": "likes", "object": "anime"},
    )
    await _seed_prompt_experience(
        session_id=session_id,
        display_text=f"{exp_marker} このすばのイベントに行った",
    )

    async def boom(*args, **kwargs):
        raise RuntimeError("forced experience selector failure")

    monkeypatch.setattr(retrieval_module, "select_experience_candidates", boom)
    monkeypatch.setenv("AMADEUS_MEMORY_MODE", "v11")
    prompt = await compile_for_session(_session_obj(session_id), "このすば")
    assert fact_marker not in prompt, "stable facts now use recall_memory, not first prompt"
    assert exp_marker not in prompt, "failed experience retrieval must omit experiences"
    assert "FACT_USE_CONTRACT" not in prompt
    assert "\nCORE FACTS\n" not in prompt

@pytest.mark.asyncio
async def test_r5_final_prompt_experience_whole_item_under_global_pressure(
    isolated_store, monkeypatch
):
    """P0: global budget pressure drops whole experiences; never char-slices."""
    from app.services import prompt_compiler as pc_module

    session_id = f"r5-final-exp-{uuid4()}"
    monkeypatch.setenv("AMADEUS_MEMORY_MODE", "v11")
    seeded = []
    for i in range(15):
        marker = f"EXP_END_{i:02d}"
        text = f"先週秋葉原で買った本 {marker} " + "。" * 200
        seeded.append((marker, text))
        await _seed_prompt_experience(session_id=session_id, display_text=text)

    real_counter = pc_module.prompt_compiler.token_counter
    monkeypatch.setattr(
        pc_module.prompt_compiler,
        "token_counter",
        lambda text: int(real_counter(text) * 1.5),
    )
    monkeypatch.setattr(pc_module.prompt_compiler, "input_budget", 2400)

    prompt = await compile_for_session(_session_obj(session_id), "秋葉原")
    survivors = [marker for marker, _ in seeded if marker in prompt]
    for marker, text in seeded:
        assert marker not in prompt or text in prompt, (
            f"{marker}: experience marker present but content partial/truncated"
        )
    if survivors:
        assert "EXPERIENCE_USE_CONTRACT" in prompt, (
            "surviving experiences must keep the usage contract intact"
        )


@pytest.mark.asyncio
async def test_r5_final_prompt_core_facts_whole_item_under_global_pressure(
    isolated_store, monkeypatch
):
    """P0 probe: core facts must share the whole-item safety mechanism."""
    from app.services import prompt_compiler as pc_module

    session_id = f"r5-final-fact-{uuid4()}"
    monkeypatch.setenv("AMADEUS_MEMORY_MODE", "v11")
    seeded = []
    for i in range(12):
        marker = f"FACT_END_{i:02d}"
        text = f"好きなアニメはこのすば {marker} " + "。" * 180
        seeded.append((marker, text))
        await create_stable_fact(
            session_id=session_id,
            worldline="steins_gate",
            identity_mode="self",
            display_text=text,
            semantic_json={"subject": "user", "relation": "likes", "object": f"a{i:02d}"},
        )

    real_counter = pc_module.prompt_compiler.token_counter
    monkeypatch.setattr(
        pc_module.prompt_compiler,
        "token_counter",
        lambda text: int(real_counter(text) * 1.5),
    )
    monkeypatch.setattr(pc_module.prompt_compiler, "input_budget", 2400)

    prompt = await compile_for_session(_session_obj(session_id), "このすば")
    survivors = [marker for marker, _ in seeded if marker in prompt]
    for marker, text in seeded:
        assert marker not in prompt or text in prompt, (
            f"{marker}: stable fact marker present but content partial/truncated"
        )
    if survivors:
        assert "FACT_USE_CONTRACT" in prompt, (
            "surviving facts must keep the usage contract intact"
        )


# ---------------------------------------------------------------------------
# MEMORY-V11-S3E-3: D38 working-context consumer authority (single injection).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_s3e3_working_context_consumer_authority(isolated_store, monkeypatch):
    """WORKING CONTEXT carries a temporary/non-authoritative boundary."""
    monkeypatch.setenv("AMADEUS_MEMORY_MODE", "v11")
    session_id = f"s3e3-wc-{uuid4()}"
    session = _session_obj(session_id)
    session.memory_summary = "比較中、候補Bの確認待ち"
    prompt = await compile_for_session(session, "こんにちは")
    assert "WORKING CONTEXT" in prompt
    assert "Temporary conversation continuity only" in prompt
    assert "Not durable user-fact or long-term episodic authority" in prompt
    assert "Must not override" in prompt
    assert prompt.count("比較中、候補Bの確認待ち") == 1
    assert "AMADEUS_WORKING_SUMMARY_D38_V1" not in prompt


@pytest.mark.asyncio
async def test_s3e3_working_context_empty_still_renders_boundary(
    isolated_store, monkeypatch
):
    monkeypatch.setenv("AMADEUS_MEMORY_MODE", "v11")
    session_id = f"s3e3-wc-empty-{uuid4()}"
    session = _session_obj(session_id)
    prompt = await compile_for_session(session, "こんにちは")
    assert "WORKING CONTEXT" in prompt
    assert "Temporary conversation continuity only" in prompt
