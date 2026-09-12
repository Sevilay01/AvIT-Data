"""Kullanıcı, sunucu oturumu ve denetim kayıtları.

Revision ID: 20260911_0002
Revises: 20260911_0001
Create Date: 2026-09-11
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260911_0002"
down_revision: str | None = "20260911_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UTC_NOW_SQL = sa.text("(strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))")


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("username", sa.String(length=64), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("role", sa.String(length=10), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.String(length=32), server_default=UTC_NOW_SQL, nullable=False),
        sa.Column("updated_at", sa.String(length=32), server_default=UTC_NOW_SQL, nullable=False),
        sa.CheckConstraint("length(username) BETWEEN 3 AND 64", name="ck_users_username_length"),
        sa.CheckConstraint("role IN ('admin', 'viewer')", name="ck_users_role"),
        sa.UniqueConstraint("username", name="uq_users_username"),
    )
    op.create_table(
        "user_sessions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("csrf_token_hash", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.String(length=32), nullable=False),
        sa.Column("last_activity_at", sa.String(length=32), nullable=False),
        sa.Column("expires_at", sa.String(length=32), nullable=False),
        sa.Column("revoked_at", sa.String(length=32), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("token_hash", name="uq_user_sessions_token_hash"),
    )
    op.create_index(
        "ix_user_sessions_user_active", "user_sessions", ["user_id", "revoked_at"]
    )
    op.create_index("ix_user_sessions_expires", "user_sessions", ["expires_at"])
    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("occurred_at", sa.String(length=32), server_default=UTC_NOW_SQL, nullable=False),
        sa.Column("actor_user_id", sa.Integer(), nullable=True),
        sa.Column("actor_username", sa.String(length=64), nullable=True),
        sa.Column("source", sa.String(length=10), nullable=False),
        sa.Column("action", sa.String(length=80), nullable=False),
        sa.Column("target_type", sa.String(length=40), nullable=True),
        sa.Column("target_id", sa.String(length=100), nullable=True),
        sa.Column("outcome", sa.String(length=10), nullable=False),
        sa.Column("metadata_json", sa.Text(), nullable=True),
        sa.CheckConstraint("source IN ('web', 'cli')", name="ck_audit_source"),
        sa.CheckConstraint(
            "outcome IN ('success', 'failure', 'denied')", name="ck_audit_outcome"
        ),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_audit_occurred", "audit_logs", ["occurred_at"])
    op.create_index("ix_audit_actor", "audit_logs", ["actor_user_id"])


def downgrade() -> None:
    op.drop_index("ix_audit_actor", table_name="audit_logs")
    op.drop_index("ix_audit_occurred", table_name="audit_logs")
    op.drop_table("audit_logs")
    op.drop_index("ix_user_sessions_expires", table_name="user_sessions")
    op.drop_index("ix_user_sessions_user_active", table_name="user_sessions")
    op.drop_table("user_sessions")
    op.drop_table("users")
