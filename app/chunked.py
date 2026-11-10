"""Transkripsi per potongan ucapan (P5.5.2 W5 lapisan 2). BAWAAN NONAKTIF (config.WHISPER_CHUNKED).

Masalah yang diatasi (P5.5.2 CI #32/#35, model asli): pada pipeline lama Whisper memproses jendela 30 dtk berisi
banyak ucapan; bila model menutup jendela dengan satu token timestamp terlalu dini, faster-whisper melompati sisa
jendela (`seek += segment_size`) beserta ucapannya. Transkripsi per ucapan mencakup 100% ucapan di semua set uji.
Selain itu pipeline lama memuat seluruh audio sekaligus (RAM ±50 MiB per menit).

Alur:
  1. FFmpeg men-stream PCM 16 kHz mono; dibaca per blok WHISPER_DECODE_WINDOW_SECONDS (tanpa seek: aman untuk berkas
     tanpa indeks; RAM dibatasi oleh blok + sisa tertunda, bukan durasi video).
  2. VAD (Silero, jeda minimum WHISPER_VAD_MIN_SILENCE_MS) pada buffer hanya sebagai PETUNJUK batas ucapan.
     Ucapan yang mungkin terpotong di ujung buffer ditunda ke putaran berikutnya (kecuali blok terakhir).
  3. Ucapan > WHISPER_CHUNK_MAX_SECONDS tanpa jeda: dipotong terkendali dengan overlap; kata di overlap
     direkonsiliasi di titik tengah overlap (tidak ganda, tidak hilang).
  4. Interval tanpa ucapan menurut VAD yang panjang DAN keras tetap ditranskripsi (VAD bisa meleset); segmen dari
     interval ini dibuang bila Whisper sendiri menilainya bukan ucapan (no_speech tinggi & logprob rendah).
  5. Tiap potongan ditranskripsi independen (tanpa teks potongan sebelumnya), bahasa ditetapkan sekali, prompt tanda
     baca sama dengan pipeline lama. Timestamp = posisi potongan pada berkas sumber.
Hasil (format sama dengan pipeline lama) baru dikembalikan setelah SELURUH audio selesai; pembatalan/timeout di
tengah jalan tidak pernah menghasilkan transkrip parsial yang dianggap final.
"""
import logging
import subprocess
from pathlib import Path
from typing import Callable, Iterator, Optional

import numpy as np

from . import config, jobcontrol, media

log = logging.getLogger("video-editing.chunked")
RATE = 16000
# Segmen dari interval non-VAD (VAD sudah menilai bukan ucapan) dibuang bila Whisper juga menilainya bukan ucapan.
# CI #41 (model small asli): halusinasi di interval ini ("Terima kasih.", "Thank you for watching!") ber-no_speech_prob
# 0,88-0,97 tetapi avg_logprob -0,56..-0,92, sehingga aturan lama (no_speech > 0,5 DAN logprob < -1,0) tidak pernah
# menangkapnya; ucapan nyata yang hanya terselamatkan lewat interval ini ber-no_speech_prob 0,195.
GAP_NO_SPEECH_PROB = 0.5
GAP_MIN_LOGPROB = -1.0
GAP_NO_SPEECH_DROP = 0.6          # VAD negatif + Whisper no_speech >= ini = dua sinyal independen -> dibuang


# ------------------------------------------------------------------ audio bertahap

def stream_audio(src: Path, block_seconds: float) -> Iterator[np.ndarray]:
    """Audio mono float32 16 kHz per blok `block_seconds` dari SATU proses FFmpeg (bisa dibatalkan)."""
    cmd = [config.FFMPEG, "-v", "error", *media.INPUT_PROTOCOLS, "-i", str(src), "-vn", "-ac", "1", "-ar", str(RATE),
           "-f", "f32le", "-"]
    jobcontrol.check()
    proc = jobcontrol.popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    block_bytes = max(1, int(block_seconds * RATE)) * 4
    try:
        while True:
            jobcontrol.check()
            raw = _read_exact(proc.stdout, block_bytes)
            if raw:
                yield np.frombuffer(raw[: len(raw) // 4 * 4], dtype=np.float32).copy()
            if len(raw) < block_bytes:
                break
        err = proc.stderr.read()
        proc.wait()
        jobcontrol.check()
        if proc.returncode != 0:
            raise media.MediaError(f"Gagal membaca audio: {err.decode(errors='replace').strip()[-400:]}")
    finally:
        if proc.poll() is None:
            jobcontrol.terminate(proc)
        for f in (proc.stdout, proc.stderr):
            if f:
                f.close()
        jobcontrol.release(proc)


def _read_exact(f, n: int) -> bytes:
    parts, got = [], 0
    while got < n:
        b = f.read(n - got)
        if not b:
            break
        parts.append(b)
        got += len(b)
    return b"".join(parts)


# ------------------------------------------------------------------ rencana potongan

def _speech_regions(audio: np.ndarray) -> list[tuple[int, int]]:
    from faster_whisper.vad import VadOptions, get_speech_timestamps
    opts = VadOptions(min_silence_duration_ms=config.WHISPER_VAD_MIN_SILENCE_MS, speech_pad_ms=config.WHISPER_VAD_PAD_MS)
    return [(r["start"], r["end"]) for r in get_speech_timestamps(audio, opts)]


def _merge_short(regions: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Gabungkan wilayah VAD < WHISPER_CHUNK_MIN_SECONDS dengan tetangga terdekat bila jedanya <=
    WHISPER_CHUNK_MERGE_GAP_SECONDS dan hasilnya <= WHISPER_CHUNK_MAX_SECONDS (bawaan nonaktif)."""
    min_len = int(config.WHISPER_CHUNK_MIN_SECONDS * RATE)
    if min_len <= 0 or len(regions) < 2:
        return regions
    max_gap = int(config.WHISPER_CHUNK_MERGE_GAP_SECONDS * RATE)
    max_len = int(config.WHISPER_CHUNK_MAX_SECONDS * RATE)
    out = [list(regions[0])]
    for s, e in regions[1:]:
        last = out[-1]
        short = (e - s) < min_len or (last[1] - last[0]) < min_len
        if short and s - last[1] <= max_gap and e - last[0] <= max_len:
            last[1] = e
        else:
            out.append([s, e])
    return [(s, e) for s, e in out]


def _split(start: int, end: int, kind: str) -> list[tuple[int, int, str]]:
    """Potong [start, end) menjadi potongan <= CHUNK_MAX dengan overlap CHUNK_OVERLAP (kata direkonsiliasi nanti)."""
    size = int(config.WHISPER_CHUNK_MAX_SECONDS * RATE)
    overlap = min(int(config.WHISPER_CHUNK_OVERLAP_SECONDS * RATE), size // 2)
    if end - start <= size:
        return [(start, end, kind)]
    out, s = [], start
    while True:
        e = min(s + size, end)
        out.append((s, e, kind))
        if e >= end:
            return out
        s = e - overlap


def _loud(audio: np.ndarray) -> bool:
    if audio.size == 0:
        return False
    rms = float(np.sqrt(np.mean(np.square(audio, dtype=np.float64))))
    return 20 * np.log10(max(rms, 1e-9)) >= config.WHISPER_GAP_FALLBACK_DBFS


def plan(buf: np.ndarray, final: bool) -> tuple[list[tuple[int, int, str]], int]:
    """(potongan [(awal, akhir, jenis)] dalam sampel buffer, jumlah sampel buffer yang selesai diproses).
    jenis: "speech" (ucapan menurut VAD) atau "gap" (interval non-VAD yang keras)."""
    regions = _merge_short(_speech_regions(buf))
    n = len(buf)
    limit = n if final else max(0, n - int(config.WHISPER_CHUNK_MAX_SECONDS * RATE))
    done, deferred_at = [], None
    for s, e in regions:
        if not final and e > limit:
            deferred_at = s
            break
        done.append((s, e))
    if final:
        consumed = n
    elif deferred_at is None:
        consumed = limit
    elif deferred_at > 0:
        consumed = deferred_at
    else:
        # Ucapan tanpa jeda sejak awal buffer melewati batas: proses bagian yang sudah pasti (potong terkendali),
        # sisakan overlap untuk putaran berikutnya agar kata di batas tidak hilang.
        s, e = regions[0]
        pieces = _split(s, limit, "speech")
        overlap = min(int(config.WHISPER_CHUNK_OVERLAP_SECONDS * RATE), int(config.WHISPER_CHUNK_MAX_SECONDS * RATE) // 2)
        consumed = max(1, limit - overlap)
        return pieces, consumed
    chunks: list[tuple[int, int, str]] = []
    gap_min = int(config.WHISPER_GAP_FALLBACK_SECONDS * RATE)
    prev_end = 0
    for s, e in done + [(consumed, consumed)]:
        if s - prev_end >= gap_min and _loud(buf[prev_end:s]):
            chunks += _split(prev_end, s, "gap")
        if e > s:
            chunks += _split(s, e, "speech")
        prev_end = max(prev_end, e)
    return chunks, consumed


# ------------------------------------------------------------------ transkripsi

def _low_quality(seg, chunk_seconds: float) -> bool:
    """Saringan multi-sinyal (bawaan nonaktif, WHISPER_SEGMENT_FILTER): segmen dibuang hanya bila DUA sinyal buruk
    bersamaan — bukan satu ambang saja — agar ucapan nyata yang lirih tidak ikut terbuang:
      no_speech tinggi & logprob rendah; atau teks berulang (compression ratio tinggi) & logprob rendah; atau
      potongan sangat pendek (< 1 dtk) & no_speech sedang & logprob rendah."""
    nsp, lp, cr = seg.no_speech_prob, seg.avg_logprob, getattr(seg, "compression_ratio", 0.0) or 0.0
    return ((nsp > 0.6 and lp < -0.7) or (cr > 2.4 and lp < -1.0)
            or (chunk_seconds < 1.0 and nsp > 0.3 and lp < -0.8))


def _transcribe_piece(model, audio: np.ndarray, language: Optional[str], offset: float, kind: str,
                      vocabulary: Optional[list] = None) -> tuple[list, str]:
    from .transcribe import PUNCTUATION_PROMPTS, decode_options, hotwords_text, segment_dict

    opts = decode_options()       # beam & temperature sama dengan jalur utama (M1.1: temperature 0)
    kw = {"language": language or None, "initial_prompt": PUNCTUATION_PROMPTS.get(language or ""),
          "word_timestamps": True, "vad_filter": False, "condition_on_previous_text": False,
          "beam_size": int(opts["beam_size"])}
    if opts.get("temperature") is not None:
        kw["temperature"] = opts["temperature"]
    hot = hotwords_text(vocabulary)
    if hot:
        kw["hotwords"] = hot
    segments, info = model.transcribe(audio, **kw)
    out = []
    for seg in segments:
        jobcontrol.check()   # titik batal/timeout per segmen
        if kind == "gap" and (seg.no_speech_prob >= GAP_NO_SPEECH_DROP
                              or (seg.no_speech_prob > GAP_NO_SPEECH_PROB and seg.avg_logprob < GAP_MIN_LOGPROB)):
            continue
        if config.WHISPER_SEGMENT_FILTER and _low_quality(seg, len(audio) / RATE):
            continue
        out.append(segment_dict(seg, offset))
    return out, getattr(info, "language", None) or language


def _reconcile(prev: list, cur: list, cut: float) -> tuple[list, list]:
    """Overlap dua potongan berurutan dari satu ucapan: kata dengan titik tengah < cut milik potongan sebelumnya,
    >= cut milik potongan berikutnya. Segmen yang kehilangan kata disusun ulang dari kata yang tersisa."""
    def keep(segs, pred):
        out = []
        for sg in segs:
            if not sg["words"]:
                mid = (sg["start"] + sg["end"]) / 2
                if pred(mid):
                    out.append(sg)
                continue
            words = [w for w in sg["words"] if pred((w["start"] + w["end"]) / 2)]
            if not words:
                continue
            if len(words) == len(sg["words"]):
                out.append(sg)
            else:
                out.append({"start": words[0]["start"], "end": words[-1]["end"],
                            "text": " ".join(w["word"] for w in words), "words": words})
        return out
    return keep(prev, lambda m: m < cut), keep(cur, lambda m: m >= cut)


def transcribe_chunked(model, path: Path, duration: float, language: Optional[str],
                       progress: Optional[Callable[[float], None]], vocabulary: Optional[list] = None) -> dict:
    from .transcribe import detect_language

    block = config.WHISPER_DECODE_WINDOW_SECONDS
    buf = np.empty(0, dtype=np.float32)
    buf_start = 0.0                                   # detik sumber untuk buf[0]
    segments: list = []
    lang = language
    prev_piece, prev_end = None, None                 # potongan terakhir (bisa lintas putaran buffer: overlap)
    stream = stream_audio(path, block)
    # Generator ditutup eksplisit: bila model/rencana gagal di tengah jalan, FFmpeg langsung dihentikan (finally di
    # stream_audio), tidak menunggu traceback dibebaskan pemanggil (W5 review: proses FFmpeg tertinggal).
    try:
        nxt = next(stream, None)
        while nxt is not None:
            buf = np.concatenate([buf, nxt]) if buf.size else nxt
            nxt = next(stream, None)
            final = nxt is None
            chunks, consumed = plan(buf, final)
            if lang is None and chunks:
                speech = [buf[s:e] for s, e, k in chunks if k == "speech"][:4]
                if speech:
                    lang = detect_language(model, np.concatenate(speech)[: RATE * 30], 1)
            for s, e, kind in chunks:
                jobcontrol.check()
                offset = buf_start + s / RATE
                piece, detected = _transcribe_piece(model, buf[s:e], lang, offset, kind, vocabulary)
                lang = lang or detected
                if prev_piece is not None and prev_end is not None and offset < prev_end:   # overlap potongan terkendali
                    cut = (offset + prev_end) / 2
                    kept_prev, piece = _reconcile(prev_piece, piece, cut)
                    segments[len(segments) - len(prev_piece):] = kept_prev
                segments += piece
                prev_piece, prev_end = piece, buf_start + e / RATE
                if progress and duration > 0:
                    progress(min(1.0, prev_end / duration))
            carry_overlap = 0 if final else consumed
            buf_start += carry_overlap / RATE
            buf = buf[carry_overlap:].copy() if not final else np.empty(0, dtype=np.float32)
            if final:
                break
    finally:
        stream.close()
    segments.sort(key=lambda sg: sg["start"])
    return {"language": lang, "segments": segments}
