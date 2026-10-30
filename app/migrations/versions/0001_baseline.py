"""Skema dasar multi-unit (commit 20ac1c9, juga P1 fc4f8ea).

Membuat 10 tabel inti. Database yang sudah ada dari versi ini dikenali otomatis & hanya di-stamp.
Revisi ini hanya MENAMBAH (tabel, kolom nullable, indeks). downgrade() menghapus data dan karenanya hanya
berjalan bila diminta eksplisit (python -m app.manage db-downgrade <rev> --allow-data-loss).
"""
import sqlalchemy as sa
from alembic import op

from app.migrate import guard_data_loss

revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('jobs',
    sa.Column('id', sa.String(length=12), nullable=False),
    sa.Column('task', sa.String(length=20), nullable=False),
    sa.Column('kind', sa.String(length=20), nullable=False),
    sa.Column('queue', sa.String(length=20), nullable=False),
    sa.Column('project_id', sa.String(length=12), nullable=False),
    sa.Column('user_id', sa.String(length=12), nullable=True),
    sa.Column('unit_id', sa.String(length=12), nullable=True),
    sa.Column('label', sa.String(length=200), nullable=False),
    sa.Column('payload', sa.Text(), nullable=False),
    sa.Column('status', sa.String(length=10), nullable=False),
    sa.Column('progress', sa.Float(), nullable=False),
    sa.Column('message', sa.String(length=300), nullable=False),
    sa.Column('result', sa.Text(), nullable=True),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('attempts', sa.Integer(), nullable=False),
    sa.Column('worker', sa.String(length=80), nullable=True),
    sa.Column('created', sa.Float(), nullable=False),
    sa.Column('started', sa.Float(), nullable=True),
    sa.Column('finished', sa.Float(), nullable=True),
    sa.Column('heartbeat', sa.Float(), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_jobs_created'), 'jobs', ['created'], unique=False)
    op.create_index(op.f('ix_jobs_project_id'), 'jobs', ['project_id'], unique=False)
    op.create_index(op.f('ix_jobs_queue'), 'jobs', ['queue'], unique=False)
    op.create_index(op.f('ix_jobs_status'), 'jobs', ['status'], unique=False)
    op.create_index(op.f('ix_jobs_unit_id'), 'jobs', ['unit_id'], unique=False)
    op.create_table('login_failures',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('key', sa.String(length=250), nullable=False),
    sa.Column('at', sa.Float(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_login_failures_key'), 'login_failures', ['key'], unique=False)
    op.create_table('settings',
    sa.Column('key', sa.String(length=60), nullable=False),
    sa.Column('value', sa.Text(), nullable=False),
    sa.PrimaryKeyConstraint('key')
    )
    op.create_table('units',
    sa.Column('id', sa.String(length=12), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('guidebook', sa.Text(), nullable=False),
    sa.Column('daily_render_quota', sa.Integer(), nullable=False),
    sa.Column('daily_ai_quota', sa.Integer(), nullable=False),
    sa.Column('created', sa.Float(), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('name')
    )
    op.create_table('usage_events',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('kind', sa.String(length=20), nullable=False),
    sa.Column('unit_id', sa.String(length=12), nullable=True),
    sa.Column('user_id', sa.String(length=12), nullable=True),
    sa.Column('at', sa.Float(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_usage_events_at'), 'usage_events', ['at'], unique=False)
    op.create_index(op.f('ix_usage_events_unit_id'), 'usage_events', ['unit_id'], unique=False)
    op.create_table('users',
    sa.Column('id', sa.String(length=12), nullable=False),
    sa.Column('email', sa.String(length=200), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('password_hash', sa.String(length=300), nullable=False),
    sa.Column('role', sa.String(length=20), nullable=False),
    sa.Column('unit_id', sa.String(length=12), nullable=True),
    sa.Column('active', sa.Boolean(), nullable=False),
    sa.Column('created', sa.Float(), nullable=False),
    sa.Column('last_login', sa.Float(), nullable=True),
    sa.ForeignKeyConstraint(['unit_id'], ['units.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('email')
    )
    op.create_index(op.f('ix_users_unit_id'), 'users', ['unit_id'], unique=False)
    op.create_table('projects',
    sa.Column('id', sa.String(length=12), nullable=False),
    sa.Column('unit_id', sa.String(length=12), nullable=True),
    sa.Column('owner_id', sa.String(length=12), nullable=True),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('source', sa.String(length=200), nullable=True),
    sa.Column('url', sa.Text(), nullable=True),
    sa.Column('duration', sa.Float(), nullable=True),
    sa.Column('width', sa.Integer(), nullable=True),
    sa.Column('height', sa.Integer(), nullable=True),
    sa.Column('fps', sa.Float(), nullable=True),
    sa.Column('has_audio', sa.Boolean(), nullable=True),
    sa.Column('language', sa.String(length=10), nullable=True),
    sa.Column('created', sa.Float(), nullable=False),
    sa.ForeignKeyConstraint(['owner_id'], ['users.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['unit_id'], ['units.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_projects_created'), 'projects', ['created'], unique=False)
    op.create_index(op.f('ix_projects_unit_id'), 'projects', ['unit_id'], unique=False)
    op.create_table('sessions',
    sa.Column('token_hash', sa.String(length=64), nullable=False),
    sa.Column('user_id', sa.String(length=12), nullable=False),
    sa.Column('created', sa.Float(), nullable=False),
    sa.Column('expires', sa.Float(), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('token_hash')
    )
    op.create_index(op.f('ix_sessions_user_id'), 'sessions', ['user_id'], unique=False)
    op.create_table('music',
    sa.Column('project_id', sa.String(length=12), nullable=False),
    sa.Column('name', sa.String(length=150), nullable=False),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('project_id', 'name')
    )
    op.create_table('renders',
    sa.Column('id', sa.String(length=12), nullable=False),
    sa.Column('project_id', sa.String(length=12), nullable=False),
    sa.Column('user_id', sa.String(length=12), nullable=True),
    sa.Column('file', sa.String(length=100), nullable=False),
    sa.Column('title', sa.String(length=200), nullable=False),
    sa.Column('start', sa.Float(), nullable=False),
    sa.Column('end', sa.Float(), nullable=False),
    sa.Column('aspect', sa.String(length=10), nullable=False),
    sa.Column('size', sa.Integer(), nullable=False),
    sa.Column('created', sa.Float(), nullable=False),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_renders_project_id'), 'renders', ['project_id'], unique=False)


def downgrade() -> None:
    guard_data_loss(revision)
    op.drop_index(op.f('ix_renders_project_id'), table_name='renders')
    op.drop_table('renders')
    op.drop_table('music')
    op.drop_index(op.f('ix_sessions_user_id'), table_name='sessions')
    op.drop_table('sessions')
    op.drop_index(op.f('ix_projects_unit_id'), table_name='projects')
    op.drop_index(op.f('ix_projects_created'), table_name='projects')
    op.drop_table('projects')
    op.drop_index(op.f('ix_users_unit_id'), table_name='users')
    op.drop_table('users')
    op.drop_index(op.f('ix_usage_events_unit_id'), table_name='usage_events')
    op.drop_index(op.f('ix_usage_events_at'), table_name='usage_events')
    op.drop_table('usage_events')
    op.drop_table('units')
    op.drop_table('settings')
    op.drop_index(op.f('ix_login_failures_key'), table_name='login_failures')
    op.drop_table('login_failures')
    op.drop_index(op.f('ix_jobs_unit_id'), table_name='jobs')
    op.drop_index(op.f('ix_jobs_status'), table_name='jobs')
    op.drop_index(op.f('ix_jobs_queue'), table_name='jobs')
    op.drop_index(op.f('ix_jobs_project_id'), table_name='jobs')
    op.drop_index(op.f('ix_jobs_created'), table_name='jobs')
    op.drop_table('jobs')
