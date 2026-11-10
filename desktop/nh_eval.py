"""Uji akurasi transkripsi dengan video NH ASLI, di komputer NH sendiri (offline, video tidak dikirim ke mana pun).

    NH-AI-Clip-Studio.exe --eval "D:\\Uji NH"  [--runs 2] [--qualities cepat,akurat,tinggi]

Folder berisi pasangan <nama>.<mp4|mov|mkv|m4a|mp3|wav> + <nama>.txt (transkrip rujukan yang diketik manual, apa
adanya, termasuk istilah Arab dengan ejaan pedoman NH). Untuk setiap mode x run, transkripsi dijalankan di proses
terpisah (puncak RAM terukur per konfigurasi). Keluaran di folder yang sama:
  laporan-uji.md / laporan-uji.json   ringkasan: WER, CER, RTF, puncak RAM, variasi antar-run, istilah kosakata
  hasil/<nama>.<mode>.run<k>.txt      transkrip mentah untuk diperiksa manusia
Kirimkan laporan-uji.md (tanpa video) untuk ditinjau. Angka hanya berlaku untuk video & komputer yang diuji.
"""
from __future__ import annotations

import json
import os
import platform
import re
import subprocess
import sys
import time
import unicodedata
from pathlib import Path

MEDIA_EXT = (".mp4", ".mov", ".mkv", ".m4a", ".mp3", ".wav", ".webm", ".aac")


def _norm(text: str) -> list[str]:
    t = unicodedata.normalize("NFKC", text).lower()
    return re.sub(r"[^\w\s]|_", " ", t).split()


def peak_ram_mb() -> float:
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        class PMC(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                        ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                        ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]
        pmc = PMC()
        pmc.cb = ctypes.sizeof(PMC)
        handle = ctypes.windll.kernel32.GetCurrentProcess()
        ctypes.windll.psapi.GetProcessMemoryInfo(handle, ctypes.byref(pmc), pmc.cb)
        return round(pmc.PeakWorkingSetSize / 2**20, 1)
    import resource
    return round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1)


def term_check(reference: str, hypothesis: str, terms: list[str]) -> dict:
    """Istilah kosakata yang muncul di rujukan: berapa yang tertulis persis sama (tanpa beda huruf besar/kecil) di
    hasil. Mengukur risiko istilah Arab/dakwah secara langsung, terpisah dari WER keseluruhan."""
    ref = " " + " ".join(_norm(reference)) + " "
    hyp = " " + " ".join(_norm(hypothesis)) + " "
    found, missed = [], []
    # Istilah terpanjang dulu, lalu kemunculannya "dikonsumsi": "Allah" di dalam "Insya Allah" tidak dihitung dua kali.
    for term in sorted(set(terms), key=lambda x: -len(_norm(x))):
        t = " " + " ".join(_norm(term)) + " "
        n = ref.count(t)
        if not n:
            continue
        got = min(n, hyp.count(t))
        found += [term] * got
        missed += [term] * (n - got)
        ref, hyp = ref.replace(t, " \x00 "), hyp.replace(t, " \x00 ", got)
    total = len(found) + len(missed)
    return {"terms_in_reference": total, "terms_correct": len(found),
            "term_accuracy": round(len(found) / total, 3) if total else None,
            "missed": sorted(set(missed))[:50]}


def child(media: Path, quality: str, out: Path) -> None:
    from app import media as app_media
    from app import transcribe, vocab
    from desktop import selftest
    dur = app_media.probe(media)["duration"]
    t0 = time.perf_counter()
    r = transcribe.transcribe(media, dur, "id", quality=quality)
    took = time.perf_counter() - t0
    words = [w for s in r["segments"] for w in s.get("words", [])]
    hyp = " ".join(w["word"] for w in words)
    ref = media.with_suffix(".txt").read_text(encoding="utf-8")
    terms = [e["term"] for e in vocab.effective(None)]
    out.write_text(json.dumps({
        "file": media.name, "quality": quality, "model": r.get("model"), "duration": round(dur, 1),
        "seconds": round(took, 1), "rtf": round(took / dur, 3) if dur else None, "peak_ram_mb": peak_ram_mb(),
        **selftest.wer_cer(ref, hyp), "words": len(words),
        "unsure_share": round(sum(float(w.get("p", 1)) < 0.5 for w in words) / len(words), 3) if words else None,
        "terms": term_check(ref, hyp, terms), "hypothesis": hyp}, ensure_ascii=False), encoding="utf-8")


def _system() -> dict:
    from app import resources
    mem = resources.memory() or {}
    return {"os": platform.platform(), "cpu": platform.processor() or platform.machine(),
            "cpu_threads": os.cpu_count(), "ram_total_gb": round(mem.get("total", 0) / 2**30, 1),
            "ram_available_gb": round(mem.get("available", 0) / 2**30, 1)}


def run(folder: Path, runs: int, qualities: list[str]) -> dict:
    from app import transcribe
    folder = folder.resolve()
    pairs = [p for p in sorted(folder.iterdir()) if p.suffix.lower() in MEDIA_EXT and p.with_suffix(".txt").exists()]
    if not pairs:
        raise SystemExit(f"Tidak ada pasangan video + .txt di {folder}")
    available = transcribe.available_qualities() or ["akurat"]
    qualities = [q for q in qualities if q in available] or available
    outdir = folder / "hasil"
    outdir.mkdir(exist_ok=True)
    rows = []
    for media in pairs:
        for q in qualities:
            for k in range(1, runs + 1):
                res_file = outdir / f"{media.stem}.{q}.run{k}.json"
                cmd = [sys.executable] if getattr(sys, "frozen", False) else [sys.executable, "-m", "desktop.launcher"]
                cmd += ["--eval-child", str(media), q, str(res_file)]
                print(f"[{media.name}] mode {q}, run {k}/{runs} ...", flush=True)
                p = subprocess.run(cmd, capture_output=True, text=True,
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                if p.returncode != 0 or not res_file.exists():
                    rows.append({"file": media.name, "quality": q, "run": k, "error": (p.stderr or "")[-1500:]})
                    continue
                row = json.loads(res_file.read_text(encoding="utf-8"))
                (outdir / f"{media.stem}.{q}.run{k}.txt").write_text(row["hypothesis"], encoding="utf-8")
                row["run"] = k
                rows.append(row)
    summary = []
    for q in qualities:
        ok = [r for r in rows if r.get("quality") == q and "error" not in r]
        if not ok:
            continue
        by_file: dict[str, list] = {}
        for r in ok:
            by_file.setdefault(r["file"], []).append(r)
        identical = all(len({r["hypothesis"] for r in rs}) == 1 for rs in by_file.values())
        wers = [r["wer"] for r in ok]
        terms_total = sum(r["terms"]["terms_in_reference"] for r in ok)
        terms_ok = sum(r["terms"]["terms_correct"] for r in ok)
        summary.append({
            "quality": q, "model": ok[0]["model"], "runs": len(ok),
            "wer_mean": round(sum(wers) / len(wers), 4), "wer_min": min(wers), "wer_max": max(wers),
            "cer_mean": round(sum(r["cer"] for r in ok) / len(ok), 4),
            "rtf_mean": round(sum(r["rtf"] for r in ok) / len(ok), 3),
            "peak_ram_mb_max": max(r["peak_ram_mb"] for r in ok),
            "runs_identical": identical if runs > 1 else None,
            "term_accuracy": round(terms_ok / terms_total, 3) if terms_total else None,
            "terms_in_reference": terms_total})
    report = {"system": _system(), "folder": str(folder), "files": [p.name for p in pairs], "runs": runs,
              "summary": summary, "rows": [{k: v for k, v in r.items() if k != "hypothesis"} for r in rows]}
    (folder / "laporan-uji.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    (folder / "laporan-uji.md").write_text(markdown(report), encoding="utf-8")
    return report


def markdown(rep: dict) -> str:
    s = rep["system"]
    lines = ["# Laporan uji transkripsi NH AI Clip Studio", "",
             f"Komputer: {s['os']}, {s['cpu']}, {s['cpu_threads']} thread, RAM {s['ram_total_gb']} GB "
             f"(tersedia saat mulai {s['ram_available_gb']} GB). Berkas: {len(rep['files'])}; run per mode: {rep['runs']}.",
             "", "| Mode | Model | WER rata-rata (min–maks) | CER | RTF | Puncak RAM | Run identik | Istilah benar |",
             "|---|---|---|---|---|---|---|---|"]
    for r in rep["summary"]:
        ident = {True: "ya", False: "**tidak**", None: "–"}[r["runs_identical"]]
        terms = f"{r['term_accuracy']:.0%} dari {r['terms_in_reference']}" if r["term_accuracy"] is not None else "–"
        lines.append(f"| {r['quality']} | {r['model']} | {r['wer_mean']:.1%} ({r['wer_min']:.1%}–{r['wer_max']:.1%}) | "
                     f"{r['cer_mean']:.1%} | {r['rtf_mean']} | {r['peak_ram_mb_max']:.0f} MB | {ident} | {terms} |")
    lines += ["", "## Per berkas", "", "| Berkas | Mode | Run | WER | CER | RTF | RAM | Istilah terlewat |",
              "|---|---|---|---|---|---|---|---|"]
    for r in rep["rows"]:
        if "error" in r:
            lines.append(f"| {r['file']} | {r['quality']} | {r['run']} | GAGAL | | | | |")
            continue
        missed = ", ".join(r["terms"]["missed"][:8])
        lines.append(f"| {r['file']} | {r['quality']} | {r['run']} | {r['wer']:.1%} | {r['cer']:.1%} | {r['rtf']} | "
                     f"{r['peak_ram_mb']:.0f} MB | {missed} |")
    lines += ["", "WER/CER: huruf kecil, tanpa tanda baca. Istilah = kosakata aplikasi yang muncul di rujukan; "
              "\"benar\" = tertulis sama persis di hasil. Penanda \"kurang yakin\" bukan jaminan kata lain benar.", ""]
    return "\n".join(lines)
