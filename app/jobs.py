"""Antrean pekerjaan di database: aman dipakai banyak proses web & worker sekaligus.

- submit()  : web memasukkan pekerjaan (status "antre")
- claim()   : worker mengambil satu pekerjaan secara atomik (PostgreSQL: FOR UPDATE SKIP LOCKED)
- Job       : dipakai pipeline untuk melaporkan progress (ditulis ke DB, dibatasi ±1x/detik)
- request_cancel(): batalkan job (antre -> batal seketika; jalan -> berhenti -> batal)
- requeue_stale(): pekerjaan milik worker yang mati dikembalikan ke antrean

Status: antre -> jalan -> selesai | gagal | batal  (jalan -> berhenti -> batal saat dibatalkan)

Antrean (P3): analisis (Whisper, wajah, preview) | render (FFmpeg) | io (unduhan dari link).
Dipisah agar analisis video panjang tidak menahan render pendek pengguna lain. Nama lama
"berat" (= analisis + render) dan "unduh" (= io) tetap diterima.

Urutan pengambilan job dalam satu antrean (claim):
  1. unit dengan job berjalan paling sedikit di antrean itu (pembagian adil antar-workspace),
  2. unit yang paling lama tidak dilayani (round-robin; unit kecil tidak menunggu antrean unit besar),
  3. prioritas job dikurangi umur tunggu / AGING_SECONDS (aging: job lama pasti naik, tidak kelaparan),
  4. waktu masuk (FIFO).
Batas: MAX_RUNNING_PER_UNIT per unit per antrean, MAX_CONCURRENT per antrean di seluruh server.
Pengambilan diserialkan (PostgreSQL: advisory lock; SQLite: kunci tulis) sehingga batas itu tepat.
"""
import json
import logging
import time
from typing import Any, Callable, Optional

from sqlalchemy import and_, case, func, insert, literal, select, text, update

from . import db, jobcontrol
from .config import (AGING_SECONDS, MAX_CONCURRENT, MAX_QUEUE_DEPTH, MAX_QUEUED_PER_UNIT,
                     MAX_RUNNING_PER_UNIT)
from .storage import new_id

log = logging.getLogger("video-editing.jobs")

QUEUES = ("analisis", "render", "io")
# Worker gabungan "berat" mendahulukan render (pendek & ditunggu pengguna), analisis bila render kosong.
LEGACY_QUEUES = {"berat": ("render", "analisis"), "unduh": ("io",)}
TASK_QUEUE = {"analyze": "analisis", "render": "render", "download": "io"}
RUNNING = ("jalan", "berhenti")
SHORT_RENDER_SECONDS = 60
_CLAIM_LOCK = 727276
ACTIVE = ("antre", "jalan", "berhenti")   # masih memakai antrean/worker
DONE = ("selesai", "gagal", "batal")
STALE_AFTER = 120     # detik tanpa heartbeat = worker dianggap mati
MAX_ATTEMPTS = 2


class Job:
    """Pegangan pekerjaan yang sedang dijalankan worker."""

    def __init__(self, data: dict):
        self.id = data["id"]
        self.task = data["task"]
        self.project_id = data["project_id"]
        self.user_id = data.get("user_id")
        self.unit_id = data.get("unit_id")
        self.payload = json.loads(data.get("payload") or "{}")
        self.progress = float(data.get("progress") or 0)
        self.message = data.get("message") or ""
        self._last_write = 0.0
        self._written_message = None

    def update(self, progress: Optional[float] = None, message: Optional[str] = None) -> None:
        # Setiap laporan kemajuan juga titik pemeriksaan batal/timeout (lihat jobcontrol).
        jobcontrol.check()
        if progress is not None:
            self.progress = round(max(0.0, min(100.0, progress)), 1)
        if message is not None:
            self.message = message[:300]
        now = time.time()
        # Pesan baru selalu ditulis; progress saja paling sering 1x per detik.
        if self.message != self._written_message or now - self._last_write >= 1.0:
            self._last_write, self._written_message = now, self.message
            with db.engine().begin() as c:
                c.execute(update(db.jobs).where(db.jobs.c.id == self.id)
                          .values(progress=self.progress, message=self.message, heartbeat=now))

    def stage(self, lo: float, hi: float, message: str) -> Callable[[float], None]:
        """Callback progress 0..1 yang dipetakan ke rentang lo..hi persen."""
        self.update(lo, message)
        return lambda frac: self.update(lo + (hi - lo) * frac)


class QueueFull(Exception):
    """Backpressure: antrean unit/server penuh. status 429 (unit) atau 503 (server)."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def queue_for(task: str) -> str:
    return TASK_QUEUE.get(task, "render")


def expand_queues(names) -> tuple[str, ...]:
    """Nama antrean dari CLI/konfigurasi; nama lama diterjemahkan. Urutan = prioritas antar-antrean."""
    out: list[str] = []
    for name in names:
        for q in LEGACY_QUEUES.get(name, (name,)):
            if q not in out:
                out.append(q)
    return tuple(out)


def priority_for(task: str, payload: dict) -> int:
    """Prioritas tetap per jenis job (lebih kecil = lebih dulu). Aging menaikkannya selama menunggu.
      10 render pendek (hasil <= 60 dtk)   20 render panjang
      30 analisis / unduhan                 40 analisis ulang"""
    if task == "render":
        req = (payload or {}).get("request") or {}
        out_secs = (req.get("end", 0) - req.get("start", 0)) / max(req.get("speed") or 1.0, 0.1)
        return 10 if out_secs <= SHORT_RENDER_SECONDS else 20
    if task == "analyze" and (payload or {}).get("reanalyze"):
        return 40
    return 30


def check_capacity(task: str, unit_id: Optional[str], queue: Optional[str] = None) -> None:
    """Backpressure sebelum menerima pekerjaan baru: tolak dengan jelas daripada antrean tak terbatas."""
    q = queue or queue_for(task)
    j = db.jobs.c
    with db.engine().connect() as c:
        if MAX_QUEUED_PER_UNIT and unit_id:
            n = c.execute(select(func.count()).where(j.queue == q, j.status == "antre", j.unit_id == unit_id)).scalar()
            if n >= MAX_QUEUED_PER_UNIT:
                raise QueueFull(429, f"Antrean unit Anda sedang penuh ({n} pekerjaan menunggu). "
                                     "Tunggu sebagian selesai, lalu coba lagi.")
        if MAX_QUEUE_DEPTH:
            n = c.execute(select(func.count()).where(j.queue == q, j.status == "antre")).scalar()
            if n >= MAX_QUEUE_DEPTH:
                raise QueueFull(503, "Server sedang sangat sibuk. Coba lagi beberapa menit lagi.")


def migrate_legacy_queues() -> int:
    """Job aktif dari versi sebelum P3 (antrean berat/unduh) dipindah ke antrean baru. Aman diulang."""
    j = db.jobs.c
    with db.engine().begin() as c:
        return c.execute(update(db.jobs).where(j.queue.in_(tuple(LEGACY_QUEUES)), j.status.in_(ACTIVE)).values(
            queue=case((j.task == "analyze", "analisis"), (j.task == "download", "io"), else_="render"))).rowcount


def submit(task: str, kind: str, project_id: str, payload: dict, label: str = "",
           queue: Optional[str] = None, user_id: Optional[str] = None, unit_id: Optional[str] = None,
           conn=None, priority: Optional[int] = None) -> dict:
    """Masukkan job ke antrean. `conn`: transaksi yang sudah berjalan (mis. lifecycle.reserve)."""
    jid = new_id()
    if not queue or queue in LEGACY_QUEUES:  # nama lama "berat"/"unduh": antrean ditentukan jenis job
        queue = queue_for(task)
    values = dict(id=jid, task=task, kind=kind, queue=queue, project_id=project_id, user_id=user_id, unit_id=unit_id,
                  label=label[:200], payload=json.dumps(payload, ensure_ascii=False), status="antre", progress=0,
                  message="Menunggu giliran...", attempts=0, created=time.time(),
                  priority=priority_for(task, payload) if priority is None else priority)
    if conn is not None:
        conn.execute(insert(db.jobs).values(**values))
        return db.row(conn.execute(select(db.jobs).where(db.jobs.c.id == jid)).first())
    with db.engine().begin() as c:
        c.execute(insert(db.jobs).values(**values))
    return get(jid)


def get(job_id: str) -> Optional[dict]:
    with db.engine().connect() as c:
        return db.row(c.execute(select(db.jobs).where(db.jobs.c.id == job_id)).first())


def for_project(project_id: str, limit: int = 50) -> list[dict]:
    with db.engine().connect() as c:
        rows = c.execute(select(db.jobs).where(db.jobs.c.project_id == project_id)
                         .order_by(db.jobs.c.created.desc()).limit(limit)).all()
    return [db.row(r) for r in reversed(rows)]


def pending_for_user(user_id: str) -> int:
    with db.engine().connect() as c:
        return c.execute(select(func.count()).select_from(db.jobs).where(
            db.jobs.c.user_id == user_id, db.jobs.c.status.in_(ACTIVE))).scalar() or 0


def view(job: dict) -> dict:
    """Bentuk JSON satu pekerjaan untuk UI, termasuk posisi antrean."""
    return view_many([job])[0]


def view_many(items: list[dict]) -> list[dict]:
    """Bentuk JSON banyak pekerjaan dengan SATU query posisi antrean (bukan satu query per pekerjaan)."""
    waiting = [j for j in items if j["status"] == "antre"]
    active: list = []
    if waiting:
        with db.engine().connect() as c:
            active = c.execute(select(db.jobs.c.queue, db.jobs.c.created).where(
                db.jobs.c.status.in_(ACTIVE),
                db.jobs.c.queue.in_({j["queue"] for j in waiting}),
                db.jobs.c.created < max(j["created"] for j in waiting))).all()
    out = []
    for job in items:
        data = {k: job[k] for k in ("id", "kind", "project_id", "label", "status", "progress", "message",
                                    "error", "created")}
        data["result"] = json.loads(job["result"]) if job.get("result") else None
        if job["status"] == "antre":
            ahead = sum(1 for q, created in active if q == job["queue"] and created < job["created"])
            if ahead:
                data["message"] = f"Menunggu giliran ({ahead} pekerjaan lain sedang/akan diproses)..."
        out.append(data)
    return out


def _pick(conn, queue: str, now: float) -> Optional[str]:
    """Job berikutnya di satu antrean menurut aturan di docstring modul, atau None."""
    j = db.jobs.c
    limit = MAX_CONCURRENT.get(queue, 0)
    if limit:
        running_total = conn.execute(select(func.count()).where(j.queue == queue, j.status.in_(RUNNING))).scalar()
        if running_total >= limit:
            return None
    unit = func.coalesce(j.unit_id, "-")
    running = (select(unit.label("u"), func.count().label("n"))
               .where(j.queue == queue, j.status.in_(RUNNING)).group_by(unit).subquery())
    served = (select(unit.label("u"), func.max(j.started).label("last"))
              .where(j.queue == queue, j.started > now - 3600).group_by(unit).subquery())
    n_running = func.coalesce(running.c.n, 0)
    effective = func.coalesce(j.priority, 50) - (literal(now) - j.created) / AGING_SECONDS
    q = (select(j.id)
         .select_from(db.jobs.outerjoin(running, running.c.u == unit).outerjoin(served, served.c.u == unit))
         .where(j.status == "antre", j.queue == queue))
    if MAX_RUNNING_PER_UNIT:
        q = q.where(n_running < MAX_RUNNING_PER_UNIT)
    q = q.order_by(n_running, func.coalesce(served.c.last, 0), effective, j.created).limit(1)
    return conn.execute(q).scalar()


def claim(queues: tuple[str, ...], worker: str, now: Optional[float] = None) -> Optional[dict]:
    """Ambil satu pekerjaan secara atomik (urutan antrean = prioritas), atau None bila tidak ada yang boleh."""
    now = now or time.time()
    j = db.jobs.c
    with db.engine().begin() as c:
        if db.is_postgres():
            c.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": _CLAIM_LOCK})
        else:  # SQLite: ambil kunci tulis sejak awal agar pilihan & pengambilan tidak diselingi worker lain
            c.execute(text("UPDATE jobs SET status = status WHERE 1 = 0"))
        for queue in queues:
            jid = _pick(c, queue, now)
            if jid is None:
                continue
            row = c.execute(update(db.jobs).where(j.id == jid, j.status == "antre").values(
                status="jalan", worker=worker, started=now, heartbeat=now,
                attempts=j.attempts + 1, message="Memulai...").returning(*db.jobs.c)).first()
            if row is not None:
                return db.row(row)
    return None


def finish(job_id: str, result: Any = None, error: Optional[str] = None, cancelled: bool = False) -> None:
    now = time.time()
    with db.engine().begin() as c:
        if cancelled:
            c.execute(update(db.jobs).where(db.jobs.c.id == job_id).values(
                status="batal", error=error, message="Dibatalkan", finished=now, heartbeat=now))
        elif error is None:
            c.execute(update(db.jobs).where(db.jobs.c.id == job_id).values(
                status="selesai", progress=100, finished=now, heartbeat=now,
                result=json.dumps(result, ensure_ascii=False, default=str)))
            # Pesan akhir "...selesai!" dari pipeline dipertahankan bila ada.
            c.execute(update(db.jobs).where(db.jobs.c.id == job_id, ~db.jobs.c.message.like("%!"))
                      .values(message="Selesai!"))
        else:
            c.execute(update(db.jobs).where(db.jobs.c.id == job_id).values(
                status="gagal", error=error[:4000], message="Gagal", finished=now, heartbeat=now))


def request_cancel(job_id: str) -> Optional[str]:
    """Minta pembatalan. Mengembalikan status baru, atau None bila job sudah selesai."""
    now = time.time()
    with db.engine().begin() as c:
        # Masih di antrean: langsung batal, tidak akan pernah diambil worker.
        if c.execute(update(db.jobs).where(db.jobs.c.id == job_id, db.jobs.c.status == "antre").values(
                status="batal", message="Dibatalkan", finished=now)).rowcount:
            return "batal"
        # Sedang berjalan: worker yang memegangnya akan menghentikan proses (lihat worker._watchdog).
        if c.execute(update(db.jobs).where(db.jobs.c.id == job_id, db.jobs.c.status == "jalan").values(
                status="berhenti", message="Membatalkan...")).rowcount:
            return "berhenti"
    current = get(job_id)
    return "berhenti" if current and current["status"] == "berhenti" else None


def status_of(job_id: str) -> Optional[str]:
    with db.engine().connect() as c:
        return c.execute(select(db.jobs.c.status).where(db.jobs.c.id == job_id)).scalar()


def touch(job_id: str) -> None:
    with db.engine().begin() as c:
        c.execute(update(db.jobs).where(db.jobs.c.id == job_id).values(heartbeat=time.time()))


def requeue(job_id: str, message: str = "Diulang karena server dimulai ulang...") -> bool:
    """Kembalikan job yang dihentikan karena server dimatikan ke antrean (percobaan tidak dihitung).

    Hanya untuk job berstatus 'jalan': job yang sedang dibatalkan pengguna tidak boleh hidup lagi.
    """
    with db.engine().begin() as c:
        return bool(c.execute(update(db.jobs).where(db.jobs.c.id == job_id, db.jobs.c.status == "jalan").values(
            status="antre", worker=None, message=message[:300],
            attempts=func.max(db.jobs.c.attempts - 1, 0) if not db.is_postgres()
            else func.greatest(db.jobs.c.attempts - 1, 0))).rowcount)


def fail_queued(queue: str, error: str, older_than: float) -> int:
    """Gagalkan job 'antre' di antrean ini yang dibuat sebelum `older_than` (job yang tidak akan pernah diproses)."""
    now = time.time()
    with db.engine().begin() as c:
        return c.execute(update(db.jobs).where(db.jobs.c.queue == queue, db.jobs.c.status == "antre",
                                               db.jobs.c.created < older_than).values(
            status="gagal", error=error[:4000], message="Gagal", finished=now, heartbeat=now)).rowcount


def requeue_stale(all_running: bool = False, worker_prefix: Optional[str] = None) -> int:
    """Kembalikan pekerjaan 'jalan' yang worker-nya mati.

    all_running=True (saat start mode lokal) menganggap semua job milik worker berawalan `worker_prefix`
    sudah mati, tanpa menunggu heartbeat basi. Job milik worker lain (mis. worker server) tidak disentuh.
    Job 'batal' tidak pernah diulang; job 'berhenti' yang worker-nya mati menjadi 'batal'.
    """
    cutoff = time.time() + 1 if all_running else time.time() - STALE_AFTER
    owner = db.jobs.c.worker.like(f"{worker_prefix}%") if worker_prefix else db.jobs.c.id.isnot(None)
    stale = and_(db.jobs.c.status == "jalan", db.jobs.c.heartbeat < cutoff, owner)
    with db.engine().begin() as c:
        # Dibatalkan saat worker-nya mati: cukup tandai batal, jangan diulang.
        c.execute(update(db.jobs).where(db.jobs.c.status == "berhenti", db.jobs.c.heartbeat < cutoff, owner).values(
            status="batal", message="Dibatalkan", finished=time.time()))
        failed = c.execute(update(db.jobs).where(stale, db.jobs.c.attempts >= MAX_ATTEMPTS).values(
            status="gagal", error="Pekerjaan terhenti berulang kali (server restart atau kehabisan memori).",
            message="Gagal", finished=time.time())).rowcount
        again = c.execute(update(db.jobs).where(stale, db.jobs.c.attempts < MAX_ATTEMPTS).values(
            status="antre", message="Diulang karena server sempat berhenti...", worker=None)).rowcount
    if failed or again:
        log.warning("Pekerjaan macet: %s diulang, %s digagalkan", again, failed)
    return again + failed


def queue_stats() -> dict:
    with db.engine().connect() as c:
        rows = c.execute(select(db.jobs.c.queue, db.jobs.c.status, func.count())
                         .where(db.jobs.c.status.in_(ACTIVE))
                         .group_by(db.jobs.c.queue, db.jobs.c.status)).all()
        alive = c.execute(select(func.count(func.distinct(db.jobs.c.worker))).where(
            db.jobs.c.status.in_(("jalan", "berhenti")), db.jobs.c.heartbeat >= time.time() - STALE_AFTER)).scalar() or 0
    out: dict = {q: {"antre": 0, "jalan": 0} for q in QUEUES}
    for q, s, n in rows:
        bucket = out.setdefault(q, {"antre": 0, "jalan": 0})
        key = "jalan" if s == "berhenti" else s
        bucket[key] = bucket.get(key, 0) + n
    out["busy_workers"] = alive
    return out


def ping() -> bool:
    with db.engine().connect() as c:
        c.execute(text("SELECT 1"))
    return True
