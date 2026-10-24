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


# ---------------------------------------------------------------------------
# Resident profile: ranking, dedupe, cap, budget, render order (prompt side)
# ---------------------------------------------------------------------------

from app.services.prompt_compiler import (  # noqa: E402
    FACT_USE_COMPLETE_SENTENCE,
    FACT_USE_CONTRACT,
    PARTIAL_PROFILE_NO_TOOL_SENTENCE,
    PARTIAL_PROFILE_SENTENCE,
    PARTIAL_PROFILE_TOOL_SENTENCE,
    RESIDENT_MAX_FACTS,
    MemorySupplement,
    PromptCompiler,
    PromptInputs,
    ResidentProfile,
    compile_for_session,
    rank_resident_candidates,
    resident_fact_rule,
)
from app.services.soul_engine import EmotionVector  # noqa: E402

EMOTION = EmotionVector(
    trust=0.55, attachment=0.35, irritation=0.2, anxiety=0.1, jealousy=0.05, volatility=0.3
)
PARTIAL_TOOL_RULE = FACT_USE_CONTRACT.replace(
    FACT_USE_COMPLETE_SENTENCE, f"{PARTIAL_PROFILE_SENTENCE} {PARTIAL_PROFILE_TOOL_SENTENCE}"
)
PARTIAL_NO_TOOL_RULE = FACT_USE_CONTRACT.replace(
    FACT_USE_COMPLETE_SENTENCE, f"{PARTIAL_PROFILE_SENTENCE} {PARTIAL_PROFILE_NO_TOOL_SENTENCE}"
)


def _row(fact_id: str, text: str | None = None, **overrides) -> dict:
    row = {
        "fact_id": fact_id,
        "version_no": 1,
        "display_text": text or f"{fact_id} の事実",
        "is_pinned": False,
        "confidence": 0.9,
        "semantic_fingerprint": f"fp-{fact_id}",
        "change_kind": "create",
        "observation_count": 0,
        "has_legacy_import": False,
        "created_at": "2026-08-01T00:00:00+00:00",
        "updated_at": "2026-08-01T00:00:00+00:00",
    }
    row.update(overrides)
    return row


def _inputs(resident: ResidentProfile | None, *, supplement=None, episodic=(), message="こんにちは", **kw) -> PromptInputs:
    return PromptInputs(
        worldline="steins_gate",
        base_identity="You are Amadeus Kurisu.",
        emotion=EMOTION,
        core_facts=[],
        episodic=list(episodic),
        web_evidence=[],
        working_summary="",
        recent_history=[],
        current_user_message=message,
        identity_mode="self",
        resident_profile=resident,
        memory_supplement=supplement,
        **kw,
    )


def _core_section(prompt: str) -> str:
    start = prompt.index("CORE FACTS\n")
    return prompt[start:].split("\n\n", 1)[0]


def _fact_lines(prompt: str) -> list[str]:
    if "CORE FACTS\n" not in prompt:
        return []
    return [line[2:] for line in _core_section(prompt).split("\n") if line.startswith("- ")]


def test_rank_order_signals_and_fingerprint_dedupe():
    """[A7] pinned > user_edit > observations > confidence > updated_at > fact_id."""
    rows = [
        _row("f-id-b", observation_count=1),
        _row("f-id-a", observation_count=1),
        _row("f-recent", observation_count=1, updated_at="2026-09-01T00:00:00+00:00"),
        _row("f-conf", observation_count=1, confidence=0.95),
        _row("f-obs", observation_count=3, confidence=0.1),
        _row("f-legacy", observation_count=0, has_legacy_import=True, confidence=0.1),
        _row("f-edit", change_kind="user_edit", confidence=0.1),
        _row("f-pin", is_pinned=True, confidence=0.1),
        # Same fingerprint as f-obs but lower rank -> dropped by dedupe.
        _row("f-obs-dup", observation_count=1, semantic_fingerprint="fp-f-obs"),
        _row("f-obs-dup2", observation_count=0, semantic_fingerprint="fp-f-obs"),
    ]
    ranked = [row["fact_id"] for row in rank_resident_candidates(rows)]
    assert ranked == [
        "f-pin",
        "f-edit",
        "f-obs",
        "f-legacy",  # legacy_import counts as >= 2 observations
        "f-conf",
        "f-recent",
        "f-id-a",
        "f-id-b",
    ]
    assert "f-obs-dup" not in ranked and "f-obs-dup2" not in ranked


def test_select_caps_at_32_and_keeps_highest_ranked():
    """[A7] at most 32 resident facts; the rest make the profile partial."""
    rows = [_row(f"f{i:02d}", f"F{i:02d} 短い事実", observation_count=40 - i) for i in range(40)]
    profile = PromptCompiler().select_resident_profile(rows, tool_capable=True)
    assert RESIDENT_MAX_FACTS == 32
    assert [row["fact_id"] for row in profile.facts] == [f"f{i:02d}" for i in range(32)]
    assert profile.candidate_count == 40
    assert profile.complete is False


def test_select_fits_1200_budget_dropping_lowest_rank_whole_facts():
    """[A7][A3] core budget 1200 incl. header+rule; whole facts, lowest rank out."""
    compiler = PromptCompiler()
    # Lowest rank is created first, so it would render first: rank != render.
    rows = [
        _row(
            f"f{i:02d}",
            f"F{i:02d} " + "長" * 400,
            observation_count=20 - i,
            created_at=f"2026-08-{20 - i:02d}T00:00:00+00:00",
        )
        for i in range(12)
    ]
    profile = compiler.select_resident_profile(rows, tool_capable=False)
    kept = [row["fact_id"] for row in profile.facts]
    assert 0 < len(kept) < 12
    assert kept == [f"f{i:02d}" for i in range(len(kept))]
    section = compiler._format_resident_profile(profile.facts, complete=False, tool_capable=False)
    assert compiler.token_counter(section) <= 1200
    # One more fact would not fit -> the cut is budget-driven, not arbitrary.
    bigger = compiler._format_resident_profile(rows[: len(kept) + 1], complete=False, tool_capable=False)
    assert compiler.token_counter(bigger) > 1200
    for row in profile.facts:
        assert f"- {row['display_text']}\n" in section + "\n"  # never char-sliced


def test_render_order_pinned_first_then_created_at_then_fact_id():
    """[A7] render order differs from rank order."""
    rows = [
        _row("z-top", observation_count=9, created_at="2026-08-05T00:00:00+00:00"),
        _row("b-tie", created_at="2026-08-02T00:00:00+00:00"),
        _row("a-tie", created_at="2026-08-02T00:00:00+00:00"),
        _row("old", created_at="2026-08-01T00:00:00+00:00"),
        _row("pin-late", is_pinned=True, created_at="2026-08-09T00:00:00+00:00"),
        _row("pin-early", is_pinned=True, created_at="2026-08-08T00:00:00+00:00"),
    ]
    compiler = PromptCompiler()
    profile = compiler.select_resident_profile(rows, tool_capable=True)
    prompt = compiler.compile(_inputs(profile))
    assert _fact_lines(prompt) == [
        "pin-early の事実",
        "pin-late の事実",
        "old の事実",
        "a-tie の事実",
        "b-tie の事実",
        "z-top の事実",
    ]


def test_rule_complete_is_fact_use_contract_verbatim():
    """[A2] complete profile -> FACT_USE_CONTRACT unchanged."""
    compiler = PromptCompiler()
    profile = compiler.select_resident_profile([_row("f1", "猫を飼っている")], tool_capable=True)
    assert profile.complete
    prompt = compiler.compile(_inputs(profile))
    assert _core_section(prompt) == f"CORE FACTS\n{FACT_USE_CONTRACT}\n- 猫を飼っている"
    assert resident_fact_rule(complete=True, tool_capable=False) == FACT_USE_CONTRACT


@pytest.mark.parametrize(
    ("tool_capable", "expected_rule", "absent"),
    [
        (True, PARTIAL_TOOL_RULE, PARTIAL_PROFILE_NO_TOOL_SENTENCE),
        (False, PARTIAL_NO_TOOL_RULE, PARTIAL_PROFILE_TOOL_SENTENCE),
    ],
)
def test_rule_partial_replaces_only_the_unknown_sentence(tool_capable, expected_rule, absent):
    """[A2] partial profile: one sentence swapped; tool vs non-tool follow-up."""
    rows = [_row(f"f{i:02d}", observation_count=50 - i) for i in range(33)]
    compiler = PromptCompiler()
    profile = compiler.select_resident_profile(rows, tool_capable=tool_capable)
    assert not profile.complete
    prompt = compiler.compile(_inputs(profile))
    section = _core_section(prompt)
    assert section.split("\n")[1] == expected_rule
    assert FACT_USE_COMPLETE_SENTENCE not in prompt
    assert absent not in prompt
    before, after = FACT_USE_CONTRACT.split(FACT_USE_COMPLETE_SENTENCE)
    assert expected_rule.startswith(before) and expected_rule.endswith(after)


def test_no_facts_means_no_section_v11_but_legacy_keeps_none():
    """[A1] v11 with zero facts: no CORE FACTS and no "(none)"; legacy unchanged."""
    compiler = PromptCompiler()
    empty = compiler.select_resident_profile([], tool_capable=True)
    v11_prompt = compiler.compile(_inputs(empty))
    # (The persona bible itself mentions "CORE FACTS" in prose.)
    assert "CORE FACTS\n" not in v11_prompt and "(none)" not in v11_prompt
    assert "FACT_USE_CONTRACT" not in v11_prompt
    legacy_prompt = compiler.compile(_inputs(None))
    assert "\n\nCORE FACTS\n(none)\n\n" in legacy_prompt


def test_v11_section_order_core_before_state_supplement_after():
    """[C] identity, persona, CORE FACTS, CURRENT STATE, supplement, episodic."""
    from app.services.memory import RetrievedMemory

    supplement = _fake_supplement([("m1", "s1", "紅茶はアールグレイが好き")], need="紅茶は？")
    compiler = PromptCompiler()
    profile = compiler.select_resident_profile([_row("f1", "猫を飼っている")], tool_capable=True)
    episode = RetrievedMemory(id=1, kind="episodic", content="去年京都へ行った", score=1, confidence=1, importance=0.5, pinned=False)
    prompt = compiler.compile(_inputs(profile, supplement=supplement, episodic=[episode]))
    order = [
        prompt.index("IDENTITY AND SECURITY\n"),
        prompt.index("PERSONA BIBLE"),
        prompt.index("\n\nCORE FACTS\n"),
        prompt.index("\n\nCURRENT STATE\n"),
        prompt.index("\n\nMEMORY CANDIDATES\n"),
        prompt.index("\n\nEPISODIC MEMORY\n"),
        prompt.index("\n\nWORKING CONTEXT\n"),
        prompt.index("\n\nCURRENT USER MESSAGE (authoritative)\n"),
    ]
    assert order == sorted(order)


def _fake_supplement(items: list[tuple[str, str, str]], *, need: str) -> MemorySupplement:
    """Build a recall-shaped supplement: items are (ref, fact_id, text) in rank order."""
    from app.services.memory_v11.recall import RecallGroup, RecallResult, _render_block

    records = {ref: text for ref, _fid, text in items}
    groups = (
        RecallGroup(
            need_index=1,
            need_text=need,
            candidate_refs=tuple(ref for ref, _fid, _text in items),
            generated_count=len(items),
            truncated=False,
            omitted_due_to_budget=0,
        ),
    )
    ranked = [
        {"ref": ref, "fact_id": fid, "version_no": 1, "display_text": text, "need_indexes": [1]}
        for ref, fid, text in items
    ]
    return MemorySupplement(
        result=RecallResult(
            status="ok",
            groups=groups,
            records=records,
            block=_render_block(status="ok", groups=groups, records=records),
            ranked_items=ranked,
        )
    )


def _present_ids(prompt: str, rows: list[dict]) -> list[str]:
    lines = set(_fact_lines(prompt))
    return [row["fact_id"] for row in rows if row["display_text"] in lines]


def test_global_budget_drops_lowest_ranked_resident_not_last_rendered():
    """[A3] CURRENT STATE survives; resident shrinks whole, lowest rank first.

    Rank order is the reverse of render order (the lowest-ranked fact was
    created first, so it renders first): the first fact to go must still be
    the lowest-ranked one, and the rule switches complete -> partial.
    """
    rows = [
        _row("hi", "HI 最上位の事実" + "。" * 60, observation_count=3, created_at="2026-08-03T00:00:00+00:00"),
        _row("mid", "MID 中位の事実" + "。" * 60, observation_count=2, created_at="2026-08-02T00:00:00+00:00"),
        _row("lo", "LO 最下位の事実" + "。" * 60, observation_count=1, created_at="2026-08-01T00:00:00+00:00"),
    ]
    full_compiler = PromptCompiler()
    profile = full_compiler.select_resident_profile(rows, tool_capable=False)
    assert profile.complete
    full = full_compiler.compile(_inputs(profile))
    assert _fact_lines(full)[0].startswith("LO ")  # lowest rank renders first
    total = full_compiler.token_counter(full)

    rank_order = ["hi", "mid", "lo"]
    seen_sizes = set()
    for budget in range(total, 50, -1):
        compiler = PromptCompiler(input_budget=budget)
        report = compiler.compile_with_report(_inputs(profile))
        prompt = report.text
        assert "\n\nCURRENT STATE\n" in prompt
        present = _present_ids(prompt, rows)
        # Always a prefix of the rank order: the lowest-ranked fact (which is
        # rendered first) is the first to go, never the last-rendered one.
        assert present == rank_order[: len(present)], (budget, present)
        assert list(report.resident_fact_ids) == present
        seen_sizes.add(len(present))
        if present and len(present) < 3:
            assert report.resident_complete is False
            assert PARTIAL_NO_TOOL_RULE in prompt
            assert FACT_USE_COMPLETE_SENTENCE not in prompt
        elif len(present) == 3:
            assert FACT_USE_CONTRACT in prompt
    assert {3, 1, 0} <= seen_sizes


def test_global_budget_drops_supplement_before_episodic():
    """[C] supplement items (lowest rank first) go before any episodic item."""
    from app.services.memory import RetrievedMemory

    supplement = _fake_supplement(
        [("m1", "s1", "SUP1 紅茶はアールグレイ" + "。" * 40), ("m2", "s2", "SUP2 紅茶にミルク" + "。" * 40)],
        need="紅茶",
    )
    episodes = [
        RetrievedMemory(id=i + 1, kind="episodic", content=f"EP{i} 旅行の思い出" + "。" * 40, score=1, confidence=1, importance=0.5, pinned=False)
        for i in range(2)
    ]
    compiler = PromptCompiler()
    profile = compiler.select_resident_profile([_row("f1", "猫を飼っている")], tool_capable=True)
    total = compiler.token_counter(compiler.compile(_inputs(profile, supplement=supplement, episodic=episodes)))
    seen_states = []
    for budget in range(total, 100, -4):
        report = PromptCompiler(input_budget=budget).compile_with_report(
            _inputs(profile, supplement=supplement, episodic=episodes)
        )
        state = (
            "SUP1" in report.text,
            "SUP2" in report.text,
            "EP0" in report.text,
            "EP1" in report.text,
        )
        seen_states.append(state)
        assert list(report.supplement_fact_ids) == [
            fid for fid, flag in (("s1", state[0]), ("s2", state[1])) if flag
        ]
        if state[2] is False or state[3] is False:
            assert not state[0] and not state[1], "episodic dropped while supplement remained"
        if state[0] is False:
            assert state[1] is False, "higher-ranked supplement item dropped first"
    assert (False, False, True, True) in seen_states


# ---------------------------------------------------------------------------
# compile_for_session: resident + supplement on real rows (40 facts, 2 pinned)
# ---------------------------------------------------------------------------

SUPPLEMENT_QUERY = "紅茶はミルクを入れる派？"
PINNED_TEXTS = ("PIN_A 呼び名はクリスでいい", "PIN_B 誕生日は七月二十五日")
RESIDENT_TEA_TEXT = "R00 紅茶はダージリンが一番"  # resident and query-related
LOW_TEXTS = (
    "L0 紅茶はアールグレイが好き",  # shares 紅茶/茶は -> admitted (2 bigrams)
    "L1 紅茶にミルクは入れない",  # shares 紅茶/ミル/ルク/入れ -> admitted
    "L2 紅茶よりコーヒー",  # shares only 紅茶 -> rejected by the gate
    "L3 犬が少し苦手",
    "L4 朝は六時に起きる",
    "L5 自転車で通学している",
    "L6 数学が得意",
    "L7 夜更かししがち",
)


async def seed_forty(sid: str) -> dict[str, str]:
    """2 pinned + 30 well-observed facts (resident) + 8 weak facts (left out)."""
    ids: dict[str, str] = {}
    for index, text in enumerate(PINNED_TEXTS):
        ids[text] = await seed_fact(
            session_id=sid,
            text=text,
            pinned=True,
            confidence=0.7,
            created_at=f"2026-08-2{index}T00:00:00+00:00",
        )
    for index in range(30):
        text = RESIDENT_TEA_TEXT if index == 0 else f"R{index:02d} 好きな数字は{index}"
        ids[text] = await seed_fact(
            session_id=sid,
            text=text,
            confidence=0.8,
            created_at=f"2026-08-01T00:{index:02d}:00+00:00",
            evidence=[[f"{text}-o1", f"{text}-o2"]],
        )
    for index, text in enumerate(LOW_TEXTS):
        ids[text] = await seed_fact(
            session_id=sid,
            text=text,
            confidence=0.6,
            created_at=f"2026-07-01T00:{index:02d}:00+00:00",
        )
    return ids


def _v11_session(sid: str) -> SimpleNamespace:
    return SimpleNamespace(
        session_id=sid,
        worldline=WORLDLINE,
        identity_mode="self",
        base_system_prompt="You are Amadeus Kurisu.",
        system_prompt="You are Amadeus Kurisu.",
        memory_summary="",
        history=[],
        self_name="",
        identity_acknowledged=False,
    )


class NotLoadedEmbedder(SpyEmbedder):
    """memory_service.embedder stand-in whose E5 model is not loaded."""

    def __init__(self, loaded: bool = False) -> None:
        super().__init__()
        self.loaded = loaded
        self.readiness_calls = 0

    def readiness(self) -> dict:
        self.readiness_calls += 1
        return {"ok": True, "loaded": self.loaded}

    def _load(self):  # pragma: no cover - must never run
        raise AssertionError("prompt compilation must never load E5")


def _deepseek_provider():
    from app.services.provider_runtime import deepseek_service

    return SimpleNamespace(provider_id="deepseek", model_id="deepseek-v4-flash", adapter=deepseek_service)


def _glm_provider():
    return SimpleNamespace(provider_id="glm", model_id="glm-probe", adapter=object())


def _supplement_records(prompt: str) -> list[str]:
    if "\n\nMEMORY CANDIDATES\n" not in prompt:
        return []
    tail = prompt.split("\n\nMEMORY CANDIDATES\n", 1)[1]
    data = tail.split("MEMORY_CANDIDATE_DATA\n", 1)[1].split("\n", 1)[0]
    return list(json.loads(data)["records"].values())


@pytest.mark.asyncio
async def test_forty_facts_profile_and_supplement(isolated_store, monkeypatch):
    """[A1][A2][A5][A6][A7] 40 facts / 2 pinned: 32 resident + gated supplement."""
    from app.services.memory import memory_service

    monkeypatch.setenv("AMADEUS_MEMORY_MODE", "v11")
    spy = NotLoadedEmbedder(loaded=False)
    monkeypatch.setattr(memory_service, "embedder", spy)
    sid = f"rp-forty-{uuid4()}"
    ids = await seed_forty(sid)
    session = _v11_session(sid)

    prompt = await compile_for_session(session, SUPPLEMENT_QUERY, provider=_deepseek_provider())
    lines = _fact_lines(prompt)
    assert len(lines) == 32
    assert lines[:2] == list(PINNED_TEXTS)  # pinned render first
    assert lines[2] == RESIDENT_TEA_TEXT  # then created_at order
    assert not any(line.startswith("L") for line in lines)
    assert _core_section(prompt).split("\n")[1] == PARTIAL_TOOL_RULE
    # Supplement: only gated, non-resident facts; resident tea fact excluded.
    records = _supplement_records(prompt)
    assert sorted(records) == sorted([LOW_TEXTS[0], LOW_TEXTS[1]])
    assert RESIDENT_TEA_TEXT not in records and LOW_TEXTS[2] not in records
    assert prompt.index("\n\nCURRENT STATE\n") < prompt.index("\n\nMEMORY CANDIDATES\n")
    assert "\nMEMORY_CANDIDATE_DATA\n" in prompt
    # E5 not loaded: never encoded, never loaded.
    assert spy.calls == []
    assert spy.readiness_calls == 1
    # Injected ids = final resident + supplement ids, stored on the session.
    resident_ids = {ids[text] for text in lines}
    expected = resident_ids | {ids[LOW_TEXTS[0]], ids[LOW_TEXTS[1]]}
    assert set(session.turn_injected_fact_ids) == expected
    assert len(session.turn_injected_fact_ids) == 34

    # Non-tool provider: same profile, non-tool partial wording.
    glm_prompt = await compile_for_session(session, SUPPLEMENT_QUERY, provider=_glm_provider())
    assert _core_section(glm_prompt).split("\n")[1] == PARTIAL_NO_TOOL_RULE
    assert _fact_lines(glm_prompt) == lines


@pytest.mark.asyncio
async def test_supplement_uses_e5_only_when_already_loaded(isolated_store, monkeypatch):
    """[A5] loaded E5 ranks admitted facts (encode once); gate still applies."""
    from app.services.memory import memory_service

    monkeypatch.setenv("AMADEUS_MEMORY_MODE", "v11")
    spy = NotLoadedEmbedder(loaded=True)
    monkeypatch.setattr(memory_service, "embedder", spy)
    sid = f"rp-e5-{uuid4()}"
    await seed_forty(sid)
    prompt = await compile_for_session(_v11_session(sid), SUPPLEMENT_QUERY, provider=_glm_provider())
    from app.services.prompt_compiler import supplement_need_text

    # Query = normalized user message (NFKC folds the fullwidth "？").
    assert spy.calls == [supplement_need_text(SUPPLEMENT_QUERY)] == ["紅茶はミルクを入れる派?"]
    assert sorted(_supplement_records(prompt)) == sorted([LOW_TEXTS[0], LOW_TEXTS[1]])


@pytest.mark.asyncio
async def test_real_embedding_adapter_is_never_loaded_by_prompt(isolated_store, monkeypatch):
    """[A5] with the real EmbeddingAdapter (not loaded) nothing loads E5."""
    from app.services.memory import EmbeddingAdapter, memory_service

    monkeypatch.setenv("AMADEUS_MEMORY_MODE", "v11")
    adapter = EmbeddingAdapter()
    loads = {"n": 0}
    encodes = {"n": 0}

    def forbidden_load():
        loads["n"] += 1
        raise AssertionError("E5 lazy load during prompt compilation")

    async def forbidden_encode(_text):
        encodes["n"] += 1
        raise AssertionError("encode_query during prompt compilation")

    monkeypatch.setattr(adapter, "_load", forbidden_load)
    monkeypatch.setattr(adapter, "encode_query", forbidden_encode)
    monkeypatch.setattr(memory_service, "embedder", adapter)
    sid = f"rp-real-{uuid4()}"
    await seed_forty(sid)
    prompt = await compile_for_session(_v11_session(sid), SUPPLEMENT_QUERY, provider=_glm_provider())
    assert loads["n"] == 0 and encodes["n"] == 0
    assert adapter._model is None
    assert _supplement_records(prompt)


@pytest.mark.asyncio
async def test_complete_profile_skips_supplement(isolated_store, monkeypatch):
    """[D] every candidate resident -> complete rule, no supplement call."""
    import app.services.memory_v11.recall as recall_module

    monkeypatch.setenv("AMADEUS_MEMORY_MODE", "v11")
    sid = f"rp-complete-{uuid4()}"
    await seed_fact(session_id=sid, text="紅茶はアールグレイが好き")
    calls = {"n": 0}

    async def counting(**kwargs):
        calls["n"] += 1
        raise AssertionError("supplement must not run for a complete profile")

    monkeypatch.setattr(recall_module, "recall_stable_facts", counting)
    session = _v11_session(sid)
    prompt = await compile_for_session(session, SUPPLEMENT_QUERY, provider=_deepseek_provider())
    assert calls["n"] == 0
    assert _core_section(prompt) == f"CORE FACTS\n{FACT_USE_CONTRACT}\n- 紅茶はアールグレイが好き"
    assert "MEMORY CANDIDATES" not in prompt
    assert len(session.turn_injected_fact_ids) == 1


@pytest.mark.asyncio
async def test_v11_no_facts_no_section_and_ids_reset(isolated_store, monkeypatch):
    """[A1] no facts -> no CORE FACTS / (none); ids reset every turn."""
    monkeypatch.setenv("AMADEUS_MEMORY_MODE", "v11")
    sid = f"rp-empty-{uuid4()}"
    session = _v11_session(sid)
    session.turn_injected_fact_ids = ("stale-id",)
    for provider in (_deepseek_provider(), _glm_provider(), None):
        prompt = await compile_for_session(session, "こんにちは", provider=provider)
        assert "CORE FACTS\n" not in prompt
        assert "(none)" not in prompt
        assert "MEMORY CANDIDATES" not in prompt
        assert session.turn_injected_fact_ids == ()
    monkeypatch.setenv("AMADEUS_MEMORY_MODE", "legacy")
    session.turn_injected_fact_ids = ("stale-id",)
    legacy = await compile_for_session(session, "こんにちは")
    assert "CORE FACTS\n(none)" in legacy
    assert session.turn_injected_fact_ids == ()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["raise", "timeout"])
async def test_supplement_failure_omits_block_and_logs_without_content(
    isolated_store, monkeypatch, caplog, failure
):
    """[D] supplement failure: block omitted, resident kept, warning w/o facts."""
    import app.services.memory_v11.recall as recall_module

    monkeypatch.setenv("AMADEUS_MEMORY_MODE", "v11")
    sid = f"rp-fail-{uuid4()}"
    await seed_forty(sid)

    async def broken(**kwargs):
        if failure == "raise":
            raise RuntimeError(f"boom {LOW_TEXTS[0]}")
        return recall_module.RecallResult(status="unavailable", diagnostics={"reason": "timeout"})

    monkeypatch.setattr(recall_module, "recall_stable_facts", broken)
    session = _v11_session(sid)
    with caplog.at_level("WARNING", logger="app.services.prompt_compiler"):
        prompt = await compile_for_session(session, SUPPLEMENT_QUERY, provider=_deepseek_provider())
    assert "MEMORY CANDIDATES" not in prompt
    assert len(_fact_lines(prompt)) == 32
    assert len(session.turn_injected_fact_ids) == 32
    warnings = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
    assert any("memory supplement" in message for message in warnings)
    for record in caplog.records:
        text = record.getMessage() + (str(record.exc_info) if record.exc_info else "")
        assert "紅茶" not in text and "PIN_A" not in text
