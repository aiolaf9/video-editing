"""Alur kerja utama: impor video, analisis (preview + transkrip + kandidat klip), dan render."""
import time
from pathlib import Path
from typing import Optional

from . import clipper, media, storage, transcribe
from .jobs import Job
from .render import build_command, output_size
from .schemas import RenderRequest
from .subtitles import SubtitleOptions, build_ass


def source_path(pid: str) -> Path:
    meta = storage.load_project(pid)
    if not meta.get("source"):
        raise FileNotFoundError("Video sumber belum ada.")
    return storage.project_dir(pid) / meta["source"]


def download(job: Job, pid: str, url: str) -> str:
    """Mengunduh video dari link (YouTube, Instagram, TikTok, dll.) dengan yt-dlp."""
    import yt_dlp

    folder = storage.project_dir(pid)
    job.update(0, "Mengunduh video...")

    def hook(d: dict) -> None:
        if d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            if total:
                job.update(20 * d.get("downloaded_bytes", 0) / total)

    opts = {
        "outtmpl": str(folder / "source.%(ext)s"),
        "format": "bv*[height<=1080]+ba/b[height<=1080]/b",
        "merge_output_format": "mp4",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "progress_hooks": [hook],
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
    files = sorted(folder.glob("source.*"), key=lambda p: p.stat().st_size, reverse=True)
    if not files:
        raise RuntimeError("Unduhan selesai tetapi file video tidak ditemukan.")
    storage.update_project(pid, source=files[0].name, name=(info.get("title") or "Video")[:120])
    return files[0].name


def analyze(job: Job, pid: str, language: Optional[str] = None, offset: float = 0.0) -> dict:
    """Probe -> preview 480p -> energi audio -> transkrip Whisper -> kandidat klip."""
    span = 100 - offset
    src = source_path(pid)
    folder = storage.project_dir(pid)
    storage.update_project(pid, status="dianalisis")

    job.update(offset, "Membaca info video...")
    info = media.probe(src)
    storage.update_project(pid, **info)

    media.make_preview(src, folder / "preview.mp4", info["duration"], info["has_audio"],
                       job.stage(offset, offset + span * 0.2, "Membuat preview..."))

    energy: list[float] = []
    transcript: dict = {"language": None, "segments": []}
    if info["has_audio"]:
        job.update(offset + span * 0.22, "Menganalisis audio...")
        energy = media.audio_energy(src)
        progress = job.stage(offset + span * 0.25, offset + span * 0.92,
                             "Membuat transkrip dengan Whisper (bisa beberapa menit)...")
        transcript = transcribe.transcribe(src, info["duration"], language, progress)
    storage.write_data(pid, "energy", energy)
    storage.write_data(pid, "transcript", transcript)

    job.update(offset + span * 0.95, "Mencari momen terbaik...")
    clips = clipper.suggest(transcript, energy, info["duration"])
    storage.write_data(pid, "clips", clips)
    storage.update_project(pid, status="siap", language=transcript.get("language"))
    job.update(100, "Analisis selesai!")
    return {"clips": len(clips)}


def import_and_analyze(job: Job, pid: str, url: str, language: Optional[str]) -> dict:
    try:
        download(job, pid, url)
    except Exception:
        storage.update_project(pid, status="gagal")
        raise
    return analyze(job, pid, language, offset=20)


def safe_analyze(job: Job, pid: str, language: Optional[str] = None) -> dict:
    try:
        return analyze(job, pid, language)
    except Exception:
        storage.update_project(pid, status="gagal")
        raise


def render(job: Job, pid: str, req: RenderRequest) -> dict:
    meta = storage.load_project(pid)
    src = source_path(pid)
    folder = storage.project_dir(pid)
    renders = folder / "renders"
    req.end = min(req.end, meta.get("duration") or req.end)
    if req.end - req.start < 1:
        raise ValueError("Rentang klip di luar durasi video.")

    rid = storage.new_id()
    out = renders / f"{rid}.mp4"
    width, height = output_size(req.aspect, meta["width"], meta["height"])

    subs = None
    if req.subtitles.enabled:
        transcript = storage.read_data(pid, "transcript", {}) or {}
        s = req.subtitles
        ass = build_ass(transcript, req.start, req.end, width, height,
                        SubtitleOptions(style=s.style, font=s.font, size=s.size, color=s.color,
                                        highlight=s.highlight, position=s.position,
                                        uppercase=s.uppercase, words_per_line=s.words_per_line),
                        speed=req.speed)
        if "Dialogue:" in ass:
            subs = renders / f"{rid}.ass"
            subs.write_text(ass, encoding="utf-8")

    music = None
    if req.music.file:
        candidate = folder / "music" / storage.safe_filename(req.music.file)
        if not candidate.is_file():
            raise FileNotFoundError("File musik tidak ditemukan. Unggah ulang musiknya.")
        music = candidate

    duration = (req.end - req.start) / req.speed
    progress = job.stage(2, 98, "Merender video...")
    try:
        media.run_ffmpeg(build_command(src, out, req, meta, subs, music, renders), duration, progress, cwd=renders)
    except media.MediaError:
        if req.encoder == "cpu":
            raise
        # Encoder GPU tidak tersedia di komputer ini: ulangi dengan CPU.
        job.update(2, "Encoder GPU gagal, mengulang dengan CPU...")
        req = req.model_copy(update={"encoder": "cpu"})
        media.run_ffmpeg(build_command(src, out, req, meta, subs, music, renders), duration, progress, cwd=renders)

    item = {
        "id": rid,
        "file": out.name,
        "title": req.title or f"Klip {req.start:.0f}s-{req.end:.0f}s",
        "start": req.start,
        "end": req.end,
        "aspect": req.aspect,
        "created": time.time(),
        "size": out.stat().st_size,
    }
    storage.append_to_list(pid, "renders", item)
    job.update(100, "Render selesai!")
    return item

