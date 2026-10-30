"""P3 final (0b39237): indeks metrik monitoring.

Indeks jobs (queue,finished).
Revisi ini hanya MENAMBAH (tabel, kolom nullable, indeks). downgrade() menghapus data dan karenanya hanya
berjalan bila diminta eksplisit (python -m app.manage db-downgrade <rev> --allow-data-loss).
"""
import sqlalchemy as sa
from alembic import op

from app.migrate import guard_data_loss

revision = "0005_metrics_index"
down_revision = '0004_scheduling'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index('ix_jobs_queue_finished', 'jobs', ['queue', 'finished'], unique=False)


def downgrade() -> None:
    guard_data_loss(revision)
    op.drop_index('ix_jobs_queue_finished', table_name='jobs')
