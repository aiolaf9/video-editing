"""Uji pemulihan staging: job unduhan yang SEDANG BERJALAN saat worker-io di-restart (SIGTERM) dan di-crash
(SIGKILL), dengan putaran pembersihan media dijalankan di tengah unduhan. Memakai fixture lambat yang dikendalikan.

    ADMIN_EMAIL=... ADMIN_PASSWORD=... COMPOSE="docker compose -f ... -f ..." python3 deploy/checks/recovery_staging.py

Desain yang diuji (app/worker.py): SIGTERM -> job dikembalikan ke antrean tanpa menghitung percobaan; crash -> saat
worker start lagi, job miliknya yang masih "jalan" diantrekan ulang (requeue_stale). Yang dibuktikan per skenario:
job akhirnya "selesai" (tidak menggantung), berkas identik dengan sumber, tepat satu job unduhan untuk proyek itu,
tidak ada berkas sementara tersisa, worker terdaftar & healthy lagi. Job lain (selesai/antre) tidak berubah.
"""
import os
import shlex
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
from e2e_staging import call, expect, login, report_and_exit, sh, verify_file, wait_job  # noqa: E402

COMPOSE = shlex.split(os.environ.get("COMPOSE", "docker compose"))


def compose(*args):
    return subprocess.run([*COMPOSE, *args], capture_output=True, text=True, timeout=300)


def psql(query):
    return sh("psql", "-U", "videoapp", "-d", "videoapp", "-tAc", query, service="db").strip()


def snapshot():
    return psql("select id || ':' || status from jobs order by id").splitlines()


def wait_running(job_id, min_progress=5, timeout=120):
    deadline = time.time() + timeout
    while time.time() < deadline:
        _, job = call("GET", f"/api/jobs/{job_id}")
        if job["status"] == "jalan" and (job.get("progress") or 0) >= min_progress:
            return job
        time.sleep(1)
    return job


def wait_healthy(service, timeout=180):
    deadline = time.time() + timeout
    while time.time() < deadline:
        cid = compose("ps", "-q", service).stdout.strip()
        state = subprocess.run(["docker", "inspect", "-f", "{{.State.Health.Status}}", cid], capture_output=True,
                               text=True).stdout.strip() if cid else ""
        if state == "healthy":
            return True
        time.sleep(2)
    return False


login()
src_sha = sh("python", "-c", "import urllib.request;print(urllib.request.urlopen("
             "'http://fixture/media/slow.mp4.sha256').read().decode())").strip()
before_other = snapshot()

for scenario, action in [("restart (SIGTERM)", lambda: compose("restart", "worker-io")),
                         ("crash (SIGKILL)", lambda: (compose("kill", "-s", "KILL", "worker-io"), compose("start", "worker-io")))]:
    code, res = call("POST", "/api/projects/from-url", {"url": "http://fixture/media/slow.mp4"})
    pid, jid = res["project"]["id"], res["job"]["id"]
    running = wait_running(jid)
    cleanup = sh("python", "-m", "app.manage", "media-cleanup")       # pembersihan di tengah unduhan
    mid = wait_running(jid, min_progress=(running.get("progress") or 0) + 1, timeout=30)
    action()
    after_action = psql(f"select status || '/' || attempts from jobs where id = '{jid}'")
    healthy = wait_healthy("worker-io")
    job = wait_job(jid, timeout=300)
    _, project = call("GET", f"/api/projects/{pid}")
    downloads = psql(f"select count(*) from jobs where project_id = '{pid}' and task = 'download'")
    ok, detail = verify_file(project, src_sha) if job["status"] == "selesai" and project.get("source") else (False, {})
    expect(f"{scenario}: job berjalan dipulihkan & selesai, berkas identik, 1 job unduhan, worker healthy",
           {"progress_saat_aksi": mid.get("progress"), "status_setelah_aksi": after_action, "akhir": job["status"],
            "error": job.get("error"), "job_unduhan": downloads, "worker_io_healthy": healthy,
            "cleanup_tengah_unduhan": cleanup.strip()[:120], **detail},
           running["status"] == "jalan" and job["status"] == "selesai" and downloads == "1" and healthy and ok)

after_map = {row.split(":")[0]: row for row in snapshot()}
after_other = [after_map.get(row.split(":")[0], "hilang") for row in before_other]
expect("job lain (selesai/gagal/antre) tidak berubah", {"sebelum": before_other, "sesudah": after_other},
       after_other == before_other)
expect("tidak ada job tertinggal 'jalan'", psql("select count(*) from jobs where status = 'jalan'"),
       psql("select count(*) from jobs where status = 'jalan'") == "0")
workers = psql("select string_agg(queues || ':' || state, ' ' order by queues) from workers where state <> 'berhenti'")
expect("worker terdaftar kembali", workers, "io:" in workers)

report_and_exit()
