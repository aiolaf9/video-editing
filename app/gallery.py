"""Galeri preset subtitle (M2.2): thumbnail & klip gerak tiap preset, dirender dengan build_ass + FFmpeg/libass
yang SAMA seperti render final (bukan tiruan CSS), di atas latar netral dan kalimat contoh. Disimpan di cache
data aplikasi; dibuat ulang otomatis bila definisi preset berubah."""
from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path

from . import media, subtitles
from .config import DATA_DIR, FFMPEG, FONTS_DIR

W, H = 360, 640
CROP_W, CROP_H = 300, 160
DURATION = 4.0
SAMPLE = ["Insyaallah,", "zakat", "fitrah", "dibayar", "sebelum", "Idul", "Fitri.", "Besarnya", "2,5", "kg", "beras."]
TERMS = ("zakat", "fitrah", "insyaallah")
CACHE = DATA_DIR / "cache" / "gallery"
_lock = threading.Lock()


def sample_transcript() -> dict:
    words, t = [], 0.15
    for w in SAMPLE:
        d = 0.22 + 0.03 * len(w)
        words.append({"start": round(t, 2), "end": round(t + d, 2), "word": w})
        t += d + 0.06
    return {"language": "id", "segments": [{"start": words[0]["start"], "end": words[-1]["end"],
                                             "text": " ".join(SAMPLE), "words": words}]}


def options_for(preset: str, overrides: dict | None = None) -> subtitles.SubtitleOptions:
    p = {**subtitles.PRESETS[preset], **(overrides or {})}
    keys = subtitles.SubtitleOptions.__dataclass_fields__
    o = subtitles.SubtitleOptions(**{k: v for k, v in p.items() if k in keys})
    o.style = p["anim"]
    o.emphasis = True
    o.emphasis_terms = TERMS
    return o


def _key(preset: str) -> str:
    # Tanpa membaca berkas .py (tidak ada di build PyInstaller): definisi preset + versi tampilan subtitle.
    raw = json.dumps(subtitles.PRESETS[preset], sort_keys=True) + subtitles.RENDER_VERSION
    return hashlib.sha256(raw.encode()).hexdigest()[:12]


def render(preset: str) -> tuple[Path, Path]:
    """(mp4, png) demo preset; dibuat sekali lalu dari cache."""
    if preset not in subtitles.PRESETS:
        raise KeyError(preset)
    CACHE.mkdir(parents=True, exist_ok=True)
    key = _key(preset)
    mp4, png = CACHE / f"{preset}-{key}.mp4", CACHE / f"{preset}-{key}.png"
    if mp4.is_file() and png.is_file():
        return mp4, png
    with _lock:
        if mp4.is_file() and png.is_file():
            return mp4, png
        for old in CACHE.glob(f"{preset}-*"):
            old.unlink(missing_ok=True)
        ass = CACHE / f"{preset}-{key}.ass"
        ass.write_text(subtitles.build_ass(sample_transcript(), 0, DURATION, W, H, options_for(preset)),
                       encoding="utf-8")
        # Latar netral bergerak pelan (gradasi) agar outline/bayangan/kotak terlihat jelas.
        bg = (f"gradients=s={W}x{H}:c0=0x2B3A55:c1=0x7A5C3E:x0=0:y0=0:x1={W}:y1={H}:speed=0.02:d={DURATION}"
              f",format=yuv420p")
        # Galeri menampilkan potongan 300x160 (diperbesar 2x) di sekitar subtitle (ukuran relatif teks = hasil 9:16 sebenarnya).
        opts = options_for(preset)
        cy = int(H * (opts.pos_y if opts.pos_y is not None else {"top": 0.16, "middle": 0.5}.get(opts.position, 0.76)))
        top = max(0, min(H - CROP_H, cy - CROP_H // 2))
        vf = (f"ass='{ass.name}':fontsdir='{_rel(FONTS_DIR, CACHE)}',crop={CROP_W}:{CROP_H}:{(W - CROP_W) // 2}:{top},"
              f"scale={CROP_W * 2}:{CROP_H * 2}:flags=lanczos")
        tmp = CACHE / f"{preset}-{key}.part.mp4"
        media.run_ffmpeg(["-f", "lavfi", "-i", bg, "-vf", vf, "-c:v", "libx264", "-preset", "veryfast", "-crf", "26",
                          "-pix_fmt", "yuv420p", "-an", "-t", str(DURATION), str(tmp.name)], DURATION, None, cwd=CACHE,
                         stall_timeout=60)
        tmp.replace(mp4)
        media.run_ffmpeg(["-ss", "1.3", "-i", mp4.name, "-frames:v", "1", png.name], 0, None, cwd=CACHE,
                         stall_timeout=30)
        ass.unlink(missing_ok=True)
    return mp4, png


def _rel(path: Path, cwd: Path) -> str:
    import os
    try:
        return os.path.relpath(path, cwd).replace("\\", "/").replace(":", r"\:")
    except ValueError:
        return str(path).replace("\\", "/").replace(":", r"\:")


__all__ = ["render", "sample_transcript", "options_for", "FFMPEG"]
