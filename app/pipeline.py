"""Alur kerja utama: impor video, analisis (preview + transkrip + kandidat klip), dan render."""
import logging
import shutil
import time
from pathlib import Path
from typing import Optional

from . import clipper, faces, jobcontrol, media, netpolicy, overlays, resources, storage, transcribe
from .config import (ANALYZE_TIMEOUT_BASE, ANALYZE_TIMEOUT_PER_MIN, DOWNLOAD_PROXY, JOB_TIMEOUT_MAX,
                     URL_IMPORT_MAX_MB, URL_IMPORT_MAX_REDIRECTS)
from .jobs import Job
from .render import build_command, output_size
from .schemas import RenderRequest
from .subtitles import SubtitleOptions, build_ass

log = logging.getLogger("video-editing.pipeline")


def source_path(pid: str) -> Path:
    meta = storage.load_project(pid)
    if not meta.get("source"):
        raise FileNotFoundError("Video sumber belum ada.")
    return storage.project_dir(pid) / meta["source"]


class _YtdlpLogger:
    """Meneruskan pesan yt-dlp ke log server agar error terlihat di jendela terminal."""

    def debug(self, msg: str) -> None:
        if not msg.startswith("[download]"):
            log.debug("yt-dlp: %s", msg)

    def info(self, msg: str) -> None:
        log.info("yt-dlp: %s", msg)

    def warning(self, msg: str) -> None:
        log.warning("yt-dlp: %s", msg)

    def error(self, msg: str) -> None:
        log.error("yt-dlp: %s", msg)


class DownloadTooLarge(RuntimeError):
    pass


# Handler HTTP yt-dlp yang koneksinya melewati socket.getaddrinfo Python (diperiksa netpolicy). Handler lain,
# mis. CurlCFFI (resolver libcurl sendiri) bila kelak terpasang, dibuang: gagal tertutup, bukan diam-diam lolos.
GUARDED_HANDLERS = {"Urllib", "Requests", "Websockets"}


def _safe_ydl(yt_dlp):
    """YoutubeDL dengan batas jumlah redirect dan tanpa pengunduh berbasis proses eksternal."""
    _forbid_process_downloaders(yt_dlp)

    class SafeYoutubeDL(yt_dlp.YoutubeDL):
        def build_request_director(self, handlers, preferences=None):
            director = super().build_request_director(handlers, preferences)
            for key in [k for k in director.handlers if k not in GUARDED_HANDLERS]:
                director.handlers.pop(key).close()
            for rh in director.handlers.values():
                if hasattr(rh, "_create_instance"):   # Requests & Urllib (websocket tidak mengikuti redirect)
                    rh._create_instance = _limit_redirects(rh._create_instance)
            return director

    return SafeYoutubeDL


def _forbid_process_downloaders(yt_dlp) -> None:
    """FFmpeg/aria2c/curl/wget/rtmpdump membuka koneksi sendiri di luar proses Python sehingga tidak melewati
    pemeriksaan alamat netpolicy. yt-dlp bisa memilihnya diam-diam (mis. HLS yang tidak didukung pengunduh native
    didelegasikan ke FFmpeg), jadi ditahan di titik eksekusinya selama unduhan berjalan dalam import_guard."""
    from yt_dlp.downloader.external import ExternalFD, FFmpegFD
    from yt_dlp.downloader.rtmp import RtmpFD

    for cls, name in ((ExternalFD, "real_download"), (ExternalFD, "_call_downloader"),
                      (FFmpegFD, "_call_downloader"), (RtmpFD, "real_download")):
        original = cls.__dict__[name]
        if getattr(original, "_netpolicy", False):
            continue

        def blocked(self, *args, _original=original, **kwargs):
            if netpolicy.guard_active():
                raise yt_dlp.utils.DownloadError("Format video ini memerlukan pengunduh eksternal yang tidak diizinkan.")
            return _original(self, *args, **kwargs)

        blocked._netpolicy = True
        setattr(cls, name, blocked)


def _limit_redirects(create):
    import urllib.request

    def wrapped(*args, **kwargs):
        instance = create(*args, **kwargs)
        if hasattr(instance, "max_redirects"):                 # requests.Session
            instance.max_redirects = URL_IMPORT_MAX_REDIRECTS
        for handler in getattr(instance, "handlers", []):     # urllib OpenerDirector
            if isinstance(handler, urllib.request.HTTPRedirectHandler):
                handler.max_redirections = URL_IMPORT_MAX_REDIRECTS
        return instance

    return wrapped


def download(job: Job, pid: str, url: str, trusted: bool = False) -> str:
    """Mengunduh video dari link (YouTube, Instagram, TikTok, dll.) dengan yt-dlp.
    Anti-SSRF (P5.4): setiap koneksi (termasuk redirect & URL manifest/segmen turunan) hanya ke IP publik, atau
    jaringan tepercaya bila diminta admin global (trusted). Lihat netpolicy & docs/SECURITY_AUDIT_URL.md."""
    import yt_dlp

    folder = storage.project_dir(pid)
    storage.update_project(pid, status="mengunduh")
    job.update(0, "Menghubungi situs video...")
    limit = URL_IMPORT_MAX_MB * 1024 * 1024
    sizes: dict = {}
    too_large: list = []

    def hook(d: dict) -> None:
        sizes[d.get("filename")] = d.get("downloaded_bytes") or 0
        if sum(sizes.values()) > limit:   # juga untuk unduhan tanpa ukuran di header (fragmen DASH/HLS)
            too_large.append(True)        # yt-dlp bisa menelan exception dari hook: catat juga sebagai flag
            raise DownloadTooLarge(f"Video melebihi batas {URL_IMPORT_MAX_MB} MB.")
        if d.get("status") != "downloading":
            if d.get("status") == "finished":
                job.update(99, "Menggabungkan video dan audio...")
            return
        done = d.get("downloaded_bytes") or 0
        total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
        if total:
            job.update(98 * done / total, f"Mengunduh video... {done / 1e6:.1f} / {total / 1e6:.1f} MB")
        else:
            job.update(None, f"Mengunduh video... {done / 1e6:.1f} MB")

    opts = {
        "outtmpl": str(folder / "source.%(ext)s"),
        "format": "bv*[height<=1080]+ba/b[height<=1080]/b",
        "merge_output_format": "mp4",
        "noplaylist": True,
        "quiet": True,
        "noprogress": True,
        "logger": _YtdlpLogger(),
        "socket_timeout": 30,
        "retries": 5,
        "fragment_retries": 5,
        "progress_hooks": [hook],
        "proxy": DOWNLOAD_PROXY,                 # "" = langsung; HTTP(S)_PROXY lingkungan tidak dipakai diam-diam
        "external_downloader": {"default": "native"},
        "hls_prefer_native": True,
        "concurrent_fragment_downloads": 1,      # satu thread: pemeriksaan alamat berlaku per thread
        # Hanya Deno (sandbox: tanpa akses jaringan/berkas kecuali diizinkan). Node/Bun tanpa sandbox: jangan diaktifkan.
        "js_runtimes": {"deno": {}},
    }
    try:
        netpolicy.check_import_url(url, trusted)   # juga untuk job yang antre sebelum P5.4
        with netpolicy.import_guard(trusted, [h for h in [netpolicy.proxy_host(DOWNLOAD_PROXY)] if h]) as guard:
            return _download(job, pid, url, folder, opts, _safe_ydl(yt_dlp), guard, too_large)
    except netpolicy.BlockedURL as exc:
        _discard(folder)
        storage.update_project(pid, status="gagal", name="Unduhan diblokir")
        raise RuntimeError(str(exc)) from None
    except jobcontrol.JobCancelled as exc:
        # Unduhan dihentikan (kapan pun, termasuk setelah yt-dlp selesai): buang file setengah jadi.
        for leftover in folder.glob("source.*"):
            leftover.unlink(missing_ok=True)
        if exc.reason != "shutdown":
            storage.update_project(pid, status="batal", name="Unduhan dibatalkan")
        raise


def _discard(folder: Path) -> None:
    for leftover in folder.glob("source.*"):  # unduhan gagal: buang potongan file
        leftover.unlink(missing_ok=True)


def _download(job: Job, pid: str, url: str, folder: Path, opts: dict, ydl_class, guard: dict,
              too_large: list) -> str:
    def refuse(exc: Optional[BaseException]) -> None:
        _discard(folder)
        storage.update_project(pid, status="gagal", name="Unduhan gagal")
        if guard["blocked"]:   # redirect/manifest/segmen menuju alamat internal: jangan ungkap alamatnya
            raise RuntimeError("Link (atau pengalihannya) mengarah ke alamat jaringan internal/privat dan diblokir.") from None
        if too_large or isinstance(exc, DownloadTooLarge):
            raise RuntimeError(f"Video melebihi batas unduhan {URL_IMPORT_MAX_MB} MB.") from None

    try:
        with ydl_class(opts) as ydl:
            info = ydl.extract_info(url, download=True)
    except jobcontrol.JobCancelled:
        raise
    except Exception as exc:
        refuse(exc)
        msg = str(exc).replace("ERROR: ", "")
        if "instagram" in url.lower() and ("login" in msg.lower() or "cookies" in msg.lower()):
            msg += "\n\nInstagram sering meminta login. Unduh videonya manual lalu unggah filenya."
        raise RuntimeError(msg) from exc
    if too_large or guard["blocked"]:
        refuse(None)
    files = sorted(folder.glob("source.*"), key=lambda p: p.stat().st_size, reverse=True)
    if not files:
        storage.update_project(pid, status="gagal", name="Unduhan gagal")
        raise RuntimeError("Unduhan selesai tetapi file video tidak ditemukan.")
    storage.update_project(pid, source=files[0].name, status="antre", name=(info.get("title") or "Video")[:120])
    job.update(100, "Unduhan selesai!")
    return files[0].name


def analyze(job: Job, pid: str, language: Optional[str] = None) -> dict:
    """Probe -> preview 480p -> energi audio -> transkrip Whisper -> kandidat klip."""
    src = source_path(pid)
    folder = storage.project_dir(pid)
    storage.update_project(pid, status="dianalisis")

    job.update(0, "Membaca info video...")
    info = media.probe(src)
    storage.update_project(pid, **info)
    _limit_analysis_time(info["duration"])

    preview = media.make_preview(src, folder / "preview.mp4", info["duration"], info["has_audio"],
                                 job.stage(0, 18, "Membuat preview..."))
    job.update(18, "Preview siap (dipakai ulang)." if preview["cached"] else "Preview siap.")
    try:
        media.make_thumbnail(folder / "preview.mp4", folder / "thumb.jpg", info["duration"])
    except Exception:  # thumbnail hanya pelengkap tampilan
        pass
    track_faces(pid, job.stage(18, 25, "Melacak posisi wajah..."))

    energy: list[float] = []
    transcript: dict = {"language": None, "segments": []}
    if info["has_audio"]:
        job.update(26, "Menganalisis audio...")
        energy = media.audio_energy(src)
        progress = job.stage(28, 92, "Membuat transkrip dengan Whisper (bisa beberapa menit)...")
        transcript = transcribe.transcribe(src, info["duration"], language, progress)
    storage.write_data(pid, "energy", energy)
    storage.write_data(pid, "transcript", transcript)

    job.update(95, "Mencari momen terbaik...")
    clips = clipper.suggest(transcript, energy, info["duration"])
    storage.write_data(pid, "clips", clips)
    storage.update_project(pid, status="siap", language=transcript.get("language"))
    job.update(100, "Analisis selesai!")
    return {"clips": len(clips)}


def track_faces(pid: str, progress=None) -> list:
    """Lacak wajah pada preview dan simpan ke faces.json. Gagal = daftar kosong (bingkai manual)."""
    meta = storage.load_project(pid)
    preview = storage.project_dir(pid) / "preview.mp4"
    try:
        data = faces.track(preview, meta["width"], meta["height"], meta.get("duration") or 0, progress)
    except Exception as exc:
        log.warning("Pelacakan wajah gagal, bingkai manual dipakai: %s", exc)
        data = []
    storage.write_data(pid, "faces", data)
    return data


def _limit_analysis_time(duration: float) -> None:
    """Batas waktu analisis sebanding durasi video (dihitung setelah probe)."""
    control = jobcontrol.current()
    if control is not None:
        control.extend_deadline(min(JOB_TIMEOUT_MAX,
                                    ANALYZE_TIMEOUT_BASE + ANALYZE_TIMEOUT_PER_MIN * max(duration, 0) / 60))
        control.check()


def safe_analyze(job: Job, pid: str, language: Optional[str] = None) -> dict:
    previous = storage.load_project(pid).get("status")
    try:
        return analyze(job, pid, language)
    except jobcontrol.JobCancelled as exc:
        # Analisis ulang yang dihentikan: hasil lama (transkrip, preview) masih utuh, kembalikan ke "siap".
        had_result = previous == "siap" and storage.read_data(pid, "transcript") is not None
        if had_result:
            storage.update_project(pid, status="siap")
        else:  # server dimatikan = akan diulang otomatis, bukan dibatalkan pengguna
            storage.update_project(pid, status="antre" if exc.reason == "shutdown" else "batal")
        raise
    except Exception:
        storage.update_project(pid, status="gagal")
        raise


def render(job: Job, pid: str, req: RenderRequest) -> dict:
    if req.encoder == "nvidia" and not resources.gpu_with_free_vram(512):
        # Tidak ada GPU NVIDIA (atau VRAM penuh): langsung CPU, tanpa percobaan yang pasti gagal.
        req = req.model_copy(update={"encoder": "cpu"})
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

    camera = None
    if req.reframe == "auto" and req.aspect != "original":
        track = storage.read_data(pid, "faces")
        if track is None:  # proyek lama yang dianalisis sebelum fitur ini ada
            track = track_faces(pid, job.stage(0, 2, "Melacak posisi wajah..."))
        camera = faces.camera_path(track, req.start, req.end) or None

    punches: list[tuple[float, float]] = []
    layers = []
    overlay_dir = renders / f"tmp_{rid}"
    if req.animations:
        overlay_dir.mkdir(exist_ok=True)
        for item in storage.read_data(pid, "plan", []) or []:
            s, e = max(item["start"], req.start), min(item["end"], req.end)
            if e - s < 0.3:
                continue
            if item["type"] == "zoom":
                punches.append((s - req.start, e - req.start))
            else:
                layers += overlays.render_item(item, overlay_dir, (width, height),
                                               (s - req.start) / req.speed, (e - req.start) / req.speed)

    def run(r: RenderRequest) -> None:
        cmd = build_command(src, out, r, meta, subs, music, renders, camera, punches, layers)
        media.run_ffmpeg(cmd, duration, progress, cwd=renders)

    progress = job.stage(2, 98, "Merender video...")
    try:
        try:
            run(req)
        except media.MediaError:
            if req.encoder == "cpu":
                raise
            # Encoder GPU tidak tersedia di komputer ini: ulangi dengan CPU.
            job.update(2, "Encoder GPU gagal, mengulang dengan CPU...")
            run(req.model_copy(update={"encoder": "cpu"}))
    except BaseException:
        # Gagal, dibatalkan, atau timeout: jangan tinggalkan MP4 setengah jadi.
        out.unlink(missing_ok=True)
        if subs is not None:
            subs.unlink(missing_ok=True)
        raise
    finally:
        shutil.rmtree(overlay_dir, ignore_errors=True)
    if subs is not None:
        subs.unlink(missing_ok=True)  # subtitle sudah dibakar ke video; file .ass tidak diperlukan lagi

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
    storage.add_render(pid, {**item, "user_id": getattr(job, "user_id", None)})
    job.update(100, "Render selesai!")
    return item

