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

# Batas waktu pekerjaan (detik). Dihitung dari ukuran pekerjaan agar video panjang tetap muat,
# tetapi job yang macet pasti berhenti. Server CPU tanpa GPU: nilai bawaan sudah longgar.
ANALYZE_TIMEOUT_BASE = float(os.environ.get("ANALYZE_TIMEOUT_BASE", "900"))        # 15 menit
ANALYZE_TIMEOUT_PER_MIN = float(os.environ.get("ANALYZE_TIMEOUT_PER_MIN", "180"))  # +3 menit per menit video
RENDER_TIMEOUT_BASE = float(os.environ.get("RENDER_TIMEOUT_BASE", "300"))          # 5 menit
RENDER_TIMEOUT_PER_SEC = float(os.environ.get("RENDER_TIMEOUT_PER_SEC", "20"))      # +20 dtk per detik hasil
DOWNLOAD_TIMEOUT = float(os.environ.get("DOWNLOAD_TIMEOUT", "3600"))
JOB_TIMEOUT_MAX = float(os.environ.get("JOB_TIMEOUT_MAX", str(6 * 3600)))
# FFmpeg tanpa laporan kemajuan selama ini (detik) dianggap macet dan dihentikan.
FFMPEG_STALL_TIMEOUT = float(os.environ.get("FFMPEG_STALL_TIMEOUT", "300"))
PROBE_TIMEOUT = float(os.environ.get("PROBE_TIMEOUT", "120"))
# Setelah batal/timeout, waktu tunggu (detik) agar job berhenti sendiri. Bila lewat, job
# ditandai gagal; worker terpisah me-restart dirinya agar tidak tertahan selamanya.
CANCEL_GRACE = float(os.environ.get("CANCEL_GRACE", "90"))

# Keamanan & batas pemakaian
COOKIE_SECURE = os.environ.get("COOKIE_SECURE", "0") == "1"      # 1 bila diakses lewat HTTPS
SESSION_DAYS = int(os.environ.get("SESSION_DAYS", "14"))
MAX_UPLOAD_MB = int(os.environ.get("MAX_UPLOAD_MB", "4096"))
MAX_PENDING_PER_USER = int(os.environ.get("MAX_PENDING_PER_USER", "5"))
# Ambil video dari link (YouTube, dll.): all | admins | none
ALLOW_URL_IMPORT = os.environ.get("ALLOW_URL_IMPORT", "all")
# DIGANTI oleh siklus media sementara (P2). Hanya dibaca untuk peringatan bila masih diisi.
RETENTION_DAYS = int(os.environ.get("RETENTION_DAYS", "0"))

# ---- Media sementara (P2) ----
# Video, preview, hasil render & musik dihapus otomatis: RETENTION jam sejak aktivitas terakhir
# pengguna, paling lama MAX jam sejak upload. Metadata (akun, proyek, guidebook) tetap disimpan.
MEDIA_LIFECYCLE = os.environ.get("MEDIA_LIFECYCLE", "on") == "on"
MEDIA_RETENTION_HOURS = float(os.environ.get("MEDIA_RETENTION_HOURS", "24"))
MEDIA_MAX_HOURS = float(os.environ.get("MEDIA_MAX_HOURS", "72"))
MEDIA_WARN_HOURS = float(os.environ.get("MEDIA_WARN_HOURS", "3"))       # tampilkan peringatan di UI
CLEANUP_INTERVAL = float(os.environ.get("CLEANUP_INTERVAL", "300"))       # detik antar pembersihan
TEMP_FILE_GRACE_MINUTES = float(os.environ.get("TEMP_FILE_GRACE_MINUTES", "30"))  # umur min. file sisa
INCOMPLETE_UPLOAD_HOURS = float(os.environ.get("INCOMPLETE_UPLOAD_HOURS", "2"))
# Perlindungan disk: sisakan minimal sebanyak ini. Upload/render baru ditolak bila kurang.
DISK_MIN_FREE_MB = float(os.environ.get("DISK_MIN_FREE_MB", "2048"))
DISK_MIN_FREE_PCT = float(os.environ.get("DISK_MIN_FREE_PCT", "5"))
# Kuota media sementara per workspace/unit (MB). 0 = tanpa batas. Bisa diatur per unit di Admin.
STORAGE_QUOTA_MB_DEFAULT = float(os.environ.get("STORAGE_QUOTA_MB_DEFAULT", "0"))
# File upload sementara (multipart) ditulis di disk data, bukan /tmp sistem: ikut terhitung & dibersihkan.
UPLOAD_TMP_DIR = DATA_DIR / "tmp"
UPLOAD_TMP_DIR.mkdir(parents=True, exist_ok=True)
