"""Skema database pada commit 20ac1c9 (dasar multi-unit (sebelum P1; sama dengan P1 fc4f8ea)).

Disalin otomatis dari `git show 20ac1c9:app/db.py` (hanya definisi tabel) untuk tes migrasi Alembic:
database dibuat persis seperti aplikasi versi itu membuatnya (create_all + indeks ad-hoc), diisi data, lalu
dinaikkan dengan migrasi. JANGAN diubah.
"""
from sqlalchemy import (BigInteger, Boolean, Column, Float, ForeignKey, Integer, MetaData, String, Table,  # noqa: F401
                        Text)

REVISION = "20ac1c9"
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
    Column("status", String(10), nullable=False, index=True),  # antre | jalan | selesai | gagal
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

# Indeks yang dibuat aplikasi versi ini di luar metadata (sebelum Alembic).
ADDED_INDEXES = []
