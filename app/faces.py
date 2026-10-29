"""Pelacakan wajah untuk auto-reframe: bingkai vertikal mengikuti posisi pembicara.

Memakai detektor YuNet (OpenCV, model kecil ~230 KB di folder models/) pada preview 480p,
dua sampel per detik, lalu posisi wajah dihaluskan agar kamera tidak bergetar.
"""
import logging
import subprocess
from pathlib import Path
from statistics import median
from typing import Callable, Optional

import numpy as np

from . import jobcontrol
from .config import BASE_DIR, FFMPEG

log = logging.getLogger("video-editing.faces")

MODEL_PATH = BASE_DIR / "models" / "face_detection_yunet_2023mar.onnx"
SAMPLE_FPS = 2
DETECT_WIDTH = 320


def _frame_size(width: int, height: int) -> tuple[int, int]:
    scale = DETECT_WIDTH / max(width, height)
    return max(2, int(width * scale) // 2 * 2), max(2, int(height * scale) // 2 * 2)


def track(video: Path, width: int, height: int, duration: float,
          progress: Optional[Callable[[float], None]] = None) -> list[Optional[dict]]:
    """Posisi wajah utama per 1/SAMPLE_FPS detik: {"x","y","s"} ternormalisasi 0..1, atau None."""
    import cv2

    w, h = _frame_size(width, height)
    detector = cv2.FaceDetectorYN.create(str(MODEL_PATH), "", (w, h), 0.6, 0.3, 20)
    detector.setInputSize((w, h))
    cmd = [FFMPEG, "-v", "error", "-i", str(video), "-an", "-vf", f"fps={SAMPLE_FPS},scale={w}:{h}",
           "-f", "rawvideo", "-pix_fmt", "bgr24", "-"]
    proc = jobcontrol.popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    assert proc.stdout is not None
    try:
        return _read_faces(proc, detector, w, h, duration, progress)
    finally:
        if proc.poll() is None:  # berhenti lebih awal (batal/error): matikan FFmpeg
            jobcontrol.terminate(proc)
        proc.wait()
        jobcontrol.release(proc)


def _read_faces(proc, detector, w: int, h: int, duration: float,
                progress: Optional[Callable[[float], None]]) -> list[Optional[dict]]:
    frame_bytes = w * h * 3
    total = max(1, int(duration * SAMPLE_FPS))
    out: list[Optional[dict]] = []
    while True:
        jobcontrol.check()
        buf = proc.stdout.read(frame_bytes)
        if len(buf) < frame_bytes:
            break
        frame = np.frombuffer(buf, np.uint8).reshape(h, w, 3)
        _, faces = detector.detect(frame)
        if faces is None or len(faces) == 0:
            out.append(None)
        else:
            # Wajah terbesar dianggap pembicara utama.
            fx, fy, fw, fh = max(faces, key=lambda f: f[2] * f[3])[:4]
            out.append({"x": round(float(fx + fw / 2) / w, 4), "y": round(float(fy + fh / 2) / h, 4),
                        "s": round(float(fh) / h, 4)})
        if progress and len(out) % 20 == 0:
            progress(min(1.0, len(out) / total))
    jobcontrol.check()
    return out


def _smoothed_positions(track_data: list[Optional[dict]], start: float, end: float) -> list[tuple[float, float]]:
    """(waktu relatif klip, x) dengan celah diisi dan dihaluskan median."""
    a = max(0, int(start * SAMPLE_FPS))
    b = min(len(track_data), int(end * SAMPLE_FPS) + 1)
    raw = [(i / SAMPLE_FPS - start, track_data[i]["x"] if track_data[i] else None) for i in range(a, b)]
    known = [x for _, x in raw if x is not None]
    if not known:
        return []
    last = known[0]
    filled = []
    for t, x in raw:
        last = x if x is not None else last
        filled.append((max(0.0, t), last))
    win = 2  # median 5 sampel = 2.5 detik
    xs = [x for _, x in filled]
    return [(t, median(xs[max(0, i - win): i + win + 1])) for i, (t, _) in enumerate(filled)]


def camera_path(track_data: list[Optional[dict]], start: float, end: float,
                dead_zone: float = 0.07, move_time: float = 0.6) -> list[tuple[float, float]]:
    """Titik kunci (t, x) gaya juru kamera: diam, lalu bergeser halus bila wajah pindah jauh.

    Mengembalikan [] bila tidak ada wajah di rentang klip.
    """
    pos = _smoothed_positions(track_data, start, end)
    if not pos:
        return []
    cur = pos[0][1]
    keys = [(0.0, cur)]
    for t, x in pos[1:]:
        if abs(x - cur) > dead_zone and t >= keys[-1][0] + move_time:
            keys.append((t, cur))                     # tahan posisi lama sampai sini
            keys.append((t + move_time, x))           # lalu geser
            cur = x
    return keys


def crop_x_expression(keys: list[tuple[float, float]], src_w: int, crop_w: int) -> str:
    """Ekspresi FFmpeg x(t) untuk filter crop dari titik kunci (t, pusat x ternormalisasi)."""
    def left(cx: float) -> int:
        return int(min(max(cx * src_w - crop_w / 2, 0), src_w - crop_w))

    pts = [(t, left(x)) for t, x in keys]
    expr = str(pts[-1][1])
    for (t0, x0), (t1, x1) in reversed(list(zip(pts, pts[1:]))):
        seg = str(x0) if x0 == x1 else f"{x0}+({x1 - x0})*(t-{t0:.3f})/{max(t1 - t0, 1e-3):.3f}"
        expr = f"if(lt(t,{t1:.3f}),{seg},{expr})"
    return expr
