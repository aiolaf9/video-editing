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


def main():
    from app import faces, framing, media
    from app.render import build_command, output_size
    from app.subtitles import SubtitleOptions, build_ass
    from bench.m2.framing_compare import contact_sheet
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("transcript")
    ap.add_argument("out")
    ap.add_argument("--start", type=float, default=0)
    ap.add_argument("--end", type=float, default=0)
    ap.add_argument("--presets", default="bounce,highlight,documentary,neon")
    ap.add_argument("--motion", action="store_true", help="tambahkan varian sesudah-lengkap (motion graphics otomatis)")
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
    w, h = output_size("9:16", info["width"], info["height"])
    variants = {"sebelum": None, **{f"sesudah-{p}": p for p in a.presets.split(",") if p}}
    if a.motion:
        variants["sesudah-lengkap"] = "highlight"
    for name, preset in variants.items():
        req = RenderRequest(start=a.start, end=end, aspect="9:16", animations=name == "sesudah-lengkap")
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
            from app.pipeline import subtitle_zones
            opts.zones = subtitle_zones(frame, req, h / w)
        punches, graphics, items = [], None, []
        if name == "sesudah-lengkap":
            from app import plan
            from app.subtitles import norm_tokens
            items = plan.heuristic(tr, [], a.start, end)
            opts.suppress = tuple((i["start"] - a.start, i["end"] - a.start, frozenset(norm_tokens(i["text"])))
                                  for i in items if i["type"] in ("hook", "kinetic"))
        ass = out / f"{name}.ass"
        ass.write_text(build_ass(tr, a.start, end, w, h, opts), encoding="utf-8")
        if name == "sesudah-lengkap":
            from app import motion
            (out / "motion-otomatis.json").write_text(json.dumps(items, indent=1, ensure_ascii=False), encoding="utf-8")
            from app.pipeline import drop_split_punches
            punches = drop_split_punches([(i["start"] - a.start, i["end"] - a.start, i.get("strength", 0.14))
                                          for i in items if i["type"] == "zoom"], frame, a.start)
            mitems = [{**i, "start": i["start"] - a.start, "end": i["end"] - a.start} for i in items
                      if i["type"] in motion.TYPES]
            graphics = out / "motion.ass"
            graphics.write_text(motion.build(mitems, w, h), encoding="utf-8")
            # kata kunci/kartu (PNG) memakai jalur overlay yang sama dengan aplikasi
            from app import overlays
            layers = []
            for i in items:
                if i["type"] in ("keyword", "card", "chips", "checklist"):
                    layers += overlays.render_item(i, out, (w, h), i["start"] - a.start, i["end"] - a.start)
        else:
            layers = []
        cmd = build_command(src, out / f"{name}.mp4", req, info, ass, None, out, camera, punches, layers, frame,
                            graphics_file=graphics)
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *cmd], check=True, cwd=out)
        ass.unlink()
        if graphics:
            graphics.unlink()
        for f in out.glob("ov_*.png"):
            f.unlink()
        print(name, "ok", flush=True)
    contact_sheet(out, list(variants), a.start, end)


if __name__ == "__main__":
    main()
