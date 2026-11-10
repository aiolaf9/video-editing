"""M1.1: koreksi transkrip menjaga timing kata, ubah waktu, cari & ganti massal, kata yang perlu ditinjau, dan
subtitle akhir (ASS yang dibakar ke MP4) identik dengan hasil koreksi pengguna."""
import json
import re
import subprocess

import pytest

from app import recipe, storage, transcript_edit
from app.config import PROJECTS_DIR
from app.subtitles import SubtitleOptions, build_ass


def _seg(words, start=None, end=None):
    ws = [{"word": w, "start": s, "end": e, "p": p} for w, s, e, p in words]
    return {"start": start if start is not None else ws[0]["start"], "end": end if end is not None else ws[-1]["end"],
            "text": " ".join(w["word"] for w in ws), "words": ws}


SEG = _seg([("insyaallah", 1.0, 1.6, 0.31), ("kita", 1.7, 1.9, 0.98), ("akan", 2.0, 2.2, 0.97),
            ("bersedekah", 2.3, 3.0, 0.95), ("hari", 3.1, 3.3, 0.99), ("ini", 3.4, 3.6, 0.99)])


# ------------------------------------------------------------------ penyelarasan (fungsi murni)

def test_replacing_one_word_keeps_all_other_timings():
    out = transcript_edit.realign(SEG, "Insya Allah kita akan bersedekah hari ini.")
    assert [w["word"] for w in out] == ["Insya", "Allah", "kita", "akan", "bersedekah", "hari", "ini."]
    # kata pengganti mengisi rentang kata yang digantikan (1,0-1,6), sisanya persis waktu Whisper
    assert out[0]["start"] == 1.0 and out[1]["end"] == 1.6
    assert [(w["start"], w["end"]) for w in out[2:]] == [(1.7, 1.9), (2.0, 2.2), (2.3, 3.0), (3.1, 3.3), (3.4, 3.6)]
    assert out[0]["p"] == out[1]["p"] == 1.0 and out[2]["p"] == 0.98     # diketik pengguna = dikonfirmasi


def test_insert_and_delete_do_not_shift_neighbours():
    ins = transcript_edit.realign(SEG, "insyaallah kita semua akan bersedekah hari ini")
    assert [w["word"] for w in ins][:4] == ["insyaallah", "kita", "semua", "akan"]
    assert ins[0]["start"] == 1.0 and ins[3]["start"] == 2.0 and ins[-1]["end"] == 3.6
    assert ins[1]["end"] <= ins[2]["start"] < ins[2]["end"] <= ins[3]["start"]
    dele = transcript_edit.realign(SEG, "insyaallah kita bersedekah hari ini")
    assert [(w["word"], w["start"]) for w in dele] == [("insyaallah", 1.0), ("kita", 1.7), ("bersedekah", 2.3),
                                                       ("hari", 3.1), ("ini", 3.4)]


def test_words_stay_ordered_and_inside_segment():
    for text in ["", "a", "satu dua tiga empat lima enam tujuh delapan sembilan sepuluh sebelas", "ini hari kita"]:
        out = transcript_edit.realign(SEG, text)
        assert len(out) == len(text.split())
        for a, b in zip(out, out[1:]):
            assert a["start"] < a["end"] <= b["start"] + 1e-6
        if out:
            assert out[0]["start"] >= SEG["start"] - 1e-6


def test_retime_and_word_times():
    seg = json.loads(json.dumps(SEG))
    transcript_edit.retime(seg, 2.0, 4.6)                                  # geser +1 dtk, rentang sama
    assert seg["words"][0]["start"] == 2.0 and seg["words"][-1]["end"] == 4.6
    transcript_edit.set_word_times(seg, [{"i": 1, "start": 2.65, "end": 2.95}])
    assert (seg["words"][1]["start"], seg["words"][1]["end"]) == (2.65, 2.95)
    with pytest.raises(transcript_edit.EditError):                         # menabrak kata berikutnya
        transcript_edit.set_word_times(seg, [{"i": 1, "start": 3.0, "end": 3.0}])
    with pytest.raises(transcript_edit.EditError):
        transcript_edit.retime(seg, 3.0, 2.0)


def test_find_replace_whole_word_and_case():
    segs = [_seg([("Insyaallah", 0, 1, 1), ("kita", 1, 2, 1)]), _seg([("insyaallahku", 2, 3, 1)]),
            _seg([("INSYAALLAH,", 3, 4, 1), ("ya", 4, 5, 1)])]
    hits = transcript_edit.find_replace(segs, "insyaallah", "Insya Allah")
    assert [(i, b) for i, _a, b in hits] == [(0, "Insya Allah kita"), (2, "Insya Allah, ya")]
    assert transcript_edit.find_replace(segs, "insyaallah", "X", case_sensitive=True) == []
    assert len(transcript_edit.find_replace(segs, "insyaallah", "X", whole_word=False)) == 3
    with pytest.raises(transcript_edit.EditError):
        transcript_edit.find_replace(segs, "   ", "x")


def test_unsure_words_respect_review():
    segs = [json.loads(json.dumps(SEG)), _seg([("zakat", 5.0, 5.4, 0.2)])]
    assert [w["word"] for w in transcript_edit.unsure_words(segs)] == ["insyaallah", "zakat"]
    assert [w["word"] for w in transcript_edit.unsure_words(segs, 4.0, 6.0)] == ["zakat"]
    segs[1]["reviewed"] = True
    assert [w["word"] for w in transcript_edit.unsure_words(segs)] == ["insyaallah"]


# ------------------------------------------------------------------ API

@pytest.fixture
def project():
    pid = storage.create_project("m11-transkrip", None)["id"]
    tr = {"language": "id", "segments": [json.loads(json.dumps(SEG)),
                                         _seg([("assalamualaikum", 0.0, 0.8, 0.4)], 0.0, 0.9),
                                         _seg([("bayar", 5.0, 5.3, 0.9), ("jakat", 5.4, 5.9, 0.35),
                                               ("insyaallah", 6.0, 6.6, 0.6)])]}
    storage.write_data(pid, "transcript", tr)
    storage.update_project(pid, status="siap", duration=8.0, source="source.mp4", width=1280, height=720,
                           has_audio=True, fps=25)
    yield pid
    storage.delete_project(pid)


def test_api_edit_keeps_timing_and_survives_reload(client, project):
    r = client.put(f"/api/projects/{project}/transcript",
                   json={"segments": [{"index": 0, "text": "Insya Allah kita akan bersedekah hari ini."}]})
    assert r.status_code == 200, r.text
    seg = r.json()["segments"][0]
    assert seg["edited"] and [w["start"] for w in seg["words"][2:]] == [1.7, 2.0, 2.3, 3.1, 3.4]
    again = client.get(f"/api/projects/{project}/transcript").json()["segments"][0]
    assert again["words"] == seg["words"]
    raw = storage.read_data(project, "transcript")["segments"][0]
    assert raw["text"] == "insyaallah kita akan bersedekah hari ini"            # transkrip Whisper tidak diubah
    r2 = client.put(f"/api/projects/{project}/transcript", json={"segments": [{"index": 0, "start": 1.2, "end": 3.8}]})
    assert r2.json()["segments"][0]["words"][0]["start"] == 1.2
    assert r2.json()["segments"][0]["text"] == "Insya Allah kita akan bersedekah hari ini."   # teks tetap
    bad = client.put(f"/api/projects/{project}/transcript", json={"segments": [{"index": 9, "text": "x"}]})
    assert bad.status_code == 422


def test_api_find_replace_preview_then_apply(client, project):
    url = f"/api/projects/{project}/transcript/replace"
    dry = client.post(url, json={"find": "insyaallah", "replace": "Insya Allah"}).json()
    assert dry["count"] == 2 and not dry["applied"] and dry["preview"][0]["after"].startswith("Insya Allah")
    assert client.get(f"/api/projects/{project}/transcript").json()["segments"][0]["text"].startswith("insyaallah")
    done = client.post(url, json={"find": "insyaallah", "replace": "Insya Allah", "dry_run": False}).json()
    assert done["applied"] and done["count"] == 2
    segs = done["transcript"]["segments"]
    assert segs[0]["text"].startswith("Insya Allah") and segs[2]["text"] == "bayar jakat Insya Allah"
    assert segs[2]["words"][0]["start"] == 5.0 and segs[2]["words"][1]["start"] == 5.4    # kata lain tak bergeser
    assert client.post(url, json={"find": "insyaallah", "replace": "x"}).json()["count"] == 0


def test_api_review_list_and_mark_reviewed(client, project):
    url = f"/api/projects/{project}/transcript/review"
    words = client.get(url).json()["words"]
    assert {w["word"] for w in words} == {"insyaallah", "assalamualaikum", "jakat"}
    assert {w["word"] for w in client.get(url, params={"start": 4.5, "end": 7}).json()["words"]} == {"jakat"}
    client.put(f"/api/projects/{project}/transcript", json={"reviewed": [2]})
    assert {w["word"] for w in client.get(url).json()["words"]} == {"insyaallah", "assalamualaikum"}
    client.put(f"/api/projects/{project}/transcript", json={"unreviewed": [2]})
    assert len(client.get(url).json()["words"]) == 3
    assert client.put(f"/api/projects/{project}/transcript", json={}).status_code == 422


def test_reanalyze_clears_m11_edits(client, project, monkeypatch, fake_transcript):
    client.put(f"/api/projects/{project}/transcript", json={"segments": [{"index": 0, "text": "x y"}], "reviewed": [1]})
    r = recipe.load(project)["recipe"]
    assert r.seg_edits and r.reviewed

    def clear(rr):
        rr.text_fixes, rr.seg_edits, rr.reviewed, rr.vocab_ignored = {}, {}, [], []
        return rr
    # jalur yang sama dengan endpoint reanalyze (tanpa menjalankan analisis sungguhan)
    recipe.server_update(project, clear, None)
    assert not recipe.load(project)["recipe"].seg_edits


# ------------------------------------------------------------------ subtitle akhir = koreksi pengguna

def _ass_words(ass: str) -> list[str]:
    out = []
    for line in ass.splitlines():
        if line.startswith("Dialogue:"):
            text = line.split(",", 9)[9]
            text = re.sub(r"\{[^}]*\}", "", text)
            out += text.split()
    return out


def test_ass_contains_exactly_the_corrected_words(client, project):
    client.put(f"/api/projects/{project}/transcript",
               json={"segments": [{"index": 0, "text": "Insya Allah kita akan bersedekah hari ini."},
                                  {"index": 2, "text": "bayar zakat insya Allah"}]})
    tr = recipe.effective_transcript(project)
    for style in ("karaoke", "pop", "word", "simple"):
        ass = build_ass(tr, 0, 8, 1080, 1920, SubtitleOptions(style=style, uppercase=False, words_per_line=3))
        words = _ass_words(ass)
        expected = {w["word"] for s in tr["segments"] for w in s["words"] if 0 <= w["start"] < 8}
        assert set(words) == expected, style        # karaoke/pop menulis baris berulang per kata yang disorot
        assert "jakat" not in words and "insyaallah" not in words


def test_render_burns_corrected_subtitles(client, project, sample_video):
    """Render nyata: subtitle yang dibakar memakai koreksi (snapshot resep), bukan teks Whisper."""
    import shutil

    from app import pipeline
    shutil.copy(sample_video, PROJECTS_DIR / project / "source.mp4")
    client.put(f"/api/projects/{project}/transcript", json={"segments": [{"index": 2, "text": "bayar zakat yuk"}]})
    seen = {}
    real = pipeline.build_ass

    def spy(transcript, *a, **k):
        seen["words"] = [w["word"] for s in transcript["segments"] for w in s["words"]]
        return real(transcript, *a, **k)

    pipeline.build_ass = spy
    try:
        state = client.get(f"/api/projects/{project}/recipe").json()
        job = client.post(f"/api/projects/{project}/render-recipe",
                          json={"version": state["version"], "start": 4.5, "end": 7.5}).json()
        import time
        deadline = time.time() + 120
        while time.time() < deadline:
            j = client.get(f"/api/jobs/{job['id']}").json()
            if j["status"] in ("selesai", "gagal", "batal"):
                break
            time.sleep(0.3)
        assert j["status"] == "selesai", j
    finally:
        pipeline.build_ass = real
    assert "zakat" in seen["words"] and "jakat" not in seen["words"]
    item = client.get(f"/api/projects/{project}").json()["renders"][-1]
    out = PROJECTS_DIR / project / "renders" / item["file"]
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(out)],
                           capture_output=True, text=True, check=True).stdout
    assert float(probe) > 2.5
