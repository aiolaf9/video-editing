"""P0: kebijakan media sementara AI Video Studio — 2 jam sejak aktivitas terakhir, paling lama 5 jam sejak upload.

Tes lain memakai 24/72 jam (conftest); di sini nilai bawaan dibaca di proses bersih dan skenarionya diuji dengan
konstanta lifecycle 2/5 jam.
"""
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest

from app import jobs, lifecycle, storage
from app.config import PROJECTS_DIR

H = 3600.0
REPO = Path(__file__).resolve().parent.parent


def test_defaults_are_2h_activity_5h_upload_with_30min_warning():
    env = {k: v for k, v in os.environ.items() if not k.startswith("MEDIA_")}
    out = subprocess.run([sys.executable, "-c", "import json; from app import config as c; print(json.dumps("
                          "[c.MEDIA_RETENTION_HOURS, c.MEDIA_MAX_HOURS, c.MEDIA_WARN_HOURS, c.MEDIA_LIFECYCLE, "
                          "c.WEB_CLEANUP]))"], env=env, cwd=REPO, capture_output=True, text=True, check=True).stdout
    assert json.loads(out.strip().splitlines()[-1]) == [2.0, 5.0, 0.5, True, True]


@pytest.fixture
def policy_2_5(monkeypatch):
    monkeypatch.setattr(lifecycle, "MEDIA_RETENTION_HOURS", 2.0)
    monkeypatch.setattr(lifecycle, "MEDIA_MAX_HOURS", 5.0)
    monkeypatch.setattr(lifecycle, "MEDIA_WARN_HOURS", 0.5)


def _project():
    pid = storage.create_project("p0-simpan")["id"]
    folder = PROJECTS_DIR / pid
    (folder / "source.mp4").write_bytes(b"v" * 4000)
    (folder / "preview.mp4").write_bytes(b"p" * 2000)
    (folder / "renders" / "klip.mp4").write_bytes(b"r" * 3000)
    storage.add_render(pid, {"id": uuid.uuid4().hex[:12], "file": "klip.mp4", "title": "Klip", "start": 0, "end": 4,
                             "aspect": "9:16", "size": 3000, "created": time.time()})
    storage.update_project(pid, status="siap", source="source.mp4", duration=8)
    lifecycle.refresh_bytes(pid)
    return pid


def test_expires_2h_after_last_activity_with_warning_only_near_the_end(policy_2_5):
    pid = _project()
    row = _row(pid)
    t0 = row["last_activity"]
    m = lifecycle.describe(row, now=t0)
    assert m["expires_at"] == pytest.approx(t0 + 2 * H, abs=5) and not m["warning"]   # baru upload: tidak ada peringatan
    assert not lifecycle.describe(row, now=t0 + 1.4 * H)["warning"]
    assert lifecycle.describe(row, now=t0 + 1.6 * H)["warning"]                           # < 30 menit lagi: peringatan
    lifecycle.sweep(now=t0 + 1.9 * H)
    assert (PROJECTS_DIR / pid / "source.mp4").exists()                                    # belum waktunya
    lifecycle.sweep(now=t0 + 2 * H + 10)
    assert not (PROJECTS_DIR / pid / "source.mp4").exists()                                # dihapus otomatis
    assert _row(pid)["state"] == "dihapus"
    assert storage.load_project(pid)["name"] == "p0-simpan"                                 # metadata tetap


def test_activity_extends_but_never_beyond_5h_after_upload(policy_2_5):
    pid = _project()
    t0 = _row(pid)["uploaded_at"]
    for hours in (1.5, 3, 4.5):                                                            # editing terus berjalan
        assert lifecycle.touch(pid, now=t0 + hours * H)
    assert lifecycle.expires_at(_row(pid)) == pytest.approx(t0 + 5 * H)
    lifecycle.sweep(now=t0 + 4.9 * H)
    assert (PROJECTS_DIR / pid / "source.mp4").exists()
    lifecycle.sweep(now=t0 + 5 * H + 10)
    assert not (PROJECTS_DIR / pid).exists() or not (PROJECTS_DIR / pid / "source.mp4").exists()


def test_active_job_protects_media_past_the_limit(policy_2_5):
    pid = _project()
    job = jobs.submit("render", "render", pid, {}, "lindung", queue="uji-p0-simpan")
    far = time.time() + 6 * H                                                              # lewat 2 jam & 5 jam
    assert jobs.claim(("uji-p0-simpan",), "w-p0")["id"] == job["id"]                       # job sedang berjalan
    from sqlalchemy import update

    from app import db
    with db.engine().begin() as c:                      # heartbeat hidup: FFmpeg benar-benar sedang jalan
        c.execute(update(db.jobs).where(db.jobs.c.id == job["id"]).values(heartbeat=far - 5))
    lifecycle.sweep(now=far)
    assert (PROJECTS_DIR / pid / "source.mp4").exists()                                    # job aktif: tidak dihapus
    assert (PROJECTS_DIR / pid / "renders" / "klip.mp4").exists()
    assert not lifecycle.delete_media(pid, "uji", now=far)
    jobs.finish(job["id"], result={})
    lifecycle.sweep(now=far)
    assert not (PROJECTS_DIR / pid / "source.mp4").exists()                                # selesai: baru dihapus


def test_expiry_survives_restart_and_web_cleanup_runs_without_workers(policy_2_5, monkeypatch):
    """Batas waktu dihitung dari stempel waktu di database (bukan timer di memori): setelah restart, pembersihan
    pertama langsung menghapus yang sudah lewat. Proses web menjalankan pembersihan cadangan sendiri."""
    from app import config, main
    pid = _project()
    from sqlalchemy import update

    from app import db
    old = time.time() - 6 * H
    with db.engine().begin() as c:
        c.execute(update(db.project_media).where(db.project_media.c.project_id == pid).values(
            uploaded_at=old, last_activity=old))
    calls = []
    real = lifecycle.sweep
    monkeypatch.setattr(lifecycle, "sweep", lambda now=None: calls.append(1) or real(now))
    monkeypatch.setattr(config, "CLEANUP_INTERVAL", 0.05)
    ev = main._start_web_cleanup()
    try:
        deadline = time.time() + 10
        while (PROJECTS_DIR / pid / "source.mp4").exists() and time.time() < deadline:
            time.sleep(0.05)
    finally:
        ev.set()
    assert calls and not (PROJECTS_DIR / pid / "source.mp4").exists()


def _row(pid):
    from sqlalchemy import select

    from app import db
    with db.engine().connect() as c:
        return db.row(c.execute(select(db.project_media).where(db.project_media.c.project_id == pid)).first())
