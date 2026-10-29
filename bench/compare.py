"""Ringkas hasil queue_bench.py (rata-rata beberapa run) menjadi tabel markdown.

    python bench/compare.py "Label=pola-file-*.json" "Label2=pola2-*.json" ...
"""
import glob
import json
import statistics
import sys


def pct(values, p):
    s = sorted(values)
    return s[max(0, min(len(s) - 1, round(p / 100 * (len(s) - 1))))] if s else None


def stats(path):
    r = json.load(open(path))
    rows = r["jobs"]
    def sel(prefix):
        return [j for j in rows if any(j["label"].startswith(p) for p in prefix)]
    if r["scenario"] in ("hol", "cancel"):
        small, big = sel(("B-", "C-")), sel(("A-",))
    else:
        small, big = sel(("B-",)), sel(("A-",))
    wait = [j["started"] - j["created"] for j in small if j["started"]]
    lat = [j["finished"] - j["created"] for j in small if j["finished"]]
    big_lat = [j["finished"] - j["created"] for j in big if j["finished"]]
    return {"small_wait_p50": pct(wait, 50), "small_wait_max": max(wait), "small_lat_max": max(lat),
            "big_lat_max": max(big_lat) if big_lat else None, "makespan": r["makespan_s"],
            "cpu": r["cpu_util_mean"], "mem": r["mem_used_peak_mb"],
            "failed": sum(j["status"] == "gagal" for j in rows)}


def main():
    keys = ["small_wait_p50", "small_wait_max", "small_lat_max", "big_lat_max", "makespan", "cpu", "mem", "failed"]
    print("| Konfigurasi | n | " + " | ".join(keys) + " |")
    print("|---" * (len(keys) + 2) + "|")
    for arg in sys.argv[1:]:
        label, pattern = arg.split("=", 1)
        runs = [stats(p) for p in sorted(glob.glob(pattern))]
        if not runs:
            continue
        avg = {k: round(statistics.mean(r[k] for r in runs), 1) if runs[0][k] is not None else None for k in keys}
        print(f"| {label} | {len(runs)} | " + " | ".join(str(avg[k]) for k in keys) + " |")


if __name__ == "__main__":
    main()
