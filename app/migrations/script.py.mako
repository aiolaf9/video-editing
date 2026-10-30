"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
"""
import sqlalchemy as sa
from alembic import op

from app.migrate import guard_data_loss

revision = ${repr(up_revision)}
down_revision = ${repr(down_revision)}
branch_labels = ${repr(branch_labels)}
depends_on = ${repr(depends_on)}


def upgrade() -> None:
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    guard_data_loss(revision)  # hapus baris ini hanya bila downgrade TIDAK menghapus data
    ${downgrades if downgrades else "pass"}
