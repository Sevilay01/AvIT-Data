from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import TypeDecorator

from app.database import Base


def utc_now() -> datetime:
    return datetime.now(UTC)


class UTCDateTime(TypeDecorator[datetime]):
    """Store UTC datetimes as ISO-8601 text because SQLite has no timezone type."""

    impl = String(32)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Any) -> str | None:
        del dialect
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")

    def process_result_value(self, value: str | None, dialect: Any) -> datetime | None:
        del dialect
        if value is None:
            return None
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


UTC_NOW_SQL = text("(strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))")


class Device(Base):
    __tablename__ = "devices"
    __table_args__ = (
        CheckConstraint("length(name) BETWEEN 1 AND 100", name="ck_devices_name_length"),
        CheckConstraint("length(ip_address) BETWEEN 2 AND 45", name="ck_devices_ip_length"),
        CheckConstraint(
            "device_type IS NULL OR length(device_type) <= 50",
            name="ck_devices_type_length",
        ),
        CheckConstraint(
            "location IS NULL OR length(location) <= 100",
            name="ck_devices_location_length",
        ),
        CheckConstraint(
            "description IS NULL OR length(description) <= 500",
            name="ck_devices_description_length",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    ip_address: Mapped[str] = mapped_column(String(45), nullable=False, unique=True)
    device_type: Mapped[str | None] = mapped_column(String(50))
    location: Mapped[str | None] = mapped_column(String(100))
    description: Mapped[str | None] = mapped_column(String(500))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    target_version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), nullable=False, default=utc_now, server_default=UTC_NOW_SQL
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
        server_default=UTC_NOW_SQL,
    )

    monitoring_results: Mapped[list[MonitoringResult]] = relationship(
        back_populates="device",
        cascade="save-update, merge",
        passive_deletes=True,
    )


class MonitoringResult(Base):
    __tablename__ = "monitoring_results"
    __table_args__ = (
        CheckConstraint("probe_mode IN ('mock', 'icmp')", name="ck_results_probe_mode"),
        CheckConstraint(
            "outcome IN ('reply', 'no_reply', 'error')",
            name="ck_results_outcome",
        ),
        CheckConstraint("latency_ms IS NULL OR latency_ms >= 0", name="ck_results_latency"),
        Index("ix_results_device_checked", "device_id", "checked_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    device_id: Mapped[int] = mapped_column(
        ForeignKey("devices.id", ondelete="RESTRICT"), nullable=False
    )
    target_ip: Mapped[str] = mapped_column(String(45), nullable=False)
    probe_mode: Mapped[str] = mapped_column(String(10), nullable=False)
    outcome: Mapped[str] = mapped_column(String(10), nullable=False)
    latency_ms: Mapped[float | None]
    checked_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), nullable=False, default=utc_now, server_default=UTC_NOW_SQL
    )
    error_message: Mapped[str | None] = mapped_column(String(240))
    source: Mapped[str] = mapped_column(String(10), default="manual", server_default="manual")
    target_version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    evaluated: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    is_current: Mapped[bool] = mapped_column(Boolean, default=True, server_default="1")

    device: Mapped[Device] = relationship(back_populates="monitoring_results")


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("length(username) BETWEEN 3 AND 64", name="ck_users_username_length"),
        CheckConstraint("role IN ('admin', 'viewer')", name="ck_users_role"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(10), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), nullable=False, default=utc_now, server_default=UTC_NOW_SQL
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
        server_default=UTC_NOW_SQL,
    )

    sessions: Mapped[list[UserSession]] = relationship(
        back_populates="user", cascade="save-update, merge", passive_deletes=True
    )


class UserSession(Base):
    __tablename__ = "user_sessions"
    __table_args__ = (
        Index("ix_user_sessions_user_active", "user_id", "revoked_at"),
        Index("ix_user_sessions_expires", "expires_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    csrf_token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    last_activity_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime())

    user: Mapped[User | None] = relationship(back_populates="sessions")


class AuditLog(Base):
    __tablename__ = "audit_logs"
    __table_args__ = (
        CheckConstraint("source IN ('web', 'cli', 'scheduler')", name="ck_audit_source"),
        CheckConstraint("outcome IN ('success', 'failure', 'denied')", name="ck_audit_outcome"),
        Index("ix_audit_occurred", "occurred_at"),
        Index("ix_audit_actor", "actor_user_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    occurred_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), nullable=False, default=utc_now, server_default=UTC_NOW_SQL
    )
    actor_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    actor_username: Mapped[str | None] = mapped_column(String(64))
    source: Mapped[str] = mapped_column(String(10), nullable=False)
    action: Mapped[str] = mapped_column(String(80), nullable=False)
    target_type: Mapped[str | None] = mapped_column(String(40))
    target_id: Mapped[str | None] = mapped_column(String(100))
    outcome: Mapped[str] = mapped_column(String(10), nullable=False)
    metadata_json: Mapped[str | None] = mapped_column(Text())


class MonitorState(Base):
    __tablename__ = "monitor_states"
    device_id: Mapped[int] = mapped_column(ForeignKey("devices.id"), primary_key=True)
    probe_mode: Mapped[str] = mapped_column(String(10), primary_key=True)
    target_version: Mapped[int] = mapped_column(Integer, nullable=False)
    no_reply_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    first_no_reply_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class Alarm(Base):
    __tablename__ = "alarms"
    __table_args__ = (
        CheckConstraint("status IN ('open', 'resolved', 'closed')", name="ck_alarm_status"),
        CheckConstraint("probe_mode IN ('mock', 'icmp')", name="ck_alarm_mode"),
        Index(
            "uq_alarm_open",
            "device_id",
            "target_ip",
            "probe_mode",
            unique=True,
            sqlite_where=text("status = 'open'"),
        ),
        Index("ix_alarm_mode_status_opened", "probe_mode", "status", "opened_at"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    device_id: Mapped[int] = mapped_column(ForeignKey("devices.id"), nullable=False)
    target_ip: Mapped[str] = mapped_column(String(45), nullable=False)
    probe_mode: Mapped[str] = mapped_column(String(10), nullable=False)
    alarm_type: Mapped[str] = mapped_column(String(80), default="ICMP yanıtı alınamıyor")
    status: Mapped[str] = mapped_column(String(10), default="open")
    first_no_reply_at: Mapped[datetime] = mapped_column(UTCDateTime())
    opened_at: Mapped[datetime] = mapped_column(UTCDateTime())
    last_observed_at: Mapped[datetime] = mapped_column(UTCDateTime())
    ended_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    end_reason: Mapped[str | None] = mapped_column(String(100))
    acknowledged_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    acknowledged_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
