import asyncio
from contextlib import ExitStack, closing
from dataclasses import dataclass, field
import json
import sqlite3
from threading import Event
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from app import db, models
from app.routers import chat_ws
from app.services import voice_cache
from app.services.conversations import conversation_service
from app.services.memory_v11 import jobs
from app.services.model_usage import usage_call
from test_voice_cache_replay import _wav


pytestmark = pytest.mark.timeout(45)
SG, BETA = "steins_gate", "beta"
REPLY = "最初の結果よ。次の手順も確認したわ。"


@dataclass
class ReplyGate:
    started: Event = field(default_factory=Event)
    finish: Event = field(default_factory=Event)
    cancelled: Event = field(default_factory=Event)
    history: list = field(default_factory=list)
    model: str = ""
    key: str = ""
    system_prompt: str = ""
    prefix: str = ""


class Provider:
    def __init__(self):
        self.replies = {}
        self.translator = AsyncMock(return_value="译文")

    def gate(self, text):
        return self.replies.setdefault(text, ReplyGate())

    async def get_chat_stream(self, active_history, model, api_key, **kwargs):
        user_text = [message["content"] for message in active_history if message["role"] == "user"][
            -1
        ]
        gate = self.replies[user_text]
        gate.history = active_history
        gate.model, gate.key = model, api_key
        gate.system_prompt = kwargs.get("system_prompt", "")
        gate.started.set()
        async with usage_call("deepseek", model) as usage:
            usage.started = True
            if gate.prefix:
                yield {"content": gate.prefix, "tool_calls": None}
            try:
                while not gate.finish.is_set():
                    await asyncio.sleep(0.005)
            except asyncio.CancelledError:
                gate.cancelled.set()
                raise
            usage.observe({"usage": {"prompt_tokens": 8, "completion_tokens": 5}})
            yield {"content": REPLY.removeprefix(gate.prefix), "tool_calls": None}

    async def translate_to_zh(self, text, api_key=None, **kwargs):
        return await self.translator(text, api_key)


@pytest.fixture
def chat(app_client, monkeypatch):
    provider = Provider()
    voice = AsyncMock(return_value=_wav(53))
    title = Mock()
    extraction = AsyncMock()
    monkeypatch.setattr(chat_ws, "deepseek_service", provider)
    monkeypatch.setattr(chat_ws, "compile_for_session", AsyncMock(return_value=""))
    monkeypatch.setattr(chat_ws.tts_manager, "synthesize_async", voice)
    monkeypatch.setattr(conversation_service, "schedule_auto_title", title)
    monkeypatch.setattr(jobs, "enqueue_memory_job", extraction)
    owner = f"queue-{uuid4()}"
    yield app_client, owner, provider, voice, title, extraction
    chat_ws.sessions.pop(owner, None)


def conv(client, owner, worldline=SG, **fields):
    response = client.post(
        "/api/conversations", json={"session_id": owner, "worldline": worldline, **fields}
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def params(owner, worldline=SG):
    return {"session_id": owner, "worldline": worldline}


def history(client, owner, cid, worldline=SG):
    response = client.get(f"/api/conversations/{cid}/messages", params=params(owner, worldline))
    assert response.status_code == 200, response.text
    return response.json()


def receive(ws, kind, **fields):
    async def read():
        async with asyncio.timeout(7):
            while True:
                message = await ws._send_rx.receive()
                assert message["type"] == "websocket.send", message
                frame = json.loads(message["text"])
                assert frame["type"] != "error", frame
                frames.append(frame)
                if frame["type"] == kind and all(
                    frame.get(key) == value for key, value in fields.items()
                ):
                    return frames

    frames = []
    return ws.portal.call(read)


def auth(ws, cid, *, tts=False):
    ws.send_json(
        {
            "type": "auth",
            "protocol_version": 2,
            "client": "desktop",
            "conversation_id": cid,
            "conversation_mode": "history",
            "enable_tts": tts,
            "worldline": SG,
        }
    )
    receive(ws, "session.ready", conversation_id=cid)
    return ws


def select(ws, cid):
    ws.send_json({"type": "conversation.switch", "conversation_id": cid, "request_id": cid})
    return receive(ws, "conversation.switched", conversation_id=cid)


def shift(ws, worldline):
    ws.send_json(
        {"type": "worldline.switch", "target_worldline": worldline, "conversation_mode": "draft"}
    )
    return receive(ws, "worldline.switched", worldline=worldline)


def submit(ws, text, *, queued=False):
    ws.send_json({"type": "chat.send", "content": text})
    if queued:
        return receive(ws, "turn.accepted", queued=True)[-1]
    return receive(ws, "turn.started")[-1]


def test_background_saves_reply_and_followups_without_audio(chat):
    client, owner, provider, voice, title, extraction = chat
    a, b = conv(client, owner), conv(client, owner)
    gate = provider.gate("第一問")
    with client.websocket_connect(f"/ws/chat?session_id={owner}") as socket:
        ws = auth(socket, a, tts=True)
        turn = submit(ws, "第一問")
        assert gate.started.wait(5)
        select(ws, b)
        gate.finish.set()
        frames = receive(ws, "turn.history_saved", conversation_id=a, turn_id=turn["turn_id"])
        assert not any(frame["type"] == "segment.audio" for frame in frames)
        receive(ws, "conversation.turn_state", conversation_id=a, running=False)
        rows = history(client, owner, a)
        assert [row["role"] for row in rows] == ["user", "assistant"]
        assert rows[-1]["content"].endswith(REPLY)
        assert rows[-1]["turn_id"] == turn["turn_id"] and rows[-1]["id"] > rows[0]["id"]
        voice.assert_not_awaited()
        assert not (voice_cache.voice_cache_root() / a).exists()
        assert extraction.await_args.kwargs["conversation_id"] == a
        assert title.call_args.args[:3] == (a, owner, SG)


def test_same_worldline_fifo_withdraws_only_the_requested_turn(chat):
    client, owner, provider, *_ = chat
    a, b, c, d = [conv(client, owner) for _ in range(4)]
    first, second, third, withdrawn = map(
        provider.gate, ("一問目", "二問目", "三問目", "撤回する文")
    )
    with client.websocket_connect(f"/ws/chat?session_id={owner}") as socket:
        ws = auth(socket, a)
        submit(ws, "一問目")
        assert first.started.wait(5)
        select(ws, b)
        submit(ws, "二問目", queued=True)
        select(ws, d)
        undone = submit(ws, "撤回する文", queued=True)
        select(ws, c)
        submit(ws, "三問目", queued=True)
        assert not second.started.is_set() and not third.started.is_set()
        assert history(client, owner, b) == []
        ws.send_json({"type": "turn.withdraw", "task_id": undone["task_id"]})
        receive(ws, "conversation.turn_state", conversation_id=d, running=False, queued=[])
        first.finish.set()
        assert second.started.wait(5)
        assert not third.started.is_set() and not withdrawn.started.is_set()
        second.finish.set()
        assert third.started.wait(5)
        third.finish.set()
        receive(ws, "turn.completed", conversation_id=c)
        assert not withdrawn.started.is_set()
        assert history(client, owner, d) == []


def test_queued_draft_binds_at_enqueue_and_uses_latest_history_without_selecting_it(chat):
    client, owner, provider, *_ = chat
    a, c = conv(client, owner), conv(client, owner)
    first, draft = provider.gate("先に送る文"), provider.gate("草稿で送る文")
    with client.websocket_connect(f"/ws/chat?session_id={owner}") as socket:
        ws = auth(socket, a)
        submit(ws, "先に送る文")
        assert first.started.wait(5)
        shift(ws, SG)
        b = submit(ws, "草稿で送る文", queued=True)["conversation_id"]
        assert b not in {a, c}
        selected = client.portal.call(conversation_service.get_selected, owner, SG)
        assert selected["id"] == b
        assert history(client, owner, b) == []
        select(ws, c)
        asyncio.run(models.save_message(owner, "user", "待機中に保存した文", conversation_id=b))
        first.finish.set()
        assert draft.started.wait(5)
        assert any(row.get("content") == "待機中に保存した文" for row in draft.history)
        draft.finish.set()
        receive(ws, "turn.history_saved", conversation_id=b)
        assert client.portal.call(conversation_service.get_selected, owner, SG)["id"] == c
        assert history(client, owner, c) == []
        assert [row["role"] for row in history(client, owner, b)] == ["user", "user", "assistant"]


def test_replacing_socket_cancels_both_worldlines_and_all_queued_turns(chat):
    client, owner, provider, *_ = chat
    a, c = conv(client, owner), conv(client, owner)
    b, d = conv(client, owner, BETA), conv(client, owner, BETA)
    active_a, active_b, queued_a, queued_b, newest = map(
        provider.gate, ("甲", "乙", "丙", "丁", "次の質問")
    )
    with ExitStack() as sockets:
        old_socket = sockets.enter_context(ExitStack())
        old = auth(
            old_socket.enter_context(client.websocket_connect(f"/ws/chat?session_id={owner}")), a
        )
        submit(old, "甲")
        assert active_a.started.wait(5)
        select(old, c)
        submit(old, "丙", queued=True)
        shift(old, BETA)
        select(old, b)
        submit(old, "乙")
        assert active_b.started.wait(5)
        select(old, d)
        submit(old, "丁", queued=True)

        new = auth(
            sockets.enter_context(client.websocket_connect(f"/ws/chat?session_id={owner}")), c
        )
        assert active_a.cancelled.wait(5) and active_b.cancelled.wait(5)
        submit(new, "次の質問")
        assert newest.started.wait(5)
        old_socket.close()
        newest.finish.set()
        receive(new, "turn.completed", conversation_id=c)
        assert not newest.cancelled.is_set()
        assert not queued_a.started.is_set() and not queued_b.started.is_set()
        assert history(client, owner, d, BETA) == []
        assert [row["content"] for row in history(client, owner, c) if row["role"] == "user"] == [
            "次の質問"
        ]


def test_worldlines_generate_concurrently(chat):
    client, owner, provider, *_ = chat
    a, b = conv(client, owner), conv(client, owner, BETA)
    first, second = provider.gate("α の質問"), provider.gate("β の質問")
    with client.websocket_connect(f"/ws/chat?session_id={owner}") as socket:
        ws = auth(socket, a)
        submit(ws, "α の質問")
        assert first.started.wait(5)
        shift(ws, BETA)
        select(ws, b)
        submit(ws, "β の質問")
        assert second.started.wait(5)
        second.finish.set()
        receive(ws, "turn.completed", conversation_id=b)
        assert [row["role"] for row in history(client, owner, a)] == ["user"]
        first.finish.set()
        receive(ws, "turn.history_saved", conversation_id=a)
        assert history(client, owner, b, BETA)[-1]["role"] == "assistant"


def test_switch_back_to_running_turn_gets_one_complete_saved_reply(chat):
    client, owner, provider, *_ = chat
    a, b = conv(client, owner), conv(client, owner)
    gate = provider.gate("確認したいこと")
    gate.prefix = "最初の結果よ。"
    with client.websocket_connect(f"/ws/chat?session_id={owner}") as socket:
        ws = auth(socket, a)
        accepted = submit(ws, "確認したいこと")
        receive(ws, "segment.ready", ja=gate.prefix)
        select(ws, b)
        select(ws, a)
        assert [row["role"] for row in history(client, owner, a)] == ["user"]
        gate.finish.set()
        frames = receive(ws, "conversation.turn_state", conversation_id=a, running=False)
        assert [frame["type"] for frame in frames].count("turn.history_saved") == 1
        assert not any(frame["type"] in {"turn.started", "segment.ready"} for frame in frames)
        rows = history(client, owner, a)
        assert [row["turn_id"] for row in rows] == [accepted["turn_id"]] * 2
        assert rows[-1]["content"].endswith(REPLY)


@pytest.mark.parametrize("operation", ["delete", "forget"])
def test_erase_cancels_target_running_and_queued_turns(chat, operation):
    client, owner, provider, *_ = chat
    a, b = conv(client, owner), conv(client, owner)
    running, queued = provider.gate("消す文"), provider.gate("待機する文")
    with client.websocket_connect(f"/ws/chat?session_id={owner}") as socket:
        ws = auth(socket, a)
        submit(ws, "消す文")
        assert running.started.wait(5)
        submit(ws, "待機する文", queued=True)
        select(ws, b)
        target = f"/api/conversations/{a}"
        if operation == "delete":
            result = client.delete(target, params=params(owner))
            assert result.status_code == 204
            assert client.get(f"{target}/messages", params=params(owner)).status_code == 404
        else:
            result = client.post(
                f"{target}/forget", params=params(owner), json={"forget_long_term": True}
            )
            assert result.status_code == 200, result.text
        assert running.cancelled.wait(5) and not queued.started.is_set()
        with closing(sqlite3.connect(db._resolve_path(SG, "history"))) as database:
            assert database.execute(
                "SELECT COUNT(*) FROM messages WHERE conversation_id = ?", (a,)
            ).fetchone() == (0,)


def test_running_turn_freezes_model_and_credentials_until_translation_finishes(
    chat, isolated_provider_credentials
):
    client, owner, provider, *_ = chat
    a, b = (
        conv(client, owner, model_id="deepseek-flash"),
        conv(client, owner, model_id="deepseek-v4-pro"),
    )
    first, second = provider.gate("最初の設定"), provider.gate("次の設定")
    isolated_provider_credentials.set("deepseek", "old-stub")
    with client.websocket_connect(f"/ws/chat?session_id={owner}") as socket:
        ws = auth(socket, a)
        submit(ws, "最初の設定")
        assert first.started.wait(5)
        select(ws, b)
        isolated_provider_credentials.set("deepseek", "new-stub")
        submit(ws, "次の設定", queued=True)
        first.finish.set()
        receive(ws, "turn.history_saved", conversation_id=a)
        assert second.started.wait(5)
        second.finish.set()
        receive(ws, "turn.completed", conversation_id=b)
    assert (first.model, first.key) == ("deepseek-flash", "old-stub")
    assert (second.model, second.key) == ("deepseek-v4-pro", "new-stub")
    translation_keys = [call.args[1] for call in provider.translator.await_args_list]
    assert translation_keys[0] == "old-stub" and translation_keys[-1] == "new-stub"


def test_background_usage_is_persisted_to_its_frozen_conversation(chat):
    client, owner, provider, *_ = chat
    a = conv(client, owner)
    gate = provider.gate("用量を記録する文")
    with client.websocket_connect(f"/ws/chat?session_id={owner}") as socket:
        ws = auth(socket, a)
        submit(ws, "用量を記録する文")
        assert gate.started.wait(5)
        shift(ws, BETA)
        gate.finish.set()
        receive(ws, "turn.history_saved", conversation_id=a)
        with closing(sqlite3.connect(db._resolve_path(SG, "control"))) as database:
            row = database.execute(
                "SELECT worldline, conversation_id, category, input_tokens FROM model_usage"
            ).fetchone()
        assert row == (SG, a, "chat", 8)


def test_truncate_returns_busy_for_hidden_and_queued_conversations(chat):
    client, owner, provider, *_ = chat
    a, b = conv(client, owner), conv(client, owner)
    gate = provider.gate("回覆中")
    first = asyncio.run(models.save_message(owner, "user", "旧文", conversation_id=b))
    with client.websocket_connect(f"/ws/chat?session_id={owner}") as socket:
        ws = auth(socket, a)
        submit(ws, "回覆中")
        assert gate.started.wait(5)
        select(ws, b)
        submit(ws, "待つ文", queued=True)
        active_row = history(client, owner, a)[0]["id"]
        for cid, cut in ((a, active_row), (b, first)):
            result = client.post(
                f"/api/conversations/{cid}/truncate",
                params=params(owner),
                json={"from_message_id": cut, "expected_last_message_id": cut},
            )
            assert result.status_code == 409
            assert result.json()["detail"]["code"] == "conversation_busy"


def test_cancel_before_start_does_not_write_the_user_line(chat, monkeypatch):
    client, owner, provider, *_ = chat
    a = conv(client, owner)
    gate = provider.gate("開始前に止める文")
    entered, release = Event(), Event()
    original = chat_ws.ChatTurnQueue.snapshot

    async def pause_snapshot(queue, turn, record):
        entered.set()
        while not release.is_set():
            await asyncio.sleep(0.005)
        return await original(queue, turn, record)

    monkeypatch.setattr(chat_ws.ChatTurnQueue, "snapshot", pause_snapshot)
    with client.websocket_connect(f"/ws/chat?session_id={owner}") as socket:
        ws = auth(socket, a)
        ws.send_json({"type": "chat.send", "content": "開始前に止める文"})
        assert entered.wait(5)
        ws.send_json({"type": "turn.cancel"})
        receive(ws, "turn.cancelled", conversation_id=a)
        receive(ws, "status", state="cancelled")
        runner = chat_ws.sessions[owner].turn_queue.runners[SG]
        release.set()

        async def finish():
            await runner

        client.portal.call(finish)
        assert not gate.started.is_set()
        assert history(client, owner, a) == []


def test_completion_keeps_summary_invalidation_during_reply(chat, monkeypatch):
    from app.services.session_manager import clear_working_summaries

    client, owner, provider, *_ = chat
    cid = conv(client, owner)
    gate = provider.gate("要約を確認する")
    summaries = []

    async def record_compression(view, *_args, **_kwargs):
        summaries.append((view.memory_summary, view.content_epoch))

    compression = AsyncMock(side_effect=record_compression)
    monkeypatch.setattr(chat_ws, "schedule_history_compression", compression)
    with client.websocket_connect(f"/ws/chat?session_id={owner}") as socket:
        ws = auth(socket, cid)
        view = chat_ws.sessions[owner]
        view.memory_summary = "OLD-SUMMARY-WITH-DELETED-FACT"
        submit(ws, "要約を確認する")
        assert gate.started.wait(5)
        previous_epoch = view.content_epoch
        client.portal.call(clear_working_summaries, owner, SG, None)
        assert view.content_epoch > previous_epoch and view.memory_summary == ""
        gate.finish.set()
        receive(ws, "turn.completed", conversation_id=cid)
        runner = view.turn_queue.runners.get(SG)
        if runner is not None:
            async def finish():
                await runner

            client.portal.call(finish)
        assert view.memory_summary == ""
        assert view.content_epoch > previous_epoch
        assert view.history[-1]["role"] == "assistant"
        compression.assert_awaited_once()
        assert summaries == [("", view.content_epoch)]


@pytest.mark.parametrize(("worldline", "operation"), [(SG, "forget"), (BETA, "delete")])
def test_erasing_visible_conversation_keeps_other_queued_turn(chat, monkeypatch, worldline, operation):
    from app.routers import conversations

    client, owner, provider, *_ = chat
    visible = conv(client, owner)
    first, queued = conv(client, owner, worldline), conv(client, owner, worldline)
    earlier, later = provider.gate("先の観測"), provider.gate("残す観測")
    erasing, release = Event(), Event()
    original = getattr(conversation_service, operation)

    async def hold_erasure(*args, **kwargs):
        erasing.set()
        while not release.is_set():
            await asyncio.sleep(0.005)
        return await original(*args, **kwargs)

    monkeypatch.setattr(conversation_service, operation, hold_erasure)
    with client.websocket_connect(f"/ws/chat?session_id={owner}") as socket:
        ws = auth(socket, visible)
        if worldline == BETA:
            shift(ws, BETA)
        select(ws, first)
        submit(ws, "先の観測")
        assert earlier.started.wait(5)
        select(ws, queued)
        submit(ws, "残す観測", queued=True)
        if worldline == BETA:
            shift(ws, SG)
        select(ws, visible)
        if operation == "forget":
            erasure = client.portal.start_task_soon(
                conversations.forget_conversation, visible, conversations.ConversationForget(), owner, SG,
            )
        else:
            erasure = client.portal.start_task_soon(
                conversations.delete_conversation, visible, owner, SG, False,
            )
        try:
            assert erasing.wait(5)
            earlier.finish.set()
            assert later.started.wait(5)
            later.finish.set()
            receive(ws, "turn.history_saved", conversation_id=queued)
        finally:
            release.set()
            erasure.result(timeout=7)
        assert [row["role"] for row in history(client, owner, queued, worldline)] == ["user", "assistant"]


@pytest.mark.parametrize("action", ["turn.cancel", "turn.withdraw"])
def test_queued_head_can_be_stopped_during_snapshot(chat, monkeypatch, action):
    client, owner, provider, *_ = chat
    a, b = conv(client, owner), conv(client, owner)
    active, later = provider.gate("先に答える"), provider.gate("待機中に止める")
    entered, release, withdrawing = Event(), Event(), Event()
    original_snapshot = chat_ws.ChatTurnQueue.snapshot
    original_withdraw = chat_ws.ChatTurnQueue.withdraw

    async def pause_snapshot(queue, turn, record):
        if turn.conversation_id == b:
            entered.set()
            while not release.is_set():
                await asyncio.sleep(0.005)
        return await original_snapshot(queue, turn, record)

    async def observe_withdraw(*args):
        withdrawing.set()
        await original_withdraw(*args)

    monkeypatch.setattr(chat_ws.ChatTurnQueue, "snapshot", pause_snapshot)
    monkeypatch.setattr(chat_ws.ChatTurnQueue, "withdraw", observe_withdraw)
    with client.websocket_connect(f"/ws/chat?session_id={owner}") as socket:
        ws = auth(socket, a)
        submit(ws, "先に答える")
        assert active.started.wait(5)
        select(ws, b)
        queued = submit(ws, "待機中に止める", queued=True)
        active.finish.set()
        try:
            assert entered.wait(5)
            if action == "turn.cancel":
                ws.send_json({"type": action})
                receive(ws, "status", state="cancelled")
            else:
                ws.send_json({"type": action, "task_id": queued["task_id"]})
                assert withdrawing.wait(5)
        finally:
            later.finish.set()
            release.set()
        receive(ws, "conversation.turn_state", conversation_id=b, running=False, queued=[])
        assert not later.started.is_set()
        assert history(client, owner, b) == []


def test_turn_starting_in_background_uses_target_identity_and_stays_silent(chat):
    client, owner, provider, voice, _, extraction = chat
    a = conv(client, owner)
    b = conv(client, owner, identity_mode="self")
    c = conv(client, owner, identity_mode="okabe")
    first, background = provider.gate("先に返事する"), provider.gate("静かに返事する")
    with client.websocket_connect(f"/ws/chat?session_id={owner}") as socket:
        ws = auth(socket, a, tts=True)
        submit(ws, "先に返事する")
        assert first.started.wait(5)
        select(ws, b)
        submit(ws, "静かに返事する", queued=True)
        select(ws, c)
        first.finish.set()
        assert background.started.wait(5)
        background.finish.set()
        receive(ws, "turn.history_saved", conversation_id=b)
        receive(ws, "conversation.turn_state", conversation_id=b, running=False)
        assert history(client, owner, b)[-1]["content"].endswith(REPLY)
        scoped = [call.kwargs for call in extraction.await_args_list if call.kwargs["conversation_id"] == b]
        assert [kwargs["identity_mode"] for kwargs in scoped] == ["self"]
        voice.assert_not_awaited()
        assert not (voice_cache.voice_cache_root() / b).exists()
