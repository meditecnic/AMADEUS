"""PyInstaller collection, byte-checked DLL deduplication and bundle inventory."""
from __future__ import annotations

from collections import defaultdict
import hashlib
from importlib import metadata
import json
from pathlib import Path
import sys
import zipfile


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def collect_inputs(config: dict) -> tuple[list, list, list, list]:
    from PyInstaller.utils.hooks import (
        collect_all, collect_data_files, collect_dynamic_libs, collect_submodules,
    )

    repo = Path(config["repo"])
    datas = [(str(repo / "backend" / relative), str(Path(relative).parent))
             for relative in config["app_files"]]
    datas.append((config["hf_cache"], "hf_cache"))
    binaries = []
    hidden = [
        "uvicorn.logging", "uvicorn.loops.asyncio", "uvicorn.protocols.http.h11_impl",
        "uvicorn.protocols.websockets.websockets_impl", "uvicorn.lifespan.on",
        "sentence_transformers.models.Transformer", "sentence_transformers.models.Pooling",
        "sentence_transformers.models.Normalize", "sherpa_onnx.lib._sherpa_onnx",
    ]
    # The tokenizer registry probes metaclip_2's shared aliases before XLM-R.
    for package in ("transformers.models.bert", "transformers.models.xlm_roberta",
                    "transformers.models.metaclip_2", "transformers.models.auto"):
        hidden.extend(collect_submodules(package, filter=lambda name: ".modeling_tf_" not in name and ".modeling_flax_" not in name))
    for package in ("sqlite_vec", "curl_cffi", "sherpa_onnx", "sentence_transformers", "ddgs"):
        data, binary, imports = collect_all(
            package, include_py_files=False,
            filter_submodules=lambda name: ".tests" not in name and ".test_" not in name,
        )
        datas.extend(data)
        binaries.extend(binary)
        hidden.extend(imports)
    datas.extend(collect_data_files("transformers"))
    datas.extend(collect_data_files("fake_useragent"))
    binaries.extend(collect_dynamic_libs("torch"))
    excludes = [
        "pytest", "_pytest", "pytest_asyncio", "pytest_timeout", "ruff",
        "tkinter", "IPython", "notebook", "matplotlib", "pandas", "tensorflow",
        "jax", "flax", "torchaudio", "torchvision", "librosa", "numba", "llvmlite",
        "scipy.tests", "numpy.tests", "sklearn.tests",
    ]
    return datas, binaries, sorted(set(hidden)), excludes


def record_analysis(analysis, config: dict) -> None:
    """Record ownership evidence before pruning duplicate root DLLs."""
    work = Path(config["work"])
    rows = [list(row) for row in (*analysis.scripts, *analysis.pure, *analysis.binaries, *analysis.datas)]
    write_json(work / "analysis.json", rows)
    libraries = defaultdict(list)
    for row in analysis.binaries:
        destination = Path(row[0]).as_posix()
        if destination.lower().endswith(".dll"):
            libraries[Path(destination).name.lower()].append(row)
    removed = set()
    changes = []
    # These packages configure their own library search directory on import.
    canonical_dirs = ("torch/lib/", "numpy.libs/", "scipy.libs/", "sherpa_onnx/lib/")
    for group in libraries.values():
        roots = [row for row in group if "/" not in Path(row[0]).as_posix()]
        packaged = [row for row in group if Path(row[0]).as_posix().startswith(canonical_dirs)]
        if not roots or not packaged:
            continue
        keep = sorted(packaged, key=lambda row: row[0])[0]
        for redundant in roots:
            kept_hash = digest(Path(keep[1]))
            removed_hash = digest(Path(redundant[1]))
            if kept_hash != removed_hash:
                print(f"DLL_DIFFERENT kept both: {redundant[0]} / {keep[0]}", flush=True)
                continue
            removed.add(redundant[0])
            change = {"removed": redundant[0], "kept": keep[0], "sha256": kept_hash,
                      "bytes": Path(redundant[1]).stat().st_size}
            changes.append(change)
            print("DLL_DEDUPE " + json.dumps(change), flush=True)
    analysis.binaries = [row for row in analysis.binaries if row[0] not in removed]
    analysis.datas = [row for row in analysis.datas
                      if not Path(row[0]).as_posix().startswith("torch/include/")
                      and ".dist-info/licenses/" not in Path(row[0]).as_posix()]
    write_json(work / "dll-deduplication.json", changes)


def license_inventory(dist: Path, rows: list) -> dict:
    """Attribute analyzed source files to wheel RECORDs and copy dist-info licenses."""
    sources = {str(Path(row[1]).resolve()).casefold() for row in rows if row[1]}
    root = dist / "THIRD_PARTY_LICENSES"
    root.mkdir()
    entries = []
    for distribution in sorted(metadata.distributions(), key=lambda item: item.metadata["Name"].lower()):
        files = distribution.files or []
        included = [file for file in files if str(Path(distribution.locate_file(file)).resolve()).casefold() in sources]
        if not included:
            continue
        name, version = distribution.metadata["Name"], distribution.version
        license_files = []
        declared = distribution.metadata.get_all("License-File") or []
        for file in files:
            parts = file.parts
            if not parts or not parts[0].endswith(".dist-info"):
                continue
            relative = Path(*parts[1:])
            lower = relative.as_posix().lower()
            if ("licenses" in relative.parts or relative.name.lower().startswith(("license", "licence", "copying", "notice", "authors"))
                    or relative.as_posix() in declared or lower in {value.lower() for value in declared}):
                origin = Path(distribution.locate_file(file))
                if origin.is_file():
                    license_files.append((relative.as_posix(), origin))
        archive_name = None
        if license_files:
            archive_name = f"{name}-{version}.zip"
            with zipfile.ZipFile(root / archive_name, "w", zipfile.ZIP_DEFLATED) as archive:
                for relative, origin in sorted(license_files):
                    archive.write(origin, relative)
            with zipfile.ZipFile(root / archive_name) as archive:
                for relative, origin in license_files:
                    if hashlib.sha256(archive.read(relative)).hexdigest() != digest(origin):
                        raise RuntimeError(f"license archive mismatch: {name}/{relative}")
        entries.append({
            "name": name, "version": version,
            "license": distribution.metadata.get("License"),
            "license_expression": distribution.metadata.get("License-Expression"),
            "declared_license_files": declared, "archive": archive_name,
            "files": [{"path": relative, "sha256": digest(origin)} for relative, origin in sorted(license_files)],
            "analyzed_files": len(included),
        })
    payload = {"count": len(entries), "packages": entries,
               "without_license_files": [item["name"] for item in entries if not item["files"]]}
    write_json(root / "manifest.json", payload)
    return payload


def finalize(config: dict) -> None:
    work = Path(config["work"])
    dist = work / "dist/amadeus-backend"
    rows = json.loads((work / "analysis.json").read_text(encoding="utf-8"))
    licenses = license_inventory(dist, rows)
    # A model card preserves the bundled model's license and pinned source information.
    model_notice = {
        **config["model"],
        "source": "https://huggingface.co/intfloat/multilingual-e5-small/tree/" + config["model"]["revision"],
    }
    write_json(dist / "THIRD_PARTY_LICENSES/e5-model.json", model_notice)
    files = [(path.relative_to(dist).as_posix(), path.stat().st_size)
             for path in dist.rglob("*") if path.is_file()]
    directories = defaultdict(int)
    for relative, size in files:
        for parent in Path(relative).parents:
            if parent != Path("."):
                directories[parent.as_posix()] += size
    dedupe = json.loads((work / "dll-deduplication.json").read_text(encoding="utf-8"))
    dlls = defaultdict(list)
    for path in dist.rglob("*"):
        if path.is_file() and path.suffix.lower() == ".dll":
            dlls[path.name.lower()].append(path)
    duplicate_dlls = []
    for name, paths in sorted(dlls.items()):
        if len(paths) > 1:
            copies = [{"path": path.relative_to(dist).as_posix(), "sha256": digest(path),
                       "bytes": path.stat().st_size} for path in sorted(paths)]
            duplicate_dlls.append({"name": name, "identical": len({item["sha256"] for item in copies}) == 1,
                                   "copies": copies, "action": "retained for package-specific loader paths"})
    write_json(work / "duplicate-dll-audit.json", duplicate_dlls)
    payload = {
        "total_bytes": sum(size for _, size in files),
        "e5_bytes": sum(size for relative, size in files if relative.startswith("_internal/hf_cache/")),
        "largest_20_files": sorted(files, key=lambda item: (-item[1], item[0]))[:20],
        "largest_20_directories": sorted(directories.items(), key=lambda item: (-item[1], item[0]))[:20],
        "deduplicated_bytes": sum(item["bytes"] for item in dedupe), "dll_deduplication": dedupe,
        "retained_duplicate_dlls": duplicate_dlls,
        "license_count": licenses["count"], "without_license_files": licenses["without_license_files"],
        "tools": {name: metadata.version(name) for name in (
            "PyInstaller", "pyinstaller-hooks-contrib", "altgraph", "pefile", "pywin32-ctypes", "packaging", "setuptools",
        )},
    }
    write_json(work / "bundle-inventory.json", payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    finalize(json.loads(Path(sys.argv[1]).read_text(encoding="utf-8")))
