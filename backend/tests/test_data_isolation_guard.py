"""Exercise source/frozen entry points in fresh processes with synthetic user dirs."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


BACKEND = Path(__file__).resolve().parents[1]
REFUSAL = "AMADEUS_DATA_DIR required (non-frozen)"


@pytest.fixture
def child_environment(tmp_path):
    # Keep conftest's credential/network/cache isolation; replace only user paths.
    env = os.environ.copy()
    for key in ("AMADEUS_DATA_DIR", "AMADEUS_DB_PATH"):
        env.pop(key, None)
    home = tmp_path / "home"
    appdata = home / "AppData" / "Roaming"
    appdata.mkdir(parents=True)
    env.update({
        "HOME": str(home),
        "USERPROFILE": str(home),
        "APPDATA": str(appdata),
        "LOCALAPPDATA": str(home / "AppData" / "Local"),
        "HF_HOME": str(home / ".cache" / "huggingface"),
        "PYTHONPATH": str(BACKEND),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHON_DOTENV_DISABLED": "1",
        "AMADEUS_CREDENTIAL_BACKEND": "memory",
    })
    return env, appdata


def _run_python(args, env):
    return subprocess.run(
        [sys.executable, *args], cwd=BACKEND, env=env,
        capture_output=True, text=True, encoding="utf-8", timeout=30,
    )


def _assert_refused(result, appdata):
    # Check side effects even when a different exception also exits with code 1.
    assert list(appdata.rglob("*")) == []
    assert result.returncode == 1, result.stdout + result.stderr
    assert REFUSAL in result.stderr


@pytest.mark.parametrize("configured", [None, "", " \t"])
@pytest.mark.parametrize("has_appdata", [True, False])
def test_source_data_root_requires_explicit_directory(child_environment, configured, has_appdata):
    env, appdata = child_environment
    if configured is not None:
        env["AMADEUS_DATA_DIR"] = configured
    if not has_appdata:
        env.pop("APPDATA")
    result = _run_python(["-c", "from app.db import get_data_root; print(get_data_root())"], env)
    _assert_refused(result, appdata)
    assert not (Path(env["HOME"]) / ".amadeus").exists()


def test_backend_startup_requires_data_directory(child_environment):
    env, appdata = child_environment
    # No fixed port or lifespan side effects, even if the rejection is broken.
    result = _run_python([
        "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1",
        "--port", "0", "--lifespan", "off",
    ], env)
    _assert_refused(result, appdata)


@pytest.mark.parametrize("args", [
    ["scripts/seed_memory_demo.py", "--session-id", "guard-synthetic-owner"],
    ["scripts/purge_mistaken_demo_seed.py"],
    ["-m", "scripts._smoke_tts_preflight"],
    ["-m", "tests.run_adversarial_live"],
], ids=["seed", "purge", "tts-preflight", "live-runner"])
def test_source_script_requires_data_directory(child_environment, args):
    env, appdata = child_environment
    _assert_refused(_run_python(args, env), appdata)


@pytest.mark.parametrize("operation", [
    "async def probe():\n    connection = await db.get_db()\n    await connection.close()\n"
    "asyncio.run(probe())",
    "asyncio.run(db.init_db())",
], ids=["connection", "migration"])
def test_database_override_requires_data_directory(child_environment, operation):
    env, appdata = child_environment
    env["AMADEUS_DB_PATH"] = str(appdata / "Amadeus" / "legacy.sqlite3")
    result = _run_python(["-c", "import asyncio\nfrom app import db\n" + operation], env)
    _assert_refused(result, appdata)


@pytest.mark.parametrize("location", ["appdata", "home", "override"])
def test_frozen_data_root_remains_compatible(child_environment, tmp_path, location):
    env, appdata = child_environment
    expected = appdata / "Amadeus"
    if location == "home":
        env.pop("APPDATA")
        expected = Path(env["HOME"]) / ".amadeus"
    elif location == "override":
        expected = tmp_path / "explicit-data"
        env["AMADEUS_DATA_DIR"] = str(expected)
    code = """
import asyncio
import sys
sys.frozen = True
from app import db
async def probe():
    connection = await db.get_db(kind="control")
    await connection.execute("CREATE TABLE guard_probe (id INTEGER)")
    await connection.commit()
    await connection.close()
asyncio.run(probe())
print(db.get_data_root())
"""
    result = _run_python(["-c", code], env)
    assert result.returncode == 0, result.stderr
    assert Path(result.stdout.strip()) == expected.resolve()
    assert (expected / "control.sqlite3").is_file()
    if location != "appdata":
        assert list(appdata.rglob("*")) == []


def test_explicit_directory_keeps_legacy_database_precedence(child_environment, tmp_path):
    env, appdata = child_environment
    data = tmp_path / "explicit-data"
    legacy = tmp_path / "legacy" / "test.sqlite3"
    env.update(AMADEUS_DATA_DIR=str(data), AMADEUS_DB_PATH=str(legacy))
    result = _run_python(["-c", """
import asyncio
import json
from app import db
asyncio.run(db.init_db())
print(json.dumps([str(db.get_data_root()), db._resolve_path()]))
"""], env)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == [str(data.resolve()), str(legacy.resolve())]
    assert legacy.is_file()
    assert (legacy.parent / ".migration-v11.complete").is_file()
    assert not (data / "control.sqlite3").exists()
    assert list(appdata.rglob("*")) == []


def test_offline_builder_without_data_directory(child_environment, tmp_path):
    from tests.test_memory_candidate_builder import _make_preview, _make_snapshot

    env, appdata = child_environment
    snapshot = _make_snapshot(tmp_path)
    preview = _make_preview(snapshot, tmp_path / "inputs")
    output = tmp_path / "candidate"
    result = _run_python([
        "scripts/build_memory_candidate.py", "--snapshot", str(snapshot),
        "--preview", str(preview), "--out", str(output),
    ], env)
    assert list(appdata.rglob("*")) == []
    assert result.returncode == 0, result.stdout + result.stderr
    assert (output / "manifest.json").is_file()
    assert (output / "worldlines" / "sg" / "memory.sqlite3").is_file()


def test_offline_rehearsal_without_data_directory(child_environment, tmp_path):
    from tests.test_legacy_core_facts_rehearsal import _base_source

    env, appdata = child_environment
    source = _base_source(tmp_path)
    preview = tmp_path / "preview.json"
    result = _run_python([
        "scripts/legacy_core_facts_rehearsal.py", "preview", "--source", str(source),
        "--out", str(preview),
    ], env)
    assert list(appdata.rglob("*")) == []
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(preview.read_text(encoding="utf-8"))["summary"]["planned"] == 2
    result = _run_python([
        "scripts/legacy_core_facts_rehearsal.py", "apply", "--source", str(source),
        "--target", str(tmp_path / "rehearsal"), "--preview", str(preview),
    ], env)
    assert list(appdata.rglob("*")) == []
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)["ok"] is True


def test_offline_runner_isolates_before_importing_checks(child_environment, tmp_path):
    env, appdata = child_environment
    # Stop at the first application-importing callback: no nested pytest or E5.
    code = """
import runpy
import sys
from pathlib import Path
from types import SimpleNamespace
namespace = runpy.run_path("scripts/memory_recall_offline_run.py")
scope = namespace["main"].__globals__
scope["SCRATCH"] = Path(sys.argv[1])
scope["subprocess"].run = lambda *a, **kw: SimpleNamespace(returncode=0, stdout="", stderr="")
def checks():
    from app.db import get_data_root
    root = get_data_root()
    assert root.is_relative_to(Path(sys.argv[1]))
    assert root.name == "data"
    raise SystemExit(0)
scope["_load_required_checks"] = checks
namespace["main"]()
"""
    result = _run_python(["-c", code, str(tmp_path / "runner")], env)
    assert result.returncode == 0, result.stderr
    assert list(appdata.rglob("*")) == []
