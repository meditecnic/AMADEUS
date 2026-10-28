"""Download pinned notices and assemble the speech component source archives."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import urllib.request
import zipfile

from bundle_support import digest, write_json

HERE = Path(__file__).resolve().parent


def download(url: str, target: Path, expected: str | None = None) -> dict:
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        print(f"DOWNLOAD {url}", flush=True)
        request = urllib.request.Request(url, headers={"User-Agent": "Amadeus-packaging/0.1.0"})
        with urllib.request.urlopen(request, timeout=120) as response, target.open("wb") as output:
            while chunk := response.read(1024 * 1024):
                output.write(chunk)
    actual = digest(target)
    if expected and actual != expected:
        raise RuntimeError(f"Source hash mismatch: {target}; expected {expected}, got {actual}")
    return {"url": url, "file": target.name, "sha256": actual, "bytes": target.stat().st_size}


def notices(work: Path) -> list[dict]:
    entries = json.loads((HERE / "upstream_materials.json").read_text(encoding="utf-8"))
    for entry in entries:
        path = work / "upstream" / entry["file"]
        download(entry["url"], path, entry["sha256"])
        entry["local_path"] = str(path)
    return entries


def build_sources(work: Path, version: str) -> dict:
    entries = [entry for entry in notices(work)
               if entry["name"] in {"sherpa-onnx-core", "eSpeak NG"} or entry["category"] == "build-source"]
    root = work / "sources"
    root.mkdir(exist_ok=True)
    archives = [download("https://github.com/k2-fsa/sherpa-onnx/archive/refs/tags/v1.13.8.zip",
                         root / "sherpa-onnx-v1.13.8.zip")]
    for name, stem in (("espeak-ng-for-piper.cmake", "espeak_ng"), ("piper-phonemize.cmake", "piper_phonemize")):
        text = (work / "upstream" / name).read_text(encoding="utf-8")
        url = re.search(rf'set\({stem}_URL\s+"([^"]+)"\)', text)[1]
        expected = re.search(rf'set\({stem}_HASH\s+"SHA256=([0-9a-f]{{64}})"\)', text)[1]
        filename = url.rsplit("/", 1)[1]
        archives.append({**download(url, root / filename, expected), "cmake": name, "expected_sha256": expected})
    manifest = {"sherpa_tag": "v1.13.8", "archives": archives,
                "notices": [{key: value for key, value in entry.items() if key != "local_path"} for entry in entries]}
    for row in archives:
        with zipfile.ZipFile(root / row["file"]) as archive:
            invalid = archive.testzip()
            if invalid:
                raise RuntimeError(f"Invalid source archive member: {row['file']}/{invalid}")
    write_json(root / "sources-manifest.json", manifest)
    output = work / f"amadeus-{version}-speech-sources.zip"
    with zipfile.ZipFile(output, "w", zipfile.ZIP_STORED) as archive:
        for row in archives:
            archive.write(root / row["file"], "archives/" + row["file"])
        archive.write(root / "sources-manifest.json", "sources-manifest.json")
        for entry in entries:
            archive.write(entry["local_path"], "notices/" + entry["file"])
    result = {"output": str(output), "bytes": output.stat().st_size, "sha256": digest(output), **manifest}
    write_json(work / "source-result.json", result)
    print(json.dumps({key: result[key] for key in ("output", "bytes", "sha256")}))
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--version", default="0.1.0")
    args = parser.parse_args()
    args.work_dir.resolve().mkdir(parents=True, exist_ok=True)
    build_sources(args.work_dir.resolve(), args.version)
