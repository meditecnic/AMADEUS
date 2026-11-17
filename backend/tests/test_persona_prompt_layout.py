"""Persona request layout: history once, fenced examples, tail reminder, budgets."""
from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

from app import config, models
from app.routers import chat_ws
from app.services import deepseek
from app.services.conversations import conversation_service
from app.services.llm_interface import TAIL_REMINDER_HEADER
from app.services.memory_v11.recall import unavailable_result
from app.services.prompt_compiler import (
    PromptCompiler,
    compile_for_session,
    example_pool,
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
    session.turn_now_local = datetime.fromisoformat("2026-11-17T02:40:00+09:00")
    session.memory_summary = "要約：温度条件を比較中"
    session.history = [*HISTORY, {"role": "user", "content": CURRENT}]
    return session


async def _send(session, provider, active_history=None) -> None:
    snapshot = SimpleNamespace(adapter=provider, model_id="layout-model", temperature=None, reasoning_effort=None,
                               context_window=None, max_output_tokens=None)
    await chat_ws.get_clean_text_stream(
        active_history=session.history if active_history is None else active_history,
        memory_summary="",
        session=session,
        provider_snapshot=snapshot,
    )


def _texts(payload: dict) -> list[str]:
    return [text for _role, text in _sent(payload)]


@pytest.mark.asyncio
@pytest.mark.parametrize("adapter", ADAPTERS)
async def test_history_and_current_message_are_sent_exactly_once(
    isolated_store, monkeypatch, adapter
):
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
    captured: list[dict] = []
    session = _session()
    before = session.history
    session.system_prompt = await compile_for_session(session, CURRENT)
    await _send(session, _provider(adapter, captured, monkeypatch))

    sent = _sent(captured[0])
    reminder = render_tail_reminder(now_local=session.turn_now_local)
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



@pytest.mark.parametrize(("hour", "gap", "expected"), [
    (12, 23, None), (2, 2, None), (2, 3, "約3時間ぶり・深夜"),
    (5, 5, None), (12, 24, "前回から約1日ぶり"), (12, 52, "前回から約2日ぶり"),
])
def test_tail_clock_only_marks_meaningful_intervals(hour, gap, expected):
    now = datetime.fromisoformat("2026-11-17T02:40:00+09:00").replace(hour=hour)
    tail = render_tail_reminder(now_local=now, previous_user_at=now - timedelta(hours=gap))
    assert f"2026-11-17 (火) {hour:02d}:40 UTC+09:00 [秋・" in tail
    assert "【今回のネット検索：未実施】" in tail
    assert (tail.splitlines()[0].split("｜", 1)[1] if "｜" in tail else None) == expected
    assert not any(old in tail for old in ("一〜四文", "聞かれたことに答え", "短い質問", "60字以内", "不確かなら聞く"))


@pytest.mark.parametrize(("worldline", "mode", "familiarity", "ids"), [
    ("steins_gate", "okabe", "initial", "A1 B1 B2 C1 C2 D2"),
    ("beta", "okabe", "initial", "A1 B1 B2 C1 C2 D2 E1"),
    ("steins_gate", "self", "initial", "D1 F1"),
    ("beta", "self", "familiar", "A2 C1 C2 D1 E2 F2"),
])
def test_examples_are_whole_and_filtered(worldline, mode, familiarity, ids):
    assert set(example_pool(worldline, mode, familiarity)) == set(ids.split())
    path = config.PROMPTS_DIR / "kurisu_fewshot.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    joined = render_persona_examples(mode, worldline=worldline, familiarity=familiarity, year=2026)
    assert joined.count("<example>") == min(6, len(ids.split()))
    for scene in data:
        included = scene["turns"][0]["content"] in joined
        if scene["id"] not in ids.split():
            assert not included
        elif len(ids.split()) <= 6:
            assert included
        if included:
            assert all(turn["content"] in joined for turn in scene["turns"])
    if mode == "self":
        assert not re.search(r"岡部|あんた|助手", joined)
    assert PromptCompiler._estimate_tokens(joined) <= PromptCompiler.BUDGETS["examples"]


def test_beta_example_shows_the_current_year_in_both_turns():
    for index in range(20):
        kwargs = dict(worldline="beta", conversation_key=str(index))
        original = render_persona_examples("okabe", year=2026, **kwargs)
        if "十六年分" in original:
            updated = render_persona_examples("okabe", year=2027, **kwargs)
            assert "2027年" in updated and updated.count("十七年") == 2
            assert "2026年だ" not in updated and "十六年" not in updated
            assert "2016年だ" in render_persona_examples("okabe", year=2016, **kwargs)
            break
    else:
        pytest.fail("no conversation sampled the beta E1 example")


@pytest.mark.asyncio
async def test_prefix_is_stable_across_turn_clock_and_current_message(isolated_store):
    session = _session()
    session.conversation_id = "same-conversation"
    first = await compile_for_session(session, CURRENT)
    session.turn_now_local += timedelta(hours=2)
    session.append_message({"role": "user", "content": "別の質問をするね。"})
    second = await compile_for_session(session, "別の質問をするね。")
    assert first == second
    assert "ミナ" in first and TAIL_REMINDER_HEADER not in first
    assert "【これまでの流れ】" in first




@pytest.mark.asyncio
async def test_self_register_switches_at_six_turns_or_with_prior_history(isolated_store):
    session = _session()
    session.history = [{"role": "user", "content": f"質問{index}"} for index in range(5)]
    initial = await compile_for_session(session, "質問4")
    assert "はじめまして、紅莉栖。" in initial and "自然な友人の口調で話す。" not in initial
    session.append_message({"role": "user", "content": "質問5"})
    familiar = await compile_for_session(session, "質問5")
    assert "はじめまして、紅莉栖。" not in familiar and "自然な友人の口調で話す。" in familiar
    assert familiar.count("<example>") == 6

    session.history = []
    for worldline, mode in [("beta", "self"), ("steins_gate", "okabe"), ("steins_gate", "self")]:
        prior = await conversation_service.create(session.session_id, worldline, identity_mode=mode)
        await models.save_message(
            session.session_id, "user", "前の質問です。", worldline=worldline, conversation_id=prior["id"],
        )
        await models.save_message(
            session.session_id, "assistant", "前の返事です。", worldline=worldline, conversation_id=prior["id"],
        )
        compiled = await compile_for_session(session, "こんにちは")
        expected = 6 if (worldline, mode) == ("steins_gate", "self") else 2
        assert compiled.count("<example>") == expected


@pytest.mark.asyncio
async def test_resident_register_preference_skips_initial_examples(isolated_store):
    from app.services.memory_v11.repository import create_stable_fact

    session = _session()
    await create_stable_fact(
        session_id=session.session_id, worldline="steins_gate", identity_mode="self",
        display_text="用户希望聊天不用敬语。",
        semantic_json={"subject": "user", "relation": "prefers", "object": "casual speech"},
    )
    compiled = await compile_for_session(session, CURRENT)
    assert compiled.count("<example>") == 6 and "自然な友人の口調で話す。" in compiled


@pytest.mark.asyncio
async def test_normal_request_retains_all_raw_history_and_resident_facts(isolated_store, monkeypatch):
    from app.security.prompt import system_prompt_base
    from app.services.memory import MEMORY_INGEST_WINDOW_N
    from app.services.prompt_compiler import PromptInputs

    session = _session("okabe")
    assert config.HISTORY_RAW_KEEP_MESSAGES == 36 and MEMORY_INGEST_WINDOW_N == 6
    assert config.HISTORY_COMPRESS_THRESHOLD == 24
    session.history = [
        {"role": "user" if index % 2 == 0 else "assistant",
         "content": f"発言{index:02d}：" + "温度と測定条件を記録する。" * 12}
        for index in range(36)
    ] + [{"role": "user", "content": CURRENT}]
    compiler = PromptCompiler()
    facts = [
        f"測定 {index:02d}：私の実験ノートでは試料を低温で保管する。"
        for index in range(32)
    ]
    profile = compiler.select_resident_profile([
        {"fact_id": str(index), "display_text": text} for index, text in enumerate(facts)
    ], tool_capable=False)
    assert compiler.BUDGETS["core"] == 1200
    assert profile.complete and len(profile.facts) == 32
    session.system_prompt = compiler.compile(PromptInputs(
        worldline=session.worldline, base_identity=system_prompt_base("okabe"),
        emotion=soul_engine.profile("steins_gate").baselines, episodic=[], web_evidence=[],
        working_summary="私たちは次の測定条件を比較している。",
        recent_history=session.history, current_user_message=CURRENT,
        local_year=2026, resident_profile=profile,
    ))
    captured = []
    await _send(session, _provider("deepseek_service", captured, monkeypatch))
    sent = _sent(captured[0])
    assert sent[0][1].count("<example>") == 6 and "【わたしについて】" in sent[0][1]
    assert all(text in sent[0][1] for text in facts)
    assert sent[1:-1] == [(row["role"], row["content"]) for row in session.history]

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
async def test_second_round_request_budget_keeps_history_and_current_turn(
    isolated_store, monkeypatch, adapter, with_web
):
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

    assert payload.reason == ""
    assert payload.history[: len(history)] == history
    assert len(payload.history) == len(history) + 1 + int(with_web)
    assert payload.history[-1]["role"] == "system"
    assert ("WEB BODY 温度" in json.dumps(payload.history, ensure_ascii=False)) == with_web
    assert sum(row["content"].count(CURRENT) for row in payload.history) == 1

    await _send(session, _provider(adapter, captured, monkeypatch), payload.history)
    sent = _sent(captured[0])
    assert sum(text.count(CURRENT) for _role, text in sent) == 1
    assert sum(text.count(TAIL_REMINDER_HEADER) for _role, text in sent) == 1
    reminder = render_tail_reminder(now_local=session.turn_now_local, searched=with_web)
    assert sum(text.count("【今回のネット検索：実施") for _role, text in sent) == int(with_web)
    if adapter == "deepseek_service":
        assert sent[-1] == ("system", reminder)
        assert sent[-3 - int(with_web)] == ("user", CURRENT)
    else:
        user_text = next(text for role, text in sent if role == "user" and CURRENT in text)
        assert user_text.startswith(CURRENT + "\n\n") and user_text.endswith(reminder)
        assert sent[-1][0] == "system" and TAIL_REMINDER_HEADER not in sent[-1][1]


@pytest.mark.asyncio
async def test_221_text_chat_skips_restore_emotion_while_voice_preserves_it(
    isolated_store, monkeypatch
):
    from unittest.mock import AsyncMock

    owner = f"emo-gate-{uuid4().hex}"
    await conversation_service.create_and_select(owner, "steins_gate", identity_mode="okabe")
    baseline = soul_engine.profile("steins_gate").baselines.model_copy(deep=True)
    mock_restore = AsyncMock(return_value=baseline)
    monkeypatch.setattr(type(soul_engine), "restore_emotion", mock_restore)

    for client_type in ("mobile", "desktop"):
        text_session = chat_ws.SessionState(owner)
        text_session.worldline = "steins_gate"
        text_session.client_type = client_type
        await text_session.load_from_db()
        await text_session.enter_draft()
        prompt = await compile_for_session(text_session, "こんにちは")
        assert "【わたしについて】" in prompt
        assert mock_restore.await_count == 0

    voice_session = chat_ws.SessionState(owner)
    voice_session.worldline = "steins_gate"
    voice_session.client_type = "voice"
    await voice_session.load_from_db()
    await voice_session.enter_draft()
    await compile_for_session(voice_session, "こんにちは")
    assert mock_restore.await_count == 3
