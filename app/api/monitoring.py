from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query, Request
from sqlalchemy import func, select, text

from app.api.devices import AdminMutation, Authenticated, DatabaseSession
from app.api.operations import operational_health
from app.models import Alarm, AlarmSilence, Device, MaintenanceWindow, MonitoringResult
from app.schemas import MonitoringResultRead
from app.services.alarms import audit_alarm
from app.services.audit import add_audit_log
from app.services.maintenance import effective_end, suppression_reason

router = APIRouter(prefix="/api", tags=["izleme ve alarmlar"])


def alarm_data(alarm, db=None, now=None):
    data = {column.name: getattr(alarm, column.name) for column in Alarm.__table__.columns}
    if db is not None:
        silences = db.scalars(select(AlarmSilence).where(AlarmSilence.alarm_id == alarm.id))
        windows = db.scalars(
            select(MaintenanceWindow).where(MaintenanceWindow.device_id == alarm.device_id)
        )
        data["silenced"] = any(s.starts_at <= now < effective_end(s) for s in silences)
        data["in_maintenance"] = any(w.starts_at <= now < effective_end(w) for w in windows)
        data["suppression_reason"] = suppression_reason(db, alarm, now)
    return data


@router.get("/monitoring/status")
def monitoring_status(request: Request, user: Authenticated):
    scheduler = request.app.state.scheduler
    last_tick = scheduler.last_scheduler_at
    return {
        "running": scheduler.running,
        "probe_mode": request.app.state.settings.monitor_mode,
        "interval_seconds": scheduler.settings.monitor_interval_seconds,
        "last_scan_at": scheduler.last_scan_at,
        "last_error": scheduler.last_error,
        "scheduler_overdue": bool(
            scheduler.running
            and last_tick
            and (request.app.state.clock() - last_tick).total_seconds()
            > scheduler.settings.monitor_interval_seconds * 2
        ),
    }


@router.post("/monitoring/{operation}")
async def control_monitoring(
    operation: Literal["start", "stop"], request: Request, db: DatabaseSession, admin: AdminMutation
):
    actor = {"source": "web", "actor_user_id": admin.user.id, "actor_username": admin.user.username}
    db.commit()
    changed = await getattr(request.app.state.scheduler, operation)()
    if changed:
        add_audit_log(
            db,
            now=request.app.state.clock(),
            action=f"monitoring.{operation}",
            outcome="success",
            **actor,
        )
        db.commit()
    return monitoring_status(request, admin)


@router.get("/alarms")
def list_alarms(
    request: Request,
    db: DatabaseSession,
    user: Authenticated,
    status: Literal["open", "resolved", "closed"] | None = None,
    probe_mode: Literal["mock", "icmp"] | None = None,
    device_id: int | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    conditions = [Alarm.probe_mode == (probe_mode or request.app.state.settings.monitor_mode)]
    if status:
        conditions.append(Alarm.status == status)
    if device_id is not None:
        conditions.append(Alarm.device_id == device_id)
    total = db.scalar(select(func.count()).select_from(Alarm).where(*conditions))
    alarms = db.scalars(
        select(Alarm).where(*conditions).order_by(Alarm.id.desc()).limit(limit).offset(offset)
    )
    return {
        "items": [alarm_data(a, db, request.app.state.clock()) for a in alarms],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


def get_alarm(db, alarm_id):
    alarm = db.get(Alarm, alarm_id)
    if alarm is None:
        raise HTTPException(404, "Alarm bulunamadı.")
    return alarm


@router.get("/alarms/{alarm_id}")
def alarm_detail(alarm_id: int, request: Request, db: DatabaseSession, user: Authenticated):
    return alarm_data(get_alarm(db, alarm_id), db, request.app.state.clock())


@router.post("/alarms/{alarm_id}/acknowledge")
def acknowledge(alarm_id: int, request: Request, db: DatabaseSession, admin: AdminMutation):
    actor = {"source": "web", "actor_user_id": admin.user.id, "actor_username": admin.user.username}
    db.commit()
    db.execute(text("BEGIN IMMEDIATE"))
    db.expire_all()
    alarm = get_alarm(db, alarm_id)
    if alarm.acknowledged_at is None:
        alarm.acknowledged_at = request.app.state.clock()
        alarm.acknowledged_by = actor["actor_user_id"]
        audit_alarm(db, alarm, "acknowledged", alarm.acknowledged_at, actor)
    db.commit()
    return alarm_data(alarm)


@router.get("/summary")
def summary(request: Request, db: DatabaseSession, user: Authenticated):
    mode = request.app.state.settings.monitor_mode
    now = request.app.state.clock()
    stale_after = request.app.state.settings.monitor_interval_seconds * 2
    # Single grouped query for latest observations of the current target and mode.
    latest_ids = (
        select(func.max(MonitoringResult.id))
        .join(Device)
        .where(
            MonitoringResult.probe_mode == mode,
            MonitoringResult.is_current.is_(True),
            MonitoringResult.target_version == Device.target_version,
            MonitoringResult.target_ip == Device.ip_address,
        )
        .group_by(MonitoringResult.device_id)
    )
    latest = {
        r.device_id: r
        for r in db.scalars(select(MonitoringResult).where(MonitoringResult.id.in_(latest_ids)))
    }
    devices = list(db.scalars(select(Device).order_by(Device.id)))
    items = []
    for device in devices:
        result = latest.get(device.id)
        fresh = bool(
            device.is_active
            and result
            and 0 <= (now - result.checked_at).total_seconds() <= stale_after
        )
        items.append(
            {
                "device_id": device.id,
                "name": device.name,
                "is_active": device.is_active,
                "fresh": fresh,
                "status": result.outcome if fresh else "stale",
                "monitoring_state": (
                    "inactive"
                    if not device.is_active
                    else "running"
                    if request.app.state.scheduler.running
                    else "paused"
                ),
                "latest": MonitoringResultRead.model_validate(result) if result else None,
            }
        )
    return {
        **monitoring_status(request, user),
        "server_time": now,
        "active_devices": sum(d.is_active for d in devices),
        "open_alarms": db.scalar(
            select(func.count())
            .select_from(Alarm)
            .where(Alarm.probe_mode == mode, Alarm.status == "open")
        ),
        "devices": items,
        "overdue_checks": sum(d["is_active"] and not d["fresh"] for d in items),
        "technical_errors": sum(d["status"] == "error" for d in items),
        "operations": operational_health(request, db),
    }
