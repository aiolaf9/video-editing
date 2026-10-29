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


def test_url_import_downloads_then_analyzes(sample_video, fake_transcript, monkeypatch):
    import shutil

    from app import pipeline, storage

    def fake_download(job, pid, url):
        shutil.copy(sample_video, storage.project_dir(pid) / "source.mp4")
        storage.update_project(pid, source="source.mp4", status="antre", name="Video dari link")
        job.update(100, "Unduhan selesai!")

    monkeypatch.setattr(pipeline, "download", fake_download)
    monkeypatch.setattr(transcribe, "transcribe", lambda *a, **k: fake_transcript)
    client = TestClient(main.app)
    res = client.post("/api/projects/from-url", json={"url": "https://example.com/v"}).json()
    pid = res["project"]["id"]
    assert _wait(client, res["job"]["id"])["status"] == "selesai"

    deadline = time.time() + 60
    while client.get(f"/api/projects/{pid}").json()["status"] != "siap":
        assert time.time() < deadline, client.get(f"/api/projects/{pid}").json()
        time.sleep(0.3)
    project = client.get(f"/api/projects/{pid}").json()
    assert project["name"] == "Video dari link"
    assert [j["kind"] for j in project["jobs"]] == ["analisis", "analisis"]


def test_failed_download_marks_project(monkeypatch):
    import yt_dlp

    class Boom:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def extract_info(self, *a, **k): raise yt_dlp.utils.DownloadError("ERROR: Video unavailable")

    monkeypatch.setattr(yt_dlp, "YoutubeDL", Boom)
    client = TestClient(main.app)
    res = client.post("/api/projects/from-url", json={"url": "https://youtube.com/watch?v=x"}).json()
    job = _wait(client, res["job"]["id"])
    assert job["status"] == "gagal" and "Video unavailable" in job["error"]
    assert client.get(f"/api/projects/{res['project']['id']}").json()["status"] == "gagal"


def test_animation_plan_endpoints_and_render(sample_video, fake_transcript, monkeypatch):
    from app import plan

    monkeypatch.setattr(transcribe, "transcribe", lambda *a, **k: fake_transcript)
    client = TestClient(main.app)
    with sample_video.open("rb") as fh:
        res = client.post("/api/projects", files={"file": ("anim.mp4", fh, "video/mp4")}).json()
    pid = res["project"]["id"]
    assert _wait(client, res["job"]["id"])["status"] == "selesai"
    assert isinstance(client.get(f"/api/projects/{pid}/faces").json(), list)

    rng = {"start": 0, "end": 8}
    auto = client.post(f"/api/projects/{pid}/plan/auto", json=rng).json()
    assert any(i["type"] == "zoom" for i in auto)

    prompt = client.post(f"/api/projects/{pid}/plan/prompt", json={**rng, "guidebook": "Gaya santai"}).json()["prompt"]
    assert "Gaya santai" in prompt and "Kenapa" in prompt

    reply = '```json\n{"items": [{"type": "card", "start": 0.5, "end": 3, "label": "Langkah 1", "text": "Kenapa viral?"},' \
            '{"type": "chips", "start": 3.2, "end": 5.5, "items": ["Rapi", "Cepat"]},' \
            '{"type": "keyword", "start": 5.7, "end": 7, "text": "Ayo"},' \
            '{"type": "zoom", "start": 1, "end": 2.2}]}\n```'
    items = client.post(f"/api/projects/{pid}/plan/import", json={**rng, "text": reply}).json()
    assert [i["type"] for i in items] == ["card", "zoom", "chips", "keyword"]
    assert client.post(f"/api/projects/{pid}/plan/import", json={**rng, "text": "halo"}).status_code == 400

    items[0]["text"] = "Diedit manual"
    saved = client.put(f"/api/projects/{pid}/plan", json=items).json()
    assert saved[0]["text"] == "Diedit manual"

    # Tanpa API key: pesan jelas, bukan crash.
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    r = client.post(f"/api/projects/{pid}/plan/ai", json=rng)
    assert r.status_code == 400 and "API key" in r.json()["detail"]
    monkeypatch.setattr(plan, "ask_claude", lambda *a, **k: [{"type": "keyword", "start": 2, "end": 3, "text": "AI"}])
    client.put("/api/settings", json={"provider": "claude", "api_key": "sk-ant-test-1234", "model": "claude-haiku-5-5"})
    s = client.get("/api/settings").json()
    claude = s["providers"]["claude"]
    assert claude["has_key"] and claude["key_hint"] == "...1234" and "sk-ant-test-1234" not in str(s)
    assert claude["model"] == "claude-haiku-5-5"
    ai = client.post(f"/api/projects/{pid}/plan/ai", json=rng).json()
    assert [i["text"] for i in ai if i["type"] == "keyword"] == ["AI"]
    client.post(f"/api/projects/{pid}/plan/import", json={**rng, "text": reply})

    body = {"start": 0, "end": 8, "reframe": "auto", "animations": True, "subtitles": {"style": "pop"}}
    job = _wait(client, client.post(f"/api/projects/{pid}/render", json=body).json()["id"])
    assert job["status"] == "selesai", job
    folder = main.storage.project_dir(pid) / "renders"
    assert not list(folder.glob("tmp_*")), "PNG sementara harus dibersihkan"
