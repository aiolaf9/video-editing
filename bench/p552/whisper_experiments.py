"""P5.5.2 remediasi W3/W5/W6/E: eksperimen Whisper ASLI terisolasi (runner GitHub, image produksi).

Semua eksperimen memanggil jalur aplikasi (app.transcribe.transcribe) di subprocess terpisah; variasi decoding
diterapkan dengan membungkus WhisperModel.transcribe HANYA di proses eksperimen (default produksi tidak diubah).

  --exp mem    W5: puncak RSS (VmHWM) & RSS setelah muat model, per model x durasi ucapan nyata (10/30/60 mnt,
               id_long FLEURS diulang). Memisahkan memori tetap (model) dari memori yang tumbuh dengan durasi.
  --exp conc   W5: dua transkripsi bersamaan di container ber-batas memori (jalankan di `docker run --memory`):
               puncak cgroup & event OOM dari memory.events.
  --exp det    W6: mode default / seed (ctranslate2.set_random_seed) / temp0 (temperature=0 saja), N run:
               hash teks, WER, RTF, jumlah fallback temperature (log faster-whisper).
  --exp gt     W3: sensitivitas acuan batas ucapan (silencedetect -30/-35/-40/-45 dB) & selisih kata pertama
               vs awal suara pada ucapan tunggal (memisahkan batas ucapan dari timestamp kata).
  --exp noise  E: varian praproses pada set SNR 10 dB (asli, afftdn, highpass+lowpass, afftdn+loudnorm) &
               ambang VAD; WER, RTF, RSS. Model sama, decoding temp0 agar perbedaan bukan karena sampling.

    python bench/p552/whisper_experiments.py <folder-korpus> <keluaran.jsonl> --exp mem --models tiny,base,small
"""
import argparse
import hashlib
import json
import logging
import os
import re
import resource
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from whisper_quality import boundary_errors, norm, wer_cer  # noqa: E402


def vm(key):
    return int(Path("/proc/self/status").read_text().split(f"{key}:")[1].split()[0]) / 1024


def ffmpeg(*args):
    subprocess.run(["ffmpeg", "-hide_banner", "-v", "error", "-y", *args], check=True)


def duration(p) -> float:
    return float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                                 str(p)], capture_output=True, text=True, check=True).stdout)


def cgroup_stats():
    """Memori cgroup container (MiB, sama dengan satuan RSS proses). TERPISAH dari VmHWM proses: cgroup mencakup semua
    proses di container + page cache; jangan dicampur."""
    cg = Path("/sys/fs/cgroup")
    out = {}
    for f, key in (("memory.peak", "cgroup_peak_mb"), ("memory.max", "cgroup_limit_mb"),
                   ("memory.current", "cgroup_current_mb")):
        if (cg / f).exists():
            raw = (cg / f).read_text().strip()
            out[key] = raw if raw == "max" else round(int(raw) / 2**20, 1)
    if (cg / "memory.events").exists():
        ev = dict(line.split() for line in (cg / "memory.events").read_text().splitlines())
        out["cgroup_oom"] = int(ev.get("oom", 0))
        out["cgroup_oom_kill"] = int(ev.get("oom_kill", 0))
    return out


def verify_decode(path) -> dict:
    """Durasi metadata (ffprobe) vs jumlah sampel hasil decode aplikasi (16 kHz) — dijalankan di proses INDUK, di luar
    pengukuran memori proses transkripsi."""
    from app import media
    meta = duration(path)
    samples = len(media.decode_audio(Path(path)))
    return {"meta_s": round(meta, 2), "decoded_s": round(samples / 16000, 2),
            "decode_ok": abs(samples / 16000 - meta) < 0.5}


def repeated(src: Path, minutes: int, work: Path) -> Path:
    """Ucapan nyata (id_long FLEURS) diulang sampai `minutes` menit — untuk memori, bukan kualitas."""
    out = work / f"speech_{minutes}m.wav"
    if not out.exists():
        reps = int(minutes * 60 // duration(src)) + 1
        lst = work / f"list_{minutes}.txt"
        lst.write_text("\n".join(f"file '{src}'" for _ in range(reps)))
        ffmpeg("-f", "concat", "-safe", "0", "-i", str(lst), "-t", str(minutes * 60), "-ar", "16000", "-ac", "1",
               str(out))
    return out


# ------------------------------------------------------------------ satu transkripsi (subprocess)

def run_one(model: str, path: str, mode: str, out_path: str, vad_threshold: str = ""):
    os.environ["WHISPER_MODEL"] = model
    from app import transcribe

    fallbacks = []

    class Count(logging.Handler):
        def emit(self, record):
            msg = record.getMessage()
            if "temperature" in msg.lower() and ("threshold" in msg.lower() or "fallback" in msg.lower()):
                fallbacks.append(msg[:160])

    lg = logging.getLogger("faster_whisper")
    lg.setLevel(logging.DEBUG)
    lg.addHandler(Count())
    if mode == "seed":
        import ctranslate2
        ctranslate2.set_random_seed(1234)
    base = vm("VmRSS")
    t0 = time.perf_counter()
    model_obj = transcribe._get_model()
    load_s = time.perf_counter() - t0
    after_load = vm("VmRSS")
    original, used = model_obj.transcribe, {}

    def patched(audio, **kw):
        if mode == "temp0":
            kw["temperature"] = 0.0
        if vad_threshold:
            kw["vad_parameters"] = {"threshold": float(vad_threshold)}
        # Parameter decoding yang benar-benar dipakai (selain default library yang tidak dioper aplikasi).
        used.update({k: (v if k != "initial_prompt" else bool(v)) for k, v in kw.items()})
        return original(audio, **kw)
    model_obj.transcribe = patched
    dur = duration(path)
    t1 = time.perf_counter()
    res = transcribe.transcribe(Path(path), dur, None)
    proc_s = time.perf_counter() - t1
    text = " ".join(s["text"] for s in res["segments"])
    ru = resource.getrusage(resource.RUSAGE_SELF)
    out = {"model": model, "mode": mode, "audio_s": round(dur, 1), "load_s": round(load_s, 2),
           "transcribe_s": round(proc_s, 1), "rtf": round(proc_s / dur, 4), "rss_start_mb": round(base, 1),
           "rss_after_load_mb": round(after_load, 1), "peak_rss_mb": round(vm("VmHWM"), 1),
           "growth_over_load_mb": round(vm("VmHWM") - after_load, 1), "cpu_s": round(ru.ru_utime + ru.ru_stime, 1),
           "fallbacks": len(fallbacks), "fallback_examples": fallbacks[:3], "language": res.get("language"),
           "text_sha": hashlib.sha256(text.encode()).hexdigest()[:16], "text": text,
           "words": [w for s in res["segments"] for w in s.get("words", [])], "cgroup": cgroup_stats(),
           "decoding": {**{k: v for k, v in used.items() if k in ("temperature", "beam_size", "vad_filter",
                                                                   "vad_parameters", "word_timestamps", "language",
                                                                   "initial_prompt")},
                        "temperature": used.get("temperature", "default library [0.0..1.0]"),
                        "seed": 1234 if mode == "seed" else None}}
    Path(out_path).write_text(json.dumps(out, ensure_ascii=False))


CHILD_TIMEOUT_S = 3600      # batas per proses transkripsi (job 240 mnt / step 210 mnt tetap berlaku di atasnya)


def oom_kills():
    """Penghitung oom_kill cgroup container (None bila tidak tersedia)."""
    return cgroup_stats().get("cgroup_oom_kill")


def classify(returncode, timed_out: bool, oom_before, oom_after) -> dict:
    """Status eksplisit. OOM HANYA bila penghitung oom_kill cgroup bertambah (bukti), bukan dari exit code 137/-9.
    Dibunuh tanpa bukti OOM -> killed_unverified. Selain ok, tidak ada status yang dihitung PASS."""
    delta = None if oom_before is None or oom_after is None else oom_after - oom_before
    if timed_out:
        status = "timeout"
    elif returncode == 0:
        status = "ok"
    elif delta:
        status = "oom"
    elif returncode in (-9, 137):
        status = "killed_unverified"
    else:
        status = "error"
    return {"status": status, "returncode": returncode, "oom_kill_delta": delta}


def child(args, model, path, mode, vad=""):
    tmp = Path(tempfile.mkstemp(suffix=".json")[1])
    cmd = [sys.executable, __file__, "--one", model, str(path), mode, str(tmp), vad]
    before, timed_out, rc, err = oom_kills(), False, None, ""
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, env={**os.environ, "HF_HUB_OFFLINE": "0"},
                           timeout=CHILD_TIMEOUT_S)
        rc, err = p.returncode, p.stderr
    except subprocess.TimeoutExpired:      # proses anak dibunuh oleh subprocess.run; dicatat, bukan PASS
        timed_out, err = True, f"timeout {CHILD_TIMEOUT_S} dtk"
    st = classify(rc, timed_out, before, oom_kills())
    if st["status"] != "ok":
        return {"model": model, "mode": mode, "error": err.strip()[-600:], **st}
    return {**json.loads(tmp.read_text()), **st}


def emit(out: Path, row: dict):
    slim = {k: v for k, v in row.items() if k not in ("text", "words")}
    with out.open("a") as f:
        f.write(json.dumps(slim, ensure_ascii=False) + "\n")
    print(json.dumps(slim, ensure_ascii=False), flush=True)


# ------------------------------------------------------------------ eksperimen

def exp_mem(args, sets, out):
    work = Path(tempfile.mkdtemp())
    src = Path(sets["id_long"]["file"])
    from app import config
    for model in args.models.split(","):
        for minutes in [int(m) for m in args.minutes.split(",")]:
            path = repeated(src, minutes, work)
            row = child(args, model, path, "default")
            # Puncak cgroup di sini KUMULATIF untuk seluruh container (semua run sebelumnya); hanya konteks, bukan
            # angka per run. Angka per run = peak_rss_mb (VmHWM proses).
            row["container_cumulative"] = cgroup_stats()
            emit(out, {"exp": "mem", "minutes": minutes, **verify_decode(path),
                       "scheduler_estimate_mb": config._WHISPER_MEM.get(model), **row})


def exp_conc(args, sets, out):
    """Dua proses transkripsi serentak (dalam container ber-batas memori). Mencatat hasil tiap proses & cgroup."""
    from app import resources
    work = Path(tempfile.mkdtemp())
    path = repeated(Path(sets["id_long"]["file"]), int(args.minutes.split(",")[0]), work)
    os.environ["WHISPER_MODEL"] = args.models.split(",")[0]
    procs, decisions, oom_before = [], [], oom_kills()
    for i in range(2):
        # Keputusan admission aplikasi SAAT INI (perkiraan per model, tanpa durasi) sebelum tiap proses dimulai.
        ok, why = resources.admit_memory("analisis")
        decisions.append({"process": i, "admit": ok, "reason": why,
                          "required_mb": resources.required_mb("analisis")})
        time.sleep(15 if i == 0 else 0)          # proses pertama sempat memuat model sebelum keputusan kedua
        tmp = work / f"conc{i}.json"
        procs.append((tmp, subprocess.Popen([sys.executable, __file__, "--one", args.models.split(",")[0], str(path),
                                              "default", str(tmp), ""], stdout=subprocess.PIPE,
                                             stderr=subprocess.PIPE, text=True)))
    done = []
    for tmp, p in procs:
        timed_out = False
        try:
            _, err = p.communicate(timeout=CHILD_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            timed_out = True
            p.kill()
            _, err = p.communicate()
        done.append((tmp, p, timed_out, err))
    # Kedua proses berbagi satu cgroup: bukti OOM (oom_kill bertambah) berlaku untuk proses yang dibunuh SIGKILL.
    oom_after, rows = oom_kills(), []
    for tmp, p, timed_out, err in done:
        st = classify(p.returncode, timed_out, oom_before, oom_after)
        r = {**json.loads(tmp.read_text()), **st} if st["status"] == "ok" and tmp.exists() else \
            {**st, "error": err.strip()[-400:]}
        rows.append(r)
    cg = cgroup_stats()
    emit(out, {"exp": "conc", "audio": verify_decode(path), "admission": decisions,
               "processes": [{k: r.get(k) for k in ("status", "returncode", "peak_rss_mb", "rtf", "error")}
                             for r in rows], **cg,
               "oom_kill_delta": None if oom_before is None or oom_after is None else oom_after - oom_before,
               "result": "OOM" if any(r["status"] == "oom" for r in rows)
               else ("OK" if all(r["status"] == "ok" for r in rows) else "NOT_OK")})


def edit_ops(ref: list, hyp: list) -> dict:
    """Jumlah substitusi / penyisipan (kata tambahan, indikasi halusinasi) / penghapusan (teks hilang)."""
    n, m = len(ref), len(hyp)
    d = [[(0, 0, 0, 0)] * (m + 1) for _ in range(n + 1)]   # (biaya, sub, ins, del)
    for i in range(1, n + 1):
        d[i][0] = (i, 0, 0, i)
    for j in range(1, m + 1):
        d[0][j] = (j, 0, j, 0)
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            c, sb, ins, de = d[i - 1][j - 1]
            best = (c + (ref[i - 1] != hyp[j - 1]), sb + (ref[i - 1] != hyp[j - 1]), ins, de)
            c, sb, ins, de = d[i][j - 1]
            best = min(best, (c + 1, sb, ins + 1, de))
            c, sb, ins, de = d[i - 1][j]
            best = min(best, (c + 1, sb, ins, de + 1))
            d[i][j] = best
    _, sb, ins, de = d[n][m]
    return {"ref_words": n, "hyp_words": m, "sub": sb, "ins": ins, "del": de}


def repetition(text: str) -> dict:
    """Indikator pengulangan: rasio kompresi zlib teks keluaran & porsi 4-gram yang berulang."""
    import zlib
    words = norm(text)
    grams = [tuple(words[i:i + 4]) for i in range(max(0, len(words) - 3))]
    raw = text.encode()
    return {"compression_ratio": round(len(raw) / max(1, len(zlib.compress(raw))), 2),
            "repeated_4gram_frac": round(1 - len(set(grams)) / len(grams), 4) if grams else 0.0}


class CorpusSchemaError(RuntimeError):
    pass


def set_audio(name: str, s: dict, work: Path) -> tuple[Path, str]:
    """Satu berkas audio + teks acuan untuk sebuah set korpus, sesuai skemanya:
    timeline   : wajib `file` & `bounds` (batas ucapan; teks acuan = gabungan teks bounds)
    utterances : wajib `items` [{file, text}] (tanpa batas ucapan); digabung jadi satu berkas, jeda 0,4 dtk.
    Kunci wajib yang hilang = kegagalan eksplisit (bukan dilewati)."""
    kind = s.get("kind")
    if kind == "timeline":
        missing = [k for k in ("file", "bounds") if k not in s]
        if missing or not s["bounds"]:
            raise CorpusSchemaError(f"set {name} (timeline): kunci wajib hilang/kosong {missing or ['bounds']}")
        return Path(s["file"]), " ".join(b["text"] for b in s["bounds"])
    if kind == "utterances":
        items = s.get("items") or []
        bad = [i for i, it in enumerate(items) if "file" not in it or "text" not in it]
        if not items or bad:
            raise CorpusSchemaError(f"set {name} (utterances): items kosong atau tanpa file/text di indeks {bad}")
        joined = work / f"{name}_joined.wav"
        if not joined.exists():
            gap = work / "gap04.wav"
            ffmpeg("-f", "lavfi", "-i", "anullsrc=r=16000:cl=mono", "-t", "0.4", str(gap))
            lst = work / f"{name}.txt"
            lst.write_text("\n".join(f"file '{p}'" for it in items for p in (it["file"], gap)))
            ffmpeg("-f", "concat", "-safe", "0", "-i", str(lst), "-ar", "16000", "-ac", "1", str(joined))
        return joined, " ".join(it["text"] for it in items)
    raise CorpusSchemaError(f"set {name}: kind tidak dikenal {kind!r}")


def det_summary(set_name: str, mode: str, rows: list[dict]) -> dict:
    ok = [r for r in rows if r.get("status") == "ok"]
    return {"exp": "det", "summary": True, "set": set_name, "mode": mode, "runs": len(rows), "ok": len(ok),
            "failed": [{"run": r["run"], "status": r.get("status")} for r in rows if r.get("status") != "ok"],
            "distinct_text_sha": sorted({r["text_sha"] for r in ok}),
            "identical": len(ok) == len(rows) and len({r["text_sha"] for r in ok}) == 1,
            "wer": sorted({r["wer"] for r in ok}), "cer": sorted({r["cer"] for r in ok}),
            "rtf_median": statistics.median(r["rtf"] for r in ok) if ok else None,
            "fallbacks": sorted({r["fallbacks"] for r in ok}),
            "ins_del": sorted({(r["ops"]["ins"], r["ops"]["del"]) for r in ok}),
            "max_repeated_4gram_frac": max((r["repetition"]["repeated_4gram_frac"] for r in ok), default=None),
            "peak_rss_mb": [min(r["peak_rss_mb"] for r in ok), max(r["peak_rss_mb"] for r in ok)] if ok else None,
            "decoding": ok[0]["decoding"] if ok else None}


def exp_det(args, sets, out):
    work = Path(tempfile.mkdtemp())
    resolved = {name: set_audio(name, sets[name], work) for name in args.sets.split(",")}   # skema dicek di awal
    for set_name, (path, ref) in resolved.items():
        for mode in ("default", "seed", "temp0"):
            rows = []
            for run in range(1, args.runs + 1):
                row = child(args, args.models.split(",")[0], path, mode)
                if row.get("status") == "ok":
                    m = wer_cer(ref, row["text"])
                    row["wer"] = round(m["word_errors"] / max(1, m["ref_words"]), 4)
                    row["cer"] = round(m["char_errors"] / max(1, m["ref_chars"]), 4)
                    row["ops"] = edit_ops(norm(ref), norm(row["text"]))
                    row["repetition"] = repetition(row["text"])
                row = {"exp": "det", "set": set_name, "run": run, **row}
                rows.append(row)
                emit(out, row)
            emit(out, det_summary(set_name, mode, rows))


def speech_bounds_at(path, db, min_sil=0.15, legacy=False):
    """legacy=True: logika LAMA (sebelum a6b8a9c), hanya untuk membandingkan acuan lama vs baru."""
    err = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(path), "-af", f"silencedetect=n={db}dB:d={min_sil}",
                          "-f", "null", "-"], capture_output=True, text=True).stderr
    d = duration(path)
    ends = [float(x) for x in re.findall(r"silence_end: ([0-9.]+)", err)]
    starts = [float(x) for x in re.findall(r"silence_start: ([0-9.]+)", err)]
    begin = ends[0] if starts and starts[0] < 0.05 and ends else 0.0
    # Hening di akhir berkas: FFmpeg mencetak silence_start lalu silence_end tepat di akhir berkas (EOF). Dulu kasus ini
    # dibaca sebagai "tidak ada hening akhir" sehingga akhir ucapan = akhir berkas (salah, P5.5.2 exp gt).
    trailing = starts and (not ends or starts[-1] > ends[-1] or (not legacy and abs(ends[-1] - d) < 0.05))
    end = starts[-1] if trailing else d
    if end <= begin and not legacy:          # berkas tanpa suara terdeteksi: tidak ada batas yang bermakna, pakai seluruh berkas
        begin, end = 0.0, d
    return begin, end


def exp_gt(args, sets, out):
    items = sets["id_clean"]["items"]
    spread = []
    for it in items:
        bs = {db: speech_bounds_at(it["file"], db) for db in (-30, -35, -40, -45)}
        starts, ends = [b[0] for b in bs.values()], [b[1] for b in bs.values()]
        spread.append({"start_spread_ms": round((max(starts) - min(starts)) * 1000),
                       "end_spread_ms": round((max(ends) - min(ends)) * 1000)})
    emit(out, {"exp": "gt", "what": "sensitivitas silencedetect -30..-45 dB (ucapan tunggal)", "n": len(spread),
               "start_spread_ms_median": statistics.median(x["start_spread_ms"] for x in spread),
               "start_spread_ms_max": max(x["start_spread_ms"] for x in spread),
               "end_spread_ms_median": statistics.median(x["end_spread_ms"] for x in spread),
               "end_spread_ms_max": max(x["end_spread_ms"] for x in spread)})
    diff = []
    for it in items:
        old, new = speech_bounds_at(it["file"], -35, legacy=True), speech_bounds_at(it["file"], -35)
        diff.append({"start_old_minus_new_ms": round((old[0] - new[0]) * 1000),
                     "end_old_minus_new_ms": round((old[1] - new[1]) * 1000)})
    emit(out, {"exp": "gt", "what": "acuan lama vs baru (-35 dB), ucapan tunggal yang sama", "n": len(diff),
               "start_old_minus_new_ms": [x["start_old_minus_new_ms"] for x in diff],
               "end_old_minus_new_ms": [x["end_old_minus_new_ms"] for x in diff],
               "end_changed": sum(x["end_old_minus_new_ms"] != 0 for x in diff),
               "end_old_minus_new_ms_median": statistics.median(x["end_old_minus_new_ms"] for x in diff)})
    model = args.models.split(",")[0]
    first, last, last_old, failed = [], [], [], []
    for it in items:
        row = child(args, model, it["file"], "temp0")
        if row["status"] != "ok":          # kegagalan dicatat, tidak dilewati diam-diam
            failed.append({"file": Path(it["file"]).name, "status": row["status"]})
            continue
        words = row.get("words") or []
        if not words:
            failed.append({"file": Path(it["file"]).name, "status": "no_words"})
            continue
        b, e = speech_bounds_at(it["file"], -35)
        first.append(round((words[0]["start"] - b) * 1000))
        last.append(round((words[-1]["end"] - e) * 1000))
        last_old.append(round((words[-1]["end"] - speech_bounds_at(it["file"], -35, legacy=True)[1]) * 1000))
    emit(out, {"exp": "gt", "what": f"kata pertama/terakhir {model} vs awal/akhir suara (-35 dB), ucapan tunggal",
               "n": len(first), "failed": failed, "first_word_minus_onset_ms": first, "last_word_minus_offset_ms": last,
               "first_abs_median_ms": statistics.median(abs(x) for x in first) if first else None,
               "last_abs_median_ms": statistics.median(abs(x) for x in last) if last else None,
               "last_word_minus_offset_ms_acuan_lama": last_old,
               "last_abs_median_ms_acuan_lama": statistics.median(abs(x) for x in last_old) if last_old else None})


NOISE_VARIANTS = {
    "asli": None,
    "afftdn": "afftdn=nf=-25",
    "bandpass": "highpass=f=100,lowpass=f=7000",
    "afftdn_loudnorm": "afftdn=nf=-25,loudnorm=I=-20",
}


def exp_noise(args, sets, out):
    s = sets["id_noisy_snr10"]
    ref = " ".join(b["text"] for b in s["bounds"])
    work = Path(tempfile.mkdtemp())
    model = args.models.split(",")[0]
    for name, af in NOISE_VARIANTS.items():
        path = Path(s["file"])
        if af:
            path = work / f"{name}.wav"
            t0 = time.perf_counter()
            ffmpeg("-i", s["file"], "-af", af, "-ar", "16000", "-ac", "1", str(path))
            pre_s = time.perf_counter() - t0
        else:
            pre_s = 0.0
        for vad in ("", "0.35", "0.65"):
            if vad and name != "asli":
                continue
            row = child(args, model, path, "temp0", vad)
            if "text" in row:
                m = wer_cer(ref, row["text"])
                row["wer"] = round(m["word_errors"] / max(1, m["ref_words"]), 4)
                row["cer"] = round(m["char_errors"] / max(1, m["ref_chars"]), 4)
                row["timestamps"] = boundary_errors(s["bounds"], row["words"])
                row["timestamps"] = {k: row["timestamps"][k] for k in ("bounds", "start_found", "end_found")}
            emit(out, {"exp": "noise", "variant": name, "filter": af, "vad_threshold": vad or "bawaan 0.5",
                       "preprocess_s": round(pre_s, 2), **row})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("corpus", nargs="?")
    ap.add_argument("out", nargs="?")
    ap.add_argument("--exp", choices=("mem", "conc", "det", "gt", "noise", "diag"))
    ap.add_argument("--windows", default="30,60", help="diag: target jendela (dtk), dipisah koma")
    ap.add_argument("--models", default="small")
    ap.add_argument("--minutes", default="10,30,60")
    ap.add_argument("--sets", default="id_long,id_noisy_snr10")
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--one", nargs=5, help=argparse.SUPPRESS)
    a = ap.parse_args()
    if a.one:
        run_one(*a.one)
        return
    out = Path(a.out)
    if a.exp == "diag":
        from whisper_diag import run_diag
        run_diag(Path(a.corpus), out, a.sets.split(","), a.models.split(",")[0],
                 [float(w) for w in a.windows.split(",") if w], emit, norm, edit_ops, wer_cer)
        return
    sets = json.loads((Path(a.corpus) / "corpus.json").read_text())["sets"]
    {"mem": exp_mem, "conc": exp_conc, "det": exp_det, "gt": exp_gt, "noise": exp_noise}[a.exp](a, sets, out)


if __name__ == "__main__":
    main()
