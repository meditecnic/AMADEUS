"""素材包普通 GET、媒体 Range 与文件边界。"""

import os
import subprocess
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def pack_dir(tmp_path, monkeypatch):
    root = tmp_path / "pack"
    root.mkdir()
    monkeypatch.setenv("AMADEUS_ASSET_PACK_DIR", str(root))
    monkeypatch.setenv("AMADEUS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("AMADEUS_DB_PATH", raising=False)
    return root


@pytest.mark.parametrize("filename, content, media_type", [
    ("images/portrait.png", b"png-content", "image/png"),
    ("audio/theme.ogg", b"OggS0123456789", "audio/ogg"),
    ("manifest.json", b'{"format":1}', "application/json"),
    ("README.md", b"# Asset pack", "text/markdown"),
])
def test_plain_get_returns_file_without_credentials(app_client, pack_dir, filename, content, media_type):
    target = pack_dir / filename
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
    response = app_client.get(f"/api/asset-pack/{filename}")
    assert response.status_code == 200
    assert response.content == content
    assert response.headers["content-type"].split(";")[0] == media_type


def test_default_pack_directory(app_client, pack_dir, monkeypatch):
    monkeypatch.delenv("AMADEUS_ASSET_PACK_DIR")
    root = Path(os.environ["AMADEUS_DATA_DIR"]) / "asset-pack" / "amadeus-original"
    root.mkdir(parents=True)
    (root / "manifest.json").write_bytes(b'{"format":1}')
    assert app_client.get("/api/asset-pack/manifest.json").json() == {"format": 1}


def test_missing_root_file_and_directory_are_404(app_client, pack_dir, monkeypatch):
    assert app_client.get("/api/asset-pack/missing.png").status_code == 404
    (pack_dir / "images").mkdir()
    assert app_client.get("/api/asset-pack/images").status_code == 404
    monkeypatch.setenv("AMADEUS_ASSET_PACK_DIR", str(pack_dir / "missing-root"))
    assert app_client.get("/api/asset-pack/manifest.json").status_code == 404


@pytest.mark.parametrize("path", [
    "%2e%2e/secret.txt", "%2e%2e%5csecret.txt", "nested/%2e%2e/%2e%2e/secret.txt",
    "%2fsecret.txt", "C:%5csecret.txt", "C:secret.txt", "%5c%5cserver%5csecret.txt", "%00",
])
def test_traversal_and_absolute_paths_are_404(app_client, pack_dir, path):
    (pack_dir.parent / "secret.txt").write_text("outside", encoding="utf-8")
    assert app_client.get(f"/api/asset-pack/{path}").status_code == 404


def test_symlink_escape_is_404(app_client, pack_dir):
    outside = pack_dir.parent / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("outside", encoding="utf-8")
    link = pack_dir / "escape"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        if os.name != "nt":
            raise
        result = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(outside)], capture_output=True)
        assert result.returncode == 0, result.stderr.decode(errors="replace")
    assert app_client.get("/api/asset-pack/escape/secret.txt").status_code == 404


def test_ogg_range_returns_selected_bytes(app_client, pack_dir):
    content = b"OggS0123456789"
    (pack_dir / "theme.ogg").write_bytes(content)
    response = app_client.get("/api/asset-pack/theme.ogg", headers={"Range": "bytes=4-7"})
    assert response.status_code == 206
    assert response.content == b"0123"
    assert response.headers["content-range"] == f"bytes 4-7/{len(content)}"
    assert response.headers["accept-ranges"] == "bytes"
    assert response.headers["content-type"] == "audio/ogg"
    assert app_client.get("/api/asset-pack/theme.ogg", headers={"Range": "bytes=100-"}).status_code == 416
