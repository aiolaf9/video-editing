"""P0.1: kepastian siklus hidup media. Job yang antre atau worker-nya mati tidak boleh menahan media selamanya.

Aturan yang diuji (kebijakan 2/5 jam + toleransi pemulihan 1 jam):
- sampai masa simpan + MEDIA_JOB_GRACE_HOURS, job aktif tetap melindungi media (worker sempat pulih);
- lewat dari itu, job antre / dibatalkan / milik worker mati diakhiri `gagal` (Kedaluwarsa) dan media dihapus;
- job yang prosesnya hidup (heartbeat baru, FFmpeg/Whisper sedang jalan) tidak pernah dihentikan atau dihapus
  filenya; file yang masih dibuka proses di mesin ini juga tidak dihapus.
"""
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest
from sqlalchemy import select, update

from app import db, jobs, lifecycle, storage
from app.config import PROJECTS_DIR

H = 3600.0
REPO = Path(__file__).resolve().parent.parent
Q = "uji-p01"          # antrean khusus tes: tidak diambil worker lain


@pytest.fixture(autouse=True)
def policy(monkeypatch):
    monkeypatch.setattr(lifecycle, "MEDIA_RETENTION_HOURS", 2.0)
    monkeypatch.setattr(lifecycle, "MEDIA_MAX_HOURS", 5.0)
    monkeypatch.setattr(lifecycle, "MEDIA_WARN_HOURS", 0.5)
    monkeypatch.setattr(lifecycle, "MEDIA_JOB_GRACE_HOURS", 1.0)


def _project(status="siap"):
    pid = storage.create_project("p01")["id"]
    folder = PROJECTS_DIR / pid
    (folder / "source.mp4").write_bytes(b"v" * 4000)
    (folder / "renders" / "klip.mp4").write_bytes(b"r" * 3000)
    storage.add_render(pid, {"id": uuid.uuid4().hex[:12], "file": "klip.mp4", "title": "Klip", "start": 0, "end": 4,
                             "aspect": "9:16", "size": 3000, "created": time.time()})
    storage.update_project(pid, status=status, source="source.mp4", duration=8)
    lifecycle.refresh_bytes(pid)
    return pid


def _active_until_cap(pid):
    """Pengguna masih mengedit sampai 4,5 jam: masa simpan jatuh pada batas keras 5 jam sejak upload."""
    t0 = _uploaded(pid)
    for hours in (1.5, 3, 4.5):
        assert lifecycle.touch(pid, now=t0 + hours * H)
    assert lifecycle.info(pid, now=t0)["expires_at"] == pytest.approx(t0 + 5 * H)
    return t0


def _uploaded(pid):
    with db.engine().connect() as c:
        return c.execute(select(db.project_media.c.uploaded_at).where(
            db.project_media.c.project_id == pid)).scalar()


def _set_job(job_id, **values):
    with db.engine().begin() as c:
        c.execute(update(db.jobs).where(db.jobs.c.id == job_id).values(**values))


def _media_state(pid):
    return lifecycle.info(pid)["state"]


def test_default_recovery_grace_is_one_hour():
    env = {k: v for k, v in os.environ.items() if not k.startswith("MEDIA_")}
    out = subprocess.run([sys.executable, "-c", "import json; from app import config as c; "
                          "print(json.dumps(c.MEDIA_JOB_GRACE_HOURS))"],
                         env=env, cwd=REPO, capture_output=True, text=True, check=True).stdout
    assert json.loads(out.strip().splitlines()[-1]) == 1.0


def test_queued_job_with_worker_offline_is_expired_after_5h_plus_grace():
    pid = _project(status="antre")
    job = jobs.submit("analyze", "analisis", pid, {}, "Analisis video", queue=Q)   # tidak ada worker untuk Q
    t0 = _active_until_cap(pid)
    lifecycle.sweep(now=t0 + 5.5 * H)                     # lewat 5 jam, masih dalam toleransi pemulihan
    assert jobs.get(job["id"])["status"] == "antre"
    assert (PROJECTS_DIR / pid / "source.mp4").exists()
    res = lifecycle.sweep(now=t0 + 6 * H + 60)           # lewat 5 jam + 1 jam toleransi
    assert res["jobs_expired"] == 1 and res["expired"] == 1
    done = jobs.get(job["id"])
    assert done["status"] == "gagal" and done["message"] == "Kedaluwarsa"
    assert "Kedaluwarsa" in done["error"] and "unggah ulang" in done["error"]
    assert not (PROJECTS_DIR / pid / "source.mp4").exists()
    assert not (PROJECTS_DIR / pid / "renders" / "klip.mp4").exists()
    assert _media_state(pid) == "dihapus"
    meta = storage.load_project(pid)
    assert meta["name"] == "p01" and meta["status"] == "gagal"    # metadata tetap, status tidak menggantung


def test_grace_counts_from_actual_expiry_not_from_5h():
    """Tanpa aktivitas, masa simpan berakhir 2 jam setelah upload; toleransi job berakhir 1 jam sesudahnya."""
    pid = _project(status="antre")
    job = jobs.submit("analyze", "analisis", pid, {}, "Analisis video", queue=Q)
    t0 = _uploaded(pid)
    lifecycle.sweep(now=t0 + 2.9 * H)
    assert jobs.get(job["id"])["status"] == "antre" and (PROJECTS_DIR / pid / "source.mp4").exists()
    lifecycle.sweep(now=t0 + 3 * H + 60)
    assert jobs.get(job["id"])["status"] == "gagal" and _media_state(pid) == "dihapus"


def test_job_from_dead_worker_is_expired_and_media_deleted():
    pid = _project()
    job = jobs.submit("render", "render", pid, {}, "Render", queue=Q)
    assert jobs.claim((Q,), "w-mati")["id"] == job["id"]
    t0 = _uploaded(pid)
    _set_job(job["id"], heartbeat=t0 + 10 * 60)          # worker mati 10 menit setelah upload, tidak pernah pulih
    lifecycle.sweep(now=t0 + 6 * H + 60)
    assert jobs.get(job["id"])["status"] == "gagal"
    assert not (PROJECTS_DIR / pid / "source.mp4").exists()
    assert storage.load_project(pid)["status"] == "siap"            # render gagal tidak mengubah status proyek


def test_cancelled_job_whose_worker_died_does_not_hold_media():
    pid = _project()
    job = jobs.submit("render", "render", pid, {}, "Render", queue=Q)
    jobs.claim((Q,), "w-mati")
    t0 = _uploaded(pid)
    _set_job(job["id"], status="berhenti", heartbeat=t0 + 60)
    lifecycle.sweep(now=t0 + 6 * H + 60)
    assert jobs.get(job["id"])["status"] == "gagal"
    assert not (PROJECTS_DIR / pid / "source.mp4").exists()


def test_running_job_with_live_heartbeat_is_never_interrupted():
    """FFmpeg/Whisper benar-benar berjalan (heartbeat baru): media tidak dihapus walau lewat 5 jam + toleransi.
    Batas untuk kasus ini adalah batas waktu job (watchdog); begitu job selesai, media langsung dihapus."""
    pid = _project()
    job = jobs.submit("render", "render", pid, {}, "Render", queue=Q)
    jobs.claim((Q,), "w-hidup")
    far = _uploaded(pid) + 7 * H
    _set_job(job["id"], heartbeat=far - 5)                # heartbeat 5 detik lalu
    res = lifecycle.sweep(now=far)
    assert res["jobs_expired"] == 0 and res["expired"] == 0
    assert jobs.get(job["id"])["status"] == "jalan"
    assert (PROJECTS_DIR / pid / "source.mp4").exists()
    jobs.finish(job["id"], result={})
    assert lifecycle.collect_if_expired(pid, now=far)    # selesai: langsung dibersihkan
    assert not (PROJECTS_DIR / pid / "source.mp4").exists()


def test_job_claimed_just_before_sweep_is_not_expired():
    """Worker pulih tepat sebelum pembersihan: job yang baru diambil (heartbeat baru) tidak diakhiri."""
    pid = _project()
    job = jobs.submit("analyze", "analisis", pid, {}, "Analisis", queue=Q)
    far = _uploaded(pid) + 6 * H + 60
    jobs.claim((Q,), "w-pulih", now=far - 1)
    lifecycle.sweep(now=far)
    assert jobs.get(job["id"])["status"] == "jalan"
    assert (PROJECTS_DIR / pid / "source.mp4").exists()
    jobs.finish(job["id"], result={})


def test_worker_back_within_grace_can_still_process():
    pid = _project()
    job = jobs.submit("render", "render", pid, {}, "Render", queue=Q)
    t0 = _active_until_cap(pid)
    lifecycle.sweep(now=t0 + 5.5 * H)
    assert jobs.claim((Q,), "w-pulih", now=t0 + 5.5 * H)["id"] == job["id"]
    lifecycle.require_present(pid)                        # file masih ada untuk job yang diterima
    jobs.finish(job["id"], result={})


def test_file_still_open_by_a_process_is_not_deleted():
    """Pengaman kedua: worker tampak mati (heartbeat basi) tetapi proses di mesin ini masih membuka file."""
    pid = _project()
    job = jobs.submit("render", "render", pid, {}, "Render", queue=Q)
    jobs.claim((Q,), "w-beku")
    t0 = _uploaded(pid)
    _set_job(job["id"], heartbeat=t0 + 60)
    far = t0 + 6 * H + 60
    with open(PROJECTS_DIR / pid / "source.mp4", "rb"):   # seperti FFmpeg yang masih membaca sumber
        lifecycle.sweep(now=far)
        assert (PROJECTS_DIR / pid / "source.mp4").exists()
        assert _media_state(pid) == "tersedia"            # dikembalikan, dicoba lagi di putaran berikutnya
    lifecycle.sweep(now=far + 300)
    assert not (PROJECTS_DIR / pid / "source.mp4").exists()


def test_every_project_has_a_bounded_lifetime():
    """Gabungan: berbagai keadaan job yang tidak aktif. Setelah 5 jam + toleransi semuanya terhapus."""
    pids = []
    for state in ("antre", "jalan-basi", "berhenti-basi"):
        pid = _project(status="antre")
        job = jobs.submit("analyze", "analisis", pid, {}, state, queue=Q)
        if state != "antre":
            jobs.claim((Q,), "w-x")
            _set_job(job["id"], status=state.split("-")[0], heartbeat=_uploaded(pid))
        pids.append(pid)
    latest = max(_uploaded(p) for p in pids)
    lifecycle.sweep(now=latest + 6 * H + 60)
    for pid in pids:
        assert _media_state(pid) == "dihapus", pid
        assert not any(j["status"] in jobs.ACTIVE for j in jobs.for_project(pid))
