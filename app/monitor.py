"""Monitoring (P3): registri worker, metrik antrean, dan snapshot sistem untuk admin & Prometheus.

Setiap worker menulis barisnya di tabel `workers` secara berkala (status, job, CPU/RAM pohon prosesnya,
snapshot mesin tempat ia berjalan). Web membaca tabel itu + tabel jobs, sehingga monitoring tetap benar
walau web dan worker berjalan di container/mesin berbeda.
"""
import json
import os
import socket
import time
from typing import Optional

from sqlalchemy import delete, func, insert, select, true, update

from . import db, jobs, lifecycle, resources
from .config import WORKER_REPORT_INTERVAL

HOST = socket.gethostname()[:80]


def alive_after() -> float:
    return max(3 * WORKER_REPORT_INTERVAL, 30.0) + 30.0


def register(name: str, queues: tuple[str, ...]) -> None:
    now = time.time()
    values = dict(host=HOST, pid=os.getpid(), queues=",".join(queues)[:60], state="idle", reason=None, job_id=None,
                  threads=resources.cpu_threads(), started=now, heartbeat=now)
    with db.engine().begin() as c:
        # Worker yang sudah lama mati (> 1 hari) dihapus di sini, bukan saat monitoring dibaca.
        c.execute(delete(db.workers).where(db.workers.c.heartbeat < now - 86400))
        if not c.execute(update(db.workers).where(db.workers.c.name == name).values(**values)).rowcount:
            c.execute(insert(db.workers).values(name=name, jobs_ok=0, jobs_failed=0, **values))


def report(name: str, state: str, job_id: Optional[str] = None, reason: Optional[str] = None,
           usage: Optional[dict] = None, system: bool = False, ok: int = 0, failed: int = 0,
           threads: Optional[int] = None) -> None:
    values = dict(state=state, job_id=job_id, reason=(reason or None) and reason[:200], heartbeat=time.time())
    if threads is not None:
        values["threads"] = threads
    if usage:
        values.update(cpu_pct=usage.get("cpu_pct"), rss_mb=usage.get("rss_mb"))
    if system:
        values["system"] = json.dumps(resources.system_snapshot())
    w = db.workers.c
    if ok:
        values["jobs_ok"] = w.jobs_ok + ok
    if failed:
        values["jobs_failed"] = w.jobs_failed + failed
    with db.engine().begin() as c:
        c.execute(update(db.workers).where(w.name == name).values(**values))


def busy_on_host(exclude: str, now: Optional[float] = None) -> tuple[int, int]:
    """(thread, job) yang sedang dipakai worker lain di host ini (untuk anggaran thread elastis)."""
    now = now or time.time()
    w = db.workers.c
    with db.engine().connect() as c:
        row = c.execute(select(func.coalesce(func.sum(w.threads), 0), func.count()).where(
            w.host == HOST, w.name != exclude, w.state == "sibuk", w.heartbeat >= now - alive_after())).first()
    return int(row[0] or 0), int(row[1] or 0)


def list_workers(now: Optional[float] = None) -> list[dict]:
    now = now or time.time()
    w = db.workers.c
    with db.engine().connect() as c:  # hanya membaca: monitoring tidak boleh menambah beban tulis
        rows = [db.row(r) for r in c.execute(select(db.workers).where(w.heartbeat >= now - 86400)
                                             .order_by(w.queues, w.name))]
    for r in rows:
        r["alive"] = r["heartbeat"] >= now - alive_after() and r["state"] != "berhenti"
        r["heartbeat_age"] = round(now - r["heartbeat"], 1)
        r["system"] = json.loads(r["system"]) if r.get("system") else None
    return rows


def _pct(values: list[float], p: float) -> Optional[float]:
    if not values:
        return None
    s = sorted(values)
    return round(s[max(0, min(len(s) - 1, round(p / 100 * (len(s) - 1))))], 2)


def queue_metrics(window: float = 3600, now: Optional[float] = None, unit_id: Optional[str] = None) -> dict:
    """Per antrean: menunggu, berjalan, umur job tertua, waktu tunggu & durasi (p50/p95), gagal, selesai."""
    now = now or time.time()
    j = db.jobs.c
    scope = (j.unit_id == unit_id) if unit_id else true()
    out = {}
    with db.engine().connect() as c:
        for q in jobs.QUEUES:
            queued = c.execute(select(func.count(), func.min(j.created)).where(
                j.queue == q, j.status == "antre", scope)).first()
            running = c.execute(select(func.count()).where(j.queue == q, j.status.in_(jobs.RUNNING), scope)).scalar()
            recent = c.execute(select(j.task, j.status, j.created, j.started, j.finished).where(
                j.queue == q, j.finished >= now - window, scope).limit(5000)).all()
            waits = [r.started - r.created for r in recent if r.started]
            runs: dict[str, list[float]] = {}
            for r in recent:
                if r.status == "selesai" and r.started:
                    runs.setdefault(r.task, []).append(r.finished - r.started)
            out[q] = {
                "queued": queued[0], "running": running,
                "oldest_wait": round(now - queued[1], 1) if queued[1] else 0,
                "wait_p50": _pct(waits, 50), "wait_p95": _pct(waits, 95),
                "duration": {t: {"p50": _pct(v, 50), "p95": _pct(v, 95), "n": len(v)} for t, v in runs.items()},
                "done": sum(r.status == "selesai" for r in recent),
                "failed": sum(r.status == "gagal" for r in recent),
                "cancelled": sum(r.status == "batal" for r in recent),
            }
        per_unit = c.execute(select(j.unit_id, j.queue, j.status, func.count()).where(
            j.status.in_(jobs.ACTIVE), scope).group_by(j.unit_id, j.queue, j.status)).all()
    units: dict = {}
    for uid, q, st, n in per_unit:
        bucket = units.setdefault(uid or "-", {}).setdefault(q, {"queued": 0, "running": 0})
        bucket["queued" if st == "antre" else "running"] += n
    return {"window": window, "queues": out, "units": units}


def snapshot() -> dict:
    disk = lifecycle.disk_status()
    return {"at": time.time(), "web": {"host": HOST, **resources.system_snapshot()},
            "disk": {k: disk[k] for k in ("total", "used", "free", "usable")},
            "workers": list_workers(), **queue_metrics()}


def prometheus(snap: dict) -> str:
    """Format teks Prometheus (dibaca /metrics)."""
    lines = []

    def metric(name, value, help_text, labels: Optional[dict] = None, kind="gauge"):
        if value is None:
            return
        if not any(l.startswith(f"# HELP {name} ") for l in lines):
            lines.append(f"# HELP {name} {help_text}")
            lines.append(f"# TYPE {name} {kind}")
        lab = ",".join(f'{k}="{str(v).replace(chr(34), "")}"' for k, v in (labels or {}).items())
        lines.append(f"{name}{{{lab}}} {value}" if lab else f"{name} {value}")

    web = snap["web"]
    metric("ve_cpu_percent", web.get("cpu_pct"), "Pemakaian CPU mesin web (persen).", {"host": web["host"]})
    if web.get("load"):
        metric("ve_load1", web["load"][0], "Load average 1 menit.", {"host": web["host"]})
    metric("ve_memory_available_bytes", web.get("mem_available"), "RAM tersedia.", {"host": web["host"]})
    metric("ve_memory_total_bytes", web.get("mem_total"), "RAM total.", {"host": web["host"]})
    for g in web.get("gpus") or []:
        metric("ve_gpu_memory_used_mb", g["mem_used_mb"], "VRAM terpakai.", {"gpu": g["index"]})
        metric("ve_gpu_util_percent", g["util_pct"], "Pemakaian GPU.", {"gpu": g["index"]})
    metric("ve_disk_free_bytes", snap["disk"]["free"], "Sisa disk data.")
    for q, m in snap["queues"].items():
        metric("ve_queue_jobs", m["queued"], "Job menunggu per antrean.", {"queue": q, "state": "queued"})
        metric("ve_queue_jobs", m["running"], "Job menunggu per antrean.", {"queue": q, "state": "running"})
        metric("ve_queue_oldest_wait_seconds", m["oldest_wait"], "Umur job tertua yang menunggu.", {"queue": q})
        metric("ve_queue_wait_seconds_p95", m["wait_p95"], "Waktu tunggu p95 (1 jam).", {"queue": q})
        metric("ve_jobs_failed_window", m["failed"], "Job gagal (1 jam).", {"queue": q})
        metric("ve_jobs_done_window", m["done"], "Job selesai (1 jam).", {"queue": q})
        for task, d in m["duration"].items():
            metric("ve_job_duration_seconds_p95", d["p95"], "Durasi job p95 (1 jam).", {"queue": q, "task": task})
    for w in snap["workers"]:
        lab = {"worker": w["name"], "queues": w["queues"]}
        metric("ve_worker_up", int(w["alive"]), "Worker hidup (heartbeat segar).", lab)
        metric("ve_worker_busy", int(w["state"] == "sibuk"), "Worker sedang mengerjakan job.", lab)
        metric("ve_worker_cpu_percent", w.get("cpu_pct"), "CPU worker + FFmpeg (100 = 1 core).", lab)
        metric("ve_worker_rss_mb", w.get("rss_mb"), "RAM worker + FFmpeg (MB).", lab)
    return "\n".join(lines) + "\n"
