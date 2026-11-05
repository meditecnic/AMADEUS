"""Validate release versions and collect the Windows release files."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil

from bundle_support import digest

REPO = Path(__file__).resolve().parents[2]


def check_version(config: dict, tag: str) -> str:
    version = config["version"]
    if tag and tag != "v" + version:
        raise ValueError(f"Tag {tag} does not match tauri.conf.json version {version}")
    return version


def collect(work: Path, output: Path, notes: str, repository: str, tag: str) -> None:
    result = json.loads((work / "installer-result.json").read_text(encoding="utf-8"))
    version = check_version(result["config"], tag)
    output.mkdir(parents=True, exist_ok=False)
    installer = Path(result["installer"])
    sources = work / "materials" / f"amadeus-{version}-speech-sources.zip"
    for path in (installer, sources):
        shutil.copyfile(path, output / path.name)
    checksums = "".join(f"{digest(path)}  {path.name}\n" for path in sorted(output.iterdir()))
    (output / "SHA256SUMS.txt").write_text(checksums, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("check", "collect"))
    parser.add_argument("--config", type=Path, default=REPO / "desktop/src-tauri/tauri.conf.json")
    parser.add_argument("--tag", default=os.environ.get("RELEASE_TAG", ""))
    parser.add_argument("--work-dir", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--notes", type=Path, default=REPO / ".github/release-template.md")
    parser.add_argument("--repository", default=os.environ.get("GITHUB_REPOSITORY", "meditecnic/AMADEUS"))
    args = parser.parse_args()
    if args.mode == "check":
        config = json.loads(args.config.read_text(encoding="utf-8"))
        print("version=" + check_version(config, args.tag))
    else:
        if args.work_dir is None or args.output is None:
            parser.error("collect requires --work-dir and --output")
        collect(args.work_dir, args.output, args.notes.read_text(encoding="utf-8"), args.repository, args.tag)


if __name__ == "__main__":
    main()
