"""M2 penyempurnaan: safe-zone dinamis motion graphics & deduplikasi subtitle-hook yang ketat."""
import pytest

from app import framing, safezone
from app.subtitles import SubtitleOptions, build_ass, collect_words, norm_tokens, spoken_span

FACE = [0.72, 0.4, 0.05, 0.1]
FACE_AT_BOARD = [0.40, 0.4, 0.05, 0.1]
BOARD = [0.06, 0.12, 0.47, 0.59]


def _data():
    s = []
    for i in range(48):
        t = i / 2
        if t < 8:
            s.append({"f": FACE, "nf": 1, "b": BOARD, "bk": "papan", "ink": 0.05, "act": 0.0})
        elif t < 16:
            s.append({"f": FACE_AT_BOARD, "nf": 1, "b": BOARD, "bk": "papan", "ink": 0.07, "act": 1.5})
        elif t < 20:
            s.append({"f": None, "nf": 0, "b": BOARD, "bk": "papan", "ink": 0.09, "act": 1.0})
        else:
            s.append({"f": FACE, "nf": 1, "b": BOARD, "bk": "papan", "ink": 0.1, "act": 0.0})
    return {"v": 1, "fps": 2, "samples": s}


def _timeline():
    d = _data()
    frame = {"segments": framing.plan(d, 0, 24, "auto"), "data": d, "zoom": 0.0, "distance": "auto"}
    subs = {"y": 0.78, "seam_y": 0.56, "size": 1.0}
    return safezone.timeline(frame, 0, 1.0, 1080, 1920, (1920, 1080), (0.5, 0.5, 0.0), "auto", subs)


def _rect(pl, item):
    w, h, x = safezone.size_of(item, 1080, 1920, pl["scale"])
    return [x, pl["y"] - h / 2, w, h]


def test_occupied_areas_per_layout():
    tl = _timeline()
    assert [s["layout"] for s in tl] == ["face", "split", "material", "face"]
    kinds = [sorted(r["kind"] for r in s["rects"]) for s in tl]
    assert kinds == [["subtitle", "wajah"], ["papan", "subtitle", "wajah"], ["papan", "subtitle"], ["subtitle", "wajah"]]
    split = tl[1]
    board = next(r["rect"] for r in split["rects"] if r["kind"] == "papan")
    face = next(r["rect"] for r in split["rects"] if r["kind"] == "wajah")
    assert board[1] + board[3] <= 0.56 and face[1] >= 0.56                  # papan di panel atas, wajah di bawah
    sub = next(r["rect"] for r in split["rects"] if r["kind"] == "subtitle")
    assert abs(sub[1] + sub[3] / 2 - 0.56) < 0.01                          # subtitle di garis sambung


def test_graphics_avoid_face_board_subtitle_and_each_other():
    tl = _timeline()
    items = [{"id": "hook", "type": "hook", "text": "Berapa zakat fitrah?", "start": 0, "end": 2.8},
             {"id": "nama", "type": "lower_third", "text": "Ustadz Ahmad", "label": "Pemateri", "start": 3, "end": 6},
             {"id": "cta", "type": "cta", "text": "Ikuti kajian berikutnya", "start": 21, "end": 24},
             {"id": "kata", "type": "keyword", "text": "PENERIMA", "start": 16.5, "end": 18}]
    res = safezone.place(items, tl, 1080, 1920)
    for it in items:
        pl = res[it["id"]]
        assert pl["action"] in ("tetap", "dipindah", "diperkecil"), (it["id"], pl)
        r = _rect(pl, it)
        busy = [b["rect"] for s in tl if s["t0"] < it["end"] and s["t1"] > it["start"] for b in s["rects"]]
        assert all(safezone._overlap(r, b) / (r[2] * r[3]) <= 0.01 for b in busy), (it["id"], r, busy)


def test_crossing_layout_switch_is_split_per_layout():
    tl = _timeline()
    it = {"id": "nama", "type": "lower_third", "text": "Ustadz Ahmad", "label": "Pemateri", "start": 6, "end": 10}
    pl = safezone.place([it], tl, 1080, 1920)["nama"]
    assert "per tata letak" in pl["action"] and [p["layouts"][0] for p in pl["parts"]] == ["face", "split"]
    assert pl["parts"][0]["end"] == pl["parts"][1]["start"] == 8.0           # pindah tepat di titik potong


def test_no_room_decorative_hidden_with_reason_info_kept():
    tl = [{"t0": 0, "t1": 10, "layout": "split", "rects": [{"kind": "papan", "rect": [0, 0, 1, 1]}]}]
    res = safezone.place([{"id": "k", "type": "kinetic", "text": "ayo", "start": 0, "end": 3},
                          {"id": "n", "type": "counter", "text": "2,5 kg", "label": "beras", "start": 0, "end": 3}],
                         tl, 1080, 1920)
    assert res["k"]["action"] == "disembunyikan" and "dekoratif" in res["k"]["reason"]
    assert res["n"]["action"] == "tumpang tindih" and "informasi tetap" in res["n"]["reason"] and res["n"]["y"]


def _tr(text, start=0.3, step=0.45):
    words, t = [], start
    for w in text.split():
        words.append({"word": w, "start": round(t, 2), "end": round(t + 0.35, 2)})
        t += step
    return {"segments": [{"start": words[0]["start"], "end": words[-1]["end"], "text": text, "words": words}]}


def _shown(tr, windows, wpl=3, end=12):
    o = SubtitleOptions(style="pop", words_per_line=wpl, uppercase=False, suppress=windows)
    return [line.split(",,", 1)[1] for line in build_ass(tr, 0, end, 1080, 1920, o).splitlines()
            if line.startswith("Dialogue:")]


def _win(tr, hook_text, t0=0, t1=2.8):
    seq = tuple(norm_tokens(hook_text))
    span = spoken_span(seq, collect_words(tr, 0, 12), t0, t1)
    return ((span[0], span[1], seq, t0, t1),) if span else ()


def plain(lines):
    import re
    return [re.sub(r"\{[^}]*\}", "", x) for x in lines]


def test_dedup_hides_only_equivalent_spoken_words():
    tr = _tr("Apa jawab mereka? Mereka menjawab dengan sabar.")
    shown = " | ".join(plain(_shown(tr, _win(tr, "Apa jawab mereka?"))))
    assert "Apa jawab mereka?" not in shown and "Mereka menjawab dengan" in shown and "sabar." in shown


@pytest.mark.parametrize("hook,spoken", [
    ("Rahasia sabar menurut Islam", "Apa jawab mereka? Mereka menjawab dengan sabar."),   # hook diedit pengguna
    ("Apa jawab mereka?", "Lalu kita bahas dulu ini. Nanti di akhir: apa jawab mereka?"),  # diucapkan di luar waktu hook
    ("Apa jawab mereka?", "Apa kata mereka tentang zakat fitrah?"),                       # mirip, tidak sama
])
def test_dedup_never_hides_non_equivalent_dialogue(hook, spoken):
    tr = _tr(spoken)
    assert plain(_shown(tr, _win(tr, hook))) == plain(_shown(tr, ()))


def test_dedup_partial_overlap_line_is_kept():
    tr = _tr("Apa jawab mereka? Mereka menjawab dengan sabar.")
    shown = plain(_shown(tr, _win(tr, "Apa jawab mereka?"), wpl=4))
    assert any("mereka?" in x.lower() for x in shown) or any("Mereka menjawab" in x for x in shown)
    assert sum(len(x.split()) for x in shown) >= 4                             # kata dialog tidak hilang


def test_api_layout_and_final_render_use_same_safe_zone(client, sample_video, fake_transcript, monkeypatch):
    import time

    from app import motion, pipeline, storage, transcribe
    monkeypatch.setattr(transcribe, "transcribe", lambda *a, **k: fake_transcript)
    with sample_video.open("rb") as fh:
        res = client.post("/api/projects", files={"file": ("sz.mp4", fh, "video/mp4")}).json()
    pid = res["project"]["id"]
    deadline = time.time() + 120
    while client.get(f"/api/jobs/{res['job']['id']}").json()["status"] not in ("selesai", "gagal") and time.time() < deadline:
        time.sleep(0.3)
    d = _data()
    d["samples"] = [dict(x) for x in d["samples"][:16]]          # 8 dtk: wajah 0-8 (video uji 8 dtk)
    for x in d["samples"][8:]:
        x.update(f=FACE_AT_BOARD, act=1.5)                         # 4-8 dtk: pemateri ke papan -> Materi + Wajah
    storage.write_data(pid, "framing", d)
    items = [{"id": "nama", "type": "lower_third", "text": "Ustadz Ahmad", "label": "Pemateri", "start": 2, "end": 6},
             {"id": "kin", "type": "kinetic", "text": "ayo kita belajar", "start": 4.5, "end": 7.5},
             {"id": "kata", "type": "keyword", "text": "BELAJAR", "start": 4.5, "end": 7.5}]
    assert client.put(f"/api/projects/{pid}/plan", json=items).status_code == 200
    lay = client.get(f"/api/projects/{pid}/motion-layout", params={"start": 0, "end": 8}).json()["items"]
    assert set(lay) == {"nama", "kin", "kata"} and all(v["reason"] for v in lay.values())
    seen = []
    real = motion.build
    monkeypatch.setattr(pipeline.motion, "build", lambda its, *a, **k: seen.append(its) or real(its, *a, **k))
    v = client.get(f"/api/projects/{pid}/recipe").json()["version"]
    job = client.post(f"/api/projects/{pid}/render-recipe", json={"version": v, "start": 0, "end": 8}).json()
    deadline = time.time() + 180
    while client.get(f"/api/jobs/{job['id']}").json()["status"] not in ("selesai", "gagal") and time.time() < deadline:
        time.sleep(0.3)
    assert client.get(f"/api/jobs/{job['id']}").json()["status"] == "selesai"
    rendered = {i["id"]: i for i in seen[-1]}
    if lay["kin"]["action"] == "disembunyikan":
        assert "kin" not in rendered                                   # dekoratif tanpa ruang: tidak dirender
    nama = [i for i in seen[-1] if i["id"] == "nama"]
    want = lay["nama"].get("parts") or [lay["nama"]]
    assert [round(i["place"]["y"], 3) for i in nama] == [p["y"] for p in want if p.get("y") is not None]


def test_hidden_big_text_never_hides_dialogue(monkeypatch):
    """Bug yang ditangkap pemeriksaan piksel MP4: kinetic text disembunyikan safe-zone (tidak ada ruang) tetapi
    subtitle kalimat yang sama ikut disembunyikan -> dialog hilang. Sekarang hanya teks besar yang tampil berlaku."""
    from app import pipeline
    from app.schemas import RenderRequest
    tr = _tr("Ada delapan golongan penerima zakat.", start=1.0)
    req = RenderRequest(start=0, end=6, aspect="9:16")
    items = [{"id": "k", "type": "kinetic", "text": "Ada delapan golongan penerima", "start": 0.8, "end": 4}]
    frame = {"segments": [{"start": 0, "end": 6, "layout": "material", "board": [0, 0, 1, 1], "face": None,
                           "reason": ""}], "data": {"samples": []}, "zoom": 0.0, "distance": "auto"}
    import tempfile
    from pathlib import Path
    monkeypatch.setattr(pipeline, "layout_items", lambda *a, **k: {"k": {"action": "disembunyikan", "reason": "uji"}})
    with tempfile.TemporaryDirectory() as d:
        o = SubtitleOptions(style="pop", uppercase=False)
        parts = pipeline.assemble(req, tr, o, items, frame, (1920, 1080), 1080, 1920, Path(d), "t")
        assert parts["placement"]["k"]["action"] == "disembunyikan"
        ass = parts["subs"].read_text(encoding="utf-8")
    assert "delapan" in ass and "penerima" in ass                              # dialog tetap tampil
    monkeypatch.setattr(pipeline, "layout_items", lambda *a, **k: {})          # kinetic tampil: baris ekuivalen disembunyikan
    with tempfile.TemporaryDirectory() as d:
        o = SubtitleOptions(style="pop", uppercase=False)
        ass = pipeline.assemble(req, tr, o, items, frame, (1920, 1080), 1080, 1920, Path(d), "t")["subs"].read_text()
    assert "delapan" not in ass.split("[Events]")[1].split("zakat")[0] and "zakat" in ass
