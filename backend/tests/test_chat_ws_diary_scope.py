import asyncio
from threading import Event
from unittest.mock import AsyncMock

import pytest

from app import models
from app.routers import chat_ws
from app.services import prompt_compiler
from test_chat_ws_turn_queue import BETA, SG, auth, conv, history, receive, select, shift, submit
from test_chat_ws_turn_queue import chat as chat


@pytest.mark.timeout(40)
def test_background_history_trim_recompiles_its_own_diaries_and_leaves_foreground_prompt(chat, monkeypatch):
    if not hasattr(prompt_compiler, "recompile_session_diaries"):
        pytest.skip("Requires the diary generation integration.")
    from app.services.memory_v11 import diary_generation

    client, owner, provider, *_ = chat
    a = conv(client, owner, identity_mode="self")
    b = conv(client, owner, BETA, identity_mode="okabe")
    origin_scope, foreground_scope = (SG, "self", a), (BETA, "okabe", b)
    first_pass, proceed = Event(), Event()
    selected = []
    origin, foreground = provider.gate("観測の続きを教えて"), provider.gate("こちらの話を続けよう")

    async def seed():
        return [
            await models.save_message(
                owner, "user" if index % 2 == 0 else "assistant",
                "観測記録を確認してから実験する。" * 900,
                conversation_id=a,
            )
            for index in range(8)
        ]

    older_ids = client.portal.call(seed)

    async def select_diaries(**context):
        scope = (context["worldline"], context["identity_mode"], context["conversation_id"])
        ids = set(context["sent_message_ids"])
        selected.append((scope, ids))
        if scope == origin_scope:
            diary = "全部の記録に対応する手記。" if older_ids[0] in ids else "裁剪後にも残る赤い手記。"
        else:
            diary = "前台で使う青い手記。"
        return [prompt_compiler.PromptDiary(diary, diary)]

    async def pause_after_compile(state, user_message, **kwargs):
        compiled = await prompt_compiler.compile_for_session(state, user_message, **kwargs)
        if user_message == "観測の続きを教えて":
            first_pass.set()
            while not proceed.is_set():
                await asyncio.sleep(0.005)
        return compiled

    diary_sync = AsyncMock()
    monkeypatch.setattr(prompt_compiler, "_select_v11_prompt_diaries", select_diaries)
    monkeypatch.setattr(chat_ws, "compile_for_session", pause_after_compile)
    monkeypatch.setattr(chat_ws, "effective_request_budget", lambda *_: (12000, 2048))
    monkeypatch.setattr(diary_generation, "sync_conversation_diary_windows", diary_sync)

    with client.websocket_connect(f"/ws/chat?session_id={owner}") as socket:
        ws = auth(socket, a)
        submit(ws, "観測の続きを教えて")
        assert first_pass.wait(5)
        shift(ws, BETA)
        select(ws, b)
        submit(ws, "こちらの話を続けよう")
        assert foreground.started.wait(5)
        view = chat_ws.sessions[owner]
        front_state = view.turn_queue.active().state
        front_prompt = (front_state.system_prompt, front_state.turn_compiled_prompt)
        proceed.set()
        assert origin.started.wait(5)
        assert selected[0] == (origin_scope, set(older_ids))
        assert [scope for scope, _ in selected] == [origin_scope, foreground_scope, origin_scope]
        assert older_ids[0] not in selected[-1][1]
        assert "裁剪後にも残る赤い手記。" in origin.system_prompt
        assert "前台で使う青い手記。" in foreground.system_prompt
        assert (front_state.system_prompt, front_state.turn_compiled_prompt) == front_prompt
        origin.finish.set()
        receive(ws, "turn.history_saved", conversation_id=a)
        saved_reply = history(client, owner, a)[-1]
        assert diary_sync.await_args.kwargs == {
            "session_id": owner, "worldline": SG, "identity_mode": "self",
            "conversation_id": a, "through_message_id": saved_reply["id"],
        }
        foreground.finish.set()
        receive(ws, "turn.completed", conversation_id=b)
