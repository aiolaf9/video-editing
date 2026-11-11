"""P0: admission menurut worker HIDUP (heartbeat masih berlaku) dan status antrean yang jelas.

Worker bawaan tes (thread "lokal-*") disembunyikan dari pemantauan; yang terlihat hanya baris worker "uji-p0-*" di
tabel workers yang heartbeat-nya diatur tes. Jadi logika hidup/mati yang dipakai adalah logika aslinya.
"""
import time

import pytest
from sqlalchemy import delete, update

from app import db, jobs, monitor, storage, transcribe

PREFIX = "uji-p0-"
H = {"Tus-Resumable": "1.0.0"}


@pytest.fixture
def fleet(monkeypatch):
    real = monitor.list_workers
    monkeypatch.setattr(monitor, "list_workers", lambda now=None: [w for w in real(now) if w["name"].startswith(PREFIX)])

    class Fleet:
        def up(self, name, queues):
            monitor.register(PREFIX + name, tuple(queues))

        def expire(self, name):
            with db.engine().begin() as c:
                c.execute(update(db.workers).where(db.workers.c.name == PREFIX + name).values(
                    heartbeat=time.time() - monitor.alive_after() - 5))

        def stop(self, name):
            monitor.report(PREFIX + name, "berhenti")

    yield Fleet()
    with db.engine().begin() as c:
        c.execute(delete(db.workers).where(db.workers.c.name.like(PREFIX + "%")))


def _tus_create(client, size=1000):
    return client.post("/api/uploads", headers={**H, "Upload-Length": str(size),
                                                "Upload-Metadata": "filename dXppLm1wNA=="})   # "uzi.mp4"


def _ready_project(name="p0-siap"):
    meta = storage.create_project(name)
    return storage.update_project(meta["id"], status="siap", duration=30.0, source="source.mp4")


RENDER = {"start": 0, "end": 4, "subtitles": {"enabled": False}, "animations": False, "reframe": "manual"}


# ------------------------------------------------------------------ admission

def test_no_live_worker_rejects_all_new_work_with_clear_message(client, sample_video, fleet):
    r = _tus_create(client)
    assert r.status_code == 503 and r.json()["queue_full"] and "analisis video sedang tidak aktif" in r.json()["detail"]
    assert r.headers["retry-after"] == "60"
    with sample_video.open("rb") as fh:
        r = client.post("/api/projects", files={"file": ("p0.mp4", fh, "video/mp4")})
    assert r.status_code == 503 and "analisis video" in r.json()["detail"]
    pid = _ready_project()["id"]
    r = client.post(f"/api/projects/{pid}/render", json=RENDER)
    assert r.status_code == 503 and "render video sedang tidak aktif" in r.json()["detail"]
    assert client.post(f"/api/projects/{pid}/reanalyze").status_code == 503
    assert not any(j["status"] in jobs.ACTIVE for j in jobs.for_project(pid))        # tidak ada job yang diterima
    storage.delete_project(pid)


def test_url_import_needs_live_io_worker(client, fleet, monkeypatch):
    from app import netpolicy
    monkeypatch.setattr(netpolicy, "check_import_url", lambda url, trusted=False: None)
    r = client.post("/api/projects/from-url", json={"url": "https://example.com/v"})
    assert r.status_code == 503 and r.json()["queue_full"]                 # tanpa pengunduh: ditolak
    fleet.up("io", ["io"])                                                 # pengunduh hidup, analisis tidak
    r = client.post("/api/projects/from-url", json={"url": "https://example.com/v"})
    assert r.status_code == 200, r.text                                   # diterima; analisis menunggu worker
    storage.delete_project(r.json()["project"]["id"])


def test_worker_back_alive_accepts_again(client, fleet):
    assert _tus_create(client).status_code == 503
    fleet.up("analisis", ["analisis", "render"])
    r = _tus_create(client)
    assert r.status_code == 201, r.text
    client.delete(r.headers["location"], headers=H)


def test_expired_heartbeat_or_stopped_worker_counts_as_not_available(client, fleet):
    fleet.up("analisis", ["analisis", "render"])
    r = _tus_create(client)
    assert r.status_code == 201
    client.delete(r.headers["location"], headers=H)
    fleet.expire("analisis")                                    # pernah terdaftar, tetapi heartbeat kedaluwarsa
    assert monitor.queue_state("analisis") in ("tanpa_worker", "mampu_offline")
    assert _tus_create(client).status_code == 503
    fleet.up("analisis", ["analisis", "render"])
    fleet.stop("analisis")                                      # heartbeat baru, tetapi worker berhenti
    assert _tus_create(client).status_code == 503


def test_busy_live_worker_still_accepts(client, fleet):
    """Worker hidup yang sedang menunggu RAM/CPU ("sementara") tetap menerima: job akan mulai sendiri."""
    fleet.up("analisis", ["analisis", "render"])
    monitor.report(PREFIX + "analisis", "menunggu", reason="RAM")
    assert monitor.queue_state("analisis") == "sementara"
    r = _tus_create(client)
    assert r.status_code == 201
    client.delete(r.headers["location"], headers=H)


def test_queue_full_still_rejected_when_workers_alive(client, fleet, monkeypatch):
    fleet.up("analisis", ["analisis", "render"])
    monkeypatch.setattr(jobs, "MAX_QUEUE_DEPTH", -1)            # setiap kedalaman antrean dianggap penuh
    r = _tus_create(client)
    assert r.status_code == 503 and "sangat sibuk" in r.json()["detail"]


def test_admission_can_be_disabled(client, fleet, monkeypatch):
    from app import config
    monkeypatch.setattr(config, "REQUIRE_LIVE_WORKER", False)
    r = _tus_create(client)
    assert r.status_code == 201
    client.delete(r.headers["location"], headers=H)


def test_upload_finalized_while_worker_down_keeps_job(client, sample_video, fake_transcript, fleet, monkeypatch):
    """Upload yang sudah diterima tidak ditolak di akhir walau worker mati di tengah upload: job disimpan & jelas."""
    monkeypatch.setattr(transcribe, "transcribe", lambda *a, **k: fake_transcript)
    monkeypatch.setattr(jobs, "claim", lambda *a, **k: None)   # worker bawaan tidak mengambil job
    fleet.up("analisis", ["analisis", "render"])
    data = sample_video.read_bytes()
    r = _tus_create(client, len(data))
    assert r.status_code == 201
    url = r.headers["location"]
    fleet.expire("analisis")                                    # worker mati di tengah upload
    r = client.patch(url, content=data, headers={**H, "Upload-Offset": "0",
                                                 "Content-Type": "application/offset+octet-stream"})
    assert r.status_code == 204, r.text
    status = client.get(url).json()
    pid = status["project_id"]
    job = [j for j in jobs.for_project(pid) if j["task"] == "analyze"][0]
    view = client.get(f"/api/jobs/{job['id']}").json()
    assert view["status"] == "antre" and view["wait"]["reason"] == "worker_tidak_aktif"
    assert "tetap tersimpan" in view["message"] and "0%" not in view["message"]
    jobs.request_cancel(job["id"])
    client.delete(f"/api/projects/{pid}")


# ------------------------------------------------------------------ status job yang jelas

def test_queued_job_message_follows_worker_state(client, fleet):
    pid = _ready_project("p0-antre")["id"]
    job = jobs.submit("render", "render", pid, {}, "Render uji", queue="uji-p0-q")
    v = jobs.view(jobs.get(job["id"]))
    assert v["wait"]["reason"] == "worker_tidak_aktif" and "tidak aktif" in v["message"]
    fleet.up("q", ["uji-p0-q"])                                  # worker antrean ini hidup kembali
    v = jobs.view(jobs.get(job["id"]))
    assert v["wait"]["reason"] == "antrean" and v["message"].startswith("Menunggu giliran")
    monitor.report(PREFIX + "q", "menunggu", reason="RAM")
    v = jobs.view(jobs.get(job["id"]))
    assert v["wait"]["reason"] == "server_sibuk" and "memori/CPU" in v["message"]
    with db.engine().begin() as c:                               # sudah lama menunggu: lamanya ditampilkan
        c.execute(update(db.jobs).where(db.jobs.c.id == job["id"]).values(created=time.time() - 3 * 60))
    v = jobs.view(jobs.get(job["id"]))
    assert "(menunggu 3 menit)" in v["message"] and v["wait"]["seconds"] >= 180
    jobs.request_cancel(job["id"])
    storage.delete_project(pid)


def test_running_job_of_dead_worker_is_reported_and_recovered(client, fleet):
    pid = _ready_project("p0-hilang")["id"]
    job = jobs.submit("render", "render", pid, {}, "Render uji", queue="uji-p0-q")
    stale = time.time() - jobs.STALE_AFTER - 10
    with db.engine().begin() as c:
        c.execute(update(db.jobs).where(db.jobs.c.id == job["id"]).values(
            status="jalan", worker=PREFIX + "mati", started=stale, heartbeat=stale, progress=40))
    v = client.get(f"/api/jobs/{job['id']}").json()
    assert v["status"] == "jalan" and v["wait"]["reason"] == "worker_hilang" and "diulang otomatis" in v["message"]
    assert jobs.requeue_stale() >= 1                             # dipulihkan begitu ada worker yang memeriksa
    v = jobs.view(jobs.get(job["id"]))
    assert v["status"] == "antre"
    jobs.request_cancel(job["id"])
    storage.delete_project(pid)


def test_unknown_queue_without_any_registered_worker_is_not_ready(fleet):
    assert monitor.queue_state("antrean-yang-tidak-pernah-ada") == "tanpa_worker"


def test_downloaded_url_waiting_for_offline_analysis_says_so(client, fleet):
    """P0.1: unduhan dari link selesai, worker analisis mati -> status menyebut keduanya dengan jujur."""
    pid = storage.create_project("p01-link")["id"]
    job = jobs.submit("analyze", "analisis", pid, {"language": None, "after_download": True}, "Analisis video")
    view = jobs.view(jobs.get(job["id"]))
    assert view["wait"]["reason"] == "worker_tidak_aktif"
    assert view["message"].startswith("Video dari link sudah berhasil diunduh. Analisis menunggu: server analisis video")
    assert "%" not in view["message"]
    plain = jobs.view(jobs.get(jobs.submit("render", "render", pid, {}, "Render")["id"]))
    assert plain["message"].startswith("Server render video sedang tidak aktif")
    fleet.up("analisis", ["analisis", "render"])                  # worker kembali: pesan tidak lagi "tidak aktif"
    # Worker yang kembali bisa langsung mengambil job (wait = None): itu juga berarti tidak lagi "tidak aktif".
    after = jobs.view(jobs.get(job["id"]))
    assert (after.get("wait") or {}).get("reason") != "worker_tidak_aktif"
    storage.delete_project(pid)
