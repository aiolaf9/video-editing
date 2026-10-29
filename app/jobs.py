"""Antrean pekerjaan di database: aman dipakai banyak proses web & worker sekaligus.

- submit()  : web memasukkan pekerjaan (status "antre")
- claim()   : worker mengambil satu pekerjaan secara atomik (PostgreSQL: FOR UPDATE SKIP LOCKED)
- Job       : dipakai pipeline untuk melaporkan progress (ditulis ke DB, dibatasi ±1x/detik)
- requeue_stale(): pekerjaan milik worker yang mati dikembalikan ke antrean
"""
import json
import logging
import time
from typing import Any, Callable, Optional

from sqlalchemy import and_, func, insert, select, text, update

from . import db
from .storage import new_id

log = logging.getLogger("video-editing.jobs")

QUEUES = ("berat", "unduh")
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


def submit(task: str, kind: str, project_id: str, payload: dict, label: str = "",
           queue: str = "berat", user_id: Optional[str] = None, unit_id: Optional[str] = None) -> dict:
    jid = new_id()
    with db.engine().begin() as c:
        c.execute(insert(db.jobs).values(
            id=jid, task=task, kind=kind, queue=queue, project_id=project_id, user_id=user_id, unit_id=unit_id,
            label=label[:200], payload=json.dumps(payload, ensure_ascii=False), status="antre", progress=0,
            message="Menunggu giliran...", attempts=0, created=time.time()))
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
            db.jobs.c.user_id == user_id, db.jobs.c.status.in_(("antre", "jalan")))).scalar() or 0


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
                db.jobs.c.status.in_(("antre", "jalan")),
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


def claim(queues: tuple[str, ...], worker: str) -> Optional[dict]:
    """Ambil satu pekerjaan tertua secara atomik, atau None bila antrean kosong."""
    now = time.time()
    inner = (select(db.jobs.c.id).where(db.jobs.c.status == "antre", db.jobs.c.queue.in_(queues))
             .order_by(db.jobs.c.created).limit(1))
    if db.is_postgres():
        inner = inner.with_for_update(skip_locked=True)
    stmt = (update(db.jobs).where(db.jobs.c.id == inner.scalar_subquery(), db.jobs.c.status == "antre")
            .values(status="jalan", worker=worker, started=now, heartbeat=now,
                    attempts=db.jobs.c.attempts + 1, message="Memulai...")
            .returning(*db.jobs.c))
    with db.engine().begin() as c:
        return db.row(c.execute(stmt).first())


def finish(job_id: str, result: Any = None, error: Optional[str] = None) -> None:
    now = time.time()
    with db.engine().begin() as c:
        if error is None:
            c.execute(update(db.jobs).where(db.jobs.c.id == job_id).values(
                status="selesai", progress=100, finished=now, heartbeat=now,
                result=json.dumps(result, ensure_ascii=False, default=str)))
            # Pesan akhir "...selesai!" dari pipeline dipertahankan bila ada.
            c.execute(update(db.jobs).where(db.jobs.c.id == job_id, ~db.jobs.c.message.like("%!"))
                      .values(message="Selesai!"))
        else:
            c.execute(update(db.jobs).where(db.jobs.c.id == job_id).values(
                status="gagal", error=error[:4000], message="Gagal", finished=now, heartbeat=now))


def touch(job_id: str) -> None:
    with db.engine().begin() as c:
        c.execute(update(db.jobs).where(db.jobs.c.id == job_id).values(heartbeat=time.time()))


def requeue_stale(all_running: bool = False) -> int:
    """Kembalikan pekerjaan 'jalan' yang worker-nya mati. all_running=True dipakai saat start mode lokal."""
    cutoff = time.time() + 1 if all_running else time.time() - STALE_AFTER
    stale = and_(db.jobs.c.status == "jalan", db.jobs.c.heartbeat < cutoff)
    with db.engine().begin() as c:
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
                         .where(db.jobs.c.status.in_(("antre", "jalan")))
                         .group_by(db.jobs.c.queue, db.jobs.c.status)).all()
        alive = c.execute(select(func.count(func.distinct(db.jobs.c.worker))).where(
            db.jobs.c.status == "jalan", db.jobs.c.heartbeat >= time.time() - STALE_AFTER)).scalar() or 0
    out: dict = {q: {"antre": 0, "jalan": 0} for q in QUEUES}
    for q, s, n in rows:
        out.setdefault(q, {"antre": 0, "jalan": 0})[s] = n
    out["busy_workers"] = alive
    return out


def ping() -> bool:
    with db.engine().connect() as c:
        c.execute(text("SELECT 1"))
    return True
