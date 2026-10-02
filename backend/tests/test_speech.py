from types import SimpleNamespace
import sys

import numpy as np
import pytest

from app.services.speech import SpeechService


@pytest.mark.parametrize('valid', [False, True])
def test_download_verifies_digest_before_publishing(tmp_path, monkeypatch, valid):
    import hashlib
    import io
    from app.services import speech

    payload = b'synthetic model bytes'
    monkeypatch.setattr(speech.urllib.request, 'urlopen', lambda *args, **kwargs: io.BytesIO(payload))
    monkeypatch.setattr(speech, 'DOWNLOAD_SHA256', {speech.MODEL_URL: hashlib.sha256(payload if valid else b'other').hexdigest()}, raising=False)
    destination = tmp_path / 'model.tar.bz2'
    if valid:
        speech._download(speech.MODEL_URL, destination)
        assert destination.read_bytes() == payload
    else:
        with pytest.raises(ValueError, match='SHA-256'):
            speech._download(speech.MODEL_URL, destination)
        assert not destination.exists()
    assert not destination.with_suffix('.bz2.part').exists()


def test_readiness_without_sherpa_does_not_download(monkeypatch):
    monkeypatch.setitem(sys.modules, 'sherpa_onnx', None)
    status = SpeechService().readiness()
    assert status['ok'] is False
    assert status['degraded'] is True
    assert status['error'] == 'sherpa-onnx/SenseVoice is not installed'


@pytest.mark.parametrize('raw,emotion_tag,expected', [
    ('今日は晴れです。', '', ('今日は晴れです。', 'NEUTRAL', 0.5)),
    ('<|zh|><|HAPPY|><|Speech|>今天很好。', '', ('今天很好。', 'HAPPY', 0.85)),
    ('今天很好。', '<|HAPPY|>', ('今天很好。', 'HAPPY', 0.85)),
])
def test_transcribe_local_onnx_stereo_pcm(tmp_path, monkeypatch, raw, emotion_tag, expected):
    (tmp_path/'model.int8.onnx').write_bytes(b'local-model')
    (tmp_path/'tokens.txt').write_text('tokens')
    monkeypatch.setenv('SENSEVOICE_MODEL', str(tmp_path))
    captured = {}

    def accept_waveform(rate, samples):
        captured.update(rate=rate, samples=samples)

    stream = SimpleNamespace(accept_waveform=accept_waveform, result=SimpleNamespace(text=raw, emotion=emotion_tag))
    model = SimpleNamespace(create_stream=lambda:stream, decode_stream=lambda s:None)
    monkeypatch.setitem(sys.modules, 'sherpa_onnx', SimpleNamespace(
        OfflineRecognizer=SimpleNamespace(from_sense_voice=lambda **kw:model)))
    stereo = np.array([[3276, 0], [-3276, 0]], dtype='<i2').tobytes()
    assert SpeechService()._transcribe_sync(stereo, 16000, 2) == expected
    assert captured['rate'] == 16000
    np.testing.assert_allclose(captured['samples'], [0.04998779, -0.04998779])


@pytest.mark.asyncio
async def test_vad_missing_sherpa_keeps_rms_fallback(monkeypatch):
    monkeypatch.setitem(sys.modules, 'sherpa_onnx', None)
    service = SpeechService()
    assert await service.has_speech(b'\0\0' * 16000) is False
    assert await service.has_speech(np.full(16000, 10000, dtype='<i2').tobytes()) is True


def test_missing_model_override_does_not_enter_native_loader(tmp_path, monkeypatch):
    monkeypatch.setenv('SENSEVOICE_MODEL', str(tmp_path/'missing.onnx'))

    def native_loader(**kwargs):
        pytest.fail('Missing configured files must be rejected before native initialization')

    monkeypatch.setitem(sys.modules, 'sherpa_onnx', SimpleNamespace(
        OfflineRecognizer=SimpleNamespace(from_sense_voice=native_loader)))
    with pytest.raises(FileNotFoundError):
        SpeechService()._load()
