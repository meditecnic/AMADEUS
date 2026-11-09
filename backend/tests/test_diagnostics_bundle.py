from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
from zipfile import ZipFile

import pytest
from fastapi.testclient import TestClient

from app import db
from app.main import app
from app.security import local_transport
from app.services import data_location, diagnostics_bundle as bundle
from app.services.provider_runtime import provider_registry
from test_backups import data_root  # noqa: F401


def read_bundle(response):
    assert response.status_code == 200, response.text
    payload = response.json()
    path = Path(payload["path"])
    assert payload["size"] == path.stat().st_size
    with ZipFile(path) as archive:
        return {name: archive.read(name).decode("utf-8") for name in archive.namelist()}


def test_bundle_allowlist_and_redaction(app_client, monkeypatch, isolated_provider_credentials):
    root = db.get_data_root()
    logs = root / "logs"
    logs.mkdir()
    key = "private-provider-value-without-prefix"
    token = "private-local-token-without-prefix"
    unconfigured_key = "sk-unconfigured-private-value-216"
    other_bearer = "private-unknown-bearer"
    password = "private-password-216"
    user = r"C:\Users\Jane Doe\AppData\Local\data"
    old_keys = "tvly-old-private-value-216 fc-old-private-value-216"
    content = f"event ready\n{old_keys}\n{key}\n{token}\n{unconfigured_key}\nBearer {other_bearer}\npassword={password}\n{user}\n"
    isolated_provider_credentials.set("deepseek", key)
    monkeypatch.setattr(local_transport, "LOCAL_API_TOKEN", token.encode("utf-8"))
    monkeypatch.setenv("AMADEUS_APP_VERSION", "0.1.0-test")
    snapshot = provider_registry.snapshot("deepseek", allow_unavailable=True)
    monkeypatch.setattr(snapshot.adapter, "base_url", "https://user:private-url-password@example.invalid/private/path?key=private-query")
    for name in bundle.LOG_NAMES:
        for suffix in ("", ".1", ".2", ".3"):
            (logs / (name + suffix)).write_text(content, encoding="utf-8")
    for relative in ("logs/unrelated.log", "exports/private.json", "auth/passcode.json",
                     "voice-pack/ref/private.wav", "asset-pack/amadeus-original/manifest.json"):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("private-file-body-216", encoding="utf-8")
    files = read_bundle(app_client.post("/api/diagnostics/bundle", headers={"authorization": f"Bearer {token}"}))
    expected = {"summary.json"} | {f"logs/{name}{suffix}" for name in bundle.LOG_NAMES for suffix in ("", ".1", ".2")}
    assert files.keys() == expected
    for text in files.values():
        for private in (key, token, unconfigured_key, other_bearer, password, user, "Jane Doe",
                        *old_keys.split(),
                        "private-url-password", "private-query", "/private/path", "private-file-body-216"):
            assert private not in text
    assert "%USERPROFILE%" in files["logs/backend-stdout.log"]
    assert "event ready" in files["logs/backend-stdout.log"]
    summary = json.loads(files["summary.json"])
    assert summary["app_version"] == "0.1.0-test"
    assert summary["data_status"] == app_client.get("/api/data/status", headers={"authorization": f"Bearer {token}"}).json()
    assert summary["schema_versions"]["control.sqlite3"] == db.SCHEMA_VERSION
    assert summary["disk_usage"]["total_bytes"] > 0
    assert summary["asset_pack_installed"] is True
    assert summary["voice_enabled"] is True
    assert summary["web_search_enabled"] is False
    assert next(p for p in summary["providers"] if p["kind"] == "deepseek")["host"] == "example.invalid"


def test_runtime_logs_outside_moved_data_and_read_limit(app_client, monkeypatch, tmp_path):
    runtime = tmp_path / "runtime"
    logs = runtime / "logs"
    logs.mkdir(parents=True)
    monkeypatch.setenv("AMADEUS_LOCATION_FILE", str(runtime / "data-location.json"))
    # No current startup file: include all three historical runs instead.
    for suffix in (".1", ".2", ".3"):
        (logs / ("backend-startup.log" + suffix)).write_text("historical", encoding="utf-8")
    (logs / "backend-stdout.log").write_bytes(b"x" * bundle.MAX_LOG_BYTES + b"outside-read-limit")
    files = read_bundle(app_client.post("/api/diagnostics/bundle"))
    assert {"logs/backend-startup.log" + s for s in (".1", ".2", ".3")} <= files.keys()
    assert len(files["logs/backend-stdout.log"]) == bundle.MAX_LOG_BYTES
    assert "outside-read-limit" not in "".join(files.values())


def test_corrupt_startup_allows_only_authenticated_bundle(data_root, monkeypatch):
    (data_root / "control.sqlite3").write_bytes(b"not a SQLite database")
    monkeypatch.setattr(local_transport, "LOCAL_API_TOKEN", b"private-216-local-token")
    headers = {"authorization": "Bearer private-216-local-token", "origin": "http://localhost:1420"}
    with TestClient(app, base_url="http://localhost") as client:
        assert client.get("/api/settings", headers=headers).status_code == 503
        assert client.post("/api/diagnostics/bundle").status_code == 401
        assert client.post("/api/diagnostics/bundle", headers={**headers, "origin": "https://invalid.example"}).status_code == 403
        files = read_bundle(client.post("/api/diagnostics/bundle", headers=headers))
        summary = json.loads(files["summary.json"])
        assert summary["data_status"]["status"] == "corrupt"
        assert summary["schema_versions"]["control.sqlite3"] == "unreadable"
        assert files.keys() == {"summary.json"}


def test_keep_five_packages_and_preserve_unrelated_files(app_client):
    directory = db.get_data_root() / "diagnostics"
    directory.mkdir()
    old = []
    for day in range(1, 7):
        path = directory / f"amadeus-diagnostics-200001{day:02d}T000000000000Z.zip"
        path.write_bytes(b"old archive")
        old.append(path)
    unrelated = directory / "unrelated.zip"
    unrelated.write_bytes(b"keep")
    read_bundle(app_client.post("/api/diagnostics/bundle"))
    assert not old[0].exists() and not old[1].exists()
    assert all(p.exists() for p in old[2:])
    assert len(list(directory.glob("amadeus-diagnostics-*.zip"))) == 5
    assert unrelated.read_bytes() == b"keep"


def test_metadata_failure_and_write_failure(app_client, monkeypatch):
    def fail():
        raise OSError("private-error-body")
    monkeypatch.setattr(data_location, "disk_usage", fail)
    summary = json.loads(read_bundle(app_client.post("/api/diagnostics/bundle"))["summary.json"])
    assert summary["disk_usage"] is None and summary["errors"]["disk_usage"] == "unavailable"
    def fail_write(*args, **kwargs):
        raise OSError("private-error-body")
    monkeypatch.setattr(bundle, "ZipFile", fail_write)
    response = app_client.post("/api/diagnostics/bundle")
    assert response.status_code == 500
    assert response.json() == {"detail": {"code": "diagnostics_bundle_failed"}}
    assert not list((db.get_data_root() / "diagnostics").glob("*.tmp"))


def test_concurrent_packages_are_complete_and_retained(app_client):
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(lambda _: bundle.create_bundle({}), range(7)))
    assert len({r["path"] for r in results}) == 7
    remaining = list((db.get_data_root() / "diagnostics").glob("*.zip"))
    assert len(remaining) == 5
    for path in remaining:
        with ZipFile(path) as archive:
            assert archive.namelist() == ["summary.json"]
            assert archive.testzip() is None


@pytest.mark.parametrize("directory_link", [False, True])
def test_linked_logs_are_excluded(app_client, tmp_path, directory_link):
    logs = db.get_data_root() / "logs"
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "backend-stdout.log").write_text("private-linked-content", encoding="utf-8")
    try:
        if directory_link:
            logs.symlink_to(outside, target_is_directory=True)
        else:
            logs.mkdir()
            (logs / "backend-stdout.log").symlink_to(outside / "backend-stdout.log")
    except OSError:
        pytest.skip("symlinks unavailable")
    assert read_bundle(app_client.post("/api/diagnostics/bundle")).keys() == {"summary.json"}
