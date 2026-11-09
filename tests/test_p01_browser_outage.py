"""P0.1: status impor URL di BROWSER saat worker analisis mati sungguhan (bukan simulasi fungsi tampilan).

Proses nyata: server web (uvicorn, tanpa worker bawaan), worker io (`python -m app.worker --queues io`, yt-dlp
asli), dan worker analisis (worker asli lewat bench/worker_shim.py; hanya Whisper diganti beban setara karena model
tidak diunduh di CI). Worker analisis dimatikan dengan SIGKILL (crash keras: tidak sempat menandai diri 'berhenti'),
lalu pengguna mengimpor link lewat UI di Chromium (Playwright):

1. impor diterima (worker io hidup) dan unduhan selesai;
2. layar menampilkan "Video dari link sudah berhasil diunduh. Analisis menunggu: server analisis video sedang tidak
   aktif ..." sebagai status error, tanpa persentase "0%";
3. worker analisis dihidupkan lagi -> analisis berjalan dan proyek menjadi "siap" tanpa tindakan pengguna.

Dijalankan khusus:  pytest -m browser tests/test_p01_browser_outage.py   (butuh `pip install playwright` + Chromium)
Tangkapan layar disimpan ke $BROWSER_ARTIFACTS bila diisi.
"""
import functools
import http.server
import os
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from conftest import _ffmpeg

pytestmark = pytest.mark.browser
playwright_api = pytest.importorskip("playwright.sync_api")

REPO = Path(__file__).resolve().parent.parent
ADMIN = ("admin@uji-browser.local", "rahasia-browser-123")   # pragma: allowlist secret
DOWNLOADED = "Video dari link sudah berhasil diunduh"
ANALYSIS_DOWN = "server analisis video sedang tidak aktif"


def _port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


@pytest.fixture(scope="module")
def media_url(tmp_path_factory):
    root = tmp_path_factory.mktemp("srv-browser")
    _ffmpeg("-f", "lavfi", "-i", "testsrc2=size=640x360:rate=25:duration=6", "-f", "lavfi", "-i",
            "sine=frequency=440:duration=6", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-shortest", str(root / "video.mp4"))
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(_Quiet, directory=str(root)))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}/video.mp4"
    srv.shutdown()
    srv.server_close()


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


@pytest.fixture
def stack(tmp_path):
    import httpx
    s = Stack(tmp_path / "data")
    s.start_web()
    try:
        c = httpx.Client(base_url=s.base, timeout=30)
        _wait(lambda: c.get("/api/health").status_code == 200, 60, "web hidup", s)
        assert c.post("/api/auth/login", json={"email": ADMIN[0], "password": ADMIN[1]}).status_code == 200
        s.api = c
        s.start_io()
        s.start_analysis()
        _wait(lambda: {"io", "analisis"} <= _alive_queues(c), 60, "worker io & analisis hidup", s)
        yield s
    finally:
        s.stop_all()


def _alive_queues(c) -> set:
    workers = c.get("/api/admin/metrics").json()["workers"]
    return {q for w in workers if w.get("alive") for q in (w.get("queues") or "").split(",") if q}


def test_url_import_while_analysis_worker_is_really_dead(stack, media_url, tmp_path):
    shots = Path(os.environ.get("BROWSER_ARTIFACTS") or tmp_path)
    shots.mkdir(parents=True, exist_ok=True)
    stack.kill("analisis")                                                   # crash keras, tanpa sinyal rapi
    # Heartbeat basi (alive_after = 60 dtk): antrean analisis tidak lagi punya worker hidup.
    _wait(lambda: "analisis" not in _alive_queues(stack.api), 120, "worker analisis dianggap mati", stack, 2)
    assert "io" in _alive_queues(stack.api)

    with playwright_api.sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 390, "height": 844})     # ukuran ponsel
        page.goto(stack.base + "/login.html")
        page.fill("#email", ADMIN[0])
        page.fill("#password", ADMIN[1])
        page.click("#loginBtn")
        page.wait_for_selector("#urlForm:not([hidden])", timeout=20000)
        page.fill("#urlInput", media_url)
        page.click("#urlForm button[type=submit]")

        status = page.locator("#analyzeStatus .status-text")
        try:
            page.wait_for_function(
                "([a, b]) => { const t = document.querySelector('#analyzeStatus .status-text');"
                " return t && t.textContent.includes(a) && t.textContent.includes(b); }",
                arg=[DOWNLOADED, ANALYSIS_DOWN], timeout=90000)
        except Exception:
            page.screenshot(path=str(shots / "p01-gagal.png"), full_page=True)
            raise AssertionError(f"Status di layar: {status.text_content()!r}\n{stack.dump_logs()}")
        text = status.text_content()
        assert "%" not in text                                                # bukan "Menunggu giliran 0%"
        assert "dilanjutkan otomatis" in text
        assert "error" in (page.locator("#analyzeStatus").get_attribute("class") or "")
        assert page.locator("#analyzeStatus").is_visible()
        page.screenshot(path=str(shots / "p01-analisis-offline.png"), full_page=True)
        # Daftar Proyek ikut akurat: bukan lagi "Mengunduh..." setelah unduhan selesai.
        page.wait_for_function("() => { const n = document.querySelector('#projectList li .name');"
                               " return n && !n.textContent.includes('Mengunduh'); }", timeout=15000)

        # Sisi server konsisten dengan layar: unduhan selesai, analisis tersimpan & menunggu.
        projects = stack.api.get("/api/projects").json()
        pid = projects[0]["id"] if isinstance(projects, list) else projects["projects"][0]["id"]
        proj = stack.api.get(f"/api/projects/{pid}").json()
        by_task = {j["label"]: j for j in proj["jobs"]}
        assert by_task["Unduh video"]["status"] == "selesai"
        assert by_task["Analisis video"]["status"] == "antre"
        assert by_task["Analisis video"]["wait"]["reason"] == "worker_tidak_aktif"
        assert proj["source"]

        # Worker analisis hidup lagi: dilanjutkan otomatis, pengguna tidak perlu mengulang apa pun.
        stack.start_analysis()
        try:
            page.wait_for_function(
                "() => { const b = document.querySelector('#analyzeStatus'); return b && b.hidden; }",
                timeout=120000)
        except Exception:
            page.screenshot(path=str(shots / "p01-gagal-pulih.png"), full_page=True)
            raise AssertionError(f"Status di layar: {status.text_content()!r}\n{stack.dump_logs()}")
        _wait(lambda: stack.api.get(f"/api/projects/{pid}").json()["status"] == "siap", 60, "proyek siap", stack)
        page.screenshot(path=str(shots / "p01-pulih.png"), full_page=True)
        browser.close()
