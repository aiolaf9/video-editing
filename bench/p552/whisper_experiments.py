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

from whisper_quality import boundary_errors, wer_cer  # noqa: E402


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
    if mode == "temp0" or vad_threshold:
        original = model_obj.transcribe

        def patched(audio, **kw):
            if mode == "temp0":
                kw["temperature"] = 0.0
            if vad_threshold:
                kw["vad_parameters"] = {"threshold": float(vad_threshold)}
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
           "words": [w for s in res["segments"] for w in s.get("words", [])], "cgroup": cgroup_stats()}
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


def exp_det(args, sets, out):
    for set_name in args.sets.split(","):
        s = sets[set_name]
        ref = s["text"] if "text" in s else " ".join(b["text"] for b in s["bounds"])
        for mode in ("default", "seed", "temp0"):
            for run in range(1, args.runs + 1):
                row = child(args, args.models.split(",")[0], s["file"], mode)
                if "text" in row:
                    m = wer_cer(ref, row["text"])
                    row["wer"] = round(m["word_errors"] / max(1, m["ref_words"]), 4)
                    row["cer"] = round(m["char_errors"] / max(1, m["ref_chars"]), 4)
                emit(out, {"exp": "det", "set": set_name, "run": run, **row})


def speech_bounds_at(path, db, min_sil=0.15):
    err = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(path), "-af", f"silencedetect=n={db}dB:d={min_sil}",
                          "-f", "null", "-"], capture_output=True, text=True).stderr
    d = duration(path)
    ends = [float(x) for x in re.findall(r"silence_end: ([0-9.]+)", err)]
    starts = [float(x) for x in re.findall(r"silence_start: ([0-9.]+)", err)]
    begin = ends[0] if starts and starts[0] < 0.05 and ends else 0.0
    end = starts[-1] if starts and (not ends or starts[-1] > ends[-1]) else d
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
    model = args.models.split(",")[0]
    first, last, failed = [], [], []
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
    emit(out, {"exp": "gt", "what": f"kata pertama/terakhir {model} vs awal/akhir suara (-35 dB), ucapan tunggal",
               "n": len(first), "failed": failed, "first_word_minus_onset_ms": first, "last_word_minus_offset_ms": last,
               "first_abs_median_ms": statistics.median(abs(x) for x in first) if first else None,
               "last_abs_median_ms": statistics.median(abs(x) for x in last) if last else None})


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
    ap.add_argument("--exp", choices=("mem", "conc", "det", "gt", "noise"))
    ap.add_argument("--models", default="small")
    ap.add_argument("--minutes", default="10,30,60")
    ap.add_argument("--sets", default="id_long,id_noisy_snr10")
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--one", nargs=5, help=argparse.SUPPRESS)
    a = ap.parse_args()
    if a.one:
        run_one(*a.one)
        return
    sets = json.loads((Path(a.corpus) / "corpus.json").read_text())["sets"]
    out = Path(a.out)
    {"mem": exp_mem, "conc": exp_conc, "det": exp_det, "gt": exp_gt, "noise": exp_noise}[a.exp](a, sets, out)


if __name__ == "__main__":
    main()
