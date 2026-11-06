"""Validate release versions and collect the Windows release files."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
from urllib.parse import quote

from bundle_support import digest, write_json

REPO = Path(__file__).resolve().parents[2]
PUBLIC_KEY_PLACEHOLDER = "REPLACE_WITH_TAURI_UPDATER_PUBLIC_KEY"


def check_version(config: dict, tag: str) -> str:
    version = config["version"]
    if tag and tag != "v" + version:
        raise ValueError(f"Tag {tag} does not match tauri.conf.json version {version}")
    return version


def check_signing(config: dict, tag: str, has_private_key: bool) -> None:
    if not tag:
        return
    key = config.get("plugins", {}).get("updater", {}).get("pubkey", "").strip()
    if not key or key == PUBLIC_KEY_PLACEHOLDER:
        raise ValueError("Tag build requires a real updater public key; pubkey is still a placeholder")
    if not has_private_key:
        raise ValueError("Tag build requires TAURI_SIGNING_PRIVATE_KEY")


def latest_manifest(version: str, installer: Path, signature: str, notes: str, repository: str) -> dict:
    if not signature.strip():
        raise ValueError("Updater signature is empty")
    return {"version": version, "notes": notes, "platforms": {"windows-x86_64": {
        "url": f"https://github.com/{repository}/releases/download/v{version}/{quote(installer.name)}",
        "signature": signature.strip(),
    }}}


def collect(work: Path, output: Path, notes: str, repository: str, tag: str) -> None:
    result = json.loads((work / "installer-result.json").read_text(encoding="utf-8"))
    version = check_version(result["config"], tag)
    notes = notes.replace("<版本>", version)
    (work / "release-notes.md").write_text(notes, encoding="utf-8")
    output.mkdir(parents=True, exist_ok=False)
    installer = Path(result["installer"])
    sources = work / "materials" / f"amadeus-{version}-speech-sources.zip"
    for path in (installer, sources):
        shutil.copyfile(path, output / path.name)
    signature = result.get("updater_signature")
    if signature:
        signature = Path(signature)
        shutil.copyfile(signature, output / signature.name)
        write_json(output / "latest.json", latest_manifest(
            version, installer, signature.read_text(encoding="utf-8"), notes, repository))
    elif tag:
        raise ValueError("Tag build is missing the signed updater artifact")
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
        version = check_version(config, args.tag)
        check_signing(config, args.tag, bool(os.environ.get("TAURI_SIGNING_PRIVATE_KEY")))
        print("version=" + version)
    else:
        if args.work_dir is None or args.output is None:
            parser.error("collect requires --work-dir and --output")
        collect(args.work_dir, args.output, args.notes.read_text(encoding="utf-8"), args.repository, args.tag)


if __name__ == "__main__":
    main()
