"""One-shot real sidecar smoke for TTS input preflight. Not a release test."""
from __future__ import annotations

import asyncio
import sys

from app.domain.segments import AudioErrorCode, prepare_tts_text
from app.services.tts_queue import TTSServiceError, tts_manager

INCIDENT = (
    "嫌身無等業単美目民』―ィ閉定状る青亀若笑自伝界本国象券正門仕──県的幕・"
    "切終性石葉門造長音数輪組羽管若権言希号存圏武可徳原容超管沢号週高印情降完七装帰社郡敵道労里員野行容辞体果割。"
)
OK = "そんなこと、あるわけないでしょ？"


async def main() -> int:
    bad = prepare_tts_text(INCIDENT)
    print(f"preflight_incident={bad.error}")
    if bad.error is not AudioErrorCode.INVALID_LANGUAGE:
        print("FAIL: incident must be invalid_language")
        return 1

    good = prepare_tts_text(OK)
    print(f"preflight_ok={good.error} text={good.text!r}")
    if good.error is not None:
        print("FAIL: valid Japanese must pass")
        return 1

    try:
        await tts_manager.synthesize_async(INCIDENT, "neutral")
        print("FAIL: incident reached synthesis")
        return 1
    except TTSServiceError as exc:
        print(f"incident_blocked code={exc.code}")

    audio = await tts_manager.synthesize_async(OK, "neutral")
    print(f"ok_audio_bytes={len(audio)} header={audio[:4]!r}")
    if len(audio) < 100 or audio[:4] != b"RIFF":
        print("FAIL: expected non-empty WAV")
        return 1

    await tts_manager.close_client()
    print("SMOKE_OK")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
