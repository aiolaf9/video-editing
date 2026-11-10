"""M2.3 Motion graphics: hook otomatis yang dapat diedit, kinetic typography, lower-third, animasi angka, CTA,
gambar/logo, punch-in yang dapat diatur, dan waktu mulai/akhir tiap elemen -> MP4 nyata lewat pipeline render."""
import io
import time

from PIL import Image

from app import media, motion, plan, storage, transcribe
from app.render import zoom_expression


def _wait(c, job_id, timeout=180):
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = c.get(f"/api/jobs/{job_id}").json()
        if job["status"] in ("selesai", "gagal", "batal"):
            return job
        time.sleep(0.3)
    raise AssertionError("job timeout")


def _events(ass):
    return [line for line in ass.splitlines() if line.startswith("Dialogue:")]


def test_motion_ass_elements_and_timing():
    items = [{"type": "hook", "text": "Berapa zakat fitrah?", "start": 0, "end": 2.8},
             {"type": "kinetic", "text": "ada delapan golongan", "start": 3, "end": 6},
             {"type": "lower_third", "text": "Ustadz Ahmad", "label": "Pemateri", "start": 6, "end": 9},
             {"type": "counter", "text": "Rp 1.500.000", "label": "per keluarga", "start": 9, "end": 12},
             {"type": "cta", "text": "Ikuti kajian berikutnya", "start": 12, "end": 15}]
    ev = _events(motion.build(items, 1080, 1920))
    assert any("Hook" in e and "ZAKAT" in e and r"\t(0,170" in e for e in ev)
    kin = [e for e in ev if ",Kinetic," in e]
    assert [e.rsplit("}", 1)[1] for e in kin] == ["ADA", "DELAPAN", "GOLONGAN"]
    assert kin[0].startswith("Dialogue: 2,0:00:03.00,0:00:04.00")          # mulai/akhir mengikuti elemen
    assert any("LowerName" in e and r"\move(" in e and "Ustadz Ahmad" in e for e in ev)
    counts = [e.rsplit("}", 1)[1] for e in ev if ",Counter," in e]
    assert counts[0] == "Rp 0" and counts[-1] == "Rp 1.500.000" and len(counts) > 10   # angka berhitung naik
    assert any(",Cta," in e and r"\fscx106" in e for e in ev)                         # tombol berdenyut
    assert motion.parse_number("2,5 kg") == ("", 2.5, 1, " kg")


def test_hook_suggestion_prefers_question_and_is_editable_text():
    words = []
    t = 0.0
    for sent in ["Hari ini kita belajar.", "Siapa yang wajib membayar zakat fitrah?", "Semua muslim."]:
        for w in sent.split():
            words.append({"word": w, "start": t, "end": t + 0.3})
            t += 0.35
    h = motion.hook_suggestion(words, 0, t)
    assert h["text"].startswith("Siapa yang wajib") and h["start"] == 0 and h["end"] == 2.8


def test_plan_keeps_motion_fields_and_heuristic_adds_hook_and_counter(fake_transcript):
    items = plan.normalize([{"type": "zoom", "start": 1, "end": 2, "strength": 0.3},
                            {"type": "image", "start": 0, "end": 5, "asset": "logo.png", "pos": "bl", "scale": 0.3},
                            {"type": "image", "start": 0, "end": 5, "asset": ""},
                            {"type": "lower_third", "start": 0, "end": 3, "text": "Nama", "label": "Jabatan"}], 0, 10)
    assert [i["type"] for i in items] == ["image", "lower_third", "zoom"]
    assert items[0]["pos"] == "bl" and items[0]["scale"] == 0.3 and items[2]["strength"] == 0.3
    tr = {"segments": [{"start": 0, "end": 8, "text": "", "words": [
        {"word": w, "start": i * 0.5, "end": i * 0.5 + 0.4} for i, w in
        enumerate("Berapa besarnya zakat fitrah itu? Menurut para ulama besarnya 2,5 kg beras untuk setiap jiwa.".split())]}]}
    auto = plan.heuristic(tr, [], 0, 8)
    kinds = {i["type"] for i in auto}
    assert "hook" in kinds and "counter" in kinds
    assert next(i for i in auto if i["type"] == "counter")["text"] == "2,5 kg"


def test_punch_in_strength_is_configurable():
    z = zoom_expression(False, 10, [(1.0, 2.0, 0.3), (5.0, 6.0)])
    assert z.startswith("(1+0.300*clip(") and "0.140*clip(" in z


def test_motion_graphics_render_real_mp4_with_logo(client, sample_video, fake_transcript, monkeypatch):
    monkeypatch.setattr(transcribe, "transcribe", lambda *a, **k: fake_transcript)
    with sample_video.open("rb") as fh:
        res = client.post("/api/projects", files={"file": ("m.mp4", fh, "video/mp4")}).json()
    pid = res["project"]["id"]
    assert _wait(client, res["job"]["id"])["status"] == "selesai"
    bad = client.post(f"/api/projects/{pid}/assets", files={"file": ("x.png", b"bukan gambar", "image/png")})
    assert bad.status_code == 400
    buf = io.BytesIO()
    Image.new("RGBA", (200, 200), (255, 0, 0, 255)).save(buf, "PNG")
    up = client.post(f"/api/projects/{pid}/assets", files={"file": ("logo NH.png", buf.getvalue(), "image/png")})
    assert up.status_code == 200 and client.get(f"/api/projects/{pid}/assets").json() == ["logo NH.png"]
    items = [{"type": "hook", "text": "Kenapa video ini viral?", "start": 0, "end": 2.5},
             {"type": "lower_third", "text": "Narasumber", "label": "Tim NH", "start": 2.5, "end": 5},
             {"type": "counter", "text": "2,5 kg", "start": 5, "end": 7},
             {"type": "kinetic", "text": "ayo coba sekarang", "start": 2, "end": 4},
             {"type": "cta", "text": "Ikuti kami", "start": 6, "end": 8},
             {"type": "zoom", "start": 1, "end": 2, "strength": 0.3},
             {"type": "image", "start": 0, "end": 8, "asset": "logo NH.png", "pos": "tr", "scale": 0.2}]
    saved = client.put(f"/api/projects/{pid}/plan", json=items)
    assert saved.status_code == 200 and len(saved.json()) == 7
    v = client.get(f"/api/projects/{pid}/recipe").json()["version"]
    prev = client.post(f"/api/projects/{pid}/preview-clip", json={"version": v, "start": 0, "end": 8})
    assert prev.status_code == 200, prev.text
    job = client.post(f"/api/projects/{pid}/render-recipe", json={"version": v, "start": 0, "end": 8}).json()
    done = _wait(client, job["id"])
    assert done["status"] == "selesai", done
    out = storage.project_dir(pid) / "renders" / done["result"]["file"]
    info = media.probe(out)
    assert (info["width"], info["height"]) == (1080, 1920) and abs(info["duration"] - 8) < 0.3
    # logo merah terlihat di kanan atas MP4 (lewat overlay PNG)
    import subprocess
    png = out.with_suffix(".png")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", "4", "-i", str(out), "-frames:v", "1", str(png)], check=True)
    r, g, b = Image.open(png).convert("RGB").getpixel((1080 - 54 - 108, 54 + 108))
    assert r > 200 and g < 60 and b < 60
    assert not list((storage.project_dir(pid) / "renders").glob("*.motion.ass"))      # berkas sementara dibersihkan


def test_punch_in_skipped_on_material_layouts():
    from app.pipeline import drop_split_punches
    frame = {"segments": [{"start": 10, "end": 14, "layout": "face"}, {"start": 14, "end": 20, "layout": "split"}]}
    assert drop_split_punches([(1, 2, 0.2), (5, 6, 0.2)], frame, 10) == [(1, 2, 0.2)]
