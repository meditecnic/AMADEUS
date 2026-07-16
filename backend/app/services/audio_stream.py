"""Incremental WAV-header demuxing for GPT-SoVITS streaming_mode=2."""

from __future__ import annotations

import struct
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AudioFormat:
    channels: int
    sample_rate: int
    sample_width: int


class WavStreamDemuxer:
    def __init__(self):
        self._buffer = bytearray()
        self.format: AudioFormat | None = None
        self._header_done = False

    def feed(self, chunk: bytes) -> list[bytes]:
        if self._header_done:
            return [chunk] if chunk else []
        self._buffer.extend(chunk)
        if len(self._buffer) < 12:
            return []
        if self._buffer[:4] != b"RIFF" or self._buffer[8:12] != b"WAVE":
            raise ValueError("GPT-SoVITS stream did not begin with a RIFF/WAVE header")
        offset = 12
        fmt = None
        while len(self._buffer) >= offset + 8:
            chunk_id = bytes(self._buffer[offset:offset + 4])
            chunk_size = struct.unpack_from("<I", self._buffer, offset + 4)[0]
            data_start = offset + 8
            if chunk_id == b"fmt ":
                if len(self._buffer) < data_start + chunk_size:
                    return []
                audio_format, channels, sample_rate, _, _, bits = struct.unpack_from("<HHIIHH", self._buffer, data_start)
                if audio_format != 1:
                    raise ValueError(f"only PCM WAV is supported, got format={audio_format}")
                fmt = AudioFormat(channels=channels, sample_rate=sample_rate, sample_width=bits // 8)
            if chunk_id == b"data":
                if fmt is None:
                    raise ValueError("WAV data chunk appeared before fmt chunk")
                self.format = fmt
                self._header_done = True
                payload = bytes(self._buffer[data_start:])
                self._buffer.clear()
                return [payload] if payload else []
            if len(self._buffer) < data_start + chunk_size:
                return []
            offset = data_start + chunk_size + (chunk_size & 1)
        return []
