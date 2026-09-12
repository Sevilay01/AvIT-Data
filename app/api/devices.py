from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.dependencies import (
    AuthContext,
    get_db,
    get_monitoring_service,
    require_admin_mutation,
    require_authenticated,
)
from app.models import Device, MonitoringResult
from app.schemas import (
    DeviceCreate,
    DevicePage,
    DeviceRead,
    DeviceUpdate,
    MonitoringResultPage,
    MonitoringResultRead,
)
from app.services.alarms import close_device_alarms
from app.services.audit import add_audit_log
from app.services.monitoring import (
    CheckInProgressError,
    MonitoringService,
    TargetNotAllowedError,
)

router = APIRouter(prefix="/api/devices", tags=["cihazlar"])
DatabaseSession = Annotated[Session, Depends(get_db)]
Monitor = Annotated[MonitoringService, Depends(get_monitoring_service)]
Authenticated = Annotated[AuthContext, Depends(require_authenticated)]
AdminMutation = Annotated[AuthContext, Depends(require_admin_mutation)]


def get_device_or_404(device_id: int, db: Session) -> Device:
    device = db.get(Device, device_id)
    if device is None:
        raise HTTPException(status_code=404, detail="Cihaz bulunamadı.")
    return device


def duplicate_ip_error() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail="Bu IP adresiyle kayıtlı başka bir cihaz var.",
    )


@router.get("", response_model=DevicePage)
def list_devices(
    db: DatabaseSession,
    current_user: Authenticated,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> DevicePage:
    del current_user
    total = db.scalar(select(func.count()).select_from(Device)) or 0
    devices = db.scalars(
        select(Device).order_by(Device.id.desc()).offset(offset).limit(limit)
    ).all()
    return DevicePage(items=list(devices), total=total, limit=limit, offset=offset)


@router.post("", response_model=DeviceRead, status_code=status.HTTP_201_CREATED)
def create_device(payload: DeviceCreate, db: DatabaseSession, admin: AdminMutation) -> Device:
    if db.scalar(select(Device.id).where(Device.ip_address == payload.ip_address)):
        raise duplicate_ip_error()
    device = Device(**payload.model_dump())
    db.add(device)
    try:
        db.flush()
        add_audit_log(
            db,
            now=admin.session.last_activity_at,
            source="web",
            action="device.create",
            outcome="success",
            actor_user_id=admin.user.id,
            actor_username=admin.user.username,
            target_type="device",
            target_id=device.id,
            metadata={"ip_address": device.ip_address},
        )
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise duplicate_ip_error() from exc
    db.refresh(device)
    return device


@router.get("/{device_id}", response_model=DeviceRead)
def get_device(device_id: int, db: DatabaseSession, current_user: Authenticated) -> Device:
    del current_user
    return get_device_or_404(device_id, db)


@router.patch("/{device_id}", response_model=DeviceRead)
def update_device(
    device_id: int,
    payload: DeviceUpdate,
    db: DatabaseSession,
    admin: AdminMutation,
) -> Device:
    db.commit()
    db.execute(text("BEGIN IMMEDIATE"))
    db.expire_all()
    device = get_device_or_404(device_id, db)
    was_active = device.is_active
    changes = payload.model_dump(exclude_unset=True)
    new_ip = changes.get("ip_address")
    if new_ip is not None:
        duplicate_id = db.scalar(
            select(Device.id).where(
                Device.ip_address == new_ip,
                Device.id != device_id,
            )
        )
        if duplicate_id:
            raise duplicate_ip_error()
    if (new_ip is not None and new_ip != device.ip_address) or (
        "is_active" in changes and changes["is_active"] != device.is_active
    ):
        device.target_version += 1
        close_device_alarms(
            db,
            device,
            "target_changed" if new_ip and new_ip != device.ip_address else "device_deactivated",
            admin.session.last_activity_at,
            {
                "source": "web",
                "actor_user_id": admin.user.id,
                "actor_username": admin.user.username,
            },
        )
    for field_name, value in changes.items():
        setattr(device, field_name, value)
    try:
        action = (
            "device.deactivate"
            if was_active and changes.get("is_active") is False
            else "device.update"
        )
        add_audit_log(
            db,
            now=admin.session.last_activity_at,
            source="web",
            action=action,
            outcome="success",
            actor_user_id=admin.user.id,
            actor_username=admin.user.username,
            target_type="device",
            target_id=device.id,
            metadata={"changed_fields": sorted(changes)},
        )
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        if new_ip is not None:
            raise duplicate_ip_error() from exc
        raise HTTPException(status_code=422, detail="Güncelleme verisi geçersiz.") from exc
    db.refresh(device)
    return device


@router.post("/{device_id}/check", response_model=MonitoringResultRead)
async def check_device(
    device_id: int,
    request: Request,
    db: DatabaseSession,
    monitor: Monitor,
    admin: AdminMutation,
) -> MonitoringResult:
    device = get_device_or_404(device_id, db)
    if not device.is_active:
        add_audit_log(
            db,
            now=admin.session.last_activity_at,
            source="web",
            action="device.check_requested",
            outcome="denied",
            actor_user_id=admin.user.id,
            actor_username=admin.user.username,
            target_type="device",
            target_id=device.id,
            metadata={"reason": "inactive_device"},
        )
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Pasif cihazlar manuel olarak kontrol edilemez.",
        )
    add_audit_log(
        db,
        now=admin.session.last_activity_at,
        source="web",
        action="device.check_requested",
        outcome="success",
        actor_user_id=admin.user.id,
        actor_username=admin.user.username,
        target_type="device",
        target_id=device.id,
        metadata={"target_ip": device.ip_address},
    )
    db.commit()
    try:
        return await monitor.check_and_record(
            request.app.state.database,
            device_id,
            source="manual",
            actor={
                "source": "web",
                "actor_user_id": admin.user.id,
                "actor_username": admin.user.username,
            },
            clock=request.app.state.clock,
            threshold=request.app.state.settings.alarm_threshold,
        )
    except (TargetNotAllowedError, CheckInProgressError) as exc:
        add_audit_log(
            db,
            now=admin.session.last_activity_at,
            source="web",
            action="device.check_result",
            outcome="denied",
            actor_user_id=admin.user.id,
            actor_username=admin.user.username,
            target_type="device",
            target_id=device.id,
            metadata={"reason": type(exc).__name__},
        )
        db.commit()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.get("/{device_id}/checks", response_model=MonitoringResultPage)
def list_checks(
    device_id: int,
    db: DatabaseSession,
    current_user: Authenticated,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> MonitoringResultPage:
    del current_user
    get_device_or_404(device_id, db)
    condition = MonitoringResult.device_id == device_id
    total = db.scalar(select(func.count()).select_from(MonitoringResult).where(condition)) or 0
    checks = db.scalars(
        select(MonitoringResult)
        .where(condition)
        .order_by(MonitoringResult.checked_at.desc(), MonitoringResult.id.desc())
        .offset(offset)
        .limit(limit)
    ).all()
    return MonitoringResultPage(items=list(checks), total=total, limit=limit, offset=offset)
