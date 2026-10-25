"""Dispatch 55: turn-level wiring of the v11 resident profile.

recall_memory exclusion of injected facts, the injected-facts lookup notice,
processor-level first-round prompts (DeepSeek tool path / GLM non-tool path)
and the real_run CORE FACTS checks. Acceptance map: [A<n>] = dispatch 6.<n>.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

from app.services.memory_v11.recall import (
    INJECTED_FACTS_LOOKUP_UNAVAILABLE_NOTICE,
    NEED_MAX_CODEPOINTS,
    NO_USABLE_FACTS_NOTICE,
    RecallResult,
    unavailable_result,
)
from app.services.turn_tool_orchestrator import (
    TurnExecutionRecord,
    apply_unsupported_combo,
    apply_v11_tool_batch,
    execute_recall,
    prepare_second_round_payload,
    refresh_candidates_for_send,
    snapshot_from_session,
    unsupported_failsoft_history,
)
from tests.test_memory_recall_b5a import _StubEmbedder
from tests.test_memory_v11_resident_profile import (
    LOW_TEXTS,
    PARTIAL_NO_TOOL_RULE,
    PARTIAL_TOOL_RULE,
    PINNED_TEXTS,
    RESIDENT_TEA_TEXT,
    SUPPLEMENT_QUERY,
    _core_section,
    _fact_lines,
    _supplement_records,
    _v11_session,
    isolated_store,  # noqa: F401  (fixture)
    seed_fact,
    seed_forty,
)

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT / "scripts"))
import memory_candidate_real_run as b3  # noqa: E402

PROVIDER = SimpleNamespace(provider_id="deepseek", model_id="m")


def _session(sid: str = "turn-owner", **overrides) -> SimpleNamespace:
    base = dict(
        session_id=sid,
        worldline="steins_gate",
        conversation_id="c1",
        identity_mode="self",
        revision=1,
        content_epoch=1,
        provider_id="deepseek",
        model="m",
        erasure_pending=False,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _record(session, injected: bool) -> TurnExecutionRecord:
    ids = ("fact-a", "fact-b") if injected else ()
    return TurnExecutionRecord(snapshot=snapshot_from_session(session, PROVIDER), injected_fact_ids=ids)


def _recall_call(needs) -> dict:
    return {
        "id": f"c-{uuid4().hex[:8]}",
        "function": {"name": "recall_memory", "arguments": json.dumps({"needs": needs}, ensure_ascii=False)},
    }


def _assert_notice(text: str, injected: bool) -> None:
    if injected:
        assert INJECTED_FACTS_LOOKUP_UNAVAILABLE_NOTICE in text
        assert "No usable stable facts" not in text
    else:
        assert NO_USABLE_FACTS_NOTICE in text
        assert INJECTED_FACTS_LOOKUP_UNAVAILABLE_NOTICE not in text


def _stub_embedder_not_loaded():
    embedder = _StubEmbedder()
    embedder.readiness = lambda: {"ok": True, "loaded": False}  # type: ignore[method-assign]
    return embedder


# ---------------------------------------------------------------------------
# [A8] every orchestrator notice path: injected wording vs unchanged wording
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("injected", [True, False])
def test_unsupported_combo_notice_follows_injected_ids(injected):
    """[A8] chat_ws ~1804 path: apply_unsupported_combo + fail-soft history."""
    record = _record(_session(), injected)
    assert record.facts_injected is injected
    notice = apply_unsupported_combo(record, reason="undeclared_combo")
    assert notice.status == "unsupported"
    assert notice.facts_injected is injected
    assert record.recall_result is notice
    _assert_notice(notice.block, injected)
    history = unsupported_failsoft_history([{"role": "user", "content": "x"}], notice)
    _assert_notice(json.dumps(history, ensure_ascii=False), injected)


@pytest.mark.asyncio
@pytest.mark.parametrize("injected", [True, False])
@pytest.mark.parametrize("case", ["cancelled", "erasure", "snapshot_changed", "invalid_json", "too_many", "no_needs"])
async def test_execute_recall_notices_follow_injected_ids(injected, case):
    """[A8] unavailable / invalid notices from execute_recall."""
    session = _session()
    record = _record(session, injected)
    calls = [_recall_call(["pet"])]
    cancelled = False
    if case == "cancelled":
        cancelled = True
    elif case == "erasure":
        session.erasure_pending = True
    elif case == "snapshot_changed":
        session.revision = 2
    elif case == "invalid_json":
        calls = [{"id": "bad", "function": {"name": "recall_memory", "arguments": "{"}}]
    elif case == "too_many":
        calls = [_recall_call([f"n{i}"]) for i in range(20)]
    elif case == "no_needs":
        calls = [{"id": "w", "function": {"name": "web_search", "arguments": "{}"}}]
    result = await execute_recall(record=record, session=session, provider=PROVIDER, calls=calls, cancelled=cancelled)
    assert result.status in {"unavailable", "invalid"}
    assert result.facts_injected is injected
    assert record.recall_result is result
    _assert_notice(result.block, injected)


@pytest.mark.asyncio
@pytest.mark.parametrize("injected", [True, False])
async def test_recall_failure_result_is_rebound_to_injected_notice(injected, monkeypatch):
    """[A8] recall timeout/error results (built inside recall.py) are re-rendered."""

    async def timing_out(**kwargs):
        return RecallResult(
            status="unavailable",
            block=unavailable_result("timeout").block,
            diagnostics={"reason": "timeout"},
        )

    monkeypatch.setattr("app.services.turn_tool_orchestrator.recall_stable_facts", timing_out)
    session = _session()
    record = _record(session, injected)
    result = await execute_recall(record=record, session=session, provider=PROVIDER, calls=[_recall_call(["pet"])])
    assert result.status == "unavailable"
    _assert_notice(result.block, injected)
    # The retry refresh path keeps the same wording.
    session.erasure_pending = True
    refreshed = await refresh_candidates_for_send(record, session, PROVIDER)
    _assert_notice(refreshed.block, injected)


@pytest.mark.asyncio
@pytest.mark.parametrize("injected", [True, False])
async def test_apply_v11_tool_batch_invalid_notice(injected):
    """[A8] over-limit tool batch -> invalid notice, no web/db side effects."""
    session = _session()
    record = _record(session, injected)
    web_hits = {"n": 0}

    async def web(_name, _args):
        web_hits["n"] += 1
        return "web"

    outcome = await apply_v11_tool_batch(
        record=record,
        session=session,
        provider=PROVIDER,
        calls=[_recall_call([f"n{i}"]) for i in range(20)],
        web_executor=web,
    )
    assert outcome.status == "invalid"
    assert web_hits["n"] == 0
    _assert_notice(outcome.recall_result.block, injected)


@pytest.mark.parametrize("injected", [True, False])
def test_second_round_unsupported_and_input_budget_notices(injected):
    """[A8] prepare_second_round_payload: compatible=False and input_budget."""
    ok = RecallResult(
        status="ok",
        records={"m1": "猫を飼っている"},
        block='MEMORY CANDIDATES\nMEMORY_CANDIDATE_DATA\n{"records":{"m1":"猫を飼っている"}}',
        facts_injected=injected,
    )
    history = [{"role": "user", "content": "ペットは？"}]
    unsupported = prepare_second_round_payload(history, ok, compatible=False)
    assert unsupported.status == "unsupported"
    _assert_notice(json.dumps(unsupported.history, ensure_ascii=False), injected)
    over = prepare_second_round_payload(history, ok, input_budget=1, system_prompt="PERSONA")
    assert over.status == "unavailable" and over.reason == "input_budget"
    _assert_notice(json.dumps(over.history, ensure_ascii=False), injected)
    assert over.result.facts_injected is injected


def test_ok_results_never_carry_a_notice():
    """[A8] the notice only exists on notice statuses; ok blocks unchanged."""
    record = _record(_session(), True)
    from app.services.turn_tool_orchestrator import bind_facts_injected

    ok = RecallResult(status="ok", records={"m1": "x"}, block="MEMORY CANDIDATES\nMEMORY_CANDIDATE_DATA\n{}")
    bound = bind_facts_injected(ok, record.facts_injected)
    assert bound.block == "MEMORY CANDIDATES\nMEMORY_CANDIDATE_DATA\n{}"
    assert bound.facts_injected is True


# ---------------------------------------------------------------------------
# [A6] recall_memory excludes this turn's injected facts
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_recall_excludes_injected_fact_ids(isolated_store, monkeypatch):  # noqa: F811
    """[A6] record.injected_fact_ids reach recall_stable_facts and are excluded."""
    from app.services.memory import memory_service

    monkeypatch.setenv("AMADEUS_MEMORY_MODE", "v11")
    monkeypatch.setattr(memory_service, "embedder", _stub_embedder_not_loaded())
    sid = f"turn-excl-{uuid4()}"
    injected = await seed_fact(session_id=sid, text="紅茶はダージリンが一番")
    other = await seed_fact(session_id=sid, text="紅茶よりコーヒー")
    session = _session(sid)
    seen: list[tuple[str, ...]] = []
    import app.services.turn_tool_orchestrator as orchestrator

    original = orchestrator.recall_stable_facts

    async def spying(**kwargs):
        seen.append(tuple(kwargs.get("exclude_fact_ids") or ()))
        return await original(**kwargs)

    monkeypatch.setattr(orchestrator, "recall_stable_facts", spying)

    plain = TurnExecutionRecord(snapshot=snapshot_from_session(session, PROVIDER))
    base = await execute_recall(record=plain, session=session, provider=PROVIDER, calls=[_recall_call(["紅茶"])])
    assert {item["fact_id"] for item in base.ranked_items} == {injected, other}

    record = TurnExecutionRecord(
        snapshot=snapshot_from_session(session, PROVIDER), injected_fact_ids=(injected,)
    )
    result = await execute_recall(record=record, session=session, provider=PROVIDER, calls=[_recall_call(["紅茶"])])
    assert seen == [(), (injected,)]
    assert {item["fact_id"] for item in result.ranked_items} == {other}
    assert list(result.records.values()) == ["紅茶よりコーヒー"]
    assert result.status in {"ok", "degraded"}  # no stored vectors -> degraded lexical


def test_supplement_need_text_is_cut_to_need_max_codepoints():
    """[D] supplement query = this turn's message, cut like NEED_MAX_CODEPOINTS."""
    from app.services.prompt_compiler import supplement_need_text

    assert NEED_MAX_CODEPOINTS == 160
    long_text = "紅茶" * 200
    assert supplement_need_text(long_text) == long_text[:160]
    assert supplement_need_text("  ") == ""


# ---------------------------------------------------------------------------
# Processor level: real first-round prompts on both provider paths
# ---------------------------------------------------------------------------


def _patch_turn_side_effects(monkeypatch, translate_targets) -> None:
    from app.services.conversations import conversation_service

    async def fake_zh(text, api_key=None, model=None):
        return "中文"

    for target in translate_targets:
        monkeypatch.setattr(target, "translate_to_zh", fake_zh)

    def fake_title(*args, **kwargs):
        async def _noop():
            return None

        return asyncio.get_running_loop().create_task(_noop())

    monkeypatch.setattr(conversation_service, "schedule_auto_title", fake_title)


async def _processor_session(owner: str, *, provider_id: str, model: str):
    from app.routers.chat_ws import SessionState
    from app.services.conversations import conversation_service

    session = SessionState(owner)
    session.worldline = "steins_gate"
    session.identity_mode = "self"
    session.enable_tts = False
    session.api_key = "offline"
    session.provider_id = provider_id
    session.model = model
    selected = await conversation_service.create_and_select(
        owner, "steins_gate", provider_id="deepseek", model_id="deepseek-v4-flash", identity_mode="self"
    )
    session.conversation_id = str(selected["id"])
    session.identity_mode = "self"
    session.history = []
    return session


def _candidate_records(text: str) -> list[str]:
    return b3._candidate_records_in(text)


@pytest.mark.asyncio
async def test_processor_deepseek_first_round_profile_and_recall_exclusion(isolated_store, monkeypatch):  # noqa: F811
    """[A1][A6] DeepSeek: CORE FACTS + supplement in round 1; recall excludes them."""
    from app.routers.chat_ws import processor_loop
    from app.services.memory import memory_service
    from app.services.provider_runtime import deepseek_service

    monkeypatch.setenv("AMADEUS_MEMORY_MODE", "v11")
    monkeypatch.setattr(memory_service, "embedder", _stub_embedder_not_loaded())
    owner = f"turn-ds-{uuid4()}"
    ids = await seed_forty(owner)
    calls: list[dict] = []

    async def fake_stream(active_history=None, memory_summary=None, api_key=None, system_prompt=None,
                          temperature=0.7, tools=None, tool_choice=None, model=None,
                          reasoning_effort=None, isolated=False):
        calls.append({"tools": tools, "system_prompt": system_prompt, "history": active_history})
        if tools:
            yield {
                "content": None,
                "tool_calls": [{
                    "index": 0,
                    "id": "call-recall",
                    "type": "function",
                    "function": {"name": "recall_memory", "arguments": json.dumps({"needs": ["紅茶"]})},
                }],
            }
            return
        yield {"content": "[EMO:neutral] ミルクは入れないのよ。", "tool_calls": None}

    monkeypatch.setattr(deepseek_service, "get_chat_stream", fake_stream)
    _patch_turn_side_effects(monkeypatch, [deepseek_service])
    session = await _processor_session(owner, provider_id="deepseek", model="deepseek-v4-flash")
    session.append_message({"role": "user", "content": SUPPLEMENT_QUERY, "id": None})
    queue: asyncio.Queue = asyncio.Queue()
    await processor_loop(session, SUPPLEMENT_QUERY, queue, session.current_epoch, session.history_epoch)

    assert len(calls) == 2 and calls[0]["tools"] and calls[1]["tools"] is None
    first = calls[0]["system_prompt"] or ""
    lines = _fact_lines(first)
    assert len(lines) == 32 and lines[:2] == list(PINNED_TEXTS) and RESIDENT_TEA_TEXT in lines
    assert _core_section(first).split("\n")[1] == PARTIAL_TOOL_RULE
    assert sorted(_supplement_records(first)) == sorted(LOW_TEXTS[:2])
    assert "(none)" not in first
    record = session.turn_execution
    injected_texts = set(lines) | set(LOW_TEXTS[:2])
    assert set(record.injected_fact_ids) == {ids[text] for text in injected_texts}
    assert len(record.injected_fact_ids) == 34
    second_blob = "\n".join(str(row.get("content") or "") for row in calls[1]["history"])
    second_records = _candidate_records(second_blob.split("\n\nCURRENT STATE\n")[-1])
    assert second_records, second_blob[-800:]
    assert not (set(second_records) & injected_texts)
    assert LOW_TEXTS[2] in second_records  # the one non-injected tea fact
    assert "No usable stable facts" not in second_blob


class _CaptureTransport:
    def __init__(self) -> None:
        self.requests: list[dict] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8")) if request.content else {}
        self.requests.append(body)
        text = (
            "data: "
            + json.dumps({"choices": [{"delta": {"content": "[EMO:neutral] 了解したわ。"}}]}, ensure_ascii=False)
            + "\n\ndata: [DONE]\n\n"
        )
        return httpx.Response(200, text=text, headers={"content-type": "text/event-stream"})


@pytest.mark.asyncio
@pytest.mark.parametrize("with_facts", [True, False])
async def test_processor_glm_non_tool_profile_and_notice(isolated_store, monkeypatch, with_facts):  # noqa: F811
    """[A1][A2][A8] GLM (no tools): CORE FACTS in round 1; injected notice only with facts."""
    from app.routers.chat_ws import processor_loop
    from app.services.memory import memory_service
    from app.services.provider_adapters import OpenAICompatibleAdapter
    from app.services.provider_registry import ProviderCapabilities, ProviderSnapshot, ProviderTask
    from app.services.provider_runtime import deepseek_service

    monkeypatch.setenv("AMADEUS_MEMORY_MODE", "v11")
    monkeypatch.setattr(memory_service, "embedder", _stub_embedder_not_loaded())
    owner = f"turn-glm-{uuid4()}"
    if with_facts:
        await seed_forty(owner)
    capture = _CaptureTransport()
    adapter = OpenAICompatibleAdapter(
        provider_id="glm",
        base_url="https://probe.invalid/v1",
        default_model="glm-probe",
        message_builder=deepseek_service.build_persona_messages,
        transport=httpx.MockTransport(capture.handler),
        credential_required=False,
    )
    snapshot = ProviderSnapshot(
        provider_id="glm",
        model_id="glm-probe",
        capabilities=ProviderCapabilities(tasks=frozenset({ProviderTask.CHAT, ProviderTask.TRANSLATION})),
        adapter=adapter,
        credential_required=False,
    )
    monkeypatch.setattr("app.routers.chat_ws.provider_registry.snapshot", lambda *_a, **_k: snapshot)
    _patch_turn_side_effects(monkeypatch, [adapter, deepseek_service])
    session = await _processor_session(owner, provider_id="glm", model="glm-probe")
    session.append_message({"role": "user", "content": SUPPLEMENT_QUERY, "id": None})
    queue: asyncio.Queue = asyncio.Queue()
    await processor_loop(session, SUPPLEMENT_QUERY, queue, session.current_epoch, session.history_epoch)

    record = session.turn_execution
    assert record.tool_plan_status == "unsupported"
    assert capture.requests
    request = capture.requests[0]
    assert not request.get("tools")
    system = request["messages"][0]["content"]
    blob = json.dumps(request, ensure_ascii=False)
    if with_facts:
        assert len(_fact_lines(system)) == 32
        assert _core_section(system).split("\n")[1] == PARTIAL_NO_TOOL_RULE
        assert sorted(_supplement_records(system)) == sorted(LOW_TEXTS[:2])
        assert len(record.injected_fact_ids) == 34
        assert record.recall_result.facts_injected is True
        assert INJECTED_FACTS_LOOKUP_UNAVAILABLE_NOTICE in blob
        assert "No usable stable facts" not in blob
    else:
        assert "CORE FACTS\n" not in system and "(none)" not in system
        assert record.injected_fact_ids == ()
        assert NO_USABLE_FACTS_NOTICE in blob
        assert INJECTED_FACTS_LOOKUP_UNAVAILABLE_NOTICE not in blob


# ---------------------------------------------------------------------------
# [A9] memory_candidate_real_run.py CORE FACTS checks on real compiled prompts
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_real_run_core_fact_checks_on_compiled_prompts(isolated_store, monkeypatch):  # noqa: F811
    """[A9] heading / (none) / heading-without-facts / surfaced fact texts."""
    from app.services.memory import memory_service
    from app.services.prompt_compiler import compile_for_session
    from tests.test_memory_v11_resident_profile import _deepseek_provider

    monkeypatch.setattr(memory_service, "embedder", _stub_embedder_not_loaded())
    monkeypatch.setenv("AMADEUS_MEMORY_MODE", "v11")
    sid = f"turn-b3-{uuid4()}"
    await seed_forty(sid)
    v11_prompt = await compile_for_session(_v11_session(sid), SUPPLEMENT_QUERY, provider=_deepseek_provider())
    second_round = {
        "role": "system",
        "content": 'MEMORY CANDIDATES\nrules\nMEMORY_CANDIDATE_DATA\n{"status":"ok","needs":[],"records":{"m1":"SECOND_ROUND_FACT"}}',
    }
    messages = [{"role": "system", "content": v11_prompt}, {"role": "user", "content": SUPPLEMENT_QUERY}, second_round]
    assert b3._system_has_core_facts_heading(messages) is True
    assert b3._system_has_core_facts_none(messages) is False
    assert b3._system_core_facts_heading_without_facts(messages) is False
    surfaced = b3._core_facts_from_messages(messages).split("\n")
    assert surfaced[:32] == _fact_lines(v11_prompt)
    assert sorted(surfaced[32:34]) == sorted(LOW_TEXTS[:2])  # supplement
    assert surfaced[34:] == ["SECOND_ROUND_FACT"]

    empty_prompt = await compile_for_session(_v11_session(f"turn-b3-empty-{uuid4()}"), "hi", provider=_deepseek_provider())
    empty = [{"role": "system", "content": empty_prompt}]
    assert b3._system_has_core_facts_heading(empty) is False
    assert b3._system_has_core_facts_none(empty) is False
    assert b3._system_core_facts_heading_without_facts(empty) is False
    assert b3._core_facts_from_messages(empty) == ""

    monkeypatch.setenv("AMADEUS_MEMORY_MODE", "legacy")
    legacy_prompt = await compile_for_session(_v11_session(f"turn-b3-legacy-{uuid4()}"), "hi")
    legacy = [{"role": "system", "content": legacy_prompt}]
    assert b3._system_has_core_facts_none(legacy) is True
    assert b3._system_core_facts_heading_without_facts(legacy) is True


def test_real_run_reads_only_the_requests_own_core_facts_section():
    """[A9] an earlier prompt embedded in IDENTITY does not leak into the check."""
    old = "IDENTITY\nCORE FACTS\nrule\n- OLD_FACT\n\nCURRENT STATE\nold"
    new_prompt = f"{old}\n\nPERSONA\np\n\nCORE FACTS\nrule\n- NEW_FACT\n\nCURRENT STATE\nnow"
    messages = [{"role": "system", "content": new_prompt}]
    assert b3._resident_core_fact_lines(new_prompt) == ["NEW_FACT"]
    assert b3._core_facts_from_messages(messages) == "NEW_FACT"
    headed_empty = [{"role": "system", "content": "PERSONA\np\n\nCORE FACTS\nrule\n\nCURRENT STATE\nnow"}]
    assert b3._system_core_facts_heading_without_facts(headed_empty) is True
