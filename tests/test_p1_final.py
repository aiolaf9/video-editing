"""Verifikasi akhir P1: shutdown, pemulihan job macet, child process, konsistensi status."""
import os
import signal
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

from sqlalchemy import update

from app import db, jobcontrol, jobs, main, media, pipeline, storage, worker
from app.jobcontrol import JobCancelled, JobControl

REPO_ROOT = Path(__file__).resolve().parent.parent
BODY = {"request": {"start": 0, "end": 4, "subtitles": {"enabled": False}, "animations": False,
                    "reframe": "manual"}}


def _alive(pattern):
    return bool(subprocess.run(["pgrep", "-f", pattern], capture_output=True, text=True).stdout.strip())


def _project():
    return storage.create_project("uji-p1")["id"]


def _age_heartbeat(job_id, seconds=999):
    with db.engine().begin() as c:
        c.execute(update(db.jobs).where(db.jobs.c.id == job_id).values(heartbeat=time.time() - seconds))


# 1. Restart/shutdown FastAPI di mode server tidak menyentuh job worker terpisah ---------------

def test_api_shutdown_in_server_mode_leaves_external_jobs(monkeypatch):
    from fastapi.testclient import TestClient

    pid = _project()
    job = jobs.submit("render", "render", pid, BODY, "server", queue="uji-srv")
    assert jobs.claim(("uji-srv",), "server-worker-7")["id"] == job["id"]
    monkeypatch.setattr(main, "EMBEDDED_WORKERS", False)
    for _ in range(2):  # dua kali start + shutdown (restart)
        with TestClient(main.app) as c:
            assert c.get("/api/health").status_code == 200
    after = jobs.get(job["id"])
    assert after["status"] == "jalan" and after["worker"] == "server-worker-7"


def test_local_startup_recovery_ignores_server_workers():
    pid = _project()
    server_job = jobs.submit("render", "render", pid, BODY, "server", queue="uji-mix")
    jobs.claim(("uji-mix",), "server-worker-1")
    local_job = jobs.submit("render", "render", pid, BODY, "lokal", queue="uji-mix")
    jobs.claim(("uji-mix",), f"{worker.LOCAL_PREFIX}berat-9")
    jobs.requeue_stale(all_running=True, worker_prefix=worker.LOCAL_PREFIX)
    assert jobs.get(server_job["id"])["status"] == "jalan"     # heartbeat masih segar: aman
    assert jobs.get(local_job["id"])["status"] == "antre"      # sisa worker lokal: diulang


# 2. Job batal tidak bisa hidup lagi lewat pemulihan job macet ---------------------------------

def test_cancelled_jobs_are_never_requeued():
    pid = _project()
    queued = jobs.submit("render", "render", pid, BODY, "a", queue="uji-batal")
    assert jobs.request_cancel(queued["id"]) == "batal"
    running = jobs.submit("render", "render", pid, BODY, "b", queue="uji-batal")
    jobs.claim(("uji-batal",), "w-1")
    assert jobs.request_cancel(running["id"]) == "berhenti"
    for jid in (queued["id"], running["id"]):
        _age_heartbeat(jid)
    jobs.requeue_stale()
    jobs.requeue_stale(all_running=True)
    jobs.requeue_stale(all_running=True, worker_prefix="w-")
    assert jobs.get(queued["id"])["status"] == "batal"
    assert jobs.get(running["id"])["status"] == "batal"
    assert jobs.claim(("uji-batal",), "w-2") is None
    assert jobs.requeue(running["id"]) is False                 # requeue saat shutdown juga menolak


def test_shutdown_requeues_job_without_counting_attempt(monkeypatch):
    pid = _project()
    marker = f"vetest-{uuid.uuid4().hex[:8]}"

    def endless(job, pid_, req):
        media.run_ffmpeg(["-f", "lavfi", "-i", "testsrc2=size=160x120:rate=10", "-metadata", f"comment={marker}",
                          "-f", "null", "-"], duration=10_000, progress=job.stage(0, 100, "x"))

    monkeypatch.setattr(pipeline, "render", endless)
    job = jobs.submit("render", "render", pid, BODY, "shutdown", queue="uji-shut")
    t = threading.Thread(target=worker.run_one, args=(("uji-shut",), "uji-shut"))
    t.start()
    deadline = time.time() + 10
    while not _alive(marker) and time.time() < deadline:
        time.sleep(0.1)
    assert jobcontrol.cancel_all("shutdown") >= 1
    t.join(15)
    after = jobs.get(job["id"])
    assert after["status"] == "antre" and after["attempts"] == 0 and after["worker"] is None
    assert not _alive(marker)


def test_standalone_worker_sigterm_requeues_and_kills_ffmpeg(tmp_path):
    pid = _project()
    marker = f"vetest-{uuid.uuid4().hex[:8]}"
    job = jobs.submit("render", "render", pid, BODY, "sigterm", queue="uji-sig")
    script = tmp_path / "w.py"
    script.write_text(
        "import sys\n"
        f"sys.path.insert(0, {repr(str(REPO_ROOT))})\n"
        "from app import media, pipeline, worker, jobs\n"
        "jobs.QUEUES = jobs.QUEUES + ('uji-sig',)\n"
        "def endless(job, pid, req):\n"
        f"    media.run_ffmpeg(['-f','lavfi','-i','testsrc2=size=160x120:rate=10','-metadata','comment={marker}',"
        "'-f','null','-'], duration=10000, progress=job.stage(0, 100, 'x'))\n"
        "pipeline.render = endless\n"
        "sys.argv = ['worker', '--queues', 'uji-sig']\n"
        "worker.main()\n")
    proc = subprocess.Popen([sys.executable, str(script)], env=os.environ.copy(),
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    deadline = time.time() + 20
    while not _alive(marker) and time.time() < deadline:
        time.sleep(0.1)
    assert _alive(marker), "worker tidak sempat menjalankan FFmpeg"
    proc.send_signal(signal.SIGTERM)
    out, _ = proc.communicate(timeout=30)
    assert proc.returncode == 0, out[-2000:]
    assert not _alive(marker), "FFmpeg tertinggal setelah worker dihentikan"
    after = jobs.get(job["id"])
    assert after["status"] == "antre" and after["attempts"] == 0


# 3. Seluruh child process ikut berhenti ---------------------------------------------------------

def test_cancel_kills_whole_process_tree():
    # Proses induk (sh) dengan dua anak (sleep) yang punya pola unik.
    a, b = 4000 + uuid.uuid4().int % 900, 5000 + uuid.uuid4().int % 900
    control = JobControl("uji", timeout=600)
    box = {}

    def work():
        try:
            with jobcontrol.activate(control):
                jobcontrol.run_capture(["sh", "-c", f"sleep {a}.25 & sleep {b}.25 & wait"])
        except BaseException as exc:  # termasuk JobCancelled (BaseException)
            box["error"] = exc

    t = threading.Thread(target=work)
    t.start()
    deadline = time.time() + 10
    while not (_alive(f"sleep {a}.25") and _alive(f"sleep {b}.25")) and time.time() < deadline:
        time.sleep(0.1)
    assert _alive(f"sleep {a}.25") and _alive(f"sleep {b}.25")
    control.cancel("dibatalkan")
    t.join(15)
    assert isinstance(box.get("error"), JobCancelled)
    time.sleep(0.3)
    assert not _alive(f"sleep {a}.25") and not _alive(f"sleep {b}.25"), "proses anak tertinggal"


# 4. Status pembatalan konsisten (dijalankan di SQLite & PostgreSQL) ------------------------------

def test_cancel_state_machine_is_consistent():
    pid = _project()
    seen = []
    j = jobs.submit("render", "render", pid, BODY, "sm", queue="uji-sm")
    seen.append(jobs.get(j["id"])["status"])                       # antre
    jobs.claim(("uji-sm",), "w")
    seen.append(jobs.get(j["id"])["status"])                       # jalan
    seen.append(jobs.request_cancel(j["id"]))                      # berhenti
    seen.append(jobs.request_cancel(j["id"]))                      # tetap berhenti (idempoten)
    jobs.finish(j["id"], error="Dibatalkan oleh pengguna.", cancelled=True)
    seen.append(jobs.get(j["id"])["status"])                       # batal
    seen.append(jobs.request_cancel(j["id"]))                      # None: sudah selesai
    assert seen == ["antre", "jalan", "berhenti", "berhenti", "batal", None]
    view = jobs.view(jobs.get(j["id"]))
    assert view["status"] == "batal" and view["message"] == "Dibatalkan"
