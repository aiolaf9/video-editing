"""Menyusun perintah FFmpeg untuk merender satu klip: crop, zoom, warna, subtitle, musik."""
import os
from pathlib import Path
from typing import Optional

from .config import FONTS_DIR
from .schemas import RenderRequest

OUTPUT_SIZES = {
    "9:16": (1080, 1920),
    "1:1": (1080, 1080),
    "4:5": (1080, 1350),
    "16:9": (1920, 1080),
}

COLOR_PRESETS = {
    "none": [],
    "vivid": ["eq=saturation=1.35:contrast=1.08"],
    "cinematic": ["curves=preset=medium_contrast",
                  "colorbalance=rs=-0.05:bs=0.08:rh=0.06:bh=-0.04",
                  "eq=saturation=0.9"],
    "warm": ["colorbalance=rs=0.08:gs=0.02:bs=-0.08:rm=0.05:bm=-0.05"],
    "cool": ["colorbalance=rs=-0.06:bs=0.08:bm=0.05"],
    "bw": ["hue=s=0", "eq=contrast=1.15"],
    "vintage": ["curves=preset=vintage", "eq=saturation=0.85"],
}

ENCODERS = {
    "cpu": ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20"],
    "nvidia": ["-c:v", "h264_nvenc", "-preset", "p4", "-cq", "21"],
    "intel": ["-c:v", "h264_qsv", "-global_quality", "22"],
    "amd": ["-c:v", "h264_amf", "-quality", "balanced", "-rc", "cqp", "-qp_i", "20", "-qp_p", "22"],
    "mac": ["-c:v", "h264_videotoolbox", "-b:v", "8M"],
}

FADE = 0.35


def _even(v: float) -> int:
    return max(2, int(v) // 2 * 2)


def output_size(aspect: str, src_w: int, src_h: int) -> tuple[int, int]:
    if aspect in OUTPUT_SIZES:
        return OUTPUT_SIZES[aspect]
    scale = min(1.0, 1920 / max(src_w, src_h))
    return _even(src_w * scale), _even(src_h * scale)


def crop_box(aspect: str, src_w: int, src_h: int, crop_x: float, crop_y: float) -> tuple[int, int, int, int]:
    """Kotak crop (w, h, x, y) terbesar dengan rasio target di dalam frame sumber."""
    if aspect not in OUTPUT_SIZES:
        return src_w, src_h, 0, 0
    ow, oh = OUTPUT_SIZES[aspect]
    target = ow / oh
    if src_w / src_h > target:
        cw, ch = _even(src_h * target), _even(src_h)
    else:
        cw, ch = _even(src_w), _even(src_w / target)
    cw, ch = min(cw, src_w), min(ch, src_h)
    x = int((src_w - cw) * crop_x)
    y = int((src_h - ch) * crop_y)
    return cw, ch, x, y


def _atempo(speed: float) -> list[str]:
    return [] if abs(speed - 1.0) < 1e-3 else [f"atempo={speed:.4f}"]


def _filter_path(path: Path, cwd: Path) -> str:
    """Path untuk opsi filter FFmpeg. Relatif bila bisa (menghindari 'C:' di Windows)."""
    try:
        p = os.path.relpath(path, cwd)
    except ValueError:  # beda drive di Windows
        p = str(path)
    p = p.replace("\\", "/").replace(":", r"\:").replace("'", r"\'")
    return f"'{p}'"


def build_command(src: Path, out: Path, req: RenderRequest, info: dict,
                  subs_file: Optional[Path], music_file: Optional[Path], workdir: Path) -> list[str]:
    """Mengembalikan argumen FFmpeg (tanpa 'ffmpeg -y' di depannya)."""
    src_w, src_h = info["width"], info["height"]
    in_dur = req.end - req.start
    out_dur = in_dur / req.speed
    ow, oh = output_size(req.aspect, src_w, src_h)
    cw, ch, cx, cy = crop_box(req.aspect, src_w, src_h, req.crop_x, req.crop_y)

    args = ["-ss", f"{req.start:.3f}", "-t", f"{in_dur:.3f}", "-i", str(src)]
    if music_file is not None:
        args += ["-stream_loop", "-1", "-t", f"{out_dur:.3f}", "-i", str(music_file)]

    # ---- video ----
    v = []
    if (cw, ch) != (src_w, src_h):
        v.append(f"crop={cw}:{ch}:{cx}:{cy}")
    if req.zoom == "slow":
        # Zoom-in perlahan 12% sepanjang klip (efek "Ken Burns").
        z = f"(1+0.12*t/{out_dur:.3f})"
        v.append(f"scale=w='trunc({ow}*{z}/2)*2':h='trunc({oh}*{z}/2)*2':eval=frame:flags=bicubic")
        v.append(f"crop={ow}:{oh}")
    else:
        v.append(f"scale={ow}:{oh}:flags=lanczos")
    if abs(req.speed - 1.0) >= 1e-3:
        v.append(f"setpts=PTS/{req.speed:.4f}")
    v += COLOR_PRESETS[req.color]
    if req.brightness or req.contrast != 1.0 or req.saturation != 1.0:
        v.append(f"eq=brightness={req.brightness:.3f}:contrast={req.contrast:.3f}:saturation={req.saturation:.3f}")
    if req.fade and out_dur > FADE * 3:
        v.append(f"fade=t=in:st=0:d={FADE}")
        v.append(f"fade=t=out:st={out_dur - FADE:.3f}:d={FADE}")
    if subs_file is not None:
        sub = f"ass={_filter_path(subs_file, workdir)}"
        if FONTS_DIR.is_dir():
            sub += f":fontsdir={_filter_path(FONTS_DIR, workdir)}"
        v.append(sub)
    v += ["setsar=1", "format=yuv420p"]
    graph = [f"[0:v]{','.join(v)}[vout]"]

    # ---- audio ----
    a_fade = [f"afade=t=in:st=0:d={FADE}", f"afade=t=out:st={out_dur - FADE:.3f}:d={FADE}"] \
        if req.fade and out_dur > FADE * 3 else []
    voice = None
    if info.get("has_audio"):
        graph.append(f"[0:a]{','.join(['aresample=48000', *_atempo(req.speed), *a_fade]) }[voice]")
        voice = "[voice]"

    music = None
    if music_file is not None:
        m_fade = [f"afade=t=out:st={max(0.0, out_dur - 1.5):.3f}:d={min(1.5, out_dur):.3f}"]
        graph.append(f"[1:a]aresample=48000,volume={req.music.volume:.3f},{','.join(m_fade)}[music]")
        music = "[music]"

    final = None
    if voice and music:
        if req.music.ducking:
            graph.append("[voice]asplit=2[v1][vsc]")
            graph.append("[music][vsc]sidechaincompress=threshold=0.02:ratio=10:attack=15:release=400[duck]")
            graph.append("[v1][duck]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[mix]")
        else:
            graph.append("[voice][music]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[mix]")
        final = "[mix]"
    else:
        final = voice or music

    if final and req.normalize_audio:
        graph.append(f"{final}loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000[aout]")
        final = "[aout]"

    args += ["-filter_complex", ";".join(graph), "-map", "[vout]"]
    if final:
        args += ["-map", final, "-c:a", "aac", "-b:a", "192k", "-ar", "48000"]
    else:
        args += ["-an"]
    args += ENCODERS[req.encoder]
    args += ["-t", f"{out_dur:.3f}", "-movflags", "+faststart", str(out)]
    return args
