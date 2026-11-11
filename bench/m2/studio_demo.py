"""M2: MP4 bukti sebelum/sesudah dari jalur render aplikasi (build_command + build_ass + framing).

  sebelum.mp4             perilaku M1.1: crop 9:16 ikut posisi x wajah + subtitle karaoke bawaan
  sesudah-<preset>.mp4    M2: Smart Framing Otomatis + preset subtitle animasi (+ sorot kata penting)
  sesudah-lengkap.mp4     M2: + motion graphics dari heuristik lokal (hook, angka, kata kunci, zoom) — tanpa edit manual
  lembar.png              frame per 3 dtk tiap varian

    python bench/m2/studio_demo.py video.mp4 transkrip.json keluar/ [--presets karaoke,bounce,...] [--end 24]
transkrip.json = {"segments": [{"start", "end", "text", "words": [{"word","start","end"}]}]} (mis. hasil Whisper).
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def safezone_items(frame: dict, start: float, end: float) -> list[dict]:
    """Elemen uji safe-zone pada momen berisiko: hook di awal, lower-third melintasi pergantian tata letak, angka
    saat Materi+Wajah, kinetic saat Fokus Materi, CTA di akhir."""
    segs = frame["segments"]
    switch = next((s["start"] for s in segs[1:]), start + (end - start) / 2)
    split = next((s for s in segs if s["layout"] == "split"), segs[0])
    material = next((s for s in segs if s["layout"] == "material"), segs[-1])
    mid = (split["start"] + split["end"]) / 2
    return [
        {"id": "hook", "type": "hook", "text": "Berapa zakat fitrah yang wajib dibayar?", "start": start, "end": start + 2.8},
        {"id": "nama", "type": "lower_third", "text": "Ustadz Ahmad", "label": "Pemateri Kajian Zakat",
         "start": max(start, switch - 2.0), "end": min(end, switch + 2.0)},
        {"id": "angka", "type": "counter", "text": "2,5 kg", "label": "beras per jiwa", "start": mid, "end": min(end, mid + 2.5)},
        {"id": "kinetic", "type": "kinetic", "text": "Ada delapan golongan penerima", "start": material["start"],
         "end": material["end"]},
        {"id": "kata", "type": "keyword", "text": "PENERIMA", "start": material["start"] + 0.5,
         "end": min(material["end"], material["start"] + 2.0)},
        {"id": "cta", "type": "cta", "text": "Ikuti kajian berikutnya", "start": max(start, end - 3.0), "end": end},
    ]


def main():
    from app import faces, framing, media
    from app.render import build_command, output_size
    from app.subtitles import SubtitleOptions
    from bench.m2.framing_compare import contact_sheet
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("transcript")
    ap.add_argument("out")
    ap.add_argument("--start", type=float, default=0)
    ap.add_argument("--end", type=float, default=0)
    ap.add_argument("--presets", default="bounce,highlight,documentary,neon")
    ap.add_argument("--motion", action="store_true", help="tambahkan varian sesudah-lengkap (motion graphics otomatis)")
    ap.add_argument("--safezone", action="store_true",
                    help="tambahkan motion-tanpa-safezone vs motion-safezone (elemen sama, termasuk saat tata letak berganti)")
    a = ap.parse_args()
    from app.schemas import RenderRequest
    from app.subtitles import PRESETS
    out = Path(a.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    src = Path(a.video).resolve()
    info = media.probe(src)
    end = a.end or info["duration"]
    tr = json.loads(Path(a.transcript).read_text(encoding="utf-8"))
    preview = out / "preview480.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-vf", "scale=-2:480", "-an", str(preview)],
                   check=True)
    data = framing.analyze(preview, info["width"], info["height"], info["duration"])
    (out / "framing.json").write_text(json.dumps(data))
    w, h = output_size("9:16", info["width"], info["height"])
    variants = {"sebelum": None, **{f"sesudah-{p}": p for p in a.presets.split(",") if p}}
    if a.motion:
        variants["sesudah-lengkap"] = "highlight"
    if a.safezone:
        variants["hanya-subtitle"] = "pop"          # pembanding piksel: subtitle sama, tanpa grafis
        variants["motion-tanpa-safezone"] = "pop"
        variants["motion-safezone"] = "pop"
    from app import pipeline
    real_layout = pipeline.layout_items
    for name, preset in variants.items():
        req = RenderRequest(start=a.start, end=end, aspect="9:16", animations=preset is not None)
        if preset is None:
            opts = SubtitleOptions()                      # bawaan M1.1
            camera, frame = faces.camera_path(framing.faces_compat(data), a.start, end) or None, None
        else:
            p = PRESETS[preset]
            keys = SubtitleOptions.__dataclass_fields__
            opts = SubtitleOptions(**{k: v for k, v in p.items() if k in keys})
            opts.style, opts.emphasis = p["anim"], True
            opts.emphasis_terms = ("zakat", "fitrah", "beras", "golongan")
            camera = None
            frame = {"segments": framing.plan(data, a.start, end, "auto"), "data": data, "zoom": 0.0,
                     "distance": "auto"}
        items = []
        if name == "sesudah-lengkap":
            from app import plan
            items = plan.heuristic(tr, [], a.start, end)
        elif name.startswith("motion-"):
            items = safezone_items(frame, a.start, end)
        (out / f"{name}.items.json").write_text(json.dumps(items, indent=1, ensure_ascii=False), encoding="utf-8")
        pipeline.layout_items = (lambda *x, **k: {}) if name == "motion-tanpa-safezone" else real_layout
        try:
            parts = pipeline.assemble(req, tr, opts if preset is not None or True else None, items, frame,
                                      (info["width"], info["height"]), w, h, out, name)
        finally:
            pipeline.layout_items = real_layout
        if parts["placement"]:
            (out / f"{name}.penempatan.json").write_text(json.dumps(parts["placement"], indent=1, ensure_ascii=False),
                                                         encoding="utf-8")
        cmd = build_command(src, out / f"{name}.mp4", req, info, parts["subs"], None, out, camera, parts["punches"],
                            parts["layers"], frame, graphics_file=parts["graphics"])
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *cmd], check=True, cwd=out)
        for f in (parts["subs"], parts["graphics"]):
            if f:
                f.unlink()
        import shutil
        shutil.rmtree(parts["overlay_dir"], ignore_errors=True)
        print(name, "ok", flush=True)
    contact_sheet(out, list(variants), a.start, end)


if __name__ == "__main__":
    main()
