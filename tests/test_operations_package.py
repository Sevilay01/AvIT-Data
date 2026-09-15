import asyncio
import json
import secrets
import sqlite3
from contextlib import closing
from datetime import timedelta
from pathlib import Path

import pytest
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from alembic import command
from app.config import IsolatedSettings
from app.main import create_app
from app.models import (
    Alarm,
    Device,
    MonitoringResult,
    MonitorState,
    NotificationOutbox,
    UserSession,
)
from app.services.backup import BackupError, backup_database, manifest_path, restore_drill, sha256
from app.services.retention import cleanup
from app.services.security import create_session
from scripts.runtime_smoke import table_rows
from tests.conftest import sqlite_url
from tests.test_enterprise import observation, rows, setup_monitor
from tests.test_phase3 import seed


def db_path(app):
    return Path(app.state.database.engine.url.database)


def seed_history(app, clock):
    device = seed(app)
    old = clock() - timedelta(days=100)
    with app.state.database.session_factory() as db:
        for i in range(8):
            db.add(
                MonitoringResult(
                    device_id=device,
                    target_ip="192.0.2.2",
                    probe_mode="mock",
                    outcome="reply",
                    checked_at=old + timedelta(seconds=i),
                    evaluated=True,
                    target_version=1,
                )
            )
        db.commit()
    return device


def test_cleanup_dry_run_is_byte_for_byte_read_only_and_explicit_cutoffs(app, clock):
    seed_history(app, clock)
    path = db_path(app)
    before = sha256(path)
    plan = cleanup(path, measurements_before=clock(), now=clock())
    assert plan["mode"] == "dry-run"
    assert plan["plan"]["measurements"]["candidates"] == 7
    assert plan["plan"]["measurements"]["protected"] == 1
    assert sha256(path) == before
    assert cleanup(path, apply=True, now=clock())["deleted"] == {"measurements": 0, "sessions": 0}
    assert sha256(path) == before


def test_retention_keeps_alarm_evidence_unevaluated_latest_streak_sessions_and_queue(app, clock):
    device = seed_history(app, clock)
    setup_monitor(app)
    asyncio.run(observation(app, device))  # Open alarm, current state, pending outbox.
    with app.state.database.session_factory() as db:
        pending = MonitoringResult(
            device_id=device,
            target_ip="192.0.2.2",
            probe_mode="mock",
            outcome="no_reply",
            checked_at=clock() - timedelta(days=50),
            evaluated=False,
        )
        db.add(pending)
        db.add(
            MonitoringResult(
                device_id=device,
                target_ip="192.0.2.2",
                probe_mode="mock",
                outcome="no_reply",
                checked_at=clock(),
                evaluated=True,
            )
        )
        # A streak in a separate generation must keep its own first evidence.
        other = Device(name="Pending streak", ip_address="192.0.2.5")
        db.add(other)
        db.flush()
        for i in range(2):
            db.add(
                MonitoringResult(
                    device_id=other.id,
                    target_ip=other.ip_address,
                    probe_mode="mock",
                    outcome="no_reply",
                    evaluated=True,
                    checked_at=clock() - timedelta(days=10, seconds=-i),
                )
            )
        db.add(
            MonitorState(
                device_id=other.id,
                probe_mode="mock",
                target_version=1,
                no_reply_count=2,
                first_no_reply_at=clock() - timedelta(days=10),
            )
        )
        active, _ = create_session(db, user_id=1, now=clock(), lifetime=timedelta(hours=8))
        expired, _ = create_session(
            db, user_id=1, now=clock() - timedelta(days=100), lifetime=timedelta(hours=1)
        )
        revoked, _ = create_session(db, user_id=1, now=clock(), lifetime=timedelta(hours=8))
        revoked.revoked_at = clock() - timedelta(days=5)
        db.commit()
        ids = active.id, expired.id, revoked.id, pending.id
    clock.advance(seconds=1)
    before_queue = table_rows(db_path(app))["notification_outbox"]
    report = cleanup(
        db_path(app),
        measurements_before=clock(),
        sessions_before=clock(),
        apply=True,
        batch_size=2,
        now=clock(),
    )
    assert report["deleted"] == {"measurements": 8, "sessions": 2}
    with app.state.database.session_factory() as db:
        assert db.get(UserSession, ids[0]) is not None
        assert db.get(UserSession, ids[1]) is None and db.get(UserSession, ids[2]) is None
        assert db.get(MonitoringResult, ids[3]) is not None
        alarm = db.scalar(select(Alarm))
        evidence = db.scalars(
            select(MonitoringResult).where(
                MonitoringResult.device_id == device, MonitoringResult.checked_at >= alarm.opened_at
            )
        ).all()
        assert len(evidence) == 2
    assert table_rows(db_path(app))["notification_outbox"] == before_queue


def test_batch_limit_interruption_and_resume_only_commit_completed_batches(app, clock):
    seed_history(app, clock)
    path = db_path(app)
    first = cleanup(
        path, measurements_before=clock(), apply=True, batch_size=2, max_batches=1, now=clock()
    )
    assert first["deleted"]["measurements"] == 2
    assert first["remaining"]["measurements"]["candidates"] == 5

    def interrupted(*args):
        raise KeyboardInterrupt()

    with pytest.raises(KeyboardInterrupt):
        cleanup(
            path,
            measurements_before=clock(),
            apply=True,
            batch_size=2,
            on_batch=interrupted,
            now=clock(),
        )
    assert (
        cleanup(path, measurements_before=clock(), now=clock())["plan"]["measurements"][
            "candidates"
        ]
        == 3
    )
    assert (
        cleanup(path, measurements_before=clock(), apply=True, now=clock())["deleted"][
            "measurements"
        ]
        == 3
    )
    assert (
        cleanup(path, measurements_before=clock(), apply=True, now=clock())["deleted"][
            "measurements"
        ]
        == 0
    )


def test_cleanup_and_replayed_events_do_not_duplicate_completed_notifications(app, clock):
    from app.services.alarms import evaluate_result
    from app.services.notifications import enqueue

    device = seed_history(app, clock)
    _, worker, sender = setup_monitor(app)
    asyncio.run(observation(app, device))
    asyncio.run(worker.tick())
    assert len(sender.messages) == 1
    clock.advance(days=100)
    for _ in range(2):
        cleanup(db_path(app), measurements_before=clock(), apply=True, now=clock())
        with app.state.database.session_factory() as db:
            alarm = db.scalar(select(Alarm))
            enqueue(db, alarm, "opened", "opened", clock(), app.state.settings)
            result = db.scalar(select(MonitoringResult).order_by(MonitoringResult.id.desc()))
            evaluate_result(db, result, 1, {"source": "cli"}, app.state.settings)
            db.commit()
        asyncio.run(worker.tick())
    assert len(rows(app)) == 1 and rows(app)[0].status == "mock_sent"
    assert len(sender.messages) == 1


@pytest.mark.parametrize(
    "status",
    [
        "pending",
        "sending",
        "retry",
        "suppressed",
        "failed",
        "disabled",
        "accepted",
        "mock_sent",
        "discarded",
    ],
)
def test_every_outbox_state_remains_a_durable_ledger(app, clock, status):
    setup_monitor(app)
    asyncio.run(observation(app, seed(app)))
    with app.state.database.session_factory() as db:
        db.scalar(select(NotificationOutbox)).status = status
        db.commit()
    before = table_rows(db_path(app))
    clock.advance(days=100)
    cleanup(
        db_path(app), measurements_before=clock(), sessions_before=clock(), apply=True, now=clock()
    )
    after = table_rows(db_path(app))
    assert before["notification_outbox"] == after["notification_outbox"]
    assert before["audit_logs"] == after["audit_logs"]


def test_timestamp_cutoff_preserves_equal_second_and_mixed_fractional_storage(app, clock):
    seed_history(app, clock)
    with app.state.database.engine.begin() as conn:
        for id_, value in (
            (1, "2026-09-11T07:00:00Z"),
            (2, "2026-09-11T07:00:00.001Z"),
            (3, "2026-09-11T06:59:59.999999Z"),
        ):
            conn.execute(
                text("UPDATE monitoring_results SET checked_at=:value WHERE id=:id"),
                {"value": value, "id": id_},
            )
    cleanup(db_path(app), measurements_before=clock(), apply=True, now=clock())
    with app.state.database.session_factory() as db:
        assert db.get(MonitoringResult, 1) and db.get(MonitoringResult, 2)
        assert db.get(MonitoringResult, 3) is None


def test_online_backup_includes_committed_wal_and_never_overwrites(app, clock, tmp_path):
    seed_history(app, clock)
    target = tmp_path / "backup.db"
    with closing(sqlite3.connect(db_path(app))) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("UPDATE devices SET name='WAL commit'")
        conn.commit()
        result = backup_database(db_path(app), target)
        assert result["integrity"] == "ok" and result["sha256"] == sha256(target)
        assert table_rows(target)["devices"] == table_rows(db_path(app))["devices"]
    before, manifest = target.read_bytes(), manifest_path(target).read_bytes()
    with pytest.raises(BackupError, match="zaten var"):
        backup_database(db_path(app), target)
    assert target.read_bytes() == before and manifest_path(target).read_bytes() == manifest


def test_backup_timeout_and_invalid_source_publish_nothing(app, tmp_path):
    target = tmp_path / "failed.db"
    with closing(sqlite3.connect(db_path(app))) as lock:
        lock.execute("BEGIN EXCLUSIVE")
        with pytest.raises(BackupError, match="süre sınırı"):
            backup_database(db_path(app), target, timeout=0.05)
        lock.rollback()
    invalid = tmp_path / "invalid.db"
    invalid.write_bytes(b"not sqlite")
    with pytest.raises(BackupError):
        backup_database(invalid, target)
    assert not target.exists() and not manifest_path(target).exists()
    assert not list(tmp_path.glob("*.partial"))


def test_backup_rejects_foreign_key_violation(app, tmp_path):
    with closing(sqlite3.connect(db_path(app))) as conn:
        conn.execute("INSERT INTO monitor_states VALUES (987, 'mock', 1, 0, NULL)")
        conn.commit()
    target = tmp_path / "invalid-fk.db"
    with pytest.raises(BackupError, match="foreign key"):
        backup_database(db_path(app), target)
    assert not target.exists()


def test_manifest_publish_failure_removes_only_owned_files(app, tmp_path, monkeypatch):
    import app.services.backup as service

    target, sentinel = tmp_path / "failed.db", tmp_path / "keep.txt"
    sentinel.write_text("keep")
    real_link = service.os.link

    def fail_manifest(source, dest):
        if str(dest).endswith(".manifest.json"):
            raise OSError("injected manifest write failure")
        real_link(source, dest)

    monkeypatch.setattr(service.os, "link", fail_manifest)
    with pytest.raises(BackupError):
        backup_database(db_path(app), target)
    assert not target.exists() and not manifest_path(target).exists()
    assert sentinel.read_text() == "keep"


def test_restore_rejects_missing_or_tampered_backup_manifest(app, tmp_path):
    backup, target = tmp_path / "backup.db", tmp_path / "restore.db"
    backup_database(db_path(app), backup)
    manifest = manifest_path(backup)
    metadata = json.loads(manifest.read_text())
    manifest.unlink()
    with pytest.raises(BackupError, match="manifest"):
        restore_drill(backup, target)
    metadata["sha256"] = "0" * 64
    manifest.write_text(json.dumps(metadata))
    with pytest.raises(BackupError, match="SHA-256"):
        restore_drill(backup, target)
    assert not target.exists()


def test_restored_app_quarantine_overrides_real_modes_and_revokes_sessions(
    app, clock, tmp_path, monkeypatch
):
    from app.services.notifications import SMTPSender

    setup_monitor(app)
    asyncio.run(observation(app, seed(app)))
    with app.state.database.session_factory() as db:
        row = db.scalar(select(NotificationOutbox))
        row.delivery_mode = "smtp"
        create_session(db, user_id=1, now=clock(), lifetime=timedelta(hours=8))
        db.commit()
    backup, target = tmp_path / "backup.db", tmp_path / "restored.db"
    backup_database(db_path(app), backup)
    restore_drill(backup, target)
    before = table_rows(target)
    with closing(sqlite3.connect(target)) as conn:
        assert (
            conn.execute("SELECT COUNT(*) FROM user_sessions WHERE revoked_at IS NULL").fetchone()[
                0
            ]
            == 0
        )

    async def forbidden(*args):
        pytest.fail("Restored copy must never send SMTP")

    monkeypatch.setattr(SMTPSender, "send", forbidden)
    settings = IsolatedSettings(
        database_url=sqlite_url(target),
        monitor_mode="icmp",
        notification_mode="smtp",
        smtp_host="smtp.example.invalid",
        smtp_from="from@example.invalid",
        smtp_to="to@example.invalid",
    )
    restored = create_app(settings, clock=clock)
    with TestClient(restored) as client:
        assert client.get("/ready").status_code == 200
        assert restored.state.settings.monitor_mode == "mock"
        assert restored.state.settings.notification_mode == "off"
        assert not restored.state.scheduler.running
        assert restored.state.notification_worker.recovery_hold
        assert table_rows(target)["notification_outbox"] == before["notification_outbox"]


def test_v4_upgrade_preserves_all_rows_and_model_matches(tmp_path):
    from app.enterprise_demo import run_demo

    path = tmp_path / "v4.db"
    asyncio.run(run_demo(path))
    config = Config("alembic.ini")
    config.attributes["database_url"] = sqlite_url(path)
    command.downgrade(config, "20260913_0004")
    before = table_rows(path)
    command.upgrade(config, "head")
    command.check(config)
    after = table_rows(path)
    for name in before:
        if name != "alembic_version":
            assert before[name] == after[name]
    assert after["recovery_guard"] == []


def test_cleanup_empty_retained_report_has_unknown_coverage(app, clock, admin_client):
    device = seed_history(app, clock)
    cleanup(db_path(app), measurements_before=clock(), apply=True, now=clock())
    response = admin_client.get(f"/api/devices/{device}/metrics").json()
    assert response["summary"]["total"] == 0
    assert response["summary"]["response_rate"] is None
    assert response["summary"]["avg_rtt_ms"] is None
    assert response["data_scope"]["complete_period"] is False
    assert "saklanan" in response["data_scope"]["notice"]


def test_demo_settings_ignore_environment_and_dotenv(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("MONITOR_MODE=icmp\nNOTIFICATION_MODE=smtp\n")
    monkeypatch.setenv("SMTP_PASSWORD", secrets.token_urlsafe(32))
    monkeypatch.setenv("DATABASE_URL", "sqlite:///real.db")
    settings = IsolatedSettings()
    assert settings.monitor_mode == "mock" and settings.notification_mode == "off"
    assert not settings.smtp_password.get_secret_value()


@pytest.mark.parametrize("offset_us", [0, 1, 1000, 999999])
def test_retention_preserves_the_exact_microsecond_cutoff(app, clock, offset_us):
    seed_history(app, clock)
    cutoff = clock() + timedelta(microseconds=offset_us)
    with app.state.database.session_factory() as db:
        for result_id, delta in ((1, -1), (2, 0), (3, 1)):
            db.get(MonitoringResult, result_id).checked_at = (
                cutoff + timedelta(microseconds=delta)
            )
        db.commit()
    report = cleanup(
        db_path(app),
        measurements_before=cutoff,
        apply=True,
        now=clock() + timedelta(seconds=2),
    )
    assert report["deleted"]["measurements"] == 5
    with app.state.database.session_factory() as db:
        assert db.get(MonitoringResult, 1) is None
        assert db.get(MonitoringResult, 2) is not None
        assert db.get(MonitoringResult, 3) is not None


def test_session_retention_preserves_exact_expiry_and_revocation_boundaries(app, clock):
    cutoff = clock() + timedelta(microseconds=1)
    ids = {}
    with app.state.database.session_factory() as db:
        for kind in ("expiry", "revocation"):
            for delta in (-1, 0, 1):
                session, _ = create_session(
                    db, user_id=1, now=clock(), lifetime=timedelta(hours=1)
                )
                boundary = cutoff + timedelta(microseconds=delta)
                if kind == "expiry":
                    session.expires_at = boundary
                else:
                    session.revoked_at = boundary
                db.flush()
                ids[kind, delta] = session.id
        db.commit()
    report = cleanup(
        db_path(app), sessions_before=cutoff, apply=True,
        now=clock() + timedelta(seconds=1),
    )
    assert report["deleted"]["sessions"] == 2
    with app.state.database.session_factory() as db:
        for (_kind, delta), session_id in ids.items():
            assert (db.get(UserSession, session_id) is None) == (delta == -1)
