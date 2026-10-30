"""P3 (0080aa0): penjadwalan antrean & registri worker.

Kolom jobs.priority (nullable); tabel workers; indeks antrean (queue,status) & (queue,started).
Revisi ini hanya MENAMBAH (tabel, kolom nullable, indeks). downgrade() menghapus data dan karenanya hanya
berjalan bila diminta eksplisit (python -m app.manage db-downgrade <rev> --allow-data-loss).
"""
import sqlalchemy as sa
from alembic import op

from app.migrate import guard_data_loss

revision = "0004_scheduling"
down_revision = '0003_media_leases'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('workers',
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('host', sa.String(length=80), nullable=False),
    sa.Column('pid', sa.Integer(), nullable=True),
    sa.Column('queues', sa.String(length=60), nullable=False),
    sa.Column('state', sa.String(length=10), nullable=False),
    sa.Column('reason', sa.String(length=200), nullable=True),
    sa.Column('job_id', sa.String(length=12), nullable=True),
    sa.Column('threads', sa.Integer(), nullable=True),
    sa.Column('cpu_pct', sa.Float(), nullable=True),
    sa.Column('rss_mb', sa.Float(), nullable=True),
    sa.Column('jobs_ok', sa.Integer(), nullable=False),
    sa.Column('jobs_failed', sa.Integer(), nullable=False),
    sa.Column('system', sa.Text(), nullable=True),
    sa.Column('started', sa.Float(), nullable=False),
    sa.Column('heartbeat', sa.Float(), nullable=False),
    sa.PrimaryKeyConstraint('name')
    )
    op.create_index(op.f('ix_workers_heartbeat'), 'workers', ['heartbeat'], unique=False)
    op.add_column('jobs', sa.Column('priority', sa.Integer(), nullable=True))
    op.create_index('ix_jobs_queue_started', 'jobs', ['queue', 'started'], unique=False)
    op.create_index('ix_jobs_queue_status', 'jobs', ['queue', 'status'], unique=False)


def downgrade() -> None:
    guard_data_loss(revision)
    op.drop_index('ix_jobs_queue_status', table_name='jobs')
    op.drop_index('ix_jobs_queue_started', table_name='jobs')
    with op.batch_alter_table('jobs') as batch:  # SQLite: DROP COLUMN lewat salin-tabel
        batch.drop_column('priority')
    op.drop_index(op.f('ix_workers_heartbeat'), table_name='workers')
    op.drop_table('workers')
