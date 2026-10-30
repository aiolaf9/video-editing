"""Worker: mengambil pekerjaan dari antrean database dan menjalankannya.

Server  : python -m app.worker --queues analisis    (Whisper; RAM besar, 1 per 2-4 vCPU)
          python -m app.worker --queues render      (FFmpeg; tambah salinan bila render sering antre)
          python -m app.worker --queues io          (unduhan dari link)
          Nama lama tetap bisa: berat (= analisis,render) dan unduh (= io). Beberapa antrean dipisah koma;
          urutannya = prioritas (mis. "render,analisis": render didahulukan, analisis bila render kosong).
Lokal   : otomatis dijalankan sebagai thread di dalam proses web (EMBEDDED_WORKERS=1).
"""
import argparse
import logging
import os
import signal
import socket
import threading
import time
import traceback
from typing import Optional


from . import db, jobcontrol, jobs, lifecycle, monitor, pipeline, resources, storage
from .config import (ANALYSIS_WORKERS, CANCEL_GRACE, CLEANUP_INTERVAL, CPU_WAIT_MAX, DOWNLOAD_TIMEOUT,
                     JOB_TIMEOUT_MAX, RENDER_TIMEOUT_BASE, RENDER_TIMEOUT_PER_SEC, RETENTION_DAYS,
                     UNSERVABLE_FAIL_AFTER_SECONDS, WORKER_REPORT_INTERVAL)
from .schemas import RenderRequest

log = logging.getLogger("video-editing.worker")
LOCAL_PREFIX = "lokal-"   # nama worker bawaan (thread di dalam proses web)


def _media_ready(pid: str) -> None:
    """Pastikan media proyek masih ada sebelum mulai. `menghapus` bisa sesaat (pembersih membatalkan
    penghapusan karena melihat job ini), jadi ditunggu sebentar."""
    for _ in range(20):
        state = lifecycle.info(pid)["state"]
        if state != "menghapus":
            break
        time.sleep(0.5)
    # Job yang sudah diterima tetap boleh jalan walau masa simpan habis saat menunggu di antrean
    # (job aktif = lease); yang ditolak hanya media yang benar-benar sudah dihapus.
    lifecycle.require_present(pid)
    lifecycle.ensure_disk()


def _friendly_error(exc: BaseException) -> str:
    text = str(exc) or exc.__class__.__name__
    if "No space left on device" in text or getattr(exc, "errno", None) == 28:
        return "Ruang disk server penuh saat memproses. Coba lagi nanti atau hubungi admin."
    return text


def _run_task(job: jobs.Job):
    pid, p = job.project_id, job.payload
    _media_ready(pid)
    if job.task == "analyze":
        result = pipeline.safe_analyze(job, pid, p.get("language"))
        db.record_usage("analyze", job.unit_id, job.user_id)
        return result
    if job.task == "download":
        pipeline.download(job, pid, p["url"], trusted=bool(p.get("trusted")))
        try:  # ukuran video dari link baru diketahui sekarang: tegakkan kuota unit secara atomik
            lifecycle.commit_size(pid, lifecycle.unit_row(job.unit_id))
        except lifecycle.StorageFull:
            for leftover in storage.project_dir(pid).glob("source.*"):
                leftover.unlink(missing_ok=True)
            storage.update_project(pid, status="gagal", name="Unduhan melebihi kuota")
            lifecycle.refresh_bytes(pid)
            raise
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
              standalone: bool, name: Optional[str] = None, meter: Optional[resources.UsageMeter] = None) -> None:
    """Berjalan di samping job: heartbeat, permintaan batal dari UI/API, batas waktu, dan job yang macet."""
    last_touch = last_report = time.time()
    while not done.wait(2.0):
        now = time.time()
        if name and now - last_report >= WORKER_REPORT_INTERVAL:
            last_report = now
            try:  # pemakaian CPU/RAM worker + FFmpeg selama job berjalan
                monitor.report(name, "sibuk", job_id, usage=meter.sample() if meter else None)
            except Exception:
                pass
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


def _report(name: str, state: str, **kw) -> None:
    try:
        monitor.report(name, state, **kw)
    except Exception:  # monitoring tidak boleh menghentikan worker
        log.debug("Laporan worker gagal:\n%s", traceback.format_exc(limit=1))


def run_one(queues: tuple[str, ...], name: str, standalone: bool = False,
            meter: Optional[resources.UsageMeter] = None) -> bool:
    """Jalankan satu pekerjaan bila ada. Mengembalikan False bila antrean kosong."""
    data = jobs.claim(queues, name)
    if data is None:
        return False
    # Cek RAM sekali lagi untuk job yang benar-benar diklaim, lalu cadangkan kebutuhannya: thread lain di proses ini
    # (mode tanam) tidak boleh ikut lolos sebelum job ini sempat memakai RAM-nya.
    with resources.ADMISSION_LOCK:
        ok, why = resources.admit_memory(data["queue"])
        if ok:
            resources.reserve(data["id"], data["queue"])
    if not ok:
        jobs.requeue(data["id"], message=WAIT_TEMPORARY)
        log.info("[%s] %s dikembalikan ke antrean: %s", name, data["id"], why)
        return False
    job = jobs.Job(data)
    control = jobcontrol.JobControl(job.id, initial_timeout(job))
    try:
        busy_threads, busy_jobs = monitor.busy_on_host(name)
    except Exception:
        busy_threads, busy_jobs = 0, None
    threads = resources.plan_job_threads(busy_threads, busy_jobs)
    log.info("[%s] mulai %s %s (proyek %s, %d thread)", name, job.task, job.id, job.project_id, threads)
    _report(name, "sibuk", job_id=job.id, threads=threads)
    done, abandoned = threading.Event(), threading.Event()
    threading.Thread(target=_watchdog, args=(job.id, control, done, abandoned, standalone, name, meter),
                     daemon=True).start()
    outcome = "failed"
    try:
        with jobcontrol.activate(control):
            result = _run_task(job)
        if not abandoned.is_set():
            jobs.finish(job.id, result=result)
            log.info("[%s] selesai %s", name, job.id)
            outcome = "ok"
        _refresh_size(job.project_id)
    except jobcontrol.JobCancelled as exc:
        log.info("[%s] %s %s", name, exc.reason, job.id)
        outcome = "cancelled"
        if exc.reason == "shutdown":
            jobs.requeue(job.id)  # server dimatikan: bukan kesalahan pengguna, lanjutkan nanti
        elif not abandoned.is_set():
            jobs.finish(job.id, error=exc.message, cancelled=exc.reason == "dibatalkan")
    except storage.NotFound:
        jobs.finish(job.id, error="Proyek sudah dihapus.")
    except lifecycle.MediaGone:
        jobs.finish(job.id, error="Media proyek sudah dihapus otomatis (melewati masa simpan).")
    except lifecycle.StorageFull as exc:
        jobs.finish(job.id, error=str(exc))
    except Exception as exc:
        log.error("[%s] gagal %s:\n%s", name, job.id, traceback.format_exc())
        if not abandoned.is_set():
            jobs.finish(job.id, error=_friendly_error(exc))
        _refresh_size(job.project_id)
    finally:
        done.set()
        resources.release(job.id)
        resources.clear_job_threads()
        _collect(job.project_id)
        _report(name, "idle", usage=meter.sample() if meter else None,
                ok=int(outcome == "ok"), failed=int(outcome == "failed"))
    return True


def admitted(queues: tuple[str, ...], waiting_since: Optional[float],
             incapable: Optional[dict] = None) -> tuple[tuple[str, ...], str]:
    """Antrean yang boleh diambil sekarang menurut resource mesin (lihat resources.admit).
    RAM: tegas (job menunggu). CPU jenuh: ditunda paling lama CPU_WAIT_MAX detik lalu tetap jalan.
    `incapable`: antrean yang tidak akan pernah muat di worker ini; tidak diambil (worker lain mungkin mampu)."""
    allowed, reasons = [], []
    for q in queues:
        if incapable and q in incapable:
            reasons.append(f"{q}: tidak akan pernah muat ({incapable[q]})")
            continue
        ok, why = resources.admit(q)
        if ok or (why.startswith("CPU") and waiting_since is not None and time.time() - waiting_since > CPU_WAIT_MAX):
            allowed.append(q)
        else:
            reasons.append(f"{q}: {why}")
    return tuple(allowed), "; ".join(reasons)


def _refresh_size(pid: str) -> None:
    try:
        lifecycle.refresh_bytes(pid)
    except Exception:  # proyek dihapus di tengah jalan, dsb.
        pass


def _collect(pid: str) -> None:
    """Job terakhir selesai dan masa simpan sudah lewat: hapus media sekarang, bukan di putaran berikutnya."""
    try:
        lifecycle.collect_if_expired(pid)
    except Exception:
        log.warning("Pembersihan media %s setelah job gagal:\n%s", pid, traceback.format_exc(limit=2))


def incapable_queues(queues: tuple[str, ...], known: Optional[dict] = None) -> dict:
    """Antrean yang tidak akan pernah muat di worker ini (lihat resources.capacity_shortfall); dicatat sekali."""
    found = {q: why for q in queues if (why := resources.capacity_shortfall(q))}
    for q, why in found.items():
        if known and known.get(q) == why:
            continue
        log.error("Antrean %s tidak akan pernah muat di worker ini: %s. Naikkan mem_limit container/RAM, atau "
                  "turunkan kebutuhan (mis. WHISPER_MODEL lebih kecil).", q, why)
    return found


# Pesan untuk pengguna biasa: tanpa angka RAM atau nama server (detail teknis hanya di log & halaman admin).
WAIT_TEMPORARY = "Menunggu kapasitas server tersedia..."
WAIT_CONFIG = "Menunggu server dengan kapasitas memori yang cukup. Bila lama tidak berubah, hubungi admin."
FAIL_CONFIG = "Pekerjaan ini membutuhkan memori lebih besar daripada kapasitas server. Hubungi admin."
_unservable_since: dict = {}     # antrean -> sejak kapan proses ini terus-menerus melihatnya tidak dapat dilayani


def handle_unservable(now: Optional[float] = None) -> int:
    """Antrean yang tidak dapat dilayani konfigurasi worker (monitor.queue_capacity == "konfigurasi"): job tetap
    menunggu dengan pesan jelas, admin diberi peringatan. Gagal permanen hanya bila UNSERVABLE_FAIL_AFTER_SECONDS > 0
    dan kondisi itu teramati terus-menerus selama itu; berubahnya konfigurasi mengulang hitungan."""
    current = monitor.unservable_queues()          # status worker selalu menurut heartbeat saat ini
    now = now or time.time()                       # `now` hanya untuk menghitung lamanya kondisi teramati
    for q in [q for q in _unservable_since if q not in current]:
        log.warning("Antrean %s kembali dapat dilayani worker.", q)
        del _unservable_since[q]
    failed = 0
    for q, why in current.items():
        if q not in _unservable_since:
            _unservable_since[q] = now
            log.error("Antrean %s tidak dapat dilayani worker mana pun yang dikenal (%s). Job menunggu; naikkan "
                      "mem_limit/RAM worker atau perkecil kebutuhan (mis. WHISPER_MODEL).", q, why)
        jobs.mark_waiting(q, WAIT_CONFIG)
        if UNSERVABLE_FAIL_AFTER_SECONDS > 0 and now - _unservable_since[q] >= UNSERVABLE_FAIL_AFTER_SECONDS:
            n = jobs.fail_queued(q, FAIL_CONFIG, older_than=now - UNSERVABLE_FAIL_AFTER_SECONDS)
            if n:
                log.error("%d job antrean %s digagalkan: tidak dapat dilayani selama %.0f dtk (%s)", n, q,
                          now - _unservable_since[q], why)
            failed += n
    return failed


def loop(queues: tuple[str, ...], name: Optional[str] = None, stop: Optional[threading.Event] = None,
         poll: float = 1.0, standalone: bool = False) -> None:
    queues = jobs.expand_queues(queues)
    name = name or f"{socket.gethostname()}-{os.getpid()}-{'+'.join(queues)}"
    stop = stop or threading.Event()
    last_maintenance = last_sweep = last_report = 0.0
    waiting_since: Optional[float] = None
    meter = resources.UsageMeter()
    incapable = incapable_queues(queues)
    nice = resources.lower_priority(queues)
    log.info("Worker %s siap (antrean: %s, jatah minimum %d thread, nice %s)", name, ", ".join(queues),
             resources.fair_share(), nice)
    if RETENTION_DAYS:
        log.warning("RETENTION_DAYS tidak dipakai lagi; masa simpan media diatur MEDIA_RETENTION_HOURS/"
                    "MEDIA_MAX_HOURS (P2).")
    try:
        monitor.register(name, queues)
    except Exception:
        log.warning("Registrasi worker gagal:\n%s", traceback.format_exc(limit=1))
    while not stop.is_set():
        try:
            now = time.time()
            if now - last_maintenance > 60:
                last_maintenance = now
                jobs.requeue_stale()
                handle_unservable(now)
            if {"analisis", "render"} & set(queues) and now - last_sweep > CLEANUP_INTERVAL:
                last_sweep = now
                lifecycle.sweep()  # hanya satu worker yang benar-benar bekerja (advisory lock)
            allowed, reason = admitted(queues, waiting_since, incapable)
            if not allowed and waiting_since is None:
                waiting_since = now
                log.warning("[%s] menunda mengambil job: %s", name, reason)
                last_report = 0.0                                   # laporkan "menunggu" segera
            if now - last_report >= WORKER_REPORT_INTERVAL:
                last_report = now
                if incapable:                                  # batas container bisa dinaikkan (docker update)
                    incapable = incapable_queues(queues, incapable)
                _report(name, "menunggu" if not allowed else "idle", reason=reason or None, usage=meter.sample(),
                        system=True, extra={"blocked_since": waiting_since if not allowed else None,
                                            "incapable": incapable})
            if not allowed:
                stop.wait(max(poll, 2.0))
                continue
            waiting_since = None
            if not run_one(allowed, name, standalone, meter):
                stop.wait(poll)
        except Exception:
            # Database sempat tidak bisa dihubungi, dsb.: tunggu lalu coba lagi.
            log.error("Kesalahan worker:\n%s", traceback.format_exc())
            stop.wait(5)
    _report(name, "berhenti")


def start_embedded(render_workers: int) -> threading.Event:
    """Worker sebagai thread di dalam proses web (mode satu komputer). Worker render khusus render, sehingga
    analisis panjang tidak menahan render; worker analisis ikut merender bila tidak ada analisis
    (konfigurasi hibrida, terukur paling seimbang di benchmark P3)."""
    # Sisa pekerjaan saat aplikasi lokal ditutup. Hanya milik worker lokal: job worker server lain aman.
    jobs.migrate_legacy_queues()
    jobs.requeue_stale(all_running=True, worker_prefix=LOCAL_PREFIX)
    stop = threading.Event()
    specs = [(("analisis", "render"), f"{LOCAL_PREFIX}analisis-{i + 1}") for i in range(max(1, ANALYSIS_WORKERS))] + \
        [(("render",), f"{LOCAL_PREFIX}render-{i + 1}") for i in range(max(1, render_workers))] + \
        [(("io",), f"{LOCAL_PREFIX}io")]
    for queues, name in specs:
        threading.Thread(target=loop, args=(queues, name, stop, 0.5), daemon=True, name=name).start()
    return stop


def main() -> None:
    parser = argparse.ArgumentParser(description="Worker antrean Video Editing AI")
    parser.add_argument("--queues", default="analisis,render,io",
                        help="analisis, render, io (dipisah koma; urutan = prioritas). Nama lama: berat, unduh")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    queues = jobs.expand_queues(q.strip() for q in args.queues.split(",") if q.strip())
    stop = threading.Event()

    def on_sigterm(_signum, _frame):
        # `docker stop` / systemd: hentikan FFmpeg, kembalikan job ke antrean, lalu keluar dengan rapi.
        log.info("SIGTERM diterima, worker berhenti setelah job dikembalikan ke antrean.")
        stop.set()
        jobcontrol.cancel_all("shutdown")

    signal.signal(signal.SIGTERM, on_sigterm)
    if os.environ.get("WORKER_NICE") and hasattr(os, "nice"):
        # Prioritas CPU lebih rendah dari web (diwarisi FFmpeg/Whisper). Tanpa WORKER_NICE, prioritas
        # diatur per antrean di loop() (render 5, io 10, analisis 15).
        os.nice(int(os.environ["WORKER_NICE"]))
    unknown = set(queues) - set(jobs.QUEUES)
    if unknown:
        parser.error(f"Antrean tidak dikenal: {', '.join(unknown)}")
    jobs.migrate_legacy_queues()
    try:
        loop(queues, stop=stop, standalone=True)
    except KeyboardInterrupt:
        jobcontrol.cancel_all("shutdown")
    log.info("Worker dihentikan.")


if __name__ == "__main__":
    main()
