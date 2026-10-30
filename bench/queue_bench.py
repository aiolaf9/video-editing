"""Benchmark antrean & resource yang dapat diulang (P3).

Menjalankan worker sungguhan terhadap PostgreSQL dengan beban tetap, lalu mengukur waktu tunggu,
latensi, throughput, keadilan antar-unit, pemakaian CPU/RAM, oversubscription FFmpeg, OOM, dan
kebocoran (proses FFmpeg tertinggal, job ganda, job menggantung). Kode yang diuji dipilih lewat --code
sehingga versi sebelum & sesudah dibandingkan di mesin yang sama.

Skenario (semua deterministik; jumlah & jadwal job identik untuk setiap konfigurasi)
  hol    : unit A 2 analisis video 120 dtk; 3 dtk kemudian unit B & C masing-masing 3 render 8 dtk.
  fair   : unit A 12 render sekaligus; 2 dtk kemudian unit B 2 render.
  mixed  : 3 unit, 2 analisis + 14 render datang bertahap selama 15 dtk.
  cancel : beban hol; 12 dtk kemudian semua job unit A dibatalkan (latensi pembatalan).

Worker (--workers), dipisah koma: ANTREAN[+ANTREAN]:JUMLAH[@CPUS/MEM/SHARES]
  "berat:2,unduh:1"                                   (proses biasa)
  "analisis+render:2@3/3g/256,render:1@3/1500m/1024,io:1@0.5/512m/512" --docker
  ANTREAN+ANTREAN = satu worker melayani beberapa antrean (urutan = prioritas).
  --docker menjalankan tiap worker sebagai container dengan --cpus/--memory/--cpu-shares sungguhan
  (image kosong + sistem file host di-mount read-only; lihat DOCKER_MOUNTS).

Whisper: diganti beban CPU tetap (lihat worker_shim.py) kecuali --real-whisper.
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
DOCKER_IMAGE = os.environ.get("BENCH_DOCKER_IMAGE", "ve-hostroot:empty")
DOCKER_MOUNTS = ["/usr", "/lib", "/lib64", "/bin", "/etc", "/sbin"]


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


def ffmpeg_threads() -> int:
    """Jumlah thread seluruh proses FFmpeg di mesin saat ini (untuk mengukur oversubscription)."""
    total = 0
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            if (entry / "comm").read_text().strip() != "ffmpeg":
                continue
            for line in (entry / "status").read_text().splitlines():
                if line.startswith("Threads:"):
                    total += int(line.split()[1])
        except OSError:
            continue
    return total


class Sampler(threading.Thread):
    """Tiap 0,5 dtk: CPU mesin, thread siap-jalan (procs_running = ukuran oversubscription), RAM, load,
    dan jumlah thread FFmpeg (termasuk thread internal yang sebagian besar tidur)."""

    def __init__(self):
        super().__init__(daemon=True)
        self.cpu, self.mem, self.load, self.ff, self.run_q, self.stop = [], [], [], [], [], threading.Event()

    @staticmethod
    def _cpu_times():
        lines = open("/proc/stat").read().splitlines()
        vals = list(map(int, lines[0].split()[1:]))
        running = next((int(l.split()[1]) for l in lines if l.startswith("procs_running")), 0)
        return sum(vals), vals[3] + vals[4], running

    @staticmethod
    def _mem_used_mb():
        info = {l.split(":")[0]: int(l.split()[1]) for l in open("/proc/meminfo")}
        return (info["MemTotal"] - info["MemAvailable"]) / 1024

    def run(self):
        last = self._cpu_times()
        while not self.stop.wait(0.5):
            cur = self._cpu_times()
            total, idle = cur[0] - last[0], cur[1] - last[1]
            self.run_q.append(max(0, cur[2] - 1))  # tanpa sampler sendiri
            last = cur
            if total:
                self.cpu.append(100.0 * (1 - idle / total))
            self.mem.append(self._mem_used_mb())
            self.load.append(os.getloadavg()[0])
            self.ff.append(ffmpeg_threads())


def pct(values, p):
    if not values:
        return None
    s = sorted(values)
    k = max(0, min(len(s) - 1, round(p / 100 * (len(s) - 1))))
    return round(s[k], 2)


def jain(values):
    values = [v for v in values if v is not None]
    if not values or not any(values):
        return 1.0
    return round(sum(values) ** 2 / (len(values) * sum(v * v for v in values)), 3)


def summarize(rows, label):
    waits = [r["started"] - r["created"] for r in rows if r["started"]]
    lat = [r["finished"] - r["created"] for r in rows if r["finished"]]
    run = [r["finished"] - r["started"] for r in rows if r["finished"] and r["started"]]
    return {"group": label, "jobs": len(rows), "ok": sum(r["status"] == "selesai" for r in rows),
            "failed": sum(r["status"] == "gagal" for r in rows), "cancelled": sum(r["status"] == "batal" for r in rows),
            "wait_p50": pct(waits, 50), "wait_p95": pct(waits, 95), "wait_max": pct(waits, 100),
            "latency_p50": pct(lat, 50), "latency_p95": pct(lat, 95), "latency_max": pct(lat, 100),
            "run_mean": round(statistics.mean(run), 2) if run else None}


def parse_workers(spec: str):
    out = []
    for part in spec.split(","):
        part = part.strip()
        limits = None
        if "@" in part:
            part, lim = part.split("@", 1)
            cpus, mem, shares = lim.split("/")
            limits = {"cpus": cpus, "mem": mem, "shares": shares}
        queues, n = part.rsplit(":", 1)
        out.append((queues.replace("+", ","), int(n), limits))
    return out


def start_worker(idx, queues, limits, code, env, data_dir, docker):
    shim = str(HERE / "worker_shim.py")
    if not docker:
        return {"proc": subprocess.Popen([sys.executable, shim, code, "--queues", queues], env=env,
                                         stdout=subprocess.DEVNULL,
                                         stderr=open(data_dir / f"w{idx}-{queues}.log", "w"))}
    name = f"ve-bench-w{idx}-{os.getpid()}"
    # Hostname sendiri per container seperti compose (penting untuk anggaran thread per host).
    cmd = ["docker", "run", "-d", "--name", name, "--hostname", name]
    if limits:
        cmd += ["--cpus", limits["cpus"], "--memory", limits["mem"], "--memory-swap", limits["mem"],
                "--cpu-shares", limits["shares"]]
    for m in DOCKER_MOUNTS:
        if Path(m).exists():
            cmd += ["-v", f"{m}:{m}:ro"]
    # kode, venv, data uji, socket PostgreSQL
    for m in {code, str(HERE.parent), str(Path(sys.executable).resolve().parent.parent), str(Path(sys.prefix)),
              str(data_dir), str(CACHE), env.get("BENCH_PG_SOCKET_DIR", "/var/lib/postgresql/vetest")}:
        if m and Path(m).exists() and not any(m == d or m.startswith(d + "/") for d in DOCKER_MOUNTS):
            cmd += ["-v", f"{m}:{m}"]
    for k in ("VIDEO_APP_DATA", "DATABASE_URL", "EMBEDDED_WORKERS", "CLEANUP_INTERVAL", "PYTHONUNBUFFERED",
              "BENCH_BURN_FRAMES_PER_SEC", "BENCH_REAL_WHISPER", "HOST_HEAVY_WORKERS", "WHISPER_MODEL",
              "WORKER_REPORT_INTERVAL", "HF_HOME"):
        if k in env:
            cmd += ["-e", f"{k}={env[k]}"]
    for kv in env.get("BENCH_EXTRA_ENV", "").split("\n"):
        if kv:
            cmd += ["-e", kv]
    cmd += [DOCKER_IMAGE, sys.executable, shim, code, "--queues", queues]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL)
    return {"container": name}


def _container_mem_peak(name):
    cid = subprocess.run(["docker", "inspect", "-f", "{{.Id}}", name], capture_output=True, text=True).stdout.strip()
    for path in (f"/sys/fs/cgroup/memory/docker/{cid}/memory.max_usage_in_bytes",
                 f"/sys/fs/cgroup/system.slice/docker-{cid}.scope/memory.peak"):
        try:
            return round(int(Path(path).read_text()) / 2**20)
        except (OSError, ValueError):
            continue
    return None


def stop_worker(w):
    if "proc" in w:
        w["proc"].send_signal(signal.SIGTERM)
        return
    w["mem_peak_mb"] = _container_mem_peak(w["container"])  # cgroup hilang setelah container berhenti
    subprocess.run(["docker", "stop", "-t", "90", w["container"]], stdout=subprocess.DEVNULL)


def worker_report(w):
    if "proc" in w:
        try:
            w["proc"].wait(90)
        except subprocess.TimeoutExpired:
            w["proc"].kill()
        return {"exit": w["proc"].returncode}
    info = json.loads(subprocess.run(["docker", "inspect", w["container"]], capture_output=True, text=True).stdout)[0]
    out = {"container": w["container"], "exit": info["State"]["ExitCode"], "oom_killed": info["State"]["OOMKilled"],
           "mem_peak_mb": w.get("mem_peak_mb")}
    subprocess.run(["docker", "rm", "-f", w["container"]], stdout=subprocess.DEVNULL)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--code", required=True)
    ap.add_argument("--db", required=True)
    ap.add_argument("--scenario", choices=("hol", "fair", "mixed", "cancel"), required=True)
    ap.add_argument("--workers", required=True)
    ap.add_argument("--docker", action="store_true")
    ap.add_argument("--real-whisper", action="store_true", help="pakai Whisper asli (model harus sudah tersedia)")
    ap.add_argument("--env", action="append", default=[], help="KEY=VALUE tambahan untuk worker")
    ap.add_argument("--timeout", type=float, default=1200)
    ap.add_argument("--out")
    args = ap.parse_args()

    code = str(Path(args.code).resolve())
    data_dir = Path(tempfile.mkdtemp(prefix="ve-bench-", dir=os.environ.get("BENCH_CACHE")))
    env = dict(os.environ, VIDEO_APP_DATA=str(data_dir), DATABASE_URL=args.db, EMBEDDED_WORKERS="0", AUTO_MIGRATE="1",
               CLEANUP_INTERVAL="1000000", PYTHONUNBUFFERED="1", WORKER_REPORT_INTERVAL="5")
    if args.real_whisper:
        env["BENCH_REAL_WHISPER"] = "1"
    extra = []
    for kv in args.env:
        k, v = kv.split("=", 1)
        env[k] = v
        extra.append(kv)
    env["BENCH_EXTRA_ENV"] = "\n".join(extra)
    os.environ.update(env)
    sys.path.insert(0, code)
    from app import db, jobs, media, storage  # noqa: E402

    from sqlalchemy import text as _text

    eng = db.engine()  # database benchmark khusus: kosongkan lalu buat ulang lewat migrasi Alembic
    db.metadata.drop_all(eng)
    with eng.begin() as c:
        c.execute(_text("DROP TABLE IF EXISTS alembic_version"))
    eng.dispose()
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
    long_projects = {u: [project(u, "long") for _ in range(2)] for u in ("A", "B")}
    short = {u: project(u, "short") for u in ("A", "B", "C")}

    workers = []
    idx = 0
    for queues, n, limits in parse_workers(args.workers):
        for _ in range(n):
            workers.append(start_worker(idx, queues, limits, code, env, data_dir, args.docker))
            idx += 1
    time.sleep(6 if args.docker else 3)  # worker siap (import, koneksi DB)
    sampler = Sampler()
    sampler.start()
    t0 = time.time()

    def at(t):
        delay = t0 + t - time.time()
        if delay > 0:
            time.sleep(delay)

    def renders(unit, n):
        for _ in range(n):
            submit(unit, "render", short[unit], {"request": clip}, f"{unit}-render")

    if args.scenario in ("hol", "cancel"):
        for pid in long_projects["A"]:
            submit("A", "analyze", pid, {"language": "id"}, "A-analisis")
        at(3)
        renders("B", 3)
        renders("C", 3)
    elif args.scenario == "fair":
        renders("A", 12)
        at(2)
        renders("B", 2)
    else:  # mixed
        submit("A", "analyze", long_projects["A"][0], {"language": "id"}, "A-analisis")
        renders("A", 2)
        at(2)
        submit("B", "analyze", long_projects["B"][0], {"language": "id"}, "B-analisis")
        renders("C", 2)
        at(5)
        renders("B", 2)
        renders("C", 2)
        at(10)
        renders("A", 2)
        renders("C", 2)
        at(15)
        renders("B", 2)

    cancel_info = None
    if args.scenario == "cancel":
        at(12)
        t_cancel = time.time()
        targets = [jid for label, jid in submitted if label.startswith("A-")]
        running = [jid for jid in targets if jobs.get(jid)["status"] == "jalan"]
        for jid in targets:
            jobs.request_cancel(jid)
        stopped = {}
        while time.time() - t_cancel < 120 and len(stopped) < len(targets):
            for jid in targets:
                if jid not in stopped and jobs.get(jid)["status"] in jobs.DONE:
                    stopped[jid] = time.time() - t_cancel
            time.sleep(0.1)
        cancel_info = {"seconds_until_all_stopped": round(max(stopped.values(), default=0), 2),
                       "running_cancelled": len(running),
                       "running_stop_max_s": round(max((stopped.get(j, 999) for j in running), default=0), 2)}

    while time.time() - t0 < args.timeout:
        if all(jobs.get(jid)["status"] in jobs.DONE for _, jid in submitted):
            break
        time.sleep(0.5)
    makespan = time.time() - t0
    sampler.stop.set()
    for w in workers:
        stop_worker(w)
    worker_info = [worker_report(w) for w in workers]
    time.sleep(1)
    leftover_ffmpeg = sum(1 for e in Path("/proc").iterdir() if e.name.isdigit() and
                          (lambda p: p.exists() and p.read_text().strip() == "ffmpeg")(e / "comm"))

    rows = []
    for label, jid in submitted:
        j = jobs.get(jid)
        rows.append({"label": label, "id": jid, "status": j["status"], "created": j["created"],
                     "started": j["started"], "finished": j["finished"], "attempts": j["attempts"],
                     "worker": j.get("worker"), "error": (j.get("error") or "")[:200]})
    done = [r for r in rows if r["status"] == "selesai"]
    render_rows = [r for r in rows if r["label"].endswith("render")]
    per_unit_wait = {}
    for u in ("A", "B", "C"):
        w = [r["started"] - r["created"] for r in render_rows if r["label"].startswith(u) and r["started"]]
        if w:
            per_unit_wait[u] = round(statistics.mean(w), 2)
    groups = sorted({r["label"] for r in rows})
    cpus = os.cpu_count()
    result = {
        "scenario": args.scenario, "workers": args.workers, "docker": args.docker, "env": args.env, "code": code,
        "cpu_count": cpus, "makespan_s": round(makespan, 2),
        "throughput_jobs_per_min": round(60 * len(done) / makespan, 2),
        "cpu_util_mean": round(statistics.mean(sampler.cpu), 1) if sampler.cpu else None,
        "cpu_util_p95": pct(sampler.cpu, 95), "mem_used_peak_mb": round(max(sampler.mem), 0) if sampler.mem else None,
        "load1_peak": round(max(sampler.load), 2) if sampler.load else None,
        "runnable_per_core_p95": round(pct(sampler.run_q, 95) / cpus, 2) if sampler.run_q else None,
        "runnable_per_core_max": round(max(sampler.run_q) / cpus, 2) if sampler.run_q else None,
        "ffmpeg_threads_peak": max(sampler.ff) if sampler.ff else None,
        "render_wait_p50": pct([r["started"] - r["created"] for r in render_rows if r["started"]], 50),
        "render_wait_p95": pct([r["started"] - r["created"] for r in render_rows if r["started"]], 95),
        "per_unit_render_wait_mean": per_unit_wait, "fairness_jain": jain(list(per_unit_wait.values())),
        "groups": [summarize([r for r in rows if r["label"] == g], g) for g in groups],
        "cancel": cancel_info, "workers_after": worker_info,
        "leaks": {"ffmpeg_processes_after_stop": leftover_ffmpeg,
                  "jobs_not_finished": sum(r["status"] not in jobs.DONE for r in rows),
                  "jobs_run_more_than_once": sum((r["attempts"] or 0) > 1 for r in rows),
                  "oom_killed": sum(bool(w.get("oom_killed")) for w in worker_info)},
        "jobs": rows,
    }
    text = json.dumps(result, indent=2)
    if args.out:
        Path(args.out).write_text(text)
    print(json.dumps({k: v for k, v in result.items() if k != "jobs"}, indent=2))
    shutil.rmtree(data_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
