"""B5B Memory read-gate offline slice: first-round Provider envelope evidence.

Every required check captures a real Provider request through
``get_clean_text_stream`` or ``processor_loop`` with a fake httpx transport.
Scripted fake tool calls prove the envelope and the product control flow only; they
are NOT evidence that the model decides correctly. No provider, network, web, TTS,
credential or paid question set is touched.
"""

from __future__ import annotations

import asyncio
import copy
import json
import os
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

from app.agent_tools import (
    AMADEUS_TOOLS,
    MEMORY_RECALL_DECISION_RULE,
    RECALL_MEMORY_TOOL,
    RECALL_MEMORY_TOOL_NAME,
    tools_include_recall_memory,
)

BACKEND = Path(__file__).resolve().parents[1]
FROZEN_QUESTION_SET = BACKEND / "tests" / "fixtures" / "memory_recall_b5b_question_set.json"

# Each item is independently required. A group is a set of alternative marker
# tuples; the item passes only if at least one alternative has every marker.
REQUIRED_SEMANTIC_ITEMS: dict[str, tuple[tuple[str, ...], ...]] = {
    "stable_personal_information": (("stable personal information",),),
    "earlier_conversations": (("earlier conversation",),),
    "not_reliably_in_active_conversation": (
        ("not reliably present in the active conversation",),
        ("not reliably available in the active conversation",),
    ),
    "read_before_guessing": (("before guessing",),),
    "read_before_do_not_know": (("do not know",),),
    "read_before_no_record": (("no record",),),
    "read_before_cannot_tell": (("cannot tell",),),
    "one_to_three_needs": (("one to three",),),
    "atomic_needs": (("atomic",),),
    "exclude_general_knowledge": (("general knowledge",),),
    "exclude_current_public_information": (("current public information",),),
    "exclude_operational_instructions": (("operational instruction",),),
    "exclude_quoted_translation": (
        ("translation of quoted text",),
        ("quoted-text translation",),
    ),
    "exclude_already_in_conversation": (("already reliably available",),),
}


def _all_system_contents(request: dict) -> list[str]:
    return [
        str(message.get("content") or "")
        for message in request.get("messages") or []
        if message.get("role") == "system"
    ]


def _system_content(request: dict) -> str:
    return "\n".join(_all_system_contents(request))


def _rule_copies(text: str) -> int:
    return (text or "").count(MEMORY_RECALL_DECISION_RULE.strip())


def _rule_copies_in_request(request: dict) -> int:
    return sum(_rule_copies(content) for content in _all_system_contents(request))


def _recall_tool_schema(request: dict) -> dict:
    for tool in request.get("tools") or []:
        function = tool.get("function") if isinstance(tool, dict) else None
        if isinstance(function, dict) and function.get("name") == RECALL_MEMORY_TOOL_NAME:
            return tool
    return {}


def _missing_semantics(text: str) -> list[str]:
    lowered = (text or "").lower()
    missing: list[str] = []
    for name, alternatives in REQUIRED_SEMANTIC_ITEMS.items():
        if not any(all(marker in lowered for marker in option) for option in alternatives):
            missing.append(name)
    return missing


def _schema_violations(tool: dict) -> list[str]:
    violations: list[str] = []
    function = tool.get("function") if isinstance(tool, dict) else None
    if not isinstance(function, dict) or function.get("name") != RECALL_MEMORY_TOOL_NAME:
        return ["schema_tool_missing"]
    parameters = function.get("parameters") or {}
    if parameters.get("type") != "object":
        violations.append("schema_type_changed")
    if parameters.get("additionalProperties") is not False:
        violations.append("schema_additional_properties_changed")
    properties = parameters.get("properties") or {}
    if set(properties) != {"needs"}:
        violations.append("schema_properties_changed")
    if list(parameters.get("required") or []) != ["needs"]:
        violations.append("schema_required_changed")
    needs = properties.get("needs") or {}
    if needs.get("type") != "array":
        violations.append("schema_needs_type_changed")
    if needs.get("minItems") != 1:
        violations.append("schema_min_items_changed")
    if needs.get("maxItems") != 3:
        violations.append("schema_max_items_changed")
    items = needs.get("items") or {}
    if items.get("type") != "string":
        violations.append("schema_item_type_changed")
    if items.get("minLength") != 1:
        violations.append("schema_min_length_changed")
    if items.get("maxLength") != 160:
        violations.append("schema_max_length_changed")
    return violations


def _needs_accepted(tool: dict, needs) -> bool:
    """Evaluate a needs value against the bounds declared in the live schema."""

    parameters = ((tool.get("function") or {}).get("parameters")) or {}
    needs_schema = ((parameters.get("properties") or {}).get("needs")) or {}
    items = needs_schema.get("items") or {}
    if not isinstance(needs, list):
        return False
    minimum = needs_schema.get("minItems")
    maximum = needs_schema.get("maxItems")
    item_min = items.get("minLength")
    item_max = items.get("maxLength")
    if minimum is not None and len(needs) < minimum:
        return False
    if maximum is not None and len(needs) > maximum:
        return False
    for item in needs:
        if not isinstance(item, str):
            return False
        if item_min is not None and len(item) < item_min:
            return False
        if item_max is not None and len(item) > item_max:
            return False
    return True


def _envelope_violations(request: dict, *, expect_read_gate: bool) -> list[str]:
    """Shared validator for every positive and negative envelope check."""

    violations: list[str] = []
    copies = _rule_copies_in_request(request)
    joined = _system_content(request)
    if expect_read_gate:
        if copies == 0:
            violations.append("read_gate_missing")
        elif copies > 1:
            violations.append("read_gate_duplicated")
        if not tools_include_recall_memory(request.get("tools")):
            violations.append("recall_memory_tool_absent")
        if request.get("tool_choice") != "auto":
            violations.append("tool_choice_not_auto")
        schema = _recall_tool_schema(request)
        description = str(((schema.get("function") or {}).get("description")) or "")
        violations.extend(
            f"tool_description_missing:{name}" for name in _missing_semantics(description)
        )
        violations.extend(f"system_rule_missing:{name}" for name in _missing_semantics(joined))
        violations.extend(_schema_violations(schema))
    elif copies:
        violations.append("read_gate_leaked")
    return violations


def _is_distinctive_literal(text: str) -> bool:
    """Frozen literals that cannot appear in product code for unrelated reasons.

    Bare ASCII words such as "active" or "pet" are deliberately excluded: they occur
    in ordinary product code and could not distinguish a leak.
    """

    if len(text) < 3:
        return False
    if any(ord(char) > 0x2000 for char in text):
        return True
    if "_" in text or "/" in text:
        return True
    return len(text.split()) >= 2


def _frozen_literals() -> list[str]:
    question_set = json.loads(FROZEN_QUESTION_SET.read_text(encoding="utf-8"))
    raw: list[str] = []
    for case in question_set.get("cases") or []:
        expected = case.get("expected") or {}
        raw.append(str(case.get("id") or ""))
        raw.append(str(case.get("query") or ""))
        for key in ("must_use_fact_keys", "must_not_use_fact_keys", "must_express_unknown_for"):
            raw.extend(str(value) for value in expected.get(key) or [])
    for entry in (question_set.get("facts") or []) + (question_set.get("scope_sentinels") or []):
        raw.append(str(entry.get("key") or ""))
        raw.append(str(entry.get("display_text") or ""))
        raw.extend(str(value) for value in entry.get("aliases") or [])
    return sorted({value for value in raw if _is_distinctive_literal(value)})


@pytest.fixture
async def isolated_store(tmp_path, monkeypatch):
    from app.db import init_db, reset_initialization_cache

    monkeypatch.delenv("AMADEUS_DB_PATH", raising=False)
    monkeypatch.setenv("AMADEUS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("AMADEUS_MEMORY_MODE", "v11")
    reset_initialization_cache()
    await init_db()
    return tmp_path


def _session(**overrides):
    base = dict(
        session_id="b5b-owner",
        worldline="steins_gate",
        conversation_id="b5b-conversation",
        identity_mode="self",
        revision=1,
        content_epoch=1,
        provider_id="deepseek",
        model="probe-model",
        erasure_pending=False,
        client_type="desktop",
        system_prompt="PERSONA PROBE",
        temperature=0.7,
        api_key="offline",
        reasoning_effort=None,
        active_streams=set(),
        protocol_version=1,
        last_assistant_emotion="neutral",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _provider(**overrides):
    base = dict(
        provider_id="deepseek",
        model_id="probe-model",
        temperature=0.7,
        reasoning_effort=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


class _StubEmbedder:
    model_name = "test-deterministic-384"
    dimensions = 384

    async def encode_passage(self, _text: str):
        return [0.01] * 384

    async def encode_query(self, _text: str):
        return [0.01] * 384


def _sse_text(text: str) -> str:
    return (
        "data: "
        + json.dumps({"choices": [{"delta": {"content": text}}]}, ensure_ascii=False)
        + "\n\ndata: [DONE]\n\n"
    )


def _sse_tool_events(*events: list[dict]) -> str:
    chunks = []
    for calls in events:
        chunks.append(
            "data: "
            + json.dumps({"choices": [{"delta": {"tool_calls": calls}}]}, ensure_ascii=False)
            + "\n\n"
        )
    chunks.append("data: [DONE]\n\n")
    return "".join(chunks)


def _frag(index, *, call_id=None, name=None, arguments=""):
    payload = {
        "index": index,
        "type": "function",
        "function": {"arguments": arguments},
    }
    if call_id is not None:
        payload["id"] = call_id
    if name is not None:
        payload["function"]["name"] = name
    return payload


class _CaptureTransport:
    """Fake httpx transport that records every Provider request body."""

    def __init__(self, responses: list[str]):
        self.responses = list(responses)
        self.requests: list[dict] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8")) if request.content else {}
        self.requests.append(body)
        text = self.responses.pop(0) if self.responses else _sse_text("[EMO:neutral] 了解したわ。")
        return httpx.Response(200, text=text, headers={"content-type": "text/event-stream"})


def _openai_adapter(capture: _CaptureTransport, *, provider_id="deepseek", model="probe-model"):
    from app.services.provider_adapters import OpenAICompatibleAdapter
    from app.services.provider_runtime import deepseek_service

    return OpenAICompatibleAdapter(
        provider_id=provider_id,
        base_url="https://probe.invalid/v1",
        default_model=model,
        message_builder=deepseek_service.build_persona_messages,
        transport=httpx.MockTransport(capture.handler),
        credential_required=False,
    )


async def _through_stream(adapter, session=None, *, tools=None, history=None):
    """Product first/second-round boundary: the real get_clean_text_stream call."""

    from app.routers.chat_ws import get_clean_text_stream
    from app.services.turn_events import TurnModeState

    return await get_clean_text_stream(
        active_history=history or [{"role": "user", "content": "probe"}],
        memory_summary="",
        session=session or _session(),
        tools=tools,
        tool_choice="auto" if tools else None,
        provider_snapshot=_provider(adapter=adapter),
        mode_state=TurnModeState(),
    )


async def _capture_first_round_request() -> dict:
    from app.services.turn_tool_orchestrator import plan_first_round_tools

    plan = plan_first_round_tools(
        v11=True,
        force_search=False,
        provider_id="deepseek",
        adapter_kind="OpenAICompatibleAdapter",
    )
    assert plan.status == "ok"
    capture = _CaptureTransport([_sse_text("[EMO:neutral] 了解したわ。")])
    await _through_stream(_openai_adapter(capture), session=_session(), tools=plan.tools)
    assert capture.requests
    return capture.requests[0]


async def _capture_web_only_request() -> dict:
    capture = _CaptureTransport([_sse_text("[EMO:neutral] 了解したわ。")])
    await _through_stream(_openai_adapter(capture), session=_session(), tools=list(AMADEUS_TOOLS))
    assert capture.requests
    return capture.requests[0]


def _plan_first_round_tools():
    from app.services.turn_tool_orchestrator import plan_first_round_tools

    return plan_first_round_tools(
        v11=True,
        force_search=False,
        provider_id="deepseek",
        adapter_kind="OpenAICompatibleAdapter",
    )


@pytest.mark.asyncio
async def test_first_round_recall_memory_envelope_carries_one_read_gate_rule():
    plan = _plan_first_round_tools()
    assert plan.status == "ok"
    assert tools_include_recall_memory(plan.tools)
    capture = _CaptureTransport([_sse_text("[EMO:neutral] 了解したわ。")])
    session = _session()
    await _through_stream(_openai_adapter(capture), session=session, tools=plan.tools)

    assert capture.requests
    request = capture.requests[0]
    assert _envelope_violations(request, expect_read_gate=True) == []
    assert _rule_copies_in_request(request) == 1
    assert tools_include_recall_memory(request.get("tools"))
    assert request.get("tool_choice") == "auto"
    assert _missing_semantics(_recall_tool_schema(request)["function"]["description"]) == []
    # Never persisted: neither the session prompt nor any non-system message.
    assert session.system_prompt == "PERSONA PROBE"
    assert MEMORY_RECALL_DECISION_RULE not in session.system_prompt
    assert not any(
        MEMORY_RECALL_DECISION_RULE in str(message.get("content") or "")
        for message in request["messages"]
        if message.get("role") != "system"
    )
    assert _missing_semantics(_system_content(request)) == []
    capture_dir = os.environ.get("B5B_CAPTURE_DIR")
    if capture_dir:
        Path(capture_dir).mkdir(parents=True, exist_ok=True)
        (Path(capture_dir) / "product-envelope.json").write_text(
            json.dumps(request, ensure_ascii=False, indent=2), encoding="utf-8"
        )


@pytest.mark.asyncio
async def test_language_leak_retry_keeps_one_rule_copy_per_request():
    capture = _CaptureTransport(
        [
            _sse_text("私はAIアシスタントです。"),
            _sse_text("[EMO:neutral] 了解したわ。"),
        ]
    )
    session = _session()
    await _through_stream(_openai_adapter(capture), session=session, tools=[RECALL_MEMORY_TOOL])

    assert len(capture.requests) >= 2, "leak retry must issue a second first-round request"
    for index, request in enumerate(capture.requests):
        assert _envelope_violations(request, expect_read_gate=True) == [], index
        assert _rule_copies_in_request(request) == 1, index
    assert session.system_prompt == "PERSONA PROBE"


@pytest.mark.asyncio
async def test_legacy_web_only_request_never_carries_read_gate_rule():
    from app.services.turn_tool_orchestrator import plan_first_round_tools

    legacy_plan = plan_first_round_tools(
        v11=False,
        force_search=False,
        provider_id="deepseek",
        adapter_kind="OpenAICompatibleAdapter",
    )
    assert legacy_plan.status == "ok"
    assert legacy_plan.tools is None
    request = await _capture_web_only_request()
    assert _envelope_violations(request, expect_read_gate=False) == []
    assert _rule_copies_in_request(request) == 0
    assert not tools_include_recall_memory(request.get("tools"))
    assert [tool["function"]["name"] for tool in request["tools"]] == ["web_search"]
    capture_dir = os.environ.get("B5B_CAPTURE_DIR")
    if capture_dir:
        Path(capture_dir).mkdir(parents=True, exist_ok=True)
        (Path(capture_dir) / "web-only-envelope.json").write_text(
            json.dumps(request, ensure_ascii=False, indent=2), encoding="utf-8"
        )


async def _drive_processor_loop(
    monkeypatch, *, provider_id: str, responses: list[str], recalled_fact: str | None = None
):
    """Drive the real processor_loop with a fake provider; count memory/web work."""

    import app.services.memory_v11.recall as recall_mod
    from app.routers.chat_ws import SessionState, processor_loop
    from app.services.conversations import conversation_service
    from app.services.memory import memory_service
    from app.services.memory_v11.repository import create_stable_fact
    from app.services.provider_registry import (
        ProviderCapabilities,
        ProviderSnapshot,
        ProviderTask,
    )
    from app.services.provider_runtime import deepseek_service

    owner = f"b5b-{uuid4()}"
    counters = {"memory_loads": 0, "web_calls": 0}
    monkeypatch.setenv("AMADEUS_MEMORY_MODE", "v11")
    monkeypatch.setattr(memory_service, "embedder", _StubEmbedder())
    if recalled_fact is not None:
        await create_stable_fact(
            session_id=owner,
            worldline="steins_gate",
            identity_mode="self",
            display_text=recalled_fact,
            semantic_json={"subject": "user", "predicate": "note", "object": "probe"},
        )
    original_load = recall_mod._load_scoped_facts

    async def counted_load(scope):
        counters["memory_loads"] += 1
        return await original_load(scope)

    async def counted_web(_name, _args):
        counters["web_calls"] += 1
        return "web"

    monkeypatch.setattr(recall_mod, "_load_scoped_facts", counted_load)
    monkeypatch.setattr("app.routers.chat_ws.execute_tool_call", counted_web)

    capture = _CaptureTransport(list(responses))
    adapter = _openai_adapter(capture, provider_id=provider_id, model=f"{provider_id}-probe")
    if provider_id == "deepseek":
        import app.services.deepseek as deepseek_mod

        original_client = httpx.AsyncClient

        def _client_factory(*args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(capture.handler)
            return original_client(*args, **kwargs)

        monkeypatch.setattr(deepseek_mod.httpx, "AsyncClient", _client_factory)

    async def _zh(*_a, **_k):
        return "中文"

    monkeypatch.setattr(adapter, "translate_to_zh", _zh)
    monkeypatch.setattr(deepseek_service, "translate_to_zh", _zh)
    snapshot = ProviderSnapshot(
        provider_id=provider_id,
        model_id=f"{provider_id}-probe",
        capabilities=ProviderCapabilities(
            tasks=frozenset({ProviderTask.CHAT, ProviderTask.TRANSLATION})
        ),
        adapter=adapter,
        credential_required=False,
    )
    monkeypatch.setattr(
        "app.routers.chat_ws.provider_registry.snapshot", lambda *_a, **_k: snapshot
    )

    def fake_title(*_args, **_kwargs):
        async def _noop():
            return None

        return asyncio.get_running_loop().create_task(_noop())

    monkeypatch.setattr(conversation_service, "schedule_auto_title", fake_title)

    session = SessionState(owner)
    session.worldline = "steins_gate"
    session.enable_tts = False
    session.api_key = "offline"
    session.provider_id = provider_id
    session.model = f"{provider_id}-probe"
    session.identity_mode = "self"
    selected = await conversation_service.create_and_select(
        owner,
        "steins_gate",
        provider_id="deepseek",
        model_id="deepseek-v4-flash",
        identity_mode="self",
    )
    session.conversation_id = str(selected["id"])
    session.append_message({"role": "user", "content": "probe question", "id": None})
    queue: asyncio.Queue = asyncio.Queue()
    await processor_loop(
        session, "probe question", queue, session.current_epoch, session.history_epoch
    )
    return capture, session, counters


@pytest.mark.asyncio
async def test_second_round_after_recall_tool_call_drops_tools_rule_and_keeps_candidates(
    isolated_store, monkeypatch
):
    fact_text = "試験用のメモです。"
    capture, session, counters = await _drive_processor_loop(
        monkeypatch,
        provider_id="deepseek",
        responses=[
            _sse_tool_events(
                [
                    _frag(
                        0,
                        call_id="call_1",
                        name=RECALL_MEMORY_TOOL_NAME,
                        arguments='{"needs":["probe"]}',
                    )
                ]
            ),
            _sse_text("[EMO:neutral] 了解したわ。"),
        ],
        recalled_fact=fact_text,
    )

    record = session.turn_execution
    assert record is not None
    assert record.tool_plan_status == "ok"
    assert counters["memory_loads"] >= 1
    assert len(capture.requests) >= 2, "a scripted recall_memory call must add a second round"
    first, second = capture.requests[0], capture.requests[1]
    assert _envelope_violations(first, expect_read_gate=True) == []
    assert _envelope_violations(second, expect_read_gate=False) == []
    assert second.get("tools") in (None, [])
    assert fact_text in json.dumps(second, ensure_ascii=False)
    assert MEMORY_RECALL_DECISION_RULE not in (session.system_prompt or "")
    assert _rule_copies_in_request(second) == 0
    capture_dir = os.environ.get("B5B_CAPTURE_DIR")
    if capture_dir:
        Path(capture_dir).mkdir(parents=True, exist_ok=True)
        (Path(capture_dir) / "second-round-envelope.json").write_text(
            json.dumps(second, ensure_ascii=False, indent=2), encoding="utf-8"
        )


@pytest.mark.asyncio
async def test_unsupported_provider_fail_soft_has_no_rule_and_zero_memory_web_executions(
    isolated_store, monkeypatch
):
    capture, session, counters = await _drive_processor_loop(
        monkeypatch,
        provider_id="glm",
        responses=[_sse_text("[EMO:neutral] 了解したわ。")],
    )

    record = session.turn_execution
    assert record is not None
    assert record.tool_plan_status == "unsupported"
    assert record.tool_plan_reason == "undeclared_combo"
    assert counters == {"memory_loads": 0, "web_calls": 0}
    assert capture.requests
    request = capture.requests[0]
    assert not request.get("tools")
    assert _envelope_violations(request, expect_read_gate=False) == []
    assert _rule_copies_in_request(request) == 0
    assert MEMORY_RECALL_DECISION_RULE not in (session.system_prompt or "")


def test_frozen_literals_absent_and_schema_bounds_pinned():
    literals = _frozen_literals()
    assert literals, "frozen question set must yield distinctive literals"
    files = [
        BACKEND / "app" / "agent_tools.py",
        BACKEND / "app" / "routers" / "chat_ws.py",
        Path(__file__),
    ]
    for path in files:
        text = path.read_text(encoding="utf-8")
        hits = [literal for literal in literals if literal in text]
        assert hits == [], (path.name, hits[:8])
    assert _schema_violations(RECALL_MEMORY_TOOL) == []
    parameters = (RECALL_MEMORY_TOOL.get("function") or {}).get("parameters") or {}
    properties = parameters.get("properties") or {}
    assert set(properties) == {"needs"}
    assert parameters.get("required") == ["needs"]
    needs = properties.get("needs") or {}
    assert needs.get("minItems") == 1
    assert needs.get("maxItems") == 3
    assert (needs.get("items") or {}).get("maxLength") == 160
    assert _needs_accepted(RECALL_MEMORY_TOOL, ["one"])
    assert not _needs_accepted(RECALL_MEMORY_TOOL, [])
    assert not _needs_accepted(RECALL_MEMORY_TOOL, ["a", "b", "c", "d"])
    assert not _needs_accepted(RECALL_MEMORY_TOOL, ["x" * 161])
