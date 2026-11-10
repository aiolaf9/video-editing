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

    monkeypatch.setattr(transcribe, "_get_model", lambda force_cpu=False, name=None: FakeModel())
    progress = []
    result = transcribe.transcribe(sample_video, 8.0, "id", progress.append)
    assert isinstance(seen["audio"], np.ndarray)  # bukan path: PyAV tidak dipakai
    assert result == {"language": "id", "model": transcribe.WHISPER_MODEL, "quality": None, "segments": [
        {"start": 0.5, "end": 0.9, "text": "Halo", "words": [{"start": 0.5, "end": 0.9, "word": "Halo", "p": 1.0}],
         "avg_logprob": 0.0, "no_speech_prob": 0.0, "compression_ratio": 0.0}]}
    assert progress


# ------------------------------------------------------------------ M1.1: mode kualitas, kosakata, deteksi bahasa

class _Recorder:
    """Model tiruan: mencatat argumen transcribe & detect_language."""

    def __init__(self, langs=None):
        self.kwargs, self.detect_lengths = None, []
        self.langs = list(langs or [])

    def detect_language(self, audio):
        self.detect_lengths.append(len(audio))
        lang = self.langs.pop(0) if self.langs else "id"
        others = [("ar", 0.05)] if lang != "ar" else [("id", 0.05)]
        return lang, 0.9, [(lang, 0.9), *others]

    def transcribe(self, audio, **kwargs):
        self.kwargs = kwargs
        w = SimpleNamespace(start=0.1, end=0.4, word=" Bismillah", probability=0.42)
        seg = SimpleNamespace(start=0.1, end=0.4, text=" Bismillah", words=[w], avg_logprob=-0.7,
                              no_speech_prob=0.01, compression_ratio=1.1)
        return iter([seg]), SimpleNamespace(language=kwargs.get("language") or "id")


def test_quality_selects_model_and_records_word_probability(sample_video, monkeypatch):
    monkeypatch.setenv("WHISPER_MODEL_FAST", "base")
    monkeypatch.setenv("WHISPER_MODEL_ACCURATE", "small")
    asked = []
    rec = _Recorder()

    def fake_get(force_cpu=False, name=None):
        asked.append(name)
        return rec

    monkeypatch.setattr(transcribe, "_get_model", fake_get)
    fast = transcribe.transcribe(sample_video, 8.0, "id", quality="cepat")
    accurate = transcribe.transcribe(sample_video, 8.0, "id", quality="akurat")
    assert asked == ["base", "small"]
    assert fast["model"] == "base" and fast["quality"] == "cepat" and accurate["model"] == "small"
    w = accurate["segments"][0]["words"][0]
    assert w == {"start": 0.1, "end": 0.4, "word": "Bismillah", "p": 0.42}
    assert accurate["segments"][0]["avg_logprob"] == -0.7


def test_vocabulary_is_passed_as_hotwords_only(sample_video, monkeypatch):
    rec = _Recorder()
    monkeypatch.setattr(transcribe, "_get_model", lambda force_cpu=False, name=None: rec)
    out = transcribe.transcribe(sample_video, 8.0, "id", vocabulary=["Nurul Hayat", "Insya Allah", "nurul hayat", " "])
    assert rec.kwargs["hotwords"] == "Nurul Hayat, Insya Allah"        # unik, tanpa kosong
    assert out["segments"][0]["text"] == "Bismillah"                   # teks tidak diganti sesudah transkripsi
    transcribe.transcribe(sample_video, 8.0, "id")
    assert "hotwords" not in rec.kwargs


def test_hotwords_are_capped():
    terms = [f"istilah{i:03d}" for i in range(400)]
    text = transcribe.hotwords_text(terms)
    assert len(text) <= transcribe.MAX_HOTWORDS_CHARS and text.startswith("istilah000")


def test_language_detected_from_several_windows_not_only_opening():
    import numpy as np
    audio = np.zeros(16000 * 300, dtype=np.float32)                  # 5 menit
    rec = _Recorder(langs=["ar", "id", "id"])                           # pembukaan berbahasa Arab (salam/doa)
    assert transcribe.detect_language(rec, audio, 3) == "id"
    assert len(rec.detect_lengths) == 3 and all(n == 16000 * 30 for n in rec.detect_lengths)
    short = _Recorder(langs=["id"])
    assert transcribe.detect_language(short, audio[: 16000 * 10], 3) == "id" and len(short.detect_lengths) == 1


def test_decode_options_env_override(monkeypatch):
    monkeypatch.setenv("WHISPER_DECODE", '{"condition_on_previous_text": false, "temperature": 0}')
    opts = transcribe.decode_options()
    kw = transcribe._transcribe_kwargs("id", None, opts)
    assert kw["condition_on_previous_text"] is False and kw["temperature"] == 0 and kw["beam_size"] == 5
    monkeypatch.setenv("WHISPER_DECODE", "bukan json")
    assert transcribe.decode_options()["condition_on_previous_text"] == transcribe.DEFAULT_DECODE[
        "condition_on_previous_text"]


def test_only_one_model_resident(monkeypatch):
    loads = []
    monkeypatch.setattr(transcribe, "_load", lambda device, name: loads.append(name) or object())
    monkeypatch.setattr(transcribe, "pick_device", lambda: "cpu")
    monkeypatch.setattr(transcribe, "_model", None)
    monkeypatch.setattr(transcribe, "_model_name", None)
    a = transcribe._get_model(name="base")
    assert transcribe._get_model(name="base") is a
    transcribe._get_model(name="small")
    assert loads == ["base", "small"] and transcribe._model_name == "small"


def test_model_paths_from_env(monkeypatch):
    monkeypatch.setenv("WHISPER_MODEL_PATHS", '{"small": "C:/app/whisper-small"}')
    assert transcribe._model_source("small") == "C:/app/whisper-small"
    assert transcribe._model_source("tiny") == "tiny"
