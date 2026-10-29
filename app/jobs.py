"""Antrian pekerjaan sederhana di dalam proses (cukup untuk aplikasi lokal satu pengguna)."""
import logging
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Optional

from .storage import new_id

log = logging.getLogger("video-editing.jobs")


class Job:
    def __init__(self, kind: str, project_id: str, label: str = ""):
        self.id = new_id()
        self.kind = kind
        self.project_id = project_id
        self.label = label
        self.status = "antre"        # antre | jalan | selesai | gagal
        self.progress = 0.0
        self.message = "Menunggu giliran..."
        self.result: Any = None
        self.error: Optional[str] = None
        self.created = time.time()

    def update(self, progress: Optional[float] = None, message: Optional[str] = None) -> None:
        if progress is not None:
            self.progress = round(max(0.0, min(100.0, progress)), 1)
        if message is not None:
            self.message = message

    def stage(self, lo: float, hi: float, message: str) -> Callable[[float], None]:
        """Callback progress 0..1 yang dipetakan ke rentang lo..hi persen."""
        self.update(lo, message)
        return lambda frac: self.update(lo + (hi - lo) * frac)

    def to_dict(self) -> dict:
        return {
            "id": self.id, "kind": self.kind, "project_id": self.project_id, "label": self.label,
            "status": self.status, "progress": self.progress, "message": self.message,
            "result": self.result, "error": self.error, "created": self.created,
        }


class JobManager:
    def __init__(self, workers: int = 1):
        self._pool = ThreadPoolExecutor(max_workers=max(1, workers), thread_name_prefix="job")
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

    def submit(self, kind: str, project_id: str, fn: Callable[[Job], Any], label: str = "") -> Job:
        job = Job(kind, project_id, label)
        with self._lock:
            self._jobs[job.id] = job
        self._pool.submit(self._run, job, fn)
        return job

    def _run(self, job: Job, fn: Callable[[Job], Any]) -> None:
        job.status = "jalan"
        try:
            job.result = fn(job)
            job.status = "selesai"
            job.update(100, job.message if job.message.endswith("!") else "Selesai!")
        except Exception as exc:
            log.error("Job %s gagal:\n%s", job.id, traceback.format_exc())
            job.status = "gagal"
            job.error = str(exc) or exc.__class__.__name__
            job.message = "Gagal"

    def get(self, job_id: str) -> Optional[Job]:
        return self._jobs.get(job_id)

    def for_project(self, project_id: str) -> list[Job]:
        return sorted((j for j in self._jobs.values() if j.project_id == project_id),
                      key=lambda j: j.created)
