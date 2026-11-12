"""Konfigurasi. Semua nilai bisa diganti lewat environment variable."""
import os
import shutil
from pathlib import Path

PKG_DIR = Path(__file__).resolve().parent
REPO_DIR = PKG_DIR.parent
WEB_DIR = PKG_DIR / "web"
FONTS_DIR = Path(os.environ.get("STORY_FONTS_DIR", REPO_DIR / "fonts"))
DATA_DIR = Path(os.environ.get("STORY_DATA", REPO_DIR / "data-storymaker")).resolve()
PROJECTS_DIR = DATA_DIR / "projects"

FFMPEG = os.environ.get("FFMPEG_BIN") or shutil.which("ffmpeg") or "ffmpeg"
FFPROBE = os.environ.get("FFPROBE_BIN") or shutil.which("ffprobe") or "ffprobe"

WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "small")
WHISPER_DEVICE = os.environ.get("WHISPER_DEVICE", "auto")
WHISPER_THREADS = int(os.environ.get("WHISPER_THREADS", "0"))

# Penyusun cerita: "auto" = Claude bila API key tersedia, selain itu heuristik lokal.
STORY_PLANNER = os.environ.get("STORY_PLANNER", "auto")       # auto | claude | heuristic
CLAUDE_MODEL = os.environ.get("STORY_CLAUDE_MODEL", "claude-opus-5-5")

MIN_CLIPS = int(os.environ.get("STORY_MIN_CLIPS", "3"))
MAX_CLIPS = int(os.environ.get("STORY_MAX_CLIPS", "10"))
MAX_UPLOAD_MB = int(os.environ.get("STORY_MAX_UPLOAD_MB", "4096"))
JOB_WORKERS = int(os.environ.get("STORY_JOB_WORKERS", "1"))

OUT_W, OUT_H, OUT_FPS = 1080, 1920, 30

PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
