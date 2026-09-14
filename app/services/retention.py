"""Opt-in, bounded SQLite retention. Outbox is also the deduplication ledger."""

from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import and_, delete, func, or_, select, text
from sqlalchemy.orm import Session

from app.database import Database
from app.models import (
    Alarm,
    AlarmSilence,
    AuditLog,
    Device,
    MaintenanceWindow,
    MonitoringHeartbeat,
    MonitoringResult,
    MonitorState,
    NotificationOutbox,
    RecoveryGuard,
    User,
    UserSession,
)


def result_protections():
    r = MonitoringResult
    # Keep the high-water ID in every device/mode/target generation, including
    # stale generations. This also prevents SQLite from reusing a deleted max ID.
    latest = select(func.max(r.id)).group_by(
        r.device_id, r.probe_mode, r.target_version, r.target_ip, r.is_current
    )
    evidence = (
        select(Alarm.id)
        .where(
            Alarm.device_id == r.device_id,
            Alarm.probe_mode == r.probe_mode,
            Alarm.target_ip == r.target_ip,
            Alarm.status == "open",
            func.julianday(r.checked_at) >= func.julianday(Alarm.first_no_reply_at),
        )
        .exists()
    )
    streak = (
        select(MonitorState.device_id)
        .where(
            MonitorState.device_id == r.device_id,
            MonitorState.probe_mode == r.probe_mode,
            MonitorState.target_version == r.target_version,
            MonitorState.no_reply_count > 0,
            func.julianday(r.checked_at) >= func.julianday(MonitorState.first_no_reply_at),
        )
        .exists()
    )
    return {
        "unevaluated": r.evaluated.is_(False),
        "latest_per_target": r.id.in_(latest),
        "open_alarm_evidence": evidence,
        "pending_streak_evidence": streak,
    }


def count(db, model, *conditions):
    return db.scalar(select(func.count()).select_from(model).where(*conditions))


def retention_predicates(*, measurements_before=None, sessions_before=None):
    reasons = result_protections()
    old = func.julianday(MonitoringResult.checked_at) < func.julianday(measurements_before)
    measurements = and_(old, ~or_(*reasons.values()))
    # Do not infer expiry from the current idle-time setting: it can change.
    # Only already revoked or absolutely expired sessions qualify, with grace
    # measured from that end time, never creation/last-activity time.
    ended = or_(
        func.julianday(UserSession.revoked_at) < func.julianday(sessions_before),
        func.julianday(UserSession.expires_at) < func.julianday(sessions_before),
    )
    return {MonitoringResult: measurements, UserSession: ended}, reasons, old


def retention_plan(db, *, measurements_before=None, sessions_before=None):
    """The caller supplies a read snapshot or IMMEDIATE write transaction."""
    predicates, reasons, old = retention_predicates(
        measurements_before=measurements_before, sessions_before=sessions_before
    )
    entries = {}
    for name, model, cutoff, condition in (
        ("measurements", MonitoringResult, measurements_before, predicates[MonitoringResult]),
        ("sessions", UserSession, sessions_before, predicates[UserSession]),
    ):
        total = count(db, model)
        candidates = count(db, model, condition) if cutoff else 0
        entries[name] = {
            "cutoff_utc": cutoff.isoformat() if cutoff else None,
            "total": total,
            "candidates": candidates,
            "protected": total - candidates,
        }
    entries["measurements"]["protection_reasons"] = {
        name: count(db, MonitoringResult, old, rule) if measurements_before else 0
        for name, rule in reasons.items()
    }
    entries["measurements"]["reason_counts_overlap"] = True
    entries["sessions"]["reason"] = "Aktif oturumlar ve kesimden sonra bitenler korunur."
    for name, model, reason in (
        (
            "outbox",
            NotificationOutbox,
            "Tüm durumlar: kalıcı tekilleştirme ve bakım uzlaştırması için gerekli.",
        ),
        ("audit", AuditLog, "Bu pakette audit silme politikası etkin değil."),
        ("alarms", Alarm, "Alarm yaşam döngüsü ve FK referansları korunur."),
        ("monitor_states", MonitorState, "Güncel izleme sayaçları korunur."),
        ("devices", Device, "Envanter ve FK referansları korunur."),
        ("users", User, "Hesaplar ve FK atıfları korunur."),
        ("maintenance", MaintenanceWindow, "Bakım geçmişi ve uzlaştırma için korunur."),
        ("silences", AlarmSilence, "Susturma geçmişi ve uzlaştırma için korunur."),
        ("heartbeat", MonitoringHeartbeat, "İzleme sağlığı korunur."),
        ("recovery_guard", RecoveryGuard, "Geri yükleme karantinası korunur."),
    ):
        entries[name] = {"candidates": 0, "protected": count(db, model), "reason": reason}
    return entries, predicates


def cleanup(
    path: Path,
    *,
    measurements_before: datetime | None = None,
    sessions_before: datetime | None = None,
    apply=False,
    batch_size=500,
    max_batches=20,
    now=None,
    on_batch=None,
):
    now = now or datetime.now(UTC)
    for cutoff in (measurements_before, sessions_before):
        if cutoff and (cutoff.tzinfo is None or cutoff > now):
            raise ValueError("Kesim zamanı saat dilimli ve gelecekte olmayan bir zaman olmalı.")
    if not 1 <= batch_size <= 5000 or not 1 <= max_batches <= 10000:
        raise ValueError("Parti boyutu 1–5000, parti sayısı 1–10000 olmalı.")
    path = path.resolve(strict=True)
    # mode=ro + query_only makes dry-run physically read-only, including CLI audit.
    import sqlite3

    from sqlalchemy import create_engine

    if apply:
        database = Database(f"sqlite:///{path.as_posix()}")
        engine = database.engine
    else:
        engine = create_engine(
            "sqlite://", creator=lambda: sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
        )
    deleted = {"measurements": 0, "sessions": 0}
    options = dict(measurements_before=measurements_before, sessions_before=sessions_before)
    try:
        with Session(engine) as db:
            if not apply:
                db.execute(text("PRAGMA query_only=ON"))
            db.execute(text("BEGIN"))
            plan, _ = retention_plan(db, **options)
            db.rollback()
            batches = 0
            if apply:
                for model, name, cutoff in (
                    (MonitoringResult, "measurements", measurements_before),
                    (UserSession, "sessions", sessions_before),
                ):
                    if not cutoff:
                        continue
                    while batches < max_batches:
                        db.execute(text("BEGIN IMMEDIATE"))
                        # Re-evaluate guards under the write lock: a web request
                        # cannot change alarm/session state between selection/deletion.
                        predicates, _, _ = retention_predicates(**options)
                        ids = list(
                            db.scalars(
                                select(model.id)
                                .where(predicates[model])
                                .order_by(model.id)
                                .limit(batch_size)
                            )
                        )
                        if not ids:
                            db.rollback()
                            break
                        db.execute(delete(model).where(model.id.in_(ids)))
                        db.commit()
                        batches += 1
                        deleted[name] += len(ids)
                        if on_batch:
                            on_batch(name, len(ids))
            db.execute(text("BEGIN"))
            remaining, _ = retention_plan(db, **options)
            db.rollback()
        return {
            "mode": "apply" if apply else "dry-run",
            "database": str(path),
            "as_of_utc": now.isoformat(),
            "plan": plan,
            "deleted": deleted,
            "batches": batches,
            "remaining": remaining,
            "automatic_cleanup": False,
        }
    finally:
        engine.dispose()
