"""B2 isolated complete-candidate runtime (lifespan + browser loop).

Reusable entry for prepare / start / stop / restart / verify / fail-start /
probe-outbound / encoding-probe.
Does not switch production data, does not commit, does not call real providers.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import os
import socket
import sqlite3
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

SCRIPT_DIR = Path(__file__).resolve().parent
BACKEND_ROOT = SCRIPT_DIR.parent
WORKTREE_ROOT = BACKEND_ROOT.parent
SCRATCH_ROOT = WORKTREE_ROOT / ".scratch" / "memory-runtime-b2"
DEFAULT_RUNS_ROOT = SCRATCH_ROOT / "runs"

sys.path.insert(0, str(SCRIPT_DIR))
from _path_guards import containing_live_root, deep_resolve  # noqa: E402

DEFAULT_BACKEND_PORT = 8001
DEFAULT_FRONTEND_PORT = 1422
PRODUCTION_PORTS = {8000, 1420, 1421}
FAKE_PROVIDER_KEY = "b2-isolated-fake-credential"
REPLY_JA = "そうですね、それは良いですね。"
SESAME_MARKER = "B2RT_SESAME"
DELETE_MARKER = "B2RT_DELETE"
REVISED_MARKER = "B2RT_SESAME_V2"
CREATE_NO_WINDOW = 0x08000000
PROVIDER_ENV_KEYS = (
    "DEEPSEEK_API_KEY",
    "OPENAI_API_KEY",
    "OPENAI_API_KEY_NEW",
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "ANTHROPIC_API_KEY",
    "TAVILY_API_KEY",
    "FIRECRAWL_API_KEY",
    "AMADEUS_DB_PATH",
    "SOVITS_SIDECAR_COMMAND",
    "SOVITS_GPT_WEIGHTS",
    "SOVITS_SOVITS_WEIGHTS",
    "PYTEST_CURRENT_TEST",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
)


class RuntimeError_(SystemExit):
    """CLI failure with a stable code prefix."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _fail(code: str, message: str, *, exit_code: int = 2) -> None:
    print(f"error[{code}]: {message}", file=sys.stderr, flush=True)
    raise SystemExit(exit_code)


def _configure_utf8_stdio() -> None:
    """Emit UTF-8 from this CLI even when the parent process is a GBK console."""
    for stream in (sys.stdout, sys.stderr):
        if stream is None:
            continue
        reconfigure = getattr(stream, "reconfigure", None)
        if not callable(reconfigure):
            continue
        try:
            reconfigure(encoding="utf-8", errors="strict")
        except (OSError, ValueError, AttributeError):
            continue


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def production_live_roots(environ: dict[str, str] | None = None) -> list[Path]:
    env = environ if environ is not None else dict(os.environ)
    roots: list[Path] = []
    appdata = (
        env.get("AMADEUS_B2_ORIGINAL_APPDATA") or env.get("APPDATA") or ""
    ).strip()
    if appdata:
        roots.append(deep_resolve(Path(appdata) / "Amadeus"))
    home = (
        env.get("AMADEUS_B2_ORIGINAL_USERPROFILE")
        or env.get("USERPROFILE")
        or env.get("HOME")
        or ""
    ).strip()
    if home:
        roots.append(deep_resolve(Path(home) / ".amadeus"))
    return roots


def run_paths(run_root: Path) -> dict[str, Path]:
    run_root = deep_resolve(run_root)
    return {
        "root": run_root,
        "snapshot": run_root / "snapshot",
        "preview": run_root / "inputs" / "preview.json",
        "candidate": run_root / "candidate",
        "profile": run_root / "fake-profile",
        "home": run_root / "fake-home",
        "local": run_root / "fake-local",
        "tmp": run_root / "tmp",
        "logs": run_root / "logs",
        "captures": run_root / "captures",
        "browser": run_root / "browser",
        "pids": run_root / "pids.json",
        "meta": run_root / "run.json",
        "env_file": run_root / "fake-profile" / "AmadeusIsolated" / ".env",
    }


def inspect_published_candidate(
    candidate: Path,
    *,
    live_roots: list[Path] | None = None,
) -> dict[str, Any]:
    """Reuse published-candidate markers; do not rebuild or scan live trees."""
    from app.services.memory_v11.candidate import (
        CANDIDATE_KIND,
        INCOMPLETE_KIND,
        _SNAPSHOT_FILES,
    )

    roots = live_roots if live_roots is not None else production_live_roots()
    resolved = deep_resolve(candidate)
    if not resolved.is_dir():
        raise ValueError(f"candidate is not a directory: {resolved}")
    live = containing_live_root(resolved, roots)
    if live is not None:
        raise ValueError(f"candidate aliases a live data root: {live}")
    incomplete = resolved / "INCOMPLETE.json"
    if incomplete.is_file():
        raise ValueError(f"refusing incomplete candidate ({INCOMPLETE_KIND})")
    manifest_path = resolved / "manifest.json"
    if not manifest_path.is_file():
        raise ValueError("missing manifest.json")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"manifest is not JSON: {exc}") from exc
    if manifest.get("kind") != CANDIDATE_KIND:
        raise ValueError(f"unexpected manifest kind: {manifest.get('kind')!r}")
    missing = [
        str(rel) for rel in _SNAPSHOT_FILES.values() if not (resolved / rel).is_file()
    ]
    if missing:
        raise ValueError(f"candidate missing database files: {missing}")
    freeze_rows: list[dict[str, Any]] = []
    schedulable_frozen = 0
    for rel in (_SNAPSHOT_FILES["sg_memory"], _SNAPSHOT_FILES["beta_memory"]):
        conn = sqlite3.connect(f"file:{resolved / rel}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                """SELECT job_id, state, retryable, last_error_code
                     FROM memory_ingest_jobs
                    WHERE last_error_code='candidate_build_freeze'"""
            ).fetchall()
            for row in rows:
                item = dict(row)
                freeze_rows.append(item)
                if item["state"] != "cancelled" or int(item["retryable"] or 0) != 0:
                    schedulable_frozen += 1
        finally:
            conn.close()
    if schedulable_frozen:
        raise ValueError("frozen jobs are still schedulable")
    return {
        "ok": True,
        "path": str(resolved),
        "manifest_kind": manifest.get("kind"),
        "builder_version": manifest.get("builder_version"),
        "frozen_job_count": len(freeze_rows),
    }


def _port_in_use(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
        except OSError:
            return True
    return False


def require_loopback_port(port: int, *, role: str) -> None:
    if port in PRODUCTION_PORTS:
        _fail(
            "production_port_refused",
            f"{role} port {port} is a production/main service port; "
            "isolation will not bind it or fall back to it",
        )
    if _port_in_use("127.0.0.1", port):
        _fail(
            "port_in_use",
            f"{role} port 127.0.0.1:{port} is already in use; "
            "refusing to kill the occupant or fall back to 8000/1420",
        )


def isolated_environ(
    *,
    run_root: Path,
    candidate: Path,
    backend_port: int,
    frontend_port: int,
    inherited: dict[str, str] | None = None,
) -> dict[str, str]:
    source = dict(inherited if inherited is not None else os.environ)
    paths = run_paths(run_root)
    for key in ("profile", "home", "local", "tmp", "logs", "captures"):
        paths[key].mkdir(parents=True, exist_ok=True)
    paths["env_file"].parent.mkdir(parents=True, exist_ok=True)
    if not paths["env_file"].exists():
        paths["env_file"].write_text("", encoding="utf-8")

    keep = {
        "PATH",
        "PATHEXT",
        "SYSTEMROOT",
        "WINDIR",
        "COMSPEC",
        "SYSTEMDRIVE",
        "NUMBER_OF_PROCESSORS",
        "PROCESSOR_ARCHITECTURE",
        "PROCESSOR_IDENTIFIER",
        "OS",
        "PYTHONUTF8",
        "PYTHONIOENCODING",
        "TMP",
        "TEMP",
        "ComSpec",
        "USERNAME",
        "USERDOMAIN",
        "COMPUTERNAME",
        "PROGRAMDATA",
        "PUBLIC",
        "ALLUSERSPROFILE",
        "HOMEDRIVE",
        "HOMEPATH",
    }
    env: dict[str, str] = {}
    for key, value in source.items():
        if key in keep:
            env[key] = value
    for key in ("SystemRoot", "windir"):
        if key in source:
            env[key] = source[key]
    original_appdata = (
        source.get("AMADEUS_B2_ORIGINAL_APPDATA")
        or source.get("APPDATA")
        or ""
    )
    original_home = (
        source.get("AMADEUS_B2_ORIGINAL_USERPROFILE")
        or source.get("USERPROFILE")
        or source.get("HOME")
        or ""
    )
    if original_appdata:
        env["AMADEUS_B2_ORIGINAL_APPDATA"] = original_appdata
    if original_home:
        env["AMADEUS_B2_ORIGINAL_USERPROFILE"] = original_home

    env["PYTHONPATH"] = str(BACKEND_ROOT)
    env["PYTHON_DOTENV_DISABLED"] = "1"
    env["AMADEUS_CREDENTIAL_BACKEND"] = "memory"
    env["AMADEUS_DATA_DIR"] = str(deep_resolve(candidate))
    env["AMADEUS_MEMORY_MODE"] = "v11"
    env["APPDATA"] = str(paths["profile"])
    env["LOCALAPPDATA"] = str(paths["local"])
    env["USERPROFILE"] = str(paths["home"])
    env["HOME"] = str(paths["home"])
    env["TEMP"] = str(paths["tmp"])
    env["TMP"] = str(paths["tmp"])
    env["BACKEND_PORT"] = str(backend_port)
    env["FRONTEND_PORT"] = str(frontend_port)
    env["AMADEUS_BACKEND_ORIGIN"] = f"http://127.0.0.1:{backend_port}"
    env["AMADEUS_FRONTEND_PORT"] = str(frontend_port)
    env["AMADEUS_B2_RUN_ROOT"] = str(paths["root"])
    env["AMADEUS_DIAGNOSTICS_OBSERVE"] = "0"
    env["PYTHONUNBUFFERED"] = "1"
    for key in PROVIDER_ENV_KEYS:
        env.pop(key, None)
        source.pop(key, None)
    return env


def _unit_vector(seed: str) -> list[float]:
    values: list[float] = []
    for block in range(12):
        digest = hashlib.sha256(f"{seed}:{block}".encode("utf-8")).digest()
        values.extend(int(byte) - 127.5 for byte in digest)
    norm = math.sqrt(sum(v * v for v in values)) or 1.0
    return [v / norm for v in values]


def _completion_payload(source_message_id: int, marker: str) -> dict[str, Any]:
    from app.services.memory_v11.contracts import AUTO_STABLE_CONFIDENCE

    confidence = 0.96
    assert AUTO_STABLE_CONFIDENCE == 0.85
    assert confidence >= AUTO_STABLE_CONFIDENCE
    return {
        "observations": [
            {
                "observation_ref": "o1",
                "source_message_ids": [int(source_message_id)],
                "display_text": f"{marker} 长期偏好",
                "semantic": {
                    "subject": "user",
                    "predicate": "likes",
                    "object": {"name": marker},
                },
                "evidence_kind": "direct_user",
                "memory_class": "stable_candidate",
                "confidence": confidence,
                "topic_label": "偏好",
            }
        ],
        "operations": [
            {
                "op": "CREATE",
                "observation_ref": "o1",
                "target_fact_id": None,
                "expected_version": None,
                "fact_text": f"{marker} 长期偏好",
                "reason_code": "direct_durable_statement",
            }
        ],
    }


class TracingCredentialStore:
    def __init__(self, inner: Any, capture_path: Path):
        self._inner = inner
        self._path = capture_path
        self.calls: list[dict[str, Any]] = []

    def _record(self, op: str, provider_id: str) -> None:
        event = {"at": _utc_now(), "op": op, "provider_id": provider_id}
        self.calls.append(event)
        _append_jsonl(self._path, event)

    def set(self, provider_id: str, secret: str) -> None:
        self._record("set", provider_id)
        return self._inner.set(provider_id, secret)

    def get(self, provider_id: str) -> str | None:
        self._record("get", provider_id)
        return self._inner.get(provider_id)

    def delete(self, provider_id: str) -> None:
        self._record("delete", provider_id)
        return self._inner.delete(provider_id)


def _install_external_fakes(
    capture_dir: Path,
    *,
    provider_transport: str = "fake",
    provider_credential: str | None = None,
) -> dict[str, Any]:
    """Replace only external service boundaries. Worker default path stays real."""
    import httpx

    from app.main import in_memory_settings
    from app.services import credentials as cred_mod
    from app.services.deepseek import build_stream_messages
    from app.services.memory import EmbeddingAdapter, memory_service
    from app.services.provider_runtime import deepseek_service
    from app.services.search import search_service
    from app.services.tts_queue import tts_manager
    from app.services.tts_sidecar import SidecarState, sidecar_supervisor

    capture_dir.mkdir(parents=True, exist_ok=True)
    chat_log = capture_dir / "provider_chat.jsonl"
    memory_log = capture_dir / "provider_memory.jsonl"
    outbound_log = capture_dir / "outbound_http.jsonl"
    cred_log = capture_dir / "credentials.jsonl"

    if cred_mod.WINDOWS_CREDENTIAL_STORE_CONSTRUCTED:
        raise RuntimeError("Windows Credential Manager store was constructed")
    tracer = TracingCredentialStore(cred_mod.credential_store, cred_log)
    cred_mod.credential_store = tracer
    tracer.set("deepseek", provider_credential or FAKE_PROVIDER_KEY)

    from app.routers import chat_ws, providers, system_api, voice_ws
    from app.services import search as search_mod

    for module in (chat_ws, providers, system_api, voice_ws, search_mod):
        setattr(module, "credential_store", tracer)

    if hasattr(cred_mod, "WindowsCredentialStore"):

        def _blocked(self, *args, **kwargs):
            raise RuntimeError("Windows Credential Manager called in isolated runtime")

        cred_mod.WindowsCredentialStore.get = _blocked  # type: ignore[method-assign]
        cred_mod.WindowsCredentialStore.set = _blocked  # type: ignore[method-assign]
        cred_mod.WindowsCredentialStore.delete = _blocked  # type: ignore[method-assign]

    class OfflineEmbedder(EmbeddingAdapter):
        def __init__(self) -> None:
            super().__init__(model_name="b2-offline-deterministic-384", dimensions=384)

        def _load(self):
            raise RuntimeError("real embedding model load is forbidden in B2 isolation")

        async def encode_passage(self, text: str) -> list[float]:
            return _unit_vector(f"passage:{text}")

        async def encode_query(self, text: str) -> list[float]:
            return _unit_vector(f"query:{text}")

    memory_service.embedder = OfflineEmbedder()
    in_memory_settings["enable_tts"] = False

    async def fake_sidecar() -> SidecarState:
        _append_jsonl(
            capture_dir / "sidecar.jsonl",
            {"at": _utc_now(), "event": "ensure_available_bypassed"},
        )
        return SidecarState("bypassed", None, False, "b2-isolated-no-sidecar")

    sidecar_supervisor.ensure_available = fake_sidecar  # type: ignore[method-assign]

    async def fake_search(*_args, **_kwargs):
        raise RuntimeError("search bypassed in isolated runtime")

    search_service.search = fake_search  # type: ignore[method-assign]

    async def fake_tts(*args, **kwargs):
        raise RuntimeError("tts bypassed in isolated runtime")

    tts_manager.synthesize_async = fake_tts  # type: ignore[attr-defined]
    if hasattr(tts_manager, "synthesize_and_queue"):
        tts_manager.synthesize_and_queue = fake_tts  # type: ignore[attr-defined]

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
        _append_jsonl(
            chat_log,
            {
                "at": _utc_now(),
                "model": model,
                "messages": messages,
                "system_prompt": system_prompt,
            },
        )
        yield {"content": REPLY_JA, "tool_calls": None}

    async def fake_translate_to_zh(text, api_key=None, model=None):
        _append_jsonl(
            capture_dir / "translations.jsonl",
            {"at": _utc_now(), "text": text},
        )
        return "中文翻译"

    async def fake_complete_json(messages, api_key=None, model=None, temperature=0.1):
        system = ""
        user = ""
        if messages:
            system = str(messages[0].get("content") or "")
            if len(messages) > 1:
                user = str(messages[-1].get("content") or "")
        if "extract durable user memory" in system:
            payload_in: dict[str, Any] = {}
            try:
                payload_in = json.loads(user)
            except json.JSONDecodeError:
                payload_in = {"user_text": user}
            text = str(payload_in.get("user_text") or "")
            source_id = int(payload_in.get("source_message_id") or 0)
            _append_jsonl(
                memory_log,
                {
                    "at": _utc_now(),
                    "source_message_id": source_id,
                    "user_text": text,
                    "messages": messages,
                },
            )
            if SESAME_MARKER in text:
                return _completion_payload(source_id, SESAME_MARKER)
            if DELETE_MARKER in text:
                return _completion_payload(source_id, DELETE_MARKER)
            return {"observations": [], "operations": []}
        if "生成简洁" in system or "标题" in system:
            _append_jsonl(
                capture_dir / "titles.jsonl",
                {"at": _utc_now(), "messages": messages},
            )
            return {"title": "隔离闭环对话"}
        raise RuntimeError(f"unhandled complete_json in isolated runtime: {system[:80]!r}")

    async def fake_list_models(api_key=None):
        return [{"id": "deepseek-flash"}, {"id": "deepseek-v4-flash"}]

    from app.services import provider_runtime as runtime_mod

    for adapter_name in ("openai_adapter", "gemini_adapter"):
        adapter = getattr(runtime_mod, adapter_name, None)
        if adapter is None:
            continue
        adapter.get_chat_stream = fake_get_chat_stream
        adapter.complete_json = fake_complete_json
        adapter.list_models = fake_list_models

    if provider_transport == "fake":
        deepseek_service.get_chat_stream = fake_get_chat_stream
        deepseek_service.translate_to_zh = fake_translate_to_zh
        deepseek_service.complete_json = fake_complete_json
        deepseek_service.list_models = fake_list_models
        outbound_guards = _install_outbound_guards(outbound_log)
    else:
        # Open only the selected model HTTP transport. Legacy search, weather
        # fallback, and other provider adapters stay fake/blocked.
        outbound_guards = _install_outbound_guards(
            outbound_log,
            block_httpx_clients=False,
        )
    return {
        "chat_log": str(chat_log),
        "memory_log": str(memory_log),
        "credential_log": str(cred_log),
        "windows_store_constructed": cred_mod.WINDOWS_CREDENTIAL_STORE_CONSTRUCTED,
        "outbound_log": str(outbound_log),
        **outbound_guards,
    }


LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


def _install_outbound_guards(
    outbound_log: Path,
    *,
    block_httpx_clients: bool = True,
) -> dict[str, Any]:
    """Block known backend httpx/DDGS exits before a real transport runs.

    Covers isolated-process mis-calls of sync/async httpx and the legacy
    weather → DDGS fallback. Not a process-wide network sandbox: urllib,
    raw sockets, Vite, and parent CLI probes are unchanged.

    When ``block_httpx_clients`` is false (B3 local/real model HTTP), Client.send
    stays available for the allowlisted model host; DDGS and module-level
    httpx.get/request remain blocked so weather fallback cannot leak.
    """
    import httpx
    from app.services import search as search_mod

    sentinel: dict[str, int] = {
        "original_client_send": 0,
        "original_async_send": 0,
        "httpx_http_transport": 0,
        "httpx_async_http_transport": 0,
        "ddgs_blocked": 0,
        "ddgs_original": 0,
    }

    original_async_send = httpx.AsyncClient.send
    original_sync_send = httpx.Client.send
    original_get = httpx.get
    original_request = httpx.request
    original_ddgs = search_mod.DDGS
    original_http_transport = getattr(httpx.HTTPTransport, "handle_request", None)
    original_async_transport = getattr(
        httpx.AsyncHTTPTransport, "handle_async_request", None
    )

    def _trap_sync_send(self, request, *args, **kwargs):
        sentinel["original_client_send"] += 1
        raise RuntimeError(f"original httpx.Client.send reached: {request.url}")

    async def _trap_async_send(self, request, *args, **kwargs):
        sentinel["original_async_send"] += 1
        raise RuntimeError(f"original httpx.AsyncClient.send reached: {request.url}")

    def _trap_http_transport(self, request):
        sentinel["httpx_http_transport"] += 1
        raise RuntimeError(f"httpx HTTPTransport reached: {request.url}")

    async def _trap_async_transport(self, request):
        sentinel["httpx_async_http_transport"] += 1
        raise RuntimeError(f"httpx AsyncHTTPTransport reached: {request.url}")

    def _trap_ddgs(*args, **kwargs):
        sentinel["ddgs_original"] += 1
        raise RuntimeError("original DDGS reached")

    def _record_and_block(kind: str, method: str, url: str) -> None:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        _append_jsonl(
            outbound_log,
            {
                "at": _utc_now(),
                "kind": kind,
                "method": method,
                "url": url,
                "host": host,
            },
        )
        if host not in LOOPBACK_HOSTS:
            raise RuntimeError(f"blocked non-loopback HTTP: {url}")
        raise RuntimeError(f"blocked unexpected loopback HTTP from isolated backend: {url}")

    async def guarded_async_send(self, request, *args, **kwargs):
        _record_and_block("async_send", request.method, str(request.url))

    def guarded_sync_send(self, request, *args, **kwargs):
        _record_and_block("sync_send", request.method, str(request.url))

    def guarded_get(url, *args, **kwargs):
        _record_and_block("httpx.get", "GET", str(url))

    def guarded_request(method, url, *args, **kwargs):
        _record_and_block("httpx.request", str(method), str(url))

    class BlockedDDGS:
        def __init__(self, *args, **kwargs):
            _append_jsonl(
                outbound_log,
                {"at": _utc_now(), "kind": "ddgs", "op": "init"},
            )
            sentinel["ddgs_blocked"] += 1
            raise RuntimeError("blocked DDGS in isolated runtime")

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def text(self, *args, **kwargs):
            raise RuntimeError("blocked DDGS.text in isolated runtime")

    # Last-line sentinels: if a guard is missed, fail closed locally.
    httpx.get = guarded_get  # type: ignore[assignment]
    httpx.request = guarded_request  # type: ignore[assignment]
    search_mod.DDGS = BlockedDDGS
    for mod_name in ("ddgs", "duckduckgo_search"):
        mod = sys.modules.get(mod_name)
        if mod is not None and hasattr(mod, "DDGS"):
            setattr(mod, "DDGS", BlockedDDGS)

    if block_httpx_clients:
        if original_http_transport is not None:
            httpx.HTTPTransport.handle_request = _trap_http_transport  # type: ignore[method-assign]
        if original_async_transport is not None:
            httpx.AsyncHTTPTransport.handle_async_request = _trap_async_transport  # type: ignore[method-assign]
        httpx.AsyncClient.send = guarded_async_send  # type: ignore[method-assign]
        httpx.Client.send = guarded_sync_send  # type: ignore[method-assign]

    sentinel_path = outbound_log.parent / "outbound_sentinel.json"
    _write_json(sentinel_path, sentinel)

    return {
        "outbound_sentinel": sentinel,
        "outbound_sentinel_path": str(sentinel_path),
        "original_httpx_send": original_async_send,
        "original_httpx_client_send": original_sync_send,
        "original_httpx_get": original_get,
        "original_httpx_request": original_request,
        "original_ddgs": original_ddgs,
        "trap_sync_send": _trap_sync_send,
        "trap_async_send": _trap_async_send,
        "trap_ddgs": _trap_ddgs,
    }


def _probe_outbound_exits(capture_dir: Path, fakes: dict[str, Any]) -> dict[str, Any]:
    import httpx
    from app.services.search import DDGS, web_search

    sentinel = fakes["outbound_sentinel"]
    cases: dict[str, Any] = {}

    def _try(name: str, fn) -> None:
        try:
            fn()
            cases[name] = {"blocked": False, "raised": None}
        except RuntimeError as exc:
            text = str(exc)
            cases[name] = {
                "blocked": text.startswith("blocked "),
                "raised": text,
            }
        except Exception as exc:  # pragma: no cover - unexpected probe failure
            cases[name] = {
                "blocked": False,
                "raised": f"{type(exc).__name__}: {exc}",
            }

    _try(
        "httpx_get_wttr",
        lambda: httpx.get("http://wttr.in/Akihabara?format=j1", timeout=0.2),
    )

    def _client_example() -> None:
        with httpx.Client(timeout=0.2) as client:
            client.get("https://example.com/")

    _try("httpx_client_example", _client_example)

    async def _async_example() -> None:
        async with httpx.AsyncClient(timeout=0.2) as client:
            await client.get("https://example.org/")

    _try("httpx_async_example", lambda: asyncio.run(_async_example()))
    _try("ddgs_init", lambda: DDGS())

    weather_text = web_search("东京天气怎么样")
    cases["web_search_weather_fallback"] = {
        "blocked": "blocked DDGS" in weather_text,
        "text": weather_text[:400],
        "ddgs_blocked": sentinel.get("ddgs_blocked", 0),
    }

    _write_json(Path(fakes["outbound_sentinel_path"]), dict(sentinel))
    log_path = capture_dir / "outbound_http.jsonl"
    log_lines = []
    if log_path.is_file():
        log_lines = [
            json.loads(line)
            for line in log_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    kinds = [row.get("kind") for row in log_lines]
    transport_idle = (
        sentinel.get("httpx_http_transport", 0) == 0
        and sentinel.get("httpx_async_http_transport", 0) == 0
        and sentinel.get("original_client_send", 0) == 0
        and sentinel.get("original_async_send", 0) == 0
        and sentinel.get("ddgs_original", 0) == 0
    )
    named_blocked = all(
        bool(cases[name].get("blocked"))
        for name in (
            "httpx_get_wttr",
            "httpx_client_example",
            "httpx_async_example",
            "ddgs_init",
            "web_search_weather_fallback",
        )
    )
    return {
        "ok": named_blocked and transport_idle,
        "command": "probe-outbound",
        "encoding_marker": "隔离闭环",
        "reply_ja": REPLY_JA,
        "stdout_encoding": getattr(sys.stdout, "encoding", None),
        "cases": cases,
        "sentinel": dict(sentinel),
        "log_kinds": kinds,
        "blocked_before_transport": named_blocked and transport_idle,
        "outbound_log": str(log_path),
    }


def cmd_prepare(args: argparse.Namespace) -> None:
    run_root = deep_resolve(Path(args.run_root))
    if run_root.exists() and any(run_root.iterdir()) and not args.force:
        _fail("run_root_exists", f"run root already exists: {run_root}")
    live_roots = production_live_roots()
    live = containing_live_root(run_root, live_roots)
    if live is not None:
        _fail("live_root", f"run root aliases live data: {live}")
    paths = run_paths(run_root)
    for key in ("snapshot", "preview"):
        if paths[key].exists() and args.force:
            pass
    os.environ.pop("AMADEUS_DATA_DIR", None)
    os.environ.pop("AMADEUS_DB_PATH", None)
    os.environ["PYTHON_DOTENV_DISABLED"] = "1"
    os.environ["AMADEUS_CREDENTIAL_BACKEND"] = "memory"
    sys.path.insert(0, str(BACKEND_ROOT / "tests"))
    from test_memory_candidate_builder import _make_preview, _make_snapshot
    from app.services.memory_v11 import candidate as cand

    snapshot = _make_snapshot(run_root)
    preview = _make_preview(snapshot, run_root / "inputs")
    output = paths["candidate"]
    if output.exists():
        _fail("candidate_exists", f"refusing to overwrite {output}")
    result = cand.build_memory_candidate(
        snapshot_dir=snapshot, preview_path=preview, output_dir=output
    )
    if not result.get("ok"):
        _fail("candidate_build_failed", json.dumps(result, ensure_ascii=False))
    inspection = inspect_published_candidate(output, live_roots=live_roots)
    meta = {
        "run_id": run_root.name,
        "created_at": _utc_now(),
        "worktree": str(WORKTREE_ROOT),
        "backend": str(BACKEND_ROOT),
        "app_file_expected": str(BACKEND_ROOT / "app" / "__init__.py"),
        "snapshot": str(snapshot),
        "preview": str(preview),
        "candidate": str(output),
        "inspection": inspection,
        "python": sys.executable,
    }
    _write_json(paths["meta"], meta)
    print(json.dumps({"ok": True, "command": "prepare", **meta}, ensure_ascii=False, indent=2))


def _apply_isolated_process_env(
    *,
    run_root: Path,
    candidate: Path,
    backend_port: int,
    frontend_port: int,
) -> tuple[dict[str, Path], list[Path]]:
    paths = run_paths(run_root)
    inherited = dict(os.environ)
    live_roots = production_live_roots(inherited)
    env = isolated_environ(
        run_root=run_root,
        candidate=candidate,
        backend_port=backend_port,
        frontend_port=frontend_port,
        inherited=inherited,
    )
    for key in list(os.environ):
        if key not in env:
            os.environ.pop(key, None)
    os.environ.update(env)
    if "PYTEST_CURRENT_TEST" in os.environ:
        _fail("pytest_env", "refusing to skip lifespan behind PYTEST_CURRENT_TEST")
    return paths, live_roots


def _load_prepared_run(args: argparse.Namespace) -> tuple[Path, dict[str, Path], Path, int, int]:
    run_root = deep_resolve(Path(args.run_root))
    paths = run_paths(run_root)
    if not paths["meta"].is_file():
        _fail("missing_run", f"run.json missing; prepare first: {paths['meta']}")
    meta = _read_json(paths["meta"])
    candidate = Path(meta["candidate"])
    return run_root, paths, candidate, int(args.backend_port), int(args.frontend_port)


def _import_isolated_app(candidate: Path, live_roots: list[Path]) -> tuple[Any, Any, dict[str, Any]]:
    try:
        inspection = inspect_published_candidate(candidate, live_roots=live_roots)
    except ValueError as exc:
        _fail("invalid_candidate", str(exc))

    import app
    from app.services import credentials as cred_mod

    app_file = Path(app.__file__).resolve()
    expected = (BACKEND_ROOT / "app" / "__init__.py").resolve()
    if app_file != expected:
        _fail("wrong_app_module", f"{app_file} != {expected}")
    if cred_mod.WINDOWS_CREDENTIAL_STORE_CONSTRUCTED:
        _fail("windows_credentials", "Windows Credential Manager store was constructed")
    return app, cred_mod, inspection


def cmd_serve(args: argparse.Namespace) -> None:
    run_root, paths, candidate, backend_port, frontend_port = _load_prepared_run(args)
    require_loopback_port(backend_port, role="backend")
    _paths, live_roots = _apply_isolated_process_env(
        run_root=run_root,
        candidate=candidate,
        backend_port=backend_port,
        frontend_port=frontend_port,
    )
    app, _cred_mod, inspection = _import_isolated_app(candidate, live_roots)
    fakes = _install_external_fakes(paths["captures"])
    from app import config as app_config
    from app.db import get_data_root
    from app.main import app as fastapi_app

    app_config.env_path = paths["env_file"]
    data_root = get_data_root()
    if data_root != candidate.resolve():
        _fail("data_root_mismatch", f"{data_root} != {candidate.resolve()}")

    import uvicorn

    print(
        json.dumps(
            {
                "event": "serve_starting",
                "app_file": str(Path(app.__file__).resolve()),
                "data_root": str(data_root),
                "backend_port": backend_port,
                "frontend_port": frontend_port,
                "windows_store_constructed": fakes["windows_store_constructed"],
                "dotenv_disabled": os.environ.get("PYTHON_DOTENV_DISABLED"),
                "memory_mode": os.environ.get("AMADEUS_MEMORY_MODE"),
                "inspection": inspection,
                "pid": os.getpid(),
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    (paths["root"] / "serve.pid").write_text(str(os.getpid()), encoding="utf-8")
    uvicorn.run(
        fastapi_app,
        host="127.0.0.1",
        port=backend_port,
        log_level="info",
    )


def cmd_probe_outbound(args: argparse.Namespace) -> None:
    """Install the same isolated fakes as serve, then exercise known exits locally."""
    run_root, paths, candidate, backend_port, frontend_port = _load_prepared_run(args)
    _paths, live_roots = _apply_isolated_process_env(
        run_root=run_root,
        candidate=candidate,
        backend_port=backend_port,
        frontend_port=frontend_port,
    )
    _app, _cred_mod, inspection = _import_isolated_app(candidate, live_roots)
    fakes = _install_external_fakes(paths["captures"])
    from app.db import get_data_root

    data_root = get_data_root()
    if data_root != candidate.resolve():
        _fail("data_root_mismatch", f"{data_root} != {candidate.resolve()}")
    report = _probe_outbound_exits(paths["captures"], fakes)
    report["inspection_ok"] = bool(inspection)
    report["data_root"] = str(data_root)
    _write_json(paths["captures"] / "outbound_probe.json", report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not report.get("ok"):
        _fail("outbound_not_blocked", "known HTTP/search exits were not blocked before transport")


def cmd_encoding_probe(_args: argparse.Namespace) -> None:
    print(
        json.dumps(
            {
                "ok": True,
                "command": "encoding-probe",
                "encoding_marker": "隔离闭环",
                "reply_ja": REPLY_JA,
                "stdout_encoding": getattr(sys.stdout, "encoding", None),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def _spawn_hidden(command: list[str], *, cwd: Path, env: dict[str, str], log_path: Path) -> subprocess.Popen:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    handle = log_path.open("ab")
    kwargs: dict[str, Any] = {
        "cwd": str(cwd),
        "env": env,
        "stdout": handle,
        "stderr": subprocess.STDOUT,
        "stdin": subprocess.DEVNULL,
    }
    if os.name == "nt":
        kwargs["creationflags"] = CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
    proc = subprocess.Popen(command, **kwargs)
    proc._b2_log_handle = handle  # type: ignore[attr-defined]
    return proc


def _wait_http(url: str, *, timeout: float, contain: str | None = None) -> None:
    import urllib.error
    import urllib.request

    deadline = time.monotonic() + timeout
    last = ""
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                body = response.read().decode("utf-8", "replace")
                if response.status == 200 and (contain is None or contain in body):
                    return
                last = f"status={response.status} body={body[:200]}"
        except Exception as exc:
            last = str(exc)
        time.sleep(0.25)
    _fail("startup_timeout", f"{url} not ready: {last}")


def cmd_start(args: argparse.Namespace) -> None:
    run_root = deep_resolve(Path(args.run_root))
    paths = run_paths(run_root)
    if not paths["meta"].is_file():
        _fail("missing_run", "prepare first")
    meta = _read_json(paths["meta"])
    candidate = Path(meta["candidate"])
    live_roots = production_live_roots()
    try:
        inspect_published_candidate(candidate, live_roots=live_roots)
    except ValueError as exc:
        _fail("invalid_candidate", str(exc))
    backend_port = int(args.backend_port)
    frontend_port = int(args.frontend_port)
    require_loopback_port(backend_port, role="backend")
    if not getattr(args, "backend_only", False):
        require_loopback_port(frontend_port, role="frontend")
    env = isolated_environ(
        run_root=run_root,
        candidate=candidate,
        backend_port=backend_port,
        frontend_port=frontend_port,
    )
    python = args.python or sys.executable
    serve_cmd = [
        python,
        str(SCRIPT_DIR / "memory_candidate_runtime.py"),
        "serve",
        "--run-root",
        str(run_root),
        "--backend-port",
        str(backend_port),
        "--frontend-port",
        str(frontend_port),
    ]
    backend = _spawn_hidden(
        serve_cmd,
        cwd=BACKEND_ROOT,
        env=env,
        log_path=paths["logs"] / "backend.log",
    )
    frontend = None
    if not getattr(args, "backend_only", False):
        frontend_env = dict(os.environ)
        frontend_env.update(
            {
                "AMADEUS_BACKEND_ORIGIN": f"http://127.0.0.1:{backend_port}",
                "AMADEUS_FRONTEND_PORT": str(frontend_port),
                "BROWSER": "none",
            }
        )
        for key in PROVIDER_ENV_KEYS:
            frontend_env.pop(key, None)
        npm = "npm.cmd" if os.name == "nt" else "npm"
        frontend = _spawn_hidden(
            [npm, "run", "dev", "--", "--host", "127.0.0.1", "--port", str(frontend_port), "--strictPort"],
            cwd=WORKTREE_ROOT / "desktop",
            env=frontend_env,
            log_path=paths["logs"] / "frontend.log",
        )
    pids = {
        "backend_pid": backend.pid,
        "frontend_pid": frontend.pid if frontend is not None else 0,
        "backend_port": backend_port,
        "frontend_port": frontend_port,
        "python": python,
        "started_at": _utc_now(),
        "run_root": str(run_root),
        "candidate": str(candidate),
        "backend_only": bool(getattr(args, "backend_only", False)),
    }
    _write_json(paths["pids"], pids)
    try:
        _wait_http(f"http://127.0.0.1:{backend_port}/health", timeout=60, contain='"ok"')
        if frontend is not None:
            _wait_http(f"http://127.0.0.1:{frontend_port}/", timeout=90)
    except SystemExit:
        cmd_stop(argparse.Namespace(run_root=str(run_root)))
        raise
    print(json.dumps({"ok": True, "command": "start", **pids}, indent=2))


def _pid_running(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            check=False,
        )
        return str(pid) in (result.stdout or "")
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _terminate_pid(pid: int) -> None:
    if not _pid_running(pid):
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T"],
            capture_output=True,
            check=False,
        )
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline and _pid_running(pid):
            time.sleep(0.2)
        if _pid_running(pid):
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                capture_output=True,
                check=False,
            )
        return
    os.kill(pid, 15)


def cmd_stop(args: argparse.Namespace) -> None:
    run_root = deep_resolve(Path(args.run_root))
    paths = run_paths(run_root)
    if not paths["pids"].is_file():
        print(json.dumps({"ok": True, "command": "stop", "note": "no pids file"}))
        return
    pids = _read_json(paths["pids"])
    serve_pid_file = paths["root"] / "serve.pid"
    extra_pid = 0
    if serve_pid_file.is_file():
        try:
            extra_pid = int(serve_pid_file.read_text(encoding="utf-8").strip() or "0")
        except ValueError:
            extra_pid = 0
    for key in ("frontend_pid", "backend_pid"):
        pid = int(pids.get(key) or 0)
        if pid:
            _terminate_pid(pid)
    if extra_pid and extra_pid not in {
        int(pids.get("frontend_pid") or 0),
        int(pids.get("backend_pid") or 0),
    }:
        _terminate_pid(extra_pid)
    still = {
        key: int(pids.get(key) or 0)
        for key in ("frontend_pid", "backend_pid")
        if _pid_running(int(pids.get(key) or 0))
    }
    pids["stopped_at"] = _utc_now()
    pids["still_running"] = still
    _write_json(paths["pids"], pids)
    if still:
        _fail("stop_incomplete", f"still running: {still}")
    print(json.dumps({"ok": True, "command": "stop", **pids}, indent=2))


def cmd_restart(args: argparse.Namespace) -> None:
    cmd_stop(args)
    cmd_start(args)


def _core_facts_block(system_prompt: str) -> str:
    start = system_prompt.find("CORE FACTS")
    if start < 0:
        return ""
    rest = system_prompt[start:]
    end = rest.find("\nEPISODIC MEMORY")
    return rest if end < 0 else rest[:end]


def _last_chat_capture(path: Path) -> dict[str, Any]:
    lines = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if not lines:
        raise AssertionError(f"no chat captures in {path}")
    return lines[-1]


async def _ws_session(*, backend_port: int, session_id: str, enable_tts: bool = False):
    import websockets

    uri = f"ws://127.0.0.1:{backend_port}/ws/chat?session_id={session_id}"
    ws = await websockets.connect(uri, origin='http://127.0.0.1:1420', open_timeout=10)
    await ws.send(
        json.dumps(
            {
                "type": "auth",
                "conversation_mode": "draft",
                "provider_id": "deepseek",
                "model": "deepseek-v4-flash",
                "enable_tts": enable_tts,
                "temperature": 0.0,
                "worldline": "steins_gate",
                "default_identity_mode": "okabe",
                "self_name": "",
                "client": "desktop",
                "protocol_version": 2,
            }
        )
    )
    return ws


async def _drain_ws(ws, *, timeout: float = 0.25) -> None:
    while True:
        try:
            await asyncio.wait_for(ws.recv(), timeout=timeout)
        except asyncio.TimeoutError:
            return


async def _ws_send_turn(ws, text: str, *, timeout: float = 30.0) -> list[dict[str, Any]]:
    await _drain_ws(ws)
    events: list[dict[str, Any]] = []
    await ws.send(json.dumps({"type": "chat.send", "content": text}))
    deadline = time.monotonic() + timeout
    saw_thinking = False
    while time.monotonic() < deadline:
        remaining = deadline - time.monotonic()
        raw = await asyncio.wait_for(ws.recv(), timeout=max(0.1, remaining))
        payload = json.loads(raw)
        events.append(payload)
        if payload.get("type") == "error":
            break
        if payload.get("type") == "status" and payload.get("state") == "thinking":
            saw_thinking = True
        if payload.get("type") == "status" and payload.get("state") == "done" and saw_thinking:
            break
        if payload.get("type") == "turn.completed" and saw_thinking:
            break
    if not saw_thinking:
        raise AssertionError(f"turn did not start thinking: {events!r}")
    return events


def _wait_fact(
    *,
    backend_port: int,
    session_id: str,
    marker: str,
    timeout: float = 40.0,
) -> dict[str, Any]:
    import urllib.parse
    import urllib.request

    params = urllib.parse.urlencode(
        {
            "session_id": session_id,
            "worldline": "steins_gate",
            "identity_mode": "okabe",
            "query": marker,
        }
    )
    url = f"http://127.0.0.1:{backend_port}/api/memory/facts?{params}"
    deadline = time.monotonic() + timeout
    last = ""
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=5) as response:
                body = json.loads(response.read().decode("utf-8"))
            cards = [
                row
                for row in (body.get("facts") or [])
                if marker in str(row.get("display_text") or "")
            ]
            if cards:
                return cards[0]
            last = json.dumps(body, ensure_ascii=False)[:400]
        except Exception as exc:
            last = str(exc)
        time.sleep(0.5)
    raise AssertionError(f"fact {marker} not visible via API before timeout: {last}")


def _memory_scope(session_id: str) -> dict[str, str]:
    return {
        "session_id": session_id,
        "worldline": "steins_gate",
        "identity_mode": "okabe",
    }


def _patch_fact(backend_port: int, session_id: str, fact_id: str, expected_version: int) -> None:
    import urllib.error
    import urllib.parse
    import urllib.request

    params = urllib.parse.urlencode(_memory_scope(session_id))
    payload = json.dumps(
        {
            "display_text": f"{REVISED_MARKER} 改成更明确的芝麻偏好",
            "expected_version": int(expected_version),
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        f"http://127.0.0.1:{backend_port}/api/memory/facts/{fact_id}?{params}",
        data=payload,
        method="PATCH",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        if response.status != 200:
            raise RuntimeError(f"patch failed: {response.status}")


def _delete_fact(backend_port: int, session_id: str, fact_id: str, expected_version: int) -> None:
    import urllib.parse
    import urllib.request

    params = urllib.parse.urlencode(
        {**_memory_scope(session_id), "expected_version": str(expected_version)}
    )
    request = urllib.request.Request(
        f"http://127.0.0.1:{backend_port}/api/memory/facts/{fact_id}?{params}",
        method="DELETE",
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        if response.status != 200:
            raise RuntimeError(f"delete failed: {response.status}")


def cmd_verify_api(args: argparse.Namespace) -> None:
    run_root = deep_resolve(Path(args.run_root))
    paths = run_paths(run_root)
    pids = _read_json(paths["pids"])
    backend_port = int(pids["backend_port"])
    session_id = args.session_id or f"b2-{uuid.uuid4()}"
    chat_log = paths["captures"] / "provider_chat.jsonl"
    if chat_log.exists():
        chat_log.write_text("", encoding="utf-8")

    async def _run() -> dict[str, Any]:
        ws = await _ws_session(backend_port=backend_port, session_id=session_id)
        try:
            first = await _ws_send_turn(
                ws,
                f"我长期喜欢黑芝麻汤圆，这是稳定偏好。标记 {SESAME_MARKER}。",
            )
            second = await _ws_send_turn(
                ws,
                f"另外我还长期收集胶片相机。标记 {DELETE_MARKER}。",
            )
            sesame = await asyncio.to_thread(
                _wait_fact,
                backend_port=backend_port,
                session_id=session_id,
                marker=SESAME_MARKER,
            )
            delete = await asyncio.to_thread(
                _wait_fact,
                backend_port=backend_port,
                session_id=session_id,
                marker=DELETE_MARKER,
            )
            third = await _ws_send_turn(
                ws,
                f"{SESAME_MARKER} {DELETE_MARKER} 长期偏好还在吗",
            )
            original_recall = _last_chat_capture(chat_log)
            original_block = _core_facts_block(str(original_recall.get("system_prompt") or ""))
            if SESAME_MARKER not in original_block:
                raise AssertionError(f"CORE FACTS missing {SESAME_MARKER}: {original_block[:800]}")
            sesame_id = str(sesame["fact_id"])
            delete_id = str(delete["fact_id"])
            await asyncio.to_thread(
                _patch_fact,
                backend_port,
                session_id,
                sesame_id,
                int(sesame.get("active_version") or 1),
            )
            await asyncio.to_thread(
                _delete_fact,
                backend_port,
                session_id,
                delete_id,
                int(delete.get("active_version") or 1),
            )
            fourth = await _ws_send_turn(
                ws,
                f"{REVISED_MARKER} {SESAME_MARKER}",
            )
            return {
                "first": first,
                "second": second,
                "third": third,
                "fourth": fourth,
                "sesame": sesame,
                "delete": delete,
                "original_block": original_block,
            }
        finally:
            await ws.close()

    bundle = asyncio.run(_run())
    events = bundle["first"] + bundle["second"]
    ja_ok = any(REPLY_JA in json.dumps(event, ensure_ascii=False) for event in events)
    if not ja_ok:
        _fail("no_japanese_reply", json.dumps(events, ensure_ascii=False)[:800])
    sesame = bundle["sesame"]
    delete = bundle["delete"]
    import urllib.parse
    import urllib.request

    proj_params = urllib.parse.urlencode(
        {
            "session_id": session_id,
            "worldline": "steins_gate",
            "identity_mode": "okabe",
            "view": "overview",
            "query": SESAME_MARKER,
        }
    )
    with urllib.request.urlopen(
        f"http://127.0.0.1:{backend_port}/api/memory/graph/projection?{proj_params}",
        timeout=10,
    ) as response:
        projection = json.loads(response.read().decode("utf-8"))
    sesame_id = str(sesame.get("fact_id"))
    fact_nodes = [
        node
        for node in (projection.get("nodes") or [])
        if node.get("kind") == "fact" and str(node.get("fact_id")) == sesame_id
    ]
    if len(fact_nodes) != 1:
        _fail("projection_mismatch", json.dumps({"sesame": sesame, "projection": projection}, ensure_ascii=False)[:1500])
    captured = _last_chat_capture(chat_log)
    block = _core_facts_block(str(captured.get("system_prompt") or ""))
    if REVISED_MARKER not in block:
        _fail("revised_core_facts_missing", block[:1000] or str(captured)[:1000])
    if DELETE_MARKER in block:
        _fail("deleted_fact_still_in_core_facts", block[:1000])
    report = {
        "ok": True,
        "command": "verify-api",
        "session_id": session_id,
        "sesame_fact_id": sesame.get("fact_id"),
        "delete_fact_id": delete.get("fact_id"),
        "projection_id": fact_nodes[0].get("projection_id"),
        "japanese_reply": REPLY_JA,
        "core_facts_contains_sesame": SESAME_MARKER in bundle["original_block"],
        "core_facts_contains_delete": DELETE_MARKER in bundle["original_block"],
        "revised_core_facts": REVISED_MARKER in block,
        "deleted_absent_after_edit": DELETE_MARKER not in block,
        "backend_port": backend_port,
        "how_not_false_positive": {
            "chat": "WS to 127.0.0.1:backend_port, captured adapter messages after real assembly",
            "fact": "API fact_id plus display_text marker, not query echo",
            "recall": "next-turn captured CORE FACTS contains marker",
            "worker": "no manual process_due_jobs_once; waited on natural poll",
        },
    }
    _write_json(paths["captures"] / "verify_api.json", report)
    print(json.dumps(report, ensure_ascii=False, indent=2))


def cmd_fail_start(args: argparse.Namespace) -> None:
    case = args.case
    run_root = deep_resolve(Path(args.run_root))
    live_roots = production_live_roots()
    if case == "missing-manifest":
        bogus = run_root / "not-a-candidate"
        bogus.mkdir(parents=True, exist_ok=True)
        try:
            inspect_published_candidate(bogus, live_roots=live_roots)
        except ValueError as exc:
            print(json.dumps({"ok": True, "case": case, "error": str(exc)}, ensure_ascii=False, indent=2))
            return
        _fail("expected_rejection", "missing manifest was accepted")
    if case == "live-root":
        if not live_roots:
            _fail("no_live_root", "cannot prove live-root rejection without APPDATA/home")
        try:
            inspect_published_candidate(live_roots[0], live_roots=live_roots)
        except ValueError as exc:
            print(json.dumps({"ok": True, "case": case, "error": str(exc)}, ensure_ascii=False, indent=2))
            return
        _fail("expected_rejection", "live root was accepted")
    if case == "production-port":
        try:
            require_loopback_port(8000, role="backend")
        except SystemExit as exc:
            if int(getattr(exc, "code", 2) or 2) == 2:
                print(json.dumps({"ok": True, "case": case, "error": "production_port_refused"}, indent=2))
                return
            raise
        _fail("expected_rejection", "port 8000 was accepted")
    if case == "port-busy":
        port = int(args.backend_port)
        holder = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        holder.bind(("127.0.0.1", port))
        holder.listen(1)
        try:
            require_loopback_port(port, role="backend")
        except SystemExit:
            print(json.dumps({"ok": True, "case": case, "port": port}, indent=2))
            return
        finally:
            holder.close()
        _fail("expected_rejection", f"busy port {port} was accepted")
    _fail("unknown_case", case)


def cmd_print_env(args: argparse.Namespace) -> None:
    run_root = deep_resolve(Path(args.run_root))
    paths = run_paths(run_root)
    candidate = Path(args.candidate) if args.candidate else paths["candidate"]
    env = isolated_environ(
        run_root=run_root,
        candidate=candidate,
        backend_port=int(args.backend_port),
        frontend_port=int(args.frontend_port),
    )
    print(json.dumps(env, ensure_ascii=False, indent=2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Isolated complete-candidate runtime")
    sub = parser.add_subparsers(dest="command", required=True)

    def add_run(p: argparse.ArgumentParser) -> None:
        p.add_argument("--run-root", required=True)
        p.add_argument("--backend-port", type=int, default=DEFAULT_BACKEND_PORT)
        p.add_argument("--frontend-port", type=int, default=DEFAULT_FRONTEND_PORT)

    prepare = sub.add_parser("prepare")
    add_run(prepare)
    prepare.add_argument("--force", action="store_true")
    prepare.set_defaults(func=cmd_prepare)

    serve = sub.add_parser("serve")
    add_run(serve)
    serve.set_defaults(func=cmd_serve)

    start = sub.add_parser("start")
    add_run(start)
    start.add_argument("--python", default="")
    start.add_argument("--backend-only", action="store_true")
    start.set_defaults(func=cmd_start)

    stop = sub.add_parser("stop")
    stop.add_argument("--run-root", required=True)
    stop.set_defaults(func=cmd_stop)

    restart = sub.add_parser("restart")
    add_run(restart)
    restart.add_argument("--python", default="")
    restart.add_argument("--backend-only", action="store_true")
    restart.set_defaults(func=cmd_restart)

    verify = sub.add_parser("verify-api")
    add_run(verify)
    verify.add_argument("--session-id", default="")
    verify.set_defaults(func=cmd_verify_api)

    fail = sub.add_parser("fail-start")
    add_run(fail)
    fail.add_argument(
        "--case",
        required=True,
        choices=("missing-manifest", "live-root", "production-port", "port-busy"),
    )
    fail.set_defaults(func=cmd_fail_start)

    show = sub.add_parser("print-env")
    add_run(show)
    show.add_argument("--candidate", default="")
    show.set_defaults(func=cmd_print_env)

    probe = sub.add_parser("probe-outbound")
    add_run(probe)
    probe.set_defaults(func=cmd_probe_outbound)

    encoding = sub.add_parser("encoding-probe")
    encoding.set_defaults(func=cmd_encoding_probe)
    return parser


def main(argv: list[str] | None = None) -> None:
    _configure_utf8_stdio()
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
