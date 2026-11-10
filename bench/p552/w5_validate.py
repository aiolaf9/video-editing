"""P5.5.2 W5: validasi pipeline LAMA vs per POTONGAN (WHISPER_CHUNKED) lewat jalur aplikasi asli (app.transcribe).

Tiap run berjalan di proses anak sendiri (flag & model dibaca saat impor; RSS puncak per run bersih).

  w5q (kualitas): set ucapan en_clean, en_nodup, id_clean, id_nodup (digabung, timeline pasti) + id_long (timeline
       dari batas ucapan korpus). Mode: legacy, chunked, chunked_w60 (jendela decode 60 dtk = banyak batas jendela),
       per_utt (pembanding, pipeline lama per ucapan; set ucapan saja).
       Diukur: WER, CER, sub/ins/del, cakupan ucapan, ucapan hilang/parsial/berisi teks ganda, kata di luar ucapan,
       pengulangan 4-gram, ucapan yang melintasi batas jendela decode, RTF, RSS puncak, cgroup, status.
       Plus satu uji pembatalan saat model memproses potongan (jalur chunked).
  w5m (memori): ucapan nyata id_long diulang 30/60 menit, legacy vs chunked, di container 3 GiB (= worker-analisis).
       Status ok/oom/timeout/error berbasis bukti (oom_kill cgroup), bukan exit code.
Artifact: angka & cuplikan <= 120 karakter untuk ucapan bermasalah (data FLEURS CC-BY), tanpa transkrip penuh.
"""
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

MODES = {"legacy": {"WHISPER_CHUNKED": "0"}, "chunked": {"WHISPER_CHUNKED": "1"},
         "chunked_w60": {"WHISPER_CHUNKED": "1", "WHISPER_DECODE_WINDOW_SECONDS": "60"},
         "per_utt": {"WHISPER_CHUNKED": "0"}}
CHILD_TIMEOUT_S = 3600
EXCERPT = 120


def _vm(key):
    return int(Path("/proc/self/status").read_text().split(f"{key}:")[1].split()[0]) / 1024


# ------------------------------------------------------------------ proses anak: satu transkripsi lewat aplikasi

def child_main(spec_path: str, out_path: str) -> None:
    spec = json.loads(Path(spec_path).read_text())
    from app import transcribe
    if os.environ.get("W5_FAKE_MODEL"):        # hanya uji lokal instrumen benchmark (tanpa unduhan model)
        import importlib.util
        mspec = importlib.util.spec_from_file_location("w5_fake", os.environ["W5_FAKE_MODEL"])
        fake = importlib.util.module_from_spec(mspec)
        mspec.loader.exec_module(fake)
        model = fake.Model()
        transcribe._get_model = lambda force_cpu=False, name=None: model
        fake.patch()

    t0 = time.perf_counter()
    model_obj = transcribe._get_model()
    load_s = time.perf_counter() - t0
    diag = None
    if spec.get("diag"):                       # perekam per potongan (w5d), hanya di proses benchmark
        from w5_diag2 import install_spies
        diag = install_spies(model_obj)
    rss_load = _vm("VmRSS")
    t1 = time.perf_counter()
    if spec.get("cancel_after"):
        from app import jobcontrol
        control = jobcontrol.JobControl("w5-batal", timeout=3600)
        timer = threading.Timer(spec["cancel_after"], control.cancel, args=("dibatalkan",))
        box = {}
        with jobcontrol.activate(control):
            timer.start()
            try:
                transcribe.transcribe(Path(spec["file"]), spec["duration"], spec.get("language"))
                box["outcome"] = "selesai (tidak terbatalkan)"
            except jobcontrol.JobCancelled:
                box["outcome"] = "dibatalkan"
        stop_s = time.perf_counter() - t1 - spec["cancel_after"]
        Path(out_path).write_text(json.dumps({"outcome": box["outcome"], "stop_after_cancel_s": round(stop_s, 2)}))
        return
    results = []
    for f, d in spec["files"]:
        results.append(transcribe.transcribe(Path(f), d, spec.get("language")))
    proc_s = time.perf_counter() - t1
    out = {"load_s": round(load_s, 2), "transcribe_s": round(proc_s, 1), "rss_after_load_mb": round(rss_load, 1),
           "peak_rss_mb": round(_vm("VmHWM"), 1),
           "languages": sorted({r.get("language") for r in results if r.get("language")}),
           "segments": [[{"start": s["start"], "end": s["end"], "words": s["words"]} for s in r["segments"]]
                        for r in results], **({"diag": diag} if diag is not None else {})}
    Path(out_path).write_text(json.dumps(out, ensure_ascii=False))


def run_child(spec: dict, env_extra: dict) -> dict:
    from whisper_experiments import cgroup_stats, classify, oom_kills
    work = Path(tempfile.mkdtemp())
    spec_p, out_p = work / "spec.json", work / "out.json"
    spec_p.write_text(json.dumps(spec))
    before, timed_out, rc, err = oom_kills(), False, None, ""
    try:
        p = subprocess.run([sys.executable, __file__, "--child", str(spec_p), str(out_p)], capture_output=True,
                           text=True, timeout=CHILD_TIMEOUT_S, env={**os.environ, **env_extra, "HF_HUB_OFFLINE": "0"})
        rc, err = p.returncode, p.stderr
    except subprocess.TimeoutExpired:
        timed_out, err = True, f"timeout {CHILD_TIMEOUT_S} dtk"
    st = classify(rc, timed_out, before, oom_kills())
    res = json.loads(out_p.read_text()) if st["status"] == "ok" and out_p.exists() else {"error": err.strip()[-600:]}
    return {**st, **res, "cgroup": cgroup_stats()}


# ------------------------------------------------------------------ penilaian

def utterance_timeline(name: str, s: dict, work: Path):
    """(berkas audio, timeline [{idx,start,end,text,file?}], ucapan kumpulan?)"""
    from whisper_diag import build_joined, verify_timeline
    if s["kind"] == "utterances":
        joined, tl = build_joined(s["items"], work, name)
        check = verify_timeline(joined, tl, s["items"])
        if not check["timeline_ok"]:
            raise RuntimeError(f"timeline {name} tidak valid: {check}")
        return joined, tl, True
    bounds = s["bounds"]
    tl = []
    for i, b in enumerate(bounds):        # rentang ucapan i = awal suaranya .. awal suara ucapan berikutnya
        end = bounds[i + 1]["start"] if i + 1 < len(bounds) else s["duration"]
        tl.append({"idx": i, "start": max(0.0, b["start"] - 0.3), "end": end, "text": b["text"]})
    return Path(s["file"]), tl, False


def score(tl: list, words: list, window_s, norm, edit_ops, wer_cer, per_utterance: bool = False) -> dict:
    from whisper_diag import assign_words
    from whisper_experiments import repetition
    by, stray = assign_words(tl, words)
    rows = []
    for u in tl:
        hyp = " ".join(by[u["idx"]])
        o = edit_ops(norm(u["text"]), norm(hyp))
        status = "missing" if o["hyp_words"] == 0 else ("partial" if o["del"] > 0.5 * o["ref_words"] else "ok")
        doubled = o["hyp_words"] >= 1.7 * max(1, o["ref_words"])
        crosses = bool(window_s) and int(u["start"] // window_s) != int(u["end"] // window_s)
        rows.append({"idx": u["idx"], "status": status, "doubled": doubled, "crosses_window": crosses,
                     "ref": u["text"], "hyp": hyp, **o})
    ref_all = " ".join(u["text"] for u in tl)
    hyp_all = " ".join(r["hyp"] for r in rows)
    tot, wc = edit_ops(norm(ref_all), norm(hyp_all)), wer_cer(ref_all, hyp_all)
    cross = [r for r in rows if r["crosses_window"]]
    bad = [r for r in rows if r["status"] != "ok" or r["doubled"]]
    extra = {}
    if per_utterance:   # korpus uji publik (FLEURS) saja: teks hipotesis per ucapan untuk memeriksa sisipan di dalamnya
        extra["per_utterance"] = [{k: r[k] for k in ("idx", "status", "doubled", "ref_words", "hyp_words", "sub", "ins",
                                                     "del")} | {"ref": r["ref"][:300], "hyp": r["hyp"][:300]}
                                  for r in rows]
    return {**extra, "wer": round((tot["sub"] + tot["ins"] + tot["del"]) / max(1, tot["ref_words"]), 4),
            "cer": round(wc["char_errors"] / max(1, wc["ref_chars"]), 4), "ops": tot,
            "utterances": len(rows), "missing": sum(r["status"] == "missing" for r in rows),
            "partial": sum(r["status"] == "partial" for r in rows), "doubled": sum(r["doubled"] for r in rows),
            "coverage_pct": round(100 * sum(r["status"] != "missing" for r in rows) / max(1, len(rows)), 1),
            "stray_words": stray, "repetition": repetition(hyp_all),
            "window_crossing_utts": len(cross), "window_crossing_missing": sum(r["status"] == "missing" for r in cross),
            "examples": [{"idx": r["idx"], "status": r["status"], "doubled": r["doubled"],
                          "crosses_window": r["crosses_window"], "ref": r["ref"][:EXCERPT], "hyp": r["hyp"][:EXCERPT]}
                         for r in bad[:10]]}


def exp_w5q(args, sets, out, emit, norm, edit_ops, wer_cer):
    work = Path(tempfile.mkdtemp())
    failed = []
    for name in args.sets.split(","):
        joined, tl, is_utts = utterance_timeline(name, sets[name], work)
        dur = tl[-1]["end"] + (0.4 if is_utts else 0.0)
        for mode in ("legacy", "chunked", "chunked_w60", "per_utt"):
            if mode == "per_utt" and not is_utts:
                continue
            emit(out, {"exp": "w5q", "set": name, "mode": mode, "status": "started"})
            files = [[u["file"], u["end"] - u["start"]] for u in tl] if mode == "per_utt" else [[str(joined), dur]]
            r = run_child({"files": files, "language": None}, MODES[mode])
            row = {"exp": "w5q", "set": name, "mode": mode, "env": MODES[mode],
                   **{k: v for k, v in r.items() if k != "segments"}}
            if r["status"] == "ok":
                words = []
                for i, segs in enumerate(r["segments"]):
                    off = tl[i]["start"] if mode == "per_utt" else 0.0
                    words += [{**w, "start": w["start"] + off, "end": w["end"] + off} for s in segs for w in s["words"]]
                window = 60.0 if mode == "chunked_w60" else (300.0 if mode == "chunked" else None)
                row.update(score(tl, words, window, norm, edit_ops, wer_cer))
                row["audio_s"] = round(dur, 1)
                row["rtf"] = round(r["transcribe_s"] / dur, 4)
            else:
                failed.append(f"{name}/{mode}:{r['status']}")
            emit(out, row)
    # Pembatalan saat model memproses potongan (jalur chunked, model & audio nyata).
    joined, tl, _ = utterance_timeline("id_long", sets["id_long"], work)
    cancel_after = float(os.environ.get("W5_CANCEL_AFTER", "20"))
    r = run_child({"file": str(joined), "duration": tl[-1]["end"], "language": None, "cancel_after": cancel_after},
                  MODES["chunked"])
    emit(out, {"exp": "w5q", "what": "pembatalan", **{k: v for k, v in r.items() if k != "segments"}})
    if r["status"] != "ok" or r.get("outcome") != "dibatalkan":
        failed.append("pembatalan")
    emit(out, {"exp": "w5q", "what": "ringkasan", "failed": failed, "result": "OK" if not failed else "NOT_OK"})
    if failed:
        raise SystemExit(f"w5q: {failed}")


def exp_w5m(args, sets, out, emit):
    from whisper_experiments import repeated, verify_decode
    work = Path(tempfile.mkdtemp())
    src = Path(sets["id_long"]["file"])
    for minutes in [int(m) for m in args.minutes.split(",")]:
        path = repeated(src, minutes, work)
        for mode in ("chunked", "legacy"):
            emit(out, {"exp": "w5m", "minutes": minutes, "mode": mode, "status": "started"})
            r = run_child({"files": [[str(path), minutes * 60.0]], "language": "id"}, MODES[mode])
            from app.config import analysis_need_mb
            emit(out, {"exp": "w5m", "minutes": minutes, "mode": mode, **verify_decode(path),
                       "admission_need_mb": analysis_need_mb(minutes * 60, chunked=mode == "chunked"),
                       **{k: v for k, v in r.items() if k != "segments"},
                       "rtf": round(r["transcribe_s"] / (minutes * 60), 4) if r["status"] == "ok" else None})
    emit(out, {"exp": "w5m", "what": "ringkasan", "result": "OK"})


if __name__ == "__main__" and len(sys.argv) == 4 and sys.argv[1] == "--child":
    child_main(sys.argv[2], sys.argv[3])
