"""Kendali job yang sedang berjalan: batas waktu, pembatalan, dan penghentian subprocess.

Setiap job di worker mendapat satu JobControl (disimpan per thread). Kode pipeline tidak perlu
tahu detailnya: cukup memanggil check() (otomatis lewat Job.update) dan menjalankan subprocess
lewat popen()/run_capture() di modul ini, sehingga proses FFmpeg/FFprobe bisa dihentikan saat
job dibatalkan atau melewati batas waktu.
"""
import os
import signal
import subprocess
import threading
import time
import weakref
from contextlib import contextmanager
from typing import Iterator, Optional

_local = threading.local()
_active: "weakref.WeakSet[JobControl]" = weakref.WeakSet()   # job yang sedang berjalan di proses ini

TERMINATE_GRACE = 5.0   # detik menunggu proses berhenti setelah SIGTERM sebelum SIGKILL


class JobCancelled(BaseException):
    """Job dihentikan (dibatalkan pengguna atau melewati batas waktu).

    Turunan BaseException (bukan Exception) supaya tidak tertelan blok `except Exception`
    di dalam pipeline atau library pihak ketiga, dan pasti sampai ke worker.
    """

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason            # "dibatalkan" | "timeout" | "shutdown"

    @property
    def message(self) -> str:
        if self.reason == "timeout":
            return "Pekerjaan dihentikan karena melewati batas waktu."
        if self.reason == "shutdown":
            return "Dihentikan karena server dimatikan; akan diulang otomatis."
        return "Dibatalkan oleh pengguna."


class SubprocessStalled(RuntimeError):
    """FFmpeg tidak melaporkan kemajuan dalam waktu yang ditentukan."""


class JobControl:
    def __init__(self, job_id: str, timeout: float):
        self.job_id = job_id
        self.started = time.time()
        self.deadline = self.started + timeout
        self.reason: Optional[str] = None
        self.cancelled_at: Optional[float] = None
        self._event = threading.Event()
        self._procs: set[subprocess.Popen] = set()
        # RLock: cancel() bisa dipanggil dari signal handler di thread yang sedang memegang lock ini.
        self._lock = threading.RLock()

    # ---- status
    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def extend_deadline(self, timeout_from_start: float) -> None:
        """Perbarui batas waktu setelah ukuran pekerjaan diketahui (mis. durasi video)."""
        self.deadline = self.started + timeout_from_start

    def remaining(self) -> float:
        return self.deadline - time.time()

    def check(self) -> None:
        """Hentikan alur kerja (raise JobCancelled) bila job dibatalkan atau waktunya habis."""
        if not self.cancelled and time.time() > self.deadline:
            self.cancel("timeout")
        if self.cancelled:
            raise JobCancelled(self.reason or "dibatalkan")

    def cancel(self, reason: str) -> None:
        """Tandai dibatalkan & hentikan semua subprocess milik job ini. Aman dipanggil dari thread lain."""
        with self._lock:
            if not self._event.is_set():
                self.reason = reason
                self.cancelled_at = time.time()
                self._event.set()
            procs = list(self._procs)
        for proc in procs:
            terminate(proc)

    # ---- subprocess
    def register(self, proc: subprocess.Popen) -> None:
        with self._lock:
            self._procs.add(proc)
            already = self._event.is_set()
        if already:  # dibatalkan tepat saat proses dimulai
            terminate(proc)

    def unregister(self, proc: subprocess.Popen) -> None:
        with self._lock:
            self._procs.discard(proc)


def current() -> Optional[JobControl]:
    return getattr(_local, "control", None)


@contextmanager
def activate(control: JobControl) -> Iterator[JobControl]:
    previous = current()
    _local.control = control
    _active.add(control)
    try:
        yield control
    finally:
        _active.discard(control)
        _local.control = previous


def cancel_all(reason: str = "shutdown") -> int:
    """Hentikan semua job di proses ini (dipanggil saat aplikasi/worker dimatikan).

    Dengan reason "shutdown" job tidak dianggap batal: worker mengembalikannya ke antrean.

    Subprocess berjalan di grup prosesnya sendiri, jadi Ctrl+C tidak otomatis sampai ke FFmpeg;
    tanpa ini FFmpeg bisa tertinggal berjalan setelah aplikasi ditutup.
    """
    controls = list(_active)
    for control in controls:
        control.cancel(reason)
    return len(controls)


def check() -> None:
    control = current()
    if control is not None:
        control.check()


def terminate(proc: subprocess.Popen) -> None:
    """Hentikan proses beserta anak-anaknya: SIGTERM, tunggu sebentar, lalu SIGKILL."""
    if proc.poll() is not None:
        return
    try:
        if os.name == "posix":
            os.killpg(proc.pid, signal.SIGTERM)
        else:
            proc.terminate()
        try:
            proc.wait(TERMINATE_GRACE)
        except subprocess.TimeoutExpired:
            if os.name == "posix":
                os.killpg(proc.pid, signal.SIGKILL)
            else:
                proc.kill()
            proc.wait(TERMINATE_GRACE)
    except (ProcessLookupError, PermissionError, OSError):
        pass


def popen(cmd: list[str], **kwargs) -> subprocess.Popen:
    """subprocess.Popen yang terdaftar pada job aktif (bisa dihentikan saat batal/timeout).

    Pemanggil wajib memanggil release(proc) setelah selesai (lihat run_capture untuk contoh).
    """
    if os.name == "posix":
        kwargs.setdefault("start_new_session", True)  # grup proses sendiri: aman di-kill bersama anaknya
    proc = subprocess.Popen(cmd, **kwargs)
    control = current()
    if control is not None:
        control.register(proc)
    return proc


def release(proc: subprocess.Popen) -> None:
    control = current()
    if control is not None:
        control.unregister(proc)


def run_capture(cmd: list[str], text: bool = False, timeout: Optional[float] = None) -> subprocess.CompletedProcess:
    """Pengganti subprocess.run(capture_output=True) yang bisa dibatalkan."""
    check()
    proc = popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=text)
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        terminate(proc)
        proc.communicate()
        raise SubprocessStalled(f"Perintah melewati batas waktu {timeout:.0f} detik: {os.path.basename(cmd[0])}")
    finally:
        release(proc)
    check()  # bila dibatalkan saat berjalan, jangan teruskan hasil yang terpotong
    return subprocess.CompletedProcess(cmd, proc.returncode, out, err)
