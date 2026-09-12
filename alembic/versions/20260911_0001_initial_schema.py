"""İlk cihaz ve kontrol sonucu şeması.

Revision ID: 20260911_0001
Revises:
Create Date: 2026-09-11
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260911_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UTC_NOW_SQL = sa.text("(strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))")


def upgrade() -> None:
    op.create_table(
        "devices",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("ip_address", sa.String(length=45), nullable=False),
        sa.Column("device_type", sa.String(length=50), nullable=True),
        sa.Column("location", sa.String(length=100), nullable=True),
        sa.Column("description", sa.String(length=500), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.String(length=32), server_default=UTC_NOW_SQL, nullable=False),
        sa.Column("updated_at", sa.String(length=32), server_default=UTC_NOW_SQL, nullable=False),
        sa.CheckConstraint("length(name) BETWEEN 1 AND 100", name="ck_devices_name_length"),
        sa.CheckConstraint("length(ip_address) BETWEEN 2 AND 45", name="ck_devices_ip_length"),
        sa.CheckConstraint(
            "device_type IS NULL OR length(device_type) <= 50", name="ck_devices_type_length"
        ),
        sa.CheckConstraint(
            "location IS NULL OR length(location) <= 100", name="ck_devices_location_length"
        ),
        sa.CheckConstraint(
            "description IS NULL OR length(description) <= 500",
            name="ck_devices_description_length",
        ),
        sa.UniqueConstraint("ip_address", name="uq_devices_ip_address"),
    )
    op.create_table(
        "monitoring_results",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("device_id", sa.Integer(), nullable=False),
        sa.Column("target_ip", sa.String(length=45), nullable=False),
        sa.Column("probe_mode", sa.String(length=10), nullable=False),
        sa.Column("outcome", sa.String(length=10), nullable=False),
        sa.Column("latency_ms", sa.Float(), nullable=True),
        sa.Column("checked_at", sa.String(length=32), server_default=UTC_NOW_SQL, nullable=False),
        sa.Column("error_message", sa.String(length=240), nullable=True),
        sa.CheckConstraint("probe_mode IN ('mock', 'icmp')", name="ck_results_probe_mode"),
        sa.CheckConstraint(
            "outcome IN ('reply', 'no_reply', 'error')", name="ck_results_outcome"
        ),
        sa.CheckConstraint("latency_ms IS NULL OR latency_ms >= 0", name="ck_results_latency"),
        sa.ForeignKeyConstraint(["device_id"], ["devices.id"], ondelete="RESTRICT"),
    )
    op.create_index(
        "ix_results_device_checked",
        "monitoring_results",
        ["device_id", "checked_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_results_device_checked", table_name="monitoring_results")
    op.drop_table("monitoring_results")
    op.drop_table("devices")
