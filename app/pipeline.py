"""Alur kerja utama: impor video, analisis (preview + transkrip + kandidat klip), dan render."""
import hashlib
import logging
import shutil
import threading
import time
from pathlib import Path
from typing import Optional

from . import clipper, faces, framing, jobcontrol, media, motion, netpolicy, overlays, recipe, resources, storage, transcribe
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
        # Juga untuk job yang antre sebelum P5.4. Dengan proxy keluar, nama host diperiksa oleh proxy.
        netpolicy.check_import_url(url, trusted, resolve_host=not DOWNLOAD_PROXY)
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


def analyze(job: Job, pid: str, language: Optional[str] = None, quality: Optional[str] = None) -> dict:
    """Probe -> preview 480p -> energi audio -> transkrip Whisper -> kandidat klip."""
    src = source_path(pid)
    folder = storage.project_dir(pid)
    storage.update_project(pid, status="dianalisis")

    job.update(0, "Membaca info video...")
    info = media.probe(src)
    storage.update_project(pid, **info)
    check_duration(info.get("duration") or 0)
    _limit_analysis_time(info["duration"])
    if info.get("has_audio"):
        # Durasi kini diketahui (probe ffprobe, tanpa decode audio): sesuaikan reservasi RAM job sebelum langkah berat.
        # Durasi sudah tersimpan di proyek (update_project di atas), jadi bila job dikembalikan ke antrean karena RAM,
        # klaim berikutnya langsung memakai kebutuhan ini (tanpa siklus klaim-probe-antre berulang).
        resources.ensure_job_memory(job.id, info["duration"], transcribe.model_for(quality))

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
        from . import vocab
        terms = vocab.whisper_terms(storage.load_project(pid).get("unit_id"))   # bawaan kosong (M1.1 eval)
        transcript = transcribe.transcribe(src, info["duration"], language, progress, quality=quality,
                                           vocabulary=terms or None)
    storage.write_data(pid, "energy", energy)
    storage.write_data(pid, "transcript", transcript)

    job.update(95, "Mencari momen terbaik...")
    clips = clipper.suggest(transcript, energy, info["duration"])
    storage.write_data(pid, "clips", clips)
    storage.update_project(pid, status="siap", language=transcript.get("language"))
    job.update(100, "Analisis selesai!")
    return {"clips": len(clips)}


def track_faces(pid: str, progress=None) -> list:
    """Analisis bingkai (M2.1): wajah + area materi -> framing.json, dan faces.json format lama. Gagal = daftar
    kosong (bingkai manual)."""
    meta = storage.load_project(pid)
    preview = storage.project_dir(pid) / "preview.mp4"
    try:
        data = framing.analyze(preview, meta["width"], meta["height"], meta.get("duration") or 0, progress)
    except Exception as exc:
        log.warning("Analisis bingkai gagal, bingkai manual dipakai: %s", exc)
        data = {"v": 1, "fps": faces.SAMPLE_FPS, "samples": []}
    storage.write_data(pid, "framing", data)
    compat = framing.faces_compat(data)
    storage.write_data(pid, "faces", compat)
    return compat


def framing_data(pid: str, job=None) -> dict:
    """framing.json; proyek lama (hanya faces.json) dipakai sebagai data wajah saja tanpa analisis ulang."""
    data = storage.read_data(pid, "framing")
    if data is not None:
        return data
    track = storage.read_data(pid, "faces")
    if track is None:
        track_faces(pid, job.stage(0, 2, "Menganalisis bingkai...") if job else None)
        return storage.read_data(pid, "framing") or {"samples": []}
    return framing.from_faces(track)


def frame_mode(req: RenderRequest) -> str:
    return req.frame_mode or ("face" if req.reframe == "auto" else "manual")


def framing_for(pid: str, req: RenderRequest, job=None) -> Optional[dict]:
    if req.aspect == "original":
        return None
    data = framing_data(pid, job)
    segs = framing.plan(data, req.start, req.end, frame_mode(req), [s.model_dump() for s in req.frame_sections])
    return {"segments": segs, "data": data, "zoom": req.frame_zoom, "distance": req.frame_distance}


class VideoTooLong(RuntimeError):
    """Video melebihi MAX_VIDEO_SECONDS: ditolak sebelum langkah berat (pesan langsung tampil ke pengguna)."""


def check_duration(duration: float) -> None:
    from . import config
    limit = config.MAX_VIDEO_SECONDS
    if limit > 0 and duration > limit:
        raise VideoTooLong(f"Video berdurasi {duration / 60:.1f} menit melebihi batas {limit / 60:.0f} menit. "
                           "Potong videonya menjadi bagian yang lebih pendek, lalu unggah ulang.")


def _limit_analysis_time(duration: float) -> None:
    """Batas waktu analisis sebanding durasi video (dihitung setelah probe)."""
    control = jobcontrol.current()
    if control is not None:
        control.extend_deadline(min(JOB_TIMEOUT_MAX,
                                    ANALYZE_TIMEOUT_BASE + ANALYZE_TIMEOUT_PER_MIN * max(duration, 0) / 60))
        control.check()


def safe_analyze(job: Job, pid: str, language: Optional[str] = None, quality: Optional[str] = None) -> dict:
    previous = storage.load_project(pid).get("status")
    try:
        return analyze(job, pid, language, quality)
    except resources.CapacityWait:
        storage.update_project(pid, status=previous or "antre")   # bukan kegagalan: job kembali ke antrean
        raise
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


def prepare(pid: str, req: RenderRequest, width: int, height: int, workdir: Path, tag: str, job=None) -> dict:
    """Bahan render (subtitle ASS, musik, rencana bingkai, punch-in, grafis) untuk ukuran output width x height.
    Dipakai render final dan pratinjau cepat agar keduanya identik (hanya resolusi yang berbeda)."""
    folder = storage.project_dir(pid)
    meta = storage.load_project(pid)
    frame = framing_for(pid, req, job)
    transcript = None
    sub_opts = None
    if req.subtitles.enabled:
        # Transkrip + koreksi teks dari snapshot resep (atau resep proyek bila permintaan tanpa snapshot).
        transcript = recipe.effective_transcript(pid, fixes=req.text_fixes, seg_edits=req.seg_edits) or {}
        sub_opts = subtitle_options(req.subtitles, pid)
    music = None
    if req.music.file:
        candidate = folder / "music" / storage.safe_filename(req.music.file)
        if not candidate.is_file():
            raise FileNotFoundError("File musik tidak ditemukan. Unggah ulang musiknya.")
        music = candidate
    items = []
    if req.animations:
        items = [i.model_dump() for i in req.overlays] if req.overlays is not None else recipe.effective_overlays(pid)
    out = assemble(req, transcript, sub_opts, items, frame, (meta["width"], meta["height"]), width, height, workdir,
                   tag, folder / "assets")
    out["music"] = music
    return out


def assemble(req: RenderRequest, transcript: Optional[dict], sub_opts: Optional[SubtitleOptions], items: list[dict],
             frame: Optional[dict], src: tuple[int, int], width: int, height: int, workdir: Path, tag: str,
             assets_dir: Optional[Path] = None) -> dict:
    """Inti penyusunan render tanpa akses proyek: subtitle (zona, gaya per bagian, deduplikasi teks besar), safe-zone
    grafis, motion ASS, PNG overlay, dan punch-in. Dipakai prepare() dan bench/m2/studio_demo.py."""
    subs = None
    placement: dict = {}
    fx_specs: list[dict] = []
    if req.animations and items:
        placement = layout_items(req, items, frame, src[0], src[1], width, height)
        fx_specs, _ = fx_plan(req, items, frame)
    if sub_opts is not None:
        sub_opts.zones = subtitle_zones(frame, req, height / max(1, width))
        from . import motionlib
        # M2.5: preset caption Motion Library per rentang didahulukan dari gaya per bagian M2.4
        fx_caps = tuple((f["start"], f["end"], f["params"].get("anim", "karaoke"), motionlib.caption_extra(f))
                        for f in fx_specs if f["kind"] == "caption")
        sub_opts.sections = fx_caps + tuple(((x.start - req.start) / req.speed, (x.end - req.start) / req.speed,
                                             x.style) for x in req.subtitles.sections
                                            if x.end > req.start and x.start < req.end)
        # Hanya teks besar yang BENAR-BENAR tampil boleh menggantikan subtitle (yang disembunyikan safe-zone tidak).
        shown = [i for i in items if (placement.get(i.get("id")) or {}).get("action") != "disembunyikan"]
        sub_opts.suppress = text_windows(req, shown, transcript or {})
        ass = build_ass(transcript or {}, req.start, req.end, width, height, sub_opts, speed=req.speed)
        if "Dialogue:" in ass:
            subs = workdir / f"{tag}.ass"
            subs.write_text(ass, encoding="utf-8")
    punches: list[tuple] = []
    layers = []
    graphics = None
    overlay_dir = workdir / f"tmp_{tag}"
    if req.animations and items:
        overlay_dir.mkdir(exist_ok=True)
        motion_items = []
        for f in fx_specs:                                   # M2.5 judul/grafis/cerita (ASS per kind)
            if f["kind"] not in motion_kinds_ass():
                continue
            pl = placement.get(f["id"])
            if pl and pl.get("parts"):
                for part in (x for x in pl["parts"] if x["action"] != "disembunyikan"):
                    motion_items.append({**f, "type": "fx", "start": part["start"], "end": part["end"], "place": part})
            elif not pl or pl["action"] not in ("disembunyikan", "dilewati"):
                motion_items.append({**f, "type": "fx", "place": pl})
        for item in items:
            if item.get("type") == "fx":
                continue
            pl = placement.get(item.get("id"))
            if pl and pl["action"] == "disembunyikan":
                continue                                    # grafis dekoratif tanpa ruang aman (alasan di editor)
            s, e = max(item["start"], req.start), min(item["end"], req.end)
            if e - s < 0.3:
                continue
            t0, t1 = (s - req.start) / req.speed, (e - req.start) / req.speed
            if item["type"] == "zoom":
                punches.append((s - req.start, e - req.start, float(item.get("strength") or 0.14)))
            elif item["type"] in motion.TYPES:
                if pl and pl.get("parts"):
                    # berpindah posisi di titik pergantian tata letak; bagian lanjutan tanpa animasi masuk ulang
                    for k, part in enumerate(x for x in pl["parts"] if x["action"] != "disembunyikan"):
                        motion_items.append({**item, "start": part["start"], "end": part["end"], "place": part,
                                             "intro": k == 0, "span": (t0, t1)})
                else:
                    motion_items.append({**item, "start": t0, "end": t1, "place": pl})
            else:
                new_layers = overlays.render_item(item, overlay_dir, (width, height), t0, t1, assets_dir=assets_dir)
                if pl and pl.get("parts"):
                    pl = next((x for x in pl["parts"] if x.get("y") is not None), None)
                if pl and pl.get("y") is not None and new_layers:
                    # kata kunci/kartu: geser seluruh grafis ke posisi aman (tanpa mengubah ukuran PNG)
                    from PIL import Image
                    tops = [ly.y for ly in new_layers]
                    bottoms = [ly.y + Image.open(ly.path).height for ly in new_layers]
                    shift = int(pl["y"] * height - (min(tops) + max(bottoms)) / 2)
                    for ly in new_layers:
                        ly.y = max(0, ly.y + shift)
                layers += new_layers
        punches = drop_split_punches(punches, frame, req.start)
        if motion_items:
            graphics = workdir / f"{tag}.motion.ass"
            graphics.write_text(motion.build(motion_items, width, height), encoding="utf-8")
    camera_fx = None
    cam_specs = [f for f in fx_specs if f["kind"] in ("camera", "transition")]
    if cam_specs:
        from . import motionlib
        camera_fx = motionlib.camera_terms(cam_specs, req.speed)
    return {"subs": subs, "frame": frame, "punches": punches, "layers": layers, "overlay_dir": overlay_dir,
            "graphics": graphics, "placement": placement, "camera_fx": camera_fx}


def motion_kinds_ass() -> tuple:
    from . import motionlib
    return motionlib.ASS_KINDS


def fx_plan(req: RenderRequest, items: list[dict], frame: Optional[dict]) -> tuple[list[dict], dict]:
    """M2.5: item fx resep -> spesifikasi render (timeline output) yang lolos guard kombinasi.
    -> (dipakai, {id: alasan dilewati}). Efek kamera (dan whip) tidak dipakai pada bagian Materi + Wajah /
    Materi saja karena memperbesar kanvas akan memotong papan dan panel wajah."""
    from . import motionlib
    specs, skipped = [], {}
    busy = [((s["start"] - req.start) / req.speed, (s["end"] - req.start) / req.speed)
            for s in (frame or {}).get("segments") or [] if s["layout"] in ("split", "material")]
    for it in items:
        if it.get("type") != "fx":
            continue
        s0, e0 = max(it["start"], req.start), min(it["end"], req.end)
        if e0 - s0 < 0.1:
            continue
        spec = motionlib.resolve({**it, "start": (s0 - req.start) / req.speed, "end": (e0 - req.start) / req.speed},
                                 req.motion_level)
        if spec is None:
            skipped[it.get("id")] = "preset tidak dikenal"
            continue
        zooms = spec["kind"] == "camera" or (spec["kind"] == "transition" and spec["params"].get("effect") == "whip")
        if zooms and any(spec["start"] < b and spec["end"] > a for a, b in busy):
            skipped[spec["id"]] = "tidak dipakai pada tata letak Materi + Wajah / Materi (akan memotong papan)"
            continue
        specs.append(spec)
    kept, why = motionlib.guard(specs, req.motion_level)
    skipped.update(why)
    return kept, skipped


def big_text_windows(req: RenderRequest, pid: str, transcript: Optional[dict] = None) -> tuple:
    if not req.animations:
        return ()
    items = [i.model_dump() for i in req.overlays] if req.overlays is not None else recipe.effective_overlays(pid)
    if transcript is None:
        transcript = recipe.effective_transcript(pid, fixes=req.text_fixes, seg_edits=req.seg_edits) or {}
    return text_windows(req, items, transcript)


def text_windows(req: RenderRequest, items: list[dict], transcript: dict) -> tuple:
    """Hook pembuka & kinetic text yang isinya SAMA dengan ucapan pada waktu tampilnya (timeline output). Subtitle
    hanya disembunyikan untuk kata-kata yang benar-benar ekuivalen (lihat subtitles._duplicates_big_text); dialog
    lain, termasuk yang tampil bersamaan, tetap ada."""
    if not req.animations:
        return ()
    from .subtitles import collect_words, norm_tokens, spoken_span
    words = collect_words(transcript, req.start, req.end, req.speed)
    out = []
    from . import motionlib
    for it in items:
        big = it["type"] in ("hook", "kinetic")
        text = it.get("text", "")
        if it["type"] == "fx":                         # M2.5: judul Motion Library juga teks besar
            spec = motionlib.resolve(it, req.motion_level)
            big = bool(spec) and spec["category"] == "titles"
            text = (spec or {}).get("params", {}).get("text", "")
        if big and it["end"] > req.start and it["start"] < req.end:
            t0 = (max(it["start"], req.start) - req.start) / req.speed
            t1 = (min(it["end"], req.end) - req.start) / req.speed
            seq = tuple(norm_tokens(text))
            span = spoken_span(seq, words, t0, t1)
            if span:
                out.append((span[0], span[1], seq, t0, t1))
    return tuple(out)


def motion_layout(pid: str, req: RenderRequest, width: int, height: int, frame: Optional[dict] = None,
                  job=None) -> dict:
    """Safe-zone (app/safezone.py): posisi setiap grafis motion/kata kunci/kartu menghindari wajah, papan, dan
    subtitle pada tata letak yang sedang dipakai. Hasil: {id: {y, scale, action, reason, ...}}."""
    if not req.animations:
        return {}
    if frame is None:
        frame = framing_for(pid, req, job)
    meta = storage.load_project(pid)
    items = [i.model_dump() for i in req.overlays] if req.overlays is not None else recipe.effective_overlays(pid)
    return layout_items(req, items, frame, meta["width"], meta["height"], width, height)


def layout_items(req: RenderRequest, items: list[dict], frame: Optional[dict], src_w: int, src_h: int,
                 width: int, height: int) -> dict:
    """Inti safe-zone tanpa akses proyek (dipakai juga oleh bench/m2/studio_demo.py)."""
    from . import safezone
    subs = None
    if req.subtitles.enabled:
        s = req.subtitles
        y = s.pos_y if s.pos_y is not None else {"top": 0.14, "middle": 0.5}.get(s.position, 0.78)
        seam = 0.56 if subtitle_zones(frame, req, height / max(1, width)) else None
        subs = {"y": y, "seam_y": seam, "size": s.size * (1.4 if s.style == "word" else 1.0)}
    tl = safezone.timeline(frame, req.start, req.speed, width, height, (src_w, src_h),
                           (req.crop_x, req.crop_y, req.frame_zoom), req.frame_distance, subs)
    cand = []
    for it in items:
        s0, e0 = max(it["start"], req.start), min(it["end"], req.end)
        if e0 - s0 >= 0.3 and it["type"] in safezone.PREFERRED:
            cand.append({**it, "start": (s0 - req.start) / req.speed, "end": (e0 - req.start) / req.speed})
    kept, skipped = fx_plan(req, items, frame)
    for f in kept:                                   # M2.5 grafis ASS Motion Library ikut safe-zone
        if f["kind"] in safezone.PREFERRED and f["end"] - f["start"] >= 0.3:
            cand.append({"id": f["id"], "type": f["kind"], "start": f["start"], "end": f["end"],
                         "text": f["params"].get("text", ""), "label": f["params"].get("label", ""),
                         "params": f["params"],
                         "prefer": [f["params"]["pos_y"]] if f["params"].get("pos_y") is not None else []})
    out = safezone.place(cand, tl, width, height)
    for fid, why in skipped.items():
        out[fid] = {"action": "dilewati", "reason": why}
    return out


def drop_split_punches(punches: list[tuple], frame: Optional[dict], start: float) -> list[tuple]:
    """Punch-in zoom tidak dipakai pada bagian Materi + Wajah / Materi saja: memperbesar seluruh kanvas akan
    memotong papan dan panel wajah."""
    if not frame:
        return punches
    busy = [(s["start"] - start, s["end"] - start) for s in frame["segments"] if s["layout"] in ("split", "material")]
    return [p for p in punches if not any(p[0] < b and p[1] > a for a, b in busy)]


def subtitle_zones(frame: Optional[dict], req: RenderRequest, ratio: float) -> tuple:
    """Bagian bertata letak Materi + Wajah (format vertikal): subtitle di garis sambung panel (bukan menutupi
    wajah). Hanya bila pengguna memakai posisi bawaan (bawah, tanpa posisi bebas)."""
    if not frame or ratio < 1.2 or req.subtitles.pos_y is not None or req.subtitles.position != "bottom":
        return ()
    return tuple(((s["start"] - req.start) / req.speed, (s["end"] - req.start) / req.speed, 0.56)
                 for s in frame["segments"] if s["layout"] == "split")


def subtitle_options(s, pid: Optional[str] = None) -> SubtitleOptions:
    terms: tuple = ()
    if s.emphasis and pid:
        try:   # istilah kosakata unit/bawaan = kandidat kata penting (hanya penanda tampilan, teks tidak diubah)
            from . import vocab
            terms = tuple(e["term"] for e in vocab.effective(storage.load_project(pid).get("unit_id")))
        except Exception as exc:
            log.warning("Kosakata untuk sorotan tidak terbaca: %s", exc)
    return SubtitleOptions(style=s.style, font=s.font, size=s.size, color=s.color, highlight=s.highlight,
                           position=s.position, uppercase=s.uppercase, words_per_line=s.words_per_line,
                           outline_color=s.outline_color, outline=s.outline, shadow=s.shadow, box=s.box,
                           box_color=s.box_color, box_opacity=s.box_opacity, pos_y=s.pos_y, emphasis=s.emphasis,
                           emphasis_color=s.emphasis_color, emphasis_terms=terms)


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
    duration = (req.end - req.start) / req.speed
    camera = None
    assets = prepare(pid, req, width, height, renders, rid, job)
    subs, music, frame = assets["subs"], assets["music"], assets["frame"]
    punches, layers, overlay_dir = assets["punches"], assets["layers"], assets["overlay_dir"]

    def run(r: RenderRequest) -> None:
        cmd = build_command(src, out, r, meta, subs, music, renders, camera, punches, layers, frame,
                            graphics_file=assets["graphics"], camera_fx=assets.get("camera_fx"))
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
        if assets["graphics"] is not None:
            assets["graphics"].unlink(missing_ok=True)
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
    exported = export_copy(meta, out, item["title"])
    if exported:
        item["export_path"] = str(exported)
    job.update(100, "Render selesai!")
    return item


def export_copy(meta: dict, out, title: str):
    """Desktop: salin MP4 ke folder ekspor (Videos\\NH AI Clip Studio\\<proyek>\\<judul>.mp4) tanpa menimpa berkas lain.
    Gagal menyalin tidak menggagalkan render (hasil tetap ada di proyek)."""
    from .config import EXPORT_DIR
    if not EXPORT_DIR:
        return None
    try:
        folder = EXPORT_DIR / (_safe_name(meta.get("name") or "Proyek") or "Proyek")
        folder.mkdir(parents=True, exist_ok=True)
        base = _safe_name(title) or "Klip"
        dest, n = folder / f"{base}.mp4", 2
        while dest.exists():
            dest, n = folder / f"{base} ({n}).mp4", n + 1
        shutil.copy2(out, dest)
        return dest
    except OSError as exc:
        log.warning("Salin ke folder ekspor gagal: %s", exc)
        return None


def _safe_name(text: str) -> str:
    """Nama berkas aman di Windows: tanpa \\ / : * ? " < > |, tanpa titik/spasi di ujung, maks 80 karakter."""
    import re
    clean = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', " ", str(text)).strip().strip(".")
    clean = re.sub(r"\s+", " ", clean)[:80].strip()
    if clean.split(".")[0].upper() in _WIN_RESERVED:
        clean = "_" + clean
    return clean


_WIN_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}



# ------------------------------------------------------------------ pratinjau cepat (M2)

PREVIEW_SCALE = 1 / 3          # 9:16 -> 360x640
PREVIEW_MAX_SECONDS = 10.0
PREVIEW_KEEP = 24              # berkas pratinjau tersimpan per proyek (yang terlama dihapus)
_preview_lock = threading.Semaphore(1)     # satu pratinjau pada satu waktu: tidak membebani PC 8 GB


def render_preview(pid: str, req: RenderRequest) -> Path:
    """MP4 pendek beresolusi kecil dari pipeline render yang SAMA (subtitle, bingkai, grafis, warna), untuk
    pratinjau yang konsisten dengan hasil akhir. Disimpan dengan kunci isi permintaan (cache)."""
    meta = storage.load_project(pid)
    req = req.model_copy(update={"end": min(req.end, req.start + PREVIEW_MAX_SECONDS, meta.get("duration") or req.end),
                                 "encoder": "cpu", "normalize_audio": False})
    if req.end - req.start < 0.5:
        raise ValueError("Rentang pratinjau terlalu pendek.")
    folder = storage.project_dir(pid) / "previews"
    folder.mkdir(exist_ok=True)
    key = hashlib.sha256((req.model_dump_json() + str(PREVIEW_SCALE)).encode()).hexdigest()[:20]
    out = folder / f"{key}.mp4"
    if out.is_file():
        out.touch()
        return out
    width, height = output_size(req.aspect, meta["width"], meta["height"])
    width, height = max(2, int(width * PREVIEW_SCALE) // 2 * 2), max(2, int(height * PREVIEW_SCALE) // 2 * 2)
    with _preview_lock:
        assets = prepare(pid, req, width, height, folder, key)
        tmp = folder / f"{key}.part.mp4"
        try:
            cmd = build_command(source_path(pid), tmp, req, meta, assets["subs"], assets["music"], folder, None,
                                assets["punches"], assets["layers"], assets["frame"], out_scale=PREVIEW_SCALE,
                                graphics_file=assets["graphics"], camera_fx=assets.get("camera_fx"))
            media.run_ffmpeg(cmd, (req.end - req.start) / req.speed, None, cwd=folder, stall_timeout=60)
            tmp.replace(out)
        finally:
            tmp.unlink(missing_ok=True)
            for f in (assets["subs"], assets["graphics"]):
                if f is not None:
                    f.unlink(missing_ok=True)
            shutil.rmtree(assets["overlay_dir"], ignore_errors=True)
    old = sorted(folder.glob("*.mp4"), key=lambda p: p.stat().st_mtime)
    for p in old[:-PREVIEW_KEEP]:
        p.unlink(missing_ok=True)
    return out
