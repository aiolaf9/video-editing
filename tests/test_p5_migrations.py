"""P5.2: migrasi Alembic — database baru, database lama berisi data (semua versi P0-P4), kegagalan & rollback,
validasi saat start, downgrade terlindungi, migrasi bersamaan. SQLite selalu; PostgreSQL bila DATABASE_URL-nya PG."""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, insert, text
from sqlalchemy.pool import NullPool

from app import migrate

REPO = Path(__file__).resolve().parent.parent
SNAPSHOTS = ["v0_20ac1c9", "v2a_7c8127a", "v2b_a19e135", "v3a_0080aa0", "v3b_0b39237", "v4_55366de"]
EXPECTED_STAMP = {"v0_20ac1c9": "0001_baseline", "v2a_7c8127a": "0002_media_lifecycle",
                  "v2b_a19e135": "0003_media_leases", "v3a_0080aa0": "0004_scheduling",
                  "v3b_0b39237": "0005_metrics_index", "v4_55366de": "0006_uploads"}
PG_URL = os.environ.get("DATABASE_URL", "") if os.environ.get("DATABASE_URL", "").startswith("postgresql") else ""
BACKENDS = ["sqlite"] + (["postgresql"] if PG_URL else [])


def load_snapshot(name):
    spec = importlib.util.spec_from_file_location(name, REPO / "tests" / "schema_snapshots" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(params=BACKENDS)
def fresh_url(request, tmp_path):
    """Database kosong khusus tes ini (SQLite file / database PostgreSQL sementara)."""
    if request.param == "sqlite":
        yield f"sqlite:///{tmp_path / 'mig.db'}"
        return
    name = f"ve_mig_{uuid.uuid4().hex[:10]}"
    admin = create_engine(PG_URL, poolclass=NullPool, isolation_level="AUTOCOMMIT")
    with admin.connect() as c:
        c.execute(text(f'CREATE DATABASE "{name}"'))
    base, _, query = PG_URL.partition("?")
    yield f"{base.rsplit('/', 1)[0]}/{name}" + (f"?{query}" if query else "")
    with admin.connect() as c:
        c.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
    admin.dispose()


def engine_for(url):
    return create_engine(url, poolclass=NullPool)


def version(url):
    eng = engine_for(url)
    try:
        with eng.connect() as c:
            if "alembic_version" not in inspect(c).get_table_names():
                return None
            return [r[0] for r in c.execute(text("SELECT version_num FROM alembic_version"))]
    finally:
        eng.dispose()


def tables(url):
    eng = engine_for(url)
    try:
        with eng.connect() as c:
            return set(inspect(c).get_table_names())
    finally:
        eng.dispose()


# ------------------------------------------------------------------ database lama berisi data

def build_legacy(url, snap):
    """Database persis seperti dibuat aplikasi versi itu (create_all + indeks ad-hoc), lalu diisi data."""
    eng = engine_for(url)
    snap.metadata.create_all(eng)
    t = snap.metadata.tables
    now = time.time()
    with eng.begin() as c:
        for sql in snap.ADDED_INDEXES:
            c.execute(text(sql))
        c.execute(insert(t["units"]).values(id="unit00000001", name="Humas", guidebook="Gaya santai",
                                            daily_render_quota=5, daily_ai_quota=10, created=now))
        c.execute(insert(t["users"]).values(id="user00000001", email="a@org.id", name="Ani", password_hash="x" * 60,
                                            role="editor", unit_id="unit00000001", active=True, created=now))
        c.execute(insert(t["sessions"]).values(token_hash="h" * 64, user_id="user00000001", created=now, expires=now + 99))
        c.execute(insert(t["projects"]).values(id="proj00000001", unit_id="unit00000001", owner_id="user00000001",
                                               name="Video lama", status="siap", source="source.mp4", duration=12.5,
                                               width=1920, height=1080, fps=30.0, has_audio=True, language="id",
                                               created=now))
        c.execute(insert(t["renders"]).values(id="rend00000001", project_id="proj00000001", user_id="user00000001",
                                              file="r.mp4", title="Klip 1", start=1.0, end=5.0, aspect="9:16",
                                              size=12345, created=now))
        c.execute(insert(t["music"]).values(project_id="proj00000001", name="lagu.mp3"))
        c.execute(insert(t["jobs"]).values(id="job000000001", task="render", kind="render", queue="berat",
                                           project_id="proj00000001", user_id="user00000001", unit_id="unit00000001",
                                           label="Klip", payload=json.dumps({"request": {"start": 1}}), status="antre",
                                           progress=0, message="Menunggu", attempts=0, created=now))
        c.execute(insert(t["settings"]).values(key="ai", value=json.dumps({"provider": "gemini"})))
        c.execute(insert(t["usage_events"]).values(kind="render", unit_id="unit00000001", user_id="user00000001", at=now))
        if "project_media" in t:
            c.execute(insert(t["project_media"]).values(project_id="proj00000001", uploaded_at=now, last_activity=now,
                                                        state="tersedia", bytes=999, state_changed=now))
            c.execute(t["units"].update().values(storage_quota_mb=500))
        if "media_leases" in t:
            c.execute(insert(t["media_leases"]).values(id="lease0000000001x", project_id="proj00000001",
                                                       unit_id="unit00000001", kind="download", bytes=0, owner="w",
                                                       created=now, expires_at=now + 60))
        if "workers" in t:
            c.execute(insert(t["workers"]).values(name="w1", host="h", queues="render", state="idle", jobs_ok=3,
                                                  jobs_failed=1, started=now, heartbeat=now))
            c.execute(t["jobs"].update().values(priority=10))
        if "uploads" in t:
            c.execute(insert(t["uploads"]).values(id="u" * 32, user_id="user00000001", unit_id="unit00000001",
                                                  project_id="proj00000001", kind="video", filename="a.mp4", ext=".mp4",
                                                  size=1000, offset=500, state="aktif", created=now, updated=now,
                                                  expires_at=now + 3600))
    eng.dispose()


def check_data(url, snap):
    eng = engine_for(url)
    try:
        with eng.connect() as c:
            row = c.execute(text("SELECT name, status, duration, width, has_audio, language FROM projects")).one()
            assert tuple(row) == ("Video lama", "siap", 12.5, 1920, True, "id")
            assert c.execute(text("SELECT email, role, unit_id FROM users")).one() == ("a@org.id", "editor", "unit00000001")
            assert c.execute(text("SELECT title, size FROM renders")).one() == ("Klip 1", 12345)
            assert c.execute(text("SELECT name FROM music")).scalar() == "lagu.mp3"
            assert c.execute(text("SELECT count(*) FROM sessions")).scalar() == 1
            assert json.loads(c.execute(text("SELECT value FROM settings WHERE key='ai'")).scalar()) == {"provider": "gemini"}
            assert json.loads(c.execute(text("SELECT payload FROM jobs")).scalar()) == {"request": {"start": 1}}
            quota = c.execute(text("SELECT storage_quota_mb FROM units")).scalar()
            priority = c.execute(text("SELECT priority FROM jobs")).scalar()
            t = snap.metadata.tables
            assert quota == (500 if "project_media" in t else None)   # kolom baru: NULL untuk data lama
            assert priority == (10 if "workers" in t else None)
            if "project_media" in t:
                assert c.execute(text("SELECT bytes FROM project_media")).scalar() == 999
            if "uploads" in t:
                assert c.execute(text('SELECT "offset", state FROM uploads')).one() == (500, "aktif")
            existing = set(inspect(c).get_table_names())
            for name in ("project_media", "media_leases", "workers", "uploads"):
                if name in t:
                    assert c.execute(text(f"SELECT count(*) FROM {name}")).scalar() == 1
                elif name in existing:  # tabel baru dari migrasi: kosong
                    assert c.execute(text(f"SELECT count(*) FROM {name}")).scalar() == 0
    finally:
        eng.dispose()


@pytest.mark.parametrize("snapshot", SNAPSHOTS)
def test_legacy_database_with_data_upgrades_without_loss(fresh_url, snapshot):
    snap = load_snapshot(snapshot)
    build_legacy(fresh_url, snap)
    st = migrate.status(engine_for(fresh_url))
    assert st["state"] == "lama" and st["legacy"] == EXPECTED_STAMP[snapshot]
    with pytest.raises(migrate.SchemaError, match="perlu di-upgrade"):
        migrate.ensure_schema(fresh_url, auto=False)  # produksi: tidak naik diam-diam
    after = migrate.ensure_schema(fresh_url, auto=True)
    assert after["state"] == "terbaru" and version(fresh_url) == [migrate.head()]
    check_data(fresh_url, snap)
    assert migrate.drift(fresh_url) == []  # skema hasil = model aplikasi, termasuk indeks & tipe


# ------------------------------------------------------------------ database baru, idempoten, validasi start

def test_fresh_database_and_idempotent_upgrade(fresh_url):
    assert migrate.status(engine_for(fresh_url))["state"] == "kosong"
    first = migrate.upgrade(fresh_url)
    assert first["after"]["state"] == "terbaru" and first["backup"] is None
    assert migrate.drift(fresh_url) == []
    again = migrate.upgrade(fresh_url)
    assert again["before"]["state"] == "terbaru" and version(fresh_url) == [migrate.head()]


def test_startup_refuses_unknown_or_inconsistent_schema(fresh_url):
    migrate.upgrade(fresh_url)
    eng = engine_for(fresh_url)
    with eng.begin() as c:
        c.execute(text("UPDATE alembic_version SET version_num = '9999_dari_masa_depan'"))
    with pytest.raises(migrate.SchemaError, match="tidak dikenal"):
        migrate.ensure_schema(fresh_url, auto=True)  # tidak pernah menebak, walau AUTO_MIGRATE aktif
    with eng.begin() as c:  # skema janggal: tabel P4 ada tetapi tabel P2.5 tidak, tanpa versi
        c.execute(text("DROP TABLE alembic_version"))
        c.execute(text("DROP TABLE media_leases"))
    eng.dispose()
    with pytest.raises(migrate.SchemaError, match="tidak dikenali"):
        migrate.ensure_schema(fresh_url, auto=True)


def test_foreign_database_is_not_touched(fresh_url):
    eng = engine_for(fresh_url)
    with eng.begin() as c:
        c.execute(text("CREATE TABLE pelanggan (id INTEGER PRIMARY KEY)"))
    eng.dispose()
    with pytest.raises(migrate.SchemaError, match="bukan milik aplikasi"):
        migrate.ensure_schema(fresh_url, auto=True)
    assert tables(fresh_url) == {"pelanggan"}


# ------------------------------------------------------------------ kegagalan migrasi

@pytest.fixture
def broken_head(tmp_path, monkeypatch):
    """Salinan migrasi + revisi 0007 yang membuat tabel & kolom lalu GAGAL di tengah jalan."""
    scripts = tmp_path / "migrations"
    shutil.copytree(migrate.MIGRATIONS_DIR, scripts, ignore=shutil.ignore_patterns("__pycache__"))
    (scripts / "versions" / "0007_rusak.py").write_text('''
import sqlalchemy as sa
from alembic import op

revision = "0007_rusak"
down_revision = "0006_uploads"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("tabel_setengah_jadi", sa.Column("id", sa.Integer(), primary_key=True))
    op.execute("INSERT INTO units (id, name, guidebook, daily_render_quota, daily_ai_quota, created) "
               "VALUES ('unitbaru0001', 'Baru', '', 0, 0, 0)")
    raise RuntimeError("migrasi gagal di tengah jalan")


def downgrade():
    pass
''')
    monkeypatch.setattr(migrate, "MIGRATIONS_DIR", scripts)
    return scripts


def test_failed_migration_rolls_back_completely(fresh_url, broken_head):
    """Database di 0004 dinaikkan ke head yang rusak: 0005, 0006, 0007 berjalan dalam SATU transaksi, jadi gagal
    = kembali utuh ke 0004 (tanpa indeks 0005, tanpa tabel uploads, tanpa sisa 0007)."""
    build_legacy(fresh_url, load_snapshot("v3a_0080aa0"))
    with pytest.raises(RuntimeError, match="gagal di tengah jalan"):
        migrate.upgrade(fresh_url)
    assert version(fresh_url) == ["0004_scheduling"]  # stamp database lama sudah tercatat, upgrade tidak
    tbl = tables(fresh_url)
    assert "uploads" not in tbl and "tabel_setengah_jadi" not in tbl
    eng = engine_for(fresh_url)
    with eng.connect() as c:
        assert "ix_jobs_queue_finished" not in {i["name"] for i in inspect(c).get_indexes("jobs")}
        assert c.execute(text("SELECT count(*) FROM units")).scalar() == 1  # data baru dari 0007 tidak tersisa
    eng.dispose()
    check_data(fresh_url, load_snapshot("v3a_0080aa0"))
    if fresh_url.startswith("sqlite"):
        assert list((Path(fresh_url.split("///")[1]).parent / "backups").glob("app-0004_scheduling-*.db"))


# ------------------------------------------------------------------ downgrade terlindungi

def test_downgrade_requires_explicit_permission(fresh_url):
    migrate.upgrade(fresh_url)
    with pytest.raises(migrate.SchemaError, match="allow-data-loss"):
        migrate.downgrade("0005_metrics_index", url=fresh_url)
    assert version(fresh_url) == [migrate.head()] and "uploads" in tables(fresh_url)
    migrate.downgrade("0003_media_leases", allow_data_loss=True, url=fresh_url)
    assert version(fresh_url) == ["0003_media_leases"]
    assert not {"uploads", "workers"} & tables(fresh_url)
    eng = engine_for(fresh_url)
    with eng.connect() as c:
        assert "priority" not in {col["name"] for col in inspect(c).get_columns("jobs")}  # SQLite: lewat batch
    eng.dispose()
    migrate.upgrade(fresh_url)
    assert migrate.drift(fresh_url) == []


# ------------------------------------------------------------------ proses bersamaan & CLI

def test_concurrent_startup_migrates_once(fresh_url, tmp_path):
    """Empat proses start bersamaan pada database kosong: semuanya berhasil, migrasi terjadi sekali."""
    code = "from app import migrate; print(migrate.ensure_schema(auto=True)['state'])"
    env = dict(os.environ, DATABASE_URL=fresh_url, VIDEO_APP_DATA=str(tmp_path / "data"), PYTHONPATH=str(REPO))
    procs = [subprocess.Popen([sys.executable, "-c", code], env=env, cwd=REPO, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, text=True) for _ in range(4)]
    outs = [p.communicate(timeout=120) for p in procs]
    assert all(p.returncode == 0 for p in procs), [o[1][-400:] for o in outs]
    assert all(o[0].strip() == "terbaru" for o in outs)
    assert version(fresh_url) == [migrate.head()]


def test_manage_cli_commands(fresh_url, tmp_path):
    env = dict(os.environ, DATABASE_URL=fresh_url, VIDEO_APP_DATA=str(tmp_path / "data"), PYTHONPATH=str(REPO))
    build_legacy(fresh_url, load_snapshot("v2a_7c8127a"))

    def run(*args):
        return subprocess.run([sys.executable, "-m", "app.manage", *args], env=env, cwd=REPO, capture_output=True,
                              text=True, timeout=120)

    st = run("db-status")
    assert st.returncode == 2 and "setara 0002_media_lifecycle" in st.stdout
    assert run("db-check").returncode == 1
    up = run("db-upgrade")
    assert up.returncode == 0 and "0002_media_lifecycle -> 0006_uploads" in up.stdout
    assert run("db-status").returncode == 0
    ok = run("db-check")
    assert ok.returncode == 0 and "OK" in ok.stdout
    down = run("db-downgrade", "0005_metrics_index")
    assert down.returncode == 1 and "allow-data-loss" in down.stderr


def test_default_policy_postgres_refuses_sqlite_upgrades(fresh_url, monkeypatch):
    """Tanpa AUTO_MIGRATE: PostgreSQL menolak start (tidak ada upgrade otomatis dari banyak web/worker);
    SQLite (satu komputer) naik otomatis dengan backup."""
    monkeypatch.setattr(migrate, "AUTO_MIGRATE", None)
    build_legacy(fresh_url, load_snapshot("v3b_0b39237"))
    if fresh_url.startswith("postgresql"):
        with pytest.raises(migrate.SchemaError, match="db-upgrade"):
            migrate.ensure_schema(fresh_url)
        assert version(fresh_url) is None  # tidak disentuh sama sekali
    else:
        assert migrate.ensure_schema(fresh_url)["state"] == "terbaru"


def test_queue_data_migration_is_separate_and_idempotent(fresh_url, monkeypatch):
    """Perpindahan antrean berat/unduh -> analisis/render/io adalah migrasi DATA di kode aplikasi
    (jobs.migrate_legacy_queues), bukan revisi Alembic: hanya job aktif, idempoten, riwayat tidak diubah."""
    build_legacy(fresh_url, load_snapshot("v2b_a19e135"))  # job antre di antrean "berat"
    migrate.upgrade(fresh_url)
    eng = engine_for(fresh_url)
    with eng.begin() as c:
        c.execute(text("INSERT INTO jobs (id, task, kind, queue, project_id, label, payload, status, progress, "
                       "message, attempts, created) VALUES ('job000000002', 'download', 'x', 'unduh', "
                       "'proj00000001', '', '{}', 'selesai', 100, '', 1, 0)"))
    from app import db, jobs
    monkeypatch.setattr(db, "_engine", eng)
    assert jobs.migrate_legacy_queues() == 1
    assert jobs.migrate_legacy_queues() == 0  # idempoten
    with eng.connect() as c:
        rows = dict(c.execute(text("SELECT id, queue FROM jobs")).all())
    assert rows == {"job000000001": "render", "job000000002": "unduh"}  # riwayat (selesai) tidak diubah
    assert version(fresh_url) == [migrate.head()]  # migrasi data tidak menyentuh versi skema
    eng.dispose()
