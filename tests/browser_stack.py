"""Helper uji browser (P0.1/P1): server web & worker sebagai PROSES NYATA yang berbagi satu data dir & database
SQLite, seperti mode server. Dipakai tests/test_p01_browser_outage.py dan tests/test_p1_browser_editor.py."""
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
ADMIN = ("admin@uji-browser.local", "rahasia-browser-123")   # pragma: allowlist secret


def _port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Stack:
    """Web + worker sebagai proses terpisah, berbagi satu data dir & database SQLite (seperti mode server)."""

    def __init__(self, data_dir: Path):
        self.port = _port()
        self.base = f"http://127.0.0.1:{self.port}"
        self.logs = data_dir / "logs"
        self.logs.mkdir(parents=True, exist_ok=True)
        env = {k: v for k, v in os.environ.items() if k not in ("DATABASE_URL", "HTTP_PROXY", "HTTPS_PROXY",
                                                                  "http_proxy", "https_proxy")}
        env.update(VIDEO_APP_DATA=str(data_dir), EMBEDDED_WORKERS="0", REQUIRE_LIVE_WORKER="1",
                   WORKER_REPORT_INTERVAL="1", ADMIN_EMAIL=ADMIN[0], ADMIN_PASSWORD=ADMIN[1],
                   URL_IMPORT_TRUSTED_NETWORKS="127.0.0.0/8", ALLOW_URL_IMPORT="admins", DOWNLOAD_PROXY="",
                   CLEANUP_INTERVAL="1000000", BENCH_BURN_FRAMES_PER_SEC="2", AUTO_MIGRATE="1",
                   MEM_RESERVE_MB="0", NO_PROXY="127.0.0.1,localhost",
                   MEDIA_RETENTION_HOURS="2", MEDIA_MAX_HOURS="5", MEDIA_WARN_HOURS="0.5")
        self.env = env
        self.procs: dict[str, subprocess.Popen] = {}

    def start(self, name, args):
        out = open(self.logs / f"{name}.log", "ab")
        self.procs[name] = subprocess.Popen(args, cwd=REPO, env=self.env, stdout=out, stderr=subprocess.STDOUT,
                                            start_new_session=True)

    def start_web(self):
        self.start("web", [sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(self.port)])

    def start_io(self):
        self.start("io", [sys.executable, "-m", "app.worker", "--queues", "io"])

    def start_render(self):
        self.start("render", [sys.executable, "-m", "app.worker", "--queues", "render"])

    def start_analysis(self):
        self.start("analisis", [sys.executable, "bench/worker_shim.py", str(REPO), "--queues", "analisis"])

    def kill(self, name):
        """Crash keras: SIGKILL ke seluruh grup proses (worker + FFmpeg anaknya)."""
        proc = self.procs.pop(name)
        os.killpg(proc.pid, signal.SIGKILL)
        proc.wait(10)

    def stop_all(self):
        for name in list(self.procs):
            proc = self.procs.pop(name)
            try:
                os.killpg(proc.pid, signal.SIGTERM)
                proc.wait(15)
            except (ProcessLookupError, subprocess.TimeoutExpired):
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass

    def dump_logs(self) -> str:
        return "\n".join(f"--- {p.name}\n{p.read_text(errors='replace')[-3000:]}" for p in self.logs.glob("*.log"))


def _wait(pred, timeout, what, stack=None, step=0.5):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        try:
            last = pred()
            if last:
                return last
        except Exception as exc:   # server belum siap
            last = exc
        time.sleep(step)
    raise AssertionError(f"Tidak tercapai dalam {timeout}s: {what} (terakhir: {last!r})\n"
                         + (stack.dump_logs() if stack else ""))


def _alive_queues(c) -> set:
    workers = c.get("/api/admin/metrics").json()["workers"]
    return {q for w in workers if w.get("alive") for q in (w.get("queues") or "").split(",") if q}
