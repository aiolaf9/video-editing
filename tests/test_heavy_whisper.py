"""Tes khusus (-m heavy): Whisper ASLI. Tidak ikut regresi biasa; CI hanya lewat workflow manual.

Mengunduh model `tiny` (±75 MB) bila belum ada. Memastikan model benar-benar termuat & transkripsi berjalan
end-to-end (bukan tiruan), dan pemeriksaan kapasitas bench/whisper_bench.py bekerja.
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.heavy
REPO = Path(__file__).resolve().parent.parent


@pytest.fixture
def online(monkeypatch):
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
    monkeypatch.setenv("HF_HUB_OFFLINE", "0")


def test_real_whisper_tiny_transcribes(online, sample_video, monkeypatch):
    from app import transcribe

    monkeypatch.setattr(transcribe, "WHISPER_MODEL", "tiny")
    monkeypatch.setattr(transcribe, "_model", None)
    monkeypatch.setattr(transcribe, "WHISPER_DEVICE", "cpu")
    out = transcribe.transcribe(sample_video, 8, "id")
    assert isinstance(out["segments"], list) and out["language"] == "id"
    assert type(transcribe._model).__name__ == "WhisperModel"  # model asli, bukan tiruan


def test_whisper_capacity_check_runs(online):
    r = subprocess.run([sys.executable, "bench/whisper_bench.py", "--check", "--model", "tiny"], cwd=REPO,
                       capture_output=True, text=True, timeout=300, env=dict(os.environ))
    assert r.returncode == 0 and '"faster_whisper": true' in r.stdout
