"""B2 isolated lifespan runtime: start/stop, fake boundaries, natural worker."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = BACKEND_ROOT / "scripts" / "memory_candidate_runtime.py"
PYTHON = Path(sys.executable)

sys.path.insert(0, str(BACKEND_ROOT / "scripts"))
import memory_candidate_runtime as b2  # noqa: E402


def _cli(
    *argv: str,
    check: bool = True,
    timeout: int = 120,
    extra_env: dict[str, str] | None = None,
    drop_env: tuple[str, ...] = (),
) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(BACKEND_ROOT)
    env["PYTHON_DOTENV_DISABLED"] = "1"
    env["AMADEUS_CREDENTIAL_BACKEND"] = "memory"
    env.pop("AMADEUS_DATA_DIR", None)
    env.pop("AMADEUS_DB_PATH", None)
    env.pop("PYTEST_CURRENT_TEST", None)
    for key in drop_env:
        env.pop(key, None)
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [str(PYTHON), str(SCRIPT), *argv],
        cwd=str(BACKEND_ROOT),
        env=env,
        text=True,
        encoding="utf-8",
        errors="strict",
        capture_output=True,
        timeout=timeout,
        check=check,
    )


def _job_rows(candidate: Path) -> list[dict]:
    conn = sqlite3.connect(str(candidate / "worldlines" / "sg" / "memory.sqlite3"))
    conn.row_factory = sqlite3.Row
    try:
        return [
            dict(r)
            for r in conn.execute(
                """SELECT job_id, state, retryable, last_error_code, source_message_id
                     FROM memory_ingest_jobs"""
            )
        ]
    finally:
        conn.close()


def test_app_module_resolves_to_this_worktree():
    import app

    assert Path(app.__file__).resolve() == BACKEND_ROOT / "app" / "__init__.py"


def test_inspect_rejects_missing_manifest_and_live_root(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(ValueError, match="missing manifest"):
        b2.inspect_published_candidate(empty, live_roots=b2.production_live_roots())

    live_roots = b2.production_live_roots()
    existing_live = [root for root in live_roots if root.exists()]
    if existing_live:
        with pytest.raises(ValueError, match="live data root"):
            b2.inspect_published_candidate(existing_live[0], live_roots=live_roots)


def test_fail_start_cases(tmp_path):
    run_root = tmp_path / "fail"
    missing = _cli(
        "fail-start",
        "--run-root",
        str(run_root),
        "--case",
        "missing-manifest",
    )
    assert missing.returncode == 0, missing.stderr
    live = _cli("fail-start", "--run-root", str(run_root), "--case", "live-root")
    assert live.returncode == 0, live.stderr
    prod = _cli("fail-start", "--run-root", str(run_root), "--case", "production-port")
    assert prod.returncode == 0, prod.stderr
    busy = _cli(
        "fail-start",
        "--run-root",
        str(run_root),
        "--backend-port",
        "8765",
        "--case",
        "port-busy",
    )
    assert busy.returncode == 0, busy.stderr + busy.stdout


def test_isolated_import_skips_dotenv_and_windows_store():
    code = r"""
import json, os
from pathlib import Path
print("dotenv_disabled", os.environ.get("PYTHON_DOTENV_DISABLED"))
print("cred_backend", os.environ.get("AMADEUS_CREDENTIAL_BACKEND"))
from app import config
from app.services import credentials as cred
print(json.dumps({
    "windows_store": cred.WINDOWS_CREDENTIAL_STORE_CONSTRUCTED,
    "store_class": type(cred.credential_store).__name__,
    "deepseek_key_empty": config.DEEPSEEK_API_KEY == "",
    "app_file": str(Path(__import__('app').__file__).resolve()),
    "env_path": str(config.env_path),
}))
"""
    env = os.environ.copy()
    env["PYTHONPATH"] = str(BACKEND_ROOT)
    env["PYTHON_DOTENV_DISABLED"] = "1"
    env["AMADEUS_CREDENTIAL_BACKEND"] = "memory"
    env.pop("DEEPSEEK_API_KEY", None)
    env.pop("AMADEUS_DATA_DIR", None)
    env.pop("AMADEUS_DB_PATH", None)
    env.pop("PYTEST_CURRENT_TEST", None)
    result = subprocess.run(
        [str(PYTHON), "-c", code],
        cwd=str(BACKEND_ROOT),
        env=env,
        text=True,
        encoding="utf-8",
        errors="strict",
        capture_output=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload["windows_store"] is False
    assert payload["store_class"] == "InMemoryCredentialStore"
    assert payload["deepseek_key_empty"] is True
    assert payload["app_file"] == str(BACKEND_ROOT / "app" / "__init__.py")


def test_prepare_start_natural_worker_restart(tmp_path):
    run_root = tmp_path / "b2-run"
    dotenv = BACKEND_ROOT / ".env"
    mtime_before = dotenv.stat().st_mtime if dotenv.exists() else None
    prepared = _cli("prepare", "--run-root", str(run_root), timeout=180)
    assert prepared.returncode == 0, prepared.stderr + prepared.stdout
    meta = json.loads((run_root / "run.json").read_text(encoding="utf-8"))
    candidate = Path(meta["candidate"])
    frozen_before = [
        row
        for row in _job_rows(candidate)
        if row.get("last_error_code") == "candidate_build_freeze"
    ]
    assert frozen_before
    assert all(row["state"] == "cancelled" and int(row["retryable"] or 0) == 0 for row in frozen_before)

    started = _cli(
        "start",
        "--run-root",
        str(run_root),
        "--backend-port",
        "8010",
        "--frontend-port",
        "1422",
        "--backend-only",
        timeout=90,
        check=False,
    )
    if started.returncode != 0:
        pytest.fail(started.stderr + "\n" + started.stdout)
    try:
        verified = _cli(
            "verify-api",
            "--run-root",
            str(run_root),
            "--session-id",
            "b2-pytest-owner",
            timeout=90,
            check=False,
        )
        assert verified.returncode == 0, verified.stderr + verified.stdout
        report = json.loads((run_root / "captures" / "verify_api.json").read_text(encoding="utf-8"))
        assert report["sesame_fact_id"]
        assert report["delete_fact_id"]
        assert report["core_facts_contains_sesame"] is True
        assert report["revised_core_facts"] is True
        assert report["deleted_absent_after_edit"] is True
        log = (run_root / "logs" / "backend.log").read_text(encoding="utf-8", errors="replace")
        assert "worker started" in log
        assert "serve_starting" in log
    finally:
        _cli("stop", "--run-root", str(run_root), check=False)

    if mtime_before is not None:
        assert dotenv.stat().st_mtime == mtime_before

    frozen_after = {
        row["job_id"]: row
        for row in _job_rows(candidate)
        if row.get("last_error_code") == "candidate_build_freeze"
    }
    for job_id, row in {r["job_id"]: r for r in frozen_before}.items():
        assert frozen_after[job_id]["state"] == "cancelled"
        assert int(frozen_after[job_id]["retryable"] or 0) == 0

    restarted = _cli(
        "start",
        "--run-root",
        str(run_root),
        "--backend-port",
        "8010",
        "--backend-only",
        timeout=90,
        check=False,
    )
    assert restarted.returncode == 0, restarted.stderr + restarted.stdout
    try:
        import urllib.parse
        import urllib.request

        params = urllib.parse.urlencode(
            {
                "session_id": "b2-pytest-owner",
                "worldline": "steins_gate",
                "identity_mode": "okabe",
                "query": "B2RT_SESAME_V2",
            }
        )
        url = f"http://127.0.0.1:8010/api/memory/facts?{params}"
        deadline = time.monotonic() + 15
        body = {}
        while time.monotonic() < deadline:
            with urllib.request.urlopen(url, timeout=5) as response:
                body = json.loads(response.read().decode("utf-8"))
            if any("B2RT_SESAME_V2" in str(row.get("display_text") or "") for row in body.get("facts") or []):
                break
            time.sleep(0.4)
        assert any(
            "B2RT_SESAME_V2" in str(row.get("display_text") or "") for row in body.get("facts") or []
        ), body
        report = json.loads((run_root / "captures" / "verify_api.json").read_text(encoding="utf-8"))
        assert any(row.get("fact_id") == report["sesame_fact_id"] for row in body.get("facts") or [])
    finally:
        _cli("stop", "--run-root", str(run_root), check=False)


def test_cli_utf8_capture_when_parent_pythonutf8_off():
    probed = _cli(
        "encoding-probe",
        extra_env={"PYTHONUTF8": "0"},
        drop_env=("PYTHONIOENCODING",),
        timeout=30,
        check=False,
    )
    assert probed.returncode == 0, probed.stderr + probed.stdout
    payload = json.loads(probed.stdout)
    assert payload["encoding_marker"] == "隔离闭环"
    assert payload["reply_ja"] == "そうですね、それは良いですね。"
    assert "隔离闭环" in probed.stdout
    assert payload["stdout_encoding"] == "utf-8"


def test_outbound_guards_block_sync_httpx_and_ddgs_before_transport(tmp_path):
    run_root = tmp_path / "b2-outbound"
    prepared = _cli("prepare", "--run-root", str(run_root), timeout=180)
    assert prepared.returncode == 0, prepared.stderr + prepared.stdout
    probed = _cli("probe-outbound", "--run-root", str(run_root), timeout=60, check=False)
    assert probed.returncode == 0, probed.stderr + probed.stdout
    report = json.loads((run_root / "captures" / "outbound_probe.json").read_text(encoding="utf-8"))
    assert report["ok"] is True
    assert report["blocked_before_transport"] is True
    assert report["encoding_marker"] == "隔离闭环"
    assert report["cases"]["httpx_get_wttr"]["blocked"] is True
    assert report["cases"]["httpx_client_example"]["blocked"] is True
    assert report["cases"]["httpx_async_example"]["blocked"] is True
    assert report["cases"]["ddgs_init"]["blocked"] is True
    assert report["cases"]["web_search_weather_fallback"]["blocked"] is True
    assert report["sentinel"]["httpx_http_transport"] == 0
    assert report["sentinel"]["httpx_async_http_transport"] == 0
    assert report["sentinel"]["original_client_send"] == 0
    assert report["sentinel"]["original_async_send"] == 0
    assert report["sentinel"]["ddgs_original"] == 0
    assert report["sentinel"]["ddgs_blocked"] >= 2
    kinds = report["log_kinds"]
    assert "httpx.get" in kinds
    assert "sync_send" in kinds
    assert "async_send" in kinds
    assert "ddgs" in kinds
    assert "wttr.in" in (report["cases"]["httpx_get_wttr"].get("raised") or "")
