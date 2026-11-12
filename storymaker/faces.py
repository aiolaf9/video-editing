"""Posisi wajah per klip (YuNet, lokal) agar bingkai vertikal 9:16 berpusat pada narasumber."""
import logging
import subprocess
from pathlib import Path
from statistics import median
from typing import Optional

import numpy as np

from .config import FFMPEG, REPO_DIR

log = logging.getLogger("storymaker.faces")

MODEL_PATH = REPO_DIR / "models" / "face_detection_yunet_2023mar.onnx"
SAMPLE_FPS = 2
DETECT_WIDTH = 320


def track(video: Path, width: int, height: int) -> list[Optional[float]]:
    """Pusat-x wajah terbesar (0..1) per 1/SAMPLE_FPS detik, None bila tidak ada wajah."""
    if not MODEL_PATH.exists():
        return []
    import cv2

    scale = DETECT_WIDTH / max(width, height)
    w, h = max(2, int(width * scale) // 2 * 2), max(2, int(height * scale) // 2 * 2)
    try:
        detector = cv2.FaceDetectorYN.create(str(MODEL_PATH), "", (w, h), 0.6, 0.3, 20)
    except Exception as exc:  # OpenCV tanpa modul DNN
        log.warning("Detektor wajah tidak tersedia: %s", exc)
        return []
    detector.setInputSize((w, h))
    cmd = [FFMPEG, "-v", "error", "-i", str(video), "-an", "-vf", f"fps={SAMPLE_FPS},scale={w}:{h}",
           "-f", "rawvideo", "-pix_fmt", "bgr24", "-"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    assert proc.stdout is not None
    size = w * h * 3
    out: list[Optional[float]] = []
    while True:
        buf = proc.stdout.read(size)
        if len(buf) < size:
            break
        _, faces = detector.detect(np.frombuffer(buf, np.uint8).reshape(h, w, 3))
        if faces is None or len(faces) == 0:
            out.append(None)
        else:
            fx, _fy, fw, _fh = max(faces, key=lambda f: f[2] * f[3])[:4]
            out.append(round(float(fx + fw / 2) / w, 4))
    proc.wait()
    return out


def center_x(track_data: list[Optional[float]], start: float, end: float,
             default: Optional[float] = 0.5) -> Optional[float]:
    """Pusat bingkai statis untuk satu potongan: median posisi wajah di rentang itu."""
    a, b = int(start * SAMPLE_FPS), int(end * SAMPLE_FPS) + 1
    xs = [x for x in (track_data or [])[a:b] if x is not None]
    return round(median(xs), 4) if xs else default
