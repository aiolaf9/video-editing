"""M2.5 Motion Library: MP4 contoh per kategori + lembar kontak + ukuran waktu/CPU/RAM, lewat jalur render aplikasi
(pipeline.assemble -> render.build_command), sama dengan pratinjau dan ekspor final.

  kategori-<id>.mp4        semua preset satu kategori bergantian pada video yang sama (urutan & waktu di indeks.json)
  tingkat-<level>.mp4      kombinasi Auto Style yang sama pada intensitas ringan / seimbang / dinamis
  lembar-<id>.png          satu frame (tengah animasi) per preset, berlabel
  ukuran.json              durasi render, waktu CPU, RAM puncak FFmpeg, kecepatan relatif real-time

    python bench/m25/library_demo.py video.mp4 keluar/ [--transcript t.json] [--seconds 24] [--categories ...]
Tanpa --transcript dibuat transkrip sintetis (kata bergiliran) agar caption tetap terlihat.
"""
import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

WORDS = ("sedekah itu membuka pintu rezeki dan menenangkan hati setiap hamba yang ikhlas berbagi kepada sesama "
         "yang membutuhkan terutama anak yatim dan kaum dhuafa di sekitar kita").split()


def synthetic_transcript(seconds: float) -> dict:
    segs, t, i = [], 0.3, 0
    while t < seconds - 0.5:
        words = []
        for _ in range(6):
            w = WORDS[i % len(WORDS)]
            d = 0.22 + 0.04 * len(w)
            words.append({"word": w, "start": round(t, 2), "end": round(t + d, 2)})
            t += d + 0.08
            i += 1
        segs.append({"start": words[0]["start"], "end": words[-1]["end"], "text": " ".join(x["word"] for x in words),
                     "words": words})
        t += 0.3
    return {"segments": segs, "language": "id"}


def category_items(cat: str, seconds: float) -> list[dict]:
    """Semua preset satu kategori pada slot berurutan (kamera/transisi juga diberi jeda agar lolos guard)."""
    from app import motionlib
    ps = [p for p in motionlib.presets() if p["category"] == cat]
    slot = seconds / max(1, len(ps))
    items = []
    for i, p in enumerate(ps):
        s = round(i * slot + 0.2, 2)
        if cat == "captions":
            e = round((i + 1) * slot, 2)
        elif cat == "transitions":
            mid = s + slot / 2
            d = float(p["defaults"].get("duration", 0.4))
            s, e = round(mid - d / 2, 2), round(mid + d / 2, 2)
        else:
            d = float(p["defaults"].get("duration", min(slot - 0.4, 4.0)))
            e = round(min((i + 1) * slot - 0.2, s + max(d, 1.2)), 2)
        items.append({"id": f"fx{i}", "type": "fx", "preset": p["id"], "params": {}, "start": s, "end": e})
    return items


def run_measured(cmd: list[str], cwd: Path) -> dict:
    """Waktu, waktu CPU, dan RAM puncak proses FFmpeg (Linux: wait4/rusage; Windows: psutil bila tersedia)."""
    from app.config import FFMPEG
    t0 = time.perf_counter()
    proc = subprocess.Popen([FFMPEG, "-y", "-loglevel", "error", *cmd], cwd=cwd)
    out = {}
    if hasattr(os, "wait4"):
        _, status, ru = os.wait4(proc.pid, 0)
        proc.returncode = os.waitstatus_to_exitcode(status)
        out = {"cpu_detik": round(ru.ru_utime + ru.ru_stime, 2), "ram_puncak_mb": round(ru.ru_maxrss / 1024, 1)}
    else:
        peak, cpu = 0, 0.0
        try:
            import psutil
            p = psutil.Process(proc.pid)
            while proc.poll() is None:
                try:
                    mi = p.memory_info()
                    peak = max(peak, getattr(mi, "peak_wset", 0) or mi.rss)
                    ct = p.cpu_times()
                    cpu = ct.user + ct.system
                except psutil.Error:
                    break
                time.sleep(0.2)
            out = {"cpu_detik": round(cpu, 2), "ram_puncak_mb": round(peak / 1048576, 1)}
        except ImportError:
            pass
        proc.wait()
    if proc.returncode:
        raise SystemExit(f"ffmpeg gagal ({proc.returncode})")
    return {"detik": round(time.perf_counter() - t0, 2), **out}


def contact(out: Path, name: str, mp4: Path, items: list[dict], labels: dict) -> None:
    from PIL import Image, ImageDraw
    frames = []
    for it in items:
        t = it["start"] + (it["end"] - it["start"]) * 0.5 if it["end"] - it["start"] < 1 else \
            it["start"] + min(1.25, (it["end"] - it["start"]) * 0.55)
        png = out / f"_f_{name}_{it['id']}.png"
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{t:.2f}", "-i", str(mp4), "-frames:v", "1",
                        "-vf", "scale=270:-2", str(png)], check=True)
        im = Image.open(png).convert("RGB")
        d = ImageDraw.Draw(im)
        d.rectangle([0, 0, im.width, 22], fill=(0, 0, 0))
        d.text((6, 5), labels[it["preset"]][:38], fill=(255, 255, 255))
        frames.append(im)
        png.unlink()
    cols = min(5, len(frames))
    rows = (len(frames) + cols - 1) // cols
    w, h = frames[0].size
    sheet = Image.new("RGB", (cols * w, rows * h), (20, 20, 20))
    for i, im in enumerate(frames):
        sheet.paste(im, ((i % cols) * w, (i // cols) * h))
    sheet.save(out / f"lembar-{name}.png")


def render(req, tr, items, frame, info, src: Path, out: Path, name: str, size: tuple[int, int]) -> dict:
    from app import pipeline
    from app.render import build_command
    from app.subtitles import SubtitleOptions, PRESETS
    p = PRESETS["karaoke"]
    opts = SubtitleOptions(**{k: v for k, v in p.items() if k in SubtitleOptions.__dataclass_fields__})
    opts.style = p["anim"]
    parts = pipeline.assemble(req, tr, opts, items, frame, (info["width"], info["height"]), size[0], size[1], out, name)
    cmd = build_command(src, out / f"{name}.mp4", req, info, parts["subs"], None, out, None, parts["punches"],
                        parts["layers"], frame, graphics_file=parts["graphics"], camera_fx=parts["camera_fx"])
    m = run_measured(cmd, out)
    for f in (parts["subs"], parts["graphics"]):
        if f:
            f.unlink()
    import shutil
    shutil.rmtree(parts["overlay_dir"], ignore_errors=True)
    (out / f"{name}.penempatan.json").write_text(json.dumps(parts["placement"], indent=1, ensure_ascii=False),
                                                encoding="utf-8")
    return m


def main():
    from app import framing, media, motionlib
    from app.render import output_size
    from app.schemas import RenderRequest
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("out")
    ap.add_argument("--transcript")
    ap.add_argument("--start", type=float, default=0)
    ap.add_argument("--seconds", type=float, default=24)
    ap.add_argument("--aspect", default="9:16")
    ap.add_argument("--categories", default=",".join(motionlib.categories()))
    ap.add_argument("--levels", action="store_true", help="tambahkan tingkat-ringan/seimbang/dinamis (kombinasi sama)")
    ap.add_argument("--strict", action="store_true", help="gagal bila ada preset kategori yang tidak tampil")
    a = ap.parse_args()
    out = Path(a.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    src = Path(a.video).resolve()
    info = media.probe(src)
    end = min(info["duration"], a.start + a.seconds)
    tr = json.loads(Path(a.transcript).read_text(encoding="utf-8")) if a.transcript else synthetic_transcript(end)
    preview = out / "_analisis480.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-t", f"{end:.2f}", "-vf", "scale=-2:480",
                    "-an", str(preview)], check=True)
    data = framing.analyze(preview, info["width"], info["height"], info["duration"])
    preview.unlink()
    frames = {mode: {"segments": framing.plan(data, a.start, end, mode), "data": data, "zoom": 0.0, "distance": "auto"}
              for mode in ("auto", "face")}
    frame = frames["auto"]
    size = output_size(a.aspect, info["width"], info["height"])
    labels = {p["id"]: p["label"] for p in motionlib.presets()}
    index, measures, skipped_all = {}, {}, {}
    for cat in [c for c in a.categories.split(",") if c]:
        items = category_items(cat, end - a.start)
        for it in items:
            it["start"] += a.start
            it["end"] += a.start
        # Contoh kategori memadatkan 5-15 preset dalam satu klip: tingkat "dinamis" (batas kepadatan paling longgar)
        # agar setiap preset tampil; pada pemakaian biasa guard tingkat seimbang tetap berlaku.
        req = RenderRequest(start=a.start, end=end, aspect=a.aspect, animations=True, motion_level="dinamis")
        name = f"kategori-{cat}"
        # Kamera & whip tidak dipakai di tata letak Materi (memotong papan): contohnya memakai Fokus Wajah.
        fr = frames["face"] if cat in ("camera", "transitions") else frame
        measures[name] = render(req, tr, items, fr, info, src, out, name, size)
        index[name] = [{"preset": it["preset"], "tingkat": "dinamis", "label": labels[it["preset"]], "mulai": it["start"] - a.start,
                        "akhir": it["end"] - a.start} for it in items]
        placed = json.loads((out / f"{name}.penempatan.json").read_text(encoding="utf-8"))
        gone = {it["preset"]: placed[it["id"]]["reason"] for it in items
                if placed.get(it["id"], {}).get("action") in ("dilewati", "disembunyikan")}
        if gone:
            print(f"PERINGATAN {name}: preset tidak tampil: {gone}", flush=True)
            skipped_all[name] = gone
        contact(out, cat, out / f"{name}.mp4", [{**it, "start": it["start"] - a.start, "end": it["end"] - a.start}
                                                for it in items], labels)
        print(name, measures[name], flush=True)
    if a.levels:
        from app import autostyle
        combo = autostyle.motion_combo("kajian", a.start, end, tr)
        for level in motionlib.LEVELS:
            req = RenderRequest(start=a.start, end=end, aspect=a.aspect, animations=True, motion_level=level)
            name = f"tingkat-{level}"
            measures[name] = render(req, tr, combo, frame, info, src, out, name, size)
            print(name, measures[name], flush=True)
        index["kombinasi-kajian"] = combo
    dur = end - a.start
    for m in measures.values():
        m["x_realtime"] = round(dur / max(0.01, m["detik"]), 2)
    index["tidak_tampil"] = skipped_all
    (out / "indeks.json").write_text(json.dumps(index, indent=1, ensure_ascii=False), encoding="utf-8")
    (out / "ukuran.json").write_text(json.dumps({"durasi_klip": dur, "ukuran": size, "cpu": os.cpu_count(),
                                                 "render": measures}, indent=1), encoding="utf-8")
    if a.strict and skipped_all:
        raise SystemExit(f"preset tidak tampil: {skipped_all}")


if __name__ == "__main__":
    main()
