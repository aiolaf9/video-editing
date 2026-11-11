"""Galeri Motion Library (M2.5): klip gerak + thumbnail tiap preset, dirender dengan jalur render aplikasi yang SAMA
(pipeline.assemble -> render.build_command: libass + ekspresi FFmpeg), bukan tiruan CSS. Adegan contoh digambar
sendiri dengan PIL (tanpa aset berhak cipta). Cache dibuat ulang bila definisi preset / versi engine berubah."""
from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path

from . import media, motionlib
from .config import DATA_DIR

W, H = 360, 640
DURATION = 4.0
CACHE = DATA_DIR / "cache" / "motion-gallery"
# Galeri yang sudah dirender saat build installer (scripts/build_motion_gallery.py, FFmpeg bundel): dipakai bila
# kuncinya sama sehingga galeri langsung tampil tanpa render pertama kali di komputer pengguna.
BUNDLED = Path(__file__).resolve().parent / "data" / "motion-gallery"
_lock = threading.Lock()
_queue: "list[str]" = []
_queue_cv = threading.Condition()
_worker: "threading.Thread | None" = None
_failed: dict = {}


def _scene(path: Path) -> None:
    """Adegan netral 9:16: latar gradasi, 'papan' samar, siluet pembicara sederhana (bentuk geometris)."""
    from PIL import Image, ImageDraw, ImageFilter
    im = Image.new("RGB", (W * 2, H * 2))
    d = ImageDraw.Draw(im)
    for y in range(H * 2):
        u = y / (H * 2)
        d.line([(0, y), (W * 2, y)], fill=(int(46 + 70 * u), int(58 + 40 * u), int(85 - 20 * u)))
    d.rounded_rectangle([60, 100, W * 2 - 60, 470], 18, fill=(214, 220, 226), outline=(120, 130, 140), width=6)
    for i in range(4):
        d.rounded_rectangle([110, 160 + i * 70, 110 + 420 - i * 60, 188 + i * 70], 10, fill=(150, 160, 172))
    cx = W
    d.ellipse([cx - 105, 560, cx + 105, 770], fill=(205, 160, 120))                     # kepala
    d.rounded_rectangle([cx - 230, 780, cx + 230, H * 2 + 40], 120, fill=(40, 96, 120))   # badan
    im = im.filter(ImageFilter.GaussianBlur(1.2)).resize((W, H), Image.LANCZOS)
    png = path.with_suffix(".png")
    im.save(png)
    media.run_ffmpeg(["-loop", "1", "-framerate", "30", "-i", png.name, "-f", "lavfi", "-i",
                      "anullsrc=r=48000:cl=mono", "-t", f"{DURATION + 0.3:.1f}", "-c:v", "libx264", "-preset",
                      "veryfast", "-crf", "18", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", path.name],
                     DURATION, None, cwd=path.parent, stall_timeout=60)
    png.unlink(missing_ok=True)


def item_for(p: dict) -> dict:
    if p["category"] == "transitions":
        d = float(p["defaults"].get("duration", 0.4))
        s, e = 2.0 - d / 2, 2.0 + d / 2
    elif p["category"] == "captions":
        s, e = 0.0, DURATION
    else:
        s, e = 0.2, min(DURATION - 0.1, 0.2 + max(1.5, float(p["defaults"].get("duration", 3.6))))
    return {"id": "g", "type": "fx", "preset": p["id"], "params": {}, "start": s, "end": e}


def _key(p: dict) -> str:
    raw = json.dumps(p, sort_keys=True) + motionlib.ENGINE_VERSION
    return hashlib.sha256(raw.encode()).hexdigest()[:12]


def cached(pid: str) -> "tuple[Path, Path] | None":
    """(mp4, png) bila sudah tersedia (bundel installer atau cache data), tanpa merender."""
    p = motionlib.get(pid)
    if not p:
        return None
    key, stem = _key(p), pid.replace(".", "_")
    for folder in (BUNDLED, CACHE):
        mp4, png = folder / f"{stem}-{key}.mp4", folder / f"{stem}-{key}.png"
        if mp4.is_file() and png.is_file():
            return mp4, png
    return None


def status(ids: "list[str] | None" = None) -> dict:
    """{id: "siap" | "antre" | "gagal"} untuk galeri (UI memuat gambar hanya bila siap)."""
    out = {}
    for p in motionlib.presets():
        if ids and p["id"] not in ids:
            continue
        out[p["id"]] = "siap" if cached(p["id"]) else ("gagal" if p["id"] in _failed else "antre")
    return out


def warm(ids: "list[str]") -> None:
    """Antrekan render galeri di SATU thread latar belakang. Permintaan HTTP tidak menunggu render, sehingga koneksi
    browser (maks ±6 per host) tetap bebas untuk menyimpan resep/pratinjau saat galeri pertama kali dibuka."""
    global _worker
    todo = [i for i in ids if motionlib.get(i) and not cached(i) and i not in _failed]
    if not todo:
        return
    with _queue_cv:
        for i in todo:
            if i not in _queue:
                _queue.append(i)
        if _worker is None or not _worker.is_alive():
            _worker = threading.Thread(target=_drain, name="motion-gallery", daemon=True)
            _worker.start()
        _queue_cv.notify()


def _drain() -> None:
    import logging
    while True:
        with _queue_cv:
            if not _queue:
                return
            pid = _queue.pop(0)
        try:
            render(pid)
        except Exception as e:                      # satu preset gagal tidak menghentikan antrean
            _failed[pid] = str(e)[:200]
            logging.getLogger(__name__).warning("Galeri Motion Library %s gagal: %s", pid, e)


def render(pid: str) -> tuple[Path, Path]:
    """(mp4, png) demo preset; dibuat sekali lalu diambil dari cache (atau bundel installer)."""
    p = motionlib.get(pid)
    if not p:
        raise KeyError(pid)
    hit = cached(pid)
    if hit:
        return hit
    CACHE.mkdir(parents=True, exist_ok=True)
    key = _key(p)
    stem = pid.replace(".", "_")
    mp4, png = CACHE / f"{stem}-{key}.mp4", CACHE / f"{stem}-{key}.png"
    with _lock:
        if mp4.is_file() and png.is_file():
            return mp4, png
        for old in CACHE.glob(f"{stem}-*"):
            old.unlink(missing_ok=True)
        scene = CACHE / f"scene-{motionlib.ENGINE_VERSION}.mp4"
        if not scene.is_file():
            _scene(scene)
        from . import gallery, pipeline
        from .render import build_command
        from .schemas import RenderRequest
        req = RenderRequest(start=0, end=DURATION, aspect="9:16", animations=True, fade=False, normalize_audio=False)
        opts = gallery.options_for("karaoke")
        opts.emphasis = False
        info = {"width": W, "height": H, "duration": DURATION + 0.3, "has_audio": True}
        tag = f"{stem}-{key}"
        parts = pipeline.assemble(req, gallery.sample_transcript(), opts, [item_for(p)], None, (W, H), W, H, CACHE, tag)
        tmp = CACHE / f"{tag}.part.mp4"
        try:
            cmd = build_command(scene, tmp, req, info, parts["subs"], None, CACHE, None, parts["punches"],
                                parts["layers"], None, graphics_file=parts["graphics"], camera_fx=parts["camera_fx"])
            media.run_ffmpeg(cmd, DURATION, None, cwd=CACHE, stall_timeout=60)
            tmp.replace(mp4)
        finally:
            tmp.unlink(missing_ok=True)
            for f in (parts["subs"], parts["graphics"]):
                if f is not None:
                    f.unlink(missing_ok=True)
            import shutil
            shutil.rmtree(parts["overlay_dir"], ignore_errors=True)
        it = item_for(p)
        t = (it["start"] + it["end"]) / 2 if p["category"] == "transitions" else min(it["end"] - 0.3, it["start"] + 1.6)
        media.run_ffmpeg(["-ss", f"{t:.2f}", "-i", mp4.name, "-frames:v", "1", png.name], 0, None, cwd=CACHE,
                         stall_timeout=30)
    return mp4, png
