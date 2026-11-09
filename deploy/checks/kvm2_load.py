"""Simulasi beban profil KVM 2 (P0.1) di dalam cgroup "host tiruan" 2 vCPU / 8 GB (.github/workflows/kvm2.yml).

Seluruh container aplikasi (compose + deploy/compose.kvm2.yml) dan satu container "layanan lain" (RAM 3 GiB tetap +
pengukur jatah CPU) berjalan di slice systemd `kvm2sim.slice` (CPUQuota=200%, MemoryMax=8G, tanpa swap). Beban nyata
lewat API (Caddy HTTPS): video ucapan pendek dianalisis (memuat model Whisper base), lalu video ucapan 15 menit
diunggah & dianalisis sementara tiga render klip diantrekan.

Lulus bila: semua job selesai; tidak ada OOM (event Docker, memory.events slice & tiap container); RAM puncak slice
< 8 GiB; layanan lain tetap mendapat CPU (>= 0,15 core) selama beban puncak. Angka waktu hanya indikatif (runner
GitHub, bukan KVM 2).

    ADMIN_EMAIL=... ADMIN_PASSWORD=... COMPOSE="docker compose -f ..." \\
        python3 deploy/checks/kvm2_load.py short.mp4 long.mp4 > kvm2-sim.json
"""
import http.cookiejar
import json
import os
import shlex
import ssl
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

BASE = os.environ.get("BASE", "https://localhost")
COMPOSE = shlex.split(os.environ.get("COMPOSE", "docker compose"))
SLICE = Path("/sys/fs/cgroup") / os.environ.get("SIM_SLICE", "kvm2sim.slice")
PROBE = os.environ.get("PROBE_CONTAINER", "layanan-lain")
TIMEOUT = float(os.environ.get("SIM_TIMEOUT", "3600"))
ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE        # sertifikat internal Caddy untuk "localhost"
opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=ctx),
                                     urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))


def call(method, path, body=None, data=None, headers=None):
    if body is not None:
        data, headers = json.dumps(body).encode(), {"Content-Type": "application/json"}
    req = urllib.request.Request(BASE + path, method=method, data=data, headers=headers or {})
    try:
        with opener.open(req, timeout=600) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"null")


def upload(path: Path):
    boundary = uuid.uuid4().hex
    head = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{path.name}\"\r\n"
            "Content-Type: video/mp4\r\n\r\n").encode()
    data = head + path.read_bytes() + f"\r\n--{boundary}--\r\n".encode()
    code, res = call("POST", "/api/projects", data=data,
                     headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    assert code == 200, (code, res)
    return res["project"]["id"] if "project" in res else res["id"]


def project(pid):
    return call("GET", f"/api/projects/{pid}")[1]


def wait_ready(pid, timeout):
    deadline = time.time() + timeout
    while time.time() < deadline:
        p = project(pid)
        if p["status"] in ("siap", "gagal", "batal"):
            return p
        time.sleep(3)
    return project(pid)


def read(path: Path, default=None):
    try:
        return path.read_text().strip()
    except OSError:
        return default


def events(path: Path) -> dict:
    raw = read(path, "")
    return {k: int(v) for k, v in (line.split() for line in raw.splitlines() if line)}


class Sampler(threading.Thread):
    """RAM & CPU slice tiap 2 detik (seluruh "host tiruan": aplikasi + layanan lain)."""

    def __init__(self):
        super().__init__(daemon=True)
        self.stop = threading.Event()
        self.max_mem = 0
        self.samples = []

    def run(self):
        last_cpu, last_t = None, None
        while not self.stop.wait(2.0):
            mem = int(read(SLICE / "memory.current", "0"))
            cpu = int(events(SLICE / "cpu.stat").get("usage_usec", 0))
            now = time.time()
            if last_cpu is not None:
                self.samples.append({"t": round(now), "mem_mb": mem // 2**20,
                                     "cpu_cores": round((cpu - last_cpu) / 1e6 / (now - last_t), 2)})
            last_cpu, last_t = cpu, now
            self.max_mem = max(self.max_mem, mem)


def probe_rates():
    """Throughput pengukur CPU "layanan lain" (satu baris JSON per detik dari container probe)."""
    out = subprocess.run(["docker", "logs", PROBE], capture_output=True, text=True).stdout
    return [json.loads(line) for line in out.splitlines() if line.startswith("{")]


def psql(query):
    out = subprocess.run([*COMPOSE, "exec", "-T", "db", "psql", "-U", "videoapp", "-d", "videoapp", "-tAc", query],
                         capture_output=True, text=True)
    return out.stdout.strip()


def containers():
    ids = subprocess.run([*COMPOSE, "ps", "-aq"], capture_output=True, text=True).stdout.split()
    ids.append(subprocess.run(["docker", "inspect", "-f", "{{.Id}}", PROBE], capture_output=True,
                              text=True).stdout.strip())
    rows = []
    for cid in filter(None, ids):
        info = json.loads(subprocess.run(["docker", "inspect", cid], capture_output=True, text=True).stdout)[0]
        scope = SLICE / f"docker-{info['Id']}.scope"
        rows.append({"name": info["Name"].lstrip("/"), "oom_killed": info["State"]["OOMKilled"],
                     "limit_mb": (info["HostConfig"]["Memory"] or 0) // 2**20,
                     "cpus": (info["HostConfig"]["NanoCpus"] or 0) / 1e9,
                     "peak_mb": int(read(scope / "memory.peak", "0")) // 2**20,
                     # CgroupParent tetap tercatat setelah container keluar (mis. migrate); folder scope-nya tidak.
                     "in_slice": info["HostConfig"].get("CgroupParent") == SLICE.name,
                     "running": info["State"]["Running"],
                     "oom_kill": events(scope / "memory.events").get("oom_kill", 0)})
    return rows


def main():
    short, long_ = Path(sys.argv[1]), Path(sys.argv[2])
    code, _ = call("POST", "/api/auth/login", {"email": os.environ["ADMIN_EMAIL"],
                                                "password": os.environ["ADMIN_PASSWORD"]})
    assert code == 200, code
    sampler = Sampler()
    sampler.start()
    t0 = time.time()
    # 1) Video pendek: memuat model Whisper base & menyiapkan sumber render.
    a = upload(short)
    pa = wait_ready(a, 1200)
    t_short = time.time() - t0
    # 2) Video 15 menit + tiga render dari video pendek, diajukan bersamaan.
    load_start = time.time()
    b = upload(long_)
    renders = []
    for start in (0, 25, 50):
        # Profil KVM 2: MAX_PENDING_PER_USER=3. Saat batas tercapai aplikasi menolak dengan 429 (perilaku yang benar);
        # pengguna mencoba lagi setelah ada slot kosong. Penolakan dicatat sebagai bukti batas antrean bekerja.
        body = {"start": start, "end": start + 25, "title": f"sim-{start}", "aspect": "9:16"}
        rejected, deadline = 0, time.time() + TIMEOUT
        code, res = call("POST", f"/api/projects/{a}/render", body)
        while code == 429 and time.time() < deadline:
            rejected += 1
            time.sleep(10)
            code, res = call("POST", f"/api/projects/{a}/render", body)
        renders.append({"start": start, "code": code, "job": (res or {}).get("id"), "ditolak_429_dulu": rejected})
    pb = wait_ready(b, TIMEOUT)
    deadline = time.time() + 900
    while time.time() < deadline and any(j["status"] in ("antre", "jalan", "berhenti") for j in project(a)["jobs"]):
        time.sleep(3)
    load_end = time.time()
    sampler.stop.set()
    sampler.join(5)

    jobs = [dict(zip(("task", "status", "wait_s", "run_s", "error"), r.split("|"))) for r in psql(
        "select task || '|' || status || '|' || round(coalesce(started - created, 0)) || '|' || "
        "round(coalesce(finished - started, 0)) || '|' || coalesce(left(error, 160), '') "
        "from jobs order by created").splitlines() if r]
    rates = probe_rates()
    baseline = [r["ops"] for r in rates if r["t"] < t0] or [1]
    full_core = max(baseline)                                   # probe sendirian = satu core penuh
    during = [r["ops"] / full_core for r in rates if load_start <= r["t"] <= load_end]
    during.sort()
    slice_events = events(SLICE / "memory.events")
    rows = containers()
    result = {
        "host_tiruan": {"cpu_max": read(SLICE / "cpu.max"), "memory_max_mb": int(read(SLICE / "memory.max", "0")) // 2**20,
                        "memory_peak_mb": int(read(SLICE / "memory.peak", "0")) // 2**20,
                        "memory_peak_sampler_mb": sampler.max_mem // 2**20, "memory_events": slice_events},
        "container": rows,
        "job": jobs,
        "render_diajukan": renders,
        "waktu_indikatif_s": {"video_pendek_siap": round(t_short), "beban_15_menit_dan_render": round(load_end - load_start)},
        "layanan_lain_cpu_core": {"median": round(during[len(during) // 2], 2) if during else None,
                                  "p10": round(during[len(during) // 10], 2) if during else None,
                                  "min": round(during[0], 2) if during else None, "n": len(during)},
        "cpu_slice_core_maks": max((s["cpu_cores"] for s in sampler.samples), default=None),
        "status_proyek": {"pendek": pa["status"], "panjang": pb["status"]},
    }
    problems = []
    if pa["status"] != "siap" or pb["status"] != "siap":
        problems.append(f"analisis tidak selesai: {result['status_proyek']}")
    if any(j["status"] != "selesai" for j in jobs):
        problems.append("ada job tidak selesai")
    if any(r["code"] != 200 for r in renders):
        problems.append("render ditolak")
    if slice_events.get("oom_kill") or slice_events.get("oom"):
        problems.append(f"OOM di host tiruan: {slice_events}")
    if any(r["oom_killed"] or r["oom_kill"] for r in rows):
        problems.append("container terkena OOM")
    if not all(r["in_slice"] for r in rows):
        problems.append("ada container di luar host tiruan")
    if result["host_tiruan"]["memory_peak_mb"] >= 8 * 1024:
        problems.append("RAM puncak mencapai batas 8 GiB")
    if not during or result["layanan_lain_cpu_core"]["p10"] < 0.15:
        problems.append(f"layanan lain kekurangan CPU: {result['layanan_lain_cpu_core']}")
    result["masalah"] = problems
    print(json.dumps(result, indent=1, ensure_ascii=False))
    print(f"RINGKASAN: {'LULUS' if not problems else 'GAGAL'} — RAM puncak host tiruan "
          f"{result['host_tiruan']['memory_peak_mb']} MB dari 8192 MB; layanan lain median "
          f"{result['layanan_lain_cpu_core']['median']} core; masalah: {problems or 'tidak ada'}")
    sys.exit(1 if problems else 0)


if __name__ == "__main__":
    main()
