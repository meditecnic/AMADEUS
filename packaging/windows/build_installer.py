"""Build the frozen backend, notices and current-user Windows NSIS installer."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time

from bundle_support import digest, sanitized_build_environment, write_json
from vc_runtime import redist_inventory, VC_NAME

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
TARGET = "x86_64-pc-windows-msvc"


def visual_studio(env: dict[str, str]) -> dict:
    vswhere = Path(os.environ["ProgramFiles(x86)"]) / "Microsoft Visual Studio/Installer/vswhere.exe"
    roots = subprocess.check_output([str(vswhere), "-products", "*", "-version", "[18.0,19.0)",
                                    "-requires", "Microsoft.VisualStudio.Component.VC.Tools.x86.x64",
                                    "-property", "installationPath"], env=env, text=True).splitlines()
    if len(roots) != 1:
        raise RuntimeError(f"Expected one completed VS 2026 installation, got {roots}")
    root = Path(roots[0]).resolve(strict=True)
    def latest(parent: Path) -> Path:
        paths = [path for path in parent.iterdir() if path.is_dir() and all(part.isdigit() for part in path.name.split("."))]
        if not paths:
            raise RuntimeError(f"No versioned directories: {parent}")
        return max(paths, key=lambda path: tuple(map(int, path.name.split("."))))
    redist = latest(root / "VC/Redist/MSVC") / "x64"
    inventory = redist_inventory(redist)
    if tuple(map(int, inventory["file_version"].split(".")[:2])) < (14, 51):
        raise RuntimeError(f"VS 2026 Redist is older than 14.51: {inventory}")
    linker = latest(root / "VC/Tools/MSVC") / "bin/Hostx64/x64/link.exe"
    if not linker.is_file():
        raise RuntimeError(f"Missing linker: {linker}")
    return {"installation": str(root), "redist": str(redist), "linker": str(linker), "runtime": inventory}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--e5-source", type=Path, required=True)
    parser.add_argument("--vc-redist", type=Path, required=True)
    parser.add_argument("--python", type=Path, default=Path(sys.executable))
    parser.add_argument("--config", type=Path, help="JSON overrides for test identifier, productName and version")
    args = parser.parse_args()
    if os.name != "nt":
        parser.error("build on Windows")
    started = time.monotonic()
    work = args.work_dir.resolve()
    work.mkdir(parents=True, exist_ok=True)
    run = work / "runs" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    logs = run / "logs"
    logs.mkdir(parents=True)
    for directory in ("tmp", "npm-cache", "cargo-target", "license-npm"):
        (work / directory).mkdir(exist_ok=True)
    env = sanitized_build_environment()
    toolchain = visual_studio(env)
    if args.vc_redist.resolve(strict=True) != Path(toolchain["redist"]).resolve():
        raise RuntimeError(f"--vc-redist must select the latest VS 2026 Redist: {toolchain['redist']}")
    env.update({"TEMP": str(work / "tmp"), "TMP": str(work / "tmp"),
                "npm_config_cache": str(work / "npm-cache"), "CARGO_TARGET_DIR": str(work / "cargo-target"),
                "PYTHONDONTWRITEBYTECODE": "1", "PYTHONIOENCODING": "utf-8", "STATIC_VCRUNTIME": "true"})
    devcmd = Path(toolchain["installation"]) / "Common7/Tools/VsDevCmd.bat"
    command = f'"{env["COMSPEC"]}" /d /s /c ""{devcmd}" -no_logo -arch=x64 -host_arch=x64 >nul && set"'
    developer_env = subprocess.check_output(command, env=env, encoding="mbcs")
    for line in developer_env.splitlines():
        key, separator, value = line.partition("=")
        if separator and key.upper() in {"PATH", "LIB", "INCLUDE", "LIBPATH", "VSINSTALLDIR", "VCTOOLSINSTALLDIR", "WINDOWSSDKDIR", "WINDOWSSDKVERSION"}:
            env[key.upper()] = value
    env["CARGO_TARGET_X86_64_PC_WINDOWS_MSVC_LINKER"] = toolchain["linker"]
    stages = []
    def execute(name: str, command: list[str], cwd: Path = REPO, output: Path | None = None) -> None:
        print("STAGE " + name + ": " + subprocess.list2cmdline(command), flush=True)
        tick = time.monotonic()
        log = logs / (name + ".log")
        with (output or log).open("w", encoding="utf-8") as stream:
            result = subprocess.run(command, cwd=cwd, env=env, stdout=stream,
                                    stderr=subprocess.PIPE if output else subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
        if output:
            log.write_text(result.stderr or "", encoding="utf-8")
        stages.append({"name": name, "command": command, "elapsed_seconds": round(time.monotonic() - tick, 3),
                       "exit_code": result.returncode, "log": str(log)})
        write_json(run / "stages.json", stages)
        if result.returncode:
            print(log.read_text(encoding="utf-8", errors="replace")[-12000:], flush=True)
            raise RuntimeError(f"Stage {name} failed ({result.returncode}); see {log}")
    config = json.loads((REPO / "desktop/src-tauri/tauri.conf.json").read_text(encoding="utf-8"))
    overrides = json.loads(args.config.read_text(encoding="utf-8")) if args.config else {}
    if set(overrides) - {"identifier", "productName", "version"}:
        parser.error("--config may only override identifier, productName and version")
    selected = {key: overrides.get(key, config[key]) for key in ("identifier", "productName", "version")}
    backend = run / "backend"
    build_work = work / "backend-build"
    execute("freeze", [str(args.python), str(HERE / "build_backend.py"), "--python", str(args.python),
                       "--work-dir", str(build_work), "--e5-source", str(args.e5_source.resolve()),
                       "--vc-redist", toolchain["redist"], "--output", str(backend)])
    evidence = run / "backend-evidence"
    evidence.mkdir()
    for path in build_work.glob("*.json"):
        shutil.copyfile(path, evidence / path.name)
    shutil.copytree(build_work / "logs", evidence / "logs")
    python = build_work / "venv/Scripts/python.exe"
    npm = shutil.which("npm.cmd")
    if not npm:
        raise RuntimeError("npm.cmd is not on PATH")
    # Runtime-only npm files provide notices before the full frontend build install.
    for name in ("package.json", "package-lock.json"):
        shutil.copyfile(REPO / "desktop" / name, work / "license-npm" / name)
    execute("license-npm", [npm, "ci", "--omit=dev", "--ignore-scripts", "--no-audit", "--no-fund"], work / "license-npm")
    cargo_metadata = run / "cargo-metadata.json"
    execute("cargo-metadata", ["cargo", "metadata", "--locked", "--format-version", "1", "--filter-platform", TARGET,
                               "--manifest-path", str(REPO / "desktop/src-tauri/Cargo.toml")], output=cargo_metadata)
    cargo = json.loads(cargo_metadata.read_text(encoding="utf-8"))
    package = next(row for row in cargo["packages"] if row["id"] == cargo["resolve"]["root"])
    binaries = [target["name"] for target in package["targets"] if "bin" in target["kind"]]
    if len(binaries) != 1:
        raise RuntimeError(f"Expected one desktop binary, got {binaries}")
    main_exe = binaries[0] + ".exe"
    licenses = run / "licenses"
    execute("licenses", [str(python), str(HERE / "license_bundle.py"), "--build-config", str(build_work / "build-config.json"),
                         "--backend", str(backend), "--work-dir", str(work / "materials"), "--output", str(licenses),
                         "--cargo-metadata", str(cargo_metadata), "--node-modules", str(work / "license-npm/node_modules"),
                         "--main-exe", main_exe])
    execute("source-materials", [str(python), str(HERE / "source_materials.py"), "--work-dir", str(work / "materials"),
                                 "--version", selected["version"]])
    execute("npm-ci", [npm, "ci", "--no-audit", "--no-fund"], REPO / "desktop")
    frontend = run / "frontend-dist"
    resources = {key: None for key in config["bundle"]["resources"]}
    resources.update({str(backend): "backend/", str(licenses): "licenses/"})
    merge = {**selected, "bundle": {"resources": resources},
             "build": {"frontendDist": str(frontend),
                       "beforeBuildCommand": subprocess.list2cmdline(["npm", "run", "build", "--", "--outDir", str(frontend)])}}
    merge_path = run / "tauri-build.conf.json"
    write_json(merge_path, merge)
    manifest_path = REPO / "desktop/src-tauri/Cargo.toml"
    manifest_before = manifest_path.read_bytes()
    execute("tauri-build", [npm, "run", "tauri", "--", "build", "--target", TARGET, "--config", str(merge_path), "--verbose", "--", "--locked"], REPO / "desktop")
    manifest_after = manifest_path.read_bytes()
    manifest_observation = {"bytes_changed": manifest_before != manifest_after,
                            "normalized_content_equal": manifest_before.replace(b"\r\n", b"\n") == manifest_after.replace(b"\r\n", b"\n"),
                            "before_crlf": manifest_before.count(b"\r\n"), "after_crlf": manifest_after.count(b"\r\n")}
    write_json(run / "cargo-manifest-observation.json", manifest_observation)
    print("CARGO_MANIFEST_NOTE Tauri CLI 2.11.3 feature synchronization can rewrite CRLF as LF; no manifest restoration or Git commands are run.", flush=True)
    if not manifest_observation["normalized_content_equal"]:
        raise RuntimeError(f"Tauri changed Cargo.toml content: {manifest_path}")
    release = work / "cargo-target" / TARGET / "release"
    exe = release / main_exe
    inspect_code = ("import json, sys\nfrom pathlib import Path\n"
                    "sys.path.insert(0, sys.argv[1])\nfrom vc_runtime import pe_details\n"
                    "print(json.dumps(pe_details(Path(sys.argv[2]))))\n")
    details = json.loads(subprocess.check_output([str(python), "-c", inspect_code, str(HERE), str(exe)], env=env, text=True))
    write_json(run / "desktop-pe.json", details)
    dynamic_vc = [name for name in details["imports"] if VC_NAME.fullmatch(name)]
    if dynamic_vc:
        raise RuntimeError(f"Desktop executable imports dynamic VC runtime: {exe}: {dynamic_vc}")
    installers = list((release / "bundle/nsis").glob("*.exe"))
    if len(installers) != 1:
        raise RuntimeError(f"Expected one NSIS installer, got {installers}")
    output = run / installers[0].name
    shutil.copyfile(installers[0], output)
    shutil.copyfile(exe, run / main_exe)
    pdb = exe.with_suffix(".pdb")
    linker_records = sorted({value.decode("utf-8") for value in re.findall(
        rb"exe\x00([^\x00]{1,400}link\.exe)\x00", pdb.read_bytes(), re.I)})
    if not any(Path(path).resolve() == Path(toolchain["linker"]).resolve() for path in linker_records):
        raise RuntimeError(f"PDB linker record does not confirm selected link.exe: {linker_records}")
    shutil.copyfile(pdb, run / pdb.name)
    linker_evidence = {"pdb": str(run / pdb.name), "sha256": digest(pdb), "linker_exe_records": linker_records}
    tools = {}
    for name, command in (("python", [str(args.python), "--version"]), ("node", ["node", "--version"]),
                          ("npm", [npm, "--version"]), ("cargo", ["cargo", "--version"]), ("rustc", ["rustc", "--version"])):
        tools[name] = subprocess.check_output(command, env=env, text=True).strip()
    result = {"run": str(run), "installer": str(output), "bytes": output.stat().st_size, "sha256": digest(output),
              "backend": str(backend), "licenses": str(licenses), "main_exe": main_exe, "config": selected,
              "toolchain": toolchain, "desktop_pe": details, "linker_evidence": linker_evidence, "stages": stages, "tools": tools,
              "elapsed_seconds": round(time.monotonic() - started, 3)}
    write_json(run / "installer-result.json", result)
    write_json(work / "installer-result.json", result)
    print("INSTALLER_OK " + json.dumps({key: result[key] for key in ("installer", "bytes", "sha256", "elapsed_seconds")}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
