"""Pembungkus FFmpeg/FFprobe: probe, preview, energi audio, dan eksekusi dengan progress."""
import hashlib
import json
import os
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Callable, Optional

import numpy as np

from . import jobcontrol
from .config import (FFMPEG, FFMPEG_STALL_TIMEOUT, FFPROBE, PREVIEW_COPY_MAX_HEIGHT, PREVIEW_COPY_MAX_KBPS,
                     PREVIEW_HEIGHT, PREVIEW_HWACCEL, PREVIEW_LONG_HEIGHT, PREVIEW_LONG_MINUTES, PREVIEW_MAX_KBPS,
                     PROBE_TIMEOUT)

ProgressFn = Optional[Callable[[float], None]]

# Resolusi analisis energi audio (detik per sampel).
ENERGY_STEP = 0.5


class MediaError(RuntimeError):
    pass


# Media dari pengguna tidak tepercaya: FFmpeg hanya boleh membuka berkas lokal (dan pipe), tidak pernah http/rtmp/...
# meskipun berkasnya ternyata playlist HLS/concat yang menyamar. Tidak bergantung pada bawaan versi FFmpeg.
INPUT_PROTOCOLS = ["-protocol_whitelist", "file,pipe"]


def local_inputs(args: list[str]) -> list[str]:
    """Sisipkan INPUT_PROTOCOLS sebelum setiap -i."""
    out: list[str] = []
    for i, arg in enumerate(args):
        if arg == "-i" and args[i - 2:i] != INPUT_PROTOCOLS:
            out += INPUT_PROTOCOLS
        out.append(arg)
    return out


def _probe_raw(path: Path) -> dict:
    cmd = [FFPROBE, "-v", "error", *INPUT_PROTOCOLS, "-print_format", "json", "-show_format", "-show_streams", str(path)]
    proc = jobcontrol.run_capture(cmd, text=True, timeout=PROBE_TIMEOUT)
    if proc.returncode != 0:
        raise MediaError(f"File tidak bisa dibaca sebagai video: {proc.stderr.strip()[-400:]}")
    return json.loads(proc.stdout)


def _rotation(video: dict) -> int:
    for side in video.get("side_data_list", []) or []:
        if "rotation" in side:
            return int(float(side["rotation"]))
    return int(video.get("tags", {}).get("rotate", 0) or 0)


def probe(path: Path) -> dict:
    info = _probe_raw(path)
    video = next((s for s in info.get("streams", []) if s.get("codec_type") == "video"), None)
    audio = next((s for s in info.get("streams", []) if s.get("codec_type") == "audio"), None)
    if video is None:
        raise MediaError("File tidak memiliki stream video.")

    width, height = int(video["width"]), int(video["height"])
    # Video dari HP sering menyimpan rotasi sebagai metadata; FFmpeg memutarnya otomatis
    # saat decode, jadi ukuran efektifnya harus ditukar.
    rotation = _rotation(video)
    if abs(rotation) % 180 == 90:
        width, height = height, width

    duration = float(info.get("format", {}).get("duration") or video.get("duration") or 0)
    fps = 30.0
    rate = video.get("avg_frame_rate") or video.get("r_frame_rate") or "30/1"
    try:
        num, den = rate.split("/")
        if float(den):
            fps = float(num) / float(den)
    except ValueError:
        pass

    return {
        "duration": duration,
        "width": width,
        "height": height,
        "fps": round(fps, 3),
        "has_audio": audio is not None,
    }


def limit_threads(args: list[str]) -> list[str]:
    """Batasi thread FFmpeg (filter & encoder) ke anggaran worker, kecuali pemanggil sudah mengatur sendiri.
    Tanpa ini x264 memakai ±1,5x jumlah core per proses, sehingga dua render bersamaan saling berebut CPU."""
    if "-threads" in args or not args:
        return list(args)
    from . import resources

    n = str(resources.cpu_threads())
    return ["-filter_threads", n, *args[:-1], "-threads", n, args[-1]]


def run_ffmpeg(args: list[str], duration: float = 0, progress: ProgressFn = None, cwd: Optional[Path] = None,
               stall_timeout: Optional[float] = None) -> None:
    """Menjalankan FFmpeg dan melaporkan progress 0..1 berdasarkan out_time.

    - Bisa dihentikan lewat job yang aktif (batal/timeout): proses di-kill, JobCancelled dinaikkan.
    - Bila FFmpeg tidak mengeluarkan laporan kemajuan selama `stall_timeout` detik (mis. input
      macet), proses dihentikan dan SubprocessStalled dinaikkan, sehingga worker tidak tertahan.
    """
    stall_timeout = FFMPEG_STALL_TIMEOUT if stall_timeout is None else stall_timeout
    jobcontrol.check()
    cmd = [FFMPEG, "-y", "-hide_banner", "-nostats", "-progress", "pipe:1", *limit_threads(local_inputs(args))]
    with tempfile.TemporaryFile(mode="w+") as err:
        proc = jobcontrol.popen(cmd, stdout=subprocess.PIPE, stderr=err, text=True, cwd=cwd)
        assert proc.stdout is not None
        last_output = [time.monotonic()]
        stalled = threading.Event()
        finished = threading.Event()

        def watch() -> None:
            while not finished.wait(1.0):
                if time.monotonic() - last_output[0] > stall_timeout:
                    stalled.set()
                    jobcontrol.terminate(proc)
                    return

        watcher = threading.Thread(target=watch, daemon=True)
        watcher.start()
        try:
            for line in proc.stdout:
                last_output[0] = time.monotonic()
                if progress and duration > 0 and line.startswith("out_time_us="):
                    try:
                        seconds = int(line.split("=", 1)[1]) / 1_000_000
                    except ValueError:
                        continue
                    progress(max(0.0, min(1.0, seconds / duration)))
            code = proc.wait()
        finally:
            finished.set()
            if proc.poll() is None:  # keluar karena exception (mis. dibatalkan): pastikan proses mati
                jobcontrol.terminate(proc)
            jobcontrol.release(proc)
        jobcontrol.check()
        if stalled.is_set():
            raise jobcontrol.SubprocessStalled(
                f"FFmpeg tidak ada kemajuan selama {stall_timeout:.0f} detik dan dihentikan.")
        if code != 0:
            err.seek(0)
            tail = err.read()[-1500:]
            raise MediaError(f"FFmpeg gagal (kode {code}):\n{tail}")


# Naikkan bila resep preview berubah: preview lama otomatis dibuat ulang, sisanya dipakai ulang.
PREVIEW_VERSION = 2


def preview_plan(src: Path, duration: float) -> dict:
    """Cara membuat preview untuk sumber ini.

    copy      : sumber sudah ramah browser (H.264 8-bit, AAC/tanpa audio, tanpa rotasi, tinggi & bitrate kecil):
                cukup remux + faststart, tanpa decode/encode. Timestamp identik dengan sumber.
    transcode : H.264 480p (video panjang: 360p + dekode cepat). Audio AAC disalin apa adanya.
    """
    raw = _probe_raw(src)
    video = next((s for s in raw.get("streams", []) if s.get("codec_type") == "video"), {})
    audio = next((s for s in raw.get("streams", []) if s.get("codec_type") == "audio"), None)
    height = int(video.get("height") or 0)
    kbps = int(raw.get("format", {}).get("bit_rate") or 0) / 1000
    audio_copy = audio is not None and audio.get("codec_name") == "aac" and int(audio.get("channels") or 0) <= 2
    copy = (video.get("codec_name") == "h264" and video.get("pix_fmt") in ("yuv420p", "yuvj420p")
            and _rotation(video) == 0 and 0 < height <= PREVIEW_COPY_MAX_HEIGHT
            and 0 < kbps <= PREVIEW_COPY_MAX_KBPS and (audio is None or audio_copy))
    long = bool(PREVIEW_LONG_MINUTES) and duration > PREVIEW_LONG_MINUTES * 60
    return {"version": PREVIEW_VERSION, "mode": "copy" if copy else "transcode",
            "height": None if copy else (PREVIEW_LONG_HEIGHT if long else PREVIEW_HEIGHT),
            "fast_decode": long and not copy, "audio": None if audio is None else ("copy" if audio_copy else "aac"),
            "hwaccel": None if copy or PREVIEW_HWACCEL == "none" else PREVIEW_HWACCEL}


def _preview_key(src: Path, plan: dict) -> str:
    st = src.stat()
    raw = json.dumps({"name": src.name, "size": st.st_size, "mtime": st.st_mtime_ns, "plan": plan}, sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()


def _preview_args(src: Path, plan: dict, out: Path, hwaccel: Optional[str]) -> list[str]:
    if plan["mode"] == "copy":
        args = ["-i", str(src), "-map", "0:v:0", "-c:v", "copy"]
    else:
        from . import resources

        n = resources.cpu_threads()
        # Decoder H.264 memakai frame-threading: thread > core menutup jeda antar-frame (decode 1080p ±15% lebih
        # cepat). Filter & encoder tetap dibatasi anggaran worker (P3).
        inopts = ["-threads", str(2 * n)]
        if plan["fast_decode"]:  # video panjang: lewati deblocking & frame non-referensi (fps lebih rendah)
            inopts += ["-skip_loop_filter", "all", "-skip_frame", "noref"]
        if hwaccel:
            inopts += ["-hwaccel", hwaccel]
        args = [*inopts, "-i", str(src), "-map", "0:v:0", "-vf", f"scale=-2:'min({plan['height']},ih)'",
                "-filter_threads", str(n), "-c:v", "libx264", "-preset", "veryfast", "-crf", "28",
                "-maxrate", f"{PREVIEW_MAX_KBPS}k", "-bufsize", f"{2 * PREVIEW_MAX_KBPS}k", "-pix_fmt", "yuv420p"]
        if plan["fast_decode"]:
            args += ["-fps_mode", "vfr"]
    if plan["audio"] == "copy":
        args += ["-map", "0:a:0", "-c:a", "copy"]
    elif plan["audio"] == "aac":
        args += ["-map", "0:a:0", "-c:a", "aac", "-b:a", "96k"]
    if plan["mode"] != "copy":
        args += ["-threads", str(resources.cpu_threads())]
    return args + ["-movflags", "+faststart", str(out)]


def make_preview(src: Path, dst: Path, duration: float, has_audio: bool = True, progress: ProgressFn = None) -> dict:
    """Preview ringan untuk browser & pelacakan wajah. Dipakai ulang selama sumber dan resepnya sama.

    Render final TIDAK memakai preview: selalu dari video sumber asli.
    """
    plan = preview_plan(src, duration)
    key = _preview_key(src, plan)
    meta_path = dst.with_name(dst.stem + ".meta.json")
    try:
        cached = json.loads(meta_path.read_text()) if dst.exists() else None
    except (OSError, ValueError):
        cached = None
    if cached and cached.get("key") == key:
        if progress:
            progress(1.0)
        return {**plan, "cached": True, "seconds": 0.0}
    # Tulis ke file sementara lalu ganti nama: preview lama tidak rusak bila proses dibatalkan.
    tmp = dst.with_name(f".{dst.stem}.part{dst.suffix}")
    started = time.time()
    try:
        try:
            run_ffmpeg(_preview_args(src, plan, tmp, plan["hwaccel"]), duration, progress)
        except MediaError:
            if not plan["hwaccel"]:
                raise
            # Dekoder hardware gagal (driver/codec): ulangi dengan CPU, hasil tetap sama.
            run_ffmpeg(_preview_args(src, plan, tmp, None), duration, progress)
        os.replace(tmp, dst)
    finally:
        tmp.unlink(missing_ok=True)
    seconds = round(time.time() - started, 2)
    meta_path.write_text(json.dumps({"key": key, "plan": plan, "seconds": seconds, "created": time.time()}))
    return {**plan, "cached": False, "seconds": seconds}


def make_thumbnail(preview: Path, dst: Path, duration: float) -> None:
    """Gambar kecil untuk daftar proyek, diambil dari preview (murah)."""
    if dst.exists() and dst.stat().st_mtime >= preview.stat().st_mtime:
        return
    tmp = dst.with_name(f".{dst.stem}.part.jpg")
    cmd = [FFMPEG, "-v", "error", "-y", "-ss", f"{max(0.0, duration * 0.1):.2f}", *INPUT_PROTOCOLS, "-i", str(preview),
           "-frames:v", "1", "-vf", "scale=320:-2", "-q:v", "5", str(tmp)]
    if jobcontrol.run_capture(cmd, timeout=60).returncode == 0 and tmp.exists():
        os.replace(tmp, dst)
    tmp.unlink(missing_ok=True)


def audio_energy(src: Path) -> list[float]:
    """Kenyaringan audio (dBFS) per ENERGY_STEP detik. Dipakai untuk menilai momen 'ramai'."""
    rate = 8000
    cmd = [FFMPEG, "-v", "error", *INPUT_PROTOCOLS, "-i", str(src), "-vn", "-ac", "1", "-ar", str(rate), "-f", "s16le", "-"]
    proc = jobcontrol.run_capture(cmd)
    if proc.returncode != 0 or not proc.stdout:
        return []
    samples = np.frombuffer(proc.stdout, dtype=np.int16).astype(np.float32) / 32768.0
    block = int(rate * ENERGY_STEP)
    count = len(samples) // block
    if count == 0:
        return []
    blocks = samples[: count * block].reshape(count, block)
    rms = np.sqrt(np.mean(blocks ** 2, axis=1))
    db = 20 * np.log10(np.maximum(rms, 1e-5))
    return [round(float(v), 2) for v in db]


def decode_audio(src: Path, rate: int = 16000) -> np.ndarray:
    """Decode audio ke mono float32 dengan FFmpeg (format masukan yang dipakai Whisper)."""
    cmd = [FFMPEG, "-v", "error", *INPUT_PROTOCOLS, "-i", str(src), "-vn", "-ac", "1", "-ar", str(rate), "-f", "f32le", "-"]
    proc = jobcontrol.run_capture(cmd)
    if proc.returncode != 0:
        raise MediaError(f"Gagal membaca audio: {proc.stderr.decode(errors='replace').strip()[-400:]}")
    return np.frombuffer(proc.stdout, dtype=np.float32).copy()
