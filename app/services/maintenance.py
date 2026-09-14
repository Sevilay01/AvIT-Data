"""UTC half-open suppression intervals. No probe or alarm transition is fabricated."""

from sqlalchemy import or_, select

from app.models import AlarmSilence, Device, MaintenanceWindow, MonitoringResult


def effective_end(interval):
    return min(interval.ends_at, interval.cancelled_at or interval.ends_at)


def intervals_for(db, alarm, now):
    windows = list(
        db.scalars(
            select(MaintenanceWindow).where(
                MaintenanceWindow.device_id == alarm.device_id,
                MaintenanceWindow.starts_at <= now,
                MaintenanceWindow.ends_at >= alarm.opened_at,
            )
        )
    )
    silences = list(
        db.scalars(
            select(AlarmSilence).where(
                AlarmSilence.alarm_id == alarm.id,
                AlarmSilence.starts_at <= now,
            )
        )
    )
    return [
        i
        for i in windows + silences
        if effective_end(i) > i.starts_at
        and effective_end(i) >= alarm.opened_at
        and (alarm.ended_at is None or i.starts_at <= alarm.ended_at)
    ]


def suppression_reason(db, alarm, now):
    # Query active windows independently of the alarm's ended_at: queued resolution
    # messages must also be suppressed if maintenance starts before dispatch.
    windows = db.scalars(
        select(MaintenanceWindow).where(
            MaintenanceWindow.device_id == alarm.device_id,
            MaintenanceWindow.starts_at <= now,
            MaintenanceWindow.ends_at > now,
            or_(MaintenanceWindow.cancelled_at.is_(None), MaintenanceWindow.cancelled_at > now),
        )
    )
    silences = db.scalars(
        select(AlarmSilence).where(
            AlarmSilence.alarm_id == alarm.id,
            AlarmSilence.starts_at <= now,
            AlarmSilence.ends_at > now,
            or_(AlarmSilence.cancelled_at.is_(None), AlarmSilence.cancelled_at > now),
        )
    )
    labels = [f"Bakım #{w.id}" for w in windows] + [f"Susturma #{s.id}" for s in silences]
    return "; ".join(labels)[:240] or None


def current_measurement(db, alarm):
    return db.scalar(
        select(MonitoringResult)
        .join(Device)
        .where(
            Device.id == alarm.device_id,
            Device.is_active.is_(True),
            MonitoringResult.probe_mode == alarm.probe_mode,
            MonitoringResult.is_current.is_(True),
            MonitoringResult.target_version == Device.target_version,
            MonitoringResult.target_ip == Device.ip_address,
            Device.ip_address == alarm.target_ip,
        )
        .order_by(MonitoringResult.id.desc())
        .limit(1)
    )


def fresh(result, now, settings):
    return bool(
        result
        and 0 <= (now - result.checked_at).total_seconds() <= settings.monitor_interval_seconds * 2
    )
