"""M2.5 Motion Library Pro: preset berbasis data, renderer per kind, guard kombinasi, intensitas, safe-zone, resep,
galeri dari engine render yang sama, Auto Style kombinasi, dan MP4 nyata lewat pipeline (pratinjau = final)."""
import re
import time

import pytest

from app import autostyle, motion, motionlib, pipeline, plan, recipe, storage, transcribe
from app.render import build_command
from app.schemas import RenderRequest
from app.subtitles import SubtitleOptions, build_ass


def _wait(c, job_id, timeout=180):
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = c.get(f"/api/jobs/{job_id}").json()
        if job["status"] in ("selesai", "gagal", "batal"):
            return job
        time.sleep(0.3)
    raise AssertionError("job timeout")


def _fx(pid, s, e, i=None, **params):
    return {"id": i or pid, "type": "fx", "preset": pid, "params": params, "start": s, "end": e}


def _tr(n=24, step=0.45):
    words = [{"word": f"kata{i}", "start": round(i * step, 2), "end": round(i * step + step * 0.8, 2)} for i in range(n)]
    return {"segments": [{"start": 0, "end": words[-1]["end"], "text": " ".join(w["word"] for w in words),
                          "words": words}]}


# ------------------------------------------------------------------------------------------- data & engine

def test_preset_data_is_valid_and_every_kind_has_a_renderer():
    ps = motionlib.presets()
    ids = [p["id"] for p in ps]
    assert len(ids) == len(set(ids)) >= 15
    cats = {p["category"] for p in ps}
    assert cats == set(motionlib.categories())                     # 6 kategori, masing-masing berisi
    from app import motion_kinds
    for p in ps:
        assert p["label"] and p["desc"] and p["controls"] and 1 <= p["weight"] <= 3, p["id"]
        if p["kind"] in motionlib.ASS_KINDS:
            assert hasattr(motion_kinds, f"_k_{p['kind']}"), p["id"]
        elif p["kind"] == "caption":
            from app.subtitles import STYLES
            assert p["defaults"]["anim"] in STYLES, p["id"]
        else:
            assert p["kind"] in ("camera", "transition"), p["id"]
    pub = motionlib.public()
    assert sum(c["count"] for c in pub["categories"]) == len(ps) and pub["levels"] == ["ringan", "seimbang", "dinamis"]


def _signature(pid):
    """Teknik libass/FFmpeg yang dipakai preset (bukan warna/teks): harus berbeda antarpreset."""
    p = motionlib.get(pid)
    spec = motionlib.resolve(_fx(pid, 0.5, 4.0), "seimbang")
    if p["kind"] in motionlib.ASS_KINDS:
        ass = motion.build([{**spec, "type": "fx"}], 1080, 1920)
        return frozenset(re.findall(r"\\(move|fscx|fscy|clip|fsp|frz|blur|p1|fad|pos|1a|an\d)", ass)) | {p["kind"]}
    if p["kind"] == "caption":
        return frozenset({"caption", p["defaults"]["anim"]})
    terms = motionlib.camera_terms([spec])
    return frozenset(k for k, v in terms.items() if v) | {p["defaults"].get("move") or p["defaults"].get("effect")}


def test_presets_are_visually_different_techniques_not_just_colour():
    sigs = {p["id"]: _signature(p["id"]) for p in motionlib.presets()}
    seen = {}
    for pid, sig in sigs.items():
        assert sig not in seen, f"{pid} sama tekniknya dengan {seen.get(sig)}"
        seen[sig] = pid


def test_ass_kinds_render_text_and_respect_safe_place_and_speed():
    spec = motionlib.resolve(_fx("title.slam_stack", 1.0, 3.5, text="Sedekah membuka pintu rezeki"), "seimbang")
    a = motion.build([{**spec, "type": "fx", "place": {"y": 0.2, "scale": 0.8}}], 1080, 1920)
    assert "SEDEKAH" in a and "REZEKI" in a
    tops = [int(m) for m in re.findall(r"\\pos\(540,(\d+)\)", a)]
    assert tops and min(tops) < 1920 * 0.3                          # posisi safe-zone dipakai
    fast = motionlib.resolve(_fx("title.mask_wipe", 0, 3, speed=2.0), "seimbang")
    slow = motionlib.resolve(_fx("title.mask_wipe", 0, 3, speed=0.5), "seimbang")
    dur = lambda s: int(re.search(r"\\t\(0,(\d+),", motion.build([{**s, "type": "fx"}], 1080, 1920)).group(1))  # noqa: E731
    assert dur(fast) < dur(slow)


def test_params_are_cleaned_no_ass_injection():
    item = plan.normalize([{"type": "fx", "preset": "title.cinematic", "start": 0, "end": 3,
                            "params": {"font": "Arial}\\pos(0,0)", "accent": "red", "text": "Judul {\\b1}",
                                       "intensity": 99, "evil": 1}}], 0, 10)[0]
    assert item["params"] == {"intensity": 2.0, "text": "Judul {\\b1}"}
    spec = motionlib.resolve(item, "seimbang")
    ass = motion.build([{**spec, "type": "fx"}], 1080, 1920)
    assert "JUDUL (B1)" in ass.upper() and "\\fnArial}" not in ass
    assert plan.normalize([{"type": "fx", "preset": "tidak.ada", "start": 0, "end": 3}], 0, 10) == []


# ------------------------------------------------------------------------------------------- kombinasi & intensitas

def test_guard_avoids_excess_and_explains():
    specs = [motionlib.resolve(x, "seimbang") for x in [
        _fx("cam.ken_burns", 0, 6, "kb"), _fx("cam.punch_in", 2, 4, "pi"),          # dua kamera bersamaan
        _fx("title.cinematic", 0, 3, "t1"), _fx("title.slam_stack", 1, 3, "t2"),     # dua judul bersamaan
        _fx("tr.flash", 8, 8.4, "f1"), _fx("tr.whip", 9, 9.4, "f2"),                 # transisi terlalu rapat
        _fx("cap.karaoke_pop", 0, 20, "c1")]]
    kept, skipped = motionlib.guard(specs, "seimbang")
    assert {"pi", "t2", "f2"} <= set(skipped) and "c1" not in skipped
    assert all(isinstance(v, str) and v for v in skipped.values())
    # tingkat dinamis mengizinkan transisi lebih rapat; ringan lebih ketat
    _, sk_d = motionlib.guard([motionlib.resolve(_fx("tr.flash", 8, 8.4, "a"), "dinamis"),
                               motionlib.resolve(_fx("tr.flash", 11.5, 11.9, "b"), "dinamis")], "dinamis")
    _, sk_r = motionlib.guard([motionlib.resolve(_fx("tr.flash", 8, 8.4, "a"), "ringan"),
                               motionlib.resolve(_fx("tr.flash", 11.5, 11.9, "b"), "ringan")], "ringan")
    assert not sk_d and "b" in sk_r


def test_intensity_levels_scale_motion():
    amp = {}
    for lv in motionlib.LEVELS:
        t = motionlib.camera_terms([motionlib.resolve(_fx("cam.punch_in", 1, 3), lv)])
        amp[lv] = float(t["z"][0].split("*")[0])
    assert amp["ringan"] < amp["seimbang"] < amp["dinamis"]
    assert RenderRequest(start=0, end=5).motion_level == "seimbang"                # bawaan


def test_camera_and_transition_reach_ffmpeg_command():
    req = RenderRequest(start=0, end=8, aspect="9:16")
    cam = motionlib.camera_terms([motionlib.resolve(_fx("cam.handheld", 1, 5), "seimbang"),
                                  motionlib.resolve(_fx("tr.whip", 6, 6.5), "seimbang")], req.speed)
    from pathlib import Path
    cmd = " ".join(build_command(Path("in.mp4"), Path("out.mp4"), req, {"width": 1280, "height": 720,
                                 "has_audio": False}, None, None, Path("."), camera_fx=cam))
    assert "sin(6.2832" in cmd and "avgblur" in cmd and "eval=frame" in cmd


def test_camera_fx_skipped_on_board_layouts_with_reason():
    req = RenderRequest(start=0, end=10, aspect="9:16")
    frame = {"segments": [{"start": 0, "end": 5, "layout": "face"}, {"start": 5, "end": 10, "layout": "split"}]}
    kept, skipped = pipeline.fx_plan(req, [_fx("cam.ken_burns", 1, 4, "ok"), _fx("cam.punch_in", 6, 8, "no"),
                                           _fx("tr.flash", 7, 7.4, "fl")], frame)
    assert [k["id"] for k in kept] == ["ok", "fl"] and "papan" in skipped["no"]


def test_caption_preset_per_range_only_changes_that_range():
    tr = _tr()
    spec = motionlib.resolve(_fx("cap.word_bounce", 4.0, 7.0), "seimbang")
    o = SubtitleOptions(style="karaoke", sections=((4.0, 7.0, "wordbounce", motionlib.caption_extra(spec)),))
    ev = [x for x in build_ass(tr, 0, 10.8, 1080, 1920, o).splitlines() if x.startswith("Dialogue:")]
    bounce = [e for e in ev if r"\frz" in e]
    assert bounce and all("0:00:04" <= e.split(",")[1] < "0:00:07" for e in bounce)
    assert all(len(re.findall(r"KATA\d+", e)) == 1 for e in bounce)              # satu kata per baris
    assert any(r"\frz" not in e and len(re.findall(r"KATA\d+", e)) == 3 for e in ev)   # di luar rentang: karaoke


def test_fx_title_counts_as_big_text_for_subtitle_dedup():
    tr = {"segments": [{"start": 0, "end": 3, "text": "sedekah membuka pintu rezeki", "words": [
        {"word": w, "start": i * 0.5, "end": i * 0.5 + 0.4} for i, w in enumerate("sedekah membuka pintu rezeki".split())]}]}
    req = RenderRequest(start=0, end=5)
    win = pipeline.text_windows(req, [_fx("title.slam_stack", 0, 3, text="Sedekah membuka pintu rezeki")], tr)
    assert win and win[0][2] == ("sedekah", "membuka", "pintu", "rezeki")


# ------------------------------------------------------------------------------------------- Auto Style

@pytest.mark.parametrize("cat", list(autostyle.CATEGORIES))
def test_auto_style_combo_for_every_category(cat):
    items = autostyle.motion_combo(cat, 0, 40, _tr(80))
    assert items and all(i["type"] == "fx" and motionlib.get(i["preset"]) for i in items)
    specs = [motionlib.resolve(i, "seimbang") for i in items]
    kept, skipped = motionlib.guard(specs, "seimbang")
    assert not skipped, skipped                                    # kombinasi bawaan tidak berlebihan
    assert sum(s["category"] == "camera" for s in specs) <= 1 and sum(s["category"] == "titles" for s in specs) <= 1
    assert not any(i["preset"] == "gfx.bar_chart" for i in items)  # data grafik tidak dikarang


def test_auto_style_progress_only_from_spoken_percentage():
    tr = _tr(80)
    assert not any(i["preset"] == "gfx.progress" for i in autostyle.motion_combo("fundraising", 0, 40, tr))
    tr["segments"][0]["words"][30]["word"] = "72"
    tr["segments"][0]["words"][31]["word"] = "persen"
    tr["segments"][0]["text"] = " ".join(w["word"] for w in tr["segments"][0]["words"])
    prog = [i for i in autostyle.motion_combo("fundraising", 0, 40, tr) if i["preset"] == "gfx.progress"]
    assert prog and prog[0]["params"]["value"] == 72


# ------------------------------------------------------------------------------------------- resep & MP4 nyata

def test_recipe_keeps_level_and_fx_and_old_recipes_default():
    r = recipe.Recipe.model_validate({"motion": {"speed": 1.0, "zoom": "none", "fade": True}})
    assert r.motion.level == "seimbang"                            # resep M1/M2 lama
    r2 = recipe.Recipe.model_validate({"motion": {"level": "dinamis"}, "overlays": [
        _fx("title.cinematic", 0, 3, "a1", text="Judul")]})
    dumped = r2.model_dump(mode="json")
    again = recipe.Recipe.model_validate(dumped)
    assert again.motion.level == "dinamis" and again.overlays[0].preset == "title.cinematic"
    assert again.overlays[0].params == {"text": "Judul"}


def test_fx_items_render_real_mp4_preview_equals_final(client, sample_video, fake_transcript, monkeypatch):
    monkeypatch.setattr(transcribe, "transcribe", lambda *a, **k: fake_transcript)
    with sample_video.open("rb") as fh:
        res = client.post("/api/projects", files={"file": ("m.mp4", fh, "video/mp4")}).json()
    pid = res["project"]["id"]
    assert _wait(client, res["job"]["id"])["status"] == "selesai"
    items = [_fx("cap.highlight_box", 0, 4, "cap"), _fx("title.cinematic", 0.2, 3.0, "ttl", text="Judul Uji"),
             _fx("cam.ken_burns", 3.2, 7.5, "cam"), _fx("tr.flash", 4.0, 4.4, "tr"),
             _fx("gfx.progress", 4.5, 7.5, "gfx", value=40, text="Uji progres"), _fx("cta.follow_bar", 5.0, 8.0, "cta")]
    saved = client.put(f"/api/projects/{pid}/plan", json=items)
    assert saved.status_code == 200 and {i["preset"] for i in saved.json()} == {i["preset"] for i in items}
    rec = client.get(f"/api/projects/{pid}/recipe").json()
    body = rec["recipe"]
    body["motion"]["level"] = "dinamis"
    put = client.put(f"/api/projects/{pid}/recipe", json={"version": rec["version"], "recipe": body})
    assert put.status_code == 200, put.text
    v = put.json()["version"]
    reopened = client.get(f"/api/projects/{pid}/recipe").json()["recipe"]
    assert reopened["motion"]["level"] == "dinamis" and sum(o["type"] == "fx" for o in reopened["overlays"]) == 6
    lay = client.get(f"/api/projects/{pid}/motion-layout", params={"start": 0, "end": 8}).json()["items"]
    assert "ttl" in lay and "gfx" in lay                           # grafis fx ikut safe-zone
    # 6 efek dalam 8 dtk: guard melewati kilat (terlalu ramai) dan menjelaskannya di editor
    assert lay["tr"]["action"] == "dilewati" and "ramai" in lay["tr"]["reason"]
    # hapus CTA (item fx bisa dihapus) -> kilat dipakai lagi
    saved = client.put(f"/api/projects/{pid}/plan", json=[i for i in items if i["id"] != "cta"])
    assert saved.status_code == 200 and not any(i["id"] == "cta" for i in saved.json())
    v = client.get(f"/api/projects/{pid}/recipe").json()["version"]
    lay = client.get(f"/api/projects/{pid}/motion-layout", params={"start": 0, "end": 8}).json()["items"]
    assert lay.get("tr", {}).get("action") != "dilewati"

    seen = []
    real_build, real_cmd = motion.build, pipeline.build_command

    def spy_build(items, w, h):
        seen.append(("ass", w, tuple(sorted(i.get("preset", i["type"]) for i in items))))
        return real_build(items, w, h)

    def spy_cmd(*a, **k):
        seen.append(("cam", bool((k.get("camera_fx") or {}).get("z")), bool((k.get("camera_fx") or {}).get("bright"))))
        return real_cmd(*a, **k)
    monkeypatch.setattr(motion, "build", spy_build)
    monkeypatch.setattr(pipeline, "build_command", spy_cmd)
    prev = client.post(f"/api/projects/{pid}/preview-clip", json={"version": v, "start": 0, "end": 8})
    assert prev.status_code == 200, prev.text
    job = client.post(f"/api/projects/{pid}/render-recipe", json={"version": v, "start": 0, "end": 8}).json()
    done = _wait(client, job["id"])
    assert done["status"] == "selesai", done
    ass_calls = [s for s in seen if s[0] == "ass"]
    cam_calls = [s for s in seen if s[0] == "cam"]
    assert len(ass_calls) == 2 and ass_calls[0][2] == ass_calls[1][2]          # elemen sama di pratinjau & final
    assert {"title.cinematic", "gfx.progress"} <= set(ass_calls[0][2])
    assert cam_calls == [("cam", True, True), ("cam", True, True)]
    from app import media
    out = storage.project_dir(pid) / "renders" / done["result"]["file"]
    info = media.probe(out)
    assert (info["width"], info["height"]) == (1080, 1920) and abs(info["duration"] - 8) < 0.3


def test_motion_gallery_renders_with_real_engine(client):
    lst = client.get("/api/motion-presets").json()
    assert len(lst["presets"]) >= 15 and set(lst["combos"]) == set(autostyle.CATEGORIES)
    r = client.get("/api/motion-presets/title.slam_stack/thumb.png")
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"
    m = client.get("/api/motion-presets/cam.ken_burns/preview.mp4")
    assert m.status_code == 200 and m.content[4:8] == b"ftyp"
    assert client.get("/api/motion-presets/tidak.ada/thumb.png").status_code == 404


def test_user_position_is_first_choice_but_safe_zone_still_applies():
    req = RenderRequest(start=0, end=6, aspect="9:16")
    top = pipeline.layout_items(req, [_fx("gfx.progress", 0.5, 4, "p", pos_y=0.2)], None, 1280, 720, 1080, 1920)
    assert abs(top["p"]["y"] - 0.2) < 1e-6
    # posisi pilihan di pita subtitle (0,78) -> dipindah dengan alasan
    low = pipeline.layout_items(req, [_fx("gfx.progress", 0.5, 4, "p", pos_y=0.78)], None, 1280, 720, 1080, 1920)
    assert abs(low["p"]["y"] - 0.78) > 0.05 and low["p"]["action"] in ("dipindah", "diperkecil")


def test_detail_area_like_bookshelf_does_not_force_split_layout():
    """Uji video kajian nyata M2.5: rak buku (area 'detail', tepi padat) di belakang imam tidak boleh memicu
    tata letak Materi + Wajah hanya karena dekat pemateri; papan tulis sungguhan tetap memicunya."""
    from app import framing
    face = [0.49, 0.40, 0.06, 0.10]
    shelf = {"f": face, "b": [0.375, 0.22, 0.625, 0.78], "bk": "detail", "act": 0.9}
    assert framing._label(shelf, shelf["b"])[0] == "face"
    assert framing._label({**shelf, "act": 3.0}, shelf["b"])[0] == "split"          # layar/benda benar berubah
    board = {**shelf, "bk": "papan", "act": 0.0}
    assert framing._label(board, board["b"])[0] == "split"


def test_all_60_presets_available_per_category_target():
    from collections import Counter
    c = Counter(p["category"] for p in motionlib.presets())
    assert c == {"captions": 15, "titles": 10, "camera": 10, "transitions": 10, "graphics": 10, "story": 5}


@pytest.mark.parametrize("pid", [p["id"] for p in motionlib.presets() if p["kind"] in motionlib.ASS_KINDS])
def test_every_ass_preset_renders_events(pid):
    spec = motionlib.resolve(_fx(pid, 0.5, 4.0), "seimbang")
    ass = motion.build([{**spec, "type": "fx"}], 1080, 1920)
    events = [x for x in ass.splitlines() if x.startswith("Dialogue:")]
    assert len(events) >= 1, pid
    assert all("{" in e and e.count("{") == e.count("}") for e in events), pid      # tag ASS seimbang


def test_new_ffmpeg_camera_and_transition_filters_run_for_real(sample_video, tmp_path):
    import subprocess
    from app import media
    info = media.probe(sample_video)
    req = RenderRequest(start=0, end=6, aspect="9:16", normalize_audio=False)
    specs = [motionlib.resolve(_fx(pid, s, e), "seimbang") for pid, s, e in
             [("cam.rotate", 0.2, 2.0), ("tr.pixelate", 2.2, 2.7), ("tr.glitch", 3.0, 3.4), ("cam.focus", 3.6, 5.6),
              ("tr.spin", 5.0, 5.5)]]
    cam = motionlib.camera_terms(specs, req.speed)
    out = tmp_path / "fx.mp4"
    cmd = build_command(sample_video, out, req, info, None, None, tmp_path, camera_fx=cam)
    joined = " ".join(cmd)
    assert "rotate=" in joined and "pixelize=" in joined and "rgbashift=" in joined
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *cmd], check=True, cwd=tmp_path)
    v = media.probe(out)
    assert (v["width"], v["height"]) == (1080, 1920) and abs(v["duration"] - 6) < 0.3


def test_gallery_uses_installer_bundle_and_renders_missing_in_background(client, tmp_path, monkeypatch):
    from app import motion_gallery
    p = motionlib.get("gfx.ring")
    key, stem = motion_gallery._key(p), "gfx_ring"
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / f"{stem}-{key}.mp4").write_bytes(b"\x00\x00\x00\x18ftypisom")
    (bundle / f"{stem}-{key}.png").write_bytes(b"\x89PNG")
    monkeypatch.setattr(motion_gallery, "BUNDLED", bundle)
    monkeypatch.setattr(motion_gallery, "CACHE", tmp_path / "cache")
    monkeypatch.setattr(motion_gallery, "_failed", {})          # status modul dari tes lain tidak ikut
    monkeypatch.setattr(motion_gallery, "_queue", [])
    assert motion_gallery.cached("gfx.ring")[0].parent == bundle          # bundel dipakai tanpa render
    st = client.get("/api/motion-presets/status", params={"ids": "gfx.ring,tr.iris"}).json()
    assert st["gfx.ring"] == "siap" and st["tr.iris"] == "antre"          # yang belum ada diantrekan
    deadline = time.time() + 180
    while (st := client.get("/api/motion-presets/status", params={"ids": "tr.iris"}).json())["tr.iris"] != "siap":
        assert time.time() < deadline, (st, motion_gallery._failed, motion_gallery._queue)
        time.sleep(0.3)
    assert (tmp_path / "cache").is_dir() and list((tmp_path / "cache").glob("tr_iris-*.mp4"))
    # tanpa ids: hanya status, tidak mengantrekan seluruh galeri
    full = client.get("/api/motion-presets/status").json()
    assert len(full) == len(motionlib.presets()) and not motion_gallery._queue
