"""Offline scale benchmark; five fresh processes/copies, real loopback HTTP/WS.

python -m scripts.scale_measure --data-dir /tmp/.../data --output /tmp/217-before
Startup includes source imports through uvicorn.started (the packaged READY
condition), not a frozen executable or OS page-cache eviction. Restore runs
after server shutdown, never renaming databases with open application handles.
"""
from __future__ import annotations

import argparse
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import json
import os
from pathlib import Path
import platform
import shutil
import socket
import sqlite3
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock


def offline(root):
    # Bind isolation BEFORE any app import, including config's dotenv loader.
    for key in list(os.environ):
        if key.startswith(("AMADEUS_", "DEEPSEEK_", "TAVILY_", "FIRECRAWL_", "SOVITS_")):
            os.environ.pop(key)
    os.environ.update(AMADEUS_DATA_DIR=str(root), PYTHON_DOTENV_DISABLED="1",
                      AMADEUS_CREDENTIAL_BACKEND="memory", HF_HUB_OFFLINE="1")
    original = socket.socket.connect

    def local_only(sock, address):
        if sock.family in (socket.AF_INET, socket.AF_INET6) and address[0] not in ("127.0.0.1", "::1", "localhost"):
            raise AssertionError("scale benchmark forbids external network")
        return original(sock, address)

    socket.socket.connect = local_only


def sample(root, launched):
    offline(root)
    import numpy as np
    import sqlite_vec
    import httpx
    import uvicorn
    from websockets.sync.client import connect
    from app import db
    from app.main import app, sidecar_supervisor
    from app.routers import chat_ws
    from app.services import backups, data_export, data_location, data_startup
    from app.services.credentials import credential_store
    from app.services.deepseek import build_stream_messages
    from app.services.memory import memory_service
    from app.services.memory_v11 import jobs
    from app.services.memory_v11.recall import MemoryScope, RecallRequest, recall_stable_facts
    from app.services.provider_runtime import deepseek_service
    from scripts.scale_fixture import QUERY, SESSION, vector

    class FakeEmbedder:
        model_name, dimensions = db.EMBEDDING_MODEL, 768

        def readiness(self):
            return {"ok": True, "loaded": True}

        async def encode_query(self, text):
            return np.frombuffer(vector(np.random.default_rng(217)), dtype="<f4").tolist()

    memory_service.embedder = FakeEmbedder()
    credential_store.set("deepseek", "scale-offline")
    sidecar_supervisor.ensure_available = AsyncMock(return_value=SimpleNamespace(url=None))
    sidecar_supervisor.close = AsyncMock()
    jobs.default_memory_completion = AsyncMock(return_value={"observations": [], "operations": []})
    captures, compile_times, server_loops = [], [], []
    original_compile = chat_ws.compile_for_session

    async def compiled(*args, **kwargs):
        server_loops.append(asyncio.get_running_loop())
        began = time.perf_counter()
        result = await original_compile(*args, **kwargs)
        compile_times.append(time.perf_counter() - began)
        return result

    chat_ws.compile_for_session = compiled

    async def stream(active_history=None, memory_summary=None, system_prompt=None, **kwargs):
        # Preserve real request message construction; replace only model I/O.
        build_stream_messages(deepseek_service.build_persona_messages, active_history, memory_summary, system_prompt, isolated=kwargs.get("isolated", False))
        captures.append(system_prompt or "")
        yield {"content": "[EMO:neutral] 週末は山に登ったのね。紅茶も好きなのね。", "tool_calls": None}

    type(deepseek_service).get_chat_stream = staticmethod(stream)
    type(deepseek_service).translate_to_zh = AsyncMock(return_value="周末登山，也喜欢红茶。")
    type(deepseek_service).compress_history = AsyncMock(return_value="週末の登山と紅茶について話した。")
    type(deepseek_service).complete_json = AsyncMock(return_value={"observations": [], "operations": []})
    ready = threading.Event()
    for owner, name in ((backups, "create_backup"), (data_export.PreparedExport, "prepare"), (data_location, "check_target")):
        original = getattr(owner, name)

        def admitted(*args, _original=original, **kwargs):
            ready.set()  # Actual admission, before the operation's blocking work.
            return _original(*args, **kwargs)

        setattr(owner, name, admitted)
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning", lifespan="on"))
    thread = threading.Thread(target=lambda: asyncio.run(server.serve(sockets=[listener])), daemon=True)
    thread.start()
    deadline = time.monotonic() + 120
    while not server.started:
        if not thread.is_alive() or time.monotonic() > deadline:
            raise RuntimeError("backend failed to reach READY")
        time.sleep(0.005)
    metrics = {"startup_ready": time.perf_counter() - launched}
    print("AMADEUS_BACKEND_READY (source uvicorn.started)", flush=True)
    base = f"http://127.0.0.1:{listener.getsockname()[1]}"
    details = {"history_pagination": "unsupported: both history APIs return the full conversation"}

    async def lag_probe(stop):
        worst = 0.0
        while not stop.is_set():
            began = time.perf_counter()
            await asyncio.sleep(0.01)
            worst = max(worst, time.perf_counter() - began - 0.01)
        return worst

    with httpx.Client(base_url=base, headers={"origin": "http://localhost:1420"}, timeout=300) as client:
        def request(method, path, **kwargs):
            response = client.request(method, path, **kwargs)
            response.raise_for_status()
            return response

        def timed(name, function):
            print(f"begin {name}", flush=True)
            began = time.perf_counter()
            value = function()
            metrics[name] = time.perf_counter() - began
            print(f"end {name}: {metrics[name]:.6f}s", flush=True)
            return value

        params = dict(session_id=SESSION, worldline="steins_gate")
        scope_params = dict(**params, identity_mode="self")
        rows = timed("conversation_list", lambda: request("GET", "/api/conversations", params=params)).json()
        assert len(rows) >= 2
        history = timed("history_full_long", lambda: request("GET", "/api/conversations/scale-0-0/messages", params=params)).json()
        assert len(history) == json.loads((root / "scale-fixture.json").read_text(encoding="utf-8"))["long_history"]
        for name, path in (("archive_facts", "facts"), ("archive_experiences", "experiences"), ("memory_chart", "chart")):
            timed(name, lambda path=path: request("GET", "/api/memory/" + path, params=scope_params))
        recalled = timed("recall_memory", lambda: asyncio.run(recall_stable_facts(
            scope=MemoryScope(SESSION, "steins_gate", "self"), request=RecallRequest(needs=(QUERY,)), embedder=memory_service.embedder)))
        details["recall"] = {"status": recalled.status, "records": len(recalled.records), "diagnostics": recalled.diagnostics}
        assert recalled.records, details["recall"]
        with connect(base.replace("http:", "ws:") + f"/ws/chat?session_id={SESSION}",
                     origin="http://localhost:1420", open_timeout=30) as ws:
            ws.send(json.dumps(dict(type="auth", conversation_id="scale-0-1", enable_tts=False)))
            def receive_until(predicate):
                while True:
                    frame = json.loads(ws.recv(timeout=120))
                    assert frame["type"] != "error", frame
                    if predicate(frame):
                        return frame
            receive_until(lambda f: f["type"] == "session.ready")

            def turn():
                began = time.perf_counter()
                ws.send(json.dumps({"type": "chat", "content": QUERY}, ensure_ascii=False))
                receive_until(lambda f: f["type"] == "status" and f.get("state") == "done")
                return time.perf_counter() - began

            metrics["websocket_turn"] = turn()
            metrics["prompt_compile"] = compile_times[0]
            details["prompt_blocks"] = {b: b in captures[0] for b in ("CORE FACTS", "EPISODIC MEMORY", "MEMORY CANDIDATES")}
            assert details["prompt_blocks"]["CORE FACTS"] and details["prompt_blocks"]["EPISODIC MEMORY"], details["prompt_blocks"]
            manifest = json.loads((root / "scale-fixture.json").read_text(encoding="utf-8"))
            if manifest["facts"] // 2 > 32:
                assert details["prompt_blocks"]["MEMORY CANDIDATES"], details["prompt_blocks"]
            loop = server_loops[0]

            def concurrent(name, function):
                ready.clear()
                stop = threading.Event()
                probe = asyncio.run_coroutine_threadsafe(lag_probe(stop), loop)
                with ThreadPoolExecutor(max_workers=1) as pool:
                    future = pool.submit(lambda: timed(name, function))
                    assert ready.wait(60), f"{name} never admitted"
                    latencies = [turn()]
                    while not future.done():
                        latencies.append(turn())
                    value = future.result()
                stop.set()
                metrics[name + "_loop_lag"] = probe.result(timeout=10)
                metrics[name + "_ws_max"] = max(latencies)
                return value

            for name, kind, fmt in (("export_md", "conversations", "md"), ("export_json", "conversations", "json"), ("export_memory", "memory", "json")):
                response = concurrent(name, lambda kind=kind, fmt=fmt: request("GET", "/api/data/export", params=dict(kind=kind, format=fmt, worldline="all")))
                details[name + "_bytes"] = len(response.content)
            details["disk_usage"] = timed("disk_usage", lambda: request("GET", "/api/data/disk")).json()
            checked = concurrent("location_check", lambda: request("POST", "/api/data/location/check", json={"path": str(root.parent / "move-target")})).json()
            assert checked["ok"], checked
            backup = concurrent("manual_backup", lambda: request("POST", "/api/data/backups")).json()
        # No active socket/session is erased. Both supported endpoints are timed together.
        def erase():
            forgotten = request("POST", "/api/conversations/scale-0-0/forget", params=params, json={"forget_long_term": True}).json()
            request("DELETE", "/api/conversations/scale-0-0", params=params)
            return forgotten
        details["erasure"] = timed("delete_and_forget", erase)
        began_restore = time.perf_counter()
        request("POST", f"/api/data/backups/{backup['id']}/restore")
        schedule_seconds = time.perf_counter() - began_restore
    server.should_exit = True
    thread.join(timeout=60)
    assert not thread.is_alive(), "server handles must be closed before restore"
    began_restore = time.perf_counter()
    assert backups.apply_pending_restore() is None
    data_startup.check_databases()
    db.reset_initialization_cache()
    asyncio.run(db.init_db())
    metrics["restore"] = schedule_seconds + time.perf_counter() - began_restore
    # Compare restored application row counts to the actual online snapshot.
    for relative in backups.database_files():
        saved = root / "backups" / backup["id"] / relative
        if not saved.exists():
            continue
        with closing(sqlite3.connect(saved)) as before, closing(sqlite3.connect(root / relative)) as after:
            for connection in (before, after):
                connection.enable_load_extension(True)
                sqlite_vec.load(connection)
            for (table,) in before.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"):
                assert before.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone() == after.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone(), table
    details["restored_counts_match"] = True
    details["db_bytes"] = {p.relative_to(root).as_posix(): p.stat().st_size for p in root.rglob("*.sqlite3") if "backups" not in p.parts}
    import resource  # Linux benchmark process high-water RSS (all server threads).
    details["peak_rss_bytes"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * (1 if sys.platform == "darwin" else 1024)
    return {"seconds": metrics, "details": details}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--sample", type=float, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.sample is not None:
        args.output.write_text(json.dumps(sample(args.data_dir, args.sample), ensure_ascii=False, indent=2), encoding="utf-8")
        return
    if not (args.data_dir / "scale-fixture.json").is_file() or args.repeats < 1:
        parser.error("use a generated scale fixture and positive repeats")
    args.output.mkdir(parents=True, exist_ok=False)
    samples = []
    for i in range(args.repeats):
        # Copying and cleanup are outside all timed regions; each deletion/restore
        # starts from the same fixture rather than a progressively smaller store.
        with tempfile.TemporaryDirectory(prefix="amadeus-scale-measure-") as temporary:
            root = Path(temporary) / "data"
            shutil.copytree(args.data_dir, root)
            result = args.output.resolve() / f"sample-{i}.json"
            with (args.output / f"sample-{i}.log").open("w", encoding="utf-8") as log:
                subprocess.run([sys.executable, "-m", "scripts.scale_measure", "--data-dir", str(root),
                                "--output", str(result), "--sample", str(time.perf_counter())],
                               cwd=Path(__file__).resolve().parents[1], stdout=log, stderr=subprocess.STDOUT, check=True, timeout=900)
            samples.append(json.loads(result.read_text(encoding="utf-8")))
            print(f"sample {i + 1}/{args.repeats} complete", flush=True)
    frequent = {"startup_ready", "conversation_list", "websocket_turn", "prompt_compile", "recall_memory"}
    summary = {}
    for name in samples[0]["seconds"]:
        values = [s["seconds"][name] for s in samples]
        limit = 1 if name in frequent or name.endswith(("_ws_max", "_loop_lag")) else 30
        summary[name] = dict(median=statistics.median(values), maximum=max(values), threshold=limit, exceeded=max(values) > limit)
    report = dict(machine=dict(system=platform.platform(), cpu=platform.processor(), logical_cpus=os.cpu_count(), python=sys.version),
                  fixture=json.loads((args.data_dir / "scale-fixture.json").read_text(encoding="utf-8")),
                  summary=summary, samples=samples)
    (args.output / "summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
