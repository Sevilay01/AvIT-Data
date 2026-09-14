"""Persist a recovery quarantine independently of environment settings."""

import sqlalchemy as sa

from alembic import op

revision = "20260914_0005"
down_revision = "20260913_0004"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "recovery_guard",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("restored_at", sa.String(32), nullable=False),
    )


def downgrade():
    op.drop_table("recovery_guard")
