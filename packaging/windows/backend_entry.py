"""Run the frozen loopback backend and honor the desktop lifetime signals."""
from __future__ import annotations

import argparse
import asyncio
import ctypes
from ctypes import wintypes
import json
import math
import os
from pathlib import Path
import socket
import sys
import threading
import traceback


def configure_environment() -> None:
    for stream in (sys.stdout, sys.stderr):
        if stream is not None:
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")
    os.environ["PYTHON_DOTENV_DISABLED"] = "1"
    os.environ.pop("AMADEUS_DB_PATH", None)
    if not getattr(sys, "frozen", False):
        raise RuntimeError("use the frozen executable for this entry point")
    root = Path(sys._MEIPASS)
    # The adapter uses the model ID, so keep Hugging Face's hub cache layout.
    os.environ["HF_HOME"] = str(root / "huggingface")
    os.environ["HF_HUB_CACHE"] = str(root / "hf_cache")
    os.environ["HUGGINGFACE_HUB_CACHE"] = str(root / "hf_cache")
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    os.environ["HF_HUB_DISABLE_IMPLICIT_TOKEN"] = "1"
    os.environ["AMADEUS_EMBEDDING_MODEL"] = "intfloat/multilingual-e5-small"
    for key in ("TRANSFORMERS_CACHE", "SENTENCE_TRANSFORMERS_HOME"):
        os.environ.pop(key, None)
    os.environ.setdefault("ASSETS_DIR", str(root / "desktop/public/assets"))


def watch_parent(pid: int, shutdown: threading.Event) -> None:
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    handle = kernel.OpenProcess(0x00100000, False, pid)
    if not handle:
        shutdown.set()
        return
    try:
        while not shutdown.is_set():
            if kernel.WaitForSingleObject(handle, 100) != 0x102:
                shutdown.set()
    finally:
        kernel.CloseHandle(handle)


def watch_stdin(shutdown: threading.Event) -> None:
    # Peek the native pipe without retaining Python stdin's lock during imports.
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetStdHandle.argtypes = [wintypes.DWORD]
    kernel.GetStdHandle.restype = wintypes.HANDLE
    kernel.GetFileType.argtypes = [wintypes.HANDLE]
    kernel.GetFileType.restype = wintypes.DWORD
    kernel.PeekNamedPipe.argtypes = [
        wintypes.HANDLE, wintypes.LPVOID, wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.DWORD),
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel.PeekNamedPipe.restype = wintypes.BOOL
    kernel.ReadFile.argtypes = [
        wintypes.HANDLE, wintypes.LPVOID, wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID,
    ]
    kernel.ReadFile.restype = wintypes.BOOL
    handle = kernel.GetStdHandle(-10)
    if kernel.GetFileType(handle) != 3:  # FILE_TYPE_PIPE
        print("AMADEUS_BACKEND_STDIN is not a pipe; waiting for EOF", file=sys.stderr, flush=True)
        if sys.stdin is not None:
            for _ in sys.stdin:
                pass
        print("AMADEUS_BACKEND_STDIN non-pipe EOF; requesting shutdown", file=sys.stderr, flush=True)
        shutdown.set()
        return
    while not shutdown.is_set():
        available = wintypes.DWORD()
        if not kernel.PeekNamedPipe(handle, None, 0, None, ctypes.byref(available), None):
            shutdown.set()
            return
        if available.value:
            buffer = ctypes.create_string_buffer(min(available.value, 65536))
            count = wintypes.DWORD()
            if not kernel.ReadFile(handle, buffer, len(buffer), ctypes.byref(count), None):
                shutdown.set()
                return
        shutdown.wait(0.05)


def self_check() -> int:
    from app.main import app
    from app.services.memory import EmbeddingAdapter
    import sherpa_onnx
    import sqlite_vec
    import sqlite3

    vector = asyncio.run(EmbeddingAdapter().encode_query("离线打包检查"))
    if len(vector) != 384 or not all(math.isfinite(value) for value in vector):
        raise RuntimeError("invalid E5 embedding")
    norm = sum(value * value for value in vector) ** 0.5
    if not 0.99 < norm < 1.01:
        raise RuntimeError("E5 embedding is not normalized")
    # Importing the native extension resolves sherpa's core and ORT DLLs.
    from sherpa_onnx.lib import _sherpa_onnx
    if not hasattr(_sherpa_onnx, "OfflineRecognizer"):
        raise RuntimeError("sherpa native recognizer is missing")
    with sqlite3.connect(":memory:") as connection:
        connection.enable_load_extension(True)
        sqlite_vec.load(connection)
        vec_version = connection.execute("select vec_version()").fetchone()[0]
    print("SELF_CHECK " + json.dumps({
        "app": bool(app), "e5": {"dimensions": len(vector), "norm": norm},
        "sherpa_onnx": sherpa_onnx.__version__, "sqlite_vec": vec_version,
        "offline": os.environ["HF_HUB_OFFLINE"],
        "cache": os.environ["HF_HUB_CACHE"],
    }, ensure_ascii=False), flush=True)
    return 0


async def serve(shutdown: threading.Event, port: int) -> int:
    import uvicorn
    from app.main import app

    if shutdown.is_set():
        return 0
    with socket.socket() as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        listener.bind(("127.0.0.1", port))
        server = uvicorn.Server(uvicorn.Config(
            app, host="127.0.0.1", port=port, lifespan="on", log_level="info",
            timeout_graceful_shutdown=3,
        ))

        async def request_shutdown() -> None:
            await asyncio.to_thread(shutdown.wait)
            print("AMADEUS_BACKEND_SHUTDOWN requested", flush=True)
            server.should_exit = True

        watcher = asyncio.create_task(request_shutdown())
        serving = asyncio.create_task(server.serve(sockets=[listener]))
        try:
            while not server.started and not serving.done():
                await asyncio.sleep(0.05)
            if server.started:
                print("AMADEUS_BACKEND_READY " + json.dumps({
                    "port": port, "pid": os.getpid(),
                }), flush=True)
            await serving
            return 0 if server.started else 1
        finally:
            shutdown.set()
            await watcher


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args()
    try:
        configure_environment()
        if args.self_check:
            return self_check()
        port = int(os.environ.get("BACKEND_PORT", "8000"))
        if not 1 <= port <= 65535:
            raise ValueError("BACKEND_PORT must be between 1 and 65535")
        parent = os.environ.get("AMADEUS_PARENT_PID", "")
        shutdown = threading.Event()
        if parent:
            if not parent.isdecimal() or int(parent) <= 0:
                raise ValueError("AMADEUS_PARENT_PID must be a positive integer")
            threading.Thread(target=watch_parent, args=(int(parent), shutdown), daemon=True).start()
        threading.Thread(target=watch_stdin, args=(shutdown,), daemon=True).start()
        return asyncio.run(serve(shutdown, port))
    except Exception as error:
        print(f"AMADEUS_BACKEND_FAILED {type(error).__name__}: {error}", flush=True)
        if args.self_check:
            traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
