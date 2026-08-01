"""Fail-closed TTS source preflight — incident-shaped garbage must never hit GPT-SoVITS."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.domain.segments import (
    MAX_TTS_SOURCE_CODEPOINTS,
    AudioErrorCode,
    Segment,
    SegmentPipeline,
    TurnIdentity,
    prepare_tts_text,
)
from app.services.tts_queue import TTSQueueManager, TTSServiceError


# Real-incident fixtures from the 2026-07 P0 TTS input pollution report.
INCIDENT_RANDOM_1 = (
    "嫌身無等業単美目民』―ィ閉定状る青亀若笑自伝界本国象券正門仕──県的幕・切終性石葉門造長音数輪組羽管若権言希号存圏武可徳原容超管沢号週高印情降完七装帰社郡敵道労里員野行容辞体果割。"
)
INCIDENT_RANDOM_2 = (
    "まずグ＿読み終えた第一再試験にリM仕　　シブコードともノ工手の工専持「内想事由イ含系ゼそ時む『宇街角城五号厦共首町丁耳次＿態・R D A当内領版』ビ食一彗限性何記＿本運命二至る抽舌人五光お代　宙惑領格マダ刻　代壪義。"
)
INCIDENT_RANDOM_3 = (
    "相模玉行答到景非／少式音念響路郡第有音表社歳担話路静回令サ好確使調状界義域無火干村対題計承後字読数回測索畂頭各パ申紀包曜在改メ防第体多遠季法表白毛止利巨市照店連織各退全者管低算距序寄表引景係例東革求何土深一武記都会。"
)
# Real Workstation→SoVITS log (2026-07-22): still reached /tts 200 under the prior gate.
REAL_LOG_COLLAPSE = (
    "ハキム、フィモンの公的同中心凝択ハfleeとま悪いい ―改newidInfoどうスタも"
    "子おろしてましせただしま角環プロｯtま字です、とのデータつきはしてい 。"
)


def test_preflight_accepts_kanji_only_japanese_identity_lines():
    """LANG-GATE-RELAX-01: kana-less natural Japanese must not be invalid_language."""
    for line in ("岡部倫太郎、通称鳳凰院凶真。", "岡部倫太郎。"):
        prepared = prepare_tts_text(line)
        assert prepared.error is None, (line, prepared.error)


def test_preflight_keeps_canonical_text_without_pronunciation_rewrite():
    """TTS-CANONICAL-PRONUNCIATION-LEXICON-01: preflight output stays canonical.

    The pronunciation surface is applied only at the GPT-SoVITS HTTP boundary;
    prepare_tts_text must never rewrite proper nouns itself.
    """
    src = "牧瀬紅莉栖、鳳凰院凶真と岡部倫太郎。Amadeusと未来ガジェット研究所について話す。"
    prepared = prepare_tts_text(src)
    assert prepared.error is None
    assert "牧瀬紅莉栖" in prepared.text
    assert "Amadeus" in prepared.text
    assert "マキセクリス" not in prepared.text

LEGIT_LATIN_EXAMPLES = [
    "大丈夫？",
    "そんなこと、あるわけないでしょ？",
    "Mackenzieの肝機能活性データをPDFにする？",
    "APIの仕様を確認してから答えるわ。",
    "DNAの実験結果について説明するわ。",
    "ハキムという名前について調べるわ。",
]


def test_incident_random_strings_are_rejected_before_sidecar():
    for fixture in (INCIDENT_RANDOM_1, INCIDENT_RANDOM_2, INCIDENT_RANDOM_3, REAL_LOG_COLLAPSE):
        prepared = prepare_tts_text(fixture)
        assert prepared.error is AudioErrorCode.INVALID_LANGUAGE, fixture[:24]


def test_real_log_collapse_fixture_is_rejected():
    """Exact SoVITS-bound string from the latest live incident must fail closed."""
    prepared = prepare_tts_text(REAL_LOG_COLLAPSE)
    assert prepared.error is not None
    assert prepared.error is AudioErrorCode.INVALID_LANGUAGE
    # Split shape from GPT-SoVITS cut log must also fail.
    part1 = (
        "ハキム、フィモンの公的同中心凝択ハfleeとま悪いい ―改newidInfoどうスタも"
        "子おろしてましせただしま角環プロｯtま字です、。"
    )
    part2 = "とのデータつきはしてい 。"
    assert prepare_tts_text(part1).error is AudioErrorCode.INVALID_LANGUAGE
    # Trailing cut fragment may look like incomplete Japanese; full/cut-1 are the
    # must-reject shapes that actually hit the sidecar in the live log.
    _ = part2


def test_valid_japanese_still_passes_preflight():
    prepared = prepare_tts_text("そんなこと、あるわけないでしょ？")
    assert prepared.error is None
    assert prepared.text == "そんなこと、あるわけないでしょ？"
    # Embarrassed-style short legitimate speech remains eligible.
    ok = prepare_tts_text("別に確信があって言ったわけじゃない。")
    assert ok.error is None
    # Kanji-only short Japanese interjection (no kana) must remain speakable.
    assert prepare_tts_text("大丈夫？").error is None
    # Brief kanji-only acknowledgements (LANG-GATE-RELAX-01).
    assert prepare_tts_text("了解。").error is None
    for example in LEGIT_LATIN_EXAMPLES:
        assert prepare_tts_text(example).error is None, example


def test_bare_ellipsis_is_soft_skip_not_hard_language_fail_shape():
    from app.domain.segments import should_hard_reject_tts_source

    prepared = prepare_tts_text("……")
    assert prepared.error is AudioErrorCode.ACTION_ONLY
    assert should_hard_reject_tts_source("……") is False
    assert should_hard_reject_tts_source(REAL_LOG_COLLAPSE) is True


def test_chinese_only_rejected_in_default_japanese_mode():
    prepared = prepare_tts_text("现在是2026年，搜索结果没有冠军信息。")
    assert prepared.error is AudioErrorCode.INVALID_LANGUAGE


def test_ambiguous_han_only_chinese_is_rejected_before_sidecar():
    for line in (
        "笑话……",
        "一加一等于二。",
        "今天心情很好。",
        "這個笑話很好笑。",
        "這個設定很重要。",
    ):
        prepared = prepare_tts_text(line)
        assert prepared.error is AudioErrorCode.INVALID_LANGUAGE, line

    # Shared-Han single-character fragments are not Chinese proof. They may be
    # skipped as too short, but must not be classified as invalid language.
    for line in ("話。", "来。", "行。", "見。", "食。"):
        prepared = prepare_tts_text(line)
        assert prepared.error is not AudioErrorCode.INVALID_LANGUAGE, line

    for line in (
        "了解。",
        "大丈夫？",
        "笑話……",
        "一文目。",
        "岡部倫太郎。",
        "岡部倫太郎、通称鳳凰院凶真。",
        "未来道具研究所。",
        "牧瀬紅莉栖。",
        "助手。",
        "世界線収束範囲。",
    ):
        prepared = prepare_tts_text(line)
        assert prepared.error is None, (line, prepared.error)


def test_random_ja_zh_mix_rejected():
    prepared = prepare_tts_text("这就是だ相模玉行答到景非／少式音念響路")
    assert prepared.error is AudioErrorCode.INVALID_LANGUAGE


def test_code_switch_quoted_chinese_terms_pass_preflight():
    """LANG-GATE-CODE-SWITCH-RELAX-02: quoted SC terms inside JA speech are speakable."""
    lines = (
        "日本語なら「研」とか「研究室」、中国語なら「实验室」とか「研究室」って場面で使い…",
        "中国語の正式な表記としては『命运石之门』が広く使われているわ。",
        "中国語の公式表記としては『命运石之门』が一般的ね。",
        "公式の中国語表記は『命运石之门』。",
        "作品タイトルは STEINS;GATE（命运石之门） として知られているわ。",
    )
    for line in lines:
        prepared = prepare_tts_text(line)
        assert prepared.error is None, (line, prepared.error)


def test_code_switch_bare_chinese_still_fails_preflight():
    for bad in ("命运石之门。", "实验室。"):
        assert prepare_tts_text(bad).error is AudioErrorCode.INVALID_LANGUAGE


def test_overlong_text_is_rejected_without_split_into_sidecar():
    long_ja = "あ" * (MAX_TTS_SOURCE_CODEPOINTS + 1) + "。"
    prepared = prepare_tts_text(long_ja)
    assert prepared.error is AudioErrorCode.TEXT_TOO_LONG


@pytest.mark.asyncio
async def test_invalid_language_never_calls_gpt_sovits_http():
    tts = TTSQueueManager()
    tts.emotion_ref_map = {
        "neutral": {"ref_audio_path": "/fake/neutral.wav", "prompt_text": "prompt"},
    }
    mock_client = MagicMock()
    mock_client.is_closed = False
    mock_client.post = AsyncMock()
    tts.client = mock_client

    with pytest.raises(TTSServiceError) as exc:
        await tts.synthesize_async(INCIDENT_RANDOM_1, "neutral")

    assert exc.value.code is AudioErrorCode.INVALID_LANGUAGE
    mock_client.post.assert_not_awaited()


@pytest.mark.asyncio
async def test_real_log_collapse_never_calls_gpt_sovits_http():
    tts = TTSQueueManager()
    tts.emotion_ref_map = {
        "neutral": {"ref_audio_path": "/fake/neutral.wav", "prompt_text": "prompt"},
    }
    mock_client = MagicMock()
    mock_client.is_closed = False
    mock_client.post = AsyncMock()
    tts.client = mock_client

    with pytest.raises(TTSServiceError) as exc:
        await tts.synthesize_async(REAL_LOG_COLLAPSE, "neutral")

    assert exc.value.code is AudioErrorCode.INVALID_LANGUAGE
    mock_client.post.assert_not_awaited()


@pytest.mark.asyncio
async def test_text_too_long_never_calls_gpt_sovits_http():
    tts = TTSQueueManager()
    tts.emotion_ref_map = {
        "neutral": {"ref_audio_path": "/fake/neutral.wav", "prompt_text": "prompt"},
    }
    mock_client = MagicMock()
    mock_client.is_closed = False
    mock_client.post = AsyncMock()
    tts.client = mock_client

    with pytest.raises(TTSServiceError) as exc:
        await tts.synthesize_async("あ" * (MAX_TTS_SOURCE_CODEPOINTS + 20), "neutral")

    assert exc.value.code is AudioErrorCode.TEXT_TOO_LONG
    mock_client.post.assert_not_awaited()


@pytest.mark.asyncio
async def test_concurrent_segments_keep_immutable_source_text():
    events: list[dict] = []
    synthesized: list[tuple[str, str]] = []

    async def translate(text: str) -> str:
        return f"译:{text}"

    async def synthesize(text: str, emotion: str) -> bytes:
        # Force reverse completion order.
        await asyncio.sleep(0.03 if text.startswith("一") else 0.0)
        synthesized.append((text, emotion))
        return f"audio:{text}".encode("utf-8")

    pipeline = SegmentPipeline(
        TurnIdentity("c", "t", 7),
        emit=events.append,
        translate=translate,
        synthesize=synthesize,
        tts_concurrency=2,
    )
    await pipeline.run([
        Segment(0, "一。", "neutral"),
        Segment(1, "二。", "embarrassed"),
    ])

    assert set(synthesized) == {("一。", "neutral"), ("二。", "embarrassed")}
    assert ("译:一。", "neutral") not in synthesized
    assert all(not item[0].startswith("译:") for item in synthesized)
    ready = [event for event in events if event["type"] == "segment.ready"]
    assert [event["ja"] for event in ready] == ["一。", "二。"]
    assert [event["zh"] for event in ready] == ["译:一。", "译:二。"]


@pytest.mark.asyncio
async def test_retry_reuses_same_immutable_source_text():
    attempts: list[str] = []

    async def translate(text: str) -> str:
        return "訳"

    async def synthesize(text: str, emotion: str) -> bytes:
        attempts.append(text)
        if len(attempts) == 1:
            from app.domain.segments import TransientSegmentError
            raise TransientSegmentError("timeout")
        return b"ok"

    pipeline = SegmentPipeline(
        TurnIdentity("c", "t", 1),
        emit=lambda _event: None,
        translate=translate,
        synthesize=synthesize,
        tts_retries=1,
    )
    await pipeline.run([Segment(0, "実験を続けるわ。", "intellectual")])
    assert attempts == ["実験を続けるわ。", "実験を続けるわ。"]


@pytest.mark.asyncio
async def test_rejected_segment_does_not_block_later_valid_segment():
    events: list[dict] = []

    async def translate(text: str) -> str:
        return "ok"

    async def synthesize(text: str, emotion: str) -> bytes:
        prepared = prepare_tts_text(text)
        if prepared.error is not None:
            raise TTSServiceError(prepared.error)
        return b"wav"

    pipeline = SegmentPipeline(
        TurnIdentity("c", "t", 3),
        emit=events.append,
        translate=translate,
        synthesize=synthesize,
        tts_concurrency=2,
    )
    await pipeline.run([
        Segment(0, INCIDENT_RANDOM_1, "neutral"),
        Segment(1, "次の実験に移りましょう。", "neutral"),
    ])

    audio_errors = [e for e in events if e["type"] == "segment.audio_error"]
    audio_ok = [e for e in events if e["type"] == "segment.audio"]
    assert any(e.get("segment_id") == 0 for e in audio_errors)
    assert any(e.get("segment_id") == 1 for e in audio_ok)
    assert any(
        e.get("reason") == AudioErrorCode.INVALID_LANGUAGE.value
        for e in audio_errors
    )


@pytest.mark.asyncio
async def test_worker_queue_rejects_garbage_without_http():
    tts = TTSQueueManager()
    tts.emotion_ref_map = {
        "neutral": {"ref_audio_path": "/fake/neutral.wav", "prompt_text": "prompt"},
    }
    mock_client = MagicMock()
    mock_client.is_closed = False
    mock_client.post = AsyncMock()
    tts.client = mock_client
    delivered: list[dict] = []

    async def cb(seq: int, payload: dict) -> None:
        delivered.append(payload)

    tts.reset_sequence("session-a", cb)
    await tts.synthesize_and_queue("session-a", INCIDENT_RANDOM_3, "neutral")
    await tts.gather_pending("session-a")

    assert delivered
    assert delivered[0]["error"] == AudioErrorCode.INVALID_LANGUAGE.value
    assert delivered[0]["audio"] is None
    mock_client.post.assert_not_awaited()
