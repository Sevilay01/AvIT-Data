"""Authenticated maintenance, silence, notification history and operational health."""

from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import AwareDatetime, BaseModel, Field, field_validator, model_validator
from sqlalchemy import func, select, text

from app.api.devices import AdminMutation, Authenticated, DatabaseSession
from app.models import (
    Alarm,
    AlarmSilence,
    Device,
    MaintenanceWindow,
    MonitoringHeartbeat,
    NotificationOutbox,
)
from app.services.audit import add_audit_log
from app.services.event_logging import log_event
from app.services.maintenance import effective_end
from app.services.notifications import ACTIVE, delivery_identity, enqueue

router = APIRouter(prefix="/api", tags=["bakım ve işletim"])


class SilenceCreate(BaseModel):
    ends_at: AwareDatetime
    reason: str = Field(min_length=1, max_length=500)

    @field_validator("reason")
    @classmethod
    def nonblank(cls, value):
        value = value.strip()
        if not value:
            raise ValueError("Gerekçe boş olamaz.")
        return value


class MaintenanceCreate(SilenceCreate):
    device_id: int = Field(gt=0)
    starts_at: AwareDatetime

    @model_validator(mode="after")
    def valid_period(self):
        if self.ends_at <= self.starts_at:
            raise ValueError("Bitiş başlangıçtan sonra olmalı.")
        return self


def serialize(row, now=None):
    data = {
        c.name: getattr(row, c.name)
        for c in row.__table__.columns
        if c.name not in {"target_key", "delivery_mode", "activated_at"}
    }
    if now is not None:
        data["active"] = row.starts_at <= now < effective_end(row)
    return data


def begin_write(db):
    db.commit()
    db.execute(text("BEGIN IMMEDIATE"))
    db.expire_all()


def record_change(db, request, admin, row, action):
    operation_id = str(uuid4())
    add_audit_log(
        db,
        now=request.app.state.clock(),
        source="web",
        action=action,
        outcome="success",
        actor_user_id=admin.user.id,
        actor_username=admin.user.username,
        target_type=row.__tablename__,
        target_id=row.id,
        metadata={"operation_id": operation_id},
    )
    db.commit()
    log_event(action, operation_id, status="success")


@router.get("/maintenance")
def maintenance_list(
    request: Request,
    db: DatabaseSession,
    user: Authenticated,
    device_id: int | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    where = [] if device_id is None else [MaintenanceWindow.device_id == device_id]
    rows = db.scalars(
        select(MaintenanceWindow)
        .where(*where)
        .order_by(MaintenanceWindow.id.desc())
        .limit(limit)
        .offset(offset)
    )
    return {
        "items": [serialize(r, request.app.state.clock()) for r in rows],
        "total": db.scalar(select(func.count()).select_from(MaintenanceWindow).where(*where)),
        "limit": limit,
        "offset": offset,
        "timezone": "UTC",
    }


@router.post("/maintenance", status_code=201)
def create_maintenance(
    payload: MaintenanceCreate,
    request: Request,
    db: DatabaseSession,
    admin: AdminMutation,
):
    now = request.app.state.clock()
    if payload.ends_at <= now:
        raise HTTPException(422, "Bitiş gelecekte olmalı.")
    begin_write(db)
    if db.get(Device, payload.device_id) is None:
        raise HTTPException(404, "Cihaz bulunamadı.")
    mode, target = delivery_identity(request.app.state.settings)
    row = MaintenanceWindow(
        device_id=payload.device_id,
        starts_at=max(now, payload.starts_at),
        ends_at=payload.ends_at,
        reason=payload.reason,
        created_at=now,
        created_by=admin.user.id,
        delivery_mode=mode,
        target_key=target,
    )
    db.add(row)
    db.flush()
    record_change(db, request, admin, row, "maintenance.create")
    return serialize(row, now)


@router.post("/maintenance/{window_id}/cancel")
def cancel_maintenance(
    window_id: int,
    request: Request,
    db: DatabaseSession,
    admin: AdminMutation,
):
    begin_write(db)
    row = db.get(MaintenanceWindow, window_id)
    if row is None:
        raise HTTPException(404, "Bakım bulunamadı.")
    if row.cancelled_at is None:
        row.cancelled_at = request.app.state.clock()
        record_change(db, request, admin, row, "maintenance.cancel")
    return serialize(row, request.app.state.clock())


@router.get("/alarms/{alarm_id}/silences")
def silence_list(alarm_id: int, request: Request, db: DatabaseSession, user: Authenticated):
    rows = db.scalars(
        select(AlarmSilence)
        .where(AlarmSilence.alarm_id == alarm_id)
        .order_by(AlarmSilence.id.desc())
        .limit(100)
    )
    return {"items": [serialize(r, request.app.state.clock()) for r in rows]}


@router.post("/alarms/{alarm_id}/silences", status_code=201)
def silence_alarm(
    alarm_id: int,
    payload: SilenceCreate,
    request: Request,
    db: DatabaseSession,
    admin: AdminMutation,
):
    now = request.app.state.clock()
    if payload.ends_at <= now:
        raise HTTPException(422, "Susturma bitişi gelecekte olmalı.")
    begin_write(db)
    alarm = db.get(Alarm, alarm_id)
    if alarm is None:
        raise HTTPException(404, "Alarm bulunamadı.")
    if alarm.status != "open":
        raise HTTPException(409, "Yalnız açık alarm için yeni susturma tanımlanabilir.")
    row = AlarmSilence(
        alarm_id=alarm_id,
        starts_at=now,
        ends_at=payload.ends_at,
        reason=payload.reason,
        created_by=admin.user.id,
    )
    db.add(row)
    db.flush()
    enqueue(
        db,
        alarm,
        "current_status",
        f"silence:{row.id}",
        now,
        request.app.state.settings,
        reason=f"Susturma #{row.id}",
    )
    record_change(db, request, admin, row, "alarm.silence")
    return serialize(row, now)


@router.post("/silences/{silence_id}/cancel")
def cancel_silence(
    silence_id: int,
    request: Request,
    db: DatabaseSession,
    admin: AdminMutation,
):
    begin_write(db)
    row = db.get(AlarmSilence, silence_id)
    if row is None:
        raise HTTPException(404, "Susturma bulunamadı.")
    if row.cancelled_at is None:
        row.cancelled_at = request.app.state.clock()
        record_change(db, request, admin, row, "alarm.silence_cancel")
    return serialize(row, request.app.state.clock())


@router.get("/notifications")
def notification_history(
    db: DatabaseSession,
    user: Authenticated,
    alarm_id: int | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    where = [] if alarm_id is None else [NotificationOutbox.alarm_id == alarm_id]
    rows = db.scalars(
        select(NotificationOutbox)
        .where(*where)
        .order_by(NotificationOutbox.id.desc())
        .limit(limit)
        .offset(offset)
    )
    return {
        "items": [{**serialize(row), "delivery_mode": row.delivery_mode} for row in rows],
        "total": db.scalar(select(func.count()).select_from(NotificationOutbox).where(*where)),
        "offset": offset,
        "limit": limit,
    }


def operational_health(request, db):
    heartbeat = db.get(MonitoringHeartbeat, 1)
    worker = request.app.state.notification_worker
    counts = dict(
        db.execute(
            select(NotificationOutbox.status, func.count()).group_by(NotificationOutbox.status)
        ).all()
    )
    oldest = db.scalar(
        select(func.min(NotificationOutbox.created_at)).where(NotificationOutbox.status.in_(ACTIVE))
    )
    return {
        "heartbeat": serialize(heartbeat) if heartbeat else None,
        "notification_mode": request.app.state.settings.notification_mode,
        "recovery_hold": getattr(request.app.state, "recovery_hold", False),
        "pending_notifications": sum(counts.get(s, 0) for s in ACTIVE),
        "failed_notifications": counts.get("failed", 0),
        "retry_notifications": counts.get("retry", 0),
        "oldest_pending_at": oldest,
        "awaiting_reconciliation": db.scalar(
            select(func.count())
            .select_from(NotificationOutbox)
            .where(
                NotificationOutbox.status == "suppressed",
                NotificationOutbox.reconciled_at.is_(None),
            )
        ),
        "notification_worker_running": worker.running,
        "notification_last_tick_at": worker.last_tick_at,
        "notification_worker_error": worker.last_error,
    }
