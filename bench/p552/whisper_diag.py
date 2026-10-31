"""P5.5.2 investigasi kualitas: kenapa audio gabungan kehilangan teks (en_clean: per ucapan WER 8,2%, gabungan 52,3%).

Audio identik dibandingkan pada beberapa mode, dengan satu model yang dimuat sekali (jalur aplikasi
app.transcribe.transcribe; variasi decoding HANYA di proses ini, default produksi tidak berubah):
  per_utt          : tiap ucapan ditranskripsi sendiri
  joined           : ucapan digabung (jeda 0,4 dtk), konfigurasi aplikasi
  joined_novad     : vad_filter=False                       -> pengaruh VAD
  joined_nocond    : condition_on_previous_text=False       -> pengaruh konteks antar-segmen
  joined_noprompt  : initial_prompt=None                    -> pengaruh prompt tanda baca
  win<N>           : gabungan dipotong per ±N dtk di jeda antar-ucapan, tiap potongan ditranskripsi terpisah

Per ucapan (timeline gabungan diketahui pasti dari durasi berkas) dicatat: kata hipotesis yang jatuh di rentang ucapan,
sub/ins/del terhadap teks acuan ucapan itu, status (ok/partial/missing), cakupan VAD, dan untuk ucapan yang hilang:
segmen mentah faster-whisper di sekitarnya (seek, start/end, no_speech_prob, avg_logprob) untuk membedakan
VAD-drop, segmen terlewat (seek melompat), kata hilang dalam segmen, dan salah penyelarasan.
Artifact terbatas: hanya cuplikan (<= 120 karakter) untuk ucapan bermasalah, tanpa transkrip penuh. Data: FLEURS CC-BY.
"""
import json
import os
import statistics
import subprocess
import tempfile
import time
from pathlib import Path

GAP = 0.4
EXCERPT = 120
MAX_EXAMPLES = 12


def _dur(p) -> float:
    return float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(p)],
                                capture_output=True, text=True, check=True).stdout)


def _ff(*a):
    subprocess.run(["ffmpeg", "-hide_banner", "-v", "error", "-y", *a], check=True)


def build_joined(items: list[dict], work: Path, name: str) -> tuple[Path, list[dict]]:
    """Gabungan + timeline ucapan pasti (start/end berkas ucapan pada timeline gabungan)."""
    gap = work / "gap.wav"
    if not gap.exists():
        _ff("-f", "lavfi", "-i", "anullsrc=r=16000:cl=mono", "-t", str(GAP), "-c:a", "pcm_s16le", str(gap))
    t, timeline, lines = 0.0, [], []
    for i, it in enumerate(items):
        d = _dur(it["file"])
        timeline.append({"idx": i, "start": round(t, 3), "end": round(t + d, 3), "text": it["text"],
                         "file": it["file"]})
        lines += [f"file '{it['file']}'", f"file '{gap}'"]
        t += d + GAP
    lst = work / f"{name}.txt"
    lst.write_text("\n".join(lines))
    out = work / f"{name}_joined.wav"
    _ff("-f", "concat", "-safe", "0", "-i", str(lst), "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", str(out))
    return out, timeline


def windows_at_gaps(timeline: list[dict], target: float) -> list[tuple[float, float, list[int]]]:
    """Potong di jeda antar-ucapan sehingga tiap jendela ±target dtk (tidak pernah memotong ucapan)."""
    out, cur, start = [], [], 0.0
    for u in timeline:
        cur.append(u["idx"])
        end = u["end"] + GAP / 2
        if end - start >= target:
            out.append((start, end, cur))
            cur, start = [], end
    if cur:
        out.append((start, timeline[-1]["end"] + GAP / 2, cur))
    return out


class Recorder:
    """Membungkus model.transcribe: menerapkan varian kwargs & merekam segmen mentah faster-whisper."""

    def __init__(self, model):
        self.model, self.original = model, model.transcribe
        self.variant, self.segments, self.kwargs = {}, [], {}
        model.transcribe = self

    def __call__(self, audio, **kw):
        kw.update(self.variant)
        self.kwargs = {k: (bool(v) if k == "initial_prompt" else v) for k, v in kw.items()}
        segs, info = self.original(audio, **kw)

        def gen():
            for s in segs:
                self.segments.append({"seek": getattr(s, "seek", None), "start": round(s.start, 2),
                                      "end": round(s.end, 2), "no_speech_prob": round(s.no_speech_prob, 3),
                                      "avg_logprob": round(s.avg_logprob, 3),
                                      "compression_ratio": round(s.compression_ratio, 2),
                                      "temperature": getattr(s, "temperature", None), "n_words": len(s.words or [])})
                yield s
        return gen(), info


def assign_words(timeline, words, pad=0.25):
    """Kata hipotesis -> ucapan berdasarkan titik tengah kata pada timeline gabungan."""
    per = {u["idx"]: [] for u in timeline}
    stray = 0
    for w in words:
        mid = (w["start"] + w["end"]) / 2
        hit = next((u["idx"] for u in timeline if u["start"] - pad <= mid <= u["end"] + pad), None)
        if hit is None:
            stray += 1
        else:
            per[hit].append(w["word"])
    return per, stray


def utterance_rows(timeline, hyp_by_utt, ops_fn, wer_fn):
    rows = []
    for u in timeline:
        hyp = " ".join(hyp_by_utt.get(u["idx"], []))
        o = ops_fn(u["text"], hyp)
        status = "missing" if o["hyp_words"] == 0 else ("partial" if o["del"] > 0.5 * o["ref_words"] else "ok")
        rows.append({"idx": u["idx"], "start": u["start"], "end": u["end"], "status": status, **o,
                     "wer": wer_fn(u["text"], hyp), "_ref": u["text"], "_hyp": hyp})
    return rows


def vad_coverage(audio, timeline):
    from faster_whisper.vad import VadOptions, get_speech_timestamps
    opts = VadOptions()
    chunks = get_speech_timestamps(audio, opts)
    cov = {}
    for u in timeline:
        a, b = int(u["start"] * 16000), int(u["end"] * 16000)
        inter = sum(max(0, min(b, c["end"]) - max(a, c["start"])) for c in chunks)
        cov[u["idx"]] = round(inter / max(1, b - a), 3)
    return {k: v for k, v in vars(opts).items()}, len(chunks), cov


def missing_evidence(u, segments):
    """Segmen mentah sebelum & sesudah ucapan yang hilang: apakah ia jatuh di celah antar-segmen (seek melompat)?"""
    before = [s for s in segments if s["end"] <= u["start"] + 0.25]
    after = [s for s in segments if s["start"] >= u["end"] - 0.25]
    prev, nxt = (before[-1] if before else None), (after[0] if after else None)
    return {"prev_segment": prev, "next_segment": nxt,
            "gap_s": round((nxt["start"] if nxt else u["end"]) - (prev["end"] if prev else u["start"]), 2)}


def run_diag(corpus: Path, out: Path, set_names: list[str], model_name: str, win_targets: list[float], emit, norm,
             edit_ops, wer_cer):
    os.environ["WHISPER_MODEL"] = model_name
    import ctranslate2
    import faster_whisper

    from app import media, transcribe

    sets = json.loads((corpus / "corpus.json").read_text())["sets"]
    work = Path(tempfile.mkdtemp())
    t0 = time.perf_counter()
    model = transcribe._get_model()
    rec = Recorder(model)
    emit(out, {"exp": "diag", "what": "konfigurasi", "model": model_name, "load_s": round(time.perf_counter() - t0, 2),
               "faster_whisper": faster_whisper.__version__, "ctranslate2": ctranslate2.__version__,
               "device": transcribe._model_device, "threads": transcribe.WHISPER_THREADS or os.cpu_count(),
               "compute_type": "int8" if transcribe._model_device == "cpu" else "default",
               "model_path": str(getattr(getattr(model, "model", None), "model_path", "") or ""),
               "corpus_revision": json.loads((corpus / "corpus.json").read_text()).get("revision")})

    def ops_fn(ref, hyp):
        return edit_ops(norm(ref), norm(hyp))

    def wer_fn(ref, hyp):
        m = wer_cer(ref, hyp)
        return round(m["word_errors"] / max(1, m["ref_words"]), 4)

    for name in set_names:
        s = sets[name]
        if s.get("kind") != "utterances" or not s.get("items"):
            raise RuntimeError(f"diag butuh set utterances: {name}")
        joined, timeline = build_joined(s["items"], work, name)
        audio = media.decode_audio(joined)
        vad_opts, n_chunks, cov = vad_coverage(audio, timeline)
        emit(out, {"exp": "diag", "set": name, "what": "audio", "utterances": len(timeline),
                   "joined_s": round(_dur(joined), 2), "timeline_end_s": timeline[-1]["end"],
                   "vad_options": vad_opts, "vad_chunks": n_chunks,
                   "vad_coverage_min": min(cov.values()), "vad_coverage_lt_50pct": [k for k, v in cov.items() if v < 0.5]})
        modes = [("per_utt", None, {}), ("joined", None, {}), ("joined_novad", None, {"vad_filter": False}),
                 ("joined_nocond", None, {"condition_on_previous_text": False}),
                 ("joined_noprompt", None, {"initial_prompt": None})]
        modes += [(f"win{int(w)}", w, {}) for w in win_targets]
        for mode, win, variant in modes:
            rec.variant, rec.segments = variant, []
            t1 = time.perf_counter()
            words_all, langs, seg_all = [], [], []
            if mode == "per_utt":
                hyp_by = {}
                for u in timeline:
                    r = transcribe.transcribe(Path(u["file"]), u["end"] - u["start"], None)
                    langs.append(r["language"])
                    hyp_by[u["idx"]] = [w["word"] for sg in r["segments"] for w in sg["words"]]
                stray = 0
            else:
                pieces = [(0.0, timeline[-1]["end"] + GAP, [u["idx"] for u in timeline])] if win is None \
                    else windows_at_gaps(timeline, win)
                for a, b, _ in pieces:
                    path = joined
                    if win is not None:
                        path = work / f"{name}_{mode}_{a:.1f}.wav"
                        _ff("-i", str(joined), "-ss", f"{a:.3f}", "-t", f"{b - a:.3f}", "-c:a", "pcm_s16le", str(path))
                    rec.segments = []
                    r = transcribe.transcribe(path, b - a, None)
                    langs.append(r["language"])
                    words_all += [{**w, "start": w["start"] + a, "end": w["end"] + a}
                                  for sg in r["segments"] for w in sg["words"]]
                    seg_all += [{**sg, "start": round(sg["start"] + a, 2), "end": round(sg["end"] + a, 2)}
                                for sg in rec.segments]
                hyp_by, stray = assign_words(timeline, words_all)
            elapsed = time.perf_counter() - t1
            rows = utterance_rows(timeline, hyp_by, ops_fn, wer_fn)
            ref_all = " ".join(u["text"] for u in timeline)
            hyp_all = " ".join(" ".join(hyp_by[u["idx"]]) for u in timeline)
            tot = edit_ops(norm(ref_all), norm(hyp_all))
            missing = [r for r in rows if r["status"] == "missing"]
            partial = [r for r in rows if r["status"] == "partial"]
            # Salah penyelarasan: hipotesis ucapan lebih cocok dengan teks ucapan tetangga daripada ucapannya sendiri.
            misaligned = []
            for r in rows:
                if r["hyp_words"] == 0:
                    continue
                own = r["wer"]
                for j in (r["idx"] - 1, r["idx"] + 1):
                    if 0 <= j < len(timeline) and wer_fn(timeline[j]["text"], r["_hyp"]) + 0.2 < own:
                        misaligned.append(r["idx"])
                        break
            worst = sorted([r for r in rows if r["status"] != "ok" or r["wer"] > 0.3], key=lambda r: -r["wer"])
            examples = [{"idx": r["idx"], "status": r["status"], "wer": r["wer"], "vad_coverage": cov[r["idx"]],
                         "ref": r["_ref"][:EXCERPT], "hyp": r["_hyp"][:EXCERPT],
                         **({"evidence": missing_evidence(r, seg_all)} if r["status"] == "missing" and seg_all else {})}
                        for r in worst[:MAX_EXAMPLES]]
            emit(out, {"exp": "diag", "set": name, "mode": mode, "variant": {k: v for k, v in variant.items()},
                       "decoding": rec.kwargs, "languages": sorted({x for x in langs if x}),
                       "seconds": round(elapsed, 1), "wer": round((tot["sub"] + tot["ins"] + tot["del"])
                                                                 / max(1, tot["ref_words"]), 4), "ops": tot,
                       "utt_missing": len(missing), "utt_partial": len(partial), "stray_words": stray,
                       "misaligned_utts": misaligned,
                       "missing_vad_coverage": [cov[r["idx"]] for r in missing],
                       "utt_wer_median": statistics.median(r["wer"] for r in rows),
                       "segments": len(seg_all) if mode != "per_utt" else None,
                       "seek_values": sorted({sg["seek"] for sg in seg_all if sg.get("seek") is not None})[:60],
                       "per_utt_status": [r["status"][0] for r in rows], "examples": examples})
