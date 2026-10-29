from types import SimpleNamespace

import numpy as np

from app import media, transcribe


def test_decode_audio_returns_16k_mono(sample_video):
    audio = media.decode_audio(sample_video)
    assert audio.dtype == np.float32 and abs(len(audio) / 16000 - 8) < 0.2


def test_transcribe_passes_decoded_array_to_whisper(sample_video, monkeypatch):
    seen = {}

    class FakeModel:
        def transcribe(self, audio, **kwargs):
            seen["audio"] = audio
            word = SimpleNamespace(start=0.5, end=0.9, word=" Halo")
            seg = SimpleNamespace(start=0.5, end=0.9, text=" Halo", words=[word])
            return iter([seg]), SimpleNamespace(language="id")

    monkeypatch.setattr(transcribe, "_get_model", lambda force_cpu=False: FakeModel())
    progress = []
    result = transcribe.transcribe(sample_video, 8.0, "id", progress.append)
    assert isinstance(seen["audio"], np.ndarray)  # bukan path: PyAV tidak dipakai
    assert result == {"language": "id", "segments": [
        {"start": 0.5, "end": 0.9, "text": "Halo", "words": [{"start": 0.5, "end": 0.9, "word": "Halo"}]}]}
    assert progress
