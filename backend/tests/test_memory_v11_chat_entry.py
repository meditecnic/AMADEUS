"""Entry-level integration tests for the v11 chat memory path (2026-09-09).

Task memory-chat-glm-20260909: drive the REAL chat chain —
SessionState → chat_ws.processor_loop → compile_for_session → v11/legacy
selectors → get_clean_text_stream → provider adapter — with a controllable
fake provider that reuses the real adapter's message assembly
(``build_stream_messages`` + ``build_persona_messages``) and captures the
exact messages envelope the network request would have carried. The voice
entry is exercised through the real ``/ws/voice`` endpoint with a fake STT.

Nothing here mocks the compiler or the selectors to fabricate a "correct
prompt": the compiler runs for real, selectors run against synthetic DBs,
and assertions are made on the captured provider boundary.

Covered:
- legacy/shadow/v11 modes deliver the right Memory to the provider boundary;
  shadow never leaks v11 rows; v11 never merges legacy/shared/other scopes;
  no second legacy Memory extraction runs while v11 owns the turn;
- a small synthetic rehearsal target produced by the real offline migration
  tool feeds one legacy_import fact into the provider input through the chat
  entry; conflicting markers of other owner/identity/worldline never appear;
- revision/deletion are reflected in the next turn's Memory section;
- a failing core Fact selector no longer fails the turn (D52): the provider
  still receives a freshly compiled prompt without the failed Facts and
  without the stale previous system prompt; the next turn recovers. The
  Experience fail-soft semantics and CancelledError propagation stay intact;
- the voice entry (fake STT) reaches the same processor/prompt/provider
  boundary.

All data is synthetic; no real APPDATA, credentials, or external models.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from app.db import (
    HISTORY_SCHEMA,
    MEMORY_SCHEMA,
    get_db,
    init_db,
    reset_initialization_cache,
)
from app.services.deepseek import build_stream_messages
from app.services.memory_v11 import migration as mig
from app.services.memory_v11.repository import create_stable_fact
from app.services.provider_runtime import deepseek_service

BACKEND_ROOT = Path(__file__).resolve().parents[1]
SG = "steins_gate"
BETA = "beta"
REPLY_JA = "そうですね、それは良いですね。"


# ---------------------------------------------------------------------------
# Fixtures / helpers.
# ---------------------------------------------------------------------------


@pytest.fixture
async def isolated_store(tmp_path, monkeypatch):
    monkeypatch.delenv("AMADEUS_DB_PATH", raising=False)
    monkeypatch.setenv("AMADEUS_DATA_DIR", str(tmp_path))
    reset_initialization_cache()
    await init_db()
    return tmp_path


def test_app_module_resolves_to_this_worktree():
    import app

    assert Path(app.__file__).resolve() == BACKEND_ROOT / "app" / "__init__.py"


class _ProviderCapture:
    """Records every chat request assembled for the provider boundary."""

    def __init__(self, reply: str = REPLY_JA):
        self.reply = reply
        self.chat_calls: list[dict] = []
        self.translations: list[str] = []

    @property
    def last_call(self) -> dict:
        assert self.chat_calls, "provider received no chat request"
        return self.chat_calls[-1]

    @property
    def last_system_prompt(self) -> str:
        system = self.last_call["messages"][0]
        assert system["role"] == "system"
        return str(system["content"])


def _install_fake_provider(monkeypatch, capture: _ProviderCapture) -> None:
    """Intercept the deepseek adapter boundary, reusing the REAL assembly.

    ``get_chat_stream`` normally builds ``messages`` then opens a network
    stream; the fake keeps the message assembly (build_stream_messages +
    build_persona_messages, including anchors and few-shot) and replaces only
    the network with a canned Japanese reply, so the captured envelope is
    exactly what a real request would have carried.
    """

    async def fake_get_chat_stream(
        active_history=None,
        memory_summary=None,
        api_key=None,
        system_prompt=None,
        temperature=0.7,
        tools=None,
        tool_choice=None,
        model=None,
        reasoning_effort=None,
        isolated=False,
    ):
        messages = build_stream_messages(
            deepseek_service.build_persona_messages,
            active_history,
            memory_summary,
            system_prompt,
            isolated=isolated,
        )
        capture.chat_calls.append(
            {
                "messages": messages,
                "system_prompt": system_prompt,
                "active_history": active_history,
                "memory_summary": memory_summary,
                "tools": tools,
                "model": model,
            }
        )
        yield {"content": capture.reply, "tool_calls": None}

    async def fake_translate_to_zh(text, api_key=None, model=None):
        capture.translations.append(text)
        return "中文翻译"

    monkeypatch.setattr(deepseek_service, "get_chat_stream", fake_get_chat_stream)
    monkeypatch.setattr(deepseek_service, "translate_to_zh", fake_translate_to_zh)


def _suppress_auto_title(monkeypatch) -> None:
    """Auto title generation is auxiliary LLM work; record instead of calling."""
    from app.services.conversations import conversation_service

    def fake_schedule(*args, **kwargs):
        async def _noop():
            return None

        return asyncio.get_running_loop().create_task(_noop())

    monkeypatch.setattr(conversation_service, "schedule_auto_title", fake_schedule)


def _record_legacy_extraction(monkeypatch) -> list:
    """Record legacy Quiet Ingest scheduling instead of running extraction."""
    from app.services.memory import memory_service

    calls: list = []

    def fake_schedule(*args, **kwargs):
        calls.append({"args": args, "kwargs": kwargs})

    monkeypatch.setattr(memory_service, "schedule_turn_extraction", fake_schedule)
    return calls


def _make_session(session_id: str, *, identity_mode: str = "self"):
    from app.routers.chat_ws import SessionState

    session = SessionState(session_id)
    session.worldline = SG
    session.identity_mode = identity_mode
    session.enable_tts = False  # user-disabled TTS: text-only turns, no sidecar
    session.api_key = "test-provider-credential"
    return session


async def _prepare_conversation(session, *, identity_mode: str = "self") -> None:
    from app.services.conversations import conversation_service

    selected = await conversation_service.create_and_select(
        session.session_id,
        session.worldline,
        provider_id="deepseek",
        model_id="deepseek-v4-flash",
        identity_mode=identity_mode,
    )
    session.conversation_id = str(selected["id"])
    session.identity_mode = identity_mode


async def _run_turn(session, user_msg: str) -> list[dict]:
    """Run one real processor turn and return every queued WS payload."""
    from app.routers.chat_ws import processor_loop

    session.append_message({"role": "user", "content": user_msg, "id": None})
    send_queue: asyncio.Queue = asyncio.Queue()
    await processor_loop(
        session, user_msg, send_queue, session.current_epoch, session.history_epoch
    )
    events: list[dict] = []
    while not send_queue.empty():
        _epoch, payload = send_queue.get_nowait()
        events.append(payload)
    return events


def _errors(events: list[dict]) -> list[dict]:
    return [e for e in events if e.get("type") == "error"]


def _done(events: list[dict]) -> bool:
    return any(
        e.get("type") == "status" and e.get("state") == "done" for e in events
    )


class _StubEmbedder:
    model_name = "intfloat/multilingual-e5-small"
    dimensions = 384

    async def encode_passage(self, text: str) -> list[float]:
        return [0.0] * 384


# ---------------------------------------------------------------------------
# 1. The D52 fix: core Fact selector failure fails soft (compiler level).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fact_selector_failure_fails_soft_at_compiler_level(
    isolated_store, monkeypatch
):
    """Counterexample for the fixed fault: a raising Fact selector used to
    propagate out of compile_for_session and fail the turn; now Facts are
    omitted, independently usable Experiences stay, legacy is not consulted,
    and the turn prompt still compiles."""
    import app.services.memory_v11.retrieval as retrieval_module
    from app.services.prompt_compiler import compile_for_session

    monkeypatch.setenv("AMADEUS_MEMORY_MODE", "v11")
    session_id = f"chat-fix-{uuid4()}"
    fact_marker = f"FACT_SEL_FAIL_{uuid4().hex[:8]}"
    legacy_marker = f"LEGACY_SEL_FAIL_{uuid4().hex[:8]}"
    exp_marker = f"EXP_SEL_FAIL_{uuid4().hex[:8]}"

    await create_stable_fact(
        session_id=session_id, worldline=SG, identity_mode="self",
        display_text=f"{fact_marker} 好きなアニメはこのすば",
        semantic_json={"subject": "user", "relation": "likes", "object": "anime"},
    )
    db = await get_db(SG, "memory")
    try:
        await db.execute(
            """INSERT INTO episodic_memories(
                   session_id, identity_mode, content, confidence, importance,
                   source_message_ids, created_at
               ) VALUES(?, 'self', ?, 0.9, 0.5, '[]', '2026-08-01T00:00:00+00:00')""",
            (session_id, f"{legacy_marker} 旧版记录"),
        )
        await db.commit()
    finally:
        await db.close()
    await _seed_experience(
        session_id=session_id, display_text=f"{exp_marker} このすばのイベントに行った"
    )

    async def boom(*args, **kwargs):
        raise RuntimeError("forced fact selector failure")

    monkeypatch.setattr(retrieval_module, "select_stable_fact_candidates", boom)
    session = SimpleNamespace(
        session_id=session_id, worldline=SG, identity_mode="self",
        base_system_prompt="You are Amadeus Kurisu.",
        system_prompt="You are Amadeus Kurisu.",
        memory_summary="", history=[], self_name="", identity_acknowledged=False,
    )
    prompt = await compile_for_session(session, "このすば")
    assert fact_marker not in prompt, "v11 first prompt must not pre-inject stable facts"
    assert exp_marker in prompt, "independent Experiences must stay available"
    assert legacy_marker not in prompt, "no legacy fallback may run"
    assert "\nCORE FACTS\n" not in prompt
    assert "EXPERIENCE_USE_CONTRACT" in prompt


@pytest.mark.asyncio
async def test_fact_selector_cancellation_still_propagates(isolated_store, monkeypatch):
    """The narrow fail-soft boundary must not swallow cancellation."""
    import app.services.memory_v11.retrieval as retrieval_module
    from app.services.prompt_compiler import compile_for_session

    monkeypatch.setenv("AMADEUS_MEMORY_MODE", "v11")
    session_id = f"chat-cancel-{uuid4()}"

    async def cancelled(*args, **kwargs):
        raise asyncio.CancelledError()

    monkeypatch.setattr(retrieval_module, "select_experience_candidates", cancelled)
    session = SimpleNamespace(
        session_id=session_id, worldline=SG, identity_mode="self",
        base_system_prompt="You are Amadeus Kurisu.",
        system_prompt="You are Amadeus Kurisu.",
        memory_summary="", history=[], self_name="", identity_acknowledged=False,
    )
    with pytest.raises(asyncio.CancelledError):
        await compile_for_session(session, "このすば")


@pytest.mark.asyncio
async def test_processor_cancelled_while_fact_selector_waits_never_reaches_provider(
    isolated_store, monkeypatch
):
    """A live processor cancellation must stop before provider publication."""
    import app.services.memory_v11.retrieval as retrieval_module
    from app.routers.chat_ws import processor_loop
    from app.services.session_manager import cancel_inflight

    monkeypatch.setenv("AMADEUS_MEMORY_MODE", "v11")
    started = asyncio.Event()
    selector_cancelled = asyncio.Event()

    async def blocked_selector(*args, **kwargs):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            selector_cancelled.set()

    monkeypatch.setattr(
        retrieval_module, "select_experience_candidates", blocked_selector
    )
    capture = _ProviderCapture()
    _install_fake_provider(monkeypatch, capture)
    _suppress_auto_title(monkeypatch)

    session = _make_session(f"processor-cancel-{uuid4()}")
    await _prepare_conversation(session)
    user_msg = "selector cancellation"
    session.append_message({"role": "user", "content": user_msg, "id": None})
    send_queue: asyncio.Queue = asyncio.Queue()
    task = asyncio.create_task(
        processor_loop(
            session,
            user_msg,
            send_queue,
            session.current_epoch,
            session.history_epoch,
        )
    )
    session.active_task = task

    await asyncio.wait_for(started.wait(), 5)
    assert await cancel_inflight(session) == 1
    assert task.cancelled()
    assert selector_cancelled.is_set()

    events = []
    while not send_queue.empty():
        _epoch, payload = send_queue.get_nowait()
        events.append(payload)
    assert capture.chat_calls == []
    assert not _done(events), events
    assert not any(event.get("type") == "text_chunk" for event in events), events
    assert session.active_task is None
    assert session.is_busy is False


@pytest.mark.asyncio
async def test_forget_route_cancels_matching_processor_before_reply(
    isolated_store, monkeypatch
):
    """The real forget runtime cancels its registered conversation processor."""
    import app.services.memory_v11.retrieval as retrieval_module
    from app import models
    from app.routers.chat_ws import processor_loop, sessions
    from app.routers.conversations import ConversationForget, forget_conversation

    monkeypatch.setenv("AMADEUS_MEMORY_MODE", "v11")
    started = asyncio.Event()
    selector_cancelled = asyncio.Event()

    async def blocked_selector(*args, **kwargs):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            selector_cancelled.set()

    monkeypatch.setattr(
        retrieval_module, "select_experience_candidates", blocked_selector
    )
    capture = _ProviderCapture()
    _install_fake_provider(monkeypatch, capture)
    _suppress_auto_title(monkeypatch)

    session = _make_session(f"forget-cancel-{uuid4()}")
    await _prepare_conversation(session)
    conversation_id = str(session.conversation_id)
    session.history = [{"role": "user", "content": "old cached context", "id": None}]
    session.memory_summary = "old cached summary"
    sessions[session.session_id] = session
    send_queue: asyncio.Queue = asyncio.Queue()
    task = None
    try:
        user_msg = "forget this in-flight turn"
        session.append_message({"role": "user", "content": user_msg, "id": None})
        task = asyncio.create_task(
            processor_loop(
                session,
                user_msg,
                send_queue,
                session.current_epoch,
                session.history_epoch,
            )
        )
        session.active_task = task
        await asyncio.wait_for(started.wait(), 5)

        await forget_conversation(
            conversation_id,
            ConversationForget(forget_long_term=False),
            session.session_id,
            session.worldline,
        )

        assert task.cancelled()
        assert selector_cancelled.is_set()
        assert session.active_task is None
        assert session.is_busy is False
        assert session.erasure_pending is False
        assert session.history == []
        assert session.memory_summary == ""
        assert await models.get_session_messages(
            session.session_id,
            worldline=session.worldline,
            conversation_id=conversation_id,
        ) == []
        events = []
        while not send_queue.empty():
            _epoch, payload = send_queue.get_nowait()
            events.append(payload)
        assert capture.chat_calls == []
        assert not _done(events), events
        assert not any(event.get("type") == "text_chunk" for event in events), events
    finally:
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        if sessions.get(session.session_id) is session:
            sessions.pop(session.session_id, None)


async def _seed_experience(*, session_id: str, display_text: str) -> None:
    import hashlib

    semantic = {"subject": "user", "predicate": "event", "object": display_text[:24]}
    blob = json.dumps(semantic, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    fingerprint = hashlib.sha256(blob.encode("utf-8")).hexdigest()
    db = await get_db(SG, "memory")
    try:
        observation_id = str(uuid4())
        now = "2026-08-01T00:00:00+00:00"
        await db.execute(
            """INSERT INTO memory_observations(
                   observation_id, session_id, identity_mode, display_text,
                   semantic_json, semantic_fingerprint, evidence_kind, memory_class,
                   confidence, topic_label_proposal, status, extractor_version,
                   reanalysis_count, expires_at, created_at, updated_at
               ) VALUES(?,?, 'self', ?, ?, ?, 'direct_user', 'episodic', 0.9, NULL,
                        'attached', 'memory-v11-1', 0, NULL, ?, ?)""",
            (observation_id, session_id, display_text, blob, fingerprint, now, now),
        )
        await db.execute(
            """INSERT INTO memory_observation_sources(
                   observation_id, source_message_id, conversation_id, source_role,
                   source_fingerprint, source_created_at, source_state, excerpt
               ) VALUES(?,?,?, 'user', ?, ?, 'present', NULL)""",
            (observation_id, 424242, "conv-x", f"fp-{observation_id[:8]}", now),
        )
        await db.execute(
            """INSERT INTO experiences(
                   experience_id, session_id, conversation_id, identity_mode,
                   observation_id, display_text, semantic_json, semantic_fingerprint,
                   confidence, status, expires_at, created_at, updated_at, deleted_at
               ) VALUES(?,?,?,?,?,?,?,? ,0.9,'active',NULL,?,?,NULL)""",
            (str(uuid4()), session_id, "conv-x", "self", observation_id,
             display_text, blob, fingerprint, now, now),
        )
        await db.commit()
    finally:
        await db.close()


# ---------------------------------------------------------------------------
# 2. Entry level: three modes at the provider boundary.
# ---------------------------------------------------------------------------


async def _seed_v11_fact(session_id: str, marker: str, *, identity_mode: str = "self", worldline: str = SG) -> None:
    await create_stable_fact(
        session_id=session_id, worldline=worldline, identity_mode=identity_mode,
        display_text=f"{marker} 好きなアニメはこのすば",
        semantic_json={"subject": "user", "relation": "likes", "object": "anime"},
    )


async def _seed_legacy_fact(session_id: str, marker: str) -> None:
    from app.services.memory import CoreFactCandidate, memory_service

    await memory_service.upsert_core_fact(
        session_id, SG,
        CoreFactCandidate(
            fact_key=f"legacy_{marker[:12]}",
            fact_value=f"{marker} 旧版の好きなアニメ",
            confidence=0.9, importance=0.85, source_message_ids=[12345],
        ),
        identity_mode="self",
    )


@pytest.mark.asyncio
async def test_v11_mode_delivers_scoped_memory_to_provider(isolated_store, monkeypatch):
    monkeypatch.setenv("AMADEUS_MEMORY_MODE", "v11")
    session_id = f"chat-v11-{uuid4()}"
    self_marker = f"V11_SELF_{uuid4().hex[:8]}"
    okabe_marker = f"V11_OKABE_{uuid4().hex[:8]}"
    other_owner_marker = f"V11_OTHER_{uuid4().hex[:8]}"
    beta_marker = f"V11_BETA_{uuid4().hex[:8]}"
    legacy_marker = f"LEGACY_MIX_{uuid4().hex[:8]}"
    shared_marker = f"SHARED_MIX_{uuid4().hex[:8]}"

    await _seed_v11_fact(session_id, self_marker, identity_mode="self")
    await _seed_v11_fact(session_id, okabe_marker, identity_mode="okabe")
    await _seed_v11_fact(f"other-{session_id[:8]}", other_owner_marker)
    await _seed_v11_fact(session_id, beta_marker, worldline=BETA)
    await _seed_legacy_fact(session_id, legacy_marker)
    # shared fact in control store (never read by the v11 chat path).
    db = await get_db(SG, "control")
    try:
        await db.execute(
            """INSERT INTO shared_user_facts(
                   owner_session_id,fact_key,fact_value,confidence,importance,
                   is_current,is_pinned,origin_worldline,origin_conversation_id,
                   origin_identity_mode,source_message_ids,created_at
               ) VALUES(?,?,?,?,?,1,0,'steins_gate','conv-x','self','[1]',?)""",
            (session_id, f"shared_{session_id[:8]}", f"{shared_marker} 共享事实", 0.9, 0.8,
             "2026-08-01T00:00:00+00:00"),
        )
        await db.commit()
    finally:
        await db.close()

    capture = _ProviderCapture()
    _install_fake_provider(monkeypatch, capture)
    _suppress_auto_title(monkeypatch)
    legacy_calls = _record_legacy_extraction(monkeypatch)

    session = _make_session(session_id)
    await _prepare_conversation(session, identity_mode="self")
    events = await _run_turn(session, "このすば")

    assert _done(events) and not _errors(events), events
    assert len(capture.chat_calls) == 1, "one provider chat request per turn"
    system_prompt = capture.last_system_prompt
    assert self_marker not in system_prompt, "v11 first round must not pre-inject facts"
    for forbidden in (okabe_marker, other_owner_marker, beta_marker, legacy_marker, shared_marker):
        assert forbidden not in system_prompt, forbidden
    assert "\nCORE FACTS\n" not in system_prompt
    tool_blob = json.dumps(capture.last_call.get("tools") or [], ensure_ascii=False)
    assert "recall_memory" in tool_blob
    assert legacy_marker not in json.dumps(capture.last_call["messages"], ensure_ascii=False)
    # The processor passes memory_summary='' to the adapter: no double append.
    assert capture.last_call["memory_summary"] == ""
    summary_messages = [
        m for m in capture.last_call["messages"]
        if "これまでの会話の記憶要約" in str(m.get("content") or "")
    ]
    assert not summary_messages
    # v11 owns the turn: exactly one chat call and no legacy extraction run.
    assert legacy_calls == []
    assert await _wait_for_v11_job(session_id)


async def _wait_for_v11_job(session_id: str, timeout: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        db = await get_db(SG, "memory")
        try:
            row = await (
                await db.execute(
                    "SELECT job_id FROM memory_ingest_jobs WHERE session_id=?",
                    (session_id,),
                )
            ).fetchone()
        finally:
            await db.close()
        if row is not None:
            return True
        await asyncio.sleep(0.05)
    return False


@pytest.mark.asyncio
async def test_legacy_mode_delivers_legacy_memory_only(isolated_store, monkeypatch):
    monkeypatch.setenv("AMADEUS_MEMORY_MODE", "legacy")
    session_id = f"chat-legacy-{uuid4()}"
    v11_marker = f"V11_ONLY_LEG_{uuid4().hex[:8]}"
    legacy_marker = f"LEGACY_REAL_{uuid4().hex[:8]}"
    await _seed_v11_fact(session_id, v11_marker)
    await _seed_legacy_fact(session_id, legacy_marker)

    capture = _ProviderCapture()
    _install_fake_provider(monkeypatch, capture)
    _suppress_auto_title(monkeypatch)
    legacy_calls = _record_legacy_extraction(monkeypatch)

    session = _make_session(session_id)
    await _prepare_conversation(session, identity_mode="self")
    events = await _run_turn(session, "このすば")

    assert _done(events) and not _errors(events), events
    system_prompt = capture.last_system_prompt
    assert legacy_marker in system_prompt, "legacy core fact must reach the provider"
    assert v11_marker not in system_prompt, "legacy mode must not read v11 rows"
    # Legacy Quiet Ingest owns extraction: scheduled once, and this turn made
    # exactly one provider chat request (no second Memory LLM call).
    assert len(legacy_calls) == 1
    assert len(capture.chat_calls) == 1


@pytest.mark.asyncio
async def test_shadow_mode_reads_legacy_and_hides_v11(isolated_store, monkeypatch):
    monkeypatch.setenv("AMADEUS_MEMORY_MODE", "shadow")
    session_id = f"chat-shadow-{uuid4()}"
    v11_marker = f"V11_ONLY_SHD_{uuid4().hex[:8]}"
    legacy_marker = f"LEGACY_SHD_{uuid4().hex[:8]}"
    await _seed_v11_fact(session_id, v11_marker)
    await _seed_legacy_fact(session_id, legacy_marker)

    capture = _ProviderCapture()
    _install_fake_provider(monkeypatch, capture)
    _suppress_auto_title(monkeypatch)

    session = _make_session(session_id)
    await _prepare_conversation(session, identity_mode="self")
    events = await _run_turn(session, "このすば")

    assert _done(events) and not _errors(events), events
    system_prompt = capture.last_system_prompt
    assert legacy_marker in system_prompt
    assert v11_marker not in system_prompt, "shadow writes must never leak to the prompt"


# ---------------------------------------------------------------------------
# 3. Real migration tool output -> chat entry -> provider boundary.
# ---------------------------------------------------------------------------


def _open(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def _build_migration_source(root: Path) -> Path:
    """Synthetic snapshot: one planned legacy fact for ownerA/self in SG,
    plus conflicting-scope records that must never leak into ownerA's chat."""
    src = root / "snapshot"
    for ns in ("sg", "beta"):
        ns_dir = src / "worldlines" / ns
        ns_dir.mkdir(parents=True)
        for name, schema in (
            ("history.sqlite3", HISTORY_SCHEMA),
            ("memory.sqlite3", MEMORY_SCHEMA),
        ):
            conn = _open(ns_dir / name)
            try:
                conn.executescript(schema)
                conn.execute(
                    "INSERT OR REPLACE INTO schema_meta(key,value) "
                    "VALUES('schema_version','11')"
                )
                conn.commit()
            finally:
                conn.close()

    def _add_conversation(wl_ns, conv_id, session_id, identity_mode):
        conn = _open(src / "worldlines" / wl_ns / "history.sqlite3")
        try:
            conn.execute(
                "INSERT INTO conversations(id,session_id,title,title_source,is_default,identity_mode)"
                " VALUES(?,?,?,'auto',0,?)",
                (conv_id, session_id, "Conversation", identity_mode),
            )
            conn.commit()
        finally:
            conn.close()

    def _add_message(wl_ns, message_id, session_id, conv_id, content):
        conn = _open(src / "worldlines" / wl_ns / "history.sqlite3")
        try:
            conn.execute(
                "INSERT INTO messages(id,session_id,conversation_id,role,content)"
                " VALUES(?,?,?,'user',?)",
                (message_id, session_id, conv_id, content),
            )
            conn.commit()
        finally:
            conn.close()

    def _add_fact(wl_ns, session_id, identity_mode, key, value, sources):
        conn = _open(src / "worldlines" / wl_ns / "memory.sqlite3")
        try:
            conn.execute(
                "INSERT INTO core_facts(session_id,identity_mode,fact_key,fact_value,"
                "confidence,importance,is_current,is_pinned,is_dismissed,"
                "source_message_ids,created_at) VALUES(?,?,?,?,0.9,0.85,1,0,0,?,"
                "'2026-08-02T00:00:00+00:00')",
                (session_id, identity_mode, key, value, json.dumps(sources)),
            )
            conn.commit()
        finally:
            conn.close()

    _add_conversation("sg", "conv-owner-a", "ownerA", "self")
    _add_message("sg", 101, "ownerA", "conv-owner-a", "私はアニメが好きです。")
    _add_conversation("sg", "conv-owner-b", "ownerB", "self")
    _add_message("sg", 102, "ownerB", "conv-owner-b", "他人の会話です。")
    _add_conversation("beta", "conv-beta", "ownerBeta", "okabe")
    _add_message("beta", 101, "ownerBeta", "conv-beta", "ベータ世界線の内容です。")

    _add_fact("sg", "ownerA", "self", "likes_anime", "喜欢看动漫", [101])
    # Conflicting records: other owner, dismissed, other worldline.
    _add_fact("sg", "ownerB", "self", "other_owner_fact", "他人の秘密の事実", [102])
    _add_fact("sg", "ownerA", "self", "dismissed_fact", "已否定的旧事实", [101])
    conn = _open(src / "worldlines" / "sg" / "memory.sqlite3")
    try:
        conn.execute("UPDATE core_facts SET is_dismissed=1 WHERE fact_key='dismissed_fact'")
        conn.commit()
    finally:
        conn.close()
    _add_fact("beta", "ownerBeta", "okabe", "beta_fact", "β世界线事实", [101])
    return src


@pytest.mark.asyncio
async def test_migrated_legacy_import_reaches_chat_provider(tmp_path, monkeypatch):
    """A real offline-migration rehearsal target feeds the chat entry.

    The synthetic target is produced by the actual preview/apply tool (not
    by hand-writing v11 rows), then used as the chat data root: the imported
    legacy_import fact must reach the provider boundary for its owner while
    conflicting-scope records never appear.
    """
    root = tmp_path / "mig"
    src = _build_migration_source(root)
    preview = mig.build_legacy_core_facts_preview(src)
    planned = [i for i in preview["items"] if i["decision"] == "planned"]
    assert {(i["worldline"], i["fact_key"]) for i in planned} == {
        (SG, "likes_anime"), (SG, "other_owner_fact"), (BETA, "beta_fact")}
    target = root / "target"
    result = mig.apply_legacy_core_facts(
        source_dir=src, target_dir=target, preview=preview
    )
    assert result["ok"] is True

    # The rehearsal target layout matches the app data layout; init the rest.
    monkeypatch.setenv("AMADEUS_DATA_DIR", str(target))
    monkeypatch.setenv("AMADEUS_MEMORY_MODE", "v11")
    monkeypatch.delenv("AMADEUS_DB_PATH", raising=False)
    reset_initialization_cache()
    await init_db()

    capture = _ProviderCapture()
    _install_fake_provider(monkeypatch, capture)
    _suppress_auto_title(monkeypatch)

    session = _make_session("ownerA")
    await _prepare_conversation(session, identity_mode="self")
    events = await _run_turn(session, "动漫")

    assert _done(events) and not _errors(events), events
    system_prompt = capture.last_system_prompt
    assert "likes_anime: 喜欢看动漫" not in system_prompt
    assert "\nCORE FACTS\n" not in system_prompt
    from app.services.memory_v11.recall import MemoryScope, RecallRequest, recall_stable_facts

    recalled = await recall_stable_facts(
        scope=MemoryScope(session_id="ownerA", worldline=SG, identity_mode="self"),
        request=RecallRequest(needs=("the user's anime preference",)),
    )
    assert any("喜欢看动漫" in text for text in recalled.records.values()), recalled.records
    all_messages = json.dumps(capture.last_call["messages"], ensure_ascii=False)
    for forbidden in ("他人の秘密の事実", "已否定的旧事实", "β世界线事实"):
        assert forbidden not in all_messages, forbidden
    # The import really is a legacy_import row in the target DB.
    db = await get_db(SG, "memory")
    try:
        row = await (
            await db.execute(
                """SELECT v.change_kind FROM stable_facts f
                   JOIN stable_fact_versions v ON v.fact_id=f.fact_id
                   WHERE f.session_id='ownerA' AND f.identity_mode='self'"""
            )
        ).fetchone()
    finally:
        await db.close()
    assert row is not None and row["change_kind"] == "legacy_import"


# ---------------------------------------------------------------------------
# 4. Revision / deletion reflected in the next turn's Memory section.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_revision_and_deletion_reflected_next_turn(isolated_store, monkeypatch):
    from app.services.memory_v11.facts import delete_fact, user_edit_fact

    monkeypatch.setenv("AMADEUS_MEMORY_MODE", "v11")
    session_id = f"chat-rev-{uuid4()}"
    marker_v1 = f"REV_OLD_{uuid4().hex[:8]}"
    marker_v2 = f"REV_NEW_{uuid4().hex[:8]}"
    deleted_marker = f"DEL_GONE_{uuid4().hex[:8]}"

    facts = []
    for marker in (marker_v1, deleted_marker):
        await create_stable_fact(
            session_id=session_id, worldline=SG, identity_mode="self",
            display_text=f"{marker} 好きなアニメはこのすば",
            semantic_json={"subject": "user", "relation": "likes", "object": marker},
        )
    db = await get_db(SG, "memory")
    try:
        rows = await (
            await db.execute(
                "SELECT fact_id FROM stable_facts WHERE session_id=? AND identity_mode='self'",
                (session_id,),
            )
        ).fetchall()
        facts = [str(r["fact_id"]) for r in rows]
    finally:
        await db.close()
    assert len(facts) == 2

    capture = _ProviderCapture()
    _install_fake_provider(monkeypatch, capture)
    _suppress_auto_title(monkeypatch)

    session = _make_session(session_id)
    await _prepare_conversation(session, identity_mode="self")
    events = await _run_turn(session, "このすば")
    assert _done(events) and not _errors(events), events
    first_prompt = capture.last_system_prompt
    assert marker_v1 not in first_prompt and deleted_marker not in first_prompt
    from app.services.memory_v11.recall import MemoryScope, RecallRequest, recall_stable_facts

    scope = MemoryScope(session_id=session_id, worldline=SG, identity_mode="self")
    first = await recall_stable_facts(
        scope=scope, request=RecallRequest(needs=("好きなアニメ",))
    )
    blob = " ".join(first.records.values())
    assert marker_v1 in blob and deleted_marker in blob

    await user_edit_fact(
        session_id=session_id, worldline=SG, identity_mode="self",
        fact_id=facts[0], display_text=f"{marker_v2} 特にこのすばが好き",
        expected_version=1, embedder=_StubEmbedder(),
    )
    await delete_fact(
        session_id=session_id, worldline=SG, identity_mode="self",
        fact_id=facts[1], expected_version=1,
    )

    events = await _run_turn(session, "このすば")
    assert _done(events) and not _errors(events), events
    next_prompt = capture.last_system_prompt
    assert marker_v2 not in next_prompt
    later = await recall_stable_facts(
        scope=scope, request=RecallRequest(needs=("好きなアニメ",))
    )
    later_blob = " ".join(later.records.values())
    assert marker_v2 in later_blob, "the new active version must be used"
    assert marker_v1 not in later_blob, "the superseded version must not persist"
    assert deleted_marker not in later_blob, "deleted content must leave recall"


# ---------------------------------------------------------------------------
# 5. Fact selector failure at the entry level: the turn survives.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fact_selector_failure_turn_survives_with_fresh_prompt(
    isolated_store, monkeypatch
):
    """Entry-level D52 proof: with a poisoned session.system_prompt from a
    previous turn, a failing Fact selector must still yield a fresh, fully
    compiled prompt without the failed Facts and without the stale prompt,
    complete the turn, and recover on the next turn."""
    import app.services.memory_v11.retrieval as retrieval_module

    monkeypatch.setenv("AMADEUS_MEMORY_MODE", "v11")
    session_id = f"chat-fail-{uuid4()}"
    fact_marker = f"FAIL_FACT_{uuid4().hex[:8]}"
    stale_marker = f"STALE_PROMPT_{uuid4().hex[:8]}"
    await _seed_v11_fact(session_id, fact_marker)

    capture = _ProviderCapture()
    _install_fake_provider(monkeypatch, capture)
    _suppress_auto_title(monkeypatch)

    session = _make_session(session_id)
    await _prepare_conversation(session, identity_mode="self")
    # Realistic post-turn-1 state: a clean client base plus a previous turn's
    # fully compiled system_prompt that carries the old marker. A broken
    # implementation would resend the stale prompt; the fixed one recompiles
    # from the clean base with fresh Memory sections.
    session.base_system_prompt = "You are Amadeus Kurisu."
    session.system_prompt = (
        f"IDENTITY AND SECURITY\n{stale_marker} 前のターンの古いプロンプト。\n\n"
        f"CORE FACTS\n- {stale_marker} 古い記憶"
    )

    async def boom(*args, **kwargs):
        raise RuntimeError("forced fact selector failure")

    with pytest.MonkeyPatch.context() as selector_patch:
        selector_patch.setattr(
            retrieval_module, "select_stable_fact_candidates", boom
        )
        events = await _run_turn(session, "今日の調子はどう？")

    assert _done(events), events
    assert not _errors(events), f"turn must not fail: {events}"
    assert len(capture.chat_calls) == 1
    system_prompt = capture.last_system_prompt
    assert stale_marker not in system_prompt, "must not reuse the previous system_prompt"
    assert fact_marker not in system_prompt, "the failed Fact must be omitted"
    assert "\nCORE FACTS\n" not in system_prompt, "v11 first round omits CORE FACTS"
    # The provider really got the current user message through real history.
    user_texts = [
        str(m.get("content")) for m in capture.last_call["messages"]
        if m.get("role") == "user"
    ]
    assert any("今日の調子はどう？" in t for t in user_texts)

    events = await _run_turn(session, "このすば")
    assert _done(events) and not _errors(events), events
    assert fact_marker not in capture.last_system_prompt
    assert "recall_memory" in json.dumps(capture.last_call.get("tools") or [], ensure_ascii=False)


@pytest.mark.asyncio
async def test_experience_selector_failure_keeps_facts(isolated_store, monkeypatch):
    """Existing experience fail-soft semantics stay intact at the entry level."""
    import app.services.memory_v11.retrieval as retrieval_module

    monkeypatch.setenv("AMADEUS_MEMORY_MODE", "v11")
    session_id = f"chat-expfail-{uuid4()}"
    fact_marker = f"EXPFAIL_FACT_{uuid4().hex[:8]}"
    await _seed_v11_fact(session_id, fact_marker)

    capture = _ProviderCapture()
    _install_fake_provider(monkeypatch, capture)
    _suppress_auto_title(monkeypatch)

    async def boom(*args, **kwargs):
        raise RuntimeError("forced experience selector failure")

    monkeypatch.setattr(retrieval_module, "select_experience_candidates", boom)
    session = _make_session(session_id)
    await _prepare_conversation(session, identity_mode="self")
    events = await _run_turn(session, "このすば")
    assert _done(events) and not _errors(events), events
    assert fact_marker not in capture.last_system_prompt
    assert "\nCORE FACTS\n" not in capture.last_system_prompt


# ---------------------------------------------------------------------------
# 6. Voice entry: fake STT -> same processor/prompt/provider boundary.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_voice_entry_reaches_same_boundary(isolated_store, monkeypatch):
    from app.routers import chat_ws, voice_ws  # noqa: F401 (real modules under test)
    from app.services.speech import speech_service

    session_id = f"voice-{uuid4()}"
    voice_marker = f"VOICE_FACT_{uuid4().hex[:8]}"
    # The voice endpoint loads the selected (default okabe) conversation and
    # the processor re-syncs identity from it, so the fact lives in okabe scope.
    await _seed_v11_fact(session_id, voice_marker, identity_mode="okabe")

    monkeypatch.setenv("AMADEUS_MEMORY_MODE", "v11")

    async def fake_transcribe_pcm(pcm, sample_rate, channels):
        return "このすば", "neutral", 0.99

    async def fake_has_speech(chunk, sample_rate, threshold):
        return True

    monkeypatch.setattr(speech_service, "transcribe_pcm", fake_transcribe_pcm)
    monkeypatch.setattr(speech_service, "has_speech", fake_has_speech)

    capture = _ProviderCapture()
    _install_fake_provider(monkeypatch, capture)
    _suppress_auto_title(monkeypatch)
    # Keep the background memory worker from making noisy provider calls.
    import app.services.memory_v11.jobs as jobs_module

    async def fake_memory_completion(**kwargs):
        return {"observations": [], "operations": []}

    monkeypatch.setattr(jobs_module, "default_memory_completion", fake_memory_completion)

    # Pre-create the session with TTS disabled so the voice turn stays local
    # (text chunks only); the endpoint reuses it from chat_ws.sessions.
    session = _make_session(session_id)
    chat_ws.sessions[session_id] = session

    from fastapi.testclient import TestClient
    from app.main import app as fastapi_app

    received: list[dict] = []
    with patch(
        "app.main.sidecar_supervisor.ensure_available",
        new=AsyncMock(return_value=SimpleNamespace(url=None)),
    ), patch("app.main.sidecar_supervisor.close", new=AsyncMock()):
        with TestClient(fastapi_app, base_url="http://localhost", headers={"host": "localhost", "origin": "http://localhost:1420"}) as client, client.websocket_connect(
            f"/ws/voice?session_id={session_id}&worldline=steins_gate"
        ) as ws:
            ws.send_json({"type": "auth"})
            ready = ws.receive_json()
            assert ready.get("type") == "voice.ready"
            ws.send_json({"type": "voice.start", "sample_rate": 16000, "channels": 1})
            ws.send_bytes(b"\x00\x01" * 5000)  # > VAD_MIN_SPEECH_MS of PCM
            ws.send_json({"type": "voice.commit"})
            deadline = time.monotonic() + 15.0
            while time.monotonic() < deadline:
                message = ws.receive_json()
                received.append(message)
                if message.get("type") == "stt.final":
                    assert message.get("content") == "このすば"
                if (
                    message.get("type") == "status"
                    and message.get("state") == "done"
                ):
                    break

    types = [m.get("type") for m in received]
    assert "stt.final" in types
    assert "error" not in types, received
    # The fake-STT text went through the SAME processor: exactly one provider
    # chat request whose freshly compiled prompt carries the voice-scoped fact.
    assert len(capture.chat_calls) == 1
    assert voice_marker not in capture.last_system_prompt
    assert "\nCORE FACTS\n" not in capture.last_system_prompt
    assert "このすば" in json.dumps(
        capture.last_call["messages"], ensure_ascii=False
    )


@pytest.mark.asyncio
async def test_v2_user_disabled_tts_emits_user_disabled_not_unbound(
    isolated_store, monkeypatch
):
    from app.domain.segments import AudioErrorCode
    from app.routers.chat_ws import processor_loop

    assert "AudioErrorCode" not in processor_loop.__code__.co_varnames

    monkeypatch.setenv("AMADEUS_MEMORY_MODE", "v11")
    capture = _ProviderCapture()
    _install_fake_provider(monkeypatch, capture)
    _suppress_auto_title(monkeypatch)
    _record_legacy_extraction(monkeypatch)

    session = _make_session(f"v2-tts-bind-{uuid4()}")
    session.protocol_version = 2
    session.enable_tts = False
    await _prepare_conversation(session)
    events = await _run_turn(session, "こんにちは")

    ready = [event for event in events if event.get("type") == "segment.ready"]
    assert ready, events
    assert any(str(event.get("ja") or "").strip() for event in ready)
    audio_errors = [
        event for event in events if event.get("type") == "segment.audio_error"
    ]
    assert audio_errors, events
    for event in audio_errors:
        reason = str(event.get("reason") or "")
        assert "free variable" not in reason
        assert "UnboundLocal" not in reason
        assert reason == AudioErrorCode.USER_DISABLED.value
