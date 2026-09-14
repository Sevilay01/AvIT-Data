"""Transactional notifications, maintenance, silences and monitoring heartbeat."""

import sqlalchemy as sa

from alembic import op

revision = "20260913_0004"
down_revision = "20260912_0003"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "maintenance_windows",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("device_id", sa.Integer(), sa.ForeignKey("devices.id"), nullable=False),
        sa.Column("starts_at", sa.String(32), nullable=False),
        sa.Column("ends_at", sa.String(32), nullable=False),
        sa.Column("reason", sa.String(500), nullable=False),
        sa.Column("created_at", sa.String(32), nullable=False),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("cancelled_at", sa.String(32)),
        sa.Column("activated_at", sa.String(32)),
        sa.Column("delivery_mode", sa.String(10), nullable=False),
        sa.Column("target_key", sa.String(64), nullable=False),
        sa.CheckConstraint("ends_at > starts_at", name="ck_maintenance_period"),
    )
    op.create_index(
        "ix_maintenance_device_period", "maintenance_windows", ["device_id", "starts_at", "ends_at"]
    )
    op.create_table(
        "alarm_silences",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("alarm_id", sa.Integer(), sa.ForeignKey("alarms.id"), nullable=False),
        sa.Column("starts_at", sa.String(32), nullable=False),
        sa.Column("ends_at", sa.String(32), nullable=False),
        sa.Column("reason", sa.String(500), nullable=False),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("cancelled_at", sa.String(32)),
        sa.CheckConstraint("ends_at > starts_at", name="ck_silence_period"),
    )
    op.create_index(
        "ix_silence_alarm_period", "alarm_silences", ["alarm_id", "starts_at", "ends_at"]
    )
    op.create_table(
        "notification_outbox",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("event_id", sa.String(36), nullable=False),
        sa.Column("alarm_id", sa.Integer(), sa.ForeignKey("alarms.id"), nullable=False),
        sa.Column("event_type", sa.String(30), nullable=False),
        sa.Column("channel", sa.String(10), nullable=False),
        sa.Column("target_key", sa.String(64), nullable=False),
        sa.Column("delivery_mode", sa.String(10), nullable=False),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.String(32), nullable=False),
        sa.Column("next_attempt_at", sa.String(32)),
        sa.Column("claimed_at", sa.String(32)),
        sa.Column("completed_at", sa.String(32)),
        sa.Column("safe_error", sa.String(240)),
        sa.Column("suppression_reason", sa.String(240)),
        sa.Column("reconciled_at", sa.String(32)),
        sa.UniqueConstraint("event_id", "channel", "target_key", name="uq_outbox_event_target"),
        sa.CheckConstraint("attempts >= 0", name="ck_outbox_attempts"),
        sa.CheckConstraint("delivery_mode IN ('off','mock','smtp')", name="ck_outbox_mode"),
        sa.CheckConstraint(
            "status IN ('disabled','pending','sending','retry','accepted','mock_sent',"
            "'failed','suppressed','discarded')",
            name="ck_outbox_status",
        ),
    )
    op.create_index("ix_outbox_due", "notification_outbox", ["status", "next_attempt_at"])
    op.create_index("ix_outbox_alarm_order", "notification_outbox", ["alarm_id", "id"])
    op.create_table(
        "monitoring_heartbeat",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("process_started_at", sa.String(32)),
        sa.Column("last_scheduler_at", sa.String(32)),
        sa.Column("last_scan_completed_at", sa.String(32)),
        sa.Column("last_check_completed_at", sa.String(32)),
    )


def downgrade():
    for table in (
        "monitoring_heartbeat",
        "notification_outbox",
        "alarm_silences",
        "maintenance_windows",
    ):
        op.drop_table(table)
