from __future__ import annotations

import argparse
import json
import time
import urllib.parse
import urllib.request
from pathlib import Path


DEFAULT_ROOT = Path(
    r"D:\谷歌下载\gpt\GPT-SoVITS-v2pro-20250604\GPT-SoVITS-v2pro-20250604"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Wait for GPT-SoVITS and load the Amadeus V2Pro weights."
    )
    parser.add_argument("--url", default="http://127.0.0.1:9880")
    parser.add_argument(
        "--gpt-weights",
        default=str(DEFAULT_ROOT / "GPT_weights" / "红莉栖语音1.ckpt"),
    )
    parser.add_argument(
        "--sovits-weights",
        default=str(DEFAULT_ROOT / "SoVITS_weights" / "红莉栖语音2.pth"),
    )
    parser.add_argument("--startup-timeout", type=float, default=180.0)
    return parser.parse_args()


def request_json(url: str, *, timeout: float) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        body = response.read().decode("utf-8", errors="replace")
        if response.status != 200:
            raise RuntimeError(f"HTTP {response.status}")
    return json.loads(body) if body else {}


def wait_until_compatible(base_url: str, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"{base_url}/openapi.json", timeout=2) as response:
                body = response.read().decode("utf-8", errors="replace")
                if response.status == 200 and '"/tts"' in body:
                    return
        except Exception as exc:  # endpoint is expected to be absent while CUDA warms up
            last_error = exc
        time.sleep(1)
    raise RuntimeError("GPT-SoVITS did not become compatible in time") from last_error


def load_weight(base_url: str, endpoint: str, path: str) -> None:
    resolved = Path(path)
    if not resolved.is_file():
        raise FileNotFoundError(resolved)
    query = urllib.parse.urlencode({"weights_path": str(resolved)})
    payload = request_json(f"{base_url}/{endpoint}?{query}", timeout=180)
    if payload.get("message") != "success":
        raise RuntimeError(f"{endpoint} rejected the configured weights")


def main() -> None:
    args = parse_args()
    base_url = args.url.rstrip("/")
    print("[Amadeus TTS] Waiting for the V2Pro endpoint...", flush=True)
    wait_until_compatible(base_url, args.startup_timeout)
    print("[Amadeus TTS] Loading GPT weights...", flush=True)
    load_weight(base_url, "set_gpt_weights", args.gpt_weights)
    print("[Amadeus TTS] Loading SoVITS V2Pro weights...", flush=True)
    load_weight(base_url, "set_sovits_weights", args.sovits_weights)
    print("[Amadeus TTS] All weights loaded successfully.", flush=True)


if __name__ == "__main__":
    main()
