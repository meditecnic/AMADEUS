"""Persona request layout: history once, fenced examples, tail reminder, budgets."""
from __future__ import annotations

import asyncio
import json
import re
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

from app import config, models
from app.routers import chat_ws
from app.security.prompt import SYSTEM_PROMPT_BASE, resolve_system_prompt_base
from app.services import deepseek
from app.services.conversations import conversation_service
from app.services.llm_interface import TAIL_REMINDER_HEADER
from app.services.memory import memory_service
from app.services.memory_v11.recall import unavailable_result
from app.services.persona_bible import persona_bible
from app.services.prompt_compiler import (
    PromptCompiler,
    PromptInputs,
    compile_for_session,
    render_persona_examples,
    render_tail_reminder,
)
from app.services.provider_adapters import (
    GeminiAdapter,
    OpenAICompatibleAdapter,
    OpenAIResponsesAdapter,
)
from app.services.provider_runtime import deepseek_service
from app.services.soul_engine import soul_engine
from app.services.turn_tool_orchestrator import prepare_second_round_payload
from test_working_summary import isolated_store  # noqa: F401

OKABE_NAMES = ("岡部", "凶真", "オカリン")
HISTORY = [
    {"role": "user", "content": "履歴U1：昨日の実験ノートを読んだ"},
    {"role": "assistant", "content": "[EMO:neutral] 履歴A1：ええ、条件の欄も見たわ。"},
    {"role": "user", "content": "履歴U2：測定は三回やった"},
    {"role": "assistant", "content": "[EMO:intellectual] 履歴A2：三回なら傾向は見えるわね。"},
    {"role": "user", "content": "履歴U3：明日は温度を変える"},
    {"role": "assistant", "content": "[EMO:neutral] 履歴A3：変えるのは一つずつにして。"},
]
CURRENT = "当前Z9：你觉得下一步先做哪个？"
SSE = {
    "chat": 'data: {"choices":[{"delta":{"content":"[EMO:neutral] まずは温度からね。"}}]}\n\ndata: [DONE]\n\n',
    "responses": 'data: {"type":"response.output_text.delta","delta":"[EMO:neutral] まずは温度からね。"}\n\n',
    "gemini": 'data: {"candidates":[{"content":{"parts":[{"text":"[EMO:neutral] まずは温度からね。"}]}}]}\n\n',
}
ADAPTERS = ("deepseek_service", "compatible_deepseek", "compatible_custom", "responses", "gemini")


def _transport(captured: list[dict], kind: str) -> httpx.MockTransport:
    async def handler(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content))
        return httpx.Response(200, text=SSE[kind], headers={"content-type": "text/event-stream"})

    return httpx.MockTransport(handler)


def _provider(name: str, captured: list[dict], monkeypatch):
    if name == "deepseek_service":
        real_client = httpx.AsyncClient
        transport = _transport(captured, "chat")
        monkeypatch.setattr(
            deepseek.httpx, "AsyncClient", lambda **kwargs: real_client(transport=transport, **kwargs)
        )
        return deepseek_service
    if name.startswith("compatible"):
        return OpenAICompatibleAdapter(
            provider_id="deepseek" if name == "compatible_deepseek" else "custom",
            base_url="https://compatible.example/v1",
            default_model="layout-model",
            message_builder=deepseek_service.build_persona_messages,
            credential_required=False,
            transport=_transport(captured, "chat"),
        )
    if name == "responses":
        return OpenAIResponsesAdapter(
            message_builder=deepseek_service.build_persona_messages,
            transport=_transport(captured, "responses"),
        )
    return GeminiAdapter(
        message_builder=deepseek_service.build_persona_messages,
        transport=_transport(captured, "gemini"),
    )


def _sent(payload: dict) -> list[tuple[str, str]]:
    """(role, text) pairs exactly as sent, system instructions first."""
    if "messages" in payload:
        return [(row["role"], str(row["content"])) for row in payload["messages"]]
    if "instructions" in payload:
        return [("system", payload["instructions"])] + [
            (row["role"], str(row["content"])) for row in payload["input"]
        ]
    system = "".join(part["text"] for part in payload["systemInstruction"]["parts"])
    return [("system", system)] + [
        (row["role"], "".join(part["text"] for part in row["parts"])) for row in payload["contents"]
    ]


def _session(identity_mode: str = "self", self_name: str = "ミナ") -> chat_ws.SessionState:
    session = chat_ws.SessionState(f"layout-{uuid4().hex}")
    session.identity_mode = identity_mode
    session.self_name = self_name
    session.api_key = "test-only-key"
    session.memory_summary = "要約：温度条件を比較中"
    session.history = [*HISTORY, {"role": "user", "content": CURRENT}]
    return session


def _no_legacy_memory(monkeypatch) -> None:
    async def empty(*_args, **_kwargs):
        return []

    monkeypatch.setattr(memory_service, "select_core_facts", empty)
    monkeypatch.setattr(memory_service, "lexical_search", empty)


async def _send(session, provider, active_history=None) -> None:
    snapshot = SimpleNamespace(adapter=provider, model_id="layout-model", temperature=None, reasoning_effort=None)
    await chat_ws.get_clean_text_stream(
        active_history=session.history if active_history is None else active_history,
        memory_summary="",
        session=session,
        provider_snapshot=snapshot,
    )


def _texts(payload: dict) -> list[str]:
    return [text for _role, text in _sent(payload)]


@pytest.mark.asyncio
@pytest.mark.parametrize("memory_mode", ["legacy", "v11"])
@pytest.mark.parametrize("adapter", ADAPTERS)
async def test_history_and_current_message_are_sent_exactly_once(
    isolated_store, monkeypatch, memory_mode, adapter
):
    monkeypatch.setenv("AMADEUS_MEMORY_MODE", memory_mode)
    _no_legacy_memory(monkeypatch)
    captured: list[dict] = []
    session = _session()
    session.system_prompt = await compile_for_session(session, CURRENT)
    await _send(session, _provider(adapter, captured, monkeypatch))

    texts = _texts(captured[0])
    for row in [*HISTORY, {"content": CURRENT}]:
        assert sum(text.count(row["content"]) for text in texts) == 1, row["content"]
    assert "RECENT DIALOGUE" not in texts[0]
    assert "CURRENT USER MESSAGE" not in texts[0]
    assert "要約：温度条件を比較中" in texts[0]


@pytest.mark.asyncio
async def test_second_round_with_tool_evidence_sends_history_once(isolated_store, monkeypatch):
    monkeypatch.setenv("AMADEUS_MEMORY_MODE", "v11")
    captured: list[dict] = []
    session = _session()
    session.system_prompt = await compile_for_session(session, CURRENT)
    payload = prepare_second_round_payload(
        session.history,
        unavailable_result("no_ranked_items"),
        [("web_search", "WEB BODY 温度")],
        system_prompt=session.system_prompt,
    )
    await _send(session, _provider("deepseek_service", captured, monkeypatch), payload.history)

    sent = _sent(captured[0])
    texts = [text for _role, text in sent]
    for row in [*HISTORY, {"content": CURRENT}]:
        assert sum(text.count(row["content"]) for text in texts) == 1, row["content"]
    assert sum(text.count("WEB BODY 温度") for text in texts) == 1
    assert sent[-1][1].startswith(TAIL_REMINDER_HEADER)


@pytest.mark.asyncio
@pytest.mark.parametrize("adapter", ADAPTERS)
async def test_tail_reminder_follows_last_user_message(isolated_store, monkeypatch, adapter):
    monkeypatch.setenv("AMADEUS_MEMORY_MODE", "legacy")
    _no_legacy_memory(monkeypatch)
    captured: list[dict] = []
    session = _session()
    before = session.history
    session.system_prompt = await compile_for_session(session, CURRENT)
    await _send(session, _provider(adapter, captured, monkeypatch))

    sent = _sent(captured[0])
    reminder = render_tail_reminder("self", "ミナ")
    assert sum(text.count(TAIL_REMINDER_HEADER) for _role, text in sent) == 1
    if adapter in {"deepseek_service", "compatible_deepseek"}:
        assert sent[-1] == ("system", reminder)
        assert sent[-2] == ("user", CURRENT)
    else:
        assert sent[-1] == ("user", f"{CURRENT}\n\n{reminder}")
        assert TAIL_REMINDER_HEADER not in sent[0][1]
    assert session.history == before


@pytest.mark.asyncio
async def test_tail_reminder_is_never_stored(isolated_store, monkeypatch):
    monkeypatch.setenv("AMADEUS_MEMORY_MODE", "legacy")
    _no_legacy_memory(monkeypatch)
    owner = uuid4().hex
    conversation = await conversation_service.create_and_select(owner, "steins_gate", identity_mode="self")
    session = _session()
    session.session_id = owner
    session.conversation_id = str(conversation["id"])
    session.enable_tts = False
    session.history = []
    captures: list[dict] = []

    class Provider:
        async def get_chat_stream(self, **kwargs):
            captures.append(kwargs)
            yield {"content": "[EMO:neutral] まずは温度からね。", "tool_calls": None}

        async def translate_to_zh(self, *args, **kwargs):
            return "先从温度开始。"

    monkeypatch.setattr(chat_ws, "deepseek_service", Provider())
    chat_ws.sessions[owner] = session
    try:
        session.append_message({"role": "user", "content": CURRENT})
        await chat_ws.processor_loop(session, CURRENT, asyncio.Queue(), session.current_epoch, session.history_epoch)
    finally:
        chat_ws.sessions.pop(owner, None)

    assert captures and captures[0]["active_history"][-1]["content"].startswith(TAIL_REMINDER_HEADER)
    stored = await models.get_session_messages(owner, conversation_id=session.conversation_id)
    assert [row["content"] for row in stored if row["role"] == "user"] == [CURRENT]
    assert all(TAIL_REMINDER_HEADER not in str(row["content"]) for row in stored)
    assert all(TAIL_REMINDER_HEADER not in str(row["content"]) for row in session.history)
    assert [row["role"] for row in session.history] == ["user", "assistant"]


def test_tail_reminder_wording_per_identity_mode():
    okabe = render_tail_reminder("okabe")
    named = render_tail_reminder("self", "ミナ")
    unnamed = render_tail_reminder("self", "")
    for text in (okabe, named, unnamed):
        assert text.startswith(TAIL_REMINDER_HEADER)
        assert "ユーザー入力の言語に関係なく、表示本文は必ず自然な日本語のみ" in text
        assert "[EMO:" in text
        assert "日本語の言い方に直す" in text
    assert "『岡部』" in okabe
    assert "『ミナ』" in named and "『あなた』" in unnamed
    for text in (named, unnamed):
        assert not any(name in text for name in OKABE_NAMES)


def _cp932(char: str) -> bool:
    try:
        char.encode("cp932")
    except UnicodeEncodeError:
        return False
    return True


def test_examples_are_japanese_and_split_by_identity_mode():
    payload = json.loads((config.PROMPTS_DIR / "kurisu_fewshot.json").read_text(encoding="utf-8"))
    allowed = "|".join(persona_bible.emotion_tones)
    assert set(payload) == {"self", "okabe"}
    for rows in payload.values():
        assert 3 <= len(rows) <= 5
        for row in rows:
            reply = row["assistant"]
            assert re.match(rf"^\[EMO:({allowed})\] ", reply), reply
            assert re.search(r"[\u3040-\u30ff]", reply), reply
            han = re.findall(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]", reply)
            assert [char for char in han if not _cp932(char)] == [], reply
    self_text = json.dumps(payload["self"], ensure_ascii=False)
    assert not any(name in self_text for name in OKABE_NAMES)


@pytest.mark.asyncio
@pytest.mark.parametrize("identity_mode", ["self", "okabe"])
async def test_examples_live_in_system_prompt_not_as_messages(isolated_store, monkeypatch, identity_mode):
    monkeypatch.setenv("AMADEUS_MEMORY_MODE", "legacy")
    _no_legacy_memory(monkeypatch)
    captured: list[dict] = []
    session = _session(identity_mode, self_name="")
    session.system_prompt = await compile_for_session(session, CURRENT)
    await _send(session, _provider("deepseek_service", captured, monkeypatch))

    messages = captured[0]["messages"]
    examples = render_persona_examples(identity_mode)
    other = render_persona_examples("okabe" if identity_mode == "self" else "self")
    assert examples in messages[0]["content"]
    assert other not in messages[0]["content"]
    assert "<examples>" in examples and "実際の会話ではない" in examples
    assert [row["content"] for row in messages[1:-1]] == [row["content"] for row in session.history]
    assert messages[-1]["content"] == render_tail_reminder(identity_mode, "")


def _inputs(identity_mode: str, worldline: str, **overrides) -> PromptInputs:
    values = dict(
        worldline=worldline,
        base_identity=resolve_system_prompt_base(SYSTEM_PROMPT_BASE, identity_mode),
        emotion=soul_engine.profile(worldline).baselines,
        core_facts=[],
        episodic=[],
        web_evidence=["ウェブ" * 2000],
        working_summary="要約" * 3000,
        recent_history=list(HISTORY),
        current_user_message=CURRENT,
        identity_mode=identity_mode,
        self_name="ミナ",
    )
    values.update(overrides)
    return PromptInputs(**values)


@pytest.mark.parametrize("memory_mode", ["legacy", "v11"])
def test_current_state_has_no_emotion_vector(memory_mode):
    from app.services.prompt_compiler import ResidentProfile

    resident = ResidentProfile(facts=[], candidate_count=0) if memory_mode == "v11" else None
    prompt = PromptCompiler().compile(_inputs("self", "steins_gate", resident_profile=resident))
    state = prompt.split("\n\nCURRENT STATE\n", 1)[1].split("\n\n", 1)[0]
    assert "identity_mode=self" in state
    assert "emotion=" not in prompt
    assert "trust" not in state


def test_character_profiles_have_no_style_rules():
    for filename in ("amadeus_sg.json", "amadeus_beta.json"):
        payload = json.loads((config.CHARACTERS_DIR / filename).read_text(encoding="utf-8"))
        assert "style_rules" not in payload
    assert soul_engine.readiness()["ok"] is True


@pytest.mark.parametrize("identity_mode", ["self", "okabe"])
@pytest.mark.parametrize("worldline", ["steins_gate", "beta"])
def test_persona_fits_its_budget_and_is_never_cut(identity_mode, worldline):
    compiler = PromptCompiler()
    identity = "IDENTITY AND SECURITY\n" + resolve_system_prompt_base(SYSTEM_PROMPT_BASE, identity_mode)
    persona = persona_bible.render_prompt(worldline, identity_mode=identity_mode)
    examples = render_persona_examples(identity_mode)
    assert compiler.token_counter(identity) <= compiler.BUDGETS["identity"]
    assert compiler.token_counter(persona) <= compiler.BUDGETS["persona"]
    assert compiler.token_counter(examples) <= compiler.BUDGETS["examples"]

    squeezed = PromptCompiler(input_budget=1).compile(_inputs(identity_mode, worldline))
    assert identity in squeezed
    assert persona in squeezed
    assert examples in squeezed


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("あいう", 3),
        ("漢字カナ", 4),
        ("「」。、", 4),
        ("ＡＢ", 2),
        ("abcd", 1),
        ("abcde", 2),
        ("ok。", 2),
        ("", 1),
    ],
)
def test_token_estimate_counts_cjk_per_character(text, expected):
    assert PromptCompiler._estimate_tokens(text) == expected


def test_fit_trims_cjk_to_the_token_budget():
    compiler = PromptCompiler()
    fitted = compiler._fit("あ" * 500, 100)
    assert compiler.token_counter(fitted) <= 100
    assert len(fitted) >= 99


@pytest.mark.asyncio
async def test_current_time_is_sent_to_the_minute(isolated_store, monkeypatch):
    captured: list[dict] = []
    session = _session("okabe", self_name="")
    session.system_prompt = "SYSTEM"
    await _send(session, _provider("deepseek_service", captured, monkeypatch))
    system = captured[0]["messages"][0]["content"]
    assert re.search(r"【現在時刻：\d{4}-\d{2}-\d{2} \d{2}:\d{2}】", system)
    assert not re.search(r"\d{2}:\d{2}:\d{2}", system)


def _long_history() -> list[dict]:
    """15 rounds of about 300 characters, ending with the current user turn."""
    rows: list[dict] = []
    for i in range(15):
        rows.append({"role": "user", "content": f"U{i}：" + ("今天记录实验条件。" * 40)[:300]})
        rows.append({"role": "assistant", "content": f"[EMO:neutral] A{i}：" + ("記録を確認するわ。" * 40)[:300]})
    rows.append({"role": "user", "content": CURRENT})
    return rows


@pytest.mark.asyncio
@pytest.mark.parametrize("with_web", [False, True])
@pytest.mark.parametrize("adapter", ["deepseek_service", "compatible_custom"])
async def test_second_round_over_budget_keeps_history_and_current_turn(
    isolated_store, monkeypatch, adapter, with_web
):
    monkeypatch.setenv("AMADEUS_MEMORY_MODE", "v11")
    captured: list[dict] = []
    session = _session()
    history = _long_history()
    session.history = history
    session.system_prompt = await compile_for_session(session, CURRENT)
    payload = prepare_second_round_payload(
        history,
        unavailable_result("no_ranked_items"),
        [("web_search", "WEB BODY 温度")] if with_web else None,
        system_prompt=session.system_prompt,
    )

    assert payload.reason == "input_budget"
    assert payload.history[: len(history)] == history
    assert len(payload.history) == len(history) + 1
    assert payload.history[-1]["role"] == "system"
    assert "WEB BODY 温度" not in json.dumps(payload.history, ensure_ascii=False)
    assert sum(row["content"].count(CURRENT) for row in payload.history) == 1

    await _send(session, _provider(adapter, captured, monkeypatch), payload.history)
    sent = _sent(captured[0])
    assert sum(text.count(CURRENT) for _role, text in sent) == 1
    assert sum(text.count(TAIL_REMINDER_HEADER) for _role, text in sent) == 1
    reminder = render_tail_reminder("self", "ミナ")
    if adapter == "deepseek_service":
        assert sent[-1] == ("system", reminder)
        assert sent[-3] == ("user", CURRENT)
    else:
        assert (f"{CURRENT}\n\n{reminder}") in [text for role, text in sent if role == "user"]
        assert sent[-1][0] == "system" and TAIL_REMINDER_HEADER not in sent[-1][1]
