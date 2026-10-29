"""Pembungkus FFmpeg/FFprobe: probe, preview, energi audio, dan eksekusi dengan progress."""
import json
import subprocess
import tempfile
from pathlib import Path
from typing import Callable, Optional

import numpy as np

from .config import FFMPEG, FFPROBE

ProgressFn = Optional[Callable[[float], None]]

# Resolusi analisis energi audio (detik per sampel).
ENERGY_STEP = 0.5


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
    # Video dari HP sering menyimpan rotasi sebagai metadata; FFmpeg memutarnya otomatis
    # saat decode, jadi ukuran efektifnya harus ditukar.
    rotation = 0
    for side in video.get("side_data_list", []) or []:
        if "rotation" in side:
            rotation = int(float(side["rotation"]))
    if not rotation and "rotate" in video.get("tags", {}):
        rotation = int(video["tags"]["rotate"])
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


def run_ffmpeg(args: list[str], duration: float = 0, progress: ProgressFn = None, cwd: Optional[Path] = None) -> None:
    """Menjalankan FFmpeg dan melaporkan progress 0..1 berdasarkan out_time."""
    cmd = [FFMPEG, "-y", "-hide_banner", "-nostats", "-progress", "pipe:1", *args]
    with tempfile.TemporaryFile(mode="w+") as err:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=err, text=True, cwd=cwd)
        assert proc.stdout is not None
        for line in proc.stdout:
            if progress and duration > 0 and line.startswith("out_time_us="):
                try:
                    seconds = int(line.split("=", 1)[1]) / 1_000_000
                    progress(max(0.0, min(1.0, seconds / duration)))
                except ValueError:
                    pass
        code = proc.wait()
        if code != 0:
            err.seek(0)
            tail = err.read()[-1500:]
            raise MediaError(f"FFmpeg gagal (kode {code}):\n{tail}")


def make_preview(src: Path, dst: Path, duration: float, has_audio: bool, progress: ProgressFn = None) -> None:
    """Membuat salinan ringan 480p H.264 agar bisa diputar lancar di browser."""
    args = ["-i", str(src), "-map", "0:v:0", "-vf", "scale=-2:'min(480,ih)'",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "28", "-pix_fmt", "yuv420p"]
    if has_audio:
        args += ["-map", "0:a:0", "-c:a", "aac", "-b:a", "96k"]
    args += ["-movflags", "+faststart", str(dst)]
    run_ffmpeg(args, duration, progress)


def audio_energy(src: Path) -> list[float]:
    """Kenyaringan audio (dBFS) per ENERGY_STEP detik. Dipakai untuk menilai momen 'ramai'."""
    rate = 8000
    cmd = [FFMPEG, "-v", "error", "-i", str(src), "-vn", "-ac", "1", "-ar", str(rate), "-f", "s16le", "-"]
    proc = subprocess.run(cmd, capture_output=True)
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
    cmd = [FFMPEG, "-v", "error", "-i", str(src), "-vn", "-ac", "1", "-ar", str(rate), "-f", "f32le", "-"]
    proc = subprocess.run(cmd, capture_output=True)
    if proc.returncode != 0:
        raise MediaError(f"Gagal membaca audio: {proc.stderr.decode(errors='replace').strip()[-400:]}")
    return np.frombuffer(proc.stdout, dtype=np.float32).copy()
