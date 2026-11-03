"""EMBEDDING math and lazy loading without downloading model weights."""
import builtins
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
import sys
import threading
import time

import numpy as np
import pytest

from app.services.memory import EMBEDDING_REVISION, EmbeddingAdapter


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
            # Keep two visible positions and one masked position for short inputs.
            ids = list(range(max(3, len(text.split()))))
            return SimpleNamespace(ids=ids, attention_mask=[1] * (len(ids) - 1) + [0])

    class Session:
        def __init__(self, path, providers, sess_options=None):
            assert path == str(tmp_path / "onnx/model.onnx")
            assert providers == ["CPUExecutionProvider"]
            self.names = ["input_ids", "attention_mask", "position_ids", "past_key_values.0.key", "past_key_values.0.value"]
            self.inputs = None

        def get_inputs(self):
            return [SimpleNamespace(name=name, shape=[1, 2, "past", 64]) for name in self.names]

        def run(self, outputs, inputs):
            assert outputs == ["sentence_embedding"]
            assert set(inputs) == set(self.names)
            for name, value in inputs.items():
                if name.startswith("past_key_values."):
                    assert value.shape == (1, 2, 0, 64)
                    assert value.dtype == np.float32
                else:
                    assert value.dtype == np.int64
            np.testing.assert_array_equal(inputs["attention_mask"], 1)
            if "position_ids" in inputs:
                np.testing.assert_array_equal(inputs["position_ids"], np.arange(inputs["input_ids"].shape[1])[None, :])
            self.inputs = inputs
            embedding = np.zeros((1, 768), dtype=np.float32)
            embedding[0, :2] = [3, 4]
            return [embedding]

    tokenizer = Tokenizer()
    monkeypatch.setitem(sys.modules, "tokenizers", SimpleNamespace(Tokenizer=Tokenizer))
    monkeypatch.setitem(sys.modules, "onnxruntime", SimpleNamespace(InferenceSession=Session, SessionOptions=SimpleNamespace))
    (tmp_path / "onnx").mkdir()
    (tmp_path / "onnx/model.onnx").write_bytes(b"fake model")
    (tmp_path / "onnx/model.onnx_data").write_bytes(b"fake weights")
    (tmp_path / "tokenizer.json").write_text("{}", encoding="utf-8")
    return EmbeddingAdapter(model_dir=tmp_path), tokenizer


def test_sentence_embedding_and_unit_norm(fake_runtime):
    adapter, _ = fake_runtime
    vector = adapter._encode("Document: first second")
    assert len(vector) == 768
    assert all(type(value) is float for value in vector)
    np.testing.assert_allclose(vector[:2], [0.6, 0.8], atol=1e-7)
    np.testing.assert_array_equal(vector[2:], 0)
    assert np.linalg.norm(vector) == pytest.approx(1)


def test_long_input_is_truncated_to_512(fake_runtime):
    adapter, _ = fake_runtime
    adapter._encode("Query: " + "long " * 700)
    assert adapter._model.inputs["input_ids"].shape == (1, 512)
    assert adapter._model.inputs["attention_mask"].shape == (1, 512)


@pytest.mark.asyncio
async def test_query_and_passage_prefixes(fake_runtime):
    adapter, tokenizer = fake_runtime
    await adapter.encode_query("hello world")
    await adapter.encode_passage("hello world")
    assert tokenizer.texts == ["Query: hello world", "Document: hello world"]


@pytest.mark.asyncio
@pytest.mark.parametrize("kind, prefix", [("query", "Query: "), ("passage", "Document: ")])
@pytest.mark.parametrize("body", ["first second ", "first second\n", "first second\u3000", "first second\u00a0", "", " \n\u3000\u00a0", "  first second  "])
async def test_whitespace_matches_reference(fake_runtime, kind, prefix, body):
    adapter, tokenizer = fake_runtime
    await getattr(adapter, f"encode_{kind}")(body)
    assert tokenizer.texts == [prefix + body]


def test_concurrent_first_encode_loads_once(fake_runtime, monkeypatch):
    adapter, _ = fake_runtime
    runtime = sys.modules["onnxruntime"]
    session_class = runtime.InferenceSession
    sessions = []
    barrier = threading.Barrier(8)

    def slow_session(*args, **kwargs):
        time.sleep(0.05)
        session = session_class(*args, **kwargs)
        sessions.append(session)
        return session

    def encode(_index):
        barrier.wait(timeout=10)
        return adapter._encode("Document: first second")

    monkeypatch.setattr(runtime, "InferenceSession", slow_session)
    with ThreadPoolExecutor(max_workers=8) as executor:
        vectors = list(executor.map(encode, range(8)))

    assert len(sessions) == 1
    assert adapter._model is sessions[0]
    expected = np.zeros(768)
    expected[:2] = [0.6, 0.8]
    assert len(vectors) == 8
    for vector in vectors:
        np.testing.assert_allclose(vector, expected, atol=1e-7)


def test_only_requested_model_inputs_are_supplied(fake_runtime):
    adapter, _ = fake_runtime
    adapter._load().names = ["input_ids", "attention_mask"]
    adapter._encode("Document: first second")


def test_loading_is_lazy_and_cached(fake_runtime):
    adapter, _ = fake_runtime
    assert adapter._model is None and adapter._tokenizer is None
    first = adapter._load()
    assert adapter._load() is first


@pytest.mark.parametrize("missing", ["onnx/model.onnx", "onnx/model.onnx_data", "tokenizer.json"])
def test_missing_snapshot_file_explains_source(fake_runtime, missing):
    adapter, _ = fake_runtime
    (adapter.model_dir / missing).unlink()
    with pytest.raises(FileNotFoundError) as exc:
        adapter._load()
    assert missing in str(exc.value)
    assert f"https://huggingface.co/{adapter.model_name}/resolve/{EMBEDDING_REVISION}/{missing}" in str(exc.value)


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
    assert calls == [(adapter.model_name, name, EMBEDDING_REVISION) for name in ("onnx/model.onnx", "tokenizer.json", "onnx/model.onnx_data")]


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
    expected = {"ok": not missing, "model": adapter.model_name, "dimensions": 768}
    if missing:
        expected.update(degraded=True, error="No module named 'onnxruntime'")
    else:
        expected["loaded"] = False
    assert result == expected
    assert adapter._model is None


def test_wrong_output_dimension_is_rejected(fake_runtime, monkeypatch):
    adapter, _ = fake_runtime
    session = adapter._load()
    monkeypatch.setattr(session, "run", lambda *args: [np.zeros((1, 384), dtype=np.float32) + 1])
    with pytest.raises(RuntimeError, match="dimension mismatch"):
        adapter._encode("Query: hello")


@pytest.mark.asyncio
async def test_parity_script_all_facts_and_queries_with_fake_onnx(fake_runtime):
    from pathlib import Path
    from scripts.jina_embedding_parity import compare

    adapter, tokenizer = fake_runtime
    dataset = Path(__file__).resolve().parents[1] / "scripts/embedding_bench_hard.json"
    result = await compare(adapter.model_dir, dataset)
    assert result["count"] == 220
    assert result["minimum_cosine"] == pytest.approx(1, abs=1e-7)
    assert len(tokenizer.texts) == 440
    assert tokenizer.texts[0].startswith("Document: ")
    assert tokenizer.texts[-1].startswith("Query: ")
