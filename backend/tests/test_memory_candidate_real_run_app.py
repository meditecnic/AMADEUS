"""App-path budget proof: WS → processor → local model HTTP → natural worker."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = BACKEND_ROOT / "scripts" / "memory_candidate_real_run.py"
PYTHON = Path(sys.executable)


def _cli(*argv: str, check: bool = True, timeout: int = 120) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(BACKEND_ROOT)
    env["PYTHON_DOTENV_DISABLED"] = "1"
    env["AMADEUS_CREDENTIAL_BACKEND"] = "memory"
    env["PYTHONUTF8"] = "1"
    env.pop("AMADEUS_DATA_DIR", None)
    env.pop("AMADEUS_DB_PATH", None)
    env.pop("DEEPSEEK_API_KEY", None)
    env.pop("PYTEST_CURRENT_TEST", None)
    env.pop("AMADEUS_B3_REAL_HTTP", None)
    env.pop("AMADEUS_B3_PROVIDER_KEY", None)
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


def _free_port() -> int:
    holder = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    holder.bind(("127.0.0.1", 0))
    port = int(holder.getsockname()[1])
    holder.close()
    if port in {8000, 1420, 1421}:
        return _free_port()
    return port


def test_ws_processor_local_http_budget_and_product_ops(tmp_path):
    run_root = tmp_path / "b3-app"
    prepared = _cli("prepare", "--run-root", str(run_root), timeout=180, check=False)
    assert prepared.returncode == 0, prepared.stderr + prepared.stdout
    port = _free_port()
    started = _cli(
        "start-local",
        "--run-root",
        str(run_root),
        "--backend-port",
        str(port),
        "--backend-only",
        "--inject",
        "fail_first_extract",
        "--max-http-attempts",
        "16",
        "--max-user-turns",
        "3",
        timeout=90,
        check=False,
    )
    if started.returncode != 0:
        _cli("stop", "--run-root", str(run_root), check=False)
    assert started.returncode == 0, started.stderr + started.stdout
    try:
        verified = _cli(
            "verify-local",
            "--run-root",
            str(run_root),
            "--session-id",
            "b3-pytest-owner",
            timeout=120,
            check=False,
        )
        assert verified.returncode == 0, verified.stderr + verified.stdout
        report = json.loads(
            (run_root / "captures" / "verify_local.json").read_text(encoding="utf-8")
        )
        assert report["ok"] is True
        assert report["fact_id"]
        assert report["retrieval_hit_piece"] is True
        assert report["archive_after_edit_has_harmonica"] is True
        assert report["core_after_edit_has_harmonica"] is True
        assert report["new_session_history_has_extract_utterance"] is False
        assert report["core_after_forget_has_harmonica"] is False
        assert "chat" in report["outbound_kinds"]
        assert "extract" in report["outbound_kinds"]
        assert int(report["user_turns"]) == 3
        attempts = [int(value) for value in (report.get("extract_attempts") or []) if value]
        assert attempts and max(attempts) >= 2
        budget = json.loads(
            (run_root / "captures" / "budget.json").read_text(encoding="utf-8")
        )
        assert budget["schema_version"] == 3
        assert budget["http_attempts"] >= 4
        assert budget["usage_known"] >= 1
        by_kind = budget.get("by_kind") or {}
        assert int(by_kind.get("chat") or 0) >= 1
        assert int(by_kind.get("extract") or 0) >= 2

        proved = _cli(
            "prove-boundaries",
            "--run-root",
            str(run_root),
            timeout=90,
            check=False,
        )
        assert proved.returncode == 0, proved.stderr + proved.stdout
        bounds = json.loads(
            (run_root / "captures" / "prove_boundaries.json").read_text(encoding="utf-8")
        )
        assert bounds["ok"] is True
        assert bounds["turn_exhausted"]["error"] is True
        assert bounds["turn_exhausted"]["chat_did_not_grow"] is True
        assert bounds["http_race"]["mock_delta"] <= 1
        assert bounds["http_exhausted"]["attempts"] == bounds["http_exhausted"]["max_http_attempts"]
    finally:
        _cli("stop", "--run-root", str(run_root), check=False)

    consumed = json.loads((run_root / "captures" / "budget.json").read_text(encoding="utf-8"))
    restarted = _cli(
        "start-local",
        "--run-root",
        str(run_root),
        "--backend-port",
        str(_free_port()),
        "--backend-only",
        "--inject",
        "fail_first_extract",
        "--max-http-attempts",
        "16",
        "--max-user-turns",
        "3",
        timeout=90,
        check=False,
    )
    assert restarted.returncode == 0, restarted.stderr + restarted.stdout
    try:
        serve = json.loads(
            (run_root / "captures" / "local_serve.json").read_text(encoding="utf-8")
        )
        assert serve["budget_http_attempts"] == consumed["http_attempts"]
        assert serve["budget_user_turns"] == consumed["user_turns"]
        proved_again = _cli(
            "prove-boundaries",
            "--run-root",
            str(run_root),
            timeout=60,
            check=False,
        )
        assert proved_again.returncode == 0, proved_again.stderr + proved_again.stdout
        again = json.loads(
            (run_root / "captures" / "prove_boundaries.json").read_text(encoding="utf-8")
        )
        assert again["turn_exhausted"]["chat_did_not_grow"] is True
        assert again["http_exhausted"]["attempts"] == consumed["max_http_attempts"]
    finally:
        _cli("stop", "--run-root", str(run_root), check=False)

    good = (run_root / "captures" / "budget.json").read_text(encoding="utf-8")
    (run_root / "captures" / "budget.json").write_text("{", encoding="utf-8")
    broken = _cli(
        "start-local",
        "--run-root",
        str(run_root),
        "--backend-port",
        str(_free_port()),
        "--backend-only",
        "--max-http-attempts",
        "16",
        "--max-user-turns",
        "3",
        timeout=60,
        check=False,
    )
    assert broken.returncode != 0
    log = (run_root / "logs" / "backend.log").read_text(encoding="utf-8", errors="replace")
    combined = broken.stderr + broken.stdout + log
    assert "budget_state" in combined
    (run_root / "captures" / "budget.json").write_text(good, encoding="utf-8")
    incompatible = json.loads(good)
    incompatible["max_http_attempts"] = 99
    (run_root / "captures" / "budget.json").write_text(
        json.dumps(incompatible), encoding="utf-8"
    )
    mismatch = _cli(
        "start-local",
        "--run-root",
        str(run_root),
        "--backend-port",
        str(_free_port()),
        "--backend-only",
        "--max-http-attempts",
        "16",
        "--max-user-turns",
        "3",
        timeout=60,
        check=False,
    )
    assert mismatch.returncode != 0
    log2 = (run_root / "logs" / "backend.log").read_text(encoding="utf-8", errors="replace")
    assert "budget_state" in mismatch.stderr + mismatch.stdout + log2
