"""Alarm transitions; caller owns the short write transaction."""

from sqlalchemy import select, update

from app.models import Alarm, Device, MonitoringResult, MonitorState
from app.services.audit import add_audit_log


def audit_alarm(db, alarm, action, now, actor):
    add_audit_log(
        db,
        now=now,
        action=f"alarm.{action}",
        outcome="success",
        target_type="alarm",
        target_id=alarm.id,
        **actor,
    )


def reset_streaks(db):
    db.execute(update(MonitorState).values(no_reply_count=0, first_no_reply_at=None))


def close_device_alarms(db, device, reason, now, actor):
    for alarm in db.scalars(
        select(Alarm).where(Alarm.device_id == device.id, Alarm.status == "open")
    ):
        alarm.status = "closed"
        alarm.ended_at = now
        alarm.end_reason = reason
        audit_alarm(db, alarm, "closed", now, actor)
    db.execute(
        update(MonitorState)
        .where(MonitorState.device_id == device.id)
        .values(no_reply_count=0, first_no_reply_at=None)
    )


def evaluate_result(db, result: MonitoringResult, threshold: int, actor):
    if result.evaluated:
        return
    claimed = db.execute(
        update(MonitoringResult)
        .where(MonitoringResult.id == result.id, MonitoringResult.evaluated.is_(False))
        .values(evaluated=True)
    )
    if not claimed.rowcount:
        return
    result.evaluated = True
    device = db.get(Device, result.device_id)
    if (
        not result.is_current
        or not device.is_active
        or device.target_version != result.target_version
        or device.ip_address != result.target_ip
    ):
        result.is_current = False
        return
    state = db.get(MonitorState, (device.id, result.probe_mode))
    if state is None:
        state = MonitorState(
            device_id=device.id,
            probe_mode=result.probe_mode,
            target_version=device.target_version,
            no_reply_count=0,
        )
        db.add(state)
    if state.target_version != device.target_version:
        state.target_version = device.target_version
        state.no_reply_count = 0
        state.first_no_reply_at = None
    alarm = db.scalar(
        select(Alarm).where(
            Alarm.device_id == device.id,
            Alarm.target_ip == result.target_ip,
            Alarm.probe_mode == result.probe_mode,
            Alarm.status == "open",
        )
    )
    now = result.checked_at
    if result.outcome == "no_reply":
        if state.no_reply_count == 0:
            state.first_no_reply_at = now
        state.no_reply_count += 1
        if alarm:
            alarm.last_observed_at = now
        elif state.no_reply_count >= threshold:
            alarm = Alarm(
                device_id=device.id,
                target_ip=result.target_ip,
                probe_mode=result.probe_mode,
                first_no_reply_at=state.first_no_reply_at,
                opened_at=now,
                last_observed_at=now,
                status="open",
            )
            db.add(alarm)
            db.flush()
            audit_alarm(db, alarm, "opened", now, actor)
    else:
        state.no_reply_count = 0
        state.first_no_reply_at = None
        if result.outcome == "reply" and alarm:
            alarm.status = "resolved"
            alarm.last_observed_at = now
            alarm.ended_at = now
            alarm.end_reason = "reply_received"
            audit_alarm(db, alarm, "resolved", now, actor)
