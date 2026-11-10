"""M2.1: MP4 pembanding Smart Framing dari satu video (lewat build_command aplikasi, tanpa subtitle).

  sebelum.mp4        perilaku M1: crop 9:16 mengikuti posisi x wajah
  otomatis.mp4       M2 mode Otomatis (wajah / materi+wajah / materi per bagian)
  wajah.mp4, materi.mp4, manual.mp4
  lembar.png         lembar kontak (1 frame per 2 dtk per varian) + rencana.json

    python bench/m2/framing_compare.py video.mp4 keluar/ [--start 0 --end 24]
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def main():
    from app import faces, framing, media
    from app.render import build_command
    from app.schemas import RenderRequest
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("out")
    ap.add_argument("--start", type=float, default=0)
    ap.add_argument("--end", type=float, default=0)
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    src = Path(a.video).resolve()
    info = media.probe(src)
    end = a.end or info["duration"]
    preview = out / "preview480.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-vf", "scale=-2:480", "-an", str(preview)],
                   check=True)
    data = framing.analyze(preview, info["width"], info["height"], info["duration"])
    (out / "framing.json").write_text(json.dumps(data))
    plans = {}
    variants = {"sebelum": None, "otomatis": "auto", "wajah": "face", "materi": "material", "manual": "manual"}
    for name, mode in variants.items():
        req = RenderRequest(start=a.start, end=end, aspect="9:16", subtitles={"enabled": False}, fade=False,
                            normalize_audio=False, animations=False)
        camera, frame = None, None
        if mode is None:
            camera = faces.camera_path(framing.faces_compat(data), a.start, end) or None
        else:
            segs = framing.plan(data, a.start, end, mode)
            plans[name] = framing.public_plan(segs)
            frame = {"segments": segs, "data": data, "zoom": 0.0, "distance": "auto"}
        cmd = build_command(src, out / f"{name}.mp4", req, info, None, None, out, camera, [], [], frame)
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *cmd], check=True, cwd=out)
        print(name, "ok", flush=True)
    (out / "rencana.json").write_text(json.dumps(plans, indent=1, ensure_ascii=False))
    # lembar kontak: baris = varian, kolom = detik 1,3,5,...
    rows = []
    times = [t + 1 for t in range(int(a.start), int(end) - 1, 3)]
    for name in variants:
        tiles = []
        for t in times:
            png = out / f"_{name}_{t}.png"
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", str(t - a.start), "-i", str(out / f"{name}.mp4"),
                            "-frames:v", "1", "-vf", "scale=180:320", str(png)], check=True)
            tiles.append(png)
        rows.append(tiles)
    from PIL import Image, ImageDraw
    sheet = Image.new("RGB", (len(times) * 184 + 90, len(rows) * 324), (30, 30, 30))
    d = ImageDraw.Draw(sheet)
    for r, (name, tiles) in enumerate(zip(variants, rows)):
        d.text((6, r * 324 + 150), name, fill=(255, 255, 255))
        for c, png in enumerate(tiles):
            sheet.paste(Image.open(png), (90 + c * 184, r * 324))
            if r == 0:
                d.text((90 + c * 184 + 4, 4), f"{times[c]}s", fill=(255, 255, 0))
            png.unlink()
    sheet.save(out / "lembar.png")


if __name__ == "__main__":
    main()
