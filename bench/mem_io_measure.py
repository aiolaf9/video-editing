"""P5.5.1 E: puncak memori worker io SUNGGUHAN (python -m app.worker --queues io) di cgroup memory v1 berbatas 512 MB.

Untuk tiap workload: reset memory.max_usage_in_bytes, kirim job unduhan lewat jobs.submit (seperti API), lalu sampel
setiap 50 ms: memory.stat total_rss (anonim), total_cache, pemakaian menurut resources._cgroup_memory (dipakai
admission: usage - inactive_file), dan RSS pohon proses worker (Python + FFmpeg). Bukti tidak OOM: failcnt &
oom_kill dari memory.oom_control, status job, sha256 berkas = sumber.
Pemakaian: memmeasure.py <cgroup-dir> <url-dasar> <kasus...>; env VIDEO_APP_DATA sama dengan worker.
"""
import hashlib
import json
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, os.getcwd())
from app import db, jobs, resources, storage  # noqa: E402

CG = Path(sys.argv[1])
BASE = sys.argv[2]
SRC = Path(sys.argv[3])
CASES = {
    "mp4": ("av.mp4", "MP4 biasa 1080p 120 dtk (117 MB, video+audio satu berkas)"),
    "hls": ("hls/index.m3u8", "HLS 1080p 120 dtk, 30 segmen .ts -> remux mp4 (FFmpegFixupM3u8)"),
    "dash": ("dash/index.mpd", "DASH 1080p 120 dtk, video & audio terpisah -> gabung FFmpeg (FFmpegMerger)"),
    "big": ("big.mp4", "MP4 besar 1080p 10 mnt (584 MB)"),
}
MB = 2**20

# resources._cgroup_memory membaca <root>/memory/memory.*; arahkan ke cgroup uji lewat symlink (jalur v1 asli).
root = Path(tempfile.mkdtemp())
(root / "memory").symlink_to(CG)


def stat():
    return {k: int(v) for k, v in (l.split() for l in (CG / "memory.stat").read_text().splitlines())}


def oom_kills():
    for line in (CG / "memory.oom_control").read_text().splitlines():
        if line.startswith("oom_kill "):
            return int(line.split()[1])
    return None


def tree_rss():
    pids = [int(p) for p in (CG / "cgroup.procs").read_text().split()]
    total = 0
    for p in pids:
        try:
            total += int(Path(f"/proc/{p}/status").read_text().split("VmRSS:")[1].split()[0]) * 1024
        except (OSError, IndexError):
            pass
    return total, len(pids)


def sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def run(keys):
    ids = []
    for key in keys:
        rel, _ = CASES[key]
        pid = storage.create_project(f"ukur-{key}")["id"]
        ids.append((key, pid, jobs.submit("download", "unduh", pid, {"url": f"{BASE}/{rel}", "trusted": True},
                                          "Unduh", queue="io")["id"]))
    peak = {"rss": 0, "cache": 0, "admission_used": 0, "tree": 0, "procs": 0}
    t0 = time.time()
    while True:
        s = stat()
        peak["rss"] = max(peak["rss"], s["total_rss"])
        peak["cache"] = max(peak["cache"], s["total_cache"])
        cg = resources._cgroup_memory(root)
        peak["admission_used"] = max(peak["admission_used"], cg[1] if cg else 0)
        t, n = tree_rss()
        peak["tree"], peak["procs"] = max(peak["tree"], t), max(peak["procs"], n)
        states = [jobs.get(j)["status"] for _, _, j in ids]
        if all(x in ("selesai", "gagal", "batal") for x in states) or time.time() - t0 > 900:
            break
        time.sleep(0.05)
    out = []
    for key, pid, jid in ids:
        job = jobs.get(jid)
        proj = storage.get_project(pid) if hasattr(storage, "get_project") else storage.load_project(pid)
        src = proj.get("source")
        ok_sha = None
        if job["status"] == "selesai" and src and key in ("mp4", "big"):
            ok_sha = sha(storage.project_dir(pid) / src) == sha(SRC / CASES[key][0])
        out.append({"kasus": key, "job": job["status"], "error": job.get("error"), "berkas": src,
                    "sha256_sama_sumber": ok_sha})
    return out, peak, time.time() - t0


def main():
    db.engine()
    limit = int((CG / "memory.limit_in_bytes").read_text())
    print(f"cgroup v1 {CG.name}: limit {limit / MB:.0f} MB; deteksi aplikasi: {resources._cgroup_memory(root)}")
    for group in sys.argv[4:]:
        keys = group.split("+")
        (CG / "memory.max_usage_in_bytes").write_text("0")
        fail0, oom0 = int((CG / "memory.failcnt").read_text()), oom_kills()
        results, peak, secs = run(keys)
        print(json.dumps({
            "workload": " + ".join(CASES[k][1] for k in keys), "detik": round(secs, 1), "hasil": results,
            "puncak_cgroup_max_usage_MB(termasuk page cache)": round(int((CG / "memory.max_usage_in_bytes")
                                                                          .read_text()) / MB, 1),
            "puncak_total_rss_anon_MB": round(peak["rss"] / MB, 1),
            "puncak_page_cache_MB": round(peak["cache"] / MB, 1),
            "puncak_terpakai_menurut_admission_MB": round(peak["admission_used"] / MB, 1),
            "puncak_RSS_pohon_proses_MB": round(peak["tree"] / MB, 1), "proses_maks": peak["procs"],
            "failcnt_baru": int((CG / "memory.failcnt").read_text()) - fail0,
            "oom_kill_baru": None if oom0 is None else oom_kills() - oom0,
        }, ensure_ascii=False, indent=1))


main()
