"""Compare the product adapter with dispatch 157's frozen jina ONNX reference.

Run from backend/ with a fresh AMADEUS_DATA_DIR and AMADEUS_DB_PATH unset:
  python scripts/jina_embedding_parity.py --model-dir /path/to/snapshot

The 120 facts and 100 queries are copied unchanged from AMADEUS commit
c82d0ea04eadb42d9ba876dd7c5650ee872f68db, scripts/embedding_bench_hard.json.
This script is for reviewers; it never downloads a model or calls a service.
"""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys

import numpy as np

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))


class OnnxEmbedder:
    """Jina-only copy of embedding_bench.py:OnnxEmbedder at c82d0ea04eadb42d9ba876dd7c5650ee872f68db.

    Removed the other model specs, last_token/mean_pool branches and the
    models_dir/jina directory convention; jina encoding operations are unchanged.
    """

    def __init__(self, model_dir: Path):
        import onnxruntime as ort
        from tokenizers import Tokenizer

        self.tokenizer = Tokenizer.from_file(str(model_dir / "tokenizer.json"))
        options = ort.SessionOptions()
        options.intra_op_num_threads = 0
        self.session = ort.InferenceSession(str(model_dir / "onnx/model.onnx"), sess_options=options,
                                            providers=["CPUExecutionProvider"])
        self.input_names = [item.name for item in self.session.get_inputs()]
        self.kv_inputs = [name for name in self.input_names if name.startswith("past_key_values.")]
        self.kv_shapes = {item.name: item.shape for item in self.session.get_inputs() if item.name in self.kv_inputs}

    def _run(self, text: str) -> np.ndarray:
        ids = self.tokenizer.encode(text).ids[:512]
        input_ids = np.array([ids], dtype=np.int64)
        feeds = {"input_ids": input_ids, "attention_mask": np.ones_like(input_ids)}
        if "token_type_ids" in self.input_names:
            feeds["token_type_ids"] = np.zeros_like(input_ids)
        if "position_ids" in self.input_names:
            feeds["position_ids"] = np.arange(len(ids), dtype=np.int64)[None, :]
        for name in self.kv_inputs:
            shape = self.kv_shapes[name]
            feeds[name] = np.zeros((1, shape[1], 0, shape[3]), dtype=np.float32)
        out = self.session.run(["sentence_embedding"], feeds)[0][0]
        vec = out.astype(np.float32)
        return vec / np.linalg.norm(vec)

    def encode_query(self, text: str) -> np.ndarray:
        return self._run("Query: {}".format(text))

    def encode_doc(self, text: str) -> np.ndarray:
        return self._run("Document: {}".format(text))


async def compare(model_dir: Path, dataset: Path) -> dict:
    from app.services.memory import EmbeddingAdapter

    data = json.loads(dataset.read_text(encoding="utf-8"))
    product, reference = EmbeddingAdapter(model_dir=model_dir), OnnxEmbedder(model_dir)
    rows = []
    for kind, entries, field in (("fact", data["facts"], "display_text"), ("query", data["cases"], "query")):
        for index, entry in enumerate(entries):
            text = entry[field]
            if kind == "fact":
                actual, expected = await product.encode_passage(text), reference.encode_doc(text)
            else:
                actual, expected = await product.encode_query(text), reference.encode_query(text)
            left, right = np.asarray(actual), np.asarray(expected)
            if left.shape != (768,) or right.shape != (768,):
                raise ValueError(f"wrong dimensions for {kind} {index}: {left.shape}, {right.shape}")
            cosine = float(np.dot(left, right) / (np.linalg.norm(left) * np.linalg.norm(right)))
            if not np.isfinite(cosine):
                raise ValueError(f"nonfinite cosine for {kind} {index}")
            rows.append({"kind": kind, "index": index, "cosine": cosine})
    return {"count": len(rows), "minimum_cosine": min(row["cosine"] for row in rows), "rows": rows}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-dir", required=True, type=Path)
    parser.add_argument("--dataset", type=Path, default=Path(__file__).with_name("embedding_bench_hard.json"))
    args = parser.parse_args()
    print(json.dumps(asyncio.run(compare(args.model_dir, args.dataset)), ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
