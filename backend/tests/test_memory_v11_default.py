"""v11 is the only memory mode: worker, turn ingest, prompt read path, routes."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.db import init_db, reset_initialization_cache
from app.services.memory_v11.repository import create_stable_fact

SG = "steins_gate"


@pytest.fixture
async def isolated_store(tmp_path, monkeypatch):
    monkeypatch.delenv("AMADEUS_DB_PATH", raising=False)
    monkeypatch.setenv("AMADEUS_DATA_DIR", str(tmp_path))
    reset_initialization_cache()
    await init_db()
    yield tmp_path
    reset_initialization_cache()


def test_app_startup_runs_v11_worker(app_client):
    from app.services.memory_v11 import jobs

    assert app_client.get("/health").status_code == 200
    assert jobs._worker_task is not None
    assert not jobs._worker_task.done()


async def test_turn_enqueues_v11_job_without_legacy_extraction(isolated_store, monkeypatch):
    import app.services.memory_v11.jobs as jobs
    from app.routers import chat_ws
    from app.routers.chat_ws import SessionState, processor_loop, sessions
    from app.services.conversations import conversation_service
    from app.services.memory import memory_service

    class FakeDeepSeek:
        async def get_chat_stream(self, *args, **kwargs):
            yield {"content": "[EMO:neutral] そうですね、それは良いですね。", "tool_calls": None}

        async def translate_to_zh(self, text, *args, **kwargs):
            return "中文翻译"

    def no_auto_title(self, *args, **kwargs):
        async def _noop():
            return None

        return asyncio.get_running_loop().create_task(_noop())

    enqueued: list[dict] = []

    async def record_enqueue(**kwargs):
        enqueued.append(kwargs)

    monkeypatch.setattr(jobs, "enqueue_memory_job", record_enqueue)
    monkeypatch.setattr(type(conversation_service), "schedule_auto_title", no_auto_title)
    # The turn adapter for provider "deepseek" is chat_ws.deepseek_service; replace it
    # outright so no request can leave the process.
    monkeypatch.setattr(chat_ws, "deepseek_service", FakeDeepSeek())

    session = SessionState(f"v11-default-{uuid4()}")
    session.worldline = SG
    session.identity_mode = "self"
    session.enable_tts = False
    session.api_key = "test-provider-credential"
    selected = await conversation_service.create_and_select(
        session.session_id, SG, provider_id="deepseek", model_id="deepseek-v4-flash",
        identity_mode="self",
    )
    session.conversation_id = str(selected["id"])
    sessions[session.session_id] = session
    try:
        session.append_message({"role": "user", "content": "質問です", "id": None})
        send_queue: asyncio.Queue = asyncio.Queue()
        await processor_loop(session, "質問です", send_queue, session.current_epoch, session.history_epoch)
        if session.background_tasks:
            await asyncio.wait_for(
                asyncio.gather(*list(session.background_tasks), return_exceptions=True), 5
            )
    finally:
        sessions.pop(session.session_id, None)

    user_ids = [m["id"] for m in session.history if m.get("role") == "user"]
    assert len(enqueued) == 1
    assert enqueued[0]["session_id"] == session.session_id
    assert enqueued[0]["identity_mode"] == "self"
    assert enqueued[0]["source_message_id"] == user_ids[-1]
    assert not hasattr(memory_service, "schedule_turn_extraction")


async def test_prompt_uses_v11_resident_facts(isolated_store):
    from app.services.prompt_compiler import compile_for_session

    session_id = f"v11-default-prompt-{uuid4()}"
    marker = f"V11_RESIDENT_{uuid4().hex[:8]}"
    await create_stable_fact(
        session_id=session_id,
        worldline=SG,
        identity_mode="self",
        display_text=f"{marker} 喜欢红茶",
        semantic_json={"subject": "user", "relation": "likes", "object": "tea"},
    )
    session = SimpleNamespace(
        session_id=session_id, worldline=SG, identity_mode="self",
        base_system_prompt="You are Amadeus Kurisu.", system_prompt="You are Amadeus Kurisu.",
        memory_summary="", history=[], self_name="", identity_acknowledged=False,
    )
    prompt = await compile_for_session(session, "你好")
    assert marker in prompt
    assert "\nCORE FACTS\n" in prompt


@pytest.mark.parametrize("path", [
    "/api/memory/forget",
    "/api/memory/facts/1/promote",
    "/api/memory/facts/1/reclassify",
    "/api/memory/facts/1/dismiss",
    "/api/memory/facts/dismiss-batch",
    "/api/memory/facts/1/delete-local",
    "/api/memory/shared-facts/1/delete",
])
def test_removed_legacy_routes_are_gone(app_client, path):
    response = app_client.post(f"{path}?session_id=default&worldline={SG}", json={})
    assert response.status_code in {404, 405}


@pytest.mark.parametrize("path", ["/api/memory/status", "/api/memory/facts"])
def test_status_and_facts_require_identity_mode(app_client, path):
    response = app_client.get(path, params={"session_id": "default", "worldline": SG})
    assert response.status_code == 422
    ok = app_client.get(path, params={"session_id": "default", "worldline": SG, "identity_mode": "self"})
    assert ok.status_code == 200
    assert "runtime_mode" not in ok.json()
