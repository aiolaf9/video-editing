"""Render timeline menjadi satu video vertikal 1080x1920.

Tahap 1: setiap potongan dirender ke format seragam (1080x1920, 30 fps, AAC 48 kHz) termasuk B-roll.
Tahap 2: potongan digabung dengan transisi (xfade + acrossfade), subtitle dibakar, musik latar
         di-ducking otomatis, dan loudness dinormalisasi ke standar media sosial (-14 LUFS).
"""
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from . import faces, media
from .config import FONTS_DIR, OUT_FPS, OUT_H, OUT_W
from .timeline import Segment, Timeline

TRANSITIONS = {"fade": "fade", "dissolve": "dissolve", "fadeblack": "fadeblack", "slide": "slideleft",
               "smooth": "smoothup", "none": None}


@dataclass
class RenderOptions:
    framing: str = "auto"         # auto | crop | blur
    transition: str = "fade"
    music: Optional[Path] = None
    music_volume: float = 0.12
    normalize: bool = True
    crf: int = 20


def _even(v: float) -> int:
    return max(2, int(v) // 2 * 2)


def frame_filter(info: dict, cx: Optional[float], framing: str) -> str:
    """Filter FFmpeg yang mengubah sumber apa pun menjadi 1080x1920.

    cx: pusat wajah (0..1) atau None bila tidak ada wajah terdeteksi.
    auto: crop mengikuti wajah bila ada; tanpa wajah (pemandangan lebar) memakai latar blur
          agar isi gambar tidak terpotong.
    """
    w, h = info["width"], info["height"]
    target = OUT_W / OUT_H
    mode = framing
    if mode == "auto":
        mode = "crop" if cx is not None else "blur"
    if cx is None:
        cx = 0.5
    if w / h <= target + 0.01:  # sudah vertikal/lebih tinggi: isi penuh
        return (f"scale={OUT_W}:{OUT_H}:force_original_aspect_ratio=increase:flags=lanczos,"
                f"crop={OUT_W}:{OUT_H}")
    if mode == "blur":
        return (f"split=2[bg][fg];[bg]scale={OUT_W}:{OUT_H}:force_original_aspect_ratio=increase,"
                f"crop={OUT_W}:{OUT_H},boxblur=20:2,eq=brightness=-0.08[bgb];"
                f"[fg]scale={OUT_W}:-2:flags=lanczos[fgs];[bgb][fgs]overlay=(W-w)/2:(H-h)/2")
    cw = min(w, _even(h * target))
    x = int(min(max(cx * w - cw / 2, 0), w - cw))
    return f"crop={cw}:{h}:{x}:0,scale={OUT_W}:{OUT_H}:flags=lanczos"


def _chain(label_in: str, filt: str, label_out: str) -> str:
    """Bungkus filter (yang mungkin berisi split bertingkat) menjadi rantai berlabel."""
    return f"{label_in}{filt},fps={OUT_FPS},setsar=1,format=yuv420p{label_out}"


def segment_command(seg: Segment, paths: dict[str, Path], infos: dict[str, dict], tracks: dict[str, list],
                    out: Path, opts: RenderOptions) -> list[str]:
    dur = seg.duration
    info = infos[seg.clip_id]
    args = ["-ss", f"{seg.src_start:.3f}", "-t", f"{dur:.3f}", "-i", str(paths[seg.clip_id])]
    for ov in seg.overlays:
        args += ["-ss", f"{ov.src_start:.3f}", "-t", f"{ov.duration:.3f}", "-i", str(paths[ov.clip_id])]

    graph = []
    cx = faces.center_x(tracks.get(seg.clip_id, []), seg.src_start, seg.src_end, None)
    graph.append(_chain("[0:v]", frame_filter(info, cx, opts.framing), "[base0]"))
    cur = "[base0]"
    for i, ov in enumerate(seg.overlays, start=1):
        ocx = faces.center_x(tracks.get(ov.clip_id, []), ov.src_start, ov.src_start + ov.duration, None)
        graph.append(_chain(f"[{i}:v]", frame_filter(infos[ov.clip_id], ocx, opts.framing), f"[o{i}]"))
        graph.append(f"[o{i}]setpts=PTS-STARTPTS+{ov.at:.3f}/TB[od{i}]")
        graph.append(f"{cur}[od{i}]overlay=0:0:eof_action=pass:"
                     f"enable='between(t,{ov.at:.3f},{ov.at + ov.duration:.3f})'[base{i}]")
        cur = f"[base{i}]"
    graph.append(f"{cur}trim=duration={dur:.3f},setpts=PTS-STARTPTS[vout]")

    if info.get("has_audio"):
        graph.append(f"[0:a]aresample=48000,aformat=channel_layouts=stereo,"
                     f"afade=t=in:st=0:d=0.04,afade=t=out:st={max(0.0, dur - 0.08):.3f}:d=0.08,"
                     f"apad,atrim=duration={dur:.3f}[aout]")
    else:
        graph.append(f"anullsrc=r=48000:cl=stereo,atrim=duration={dur:.3f}[aout]")

    return [*args, "-filter_complex", ";".join(graph), "-map", "[vout]", "-map", "[aout]",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "16", "-r", str(OUT_FPS),
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-t", f"{dur:.3f}", str(out)]


def _filter_path(path: Path, cwd: Path) -> str:
    try:
        p = os.path.relpath(path, cwd)
    except ValueError:  # beda drive (Windows)
        p = str(path)
    p = p.replace("\\", "/").replace(":", r"\:").replace("'", r"\'")
    return f"'{p}'"


def final_command(parts: list[Path], durations: list[float], timeline: Timeline, subs: Optional[Path],
                  out: Path, opts: RenderOptions, workdir: Path) -> list[str]:
    args = []
    for p in parts:
        args += ["-i", str(p)]
    n = len(parts)
    total = timeline.duration
    if opts.music is not None:
        args += ["-stream_loop", "-1", "-t", f"{total:.3f}", "-i", str(opts.music)]

    graph = []
    trans = TRANSITIONS.get(opts.transition)
    T = timeline.transition if trans else 0.0
    if n == 1:
        v, a = "[0:v]", "[0:a]"
    elif T > 0:
        v, a, offset = "[0:v]", "[0:a]", 0.0
        for i in range(1, n):
            offset += durations[i - 1] - T
            graph.append(f"{v}[{i}:v]xfade=transition={trans}:duration={T:.3f}:offset={offset:.3f}[vx{i}]")
            graph.append(f"{a}[{i}:a]acrossfade=d={T:.3f}:c1=tri:c2=tri[ax{i}]")
            v, a = f"[vx{i}]", f"[ax{i}]"
    else:
        ins = "".join(f"[{i}:v][{i}:a]" for i in range(n))
        graph.append(f"{ins}concat=n={n}:v=1:a=1[vc][ac]")
        v, a = "[vc]", "[ac]"

    vf = [f"fade=t=in:st=0:d=0.25", f"fade=t=out:st={max(0.0, total - 0.5):.3f}:d=0.5"]
    if subs is not None:
        sub = f"ass={_filter_path(subs, workdir)}"
        if FONTS_DIR.is_dir():
            sub += f":fontsdir={_filter_path(FONTS_DIR, workdir)}"
        vf.append(sub)
    vf += ["format=yuv420p"]
    graph.append(f"{v}{','.join(vf)}[vout]")

    if opts.music is not None:
        graph.append(f"[{n}:a]aresample=48000,aformat=channel_layouts=stereo,volume={opts.music_volume:.3f},"
                     f"afade=t=out:st={max(0.0, total - 1.5):.3f}:d=1.5[mus]")
        graph.append(f"{a}asplit=2[voice][sc]")
        graph.append("[mus][sc]sidechaincompress=threshold=0.03:ratio=8:attack=20:release=500[duck]")
        graph.append("[voice][duck]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[mix]")
        a = "[mix]"
    if opts.normalize:
        graph.append(f"{a}loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000[aout]")
    else:
        graph.append(f"{a}anull[aout]")

    return [*args, "-filter_complex", ";".join(graph), "-map", "[vout]", "-map", "[aout]",
            "-c:v", "libx264", "-preset", "medium", "-crf", str(opts.crf), "-profile:v", "high",
            "-r", str(OUT_FPS), "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
            "-t", f"{total:.3f}", "-movflags", "+faststart", str(out)]


def render(timeline: Timeline, paths: dict[str, Path], infos: dict[str, dict], tracks: dict[str, list],
           subs_ass: Optional[str], out: Path, workdir: Path, opts: RenderOptions,
           progress: Optional[Callable[[float, str], None]] = None) -> Path:
    workdir.mkdir(parents=True, exist_ok=True)
    parts, durations = [], []
    n = len(timeline.segments)
    if n == 0:
        raise media.MediaError("Timeline kosong: tidak ada potongan untuk dirender.")
    for i, seg in enumerate(timeline.segments):
        part = workdir / f"seg_{i:03d}.mp4"
        if progress:
            progress(0.8 * i / n, f"Memotong bagian {i + 1}/{n}...")
        media.run_ffmpeg(segment_command(seg, paths, infos, tracks, part, opts), seg.duration)
        parts.append(part)
        durations.append(seg.duration)
    subs = None
    if subs_ass:
        subs = workdir / "subtitles.ass"
        subs.write_text(subs_ass, encoding="utf-8")
    if progress:
        progress(0.8, "Menggabungkan, transisi, subtitle, dan audio...")
    cmd = final_command(parts, durations, timeline, subs, out, opts, workdir)
    media.run_ffmpeg(cmd, timeline.duration,
                     (lambda p: progress(0.8 + 0.2 * p, "Render akhir...")) if progress else None, cwd=workdir)
    for p in parts:
        p.unlink(missing_ok=True)
    return out
