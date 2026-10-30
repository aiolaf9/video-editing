"""Benchmark preview (P4): resep lama vs baru pada input yang sama.

    python bench/preview_bench.py --workdir /tmp/pv --threads 4 --out preview.json
Input dibuat sekali (cache di --workdir):
  heavy  : 1080p30, 5 mnt, ±12 Mbps (setara footage HP) -> transcode
  light  : 720p30, 5 mnt, ±3 Mbps (setara video unduhan/ekspor medsos) -> remux (baru) vs transcode (lama)
  long   : input heavy dengan mode video panjang dipaksa (360p + dekode cepat)
Kualitas: SSIM 60 dtk pertama terhadap sumber yang di-scale ke ukuran preview (lanczos).
"""
import argparse
import json
import os
import re
import resource
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def ff(*args):
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *args], check=True)


def inputs(work: Path) -> dict:
    work.mkdir(parents=True, exist_ok=True)
    out = {"heavy": work / "heavy1080.mp4", "light": work / "light720.mp4"}
    if not out["heavy"].exists():
        ff("-f", "lavfi", "-i", "testsrc2=size=1920x1080:rate=30:duration=300,noise=alls=8:allf=t",
           "-f", "lavfi", "-i", "sine=frequency=440:duration=300", "-c:v", "libx264", "-preset", "veryfast",
           "-crf", "23", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k", "-shortest", str(out["heavy"]))
    if not out["light"].exists():
        ff("-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30:duration=300,noise=alls=3:allf=t",
           "-f", "lavfi", "-i", "sine=frequency=440:duration=300", "-c:v", "libx264", "-preset", "veryfast",
           "-b:v", "2800k", "-maxrate", "3000k", "-bufsize", "6000k", "-pix_fmt", "yuv420p", "-c:a", "aac",
           "-b:a", "128k", "-shortest", str(out["light"]))
    return out


def ssim(preview: Path, src: Path) -> float:
    h = int(subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v", "-show_entries", "stream=width,height",
                            "-of", "csv=p=0", str(preview)], capture_output=True, text=True).stdout.split(",")[1])
    w = int(round(h * 16 / 9 / 2) * 2)
    err = subprocess.run(["ffmpeg", "-hide_banner", "-t", "60", "-i", str(preview), "-t", "60", "-i", str(src), "-lavfi",
                          f"[1:v]scale={w}:{h}:flags=lanczos[r];[0:v]scale={w}:{h}[p];[p][r]ssim", "-f", "null", "-"],
                         capture_output=True, text=True).stderr
    return float(re.search(r"All:([0-9.]+)", err).group(1))


def _ffmpeg_rss_mb() -> float:
    total = 0
    for e in Path("/proc").iterdir():
        try:
            if e.name.isdigit() and (e / "comm").read_text().strip() == "ffmpeg":
                total += next(int(l.split()[1]) for l in (e / "status").read_text().splitlines() if l.startswith("VmRSS:"))
        except (OSError, StopIteration):
            continue
    return total / 1024


def measure(fn):
    import threading

    peak, stop = [0.0], threading.Event()

    def sample():
        while not stop.wait(0.2):
            peak[0] = max(peak[0], _ffmpeg_rss_mb())

    th = threading.Thread(target=sample, daemon=True)
    th.start()
    r0 = resource.getrusage(resource.RUSAGE_CHILDREN)
    t = time.time()
    extra = fn()
    wall = time.time() - t
    stop.set()
    th.join()
    r1 = resource.getrusage(resource.RUSAGE_CHILDREN)
    cpu = (r1.ru_utime - r0.ru_utime) + (r1.ru_stime - r0.ru_stime)
    return {"seconds": round(wall, 2), "cpu_pct": round(100 * cpu / wall) if wall > 0.05 else None,
            "ffmpeg_peak_rss_mb": round(peak[0]), **(extra or {})}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workdir", required=True)
    ap.add_argument("--threads", type=int, default=4, help="anggaran thread job (P3), sama untuk lama & baru")
    ap.add_argument("--out")
    args = ap.parse_args()
    work = Path(args.workdir)
    os.environ.setdefault("VIDEO_APP_DATA", str(work / "data"))
    sys.path.insert(0, str(ROOT))
    from app import media, resources

    resources._job_threads.n = args.threads  # anggaran tetap agar lama & baru dibandingkan setara
    src = inputs(work)

    def old_recipe(s: Path, out: Path):
        """make_preview sebelum P4 (commit 0b39237), dijalankan lewat run_ffmpeg yang sama."""
        info = media.probe(s)
        a = ["-i", str(s), "-map", "0:v:0", "-vf", "scale=-2:'min(480,ih)'", "-c:v", "libx264", "-preset", "veryfast",
             "-crf", "28", "-pix_fmt", "yuv420p", "-map", "0:a:0", "-c:a", "aac", "-b:a", "96k",
             "-movflags", "+faststart", str(out)]
        media.run_ffmpeg(a, info["duration"])

    def new_recipe(s: Path, out: Path, long=False):
        old = media.PREVIEW_LONG_MINUTES
        media.PREVIEW_LONG_MINUTES = 1 if long else old
        try:
            info = media.probe(s)
            meta = out.with_name(out.stem + ".meta.json")
            meta.unlink(missing_ok=True)
            r = media.make_preview(s, out, info["duration"])
            return {"mode": r["mode"], "fast_decode": r["fast_decode"]}
        finally:
            media.PREVIEW_LONG_MINUTES = old

    rows = []
    for name, s in (("heavy", src["heavy"]), ("light", src["light"])):
        o_old, o_new = work / f"{name}_old.mp4", work / f"{name}_new.mp4"
        r_old = measure(lambda: old_recipe(s, o_old))
        r_new = measure(lambda: new_recipe(s, o_new))
        r_cache = measure(lambda: {"cached": media.make_preview(s, o_new, media.probe(s)["duration"])["cached"]})
        for label, r, o in (("lama", r_old, o_old), ("baru", r_new, o_new)):
            r.update(input=name, recipe=label, ssim=round(ssim(o, s), 4),
                     kbps=round(o.stat().st_size * 8 / 300 / 1000))
            rows.append(r)
        r_cache.update(input=name, recipe="baru (cache, analisis ulang)")
        rows.append(r_cache)
    o_long = work / "heavy_long.mp4"
    r_long = measure(lambda: new_recipe(src["heavy"], o_long, long=True))
    r_long.update(input="heavy", recipe="baru, mode video panjang", ssim=round(ssim(o_long, src["heavy"]), 4),
                  kbps=round(o_long.stat().st_size * 8 / 300 / 1000))
    rows.append(r_long)
    base = {r["input"]: r["seconds"] for r in rows if r["recipe"] == "lama"}
    for r in rows:
        r["speedup"] = round(base[r["input"]] / r["seconds"], 2) if r["seconds"] else None
        print(json.dumps(r))
    if args.out:
        Path(args.out).write_text(json.dumps(rows, indent=2))


if __name__ == "__main__":
    main()
