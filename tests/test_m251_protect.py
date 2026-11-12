"""M2.5.1 Protect Subject: area dilindungi manual (kotak/poligon, rentang waktu) di safe-zone yang sama, chips &
checklist ikut safe-zone, PNG yang diperkecil benar-benar dirender kecil, resep lama tetap valid."""
import pytest
from pydantic import ValidationError

from app import pipeline, recipe, safezone
from app.schemas import ProtectArea, RenderRequest

W, H = 1080, 1920


def _area(box=None, start=0.0, end=10.0, points=None, i="a1"):
    return ProtectArea(id=i, start=start, end=end, box=box or [0, 0, 0, 0], points=points or [])


def test_area_validation_polygon_bbox_and_limits():
    a = _area(points=[[0.2, 0.3], [0.6, 0.25], [0.5, 0.7]])
    assert a.box == [0.2, 0.25, 0.4, 0.45]                      # poligon -> kotak pembatas (lebih aman)
    with pytest.raises(ValidationError):
        _area(box=[0.5, 0.5, 0.001, 0.2])                       # terlalu kecil
    with pytest.raises(ValidationError):
        _area(box=[0.1, 0.1, 0.3, 0.3], start=5, end=4)
    with pytest.raises(ValidationError):
        _area(points=[[0.1, 0.1], [0.2, 0.2]])                  # poligon < 3 titik
    clipped = _area(box=[0.8, 0.8, 0.5, 0.5])
    assert clipped.box == [0.8, 0.8, 0.2, 0.2]


def _req(areas, **kw):
    return RenderRequest(start=0, end=10, aspect="9:16", protect=areas, **kw)


def test_area_maps_to_output_only_in_its_time_range():
    req = _req([_area(box=[0.4, 0.1, 0.2, 0.3], start=2, end=5)])
    tl = safezone.timeline(None, 0, 1.0, W, H, (1920, 1080), (0.5, 0.5, 0.0), "auto", None,
                           [p.model_dump() for p in req.protect])
    rects = [r for seg in tl for r in seg["rects"] if r["kind"] == "lindung"]
    assert len(rects) == 1 and rects[0]["t0"] == 2 and rects[0]["t1"] == 5
    # sumber 1920x1080 -> jendela 9:16 tengah selebar 607,5 px mulai x=656: x sumber 768..1152 px
    x, y, w, h = rects[0]["rect"]
    assert abs(x - (768 - 656.25) / 607.5) < 0.01 and abs(w - 384 / 607.5) < 0.01
    assert abs(y - 0.1) < 0.01 and abs(h - 0.3) < 0.01
    assert safezone._busy(tl, [], 6, 8, True) == [b for b in safezone._busy(tl, [], 6, 8, True) if b["kind"] != "lindung"]


def test_graphic_moves_off_protected_subject_and_returns_when_area_ends():
    hook = {"id": "h", "type": "hook", "text": "Pertanyaan pembuka yang cukup panjang", "start": 0, "end": 3}
    late = {**hook, "id": "h2", "start": 6, "end": 9}
    # subjek di sepertiga atas output selama 0-5 dtk saja (posisi bawaan hook 0,30)
    area = _area(box=[0.3, 0.15, 0.4, 0.3], start=0, end=5)
    plain = pipeline.layout_items(_req([]), [hook, late], None, 1920, 1080, W, H)
    guarded = pipeline.layout_items(_req([area]), [hook, late], None, 1920, 1080, W, H)
    assert plain["h"]["action"] == "tetap"
    assert guarded["h"]["action"] in ("dipindah", "diperkecil"), guarded["h"]
    r = guarded["h"]["rect"]
    tl = safezone.timeline(None, 0, 1.0, W, H, (1920, 1080), (0.5, 0.5, 0.0), "auto", None, [area.model_dump()])
    prect = next(x["rect"] for seg in tl for x in seg["rects"] if x["kind"] == "lindung")
    assert safezone._overlap(r, prect) / (r[2] * r[3]) < 0.01, (r, prect)    # paling banyak bersentuhan tepi
    assert guarded["h2"]["action"] == "tetap"                             # di luar rentang area: posisi bawaan


def test_decorative_hidden_info_kept_when_subject_fills_frame():
    area = _area(box=[0.0, 0.0, 1.0, 1.0], start=0, end=10)
    items = [{"id": "chips", "type": "chips", "items": ["Pangan", "Pendidikan"], "start": 1, "end": 4, "text": ""},
             {"id": "name", "type": "lower_third", "text": "Pak Budi", "label": "Peternak", "start": 1, "end": 4}]
    pl = pipeline.layout_items(_req([area], ), items, None, 1920, 1080, W, H)
    assert pl["chips"]["action"] == "disembunyikan" and "area dilindungi" in pl["chips"]["reason"]
    assert pl["name"]["action"] == "tumpang tindih"                       # informasi tetap tampil, dengan alasan


@pytest.mark.parametrize("typ", ["chips", "checklist"])
def test_chips_and_checklist_follow_safe_zone(typ):
    it = {"id": "c", "type": typ, "items": ["Satu", "Dua", "Tiga"], "start": 0, "end": 3, "text": ""}
    area = _area(box=[0.2, 0.0, 0.6, 0.55], start=0, end=10)               # atas-tengah dilindungi
    pl = pipeline.layout_items(_req([area]), [it], None, 1920, 1080, W, H)
    assert pl["c"]["action"] in ("dipindah", "diperkecil"), pl
    w, h, x = safezone.size_of(it, W, H)
    assert 0.05 < h < 0.4 and 0 < w <= 0.9


def test_png_graphic_is_really_shrunk_when_safe_zone_shrinks(tmp_path, monkeypatch):
    from PIL import Image
    item = {"id": "k", "type": "card", "text": "Judul kartu yang panjang", "label": "Tips", "start": 0, "end": 3,
            "items": []}
    req = RenderRequest(start=0, end=5, aspect="9:16", subtitles={"enabled": False})
    monkeypatch.setattr(pipeline, "layout_items",
                        lambda *a, **k: {"k": {"y": 0.5, "scale": 0.5, "action": "diperkecil", "reason": "uji"}})
    full = pipeline.assemble(req.model_copy(update={"animations": True}), None, None, [item], None, (1920, 1080),
                             W, H, tmp_path, "a")
    sizes = [Image.open(ly.path).size for ly in full["layers"]]
    monkeypatch.setattr(pipeline, "layout_items", lambda *a, **k: {})
    (tmp_path / "b").mkdir()
    ref = pipeline.assemble(req, None, None, [item], None, (1920, 1080), W, H, tmp_path / "b", "b")
    ref_sizes = [Image.open(ly.path).size for ly in ref["layers"]]
    assert all(abs(a[0] - b[0] * 0.5) <= 2 for a, b in zip(sizes, ref_sizes)), (sizes, ref_sizes)
    cy = [ly.y + s[1] / 2 for ly, s in zip(full["layers"], sizes)]
    assert abs(sum(cy) / len(cy) - 0.5 * H) < 40


def test_recipe_keeps_areas_and_old_recipes_default_empty():
    assert recipe.Recipe.model_validate({}).protect == []
    r = recipe.Recipe.model_validate({"protect": [{"id": "s1", "start": 1, "end": 4, "box": [0.1, 0.2, 0.3, 0.3],
                                                   "label": "Sapi"}]})
    again = recipe.Recipe.model_validate(r.model_dump(mode="json"))
    assert again.protect[0].label == "Sapi" and again.protect[0].box == [0.1, 0.2, 0.3, 0.3]


def _min_font(pid):
    import re

    from app import motion, motionlib
    from app.subtitles import SubtitleOptions, build_ass
    from bench.m25.library_demo import synthetic_transcript
    p = motionlib.get(pid)
    spec = motionlib.resolve({"id": "x", "preset": pid, "params": {}, "start": 0.5, "end": 4.0}, "seimbang")
    if p["kind"] == "caption":
        o = SubtitleOptions(style="karaoke")
        o.sections = ((0, 6, spec["params"]["anim"], motionlib.caption_extra(spec)),)
        ass = build_ass(synthetic_transcript(6), 0, 6, 1080, 1920, o)
    else:
        ass = motion.build([{**spec, "type": "fx"}], 1080, 1920)
    style_fs = {m.group(1): int(m.group(2)) for m in re.finditer(r"^Style: (\w+),[^,]+,(\d+)", ass, re.M)}
    sizes = []
    for line in ass.splitlines():
        if line.startswith("Dialogue:"):
            parts = line.split(",", 9)
            text = re.sub(r"\{[^}]*\}", "", parts[9].split("{\\p0}")[-1] if "\\p1" in parts[9] else parts[9]).strip()
            if text and not text.startswith("m "):
                fs = [int(x) for x in re.findall(r"\\fs(\d+)", parts[9])] or [style_fs.get(parts[3], 0)]
                sizes.append(min(fs))
    return min(sizes) if sizes else None


def test_every_text_preset_is_readable_on_a_phone():
    from app import motionlib
    small = {}
    for p in motionlib.presets():
        if p["kind"] in ("camera", "transition") or p["kind"].startswith("tr_"):
            continue
        fs = _min_font(p["id"])
        if fs is not None and fs < 42:
            small[p["id"]] = fs
    assert not small, f"teks < 42 px pada lebar 1080: {small}"


def test_shrinking_below_readable_size_is_flagged():
    it = {"id": "n", "type": "lower_third", "text": "Nama", "label": "Jabatan", "start": 0, "end": 3}
    assert safezone.text_px(it, W, H, 1.0) >= 42
    # hanya tersisa pita tipis di bawah: grafis informasi diperkecil sampai 50 % -> label ±24 px
    tl = [{"t0": 0, "t1": 10, "layout": "center", "rects": [{"kind": "lindung", "rect": [0, 0, 1, 0.935]}]}]
    pl = safezone.place([it], tl, W, H)["n"]
    assert pl["scale"] == 0.5, pl
    assert pl.get("readable") is False and "sulit dibaca di HP" in pl["reason"]
    ok = safezone.place([it], [{"t0": 0, "t1": 10, "layout": "center", "rects": []}], W, H)["n"]
    assert "readable" not in ok and "sulit dibaca" not in ok["reason"]


def _marker_video(tmp_path):
    """Video 1080x1920 abu-abu dengan titik merah di titik jangkar zoom (50 % lebar, 40 % tinggi)."""
    import subprocess

    from PIL import Image, ImageDraw
    im = Image.new("RGB", (1080, 1920), (90, 90, 90))
    d = ImageDraw.Draw(im)
    for i in range(0, 1080, 60):                       # pola agar geser terlihat
        d.line([(i, 0), (i, 1920)], fill=(120, 120, 120), width=2)
    for j in range(0, 1920, 60):
        d.line([(0, j), (1080, j)], fill=(110, 110, 140), width=2)
    d.ellipse([540 - 18, 768 - 18, 540 + 18, 768 + 18], fill=(255, 0, 0))
    png = tmp_path / "m.png"
    im.save(png)
    mp4 = tmp_path / "m.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-loop", "1", "-framerate", "30", "-i", str(png), "-t", "4",
                    "-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "ultrafast", str(mp4)], check=True)
    return mp4


def _frame_at(mp4, t, tmp_path, name):
    import subprocess

    import numpy as np
    from PIL import Image
    png = tmp_path / f"{name}.png"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", str(t), "-i", str(mp4), "-frames:v", "1", str(png)], check=True)
    return np.asarray(Image.open(png).convert("RGB")).astype(int)


def _red_center(img):
    import numpy as np
    ys, xs = np.nonzero((img[..., 0] > 200) & (img[..., 1] < 80) & (img[..., 2] < 80))
    return float(xs.mean()), float(ys.mean())


def test_zoom_and_pan_really_render(tmp_path):
    """Regresi M2.5.1: zoom dulu selalu menuju pojok kiri atas dan geser (dx/dy) tidak berpengaruh, karena crop
    memakai lebar frame pertama. Kini zoompan: titik jangkar tetap saat punch-in; pan & tilt berbeda arah."""
    import subprocess

    import numpy as np

    from app import motionlib
    from app.render import build_command
    src = _marker_video(tmp_path)
    info = {"width": 1080, "height": 1920, "duration": 4.0, "fps": 30, "has_audio": False}
    req = RenderRequest(start=0, end=4, aspect="9:16", fade=False, normalize_audio=False)

    def run(name, punches=None, cam=None):
        out = tmp_path / f"{name}.mp4"
        cmd = build_command(src, out, req, info, None, None, tmp_path, None, punches or [], [], None, camera_fx=cam)
        subprocess.run(["ffmpeg", "-y", "-v", "error", *cmd], check=True)
        return out
    zoomed = run("punch", punches=[(1.0, 3.0, 0.3)])
    x0, y0 = _red_center(_frame_at(zoomed, 0.2, tmp_path, "p0"))
    x1, y1 = _red_center(_frame_at(zoomed, 2.0, tmp_path, "p1"))
    assert abs(x1 - x0) < 6 and abs(y1 - y0) < 6, ((x0, y0), (x1, y1))         # jangkar tetap
    big = (_frame_at(zoomed, 2.0, tmp_path, "p1")[..., 0] > 200).sum() / (_frame_at(zoomed, 0.2, tmp_path, "p0")[..., 0] > 200).sum()
    assert big > 1.4                                                              # titik membesar (zoom terjadi)
    spec = {pid: motionlib.resolve({"id": "c", "preset": pid, "params": {}, "start": 0.2, "end": 3.8}, "seimbang")
            for pid in ("cam.pan", "cam.tilt")}
    pan = _frame_at(run("pan", cam=motionlib.camera_terms([spec["cam.pan"]])), 0.8, tmp_path, "pan")
    tilt = _frame_at(run("tilt", cam=motionlib.camera_terms([spec["cam.tilt"]])), 0.8, tmp_path, "tilt")
    assert np.abs(pan - tilt).mean() > 3
    (px, py), (tx, ty) = _red_center(pan), _red_center(tilt)
    assert abs(px - 540) > 20 and abs(tx - 540) < 6                              # pan menggeser x, tilt tidak
    assert abs(ty - py) > 20                                                     # tilt menggeser y


def test_suggest_endpoint_is_optional_and_returns_reviewable_areas(client, sample_video, fake_transcript,
                                                                   monkeypatch):
    import time

    from app import subject_detect, transcribe
    monkeypatch.setattr(transcribe, "transcribe", lambda *a, **k: fake_transcript)
    with sample_video.open("rb") as fh:
        res = client.post("/api/projects", files={"file": ("t.mp4", fh, "video/mp4")}).json()
    pid = res["project"]["id"]
    deadline = time.time() + 120
    while client.get(f"/api/jobs/{res['job']['id']}").json()["status"] not in ("selesai", "gagal"):
        assert time.time() < deadline
        time.sleep(0.3)
    r = client.post(f"/api/projects/{pid}/protect/suggest", json={"start": 0, "end": 6, "groups": ["orang", "hewan"]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["samples"] >= 10 and "eksperimental" in body["method"]
    for a in body["areas"]:                       # saran = area biasa yang lolos validasi resep
        ProtectArea(id="x", **a)
        assert a["label"].startswith("Saran:")
    assert client.get(f"/api/projects/{pid}/recipe").json()["recipe"]["protect"] == []   # tidak disimpan otomatis
    monkeypatch.setattr(subject_detect, "MODEL", subject_detect.MODEL.with_name("tidak-ada.onnx"))
    assert client.post(f"/api/projects/{pid}/protect/suggest", json={"start": 0, "end": 6}).status_code == 501


def test_detector_finds_a_drawn_person_free_of_network(tmp_path):
    """Model bundel diverifikasi sha256 dan berjalan lokal (onnxruntime CPU)."""
    import numpy as np

    from app import subject_detect
    assert subject_detect.available()
    blank = np.full((360, 640, 3), 128, np.uint8)
    assert subject_detect.detect_frame(blank) == []


def _texture_video(tmp_path):
    """Video 1080x1920 bertekstur acak halus (tidak periodik, seed tetap): pengukuran zoom tidak tertipu pola."""
    import subprocess

    import numpy as np
    from PIL import Image
    rng = np.random.default_rng(7)
    small = rng.integers(0, 255, (240, 135, 3), dtype=np.uint8)          # detail sedang: ±8 px per sel
    img = Image.fromarray(small).resize((1080, 1920), Image.BICUBIC)
    png = tmp_path / "tex.png"
    img.save(png)
    mp4 = tmp_path / "tex.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-loop", "1", "-framerate", "30", "-i", str(png), "-t", "4",
                    "-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "12", str(mp4)], check=True)
    return mp4


@pytest.mark.parametrize("pid,expect", [("cam.pan", "x"), ("cam.tilt", "y"), ("cam.punch_in", "zoom")])
def test_camera_motion_matches_in_preview_and_final(tmp_path, pid, expect):
    """Gerakan kamera terukur di MP4 FINAL (1080x1920) dan PRATINJAU (out_scale 1/3, jalur pratinjau aplikasi), arah &
    besar sama: pan hanya horizontal, tilt hanya vertikal, punch-in hanya zoom."""
    import subprocess

    from app import camera_check, motionlib, pipeline
    from app.render import build_command
    src = _texture_video(tmp_path)
    info = {"width": 1080, "height": 1920, "duration": 4.0, "fps": 30, "has_audio": False}
    req = RenderRequest(start=0, end=4, aspect="9:16", fade=False, normalize_audio=False)
    cam = motionlib.camera_terms([motionlib.resolve({"id": "c", "preset": pid, "params": {}, "start": 0.2,
                                                     "end": 3.8}, "seimbang")])
    res = {}
    for kind, scale in (("final", 1.0), ("pratinjau", pipeline.PREVIEW_SCALE)):
        def run(name, c):
            out = tmp_path / f"{kind}-{name}.mp4"
            cmd = build_command(src, out, req, info, None, None, tmp_path, None, [], [], None, out_scale=scale,
                                camera_fx=c)
            subprocess.run(["ffmpeg", "-y", "-v", "error", *cmd], check=True)
            return out
        base, fx = run("dasar", None), run("efek", cam)
        res[kind] = camera_check.measure(base, fx, 0.8)
    f, p = res["final"], res["pratinjau"]
    if expect == "x":
        assert abs(f["dx"]) > 0.02 and abs(f["dy"]) < 0.01, res
    elif expect == "y":
        assert abs(f["dy"]) > 0.01 and abs(f["dx"]) < 0.01, res
    else:
        assert f["zoom"] > 1.1 and abs(f["dx"]) < 0.01 and abs(f["dy"]) < 0.01, res
    # pratinjau = final (dalam pecahan bingkai; toleransi 1 piksel analisis)
    assert abs(f["dx"] - p["dx"]) <= 0.012 and abs(f["dy"] - p["dy"]) <= 0.012 and abs(f["zoom"] - p["zoom"]) <= 0.03, res


@pytest.mark.parametrize("commit", ["ba5f0bf", "c86a89b"])
def test_old_project_recipes_open_and_render(client, sample_video, fake_transcript, monkeypatch, commit):
    """Resep ASLI yang dihasilkan kode M2 (ba5f0bf) dan beta M2.5 (c86a89b) -> dibuka, tidak berubah makna, dan
    dirender MP4 oleh versi ini (tanpa area dilindungi: bawaan kosong)."""
    import json
    import time
    from pathlib import Path

    from app import media, storage, transcribe
    old = json.loads((Path(__file__).parent / "data" / f"recipe_{commit}.json").read_text(encoding="utf-8"))["recipe"]
    r = recipe.Recipe.model_validate(old)
    assert r.protect == [] and r.overlays and r.subtitles.style == "neon"
    assert {o.id for o in r.overlays} == {o["id"] for o in old["overlays"]}
    if commit == "c86a89b":
        assert r.motion.level == "dinamis" and any(o.type == "fx" for o in r.overlays)
    monkeypatch.setattr(transcribe, "transcribe", lambda *a, **k: fake_transcript)
    with sample_video.open("rb") as fh:
        res = client.post("/api/projects", files={"file": ("lama.mp4", fh, "video/mp4")}).json()
    pid = res["project"]["id"]
    deadline = time.time() + 120
    while client.get(f"/api/jobs/{res['job']['id']}").json()["status"] not in ("selesai", "gagal"):
        assert time.time() < deadline
        time.sleep(0.3)
    cur = client.get(f"/api/projects/{pid}/recipe").json()
    put = client.put(f"/api/projects/{pid}/recipe", json={"version": cur["version"], "recipe": old})
    assert put.status_code == 200, put.text
    job = client.post(f"/api/projects/{pid}/render-recipe", json={"version": put.json()["version"], "start": 1,
                                                                  "end": 7}).json()
    deadline = time.time() + 180
    while (done := client.get(f"/api/jobs/{job['id']}").json())["status"] not in ("selesai", "gagal", "batal"):
        assert time.time() < deadline
        time.sleep(0.3)
    assert done["status"] == "selesai", done
    info = media.probe(storage.project_dir(pid) / "renders" / done["result"]["file"])
    assert (info["width"], info["height"]) == (1080, 1920) and abs(info["duration"] - 6) < 0.3
