"""P2.5 (a19e135): lease media & reservasi kuota.

Tabel media_leases.
Revisi ini hanya MENAMBAH (tabel, kolom nullable, indeks). downgrade() menghapus data dan karenanya hanya
berjalan bila diminta eksplisit (python -m app.manage db-downgrade <rev> --allow-data-loss).
"""
import sqlalchemy as sa
from alembic import op

from app.migrate import guard_data_loss

revision = "0003_media_leases"
down_revision = '0002_media_lifecycle'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('media_leases',
    sa.Column('id', sa.String(length=16), nullable=False),
    sa.Column('project_id', sa.String(length=12), nullable=False),
    sa.Column('unit_id', sa.String(length=12), nullable=True),
    sa.Column('kind', sa.String(length=10), nullable=False),
    sa.Column('bytes', sa.BigInteger(), nullable=False),
    sa.Column('owner', sa.String(length=80), nullable=False),
    sa.Column('created', sa.Float(), nullable=False),
    sa.Column('expires_at', sa.Float(), nullable=False),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_media_leases_expires_at'), 'media_leases', ['expires_at'], unique=False)
    op.create_index(op.f('ix_media_leases_project_id'), 'media_leases', ['project_id'], unique=False)
    op.create_index(op.f('ix_media_leases_unit_id'), 'media_leases', ['unit_id'], unique=False)


def downgrade() -> None:
    guard_data_loss(revision)
    op.drop_index(op.f('ix_media_leases_unit_id'), table_name='media_leases')
    op.drop_index(op.f('ix_media_leases_project_id'), table_name='media_leases')
    op.drop_index(op.f('ix_media_leases_expires_at'), table_name='media_leases')
    op.drop_table('media_leases')
