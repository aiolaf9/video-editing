"""Lingkungan Alembic. Dipanggil lewat app.migrate (aplikasi & manage.py) atau CLI `alembic` (pengembang)."""
from alembic import context
from sqlalchemy import text

from app import db

config = context.config
target_metadata = db.metadata


def _configure(connection) -> None:
    context.configure(
        connection=connection, target_metadata=target_metadata, compare_type=True,
        render_as_batch=connection.dialect.name == "sqlite",  # ALTER terbatas di SQLite
        transaction_per_migration=False,                       # seluruh upgrade = satu transaksi
        # Alembic menganggap SQLite tanpa DDL transaksional lalu meng-commit tiap revisi. SQLite sebenarnya
        # mendukungnya bila transaksi dibuka eksplisit (migration_engine: BEGIN IMMEDIATE), jadi gagal = rollback utuh.
        transactional_ddl=True,
    )


def run_migrations_online() -> None:
    from app import migrate

    connection = config.attributes.get("connection")
    owned = connection is None
    if owned:
        # URL dari pemanggil (app.migrate: cfg.attributes["url"]); CLI `alembic`: DATABASE_URL aplikasi.
        engine = migrate.migration_engine(config.attributes.get("url"))
        connection = engine.connect()
    try:
        _configure(connection)
        with context.begin_transaction():
            if connection.dialect.name == "postgresql" and not config.attributes.get("locked"):
                # CLI `alembic` langsung: satu proses saja yang memigrasi. (Lewat app.migrate kunci sudah
                # dipegang _process_lock; meminta lagi di koneksi lain = menunggu diri sendiri.)
                connection.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": migrate.LOCK_KEY})
            context.run_migrations()
    finally:
        if owned:
            connection.close()


def run_migrations_offline() -> None:
    """`alembic upgrade head --sql`: SQL untuk ditinjau DBA, tanpa menyentuh database."""
    from app.config import DATABASE_URL

    url = config.attributes.get("url") or DATABASE_URL
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True,
                      render_as_batch=url.startswith("sqlite"))
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
