"""Periyodik izleme ve kalıcı alarm yaşam döngüsü."""

import sqlalchemy as sa

from alembic import op

revision = "20260912_0003"
down_revision = "20260911_0002"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "devices", sa.Column("target_version", sa.Integer(), nullable=False, server_default="1")
    )
    op.add_column(
        "monitoring_results",
        sa.Column("source", sa.String(10), nullable=False, server_default="manual"),
    )
    op.add_column(
        "monitoring_results",
        sa.Column("target_version", sa.Integer(), nullable=False, server_default="1"),
    )
    # Historical measurements are not replayed into new alarms.
    op.add_column(
        "monitoring_results",
        sa.Column("evaluated", sa.Boolean(), nullable=False, server_default="0"),
    )
    op.add_column(
        "monitoring_results",
        sa.Column("is_current", sa.Boolean(), nullable=False, server_default="1"),
    )
    op.execute("UPDATE monitoring_results SET evaluated = 1")
    with op.batch_alter_table("audit_logs") as batch:
        batch.drop_constraint("ck_audit_source", type_="check")
        batch.create_check_constraint("ck_audit_source", "source IN ('web', 'cli', 'scheduler')")
    op.create_table(
        "monitor_states",
        sa.Column("device_id", sa.Integer(), sa.ForeignKey("devices.id"), primary_key=True),
        sa.Column("probe_mode", sa.String(10), primary_key=True),
        sa.Column("target_version", sa.Integer(), nullable=False),
        sa.Column("no_reply_count", sa.Integer(), nullable=False),
        sa.Column("first_no_reply_at", sa.String(32)),
    )
    op.create_table(
        "alarms",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("device_id", sa.Integer(), sa.ForeignKey("devices.id"), nullable=False),
        sa.Column("target_ip", sa.String(45), nullable=False),
        sa.Column("probe_mode", sa.String(10), nullable=False),
        sa.Column("alarm_type", sa.String(80), nullable=False),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("first_no_reply_at", sa.String(32), nullable=False),
        sa.Column("opened_at", sa.String(32), nullable=False),
        sa.Column("last_observed_at", sa.String(32), nullable=False),
        sa.Column("ended_at", sa.String(32)),
        sa.Column("end_reason", sa.String(100)),
        sa.Column("acknowledged_at", sa.String(32)),
        sa.Column("acknowledged_by", sa.Integer(), sa.ForeignKey("users.id")),
        sa.CheckConstraint("status IN ('open', 'resolved', 'closed')", name="ck_alarm_status"),
        sa.CheckConstraint("probe_mode IN ('mock', 'icmp')", name="ck_alarm_mode"),
    )
    op.create_index(
        "uq_alarm_open",
        "alarms",
        ["device_id", "target_ip", "probe_mode"],
        unique=True,
        sqlite_where=sa.text("status = 'open'"),
    )
    op.create_index("ix_alarm_mode_status_opened", "alarms", ["probe_mode", "status", "opened_at"])


def downgrade():
    op.drop_table("alarms")
    op.drop_table("monitor_states")
    # Scheduler audit records are retained and mapped into the previous source vocabulary.
    op.execute("UPDATE audit_logs SET source = 'cli' WHERE source = 'scheduler'")
    with op.batch_alter_table("audit_logs") as batch:
        batch.drop_constraint("ck_audit_source", type_="check")
        batch.create_check_constraint("ck_audit_source", "source IN ('web', 'cli')")
    with op.batch_alter_table("monitoring_results") as batch:
        for name in ("source", "target_version", "evaluated", "is_current"):
            batch.drop_column(name)
    with op.batch_alter_table("devices") as batch:
        batch.drop_column("target_version")
