"""P2 final hardening: reservasi kuota atomik & lease file aktif (upload, unduhan, job, crash)."""
import os
import threading
import time
import uuid

import anyio
import pytest
from conftest import login_client
from sqlalchemy import select, update

from app import db, jobs, lifecycle, main, storage, transcribe, worker
from app.config import PROJECTS_DIR, UPLOAD_TMP_DIR

H = 3600.0
MB = 1024 * 1024


def _set_media(pid, **values):
    with db.engine().begin() as c:
        c.execute(update(db.project_media).where(db.project_media.c.project_id == pid).values(**values))


def _expire(pid, hours=30):
    _set_media(pid, uploaded_at=time.time() - hours * H, last_activity=time.time() - hours * H)


def _state(pid):
    return lifecycle.info(pid)["state"]


def _leases(unit_id=None, pid=None):
    ml = db.media_leases.c
    q = select(db.media_leases)
    if unit_id:
        q = q.where(ml.unit_id == unit_id)
    if pid:
        q = q.where(ml.project_id == pid)
    with db.engine().connect() as c:
        return [db.row(r) for r in c.execute(q)]


def _project(unit_id=None, render=True):
    pid = storage.create_project("uji-hardening", unit_id)["id"]
    folder = PROJECTS_DIR / pid
    (folder / "source.mp4").write_bytes(b"v" * 4000)
    if render:
        (folder / "renders" / "klip.mp4").write_bytes(os.urandom(300_000))
        storage.add_render(pid, {"id": uuid.uuid4().hex[:12], "file": "klip.mp4", "title": "Klip", "start": 0,
                                 "end": 4, "aspect": "9:16", "size": 300_000, "created": time.time()})
    storage.update_project(pid, status="siap", source="source.mp4", duration=8)
    lifecycle.refresh_bytes(pid)
    return pid


@pytest.fixture
def unit(client):
    u = client.post("/api/admin/units", json={"name": f"Kuota {uuid.uuid4().hex[:8]}"}).json()
    return lifecycle.unit_row(u["id"])


def _set_quota(unit_id, mb):
    with db.engine().begin() as c:
        c.execute(update(db.units).where(db.units.c.id == unit_id).values(storage_quota_mb=mb))
    return lifecycle.unit_row(unit_id)


# ------------------------------------------------------------------ 1. kuota atomik

def test_parallel_reservations_never_exceed_quota(unit):
    """20 thread memesan 1 MB sekaligus pada kuota 10 MB: tepat 10 yang berhasil (SQLite & PostgreSQL)."""
    u = _set_quota(unit["id"], 10)
    pids = [_project(unit["id"], render=False) for _ in range(20)]
    for pid in pids:
        _set_media(pid, bytes=0)
    ok, full = [], []
    barrier = threading.Barrier(20)

    def grab(pid):
        barrier.wait()
        try:
            ok.append(lifecycle.acquire_lease(pid, "upload", nbytes=MB, unit=u))
        except lifecycle.StorageFull:
            full.append(pid)

    threads = [threading.Thread(target=grab, args=(p,)) for p in pids]
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)
    assert len(ok) == 10 and len(full) == 10
    assert lifecycle.unit_usage(unit["id"]) == 10 * MB
    for lid in ok:
        lifecycle.release_lease(lid, collect=False)
    assert lifecycle.unit_usage(unit["id"]) == 0


def test_concurrent_uploads_same_workspace(client, unit, sample_video, fake_transcript, monkeypatch):
    """Enam upload bersamaan ke unit yang sisa kuotanya cukup untuk 2 file: tepat 2 yang diterima."""
    monkeypatch.setattr(transcribe, "transcribe", lambda *a, **k: fake_transcript)
    size = sample_video.stat().st_size
    u = _set_quota(unit["id"], 50)
    filler = _project(unit["id"], render=False)
    _set_media(filler, bytes=50 * MB - int(2.5 * size))  # sisa kuota = 2,5 file
    email = f"paralel{uuid.uuid4().hex[:8]}@org.id"
    client.post("/api/admin/users", json={"email": email, "password": "password-123", "role": "editor",
                                          "unit_id": u["id"]})
    clients = [login_client(email, "password-123") for _ in range(6)]
    barrier = threading.Barrier(6)
    codes = []

    def upload(c):
        barrier.wait()
        with sample_video.open("rb") as fh:
            codes.append(c.post("/api/projects", files={"file": ("p.mp4", fh, "video/mp4")}).status_code)

    threads = [threading.Thread(target=upload, args=(c,)) for c in clients]
    for t in threads:
        t.start()
    for t in threads:
        t.join(120)
    assert sorted(codes) == [200, 200, 507, 507, 507, 507], codes
    assert _leases(unit_id=u["id"]) == []  # semua reservasi sudah diganti ukuran aktual / dilepas
    projects = [p for p in client.get(f"/api/projects?unit={u['id']}").json() if p["id"] != filler]
    assert len(projects) == 2  # upload yang ditolak tidak meninggalkan proyek


def test_failed_upload_releases_reservation(client, unit, sample_video, monkeypatch):
    u = _set_quota(unit["id"], 50)
    email = f"gagal{uuid.uuid4().hex[:8]}@org.id"
    client.post("/api/admin/users", json={"email": email, "password": "password-123", "role": "editor",
                                          "unit_id": u["id"]})
    c = login_client(email, "password-123")
    seen = {}

    def broken(upload, dst, request, lease=None, reserved=None):
        seen["leases"] = _leases(unit_id=u["id"])
        raise OSError(5, "Input/output error")  # mis. koneksi/disk bermasalah di tengah upload

    monkeypatch.setattr(main, "_save_upload", broken)
    folders = set(os.listdir(PROJECTS_DIR))
    with sample_video.open("rb") as fh, pytest.raises(OSError):
        c.post("/api/projects", files={"file": ("x.mp4", fh, "video/mp4")})
    assert len(seen["leases"]) == 1 and seen["leases"][0]["bytes"] == sample_video.stat().st_size
    assert _leases(unit_id=u["id"]) == [] and lifecycle.unit_usage(u["id"]) == 0
    assert set(os.listdir(PROJECTS_DIR)) == folders


def test_crashed_upload_reservation_recovers(unit):
    """Proses web mati saat upload: lease tidak pernah dilepas, tetapi kedaluwarsa sendiri."""
    u = _set_quota(unit["id"], 5)
    pid = _project(unit["id"], render=False)
    _set_media(pid, bytes=0)
    lifecycle.acquire_lease(pid, "upload", nbytes=4 * MB, unit=u)
    with pytest.raises(lifecycle.StorageFull):
        lifecycle.acquire_lease(pid, "upload", nbytes=2 * MB, unit=u)
    lifecycle.sweep(now=time.time() + lifecycle.LEASE_TTL["upload"] + 5)
    assert _leases(pid=pid) == []
    lifecycle.release_lease(lifecycle.acquire_lease(pid, "upload", nbytes=2 * MB, unit=u), collect=False)


def test_quota_from_url_enforced_on_actual_size(unit):
    u = _set_quota(unit["id"], 1)
    pid = _project(unit["id"], render=False)
    (PROJECTS_DIR / pid / "source.webm").write_bytes(b"x" * (2 * MB))  # hasil unduhan ternyata 2 MB
    with pytest.raises(lifecycle.StorageFull):
        lifecycle.commit_size(pid, u)
    (PROJECTS_DIR / pid / "source.webm").unlink()
    assert lifecycle.commit_size(pid, u) < MB


# ------------------------------------------------------------------ 2. lease file aktif

def test_upload_lease_protects_then_immediate_cleanup():
    pid = _project()
    lid = lifecycle.acquire_lease(pid, "upload")
    _expire(pid)  # masa simpan habis saat upload masih berjalan
    lifecycle.sweep()
    assert (PROJECTS_DIR / pid / "source.mp4").exists() and _state(pid) == "kedaluwarsa"
    lifecycle.release_lease(lid)  # upload selesai -> langsung dibersihkan, tanpa menunggu sweep
    assert _state(pid) == "dihapus" and not (PROJECTS_DIR / pid).exists()


def _run_response(response, on_body):
    scope = {"type": "http", "method": "GET", "path": "/", "headers": [], "query_string": b""}

    async def receive():
        await anyio.sleep(3600)
        return {"type": "http.disconnect"}

    async def send(message):
        if message["type"] == "http.response.body" and message.get("body"):
            await anyio.to_thread.run_sync(on_body)

    anyio.run(response, scope, receive, send)


def test_download_lease_protects_during_stream_and_releases():
    pid = _project()
    path = PROJECTS_DIR / pid / "renders" / "klip.mp4"
    lid = lifecycle.acquire_lease(pid, "download")
    seen = []

    def during_download():
        if not seen:
            _expire(pid, hours=80)  # lewat batas 72 jam di tengah unduhan
            lifecycle.sweep()
            seen.append((len(_leases(pid=pid)), path.exists(), _state(pid)))

    _run_response(main.LeasedFileResponse(path, lid, media_type="video/mp4"), during_download)
    assert seen == [(1, True, "kedaluwarsa")]
    assert _leases(pid=pid) == [] and _state(pid) == "dihapus"  # dibersihkan segera setelah unduhan selesai


def test_interrupted_download_releases_lease():
    pid = _project()
    lid = lifecycle.acquire_lease(pid, "download")

    def disconnect():
        raise OSError("koneksi terputus")

    with pytest.raises(OSError):
        _run_response(main.LeasedFileResponse(PROJECTS_DIR / pid / "renders" / "klip.mp4", lid), disconnect)
    assert _leases(pid=pid) == []
    assert (PROJECTS_DIR / pid / "renders" / "klip.mp4").exists()  # media belum kedaluwarsa: tetap ada


def test_download_endpoint_holds_and_releases_lease(client):
    pid = _project()
    r = client.get(f"/api/projects/{pid}/renders/klip.mp4?download=true", headers={"Range": "bytes=10-"})
    assert r.status_code == 206 and len(r.content) == 300_000 - 10
    assert _leases(pid=pid) == []


def test_queued_job_runs_after_expiry_then_media_collected(monkeypatch):
    """Job yang masih menunggu saat masa simpan habis tetap dijalankan; media dihapus segera setelah
    job terakhir selesai (bukan menunggu putaran pembersihan)."""
    pid = _project()
    first = jobs.submit("render", "render", pid, {}, "a", queue="uji-collect")
    second = jobs.submit("render", "render", pid, {}, "b", queue="uji-collect")
    _expire(pid, hours=80)
    lifecycle.sweep()
    assert _state(pid) == "kedaluwarsa" and (PROJECTS_DIR / pid / "source.mp4").exists()
    ran = []

    def fake_task(job):
        worker._media_ready(job.project_id)  # tidak menolak: media belum dihapus
        ran.append(job.id)
        return {}

    monkeypatch.setattr(worker, "_run_task", fake_task)
    assert worker.run_one(("uji-collect",), "w-collect")
    assert jobs.get(first["id"])["status"] == "selesai"
    assert (PROJECTS_DIR / pid / "source.mp4").exists()  # job kedua masih memegang lease
    assert worker.run_one(("uji-collect",), "w-collect")
    assert ran == [first["id"], second["id"]]
    assert _state(pid) == "dihapus" and not (PROJECTS_DIR / pid).exists()
    with pytest.raises(lifecycle.MediaGone):
        lifecycle.require_present(pid)


def test_recovering_worker_job_keeps_media():
    pid = _project()
    job = jobs.submit("render", "render", pid, {}, "x", queue="uji-pulih")
    jobs.claim(("uji-pulih",), "w-mati")
    with db.engine().begin() as c:  # worker mati: heartbeat basi, belum dipulihkan
        c.execute(update(db.jobs).where(db.jobs.c.id == job["id"]).values(heartbeat=time.time() - 9999))
    _expire(pid, hours=80)
    lifecycle.sweep()
    assert (PROJECTS_DIR / pid / "source.mp4").exists()
    jobs.requeue_stale()  # dipulihkan ke antrean: tetap dilindungi
    lifecycle.sweep()
    assert (PROJECTS_DIR / pid / "source.mp4").exists()
    jobs.finish(job["id"], cancelled=True)
    lifecycle.sweep()
    assert _state(pid) == "dihapus"


def test_open_files_are_never_deleted_by_age():
    pid = _project()
    part = PROJECTS_DIR / pid / "source.f137.mp4.part"
    tmp = UPLOAD_TMP_DIR / f"tmpaktif{uuid.uuid4().hex[:6]}"
    UPLOAD_TMP_DIR.mkdir(parents=True, exist_ok=True)
    with part.open("wb") as fh_part, tmp.open("wb") as fh_tmp:  # masih ditulis proses (di sini: tes ini)
        fh_part.write(b"x")
        fh_tmp.write(b"x")
        fh_part.flush()
        fh_tmp.flush()
        old = time.time() - 48 * H
        os.utime(part, (old, old))
        os.utime(tmp, (old, old))
        stats = lifecycle.clean_orphans()
        assert part.exists() and tmp.exists() and stats["skipped_in_use"] >= 2
    lifecycle.clean_orphans()  # setelah ditutup: sisa yang tertinggal boleh dibersihkan
    assert not part.exists() and not tmp.exists()


def test_cleanup_rechecks_ownership_under_lock(monkeypatch):
    """Upload dimulai di antara pengambilan daftar dan penghapusan: file tidak boleh dihapus."""
    pid = _project()
    part = PROJECTS_DIR / pid / "source.webm.part"
    part.write_bytes(b"x")
    old = time.time() - 2 * H
    os.utime(part, (old, old))
    real = lifecycle._orphan_candidates

    def candidates_then_upload_starts(*args):
        out = real(*args)
        lifecycle.acquire_lease(pid, "upload")
        return out

    monkeypatch.setattr(lifecycle, "_orphan_candidates", candidates_then_upload_starts)
    lifecycle.clean_orphans()
    assert part.exists()


# ------------------------------------------------------------------ riwayat render

def test_render_history_not_offered_for_download(client):
    pid = _project()
    (PROJECTS_DIR / pid / "renders" / "hilang.mp4").write_bytes(b"x")
    storage.add_render(pid, {"id": uuid.uuid4().hex[:12], "file": "hilang.mp4", "title": "Hilang", "start": 0,
                             "end": 1, "aspect": "9:16", "size": 1, "created": time.time()})
    (PROJECTS_DIR / pid / "renders" / "hilang.mp4").unlink()
    renders = {r["file"]: r for r in client.get(f"/api/projects/{pid}").json()["renders"]}
    assert renders["klip.mp4"]["available"] is True and renders["hilang.mp4"]["available"] is False
    assert lifecycle.delete_media(pid, "uji")
    view = client.get(f"/api/projects/{pid}").json()
    assert len(view["renders"]) == 2 and not any(r["available"] for r in view["renders"])  # riwayat tetap ada
    assert client.get(f"/api/projects/{pid}/renders/klip.mp4?download=true").status_code == 410
