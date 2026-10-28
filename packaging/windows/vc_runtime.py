"""Normalize app-local VC runtime files and audit PE imports/toolset versions."""
from __future__ import annotations

import ast
import json
from pathlib import Path
import re
import shutil

from bundle_support import digest, file_version, write_json


VC_NAME = re.compile(r"(?:vcruntime|msvcp|msvcr|vcomp|concrt|vccorlib)\d+.*\.dll", re.I)
PE_SUFFIXES = {".exe", ".dll", ".pyd"}


def redist_inventory(root: Path) -> dict:
    """Require the CRT and OpenMP redistributables from one versioned x64 tree."""
    root = root.resolve(strict=True)
    files, directories = {}, {}
    for kind, required in (
        ("CRT", ("vcruntime140.dll", "vcruntime140_1.dll", "msvcp140.dll")),
        ("OpenMP", ("vcomp140.dll",)),
    ):
        folders = sorted(path for path in root.glob(f"Microsoft.VC14*.{kind}") if path.is_dir())
        if not folders:
            raise RuntimeError(f"Missing VC Redist file: {root / ('Microsoft.VC14*.' + kind) / required[0]}")
        if len(folders) != 1:
            raise RuntimeError(f"Expected exactly one Microsoft.VC14*.{kind} directory in {root}; found {folders}")
        folder = folders[0]
        directories[kind] = folder.name
        available = {path.name.lower(): path for path in folder.glob("*.dll")}
        for name in required:
            if name not in available:
                raise RuntimeError(f"Missing VC Redist file: {folder / name}")
        for name, path in sorted(available.items()):
            files[name] = {"source_path": str(path), "file_version": file_version(path), "sha256": digest(path)}
    versions = {entry["file_version"] for entry in files.values()}
    if None in versions or len(versions) != 1:
        raise RuntimeError(f"VC Redist files must have one file version: {files}")
    version = versions.pop()
    return {"path": str(root), "directory_version": root.parent.name, "directories": directories,
            "file_version": version, "files": files}


def pe_details(path: Path) -> dict:
    import pefile

    with pefile.PE(str(path), fast_load=True) as pe:
        pe.parse_data_directories(directories=[
            pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_IMPORT"],
            pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_DELAY_IMPORT"],
        ])
        imports = sorted({entry.dll.decode("ascii").lower() for table in (
            "DIRECTORY_ENTRY_IMPORT", "DIRECTORY_ENTRY_DELAY_IMPORT",
        ) for entry in getattr(pe, table, [])})
        return {"linker_version": [pe.OPTIONAL_HEADER.MajorLinkerVersion, pe.OPTIONAL_HEADER.MinorLinkerVersion],
                "imports": imports}


def redist_name(name: str) -> str:
    """Remove delvewheel's content-hash suffix while preserving the installed name."""
    return re.sub(r"-[0-9a-f]{8,64}(?=\.dll$)", "", name.lower())


def audit_bundle(dist: Path, redist: dict, report: Path, *, replacements: list | None = None,
                 additions: list | None = None) -> dict:
    root = dist / "_internal"
    paths = sorted(path for path in dist.rglob("*") if path.is_file() and path.suffix.lower() in PE_SUFFIXES)
    details = {path: pe_details(path) for path in paths}
    runtimes, by_name, mismatches = [], {}, []
    originals = {row["bundle_path"]: row for row in replacements or []}
    for path in paths:
        if not VC_NAME.fullmatch(path.name):
            continue
        relative = path.relative_to(dist).as_posix()
        source = redist["files"].get(redist_name(path.name))
        row = {"bundle_path": relative, "file_version": file_version(path), "sha256": digest(path),
               "scope": "root" if path.parent == root else "package-local",
               "source_path": source["source_path"] if source else None,
               "original_file_version": originals.get(relative, {}).get("original_file_version"),
               "original_sha256": originals.get(relative, {}).get("original_sha256")}
        runtimes.append(row)
        by_name.setdefault(path.name.lower(), []).append(row)
        if not source or row["sha256"] != source["sha256"]:
            mismatches.append(row)
    components, violations, missing, runtime_violations = [], [], [], []
    ceiling = tuple(map(int, redist["file_version"].split(".")[:2]))
    for path, pe in details.items():
        vc_imports = [name for name in pe["imports"] if VC_NAME.fullmatch(name)]
        if not vc_imports:
            continue
        row = {"bundle_path": path.relative_to(dist).as_posix(), "linker_version": pe["linker_version"],
               "vc_imports": vc_imports, "runtime_candidates": []}
        components.append(row)
        if tuple(pe["linker_version"]) > ceiling:
            violations.append(row)
        for name in vc_imports:
            candidates = by_name.get(name, [])
            # Every import has a local/root fallback; package preloads must also be compatible.
            if not any((dist / item["bundle_path"]).parent in {path.parent, root} for item in candidates):
                missing.append({"importer": row["bundle_path"], "dll": name})
            for runtime in candidates:
                row["runtime_candidates"].append({"dll": name, "path": runtime["bundle_path"], "version": runtime["file_version"]})
                version = runtime["file_version"]
                if not version or tuple(pe["linker_version"]) > tuple(map(int, version.split(".")[:2])):
                    runtime_violations.append({"importer": row["bundle_path"], "linker_version": pe["linker_version"],
                                               "runtime": runtime["bundle_path"], "runtime_version": version})
    payload = {"redist": redist, "replacements": replacements or [], "additions": additions or [],
               "runtimes": runtimes, "pe_count": len(paths), "components": components, "violations": violations,
               "missing_imports": missing, "runtime_violations": runtime_violations, "runtime_mismatches": mismatches}
    write_json(report, payload)
    errors = [f"{row['bundle_path']}: linker {row['linker_version']} exceeds Redist {redist['file_version']}" for row in violations]
    errors += [f"{row['importer']}: missing VC import {row['dll']}" for row in missing]
    errors += [f"{row['importer']}: linker {row['linker_version']} exceeds {row['runtime']} version {row['runtime_version']}" for row in runtime_violations]
    errors += [f"VC runtime does not match selected Redist: {row['bundle_path']} ({row['file_version']})" for row in mismatches]
    if errors:
        raise RuntimeError("VC bundle audit failed:\n" + "\n".join(errors))
    return payload


def normalize_and_audit(dist: Path, redist: dict, report: Path) -> dict:
    root = dist / "_internal"
    replacements, additions = [], []
    for path in sorted(dist.rglob("*")):
        if not path.is_file() or not VC_NAME.fullmatch(path.name):
            continue
        source = redist["files"].get(redist_name(path.name))
        if not source:
            raise RuntimeError(f"No matching VC Redist file for bundle runtime: {path}")
        original = {"original_file_version": file_version(path), "original_sha256": digest(path)}
        shutil.copyfile(source["source_path"], path)
        if digest(path) != source["sha256"]:
            raise RuntimeError(f"VC copy hash mismatch: {path}")
        replacements.append({"bundle_path": path.relative_to(dist).as_posix(), **source, **original})
    while True:
        added = False
        for path in sorted(dist.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in PE_SUFFIXES:
                continue
            for name in pe_details(path)["imports"]:
                if not VC_NAME.fullmatch(name):
                    continue
                if any(file.name.lower() == name for directory in {root, path.parent} for file in directory.iterdir() if file.is_file()):
                    continue
                source = redist["files"].get(redist_name(name))
                if not source:
                    raise RuntimeError(f"Missing VC Redist file for {path}: {name}")
                target = root / name
                shutil.copyfile(source["source_path"], target)
                additions.append({"bundle_path": target.relative_to(dist).as_posix(), "required_by": path.relative_to(dist).as_posix(), **source})
                added = True
        if not added:
            break
    return audit_bundle(dist, redist, report, replacements=replacements, additions=additions)


def prune_sherpa_c_api(dist: Path, source: Path, report: Path) -> dict:
    candidates = {"sherpa-onnx-c-api.dll", "sherpa-onnx-cxx-api.dll"}
    files = sorted(path for path in dist.rglob("*") if path.is_file())
    before = {"files": len(files), "bytes": sum(path.stat().st_size for path in files)}
    imports, references = [], []
    for path in files:
        if path.suffix.lower() not in PE_SUFFIXES:
            continue
        details = pe_details(path)
        row = {"bundle_path": path.relative_to(dist).as_posix(), **details}
        if "sherpa_onnx" in path.parts:
            imports.append(row)
        if path.name.lower() not in candidates and candidates.intersection(details["imports"]):
            references.append(row)
    python_sources, loader_calls = [], []
    for path in sorted(source.rglob("*.py")):
        content = path.read_text(encoding="utf-8")
        calls = [node for node in ast.walk(ast.parse(content)) if isinstance(node, ast.Call)]
        loaders = [{"line": node.lineno, "call": ast.unparse(node.func)} for node in calls
                   if re.search(r"(?:CDLL|WinDLL|LoadLibrary\w*|load_library)$", ast.unparse(node.func))]
        row = {"path": path.relative_to(source).as_posix(), "sha256": digest(path), "loader_calls": loaders,
               "name_references": [line for line, text in enumerate(content.splitlines(), 1)
                                   if "sherpa-onnx-c-api" in text or "sherpa-onnx-cxx-api" in text]}
        python_sources.append(row)
        if loaders:
            loader_calls.append(row)
    removed = []
    if not references and not loader_calls:
        for path in files:
            if path.name.lower() in candidates:
                removed.append({"bundle_path": path.relative_to(dist).as_posix(), "sha256": digest(path),
                                "bytes": path.stat().st_size})
                path.unlink()
    payload = {"files": imports, "importers": references, "python_sources": python_sources,
               "python_loader_calls": loader_calls, "removed": removed, "before": before,
               "after": {"files": before["files"] - len(removed),
                         "bytes": before["bytes"] - sum(row["bytes"] for row in removed)}}
    write_json(report, payload)
    return payload


if __name__ == "__main__":
    import sys

    config = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    work = Path(config["work"])
    dist = work / "dist/amadeus-backend"
    redist = redist_inventory(Path(config["vc_redist"]))
    prune_sherpa_c_api(dist, Path(config["venv"]) / "Lib/site-packages/sherpa_onnx", work / "sherpa-imports.json")
    audit = normalize_and_audit(dist, redist, work / "vc-runtime-audit.json")
    sources_path = work / "binary-sources.json"
    sources = json.loads(sources_path.read_text(encoding="utf-8"))
    sources["allowed_roots"]["vc_redist"] = redist["path"]
    replacements = {row["bundle_path"]: row for row in audit["replacements"]}
    for row in sources["non_venv_binaries"]:
        if row["bundle_path"] in replacements:
            row.update(replacements[row["bundle_path"]])
    write_json(sources_path, sources)
