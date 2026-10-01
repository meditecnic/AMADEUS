# backend/tests/test_tts_optimization.py
import argparse
import asyncio
import os
import struct
import sys
import time
import wave
import httpx

# Add backend directory to sys.path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.tts_queue import EMOTION_REF_MAP
from tests.test_all_emotions import PRESETS, compute_peak

def parse_args():
    parser = argparse.ArgumentParser(description="TTS parameter optimization and reference audio tuning scan")
    parser.add_argument("--quick", action="store_true", help="Only test raw audio × olv preset × repetition_penalty [1.35, 1.5]")
    parser.add_argument("--sovits-url", type=str, default="http://127.0.0.1:9880", help="GPT-SoVITS base URL")
    parser.add_argument("--out-dir", type=str, default=None, help="Output directory")
    parser.add_argument("--timeout", type=float, default=60.0, help="Per-synthesis timeout in seconds")
    return parser.parse_args()

def get_wav_duration(path: str) -> float:
    try:
        with wave.open(path, 'rb') as wf:
            return wf.getnframes() / wf.getframerate()
    except Exception:
        return 0.0

async def synthesize_optimized(client, text, ref_config, audio_source, preset_name, repetition_penalty, sovits_url, timeout):
    preset = PRESETS[preset_name]

    # Resolve reference audio path
    ref_path = ref_config["ref_audio_path"]
    if audio_source == "raw":
        if ref_path.endswith(".wav") and not ref_path.endswith("_raw.wav"):
            ref_path = ref_path[:-4] + "_raw.wav"

    payload = {
        "text": text,
        "text_lang": "ja",
        "ref_audio_path": ref_path,
        "prompt_text": ref_config["prompt_text"],
        "prompt_lang": "ja",
        "media_type": "wav",
    }

    # Inject preset parameters
    for k, v in preset.items():
        if v is not None:
            payload[k] = v

    # Inject repetition penalty, exclude streaming mode
    if repetition_penalty is not None:
        payload["repetition_penalty"] = float(repetition_penalty)

    payload.pop("streaming_mode", None)

    url = sovits_url.rstrip("/") + "/tts"
    response = await client.post(url, json=payload, timeout=timeout)
    if response.status_code != 200:
        raise RuntimeError(f"GPT-SoVITS {response.status_code}: {response.text}")
    return response.content

async def main():
    args = parse_args()

    out_dir = args.out_dir or os.path.join(os.path.dirname(os.path.abspath(__file__)), "tts_output", "optimization")
    os.makedirs(out_dir, exist_ok=True)

    # Define test parameters based on mode
    if args.quick:
        sources = ["raw"]
        presets = ["olv"]
        rep_penalties = [1.35, 1.5]
        emotions = ["embarrassed"]
        test_matrix = [
            ("emb_def", "ち、違うんだからね！"),
            ("normal", "あなたに会いに来たの、岡部倫太郎さん。")
        ]
        print("[NOTE] normalized baseline = 当前生产行为（已知 embarrassed 有问题），本次仅测 raw 变体")
    else:
        sources = ["normalized", "raw"]
        presets = ["olv", "tuned"]
        rep_penalties = [1.35, 1.5, 1.8]
        emotions = ["embarrassed", "neutral"]
        test_matrix = [
            ("emb_def", "ち、違うんだからね！"),
            ("normal", "あなたに会いに来たの、岡部倫太郎さん。")
        ]

    print("=" * 72)
    print("TTS Parameters Optimization Matrix Scan")
    print(f"  SoVITS URL : {args.sovits_url}")
    print(f"  Timeout    : {args.timeout}s")
    print(f"  Output dir : {out_dir}")
    print(f"  Sources    : {sources}")
    print(f"  Presets    : {presets}")
    print(f"  Rep Penalties: {rep_penalties}")
    print(f"  Emotions   : {emotions}")
    print(f"  Quick mode : {'ON' if args.quick else 'OFF'}")
    print("=" * 72)

    client = httpx.AsyncClient(trust_env=False)
    results = []

    # To compute average durations per (emotion, text_label)
    durations_map = {}

    try:
        for source in sources:
            for preset in presets:
                for rp in rep_penalties:
                    for emotion in emotions:
                        ref = EMOTION_REF_MAP.get(emotion)
                        if ref is None:
                            continue

                        for label, text in test_matrix:
                            filename = f"{source}_{preset}_rp{rp}_{emotion}_{label}.wav"
                            filepath = os.path.join(out_dir, filename)

                            print(f"[{source}/{preset}/rp={rp}][{emotion}][{label}] synthesizing...", end=" ", flush=True)
                            start = time.monotonic()
                            status = "OK"
                            size_kb = 0.0
                            peak = 0
                            duration = 0.0
                            err_msg = ""

                            try:
                                audio_bytes = await synthesize_optimized(
                                    client, text, ref, source, preset, rp, args.sovits_url, args.timeout
                                )
                                elapsed = time.monotonic() - start
                                with open(filepath, "wb") as f:
                                    f.write(audio_bytes)
                                size_kb = len(audio_bytes) / 1024
                                peak = compute_peak(filepath)
                                duration = get_wav_duration(filepath)

                                # Store for average duration calculation
                                durations_key = (emotion, label)
                                durations_map.setdefault(durations_key, []).append(duration)

                                print(f"OK ({elapsed:.1f}s, {size_kb:.1f}KB, dur={duration:.2f}s, peak={peak})")
                            except httpx.HTTPStatusError as e:
                                elapsed = time.monotonic() - start
                                status = "ERROR"
                                err_msg = f"HTTP {e.response.status_code}: {e.response.text[:100]}"
                                print(f"ERROR: {err_msg}")
                            except Exception as e:
                                elapsed = time.monotonic() - start
                                status = "ERROR"
                                err_msg = str(e)[:100]
                                print(f"ERROR: {err_msg}")

                            results.append({
                                "source": source,
                                "preset": preset,
                                "repetition_penalty": rp,
                                "emotion": emotion,
                                "label": label,
                                "text": text,
                                "status": status,
                                "size_kb": size_kb,
                                "duration": duration,
                                "peak": peak,
                                "file": filename if status == "OK" else "-",
                                "error": err_msg
                            })

        # Compute averages and flag anomalies
        avg_durations = {}
        for key, durs in durations_map.items():
            if durs:
                avg_durations[key] = sum(durs) / len(durs)

        # Final Summary
        print()
        print("=" * 90)
        print("Summary - TTS Parameter Optimization Scan Results")
        print(f"{'Source':<12} {'Preset':<6} {'RP':<4} {'Emotion':<12} {'Label':<8} {'Status':<6} {'Size(KB)':>8} {'Dur(s)':>6} {'Peak':>6} {'Flags'}")
        print("-" * 90)

        ok_count = 0
        for r in results:
            if r["status"] == "OK":
                ok_count += 1
                flags = []
                # Check heuristic 1: Duration > 2x average
                avg_dur = avg_durations.get((r["emotion"], r["label"]), 0.0)
                if avg_dur > 0 and r["duration"] > 2 * avg_dur:
                    flags.append("WARN_REPETITION")
                # Check heuristic 2: Duration > 15s and text length < 15 chars
                if r["duration"] > 15.0 and len(r["text"]) < 15:
                    flags.append("WARN_NOISE")

                flags_str = ",".join(flags) if flags else "-"
                print(f"{r['source']:<12} {r['preset']:<6} {r['repetition_penalty']:<4} {r['emotion']:<12} {r['label']:<8} {r['status']:<6} {r['size_kb']:>7.1f}K {r['duration']:>5.2f}s {r['peak']:>6} {flags_str}")
            else:
                print(f"{r['source']:<12} {r['preset']:<6} {r['repetition_penalty']:<4} {r['emotion']:<12} {r['label']:<8} {r['status']:<6} {'-':>8} {'-':>6} {'-':>6} {r['error']}")

        print("-" * 90)
        print(f"Total Runs: {len(results)}  |  Successful: {ok_count}  |  Failed: {len(results) - ok_count}")
        print("=" * 90)

    finally:
        await client.aclose()

if __name__ == "__main__":
    asyncio.run(main())
