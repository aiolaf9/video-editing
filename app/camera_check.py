"""M2.5.1 pemeriksa gerakan kamera dari MP4 (dipakai tes & selftest aplikasi terpasang, tanpa jaringan).

Membandingkan frame MP4 ber-efek dengan MP4 TANPA efek pada detik yang sama (sumber, bingkai & subtitle sama),
sehingga yang terukur hanya transformasi kamera:
  * geser   : korelasi fase (numpy FFT) -> (dx, dy) sebagai pecahan lebar/tinggi frame;
  * zoom    : skala terbaik (1,0..1,6) yang memetakan frame dasar ke frame ber-efek di sekitar titik jangkar.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np

from .config import FFMPEG

GW = 180          # lebar frame analisis (tinggi mengikuti rasio)


def gray(mp4: Path, t: float, width: int = GW) -> np.ndarray:
    out = subprocess.run([FFMPEG, "-v", "error", "-ss", f"{t:.3f}", "-i", str(mp4), "-frames:v", "1", "-vf",
                          f"scale={width}:-2,format=gray", "-f", "rawvideo", "-"], capture_output=True, check=True,
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
    h = len(out) // width
    return np.frombuffer(out[: h * width], np.uint8).reshape(h, width).astype(np.float64)


def shift(base: np.ndarray, moved: np.ndarray) -> tuple[float, float]:
    """Pergeseran isi `moved` terhadap `base` (pecahan lebar, tinggi). Positif = isi bergerak ke kanan/bawah."""
    a = base - base.mean()
    b = moved - moved.mean()
    win = np.outer(np.hanning(a.shape[0]), np.hanning(a.shape[1]))
    fa, fb = np.fft.fft2(a * win), np.fft.fft2(b * win)
    r = fb * np.conj(fa)
    r /= np.abs(r) + 1e-9
    c = np.abs(np.fft.ifft2(r))
    y, x = np.unravel_index(int(c.argmax()), c.shape)
    h, w = c.shape
    dy = y - h if y > h // 2 else y
    dx = x - w if x > w // 2 else x
    return float(dx / w), float(dy / h)


def zoom(base: np.ndarray, moved: np.ndarray, anchor=(0.5, 0.4)) -> float:
    """Skala zoom terbaik: potong `base` selebar 1/s di sekitar jangkar, perbesar, bandingkan dengan `moved`."""
    from PIL import Image
    img = Image.fromarray(base.astype(np.uint8))
    best, best_err = 1.0, None
    for s in np.linspace(1.0, 1.6, 31):
        crop = _zoomed(img, s, anchor)
        err = float(np.mean((np.asarray(crop, np.float64) - moved) ** 2))
        if best_err is None or err < best_err:
            best, best_err = float(s), err
    return best


def _zoomed(img, s: float, anchor=(0.5, 0.4)):
    """Potongan selebar 1/s di titik jangkar (sama dengan render: x=(W-W/s)/2, y=(H-H/s)*0,4), diperbesar."""
    from PIL import Image
    w, h = img.size
    cw, ch = w / s, h / s
    x0, y0 = (w - cw) * anchor[0], (h - ch) * anchor[1]
    return img.crop((x0, y0, x0 + cw, y0 + ch)).resize((w, h), Image.BILINEAR)


def measure(base_mp4: Path, fx_mp4: Path, t: float) -> dict:
    """{zoom, dx, dy}: zoom & geser ditaksir BERSAMA. Untuk tiap skala kandidat, frame dasar dizoom di titik jangkar,
    pergeseran sisa dicari dengan korelasi fase, lalu galat dihitung di area tengah setelah digeser; dipilih galat
    terkecil (zoom saja tanpa koreksi geser keliru pada efek yang menggabungkan zoom & geser, mis. pan)."""
    from PIL import Image
    a, b = gray(base_mp4, t), gray(fx_mp4, t)
    n = min(a.shape[0], b.shape[0])
    a, b = a[:n], b[:n]
    img = Image.fromarray(a.astype(np.uint8))
    h, w = a.shape
    my, mx = h // 6, w // 6
    best = None
    for s in np.linspace(1.0, 1.6, 61):
        az = np.asarray(_zoomed(img, float(s)), np.float64)
        dx, dy = shift(az, b)
        moved = np.roll(az, (int(round(dy * h)), int(round(dx * w))), axis=(0, 1))
        err = float(np.mean((moved[my:-my, mx:-mx] - b[my:-my, mx:-mx]) ** 2))
        if best is None or err < best[0]:
            best = (err, float(s), dx, dy)
    _, s, dx, dy = best
    return {"t": t, "zoom": round(s, 3), "dx": round(float(dx), 4), "dy": round(float(dy), 4)}
