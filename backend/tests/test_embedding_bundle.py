"""Pinned embedding whitelist, source integrity, and copied-byte verification."""
import hashlib
from email.message import Message
from pathlib import Path
from types import SimpleNamespace
import zipfile

import pytest

from app.services.memory import EMBEDDING_REVISION


@pytest.fixture
def builder(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "packaging/windows"))
    import build_backend
    return build_backend


def test_whitelist_hashes_and_runtime_revision(builder):
    assert builder.EMBEDDING_REVISION == EMBEDDING_REVISION
    assert set(builder.EMBEDDING_FILES) == {
        "onnx/model.onnx", "onnx/model.onnx_data", "tokenizer.json", "README.md", "LICENSE",
    }


def test_unverified_hashes_block_packaging(builder, tmp_path, monkeypatch):
    monkeypatch.setattr(builder, "EMBEDDING_FILES", {"onnx/model.onnx": None})
    with pytest.raises(RuntimeError, match="SHA-256 not yet verified"):
        builder.stage_embedding(tmp_path / "source", tmp_path / "cache")



@pytest.fixture
def stage(builder, monkeypatch, tmp_path):
    source, cache = tmp_path / "source", tmp_path / "cache"
    expected = {}
    for name in builder.EMBEDDING_FILES:
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(("synthetic " + name).encode("utf-8"))
        expected[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    (source / "model.safetensors").write_bytes(b"must not copy")
    monkeypatch.setattr(builder, "EMBEDDING_FILES", expected)
    return source, cache


def test_stage_only_whitelisted_files_and_preserve_source(builder, stage):
    source, cache = stage
    before = {p.relative_to(source): p.read_bytes() for p in source.rglob("*") if p.is_file()}
    rows = builder.stage_embedding(source, cache)
    snapshot = cache / "models--jinaai--jina-embeddings-v5-text-nano-retrieval/snapshots" / EMBEDDING_REVISION
    assert {p.relative_to(snapshot).as_posix() for p in snapshot.rglob("*") if p.is_file()} == set(builder.EMBEDDING_FILES)
    assert {row["path"]: row["sha256"] for row in rows} == builder.EMBEDDING_FILES
    assert before == {p.relative_to(source): p.read_bytes() for p in source.rglob("*") if p.is_file()}
    assert (snapshot.parents[1] / "refs/main").read_text(encoding="utf-8") == EMBEDDING_REVISION
    assert builder.stage_embedding(source, cache) == rows


@pytest.mark.parametrize("failure", ["missing", "changed", "bad_copy"])
def test_stage_rejects_missing_or_corrupt_bytes(builder, stage, monkeypatch, failure):
    source, cache = stage
    path = source / "onnx/model.onnx"
    if failure == "missing":
        path.unlink()
        message = "missing embedding file"
    elif failure == "changed":
        path.write_bytes(b"changed")
        message = "source hash mismatch"
    else:
        monkeypatch.setattr(builder.shutil, "copyfile", lambda origin, target: target.write_bytes(b"corrupt"))
        message = "copy mismatch"
    with pytest.raises((RuntimeError, FileNotFoundError), match=message):
        builder.stage_embedding(source, cache)


def test_onnx_package_notices_are_archived(builder, monkeypatch, tmp_path):
    import bundle_support

    site = tmp_path / "site"
    package = site / "onnxruntime"
    package.mkdir(parents=True)
    files = [Path("onnxruntime") / name for name in ("LICENSE", "ThirdPartyNotices.txt", "native.pyd")]
    for file in files:
        (site / file).write_bytes(file.as_posix().encode("utf-8"))
    meta = Message()
    meta["Name"], meta["License"] = "onnxruntime", "MIT"
    distribution = SimpleNamespace(metadata=meta, version="1.31.1", files=files,
                                   locate_file=lambda file: site / file)
    monkeypatch.setattr(bundle_support.metadata, "distributions", lambda: [distribution])
    dist = tmp_path / "dist"
    dist.mkdir()
    inventory = bundle_support.license_inventory(dist, [["native.pyd", str(site / files[-1]), "EXTENSION"]])
    assert inventory["without_license_files"] == []
    notice = inventory["packages"][0]
    with zipfile.ZipFile(dist / "THIRD_PARTY_LICENSES" / notice["archive"]) as archive:
        assert set(archive.namelist()) == {"onnxruntime/LICENSE", "onnxruntime/ThirdPartyNotices.txt"}
        for file in files[:2]:
            assert archive.read(file.as_posix()) == (site / file).read_bytes()
