"""Real standalone pi and real chat processor against one local synthetic API."""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app import config
from app.db import init_db
from app.services.pi_agent import PiSupervisor


@pytest.fixture
def pi_binary():
    binary = os.environ.get("AMADEUS_TEST_PI_EXECUTABLE", "")
    if not binary or not Path(binary).is_file():
        pytest.skip("set AMADEUS_TEST_PI_EXECUTABLE to the separately downloaded pi 1.5.0 standalone; CI does not bundle pi")
    return str(Path(binary).resolve())


@contextmanager
def synthetic_api(folder, *, guard_probe=False, delegate=True):
    state = SimpleNamespace(requests=[], tool_results=[], observed_reply="", tools=[])

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            wire = self.rfile.read(int(self.headers["Content-Length"]))
            body = json.loads(wire)
            state.requests.append({"path": self.path, "body": body, "wire": wire})
            names = [t["function"]["name"] for t in body.get("tools", [])]
            results = [m for m in body["messages"] if m["role"] == "tool"]
            if "read" in names and not results:
                state.tools = names
                calls = [("read", {"path": "note.txt"})]
                if guard_probe:
                    calls += [("ls", {"path": "."}), ("grep", {"path": ".", "pattern": "INSIDE_OK"})]
                    calls += [(name, {"path": path, **({"pattern": "OUTSIDE"} if name == "grep" else {})})
                              for name in ("read", "ls", "grep")
                              for path in ("..", str(folder.parent), "link")]
                    calls += [("read", {"path": "../outside.txt"}), ("read", {"path": "link/outside.txt"})]
                    calls += [("read", {"path": "confus\u00a0able.txt"})]
                delta = {"role": "assistant", "tool_calls": [
                    {"index": i, "id": f"read_{i}", "type": "function",
                     "function": {"name": name, "arguments": json.dumps(args)}}
                    for i, (name, args) in enumerate(calls)
                ]}
                finish = "tool_calls"
            elif results:
                state.tool_results = results
                content = "\n".join(str(r["content"]) for r in results)
                delta = {"role": "assistant", "content": content}
                finish = "stop"
            elif delegate and "delegate_task" in names:
                delta = {"role": "assistant", "tool_calls": [{"index": 0, "id": "delegate_1", "type": "function",
                    "function": {"name": "delegate_task", "arguments": json.dumps({
                        "task": "Read note.txt", "folder": str(folder)})}}]}
                finish = "tool_calls"
            else:
                state.observed_reply = json.dumps(body["messages"], ensure_ascii=False)
                delta = {"role": "assistant", "content": "[EMO:neutral] 確認した内容は「INSIDE_OK」です。"}
                finish = "stop"
            chunks = [
                {"id": "synthetic", "object": "chat.completion.chunk", "created": 1, "model": "synthetic",
                 "choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
                {"id": "synthetic", "object": "chat.completion.chunk", "created": 1, "model": "synthetic",
                 "choices": [{"index": 0, "delta": {}, "finish_reason": finish}],
                 "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120}},
            ]
            data = ("".join("data: " + json.dumps(c, ensure_ascii=False) + "\n\n" for c in chunks)
                    + "data: [DONE]\n\n").encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        state.url = f"http://127.0.0.1:{server.server_port}/v1"
        yield state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.mark.asyncio
async def test_standalone_root_guard_and_u2028(pi_binary, tmp_path, monkeypatch):
    folder = tmp_path / "work"
    folder.mkdir()
    (folder / "note.txt").write_text("INSIDE_OK\u2028SAME_RECORD", encoding="utf-8")
    (tmp_path / "outside.txt").write_text("OUTSIDE_SECRET_MARKER", encoding="utf-8")
    try:
        (folder / "link").symlink_to(tmp_path, target_is_directory=True)
        (folder / "confus able.txt").symlink_to(tmp_path / "outside.txt")
    except OSError:
        pytest.skip("symlink privilege unavailable on this Windows account")
    (folder / "confus\u00a0able.txt").write_text("harmless", encoding="utf-8")
    monkeypatch.setattr(config, "PI_AGENT_ENABLED", True)
    monkeypatch.setattr(config, "PI_AGENT_EXECUTABLE", pi_binary)
    user_pi = Path.home() / ".pi" / "agent"
    extensions = user_pi / "extensions"
    extensions.mkdir(parents=True)
    marker = tmp_path / "user-extension-loaded"
    extension = extensions / "must-not-load.ts"
    extension.write_text(
        'import {writeFileSync} from "node:fs"; export default function() {'
        f'writeFileSync({json.dumps(str(marker))}, "loaded");' + '}', encoding="utf-8")
    original = extension.read_bytes()
    with synthetic_api(folder, guard_probe=True) as api:
        text = await PiSupervisor().run("inspect notes", str(folder), base_url=api.url,
                                        model="synthetic", api_key="synthetic-only", timeout=20)
    results = [r["content"] for r in api.tool_results]
    assert "INSIDE_OK" in str(results)
    assert "OUTSIDE_SECRET_MARKER" not in str(results)
    assert str(results).count("Path is outside the delegated folder") >= 12
    assert set(api.tools) == {"read", "ls", "grep"}
    assert "INSIDE_OK" in text and "SAME_RECORD" in text
    assert all(r["path"] == "/v1/chat/completions" for r in api.requests)
    assert not marker.exists() and extension.read_bytes() == original
    assert sorted(p.name for p in user_pi.iterdir()) == ["extensions"]


async def chat_turn(monkeypatch, api, *, enabled, binary="unused"):
    from app.routers import chat_ws
    from app.services.conversations import conversation_service
    from app.services.credentials import InMemoryCredentialStore
    from app.services.provider_runtime import deepseek_service

    monkeypatch.setattr(config, "PI_AGENT_ENABLED", enabled)
    monkeypatch.setattr(config, "PI_AGENT_EXECUTABLE", binary)
    monkeypatch.setattr(deepseek_service, "base_url", api.url)
    monkeypatch.setattr(deepseek_service, "translate_to_zh", AsyncMock(return_value="已读取。"))
    monkeypatch.setattr(conversation_service, "schedule_auto_title", lambda *a, **kw: None)
    store = InMemoryCredentialStore()
    store.set("deepseek", "synthetic-only")
    monkeypatch.setattr(chat_ws, "credential_store", store)
    await init_db()
    session = chat_ws.SessionState(f"pi-{uuid4()}")
    session.identity_mode = "self"
    session.enable_tts = False
    session.api_key = store.get("deepseek")
    selected = await conversation_service.create_and_select(session.session_id, session.worldline, identity_mode="self")
    session.conversation_id = str(selected["id"])
    # Freeze this input to make the disabled request a stable, complete byte snapshot.
    async def compile_prompt(*_args, **_kwargs):
        return "SYNTHETIC SYSTEM"
    monkeypatch.setattr(chat_ws, "compile_for_session", compile_prompt)
    original_system_prompt = chat_ws._request_system_prompt
    def fixed_system_prompt(*args, **kwargs):
        return re.sub(r"【現在時刻：[^】]+】", "【現在時刻：2026-11-05 00:00】",
                      original_system_prompt(*args, **kwargs))
    monkeypatch.setattr(chat_ws, "_request_system_prompt", fixed_system_prompt)
    user = "このフォルダーのメモを読んで。"
    session.append_message({"role": "user", "content": user, "id": None})
    queue = asyncio.Queue()
    await asyncio.wait_for(chat_ws.processor_loop(
        session, user, queue, session.current_epoch, session.history_epoch,
    ), 30)
    events = []
    while not queue.empty():
        events.append(queue.get_nowait()[1])
    assert not [e for e in events if e.get("type") == "error"], events
    return session, events


@pytest.mark.asyncio
async def test_chat_delegate_pi_read_result_returns_in_her_reply(pi_binary, tmp_path, monkeypatch):
    (tmp_path / "note.txt").write_text("INSIDE_OK", encoding="utf-8")
    with synthetic_api(tmp_path) as api:
        session, events = await chat_turn(monkeypatch, api, enabled=True, binary=pi_binary)
    assert len(api.requests) == 4  # Amadeus tool call, two pi requests, Amadeus answer.
    assert "INSIDE_OK" in api.observed_reply
    assert "[delegate_task]" in api.observed_reply
    assert "確認した内容は「INSIDE_OK」です。" in session.history[-1]["content"]
    assert not [e for e in events if e.get("tool") == "delegate_task"]
    assert not [e for e in events if str(e.get("type", "")).startswith("pi.")]


@pytest.mark.asyncio
async def test_disabled_complete_request_bytes_match_pre_spike(monkeypatch, tmp_path):
    with synthetic_api(tmp_path, delegate=False) as api:
        await chat_turn(monkeypatch, api, enabled=False)
    assert len(api.requests) == 1
    # Port is not in the request body; fixed system/history make it deterministic.
    wire = api.requests[0]["wire"]
    assert b"delegate_task" not in wire
    # Updated in [221] alongside intentional self-mode provider anchor and tail_reminder updates.
    assert hashlib.sha256(wire).hexdigest() == "a80bcf7984b0e9a551745039ace8568a1a3f9936b2e363716ce7d3fbcc68833e"
