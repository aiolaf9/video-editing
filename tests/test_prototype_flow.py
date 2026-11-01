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
