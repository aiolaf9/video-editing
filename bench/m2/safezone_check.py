"""Bukti safe-zone dari MP4 (bukan dari rencana): piksel grafis = selisih antara MP4 bergrafis dan MP4 "hanya-subtitle"
(sumber, bingkai & subtitle sama). Setiap 0,25 dtk diukur berapa persen area WAJAH, PAPAN, dan SUBTITLE yang tertutup
grafis. Area dihitung dengan geometri bingkai aplikasi (app/framing.occupied_rects) dari framing.json yang sama.

    python bench/m2/safezone_check.py <folder studio_demo --safezone> [--end 24]
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
W, H = 270, 480


def frames(path: Path, end: float, step: float) -> list[np.ndarray]:
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-t", str(end), "-vf", f"fps={1 / step},scale={W}:{H}",
                          "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True, check=True).stdout
    n = len(raw) // (W * H * 3)
    return [np.frombuffer(raw[i * W * H * 3:(i + 1) * W * H * 3], np.uint8).reshape(H, W, 3).astype(np.int16)
            for i in range(n)]


def main():
    from app import framing, safezone
    ap = argparse.ArgumentParser()
    ap.add_argument("folder")
    ap.add_argument("--end", type=float, default=24)
    ap.add_argument("--step", type=float, default=0.25)
    ap.add_argument("--strict", action="store_true", help="gagal bila safe-zone menutup wajah/subtitle > 2 % atau papan > 5 %")
    a = ap.parse_args()
    f = Path(a.folder)
    data = json.loads((f / "framing.json").read_text())
    frame = {"segments": framing.plan(data, 0, a.end, "auto"), "data": data, "zoom": 0.0, "distance": "auto"}
    info = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
                           "-of", "csv=p=0", str(f / "hanya-subtitle.mp4")], capture_output=True, text=True).stdout
    tl = safezone.timeline(frame, 0, 1.0, 1080, 1920, (1920, 1080), (0.5, 0.5, 0.0), "auto",
                           {"y": 0.76, "seam_y": 0.56, "size": 1.0})
    base = frames(f / "hanya-subtitle.mp4", a.end, a.step)
    report = {"output": info.strip(), "step": a.step}
    for v in ("motion-tanpa-safezone", "motion-safezone"):
        frs = frames(f / f"{v}.mp4", a.end, a.step)
        worst = {"wajah": 0.0, "papan": 0.0, "subtitle": 0.0}
        hits = {"wajah": 0, "papan": 0, "subtitle": 0}
        detail = []
        for i, (x, b) in enumerate(zip(frs, base)):
            t = i * a.step
            mask = np.abs(x - b).max(axis=2) > 48            # piksel yang diubah grafis
            seg = next((s for s in tl if s["t0"] <= t < s["t1"]), tl[-1])
            row = {"t": t, "layout": seg["layout"]}
            for r in seg["rects"]:
                x0, y0 = int(r["rect"][0] * W), int(r["rect"][1] * H)
                x1, y1 = int((r["rect"][0] + r["rect"][2]) * W), int((r["rect"][1] + r["rect"][3]) * H)
                cov = float(mask[y0:y1, x0:x1].mean()) if y1 > y0 and x1 > x0 else 0.0
                k = r["kind"]
                row[k] = round(cov * 100, 1)
                worst[k] = max(worst[k], cov * 100)
                hits[k] += cov > 0.02
            detail.append(row)
        report[v] = {"maks_tertutup_%": {k: round(x, 1) for k, x in worst.items()},
                     "frame_tertutup_>2%": hits, "frame": len(detail), "rinci": detail}
        print(f"{v:24s} maks tertutup: wajah {worst['wajah']:5.1f} %  papan {worst['papan']:5.1f} %  "
              f"subtitle {worst['subtitle']:5.1f} %  | frame >2 %: {hits}  (dari {len(detail)})", flush=True)
    (f / "safezone-cek.json").write_text(json.dumps(report, indent=1, ensure_ascii=False))
    worst = report["motion-safezone"]["maks_tertutup_%"]
    if a.strict and (worst["wajah"] > 2 or worst["subtitle"] > 2 or worst["papan"] > 5):
        raise SystemExit(f"safe-zone gagal: {worst}")


if __name__ == "__main__":
    main()
