"""Pinned embedding whitelist, source integrity, and copied-byte verification."""
import hashlib
from email.message import Message
import json
from pathlib import Path
import shutil
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
    assert builder.EMBEDDING_FILES == {
        "onnx/model.onnx": "ad8244eea593ae8488cfd821a7d573aa51a350bc817702d6da99f3bccab24f49",
        "onnx/model.onnx_data": "7eeca7c5e63a9047991df400cdd57c3cf05fabc0168700a4ecee42d1eba8093d",
        "tokenizer.json": "98d4a1d32152d6cedf85b5e88f3b205106dca1fe72aaab34e0ac13c238421069",
        "README.md": "9f2433969e7962fffd5cd2bfef18bb319032dc973090d5449628d9bb4d219381",
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


def test_reused_stage_removes_other_models_and_unpinned_entries(builder, stage, tmp_path, capsys):
    source, cache = stage
    model = cache / builder.EMBEDDING_CACHE_MODEL
    snapshot = builder.embedding_snapshot(cache)
    stale = [
        cache / "models--intfloat--multilingual-e5-small/snapshots/old/README.md",
        model / "snapshots/old/README.md",
        snapshot / "onnx/model_quantized.onnx",
        model / "refs/old",
        cache / "unused.txt",
    ]
    for path in stale:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"stale")
    (snapshot / "unused-directory").mkdir()
    sibling = tmp_path / "outside/README.md"
    sibling.parent.mkdir()
    sibling.write_bytes(b"outside cache")
    source_before = {p.relative_to(source): p.read_bytes() for p in source.rglob("*") if p.is_file()}

    builder.stage_embedding(source, cache)

    assert set(cache.iterdir()) == {model}
    assert set((model / "snapshots").iterdir()) == {snapshot}
    assert {p.relative_to(snapshot).as_posix() for p in snapshot.rglob("*") if p.is_file()} == set(builder.EMBEDDING_FILES)
    assert set((model / "refs").iterdir()) == {model / "refs/main"}
    assert not any(path.exists() for path in stale)
    assert not (snapshot / "unused-directory").exists()
    assert sibling.read_bytes() == b"outside cache"
    assert source_before == {p.relative_to(source): p.read_bytes() for p in source.rglob("*") if p.is_file()}
    output = capsys.readouterr().out
    assert "Removed staged embedding entry:" in output
    assert "models--intfloat--multilingual-e5-small" in output
    assert str(model / "snapshots/old") in output
    builder.validate_embedding_cache(cache)


@pytest.fixture
def frozen_backend(builder, stage, tmp_path):
    source, cache = stage
    builder.stage_embedding(source, cache)
    backend = tmp_path / "backend"
    shutil.copytree(cache, backend / "_internal/hf_cache")
    return backend


def test_frozen_embedding_cache_accepts_only_pinned_files(builder, frozen_backend):
    builder.validate_embedding_cache(frozen_backend / "_internal/hf_cache")


@pytest.mark.parametrize("failure", ["extra_model", "extra_file", "extra_directory", "missing_file", "bad_hash"])
def test_frozen_embedding_cache_rejects_invalid_contents(builder, frozen_backend, failure):
    cache = frozen_backend / "_internal/hf_cache"
    snapshot = builder.embedding_snapshot(cache)
    if failure == "extra_model":
        path = cache / "models--intfloat--multilingual-e5-small/README.md"
        path.parent.mkdir()
        path.write_bytes(b"old model")
        message = "models--intfloat--multilingual-e5-small"
    elif failure == "extra_file":
        (snapshot / "extra.bin").write_bytes(b"unexpected")
        message = "extra.bin"
    elif failure == "extra_directory":
        (snapshot / "extra-directory").mkdir()
        message = "extra-directory"
    elif failure == "missing_file":
        (snapshot / "onnx/model.onnx_data").unlink()
        message = "model.onnx_data"
    else:
        (snapshot / "onnx/model.onnx_data").write_bytes(b"bad weights")
        message = "hash mismatch"
    with pytest.raises(RuntimeError, match=message):
        builder.validate_embedding_cache(cache)


@pytest.fixture
def model_license(builder, stage, monkeypatch, tmp_path):
    import license_bundle

    source, cache = stage
    builder.stage_embedding(source, cache)
    backend = tmp_path / "license-backend"
    frozen_cache = backend / "_internal/hf_cache"
    # A root-level E5 card is found before descending into any model directory.
    old_card = frozen_cache / "README.md"
    old_card.parent.mkdir(parents=True)
    old_card.write_bytes(b"E5 card must not be copied as jina")
    shutil.copytree(cache, frozen_cache, dirs_exist_ok=True)
    assert next(frozen_cache.rglob("README.md")) == old_card
    notice = backend / "THIRD_PARTY_LICENSES/embedding-model.json"
    notice.parent.mkdir()
    notice.write_text(json.dumps({
        "id": "jinaai/jina-embeddings-v5-text-nano-retrieval", "revision": builder.EMBEDDING_REVISION,
        "source": "pinned model source", "attribution": "Jina AI", "license_url": "CC-BY-NC-4.0",
    }), encoding="utf-8")
    monkeypatch.setattr(license_bundle, "notices", lambda *args: [])
    monkeypatch.setattr(license_bundle, "python_notices", lambda *args: {})
    for name in ("cpython_notices", "rust_notices", "frontend_notices", "native_notices"):
        monkeypatch.setattr(license_bundle, name, lambda *args: None)
    output = tmp_path / "licenses"

    def generate():
        return license_bundle.generate({}, backend, tmp_path, output, tmp_path, tmp_path, "Amadeus.exe")

    return frozen_cache, source, output, generate


def test_model_license_uses_pinned_jina_card_even_when_e5_is_first(builder, model_license):
    _, source, output, generate = model_license
    manifest = generate()
    card = output / "models/jina-embeddings-v5-text-nano-retrieval-README.md"
    assert card.read_bytes() == (source / "README.md").read_bytes()
    model = next(row for row in manifest["components"] if row["category"] == "model")
    assert model["license"] == "CC-BY-NC-4.0"
    assert model["license_files"][0]["sha256"] == builder.EMBEDDING_FILES["README.md"]


def test_model_license_rejects_bad_jina_card(builder, model_license):
    frozen_cache, _, _, generate = model_license
    card = builder.embedding_snapshot(frozen_cache) / "README.md"
    card.write_bytes(b"not the pinned jina card")
    with pytest.raises(RuntimeError, match="model card missing or hash mismatch"):
        generate()


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
