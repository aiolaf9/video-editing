"""Ringkas hasil queue_bench.py menjadi tabel markdown (rata-rata semua run yang cocok dengan pola).

    python bench/compare.py DIR "Label=PREFIX" "Label2=PREFIX2" ...
Membaca DIR/PREFIX-<skenario>-<run>.json untuk skenario hol, fair, mixed, cancel.
"""
import glob
import json
import os
import statistics
import sys


def pct(values, p):
    s = sorted(v for v in values if v is not None)
    return s[max(0, min(len(s) - 1, round(p / 100 * (len(s) - 1))))] if s else None


def mean(xs):
    xs = [x for x in xs if x is not None]
    return round(statistics.mean(xs), 1) if xs else None


def waits(rows, prefixes, kind="render"):
    return [r["started"] - r["created"] for r in rows
            if r["label"].endswith(kind) and r["label"][0] in prefixes and r["started"]]


def latest(rows, prefixes, kind):
    lat = [r["finished"] - r["created"] for r in rows if r["label"].endswith(kind) and r["label"][0] in prefixes
           and r["finished"]]
    return max(lat) if lat else None


def common(r):
    wp = [w["mem_peak_mb"] for w in r["workers_after"] if w.get("mem_peak_mb")]
    return {"makespan": r["makespan_s"], "thr": r["throughput_jobs_per_min"], "cpu": r["cpu_util_mean"],
            "ram": r["mem_used_peak_mb"], "wram": max(wp) if wp else None,
            "runq": r.get("runnable_per_core_p95"), "oom": r["leaks"]["oom_killed"],
            "leak": r["leaks"]["ffmpeg_processes_after_stop"] + r["leaks"]["jobs_not_finished"]
            + r["leaks"]["jobs_run_more_than_once"]}


def metrics(r):
    rows, sc = r["jobs"], r["scenario"]
    m = common(r)
    if sc in ("hol", "cancel"):
        w = waits(rows, "BC")
        m.update(w50=pct(w, 50), w95=pct(w, 95), ana=latest(rows, "A", "analisis"))
    if sc == "fair":
        w = waits(rows, "B")
        m.update(w50=pct(w, 50), w95=pct(w, 95), big=latest(rows, "A", "render"))
    if sc == "mixed":
        w = waits(rows, "ABC")
        m.update(w50=pct(w, 50), w95=pct(w, 95), ana=latest(rows, "AB", "analisis"), jain=r["fairness_jain"])
    if sc == "cancel":
        m.update(stop=r["cancel"]["seconds_until_all_stopped"], stop_run=r["cancel"]["running_stop_max_s"])
    return m


COLS = {
    "hol": [("w50", "render tunggu p50"), ("w95", "p95"), ("ana", "analisis selesai"), ("makespan", "makespan"),
            ("thr", "job/mnt"), ("cpu", "CPU %"), ("runq", "siap-jalan/core p95"), ("wram", "RAM worker maks MB"),
            ("oom", "OOM"), ("leak", "bocor")],
    "fair": [("w50", "unit kecil tunggu p50"), ("w95", "p95"), ("big", "unit besar selesai"), ("makespan", "makespan"),
             ("thr", "job/mnt"), ("cpu", "CPU %"), ("runq", "siap-jalan/core p95"), ("oom", "OOM"), ("leak", "bocor")],
    "mixed": [("w50", "render tunggu p50"), ("w95", "p95"), ("ana", "analisis selesai"), ("jain", "fairness (Jain)"),
              ("makespan", "makespan"), ("thr", "job/mnt"), ("cpu", "CPU %"), ("ram", "RAM mesin MB"),
              ("runq", "siap-jalan/core p95"), ("oom", "OOM"), ("leak", "bocor")],
    "cancel": [("stop", "semua job A berhenti (dtk)"), ("stop_run", "job berjalan berhenti (dtk)"),
               ("w50", "render B/C tunggu p50"), ("makespan", "makespan"), ("leak", "bocor")],
}


def main():
    folder, specs = sys.argv[1], sys.argv[2:]
    for sc, cols in COLS.items():
        print(f"\n### {sc}\n")
        print("| Konfigurasi | n | " + " | ".join(c[1] for c in cols) + " |")
        print("|---" * (len(cols) + 2) + "|")
        for spec in specs:
            label, prefix = spec.split("=", 1)
            files = sorted(glob.glob(os.path.join(folder, f"{prefix}-{sc}-*.json")))
            if not files:
                continue
            runs = [metrics(json.load(open(f))) for f in files]
            vals = [mean(r.get(k) for r in runs) for k, _ in cols]
            print(f"| {label} | {len(runs)} | " + " | ".join("–" if v is None else str(v) for v in vals) + " |")


if __name__ == "__main__":
    main()
