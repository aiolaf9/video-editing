"""Menu "Uji akurasi" aplikasi desktop (M1.1): menjalankan desktop/nh_eval.py di latar belakang dari antarmuka,
tanpa CMD. Satu uji pada satu waktu; video tidak dikirim ke mana pun (semua di komputer ini)."""
from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path
from typing import Optional

log = logging.getLogger("desktop_eval")
_lock = threading.Lock()
_stop = threading.Event()
_state: dict = {"running": False}


def status() -> dict:
    with _lock:
        return dict(_state)


def _set(**kw) -> None:
    with _lock:
        _state.update(kw)


def scan(folder: str) -> dict:
    from desktop import nh_eval
    return nh_eval.scan(Path(folder).expanduser())


def start(folder: str, runs: int, qualities: list[str], data_dir: Optional[Path] = None) -> dict:
    from desktop import nh_eval
    info = nh_eval.scan(Path(folder).expanduser())     # ValueError bila folder tidak ada
    if not [n for n in info["pairs"] if n not in info["empty_reference"]]:
        raise ValueError("Folder belum berisi pasangan video + transkrip rujukan .txt (nama sama).")
    with _lock:
        if _state.get("running"):
            raise RuntimeError("Uji akurasi sedang berjalan.")
        _state.clear()
        _state.update(running=True, folder=info["folder"], started=time.time(), done=0, total=None, current=None,
                      summary=None, error=None, cancelled=False, report=None)
    _stop.clear()

    def progress(p: dict) -> None:
        _set(done=p["done"], total=p["total"],
             current=f"{p['file']} — mode {p['quality']}, run {p['run']}" if p.get("file") else None)

    def work() -> None:
        try:
            rep = nh_eval.run(Path(info["folder"]), runs, qualities, progress=progress, stop=_stop.is_set,
                              data_dir=data_dir)
            _set(summary=rep["summary"], cancelled=rep.get("cancelled", False),
                 errors=sum("error" in r for r in rep["rows"]), skipped=rep.get("skipped_files", []),
                 system=rep["system"], report=str(Path(info["folder"]) / "laporan-uji.md"))
        except Exception as exc:  # laporkan ke antarmuka, jangan matikan server
            log.exception("Uji akurasi gagal")
            _set(error=f"{type(exc).__name__}: {exc}")
        finally:
            _set(running=False, finished=time.time(), current=None)

    threading.Thread(target=work, name="uji-akurasi", daemon=True).start()
    return status()


def cancel() -> dict:
    _stop.set()
    return status()


def open_folder() -> dict:
    folder = status().get("folder")
    if not folder:
        raise ValueError("Belum ada uji akurasi.")
    if os.name == "nt":
        os.startfile(folder)  # nosec B606 - folder yang dipilih pengguna sendiri untuk uji
    return {"path": folder}


def pick_folder() -> Optional[str]:
    """Dialog pilih folder Windows lewat jendela pywebview. None bila dibatalkan; RuntimeError bila tidak ada jendela
    (mode browser cadangan) -> antarmuka memakai kolom teks."""
    try:
        import webview
    except ImportError as exc:
        raise RuntimeError("dialog folder tidak tersedia") from exc
    if not getattr(webview, "windows", None):
        raise RuntimeError("dialog folder tidak tersedia")
    kind = getattr(getattr(webview, "FileDialog", None), "FOLDER", None) or getattr(webview, "FOLDER_DIALOG")
    res = webview.windows[0].create_file_dialog(kind)
    return (res[0] if isinstance(res, (list, tuple)) else res) if res else None
