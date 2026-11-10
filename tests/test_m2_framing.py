"""M2.1 Smart Framing: deteksi area materi (heuristik), rencana Otomatis/Wajah/Materi/Manual, pilihan per bagian,
render per bagian menjadi MP4 nyata, kompatibilitas resep lama, dan pratinjau MP4 dari pipeline yang sama."""
import json
import subprocess
import time

import numpy as np
import pytest

from app import framing, media, recipe, storage, transcribe
from app.render import build_command
from app.schemas import RenderRequest


def _wait(c, job_id, timeout=180):
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = c.get(f"/api/jobs/{job_id}").json()
        if job["status"] in ("selesai", "gagal", "batal"):
            return job
        time.sleep(0.3)
    raise AssertionError("job timeout")


def _lecture_frame(board=True, wall=(150, 176, 196)):
    """Frame BGR 320x180: dinding krem, papan putih bertulisan (kiri), orang berbaju putih (kanan)."""
    f = np.zeros((180, 320, 3), np.uint8)
    f[:] = wall
    import cv2
    if board:
        cv2.rectangle(f, (20, 20), (170, 125), (232, 236, 236), -1)
        for k in range(5):
            cv2.putText(f, "zakat fitrah 2,5 kg", (28, 40 + k * 18), cv2.FONT_HERSHEY_SIMPLEX, 0.38, (110, 40, 20), 1)
    cv2.rectangle(f, (230, 90), (300, 180), (245, 245, 245), -1)      # kemeja putih (bukan papan)
    return f


def test_detect_board_finds_whiteboard_not_wall_or_shirt():
    box, kind, ink = framing.detect_board(_lecture_frame(), [0.78, 0.30, 0.06, 0.15])
    assert kind == "papan" and ink > 0.01
    assert abs(box[0] - 20 / 320) < 0.03 and abs(box[2] - 151 / 320) < 0.04 and abs(box[3] - 106 / 180) < 0.05
    # dinding putih polos tanpa tulisan + kemeja putih: tidak ada papan
    plain = _lecture_frame(board=False, wall=(235, 236, 236))
    box, kind, _ = framing.detect_board(plain, [0.78, 0.30, 0.06, 0.15])
    assert kind != "papan"


def _data(samples):
    return {"v": 1, "fps": 2, "samples": samples}


FACE_R = [0.72, 0.4, 0.05, 0.1]
FACE_AT_BOARD = [0.40, 0.4, 0.05, 0.1]
BOARD = [0.06, 0.12, 0.47, 0.59]


def _timeline():
    s = []
    for i in range(48):          # 24 dtk
        t = i / 2
        if t < 8:
            s.append({"f": FACE_R, "nf": 1, "b": BOARD, "bk": "papan", "ink": 0.05, "act": 0.0})
        elif t < 16:
            s.append({"f": FACE_AT_BOARD, "nf": 1, "b": BOARD, "bk": "papan", "ink": 0.07, "act": 1.5})
        elif t < 20:
            s.append({"f": None, "nf": 0, "b": BOARD, "bk": "papan", "ink": 0.09, "act": 1.0})
        else:
            s.append({"f": FACE_R, "nf": 1, "b": BOARD, "bk": "papan", "ink": 0.1, "act": 0.0})
    return _data(s)


def test_auto_plan_switches_with_reasons_and_minimum_section_length():
    segs = framing.plan(_timeline(), 0, 24, "auto")
    assert [s["layout"] for s in segs] == ["face", "split", "material", "face"]
    assert [round(s["start"]) for s in segs] == [0, 8, 16, 20] and segs[-1]["end"] == 24
    assert all(s["reason"] for s in segs) and "dekat area materi" in segs[1]["reason"]
    # kedipan 1 sampel (0,5 dtk) tidak membuat bagian baru
    d = _timeline()
    d["samples"][4] = {**d["samples"][4], "f": None}
    assert [s["layout"] for s in framing.plan(d, 0, 24, "auto")] == ["face", "split", "material", "face"]
    assert all(s["end"] - s["start"] >= framing.MIN_SECTION for s in framing.plan(d, 0, 24, "auto"))


def test_modes_and_user_section_override():
    d = _timeline()
    assert [s["layout"] for s in framing.plan(d, 0, 24, "face")] == ["face"]
    assert [s["layout"] for s in framing.plan(d, 0, 24, "manual")] == ["center"]
    assert {s["layout"] for s in framing.plan(d, 0, 24, "material")} == {"split", "material"}
    # tanpa papan: Fokus Materi jatuh ke wajah, dengan alasan yang jujur
    no_board = _data([{"f": FACE_R, "nf": 1, "b": None, "bk": "", "ink": 0, "act": 0}] * 20)
    seg = framing.plan(no_board, 0, 10, "material")[0]
    assert seg["layout"] == "face" and "tidak terdeteksi" in seg["reason"]
    # pilihan pengguna menimpa bagian 2–6 dtk
    segs = framing.plan(d, 0, 24, "auto", [{"start": 2, "end": 6, "layout": "split"}])
    assert [(s["start"], s["layout"]) for s in segs[:3]] == [(0, "face"), (2, "split"), (6, "face")]
    assert segs[1]["manual"] and segs[1]["reason"] == "dipilih pengguna"


def test_legacy_recipe_keeps_m1_framing():
    old = recipe.Recipe.model_validate({"crop": {"x": 0.2, "y": 0.5, "reframe": "auto"}})
    assert old.crop.mode == "face"
    old = recipe.Recipe.model_validate({"crop": {"x": 0.2, "y": 0.5, "reframe": "manual"}})
    assert old.crop.mode == "manual" and old.crop.x == 0.2
    assert recipe.Recipe().crop.mode == "auto"                     # proyek baru: Otomatis


@pytest.fixture(scope="module")
def wide_video(tmp_path_factory):
    path = tmp_path_factory.mktemp("m2") / "wide.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=25:duration=12",
                    "-f", "lavfi", "-i", "sine=frequency=300:duration=12", "-c:v", "libx264", "-preset", "ultrafast",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path)], check=True)
    return path


@pytest.mark.parametrize("aspect,size", [("9:16", (1080, 1920)), ("16:9", (1920, 1080))])
def test_render_per_section_layouts_produces_real_mp4(tmp_path, wide_video, aspect, size):
    d = _data(_timeline()["samples"][:24])          # 12 dtk: wajah 0-8, papan+wajah 8-12
    segs = framing.plan(d, 1, 11, "auto", [{"start": 9, "end": 11, "layout": "material"}])
    assert [s["layout"] for s in segs] == ["face", "split", "material"]
    info = media.probe(wide_video)
    req = RenderRequest(start=1, end=11, aspect=aspect, subtitles={"enabled": False}, animations=False)
    out = tmp_path / "o.mp4"
    cmd = build_command(wide_video, out, req, info, None, None, tmp_path, None, [], [],
                        {"segments": segs, "data": d, "zoom": 0.0, "distance": "auto"})
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *cmd], check=True, cwd=tmp_path)
    got = media.probe(out)
    assert (got["width"], got["height"]) == size and abs(got["duration"] - 10) < 0.3 and got["has_audio"]


def test_framing_api_and_preview_clip_use_same_pipeline(client, sample_video, fake_transcript, monkeypatch):
    monkeypatch.setattr(transcribe, "transcribe", lambda *a, **k: fake_transcript)
    with sample_video.open("rb") as fh:
        res = client.post("/api/projects", files={"file": ("kajian.mp4", fh, "video/mp4")}).json()
    pid = res["project"]["id"]
    assert _wait(client, res["job"]["id"])["status"] == "selesai"
    assert storage.read_data(pid, "framing")["samples"]                          # analisis M2 tersimpan
    plan = client.get(f"/api/projects/{pid}/framing", params={"start": 0, "end": 6}).json()
    assert plan["mode"] == "auto" and plan["segments"] and plan["segments"][0]["label"]
    assert "heuristik" in plan["detector"]
    state = client.get(f"/api/projects/{pid}/recipe").json()
    rec = state["recipe"]
    rec["crop"].update(mode="manual", x=0.1, zoom=1.5)
    v = client.put(f"/api/projects/{pid}/recipe", json={"version": state["version"], "recipe": rec}).json()["version"]
    assert client.post(f"/api/projects/{pid}/preview-clip", json={"version": v + 5, "start": 0, "end": 4}).status_code == 409
    r = client.post(f"/api/projects/{pid}/preview-clip", json={"version": v, "start": 1, "end": 30})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["end"] - body["start"] <= 10
    mp4 = client.get(body["url"])
    assert mp4.status_code == 200 and mp4.headers["content-type"] == "video/mp4"
    path = storage.project_dir(pid) / "previews" / body["url"].rsplit("/", 1)[1]
    got = media.probe(path)
    assert (got["width"], got["height"]) == (360, 640)
    again = client.post(f"/api/projects/{pid}/preview-clip", json={"version": v, "start": 1, "end": 30}).json()
    assert again["url"] == body["url"]                                       # cache: tidak dirender ulang
    assert client.get(f"/api/projects/{pid}/previews/..%2Fapp.db").status_code == 404
    json.dumps(plan)


def test_user_choice_that_cannot_be_honoured_is_explained():
    no_board = _data([{"f": FACE_R, "nf": 1, "b": None, "bk": "", "ink": 0, "act": 0}] * 20)
    seg = framing.plan(no_board, 0, 10, "auto", [{"start": 0, "end": 10, "layout": "split"}])[0]
    assert seg["layout"] == "face" and "tidak tersedia" in seg["reason"] and "area materi" in seg["reason"]
