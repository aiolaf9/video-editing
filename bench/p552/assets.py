"""P5.5.2-A: aset benchmark media sintetis dengan penanda sinkronisasi yang dapat dideteksi otomatis.

Setiap aset: latar gelap, konten bergerak (testsrc2) di tengah, FLASH putih satu frame penuh setiap PERIOD detik
(t = 0, 2, 4, ...) dan BEEP 1 kHz 40 ms pada waktu yang sama. Dari output (preview/render) waktu flash & beep
dideteksi ulang untuk mengukur offset & drift audio-video. Bukan media nyata: lihat batasan di RESULTS_P5_5_2.md.

    python bench/p552/assets.py <folder-keluaran> [--long-seconds 1800]
"""
import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

PERIOD = 2.0
BEEP = 0.04

# nama: (container, lebar, tinggi, fps (str ffmpeg), durasi, kanal audio, sample rate, vcodec, acodec, catatan)
MATRIX = {
    "mp4_h264_aac_1080p30_16x9_stereo": ("mp4", 1920, 1080, "30", 60, 2, 48000, "libx264", "aac", "acuan"),
    "mov_h264_aac_1080p30_16x9": ("mov", 1920, 1080, "30", 60, 2, 48000, "libx264", "aac", "MOV"),
    "mp4_h264_1080x1920_30_9x16": ("mp4", 1080, 1920, "30", 60, 2, 48000, "libx264", "aac", "vertikal"),
    "mp4_h264_720p25_mono_44k": ("mp4", 1280, 720, "25", 60, 1, 44100, "libx264", "aac", "mono 44,1 kHz"),
    "mp4_h264_1080p23976": ("mp4", 1920, 1080, "24000/1001", 60, 2, 48000, "libx264", "aac", "23,976 fps"),
    "mp4_h264_1080p60": ("mp4", 1920, 1080, "60", 60, 2, 48000, "libx264", "aac", "60 fps"),
    "mp4_hevc_aac_1080p30": ("mp4", 1920, 1080, "30", 60, 2, 48000, "libx265", "aac", "HEVC"),
    "webm_vp9_opus_720p30": ("webm", 1280, 720, "30", 60, 2, 48000, "libvpx-vp9", "libopus", "WebM"),
    "mp4_h264_vfr": ("mp4", 1280, 720, "vfr", 60, 2, 48000, "libx264", "aac", "VFR (interval 1/60 & 2/60 dtk, rata-rata 36 fps)"),
}


def ffmpeg(args, cwd=None):
    subprocess.run(["ffmpeg", "-hide_banner", "-v", "error", "-y", *args], check=True, cwd=cwd)


def make(out: Path, container, w, h, fps, dur, ch, rate, vcodec, acodec):
    rate_v = "60" if fps == "vfr" else fps
    box = int(min(w, h) * 0.4) // 2 * 2
    flash = f"drawbox=t=fill:c=white:enable='lt(mod(t\\,{PERIOD})\\,1/({rate_v}))'"
    vf = (f"color=c=0x101010:s={w}x{h}:r={rate_v}:d={dur}[bg];"
          f"testsrc2=s={box}x{box}:r={rate_v}:d={dur}[fg];"
          f"[bg][fg]overlay=(W-w)/2:(H-h)/2,{flash}[v]")
    layout = "mono" if ch == 1 else "stereo"
    beep = f"0.5*sin(2*PI*1000*t)*lt(mod(t\\,{PERIOD})\\,{BEEP})"
    af = f"aevalsrc='{beep}':s={rate}:d={dur}:c={layout}[a]"
    venc = {"libx264": ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p"],
            "libx265": ["-c:v", "libx265", "-preset", "fast", "-crf", "24", "-tag:v", "hvc1", "-pix_fmt", "yuv420p"],
            "libvpx-vp9": ["-c:v", "libvpx-vp9", "-b:v", "2M", "-deadline", "realtime", "-cpu-used", "8"]}[vcodec]
    aenc = ["-c:a", acodec, "-b:a", "128k"]
    tmp = out.with_suffix(".cfr" + out.suffix) if fps == "vfr" else out
    ffmpeg(["-filter_complex", f"{vf};{af}", "-map", "[v]", "-map", "[a]", *venc, *aenc,
            *(["-movflags", "+faststart"] if container in ("mp4", "mov") else []), str(tmp)])
    if fps == "vfr":
        # VFR: dari 60 fps buang frame ke-1 & ke-3 dari tiap 5 (interval 1/60, 2/60, 2/60 dtk) dengan timestamp
        # ASLI dipertahankan, sehingga flash (frame kelipatan 120) tetap ada dan tetap sejajar dengan beep.
        ffmpeg(["-i", str(tmp), "-vf", "select='not(eq(mod(n\\,5)\\,1)+eq(mod(n\\,5)\\,3))'",
                "-fps_mode", "vfr", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
                "-c:a", "copy", str(out)])
        tmp.unlink()


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--long-seconds", type=int, default=1800)
    ap.add_argument("--only", default="")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    matrix = dict(MATRIX)
    if a.long_seconds:
        matrix["mp4_h264_aac_720p30_long"] = ("mp4", 1280, 720, "30", a.long_seconds, 2, 48000, "libx264", "aac",
                                              f"panjang {a.long_seconds} dtk")
    mpath = out / "manifest.json"
    manifest = json.loads(mpath.read_text()) if mpath.exists() else {}
    for name, spec in matrix.items():
        if a.only and name not in a.only.split(","):
            continue
        path = out / f"{name}.{spec[0]}"
        if not path.exists():
            print("membuat", path.name, file=sys.stderr, flush=True)
            make(path, *spec[:9])
        manifest[name] = {"file": path.name, "container": spec[0], "size": [spec[1], spec[2]], "fps": spec[3],
                          "duration": spec[4], "channels": spec[5], "rate": spec[6], "vcodec": spec[7],
                          "acodec": spec[8], "note": spec[9], "bytes": path.stat().st_size, "sha256": sha256(path),
                          "markers": {"period": PERIOD, "beep": BEEP}, "license": "sintetis (dibuat FFmpeg)"}
    mpath.write_text(json.dumps(manifest, indent=1))
    print(json.dumps({k: v["file"] for k, v in manifest.items()}, indent=1))


if __name__ == "__main__":
    main()
