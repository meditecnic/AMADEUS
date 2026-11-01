"""E5 math and lazy loading without downloading model weights."""
import builtins
from types import SimpleNamespace
import sys

import numpy as np
import pytest

from app.services.memory import E5_REVISION, EmbeddingAdapter


@pytest.fixture
def offline_embedding_model():
    """Use the actual adapter with fake ONNX/tokenizer dependencies below."""


@pytest.fixture
def fake_runtime(monkeypatch, tmp_path):
    class Tokenizer:
        def __init__(self):
            self.max_length = None
            self.texts = []

        @classmethod
        def from_file(cls, path):
            assert path == str(tmp_path / "tokenizer.json")
            return tokenizer

        def enable_truncation(self, max_length):
            self.max_length = max_length

        def encode(self, text):
            self.texts.append(text)
            ids = list(range(len(text.split())))[:self.max_length]
            return SimpleNamespace(ids=ids, attention_mask=[1] * (len(ids) - 1) + [0])

    class Session:
        def __init__(self, path, providers):
            assert path == str(tmp_path / "onnx/model.onnx")
            assert providers == ["CPUExecutionProvider"]
            self.names = ["input_ids", "attention_mask", "token_type_ids"]
            self.inputs = None

        def get_inputs(self):
            return [SimpleNamespace(name=name) for name in self.names]

        def run(self, outputs, inputs):
            assert outputs == ["last_hidden_state"]
            assert set(inputs) == set(self.names)
            assert all(value.dtype == np.int64 for value in inputs.values())
            if "token_type_ids" in inputs:
                assert not inputs["token_type_ids"].any()
            self.inputs = inputs
            hidden = np.zeros((*inputs["input_ids"].shape, 384), dtype=np.float32)
            hidden[:, 0, 0] = 3
            hidden[:, 1, 1] = 4
            hidden[:, -1, :] = 1000  # This masked token must contribute nothing.
            return [hidden]

    tokenizer = Tokenizer()
    monkeypatch.setitem(sys.modules, "tokenizers", SimpleNamespace(Tokenizer=Tokenizer))
    monkeypatch.setitem(sys.modules, "onnxruntime", SimpleNamespace(InferenceSession=Session))
    (tmp_path / "onnx").mkdir()
    (tmp_path / "onnx/model.onnx").write_bytes(b"fake model")
    (tmp_path / "tokenizer.json").write_text("{}", encoding="utf-8")
    return EmbeddingAdapter(model_dir=tmp_path), tokenizer


def test_masked_mean_pooling_and_unit_norm(fake_runtime):
    adapter, _ = fake_runtime
    vector = adapter._encode("passage: first second")
    assert len(vector) == 384
    assert all(type(value) is float for value in vector)
    np.testing.assert_allclose(vector[:2], [0.6, 0.8], atol=1e-7)
    np.testing.assert_array_equal(vector[2:], 0)
    assert np.linalg.norm(vector) == pytest.approx(1)


def test_long_input_is_truncated_to_512(fake_runtime):
    adapter, _ = fake_runtime
    adapter._encode("query: " + "long " * 700)
    assert adapter._model.inputs["input_ids"].shape == (1, 512)
    assert adapter._model.inputs["attention_mask"].shape == (1, 512)


@pytest.mark.asyncio
async def test_query_and_passage_prefixes(fake_runtime):
    adapter, tokenizer = fake_runtime
    await adapter.encode_query("hello world")
    await adapter.encode_passage("hello world")
    assert tokenizer.texts == ["query: hello world", "passage: hello world"]


def test_only_requested_model_inputs_are_supplied(fake_runtime):
    adapter, _ = fake_runtime
    adapter._load().names = ["input_ids", "attention_mask"]
    adapter._encode("passage: first second")


def test_loading_is_lazy_and_cached(fake_runtime):
    adapter, _ = fake_runtime
    assert adapter._model is None and adapter._tokenizer is None
    first = adapter._load()
    assert adapter._load() is first


@pytest.mark.parametrize("missing", ["onnx/model.onnx", "tokenizer.json"])
def test_missing_snapshot_file_explains_source(fake_runtime, missing):
    adapter, _ = fake_runtime
    (adapter.model_dir / missing).unlink()
    with pytest.raises(FileNotFoundError) as exc:
        adapter._load()
    assert missing in str(exc.value)
    assert f"https://huggingface.co/{adapter.model_name}/resolve/{E5_REVISION}/{missing}" in str(exc.value)


def test_default_downloads_use_pinned_revision(fake_runtime, monkeypatch):
    adapter, _ = fake_runtime
    snapshot = adapter.model_dir
    adapter.model_dir = None
    calls = []

    def download(repo_id, filename, revision):
        calls.append((repo_id, filename, revision))
        return str(snapshot / filename)

    monkeypatch.setitem(sys.modules, "huggingface_hub", SimpleNamespace(hf_hub_download=download))
    adapter._load()
    assert calls == [(adapter.model_name, name, E5_REVISION) for name in ("onnx/model.onnx", "tokenizer.json")]


@pytest.mark.parametrize("missing", [False, True])
def test_readiness_checks_runtime_without_loading(monkeypatch, missing):
    adapter = EmbeddingAdapter()
    for name in ("onnxruntime", "tokenizers", "sqlite_vec"):
        monkeypatch.setitem(sys.modules, name, SimpleNamespace())
    original_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        if missing and name == "onnxruntime":
            raise ImportError("No module named 'onnxruntime'")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    result = adapter.readiness()
    expected = {"ok": not missing, "model": adapter.model_name, "dimensions": 384}
    if missing:
        expected.update(degraded=True, error="No module named 'onnxruntime'")
    else:
        expected["loaded"] = False
    assert result == expected
    assert adapter._model is None
