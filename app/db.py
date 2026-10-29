"""Database aplikasi (SQLAlchemy Core).

- Lokal / satu komputer : SQLite di folder data (tanpa instalasi apa pun)
- Server                : PostgreSQL, diatur lewat DATABASE_URL
"""
import json
import time
from typing import Any, Optional

from sqlalchemy import (Boolean, Column, Float, ForeignKey, Integer, MetaData, String, Table, Text,
                        create_engine, event, func, select, text)
from sqlalchemy.engine import Engine

from .config import DATABASE_URL, DB_MAX_OVERFLOW, DB_POOL_SIZE

metadata = MetaData()

units = Table(
    "units", metadata,
    Column("id", String(12), primary_key=True),
    Column("name", String(120), nullable=False, unique=True),
    Column("guidebook", Text, nullable=False, default=""),
    Column("daily_render_quota", Integer, nullable=False, default=0),   # 0 = tanpa batas
    Column("daily_ai_quota", Integer, nullable=False, default=0),
    Column("created", Float, nullable=False),
)

users = Table(
    "users", metadata,
    Column("id", String(12), primary_key=True),
    Column("email", String(200), nullable=False, unique=True),
    Column("name", String(120), nullable=False),
    Column("password_hash", String(300), nullable=False),
    Column("role", String(20), nullable=False),            # admin | unit_admin | editor
    Column("unit_id", String(12), ForeignKey("units.id", ondelete="SET NULL"), nullable=True, index=True),
    Column("active", Boolean, nullable=False, default=True),
    Column("created", Float, nullable=False),
    Column("last_login", Float, nullable=True),
)

sessions = Table(
    "sessions", metadata,
    Column("token_hash", String(64), primary_key=True),
    Column("user_id", String(12), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True),
    Column("created", Float, nullable=False),
    Column("expires", Float, nullable=False),
)

login_failures = Table(
    "login_failures", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("key", String(250), nullable=False, index=True),
    Column("at", Float, nullable=False),
)

projects = Table(
    "projects", metadata,
    Column("id", String(12), primary_key=True),
    Column("unit_id", String(12), ForeignKey("units.id", ondelete="SET NULL"), nullable=True, index=True),
    Column("owner_id", String(12), ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
    Column("name", String(200), nullable=False),
    Column("status", String(20), nullable=False),
    Column("source", String(200), nullable=True),
    Column("url", Text, nullable=True),
    Column("duration", Float, nullable=True),
    Column("width", Integer, nullable=True),
    Column("height", Integer, nullable=True),
    Column("fps", Float, nullable=True),
    Column("has_audio", Boolean, nullable=True),
    Column("language", String(10), nullable=True),
    Column("created", Float, nullable=False, index=True),
)

renders = Table(
    "renders", metadata,
    Column("id", String(12), primary_key=True),
    Column("project_id", String(12), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True),
    Column("user_id", String(12), nullable=True),
    Column("file", String(100), nullable=False),
    Column("title", String(200), nullable=False),
    Column("start", Float, nullable=False),
    Column("end", Float, nullable=False),
    Column("aspect", String(10), nullable=False),
    Column("size", Integer, nullable=False),
    Column("created", Float, nullable=False),
)

music = Table(
    "music", metadata,
    Column("project_id", String(12), ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True),
    Column("name", String(150), primary_key=True),
)

jobs = Table(
    "jobs", metadata,
    Column("id", String(12), primary_key=True),
    Column("task", String(20), nullable=False),             # analyze | download | render
    Column("kind", String(20), nullable=False),             # analisis | render (untuk tampilan)
    Column("queue", String(20), nullable=False, index=True),  # berat | unduh
    Column("project_id", String(12), nullable=False, index=True),
    Column("user_id", String(12), nullable=True),
    Column("unit_id", String(12), nullable=True, index=True),
    Column("label", String(200), nullable=False, default=""),
    Column("payload", Text, nullable=False, default="{}"),
    Column("status", String(10), nullable=False, index=True),  # antre|jalan|berhenti|selesai|gagal|batal (maks 10)
    Column("progress", Float, nullable=False, default=0),
    Column("message", String(300), nullable=False, default=""),
    Column("result", Text, nullable=True),
    Column("error", Text, nullable=True),
    Column("attempts", Integer, nullable=False, default=0),
    Column("worker", String(80), nullable=True),
    Column("created", Float, nullable=False, index=True),
    Column("started", Float, nullable=True),
    Column("finished", Float, nullable=True),
    Column("heartbeat", Float, nullable=True),
)

settings = Table(
    "settings", metadata,
    Column("key", String(60), primary_key=True),
    Column("value", Text, nullable=False),
)

usage = Table(
    "usage_events", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("kind", String(20), nullable=False),             # ai | analyze | render
    Column("unit_id", String(12), nullable=True, index=True),
    Column("user_id", String(12), nullable=True),
    Column("at", Float, nullable=False, index=True),
)

_engine: Optional[Engine] = None


def engine() -> Engine:
    global _engine
    if _engine is None:
        if DATABASE_URL.startswith("sqlite"):
            _engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False, "timeout": 30})

            @event.listens_for(_engine, "connect")
            def _sqlite_pragmas(conn, _record):
                cur = conn.cursor()
                cur.execute("PRAGMA journal_mode=WAL")   # baca & tulis bersamaan
                cur.execute("PRAGMA foreign_keys=ON")
                cur.execute("PRAGMA busy_timeout=30000")
                cur.close()
            metadata.create_all(_engine)
        else:
            # Permintaan yang melebihi pool menunggu giliran (pool_timeout), bukan membuka koneksi baru,
            # sehingga lonjakan pengguna tidak membuat PostgreSQL kehabisan koneksi.
            _engine = create_engine(DATABASE_URL, pool_size=DB_POOL_SIZE, max_overflow=DB_MAX_OVERFLOW,
                                    pool_timeout=30, pool_recycle=1800, pool_pre_ping=True)
            # Beberapa proses web/worker bisa start bersamaan: kunci agar tabel dibuat satu kali saja.
            with _engine.begin() as conn:
                conn.execute(text("SELECT pg_advisory_xact_lock(727274)"))
                metadata.create_all(conn)
    return _engine


def is_postgres() -> bool:
    return engine().dialect.name == "postgresql"


def row(r) -> Optional[dict]:
    return dict(r._mapping) if r is not None else None


def get_setting(key: str, default: Any = None) -> Any:
    with engine().connect() as c:
        r = c.execute(select(settings.c.value).where(settings.c.key == key)).scalar()
    return json.loads(r) if r is not None else default


def set_setting(key: str, value: Any) -> None:
    data = json.dumps(value, ensure_ascii=False)
    with engine().begin() as c:
        if c.execute(select(settings.c.key).where(settings.c.key == key)).first():
            c.execute(settings.update().where(settings.c.key == key).values(value=data))
        else:
            c.execute(settings.insert().values(key=key, value=data))


def record_usage(kind: str, unit_id: Optional[str], user_id: Optional[str]) -> None:
    with engine().begin() as c:
        c.execute(usage.insert().values(kind=kind, unit_id=unit_id, user_id=user_id, at=time.time()))


def start_of_today() -> float:
    lt = time.localtime()
    return time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1))


def usage_count(kind: str, unit_id: str, since: float) -> int:
    with engine().connect() as c:
        return c.execute(select(func.count()).select_from(usage).where(
            usage.c.kind == kind, usage.c.unit_id == unit_id, usage.c.at >= since)).scalar() or 0
