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

from bundle_support import sanitized_build_environment

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
TEST_ONLY = {"pytest", "pytest-asyncio", "pytest-timeout", "ruff", "iniconfig", "pluggy"}
EMBEDDING_REVISION = "ac5d898c8d382b17167c33e5c8af644a3519b47d"
EMBEDDING_FILES = {
    "onnx/model.onnx": "ad8244eea593ae8488cfd821a7d573aa51a350bc817702d6da99f3bccab24f49",
    "onnx/model.onnx_data": "7eeca7c5e63a9047991df400cdd57c3cf05fabc0168700a4ecee42d1eba8093d",
    "tokenizer.json": "98d4a1d32152d6cedf85b5e88f3b205106dca1fe72aaab34e0ac13c238421069",
    "README.md": "9f2433969e7962fffd5cd2bfef18bb319032dc973090d5449628d9bb4d219381",
}
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


def build_environment(work: Path, venv: Path, base_python: Path) -> dict[str, str]:
    env = sanitized_build_environment()
    system = Path(os.environ["SystemRoot"]).resolve()
    env.update({
        "PATH": os.pathsep.join(str(path) for path in (venv / "Scripts", base_python, system / "System32", system)),
        "TEMP": str(work / "tmp"), "TMP": str(work / "tmp"),
        "PIP_CACHE_DIR": str(work / "pip-cache"), "PIP_DISABLE_PIP_VERSION_CHECK": "1",
        "PYINSTALLER_CONFIG_DIR": str(work / "pyinstaller-cache"),
        "PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONNOUSERSITE": "1", "PYTHON_DOTENV_DISABLED": "1",
        "HF_HOME": str(work / "unused-hf-home"), "HF_HUB_OFFLINE": "1",
        "AMADEUS_CREDENTIAL_BACKEND": "memory",
        "AMADEUS_DATA_DIR": str(work / "analysis-data"),
    })
    return env


def stage_embedding(source: Path, cache: Path) -> list[dict]:
    """Copy only pinned model files; verify the source and each staged copy."""
    model = cache / "models--jinaai--jina-embeddings-v5-text-nano-retrieval"
    snapshot = model / "snapshots" / EMBEDDING_REVISION
    snapshot.mkdir(parents=True, exist_ok=True)
    model_files = []
    for relative, expected in EMBEDDING_FILES.items():
        if expected is None:
            raise RuntimeError(f"embedding SHA-256 not yet verified: {relative}; complete EMBEDDING_FILES before packaging")
        origin = source / relative
        target = snapshot / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if not origin.is_file():
            raise FileNotFoundError(f"missing embedding file: {relative}")
        if sha256(origin) != expected:
            raise RuntimeError(f"embedding source hash mismatch: {relative}")
        if not target.is_file() or sha256(target) != expected:
            shutil.copyfile(origin, target)
        if sha256(target) != expected:
            raise RuntimeError(f"embedding copy mismatch: {relative}")
        model_files.append({"path": relative, "sha256": expected, "bytes": target.stat().st_size})
    (model / "refs").mkdir(exist_ok=True)
    reference = model / "refs/main"
    if not reference.is_file() or reference.read_text(encoding="utf-8") != EMBEDDING_REVISION:
        reference.write_text(EMBEDDING_REVISION, encoding="utf-8")
    return model_files


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", type=Path, default=Path(sys.executable), help="CPython 3.11 x64 used to create the venv")
    parser.add_argument("--work-dir", type=Path, required=True, help="venv, cache, temp, logs and build output")
    parser.add_argument("--venv", type=Path, help="defaults to WORK_DIR/venv")
    parser.add_argument("--embedding-source", type=Path, required=True, help=f"read-only embedding snapshot {EMBEDDING_REVISION}")
    parser.add_argument("--vc-redist", type=Path, required=True, help="Build Tools Redist/MSVC/<version>/x64 directory")
    parser.add_argument("--output", type=Path, default=REPO / "desktop/src-tauri/resources/backend")
    parser.add_argument("--prepare-only", action="store_true", help="install dependencies without freezing")
    parser.add_argument("--clean", action="store_true", help="discard PyInstaller analysis caches")
    args = parser.parse_args()
    if os.name != "nt":
        parser.error("build on Windows x64")
    from vc_runtime import redist_inventory
    redist = redist_inventory(args.vc_redist)
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
    source = args.embedding_source.resolve(strict=True)
    if source.name != EMBEDDING_REVISION:
        parser.error(f"--embedding-source must be the pinned snapshot directory {EMBEDDING_REVISION}")
    for relative, expected in EMBEDDING_FILES.items():
        if expected is None:
            raise RuntimeError(f"embedding SHA-256 not yet verified: {relative}; complete EMBEDDING_FILES before packaging")
        if not (source / relative).is_file():
            parser.error(f"missing embedding file: {relative}")
        if sha256(source / relative) != expected:
            raise RuntimeError(f"embedding source hash mismatch: {relative}")
    work.mkdir(parents=True, exist_ok=True)
    for name in ("tmp", "pip-cache", "pyinstaller-cache", "logs"):
        (work / name).mkdir(exist_ok=True)
    base_python = Path(subprocess.check_output(
        [str(args.python), "-I", "-c", "import sys; print(sys.base_prefix)"], text=True, encoding="utf-8",
        env=sanitized_build_environment(),
    ).strip()).resolve(strict=True)
    env = build_environment(work, venv, base_python)
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
    model_files = stage_embedding(source, cache)
    config = {
        "repo": str(REPO), "work": str(work), "packaging": str(HERE),
        "hf_cache": str(cache), "app_files": APP_FILES,
        "build_path": env["PATH"], "venv": str(venv), "base_python": str(base_python),
        "vc_redist": redist["path"],
        "model": {"id": "jinaai/jina-embeddings-v5-text-nano-retrieval", "revision": EMBEDDING_REVISION, "files": model_files},
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
    run([str(python), str(HERE / "vc_runtime.py"), str(config_path)],
        env, work, work / "logs/vc-runtime.log")
    run([str(python), str(HERE / "bundle_support.py"), str(config_path)],
        env, work, work / "logs/inventory.log")
    dist = work / "dist/amadeus-backend"
    shutil.copytree(dist, output)
    write_json(work / "build-result.json", {
        "elapsed_seconds": round(time.monotonic() - started, 3), "output": str(output),
        "python": subprocess.check_output([str(python), "--version"], env=env, text=True, encoding="utf-8").strip(),
        "inputs": config["inputs"],
    })
    print(f"Backend staged at {output}; elapsed={time.monotonic() - started:.3f}s", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
