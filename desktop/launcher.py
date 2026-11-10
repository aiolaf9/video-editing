"""NH AI Clip Studio (desktop) — peluncur aplikasi Windows.

Satu proses: server lokal (FastAPI + worker AI/FFmpeg bawaan) di 127.0.0.1, lalu jendela aplikasi (pywebview /
Edge WebView2). Tanpa login, tanpa cloud, tanpa API berbayar. Data di %LOCALAPPDATA%\\NH AI Clip Studio, hasil
ekspor di Videos\\NH AI Clip Studio. Proyek tidak pernah dihapus otomatis.

    NH-AI-Clip-Studio.exe                       buka aplikasi
    NH-AI-Clip-Studio.exe --selftest VIDEO      uji jalur lengkap tanpa jendela (dipakai CI), laporan JSON
"""
from __future__ import annotations

import argparse
import json
import logging
import logging.handlers
import os
import secrets
import socket
import sys
import threading
import time
import urllib.request
from pathlib import Path

APP_NAME = "NH AI Clip Studio"
PREFERRED_PORT = 47811
DESKTOP_MODELS = ("base", "small")   # dibundel installer: base = Cepat, small = Akurat (lihat docs/M1_1_TRANSKRIP.md)          # port tetap (bila bebas) agar penyimpanan lokal jendela konsisten antar peluncuran
log = logging.getLogger("nh-desktop")


def resource_dir() -> Path:
    """Folder berkas bawaan: _internal (PyInstaller) atau akar repo saat dijalankan dari kode sumber."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent.parent


def default_data_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".local" / "share")
    return Path(base) / APP_NAME


def default_export_dir() -> Path:
    return Path.home() / "Videos" / APP_NAME


def _bundled(name: str) -> str | None:
    exe = name + (".exe" if os.name == "nt" else "")
    path = resource_dir() / "bin" / exe
    return str(path) if path.exists() else None


def configure_env(data_dir: Path, export_dir: Path) -> str:
    """Atur lingkungan SEBELUM app diimpor (config dibaca saat impor). Mengembalikan kunci peluncuran."""
    key = secrets.token_urlsafe(32)
    res = resource_dir()
    forced = {
        "DESKTOP_MODE": "1",
        "DESKTOP_LAUNCH_KEY": key,
        "VIDEO_APP_DATA": str(data_dir),
        "EXPORT_DIR": str(export_dir),
        "EMBEDDED_WORKERS": "1",          # worker AI & FFmpeg berjalan otomatis di proses ini
        "MEDIA_LIFECYCLE": "0",           # proyek lokal tidak dihapus otomatis (bukan aturan 2/5 jam versi web)
        "AUTO_MIGRATE": "1",
        "COOKIE_SECURE": "0",
        "ALLOW_URL_IMPORT": "none",       # M1: hanya berkas lokal, tanpa internet
        "HF_HUB_OFFLINE": "1",            # tidak pernah mengunduh model saat berjalan
        "TRANSFORMERS_OFFLINE": "1",
        "HF_HUB_DISABLE_TELEMETRY": "1",
    }
    # M1.1: model per mode kualitas, dari folder bundel installer (whisper-<nama>). Akurat = bawaan.
    models = {name: str(res / f"whisper-{name}") for name in DESKTOP_MODELS
              if (res / f"whisper-{name}" / "model.bin").exists()}
    fast = next((m for m in ("base",) if m in models), None)
    accurate = next((m for m in ("small", "base") if m in models), None)
    if models:
        forced["WHISPER_MODEL_PATHS"] = json.dumps(models)
        forced["WHISPER_MODEL"] = accurate or fast
        forced["WHISPER_MODEL_FAST"] = fast or accurate
        forced["WHISPER_MODEL_ACCURATE"] = accurate or fast
        forced["WHISPER_QUALITY_DEFAULT"] = "akurat"
    for name, env in (("ffmpeg", "FFMPEG_BIN"), ("ffprobe", "FFPROBE_BIN")):
        found = _bundled(name)
        if found:
            forced[env] = found
    os.environ.update(forced)
    os.environ.setdefault("MAX_UPLOAD_MB", "20480")
    for name in ("DATABASE_URL", "ADMIN_EMAIL", "ADMIN_PASSWORD", "AI_PROVIDER", "WHISPER_MODEL_DIR"):
        os.environ.pop(name, None)       # konfigurasi server/web tidak berlaku di desktop
    return key


def setup_logging(data_dir: Path) -> Path:
    # Aplikasi berjendela (tanpa konsol): stdout/stderr None. Arahkan ke devnull agar log library tidak gagal.
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")   # noqa: SIM115
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")   # noqa: SIM115
    logs = data_dir / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    path = logs / "desktop.log"
    handler = logging.handlers.RotatingFileHandler(path, maxBytes=5_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(logging.INFO)
    return path


class SingleInstance:
    """Kunci berkas: satu salinan aplikasi per pengguna (dua salinan berbagi database & folder proyek)."""

    def __init__(self, path: Path):
        self.path = path
        self.fh = None

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.fh = open(self.path, "a+")   # noqa: SIM115 - dibuka selama aplikasi berjalan
        try:
            if os.name == "nt":
                import msvcrt
                self.fh.seek(0)
                msvcrt.locking(self.fh.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            self.fh.close()
            self.fh = None
            return False


def free_port() -> int:
    for port in (PREFERRED_PORT, 0):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return s.getsockname()[1]
            except OSError:
                continue
    raise RuntimeError("Tidak ada port lokal yang bebas.")


class LocalServer:
    def __init__(self, port: int):
        import uvicorn

        from app import main
        self.port = port
        config = uvicorn.Config(main.app, host="127.0.0.1", port=port, log_config=None, access_log=False,
                                lifespan="on", timeout_graceful_shutdown=20)
        self.server = uvicorn.Server(config)
        self.thread = threading.Thread(target=self.server.run, name="server", daemon=True)

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self, timeout: float = 120) -> None:
        self.thread.start()
        deadline = time.time() + timeout
        while time.time() < deadline:
            if not self.thread.is_alive():
                raise RuntimeError("Server lokal berhenti saat dimulai. Lihat log.")
            try:
                with urllib.request.urlopen(self.base + "/api/health", timeout=3) as r:  # nosec B310 - localhost
                    if r.status == 200:
                        return
            except OSError:
                time.sleep(0.3)
        raise RuntimeError("Server lokal tidak siap dalam batas waktu.")

    def stop(self) -> None:
        self.server.should_exit = True     # lifespan: FFmpeg dihentikan, job dikembalikan ke antrean
        self.thread.join(30)


def message_box(text: str) -> None:
    log.info("Pesan: %s", text)
    if os.name == "nt" and os.environ.get("NH_SILENT") != "1":   # NH_SILENT=1: uji otomatis tanpa dialog
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, text, APP_NAME, 0x40)
            return
        except Exception:   # pragma: no cover
            pass
    print(text)


def open_window(url: str, data_dir: Path) -> bool:
    """Jendela aplikasi (WebView2). False bila tidak tersedia -> dipakai browser bawaan."""
    try:
        import webview
    except Exception:
        log.exception("pywebview tidak tersedia")
        return False
    try:
        webview.settings["ALLOW_DOWNLOADS"] = True
        webview.create_window(APP_NAME, url, width=1440, height=920, min_size=(1024, 680))
        log.info("Membuka jendela aplikasi (pywebview %s)", getattr(webview, "__version__", "?"))
        webview.start(gui="edgechromium" if os.name == "nt" else None, private_mode=False,
                      storage_path=str(data_dir / "webview"))
        return True
    except Exception:
        log.exception("Jendela WebView2 gagal dibuka")
        return False


def browser_fallback(url: str) -> None:
    """Tanpa WebView2: buka di browser bawaan dan tampilkan jendela kecil untuk menutup aplikasi."""
    import webbrowser
    log.info("Jendela WebView2 tidak tersedia: memakai browser bawaan")
    webbrowser.open(url)
    try:
        import tkinter as tk
        root = tk.Tk()
        root.title(APP_NAME)
        root.geometry("420x140")
        tk.Label(root, text=f"{APP_NAME} berjalan di browser Anda.\nTutup jendela ini untuk keluar dari aplikasi.",
                 justify="center", padx=16, pady=16).pack()
        tk.Button(root, text="Buka lagi di browser", command=lambda: webbrowser.open(url)).pack()
        root.mainloop()
    except Exception:
        log.exception("tkinter tidak tersedia; aplikasi tetap berjalan sampai proses dihentikan")
        threading.Event().wait()


def run_app(args) -> int:
    data_dir = Path(args.data_dir) if args.data_dir else default_data_dir()
    export_dir = Path(args.export_dir) if args.export_dir else default_export_dir()
    data_dir.mkdir(parents=True, exist_ok=True)
    log_path = setup_logging(data_dir)
    lock = SingleInstance(data_dir / "app.lock")
    if not lock.acquire():
        message_box(f"{APP_NAME} sudah terbuka. Gunakan jendela yang sudah ada.")
        return 0
    key = configure_env(data_dir, export_dir)
    log.info("Mulai %s; data=%s ekspor=%s", APP_NAME, data_dir, export_dir)
    try:
        server = LocalServer(free_port())
        server.start()
    except Exception as exc:
        log.exception("Server gagal")
        message_box(f"{APP_NAME} gagal dimulai: {exc}\nLog: {log_path}")
        return 1
    url = f"{server.base}/api/desktop/enter?key={key}"
    try:
        if not open_window(url, data_dir):
            browser_fallback(url)
    finally:
        server.stop()
        log.info("Aplikasi ditutup.")
    return 0


def run_selftest(args) -> int:
    """Tanpa jendela: server + worker sungguhan, lalu desktop.selftest (upload -> AI -> subtitle -> ekspor MP4)."""
    data_dir = Path(args.data_dir) if args.data_dir else default_data_dir()
    export_dir = Path(args.export_dir) if args.export_dir else default_export_dir()
    data_dir.mkdir(parents=True, exist_ok=True)
    setup_logging(data_dir)
    key = configure_env(data_dir, export_dir)
    report: dict = {"ok": False}
    server = None
    try:
        server = LocalServer(free_port())
        server.start()
        from desktop import selftest
        ref = Path(args.reference).read_text(encoding="utf-8") if args.reference else ""
        report = selftest.run(server.base, key, Path(args.selftest), export_dir, language=args.language,
                              quality=args.quality, reference=ref)
    except Exception as exc:
        log.exception("Selftest gagal")
        report = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    finally:
        if server:
            server.stop()
    text = json.dumps(report, indent=2, ensure_ascii=False)
    if args.report:
        Path(args.report).write_text(text, encoding="utf-8")
    print(text)
    return 0 if report.get("ok") else 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="NH-AI-Clip-Studio")
    parser.add_argument("--selftest", metavar="VIDEO", help="uji jalur lengkap tanpa jendela")
    parser.add_argument("--report", help="tulis laporan selftest (JSON) ke berkas ini")
    parser.add_argument("--language", default="en", help="bahasa ucapan video selftest")
    parser.add_argument("--quality", default="", help="mode kualitas selftest: cepat / akurat")
    parser.add_argument("--reference", help="transkrip rujukan (teks) untuk WER/CER selftest")
    parser.add_argument("--data-dir", help="folder data (bawaan: %%LOCALAPPDATA%%\\NH AI Clip Studio)")
    parser.add_argument("--export-dir", help="folder ekspor (bawaan: Videos\\NH AI Clip Studio)")
    args = parser.parse_args(argv)
    if args.selftest:
        return run_selftest(args)
    return run_app(args)


if __name__ == "__main__":
    sys.exit(main())
