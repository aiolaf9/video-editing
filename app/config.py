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
# Migrasi skema otomatis saat start (P5). Kosong = otomatis untuk SQLite (satu komputer), TIDAK untuk PostgreSQL:
# di server jalankan `python -m app.manage db-upgrade` (Docker: service `migrate`) setelah backup.
_auto = os.environ.get("AUTO_MIGRATE", "").strip().lower()
AUTO_MIGRATE = None if _auto == "" else _auto not in ("0", "off", "false", "no")

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
# Impor dari link hanya ke alamat IP publik (P5.4, anti-SSRF). Jaringan internal tepercaya (CIDR, dipisah koma)
# hanya berlaku untuk impor yang diminta admin global, mis. "10.20.0.0/16,fd00:abcd::/48".
URL_IMPORT_TRUSTED_NETWORKS = os.environ.get("URL_IMPORT_TRUSTED_NETWORKS", "")
URL_IMPORT_MAX_MB = int(os.environ.get("URL_IMPORT_MAX_MB", str(MAX_UPLOAD_MB)))
URL_IMPORT_MAX_REDIRECTS = int(os.environ.get("URL_IMPORT_MAX_REDIRECTS", "5"))
# Proxy keluar untuk unduhan (kosong = koneksi langsung; variabel HTTP(S)_PROXY lingkungan diabaikan).
# Bila diisi, proxy itulah yang wajib menyaring alamat internal (aplikasi hanya melihat alamat proxy).
DOWNLOAD_PROXY = os.environ.get("DOWNLOAD_PROXY", "")
# DIGANTI oleh siklus media sementara (P2). Hanya dibaca untuk peringatan bila masih diisi.
RETENTION_DAYS = int(os.environ.get("RETENTION_DAYS", "0"))

# ---- Media sementara (P2) ----
# Video, preview, hasil render & musik dihapus otomatis: RETENTION jam sejak aktivitas terakhir
# pengguna, paling lama MAX jam sejak upload. Metadata (akun, proyek, guidebook) tetap disimpan.
MEDIA_LIFECYCLE = os.environ.get("MEDIA_LIFECYCLE", "on").strip().lower() not in ("0", "off", "false", "no")
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

# ---- Resource & penjadwalan (P3) ----
# Thread CPU per job berat (FFmpeg, Whisper, OpenCV). 0 = otomatis: core tersedia (termasuk batas
# `cpus` container) dibagi HOST_HEAVY_WORKERS, yaitu jumlah worker analisis+render yang berjalan
# bersamaan di satu mesin.
WORKER_THREADS = int(os.environ.get("WORKER_THREADS", "0"))
HOST_HEAVY_WORKERS = int(os.environ.get("HOST_HEAVY_WORKERS", "2"))
# Opsional: worker menunda mengambil job bila load rata-rata 1 menit per core melebihi ini, paling lama
# CPU_WAIT_MAX detik. Bawaan MATI (0): CPU sudah dibatasi anggaran thread & `cpus` container, sedangkan load
# average tertinggal ±1 menit dan ikut naik karena worker sendiri, sehingga render bisa tertahan tanpa
# alasan (terukur di benchmark P3). Aktifkan hanya bila server juga dipakai proses berat lain.
CPU_MAX_LOAD_PER_CORE = float(os.environ.get("CPU_MAX_LOAD_PER_CORE", "0"))
CPU_WAIT_MAX = float(os.environ.get("CPU_WAIT_MAX", "60"))
# Penjaga RAM: job hanya diambil bila RAM tersedia >= kebutuhan job + cadangan. Kebutuhan analisis
# mengikuti ukuran model Whisper (dimuat sekali per proses worker).
MEM_RESERVE_MB = int(os.environ.get("MEM_RESERVE_MB", "512"))
# Cadangan di atas berlaku untuk RAM HOST (sistem operasi & proses lain). Di dalam batas container (`mem_limit`)
# hanya dibutuhkan ruang aman kecil: batas itu sudah memisahkan container dari host.
CONTAINER_MEM_HEADROOM_MB = int(os.environ.get("CONTAINER_MEM_HEADROOM_MB", "64"))
_WHISPER_MEM = {"tiny": 700, "base": 900, "small": 1600, "medium": 3200, "large-v3": 5500, "large-v2": 5500}
TASK_MEM_MB = {
    "analisis": int(os.environ.get("ANALYSIS_MEM_MB", str(_WHISPER_MEM.get(WHISPER_MODEL, 2500)))),
    "analisis_loaded": int(os.environ.get("ANALYSIS_LOADED_MEM_MB", "600")),
    "render": int(os.environ.get("RENDER_MEM_MB", "700")),
    "io": int(os.environ.get("IO_MEM_MB", "300")),
}
# GPU dipakai hanya bila terdeteksi (nvidia-smi) dengan VRAM bebas minimal ini.
GPU_MIN_FREE_MB = int(os.environ.get("GPU_MIN_FREE_MB", "2048"))
# Penjadwalan antrean
AGING_SECONDS = float(os.environ.get("AGING_SECONDS", "60"))        # prioritas naik 1 tingkat per N detik menunggu
MAX_RUNNING_PER_UNIT = int(os.environ.get("MAX_RUNNING_PER_UNIT", "3"))   # per antrean; 0 = tanpa batas
MAX_QUEUED_PER_UNIT = int(os.environ.get("MAX_QUEUED_PER_UNIT", "30"))    # job menunggu per unit; 0 = tanpa batas
MAX_QUEUE_DEPTH = int(os.environ.get("MAX_QUEUE_DEPTH", "300"))           # job menunggu per antrean (seluruh server)
# Batas job berjalan bersamaan per antrean di seluruh server (0 = sebanyak worker yang ada).
MAX_CONCURRENT = {
    "analisis": int(os.environ.get("MAX_CONCURRENT_ANALYSES", "0")),
    "render": int(os.environ.get("MAX_CONCURRENT_RENDERS", "0")),
    "io": int(os.environ.get("MAX_CONCURRENT_IO", "0")),
}
# Worker bawaan (mode satu komputer): jumlah thread worker per antrean.
ANALYSIS_WORKERS = int(os.environ.get("ANALYSIS_WORKERS", "1"))
WORKER_REPORT_INTERVAL = float(os.environ.get("WORKER_REPORT_INTERVAL", "10"))
# Token untuk /metrics (format Prometheus). Kosong = endpoint dimatikan.
METRICS_TOKEN = os.environ.get("METRICS_TOKEN", "")

# ---- Upload bertahap & preview (P4) ----
# Upload dipotong per chunk (protokol tus). Upload yang terputus bisa dilanjutkan selama UPLOAD_RESUME_HOURS
# sejak aktivitas terakhir; setelah itu file setengah jadi & reservasi kuotanya dibersihkan otomatis.
UPLOAD_RESUME_HOURS = float(os.environ.get("UPLOAD_RESUME_HOURS", "12"))
UPLOAD_CHUNK_MAX_MB = int(os.environ.get("UPLOAD_CHUNK_MAX_MB", "64"))       # batas satu PATCH
UPLOAD_CHUNK_MB = int(os.environ.get("UPLOAD_CHUNK_MB", "8"))                # ukuran chunk awal di browser
MAX_ACTIVE_UPLOADS_PER_USER = int(os.environ.get("MAX_ACTIVE_UPLOADS_PER_USER", "4"))
# Preview: sumber yang sudah ramah browser (H.264 8-bit, AAC, tinggi <= batas, bitrate <= batas) tidak di-transcode,
# cukup disalin ulang wadahnya (remux). Selain itu transcode ringan 480p (video panjang: 360p).
PREVIEW_HEIGHT = int(os.environ.get("PREVIEW_HEIGHT", "480"))
# 720p: pelacakan wajah membaca preview; preview 1080p (tanpa transcode) membuat langkah itu jauh lebih berat.
PREVIEW_COPY_MAX_HEIGHT = int(os.environ.get("PREVIEW_COPY_MAX_HEIGHT", "720"))
PREVIEW_COPY_MAX_KBPS = int(os.environ.get("PREVIEW_COPY_MAX_KBPS", "4000"))
PREVIEW_LONG_MINUTES = float(os.environ.get("PREVIEW_LONG_MINUTES", "20"))   # 0 = tidak ada mode video panjang
PREVIEW_LONG_HEIGHT = int(os.environ.get("PREVIEW_LONG_HEIGHT", "360"))
PREVIEW_MAX_KBPS = int(os.environ.get("PREVIEW_MAX_KBPS", "1500"))
# Dekode hardware untuk preview: none | auto | vaapi | cuda | qsv. Gagal = otomatis kembali ke CPU.
PREVIEW_HWACCEL = os.environ.get("PREVIEW_HWACCEL", "none").strip().lower()
