"""Small, offline evaluation helpers extracted from the pre-v11-only archive."""
from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlparse

from _path_guards import containing_live_root, deep_resolve  # noqa: F401

BACKEND_ROOT = Path(__file__).resolve().parents[1]
OFFICIAL_DEEPSEEK_HOST = "api.deepseek.com"
BUILTIN_DEEPSEEK_BASE_URL = "https://api.deepseek.com"
CURRENCY_CAP_CNY = 10
PEAK_INPUT_CNY_PER_MILLION = 2
PEAK_OUTPUT_CNY_PER_MILLION = 8
PEAK_CACHE_HIT_CNY_PER_MILLION = 0.04

PROVIDER_ENV_KEYS = (
    "AMADEUS_LOCAL_API_TOKEN",
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
        "local_api_token": run_root / "local-api-token",
        "env_file": run_root / "fake-profile" / "AmadeusIsolated" / ".env",
    }


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
    env["AMADEUS_BACKEND_ORIGIN"] = f"http://127.0.0.1:{backend_port}"
    env["AMADEUS_FRONTEND_PORT"] = str(frontend_port)
    env["AMADEUS_B2_RUN_ROOT"] = str(paths["root"])
    env["AMADEUS_DIAGNOSTICS_OBSERVE"] = "0"
    env["PYTHONUNBUFFERED"] = "1"
    for key in PROVIDER_ENV_KEYS:
        env.pop(key, None)
        source.pop(key, None)
    return env


def official_deepseek_base_url(url: str) -> bool:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    port = parsed.port
    path = parsed.path or ""
    return (
        parsed.scheme == "https"
        and host == OFFICIAL_DEEPSEEK_HOST
        and port in {None, 443}
        and path.rstrip("/") == ""
        and parsed.username is None
        and parsed.query == ""
        and parsed.fragment == ""
    )
