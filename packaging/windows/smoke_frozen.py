"""Exercise a frozen backend with isolated data and a local synthetic provider."""
from __future__ import annotations

import argparse
import asyncio
from contextlib import closing, contextmanager
import ctypes
from ctypes import wintypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request

USER_TEXT = "这是冻结包的本地验收消息。"
REPLY = "パッケージの動作を確認しました。"
CREATE_NO_WINDOW = 0x08000000


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def process_snapshot() -> dict[int, tuple[int, str]]:
    class Entry(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", wintypes.LONG),
            ("dwFlags", wintypes.DWORD), ("szExeFile", wintypes.WCHAR * 260),
        ]
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    for name in ("Process32FirstW", "Process32NextW"):
        method = getattr(kernel, name)
        method.argtypes = [wintypes.HANDLE, ctypes.POINTER(Entry)]
        method.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    snapshot = kernel.CreateToolhelp32Snapshot(2, 0)
    if snapshot == wintypes.HANDLE(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    processes = {}
    entry = Entry()
    entry.dwSize = ctypes.sizeof(entry)
    try:
        available = kernel.Process32FirstW(snapshot, ctypes.byref(entry))
        while available:
            processes[entry.th32ProcessID] = (entry.th32ParentProcessID, entry.szExeFile)
            available = kernel.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel.CloseHandle(snapshot)
    return processes


def update_children(root_pid: int, tracked: set[int]) -> None:
    processes = process_snapshot()
    ancestors = tracked | {root_pid}
    while True:
        children = {pid for pid, (parent, _) in processes.items() if parent in ancestors}
        if children <= ancestors:
            break
        ancestors |= children
    tracked.update(ancestors - {root_pid})


def free_port() -> int:
    while True:
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        if port != 8000:
            return port


def clean_environment(root: Path, port: int, provider_port: int) -> dict[str, str]:
    system = Path(os.environ["SystemRoot"])
    env = {key: value for key, value in os.environ.items()
           if key.upper() in {"SYSTEMROOT", "WINDIR", "COMSPEC", "SYSTEMDRIVE", "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE"}}
    for relative in ("home", "appdata", "localappdata", "temp", "data", "cwd"):
        (root / relative).mkdir(parents=True)
    env.update({
        "PATH": os.pathsep.join(str(path) for path in (system / "System32", system)),
        "HOME": str(root / "home"), "USERPROFILE": str(root / "home"),
        "APPDATA": str(root / "appdata"), "LOCALAPPDATA": str(root / "localappdata"),
        "TEMP": str(root / "temp"), "TMP": str(root / "temp"),
        "AMADEUS_DATA_DIR": str(root / "data"), "AMADEUS_CREDENTIAL_BACKEND": "memory",
        "PYTHON_DOTENV_DISABLED": "1", "BACKEND_PORT": str(port),
        "AMADEUS_CUSTOM_PROVIDER_BASE_URL": f"http://127.0.0.1:{provider_port}/v1",
        "AMADEUS_CUSTOM_PROVIDER_MODEL": "frozen-smoke",
        "SOVITS_URL": f"http://127.0.0.1:{provider_port}/unused-tts",
        "AMADEUS_MEMORY_MODE": "v11",
    })
    return env


@contextmanager
def fake_service(log_path: Path, port: int = 0, *, sentinel: bool = False):
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def respond(self, status: int, payload: dict) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            requests.append({"method": "GET", "path": self.path})
            if not sentinel and self.path == "/v1/models":
                self.respond(200, {"object": "list", "data": [{"id": "frozen-smoke", "object": "model"}]})
            else:
                self.respond(404, {"error": "no sidecar"})

        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
            requests.append({"method": "POST", "path": self.path, "stream": bool(payload.get("stream")), "model": payload.get("model")})
            if sentinel or self.path != "/v1/chat/completions":
                self.respond(404, {"error": "unexpected endpoint"})
                return
            if payload.get("stream"):
                chunks = [
                    {"choices": [{"index": 0, "delta": {"content": "[EMO:neutral] " + REPLY}, "finish_reason": None}]},
                    {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
                ]
                body = ("".join("data: " + json.dumps(chunk, ensure_ascii=False) + "\n\n" for chunk in chunks)
                        + "data: [DONE]\n\n").encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                content = (json.dumps({"facts": [], "edges": [], "retractions": []})
                           if payload.get("response_format") else "冻结包本地验收完成。")
                self.respond(200, {"choices": [{"message": {"role": "assistant", "content": content}, "finish_reason": "stop"}]})

    class Server(ThreadingHTTPServer):
        allow_reuse_address = False
        daemon_threads = True

        def server_bind(self):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            super().server_bind()

    server = Server(("127.0.0.1", port), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_port, requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        write_json(log_path, requests)


def wait_health(process: subprocess.Popen, port: int, tracked: set[int], timeout: float) -> float:
    started = time.monotonic()
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    while time.monotonic() - started < timeout:
        update_children(process.pid, tracked)
        if process.poll() is not None:
            raise RuntimeError(f"backend exited before health: {process.returncode}")
        try:
            with opener.open(f"http://127.0.0.1:{port}/health", timeout=0.5) as response:
                if response.status == 200 and json.load(response).get("status") == "ok":
                    return time.monotonic() - started
        except (OSError, ValueError):
            pass
        time.sleep(0.1)
    raise TimeoutError("backend did not become healthy")


async def chat(port: int, events_path: Path) -> list[dict]:
    import websockets

    events = []
    try:
        async with websockets.connect(
            f"ws://127.0.0.1:{port}/ws/chat?session_id=frozen-smoke",
            origin="http://127.0.0.1:1420", proxy=None, open_timeout=10,
        ) as connection:
            await connection.send(json.dumps({
                "type": "auth", "protocol_version": 2, "client": "desktop",
                "conversation_mode": "draft", "provider_id": "custom", "model": "frozen-smoke",
                "enable_tts": False, "worldline": "steins_gate", "temperature": 0.0,
            }))
            ready = json.loads(await asyncio.wait_for(connection.recv(), timeout=20))
            events.append(ready)
            if ready.get("type") != "session.ready":
                raise AssertionError(f"auth failed: {ready}")
            await connection.send(json.dumps({"type": "chat.send", "content": USER_TEXT}, ensure_ascii=False))
            async with asyncio.timeout(90):
                while True:
                    event = json.loads(await connection.recv())
                    events.append(event)
                    if event.get("type") == "error":
                        raise AssertionError(f"chat failed: {event}")
                    if event.get("type") == "turn.completed":
                        break
            if not any(event.get("type") == "segment.ready" for event in events):
                raise AssertionError("no segment.ready event")
            if REPLY not in json.dumps(events, ensure_ascii=False):
                raise AssertionError("synthetic provider reply did not reach the client")
            if any(event.get("type") == "segment.audio" and event.get("audio") for event in events):
                raise AssertionError("unexpected TTS audio")
    finally:
        write_json(events_path, events)
    return events


def verify_history(data: Path) -> list[dict]:
    path = data / "worldlines/sg/history.sqlite3"
    with closing(sqlite3.connect(path, timeout=0.2)) as connection:
        rows = connection.execute("select role, content from messages where session_id=? order by id", ("frozen-smoke",)).fetchall()
    if not any(role == "user" and USER_TEXT in content for role, content in rows):
        raise AssertionError("user message was not persisted")
    if not any(role == "assistant" and REPLY in content for role, content in rows):
        raise AssertionError("assistant message was not persisted")
    return [{"role": role, "content": content} for role, content in rows]


def verify_unlocked(data: Path) -> list[str]:
    databases = sorted(data.rglob("*.sqlite3"))
    if not databases:
        raise AssertionError("no databases created")
    for path in databases:
        with closing(sqlite3.connect(path, timeout=0.1)) as connection:
            connection.execute("BEGIN EXCLUSIVE")
            connection.rollback()
        renamed = path.with_suffix(".smoke-unlocked")
        path.rename(renamed)
        renamed.rename(path)
    return [path.relative_to(data).as_posix() for path in databases]


def run_case(exe: Path, root: Path, provider_port: int, mode: str, timeout: float) -> dict:
    root.mkdir()
    port = free_port()
    env = clean_environment(root, port, provider_port)
    parent = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"], creationflags=CREATE_NO_WINDOW)
    env["AMADEUS_PARENT_PID"] = str(parent.pid)
    tracked: set[int] = set()
    process = None
    before = process_snapshot()
    try:
        with (root / "backend.log").open("wb") as log:
            started = time.monotonic()
            process = subprocess.Popen([str(exe)], cwd=root / "cwd", env=env, stdin=subprocess.PIPE,
                                       stdout=log, stderr=subprocess.STDOUT, creationflags=CREATE_NO_WINDOW)
            healthy_seconds = wait_health(process, port, tracked, timeout)
            healthy_seconds = time.monotonic() - started
            print(f"HEALTH {mode} {healthy_seconds:.3f}s port={port}", flush=True)
            events = asyncio.run(chat(port, root / "events.json"))
            history = verify_history(root / "data")
            write_json(root / "history.json", history)
            update_children(process.pid, tracked)
            stopped_at = time.monotonic()
            if mode == "parent":
                parent.kill()
                parent.wait(timeout=3)
            else:
                process.stdin.write(b"shutdown\n")
                process.stdin.flush()
                process.stdin.close()
            while process.poll() is None and time.monotonic() - stopped_at < 10:
                update_children(process.pid, tracked)
                time.sleep(0.05)
            if process.poll() is None:
                raise AssertionError(f"{mode} shutdown exceeded 10 seconds")
            exit_seconds = time.monotonic() - stopped_at
            if process.returncode != 0:
                raise AssertionError(f"{mode} shutdown exit code: {process.returncode}")
        after = process_snapshot()
        remaining = sorted((tracked | {process.pid}) & after.keys())
        new_backends = [pid for pid, (_, name) in after.items()
                        if name.lower() == exe.name.lower() and pid not in before]
        if remaining or new_backends:
            raise AssertionError(f"residual processes: {sorted(set(remaining + new_backends))}")
        databases = verify_unlocked(root / "data")
        log_text = (root / "backend.log").read_text(encoding="utf-8")
        if "AMADEUS_BACKEND_READY" not in log_text or "Application shutdown complete" not in log_text:
            raise AssertionError("missing ready / graceful shutdown log")
        result = {"mode": mode, "port": port, "healthy_seconds": round(healthy_seconds, 3),
                  "exit_seconds": round(exit_seconds, 3), "exit_code": process.returncode,
                  "events": [event["type"] for event in events], "history_rows": len(history),
                  "unlocked_databases": databases, "residual_processes": remaining, "path": env["PATH"]}
        write_json(root / "result.json", result)
        print("SMOKE_CASE " + json.dumps(result, ensure_ascii=False), flush=True)
        return result
    finally:
        if process is not None:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
            if process.stdin is not None and not process.stdin.closed:
                process.stdin.close()
        if parent.poll() is None:
            parent.kill()
        parent.wait(timeout=5)
        # On failure, terminate only descendants captured from this smoke backend.
        for pid in tracked & process_snapshot().keys():
            subprocess.run([str(Path(os.environ["SystemRoot"]) / "System32/taskkill.exe"), "/PID", str(pid), "/T", "/F"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=CREATE_NO_WINDOW)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exe", type=Path, required=True)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--startup-timeout", type=float, default=180)
    args = parser.parse_args()
    if os.name != "nt":
        parser.error("run on Windows")
    exe = args.exe.resolve(strict=True)
    args.work_dir.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="frozen-", dir=args.work_dir.resolve()))
    print(f"SMOKE_ROOT {root}", flush=True)
    result = {"root": str(root), "exe": str(exe), "ok": False}
    try:
        # The app probes 9880 at startup. Own it before launch to avoid any real TTS.
        with fake_service(root / "sidecar-sentinel.json", 9880, sentinel=True), fake_service(root / "provider.json") as (provider_port, requests):
            check_root = root / "self-check"
            check_root.mkdir()
            env = clean_environment(check_root, free_port(), provider_port)
            with (check_root / "output.log").open("wb") as output:
                checked = subprocess.run([str(exe), "--self-check"], cwd=check_root / "cwd", env=env,
                                         stdout=output, stderr=subprocess.STDOUT, timeout=180, creationflags=CREATE_NO_WINDOW)
            check_text = (check_root / "output.log").read_text(encoding="utf-8")
            print(check_text, flush=True)
            if checked.returncode != 0 or "SELF_CHECK " not in check_text:
                raise AssertionError(f"self-check failed ({checked.returncode})")
            result["self_check_exit_code"] = checked.returncode
            result["cases"] = [run_case(exe, root / mode, provider_port, mode, args.startup_timeout) for mode in ("parent", "stdin")]
            if not any(request.get("stream") for request in requests):
                raise AssertionError("fake provider received no streamed chat")
            if any("tts" in request["path"] for request in requests):
                raise AssertionError("unexpected TTS request")
        result["ok"] = True
        print("SMOKE_OK", flush=True)
        return 0
    except Exception as error:
        result["error"] = f"{type(error).__name__}: {error}"
        print("SMOKE_FAILED " + result["error"], flush=True)
        return 1
    finally:
        write_json(root / "result.json", result)


if __name__ == "__main__":
    sys.exit(main())
