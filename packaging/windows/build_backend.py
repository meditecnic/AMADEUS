"""Build a Windows x64 onedir backend in an isolated CPython 3.11 venv."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
TEST_ONLY = {"pytest", "pytest-asyncio", "pytest-timeout", "ruff", "iniconfig", "pluggy"}
E5_REVISION = "614241f622f53c4eeff9890bdc4f31cfecc418b3"
E5_FILES = (
    "config.json", "model.safetensors", "modules.json", "sentence_bert_config.json",
    "special_tokens_map.json", "tokenizer_config.json", "tokenizer.json",
    "sentencepiece.bpe.model", "1_Pooling/config.json", "README.md",
)
APP_FILES = (
    "characters/amadeus_sg.json", "characters/amadeus_beta.json",
    "characters/persona_bible_v1.json", "characters/model_capability_manifest_v1.json",
    "characters/tts_pronunciation_lexicon_v1.json", "prompts/kurisu_fewshot.json",
)


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def runtime_lock(source: Path) -> str:
    """Retain the repository's pins, markers and hashes, excluding test-only blocks."""
    blocks = re.split(r"(?=^[a-zA-Z0-9_.-]+==)", source.read_text(encoding="utf-8"), flags=re.M)
    selected = ["# Derived from backend/requirements-dev.lock; test-only distributions excluded.\n"]
    for block in blocks:
        match = re.match(r"([a-zA-Z0-9_.-]+)==", block)
        if match and match[1].lower().replace("_", "-") not in TEST_ONLY:
            selected.append(block)
    return "".join(selected)


def run(command: list[str], env: dict[str, str], cwd: Path, log: Path) -> None:
    print("RUN " + subprocess.list2cmdline(command), flush=True)
    with log.open("w", encoding="utf-8") as stream:
        result = subprocess.run(command, cwd=cwd, env=env, stdout=stream, stderr=subprocess.STDOUT)
    if result.returncode:
        print(log.read_text(encoding="utf-8", errors="replace")[-10000:], flush=True)
        raise RuntimeError(f"command failed ({result.returncode}); see {log}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", type=Path, default=Path(sys.executable), help="CPython 3.11 x64 used to create the venv")
    parser.add_argument("--work-dir", type=Path, required=True, help="venv, cache, temp, logs and build output")
    parser.add_argument("--venv", type=Path, help="defaults to WORK_DIR/venv")
    parser.add_argument("--e5-source", type=Path, required=True, help=f"read-only E5 snapshot {E5_REVISION}")
    parser.add_argument("--output", type=Path, default=REPO / "desktop/src-tauri/resources/backend")
    parser.add_argument("--prepare-only", action="store_true", help="install dependencies without freezing")
    parser.add_argument("--clean", action="store_true", help="discard PyInstaller analysis caches")
    args = parser.parse_args()
    if os.name != "nt":
        parser.error("build on Windows x64")
    started = time.monotonic()
    work = args.work_dir.resolve()
    output = args.output.resolve()
    venv = (args.venv or work / "venv").resolve()
    if not venv.is_relative_to(work):
        parser.error("--venv must be inside --work-dir")
    if output.exists() and not args.prepare_only:
        parser.error("--output already exists; preserve it and choose a fresh directory")
    if output.is_relative_to(work) or work.is_relative_to(output):
        parser.error("--output and --work-dir must not contain each other")
    source = args.e5_source.resolve(strict=True)
    if source.name != E5_REVISION:
        parser.error(f"--e5-source must be the pinned snapshot directory {E5_REVISION}")
    for relative in E5_FILES:
        if not (source / relative).is_file():
            parser.error(f"missing E5 file: {relative}")
    work.mkdir(parents=True, exist_ok=True)
    for name in ("tmp", "pip-cache", "pyinstaller-cache", "logs"):
        (work / name).mkdir(exist_ok=True)
    env = dict(os.environ)
    for key in list(env):
        if (key.startswith(("AMADEUS_", "SOVITS_", "HF_", "HUGGINGFACE_", "TRANSFORMERS_", "SENTENCE_TRANSFORMERS_"))
                or key in {"PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "ASSETS_DIR", "SENSEVOICE_MODEL",
                           "SENSEVOICE_TOKENS", "DEEPSEEK_API_KEY", "TAVILY_API_KEY", "FIRECRAWL_API_KEY"}):
            env.pop(key)
    env.update({
        "TEMP": str(work / "tmp"), "TMP": str(work / "tmp"),
        "PIP_CACHE_DIR": str(work / "pip-cache"), "PIP_DISABLE_PIP_VERSION_CHECK": "1",
        "PYINSTALLER_CONFIG_DIR": str(work / "pyinstaller-cache"),
        "PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONNOUSERSITE": "1", "PYTHON_DOTENV_DISABLED": "1",
        "HF_HOME": str(work / "unused-hf-home"), "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1", "AMADEUS_CREDENTIAL_BACKEND": "memory",
        "AMADEUS_DATA_DIR": str(work / "analysis-data"),
    })
    python = venv / "Scripts/python.exe"
    if not python.exists():
        run([str(args.python), "-m", "venv", str(venv)], env, work, work / "logs/venv.log")
    run([str(python), "-c", "import struct,sys; assert sys.version_info[:2] == (3,11) and struct.calcsize('P') == 8"],
        env, work, work / "logs/python.log")
    lock = work / "requirements-runtime.lock"
    lock.write_text(runtime_lock(REPO / "backend/requirements-dev.lock"), encoding="utf-8")
    for name, requirement in (("build", HERE / "requirements-build.txt"), ("runtime", lock)):
        run([str(python), "-m", "pip", "install", "--only-binary=:all:", "--require-hashes",
             "--report", str(work / f"install-{name}.json"), "-r", str(requirement)],
            env, work, work / f"logs/install-{name}.log")
    run([str(python), "-m", "pip", "check"], env, work, work / "logs/pip-check.log")
    if args.prepare_only:
        print(f"Prepared build venv: {python}", flush=True)
        return 0
    cache = work / "model-stage/hf_cache"
    model = cache / "models--intfloat--multilingual-e5-small"
    snapshot = model / "snapshots" / E5_REVISION
    snapshot.mkdir(parents=True, exist_ok=True)
    model_files = []
    for relative in E5_FILES:
        origin = source / relative
        target = snapshot / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(origin, target)
        digest = sha256(origin)
        if sha256(target) != digest:
            raise RuntimeError(f"E5 copy mismatch: {relative}")
        model_files.append({"path": relative, "sha256": digest, "bytes": target.stat().st_size})
    (model / "refs").mkdir(exist_ok=True)
    (model / "refs/main").write_text(E5_REVISION, encoding="utf-8")
    config = {
        "repo": str(REPO), "work": str(work), "packaging": str(HERE),
        "hf_cache": str(cache), "app_files": APP_FILES,
        "model": {"id": "intfloat/multilingual-e5-small", "revision": E5_REVISION, "files": model_files},
        "inputs": {p.relative_to(REPO).as_posix(): sha256(p) for p in (
            REPO / "backend/requirements.txt", REPO / "backend/requirements-dev.lock",
            HERE / "requirements-build.txt", HERE / "backend.spec", *sorted(HERE.glob("*.py")),
        )},
    }
    config_path = work / "build-config.json"
    write_json(config_path, config)
    env["AMADEUS_BUILD_CONFIG"] = str(config_path)
    run([str(python), "-m", "PyInstaller", "--noconfirm", *(["--clean"] if args.clean else []),
         "--distpath", str(work / "dist"), "--workpath", str(work / "build"),
         str(HERE / "backend.spec")], env, work, work / "logs/pyinstaller.log")
    run([str(python), str(HERE / "bundle_support.py"), str(config_path)],
        env, work, work / "logs/inventory.log")
    dist = work / "dist/amadeus-backend"
    shutil.copytree(dist, output)
    write_json(work / "build-result.json", {
        "elapsed_seconds": round(time.monotonic() - started, 3), "output": str(output),
        "python": subprocess.check_output([str(python), "--version"], text=True).strip(),
        "inputs": config["inputs"],
    })
    print(f"Backend staged at {output}; elapsed={time.monotonic() - started:.3f}s", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
