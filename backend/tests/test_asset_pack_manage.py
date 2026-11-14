"""Tests for asset pack inspection, validation, atomic installation, removal, and security."""

from __future__ import annotations

import base64
from io import BytesIO
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
from types import SimpleNamespace
import warnings
import zipfile

import pytest

from app.security import local_transport
from app.services import asset_pack_installer as installer
from app.services import data_startup

# Minimal 1x1 transparent PNG (valid signature + IHDR + IDAT + IEND)
_TINY_PNG = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15c4"
    b"\x00\x00\x00\rIDATx\x9cc`\x00\x00\x00\x02\x00\x01H\xaf\xa4q"
    b"\x00\x00\x00\x00IEND\xaeB`\x82"
)
_TINY_OGG = b"OggS\x00\x02\x00\x00\x00\x00\x00\x00\x00\x00dummy-ogg-payload"


def _sample_bytes(rel_path: str) -> bytes:
    if rel_path.endswith(".png"):
        # Make each PNG slightly distinct while keeping valid PNG magic bytes.
        return _TINY_PNG + rel_path.encode("utf-8")
    if rel_path.endswith(".ogg"):
        return _TINY_OGG + rel_path.encode("utf-8")
    return b"data"


def _build_pack_dir(
    root: Path,
    *,
    files: list[str] | None = None,
    manifest: dict | bytes | None = None,
    extra_files: dict[str, bytes] | None = None,
    wrapper: str | None = None,
) -> Path:
    base = root / wrapper if wrapper else root
    base.mkdir(parents=True, exist_ok=True)
    target_files = list(installer.REQUIRED_FILES) if files is None else files
    for rel in target_files:
        path = base / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(_sample_bytes(rel))
    if extra_files:
        for rel, content in extra_files.items():
            path = base / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
    if manifest is not None:
        manifest_path = base / "manifest.json"
        if isinstance(manifest, bytes):
            manifest_path.write_bytes(manifest)
        else:
            manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    return root


def _build_zip_bytes(
    *,
    files: list[str] | None = None,
    manifest: dict | bytes | None = None,
    extra_files: dict[str, bytes] | None = None,
    wrapper: str | None = None,
    raw_entries: list[tuple[str | zipfile.ZipInfo, bytes]] | None = None,
) -> bytes:
    buf = BytesIO()
    prefix = f"{wrapper}/" if wrapper else ""
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        target_files = list(installer.REQUIRED_FILES) if files is None else files
        for rel in target_files:
            zf.writestr(f"{prefix}{rel}", _sample_bytes(rel))
        if extra_files:
            for rel, content in extra_files.items():
                zf.writestr(f"{prefix}{rel}", content)
        if manifest is not None:
            raw = manifest if isinstance(manifest, bytes) else json.dumps(manifest, ensure_ascii=False).encode("utf-8")
            zf.writestr(f"{prefix}manifest.json", raw)
        if raw_entries:
            for entry, content in raw_entries:
                zf.writestr(entry, content)
    return buf.getvalue()


def test_status_uninstalled_and_partial_and_env_override(app_client, tmp_path, monkeypatch):
    resp = app_client.get("/api/data/asset-pack")
    assert resp.status_code == 200
    status = resp.json()
    assert status["installed"] is False
    assert status["complete"] is False
    assert status["movable"] is True
    assert status["source"] == "data_dir"
    assert status["name"] is None
    assert status["version"] is None
    assert status["size_bytes"] == 0
    assert status["required_count"] == 34
    assert status["present_count"] == 0
    assert len(status["missing_files"]) == 34

    # Simulate partial manual files in data_dir.
    data_dir = Path(os.environ["AMADEUS_DATA_DIR"])
    partial_root = data_dir / "asset-pack" / "amadeus-original"
    (partial_root / "assets" / "images").mkdir(parents=True)
    (partial_root / "assets" / "images" / "kurisu_normal1.png").write_bytes(_TINY_PNG)
    status = app_client.get("/api/data/asset-pack").json()
    assert status["installed"] is True
    assert status["complete"] is False
    assert status["present_count"] == 1
    assert len(status["missing_files"]) == 33
    assert status["size_bytes"] == len(_TINY_PNG)

    # Set AMADEUS_ASSET_PACK_DIR override -> non-movable, source="env_override".
    override_dir = tmp_path / "ext-pack"
    _build_pack_dir(override_dir, manifest={"format": 1, "name": "外部素材包", "version": "2.1"})
    monkeypatch.setenv("AMADEUS_ASSET_PACK_DIR", str(override_dir))
    status = app_client.get("/api/data/asset-pack").json()
    assert status["installed"] is True
    assert status["complete"] is True
    assert status["movable"] is False
    assert status["source"] == "env_override"
    assert status["name"] == "外部素材包"
    assert status["version"] == "2.1"
    assert status["present_count"] == 34
    assert status["missing_files"] == []

    # Override still allows status even when data_startup is unavailable, while install/delete return 409.
    assert app_client.post("/api/data/asset-pack/install", json={"source_path": str(override_dir)}).status_code == 409
    assert app_client.post("/api/data/asset-pack/install", json={"source_path": str(override_dir)}).json()["detail"]["code"] == "asset_pack_not_movable"
    del_resp = app_client.delete("/api/data/asset-pack")
    assert del_resp.status_code == 409
    assert del_resp.json()["detail"]["code"] == "asset_pack_not_movable"
    assert (override_dir / "assets" / "images" / "kurisu_normal1.png").is_file()


def test_data_unavailable_blocks_all_endpoints_in_data_dir_mode(app_client, tmp_path, monkeypatch):
    monkeypatch.setattr(data_startup, "available", lambda: False)
    pack_dir = _build_pack_dir(tmp_path / "src")
    zip_bytes = _build_zip_bytes()

    for call in (
        lambda: app_client.get("/api/data/asset-pack"),
        lambda: app_client.post("/api/data/asset-pack/inspect", json={"source_path": str(pack_dir)}),
        lambda: app_client.post("/api/data/asset-pack/inspect", content=zip_bytes, headers={"Content-Type": "application/zip"}),
        lambda: app_client.post("/api/data/asset-pack/install", json={"source_path": str(pack_dir)}),
        lambda: app_client.delete("/api/data/asset-pack"),
    ):
        resp = call()
        assert resp.status_code == 409
        assert resp.json()["detail"]["code"] == "data_unavailable"

    # When AMADEUS_ASSET_PACK_DIR is set, status still reports env override even if data_dir is corrupt.
    monkeypatch.setenv("AMADEUS_ASSET_PACK_DIR", str(pack_dir))
    status_resp = app_client.get("/api/data/asset-pack")
    assert status_resp.status_code == 200
    assert status_resp.json()["source"] == "env_override"


def test_inspect_and_install_from_wrapped_directory_and_uninstall(app_client, tmp_path):
    src_root = _build_pack_dir(
        tmp_path / "wrapped-src",
        wrapper="kurisu-pack",
        manifest={"format": 1, "name": "红莉栖典藏包", "version": "1.2.3"},
        extra_files={
            "assets/boot/boot_ring_1.png": _TINY_PNG,
            "audio/sfx/connected.ogg": _TINY_OGG,
            "README.md": b"# Pack readme\n",
            ".DS_Store": b"junk",
            "notes.txt": b"unsupported",
        },
    )

    inspect_resp = app_client.post("/api/data/asset-pack/inspect", json={"source_path": str(src_root)})
    assert inspect_resp.status_code == 200
    inspected = inspect_resp.json()
    assert inspected["valid"] is True
    assert inspected["source_type"] == "directory"
    assert inspected["stripped_root"] == "kurisu-pack"
    assert inspected["name"] == "红莉栖典藏包"
    assert inspected["version"] == "1.2.3"
    assert inspected["required_count"] == 34
    assert inspected["found_count"] == 34
    assert inspected["missing_files"] == []
    assert inspected["invalid_files"] == []
    assert inspected["errors"] == []
    assert any("notes.txt" in w for w in inspected["warnings"])
    assert inspected["total_bytes"] > 0
    assert len(inspected["previews"]) == 9
    emotions = [p["emotion"] for p in inspected["previews"]]
    assert emotions == [
        "neutral", "tsundere", "embarrassed", "intellectual", "happy",
        "surprised", "annoyed", "disappointed", "sad",
    ]
    first_preview = inspected["previews"][0]
    assert first_preview["path"] == "assets/images/kurisu_normal1.png"
    assert first_preview["data_url"].startswith("data:image/png;base64,")
    decoded = base64.b64decode(first_preview["data_url"].split(",", 1)[1])
    assert decoded == _sample_bytes("assets/images/kurisu_normal1.png")

    install_resp = app_client.post("/api/data/asset-pack/install", json={"source_path": str(src_root)})
    assert install_resp.status_code == 200
    installed = install_resp.json()
    assert installed["installed"] is True
    assert installed["complete"] is True
    assert installed["name"] == "红莉栖典藏包"
    assert installed["version"] == "1.2.3"
    assert installed["present_count"] == 34

    # Verify static GET /api/asset-pack/* serves manifest & files cleanly and skips unsupported .txt.
    manifest_resp = app_client.get("/api/asset-pack/manifest.json")
    assert manifest_resp.status_code == 200
    manifest_json = manifest_resp.json()
    assert manifest_json["format"] == 1
    assert manifest_json["name"] == "红莉栖典藏包"
    assert "assets/boot/boot_ring_1.png" in manifest_json["files"]
    assert "audio/sfx/connected.ogg" in manifest_json["files"]
    assert "notes.txt" not in manifest_json["files"]
    assert app_client.get("/api/asset-pack/assets/images/kurisu_normal1.png").status_code == 200
    assert app_client.get("/api/asset-pack/audio/bgm/Amadeus.ogg").status_code == 200
    assert app_client.get("/api/asset-pack/notes.txt").status_code == 404

    # Delete rejects query params and non-empty body.
    assert app_client.delete("/api/data/asset-pack?force=1").status_code == 422
    assert app_client.delete("/api/data/asset-pack?force=1").json()["detail"]["code"] == "invalid_delete_request"
    body_del = app_client.request("DELETE", "/api/data/asset-pack", content=b"{}")
    assert body_del.status_code == 422
    assert body_del.json()["detail"]["code"] == "invalid_delete_request"

    # Clean delete succeeds and returns uninstalled status.
    del_resp = app_client.delete("/api/data/asset-pack")
    assert del_resp.status_code == 200
    after_del = del_resp.json()
    assert after_del["installed"] is False
    assert after_del["complete"] is False
    assert after_del["present_count"] == 0
    assert app_client.get("/api/asset-pack/manifest.json").json() == {"installed": False}

    # Repeated delete is idempotent.
    assert app_client.delete("/api/data/asset-pack").json()["installed"] is False


@pytest.mark.parametrize("content_type", ["application/zip", "application/octet-stream"])
def test_inspect_and_install_from_zip_path_and_raw_binary_upload(app_client, tmp_path, content_type):
    zip_bytes = _build_zip_bytes(
        wrapper="amadeus-pack",
        extra_files={"__MACOSX/amadeus-pack/._kurisu.png": b"appledouble"},
    )
    zip_file = tmp_path / "pack.zip"
    zip_file.write_bytes(zip_bytes)

    # Local zip path via JSON
    inspect_local = app_client.post("/api/data/asset-pack/inspect", json={"source_path": str(zip_file)})
    assert inspect_local.status_code == 200
    assert inspect_local.json()["valid"] is True
    assert inspect_local.json()["source_type"] == "zip"
    assert inspect_local.json()["stripped_root"] == "amadeus-pack"

    # Raw binary upload inspect & install
    before_tmp = set(Path(tempfile.gettempdir()).glob("amadeus-pack-upload-*.zip"))
    inspect_upload = app_client.post(
        "/api/data/asset-pack/inspect",
        content=zip_bytes,
        headers={"Content-Type": content_type},
    )
    assert inspect_upload.status_code == 200
    assert inspect_upload.json()["valid"] is True
    assert inspect_upload.json()["stripped_root"] == "amadeus-pack"

    install_upload = app_client.post(
        "/api/data/asset-pack/install",
        content=zip_bytes,
        headers={"Content-Type": content_type},
    )
    assert install_upload.status_code == 200
    assert install_upload.json()["installed"] is True
    assert install_upload.json()["complete"] is True
    after_tmp = set(Path(tempfile.gettempdir()).glob("amadeus-pack-upload-*.zip"))
    assert after_tmp <= before_tmp


def test_incomplete_and_invalid_magic_pack_rejected_without_installing(app_client, tmp_path):
    partial_files = [f for f in installer.REQUIRED_FILES if f != "audio/bgm/Amadeus.ogg"]
    partial_dir = _build_pack_dir(tmp_path / "partial", files=partial_files)

    inspect_partial = app_client.post("/api/data/asset-pack/inspect", json={"source_path": str(partial_dir)})
    assert inspect_partial.status_code == 200
    body = inspect_partial.json()
    assert body["valid"] is False
    assert body["found_count"] == 33
    assert body["missing_files"] == ["audio/bgm/Amadeus.ogg"]
    assert len(body["previews"]) == 9

    install_partial = app_client.post("/api/data/asset-pack/install", json={"source_path": str(partial_dir)})
    assert install_partial.status_code == 422
    assert install_partial.json()["detail"]["code"] == "invalid_asset_pack"
    assert app_client.get("/api/data/asset-pack").json()["installed"] is False

    # Bad PNG & OGG headers
    bad_dir = _build_pack_dir(tmp_path / "bad-magic")
    (bad_dir / "assets/images/kurisu_normal1.png").write_bytes(b"GIF89a-not-png")
    (bad_dir / "audio/bgm/Amadeus.ogg").write_bytes(b"ID3-not-ogg")
    bad_dir_manifest = bad_dir / "manifest.json"
    bad_dir_manifest.write_text("{not-json", encoding="utf-8")

    inspect_bad = app_client.post("/api/data/asset-pack/inspect", json={"source_path": str(bad_dir)}).json()
    assert inspect_bad["valid"] is False
    invalid_paths = {item["path"] for item in inspect_bad["invalid_files"]}
    assert {"assets/images/kurisu_normal1.png", "audio/bgm/Amadeus.ogg", "manifest.json"} <= invalid_paths
    assert len(inspect_bad["previews"]) == 8  # neutral frame-1 excluded because invalid PNG

    install_bad = app_client.post("/api/data/asset-pack/install", json={"source_path": str(bad_dir)})
    assert install_bad.status_code == 422
    assert install_bad.json()["detail"]["code"] == "invalid_asset_pack"
    assert app_client.get("/api/data/asset-pack").json()["installed"] is False


@pytest.mark.parametrize("bad_entry", [
    "../evil.png",
    "..\\evil.png",
    "assets/../../evil.png",
    "assets/..\\..\\evil.png",
    "/etc/passwd",
    "\\Windows\\System32\\evil.png",
    "\\\\server\\share\\evil.png",
    "//server/share/evil.png",
    "C:\\evil.png",
    "C:evil.png",
])
def test_zip_slip_and_unsafe_paths_rejected(app_client, bad_entry):
    malicious_zip = _build_zip_bytes(raw_entries=[(bad_entry, _TINY_PNG)])
    for endpoint in ("/api/data/asset-pack/inspect", "/api/data/asset-pack/install"):
        resp = app_client.post(endpoint, content=malicious_zip, headers={"Content-Type": "application/zip"})
        assert resp.status_code == 422, (bad_entry, resp.text)
        assert resp.json()["detail"]["code"] == "unsafe_archive"
    assert app_client.get("/api/data/asset-pack").json()["installed"] is False


def test_zip_symlink_reparse_case_collision_and_encrypted_rejected(app_client):
    # POSIX symlink inside zip
    sym_info = zipfile.ZipInfo("assets/images/link.png")
    sym_info.external_attr = (stat.S_IFLNK | 0o777) << 16
    sym_zip = _build_zip_bytes(raw_entries=[(sym_info, b"/etc/passwd")])
    resp = app_client.post("/api/data/asset-pack/inspect", content=sym_zip, headers={"Content-Type": "application/zip"})
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "unsafe_archive"

    # Windows reparse point inside zip
    win_info = zipfile.ZipInfo("assets/images/junction.png")
    win_info.create_system = 10
    win_info.external_attr = 0x400
    win_zip = _build_zip_bytes(raw_entries=[(win_info, b"junction")])
    resp = app_client.post("/api/data/asset-pack/inspect", content=win_zip, headers={"Content-Type": "application/zip"})
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "unsafe_archive"

    # Case-colliding entry
    dup_zip = _build_zip_bytes(raw_entries=[("assets/images/Kurisu_Normal1.png", _TINY_PNG)])
    resp = app_client.post("/api/data/asset-pack/install", content=dup_zip, headers={"Content-Type": "application/zip"})
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "unsafe_archive"

    # Encrypted flag (set bit 0 in local & central directory headers)
    enc_buf = BytesIO()
    with zipfile.ZipFile(enc_buf, "w") as zf:
        zf.writestr("assets/images/secret.png", _TINY_PNG)
    enc_bytes = bytearray(enc_buf.getvalue())
    enc_bytes[6] |= 0x01
    cd_offset = enc_bytes.index(b"PK\x01\x02")
    enc_bytes[cd_offset + 8] |= 0x01
    resp = app_client.post(
        "/api/data/asset-pack/inspect",
        content=bytes(enc_bytes),
        headers={"Content-Type": "application/zip"},
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "unsafe_archive"


def test_os_symlinks_and_ntfs_reparse_points_rejected(app_client, tmp_path, monkeypatch):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.png").write_bytes(_TINY_PNG)

    pack_dir = _build_pack_dir(tmp_path / "dir-with-link")
    link = pack_dir / "assets" / "images" / "escape"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        if not (os.name == "nt" and getattr(exc, "winerror", None) == 1314):
            raise
    else:
        resp = app_client.post("/api/data/asset-pack/inspect", json={"source_path": str(pack_dir)})
        assert resp.status_code == 422
        assert resp.json()["detail"]["code"] == "unsafe_source_path"

    if os.name == "nt":
        junc_pack = _build_pack_dir(tmp_path / "dir-with-real-junction")
        junc_dir = junc_pack / "assets" / "images" / "escape-junc"
        subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(junc_dir), str(outside)],
            check=True,
            capture_output=True,
            text=True,
        )
        try:
            resp = app_client.post("/api/data/asset-pack/inspect", json={"source_path": str(junc_pack)})
            assert resp.status_code == 422
            assert resp.json()["detail"]["code"] == "unsafe_source_path"
        finally:
            if installer._path_exists_or_link(junc_dir):
                os.rmdir(junc_dir)

    # Simulate Windows FILE_ATTRIBUTE_REPARSE_POINT (0x400) on a directory entry.
    clean_dir = _build_pack_dir(tmp_path / "dir-with-junction")
    orig_scandir = os.scandir

    class _ReparseEntry:
        def __init__(self, inner):
            self._inner = inner
            self.name = inner.name
            self.path = inner.path

        def is_symlink(self):
            return False

        def stat(self, *, follow_symlinks=True):
            real = self._inner.stat(follow_symlinks=follow_symlinks)
            return SimpleNamespace(
                st_mode=real.st_mode,
                st_size=real.st_size,
                st_file_attributes=0x400 if self.name == "images" else 0,
            )

    class _ScanWrapper:
        def __init__(self, target):
            self._ctx = orig_scandir(target)

        def __enter__(self):
            return [_ReparseEntry(e) for e in self._ctx.__enter__()]

        def __exit__(self, *args):
            return self._ctx.__exit__(*args)

    monkeypatch.setattr(os, "scandir", _ScanWrapper)
    resp = app_client.post("/api/data/asset-pack/inspect", json={"source_path": str(clean_dir)})
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "unsafe_source_path"


def test_zip_bomb_and_payload_limits(app_client, monkeypatch):
    # Upload byte limit via Content-Length and streaming -> 413
    zip_bytes = _build_zip_bytes()
    monkeypatch.setattr(installer, "MAX_UPLOAD_BYTES", len(zip_bytes) - 10)
    resp = app_client.post("/api/data/asset-pack/inspect", content=zip_bytes, headers={"Content-Type": "application/zip"})
    assert resp.status_code == 413
    assert resp.json()["detail"]["code"] == "payload_too_large"
    resp = app_client.post(
        "/api/data/asset-pack/install",
        content=b"PK\x03\x04",
        headers={"Content-Type": "application/zip", "Content-Length": str(len(zip_bytes))},
    )
    assert resp.status_code == 413
    assert resp.json()["detail"]["code"] == "payload_too_large"
    monkeypatch.setattr(installer, "MAX_UPLOAD_BYTES", 512 * 1024 * 1024)

    # Entry count limit -> 422
    monkeypatch.setattr(installer, "MAX_ZIP_ENTRIES", 10)
    resp = app_client.post("/api/data/asset-pack/inspect", content=zip_bytes, headers={"Content-Type": "application/zip"})
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "unsafe_archive"
    monkeypatch.setattr(installer, "MAX_ZIP_ENTRIES", 500)

    # Compression ratio bomb (200 KiB of repeated zeros -> high ratio)
    bomb_zip = _build_zip_bytes(extra_files={"assets/ui/bomb.png": _TINY_PNG + b"\x00" * (200 * 1024)})
    resp = app_client.post("/api/data/asset-pack/inspect", content=bomb_zip, headers={"Content-Type": "application/zip"})
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "unsafe_archive"

    # Single entry exceeds MAX_ENTRY_UNCOMPRESSED_BYTES -> 422 unsafe_archive
    monkeypatch.setattr(installer, "MAX_ENTRY_UNCOMPRESSED_BYTES", 1024)
    big_entry_zip = _build_zip_bytes(extra_files={"assets/ui/large.png": _TINY_PNG + b"A" * 2048})
    resp = app_client.post(
        "/api/data/asset-pack/inspect",
        content=big_entry_zip,
        headers={"Content-Type": "application/zip"},
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "unsafe_archive"

    # Forged central-directory uncompressed size (header lies 10 bytes) -> rejected 422
    import struct
    forged_buf = BytesIO()
    with zipfile.ZipFile(forged_buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("assets/images/kurisu_normal1.png", _TINY_PNG + b"A" * 4096)
    forged_bytes = bytearray(forged_buf.getvalue())
    cd_idx = forged_bytes.index(b"PK\x01\x02")
    struct.pack_into("<I", forged_bytes, 22, 10)
    struct.pack_into("<I", forged_bytes, cd_idx + 24, 10)
    resp = app_client.post(
        "/api/data/asset-pack/inspect",
        content=bytes(forged_bytes),
        headers={"Content-Type": "application/zip"},
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] in {"invalid_archive", "unsafe_archive"}
    monkeypatch.setattr(installer, "MAX_ENTRY_UNCOMPRESSED_BYTES", 64 * 1024 * 1024)

    # Total uncompressed size limit -> 422
    monkeypatch.setattr(installer, "MAX_TOTAL_UNCOMPRESSED_BYTES", 500)
    resp = app_client.post("/api/data/asset-pack/inspect", content=zip_bytes, headers={"Content-Type": "application/zip"})
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "unsafe_archive"


def test_install_lock_busy_and_atomic_swap_rollback(app_client, tmp_path, monkeypatch):
    pack_v1 = _build_pack_dir(tmp_path / "pack-v1", manifest={"format": 1, "name": "pack", "version": "1.0"})
    pack_v2 = _build_pack_dir(tmp_path / "pack-v2", manifest={"format": 1, "name": "pack", "version": "2.0"})

    assert app_client.post("/api/data/asset-pack/install", json={"source_path": str(pack_v1)}).status_code == 200
    assert app_client.get("/api/data/asset-pack").json()["version"] == "1.0"

    # Busy lock returns 409 asset_pack_busy
    assert installer._install_lock.acquire(blocking=False)
    try:
        busy_install = app_client.post("/api/data/asset-pack/install", json={"source_path": str(pack_v2)})
        assert busy_install.status_code == 409
        assert busy_install.json()["detail"]["code"] == "asset_pack_busy"
        busy_delete = app_client.delete("/api/data/asset-pack")
        assert busy_delete.status_code == 409
        assert busy_delete.json()["detail"]["code"] == "asset_pack_busy"
    finally:
        installer._install_lock.release()

    # Simulate failure on second os.replace (staging -> target) and verify rollback to v1.0.
    real_replace = os.replace
    calls = 0

    def fail_second_replace(src, dst):
        nonlocal calls
        if Path(src).name == "amadeus-original" or Path(dst).name == "amadeus-original":
            calls += 1
            if calls == 2:
                raise OSError("injected swap failure")
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", fail_second_replace)
    failed = app_client.post("/api/data/asset-pack/install", json={"source_path": str(pack_v2)})
    assert failed.status_code == 500
    assert failed.json()["detail"]["code"] == "asset_pack_install_failed"

    monkeypatch.setattr(os, "replace", real_replace)
    status = app_client.get("/api/data/asset-pack").json()
    assert status["installed"] is True
    assert status["complete"] is True
    assert status["version"] == "1.0"
    pack_parent = Path(os.environ["AMADEUS_DATA_DIR"]) / "asset-pack"
    assert [p.name for p in pack_parent.iterdir()] == ["amadeus-original"]

    # Also verify failure on first swap (target -> backup) cleans staging and keeps v1.0.
    def fail_first_replace(src, dst):
        if Path(src).name == "amadeus-original":
            raise OSError("injected first swap failure")
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", fail_first_replace)
    failed_first = app_client.post("/api/data/asset-pack/install", json={"source_path": str(pack_v2)})
    assert failed_first.status_code == 500
    assert failed_first.json()["detail"]["code"] == "asset_pack_install_failed"
    monkeypatch.setattr(os, "replace", real_replace)
    assert app_client.get("/api/data/asset-pack").json()["version"] == "1.0"
    assert [p.name for p in pack_parent.iterdir()] == ["amadeus-original"]


def test_request_validation_errors(app_client, tmp_path):
    assert app_client.post("/api/data/asset-pack/inspect", json={"source_path": "relative/path"}).status_code == 422
    assert app_client.post("/api/data/asset-pack/inspect", json={"source_path": str(tmp_path / "missing")}).status_code == 422
    assert app_client.post("/api/data/asset-pack/inspect", json={"source_path": str(tmp_path), "extra": 1}).status_code == 422
    assert app_client.post("/api/data/asset-pack/inspect", content=b"", headers={"Content-Type": "application/zip"}).status_code == 422
    assert app_client.post("/api/data/asset-pack/inspect", content=b"not-a-zip", headers={"Content-Type": "application/zip"}).status_code == 422
    assert app_client.post(
        "/api/data/asset-pack/inspect",
        content=b"--boundary\r\n",
        headers={"Content-Type": "multipart/form-data; boundary=boundary"},
    ).status_code == 422


def test_local_bearer_token_and_origin_guard_enforced(app_client, tmp_path, monkeypatch):
    monkeypatch.setattr(local_transport, "LOCAL_API_TOKEN", b"synthetic-pack-token")
    pack_dir = _build_pack_dir(tmp_path / "auth-pack")

    # Missing or bad Bearer token -> 401
    for method, path, kwargs in (
        ("GET", "/api/data/asset-pack", {}),
        ("POST", "/api/data/asset-pack/inspect", {"json": {"source_path": str(pack_dir)}}),
        ("POST", "/api/data/asset-pack/install", {"json": {"source_path": str(pack_dir)}}),
        ("DELETE", "/api/data/asset-pack", {}),
    ):
        assert app_client.request(method, path, **kwargs).status_code == 401
        bad = app_client.request(method, path, headers={"Authorization": "Bearer wrong"}, **kwargs)
        assert bad.status_code == 401

    # Untrusted Origin with valid token -> 403
    bad_origin_headers = {
        "Authorization": "Bearer synthetic-pack-token",
        "Origin": "https://evil.example",
    }
    assert app_client.post(
        "/api/data/asset-pack/inspect",
        json={"source_path": str(pack_dir)},
        headers=bad_origin_headers,
    ).status_code == 403
    assert app_client.post(
        "/api/data/asset-pack/install",
        json={"source_path": str(pack_dir)},
        headers=bad_origin_headers,
    ).status_code == 403
    assert app_client.delete("/api/data/asset-pack", headers=bad_origin_headers).status_code == 403

    # Valid token and trusted origin -> 200
    good_headers = {
        "Authorization": "Bearer synthetic-pack-token",
        "Origin": "http://localhost:1420",
    }
    assert app_client.get("/api/data/asset-pack", headers=good_headers).status_code == 200
    assert app_client.post(
        "/api/data/asset-pack/inspect",
        json={"source_path": str(pack_dir)},
        headers=good_headers,
    ).json()["valid"] is True
    assert app_client.post(
        "/api/data/asset-pack/install",
        json={"source_path": str(pack_dir)},
        headers=good_headers,
    ).json()["installed"] is True
    assert app_client.delete("/api/data/asset-pack", headers=good_headers).json()["installed"] is False


def test_windows_trailing_dot_space_and_reserved_names_rejected(app_client, tmp_path, monkeypatch):
    pack_v1 = _build_pack_dir(tmp_path / "pack-v1", manifest={"format": 1, "name": "pack", "version": "1.0"})
    assert app_client.post("/api/data/asset-pack/install", json={"source_path": str(pack_v1)}).status_code == 200
    installed_sprite = (
        Path(os.environ["AMADEUS_DATA_DIR"]) / "asset-pack" / "amadeus-original" / "assets" / "images" / "kurisu_normal1.png"
    )
    original_bytes = installed_sprite.read_bytes()

    # Complete pack with trailing-dot directory alias -> 422 unsafe_archive and original pack intact.
    dot_zip = _build_zip_bytes(extra_files={"assets/images./kurisu_normal1.png": _TINY_PNG + b"overwritten"})
    for endpoint in ("/api/data/asset-pack/inspect", "/api/data/asset-pack/install"):
        resp = app_client.post(endpoint, content=dot_zip, headers={"Content-Type": "application/zip"})
        assert resp.status_code == 422
        assert resp.json()["detail"]["code"] == "unsafe_archive"

    status = app_client.get("/api/data/asset-pack").json()
    assert status["installed"] is True
    assert status["version"] == "1.0"
    assert installed_sprite.read_bytes() == original_bytes

    # Trailing space segment and Windows reserved device names in ZIP -> 422 unsafe_archive.
    for bad_rel in (
        "assets/images /kurisu_normal1.png",
        "assets/images/CON.png",
        "audio/bgm/NUL.ogg",
        "LPT1/extra.png",
    ):
        bad_zip = _build_zip_bytes(extra_files={bad_rel: _TINY_PNG + b"bad"})
        resp = app_client.post("/api/data/asset-pack/inspect", content=bad_zip, headers={"Content-Type": "application/zip"})
        assert resp.status_code == 422
        assert resp.json()["detail"]["code"] == "unsafe_archive"

    # Directory inspection rejects Windows reserved names (real file on POSIX, scandir simulation everywhere).
    if os.name != "nt":
        bad_dir = _build_pack_dir(tmp_path / "dir-reserved")
        (bad_dir / "assets" / "images" / "CON.png").write_bytes(_TINY_PNG)
        resp = app_client.post("/api/data/asset-pack/inspect", json={"source_path": str(bad_dir)})
        assert resp.status_code == 422
        assert resp.json()["detail"]["code"] == "unsafe_source_path"

    clean_dir = _build_pack_dir(tmp_path / "dir-reserved-sim")
    orig_scandir = os.scandir

    class _FakeReservedEntry:
        def __init__(self, parent: Path, name: str):
            self.name = name
            self.path = str(parent / name)

        def is_symlink(self):
            return False

        def stat(self, *, follow_symlinks=True):
            return SimpleNamespace(
                st_mode=stat.S_IFREG | 0o644,
                st_size=len(_TINY_PNG),
                st_file_attributes=0,
            )

    class _ScanWithReserved:
        def __init__(self, target):
            self._target = Path(target)
            self._ctx = orig_scandir(target)

        def __enter__(self):
            entries = list(self._ctx.__enter__())
            if self._target.name == "images":
                entries.append(_FakeReservedEntry(self._target, "CON.png"))
            return entries

        def __exit__(self, *args):
            return self._ctx.__exit__(*args)

    monkeypatch.setattr(os, "scandir", _ScanWithReserved)
    try:
        resp = app_client.post("/api/data/asset-pack/inspect", json={"source_path": str(clean_dir)})
        assert resp.status_code == 422
        assert resp.json()["detail"]["code"] == "unsafe_source_path"
    finally:
        monkeypatch.setattr(os, "scandir", orig_scandir)

    # Exclusive staging ('xb') still blocks duplicate target writes if collision precheck were bypassed.
    orig_collision_check = installer._check_path_collisions
    monkeypatch.setattr(installer, "_check_path_collisions", lambda _paths: None)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            dup_zip = _build_zip_bytes(raw_entries=[("assets/images/kurisu_normal1.png", _TINY_PNG + b"dup")])
        dup_resp = app_client.post("/api/data/asset-pack/install", content=dup_zip, headers={"Content-Type": "application/zip"})
        assert dup_resp.status_code == 422
        assert dup_resp.json()["detail"]["code"] == "unsafe_archive"
    finally:
        monkeypatch.setattr(installer, "_check_path_collisions", orig_collision_check)


def test_uninstall_readonly_files_and_sweeps_stale_temp_dirs(app_client, tmp_path):
    pack_dir = _build_pack_dir(tmp_path / "readonly-pack")
    assert app_client.post("/api/data/asset-pack/install", json={"source_path": str(pack_dir)}).status_code == 200

    pack_parent = Path(os.environ["AMADEUS_DATA_DIR"]) / "asset-pack"
    installed_root = pack_parent / "amadeus-original"
    ro_sprite = installed_root / "assets" / "images" / "kurisu_normal1.png"
    ro_bgm = installed_root / "audio" / "bgm" / "Amadeus.ogg"
    os.chmod(ro_sprite, 0o444)
    os.chmod(ro_bgm, 0o444)

    stale_del = pack_parent / ".del-asset-pack-stale"
    stale_nested = stale_del / "nested"
    stale_nested.mkdir(parents=True)
    stale_file = stale_nested / "locked.png"
    stale_file.write_bytes(_TINY_PNG)
    os.chmod(stale_file, 0o444)
    if os.name != "nt":
        # On POSIX, a 0o555 subdir triggers PermissionError during rmtree -> exercises chmod + retry.
        os.chmod(stale_nested, 0o555)

    stale_tmp = pack_parent / ".tmp-asset-pack-stale"
    stale_tmp.mkdir(parents=True)
    (stale_tmp / "leftover.txt").write_text("stale", encoding="utf-8")

    try:
        resp = app_client.delete("/api/data/asset-pack")
        assert resp.status_code == 200
        status = resp.json()
        assert status["installed"] is False
        assert status["complete"] is False
        assert not stale_del.exists()
        assert not stale_tmp.exists()
        assert not pack_parent.exists()
    finally:
        for path in (stale_nested, stale_file, ro_sprite, ro_bgm):
            if path.exists():
                try:
                    os.chmod(path, 0o700 if path.is_dir() else 0o600)
                except OSError:
                    pass


def test_uninstall_failure_rollback_and_partial_cleanup(app_client, tmp_path, monkeypatch):
    pack_dir = _build_pack_dir(tmp_path / "rollback-pack", manifest={"format": 1, "name": "pack", "version": "1.0"})
    assert app_client.post("/api/data/asset-pack/install", json={"source_path": str(pack_dir)}).status_code == 200
    pack_parent = Path(os.environ["AMADEUS_DATA_DIR"]) / "asset-pack"

    # Case 1: os.replace(target_dir, trash_dir) fails -> active pack untouched, 500.
    real_replace = os.replace

    def fail_trash_replace(src, dst):
        if Path(dst).name.startswith(".del-asset-pack-"):
            raise PermissionError("injected move lock")
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", fail_trash_replace)
    try:
        resp = app_client.delete("/api/data/asset-pack")
        assert resp.status_code == 500
        assert resp.json()["detail"]["code"] == "asset_pack_remove_failed"
    finally:
        monkeypatch.setattr(os, "replace", real_replace)
    assert app_client.get("/api/data/asset-pack").json()["installed"] is True
    assert [p.name for p in pack_parent.iterdir()] == ["amadeus-original"]

    # Case 2: rmtree fails before deleting anything -> rolled back to active pack, 500.
    real_rmtree = installer._rmtree_managed

    def fail_rmtree_intact(_path: Path):
        raise PermissionError("injected intact lock")

    monkeypatch.setattr(installer, "_rmtree_managed", fail_rmtree_intact)
    try:
        resp = app_client.delete("/api/data/asset-pack")
        assert resp.status_code == 500
        assert resp.json()["detail"]["code"] == "asset_pack_remove_failed"
    finally:
        monkeypatch.setattr(installer, "_rmtree_managed", real_rmtree)
    status = app_client.get("/api/data/asset-pack").json()
    assert status["installed"] is True
    assert status["complete"] is True
    assert status["version"] == "1.0"
    assert [p.name for p in pack_parent.iterdir()] == ["amadeus-original"]

    # Case 3: rmtree deletes one file then fails -> keeps .del-* for later sweep, installed is False.
    def fail_rmtree_partial(path: Path):
        (path / "manifest.json").unlink()
        raise PermissionError("injected mid-delete lock")

    monkeypatch.setattr(installer, "_rmtree_managed", fail_rmtree_partial)
    try:
        resp = app_client.delete("/api/data/asset-pack")
        assert resp.status_code == 500
        assert resp.json()["detail"]["code"] == "asset_pack_remove_failed"
    finally:
        monkeypatch.setattr(installer, "_rmtree_managed", real_rmtree)
    assert app_client.get("/api/data/asset-pack").json()["installed"] is False
    leftovers = [p.name for p in pack_parent.iterdir()]
    assert len(leftovers) == 1 and leftovers[0].startswith(".del-asset-pack-")

    # Subsequent DELETE sweeps the leftover .del-* cleanly.
    retry = app_client.delete("/api/data/asset-pack")
    assert retry.status_code == 200
    assert retry.json()["installed"] is False
    assert not pack_parent.exists()
