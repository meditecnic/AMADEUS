"""Second-round embedding bench on the harder question set (dispatch 157).

Measurement only: no product code is modified, no paid API is called. It reuses the
ONNX runner and the product-function replica of `recall_stable_facts` from
`embedding_bench.py` (dispatch 153) and swaps in `embedding_bench_hard.json`.

Stages (run from backend/; `rank` needs AMADEUS_DATA_DIR pointing at a fresh dir and
AMADEUS_DB_PATH unset):

  embed   one variant per process: vectors + cost numbers (no `app` import)
  rank    hit rate / MRR / leak / bootstrap / cosine statistics -> results.json, raw-ranking.json
  sha     SHA-256 and size of every model file used -> files.json

Example:
  python scripts/embedding_bench_hard.py embed --variant e5 --models-dir M --out-dir O
  python scripts/embedding_bench_hard.py rank --out-dir O
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import embedding_bench as base  # noqa: E402

HARD_PATH = Path(__file__).resolve().with_name("embedding_bench_hard.json")
COMBOS = ("zh-zh", "ja-ja", "zh-ja", "ja-zh")
BOOTSTRAP_RESAMPLES = 10000
BOOTSTRAP_SEED = 0

# variant -> (base model key, onnx file, dims to rank). `jina_int8` is the vendor's own
# quantised export (onnx/model_quantized.onnx); nothing was quantised locally.
VARIANTS = {
    "e5": {"model": "e5", "onnx": "onnx/model.onnx", "dims": [384]},
    "jina": {"model": "jina", "onnx": "onnx/model.onnx", "dims": [768, 256]},
    "jina_int8": {"model": "jina", "onnx": "onnx/model_quantized.onnx", "dims": [768, 256]},
    "gemma": {"model": "gemma", "onnx": "onnx/model.onnx", "dims": [768]},
}
for _name, _spec in VARIANTS.items():
    base.MODELS[_name] = {**base.MODELS[_spec["model"]], "onnx": _spec["onnx"]}

# Thresholds picked on the b5c set in dispatch 153, applied here unchanged: query language -> value.
B5C_THRESHOLDS = {
    "e5@384": {"zh": 0.859, "ja": 0.763},
    "jina@768": {"zh": 0.551, "ja": 0.390},
    "gemma@768": {"zh": 0.472, "ja": 0.349},
}


def load_hard():
    payload = base.read_json(HARD_PATH)
    return payload, payload["facts"], payload["cases"]


def stage_embed(args) -> None:
    key = args.variant
    _, facts, cases = load_hard()
    queries = [c["query"] for c in cases]
    spec = base.MODELS[key]
    root = Path(args.models_dir) / spec["dir"]
    size_bytes = sum((root / name).stat().st_size for name in
                     (spec["onnx"], spec["onnx"] + "_data", spec["tokenizer"]) if (root / name).exists())

    started = time.perf_counter()
    embedder = base.OnnxEmbedder(key, args.models_dir, threads=0)
    load_s = time.perf_counter() - started

    started = time.perf_counter()
    fact_vectors = np.stack([embedder.encode_doc(f["display_text"]) for f in facts])
    index_s = time.perf_counter() - started
    query_vectors = np.stack([embedder.encode_query(text) for text in queries])
    default_latency = base.median_latency(embedder, queries)
    peak_default_mb = base.peak_rss_mb()
    del embedder

    single = base.OnnxEmbedder(key, args.models_dir, threads=1)
    single.encode_query(queries[0])
    single_latency = base.median_latency(single, queries)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    np.savez(out_dir / f"vectors-{key}.npz", facts=fact_vectors, queries=query_vectors)
    tag = f"-{args.tag}" if args.tag else ""
    base.write_json(out_dir / f"cost-{key}{tag}.json", {
        "variant": key, "repo": spec["repo"], "onnx": spec["onnx"], "file_bytes": size_bytes,
        "load_s": round(load_s, 2), "index_120_facts_s": round(index_s, 2),
        "query_latency_default_threads": default_latency, "query_latency_1_thread": single_latency,
        "peak_rss_mb_after_default_phase": round(peak_default_mb, 0),
        "peak_rss_mb_final": round(base.peak_rss_mb(), 0), "n_query_texts": len(queries),
        "cpu_count": os.cpu_count(), "ort_version": __import__("onnxruntime").__version__,
    })
    print(f"[{key}] load {load_s:.1f}s index {index_s:.1f}s peak {peak_default_mb:.0f}MB "
          f"default {default_latency['median_ms']}ms 1-thread {single_latency['median_ms']}ms")


def stage_sha(args) -> None:
    from huggingface_hub import HfApi

    api = HfApi()
    out = {}
    for key, spec in base.MODELS.items():
        if key not in VARIANTS:
            continue
        root = Path(args.models_dir) / spec["dir"]
        files = {}
        for rel in (spec["onnx"], spec["onnx"] + "_data", spec["tokenizer"]):
            path = root / rel
            if path.exists():
                files[rel] = {"bytes": path.stat().st_size, "sha256": base.sha256_file(path)}
        out[key] = {"repo": spec["repo"], "commit": api.model_info(spec["repo"]).sha, "files": files}
    base.write_json(Path(args.out_dir) / "files.json", out)


def case_metrics(subset, per_case):
    n = len(subset)
    out = {"n": n}
    for mode, field in (("vector", "vector_top4"), ("merged", "merged_top4")):
        hits = [any(k in per_case[c["id"]][field] for k in c["must_use_fact_keys"]) for c in subset]
        para = [h for h, c in zip(hits, subset) if c["paraphrase"]]
        leaks = sum(sum(1 for k in c["must_not_use_fact_keys"] if k in per_case[c["id"]][field]) for c in subset)
        out[mode] = {"hits": sum(hits), "paraphrase_hits": sum(para), "paraphrase_n": len(para),
                     "must_not_in_top4": leaks, "must_not_total": sum(len(c["must_not_use_fact_keys"]) for c in subset)}
    rr = []
    for c in subset:
        keys = [k for k, _ in per_case[c["id"]]["top10"]]
        ranks = [keys.index(k) + 1 for k in c["must_use_fact_keys"] if k in keys]
        rr.append(1.0 / min(ranks) if ranks else 0.0)
    out["mrr_at_10_vector"] = round(sum(rr) / n, 4)
    out["misses"] = {mode: [{"case": c["id"], "combo": c["combo"], "top4": per_case[c["id"]][field]}
                            for c in subset
                            if not any(k in per_case[c["id"]][field] for k in c["must_use_fact_keys"])]
                     for mode, field in (("vector", "vector_top4"), ("merged", "merged_top4"))}
    return out


def hit_array(subset, per_case, field="merged_top4"):
    return np.array([any(k in per_case[c["id"]][field] for k in c["must_use_fact_keys"]) for c in subset], dtype=float)


def paired_bootstrap(a: np.ndarray, b: np.ndarray) -> dict:
    """Percentage-point difference of mean hit (a minus b), resampling questions with replacement."""
    rng = np.random.default_rng(BOOTSTRAP_SEED)
    idx = rng.integers(0, len(a), size=(BOOTSTRAP_RESAMPLES, len(a)))
    diff = (a[idx].mean(axis=1) - b[idx].mean(axis=1)) * 100.0
    lo, hi = np.percentile(diff, [2.5, 97.5])
    return {"diff_pp": round(float((a.mean() - b.mean()) * 100.0), 2), "ci95_pp": [round(float(lo), 2), round(float(hi), 2)],
            "n": len(a)}


def top1_stats(values):
    values = sorted(values)
    return {"min": values[0], "median": round(statistics.median(values), 6), "max": values[-1], "n": len(values)}


def best_threshold(cases, per_case):
    """'Has memory' when top-1 cosine >= t. Count of right calls over the given cases; ties -> smaller t."""
    low = [per_case[c["id"]]["top1_cos"] for c in cases if c["category"] != "memory"]
    high = [per_case[c["id"]]["top1_cos"] for c in cases if c["category"] == "memory"]
    best = max(((sum(v < t for v in low) + sum(v >= t for v in high), t) for t in sorted(set(low + high))),
               key=lambda x: (x[0], -x[1]))
    return {"t": best[1], "correct": best[0], "of": len(low) + len(high)}


def apply_threshold(cases, per_case, thresholds):
    correct = 0
    for c in cases:
        t = thresholds[c["query_lang"]]
        has = per_case[c["id"]]["top1_cos"] >= t
        correct += int(has == (c["category"] == "memory"))
    return {"thresholds": thresholds, "correct": correct, "of": len(cases)}


def stage_rank(args) -> None:
    base.guard_env()
    fns = base.product_modules()
    normalize_needs = fns[2]
    _, facts, cases = load_hard()
    rows = base.fact_pool(facts, fns[3])
    memory = [c for c in cases if c["category"] == "memory"]
    out_dir = Path(args.out_dir)
    results, raw, hits = {}, {}, {}
    for variant, spec in VARIANTS.items():
        path = out_dir / f"vectors-{variant}.npz"
        if not path.exists():
            print(f"skip {variant}: no vectors")
            continue
        data = np.load(path)
        for dim in spec["dims"]:
            label = f"{variant}@{dim}"
            fact_vecs = np.stack([base.truncate_renorm(v, dim) for v in data["facts"]])
            per_case = {}
            for case, vec in zip(cases, data["queries"]):
                need = normalize_needs([case["query"]])[0]
                per_case[case["id"]] = base.rank_one(need, base.truncate_renorm(vec, dim), fact_vecs, rows, fns)
            raw[label] = per_case
            entry = {"all": case_metrics(memory, per_case)}
            for combo in COMBOS:
                entry[combo] = case_metrics([c for c in memory if c["combo"] == combo], per_case)
            no_mem = [c for c in cases if c["category"] != "memory"]
            entry["top1_cosine"] = {
                "memory": top1_stats([per_case[c["id"]]["top1_cos"] for c in memory]),
                "no_memory_all": top1_stats([per_case[c["id"]]["top1_cos"] for c in no_mem]),
                "no_memory_in_domain": top1_stats([per_case[c["id"]]["top1_cos"] for c in cases if c["category"] == "no_memory_in_domain"]),
                "no_memory_unrelated": top1_stats([per_case[c["id"]]["top1_cos"] for c in cases if c["category"] == "no_memory_unrelated"]),
                "best_single_threshold_100": best_threshold(cases, per_case),
                "best_threshold_zh_queries": best_threshold([c for c in cases if c["query_lang"] == "zh"], per_case),
                "best_threshold_ja_queries": best_threshold([c for c in cases if c["query_lang"] == "ja"], per_case),
            }
            if label in B5C_THRESHOLDS:
                entry["top1_cosine"]["b5c_thresholds_applied"] = apply_threshold(cases, per_case, B5C_THRESHOLDS[label])
            results[label] = entry
            hits[label] = {
                "all": hit_array(memory, per_case),
                **{combo: hit_array([c for c in memory if c["combo"] == combo], per_case) for combo in COMBOS},
            }

    reference = "e5@384"
    for label, entry in results.items():
        if label == reference or reference not in hits:
            continue
        entry["bootstrap_vs_e5_merged_hit"] = {scope: paired_bootstrap(hits[label][scope], hits[reference][scope])
                                               for scope in ("all", *COMBOS)}

    if "jina" in VARIANTS and (out_dir / "vectors-jina.npz").exists() and (out_dir / "vectors-jina_int8.npz").exists():
        fp32, int8 = np.load(out_dir / "vectors-jina.npz"), np.load(out_dir / "vectors-jina_int8.npz")
        cos = np.sum(np.concatenate([fp32["facts"], fp32["queries"]]) * np.concatenate([int8["facts"], int8["queries"]]), axis=1)
        results["_jina_int8_vs_fp32_cosine"] = {"n": int(len(cos)), "min": round(float(cos.min()), 6),
                                                "median": round(float(np.median(cos)), 6)}

    base.write_json(out_dir / "results.json", results)
    base.write_json(out_dir / "raw-ranking.json", raw)
    print(f"wrote {out_dir / 'results.json'}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("stage", choices=["embed", "rank", "sha"])
    parser.add_argument("--variant", choices=sorted(VARIANTS), help="embed stage only")
    parser.add_argument("--models-dir", help="embed and sha stages")
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--tag", default="", help="embed stage: suffix for the cost file (repeat runs)")
    args = parser.parse_args()
    {"embed": stage_embed, "rank": stage_rank, "sha": stage_sha}[args.stage](args)


if __name__ == "__main__":
    main()
