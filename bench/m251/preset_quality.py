"""M2.5.1 uji kualitas 60 preset Motion Library:

  1. keterbacaan HP  : ukuran font terkecil tiap preset (px pada lebar 1080) dari ASS yang dirender engine
  2. variasi nyata   : jarak antarpreset satu kategori dari klip galeri (adegan & subtitle sama). Sinyal efek =
                       selisih tiap frame terhadap klip tanpa efek; jarak = rata-rata selisih dua sinyal. Pasangan
                       paling mirip per kategori dilaporkan; pasangan < 25 % median kategori ditandai "mirip".
  3. PC rendah       : render 1080x1920 & pratinjau 360x640 dengan FFmpeg dibatasi 1 dan 2 inti CPU (taskset).

    python bench/m251/preset_quality.py keluar/ [--video kajian.mp4 --transcript t.json --seconds 30]
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
GW, GH = 90, 160


def clip_frames(path: Path) -> np.ndarray:
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-vf", f"fps=6,scale={GW}:{GH}", "-f", "rawvideo",
                          "-pix_fmt", "gray", "-"], capture_output=True, check=True).stdout
    n = len(raw) // (GW * GH)
    return np.frombuffer(raw[: n * GW * GH], np.uint8).reshape(n, GH, GW).astype(np.float32)


def legibility() -> dict:
    sys.path.insert(0, str(ROOT / "tests"))
    from test_m251_protect import _min_font
    from app import motionlib
    out = {}
    for p in motionlib.presets():
        if p["kind"] in ("camera", "transition") or p["kind"].startswith("tr_"):
            continue
        out[p["id"]] = _min_font(p["id"])
    return out


def variation(out: Path) -> dict:
    from app import gallery, motion_gallery, motionlib, pipeline
    from app.render import build_command
    from app.schemas import RenderRequest
    cache = out / "galeri"
    cache.mkdir(parents=True, exist_ok=True)
    motion_gallery.CACHE, motion_gallery.BUNDLED = cache, cache / "_x"
    clips = {p["id"]: motion_gallery.render(p["id"])[0] for p in motionlib.presets()}
    # klip dasar: adegan + subtitle sama, tanpa efek
    scene = next(cache.glob("scene-*.mp4"))
    req = RenderRequest(start=0, end=motion_gallery.DURATION, aspect="9:16", animations=False, fade=False,
                        normalize_audio=False)
    opts = gallery.options_for("karaoke")
    opts.emphasis = False
    W, H = motion_gallery.W, motion_gallery.H
    parts = pipeline.assemble(req, gallery.sample_transcript(), opts, [], None, (W, H), W, H, cache, "dasar")
    info = {"width": W, "height": H, "duration": motion_gallery.DURATION + 0.3, "has_audio": True}
    subprocess.run(["ffmpeg", "-y", "-v", "error", *build_command(scene, cache / "dasar.mp4", req, info, parts["subs"],
                                                                   None, cache, None, [], [], None)],
                   check=True, cwd=cache)
    base = clip_frames(cache / "dasar.mp4")
    sig = {}
    for pid, mp4 in clips.items():
        f = clip_frames(mp4)
        n = min(len(f), len(base))
        sig[pid] = f[:n] - base[:n]
    res = {}
    for cat in motionlib.categories():
        ids = [p["id"] for p in motionlib.presets() if p["category"] == cat]
        pairs = []
        for i, a in enumerate(ids):
            for b in ids[i + 1:]:
                n = min(len(sig[a]), len(sig[b]))
                pairs.append((float(np.abs(sig[a][:n] - sig[b][:n]).mean()), a, b))
        pairs.sort()
        med = float(np.median([d for d, _, _ in pairs]))
        res[cat] = {"median": round(med, 2), "paling_mirip": [(round(d, 2), a, b) for d, a, b in pairs[:3]],
                    "mirip": [(round(d, 2), a, b) for d, a, b in pairs if d < 0.25 * med],
                    "kekuatan_efek": {k: round(float(np.abs(sig[k]).mean()), 2) for k in ids}}
    return res


def speed(out: Path, video: Path, transcript: Path, seconds: float) -> dict:
    """Render kombinasi Auto Style Kajian (judul + kamera + bab + CTA + caption) dengan 1 / 2 / semua inti."""
    from app import autostyle, framing, media
    from app.render import output_size
    from app.schemas import RenderRequest
    from bench.m25.library_demo import render
    info = media.probe(video)
    end = min(info["duration"], seconds)
    tr = json.loads(transcript.read_text(encoding="utf-8")) if transcript else None
    data = framing.analyze(video, info["width"], info["height"], info["duration"])
    frame = {"segments": framing.plan(data, 0, end, "auto"), "data": data, "zoom": 0.0, "distance": "auto"}
    combo = autostyle.motion_combo("kajian", 0, end, tr)
    res = {"durasi_klip": end, "sumber": f"{info['width']}x{info['height']}", "elemen": len(combo)}
    import bench.m25.library_demo as L
    from app import pipeline
    from app.render import build_command
    from app.subtitles import PRESETS, SubtitleOptions
    orig = L.subprocess.Popen
    for cores in ("0", "0,1", None):
        def popen(cmd, *a, _c=cores, **k):
            if _c and cmd and str(cmd[0]).endswith("ffmpeg"):
                cmd = ["taskset", "-c", _c, *cmd]
            return orig(cmd, *a, **k)
        label = "1 inti" if cores == "0" else "2 inti" if cores else f"{os.cpu_count()} inti"
        L.subprocess.Popen = popen
        try:
            # render final 1080x1920 (jalur sama dengan ekspor)
            req = RenderRequest(start=0, end=end, aspect="9:16", animations=True)
            tag = f"final-{cores or 'semua'}".replace(",", "")
            m = render(req, tr or {"segments": []}, combo, frame, info, video, out, tag,
                       output_size("9:16", info["width"], info["height"]))
            m["x_realtime"] = round(end / m["detik"], 2)
            res[f"final {label}"] = m
            (out / f"{tag}.mp4").unlink(missing_ok=True)
            # pratinjau seperti aplikasi: maks 10 dtk, skala PREVIEW_SCALE, tanpa normalisasi audio
            pend = min(end, pipeline.PREVIEW_MAX_SECONDS)
            preq = RenderRequest(start=0, end=pend, aspect="9:16", animations=True, normalize_audio=False)
            fw, fh = output_size("9:16", info["width"], info["height"])
            pw, ph = max(2, int(fw * pipeline.PREVIEW_SCALE) // 2 * 2), max(2, int(fh * pipeline.PREVIEW_SCALE) // 2 * 2)
            p = PRESETS["karaoke"]
            opts = SubtitleOptions(**{k: v for k, v in p.items() if k in SubtitleOptions.__dataclass_fields__})
            opts.style = p["anim"]
            parts = pipeline.assemble(preq, tr or {"segments": []}, opts, combo, frame, (info["width"], info["height"]),
                                      pw, ph, out, "pv")
            cmd = build_command(video, out / "pv.mp4", preq, info, parts["subs"], None, out, None, parts["punches"],
                                parts["layers"], frame, out_scale=pipeline.PREVIEW_SCALE,
                                graphics_file=parts["graphics"], camera_fx=parts["camera_fx"])
            pm = L.run_measured(cmd, out)
            pm["durasi"] = pend
            pm["x_realtime"] = round(pend / pm["detik"], 2)
            res[f"pratinjau {label}"] = pm
            print(tag, m, "| pratinjau", pm, flush=True)
        finally:
            L.subprocess.Popen = orig
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--video")
    ap.add_argument("--transcript")
    ap.add_argument("--seconds", type=float, default=30)
    ap.add_argument("--skip-variation", action="store_true")
    a = ap.parse_args()
    out = Path(a.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    report = {"keterbacaan_px_lebar1080": legibility()}
    small = {k: v for k, v in report["keterbacaan_px_lebar1080"].items() if v is not None and v < 42}
    report["keterbacaan_di_bawah_42px"] = small
    print("keterbacaan: terkecil", min(v for v in report["keterbacaan_px_lebar1080"].values() if v), "px; <42:", small)
    if not a.skip_variation:
        t = time.time()
        report["variasi"] = variation(out)
        report["variasi_detik"] = round(time.time() - t, 1)
        for cat, v in report["variasi"].items():
            print(cat, "median", v["median"], "paling mirip", v["paling_mirip"][0], "mirip<25%:", v["mirip"])
    if a.video:
        report["kecepatan"] = speed(out, Path(a.video).resolve(), Path(a.transcript) if a.transcript else None,
                                    a.seconds)
    (out / "kualitas.json").write_text(json.dumps(report, indent=1, ensure_ascii=False), encoding="utf-8")
    shutil.rmtree(out / "galeri" / "_x", ignore_errors=True)


if __name__ == "__main__":
    main()
