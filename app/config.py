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

# Thread CPU per proses Whisper. 0 = otomatis. Di server dengan banyak worker,
# isi (jumlah core / jumlah worker) agar worker tidak saling berebut CPU.
WHISPER_THREADS = int(os.environ.get("WHISPER_THREADS", "0"))

PROJECTS_DIR.mkdir(parents=True, exist_ok=True)

# Database: SQLite (lokal) atau PostgreSQL (server), mis. postgresql+psycopg://user:pass@db/videoapp
DATABASE_URL = os.environ.get("DATABASE_URL") or f"sqlite:///{DATA_DIR / 'app.db'}"

# Koneksi database per proses. Total koneksi = (proses web + worker) x (POOL + OVERFLOW)
# harus di bawah max_connections PostgreSQL (bawaan 100).
DB_POOL_SIZE = int(os.environ.get("DB_POOL_SIZE", "5"))
DB_MAX_OVERFLOW = int(os.environ.get("DB_MAX_OVERFLOW", "5"))

# Worker di dalam proses web (praktis untuk satu komputer). Di server, worker dijalankan
# terpisah dengan `python -m app.worker` sehingga web tetap ringan walau render menumpuk.
EMBEDDED_WORKERS = os.environ.get("EMBEDDED_WORKERS", "1" if DATABASE_URL.startswith("sqlite") else "0") == "1"
# Jumlah pekerjaan berat (analisis/render) yang jalan bersamaan pada worker bawaan.
JOB_WORKERS = int(os.environ.get("JOB_WORKERS", "1"))

# Keamanan & batas pemakaian
COOKIE_SECURE = os.environ.get("COOKIE_SECURE", "0") == "1"      # 1 bila diakses lewat HTTPS
SESSION_DAYS = int(os.environ.get("SESSION_DAYS", "14"))
MAX_UPLOAD_MB = int(os.environ.get("MAX_UPLOAD_MB", "4096"))
MAX_PENDING_PER_USER = int(os.environ.get("MAX_PENDING_PER_USER", "5"))
# Ambil video dari link (YouTube, dll.): all | admins | none
ALLOW_URL_IMPORT = os.environ.get("ALLOW_URL_IMPORT", "all")
# Hapus file proyek lebih tua dari N hari (0 = tidak pernah)
RETENTION_DAYS = int(os.environ.get("RETENTION_DAYS", "0"))
