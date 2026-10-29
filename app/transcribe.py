"""Transkripsi lokal dengan faster-whisper (gratis, tanpa API)."""
import logging
import threading
from pathlib import Path
from typing import Callable, Optional

from . import jobcontrol, media
from .config import WHISPER_DEVICE, WHISPER_MODEL, WHISPER_THREADS

log = logging.getLogger("video-editing.transcribe")

_model = None
_model_device = None
_lock = threading.Lock()


def _load(device: str):
    from faster_whisper import WhisperModel

    from . import resources

    compute_type = "int8" if device == "cpu" else "default"
    # Model dimuat sekali per proses, jadi jumlah thread tidak bisa elastis per job: pakai core yang tersedia
    # untuk proses ini (termasuk batas `cpus` container). Prioritas rendah (nice) membuat render tetap didahulukan.
    threads = WHISPER_THREADS or resources.effective_cpus()
    log.info("Memuat model Whisper '%s' di %s (%d thread)", WHISPER_MODEL, device, threads)
    return WhisperModel(WHISPER_MODEL, device=device, compute_type=compute_type, cpu_threads=threads)


def pick_device() -> str:
    """auto: GPU hanya bila benar-benar terdeteksi dengan VRAM bebas cukup; selain itu CPU.
    Tidak mencoba CUDA secara membabi buta (lambat & boros RAM saat gagal di server tanpa GPU)."""
    if WHISPER_DEVICE != "auto":
        return WHISPER_DEVICE
    from . import resources

    return "cuda" if resources.gpu_with_free_vram() else "cpu"


def _get_model(force_cpu: bool = False):
    global _model, _model_device
    want = "cpu" if force_cpu else pick_device()
    if _model is None or (force_cpu and _model_device != "cpu"):
        try:
            _model, _model_device = _load(want), want
        except Exception as exc:  # GPU/driver tidak tersedia
            if want == "cpu":
                raise
            log.warning("Gagal memuat Whisper di %s (%s), memakai CPU.", want, exc)
            _model, _model_device = _load("cpu"), "cpu"
    return _model


# Whisper meniru gaya teks pada prompt awal. Contoh kalimat bertanda baca membuat
# transkrip ikut bertanda baca, sehingga subtitle & klip terpotong di akhir kalimat.
PUNCTUATION_PROMPTS = {
    "id": "Halo, semuanya! Jadi, hari ini kita bahas hal yang penting. Kenapa? Karena banyak orang belum tahu.",
    "ms": "Hai, semua! Jadi, hari ini kita bincangkan perkara penting. Kenapa? Sebab ramai yang belum tahu.",
    "jv": "Halo, kanca kabeh! Dadi, dina iki awake dhewe ngrembug bab sing penting. Kenapa? Amarga akeh sing durung ngerti.",
    "en": "Hello, everyone! So, today we're talking about something important. Why? Because most people don't know.",
}


def _detect_language(model, audio) -> Optional[str]:
    try:
        lang, _prob, _all = model.detect_language(audio[: 16000 * 30])
        return lang
    except Exception as exc:  # metode ini tidak ada di semua versi faster-whisper
        log.info("Deteksi bahasa awal dilewati: %s", exc)
        return None


def _run(model, audio, duration: float, language: Optional[str], progress) -> dict:
    if not language:
        language = _detect_language(model, audio)
    segments, info = model.transcribe(
        audio,
        language=language or None,
        initial_prompt=PUNCTUATION_PROMPTS.get(language or ""),
        word_timestamps=True,
        vad_filter=True,
        beam_size=5,
    )
    out = []
    for seg in segments:
        jobcontrol.check()  # titik batal/timeout per segmen (Whisper tidak bisa di-kill dari luar)
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
