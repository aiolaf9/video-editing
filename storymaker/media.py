"""Pembungkus FFmpeg/FFprobe/OpenCV: probe, decode audio, deteksi pergantian adegan, kualitas gambar."""
import json
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Callable, Optional

import numpy as np

from .config import FFMPEG, FFPROBE

ProgressFn = Optional[Callable[[float], None]]


class MediaError(RuntimeError):
    pass


def probe(path: Path) -> dict:
    cmd = [FFPROBE, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise MediaError(f"File tidak bisa dibaca sebagai video: {proc.stderr.strip()[-400:]}")
    info = json.loads(proc.stdout)
    video = next((s for s in info.get("streams", []) if s.get("codec_type") == "video"), None)
    audio = next((s for s in info.get("streams", []) if s.get("codec_type") == "audio"), None)
    if video is None:
        raise MediaError("File tidak memiliki stream video.")
    width, height = int(video["width"]), int(video["height"])
    rotation = 0
    for side in video.get("side_data_list", []) or []:
        if "rotation" in side:
            rotation = int(float(side["rotation"]))
    if not rotation and "rotate" in video.get("tags", {}):
        rotation = int(video["tags"]["rotate"])
    if abs(rotation) % 180 == 90:  # video HP: rotasi disimpan sebagai metadata
        width, height = height, width
    duration = float(info.get("format", {}).get("duration") or video.get("duration") or 0)
    fps = 30.0
    try:
        num, den = (video.get("avg_frame_rate") or "30/1").split("/")
        if float(den) and float(num):
            fps = float(num) / float(den)
    except ValueError:
        pass
    return {"duration": round(duration, 3), "width": width, "height": height,
            "fps": round(fps, 3), "has_audio": audio is not None}


def run_ffmpeg(args: list[str], duration: float = 0, progress: ProgressFn = None,
               cwd: Optional[Path] = None) -> None:
    """Menjalankan FFmpeg dan melaporkan progress 0..1 berdasarkan out_time."""
    cmd = [FFMPEG, "-y", "-hide_banner", "-nostats", "-progress", "pipe:1", *args]
    with tempfile.TemporaryFile(mode="w+") as err:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=err, text=True, cwd=cwd)
        assert proc.stdout is not None
        for line in proc.stdout:
            if progress and duration > 0 and line.startswith("out_time_us="):
                try:
                    progress(max(0.0, min(1.0, int(line.split("=", 1)[1]) / 1e6 / duration)))
                except ValueError:
                    pass
        code = proc.wait()
        if code != 0:
            err.seek(0)
            raise MediaError(f"FFmpeg gagal (kode {code}):\n{err.read()[-1500:]}")


def decode_audio(src: Path, rate: int = 16000) -> np.ndarray:
    """Audio mono float32 (format masukan Whisper)."""
    cmd = [FFMPEG, "-v", "error", "-i", str(src), "-vn", "-ac", "1", "-ar", str(rate), "-f", "f32le", "-"]
    proc = subprocess.run(cmd, capture_output=True)
    if proc.returncode != 0:
        raise MediaError(f"Gagal membaca audio: {proc.stderr.decode(errors='replace').strip()[-400:]}")
    return np.frombuffer(proc.stdout, dtype=np.float32).copy()


def audio_energy(src: Path, step: float = 0.5) -> list[float]:
    """Kenyaringan (dBFS) per `step` detik."""
    rate = 8000
    try:
        samples = decode_audio(src, rate)
    except MediaError:
        return []
    block = int(rate * step)
    count = len(samples) // block
    if count == 0:
        return []
    blocks = samples[: count * block].reshape(count, block)
    rms = np.sqrt(np.mean(blocks ** 2, axis=1))
    return [round(float(v), 2) for v in 20 * np.log10(np.maximum(rms, 1e-5))]


_PTS = re.compile(r"pts_time:([0-9.]+)")


def scene_cuts(src: Path, threshold: float = 0.32) -> list[float]:
    """Detik-detik terjadinya pergantian adegan (potongan kamera) di dalam satu file."""
    cmd = [FFMPEG, "-hide_banner", "-v", "info", "-i", str(src), "-an",
           "-vf", f"scale=320:-2,select='gt(scene,{threshold})',showinfo", "-f", "null", "-"]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    return sorted({round(float(m.group(1)), 2) for m in _PTS.finditer(proc.stderr)})


def frame_stats(src: Path, fps: float = 2.0) -> list[dict]:
    """Statistik gambar per sampel: ketajaman, kecerahan, gerakan (selisih antar frame)."""
    import cv2

    w, h = 192, 192
    cmd = [FFMPEG, "-v", "error", "-i", str(src), "-an",
           "-vf", f"fps={fps},scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}",
           "-f", "rawvideo", "-pix_fmt", "gray", "-"]
    proc = subprocess.run(cmd, capture_output=True)
    if proc.returncode != 0 or not proc.stdout:
        return []
    frames = np.frombuffer(proc.stdout, dtype=np.uint8)
    n = len(frames) // (w * h)
    frames = frames[: n * w * h].reshape(n, h, w)
    out, prev = [], None
    for i, f in enumerate(frames):
        sharp = float(cv2.Laplacian(f, cv2.CV_64F).var())
        motion = 0.0 if prev is None else float(np.mean(np.abs(f.astype(np.int16) - prev.astype(np.int16))))
        out.append({"t": round(i / fps, 2), "sharp": round(sharp, 1), "bright": round(float(f.mean()), 1),
                    "motion": round(motion, 2)})
        prev = f
    return out


def thumbnail(src: Path, t: float, dst: Path, width: int = 320) -> None:
    run_ffmpeg(["-ss", f"{max(0.0, t):.3f}", "-i", str(src), "-frames:v", "1",
                "-vf", f"scale={width}:-2", "-q:v", "4", str(dst)])


def make_preview(src: Path, dst: Path, duration: float, has_audio: bool, progress: ProgressFn = None) -> None:
    """Salinan ringan 480p agar bisa diputar lancar di browser."""
    args = ["-i", str(src), "-map", "0:v:0", "-vf", "scale=-2:'min(480,ih)'",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "28", "-pix_fmt", "yuv420p"]
    if has_audio:
        args += ["-map", "0:a:0", "-c:a", "aac", "-b:a", "96k"]
    args += ["-movflags", "+faststart", str(dst)]
    run_ffmpeg(args, duration, progress)
