"""Transkripsi lokal dengan faster-whisper (gratis, tanpa API)."""
import logging
import threading
from pathlib import Path
from typing import Callable, Optional

from . import media
from .config import WHISPER_DEVICE, WHISPER_MODEL

log = logging.getLogger("video-editing.transcribe")

_model = None
_model_device = None
_lock = threading.Lock()


def _load(device: str):
    from faster_whisper import WhisperModel

    compute_type = "int8" if device == "cpu" else "default"
    log.info("Memuat model Whisper '%s' di %s", WHISPER_MODEL, device)
    return WhisperModel(WHISPER_MODEL, device=device, compute_type=compute_type)


def _get_model(force_cpu: bool = False):
    global _model, _model_device
    want = "cpu" if force_cpu else WHISPER_DEVICE
    if _model is None or (force_cpu and _model_device != "cpu"):
        try:
            _model, _model_device = _load(want), want
        except Exception as exc:  # GPU/driver tidak tersedia
            if want == "cpu":
                raise
            log.warning("Gagal memuat Whisper di %s (%s), memakai CPU.", want, exc)
            _model, _model_device = _load("cpu"), "cpu"
    return _model


def _run(model, audio, duration: float, language: Optional[str], progress) -> dict:
    segments, info = model.transcribe(
        audio,
        language=language or None,
        word_timestamps=True,
        vad_filter=True,
        beam_size=5,
    )
    out = []
    for seg in segments:
        words = [
            {"start": round(w.start, 3), "end": round(w.end, 3), "word": w.word.strip()}
            for w in (seg.words or [])
            if w.word.strip()
        ]
        out.append({
            "start": round(seg.start, 3),
            "end": round(seg.end, 3),
            "text": seg.text.strip(),
            "words": words,
        })
        if progress and duration > 0:
            progress(min(1.0, seg.end / duration))
    return {"language": info.language, "segments": out}


def transcribe(path: Path, duration: float, language: Optional[str] = None,
               progress: Optional[Callable[[float], None]] = None) -> dict:
    # Audio di-decode sendiri dengan FFmpeg: lebih tahan terhadap format aneh dan
    # tidak bergantung pada PyAV (versi barunya tidak cocok dengan faster-whisper).
    audio = media.decode_audio(path)
    if audio.size == 0:
        return {"language": language, "segments": []}
    with _lock:
        model = _get_model()
        try:
            return _run(model, audio, duration, language, progress)
        except RuntimeError as exc:
            # Library CUDA (cuBLAS/cuDNN) baru ketahuan hilang saat inferensi.
            if _model_device == "cpu" or not any(k in str(exc).lower() for k in ("cuda", "cublas", "cudnn")):
                raise
            log.warning("Whisper GPU gagal (%s), mengulang di CPU.", exc)
            return _run(_get_model(force_cpu=True), audio, duration, language, progress)
