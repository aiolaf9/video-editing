"""P4 (55366de): sesi upload bertahap (tus).

Tabel uploads.
Revisi ini hanya MENAMBAH (tabel, kolom nullable, indeks). downgrade() menghapus data dan karenanya hanya
berjalan bila diminta eksplisit (python -m app.manage db-downgrade <rev> --allow-data-loss).
"""
import sqlalchemy as sa
from alembic import op

from app.migrate import guard_data_loss

revision = "0006_uploads"
down_revision = '0005_metrics_index'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('uploads',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('user_id', sa.String(length=12), nullable=False),
    sa.Column('unit_id', sa.String(length=12), nullable=True),
    sa.Column('project_id', sa.String(length=12), nullable=True),
    sa.Column('kind', sa.String(length=10), nullable=False),
    sa.Column('filename', sa.String(length=200), nullable=False),
    sa.Column('ext', sa.String(length=10), nullable=False),
    sa.Column('size', sa.BigInteger(), nullable=False),
    sa.Column('offset', sa.BigInteger(), nullable=False),
    sa.Column('lease_id', sa.String(length=16), nullable=True),
    sa.Column('state', sa.String(length=10), nullable=False),
    sa.Column('error', sa.String(length=300), nullable=True),
    sa.Column('meta', sa.Text(), nullable=True),
    sa.Column('created', sa.Float(), nullable=False),
    sa.Column('updated', sa.Float(), nullable=False),
    sa.Column('expires_at', sa.Float(), nullable=False),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_uploads_expires_at'), 'uploads', ['expires_at'], unique=False)
    op.create_index(op.f('ix_uploads_project_id'), 'uploads', ['project_id'], unique=False)
    op.create_index(op.f('ix_uploads_state'), 'uploads', ['state'], unique=False)
    op.create_index(op.f('ix_uploads_unit_id'), 'uploads', ['unit_id'], unique=False)
    op.create_index(op.f('ix_uploads_user_id'), 'uploads', ['user_id'], unique=False)


def downgrade() -> None:
    guard_data_loss(revision)
    op.drop_index(op.f('ix_uploads_user_id'), table_name='uploads')
    op.drop_index(op.f('ix_uploads_unit_id'), table_name='uploads')
    op.drop_index(op.f('ix_uploads_state'), table_name='uploads')
    op.drop_index(op.f('ix_uploads_project_id'), table_name='uploads')
    op.drop_index(op.f('ix_uploads_expires_at'), table_name='uploads')
    op.drop_table('uploads')
