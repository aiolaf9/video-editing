"""Prototipe V2 (Prompt 1): koreksi transkrip/subtitle oleh pengguna dan batas durasi video demo."""
import copy
import time

from app import config, transcribe, transcript_edit
from app.subtitles import SubtitleOptions, build_ass


def _wait(client, job_id, timeout=120):
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] in ("selesai", "gagal", "batal"):
            return job
        time.sleep(0.3)
    raise AssertionError("job timeout")


def _upload(client, video, name="uji.mp4"):
    with video.open("rb") as fh:
        res = client.post("/api/projects", files={"file": (name, fh, "video/mp4")})
    assert res.status_code == 200, res.text
    return res.json()["project"]["id"], res.json()["job"]["id"]


# ------------------------------------------------------------------ logika koreksi

def test_edit_same_word_count_keeps_word_timings(fake_transcript):
    t = copy.deepcopy(fake_transcript)
    before = [(w["start"], w["end"]) for w in t["segments"][0]["words"]]
    transcript_edit.apply_edits(t, [{"index": 0, "text": "Kenapa  klip ini viral?"}])
    seg = t["segments"][0]
    assert [w["word"] for w in seg["words"]] == ["Kenapa", "klip", "ini", "viral?"]
    assert [(w["start"], w["end"]) for w in seg["words"]] == before and seg["edited"] is True
    assert seg["text"] == "Kenapa klip ini viral?"


def test_edit_different_word_count_spreads_over_segment(fake_transcript):
    t = copy.deepcopy(fake_transcript)
    seg = t["segments"][1]
    start, end = seg["words"][0]["start"], seg["words"][-1]["end"]
    transcript_edit.apply_edits(t, [{"index": 1, "text": "Editnya rapi"}])
    words = t["segments"][1]["words"]
    assert [w["word"] for w in words] == ["Editnya", "rapi"]
    assert words[0]["start"] == start and abs(words[-1]["end"] - end) < 0.01
    transcript_edit.apply_edits(t, [{"index": 2, "text": "   "}])             # kosong = tanpa subtitle
    assert t["segments"][2]["words"] == [] and t["segments"][2]["text"] == ""


def test_edit_rejects_unknown_segment(fake_transcript):
    try:
        transcript_edit.apply_edits(copy.deepcopy(fake_transcript), [{"index": 9, "text": "x"}])
    except transcript_edit.EditError:
        return
    raise AssertionError("indeks di luar jangkauan harus ditolak")


def test_corrected_text_is_used_by_subtitles(fake_transcript):
    t = transcript_edit.apply_edits(copy.deepcopy(fake_transcript), [{"index": 0, "text": "Kenapa klip ini meledak?"}])
    ass = build_ass(t, 0.0, 5.0, 1080, 1920, SubtitleOptions())
    assert "MELEDAK" in ass.upper() and "VIRAL" not in ass.upper()      # gaya bawaan memakai huruf kapital


# ------------------------------------------------------------------ API

def test_transcript_edit_endpoint_then_render(sample_video, fake_transcript, monkeypatch, client):
    monkeypatch.setattr(transcribe, "transcribe", lambda *a, **k: copy.deepcopy(fake_transcript))
    pid, jid = _upload(client, sample_video)
    assert _wait(client, jid)["status"] == "selesai"
    bad = client.put(f"/api/projects/{pid}/transcript", json={"segments": [{"index": 99, "text": "x"}]})
    assert bad.status_code == 422
    res = client.put(f"/api/projects/{pid}/transcript", json={"segments": [{"index": 0, "text": "Kenapa klip ini meledak?"}]})
    assert res.status_code == 200, res.text
    stored = client.get(f"/api/projects/{pid}/transcript").json()
    assert stored["segments"][0]["text"] == "Kenapa klip ini meledak?"
    job = _wait(client, client.post(f"/api/projects/{pid}/render",
                                    json={"start": 0.0, "end": 6.0, "title": "Koreksi", "aspect": "9:16"}).json()["id"])
    assert job["status"] == "selesai", job
    client.delete(f"/api/projects/{pid}")


def test_transcript_edit_before_analysis_is_rejected(client):
    from app import storage
    pid = storage.create_project("belum-dianalisis")["id"]
    res = client.put(f"/api/projects/{pid}/transcript", json={"segments": [{"index": 0, "text": "x"}]})
    assert res.status_code in (404, 409)   # proyek tanpa pemilik terlihat atau belum ada transkrip
    storage.delete_project(pid)


def test_video_longer_than_limit_fails_with_clear_message(sample_video, fake_transcript, monkeypatch, client):
    monkeypatch.setattr(transcribe, "transcribe", lambda *a, **k: (_ for _ in ()).throw(AssertionError("tidak boleh")))
    monkeypatch.setattr(config, "MAX_VIDEO_SECONDS", 5.0)                  # sample_video berdurasi 8 detik
    assert client.get("/api/auth/me").json()["max_video_seconds"] == 5.0
    pid, jid = _upload(client, sample_video)
    job = _wait(client, jid)
    assert job["status"] == "gagal" and "melebihi batas" in job["error"] and "menit" in job["error"], job
    assert client.get(f"/api/projects/{pid}").json()["status"] == "gagal"
    client.delete(f"/api/projects/{pid}")


def test_no_limit_by_default():
    assert config.MAX_VIDEO_SECONDS == 0.0


def test_transcript_edit_is_unit_isolated_and_blocked_during_analysis(sample_video, fake_transcript, monkeypatch, client):
    from app import jobs
    from tests.conftest import login_client
    monkeypatch.setattr(transcribe, "transcribe", lambda *a, **k: copy.deepcopy(fake_transcript))
    a = client.post("/api/admin/units", json={"name": f"Unit A {time.time()}"}).json()
    b = client.post("/api/admin/units", json={"name": f"Unit B {time.time()}"}).json()
    users = {}
    for key, unit in (("a", a), ("b", b)):
        email = f"{key}{time.time()}@uji.local"
        r = client.post("/api/admin/users", json={"email": email, "name": key, "password": "password-123",
                                                  "role": "editor", "unit_id": unit["id"]})
        assert r.status_code == 200, r.text
        users[key] = login_client(email, "password-123")
    pid, jid = _upload(users["a"], sample_video)
    assert _wait(users["a"], jid)["status"] == "selesai"
    body = {"segments": [{"index": 0, "text": "Teks dari unit lain"}]}
    assert users["b"].put(f"/api/projects/{pid}/transcript", json=body).status_code == 404    # unit lain: tak terlihat
    assert users["a"].get(f"/api/projects/{pid}/transcript").json()["segments"][0]["text"] != "Teks dari unit lain"
    assert users["a"].put(f"/api/projects/{pid}/transcript", json={"segments": [{"index": 0, "text": "x" * 2001}]}
                          ).status_code == 422                                                  # validasi panjang
    held = jobs.submit("analyze", "analisis", pid, {"language": None}, "Analisis ulang", queue="uji-proto-blok")
    assert users["a"].put(f"/api/projects/{pid}/transcript", json=body).status_code == 409    # analisis berjalan
    jobs.request_cancel(held["id"])
    assert users["a"].put(f"/api/projects/{pid}/transcript", json=body).status_code == 200
    users["a"].delete(f"/api/projects/{pid}")


def _events(ass: str) -> list[tuple[float, float, str]]:
    def sec(t):
        h, m, s = t.split(":")
        return int(h) * 3600 + int(m) * 60 + float(s)
    out = []
    for line in ass.splitlines():
        if line.startswith("Dialogue:"):
            parts = line.split(",", 9)
            out.append((sec(parts[1]), sec(parts[2]), parts[9]))
    return out


def test_overlapping_segments_give_ordered_non_overlapping_subtitles():
    # Batas potongan transkripsi: segmen A berakhir setelah segmen B mulai (seperti 95,4-115,4 vs 114,4-134,4).
    w = lambda s, e, t: {"start": s, "end": e, "word": t}   # noqa: E731
    t = {"segments": [
        {"start": 0.0, "end": 3.0, "text": "satu dua tiga", "words": [w(0.0, 1.0, "satu"), w(1.0, 2.0, "dua"), w(2.0, 3.0, "tiga")]},
        {"start": 2.4, "end": 5.0, "text": "empat lima", "words": [w(2.4, 3.6, "empat"), w(3.6, 5.0, "lima")]},
    ]}
    for style in ("simple", "karaoke", "word"):
        ev = _events(build_ass(t, 0.0, 5.0, 1080, 1920, SubtitleOptions(style=style, words_per_line=2)))
        assert ev and all(e > s for s, e, _ in ev)
        for (s1, e1, _), (s2, e2, _) in zip(ev, ev[1:]):
            assert s2 >= s1 and e1 <= s2 + 1e-6, (style, ev)              # urut waktu, tidak bertumpuk
    words = __import__("app.subtitles", fromlist=["collect_words"]).collect_words(t, 0.0, 5.0)
    assert [x["word"] for x in words] == ["satu", "dua", "tiga", "empat", "lima"]


def test_redistributed_words_stay_inside_segment():
    t = {"segments": [{"start": 10.0, "end": 14.0, "text": "a b",
                       "words": [{"start": 9.2, "end": 11.0, "word": "a"}, {"start": 11.0, "end": 15.3, "word": "b"}]}]}
    transcript_edit.apply_edits(t, [{"index": 0, "text": "satu dua tiga empat"}])
    ws = t["segments"][0]["words"]
    assert len(ws) == 4 and ws[0]["start"] >= 10.0 and ws[-1]["end"] <= 14.0 + 1e-6


def test_subtitle_ordering_never_drops_words_and_leaves_normal_words_alone(caplog):
    from app.subtitles import collect_words
    w = lambda s, e, t: {"start": s, "end": e, "word": t}   # noqa: E731
    normal = {"segments": [{"start": 0.0, "end": 3.0, "text": "a b c",
                            "words": [w(0.0, 1.0, "satu"), w(1.0, 2.0, "dua"), w(2.2, 3.0, "tiga")]}]}
    assert collect_words(normal, 0.0, 3.0) == [w(0.0, 1.0, "satu"), w(1.0, 2.0, "dua"), w(2.2, 3.0, "tiga")]
    covered = {"segments": [
        {"start": 0.0, "end": 3.0, "text": "panjang dua", "words": [w(0.0, 2.0, "panjang"), w(2.0, 3.0, "dua")]},
        {"start": 0.5, "end": 1.5, "text": "tertutup", "words": [w(0.5, 1.5, "tertutup")]},
    ]}
    with caplog.at_level("INFO", logger="video-editing.subtitles"):
        out = collect_words(covered, 0.0, 3.0)
    assert [x["word"] for x in out] == ["panjang", "tertutup", "dua"]            # tidak ada kata yang hilang
    assert all(b["start"] >= a["start"] for a, b in zip(out, out[1:]))
    assert all(x["end"] - x["start"] > 0.1 for x in out)
    assert "digeser" in caplog.text                                                 # tidak diam-diam
    ev = _events(build_ass(covered, 0.0, 3.0, 1080, 1920, SubtitleOptions(style="simple", words_per_line=1)))
    assert len(ev) == 3 and all(e1 <= s2 + 1e-6 for (_, e1, _), (s2, _, _) in zip(ev, ev[1:]))
