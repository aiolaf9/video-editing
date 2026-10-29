"""Resource mesin untuk worker (P3): anggaran CPU, penjaga RAM, deteksi GPU, dan metrik sistem.

Tanpa dependensi tambahan: membaca /proc dan cgroup (Linux/Docker). Di sistem lain (mis. Windows
untuk pemakaian lokal) fungsi mengembalikan None dan pemeriksaan dilewati, bukan menolak pekerjaan.

Aturan
- CPU   : anggaran thread elastis per job (FFmpeg, OpenCV): core yang tidak sedang dipakai job lain di host
          yang sama, minimal jatah adil cpus/HOST_HEAVY_WORKERS. Prioritas antar-antrean lewat nice
          (render didahulukan, analisis paling rendah), sehingga CPU kosong tetap terpakai tetapi render &
          halaman web tidak tersendat oleh analisis panjang. Batas keras: `cpus` container.
- RAM   : worker hanya mengambil job bila RAM tersedia >= kebutuhan job + cadangan (admission control).
          Lebih baik job menunggu di antrean daripada proses dibunuh OOM killer.
- CPU   : bila server sudah jenuh (load per core tinggi), worker menunda mengambil job sebentar.
- GPU   : TIDAK diasumsikan ada. Dipakai hanya bila nvidia-smi mendeteksi GPU dengan VRAM bebas cukup.
"""
import logging
import math
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Optional

from .config import (CPU_MAX_LOAD_PER_CORE, GPU_MIN_FREE_MB, HOST_HEAVY_WORKERS, MEM_RESERVE_MB, TASK_MEM_MB,
                     WORKER_THREADS)

log = logging.getLogger("video-editing.resources")


# ------------------------------------------------------------------ CPU

def _cgroup_cpus() -> Optional[float]:
    """Batas CPU container (docker `cpus:`), bila ada."""
    try:
        quota, period = Path("/sys/fs/cgroup/cpu.max").read_text().split()[:2]
        if quota != "max":
            return int(quota) / int(period)
    except (OSError, ValueError):
        pass
    try:  # cgroup v1
        quota = int(Path("/sys/fs/cgroup/cpu/cpu.cfs_quota_us").read_text())
        period = int(Path("/sys/fs/cgroup/cpu/cpu.cfs_period_us").read_text())
        if quota > 0:
            return quota / period
    except (OSError, ValueError):
        pass
    return None


def effective_cpus() -> int:
    try:
        n = len(os.sched_getaffinity(0))
    except (AttributeError, OSError):
        n = os.cpu_count() or 1
    limit = _cgroup_cpus()
    if limit:
        n = min(n, max(1, math.floor(limit)))
    return max(1, n)


def fair_share() -> int:
    """Jatah minimum satu job: core tersedia dibagi jumlah worker berat yang bisa jalan bersamaan."""
    return max(1, effective_cpus() // max(1, HOST_HEAVY_WORKERS))


_job_threads = threading.local()


def cpu_threads() -> int:
    """Thread untuk job yang sedang berjalan di thread ini (ditetapkan saat job mulai, lihat
    plan_job_threads). WORKER_THREADS > 0 = selalu tetap."""
    if WORKER_THREADS > 0:
        return WORKER_THREADS
    return getattr(_job_threads, "n", None) or fair_share()


def plan_job_threads(busy_threads: int, busy_jobs: Optional[int] = None) -> int:
    """Anggaran elastis untuk job baru: core yang tidak dipakai job lain di host ini, minimal bagian adil
    di antara job yang benar-benar sedang berjalan (cpus / (job_berjalan + 1)).
    Contoh 4 core: job pertama 4 thread; job kedua yang mulai saat itu 2 thread (bukan 1 atau 4)."""
    cpus = effective_cpus()
    if WORKER_THREADS > 0:
        n = WORKER_THREADS
    else:
        share = cpus // (busy_jobs + 1) if busy_jobs is not None else fair_share()
        n = max(1, share, min(cpus, cpus - busy_threads))
    _job_threads.n = n
    return n


def clear_job_threads() -> None:
    _job_threads.n = None


QUEUE_NICE = {"render": 5, "io": 10, "analisis": 15}


def lower_priority(queues) -> Optional[int]:
    """Turunkan prioritas CPU thread worker ini (diwarisi FFmpeg/Whisper yang dijalankannya).
    Linux: nice berlaku per thread, sehingga worker bawaan (thread di proses web) juga bisa diatur."""
    if not hasattr(os, "setpriority"):
        return None
    target = min((QUEUE_NICE.get(q, 10) for q in queues), default=10)
    try:
        tid = threading.get_native_id()
        current = os.getpriority(os.PRIO_PROCESS, tid)
        if target > current:
            os.setpriority(os.PRIO_PROCESS, tid, target)
        return max(target, current)
    except OSError:
        return None


def load_per_core() -> Optional[float]:
    try:
        return os.getloadavg()[0] / effective_cpus()
    except (AttributeError, OSError):
        return None


_cpu_lock = threading.Lock()
_cpu_last: Optional[tuple[int, int]] = None


def cpu_percent() -> Optional[float]:
    """Pemakaian CPU seluruh mesin sejak panggilan sebelumnya (persen)."""
    global _cpu_last
    try:
        vals = list(map(int, Path("/proc/stat").read_text().splitlines()[0].split()[1:]))
    except (OSError, ValueError, IndexError):
        return None
    total, idle = sum(vals), vals[3] + (vals[4] if len(vals) > 4 else 0)
    with _cpu_lock:
        last, _cpu_last = _cpu_last, (total, idle)
    if last is None or total <= last[0]:
        return None
    return round(100.0 * (1 - (idle - last[1]) / (total - last[0])), 1)


# ------------------------------------------------------------------ RAM

def _meminfo() -> Optional[dict]:
    try:
        return {l.split(":")[0]: int(l.split()[1]) * 1024 for l in Path("/proc/meminfo").read_text().splitlines()}
    except (OSError, ValueError, IndexError):
        return None


def _cgroup_memory() -> Optional[tuple[int, int]]:
    """(batas, terpakai) container dalam byte, bila dibatasi (docker `mem_limit`)."""
    for limit_f, used_f in (("/sys/fs/cgroup/memory.max", "/sys/fs/cgroup/memory.current"),
                            ("/sys/fs/cgroup/memory/memory.limit_in_bytes", "/sys/fs/cgroup/memory/memory.usage_in_bytes")):
        try:
            raw = Path(limit_f).read_text().strip()
            if raw == "max" or int(raw) > 1 << 60:
                return None
            return int(raw), int(Path(used_f).read_text().strip())
        except (OSError, ValueError):
            continue
    return None


def memory() -> Optional[dict]:
    """total & available (byte) untuk proses ini: yang lebih kecil antara host dan batas container."""
    info = _meminfo()
    if info is None:
        return None
    total, avail = info.get("MemTotal", 0), info.get("MemAvailable", info.get("MemFree", 0))
    cg = _cgroup_memory()
    if cg:
        limit, used = cg
        total, avail = min(total, limit), min(avail, max(0, limit - used))
    return {"total": total, "available": avail, "used": max(0, total - avail)}


def required_mb(queue: str) -> int:
    """Perkiraan RAM tambahan untuk satu job di antrean ini (lihat TASK_MEM_MB)."""
    need = TASK_MEM_MB.get(queue, TASK_MEM_MB.get("render", 700))
    if queue == "analisis":
        try:
            from . import transcribe
            if transcribe._model is not None:  # model sudah dimuat di proses ini: tidak perlu RAM lagi
                need = TASK_MEM_MB.get("analisis_loaded", 600)
        except ImportError:
            pass
    return need


# ------------------------------------------------------------------ GPU

_gpu_cache: tuple[float, Optional[list]] = (0.0, None)


def gpus(max_age: float = 30.0) -> list[dict]:
    """GPU NVIDIA yang terdeteksi (kosong bila tidak ada; tidak pernah diasumsikan ada)."""
    global _gpu_cache
    if time.time() - _gpu_cache[0] < max_age and _gpu_cache[1] is not None:
        return _gpu_cache[1]
    found: list[dict] = []
    if shutil.which("nvidia-smi"):
        try:
            out = subprocess.run(["nvidia-smi", "--query-gpu=index,name,memory.total,memory.used,utilization.gpu",
                                  "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=5).stdout
            found = parse_nvidia_smi(out)
        except (OSError, subprocess.SubprocessError):
            found = []
    _gpu_cache = (time.time(), found)
    return found


def parse_nvidia_smi(out: str) -> list[dict]:
    found = []
    for line in out.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 5:
            continue
        try:
            found.append({"index": int(parts[0]), "name": parts[1], "mem_total_mb": int(float(parts[2])),
                          "mem_used_mb": int(float(parts[3])), "util_pct": float(parts[4])})
        except ValueError:
            continue
    return found


def gpu_with_free_vram(min_free_mb: Optional[float] = None) -> Optional[dict]:
    need = GPU_MIN_FREE_MB if min_free_mb is None else min_free_mb
    for g in gpus():
        if g["mem_total_mb"] - g["mem_used_mb"] >= need:
            return g
    return None


# ------------------------------------------------------------------ admission control

def admit(queue: str) -> tuple[bool, str]:
    """Boleh mengambil job baru dari antrean ini sekarang? (ok, alasan bila tidak)."""
    mem = memory()
    if mem is not None:
        need = (required_mb(queue) + MEM_RESERVE_MB) * 1024 * 1024
        if mem["available"] < need:
            return False, (f"RAM tersedia {mem['available'] / 2**20:.0f} MB < kebutuhan "
                           f"{need / 2**20:.0f} MB (job menunggu)")
    load = load_per_core()
    if queue != "io" and load is not None and CPU_MAX_LOAD_PER_CORE > 0 and load > CPU_MAX_LOAD_PER_CORE:
        return False, f"CPU jenuh (load {load:.2f}/core > {CPU_MAX_LOAD_PER_CORE})"
    return True, ""


# ------------------------------------------------------------------ pemakaian per proses (worker + FFmpeg)

_CLK = os.sysconf("SC_CLK_TCK") if hasattr(os, "sysconf") else 100
_PAGE = os.sysconf("SC_PAGE_SIZE") if hasattr(os, "sysconf") else 4096


def _proc_stat(pid: int) -> Optional[tuple[int, float, int]]:
    """(ppid, detik CPU, RSS byte) satu proses."""
    try:
        raw = Path(f"/proc/{pid}/stat").read_text()
        fields = raw[raw.rindex(")") + 2:].split()
        return int(fields[1]), (int(fields[11]) + int(fields[12])) / _CLK, int(fields[21]) * _PAGE
    except (OSError, ValueError, IndexError):
        return None


def process_tree_usage(root: Optional[int] = None) -> Optional[dict]:
    """CPU (detik kumulatif) & RSS proses ini beserta seluruh turunannya (FFmpeg, dll.)."""
    root = root or os.getpid()
    if not Path("/proc/self/stat").exists():
        return None
    stats = {}
    for entry in Path("/proc").iterdir():
        if entry.name.isdigit():
            st = _proc_stat(int(entry.name))
            if st:
                stats[int(entry.name)] = st
    children: dict[int, list[int]] = {}
    for pid, (ppid, _c, _r) in stats.items():
        children.setdefault(ppid, []).append(pid)
    todo, cpu, rss, n = [root], 0.0, 0, 0
    while todo:
        pid = todo.pop()
        if pid in stats:
            cpu += stats[pid][1]
            rss += stats[pid][2]
            n += 1
        todo += children.get(pid, [])
    return {"cpu_seconds": cpu, "rss": rss, "processes": n}


class UsageMeter:
    """CPU% (100 = satu core penuh) & RSS pohon proses worker, dihitung dari selisih antar-panggilan."""

    def __init__(self):
        self._last: Optional[tuple[float, float]] = None

    def sample(self) -> dict:
        u = process_tree_usage()
        if u is None:
            return {}
        now = time.time()
        pct = None
        if self._last and now > self._last[0]:
            pct = round(100.0 * max(0.0, u["cpu_seconds"] - self._last[1]) / (now - self._last[0]), 1)
        self._last = (now, u["cpu_seconds"])
        return {"cpu_pct": pct, "rss_mb": round(u["rss"] / 2**20, 1), "processes": u["processes"]}


def system_snapshot() -> dict:
    mem = memory()
    try:
        la = os.getloadavg()
    except (AttributeError, OSError):
        la = None
    return {
        "cpus": effective_cpus(), "cpu_pct": cpu_percent(), "load": [round(x, 2) for x in la] if la else None,
        "mem_total": mem and mem["total"], "mem_available": mem and mem["available"],
        "gpus": gpus(), "worker_threads": cpu_threads(),
    }
