"""Migrasi skema database dengan Alembic (P5). Lihat docs/DATABASE_MIGRATIONS.md.

- ensure_schema(): dipanggil sekali saat proses web/worker pertama kali memakai database. Database harus berada
  di revisi terbaru (head). Bila tertinggal: upgrade otomatis hanya bila AUTO_MIGRATE aktif (bawaan: SQLite ya,
  PostgreSQL tidak); selain itu proses berhenti dengan pesan jelas. Revisi tidak dikenal (database lebih baru dari
  aplikasi) selalu ditolak.
- Database lama tanpa tabel alembic_version (dibuat sebelum P5 dengan create_all) dikenali dari ciri skemanya,
  di-stamp ke revisi yang sesuai, lalu dinaikkan. Data tidak disentuh.
- Upgrade: satu transaksi (PostgreSQL & SQLite) + kunci (advisory lock / BEGIN IMMEDIATE + file lock) sehingga
  beberapa proses yang start bersamaan aman; SQLite disalin ke data/backups/ sebelum diubah.
"""
import fcntl
import logging
import os
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional

from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.pool import NullPool

from .config import AUTO_MIGRATE, DATA_DIR, DATABASE_URL

log = logging.getLogger("video-editing.migrate")

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"
LOCK_KEY = 727274  # sama dengan kunci create_all lama: proses versi lama & baru tidak berjalan bersamaan
CORE_TABLES = {"units", "users", "projects", "jobs"}
KEEP_SQLITE_BACKUPS = 5


class SchemaError(RuntimeError):
    """Versi skema database tidak cocok dengan aplikasi (perlu tindakan operator)."""


def guard_data_loss(revision: str) -> None:
    """Dipanggil di awal setiap downgrade() yang menghapus tabel/kolom."""
    from alembic import context

    if not (context.config.attributes.get("allow_data_loss") or os.environ.get("ALEMBIC_ALLOW_DATA_LOSS") == "1"):
        raise SchemaError(f"Downgrade {revision} menghapus tabel/kolom beserta datanya. Buat backup, lalu jalankan "
                          "python -m app.manage db-downgrade <revisi> --allow-data-loss")


def _config(url: Optional[str] = None, locked: bool = False) -> Config:
    cfg = Config()
    cfg.attributes["locked"] = locked
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    cfg.set_main_option("sqlalchemy.url", (url or DATABASE_URL).replace("%", "%%"))
    cfg.attributes["url"] = url or DATABASE_URL
    return cfg


def head() -> str:
    return ScriptDirectory.from_config(_config()).get_current_head()


def known_revisions() -> set[str]:
    return {r.revision for r in ScriptDirectory.from_config(_config()).walk_revisions()}


def migration_engine(url: Optional[str] = None) -> Engine:
    """Engine khusus migrasi. SQLite: DDL di dalam transaksi sungguhan (driver sqlite3 bawaan tidak membungkus
    DDL dengan transaksi) dan BEGIN IMMEDIATE = kunci tulis selama migrasi."""
    url = url or DATABASE_URL
    if not url.startswith("sqlite"):
        return create_engine(url, poolclass=NullPool)
    eng = create_engine(url, poolclass=NullPool, connect_args={"timeout": 60})

    @event.listens_for(eng, "connect")
    def _no_autobegin(dbapi_conn, _record):
        dbapi_conn.isolation_level = None

    @event.listens_for(eng, "begin")
    def _begin(conn):
        conn.exec_driver_sql("BEGIN IMMEDIATE")

    return eng


# ------------------------------------------------------------------ status & deteksi database lama

def _features(conn) -> dict:
    insp = inspect(conn)
    tables = set(insp.get_table_names())

    def cols(t):
        return {c["name"] for c in insp.get_columns(t)} if t in tables else set()

    def idx(t):
        return {i["name"] for i in insp.get_indexes(t)} if t in tables else set()

    jobs_idx = idx("jobs")
    # Ciri tiap revisi (urut). Semua harus ada agar revisi itu dianggap sudah diterapkan.
    return {"tables": tables, "steps": [
        ("0001_baseline", CORE_TABLES <= tables),
        ("0002_media_lifecycle", "project_media" in tables and "storage_quota_mb" in cols("units")),
        ("0003_media_leases", "media_leases" in tables),
        ("0004_scheduling", "workers" in tables and "priority" in cols("jobs")
         and {"ix_jobs_queue_status", "ix_jobs_queue_started"} <= jobs_idx),
        ("0005_metrics_index", "ix_jobs_queue_finished" in jobs_idx),
        ("0006_uploads", "uploads" in tables),
    ]}


def detect_legacy_revision(conn) -> Optional[str]:
    """Revisi yang setara dengan skema database tanpa alembic_version. None = database kosong.
    SchemaError bila cirinya tidak konsisten (mis. tabel revisi baru ada tetapi revisi sebelumnya belum)."""
    f = _features(conn)
    app_tables = f["tables"] - {"alembic_version"}
    if not app_tables:
        return None
    rev, gap = None, False
    for name, present in f["steps"]:
        if present and not gap:
            rev = name
        elif present and gap:
            raise SchemaError(f"Skema database tidak dikenali: ciri {name} ada, tetapi revisi sebelumnya belum lengkap. "
                              "Periksa manual sebelum migrasi (lihat docs/DATABASE_MIGRATIONS.md).")
        else:
            gap = True
    if rev is None:
        raise SchemaError(f"Database berisi tabel yang bukan milik aplikasi ini: {sorted(app_tables)[:5]}")
    return rev


def status(engine: Optional[Engine] = None) -> dict:
    eng = engine or migration_engine()
    try:
        with eng.connect() as conn:
            current = MigrationContext.configure(conn).get_current_revision()
            legacy = detect_legacy_revision(conn) if current is None else None
    finally:
        if engine is None:
            eng.dispose()
    h = head()
    if current is None and legacy is None:
        state = "kosong"
    elif current is None:
        state = "lama"            # sebelum P5: perlu stamp + upgrade
    elif current == h:
        state = "terbaru"
    elif current in known_revisions():
        state = "tertinggal"
    else:
        state = "tidak-dikenal"   # lebih baru dari aplikasi ini
    return {"current": current, "legacy": legacy, "head": h, "state": state}


# ------------------------------------------------------------------ upgrade / downgrade

@contextmanager
def _process_lock(url: str) -> Iterator[None]:
    """Satu proses migrasi per database. PostgreSQL: advisory lock sesi; SQLite: kunci file di samping database."""
    if url.startswith("sqlite"):
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        with open(DATA_DIR / ".migrate.lock", "w") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(fh, fcntl.LOCK_UN)
        return
    eng = create_engine(url, poolclass=NullPool)
    with eng.connect() as conn:
        conn.execute(text("SELECT pg_advisory_lock(:k)"), {"k": LOCK_KEY})
        conn.commit()
        try:
            yield
        finally:
            conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": LOCK_KEY})
            conn.commit()
    eng.dispose()


def _sqlite_path(url: str) -> Optional[Path]:
    path = url.split("sqlite:///", 1)[1] if url.startswith("sqlite:///") else ""
    return Path(path) if path and path != ":memory:" else None


def backup_sqlite(url: str, label: str) -> Optional[Path]:
    """Salinan konsisten (sqlite3 backup API, aman walau ada pembaca/penulis lain) sebelum migrasi."""
    src = _sqlite_path(url)
    if src is None or not src.exists():
        return None
    folder = src.parent / "backups"
    folder.mkdir(exist_ok=True)
    dst = folder / f"app-{label}-{time.strftime('%Y%m%d-%H%M%S')}.db"
    with sqlite3.connect(src) as s, sqlite3.connect(dst) as d:
        s.backup(d)
    old = sorted(folder.glob("app-*.db"), key=lambda p: p.stat().st_mtime)
    for p in old[:-KEEP_SQLITE_BACKUPS]:
        p.unlink(missing_ok=True)
    return dst


def upgrade(url: Optional[str] = None, target: str = "head") -> dict:
    """Naikkan ke `target`. Database lama di-stamp dulu. Mengembalikan status sebelum & sesudah."""
    url = url or DATABASE_URL
    with _process_lock(url):
        eng = migration_engine(url)
        try:
            before = status(eng)
            if before["state"] == "tidak-dikenal":
                raise SchemaError(f"Revisi database {before['current']} tidak dikenal aplikasi ini (database lebih baru?).")
            if before["state"] == "terbaru" and target == "head":
                return {"before": before, "after": before, "backup": None}
            backup = (backup_sqlite(url, before["current"] or before["legacy"])
                      if url.startswith("sqlite") and before["state"] != "kosong" else None)
            cfg = _config(url, locked=True)
            if before["state"] == "lama":
                log.warning("Database tanpa versi skema: dikenali sebagai %s, di-stamp lalu di-upgrade.", before["legacy"])
                command.stamp(cfg, before["legacy"])
            log.info("Migrasi database %s -> %s", before["current"] or before["legacy"] or "kosong", target)
            command.upgrade(cfg, target)
            after = status(eng)
        finally:
            eng.dispose()
    return {"before": before, "after": after, "backup": str(backup) if backup else None}


def downgrade(target: str, allow_data_loss: bool = False, url: Optional[str] = None) -> dict:
    url = url or DATABASE_URL
    with _process_lock(url):
        eng = migration_engine(url)
        try:
            before = status(eng)
            backup = backup_sqlite(url, f"sebelum-downgrade-{before['current']}") if url.startswith("sqlite") else None
            cfg = _config(url, locked=True)
            cfg.attributes["allow_data_loss"] = allow_data_loss
            command.downgrade(cfg, target)
            after = status(eng)
        finally:
            eng.dispose()
    return {"before": before, "after": after, "backup": str(backup) if backup else None}


def drift(url: Optional[str] = None) -> list:
    """Perbedaan antara skema database dan db.metadata (harus kosong setelah upgrade head)."""
    from alembic.autogenerate import compare_metadata

    from . import db

    eng = migration_engine(url)
    try:
        with eng.connect() as conn:
            ctx = MigrationContext.configure(conn, opts={"compare_type": True})
            diffs = compare_metadata(ctx, db.metadata)
    finally:
        eng.dispose()
    return [d for d in diffs if not (isinstance(d, tuple) and d and d[0] == "remove_table" and d[1].name == "alembic_version")]


def ensure_schema(url: Optional[str] = None, auto: Optional[bool] = None) -> dict:
    """Validasi saat start. Lihat docstring modul."""
    url = url or DATABASE_URL
    auto = AUTO_MIGRATE if auto is None else auto
    if auto is None:
        auto = url.startswith("sqlite")
    eng = migration_engine(url)
    try:
        st = status(eng)
    finally:
        eng.dispose()
    if st["state"] == "terbaru":
        return st
    if st["state"] == "tidak-dikenal":
        raise SchemaError(f"Database berada di revisi {st['current']} yang tidak dikenal aplikasi ini (database lebih "
                          "baru dari aplikasi?). Jalankan aplikasi versi yang sesuai atau pulihkan backup.")
    if not auto:
        raise SchemaError(f"Skema database perlu di-upgrade ({st['current'] or st['legacy'] or 'kosong'} -> {st['head']}). "
                          "Buat backup lalu jalankan: python -m app.manage db-upgrade "
                          "(Docker: docker compose run --rm migrate).")
    return upgrade(url)["after"]
