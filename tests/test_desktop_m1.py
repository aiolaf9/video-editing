"""Desktop M1: mode satu pengguna tanpa login (kunci peluncuran), penjaga Host, proyek tidak dihapus otomatis,
ekspor massal (klip x rasio) menjadi MP4 sungguhan, dan salinan ke folder ekspor."""
import json
import subprocess
import time

import pytest
from fastapi.testclient import TestClient

from app import config, jobs, lifecycle, main, pipeline, storage, transcribe
from app.config import PROJECTS_DIR

KEY = "kunci-uji-desktop-0123456789"  # pragma: allowlist secret


def _wait(c, job_id, timeout=240):
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = c.get(f"/api/jobs/{job_id}").json()
        if job["status"] in ("selesai", "gagal", "batal"):
            return job
        time.sleep(0.3)
    raise AssertionError("job timeout")


@pytest.fixture
def desktop(monkeypatch, tmp_path, app_client):
    export = tmp_path / "Videos" / "NH AI Clip Studio"
    monkeypatch.setattr(main, "DESKTOP_MODE", True)
    monkeypatch.setattr(main, "DESKTOP_LAUNCH_KEY", KEY)
    monkeypatch.setattr(main, "EXPORT_DIR", export)
    monkeypatch.setattr(config, "EXPORT_DIR", export)
    main.ensure_desktop_user()
    return export


def _desktop_client():
    return TestClient(main.app, base_url="http://127.0.0.1:47811")


def test_enter_requires_launch_key(desktop):
    c = _desktop_client()
    assert c.get("/api/auth/me").status_code == 401
    assert c.get("/api/desktop/enter").status_code == 404
    assert c.get("/api/desktop/enter", params={"key": "salah"}).status_code == 404
    r = c.get("/api/desktop/enter", params={"key": KEY}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/"
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie
    me = c.get("/api/auth/me").json()
    assert me["desktop"] is True and me["email"] == config.DESKTOP_USER_EMAIL
    assert me["export_dir"] == str(desktop)
    # Peluncuran berikutnya menggantikan sesi lama (sesi tidak menumpuk).
    old = c.cookies.get("ve_session")
    c2 = _desktop_client()
    c2.get("/api/desktop/enter", params={"key": KEY}, follow_redirects=False)
    assert c2.get("/api/auth/me").status_code == 200
    c.cookies.set("ve_session", old)
    assert c.get("/api/auth/me").status_code == 401


def test_enter_disabled_outside_desktop(app_client, monkeypatch):
    monkeypatch.setattr(main, "DESKTOP_LAUNCH_KEY", KEY)
    r = TestClient(main.app).get("/api/desktop/enter", params={"key": KEY}, follow_redirects=False)
    assert r.status_code == 404                                          # versi web tidak berubah
    assert TestClient(main.app).get("/api/desktop/info").json() == {"desktop": False}


def test_desktop_rejects_foreign_host_login_and_sw(desktop):
    evil = TestClient(main.app, base_url="http://evil.example:47811")
    assert evil.get("/api/desktop/enter", params={"key": KEY}).status_code == 403   # DNS rebinding
    assert evil.get("/").status_code == 403
    c = _desktop_client()
    assert c.get("/api/desktop/info").json() == {"desktop": True}
    login = c.post("/api/auth/login", json={"email": config.DESKTOP_USER_EMAIL, "password": "apa-saja-123"})
    assert login.status_code == 404                                      # tidak ada login di desktop
    assert c.get("/sw.js").status_code == 404                            # tanpa cache offline PWA
    assert TestClient(main.app, base_url="http://localhost:5000").get("/api/desktop/info").status_code == 200


def test_desktop_cleanup_keeps_projects(desktop, fake_transcript):
    pid = storage.create_project("desktop-simpan", None)["id"]
    folder = PROJECTS_DIR / pid
    (folder / "source.mp4").write_bytes(b"\0" * 1024)
    (folder / "renders").mkdir(exist_ok=True)
    (folder / "renders" / "tmp_sisa").mkdir()
    storage.write_data(pid, "transcript", fake_transcript)
    storage.update_project(pid, status="siap", duration=8.0, source="source.mp4")
    far = time.time() + 400 * 86400                                      # lebih dari setahun kemudian
    lifecycle.desktop_cleanup(now=far)
    assert (folder / "source.mp4").exists() and storage.read_data(pid, "transcript")
    assert storage.load_project(pid)["status"] == "siap"
    storage.delete_project(pid)


def test_safe_export_names():
    assert pipeline._safe_name('Klip 1 (9:16)') == "Klip 1 (9 16)"
    assert pipeline._safe_name('a/b\\c:d*e?f"g<h>|') == "a b c d e f g h"
    assert pipeline._safe_name("CON") == "_CON"
    assert pipeline._safe_name("nul.txt") == "_nul.txt"
    assert pipeline._safe_name("  titik...  ") == "titik"
    assert len(pipeline._safe_name("x" * 300)) == 80


def _probe(path):
    return json.loads(subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                                      "stream=width,height,codec_name", "-show_entries", "format=duration",
                                      "-of", "json", str(path)], capture_output=True, text=True, check=True).stdout)


def test_render_batch_real_mp4_and_export_copies(desktop, sample_video, fake_transcript, monkeypatch):
    monkeypatch.setattr(transcribe, "transcribe", lambda *a, **k: fake_transcript)
    c = _desktop_client()
    c.get("/api/desktop/enter", params={"key": KEY}, follow_redirects=False)
    with sample_video.open("rb") as fh:
        res = c.post("/api/projects", files={"file": ("Ceramah Pagi.mp4", fh, "video/mp4")}).json()
    pid = res["project"]["id"]
    assert _wait(c, res["job"]["id"])["status"] == "selesai"
    clips = c.post(f"/api/projects/{pid}/clips/suggest", json={"target": 5}).json()
    assert clips and all("score" in k and k.get("reasons") for k in clips)   # rekomendasi dengan skor & alasan

    state = c.get(f"/api/projects/{pid}/recipe").json()
    rec = state["recipe"]
    rec["subtitles"]["style"] = "pop"
    v = c.put(f"/api/projects/{pid}/recipe", json={"version": state["version"], "recipe": rec}).json()["version"]
    v = c.put(f"/api/projects/{pid}/recipe", json={"version": v, "recipe": rec}).json()["version"]
    body = {"version": v, "aspects": ["9:16", "1:1", "16:9"],
            "clips": [{"start": 0.4, "end": 3.0, "title": "Klip #1"}, {"start": 3.0, "end": 6.5, "title": "Klip #2"}]}
    assert c.post(f"/api/projects/{pid}/render-batch", json={**body, "version": v - 1}).status_code == 409
    assert c.post(f"/api/projects/{pid}/render-batch",
                  json={**body, "aspects": ["9:16", "9:16"]}).status_code == 422          # rasio ganda

    monkeypatch.setattr(main, "MAX_PENDING_PER_USER", 5)
    before = jobs.pending_for_user(c.get("/api/auth/me").json()["id"])
    assert c.post(f"/api/projects/{pid}/render-batch", json=body).status_code == 429   # 6 > 5: tidak ada yang antre
    assert jobs.pending_for_user(c.get("/api/auth/me").json()["id"]) == before
    monkeypatch.setattr(main, "MAX_PENDING_PER_USER", 150)

    r = c.post(f"/api/projects/{pid}/render-batch", json=body)
    assert r.status_code == 200, r.text
    assert r.json()["count"] == 6
    for job in r.json()["jobs"]:
        done = _wait(c, job["id"])
        assert done["status"] == "selesai", done

    sizes = {"9x16": (1080, 1920), "1x1": (1080, 1080), "16x9": (1920, 1080)}
    renders = c.get(f"/api/projects/{pid}").json()["renders"]
    assert len(renders) == 6
    for item in renders:
        aspect = item["title"].rsplit("(", 1)[1].rstrip(")")
        info = _probe(PROJECTS_DIR / pid / "renders" / item["file"])
        stream = info["streams"][0]
        assert (stream["width"], stream["height"]) == sizes[aspect] and stream["codec_name"] == "h264"
        assert 2.0 < float(info["format"]["duration"]) < 4.2
    exported = sorted(p.name for p in (desktop / "Ceramah Pagi").glob("*.mp4"))
    assert exported == sorted(f"Klip #{n} ({a}).mp4" for n in (1, 2) for a in sizes)
    for p in (desktop / "Ceramah Pagi").glob("*.mp4"):
        assert _probe(p)["streams"][0]["codec_name"] == "h264"
    c.delete(f"/api/projects/{pid}")
    assert (desktop / "Ceramah Pagi").exists()       # menghapus proyek tidak menghapus hasil ekspor pengguna


def test_oslock_exclusive_between_handles(tmp_path):
    from app import oslock
    path = tmp_path / "x.part"
    path.write_bytes(b"x" * 64)
    with open(path, "r+b") as a, open(path, "r+b") as b:
        assert oslock.try_lock(a)
        assert not oslock.try_lock(b)
        oslock.unlock(a)
        assert oslock.try_lock(b)
        oslock.unlock(b)
