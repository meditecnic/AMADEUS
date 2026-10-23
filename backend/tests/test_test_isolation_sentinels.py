"""Regression tests for the test-isolation sentinel scenarios (audit P0).

A small sample of the suite is run in a child pytest process against a
throwaway copy of the backend, with a real-looking configuration aimed at an
empty sentinel directory:

* inherited_env: the shell that starts pytest already exports
  AMADEUS_DB_PATH / AMADEUS_DATA_DIR pointing at the sentinel directory, a
  fake DEEPSEEK_API_KEY, a fake custom provider address and model, and the
  two local_transport security switches turned on;
* dotenv_file: backend/.env (the file app/config.py loads) contains the same
  values. PYTHON_DOTENV_DISABLED is removed from the child environment, so
  only tests/conftest.py itself can stop the file from being loaded.

The copied tests/conftest.py is the one under test. After the run the
sentinel directory must still be empty, the fake key must not be visible to
app code, the custom provider must keep its code defaults and both security
switches must read as off. Without the isolation each scenario wrote 7 files into the
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
# Inherited only in the inherited_env scenario. The address does not exist
# (.invalid is a reserved top-level domain) and is never contacted.
INHERITED_ONLY_VALUES = {
    "AMADEUS_CUSTOM_PROVIDER_BASE_URL": "http://sentinel-custom-provider.invalid:9/v1",
    "AMADEUS_CUSTOM_PROVIDER_MODEL": "sentinel-fake-model",
    "AMADEUS_ALLOW_REMOTE_SOVITS": "1",
    "AMADEUS_ALLOW_MISSING_WS_ORIGIN": "1",
}

PROBE_TEST = '''
import os

import pytest

INHERITED_ONLY_KEYS = {inherited_only_keys!r}


def test_sentinel_configuration_does_not_reach_app_code():
    from app import config
    from app.db import _resolve_path, get_data_root
    from app.security import local_transport
    from app.services.provider_runtime import custom_provider_settings

    sentinel = os.path.realpath(os.environ["AMADEUS_SENTINEL_DIR"])
    assert config.DEEPSEEK_API_KEY != "{fake_key}"
    assert os.environ.get("DEEPSEEK_API_KEY") is None
    assert os.environ.get("AMADEUS_DB_PATH") is None
    for path in (str(get_data_root()), _resolve_path(), _resolve_path(kind="memory")):
        assert not os.path.realpath(path).startswith(sentinel), path

    for key in INHERITED_ONLY_KEYS:
        assert os.environ.get(key) is None, key
    # The shared "custom" provider was registered with the code defaults.
    custom = custom_provider_settings()
    assert custom["base_url"] == "http://127.0.0.1:1234/v1"
    assert custom["default_model"] == "local-model"
    # Both local_transport security switches read as off.
    assert not local_transport._enabled("AMADEUS_ALLOW_REMOTE_SOVITS")
    assert not local_transport._enabled("AMADEUS_ALLOW_MISSING_WS_ORIGIN")
    with pytest.raises(ValueError, match="loopback"):
        local_transport.sovits_tts_url("http://sentinel-tts.invalid:9880")
'''.format(fake_key=FAKE_KEY, inherited_only_keys=sorted(INHERITED_ONLY_VALUES))


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
        env.update(INHERITED_ONLY_VALUES)
    else:
        for key in (*sentinel_values, *INHERITED_ONLY_VALUES):
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
        encoding="utf-8",
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


def test_shared_tts_sidecar_stand_in_matches_real_offline_result():
    """conftest's offline_tts_sidecar returns what the real code returns when
    nothing listens on 9880 and SOVITS_SIDECAR_COMMAND is unset; supervisors
    built by tests keep the real methods."""
    import asyncio
    from dataclasses import asdict

    from app.services.tts_sidecar import SidecarSupervisor, sidecar_supervisor

    async def unreachable(self, url):
        return False

    real = SidecarSupervisor()
    assert "ensure_available" not in vars(real)
    assert os.environ.get("SOVITS_SIDECAR_COMMAND") is None
    real._compatible = unreachable.__get__(real)
    real._occupied = unreachable.__get__(real)

    async def run():
        real_state = asdict(await real.ensure_available())
        real.state = SidecarSupervisor().state
        real_health = await real.health()
        stand_in_state = asdict(await sidecar_supervisor.ensure_available())
        sidecar_supervisor.state = SidecarSupervisor().state
        stand_in_health = await sidecar_supervisor.health()
        return real_state, real_health, stand_in_state, stand_in_health

    real_state, real_health, stand_in_state, stand_in_health = asyncio.run(run())
    assert stand_in_state == real_state
    assert stand_in_health == real_health
    assert real_health == {
        "ok": False,
        "status": "degraded",
        "url": None,
        "managed": False,
        "error": "no compatible GPT-SoVITS endpoint on ports 9880-9890",
    }
