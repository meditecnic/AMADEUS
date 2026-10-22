"""Regression tests for the test-isolation sentinel scenarios (audit P0).

A small sample of the suite is run in a child pytest process against a
throwaway copy of the backend, with a real-looking configuration aimed at an
empty sentinel directory:

* inherited_env: the shell that starts pytest already exports
  AMADEUS_DB_PATH / AMADEUS_DATA_DIR pointing at the sentinel directory and a
  fake DEEPSEEK_API_KEY;
* dotenv_file: backend/.env (the file app/config.py loads) contains the same
  values. PYTHON_DOTENV_DISABLED is removed from the child environment, so
  only tests/conftest.py itself can stop the file from being loaded.

The copied tests/conftest.py is the one under test. After the run the
sentinel directory must still be empty and the fake key must not be visible
to app code. Without the isolation each scenario wrote 7 files into the
sentinel directory (5 SQLite databases, .migration.lock and
.migration-v11.complete).

The copy lives under tmp_path, so the repository never gets a backend/.env.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
SAMPLE_TESTS = ("test_provider_registry.py",)
FAKE_KEY = "sk-sentinel-fake-value-not-a-key"

PROBE_TEST = '''
import os


def test_sentinel_configuration_does_not_reach_app_code():
    from app import config
    from app.db import _resolve_path, get_data_root

    sentinel = os.path.realpath(os.environ["AMADEUS_SENTINEL_DIR"])
    assert config.DEEPSEEK_API_KEY != "{fake_key}"
    assert os.environ.get("DEEPSEEK_API_KEY") is None
    assert os.environ.get("AMADEUS_DB_PATH") is None
    for path in (str(get_data_root()), _resolve_path(), _resolve_path(kind="memory")):
        assert not os.path.realpath(path).startswith(sentinel), path
'''.format(fake_key=FAKE_KEY)


def _copy_backend(dst: Path) -> None:
    ignore = shutil.ignore_patterns("__pycache__", ".env")
    for name in ("app", "characters", "prompts"):
        shutil.copytree(BACKEND_ROOT / name, dst / name, ignore=ignore)
    shutil.copy2(BACKEND_ROOT / "pytest.ini", dst / "pytest.ini")
    tests = dst / "tests"
    tests.mkdir()
    shutil.copy2(BACKEND_ROOT / "tests" / "conftest.py", tests / "conftest.py")
    for name in SAMPLE_TESTS:
        shutil.copy2(BACKEND_ROOT / "tests" / name, tests / name)
    (tests / "test_zz_sentinel_probe.py").write_text(PROBE_TEST, encoding="utf-8")


def _files_under(root: Path) -> list[str]:
    return sorted(str(p.relative_to(root)) for p in root.rglob("*"))


@pytest.mark.parametrize("scenario", ["inherited_env", "dotenv_file"])
def test_sentinel_configuration_is_never_used(tmp_path, scenario):
    sentinel = tmp_path / "sentinel"
    sentinel.mkdir()
    backend = tmp_path / "backend"
    _copy_backend(backend)

    sentinel_values = {
        "AMADEUS_DB_PATH": str(sentinel / "amadeus.db"),
        "AMADEUS_DATA_DIR": str(sentinel),
        "DEEPSEEK_API_KEY": FAKE_KEY,
    }
    env = dict(os.environ)
    for key in ("PYTHON_DOTENV_DISABLED", "PYTEST_ADDOPTS", "PYTEST_CURRENT_TEST"):
        env.pop(key, None)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["AMADEUS_SENTINEL_DIR"] = str(sentinel)
    if scenario == "inherited_env":
        env.update(sentinel_values)
    else:
        for key in sentinel_values:
            env.pop(key, None)
        (backend / ".env").write_text(
            "".join(f"{key}={value}\n" for key, value in sentinel_values.items()),
            encoding="utf-8",
        )

    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests"],
        cwd=str(backend),
        env=env,
        capture_output=True,
        text=True,
        timeout=600,
    )

    assert _files_under(sentinel) == []
    assert result.returncode == 0, result.stdout[-4000:] + result.stderr[-4000:]
    assert FAKE_KEY not in result.stdout + result.stderr


def test_credential_store_is_in_memory_and_windows_store_is_refused():
    from app.services import credentials

    assert os.environ.get("AMADEUS_CREDENTIAL_BACKEND") == "memory"
    assert credentials.WINDOWS_CREDENTIAL_STORE_CONSTRUCTED is False
    assert isinstance(credentials._select_credential_store(), credentials.InMemoryCredentialStore)
    with pytest.raises(RuntimeError, match="must not construct WindowsCredentialStore"):
        credentials.WindowsCredentialStore()
