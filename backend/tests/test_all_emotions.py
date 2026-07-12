# backend/tests/test_all_emotions.py
# Usage: python -m tests.test_all_emotions [--emotion embarrassed] [--preset baseline|olv|tuned|all] [--quick] [--sovits-url http://127.0.0.1:9880] [--out-dir backend/tests/tts_output] [--timeout 60]

import argparse
import asyncio
import os
import struct
import sys
import time
import httpx

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.tts_queue import TTSQueueManager, EMOTION_REF_MAP, TTSServiceError

PRESETS = {
    "baseline": {
        "top_k": 5, "top_p": 0.8, "temperature": 0.4, "speed": 1.0,
        "text_split_method": None, "batch_size": None, "streaming_mode": None,
    },
    "olv": {
        "top_k": None, "top_p": None, "temperature": None, "speed": None,
        "text_split_method": "cut5", "batch_size": "1", "streaming_mode": None,
    },
    "tuned": {
        # OLV 基础 + 放宽采样（top_k↑ top_p↑ temp↑，减少 embarrassed 重复）
        "top_k": 10, "top_p": 0.9, "temperature": 0.6, "speed": 1.0,
        "text_split_method": "cut5", "batch_size": "1", "streaming_mode": None,
    },
}

TEST_MATRIX = [
    ("normal",    "あなたに会いに来たの、岡部倫太郎さん。"),
    ("normal2",   "超重力による無限圧縮、およびカーブラックホール内の特異点通過に耐えられなかったと思われる。"),
    ("short_te",  "て"),
    ("short_e",   "えっ"),
    ("short_a",   "あ"),
    ("ellipsis",  "…"),
    ("mixed",     "えっと… て。 それでね。"),
    ("emb_def",   "ち、違うんだからね！"),
    ("tsun_def",  "誰が助手よ！私は牧瀬紅莉栖よ。"),
]

QUICK_EMOTIONS = ["embarrassed", "intellectual", "sad", "surprised", "tsundere"]
QUICK_LABELS = {"normal", "short_te", "short_e", "mixed", "emb_def"}


def parse_args():
    parser = argparse.ArgumentParser(description="TTS emotion diagnostic — synthesize all 9 emotions against GPT-SoVITS")
    parser.add_argument("--emotion", type=str, default=None, help="Test single emotion only (default: all 9)")
    parser.add_argument("--preset", type=str, default="all", choices=list(PRESETS.keys()) + ["all"],
                        help="Parameter preset: baseline|olv|tuned|all (default: all)")
    parser.add_argument("--quick", action="store_true", help="Only test problem emotions × key texts")
    parser.add_argument("--sovits-url", type=str, default="http://127.0.0.1:9880", help="GPT-SoVITS base URL")
    parser.add_argument("--out-dir", type=str, default=None, help="Output directory (default: backend/tests/tts_output)")
    parser.add_argument("--timeout", type=float, default=60.0, help="Per-synthesis timeout in seconds (default: 60)")
    return parser.parse_args()


def compute_peak(path: str) -> int:
    with open(path, "rb") as f:
        data = f.read()
    idx = data.find(b"data")
    if idx < 0:
        return 0
    audio = data[idx + 8:]
    if len(audio) < 2:
        return 0
    n = len(audio) // 2
    samples = struct.unpack("<" + "h" * n, audio[:n * 2])
    return max(abs(s) for s in samples) if samples else 0


async def synthesize_direct(client, text, ref_config, preset_name, sovits_url, timeout):
    preset = PRESETS[preset_name]
    payload = {
        "text": text, "text_lang": "ja",
        "ref_audio_path": ref_config["ref_audio_path"],
        "prompt_text": ref_config["prompt_text"], "prompt_lang": "ja",
        "media_type": "wav",
    }
    for k, v in preset.items():
        if v is not None:
            payload[k] = v
    url = sovits_url.rstrip("/") + "/tts"
    response = await client.post(url, json=payload, timeout=timeout)
    if response.status_code != 200:
        raise RuntimeError(f"GPT-SoVITS {response.status_code}")
    return response.content


async def main():
    args = parse_args()

    out_dir = args.out_dir or os.path.join(os.path.dirname(os.path.abspath(__file__)), "tts_output")
    os.makedirs(out_dir, exist_ok=True)

    if args.preset == "all":
        preset_names = list(PRESETS.keys())
    else:
        preset_names = [args.preset]

    all_emotions = list(EMOTION_REF_MAP.keys())
    emotions = [args.emotion] if args.emotion else all_emotions

    if args.quick:
        emotions = [e for e in emotions if e in QUICK_EMOTIONS]
        test_matrix = [(l, t) for l, t in TEST_MATRIX if l in QUICK_LABELS]
    else:
        test_matrix = list(TEST_MATRIX)

    print("=" * 72)
    print("TTS Emotion Diagnostic — Parameter Matrix Compare")
    print(f"  SoVITS URL : {args.sovits_url}")
    print(f"  Timeout    : {args.timeout}s")
    print(f"  Output dir : {out_dir}")
    print(f"  Presets    : {preset_names}")
    print(f"  Emotions   : {emotions}")
    print(f"  Test cases : {len(test_matrix)}")
    print(f"  Quick mode : {'ON' if args.quick else 'OFF'}")
    print("=" * 72)

    client = httpx.AsyncClient()
    all_results = {}

    try:
        for preset_name in preset_names:
            preset_dir = os.path.join(out_dir, preset_name)
            os.makedirs(preset_dir, exist_ok=True)
            results = []

            print(f"\n--- Preset: {preset_name} ---")

            for emotion in emotions:
                ref = EMOTION_REF_MAP.get(emotion)
                if ref is None:
                    print(f"[SKIP] {emotion}: no reference audio")
                    continue

                for idx, (label, text) in enumerate(test_matrix):
                    fname = f"{emotion}_{idx}_{label}.wav"
                    fpath = os.path.join(preset_dir, fname)

                    print(f"[{preset_name}/{emotion}][{label}] synthesizing...", end=" ", flush=True)
                    start = time.monotonic()
                    status = "OK"
                    size_kb = 0
                    peak = 0
                    peak_flag = ""

                    try:
                        audio_bytes = await synthesize_direct(
                            client, text, ref, preset_name, args.sovits_url, args.timeout,
                        )
                        elapsed = time.monotonic() - start
                        with open(fpath, "wb") as f:
                            f.write(audio_bytes)
                        size_kb = len(audio_bytes) / 1024
                        peak = compute_peak(fpath)
                        peak_flag = " ⚠PEAK" if peak > 32000 else ""
                        print(f"OK ({elapsed:.1f}s, {size_kb:.1f}KB, peak={peak}{peak_flag})")
                    except asyncio.TimeoutError:
                        elapsed = time.monotonic() - start
                        status = "TIMEOUT"
                        print(f"TIMEOUT ({elapsed:.1f}s)")
                    except RuntimeError as e:
                        elapsed = time.monotonic() - start
                        status = "ERROR"
                        print(f"ERROR: {e}")
                    except Exception as e:
                        elapsed = time.monotonic() - start
                        status = "ERROR"
                        print(f"ERROR ({type(e).__name__}): {e}")

                    results.append({
                        "emotion": emotion,
                        "label": label,
                        "status": status,
                        "size_kb": size_kb,
                        "file": fname if status == "OK" else "-",
                        "peak": peak,
                        "peak_flag": peak_flag,
                    })

            all_results[preset_name] = results

            ok_count = sum(1 for r in results if r["status"] == "OK")
            timeout_count = sum(1 for r in results if r["status"] == "TIMEOUT")
            error_count = sum(1 for r in results if r["status"] == "ERROR")
            print(f"\n[{preset_name}] Total: {len(results)}  |  OK: {ok_count}  |  TIMEOUT: {timeout_count}  |  ERROR: {error_count}")

    finally:
        await client.aclose()

    for preset_name, results in all_results.items():
        print()
        print("=" * 80)
        print(f"Summary — {preset_name}")
        print(f"{'Emotion':<14} {'Label':<12} {'Status':<8} {'Size(KB)':>9} {'Peak':>8} {'File'}")
        print("-" * 80)
        ok_count = 0
        timeout_count = 0
        error_count = 0
        for r in results:
            if r["status"] == "OK":
                ok_count += 1
            elif r["status"] == "TIMEOUT":
                timeout_count += 1
            else:
                error_count += 1
            peak_str = f"{r['peak']}{r['peak_flag']}" if r["status"] == "OK" else "-"
            print(f"{r['emotion']:<14} {r['label']:<12} {r['status']:<8} {r['size_kb']:>8.1f}K {peak_str:>8} {r['file']}")
        print("-" * 80)
        print(f"Total: {len(results)}  |  OK: {ok_count}  |  TIMEOUT: {timeout_count}  |  ERROR: {error_count}")
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(main())
