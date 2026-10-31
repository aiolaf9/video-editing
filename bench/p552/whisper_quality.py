"""P5.5.2-B: kualitas & kecepatan Whisper ASLI lewat fungsi aplikasi (app.transcribe.transcribe).

Per model & per set korpus (fleurs_corpus.py), N run di subprocess terpisah: waktu muat model, waktu transkripsi,
RTF (= waktu transkripsi / durasi audio), CPU, puncak RSS (VmHWM), bahasa terdeteksi (language=None, seperti
bawaan aplikasi), WER & CER (teks dinormalisasi: huruf kecil, tanpa tanda baca), dan selisih batas awal/akhir
ucapan vs kata Whisper terdekat (timeline). Tidak ada Whisper tiruan: model diunduh dari Hugging Face.

    python bench/p552/whisper_quality.py <folder-korpus> <keluaran.jsonl> --models tiny,base,small [--runs 3]
"""
import argparse
import hashlib
import json
import os
import re
import resource
import statistics
import subprocess
import sys
import time
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def norm(text: str) -> list[str]:
    t = unicodedata.normalize("NFKC", text).lower()
    t = re.sub(r"[^\w\s]|_", " ", t)
    return t.split()


def edit_distance(a: list, b: list) -> int:
    prev = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        cur = [i]
        for j, y in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (x != y)))
        prev = cur
    return prev[-1]


def wer_cer(ref: str, hyp: str) -> dict:
    r, h = norm(ref), norm(hyp)
    rc, hc = list("".join(r)), list("".join(h))
    return {"ref_words": len(r), "word_errors": edit_distance(r, h), "ref_chars": len(rc),
            "char_errors": edit_distance(rc, hc)}


def boundary_errors(bounds: list[dict], words: list[dict], window=1.5) -> dict:
    starts, ends = [], []
    for b in bounds:
        s = [w["start"] - b["start"] for w in words if abs(w["start"] - b["start"]) <= window]
        e = [w["end"] - b["end"] for w in words if abs(w["end"] - b["end"]) <= window]
        if s:
            starts.append(min(s, key=abs) * 1000)
        if e:
            ends.append(min(e, key=abs) * 1000)
    return {"bounds": len(bounds), "start_found": len(starts), "end_found": len(ends),
            "start_err_ms": [round(x) for x in starts], "end_err_ms": [round(x) for x in ends]}


def vmhwm_mb() -> float:
    return int(Path("/proc/self/status").read_text().split("VmHWM:")[1].split()[0]) / 1024


def child(model: str, set_name: str, corpus: Path, out: Path):
    os.environ["WHISPER_MODEL"] = model
    from app import transcribe

    data = json.loads((corpus / "corpus.json").read_text())["sets"][set_name]
    t0 = time.perf_counter()
    transcribe._get_model()
    load_s = time.perf_counter() - t0
    items = data["items"] if data["kind"] == "utterances" else [data]
    hyp_all, ref_all, words_all, langs, audio_s, proc_s = [], [], [], [], 0.0, 0.0
    for it in items:
        dur = it.get("duration") or 0.0
        t1 = time.perf_counter()
        r = transcribe.transcribe(Path(it["file"]), dur, None)
        proc_s += time.perf_counter() - t1
        audio_s += dur
        langs.append(r.get("language"))
        hyp_all.append(" ".join(s["text"] for s in r["segments"]))
        ref_all.append(it["text"] if "text" in it else " ".join(b["text"] for b in it["bounds"]))
        words_all = [w for s in r["segments"] for w in s.get("words", [])]
    acc = {"ref_words": 0, "word_errors": 0, "ref_chars": 0, "char_errors": 0}
    for ref, hyp in zip(ref_all, hyp_all):
        for k, v in wer_cer(ref, hyp).items():
            acc[k] += v
    ru = resource.getrusage(resource.RUSAGE_SELF)
    ruc = resource.getrusage(resource.RUSAGE_CHILDREN)
    res = {"model": model, "set": set_name, "lang_expected": data["lang"], "items": len(items),
           "speaker_source": data.get("speaker_source"), "two_speakers_verified": data.get("two_speakers_verified"),
           "lang_detected": dict((x, langs.count(x)) for x in set(langs)),
           "load_s": round(load_s, 2), "transcribe_s": round(proc_s, 2), "audio_s": round(audio_s, 1),
           "rtf": round(proc_s / audio_s, 4) if audio_s else None,
           "cpu_s": round(ru.ru_utime + ru.ru_stime + ruc.ru_utime + ruc.ru_stime, 1),
           "peak_rss_mb": round(vmhwm_mb(), 1), "threads": transcribe.WHISPER_THREADS or os.cpu_count(),
           "device": transcribe._model_device,
           "wer": round(acc["word_errors"] / max(1, acc["ref_words"]), 4),
           "cer": round(acc["char_errors"] / max(1, acc["ref_chars"]), 4), **acc,
           "hyp_sha256": hashlib.sha256("\n".join(hyp_all).encode()).hexdigest()[:16],
           "hyp_sample": hyp_all[0][:300], "ref_sample": ref_all[0][:300]}
    if data["kind"] == "timeline":
        res["timestamps"] = boundary_errors(data["bounds"], words_all)
    out.write_text(json.dumps(res, ensure_ascii=False))


def model_revision(model: str):
    try:
        from huggingface_hub import HfApi
        return HfApi().model_info(f"Systran/faster-whisper-{model}").sha
    except Exception as exc:  # dicatat, tidak menggagalkan benchmark
        return f"tidak diketahui ({type(exc).__name__})"


def summarize(rows: list[dict]) -> list[dict]:
    out = []
    for key in sorted({(r["model"], r["set"]) for r in rows if "error" not in r}):
        rs = [r for r in rows if (r.get("model"), r.get("set")) == key and "error" not in r]

        def stat(f):
            v = sorted(r[f] for r in rs)
            return {"median": statistics.median(v), "p95_nearest_rank": v[max(0, -(-95 * len(v) // 100) - 1)],
                    "n": len(v)}
        row = {"model": key[0], "set": key[1], "rtf": stat("rtf"), "load_s": stat("load_s"),
               "peak_rss_mb": stat("peak_rss_mb"), "wer": rs[0]["wer"], "cer": rs[0]["cer"],
               "deterministic": len({r["hyp_sha256"] for r in rs}) == 1, "lang_detected": rs[0]["lang_detected"],
               "speaker_source": rs[0].get("speaker_source")}
        if "timestamps" in rs[0]:
            ts = rs[0]["timestamps"]
            for side in ("start", "end"):
                errs = sorted(abs(e) for e in ts[f"{side}_err_ms"])
                row[f"{side}_abs_ms_median"] = statistics.median(errs) if errs else None
                row[f"{side}_within_300ms"] = f"{sum(e <= 300 for e in errs)}/{ts['bounds']}"
        out.append(row)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("corpus")
    ap.add_argument("out")
    ap.add_argument("--models", default="tiny,base,small")
    ap.add_argument("--sets", default="")
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--child", nargs=2, help=argparse.SUPPRESS)
    a = ap.parse_args()
    corpus, out = Path(a.corpus), Path(a.out)
    if a.child:
        child(a.child[0], a.child[1], corpus, out)
        return
    sets = a.sets.split(",") if a.sets else list(json.loads((corpus / "corpus.json").read_text())["sets"])
    rows = []
    for model in a.models.split(","):
        rev = model_revision(model)
        for s in sets:
            for run in range(1, a.runs + 1):
                tmp = out.with_suffix(f".{model}.{s}.json")
                p = subprocess.run([sys.executable, __file__, a.corpus, str(tmp), "--child", model, s],
                                   capture_output=True, text=True, env={**os.environ, "HF_HUB_OFFLINE": "0"})
                row = ({**json.loads(tmp.read_text()), "run": run, "model_revision": rev} if p.returncode == 0
                       else {"model": model, "set": s, "run": run, "error": p.stderr.strip()[-800:]})
                rows.append(row)
                with out.open("a") as f:
                    f.write(json.dumps(row, ensure_ascii=False) + "\n")
                print(json.dumps({k: row.get(k) for k in ("model", "set", "run", "rtf", "wer", "cer", "load_s",
                                                          "peak_rss_mb", "lang_detected", "error")},
                                 ensure_ascii=False), flush=True)
    print("RINGKASAN " + json.dumps(summarize(rows), ensure_ascii=False))


if __name__ == "__main__":
    main()
