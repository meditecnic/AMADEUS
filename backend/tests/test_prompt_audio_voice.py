from __future__ import annotations

import struct

import pytest

from app.services.audio_stream import WavStreamDemuxer
from app.services.memory import RetrievedMemory
from app.services.prompt_compiler import PromptCompiler, PromptInputs
from app.services.soul_engine import soul_engine
from app.services.speech import SpeechService


def _wav_header(data_size=8, channels=1, sample_rate=16000, bits=16):
    byte_rate = sample_rate * channels * bits // 8
    block = channels * bits // 8
    return b"RIFF" + struct.pack("<I", 36 + data_size) + b"WAVEfmt " + struct.pack("<IHHIIHH", 16, 1, channels, sample_rate, byte_rate, block, bits) + b"data" + struct.pack("<I", data_size)


def test_wav_demuxer_handles_split_header_once():
    demux = WavStreamDemuxer()
    header = _wav_header()
    assert demux.feed(header[:9]) == []
    assert demux.feed(header[9:] + b"1234") == [b"1234"]
    assert demux.format.sample_rate == 16000
    assert demux.feed(b"5678") == [b"5678"]


def test_prompt_budget_preserves_identity_and_current_message():
    compiler = PromptCompiler(input_budget=800)
    facts = [RetrievedMemory(id=i, kind="core", content=("fact " + str(i) + " x" * 80), score=1.0) for i in range(30)]
    prompt = compiler.compile(PromptInputs(
        worldline="steins_gate",
        base_identity="IMMUTABLE IDENTITY",
        emotion=soul_engine.profile("steins_gate").baselines,
        core_facts=facts,
        episodic=[],
        web_evidence=[],
        working_summary="summary " * 500,
        recent_history=[{"role": "user", "content": "old " * 500}],
        current_user_message="CURRENT MESSAGE MUST SURVIVE",
    ))
    assert "IMMUTABLE IDENTITY" in prompt
    assert "CURRENT MESSAGE MUST SURVIVE" in prompt
    assert compiler.token_counter(prompt) <= 800


def test_pcm_speech_energy_detection():
    silence = b"\x00\x00" * 1000
    speech = struct.pack("<" + "h" * 1000, *([10000] * 1000))
    assert SpeechService.pcm_has_speech(silence) is False
    assert SpeechService.pcm_has_speech(speech) is True
