"""Assemble and verify the installer-wide third-party license inventory."""
from __future__ import annotations

import argparse
from collections import Counter
from importlib import metadata
import json
from pathlib import Path
import re
import shutil
import ssl
import sqlite3
import sys
import zipfile

from bundle_support import digest, write_json
from source_materials import notices

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]


def license_files(root: Path) -> list[Path]:
    files = []
    for path in sorted(root.iterdir()):
        if path.is_file() and path.name.lower().startswith(("license", "licence", "copying", "notice", "copyright", "unlicense", "ofl", "third-party", "third_party")):
            files.append(path)
        elif path.is_dir() and path.name.lower() in {"licenses", "licences", "notices"}:
            files.extend(sorted(item for item in path.rglob("*") if item.is_file()))
    return files


class Bundle:
    def __init__(self, output: Path, backend: Path, main_exe: str):
        self.output, self.backend, self.main_exe = output, backend, main_exe
        output.mkdir(parents=True)
        self.components, self.mismatches, self.gaps = [], [], []

    def copy(self, source: Path, relative: str) -> dict:
        target = self.output / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        expected = digest(source)
        if digest(target) != expected:
            raise RuntimeError(f"License copy hash mismatch: {source} -> {target}")
        return {"path": "licenses/" + relative, "sha256": expected}

    def text(self, relative: str, content: str) -> dict:
        target = self.output / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return {"path": "licenses/" + relative, "sha256": digest(target)}

    def add(self, category: str, name: str, version: str, source: str | dict,
            license: str | None, installed_paths: list[str], files: list[dict], **extra) -> dict:
        row = {"category": category, "name": name, "version": version, "source": source,
               "license": license, "installed_paths": installed_paths, "license_files": files, **extra}
        self.components.append(row)
        return row


def python_notices(bundle: Bundle, config: dict, pins: list[dict]) -> dict[str, dict]:
    source = bundle.backend / "THIRD_PARTY_LICENSES"
    shutil.copytree(source, bundle.output / "python")
    manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
    supplemental = {row["name"].lower(): row for row in pins if row["category"] == "python-upstream"}
    analyzed = json.loads((Path(config["work"]) / "analysis.json").read_text(encoding="utf-8"))
    destinations = {}
    for destination, origin, kind in analyzed:
        if not origin:
            continue
        path = bundle.backend / "_internal" / destination
        installed = "backend/amadeus-backend.exe" if kind in {"PYMODULE", "PYSOURCE"} else None
        if path.is_file():
            installed = "backend/_internal/" + Path(destination).as_posix()
        if installed:
            destinations.setdefault(str(Path(origin).resolve()).casefold(), set()).add(installed)
    result = {}
    for row in manifest["packages"]:
        files = []
        if row["archive"]:
            path = bundle.output / "python" / row["archive"]
            with zipfile.ZipFile(path) as archive:
                for member in row["files"]:
                    import hashlib
                    if hashlib.sha256(archive.read(member["path"])).hexdigest() != member["sha256"]:
                        raise RuntimeError(f"Python license member mismatch: {path}/{member['path']}")
            files.append({"path": "licenses/python/" + row["archive"], "sha256": digest(path), "members": row["files"]})
        pin = supplemental.get(row["name"].lower())
        if not files and pin:
            if pin["version"] != row["version"]:
                bundle.mismatches.append({"name": row["name"], "installed_version": row["version"], "notice_version": pin["version"]})
            files.append(bundle.copy(Path(pin["local_path"]), "python/upstream/" + pin["file"]))
        license = row["license_expression"] or row["license"] or (pin["license"] if pin else None)
        distribution = metadata.distribution(row["name"])
        installed_paths = set()
        for file in distribution.files or []:
            installed_paths.update(destinations.get(str(Path(distribution.locate_file(file)).resolve()).casefold(), set()))
        result[row["name"].lower()] = bundle.add(
            "python", row["name"], row["version"],
            {"wheel": f"https://pypi.org/project/{row['name']}/{row['version']}/",
             "upstream_notice": {key: pin[key] for key in ("url", "ref", "sha256")} if pin else None},
            license, sorted(installed_paths) or ["backend/amadeus-backend.exe"], files,
        )
    return result


def cpython_notices(bundle: Bundle, config: dict, pins: list[dict]) -> None:
    base = Path(config["base_python"])
    notice = bundle.copy(base / "LICENSE.txt", "cpython/LICENSE.txt")
    text = (base / "LICENSE.txt").read_text(encoding="utf-8").lower()
    rows = json.loads((Path(config["work"]) / "analysis.json").read_text(encoding="utf-8"))
    binaries = []
    for destination, source, kind in rows:
        if kind not in {"BINARY", "EXTENSION"} or not Path(source).resolve().is_relative_to(base):
            continue
        path = bundle.backend / "_internal" / destination
        if path.is_file() and not re.match(r"(?:vcruntime|msvcp|vcomp)", path.name, re.I):
            binaries.append({"path": "backend/_internal/" + Path(destination).as_posix(), "sha256": digest(path)})
    bundle.add("cpython", "CPython", sys.version.split()[0], "https://www.python.org/downloads/release/python-3119/",
               "PSF-2.0 and bundled component licenses", [row["path"] for row in binaries], [notice], native_binaries=binaries)
    native = [
        ("OpenSSL", ssl.OPENSSL_VERSION.split()[1], "Apache-2.0", ("libcrypto", "libssl", "_ssl"), "openssl", "OpenSSL-LICENSE.txt"),
        ("libffi", "3.4.4", "MIT", ("libffi", "_ctypes"), "libffi", None),
        ("SQLite", sqlite3.sqlite_version, "Public domain", ("sqlite3", "_sqlite3"), "sqlite", "SQLite-LICENSE.md"),
        ("bzip2", "1.0.8", "bzip2-1.0.8", ("_bz2",), "bzip2", None),
        ("XZ Utils / liblzma", "5.2.5", "Public-domain and component licenses", ("_lzma",), "lzma", "xz-COPYING"),
        ("zlib", "1.3.1", "Zlib", ("python311",), "zlib", "zlib-LICENSE"),
    ]
    for name, version, license, prefixes, marker, supplement in native:
        paths = [row["path"] for row in binaries if Path(row["path"]).name.lower().startswith(prefixes)]
        if not paths:
            continue
        files = [notice]
        if marker not in text:
            bundle.gaps.append({"category": "cpython-license-section", "name": name,
                                "missing_from": "licenses/cpython/LICENSE.txt", "supplement": supplement})
        if supplement:
            pin = next(row for row in pins if row["file"] == supplement)
            files.append(bundle.copy(Path(pin["local_path"]), "cpython/" + supplement))
            if version != pin["version"]:
                bundle.mismatches.append({"name": name, "installed_version": version, "notice_version": pin["version"]})
        bundle.add("cpython-native", name, version,
                   "https://github.com/python/cpython/blob/v3.11.9/PCbuild/get_externals.bat", license, paths, files,
                   present_in_cpython_license=marker in text)


def rust_notices(bundle: Bundle, metadata_path: Path) -> None:
    cargo = json.loads(metadata_path.read_text(encoding="utf-8"))
    packages = {row["id"]: row for row in cargo["packages"]}
    nodes = {row["id"]: row for row in cargo["resolve"]["nodes"]}
    pending, visited = [cargo["resolve"]["root"]], set()
    while pending:
        key = pending.pop()
        if key in visited:
            continue
        visited.add(key)
        package = packages[key]
        if any("proc-macro" in target["kind"] for target in package["targets"]):
            continue
        for dep in nodes[key]["deps"]:
            if any(kind["kind"] is None for kind in dep["dep_kinds"]):
                pending.append(dep["pkg"])
        if package["source"] is None:
            continue
        root = Path(package["manifest_path"]).parent
        origins = license_files(root)
        if package.get("license_file"):
            explicit = (root / package["license_file"]).resolve()
            if explicit.is_file() and explicit.is_relative_to(root.resolve()) and explicit not in origins:
                origins.append(explicit)
        files = [bundle.copy(path, f"rust/{package['name']}-{package['version']}/" + path.relative_to(root).as_posix())
                 for path in origins]
        bundle.add("rust", package["name"], package["version"],
                   {"registry": package["source"], "url": f"https://crates.io/crates/{package['name']}/{package['version']}",
                    "checksum": package.get("checksum")}, package["license"], [bundle.main_exe], files)


def frontend_notices(bundle: Bundle, node_modules: Path, pins: list[dict]) -> None:
    lock = json.loads((REPO / "desktop/package-lock.json").read_text(encoding="utf-8"))
    for location, row in lock["packages"].items():
        if not location or row.get("dev"):
            continue
        root = node_modules.parent / location
        if not root.is_dir():
            if row.get("optional"):
                continue
            raise RuntimeError(f"Production npm dependency is missing: {root}")
        package = json.loads((root / "package.json").read_text(encoding="utf-8"))
        if row["version"] != package["version"]:
            raise RuntimeError(f"npm version mismatch: {root}")
        name = package["name"]
        files = [bundle.copy(path, f"npm/{name.replace('/', '__')}-{row['version']}/" + path.relative_to(root).as_posix())
                 for path in license_files(root)]
        bundle.add("npm", name, row["version"], {"url": row.get("resolved"), "integrity": row.get("integrity")},
                   row.get("license") or package.get("license"), [bundle.main_exe], files)
    for name, version, source, license, origin in (
        ("Oxanium, IBM Plex Mono, Noto Sans CJK", "vendored", "desktop/public/assets/fonts/s4-ds/PROVENANCE.txt", "OFL-1.1", "desktop/public/assets/fonts/s4-ds/OFL.txt"),
        ("LXGW Neo XiHei", "1.305", "https://github.com/lxgw/LxgwNeoXiHei/releases/tag/v1.305", "IPA Font License 1.0", "desktop/public/assets/fonts/memory/LICENSE.md"),
        ("Worldline transition runtime", "vendored", "https://github.com/meditecnic/worldline-transition", "MIT", "desktop/src/worldline-engine/LICENSE"),
    ):
        files = [bundle.copy(REPO / origin, "frontend/" + name.split(",")[0].replace(" ", "-") + "/" + Path(origin).name)]
        bundle.add("frontend", name, version, source, license, [bundle.main_exe], files)
    font = next(row for row in pins if row["file"] == "ShareTechMono-OFL.txt")
    bundle.add("font", "Share Tech Mono", "unversioned", {key: font[key] for key in ("url", "ref")}, "OFL-1.1",
               [bundle.main_exe], [bundle.copy(Path(font["local_path"]), "fonts/ShareTechMono-OFL.txt")])


def native_notices(bundle: Bundle, config: dict, pins: list[dict], python: dict) -> None:
    audit = json.loads((Path(config["work"]) / "vc-runtime-audit.json").read_text(encoding="utf-8"))
    redist = audit["redist"]
    text = json.dumps({"redist": redist, "distributed_files": audit["runtimes"],
                       "terms": "https://visualstudio.microsoft.com/license-terms/vs2026-ga-diagnostic-buildtools/",
                       "redist_list": "https://learn.microsoft.com/en-us/visualstudio/releases/2026/redistribution"}, ensure_ascii=False, indent=2)
    file = bundle.text("vc-runtime/SOURCE-AND-TERMS.json", text)
    bundle.add("vc-runtime", "Microsoft Visual C++ app-local runtime", redist["file_version"], redist["path"],
               "Microsoft Visual Studio redistribution terms", ["backend/" + row["bundle_path"] for row in audit["runtimes"] if row["scope"] == "root"], [file])
    for runtime in audit["runtimes"]:
        if runtime["scope"] == "package-local":
            owner = "numpy" if "/numpy.libs/" in runtime["bundle_path"] else "scikit-learn"
            bundle.add("vc-runtime", "Package-local " + runtime["bundle_path"], runtime["file_version"],
                       python[owner]["source"], "Microsoft Visual Studio redistribution terms", ["backend/" + runtime["bundle_path"]], [file],
                       binary_sha256=runtime["sha256"])
    speech = next(row for row in pins if row["name"] == "eSpeak NG")
    binaries = ["backend/" + path.relative_to(bundle.backend).as_posix() for path in bundle.backend.rglob("*")
                if path.is_file() and (path.name.startswith("_sherpa_onnx") or path.name == "sherpa-onnx-c-api.dll")]
    source_rows = []
    for file, stem in (("espeak-ng-for-piper.cmake", "espeak_ng"), ("piper-phonemize.cmake", "piper_phonemize")):
        cmake = next(row for row in pins if row["file"] == file)
        content = Path(cmake["local_path"]).read_text(encoding="utf-8")
        source_rows.append({"cmake_url": cmake["url"], "cmake_sha256": cmake["sha256"],
                            "url": re.search(rf'set\({stem}_URL\s+"([^"]+)"\)', content)[1],
                            "sha256": re.search(rf'set\({stem}_HASH\s+"SHA256=([0-9a-f]{{64}})"\)', content)[1]})
    note = bundle.text("copyleft/SPEECH-SOURCE.json", json.dumps({
        "binary_files_containing_espeak_ng": binaries,
        "sherpa_source": "https://github.com/k2-fsa/sherpa-onnx/tree/v1.13.8", "dependencies": source_rows,
        "source_package": "amadeus-0.1.0-speech-sources.zip",
        "download_command": "py -3.11 packaging/windows/source_materials.py --work-dir <artifact-directory>",
    }, indent=2))
    description = "# Speech component source\n\neSpeak NG revision: `" + speech["version"] + "`.\n\nContained in:\n"
    description += "".join(f"- `{path}`\n" for path in binaries)
    description += "\nSherpa source: https://github.com/k2-fsa/sherpa-onnx/tree/v1.13.8\n\nBuild dependency archives:\n"
    for source in source_rows:
        description += f"\n- {source['url']}\n  SHA-256: `{source['sha256']}`\n  Declared by: {source['cmake_url']}\n"
    description += ("\nDownload and verify these sources with `packaging/windows/source_materials.py --work-dir <artifact-directory>`. "
                    "Publish the resulting `amadeus-0.1.0-speech-sources.zip` alongside this application version.\n")
    source_note = bundle.text("copyleft/SPEECH-SOURCE.md", description)
    bundle.add("copyleft", "eSpeak NG", speech["version"], {key: speech[key] for key in ("url", "ref")}, "GPL-3.0", binaries,
               [bundle.copy(Path(speech["local_path"]), "copyleft/eSpeak-NG-COPYING"), note, source_note])
    libs = []
    for owner in ("scipy", "numpy"):
        native = [path for path in (bundle.backend / f"_internal/{owner}.libs").glob("*.dll") if "openblas" in path.name.lower()]
        paths = ["backend/" + path.relative_to(bundle.backend).as_posix() for path in native]
        libs.extend(paths)
        if native:
            evidence = [path for path in native if b"gcc-10.3.0/libgfortran" in path.read_bytes()]
            version = "GCC 10.3.0 (binary source-path strings)" if len(evidence) == len(native) else "unconfirmed"
            bundle.add("copyleft", f"{owner} libgfortran runtime", version,
                       "https://github.com/gcc-mirror/gcc/tree/releases/gcc-10.3.0/libgfortran", "GPL-3.0-or-later WITH GCC-exception-3.1",
                       paths, python[owner]["license_files"], linkage=f"statically included in the {owner} wheel's OpenBLAS DLL")
    quad = next(row for row in pins if row["file"] == "gcc-libquadmath-COPYING.LIB")
    bundle.add("copyleft", "libquadmath review material", "unconfirmed", quad["url"], "LGPL-2.1-or-later", libs,
               [bundle.copy(Path(quad["local_path"]), "copyleft/libquadmath-COPYING.LIB")],
               source_ref=quad["ref"], verification="No separate DLL or identifying string found; static inclusion/version not established")
    bundle.gaps.append({"category": "copyleft-provenance", "name": "libquadmath", "detail": "Static inclusion and exact source version require confirmation"})
    for row in python.values():
        label = row["license"] or ""
        if re.search(r"\b(?:A?GPL|LGPL)[- v]?\d", label, re.I) and row["name"].lower() != "scipy":
            source = row["source"]
            if row["name"].lower() == "pyinstaller":
                source = {"distribution": source, "source": f"https://github.com/pyinstaller/pyinstaller/tree/v{row['version']}"}
            bundle.add("copyleft", row["name"], row["version"], source, row["license"], row["installed_paths"], row["license_files"])


def generate(config: dict, backend: Path, work: Path, output: Path, cargo: Path, node_modules: Path, main_exe: str) -> dict:
    if output.exists():
        raise RuntimeError(f"License output already exists: {output}")
    bundle = Bundle(output, backend, main_exe)
    pins = notices(work)
    for name in ("LICENSE", "THIRD_PARTY_NOTICES.md"):
        bundle.copy(REPO / name, name)
    python = python_notices(bundle, config, pins)
    cpython_notices(bundle, config, pins)
    rust_notices(bundle, cargo)
    frontend_notices(bundle, node_modules, pins)
    native_notices(bundle, config, pins, python)
    model = json.loads((backend / "THIRD_PARTY_LICENSES/e5-model.json").read_text(encoding="utf-8"))
    card = next((backend / "_internal/hf_cache").rglob("README.md"))
    bundle.add("model", model["id"], model["revision"], model["source"], "MIT", ["backend/_internal/hf_cache"],
               [bundle.copy(card, "models/multilingual-e5-small-README.md")])
    files = [{"path": "licenses/" + path.relative_to(output).as_posix(), "sha256": digest(path)}
             for path in sorted(output.rglob("*")) if path.is_file()]
    result = {"schema_version": 1, "components": bundle.components, "files": files,
              "counts": dict(Counter(row["category"] for row in bundle.components)),
              "without_license_files": [{key: row[key] for key in ("category", "name", "version")}
                                        for row in bundle.components if not row["license_files"]],
              "version_mismatches": bundle.mismatches, "coverage_gaps": bundle.gaps}
    write_json(output / "manifest.json", result)
    return result


def verify(root: Path, license_dir: Path | None = None) -> dict:
    directory = license_dir or root / "licenses"
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    checked = 0
    for entry in manifest["files"]:
        path = (directory / Path(entry["path"]).relative_to("licenses")).resolve()
        if not path.is_relative_to(directory.resolve()) or not path.is_file() or digest(path) != entry["sha256"]:
            raise RuntimeError(f"License hash mismatch or missing file: {entry['path']}")
        checked += 1
    for entry in manifest["components"]:
        for file in entry["license_files"]:
            path = (directory / Path(file["path"]).relative_to("licenses")).resolve()
            if not path.is_relative_to(directory.resolve()) or not path.is_file() or digest(path) != file["sha256"]:
                raise RuntimeError(f"Component license hash mismatch: {entry['name']}: {path}")
            if file.get("members"):
                import hashlib
                with zipfile.ZipFile(path) as archive:
                    for member in file["members"]:
                        if hashlib.sha256(archive.read(member["path"])).hexdigest() != member["sha256"]:
                            raise RuntimeError(f"License ZIP member mismatch: {path}: {member['path']}")
        if license_dir is None:
            for relative in entry["installed_paths"]:
                path = (root / relative).resolve()
                if not path.is_relative_to(root.resolve()) or not path.exists():
                    raise RuntimeError(f"Installed component path missing: {entry['name']}: {relative}")
    result = {"files_checked": checked, "components_checked": len(manifest["components"]), "counts": manifest["counts"]}
    print("LICENSES_OK " + json.dumps(result))
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify", type=Path, help="verify an installed application root")
    parser.add_argument("--build-config", type=Path)
    parser.add_argument("--backend", type=Path)
    parser.add_argument("--work-dir", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--cargo-metadata", type=Path)
    parser.add_argument("--node-modules", type=Path)
    parser.add_argument("--main-exe", default="desktop.exe")
    args = parser.parse_args()
    if args.verify:
        verify(args.verify.resolve())
    else:
        for name in ("build_config", "backend", "work_dir", "output", "cargo_metadata", "node_modules"):
            if getattr(args, name) is None:
                parser.error(f"--{name.replace('_', '-')} is required when generating")
        config = json.loads(args.build_config.read_text(encoding="utf-8"))
        result = generate(config, args.backend.resolve(), args.work_dir.resolve(), args.output.resolve(),
                          args.cargo_metadata.resolve(), args.node_modules.resolve(), args.main_exe)
        verify(args.output.resolve().parent, args.output.resolve())
