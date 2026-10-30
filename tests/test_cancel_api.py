"""P1: pembatalan & batas waktu end-to-end lewat API dan worker bawaan."""
import subprocess
import time
import uuid
from pathlib import Path

import pytest
from conftest import login_client

from app import jobs, media, netpolicy, pipeline, storage, transcribe, worker


def _wait_status(c, job_id, statuses, timeout=60):
    deadline = time.time() + timeout
    job = None
    while time.time() < deadline:
        job = c.get(f"/api/jobs/{job_id}").json()
        if job["status"] in statuses:
            return job
        time.sleep(0.2)
    raise AssertionError(f"status {statuses} tidak tercapai, terakhir: {job}")


def _alive(marker):
    return bool(subprocess.run(["pgrep", "-f", marker], capture_output=True, text=True).stdout.strip())


@pytest.fixture
def ready_project(client, sample_video, fake_transcript, monkeypatch):
    monkeypatch.setattr(transcribe, "transcribe", lambda *a, **k: fake_transcript)
    with sample_video.open("rb") as fh:
        res = client.post("/api/projects", files={"file": ("batal.mp4", fh, "video/mp4")}).json()
    _wait_status(client, res["job"]["id"], ("selesai",))
    return res["project"]["id"]


def _endless_render(marker):
    """Pengganti pipeline.render: FFmpeg tanpa akhir yang melaporkan progress (seperti render asli)."""
    def fake(job, pid, req):
        args = ["-f", "lavfi", "-i", "testsrc2=size=160x120:rate=10", "-metadata", f"comment={marker}",
                "-f", "null", "-"]
        media.run_ffmpeg(args, duration=10_000, progress=job.stage(2, 98, "Merender video..."))
    return fake


REPO_ROOT = Path(__file__).resolve().parent.parent
RENDER_BODY = {"start": 0, "end": 4, "subtitles": {"enabled": False}, "animations": False, "reframe": "manual"}


def test_cancel_running_render_kills_ffmpeg(client, ready_project, monkeypatch):
    marker = f"vetest-{uuid.uuid4().hex[:8]}"
    monkeypatch.setattr(pipeline, "render", _endless_render(marker))
    job = client.post(f"/api/projects/{ready_project}/render", json=RENDER_BODY).json()
    _wait_status(client, job["id"], ("jalan",))
    deadline = time.time() + 10
    while not _alive(marker) and time.time() < deadline:
        time.sleep(0.1)
    assert _alive(marker)

    started = time.time()
    r = client.post(f"/api/jobs/{job['id']}/cancel").json()
    assert r["status"] in ("berhenti", "batal")
    done = _wait_status(client, job["id"], ("batal",), timeout=20)
    assert time.time() - started < 10, "pembatalan harus selesai dalam hitungan detik"
    assert done["message"] == "Dibatalkan"
    assert not _alive(marker), "FFmpeg masih hidup setelah job dibatalkan"
    # Job selesai tidak bisa dibatalkan lagi; proyek bisa dihapus/diproses lagi.
    assert client.post(f"/api/jobs/{job['id']}/cancel").status_code == 409


def test_cancel_queued_job_never_runs(client, ready_project):
    meta = storage.load_project(ready_project)
    # Antrean "uji" tidak dilayani worker mana pun: job pasti masih menunggu.
    job = jobs.submit("render", "render", ready_project, {"request": RENDER_BODY}, "antre", queue="uji",
                      user_id=None, unit_id=meta["unit_id"])
    r = client.post(f"/api/jobs/{job['id']}/cancel")
    assert r.status_code == 200 and r.json()["status"] == "batal"
    assert jobs.claim(("uji",), "uji") is None


def test_render_timeout_marks_failed(client, ready_project, monkeypatch):
    marker = f"vetest-{uuid.uuid4().hex[:8]}"

    def no_progress_render(job, pid, req):
        # Tanpa callback progress: batas waktu harus ditegakkan oleh watchdog, bukan oleh pipeline.
        media.run_ffmpeg(["-f", "lavfi", "-i", "testsrc2=size=160x120:rate=10", "-metadata", f"comment={marker}",
                          "-f", "null", "-"], duration=0)

    monkeypatch.setattr(pipeline, "render", no_progress_render)
    monkeypatch.setattr(worker, "initial_timeout", lambda job: 2.0)
    job = client.post(f"/api/projects/{ready_project}/render", json=RENDER_BODY).json()
    done = _wait_status(client, job["id"], ("gagal", "selesai", "batal"), timeout=30)
    assert done["status"] == "gagal" and "batas waktu" in done["error"]
    assert not _alive(marker)


def test_unresponsive_job_is_abandoned_not_stuck(client, ready_project, monkeypatch):
    """Job yang tidak pernah memeriksa pembatalan (mis. tertahan di kode native) tidak boleh menggantung."""
    def stuck_render(job, pid, req):
        time.sleep(8)  # tidak memanggil check(), tidak punya subprocess yang bisa di-kill

    monkeypatch.setattr(pipeline, "render", stuck_render)
    monkeypatch.setattr(worker, "initial_timeout", lambda job: 1.0)
    monkeypatch.setattr(worker, "CANCEL_GRACE", 2.0)
    job = client.post(f"/api/projects/{ready_project}/render", json=RENDER_BODY).json()
    done = _wait_status(client, job["id"], ("gagal",), timeout=20)
    assert "tidak merespons" in done["error"]
    time.sleep(6)  # setelah fungsi macet akhirnya kembali, status tidak boleh berubah jadi "selesai"
    assert client.get(f"/api/jobs/{job['id']}").json()["status"] == "gagal"


def test_cancel_reanalysis_keeps_ready_project(client, ready_project, monkeypatch):
    def slow_transcribe(path, duration, language=None, progress=None):
        for i in range(400):
            time.sleep(0.05)
            if progress:
                progress(i / 400)
        return {"language": "id", "segments": []}

    monkeypatch.setattr(transcribe, "transcribe", slow_transcribe)
    job = client.post(f"/api/projects/{ready_project}/reanalyze").json()
    _wait_status(client, job["id"], ("jalan",))
    while client.get(f"/api/jobs/{job['id']}").json()["progress"] < 30:
        time.sleep(0.2)
    client.post(f"/api/jobs/{job['id']}/cancel")
    _wait_status(client, job["id"], ("batal",), timeout=20)
    project = client.get(f"/api/projects/{ready_project}").json()
    assert project["status"] == "siap", "hasil analisis lama harus tetap bisa dipakai"
    assert (storage.project_dir(ready_project) / "preview.mp4").exists()
    assert not list(storage.project_dir(ready_project).glob(".preview.part*"))


def test_cancel_new_analysis_marks_project_cancelled(client, sample_video, monkeypatch):
    def slow_transcribe(path, duration, language=None, progress=None):
        for i in range(400):
            time.sleep(0.05)
            if progress:
                progress(i / 400)
        return {"language": "id", "segments": []}

    monkeypatch.setattr(transcribe, "transcribe", slow_transcribe)
    with sample_video.open("rb") as fh:
        res = client.post("/api/projects", files={"file": ("baru.mp4", fh, "video/mp4")}).json()
    pid, jid = res["project"]["id"], res["job"]["id"]
    _wait_status(client, jid, ("jalan",))
    while client.get(f"/api/jobs/{jid}").json()["progress"] < 30:
        time.sleep(0.2)
    client.post(f"/api/jobs/{jid}/cancel")
    _wait_status(client, jid, ("batal",), timeout=20)
    assert client.get(f"/api/projects/{pid}").json()["status"] == "batal"
    # Setelah dibatalkan, proyek bisa dihapus (tidak tertahan "masih diproses").
    assert client.delete(f"/api/projects/{pid}").json()["ok"]


def test_cancel_permissions(client, ready_project):
    stamp = uuid.uuid4().hex[:8]
    meta = storage.load_project(ready_project)
    other = client.post("/api/admin/units", json={"name": f"Lain {stamp}"}).json()
    for email, unit in [(f"rekan{stamp}@org.id", meta["unit_id"]), (f"asing{stamp}@org.id", other["id"])]:
        client.post("/api/admin/users", json={"email": email, "password": "password-123", "role": "editor",
                                              "unit_id": unit})
    colleague = login_client(f"rekan{stamp}@org.id", "password-123")
    stranger = login_client(f"asing{stamp}@org.id", "password-123")
    job = jobs.submit("render", "render", ready_project, {"request": RENDER_BODY}, "x", queue="uji",
                      user_id="orang-lain", unit_id=meta["unit_id"])
    assert stranger.post(f"/api/jobs/{job['id']}/cancel").status_code == 404   # unit lain: tidak terlihat
    assert colleague.post(f"/api/jobs/{job['id']}/cancel").status_code == 403  # bukan pemilik/Admin Unit
    assert client.post(f"/api/jobs/{job['id']}/cancel").json()["status"] == "batal"  # admin boleh


def test_worker_dies_while_cancelling(ready_project):
    from sqlalchemy import update

    from app import db

    job = jobs.submit("render", "render", ready_project, {"request": RENDER_BODY}, "x", queue="uji")
    assert jobs.claim(("uji",), "akan-mati")["id"] == job["id"]
    assert jobs.request_cancel(job["id"]) == "berhenti"
    with db.engine().begin() as c:  # worker mati: heartbeat berhenti
        c.execute(update(db.jobs).where(db.jobs.c.id == job["id"]).values(heartbeat=time.time() - 999))
    jobs.requeue_stale()
    assert jobs.get(job["id"])["status"] == "batal"  # tidak diulang, tidak menggantung


def test_cancel_download_cleans_partial_files(client, sample_video, monkeypatch):
    """yt-dlp tidak boleh menelan pembatalan; file setengah jadi harus dibuang."""
    import http.server
    import threading

    data = sample_video.read_bytes()

    class Slow(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_HEAD(self):
            self.send_response(200)
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()

        def do_GET(self):
            self.do_HEAD()
            try:
                for i in range(0, len(data), 4096):  # sangat lambat: ±40 KB/detik
                    self.wfile.write(data[i:i + 4096])
                    self.wfile.flush()
                    time.sleep(0.1)
            except (BrokenPipeError, ConnectionResetError):
                pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Slow)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setattr(netpolicy, "URL_IMPORT_TRUSTED_NETWORKS", "127.0.0.0/8")   # server uji lokal, impor admin
    try:
        res = client.post("/api/projects/from-url", json={"url": f"http://127.0.0.1:{srv.server_port}/video.mp4"})
        assert res.status_code == 200, res.text
        pid, jid = res.json()["project"]["id"], res.json()["job"]["id"]
        deadline = time.time() + 30
        while time.time() < deadline:
            job = client.get(f"/api/jobs/{jid}").json()
            if job["status"] == "jalan" and "MB" in (job["message"] or ""):
                break
            assert job["status"] in ("antre", "jalan"), job
            time.sleep(0.2)
        client.post(f"/api/jobs/{jid}/cancel")
        done = _wait_status(client, jid, ("batal", "gagal", "selesai"), timeout=20)
        assert done["status"] == "batal", done
        assert client.get(f"/api/projects/{pid}").json()["status"] == "batal"
        assert not list(storage.project_dir(pid).glob("source.*")), "file unduhan setengah jadi tertinggal"
    finally:
        srv.shutdown()


def test_standalone_worker_exits_when_job_is_stuck(ready_project, tmp_path):
    """Mode server: worker terpisah keluar (kode 3) agar di-restart Docker, dan job tidak menggantung."""
    import os
    import sys

    job = jobs.submit("render", "render", ready_project, {"request": RENDER_BODY}, "macet", queue="uji-proses")
    script = tmp_path / "stuck_worker.py"
    script.write_text(
        "import sys, time\n"
        f"sys.path.insert(0, {repr(str(REPO_ROOT))})\n"
        "from app import pipeline, worker\n"
        "pipeline.render = lambda job, pid, req: time.sleep(60)\n"
        "worker.initial_timeout = lambda job: 1.0\n"
        "worker.CANCEL_GRACE = 2.0\n"
        "worker.loop(('uji-proses',), 'uji-proses', standalone=True)\n")
    proc = subprocess.run([sys.executable, str(script)], env=os.environ.copy(), capture_output=True, text=True,
                          timeout=60)
    assert proc.returncode == 3, proc.stderr[-2000:]
    done = jobs.get(job["id"])
    assert done["status"] == "gagal" and "tidak merespons" in done["error"]


def test_status_values_fit_database_columns():
    """SQLite tidak memeriksa panjang VARCHAR, PostgreSQL menolaknya: jaga di sini agar ketahuan lebih awal."""
    from app import db

    limit = db.jobs.c.status.type.length
    assert all(len(s) <= limit for s in (*jobs.ACTIVE, *jobs.DONE)), (jobs.ACTIVE, jobs.DONE, limit)
    from app import uploads
    assert all(len(s) <= db.uploads.c.state.type.length for s in uploads.STATES), uploads.STATES
    assert all(len(s) <= db.workers.c.state.type.length for s in ("idle", "sibuk", "menunggu", "berhenti"))
    assert len("mengunggah") <= db.projects.c.status.type.length
