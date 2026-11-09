"""P1: resep editing per proyek & template editing.

Tabel project_recipes dan edit_templates.
Revisi ini hanya MENAMBAH tabel. downgrade() menghapus data dan karenanya hanya berjalan bila diminta eksplisit
(python -m app.manage db-downgrade <rev> --allow-data-loss).
"""
import sqlalchemy as sa
from alembic import op

from app.migrate import guard_data_loss

revision = "0007_recipes"
down_revision = "0006_uploads"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('project_recipes',
    sa.Column('project_id', sa.String(length=12), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('data', sa.Text(), nullable=False),
    sa.Column('updated', sa.Float(), nullable=False),
    sa.Column('updated_by', sa.String(length=12), nullable=True),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('project_id')
    )
    op.create_table('edit_templates',
    sa.Column('id', sa.String(length=12), nullable=False),
    sa.Column('scope', sa.String(length=10), nullable=False),
    sa.Column('unit_id', sa.String(length=12), nullable=True),
    sa.Column('owner_id', sa.String(length=12), nullable=True),
    sa.Column('name', sa.String(length=80), nullable=False),
    sa.Column('data', sa.Text(), nullable=False),
    sa.Column('created', sa.Float(), nullable=False),
    sa.Column('updated', sa.Float(), nullable=False),
    sa.ForeignKeyConstraint(['owner_id'], ['users.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['unit_id'], ['units.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_edit_templates_owner_id'), 'edit_templates', ['owner_id'], unique=False)
    op.create_index(op.f('ix_edit_templates_unit_id'), 'edit_templates', ['unit_id'], unique=False)


def downgrade() -> None:
    guard_data_loss(revision)
    op.drop_index(op.f('ix_edit_templates_unit_id'), table_name='edit_templates')
    op.drop_index(op.f('ix_edit_templates_owner_id'), table_name='edit_templates')
    op.drop_table('edit_templates')
    op.drop_table('project_recipes')
