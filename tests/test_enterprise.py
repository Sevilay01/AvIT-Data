import asyncio
import socket
import sqlite3
import ssl
from datetime import datetime, timedelta
from email.message import EmailMessage

import pytest
from alembic.config import Config
from sqlalchemy import func, inspect, select, text
from sqlalchemy.exc import IntegrityError

from alembic import command
from app.config import Settings
from app.database import Database
from app.models import (
    Alarm,
    AlarmSilence,
    AuditLog,
    MaintenanceWindow,
    MonitoringResult,
    NotificationOutbox,
)
from app.services.maintenance import suppression_reason
from app.services.notifications import (
    NotificationWorker,
    SMTPFailure,
    SMTPSender,
    delivery_identity,
    enqueue,
)
from tests.conftest import migrate, mutation_headers, sqlite_url
from tests.test_phase3 import FixedProvider, alarms, seed


@pytest.fixture(autouse=True)
def block_real_network(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("Enterprise tests must not connect to a network or run ping")

    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(asyncio, "open_connection", blocked)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", blocked)


class Sender:
    def __init__(self, error=None):
        self.messages = []
        self.error = error

    async def send(self, message, settings):
        self.messages.append(message)
        if self.error:
            raise self.error


def setup_monitor(app, *, mode="mock", sender=None):
    app.state.settings.notification_mode = mode
    if mode == "smtp":
        app.state.settings.smtp_host = "mail.example.invalid"
        app.state.settings.smtp_from = "monitor@example.invalid"
        app.state.settings.smtp_to = "ops@example.invalid"
    provider = FixedProvider()
    if mode == "smtp":
        provider.mode = "icmp"  # Stub only: never a real ICMP provider.
    app.state.monitoring_service.provider = provider
    sender = sender or Sender()
    worker = NotificationWorker(
        app.state.database,
        app.state.settings,
        app.state.clock,
        mock_sender=sender,
        smtp_sender=sender,
    )
    return provider, worker, sender


async def observation(app, device_id):
    return await app.state.monitoring_service.check_and_record(
        app.state.database,
        device_id,
        source="scheduled",
        actor={"source": "scheduler", "actor_username": "system/scheduler"},
        clock=app.state.clock,
        threshold=1,
    )


def rows(app):
    with app.state.database.session_factory() as db:
        return list(db.scalars(select(NotificationOutbox).order_by(NotificationOutbox.id)))


def window(app, device_id, seconds=60, start=None):
    now = app.state.clock()
    mode, target = delivery_identity(app.state.settings)
    with app.state.database.session_factory() as db:
        item = MaintenanceWindow(
            device_id=device_id,
            starts_at=start or now,
            ends_at=now + timedelta(seconds=seconds),
            reason="Planlı çalışma",
            created_at=now,
            created_by=1,
            delivery_mode=mode,
            target_key=target,
        )
        db.add(item)
        db.commit()
        return item.id


def silence(app, alarm_id, seconds=90):
    now = app.state.clock()
    with app.state.database.session_factory() as db:
        item = AlarmSilence(
            alarm_id=alarm_id,
            starts_at=now,
            ends_at=now + timedelta(seconds=seconds),
            reason="İnceleme",
            created_by=1,
        )
        db.add(item)
        db.flush()
        enqueue(
            db,
            db.get(Alarm, alarm_id),
            "current_status",
            f"silence:{item.id}",
            now,
            app.state.settings,
            reason=f"Susturma #{item.id}",
        )
        db.commit()
        return item.id


def test_outbox_and_alarm_rollback_after_enqueue(app, monkeypatch):
    import app.services.notifications as notifications

    setup_monitor(app)
    device = seed(app)
    original = notifications.enqueue

    def fail(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("transaction aborted")

    monkeypatch.setattr(notifications, "enqueue", fail)
    with pytest.raises(RuntimeError, match="transaction aborted"):
        asyncio.run(observation(app, device))
    with app.state.database.session_factory() as db:
        for model in (Alarm, NotificationOutbox, MonitoringResult):
            assert db.scalar(select(func.count()).select_from(model)) == 0


def test_outbox_deduplication_in_code_and_database(app):
    setup_monitor(app)
    asyncio.run(observation(app, seed(app)))
    with app.state.database.session_factory() as db:
        alarm = db.get(Alarm, 1)
        for _ in range(3):
            enqueue(db, alarm, "opened", "opened", app.state.clock(), app.state.settings)
        db.commit()
        row = rows(app)[0]
        assert len(rows(app)) == 1
        duplicate = {
            c.name: getattr(row, c.name)
            for c in NotificationOutbox.__table__.columns
            if c.name != "id"
        }
        db.add(NotificationOutbox(**duplicate))
        with pytest.raises(IntegrityError):
            db.commit()


@pytest.mark.parametrize("error", [TimeoutError("password=secret"), SMTPFailure("cookie=secret")])
def test_smtp_errors_retry_backoff_max_and_safe_logging(app, clock, caplog, error):
    caplog.set_level("INFO", logger="avit.operations")
    _, worker, sender = setup_monitor(app, mode="smtp", sender=Sender(error))
    app.state.settings.notification_max_attempts = 3
    app.state.settings.notification_retry_seconds = 10
    asyncio.run(observation(app, seed(app)))
    asyncio.run(worker.tick())
    assert rows(app)[0].status == "retry"
    assert rows(app)[0].next_attempt_at == clock() + timedelta(seconds=10)
    asyncio.run(worker.tick())
    assert len(sender.messages) == 1
    clock.advance(seconds=10)
    asyncio.run(worker.tick())
    assert rows(app)[0].next_attempt_at == clock() + timedelta(seconds=20)
    clock.advance(seconds=20)
    asyncio.run(worker.tick())
    assert rows(app)[0].status == "failed"
    clock.advance(hours=1)
    asyncio.run(worker.tick())
    assert len(sender.messages) == 3
    assert "secret" not in (rows(app)[0].safe_error + caplog.text)
    assert rows(app)[0].event_id in caplog.text


def test_sender_total_timeout_cancels_without_long_wait(app):
    class Blocked(Sender):
        cancelled = False

        async def send(self, message, settings):
            try:
                await asyncio.Event().wait()
            finally:
                self.cancelled = True

    sender = Blocked()
    _, worker, _ = setup_monitor(app, mode="smtp", sender=sender)
    app.state.settings.notification_timeout_seconds = 0.01
    asyncio.run(observation(app, seed(app)))
    asyncio.run(worker.tick())
    assert sender.cancelled
    assert rows(app)[0].status == "retry"
    assert "zaman aşımı" in rows(app)[0].safe_error


def test_restart_after_acceptance_can_duplicate_stable_message_id(app, clock):
    _, worker, sender = setup_monitor(app, mode="smtp")
    asyncio.run(observation(app, seed(app)))
    claimed = worker.claim()[0]
    row, alarm = worker.preflight(claimed)
    from app.services.notifications import build_message

    # Server accepted DATA but the process died before complete() committed.
    asyncio.run(sender.send(build_message(row, alarm, app.state.settings), app.state.settings))
    replacement = NotificationWorker(
        app.state.database, app.state.settings, clock, smtp_sender=sender
    )
    replacement.recover()
    assert rows(app)[0].status == "retry"
    clock.advance(seconds=30)
    asyncio.run(replacement.tick())
    assert rows(app)[0].status == "accepted"
    assert rows(app)[0].attempts == 2
    assert len(sender.messages) == 2
    assert sender.messages[0]["Message-ID"] == sender.messages[1]["Message-ID"]


def test_recovery_does_not_exceed_attempt_limit(app):
    _, worker, _ = setup_monitor(app, mode="smtp")
    app.state.settings.notification_max_attempts = 1
    asyncio.run(observation(app, seed(app)))
    worker.preflight(worker.claim()[0])
    worker.recover()
    assert rows(app)[0].status == "failed"


@pytest.mark.parametrize("initial", ["off", "mock"])
def test_old_modes_never_send_to_smtp(app, initial):
    provider, worker, sender = setup_monitor(app, mode=initial)
    provider.mode = "icmp"  # Isolates delivery-mode persistence from mock-probe safeguard.
    asyncio.run(observation(app, seed(app)))
    setup_monitor(app, mode="smtp")
    asyncio.run(worker.tick())
    assert not sender.messages
    assert rows(app)[0].status == ("disabled" if initial == "off" else "discarded")


def test_mock_measurement_cannot_send_real_smtp_and_route_changes_discard(app):
    provider, worker, sender = setup_monitor(app, mode="smtp")
    provider.mode = "mock"
    smtp_sender = Sender()
    worker.smtp_sender = smtp_sender
    asyncio.run(observation(app, seed(app)))
    asyncio.run(worker.tick())
    assert rows(app)[0].status == "mock_sent"
    assert not smtp_sender.messages
    assert len(sender.messages) == 1
    provider.mode = "icmp"
    asyncio.run(observation(app, seed(app, "192.0.2.5")))
    app.state.settings.smtp_to = "changed@example.invalid"
    asyncio.run(worker.tick())
    assert rows(app)[1].status == "discarded"
    assert not smtp_sender.messages


def test_same_alarm_delivery_order_survives_retry(app, clock):
    provider, worker, sender = setup_monitor(app, sender=Sender(TimeoutError()))
    device = seed(app)
    asyncio.run(observation(app, device))
    asyncio.run(worker.tick())
    provider.outcome = "reply"
    asyncio.run(observation(app, device))
    asyncio.run(worker.tick())
    assert [r.status for r in rows(app)] == ["retry", "pending"]
    sender.error = None
    clock.advance(seconds=30)
    asyncio.run(worker.tick())
    asyncio.run(worker.tick())
    assert [r.event_type for r in rows(app) if r.status == "mock_sent"] == ["opened", "resolved"]


def test_bounded_parallelism_and_slow_sender_does_not_block_probes(app):
    async def run():
        entered, release = asyncio.Event(), asyncio.Event()
        active = maximum = 0

        class Blocked(Sender):
            async def send(self, message, settings):
                nonlocal active, maximum
                active += 1
                maximum = max(maximum, active)
                if active == 2:
                    entered.set()
                await release.wait()
                active -= 1

        _, worker, _ = setup_monitor(app, sender=Blocked())
        for i in range(1, 5):
            await observation(app, seed(app, f"192.0.2.{i}"))
        task = asyncio.create_task(worker.tick())
        await asyncio.wait_for(entered.wait(), 2)
        # Independent check finishes while both senders are blocked.
        result = await observation(app, seed(app, "192.0.2.6"))
        assert result.outcome == "no_reply"
        assert maximum == 2
        release.set()
        await task
        assert maximum == 2
        assert len([r for r in rows(app) if r.status == "mock_sent"]) == 2

    asyncio.run(run())


def test_maintenance_start_inclusive_end_exclusive_and_preflight(app, clock):
    _, worker, sender = setup_monitor(app)
    device = seed(app)
    asyncio.run(observation(app, device))
    claimed = worker.claim()[0]
    window(app, device, 20, start=clock() + timedelta(seconds=10))
    with app.state.database.session_factory() as db:
        assert suppression_reason(db, db.get(Alarm, 1), clock()) is None
    clock.advance(seconds=10)
    # Maintenance appeared after claim, still checked before sending.
    asyncio.run(worker.dispatch(claimed))
    assert rows(app)[0].status == "suppressed"
    assert not sender.messages
    with app.state.database.session_factory() as db:
        assert suppression_reason(db, db.get(Alarm, 1), clock())
        assert suppression_reason(db, db.get(Alarm, 1), clock() + timedelta(seconds=10)) is None


def test_maintenance_continued_alarm_waits_for_post_end_fresh_measurement(app, clock):
    provider, worker, sender = setup_monitor(app)
    device = seed(app)
    window(app, device)
    asyncio.run(observation(app, device))
    asyncio.run(worker.tick())
    assert not sender.messages
    clock.advance(seconds=60)
    asyncio.run(worker.tick())
    assert not sender.messages  # Prior no_reply is recent, but precedes maintenance end.
    provider.outcome = "error"
    asyncio.run(observation(app, device))
    asyncio.run(worker.tick())
    assert alarms(app)[0].status == "open"
    assert not sender.messages
    provider.outcome = "no_reply"
    asyncio.run(observation(app, device))
    asyncio.run(worker.tick())
    asyncio.run(worker.tick())
    assert len(sender.messages) == 1
    assert [r.event_type for r in rows(app) if r.status == "mock_sent"] == ["current_status"]


def test_entire_lifecycle_inside_maintenance_never_replayed(app, clock):
    provider, worker, sender = setup_monitor(app)
    device = seed(app)
    window(app, device)
    asyncio.run(observation(app, device))
    clock.advance(seconds=5)
    provider.outcome = "reply"
    asyncio.run(observation(app, device))
    clock.advance(seconds=55)
    asyncio.run(worker.tick())  # Worker was down throughout maintenance.
    assert not sender.messages
    assert alarms(app)[0].status == "resolved"
    assert all(r.reconciled_at for r in rows(app))


def test_accepted_open_then_resolved_in_maintenance_sends_one_summary(app, clock):
    provider, worker, sender = setup_monitor(app)
    device = seed(app)
    asyncio.run(observation(app, device))
    asyncio.run(worker.tick())
    window(app, device)
    clock.advance(seconds=10)
    provider.outcome = "reply"
    asyncio.run(observation(app, device))
    asyncio.run(worker.tick())
    assert len(sender.messages) == 1
    clock.advance(seconds=50)
    asyncio.run(worker.tick())
    asyncio.run(worker.tick())
    assert [r.event_type for r in rows(app) if r.status == "mock_sent"] == [
        "opened",
        "resolved_summary",
    ]


def test_overlapping_windows_and_silences_reconcile_only_after_all_end(app, clock):
    _, worker, sender = setup_monitor(app)
    device = seed(app)
    window(app, device, 40)
    window(app, device, 60)
    asyncio.run(observation(app, device))
    silence(app, 1, 90)
    asyncio.run(worker.tick())
    for step in (40, 20, 29):
        clock.advance(seconds=step)
        asyncio.run(observation(app, device))
        asyncio.run(worker.tick())
        assert not sender.messages
    clock.advance(seconds=1)
    asyncio.run(observation(app, device))
    asyncio.run(worker.tick())
    assert len(sender.messages) == 1


def test_cancel_maintenance_uses_actual_end_and_does_not_resolve_alarm(app, clock):
    _, worker, sender = setup_monitor(app)
    device = seed(app)
    window_id = window(app, device)
    asyncio.run(observation(app, device))
    asyncio.run(worker.tick())
    clock.advance(seconds=10)
    with app.state.database.session_factory() as db:
        db.get(MaintenanceWindow, window_id).cancelled_at = clock()
        db.commit()
    asyncio.run(observation(app, device))
    asyncio.run(worker.tick())
    assert alarms(app)[0].status == "open"
    assert len(sender.messages) == 1


def test_original_resolution_after_maintenance_subsumes_summary(app, clock):
    provider, worker, sender = setup_monitor(app)
    device = seed(app)
    asyncio.run(observation(app, device))
    asyncio.run(worker.tick())
    window(app, device)
    asyncio.run(worker.tick())
    clock.advance(seconds=60)
    provider.outcome = "reply"
    asyncio.run(observation(app, device))
    asyncio.run(worker.tick())
    asyncio.run(worker.tick())
    assert len(sender.messages) == 2
    assert [r.event_type for r in rows(app) if r.status == "mock_sent"] == ["opened", "resolved"]


def test_maintenance_mode_snapshot_cannot_promote_old_events(app, clock):
    provider, worker, sender = setup_monitor(app)
    provider.mode = "icmp"
    device = seed(app)
    window(app, device)
    asyncio.run(observation(app, device))
    setup_monitor(app, mode="smtp")
    clock.advance(seconds=60)
    asyncio.run(worker.tick())
    assert not sender.messages
    assert all(r.status == "discarded" for r in rows(app))


def test_viewer_and_csrf_for_every_new_mutation(viewer_client, app, clock):
    device = seed(app)
    body = {
        "device_id": device,
        "starts_at": clock().isoformat(),
        "ends_at": (clock() + timedelta(hours=1)).isoformat(),
        "reason": "Planlı bakım",
    }
    for path in (
        "/api/maintenance",
        "/api/maintenance/1/cancel",
        "/api/alarms/1/silences",
        "/api/silences/1/cancel",
    ):
        assert (
            viewer_client.post(path, json=body, headers=mutation_headers(viewer_client)).status_code
            == 403
        )
    for path in (
        "/api/maintenance",
        "/api/notifications",
        "/api/alarms/1/silences",
        "/api/summary",
    ):
        assert viewer_client.get(path).status_code == 200
    page = viewer_client.get("/").text
    assert 'id="maintenance-form"' not in page
    assert 'id="silence-form"' not in page


def test_admin_operations_validate_time_csrf_audit_and_independent_states(admin_client, app, clock):
    device = seed(app)
    asyncio.run(observation(app, device))
    body = {
        "device_id": device,
        "starts_at": clock().isoformat(),
        "ends_at": (clock() + timedelta(minutes=5)).isoformat(),
        "reason": "Bakım",
    }
    headers = mutation_headers(admin_client)
    for path in (
        "/api/maintenance",
        "/api/maintenance/1/cancel",
        "/api/alarms/1/silences",
        "/api/silences/1/cancel",
    ):
        assert admin_client.post(path, json=body).status_code == 403
        assert (
            admin_client.post(
                path, json=body, headers={**headers, "Origin": "https://evil.invalid"}
            ).status_code
            == 403
        )
    for invalid in (
        {**body, "reason": "   "},
        {**body, "starts_at": "2026-09-11T07:00:00"},
        {**body, "ends_at": clock().isoformat()},
    ):
        assert (
            admin_client.post("/api/maintenance", json=invalid, headers=headers).status_code == 422
        )
    created = admin_client.post("/api/maintenance", json=body, headers=headers)
    assert created.status_code == 201
    assert datetime.fromisoformat(created.json()["starts_at"]).utcoffset() == timedelta(0)
    with app.state.database.engine.connect() as conn:
        assert conn.scalar(text("SELECT starts_at FROM maintenance_windows LIMIT 1")).endswith("Z")
    assert (
        admin_client.post("/api/alarms/1/silences", json=body, headers=headers).status_code == 201
    )
    assert admin_client.post("/api/alarms/1/acknowledge", headers=headers).status_code == 200
    alarm = admin_client.get("/api/alarms/1").json()
    assert alarm["status"] == "open" and alarm["acknowledged_at"]
    assert alarm["silenced"] and alarm["in_maintenance"]
    assert admin_client.post("/api/maintenance/1/cancel", headers=headers).status_code == 200
    assert admin_client.post("/api/silences/1/cancel", headers=headers).status_code == 200
    alarm = admin_client.get("/api/alarms/1").json()
    assert not alarm["silenced"] and not alarm["in_maintenance"] and alarm["status"] == "open"
    with app.state.database.session_factory() as db:
        actions = set(db.scalars(select(AuditLog.action)))
        assert {
            "maintenance.create",
            "maintenance.cancel",
            "alarm.silence",
            "alarm.silence_cancel",
        }.issubset(actions)


def test_health_separates_paused_stale_error_and_no_reply(admin_client, app, clock):
    provider, _, _ = setup_monitor(app)
    device = seed(app)
    asyncio.run(observation(app, device))
    summary = admin_client.get("/api/summary").json()
    assert not summary["running"]
    assert summary["devices"][0]["status"] == "no_reply"
    assert summary["devices"][0]["monitoring_state"] == "paused"
    provider.outcome = "error"
    asyncio.run(observation(app, device))
    summary = admin_client.get("/api/summary").json()
    assert summary["technical_errors"] == 1 and summary["overdue_checks"] == 0
    assert summary["operations"]["heartbeat"]["last_check_completed_at"]
    clock.advance(seconds=121)
    summary = admin_client.get("/api/summary").json()
    assert summary["overdue_checks"] == 1
    assert summary["devices"][0]["status"] == "stale"
    assert alarms(app)[0].status == "open"
    assert admin_client.get("/health").json() == {"status": "ok"}
    assert admin_client.get("/ready").json() == {"status": "ready"}


def test_new_operations_require_authentication(client):
    for path in (
        "/api/maintenance",
        "/api/notifications",
        "/api/alarms/1/silences",
        "/api/summary",
    ):
        assert client.get(path).status_code == 401
    assert set(client.get("/ready").json()) == {"status"}


def test_v3_upgrade_preserves_all_existing_columns_and_downgrade(tmp_path, monkeypatch):
    url = sqlite_url(tmp_path / "old.db")
    migrate(url, monkeypatch, "20260912_0003")
    database = Database(url)
    with database.engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO devices (id,name,ip_address,is_active) VALUES (1,'Eski','192.0.2.2',1)"
            )
        )
        conn.execute(
            text(
                "INSERT INTO users (id,username,password_hash,role,is_active) "
                "VALUES (1,'admin','existing-hash','admin',1)"
            )
        )
        conn.execute(
            text(
                "INSERT INTO monitoring_results "
                "(device_id,target_ip,probe_mode,outcome) VALUES (1,'192.0.2.2','mock','no_reply')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO alarms (id,device_id,target_ip,probe_mode,alarm_type,status,"
                "first_no_reply_at,opened_at,last_observed_at,acknowledged_by) "
                "VALUES (1,1,'192.0.2.2','mock','ICMP','open',"
                "'2026-09-11T07:00:00Z','2026-09-11T07:00:00Z','2026-09-11T07:00:00Z',1)"
            )
        )
        conn.execute(
            text(
                "INSERT INTO audit_logs (source,action,outcome,actor_user_id) "
                "VALUES ('cli','seed','success',1)"
            )
        )
        conn.execute(
            text(
                "INSERT INTO user_sessions "
                "(token_hash,csrf_token_hash,user_id,created_at,last_activity_at,expires_at) "
                "VALUES ('old-token-hash','old-csrf-hash',1,'2026-09-11T07:00:00Z',"
                "'2026-09-11T07:00:00Z','2026-09-11T08:00:00Z')"
            )
        )
        names = [n for n in inspect(conn).get_table_names() if n != "alembic_version"]
        before = {n: conn.execute(text(f"SELECT * FROM {n}")).all() for n in names}
    migrate(url, monkeypatch)
    cfg = Config("alembic.ini")
    cfg.attributes["database_url"] = url
    command.check(cfg)
    for stage in ("upgrade", "downgrade"):
        if stage == "downgrade":
            command.downgrade(cfg, "20260912_0003")
        with database.engine.connect() as conn:
            for name, original in before.items():
                assert conn.execute(text(f"SELECT * FROM {name}")).all() == original
            assert conn.execute(text("PRAGMA integrity_check")).scalar() == "ok"
            assert not conn.execute(text("PRAGMA foreign_key_check")).all()
    command.upgrade(cfg, "head")
    database.dispose()


@pytest.mark.parametrize("tls", ["starttls", "implicit"])
def test_smtp_adapter_verified_tls_and_data_acceptance_without_network(monkeypatch, tls):
    transcript = []
    responses = [b"220 ready\r\n", b"250-hello\r\n", b"250 STARTTLS AUTH PLAIN\r\n"]
    if tls == "starttls":
        responses += [b"220 ready for tls\r\n", b"250 AUTH PLAIN\r\n"]
    responses += [
        b"235 authenticated\r\n",
        b"250 ok\r\n",
        b"250 ok\r\n",
        b"354 data\r\n",
        b"250 accepted\r\n",
    ]

    class Reader:
        async def readline(self):
            return responses.pop(0)

    class Writer:
        closed = False

        def write(self, value):
            transcript.append(value)

        async def drain(self):
            pass

        async def start_tls(self, context, server_hostname):
            assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname
            assert server_hostname == "smtp.example.invalid"
            transcript.append(b"VERIFIED_TLS")

        def close(self):
            self.closed = True

    writer = Writer()

    async def connect(*args, **kwargs):
        if tls == "implicit":
            assert kwargs["ssl"].verify_mode == ssl.CERT_REQUIRED
            assert kwargs["ssl"].check_hostname
            assert kwargs["server_hostname"] == "smtp.example.invalid"
        return Reader(), writer

    monkeypatch.setattr(asyncio, "open_connection", connect)
    settings = Settings(
        _env_file=None,
        notification_mode="smtp",
        smtp_host="smtp.example.invalid",
        smtp_from="from@example.invalid",
        smtp_to="to@example.invalid",
        smtp_tls=tls,
        smtp_username="account",
        smtp_password="test-only",
    )
    message = EmailMessage()
    message.set_content(".dot\nTürkçe", cte="quoted-printable")
    asyncio.run(SMTPSender().send(message, settings))
    assert writer.closed and not responses
    assert b"..dot" in transcript[-1]
    if tls == "starttls":
        assert transcript.index(b"VERIFIED_TLS") < next(
            i for i, part in enumerate(transcript) if part.startswith(b"AUTH")
        )


def test_smtp_does_not_fall_back_if_starttls_missing(monkeypatch):
    class Reader:
        values = iter([b"220 hello\r\n", b"250 no-secure-transport\r\n"])

        async def readline(self):
            return next(self.values)

    class Writer:
        closed = False
        writes = []

        def write(self, value):
            self.writes.append(value)

        async def drain(self):
            pass

        def close(self):
            self.closed = True

    writer = Writer()

    async def connect(*args, **kwargs):
        return Reader(), writer

    monkeypatch.setattr(asyncio, "open_connection", connect)
    with pytest.raises(SMTPFailure):
        asyncio.run(SMTPSender().send(EmailMessage(), Settings(_env_file=None)))
    assert writer.closed and writer.writes == [b"EHLO avit-monitor.local\r\n"]


def test_result_commit_failure_is_recovered_without_process_restart(app, clock, monkeypatch):
    _, worker, sender = setup_monitor(app)
    asyncio.run(observation(app, seed(app)))
    original = worker.complete

    def fail_once(*args):
        monkeypatch.setattr(worker, "complete", original)
        raise RuntimeError("temporary DB failure")

    monkeypatch.setattr(worker, "complete", fail_once)
    with pytest.raises(RuntimeError, match="sonraki turda"):
        asyncio.run(worker.tick())
    assert rows(app)[0].status == "sending"
    asyncio.run(worker.tick())
    assert rows(app)[0].status == "retry"
    clock.advance(seconds=30)
    asyncio.run(worker.tick())
    assert rows(app)[0].status == "mock_sent"
    assert len(sender.messages) == 2  # Uncertain completion can duplicate even in one process.


def test_stale_resolution_summary_waits_for_new_measurement(app, clock):
    provider, worker, sender = setup_monitor(app)
    device = seed(app)
    asyncio.run(observation(app, device))
    asyncio.run(worker.tick())
    window(app, device, 600)
    clock.advance(seconds=10)
    provider.outcome = "reply"
    asyncio.run(observation(app, device))
    asyncio.run(worker.tick())
    clock.advance(seconds=590)
    asyncio.run(worker.tick())
    assert len(sender.messages) == 1
    asyncio.run(observation(app, device))
    asyncio.run(worker.tick())
    assert [r.event_type for r in rows(app) if r.status == "mock_sent"] == [
        "opened",
        "resolved_summary",
    ]


def test_cancel_future_maintenance_does_not_suppress_or_emit_summary(app, clock):
    _, worker, sender = setup_monitor(app)
    device = seed(app)
    wid = window(app, device, 120, start=clock() + timedelta(seconds=60))
    with app.state.database.session_factory() as db:
        db.get(MaintenanceWindow, wid).cancelled_at = clock()
        db.commit()
    asyncio.run(observation(app, device))
    asyncio.run(worker.tick())
    clock.advance(seconds=120)
    asyncio.run(observation(app, device))
    asyncio.run(worker.tick())
    assert len(sender.messages) == 1
    assert len(rows(app)) == 1


def test_demo_preserves_existing_file_and_all_tables_restore(tmp_path, monkeypatch):
    from app.enterprise_demo import run_demo

    # Explicit demo configuration must override dangerous ambient delivery settings.
    monkeypatch.setenv("NOTIFICATION_MODE", "smtp")
    path, restored = tmp_path / "demo.db", tmp_path / "restored.db"
    evidence = asyncio.run(run_demo(path))
    assert evidence["completed_mock_events"] == ["current_status", "resolved_summary"]
    assert not evidence["network_used"]
    before = path.read_bytes()
    with pytest.raises(FileExistsError):
        asyncio.run(run_demo(path))
    assert path.read_bytes() == before
    with sqlite3.connect(path) as source, sqlite3.connect(restored) as target:
        source.backup(target)
        names = [
            row[0] for row in source.execute("SELECT name FROM sqlite_master WHERE type='table'")
        ]
        for name in names:
            assert (
                source.execute(f'SELECT * FROM "{name}"').fetchall()
                == target.execute(f'SELECT * FROM "{name}"').fetchall()
            )
        assert target.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert not target.execute("PRAGMA foreign_key_check").fetchall()
        assert target.execute("SELECT COUNT(*) FROM users WHERE is_active=1").fetchone()[0] == 0


@pytest.mark.parametrize(
    "override",
    [
        {"smtp_tls": "none"},
        {"smtp_from": "from@example.invalid\r\nInjected: x"},
        {"smtp_to": "to@example.invalid\r\n"},
        {"smtp_username": "user", "smtp_password": ""},
    ],
)
def test_invalid_smtp_config_does_not_expose_secret(override):
    args = dict(
        notification_mode="smtp",
        smtp_host="smtp.example.invalid",
        smtp_from="from@example.invalid",
        smtp_to="to@example.invalid",
        smtp_username="user",
        smtp_password="hidden-test-secret",
    )
    args.update(override)
    with pytest.raises(ValueError) as error:
        Settings(_env_file=None, **args)
    assert "hidden-test-secret" not in str(error.value)
