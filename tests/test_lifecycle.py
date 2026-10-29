"""P2: siklus media sementara (retensi, lease job aktif, pembersihan aman, disk & kuota, lintas workspace)."""
import os
import shutil
import tempfile
import time
import uuid
from pathlib import Path

import pytest
from conftest import login_client
from sqlalchemy import delete, select, update

from app import db, jobs, lifecycle, main, storage, transcribe
from app.config import DATA_DIR, PROJECTS_DIR, UPLOAD_TMP_DIR

H = 3600.0
RENDER_BODY = {"start": 0, "end": 4, "subtitles": {"enabled": False}, "animations": False, "reframe": "manual"}


# ------------------------------------------------------------------ helper

def _media_row(pid):
    with db.engine().connect() as c:
        return db.row(c.execute(select(db.project_media).where(db.project_media.c.project_id == pid)).first())


def _set_media(pid, **values):
    with db.engine().begin() as c:
        c.execute(update(db.project_media).where(db.project_media.c.project_id == pid).values(**values))


def _age(pid, uploaded_ago, active_ago=None):
    """Mundurkan waktu upload/aktivitas (relatif terhadap sekarang)."""
    now = time.time()
    _set_media(pid, uploaded_at=now - uploaded_ago * H,
               last_activity=now - (uploaded_ago if active_ago is None else active_ago) * H)


def _make_old(path: Path, hours=2):
    t = time.time() - hours * H
    for root, dirs, files in os.walk(path) if path.is_dir() else []:
        for name in dirs + files:
            os.utime(os.path.join(root, name), (t, t))
    os.utime(path, (t, t))


def _managed_project(name="uji-p2", unit_id=None):
    """Proyek 'siap' dengan sumber, preview, dan satu hasil render (tanpa analisis sungguhan)."""
    pid = storage.create_project(name, unit_id)["id"]
    folder = PROJECTS_DIR / pid
    (folder / "source.mp4").write_bytes(b"v" * 4000)
    (folder / "preview.mp4").write_bytes(b"p" * 2000)
    (folder / "renders" / "klip.mp4").write_bytes(b"r" * 3000)
    storage.write_data(pid, "transcript", {"language": "id", "segments": []})
    storage.add_render(pid, {"id": uuid.uuid4().hex[:12], "file": "klip.mp4", "title": "Klip uji", "start": 0,
                             "end": 4, "aspect": "9:16", "size": 3000, "created": time.time()})
    storage.update_project(pid, status="siap", source="source.mp4", duration=8)
    lifecycle.refresh_bytes(pid)
    return pid


def _wait_status(c, job_id, statuses, timeout=90):
    deadline = time.time() + timeout
    job = None
    while time.time() < deadline:
        job = c.get(f"/api/jobs/{job_id}").json()
        if job["status"] in statuses:
            return job
        time.sleep(0.2)
    raise AssertionError(f"status {statuses} tidak tercapai, terakhir: {job}")


@pytest.fixture
def ready_project(client, sample_video, fake_transcript, monkeypatch):
    """Proyek hasil upload + analisis sungguhan lewat API (transkripsi dipalsukan)."""
    monkeypatch.setattr(transcribe, "transcribe", lambda *a, **k: fake_transcript)
    with sample_video.open("rb") as fh:
        res = client.post("/api/projects", files={"file": ("p2.mp4", fh, "video/mp4")}).json()
    _wait_status(client, res["job"]["id"], ("selesai",))
    return res["project"]["id"]


# ------------------------------------------------------------------ retensi

def test_new_project_registered_with_24h_retention():
    before = time.time()
    pid = storage.create_project("baru-p2")["id"]
    m = lifecycle.info(pid)
    assert m["state"] == "tersedia" and not m["warning"]
    assert before + 24 * H - 5 <= m["expires_at"] <= time.time() + 24 * H
    assert before + 72 * H - 5 <= m["hard_expires_at"] <= time.time() + 72 * H


def test_retention_24h_after_last_activity():
    pid = _managed_project()
    t0 = _media_row(pid)["last_activity"]
    lifecycle.sweep(now=t0 + 23.9 * H)
    assert (PROJECTS_DIR / pid / "source.mp4").exists()
    assert lifecycle.describe(_media_row(pid), now=t0 + 23 * H)["warning"]  # < 3 jam lagi: peringatan
    lifecycle.sweep(now=t0 + 24 * H + 10)
    assert not (PROJECTS_DIR / pid).exists()
    row = _media_row(pid)
    assert row["state"] == "dihapus" and row["delete_reason"] == "kedaluwarsa" and row["bytes"] == 0
    # Metadata tetap: proyek & daftar render masih ada di database.
    meta = storage.load_project(pid)
    assert meta["status"] == "siap" and [r["file"] for r in meta["renders"]] == ["klip.mp4"]


def test_activity_extends_but_never_beyond_72h():
    pid = _managed_project()
    t0 = _media_row(pid)["uploaded_at"]
    for hours in (20, 40, 60, 70):  # pengguna aktif terus
        assert lifecycle.touch(pid, now=t0 + hours * H)
    assert lifecycle.expires_at(_media_row(pid)) == pytest.approx(t0 + 72 * H)
    lifecycle.sweep(now=t0 + 71.9 * H)
    assert (PROJECTS_DIR / pid / "source.mp4").exists()
    lifecycle.sweep(now=t0 + 72 * H + 10)
    assert not (PROJECTS_DIR / pid).exists()
    with pytest.raises(lifecycle.MediaGone):
        lifecycle.touch(pid, now=t0 + 72 * H + 20)


def test_touch_is_rate_limited():
    pid = _managed_project()
    t0 = _media_row(pid)["last_activity"]
    assert lifecycle.touch(pid, now=t0 + 120)
    assert not lifecycle.touch(pid, now=t0 + 150)  # < 60 detik sejak tulisan terakhir: tidak menulis DB
    assert _media_row(pid)["last_activity"] == pytest.approx(t0 + 120)


def test_polling_does_not_extend_retention(client, ready_project):
    pid = ready_project
    _age(pid, uploaded_ago=22, active_ago=22)
    before = _media_row(pid)["last_activity"]
    job_id = storage.load_project(pid) and jobs.for_project(pid)[0]["id"]
    for url in (f"/api/projects/{pid}", f"/api/jobs/{job_id}", f"/api/projects/{pid}/preview",
                f"/api/projects/{pid}/transcript", f"/api/projects/{pid}/clips", f"/api/projects/{pid}/plan",
                "/api/projects"):
        assert client.get(url).status_code == 200, url
    assert _media_row(pid)["last_activity"] == before
    view = client.get(f"/api/projects/{pid}").json()["media"]
    assert view["state"] == "tersedia" and view["warning"] and view["remaining"] < 2.1 * H
    # Aksi pengguna (cari klip) memperpanjang masa simpan.
    assert client.post(f"/api/projects/{pid}/clips/suggest", json={"target": 30, "top_n": 3}).status_code == 200
    assert _media_row(pid)["last_activity"] > time.time() - 30
    assert not client.get(f"/api/projects/{pid}").json()["media"]["warning"]


def test_user_action_capped_by_72h_via_api(client, ready_project):
    pid = ready_project
    _age(pid, uploaded_ago=71.5, active_ago=1)
    client.post(f"/api/projects/{pid}/clips/suggest", json={"target": 30, "top_n": 3})
    m = client.get(f"/api/projects/{pid}").json()["media"]
    assert m["remaining"] <= 0.5 * H + 5  # tetap berakhir 72 jam sejak upload


# ------------------------------------------------------------------ job aktif = lease

def test_active_job_media_never_deleted():
    pid = _managed_project()
    t_far = time.time() + 100 * H  # jauh melewati 24 jam dan 72 jam
    job = jobs.submit("render", "render", pid, {"request": RENDER_BODY}, "lease", queue="uji-p2")
    for status_step in ("antre", "jalan", "berhenti"):
        if status_step == "jalan":
            assert jobs.claim(("uji-p2",), "w-p2")["id"] == job["id"]
        if status_step == "berhenti":
            assert jobs.request_cancel(job["id"]) == "berhenti"
        assert jobs.get(job["id"])["status"] == status_step
        lifecycle.sweep(now=t_far)
        assert (PROJECTS_DIR / pid / "source.mp4").exists(), status_step
        assert (PROJECTS_DIR / pid / "renders" / "klip.mp4").exists(), status_step
        assert _media_row(pid)["state"] == "tersedia"
        assert not lifecycle.delete_media(pid, "uji", now=t_far)  # hapus langsung pun ditolak
    jobs.finish(job["id"], cancelled=True)
    lifecycle.sweep(now=t_far)
    assert not (PROJECTS_DIR / pid).exists()
    assert _media_row(pid)["state"] == "dihapus"


def test_reserve_rejects_while_deleting(client, ready_project):
    pid = ready_project
    _set_media(pid, state="menghapus", state_changed=time.time())
    with pytest.raises(lifecycle.MediaGone):
        with lifecycle.reserve(pid):
            pass
    r = client.post(f"/api/projects/{pid}/render", json=RENDER_BODY)
    assert r.status_code == 410 and r.json()["media_gone"]
    assert not any(j["kind"] == "render" for j in jobs.for_project(pid))  # tidak ada job yang lolos
    _set_media(pid, state="tersedia")


def test_job_inserted_during_delete_aborts_deletion():
    """Fase 2 penghapusan memeriksa ulang lease SESUDAH penandaan `menghapus`."""
    pid = _managed_project()
    now = time.time()
    _set_media(pid, state="menghapus", state_changed=now)               # fase 1 sudah terjadi
    job = jobs.submit("render", "render", pid, {}, "telat", queue="uji-p2b")  # job masuk tepat sebelum fase 1
    assert not lifecycle._finish_delete(pid, "kedaluwarsa", now)
    assert _media_row(pid)["state"] == "tersedia"
    assert (PROJECTS_DIR / pid / "source.mp4").exists()
    jobs.finish(job["id"], cancelled=True)


# ------------------------------------------------------------------ worker crash

def test_cleanup_after_worker_crash():
    # (a) Worker mati di tengah penghapusan: status `menghapus` tertinggal -> dilanjutkan sweep berikutnya.
    pid = _managed_project()
    _set_media(pid, state="menghapus", state_changed=time.time() - 3600)
    res = lifecycle.sweep()
    assert res["resumed"] >= 1 and not (PROJECTS_DIR / pid).exists()
    assert _media_row(pid)["state"] == "dihapus"

    # (b) Worker mati saat render: job dipulihkan ke antrean dan tetap melindungi media.
    pid2 = _managed_project()
    job = jobs.submit("render", "render", pid2, {"request": RENDER_BODY}, "crash", queue="uji-crash")
    jobs.claim(("uji-crash",), "w-mati")
    with db.engine().begin() as c:
        c.execute(update(db.jobs).where(db.jobs.c.id == job["id"]).values(heartbeat=time.time() - 9999))
    jobs.requeue_stale()
    assert jobs.get(job["id"])["status"] == "antre"
    renders = PROJECTS_DIR / pid2 / "renders"
    (renders / "tmp_abc").mkdir()
    (renders / "tmp_abc" / "frame.png").write_bytes(b"x")
    (renders / "sub_x.ass").write_text("[Script Info]")
    (renders / "setengah.mp4").write_bytes(b"x" * 100)
    for p in (renders / "tmp_abc", renders / "sub_x.ass", renders / "setengah.mp4"):
        _make_old(p)
    lifecycle.sweep(now=time.time() + 100 * H)
    assert (renders / "tmp_abc").exists() and (renders / "setengah.mp4").exists()  # job aktif: tidak disentuh

    # (c) Sesudah job selesai/batal: sisa render terputus dibersihkan, render tercatat tetap ada.
    jobs.finish(job["id"], cancelled=True)
    stats = lifecycle.clean_orphans()
    assert stats["files"] >= 3
    assert not (renders / "tmp_abc").exists() and not (renders / "sub_x.ass").exists()
    assert not (renders / "setengah.mp4").exists()
    assert (renders / "klip.mp4").exists()


# ------------------------------------------------------------------ file sisa / orphan

def test_orphan_folders_and_temp_files():
    orphan = PROJECTS_DIR / uuid.uuid4().hex[:12]
    (orphan / "renders").mkdir(parents=True)
    (orphan / "source.mp4").write_bytes(b"x" * 500)
    _make_old(orphan)
    legacy = PROJECTS_DIR / uuid.uuid4().hex[:12]
    legacy.mkdir()
    (legacy / "project.json").write_text("{}")
    (legacy / "source.mp4").write_bytes(b"x")
    _make_old(legacy)
    fresh = PROJECTS_DIR / uuid.uuid4().hex[:12]
    fresh.mkdir()  # bisa jadi proyek yang sedang dibuat: masa tenggang
    UPLOAD_TMP_DIR.mkdir(parents=True, exist_ok=True)
    stale_tmp = UPLOAD_TMP_DIR / f"tmpupload{uuid.uuid4().hex[:6]}"
    stale_tmp.write_bytes(b"x" * 100)
    _make_old(stale_tmp, hours=25)
    young_tmp = UPLOAD_TMP_DIR / f"tmpupload{uuid.uuid4().hex[:6]}"
    young_tmp.write_bytes(b"x")
    _make_old(young_tmp, hours=7)  # bisa milik job panjang yang masih berjalan

    lifecycle.clean_orphans()
    assert not orphan.exists()
    assert legacy.exists() and (legacy / "source.mp4").exists()
    assert fresh.exists()
    assert not stale_tmp.exists() and young_tmp.exists()
    shutil.rmtree(legacy)
    shutil.rmtree(fresh)
    young_tmp.unlink()


def test_failed_download_and_partial_files_cleaned():
    pid = _managed_project()
    folder = PROJECTS_DIR / pid
    junk = [folder / "source.f137.mp4.part", folder / "source.webm", folder / "source.mp4.ytdl",
            folder / ".preview.part.mp4", folder / "plan.json.ab12cd.tmp"]
    for p in junk:
        p.write_bytes(b"x" * 10)
        _make_old(p)
    young = folder / "source.f140.m4a.part"
    young.write_bytes(b"x")
    lifecycle.clean_orphans()
    assert not any(p.exists() for p in junk)
    assert young.exists()  # mungkin masih ditulis
    assert (folder / "source.mp4").exists() and (folder / "preview.mp4").exists()


def test_incomplete_upload_removed_but_legacy_kept():
    pid = storage.create_project("putus")["id"]  # status `baru`: upload tidak pernah selesai
    with db.engine().begin() as c:
        c.execute(update(db.projects).where(db.projects.c.id == pid).values(created=time.time() - 3 * H))
    legacy = storage.create_project("lama-baru")["id"]
    with db.engine().begin() as c:
        c.execute(update(db.projects).where(db.projects.c.id == legacy).values(created=time.time() - 3 * H))
        c.execute(delete(db.project_media).where(db.project_media.c.project_id == legacy))
    lifecycle.sweep()
    with pytest.raises(storage.NotFound):
        storage.load_project(pid)
    assert not (PROJECTS_DIR / pid).exists()
    assert storage.load_project(legacy)["status"] == "baru" and (PROJECTS_DIR / legacy).exists()
    storage.delete_project(legacy)


# ------------------------------------------------------------------ media sudah dihapus

def test_api_after_media_deleted(client, ready_project):
    pid = ready_project
    assert lifecycle.delete_media(pid, "uji")
    view = client.get(f"/api/projects/{pid}")
    assert view.status_code == 200 and view.json()["media"]["state"] == "dihapus"
    listed = {p["id"]: p for p in client.get("/api/projects").json()}
    assert listed[pid]["media"]["state"] == "dihapus"
    for method, url, body in [("get", f"/api/projects/{pid}/preview", None),
                              ("get", f"/api/projects/{pid}/transcript", None),
                              ("get", f"/api/projects/{pid}/clips", None),
                              ("get", f"/api/projects/{pid}/plan", None),
                              ("post", f"/api/projects/{pid}/render", RENDER_BODY),
                              ("post", f"/api/projects/{pid}/reanalyze", None),
                              ("post", f"/api/projects/{pid}/clips/suggest", {"target": 30, "top_n": 3})]:
        r = getattr(client, method)(url, **({"json": body} if body is not None else {}))
        assert r.status_code == 410, (url, r.status_code, r.text)
    assert client.delete(f"/api/projects/{pid}").json()["ok"]  # proyek tetap bisa dihapus dari daftar
    assert client.get(f"/api/projects/{pid}").status_code == 404


# ------------------------------------------------------------------ download & Range

def test_interrupted_and_range_download_keeps_file(client, ready_project):
    pid = ready_project
    renders = PROJECTS_DIR / pid / "renders"
    data = os.urandom(300_000)
    (renders / "unduh.mp4").write_bytes(data)
    storage.add_render(pid, {"id": uuid.uuid4().hex[:12], "file": "unduh.mp4", "title": "Unduh", "start": 0,
                             "end": 4, "aspect": "9:16", "size": len(data), "created": time.time()})
    url = f"/api/projects/{pid}/renders/unduh.mp4?download=true"

    # Unduhan terputus di tengah jalan.
    with client.stream("GET", url) as r:
        assert r.status_code == 200
        next(r.iter_bytes())
    # Melanjutkan dengan Range.
    r = client.get(url, headers={"Range": "bytes=100000-"})
    assert r.status_code == 206 and r.content == data[100000:]
    assert r.headers["content-range"] == f"bytes 100000-{len(data) - 1}/{len(data)}"
    r = client.get(f"/api/projects/{pid}/renders/unduh.mp4", headers={"Range": "bytes=0-99"})
    assert r.status_code == 206 and r.content == data[:100]
    # Download tidak menghapus hasil render; bisa diunduh ulang utuh.
    assert (renders / "unduh.mp4").exists()
    assert client.get(url).content == data
    assert "attachment" in client.get(url).headers.get("content-disposition", "")
    # Render yang filenya hilang -> 410 dengan pesan jelas, bukan 500.
    (renders / "unduh.mp4").unlink()
    assert client.get(url).status_code == 410


# ------------------------------------------------------------------ disk hampir penuh

def _fake_disk(usable):
    def status():
        return {"total": 100 * 10**9, "used": 99 * 10**9, "free": usable + 10**9, "reserve": 10**9,
                "usable": usable}
    return status


def test_disk_almost_full_rejects_new_work(client, ready_project, sample_video, monkeypatch):
    before = {p["id"] for p in client.get("/api/projects").json()}
    folders = set(os.listdir(PROJECTS_DIR))
    monkeypatch.setattr(lifecycle, "disk_status", _fake_disk(0))
    with sample_video.open("rb") as fh:
        r = client.post("/api/projects", files={"file": ("penuh.mp4", fh, "video/mp4")})
    assert r.status_code == 507 and r.json()["storage_full"] and "penuh" in r.json()["detail"]
    r = client.post("/api/projects/from-url", json={"url": "https://contoh.local/v.mp4"})
    assert r.status_code == 507
    r = client.post(f"/api/projects/{ready_project}/render", json=RENDER_BODY)
    assert r.status_code == 507
    assert {p["id"] for p in client.get("/api/projects").json()} == before   # tidak ada proyek setengah jadi
    assert set(os.listdir(PROJECTS_DIR)) == folders
    # Sedikit ruang tersisa, tetapi lebih kecil dari file yang diunggah: ditolak sebelum isi diterima.
    monkeypatch.setattr(lifecycle, "disk_status", _fake_disk(1000))
    with sample_video.open("rb") as fh:
        assert client.post("/api/projects", files={"file": ("besar.mp4", fh, "video/mp4")}).status_code == 507
    # Membuka proyek yang ada tetap bisa.
    assert client.get(f"/api/projects/{ready_project}").status_code == 200


def test_upload_failure_leaves_no_partial_project(client, sample_video, monkeypatch):
    calls = {"n": 0}
    real = lifecycle.ensure_disk

    def flaky(incoming=0):
        calls["n"] += 1
        if calls["n"] > 1:  # lolos di middleware, gagal saat file mulai ditulis
            raise lifecycle.StorageFull("Ruang penyimpanan server sedang hampir penuh.")
        return real(incoming)

    folders = set(os.listdir(PROJECTS_DIR))
    monkeypatch.setattr(lifecycle, "ensure_disk", flaky)
    with sample_video.open("rb") as fh:
        r = client.post("/api/projects", files={"file": ("gagal.mp4", fh, "video/mp4")})
    assert r.status_code == 507
    assert set(os.listdir(PROJECTS_DIR)) == folders


def test_upload_temp_files_stay_inside_data_dir():
    assert Path(tempfile.gettempdir()).resolve() == UPLOAD_TMP_DIR.resolve()
    assert DATA_DIR.resolve() in UPLOAD_TMP_DIR.resolve().parents


# ------------------------------------------------------------------ lintas workspace & kuota

@pytest.fixture
def two_units(client):
    stamp = uuid.uuid4().hex[:8]
    a = client.post("/api/admin/units", json={"name": f"Unit A {stamp}"}).json()
    b = client.post("/api/admin/units", json={"name": f"Unit B {stamp}"}).json()
    users = {}
    for key, unit in (("a", a), ("b", b)):
        email = f"p2{key}{stamp}@org.id"
        r = client.post("/api/admin/users", json={"email": email, "password": "password-123", "role": "editor",
                                                  "unit_id": unit["id"]})
        assert r.status_code == 200, r.text
        users[key] = login_client(email, "password-123")
    return a, b, users


def test_cross_workspace_protection(client, two_units):
    a, b, users = two_units
    pid = _managed_project("milik-a", unit_id=a["id"])
    _age(pid, uploaded_ago=20, active_ago=20)
    before = _media_row(pid)["last_activity"]
    for method, url, body in [("get", f"/api/projects/{pid}", None),
                              ("get", f"/api/projects/{pid}/preview", None),
                              ("get", f"/api/projects/{pid}/renders/klip.mp4?download=true", None),
                              ("post", f"/api/projects/{pid}/clips/suggest", {"target": 30, "top_n": 3}),
                              ("post", f"/api/projects/{pid}/render", RENDER_BODY),
                              ("delete", f"/api/projects/{pid}", None)]:
        r = getattr(users["b"], method)(url, **({"json": body} if body is not None else {}))
        assert r.status_code == 404, (url, r.status_code)
    assert _media_row(pid)["last_activity"] == before   # unit lain tidak bisa memperpanjang masa simpan
    assert (PROJECTS_DIR / pid / "source.mp4").exists()  # ... maupun menghapus
    assert pid not in {p["id"] for p in users["b"].get("/api/projects").json()}
    assert users["a"].get(f"/api/projects/{pid}/renders/klip.mp4?download=true").status_code == 200


def test_storage_quota_per_unit(client, two_units, sample_video, fake_transcript, monkeypatch):
    monkeypatch.setattr(transcribe, "transcribe", lambda *a, **k: fake_transcript)
    a, b, users = two_units
    r = client.put(f"/api/admin/units/{a['id']}", json={"name": a["name"], "storage_quota_mb": 1})
    assert r.status_code == 200
    pid = _managed_project("isi-a", unit_id=a["id"])
    _set_media(pid, bytes=900 * 1024)  # 0,9 MB dari kuota 1 MB terpakai
    units = {u["id"]: u for u in client.get("/api/admin/units").json()}
    assert units[a["id"]]["storage_used"] == 900 * 1024 and units[a["id"]]["storage_quota"] == 1024 * 1024
    with sample_video.open("rb") as fh:
        r = users["a"].post("/api/projects", files={"file": ("a.mp4", fh, "video/mp4")})
    assert r.status_code == 507 and "Kuota penyimpanan" in r.json()["detail"]
    with sample_video.open("rb") as fh:  # unit lain tidak terpengaruh
        r = users["b"].post("/api/projects", files={"file": ("b.mp4", fh, "video/mp4")})
    assert r.status_code == 200, r.text
    _wait_status(users["b"], r.json()["job"]["id"], ("selesai",))
    # Media yang sudah dihapus tidak dihitung lagi ke kuota.
    assert lifecycle.delete_media(pid, "uji")
    assert lifecycle.unit_usage(a["id"]) == 0
    # Kosongkan kuota (null) = kembali ke bawaan.
    client.put(f"/api/admin/units/{a['id']}", json={"name": a["name"], "storage_quota_mb": None})
    assert {u["id"]: u for u in client.get("/api/admin/units").json()}[a["id"]]["storage_quota_mb"] is None


# ------------------------------------------------------------------ migrasi proyek lama

def test_legacy_migration_is_safe_and_reversible_by_default():
    pid = _managed_project("lama")
    with db.engine().begin() as c:
        c.execute(delete(db.project_media).where(db.project_media.c.project_id == pid))
    assert lifecycle.info(pid)["state"] == "lama"
    lifecycle.sweep(now=time.time() + 1000 * H)  # tanpa migrasi: tidak pernah dihapus otomatis
    assert (PROJECTS_DIR / pid / "source.mp4").exists()
    lifecycle.require_available(pid)  # proyek lama tetap bisa dipakai seperti biasa

    report = lifecycle.migrate_legacy()  # dry-run
    assert pid in {p["id"] for p in report["projects"]} and not report["applied"]
    assert _media_row(pid) is None

    now = time.time()
    report = lifecycle.migrate_legacy(apply=True, grace_hours=72, now=now)
    assert report["applied"] and _media_row(pid)["grace_until"] == pytest.approx(now + 72 * H)
    assert lifecycle.migrate_legacy()["count"] == 0  # aman dijalankan ulang
    lifecycle.sweep(now=now + 71 * H)  # masa tenggang: belum dihapus walau > 24 jam tanpa aktivitas
    assert (PROJECTS_DIR / pid / "source.mp4").exists()
    lifecycle.sweep(now=now + 72 * H + 10)
    assert not (PROJECTS_DIR / pid).exists()
    assert storage.load_project(pid)["name"] == "lama"


def test_manage_commands(capsys):
    from app import manage

    pid = _managed_project("cli")
    with db.engine().begin() as c:
        c.execute(delete(db.project_media).where(db.project_media.c.project_id == pid))
    manage.main(["media-migrate"])
    out = capsys.readouterr().out
    assert pid in out and _media_row(pid) is None
    manage.main(["media-migrate", "--apply", "--grace-hours", "1"])
    assert _media_row(pid)["grace_until"] > time.time()
    manage.main(["media-cleanup"])
    assert (PROJECTS_DIR / pid / "source.mp4").exists()


# ------------------------------------------------------------------ admin & konfigurasi

def test_admin_stats_and_me_expose_storage(client):
    st = client.get("/api/admin/stats").json()["storage"]
    assert {"media_bytes", "projects_by_state", "retention_hours", "max_hours"} <= set(st)
    assert st["disk"]["free"] > 0 and "legacy_projects" in st
    pol = client.get("/api/auth/me").json()["media_policy"]
    assert pol == {"lifecycle": True, "retention_hours": 24, "max_hours": 72}


def test_lifecycle_switch_off(monkeypatch):
    pid = _managed_project()
    monkeypatch.setattr(lifecycle, "MEDIA_LIFECYCLE", False)
    assert lifecycle.sweep(now=time.time() + 1000 * H)["skipped"]
    assert (PROJECTS_DIR / pid / "source.mp4").exists()


@pytest.mark.skipif(not db.is_postgres(), reason="advisory lock hanya di PostgreSQL")
def test_concurrent_sweeps_only_one_runs():
    from sqlalchemy import text

    with db.engine().connect() as other:
        assert other.execute(text("SELECT pg_try_advisory_lock(727275)")).scalar()
        try:
            assert lifecycle.sweep()["skipped"]
        finally:
            other.execute(text("SELECT pg_advisory_unlock(727275)"))
            other.commit()
    assert not lifecycle.sweep()["skipped"]


def test_unit_admin_stats_scoped_to_own_unit(client):
    stamp = uuid.uuid4().hex[:8]
    mine = client.post("/api/admin/units", json={"name": f"Statistik {stamp}"}).json()
    other = client.post("/api/admin/units", json={"name": f"Statistik lain {stamp}"}).json()
    email = f"ua{stamp}@org.id"
    client.post("/api/admin/users", json={"email": email, "password": "password-123", "role": "unit_admin",
                                          "unit_id": mine["id"]})
    ua = login_client(email, "password-123")
    _set_media(_managed_project("punya-saya", unit_id=mine["id"]), bytes=1000)
    _set_media(_managed_project("punya-lain", unit_id=other["id"]), bytes=5_000_000)
    st = ua.get("/api/admin/stats").json()["storage"]
    assert st["media_bytes"] == 1000 and st["projects_by_state"] == {"tersedia": 1}
    assert "disk" not in st and "legacy_projects" not in st
