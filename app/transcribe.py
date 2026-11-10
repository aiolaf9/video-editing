"""Transkripsi lokal dengan faster-whisper (gratis, tanpa API).

M1.1:
- Mode kualitas per job ("cepat" / "akurat") -> nama model (WHISPER_MODEL_FAST / WHISPER_MODEL_ACCURATE). Tanpa mode,
  dipakai WHISPER_MODEL (perilaku versi web tidak berubah). Hanya satu model tinggal di RAM: ganti mode = model lama
  dilepas.
- Bahasa: bila tidak dipilih, dideteksi dari beberapa jendela audio (awal, tengah, akhir), bukan hanya 30 detik
  pertama. Pembukaan ceramah ("Assalamualaikum... bismillah...") dapat terdeteksi sebagai bahasa Arab dan membuat
  seluruh transkrip salah bahasa.
- Kosakata (istilah dakwah, Arab, nama lembaga/program) diberikan sebagai `hotwords`: hanya membiaskan decoder ke
  ejaan tersebut BILA audionya mendukung. Tidak ada penggantian teks otomatis sesudah transkripsi (lihat vocab.py:
  saran istilah selalu menunggu konfirmasi pengguna).
- Probabilitas per kata ("p") dan metrik segmen (avg_logprob, no_speech_prob, compression_ratio) disimpan agar editor
  dapat menandai bagian yang tidak yakin.
- Opsi decoding dapat diatur lewat WHISPER_DECODE (JSON) untuk evaluasi; bawaan dipilih dari hasil evaluasi WER/CER
  (docs/M1_1_TRANSKRIP.md), bukan asumsi.
"""
import gc
import json
import logging
import os
import threading
from pathlib import Path
from typing import Callable, Optional

from . import jobcontrol, media
from .config import WHISPER_DEVICE, WHISPER_MODEL, WHISPER_MODEL_DIR, WHISPER_THREADS

log = logging.getLogger("video-editing.transcribe")

QUALITIES = ("cepat", "akurat")
_model = None
_model_device = None
_model_name: Optional[str] = None
_lock = threading.Lock()


def model_for(quality: Optional[str]) -> str:
    """Nama model untuk mode kualitas. None/tidak dikenal = WHISPER_MODEL (bawaan server/web)."""
    if quality == "cepat":
        return os.environ.get("WHISPER_MODEL_FAST") or WHISPER_MODEL
    if quality == "akurat":
        return os.environ.get("WHISPER_MODEL_ACCURATE") or WHISPER_MODEL
    return WHISPER_MODEL


def _model_source(name: str) -> str:
    """Folder model lokal (desktop: dibundel installer) atau nama model (diunduh/di-cache faster-whisper)."""
    try:
        paths = json.loads(os.environ.get("WHISPER_MODEL_PATHS") or "{}")
    except ValueError:
        paths = {}
    if paths.get(name):
        return paths[name]
    if WHISPER_MODEL_DIR and name == WHISPER_MODEL:
        return WHISPER_MODEL_DIR
    return name


def _load(device: str, name: str):
    from faster_whisper import WhisperModel

    from . import resources

    compute_type = "int8" if device == "cpu" else "default"
    # Model dimuat sekali per proses, jadi jumlah thread tidak bisa elastis per job: pakai core yang tersedia
    # untuk proses ini (termasuk batas `cpus` container). Prioritas rendah (nice) membuat render tetap didahulukan.
    threads = WHISPER_THREADS or resources.effective_cpus()
    source = _model_source(name)
    log.info("Memuat model Whisper '%s' (%s) di %s (%d thread)", name, source, device, threads)
    return WhisperModel(source, device=device, compute_type=compute_type, cpu_threads=threads)


def pick_device() -> str:
    """auto: GPU hanya bila benar-benar terdeteksi dengan VRAM bebas cukup; selain itu CPU.
    Tidak mencoba CUDA secara membabi buta (lambat & boros RAM saat gagal di server tanpa GPU)."""
    if WHISPER_DEVICE != "auto":
        return WHISPER_DEVICE
    from . import resources

    return "cuda" if resources.gpu_with_free_vram() else "cpu"


def _get_model(force_cpu: bool = False, name: Optional[str] = None):
    global _model, _model_device, _model_name
    name = name or WHISPER_MODEL
    want = "cpu" if force_cpu else pick_device()
    if _model is not None and _model_name != name:
        log.info("Melepas model Whisper '%s' (beralih ke '%s')", _model_name, name)
        _model = None
        gc.collect()
    if _model is None or (force_cpu and _model_device != "cpu"):
        try:
            _model, _model_device = _load(want, name), want
        except Exception as exc:  # GPU/driver tidak tersedia
            if want == "cpu":
                raise
            log.warning("Gagal memuat Whisper di %s (%s), memakai CPU.", want, exc)
            _model, _model_device = _load("cpu", name), "cpu"
        _model_name = name
    return _model


# Whisper meniru gaya teks pada prompt awal. Contoh kalimat bertanda baca membuat
# transkrip ikut bertanda baca, sehingga subtitle & klip terpotong di akhir kalimat.
PUNCTUATION_PROMPTS = {
    "id": "Halo, semuanya! Jadi, hari ini kita bahas hal yang penting. Kenapa? Karena banyak orang belum tahu.",
    "ms": "Hai, semua! Jadi, hari ini kita bincangkan perkara penting. Kenapa? Sebab ramai yang belum tahu.",
    "jv": "Halo, kanca kabeh! Dadi, dina iki awake dhewe ngrembug bab sing penting. Kenapa? Amarga akeh sing durung ngerti.",
    "en": "Hello, everyone! So, today we're talking about something important. Why? Because most people don't know.",
}

# Opsi decoding bawaan, dipilih dari evaluasi M1.1 (FLEURS id, docs/M1_1_TRANSKRIP.md bagian 4): tanpa
# conditioning teks sebelumnya + temperature 0. Audio ±10 mnt: small WER 28,3 % (M1) -> 16,0 %, base 38,9 % -> 30,7 %;
# 2x lebih cepat, RAM lebih kecil, dan deterministik (fallback temperature acak membuat hasil berubah antar-run).
DEFAULT_DECODE = {
    "beam_size": 5,
    "condition_on_previous_text": False,
    "vad": {},                      # parameter VadOptions; {} = bawaan faster-whisper
    "temperature": 0.0,             # tanpa fallback acak (None = bawaan faster-whisper 0,0..1,0)
    "hallucination_silence_threshold": None,
    "use_hotwords": True,
    "detect_windows": 3,            # jumlah jendela deteksi bahasa bila bahasa tidak dipilih
}
MAX_HOTWORDS_CHARS = 600            # hotwords masuk prompt Whisper (maks 224 token, dibagi dengan teks sebelumnya)


def decode_options() -> dict:
    opts = dict(DEFAULT_DECODE)
    raw = os.environ.get("WHISPER_DECODE")
    if raw:
        try:
            opts.update(json.loads(raw))
        except ValueError:
            log.warning("WHISPER_DECODE bukan JSON yang sah: diabaikan")
    return opts


def hotwords_text(vocabulary: Optional[list[str]]) -> Optional[str]:
    """Istilah kosakata untuk hotwords, unik, dipotong agar tidak memenuhi jendela prompt Whisper."""
    out, seen, size = [], set(), 0
    for term in vocabulary or []:
        t = " ".join(str(term).split())
        if not t or t.lower() in seen:
            continue
        if size + len(t) + 2 > MAX_HOTWORDS_CHARS:
            break
        seen.add(t.lower())
        out.append(t)
        size += len(t) + 2
    return ", ".join(out) or None


def detect_language(model, audio, windows: int = 3) -> Optional[str]:
    """Bahasa dari beberapa jendela 30 dtk (awal, tengah, akhir), dijumlahkan per probabilitas. Pembukaan
    berbahasa Arab (salam, doa) tidak lagi menentukan bahasa seluruh video sendirian."""
    rate, win = 16000, 16000 * 30
    total = len(audio)
    if total == 0:
        return None
    starts = [0] if total <= win or windows <= 1 else [
        int(i * (total - win) / (windows - 1)) for i in range(windows)]
    scores: dict[str, float] = {}
    for s in starts:
        try:
            _lang, _prob, all_probs = model.detect_language(audio[s: s + win])
        except Exception as exc:  # metode ini tidak ada di semua versi faster-whisper
            log.info("Deteksi bahasa dilewati: %s", exc)
            return None
        for lang, prob in (all_probs or [(_lang, _prob)])[:5]:
            scores[lang] = scores.get(lang, 0.0) + float(prob)
    if not scores:
        return None
    best = max(scores, key=scores.get)
    log.info("Deteksi bahasa (%d jendela, %.0f dtk audio): %s", len(starts), total / rate,
             ", ".join(f"{k}={v:.2f}" for k, v in sorted(scores.items(), key=lambda kv: -kv[1])[:3]))
    return best


def _transcribe_kwargs(language: Optional[str], vocabulary: Optional[list[str]], opts: dict) -> dict:
    kw = {
        "language": language or None,
        "initial_prompt": PUNCTUATION_PROMPTS.get(language or ""),
        "word_timestamps": True,
        "vad_filter": True,
        "beam_size": int(opts["beam_size"]),
        "condition_on_previous_text": bool(opts["condition_on_previous_text"]),
    }
    if opts.get("vad"):
        kw["vad_parameters"] = dict(opts["vad"])
    if opts.get("temperature") is not None:
        kw["temperature"] = opts["temperature"]
    if opts.get("hallucination_silence_threshold") is not None:
        kw["hallucination_silence_threshold"] = float(opts["hallucination_silence_threshold"])
    hot = hotwords_text(vocabulary) if opts.get("use_hotwords", True) else None
    if hot:
        kw["hotwords"] = hot
    return kw


def segment_dict(seg, offset: float = 0.0) -> dict:
    """Segmen faster-whisper -> dict transkrip (dengan probabilitas kata & metrik keyakinan segmen)."""
    words = [
        {"start": round(w.start + offset, 3), "end": round(w.end + offset, 3), "word": w.word.strip(),
         "p": round(float(getattr(w, "probability", 1.0) or 0.0), 3)}
        for w in (seg.words or [])
        if w.word.strip()
    ]
    return {
        "start": round(seg.start + offset, 3),
        "end": round(seg.end + offset, 3),
        "text": seg.text.strip(),
        "words": words,
        "avg_logprob": round(float(getattr(seg, "avg_logprob", 0.0) or 0.0), 3),
        "no_speech_prob": round(float(getattr(seg, "no_speech_prob", 0.0) or 0.0), 3),
        "compression_ratio": round(float(getattr(seg, "compression_ratio", 0.0) or 0.0), 3),
    }


def _run(model, audio, duration: float, language: Optional[str], progress,
         vocabulary: Optional[list[str]] = None) -> dict:
    opts = decode_options()
    if not language:
        language = detect_language(model, audio, int(opts.get("detect_windows") or 1))
    segments, info = model.transcribe(audio, **_transcribe_kwargs(language, vocabulary, opts))
    out = []
    for seg in segments:
        jobcontrol.check()  # titik batal/timeout per segmen (Whisper tidak bisa di-kill dari luar)
        out.append(segment_dict(seg))
        if progress and duration > 0:
            progress(min(1.0, seg.end / duration))
    return {"language": info.language, "segments": out}


def transcribe(path: Path, duration: float, language: Optional[str] = None,
               progress: Optional[Callable[[float], None]] = None, *, quality: Optional[str] = None,
               vocabulary: Optional[list[str]] = None) -> dict:
    from . import config
    name = model_for(quality)
    meta = {"model": name, "quality": quality if quality in QUALITIES else None}
    if config.WHISPER_CHUNKED:   # W5 lapisan 2 (bawaan nonaktif): per potongan ucapan, audio dibaca bertahap
        return {**_transcribe_chunked(path, duration, language, progress, name, vocabulary), **meta}
    # Audio di-decode sendiri dengan FFmpeg: lebih tahan terhadap format aneh dan
    # tidak bergantung pada PyAV (versi barunya tidak cocok dengan faster-whisper).
    audio = media.decode_audio(path)
    if audio.size == 0:
        return {"language": language, "segments": [], **meta}
    with _lock:
        model = _get_model(name=name)
        try:
            return {**_run(model, audio, duration, language, progress, vocabulary), **meta}
        except RuntimeError as exc:
            # Library CUDA (cuBLAS/cuDNN) baru ketahuan hilang saat inferensi.
            if _model_device == "cpu" or not any(k in str(exc).lower() for k in ("cuda", "cublas", "cudnn")):
                raise
            log.warning("Whisper GPU gagal (%s), mengulang di CPU.", exc)
            return {**_run(_get_model(force_cpu=True, name=name), audio, duration, language, progress, vocabulary),
                    **meta}


def _transcribe_chunked(path: Path, duration: float, language: Optional[str], progress, name: str,
                        vocabulary: Optional[list[str]]) -> dict:
    from .chunked import transcribe_chunked
    with _lock:
        model = _get_model(name=name)
        try:
            return transcribe_chunked(model, path, duration, language, progress, vocabulary=vocabulary)
        except RuntimeError as exc:
            if _model_device == "cpu" or not any(k in str(exc).lower() for k in ("cuda", "cublas", "cudnn")):
                raise
            log.warning("Whisper GPU gagal (%s), mengulang di CPU.", exc)
            return transcribe_chunked(_get_model(force_cpu=True, name=name), path, duration, language, progress,
                                      vocabulary=vocabulary)
