"""Konfigurasi aplikasi. Semua nilai bisa diganti lewat environment variable."""
import os
import shutil
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("VIDEO_APP_DATA", BASE_DIR / "data")).resolve()
PROJECTS_DIR = DATA_DIR / "projects"
FONTS_DIR = BASE_DIR / "fonts"
WEB_DIR = BASE_DIR / "web"

FFMPEG = os.environ.get("FFMPEG_BIN") or shutil.which("ffmpeg") or "ffmpeg"
FFPROBE = os.environ.get("FFPROBE_BIN") or shutil.which("ffprobe") or "ffprobe"

# Ukuran model Whisper: tiny, base, small, medium, large-v3.
# "small" adalah kompromi yang baik antara akurasi dan kecepatan di laptop.
WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "small")
# "auto" memakai GPU NVIDIA bila tersedia, lalu jatuh ke CPU bila gagal.
WHISPER_DEVICE = os.environ.get("WHISPER_DEVICE", "auto")

# Jumlah pekerjaan berat (analisis/render) yang jalan bersamaan.
# 1 paling aman untuk laptop.
JOB_WORKERS = int(os.environ.get("JOB_WORKERS", "1"))

PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
