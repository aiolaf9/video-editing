"""Benchmark antrean & resource yang dapat diulang (P3).

Menjalankan worker sungguhan (subprocess) terhadap PostgreSQL dengan beban tetap, lalu mengukur
waktu tunggu, latensi, throughput, pemakaian CPU/RAM, dan kegagalan. Kode yang diuji dipilih lewat
--code sehingga versi sebelum & sesudah dapat dibandingkan di mesin yang sama.

Skenario
  hol      : unit A memulai 2 analisis video panjang (120 dtk), 3 dtk kemudian unit B & C masing-masing
             mengirim 3 render pendek (8 dtk). Mengukur apakah render pendek tertahan analisis panjang.
  fair     : unit A mengirim 12 render sekaligus, 2 dtk kemudian unit B mengirim 2 render.
             Mengukur apakah unit kecil harus menunggu seluruh antrean unit besar.
  cancel   : beban penuh (hol) lalu semua job unit A dibatalkan; mengukur waktu sampai berhenti.

Contoh
  python bench/queue_bench.py --code . --db postgresql+psycopg://... --scenario hol \
      --workers "analisis:1,render:1,io:1" --out hasil.json
"""
import argparse
import json
import os
import shutil
import signal
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
CACHE = Path(os.environ.get("BENCH_CACHE", tempfile.gettempdir())) / "ve-bench-media"


def ffmpeg(*args):
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *args], check=True)


def make_media() -> dict:
    CACHE.mkdir(parents=True, exist_ok=True)
    out = {}
    for name, secs in (("long", 120), ("short", 60)):
        path = CACHE / f"{name}.mp4"
        if not path.exists():
            ffmpeg("-f", "lavfi", "-i", f"testsrc2=size=1280x720:rate=25:duration={secs}",
                   "-f", "lavfi", "-i", f"sine=frequency=330:duration={secs}",
                   "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path))
        out[name] = path
    return out


class Sampler(threading.Thread):
    """CPU (seluruh mesin, /proc/stat) & RAM terpakai (/proc/meminfo) tiap 0,5 dtk."""

    def __init__(self):
        super().__init__(daemon=True)
        self.cpu, self.mem, self.stop = [], [], threading.Event()

    @staticmethod
    def _cpu_times():
        vals = list(map(int, open("/proc/stat").readline().split()[1:]))
        idle = vals[3] + vals[4]
        return sum(vals), idle

    @staticmethod
    def _mem_used_mb():
        info = {l.split(":")[0]: int(l.split()[1]) for l in open("/proc/meminfo")}
        return (info["MemTotal"] - info["MemAvailable"]) / 1024

    def run(self):
        last = self._cpu_times()
        while not self.stop.wait(0.5):
            cur = self._cpu_times()
            total, idle = cur[0] - last[0], cur[1] - last[1]
            last = cur
            if total:
                self.cpu.append(100.0 * (1 - idle / total))
            self.mem.append(self._mem_used_mb())


def pct(values, p):
    if not values:
        return None
    s = sorted(values)
    k = max(0, min(len(s) - 1, round(p / 100 * (len(s) - 1))))
    return round(s[k], 2)


def summarize(rows, label):
    waits = [r["started"] - r["created"] for r in rows if r["started"]]
    lat = [r["finished"] - r["created"] for r in rows if r["finished"]]
    run = [r["finished"] - r["started"] for r in rows if r["finished"] and r["started"]]
    return {"group": label, "jobs": len(rows), "ok": sum(r["status"] == "selesai" for r in rows),
            "failed": sum(r["status"] == "gagal" for r in rows), "cancelled": sum(r["status"] == "batal" for r in rows),
            "wait_p50": pct(waits, 50), "wait_p95": pct(waits, 95), "wait_max": pct(waits, 100),
            "latency_p50": pct(lat, 50), "latency_p95": pct(lat, 95), "latency_max": pct(lat, 100),
            "run_mean": round(statistics.mean(run), 2) if run else None}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--code", required=True)
    ap.add_argument("--db", required=True)
    ap.add_argument("--scenario", choices=("hol", "fair", "cancel"), required=True)
    ap.add_argument("--workers", required=True, help='mis. "berat:2,unduh:1" atau "analisis:1,render:1,io:1"')
    ap.add_argument("--env", action="append", default=[], help="KEY=VALUE tambahan untuk worker")
    ap.add_argument("--timeout", type=float, default=1200)
    ap.add_argument("--out")
    args = ap.parse_args()

    code = str(Path(args.code).resolve())
    data_dir = Path(tempfile.mkdtemp(prefix="ve-bench-"))
    env = dict(os.environ, VIDEO_APP_DATA=str(data_dir), DATABASE_URL=args.db, EMBEDDED_WORKERS="0",
               CLEANUP_INTERVAL="1000000", PYTHONUNBUFFERED="1")
    for kv in args.env:
        k, v = kv.split("=", 1)
        env[k] = v
    os.environ.update(env)
    sys.path.insert(0, code)
    from sqlalchemy import select, update  # noqa: E402

    from app import db, jobs, media, storage  # noqa: E402

    db.metadata.drop_all(db.engine())
    db._engine = None
    db.engine()
    files = make_media()
    units = {}
    with db.engine().begin() as c:
        for name in ("A", "B", "C"):
            uid = storage.new_id()
            c.execute(db.units.insert().values(id=uid, name=f"Unit {name}", created=time.time()))
            units[name] = uid

    def project(unit, kind):
        pid = storage.create_project(f"bench-{kind}", units[unit])["id"]
        shutil.copy(files[kind], storage.project_dir(pid) / "source.mp4")
        info = media.probe(storage.project_dir(pid) / "source.mp4")
        cols = {c.name for c in db.projects.columns}
        storage.update_project(pid, source="source.mp4", status="siap" if kind == "short" else "antre",
                               **{k: v for k, v in info.items() if k in cols})
        return pid

    def queue_for(task):
        if hasattr(jobs, "queue_for"):
            return jobs.queue_for(task)
        return "unduh" if task == "download" else "berat"

    submitted = []

    def submit(unit, task, pid, payload, label):
        job = jobs.submit(task, "render" if task == "render" else "analisis", pid, payload, label,
                          queue=queue_for(task), unit_id=units[unit], user_id=None)
        submitted.append((label, job["id"]))
        return job["id"]

    clip = {"start": 5, "end": 13, "aspect": "9:16", "reframe": "manual", "animations": False,
            "subtitles": {"enabled": False}}

    # Proyek disiapkan sebelum worker berjalan (tidak dihitung dalam pengukuran).
    long_projects = [project("A", "long") for _ in range(2)]
    short = {u: project(u, "short") for u in ("A", "B", "C")}

    procs = []
    for spec in args.workers.split(","):
        queue, n = spec.split(":")
        for i in range(int(n)):
            procs.append(subprocess.Popen([sys.executable, str(HERE / "worker_shim.py"), code, "--queues", queue],
                                          env=env, stdout=subprocess.DEVNULL, stderr=open(data_dir / f"w-{queue}-{i}.log", "w")))
    time.sleep(3)  # worker siap (import, koneksi DB)
    sampler = Sampler()
    sampler.start()
    t0 = time.time()

    if args.scenario in ("hol", "cancel"):
        for pid in long_projects:
            submit("A", "analyze", pid, {"language": "id"}, "A-analisis")
        time.sleep(3)
        for u in ("B", "C"):
            for _ in range(3):
                submit(u, "render", short[u], {"request": clip}, f"{u}-render")
    else:
        for _ in range(12):
            submit("A", "render", short["A"], {"request": clip}, "A-render")
        time.sleep(2)
        for _ in range(2):
            submit("B", "render", short["B"], {"request": clip}, "B-render")

    cancel_info = None
    if args.scenario == "cancel":
        time.sleep(12)
        t_cancel = time.time()
        for label, jid in submitted:
            if label.startswith("A-"):
                jobs.request_cancel(jid)
        while time.time() - t_cancel < 120:
            st = [jobs.get(jid)["status"] for label, jid in submitted if label.startswith("A-")]
            if all(s in jobs.DONE for s in st):
                break
            time.sleep(0.2)
        cancel_info = {"seconds_until_all_stopped": round(time.time() - t_cancel, 2)}

    while time.time() - t0 < args.timeout:
        if all(jobs.get(jid)["status"] in jobs.DONE for _, jid in submitted):
            break
        time.sleep(0.5)
    makespan = time.time() - t0
    sampler.stop.set()
    for p in procs:
        p.send_signal(signal.SIGTERM)
    for p in procs:
        try:
            p.wait(60)
        except subprocess.TimeoutExpired:
            p.kill()

    rows = []
    for label, jid in submitted:
        j = jobs.get(jid)
        rows.append({"label": label, "id": jid, "status": j["status"], "created": j["created"],
                     "started": j["started"], "finished": j["finished"], "error": (j.get("error") or "")[:200]})
    groups = sorted({r["label"] for r in rows})
    result = {
        "scenario": args.scenario, "workers": args.workers, "env": args.env, "code": code,
        "cpu_count": os.cpu_count(), "makespan_s": round(makespan, 2),
        "cpu_util_mean": round(statistics.mean(sampler.cpu), 1) if sampler.cpu else None,
        "cpu_util_p95": pct(sampler.cpu, 95), "mem_used_peak_mb": round(max(sampler.mem), 0) if sampler.mem else None,
        "groups": [summarize([r for r in rows if r["label"] == g], g) for g in groups],
        "cancel": cancel_info, "jobs": rows,
    }
    text = json.dumps(result, indent=2)
    if args.out:
        Path(args.out).write_text(text)
    print(json.dumps({k: v for k, v in result.items() if k != "jobs"}, indent=2))
    shutil.rmtree(data_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
