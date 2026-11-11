"""M2.5.1 bukti Area Dilindungi dari PIKSEL MP4: video dengan subjek bergerak (mis. peternakan), elemen motion sama,
dirender lewat jalur aplikasi tiga kali: hanya-subtitle, motion TANPA area dilindungi, motion DENGAN area dilindungi.
Piksel grafis = selisih MP4 bermotion dengan MP4 hanya-subtitle. Subjek pembanding = semua hewan/orang yang terdeteksi
per sampel 0,5 dtk (YOLOX-nano lokal) dipetakan ke bingkai output dengan geometri yang sama dengan safe-zone.

  sebelum.mp4 / sesudah.mp4 / banding.mp4 (berdampingan) / lembar.png / hasil.json / area.json

    python bench/m251/protect_compare.py video.mp4 keluar/ [--start 20] [--seconds 40] [--areas area.json]
Tanpa --areas, area diambil dari saran otomatis (eksperimental) untuk objek utama; hasil.json mencatat sumbernya.
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
STEP = 0.5


def items(start: float) -> list[dict]:
    """Elemen contoh campuran: judul, nama (informasi), chips (dekoratif), checklist, grafis, bab, CTA."""
    def fx(i, pid, s, e, **p):
        return {"id": i, "type": "fx", "preset": pid, "params": p, "start": start + s, "end": start + e}
    return [
        fx("judul", "title.cinematic", 0.3, 3.3, text="Ternak Sehat Berkah", label="Contoh liputan"),
        {"id": "nama", "type": "lower_third", "text": "Nama peternak", "label": "Peternak (contoh)",
         "start": start + 4, "end": start + 8},
        {"id": "chips", "type": "chips", "items": ["Pakan", "Vaksin", "Kandang"], "text": "", "label": "",
         "start": start + 9, "end": start + 13},
        fx("progres", "gfx.progress", 14, 18, value=60, text="Contoh progres"),
        {"id": "cek", "type": "checklist", "items": ["Cek kesehatan", "Beri pakan", "Bersihkan kandang"],
         "text": "", "label": "", "start": start + 19, "end": start + 24},
        fx("bab", "story.chapter", 25, 28, text="Merawat Anak Kambing", label="02"),
        fx("cincin", "gfx.ring", 29, 33, value=80, text="Contoh capaian"),
        fx("cta", "cta.follow_bar", 35, 39, text="Ikuti kisah peternak", label="@contoh"),
    ]


def frames(path: Path, seconds: float) -> list[np.ndarray]:
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-t", str(seconds), "-vf",
                          f"fps={1 / STEP},scale={W}:{H}", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                         capture_output=True, check=True).stdout
    n = len(raw) // (W * H * 3)
    return [np.frombuffer(raw[i * W * H * 3:(i + 1) * W * H * 3], np.uint8).reshape(H, W, 3).astype(np.int16)
            for i in range(n)]


def subject_truth(src: Path, start: float, seconds: float, info: dict, frame: dict, req) -> list[list[list[float]]]:
    """Per sampel: kotak OUTPUT semua hewan/orang terdeteksi (bukan hanya objek utama)."""
    from app import framing, subject_detect
    wanted = subject_detect.GROUPS["hewan"] | subject_detect.GROUPS["orang"]
    out = []
    samples = list(subject_detect._frames(src, start, start + seconds, info["width"], info["height"]))
    for t, img in samples:
        dets = [d for d in subject_detect.detect_frame(img) if d["cls"] in wanted]
        seg = next((s for s in frame["segments"] if s["start"] <= t < s["end"]), frame["segments"][-1])
        rects = framing.occupied_rects(seg, frame["data"], info["width"], info["height"], 1080, 1920,
                                       (req.crop_x, req.crop_y, req.frame_zoom), req.frame_distance,
                                       protect=[{"box": d["box"], "id": d["cls"]} for d in dets])
        out.append([r["rect"] for r in rects if r["kind"] == "lindung"])
    return out


def render(req, tr, its, frame, info, src, out, name):
    from app import pipeline
    from app.render import build_command
    from app.subtitles import PRESETS, SubtitleOptions
    p = PRESETS["karaoke"]
    opts = SubtitleOptions(**{k: v for k, v in p.items() if k in SubtitleOptions.__dataclass_fields__})
    opts.style = p["anim"]
    parts = pipeline.assemble(req, tr, opts, its, frame, (info["width"], info["height"]), 1080, 1920, out, name)
    cmd = build_command(src, out / f"{name}.mp4", req, info, parts["subs"], None, out, None, parts["punches"],
                        parts["layers"], frame, graphics_file=parts["graphics"], camera_fx=parts["camera_fx"])
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *cmd], check=True, cwd=out)
    for f in (parts["subs"], parts["graphics"]):
        if f:
            f.unlink()
    import shutil
    shutil.rmtree(parts["overlay_dir"], ignore_errors=True)
    return parts["placement"]


def main():
    from app import framing, media, subject_detect
    from app.schemas import RenderRequest
    from bench.m25.library_demo import synthetic_transcript
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("out")
    ap.add_argument("--start", type=float, default=0)
    ap.add_argument("--seconds", type=float, default=40)
    ap.add_argument("--areas", help="JSON daftar area (detik sumber); bawaan: saran otomatis objek utama")
    a = ap.parse_args()
    out = Path(a.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    src = Path(a.video).resolve()
    info = media.probe(src)
    end = min(info["duration"], a.start + a.seconds)
    seconds = end - a.start
    data = framing.analyze(src, info["width"], info["height"], info["duration"])
    frame = {"segments": framing.plan(data, a.start, end, "auto"), "data": data, "zoom": 0.0, "distance": "auto"}
    if a.areas:
        areas, origin = json.loads(Path(a.areas).read_text()), f"berkas {a.areas}"
    else:
        sug = subject_detect.suggest(src, a.start, end, info["width"], info["height"], ("hewan", "orang"))
        areas, origin = sug["areas"], f"saran otomatis objek utama ({sug['seen']}/{sug['samples']} sampel berisi subjek)"
    for i, x in enumerate(areas):
        x.setdefault("id", f"a{i}")
    (out / "area.json").write_text(json.dumps(areas, indent=1), encoding="utf-8")
    tr = synthetic_transcript(end)                  # tanpa ucapan: kata contoh hanya untuk menampilkan subtitle
    base_req = RenderRequest(start=a.start, end=end, aspect="9:16", animations=True)
    its = items(a.start)
    render(base_req.model_copy(update={"animations": False}), tr, [], frame, info, src, out, "hanya-subtitle")
    place = {"sebelum": render(base_req, tr, its, frame, info, src, out, "sebelum")}
    with_req = RenderRequest(start=a.start, end=end, aspect="9:16", animations=True, protect=areas)
    place["sesudah"] = render(with_req, tr, its, frame, info, src, out, "sesudah")
    truth = subject_truth(src, a.start, seconds, info, frame, base_req)
    base = frames(out / "hanya-subtitle.mp4", seconds)
    report = {"sumber_area": origin, "area": len(areas), "langkah": STEP, "varian": {}}
    for v in ("sebelum", "sesudah"):
        frs = frames(out / f"{v}.mp4", seconds)
        cov, gfx_on_subject, subtitle_cov, visible = [], [], [], 0
        for i, (x, b) in enumerate(zip(frs, base)):
            mask = np.abs(x - b).max(axis=2) > 48
            if mask.mean() < 0.002:
                continue                            # tidak ada grafis pada sampel ini
            visible += 1
            subj = np.zeros((H, W), bool)
            for r in truth[i] if i < len(truth) else []:
                subj[int(r[1] * H):int((r[1] + r[3]) * H), int(r[0] * W):int((r[0] + r[2]) * W)] = True
            if subj.any():
                cov.append(float((mask & subj).sum() / subj.sum()))
            gfx_on_subject.append(float((mask & subj).sum() / max(1, mask.sum())))
            band = mask[int(0.70 * H):int(0.84 * H)]
            subtitle_cov.append(float(band.mean()))
        hidden = [k for k, p in place[v].items() if p.get("action") == "disembunyikan"]
        report["varian"][v] = {
            "sampel_bergrafis": visible,
            "subjek_tertutup_rata2_pct": round(100 * float(np.mean(cov)) if cov else 0.0, 1),
            "subjek_tertutup_maks_pct": round(100 * float(np.max(cov)) if cov else 0.0, 1),
            "sampel_subjek_tertutup_gt5pct": int(sum(c > 0.05 for c in cov)),
            "piksel_grafis_di_atas_subjek_pct": round(100 * float(np.mean(gfx_on_subject)) if gfx_on_subject else 0, 1),
            "pita_subtitle_tertutup_rata2_pct": round(100 * float(np.mean(subtitle_cov)) if subtitle_cov else 0, 1),
            "disembunyikan": hidden,
            "penempatan": {k: p.get("action") for k, p in place[v].items()},
        }
    (out / "hasil.json").write_text(json.dumps(report, indent=1, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(report["varian"], indent=1, ensure_ascii=False))
    # berdampingan + lembar kontak dengan kotak subjek (hijau) sebagai acuan
    import shutil as _sh
    _sh.copy(ROOT / "fonts" / "Poppins-SemiBold.ttf", out / "label.ttf")     # Windows: tanpa fontconfig/jalur C:
    lab = "fontfile=label.ttf:x=20:y=20:fontsize=34:fontcolor=white:box=1:boxcolor=black@0.6"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", "sebelum.mp4", "-i", "sesudah.mp4",
                    "-filter_complex", f"[0:v]scale=540:960,drawtext=text='SEBELUM':{lab}[a];"
                    f"[1:v]scale=540:960,drawtext=text='SESUDAH (area dilindungi)':{lab}[b];[a][b]hstack",
                    "-map", "0:a?", "-c:v", "libx264", "-crf", "23", "-preset", "veryfast", "-c:a", "aac",
                    "banding.mp4"], check=True, cwd=out)
    (out / "label.ttf").unlink()
    from PIL import Image, ImageDraw
    pre, post = frames(out / "sebelum.mp4", seconds), frames(out / "sesudah.mp4", seconds)
    times = [it["start"] - a.start + min(1.5, (it["end"] - it["start"]) / 2) for it in its]
    tiles = []
    for t in times:
        i = min(len(pre) - 1, int(t / STEP))
        row = []
        for img in (pre[i], post[i]):
            im = Image.fromarray(img.astype(np.uint8))
            d = ImageDraw.Draw(im)
            for r in truth[i] if i < len(truth) else []:
                d.rectangle([r[0] * W, r[1] * H, (r[0] + r[2]) * W, (r[1] + r[3]) * H], outline=(60, 230, 120), width=2)
            row.append(im)
        tiles.append(row)
    sheet = Image.new("RGB", (W * 2 * 4 + 30, (H + 20) * 2), (15, 15, 15))
    d = ImageDraw.Draw(sheet)
    for k, (pa, pb) in enumerate(tiles):
        col, rw = k % 4, k // 4
        x0 = col * (W * 2 + 10)
        y0 = rw * (H + 20)
        sheet.paste(pa, (x0, y0 + 18))
        sheet.paste(pb, (x0 + W, y0 + 18))
        d.text((x0 + 4, y0 + 3), f"{its[k]['id']} @ {times[k]:.1f}s  sebelum | sesudah", fill=(255, 255, 255))
    sheet.save(out / "lembar.png")


if __name__ == "__main__":
    main()
