"""P5.5.2 W5 lapisan 2: transkripsi per potongan ucapan (flag WHISPER_CHUNKED, bawaan nonaktif).

Model tiruan "mendengar" nada: tiap 0,5 dtk bernada menjadi satu kata unik `f<frekuensi>` dengan timestamp-nya, sehingga
kehilangan/duplikasi kata di batas potongan & jendela terdeteksi persis. VAD Silero tidak menganggap nada sebagai
ucapan, jadi di tes ini VAD diganti detektor energi sederhana (VAD asli divalidasi di benchmark CI dengan ucapan nyata).
"""
import subprocess
import threading
from pathlib import Path
from types import SimpleNamespace as NS

import numpy as np
import pytest

from app import chunked, config, jobcontrol, media, transcribe

RATE = 16000
FRAME = 0.5


def _tones(path: Path, plan, fmt="wav"):
    """plan: daftar (detik, frekuensi atau 0 = hening/derau pelan atau -1 = derau keras). Frekuensi berubah tiap 0,5 dtk
    bila diberi daftar."""
    parts = []
    for dur, f in plan:
        n = int(dur * RATE)
        t = np.arange(n) / RATE
        if isinstance(f, list):
            seg = np.concatenate([0.3 * np.sin(2 * np.pi * fi * np.arange(int(FRAME * RATE)) / RATE) for fi in f])
        elif f > 0:
            seg = 0.3 * np.sin(2 * np.pi * f * t)
        elif f == -1:
            seg = 0.2 * np.random.default_rng(1).standard_normal(n)
        else:
            seg = 0.0005 * np.random.default_rng(2).standard_normal(n)
        parts.append(seg.astype(np.float32))
    audio = np.concatenate(parts)
    raw = path.with_suffix(".f32")
    raw.write_bytes(audio.tobytes())
    codec = {"wav": ["-c:a", "pcm_s16le"], "aac": ["-c:a", "aac", "-b:a", "96k"], "ts": ["-c:a", "aac", "-f", "mpegts"]}
    subprocess.run(["ffmpeg", "-hide_banner", "-v", "error", "-y", "-f", "f32le", "-ar", str(RATE), "-ac", "1",
                    "-i", str(raw), *codec[fmt], str(path)], check=True)
    return audio


def _frame_words(audio: np.ndarray, offset: float = 0.0):
    """Kata per bingkai 0,5 dtk yang bernada (frekuensi dominan, dibulatkan 10 Hz)."""
    out = []
    n = int(FRAME * RATE)
    for i in range(len(audio) // n):
        fr = audio[i * n:(i + 1) * n]
        if np.sqrt(np.mean(fr ** 2)) < 0.05:
            continue
        spec = np.abs(np.fft.rfft(fr))
        freq = int(round(np.argmax(spec) * RATE / n / 10.0) * 10)
        if freq <= 0:
            continue
        out.append((offset + i * FRAME, offset + (i + 1) * FRAME, f"f{freq}"))
    return out


class ToneModel:
    """Meniru antarmuka WhisperModel.transcribe & detect_language."""

    def __init__(self):
        self.calls, self.detect_calls, self.max_audio = [], 0, 0

    def detect_language(self, audio):
        self.detect_calls += 1
        return "id", 0.99, []

    def transcribe(self, audio, **kw):
        self.calls.append(kw)
        self.max_audio = max(self.max_audio, len(audio))
        words = _frame_words(audio)
        segs = []
        for i in range(0, len(words), 4):                      # 4 kata per segmen
            grp = words[i:i + 4]
            ws = [NS(start=a, end=b, word=f" {w}") for a, b, w in grp]
            segs.append(NS(start=grp[0][0], end=grp[-1][1], text=" " + " ".join(w for _, _, w in grp), words=ws,
                           no_speech_prob=0.1, avg_logprob=-0.2))
        return iter(segs), NS(language=kw.get("language") or "id")


def _energy_vad(audio):
    """Pengganti Silero untuk sinyal nada: wilayah 10 ms dengan RMS > 0,05, digabung bila jeda < 0,5 dtk."""
    hop = RATE // 100
    loud = [np.sqrt(np.mean(audio[i:i + hop] ** 2)) > 0.05 for i in range(0, len(audio) - hop + 1, hop)]
    regions, start, quiet = [], None, 0
    for k, v in enumerate(loud):
        if v:
            if start is None:
                start = k
            quiet = 0
        elif start is not None:
            quiet += 1
            if quiet >= 50:
                regions.append((start * hop, (k - quiet + 1) * hop))
                start, quiet = None, 0
    if start is not None:
        regions.append((start * hop, (len(loud) - quiet) * hop))
    return regions


@pytest.fixture
def tone(monkeypatch):
    model = ToneModel()
    monkeypatch.setattr(chunked, "_speech_regions", _energy_vad)
    monkeypatch.setattr(transcribe, "_get_model", lambda force_cpu=False: model)
    monkeypatch.setattr(config, "WHISPER_CHUNKED", True)
    monkeypatch.setattr(config, "WHISPER_DECODE_WINDOW_SECONDS", 30.0)
    monkeypatch.setattr(config, "WHISPER_CHUNK_MAX_SECONDS", 10.0)
    monkeypatch.setattr(config, "WHISPER_CHUNK_OVERLAP_SECONDS", 1.0)
    return model


def _words(result):
    return [(w["start"], w["end"], w["word"]) for s in result["segments"] for w in s["words"]]


def _same(got, expected, tol=0.02):
    assert [w for _, _, w in got] == [w for _, _, w in expected]
    for (a, b, _), (c, d, _) in zip(got, expected):
        assert abs(a - c) <= tol and abs(b - d) <= tol


def test_flag_off_keeps_legacy_pipeline(monkeypatch, tmp_path):
    assert config.WHISPER_CHUNKED is False                       # bawaan produksi: nonaktif
    seen = []
    monkeypatch.setattr(media, "decode_audio", lambda p, **k: seen.append(p) or np.zeros(RATE, np.float32))
    monkeypatch.setattr(transcribe, "_get_model", lambda force_cpu=False: ToneModel())
    transcribe.transcribe(tmp_path / "x.wav", 1.0, "id")
    assert seen                                                  # jalur lama (decode seluruh audio) yang dipakai


def test_utterances_across_windows_no_loss_no_duplicate(tone, tmp_path):
    plan = []
    for i in range(24):                                          # 24 ucapan 3 dtk, jeda 1,5 dtk: ±108 dtk
        plan += [(3.0, 300 + 20 * i), (1.5, 0)]
    path = tmp_path / "a.wav"
    audio = _tones(path, plan)
    res = transcribe.transcribe(path, len(audio) / RATE, None)
    _same(_words(res), _frame_words(audio))
    assert res["language"] == "id" and tone.detect_calls == 1    # bahasa dideteksi sekali
    assert all(c["language"] == "id" and c["condition_on_previous_text"] is False and c["vad_filter"] is False
               for c in tone.calls)                              # tiap potongan independen, bahasa tetap
    assert len(tone.calls) == 24                                 # satu potongan per ucapan
    assert tone.max_audio <= 10 * RATE + 1


def test_long_speech_without_pause_is_split_with_overlap_and_reconciled(tone, tmp_path):
    freqs = [300 + 10 * i for i in range(130)]                   # 65 dtk tanpa jeda, kata unik tiap 0,5 dtk
    path = tmp_path / "b.wav"
    audio = _tones(path, [(1.0, 0), (65.0, freqs), (1.0, 0)])
    res = transcribe.transcribe(path, len(audio) / RATE, "id")
    _same(_words(res), _frame_words(audio))                      # tiap kata tepat sekali walau dipotong & overlap
    assert len(tone.calls) >= 7 and tone.max_audio <= 10 * RATE + 1


def test_memory_bounded_by_window_not_duration(tone, tmp_path, monkeypatch):
    seen = []
    real = chunked.plan

    def spy(buf, final):
        seen.append(len(buf))
        return real(buf, final)
    monkeypatch.setattr(chunked, "plan", spy)
    plan = []
    for i in range(60):                                          # ±270 dtk audio, jendela 30 dtk
        plan += [(3.0, 300 + 10 * i), (1.5, 0)]
    path = tmp_path / "c.wav"
    audio = _tones(path, plan)
    res = transcribe.transcribe(path, len(audio) / RATE, "id")
    assert max(seen) <= 2 * 30 * RATE and len(seen) >= 8         # buffer <= 2 jendela, bukan seluruh audio
    _same(_words(res), _frame_words(audio))


def test_loud_interval_missed_by_vad_is_not_dropped(tone, tmp_path, monkeypatch):
    path = tmp_path / "d.wav"
    audio = _tones(path, [(3.0, 400), (1.0, 0), (4.0, 700), (1.0, 0), (3.0, 500)])
    full = _energy_vad(audio)
    monkeypatch.setattr(chunked, "_speech_regions", lambda a: [r for r in _energy_vad(a)
                                                                if not (r[0] > 3 * RATE and r[1] < 9 * RATE)])
    assert len(full) == 3
    res = transcribe.transcribe(path, len(audio) / RATE, "id")
    assert [w for _, _, w in _words(res)].count("f700") == 8      # ucapan tengah tetap ditranskripsi (fallback)


def test_quiet_interval_is_not_transcribed(monkeypatch):
    monkeypatch.setattr(chunked, "_speech_regions", lambda a: [(0, 2 * RATE), (6 * RATE, 8 * RATE)])
    monkeypatch.setattr(config, "WHISPER_CHUNK_MAX_SECONDS", 10.0)
    buf = np.concatenate([0.3 * np.ones(2 * RATE), 0.0005 * np.ones(4 * RATE), 0.3 * np.ones(2 * RATE)]).astype(
        np.float32)
    chunks, consumed = chunked.plan(buf, final=True)
    assert [(s, e, k) for s, e, k in chunks] == [(0, 2 * RATE, "speech"), (6 * RATE, 8 * RATE, "speech")]
    assert consumed == len(buf)


def test_gap_segments_rejected_when_whisper_says_no_speech(monkeypatch):
    class NoSpeech:
        def transcribe(self, audio, **kw):
            seg = NS(start=0.0, end=1.0, text=" Terima kasih", words=[NS(start=0, end=1, word=" Terima")],
                     no_speech_prob=0.9, avg_logprob=-1.5)
            return iter([seg]), NS(language="id")
    segs, _ = chunked._transcribe_piece(NoSpeech(), np.zeros(RATE, np.float32), "id", 10.0, "gap")
    assert segs == []                                            # halusinasi di interval non-VAD dibuang
    segs, _ = chunked._transcribe_piece(NoSpeech(), np.zeros(RATE, np.float32), "id", 10.0, "speech")
    assert segs and segs[0]["start"] == 10.0                     # ucapan VAD tidak difilter tambahan


@pytest.mark.parametrize("fmt", ["aac", "ts"])
def test_compressed_and_unindexed_files_stream(tone, tmp_path, fmt):
    path = tmp_path / f"e.{'m4a' if fmt == 'aac' else 'ts'}"
    plan = []
    for i in range(8):
        plan += [(3.0, 400 + 40 * i), (1.5, 0)]
    _tones(path, plan, fmt=fmt)
    res = transcribe.transcribe(path, 36.0, "id")
    assert len({w for _, _, w in _words(res)}) == 8              # semua ucapan, berkas tanpa seek (MPEG-TS) juga


def test_corrupt_file_raises_and_returns_nothing(tone, tmp_path):
    bad = tmp_path / "rusak.mp4"
    bad.write_bytes(b"\x00\x01bukan video" * 1000)
    with pytest.raises(media.MediaError):
        transcribe.transcribe(bad, 10.0, "id")


def test_cancel_while_model_processes_chunk(tone, tmp_path, monkeypatch):
    plan = []
    for i in range(10):
        plan += [(3.0, 300 + 20 * i), (1.5, 0)]
    path = tmp_path / "f.wav"
    _tones(path, plan)
    control = jobcontrol.JobControl("uji-batal", timeout=600)
    original = tone.transcribe

    def slow(audio, **kw):
        if len(tone.calls) == 3:
            control.cancel("dibatalkan")                         # dibatalkan saat model memproses potongan ke-4
        return original(audio, **kw)
    monkeypatch.setattr(tone, "transcribe", slow)
    box = {}

    def run():
        with jobcontrol.activate(control):
            try:
                box["result"] = transcribe.transcribe(path, 45.0, "id")
            except BaseException as exc:
                box["error"] = exc
    t = threading.Thread(target=run)
    t.start()
    t.join(30)
    assert isinstance(box.get("error"), jobcontrol.JobCancelled) and "result" not in box
    assert len(tone.calls) <= 5                                  # berhenti cepat, tidak menyelesaikan semua potongan


def test_admission_need_follows_flag(monkeypatch):
    monkeypatch.setattr(config, "WHISPER_CHUNKED", True)
    assert config.analysis_need_mb(6 * 3600, "small") == config.analysis_need_mb(600, "small")
    monkeypatch.setattr(config, "WHISPER_CHUNKED", False)
    assert config.analysis_need_mb(6 * 3600, "small") > config.analysis_need_mb(600, "small")


def _ffmpeg_children(path):
    """Decoder FFmpeg milik tes ini saja (worker tanam dari tes lain bisa menjalankan FFmpeg lain bersamaan)."""
    import os
    out = subprocess.run(["ps", "-ww", "-o", "pid=,args=", "--ppid", str(os.getpid())], capture_output=True,
                         text=True).stdout                     # -ww: argumen tidak dipotong lebar kolom
    return [ln for ln in out.splitlines() if "ffmpeg" in ln and str(path) in ln]


def test_decoder_stopped_when_model_fails_midway(tone, tmp_path, monkeypatch):
    """W5 review: dulu FFmpeg tetap hidup selama exception masih dirujuk pemanggil (generator tidak ditutup)."""
    path = tmp_path / "g.wav"
    plan = []
    for i in range(40):
        plan += [(3.0, 300 + 10 * i), (1.5, 0)]
    _tones(path, plan)
    calls = []

    def boom(audio, **kw):
        calls.append(1)
        if len(calls) == 2:
            raise RuntimeError("model gagal")
        return iter([]), NS(language="id")
    monkeypatch.setattr(tone, "transcribe", boom)
    kept = []
    try:
        transcribe.transcribe(path, 180.0, "id")
    except RuntimeError as exc:
        kept.append(exc)                                         # traceback masih hidup
    assert kept and _ffmpeg_children(path) == []


def test_chunked_path_never_decodes_whole_audio(tone, tmp_path, monkeypatch):
    def forbidden(*a, **k):
        raise AssertionError("decode seluruh audio dipanggil di jalur potongan")
    monkeypatch.setattr(media, "decode_audio", forbidden)
    path = tmp_path / "h.wav"
    audio = _tones(path, [(3.0, 400), (1.5, 0), (3.0, 500)])
    res = transcribe.transcribe(path, len(audio) / RATE, None)
    assert {w for _, _, w in _words(res)} == {"f400", "f500"}


def test_merge_short_regions_off_by_default_and_bounded(monkeypatch):
    regs = [(0, RATE // 2), (RATE, 4 * RATE), (int(4.3 * RATE), int(4.6 * RATE)), (10 * RATE, 12 * RATE)]
    assert config.WHISPER_CHUNK_MIN_SECONDS == 0 and chunked._merge_short(regs) == regs     # bawaan: tidak berubah
    monkeypatch.setattr(config, "WHISPER_CHUNK_MIN_SECONDS", 1.0)
    monkeypatch.setattr(config, "WHISPER_CHUNK_MERGE_GAP_SECONDS", 1.0)
    monkeypatch.setattr(config, "WHISPER_CHUNK_MAX_SECONDS", 10.0)
    merged = chunked._merge_short(regs)
    # 0,5 dtk digabung dengan berikutnya (jeda 0,5); 0,3 dtk digabung (jeda 0,3); wilayah terpisah 5,4 dtk tetap.
    assert merged == [(0, int(4.6 * RATE)), (10 * RATE, 12 * RATE)]
    monkeypatch.setattr(config, "WHISPER_CHUNK_MAX_SECONDS", 3.0)       # tidak pernah melewati batas potongan
    assert all(e - s <= 4 * RATE for s, e in chunked._merge_short(regs))


def test_merge_keeps_every_word(tone, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "WHISPER_CHUNK_MIN_SECONDS", 2.0)
    plan = []
    for i in range(12):                  # ucapan pendek 1 dtk, jeda 1 dtk (sejajar bingkai 0,5 dtk model tiruan)
        plan += [(1.0, 300 + 20 * i), (1.0, 0)]
    path = tmp_path / "m.wav"
    audio = _tones(path, plan)
    res = transcribe.transcribe(path, len(audio) / RATE, "id")
    _same(_words(res), _frame_words(audio))
    assert len(tone.calls) < 12                                   # potongan pendek benar-benar digabung


def test_segment_filter_needs_two_bad_signals(monkeypatch):
    seg = lambda nsp, lp, cr=1.5: NS(no_speech_prob=nsp, avg_logprob=lp, compression_ratio=cr)  # noqa: E731
    assert chunked._low_quality(seg(0.9, -1.0), 5.0)              # no_speech tinggi & logprob rendah
    assert not chunked._low_quality(seg(0.9, -0.3), 5.0)           # ucapan lirih tapi yakin: tetap
    assert not chunked._low_quality(seg(0.1, -1.5), 5.0)           # logprob rendah saja: tetap
    assert chunked._low_quality(seg(0.1, -1.2, cr=3.0), 5.0)       # berulang & logprob rendah
    assert chunked._low_quality(seg(0.4, -0.9), 0.6)               # potongan < 1 dtk & dua sinyal sedang
    assert not chunked._low_quality(seg(0.4, -0.9), 3.0)


def test_segment_filter_off_by_default():
    assert config.WHISPER_SEGMENT_FILTER is False

    class Bad:
        def transcribe(self, audio, **kw):
            s = NS(start=0.0, end=1.0, text=" halo", words=[NS(start=0, end=1, word=" halo")], no_speech_prob=0.9,
                   avg_logprob=-1.2, compression_ratio=1.0)
            return iter([s]), NS(language="id")
    segs, _ = chunked._transcribe_piece(Bad(), np.zeros(RATE, np.float32), "id", 0.0, "speech")
    assert segs                                                   # bawaan: perilaku lama, tidak disaring
