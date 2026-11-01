"""Offline embedding-model bench for the b5c question set (dispatch 153).

Measurement only: no product code is modified, no paid API is called.
Inference runs on ONNX Runtime (CPU) with `tokenizers`; torch is used only by
the `parity` stage to run the product's own EmbeddingAdapter as a reference.

Stages (run from backend/, with AMADEUS_DATA_DIR pointing at a fresh dir and
AMADEUS_DB_PATH unset for `rank` and `parity`):

  manifest   repo commits and SHA-256 of every file used (needs network)
  embed      one model per process: vectors + cost numbers (no `app` import)
  rank       hit-rate / MRR / leak / cosine statistics -> results.json
  parity     E5 ONNX vs product EmbeddingAdapter, and replica vs product recall

Example:
  python scripts/embedding_bench.py embed --model e5 --models-dir M --out-dir O
  python scripts/embedding_bench.py rank --models-dir M --out-dir O
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import resource
import statistics
import sys
import time
import uuid
from pathlib import Path

import numpy as np

BACKEND = Path(__file__).resolve().parents[1]
QSET_PATH = BACKEND / "tests" / "fixtures" / "memory_recall_b5c_question_set.json"
JA_PATH = Path(__file__).resolve().with_name("embedding_bench_ja_queries.json")

HIT_CATEGORIES = ("resident", "outside_lexical", "outside_paraphrase", "change_current", "time")
SEPARATION_GROUP = ("unknown", "irrelevant")
QWEN_TASK = "Given a web search query, retrieve relevant passages that answer the query"

# Prefixes are copied from each model card (see report). `query_fmt` receives the raw query.
MODELS = {
    "e5": {
        "repo": "intfloat/multilingual-e5-small",
        "dir": "e5",
        "onnx": "onnx/model.onnx",
        "tokenizer": "onnx/tokenizer.json",
        "query_fmt": "query: {}",
        "doc_fmt": "passage: {}",
        "kind": "mean_pool",
        "dims": [384],
    },
    "qwen3": {
        "repo": "onnx-community/Qwen3-Embedding-0.6B-ONNX",
        "dir": "qwen3",
        "onnx": "onnx/model.onnx",
        "tokenizer": "tokenizer.json",
        "query_fmt": "Instruct: " + QWEN_TASK + "\nQuery:{}",
        "doc_fmt": "{}",
        "kind": "last_token",
        "dims": [1024, 384],
    },
    "gemma": {
        "repo": "onnx-community/embeddinggemma-300m-ONNX",
        "dir": "gemma",
        "onnx": "onnx/model.onnx",
        "tokenizer": "tokenizer.json",
        "query_fmt": "task: search result | query: {}",
        "doc_fmt": "title: none | text: {}",
        "kind": "sentence_embedding",
        "dims": [768, 384, 256],
    },
    "jina": {
        "repo": "jinaai/jina-embeddings-v5-text-nano-retrieval",
        "dir": "jina",
        "onnx": "onnx/model.onnx",
        "tokenizer": "tokenizer.json",
        "query_fmt": "Query: {}",
        "doc_fmt": "Document: {}",
        "kind": "sentence_embedding",
        "dims": [768, 384, 256],
    },
}
MAX_TOKENS = 512


def read_json(path: Path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path: Path, payload) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(payload, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")


def load_inputs():
    qset = read_json(QSET_PATH)
    ja = read_json(JA_PATH)["queries"]
    cases = qset["cases"]
    missing = [c["id"] for c in cases if c["id"] not in ja]
    extra = sorted(set(ja) - {c["id"] for c in cases})
    if missing or extra:
        raise SystemExit(f"ja queries do not match cases: missing={missing} extra={extra}")
    facts = qset["facts"]
    return qset, facts, cases, ja


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def l2_normalize(vec: np.ndarray) -> np.ndarray:
    vec = vec.astype(np.float32)
    return vec / np.linalg.norm(vec)


def truncate_renorm(vec: np.ndarray, dim: int) -> np.ndarray:
    return l2_normalize(vec[:dim]) if dim < len(vec) else vec


class OnnxEmbedder:
    """One ONNX model, batch size 1, so no padding rules are needed."""

    def __init__(self, key: str, models_dir: Path, threads: int = 0):
        import onnxruntime as ort
        from tokenizers import Tokenizer

        spec = MODELS[key]
        self.key = key
        self.spec = spec
        root = Path(models_dir) / spec["dir"]
        self.tokenizer = Tokenizer.from_file(str(root / spec["tokenizer"]))
        options = ort.SessionOptions()
        options.intra_op_num_threads = threads
        self.session = ort.InferenceSession(str(root / spec["onnx"]), sess_options=options,
                                            providers=["CPUExecutionProvider"])
        self.input_names = [item.name for item in self.session.get_inputs()]
        self.output_names = [item.name for item in self.session.get_outputs()]
        self.kv_inputs = [name for name in self.input_names if name.startswith("past_key_values.")]
        self.kv_shapes = {item.name: item.shape for item in self.session.get_inputs() if item.name in self.kv_inputs}

    def _run(self, text: str) -> np.ndarray:
        ids = self.tokenizer.encode(text).ids[:MAX_TOKENS]
        input_ids = np.array([ids], dtype=np.int64)
        feeds = {"input_ids": input_ids, "attention_mask": np.ones_like(input_ids)}
        if "token_type_ids" in self.input_names:
            feeds["token_type_ids"] = np.zeros_like(input_ids)
        if "position_ids" in self.input_names:
            feeds["position_ids"] = np.arange(len(ids), dtype=np.int64)[None, :]
        for name in self.kv_inputs:
            shape = self.kv_shapes[name]
            feeds[name] = np.zeros((1, shape[1], 0, shape[3]), dtype=np.float32)
        kind = self.spec["kind"]
        if kind == "sentence_embedding":
            out = self.session.run(["sentence_embedding"], feeds)[0][0]
        else:
            hidden = self.session.run(["last_hidden_state"], feeds)[0][0]
            out = hidden.mean(axis=0) if kind == "mean_pool" else hidden[-1]
        return l2_normalize(out)

    def encode_query(self, text: str) -> np.ndarray:
        return self._run(self.spec["query_fmt"].format(text))

    def encode_doc(self, text: str) -> np.ndarray:
        return self._run(self.spec["doc_fmt"].format(text))


def peak_rss_mb() -> float:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0


def median_latency(embedder: OnnxEmbedder, texts: list[str], passes: int = 3) -> dict:
    """Per pass: median of per-query wall time (tokenize + run + pool). Result: median over passes."""
    per_pass = []
    for _ in range(passes):
        timings = []
        for text in texts:
            started = time.perf_counter()
            embedder.encode_query(text)
            timings.append((time.perf_counter() - started) * 1000.0)
        per_pass.append(statistics.median(timings))
    return {"per_pass_median_ms": [round(v, 2) for v in per_pass], "median_ms": round(statistics.median(per_pass), 2)}


def stage_embed(args) -> None:
    key = args.model
    _, facts, cases, ja = load_inputs()
    zh_queries = [c["query"] for c in cases]
    ja_queries = [ja[c["id"]] for c in cases]
    spec = MODELS[key]
    root = Path(args.models_dir) / spec["dir"]
    size_bytes = sum((root / name).stat().st_size for name in
                     (spec["onnx"], spec["onnx"] + "_data", spec["tokenizer"]) if (root / name).exists())

    started = time.perf_counter()
    embedder = OnnxEmbedder(key, args.models_dir, threads=0)
    load_s = time.perf_counter() - started

    started = time.perf_counter()
    fact_vectors = np.stack([embedder.encode_doc(f["display_text"]) for f in facts])
    index_s = time.perf_counter() - started
    zh_vectors = np.stack([embedder.encode_query(text) for text in zh_queries])
    ja_vectors = np.stack([embedder.encode_query(text) for text in ja_queries])
    default_latency = median_latency(embedder, zh_queries)
    peak_mb = peak_rss_mb()
    del embedder

    single = OnnxEmbedder(key, args.models_dir, threads=1)
    single.encode_query(zh_queries[0])
    single_latency = median_latency(single, zh_queries)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    np.savez(out_dir / f"vectors-{key}.npz", facts=fact_vectors, zh=zh_vectors, ja=ja_vectors)
    write_json(out_dir / f"cost-{key}.json", {
        "model": key, "repo": spec["repo"], "file_bytes": size_bytes,
        "load_s": round(load_s, 2), "index_48_facts_s": round(index_s, 2),
        "query_latency_default_threads": default_latency, "query_latency_1_thread": single_latency,
        "peak_rss_mb": round(peak_mb, 0), "cpu_count": os.cpu_count(),
        "ort_version": __import__("onnxruntime").__version__,
    })
    print(f"[{key}] load {load_s:.1f}s index {index_s:.1f}s peak {peak_mb:.0f}MB "
          f"default {default_latency['median_ms']}ms 1-thread {single_latency['median_ms']}ms")


def stage_manifest(args) -> None:
    from huggingface_hub import HfApi

    api = HfApi()
    extra_repos = {"qwen3": "Qwen/Qwen3-Embedding-0.6B", "gemma": "google/embeddinggemma-300m",
                   "jina": "jinaai/jina-embeddings-v5-text-nano"}
    manifest = {}
    for key, spec in MODELS.items():
        root = Path(args.models_dir) / spec["dir"]
        files = {}
        for path in sorted(p for p in root.rglob("*") if p.is_file() and ".cache" not in p.parts):
            files[str(path.relative_to(root))] = {"bytes": path.stat().st_size, "sha256": sha256_file(path)}
        entry = {"repo": spec["repo"], "commit": api.model_info(spec["repo"]).sha, "files": files}
        if key in extra_repos:
            entry["base_repo"] = extra_repos[key]
            entry["base_commit"] = api.model_info(extra_repos[key]).sha
        manifest[key] = entry
    write_json(Path(args.out_dir) / "manifest.json", manifest)


def product_modules():
    """Product functions imported (never re-implemented). Needs AMADEUS_DATA_DIR."""
    if str(BACKEND) not in sys.path:
        sys.path.insert(0, str(BACKEND))
    from app.services.memory_v11.recall import _alternate_merge, normalize_needs, structured_score
    from app.services.memory_v11.repository import _canonical_fingerprint
    from app.services.memory_v11.retrieval import _cosine

    return structured_score, _alternate_merge, normalize_needs, _canonical_fingerprint, _cosine


def guard_env() -> None:
    if (os.environ.get("AMADEUS_DB_PATH") or "").strip():
        raise SystemExit("AMADEUS_DB_PATH must be unset")
    if not (os.environ.get("AMADEUS_DATA_DIR") or "").strip():
        raise SystemExit("set AMADEUS_DATA_DIR to a fresh directory before importing app")


def fact_pool(facts, canonical_fingerprint):
    """Rows as the product loads them. b5c seeds semantic_json from the file position
    (scripts/memory_recall_b5c.py seed_store) and leaves topic_id empty. fact_id is
    deterministic here so that tie-breaks are reproducible."""
    rows = []
    for index, fact in enumerate(facts):
        semantic = {"subject": "user", "predicate": "note", "object": str(index)}
        rows.append({
            "key": fact["key"], "fact_id": str(uuid.UUID(int=index + 1)), "version_no": 1,
            "display_text": fact["display_text"], "is_pinned": False, "confidence": 0.9,
            "semantic_json": json.dumps(semantic, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            "semantic_fingerprint": canonical_fingerprint(semantic), "topic_id": None,
        })
    return rows


def rank_one(need, qvec, fact_vecs, rows, fns):
    structured_score, alternate_merge, _, _, cosine = fns
    structured_ranked = sorted(rows, key=lambda r: (-structured_score(
        need=need, display=r["display_text"], semantic_json=r["semantic_json"], topic_id=r["topic_id"]),
        -r["confidence"], r["fact_id"]))
    structured = [r for r in structured_ranked if structured_score(
        need=need, display=r["display_text"], semantic_json=r["semantic_json"], topic_id=r["topic_id"]) > 0][:4]
    qlist = [float(x) for x in qvec]
    scored = [(float(cosine(qlist, [float(x) for x in fact_vecs[i]])), row) for i, row in enumerate(rows)]
    scored.sort(key=lambda pair: (-pair[0], pair[1]["fact_id"]))
    vector_top = [row for _, row in scored[:4]]
    merged = alternate_merge(structured, vector_top, limit=4)
    return {
        "top10": [[row["key"], round(score, 6)] for score, row in scored[:10]],
        "vector_top4": [row["key"] for row in vector_top],
        "merged_top4": [row["key"] for row in merged],
        "structured_top": [row["key"] for row in structured],
        "top1_cos": round(scored[0][0], 6),
    }


def summarize_case_rows(cases, rows_by_case, pool_keys):
    summary = {}
    for mode, field in (("vector", "vector_top4"), ("merged", "merged_top4")):
        per_cat = {cat: [0, 0] for cat in HIT_CATEGORIES}
        leaks = [0, 0]
        misses = []
        for case in cases:
            top4 = rows_by_case[case["id"]][field]
            use = case["expected"]["must_use_fact_keys"]
            bad = [k for k in case["expected"]["must_not_use_fact_keys"] if k in pool_keys]
            leaks[1] += len(bad)
            leaks[0] += sum(1 for k in bad if k in top4)
            if case["category"] in HIT_CATEGORIES:
                hit = any(k in top4 for k in use)
                per_cat[case["category"]][0] += int(hit)
                per_cat[case["category"]][1] += 1
                if not hit:
                    misses.append({"case": case["id"], "top4": top4})
        total = [sum(v[0] for v in per_cat.values()), sum(v[1] for v in per_cat.values())]
        summary[mode] = {"hit_total": total, "hit_by_category": per_cat, "must_not_in_top4": leaks, "misses": misses}
    rr = []
    for case in cases:
        if case["category"] not in HIT_CATEGORIES:
            continue
        keys = [k for k, _ in rows_by_case[case["id"]]["top10"]]
        ranks = [keys.index(k) + 1 for k in case["expected"]["must_use_fact_keys"] if k in keys]
        rr.append(1.0 / min(ranks) if ranks else 0.0)
    summary["mrr_at_10"] = round(sum(rr) / len(rr), 4)
    return summary


def separation(cases, rows_by_case):
    low = sorted(rows_by_case[c["id"]]["top1_cos"] for c in cases if c["category"] in SEPARATION_GROUP)
    high = sorted(rows_by_case[c["id"]]["top1_cos"] for c in cases if c["category"] in HIT_CATEGORIES)

    def stats(values):
        return {"min": values[0], "median": round(statistics.median(values), 6), "max": values[-1]}

    # Best single threshold: "has memory" when top-1 cosine >= t. Facts only, no recommendation.
    candidates = sorted(set(low + high))
    best = max(((sum(v < t for v in low) + sum(v >= t for v in high), t) for t in candidates), key=lambda x: (x[0], -x[1]))
    return {"unknown_irrelevant_top1": stats(low), "five_hit_categories_top1": stats(high),
            "cleanly_separable": low[-1] < high[0],
            "best_threshold": {"t": best[1], "correct": best[0], "of": len(low) + len(high)}}


def stage_rank(args) -> None:
    guard_env()
    fns = product_modules()
    _, facts, cases, ja = load_inputs()
    normalize_needs = fns[2]
    rows = fact_pool(facts, fns[3])
    pool_keys = {r["key"] for r in rows}
    out_dir = Path(args.out_dir)
    results, raw = {}, {}
    for key, spec in MODELS.items():
        path = out_dir / f"vectors-{key}.npz"
        if not path.exists():
            print(f"skip {key}: no vectors")
            continue
        data = np.load(path)
        for dim in spec["dims"]:
            label = f"{key}@{dim}"
            fact_vecs = np.stack([truncate_renorm(v, dim) for v in data["facts"]])
            results[label] = {}
            for lang, vectors, texts in (("zh", data["zh"], [c["query"] for c in cases]),
                                         ("ja", data["ja"], [ja[c["id"]] for c in cases])):
                rows_by_case = {}
                for case, vec, text in zip(cases, vectors, texts):
                    need = normalize_needs([text])[0]
                    rows_by_case[case["id"]] = rank_one(need, truncate_renorm(vec, dim), fact_vecs, rows, fns)
                raw[f"{label}/{lang}"] = rows_by_case
                results[label][lang] = {**summarize_case_rows(cases, rows_by_case, pool_keys),
                                        "separation": separation(cases, rows_by_case)}
    write_json(out_dir / "results.json", results)
    write_json(out_dir / "raw-ranking.json", raw)
    print(f"wrote {out_dir / 'results.json'}")


def stage_parity(args) -> None:
    """Step 1: ONNX E5 vs product EmbeddingAdapter (torch). Step 3b: replica vs product recall."""
    guard_env()
    import asyncio

    fns = product_modules()
    _, facts, cases, ja = load_inputs()
    normalize_needs = fns[2]
    out_dir = Path(args.out_dir)
    data = np.load(out_dir / "vectors-e5.npz")

    from app.services.memory import EmbeddingAdapter

    adapter = EmbeddingAdapter()

    async def encode_all():
        facts_t = [np.array(await adapter.encode_passage(f["display_text"]), dtype=np.float32) for f in facts]
        zh_t = [np.array(await adapter.encode_query(c["query"]), dtype=np.float32) for c in cases]
        ja_t = [np.array(await adapter.encode_query(ja[c["id"]]), dtype=np.float32) for c in cases]
        return np.stack(facts_t), np.stack(zh_t), np.stack(ja_t)

    torch_facts, torch_zh, torch_ja = asyncio.run(encode_all())
    report = {}
    for name, ref, got in (("facts", torch_facts, data["facts"]), ("zh_queries", torch_zh, data["zh"]),
                           ("ja_queries", torch_ja, data["ja"])):
        cos = np.sum(ref * got, axis=1) / (np.linalg.norm(ref, axis=1) * np.linalg.norm(got, axis=1))
        report[name] = {"n": len(cos), "min_cosine": float(cos.min()), "all_ge_0.999": bool((cos >= 0.999).all())}
    print(json.dumps(report, indent=1))

    # Replica (ONNX E5 vectors + imported product functions) vs the product's recall_stable_facts.
    rows = fact_pool(facts, fns[3])
    fact_vecs = np.stack(list(data["facts"]))

    async def product_side():
        import itertools

        from app.db import init_db
        from app.services.memory_v11 import repository
        from app.services.memory_v11.recall import MemoryScope, RecallRequest, recall_stable_facts
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import memory_recall_b5c as b5c

        counter = itertools.count(1)
        original = repository.uuid.uuid4
        repository.uuid.uuid4 = lambda: uuid.UUID(int=next(counter))
        await init_db()
        adapter._load()
        try:
            await b5c.seed_store(read_json(QSET_PATH), cases[0], "v11", adapter)
        finally:
            repository.uuid.uuid4 = original
        scope = MemoryScope("ownerA", "steins_gate", "self")
        out = {}
        for lang, texts in (("zh", [c["query"] for c in cases]), ("ja", [ja[c["id"]] for c in cases])):
            for case, text in zip(cases, texts):
                need = normalize_needs([text])[0]
                result = await recall_stable_facts(scope=scope, request=RecallRequest(needs=(need,)), embedder=adapter)
                generated = result.diagnostics["generated"][0]["generated_candidates"]
                out[f"{lang}/{case['id']}"] = [item["display_text"] for item in generated]
        return out

    product = asyncio.run(product_side())
    text_of = {r["key"]: r["display_text"] for r in rows}
    compare = {"cases": 0, "identical": 0, "differences": []}
    for lang, vectors, texts in (("zh", data["zh"], [c["query"] for c in cases]),
                                 ("ja", data["ja"], [ja[c["id"]] for c in cases])):
        for case, vec, text in zip(cases, vectors, texts):
            need = normalize_needs([text])[0]
            mine = [text_of[k] for k in rank_one(need, vec, fact_vecs, rows, fns)["merged_top4"]]
            theirs = product[f"{lang}/{case['id']}"]
            compare["cases"] += 1
            if mine == theirs:
                compare["identical"] += 1
            else:
                compare["differences"].append({"case": f"{lang}/{case['id']}", "replica": mine, "product": theirs})
    report["recall_replica_vs_product"] = compare
    write_json(out_dir / "parity.json", report)
    print(json.dumps({k: v for k, v in report.items() if k != "recall_replica_vs_product"}, indent=1))
    print("recall compare:", compare["identical"], "/", compare["cases"], "identical")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("stage", choices=["manifest", "embed", "rank", "parity"])
    parser.add_argument("--model", choices=sorted(MODELS), help="embed stage only")
    parser.add_argument("--models-dir", required=True)
    parser.add_argument("--out-dir", required=True)
    args = parser.parse_args()
    {"manifest": stage_manifest, "embed": stage_embed, "rank": stage_rank, "parity": stage_parity}[args.stage](args)


if __name__ == "__main__":
    main()
