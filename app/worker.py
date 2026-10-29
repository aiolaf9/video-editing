"""Worker: mengambil pekerjaan dari antrean database dan menjalankannya.

Server  : python -m app.worker --queues berat        (jalankan beberapa salinan sesuai jumlah CPU)
          python -m app.worker --queues unduh
Lokal   : otomatis dijalankan sebagai thread di dalam proses web (EMBEDDED_WORKERS=1).
"""
import argparse
import logging
import os
import socket
import threading
import time
import traceback
from typing import Optional

from sqlalchemy import select

from . import db, jobcontrol, jobs, pipeline, storage
from .config import (CANCEL_GRACE, DOWNLOAD_TIMEOUT, JOB_TIMEOUT_MAX, RENDER_TIMEOUT_BASE, RENDER_TIMEOUT_PER_SEC,
                     RETENTION_DAYS)
from .schemas import RenderRequest

log = logging.getLogger("video-editing.worker")


def _run_task(job: jobs.Job):
    pid, p = job.project_id, job.payload
    if job.task == "analyze":
        result = pipeline.safe_analyze(job, pid, p.get("language"))
        db.record_usage("analyze", job.unit_id, job.user_id)
        return result
    if job.task == "download":
        pipeline.download(job, pid, p["url"])
        jobs.submit("analyze", "analisis", pid, {"language": p.get("language")}, "Analisis video",
                    user_id=job.user_id, unit_id=job.unit_id)
        return {"downloaded": True}
    if job.task == "render":
        result = pipeline.render(job, pid, RenderRequest(**p["request"]))
        db.record_usage("render", job.unit_id, job.user_id)
        return result
    raise ValueError(f"Jenis pekerjaan tidak dikenal: {job.task}")


def initial_timeout(job: jobs.Job) -> float:
    """Batas waktu awal per jenis job. Analisis diperketat setelah durasi video diketahui (pipeline.analyze)."""
    if job.task == "render":
        req = job.payload.get("request", {})
        out_seconds = max(1.0, (req.get("end", 0) - req.get("start", 0)) / max(req.get("speed") or 1.0, 0.1))
        return min(JOB_TIMEOUT_MAX, RENDER_TIMEOUT_BASE + RENDER_TIMEOUT_PER_SEC * out_seconds)
    if job.task == "download":
        return min(JOB_TIMEOUT_MAX, DOWNLOAD_TIMEOUT)
    return JOB_TIMEOUT_MAX


def _watchdog(job_id: str, control: jobcontrol.JobControl, done: threading.Event, abandoned: threading.Event,
              standalone: bool) -> None:
    """Berjalan di samping job: heartbeat, permintaan batal dari UI/API, batas waktu, dan job yang macet."""
    last_touch = time.time()
    while not done.wait(2.0):
        now = time.time()
        try:
            if not control.cancelled:
                if now > control.deadline:
                    log.warning("Job %s melewati batas waktu, dihentikan.", job_id)
                    control.cancel("timeout")
                elif jobs.status_of(job_id) == "berhenti":
                    log.info("Job %s dibatalkan pengguna, menghentikan proses.", job_id)
                    control.cancel("dibatalkan")
            elif now - (control.cancelled_at or now) > CANCEL_GRACE:
                # Proses sudah diminta berhenti tetapi job tidak juga selesai (mis. tertahan di kode native).
                # Selesaikan statusnya agar tidak menggantung, lalu berhenti mengirim heartbeat.
                log.error("Job %s tidak berhenti %ss setelah %s.", job_id, CANCEL_GRACE, control.reason)
                jobs.finish(job_id, error="Pekerjaan tidak merespons dan dihentikan paksa.",
                            cancelled=control.reason == "dibatalkan")
                abandoned.set()
                if standalone:
                    # Worker terpisah: keluar agar container/service manager menjalankannya ulang dalam
                    # keadaan bersih. Thread yang macet tidak bisa dihentikan dari dalam proses yang sama.
                    log.critical("Worker keluar untuk dimulai ulang.")
                    os._exit(3)
                return
            if now - last_touch >= 30:
                jobs.touch(job_id)
                last_touch = now
        except Exception:
            log.warning("Watchdog job %s: %s", job_id, traceback.format_exc(limit=1))


def run_one(queues: tuple[str, ...], name: str, standalone: bool = False) -> bool:
    """Jalankan satu pekerjaan bila ada. Mengembalikan False bila antrean kosong."""
    data = jobs.claim(queues, name)
    if data is None:
        return False
    job = jobs.Job(data)
    control = jobcontrol.JobControl(job.id, initial_timeout(job))
    log.info("[%s] mulai %s %s (proyek %s)", name, job.task, job.id, job.project_id)
    done, abandoned = threading.Event(), threading.Event()
    threading.Thread(target=_watchdog, args=(job.id, control, done, abandoned, standalone), daemon=True).start()
    try:
        with jobcontrol.activate(control):
            result = _run_task(job)
        if not abandoned.is_set():
            jobs.finish(job.id, result=result)
            log.info("[%s] selesai %s", name, job.id)
    except jobcontrol.JobCancelled as exc:
        log.info("[%s] %s %s", name, "dibatalkan" if exc.reason == "dibatalkan" else "timeout", job.id)
        if not abandoned.is_set():
            jobs.finish(job.id, error=exc.message, cancelled=exc.reason == "dibatalkan")
    except storage.NotFound:
        jobs.finish(job.id, error="Proyek sudah dihapus.")
    except Exception as exc:
        log.error("[%s] gagal %s:\n%s", name, job.id, traceback.format_exc())
        if not abandoned.is_set():
            jobs.finish(job.id, error=str(exc) or exc.__class__.__name__)
    finally:
        done.set()
    return True


def cleanup_old_projects() -> int:
    if RETENTION_DAYS <= 0:
        return 0
    cutoff = time.time() - RETENTION_DAYS * 86400
    with db.engine().connect() as c:
        old = [r[0] for r in c.execute(select(db.projects.c.id).where(db.projects.c.created < cutoff))]
    for pid in old:
        if not any(j["status"] in jobs.ACTIVE for j in jobs.for_project(pid)):
            storage.delete_project(pid)
    if old:
        log.info("Membersihkan %s proyek lebih tua dari %s hari", len(old), RETENTION_DAYS)
    return len(old)


def loop(queues: tuple[str, ...], name: Optional[str] = None, stop: Optional[threading.Event] = None,
         poll: float = 1.0, standalone: bool = False) -> None:
    name = name or f"{socket.gethostname()}-{os.getpid()}-{'+'.join(queues)}"
    stop = stop or threading.Event()
    last_maintenance = 0.0
    log.info("Worker %s siap (antrean: %s)", name, ", ".join(queues))
    while not stop.is_set():
        try:
            if time.time() - last_maintenance > 60:
                last_maintenance = time.time()
                jobs.requeue_stale()
                if "berat" in queues:
                    cleanup_old_projects()
            if not run_one(queues, name, standalone):
                stop.wait(poll)
        except Exception:
            # Database sempat tidak bisa dihubungi, dsb.: tunggu lalu coba lagi.
            log.error("Kesalahan worker:\n%s", traceback.format_exc())
            stop.wait(5)


def start_embedded(heavy_workers: int) -> threading.Event:
    """Worker sebagai thread di dalam proses web (mode satu komputer)."""
    jobs.requeue_stale(all_running=True)  # sisa pekerjaan saat aplikasi ditutup
    stop = threading.Event()
    specs = [("berat", f"lokal-berat-{i + 1}") for i in range(max(1, heavy_workers))] + [("unduh", "lokal-unduh")]
    for queue, name in specs:
        threading.Thread(target=loop, args=((queue,), name, stop, 0.5), daemon=True, name=name).start()
    return stop


def main() -> None:
    parser = argparse.ArgumentParser(description="Worker antrean Video Editing AI")
    parser.add_argument("--queues", default="berat,unduh", help="berat, unduh, atau keduanya (dipisah koma)")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    queues = tuple(q.strip() for q in args.queues.split(",") if q.strip())
    if hasattr(os, "nice"):
        # Prioritas CPU lebih rendah dari web (diwarisi FFmpeg/Whisper): halaman tetap responsif
        # walau semua core sedang dipakai render.
        os.nice(int(os.environ.get("WORKER_NICE", "10")))
    unknown = set(queues) - set(jobs.QUEUES)
    if unknown:
        parser.error(f"Antrean tidak dikenal: {', '.join(unknown)}")
    try:
        loop(queues, standalone=True)
    except KeyboardInterrupt:
        jobcontrol.cancel_all()
        log.info("Worker dihentikan.")


if __name__ == "__main__":
    main()
