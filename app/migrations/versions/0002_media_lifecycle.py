"""P2.1 (7c8127a): siklus media sementara.

Tabel project_media; kolom units.storage_quota_mb (nullable).
Revisi ini hanya MENAMBAH (tabel, kolom nullable, indeks). downgrade() menghapus data dan karenanya hanya
berjalan bila diminta eksplisit (python -m app.manage db-downgrade <rev> --allow-data-loss).
"""
import sqlalchemy as sa
from alembic import op

from app.migrate import guard_data_loss

revision = "0002_media_lifecycle"
down_revision = '0001_baseline'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('project_media',
    sa.Column('project_id', sa.String(length=12), nullable=False),
    sa.Column('uploaded_at', sa.Float(), nullable=False),
    sa.Column('last_activity', sa.Float(), nullable=False),
    sa.Column('grace_until', sa.Float(), nullable=True),
    sa.Column('state', sa.String(length=10), nullable=False),
    sa.Column('bytes', sa.BigInteger(), nullable=False),
    sa.Column('state_changed', sa.Float(), nullable=True),
    sa.Column('deleted_at', sa.Float(), nullable=True),
    sa.Column('delete_reason', sa.String(length=20), nullable=True),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('project_id')
    )
    op.create_index(op.f('ix_project_media_state'), 'project_media', ['state'], unique=False)
    op.add_column('units', sa.Column('storage_quota_mb', sa.Integer(), nullable=True))


def downgrade() -> None:
    guard_data_loss(revision)
    with op.batch_alter_table('units') as batch:  # SQLite: DROP COLUMN lewat salin-tabel
        batch.drop_column('storage_quota_mb')
    op.drop_index(op.f('ix_project_media_state'), table_name='project_media')
    op.drop_table('project_media')
