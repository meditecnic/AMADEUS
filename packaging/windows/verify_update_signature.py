"""Verify Tauri's base64-wrapped minisign update and its release metadata."""
from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import json
from pathlib import Path
import sys
from urllib.parse import unquote, urlsplit

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey


def decode64(value: str, label: str) -> bytes:
    try:
        return base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error) as error:
        raise ValueError(f"{label}: invalid base64") from error


def minisign_lines(value: str, label: str, count: int) -> list[str]:
    try:
        lines = decode64(value, label).decode("utf-8").splitlines()
    except UnicodeError as error:
        raise ValueError(f"{label}: invalid UTF-8") from error
    if len(lines) != count:
        raise ValueError(f"{label}: expected {count} minisign lines")
    return lines


def verify_update(installer: Path, signature: Path, config: Path, latest: Path) -> dict:
    config_data = json.loads(config.read_text(encoding="utf-8"))
    public_lines = minisign_lines(config_data["plugins"]["updater"]["pubkey"], "public key", 2)
    public = decode64(public_lines[1], "public key packet")
    if len(public) != 42 or public[:2] not in (b"Ed", b"ED"):
        raise ValueError("public key: invalid minisign packet or algorithm")
    signature_text = signature.read_text(encoding="utf-8").strip()
    lines = minisign_lines(signature_text, "signature", 4)
    packet = decode64(lines[1], "signature packet")
    if len(packet) != 74 or packet[:2] not in (b"Ed", b"ED"):
        raise ValueError("signature: invalid minisign packet or algorithm")
    if packet[2:10] != public[2:10]:
        raise ValueError("key id: signature does not match configured public key")
    if not lines[2].startswith("trusted comment: "):
        raise ValueError("trusted comment: missing minisign prefix")
    global_signature = decode64(lines[3], "trusted comment global signature")
    if len(global_signature) != 64:
        raise ValueError("trusted comment: invalid global signature length")
    # minisign-verify 0.2.5: ED signs BLAKE2b-512; Ed signs the raw file.
    # Both use ordinary Ed25519, not Ed25519ph. Global signs sig || comment.
    blake = hashlib.blake2b(digest_size=64)
    sha = hashlib.sha256()
    size = 0
    with installer.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            blake.update(chunk)
            sha.update(chunk)
            size += len(chunk)
    message = blake.digest() if packet[:2] == b"ED" else installer.read_bytes()
    key = Ed25519PublicKey.from_public_bytes(public[10:])
    try:
        key.verify(packet[10:], message)
    except InvalidSignature as error:
        raise ValueError("installer signature: verification failed") from error
    try:
        key.verify(global_signature, packet[10:] + lines[2][17:].encode("utf-8"))
    except InvalidSignature as error:
        raise ValueError("trusted comment global signature: verification failed") from error
    windows = json.loads(latest.read_text(encoding="utf-8"))["platforms"]["windows-x86_64"]
    # release_artifacts.latest_manifest strips the sidecar's outer whitespace.
    if windows["signature"] != signature_text:
        raise ValueError("latest.json signature: does not match .sig contents")
    if unquote(urlsplit(windows["url"]).path.rsplit("/", 1)[-1]) != installer.name:
        raise ValueError("latest.json url: filename does not match installer")
    return {"key_id": public[2:10][::-1].hex().upper(), "filename": installer.name,
            "size": size, "sha256": sha.hexdigest()}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("installer", "signature", "config", "latest"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = verify_update(args.installer, args.signature, args.config, args.latest)
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(f"UPDATE_SIGNATURE_FAILED: {error}", file=sys.stderr)
        return 1
    print("UPDATE_SIGNATURE_OK " + json.dumps(result, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
