import time

from fastapi.testclient import TestClient

from app import main, transcribe


def _wait(client, job_id, timeout=120):
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] in ("selesai", "gagal"):
            return job
        time.sleep(0.3)
    raise AssertionError("job timeout")


def test_upload_analyze_render_flow(sample_video, sample_music, fake_transcript, monkeypatch):
    # Whisper diganti transkrip palsu agar tes cepat dan tanpa unduh model.
    monkeypatch.setattr(transcribe, "transcribe", lambda *a, **k: fake_transcript)
    client = TestClient(main.app)

    assert client.get("/").status_code == 200
    assert client.get("/api/health").json()["ffmpeg"] is True

    with sample_video.open("rb") as fh:
        res = client.post("/api/projects", files={"file": ("demo video.mp4", fh, "video/mp4")})
    assert res.status_code == 200, res.text
    pid = res.json()["project"]["id"]
    job = _wait(client, res.json()["job"]["id"])
    assert job["status"] == "selesai", job

    project = client.get(f"/api/projects/{pid}").json()
    assert project["status"] == "siap" and project["name"] == "demo video"
    assert client.get(f"/api/projects/{pid}/preview").status_code == 200
    assert client.get(f"/api/projects/{pid}/transcript").json()["segments"]
    clips = client.post(f"/api/projects/{pid}/clips/suggest", json={"target": 5}).json()
    assert clips

    with sample_music.open("rb") as fh:
        music = client.post(f"/api/projects/{pid}/music", files={"file": ("lagu keren.mp3", fh, "audio/mpeg")}).json()

    body = {"start": clips[0]["start"], "end": clips[0]["end"], "title": "Klip uji",
            "music": {"file": music["file"]}, "subtitles": {"style": "karaoke"}}
    job = _wait(client, client.post(f"/api/projects/{pid}/render", json=body).json()["id"])
    assert job["status"] == "selesai", job

    item = client.get(f"/api/projects/{pid}").json()["renders"][0]
    dl = client.get(f"/api/projects/{pid}/renders/{item['file']}?download=true")
    assert dl.status_code == 200 and "Klip%20uji.mp4" in dl.headers["content-disposition"]

    assert client.delete(f"/api/projects/{pid}").json()["ok"]
    assert client.get(f"/api/projects/{pid}").status_code == 404


def test_rejects_bad_input():
    client = TestClient(main.app)
    assert client.post("/api/projects", files={"file": ("x.txt", b"hi", "text/plain")}).status_code == 400
    assert client.get("/api/projects/../../etc").status_code == 404
    assert client.get("/api/projects/zzzzzzzzzzzz").status_code == 404
    assert client.post("/api/projects/from-url", json={"url": "ftp://example.com/x"}).status_code == 400
