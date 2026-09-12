import asyncio
import subprocess
import sys

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from app.config import Settings
from app.demo import prepare_demo
from app.main import create_app
from app.models import Alarm, AuditLog, Device, MonitoringResult, MonitorState
from app.services.alarms import evaluate_result
from app.services.monitoring import CheckInProgressError, MockProbeProvider, ProbeResult
from app.services.process_lock import DatabaseProcessLock
from app.services.scheduler import SYSTEM_ACTOR, MonitoringScheduler
from tests.conftest import ORIGIN, migrate, mutation_headers, sqlite_url
from tests.test_api import create_device


class FixedProvider:
    mode = "mock"

    def __init__(self, outcome="no_reply"):
        self.outcome = outcome

    async def probe(self, target):
        return ProbeResult(self.outcome)


def seed(app, ip="192.0.2.2", active=True):
    with app.state.database.session_factory() as db:
        device = Device(name="Test", ip_address=ip, is_active=active)
        db.add(device)
        db.commit()
        return device.id


async def measure(app, device_id, source="scheduled"):
    return await app.state.monitoring_service.check_and_record(
        app.state.database,
        device_id,
        source=source,
        actor=SYSTEM_ACTOR,
        clock=app.state.clock,
        threshold=3,
    )


def alarms(app):
    with app.state.database.session_factory() as db:
        return list(db.scalars(select(Alarm).order_by(Alarm.id)))


def test_lifecycle_threshold_dedup_error_and_mode_isolation(app, clock):
    device_id = seed(app)
    provider = FixedProvider()
    app.state.monitoring_service.provider = provider

    async def run():
        first = await measure(app, device_id)
        await measure(app, device_id)
        assert alarms(app) == []
        with app.state.database.session_factory() as db:
            stale_copy = db.get(MonitoringResult, first.id)
            stale_copy.evaluated = False
            evaluate_result(db, stale_copy, 3, SYSTEM_ACTOR)
            db.rollback()
        assert alarms(app) == []
        clock.advance(seconds=1)
        await measure(app, device_id)
        alarm_id = alarms(app)[0].id
        await measure(app, device_id)
        assert len(alarms(app)) == 1
        provider.outcome = "error"
        await measure(app, device_id)
        assert alarms(app)[0].status == "open"
        with app.state.database.session_factory() as db:
            assert db.get(MonitorState, (device_id, "mock")).no_reply_count == 0
        provider.mode = "icmp"
        provider.outcome = "reply"
        await measure(app, device_id)
        assert alarms(app)[0].status == "open"
        provider.mode = "mock"
        await measure(app, device_id)
        assert alarms(app)[0].id == alarm_id
        assert alarms(app)[0].status == "resolved"

    asyncio.run(run())


def test_error_breaks_pending_streak(app):
    device_id = seed(app)
    provider = FixedProvider()
    app.state.monitoring_service.provider = provider

    async def run():
        await measure(app, device_id)
        await measure(app, device_id)
        provider.outcome = "error"
        await measure(app, device_id)
        provider.outcome = "no_reply"
        await measure(app, device_id)
        assert not alarms(app)

    asyncio.run(run())


def test_acknowledgment_csrf_roles_and_pagination(admin_client, app):
    device = create_device(admin_client, ip_address="192.0.2.2")
    for _ in range(3):
        assert (
            admin_client.post(
                f"/api/devices/{device['id']}/check", headers=mutation_headers(admin_client)
            ).status_code
            == 200
        )
    page = admin_client.get("/api/alarms?status=open&limit=1").json()
    alarm_id = page["items"][0]["id"]
    assert page["total"] == 1
    assert admin_client.get("/api/alarms?offset=1").json()["items"] == []
    for path in [
        "/api/monitoring/start",
        "/api/monitoring/stop",
        f"/api/alarms/{alarm_id}/acknowledge",
    ]:
        assert admin_client.post(path).status_code == 403
        assert (
            admin_client.post(
                path,
                headers={
                    "Origin": "http://evil.invalid",
                    "X-CSRF-Token": mutation_headers(admin_client)["X-CSRF-Token"],
                },
            ).status_code
            == 403
        )
    response = admin_client.post(
        f"/api/alarms/{alarm_id}/acknowledge", headers=mutation_headers(admin_client)
    )
    assert response.json()["status"] == "open"
    assert response.json()["acknowledged_by"] is not None
    assert (
        admin_client.post(
            f"/api/alarms/{alarm_id}/acknowledge", headers=mutation_headers(admin_client)
        ).json()
        == response.json()
    )
    with app.state.database.session_factory() as db:
        assert (
            db.scalar(
                select(func.count())
                .select_from(AuditLog)
                .where(AuditLog.action == "alarm.acknowledged")
            )
            == 1
        )


@pytest.mark.parametrize(
    "path", ["/api/monitoring/start", "/api/monitoring/stop", "/api/alarms/1/acknowledge"]
)
def test_viewer_cannot_control(viewer_client, path):
    assert viewer_client.post(path, headers=mutation_headers(viewer_client)).status_code == 403
    assert viewer_client.get("/api/summary").status_code == 200
    assert viewer_client.get("/api/alarms").status_code == 200


@pytest.mark.parametrize(
    "path", ["/api/summary", "/api/alarms", "/api/alarms/1", "/api/monitoring/status"]
)
def test_anonymous_reads_rejected(client, path):
    assert client.get(path).status_code == 401


@pytest.mark.parametrize(
    "changes,reason",
    [({"ip_address": "192.0.2.8"}, "target_changed"), ({"is_active": False}, "device_deactivated")],
)
def test_admin_closure(admin_client, app, changes, reason):
    device = create_device(admin_client, ip_address="192.0.2.2")
    for _ in range(3):
        admin_client.post(
            f"/api/devices/{device['id']}/check", headers=mutation_headers(admin_client)
        )
    assert (
        admin_client.patch(
            f"/api/devices/{device['id']}", json=changes, headers=mutation_headers(admin_client)
        ).status_code
        == 200
    )
    assert alarms(app)[0].status == "closed"
    assert alarms(app)[0].end_reason == reason


def test_edit_during_check_and_same_device_collision(app):
    device_id = seed(app)

    async def run():
        entered, release = asyncio.Event(), asyncio.Event()

        class BlockingProvider:
            mode = "mock"

            async def probe(self, target):
                entered.set()
                await release.wait()
                return ProbeResult("no_reply")

        app.state.monitoring_service.provider = BlockingProvider()
        task = asyncio.create_task(measure(app, device_id, "manual"))
        await entered.wait()
        with pytest.raises(CheckInProgressError):
            await measure(app, device_id)
        # This write succeeds while the probe is suspended: no network-spanning write lock.
        with app.state.database.session_factory() as db:
            device = db.get(Device, device_id)
            device.ip_address = "192.0.2.8"
            device.target_version += 1
            db.commit()
        release.set()
        result = await task
        assert result.target_ip == "192.0.2.2"
        assert not result.is_current
        assert not alarms(app)
        with app.state.database.session_factory() as db:
            assert db.get(MonitorState, (device_id, "mock")) is None

    asyncio.run(run())


def test_scheduler_idempotency_inactive_isolation_and_cancellation(app):
    seed(app)
    seed(app, "192.0.2.1")
    inactive_id = seed(app, "192.0.2.3", False)

    async def run():
        entered = asyncio.Event()

        class BlockingProvider:
            mode = "mock"

            async def probe(self, target):
                if target == "192.0.2.1":
                    raise RuntimeError("mechanism failure")
                entered.set()
                await asyncio.Event().wait()

        app.state.monitoring_service.provider = BlockingProvider()
        scheduler = MonitoringScheduler(
            app.state.database, app.state.monitoring_service, app.state.settings, app.state.clock
        )
        assert await scheduler.start()
        first_task = scheduler.task
        assert not await scheduler.start()
        assert scheduler.task is first_task
        await entered.wait()
        assert await scheduler.stop()
        assert not await scheduler.stop()
        assert not scheduler.running
        with app.state.database.session_factory() as db:
            results = list(db.scalars(select(MonitoringResult)))
            assert all(r.device_id != inactive_id for r in results)
            assert [r.outcome for r in results] == ["error"]
            assert results[0].source == "scheduled"
        assert not app.state.monitoring_service._active_devices

    asyncio.run(run())


def test_restart_preserves_open_and_ack_but_resets_pending(app, clock):
    device_id = seed(app)
    app.state.monitoring_service.provider = FixedProvider()
    asyncio.run(measure(app, device_id))
    asyncio.run(measure(app, device_id))
    asyncio.run(measure(app, device_id))
    with app.state.database.session_factory() as db:
        alarm = db.scalar(select(Alarm))
        alarm.acknowledged_at = clock()
        alarm.acknowledged_by = 1
        db.commit()
    restarted = create_app(app.state.settings, clock=clock)
    with TestClient(restarted, base_url=ORIGIN):
        assert not restarted.state.scheduler.running
        assert alarms(restarted)[0].status == "open"
        assert alarms(restarted)[0].acknowledged_by == 1
        with restarted.state.database.session_factory() as db:
            assert db.get(MonitorState, (device_id, "mock")).no_reply_count == 0


def test_database_rejects_duplicate_open_alarm(app):
    device_id = seed(app)
    app.state.monitoring_service.provider = FixedProvider()
    for _ in range(3):
        asyncio.run(measure(app, device_id))
    first = alarms(app)[0]
    with app.state.database.session_factory() as db:
        db.add(
            Alarm(
                device_id=device_id,
                target_ip=first.target_ip,
                probe_mode="mock",
                status="open",
                first_no_reply_at=first.first_no_reply_at,
                opened_at=first.opened_at,
                last_observed_at=first.last_observed_at,
            )
        )
        with pytest.raises(IntegrityError):
            db.commit()


def test_other_process_is_rejected_and_lock_released(tmp_path):
    url = sqlite_url(tmp_path / "locked.db")
    lock = DatabaseProcessLock(url)
    script = (
        "import sys; from app.services.process_lock import DatabaseProcessLock; "
        "lock=DatabaseProcessLock(sys.argv[1]); lock.acquire(); lock.release()"
    )
    lock.acquire()
    try:
        blocked = subprocess.run(
            [sys.executable, "-c", script, url], capture_output=True, timeout=10
        )
        assert blocked.returncode != 0
        assert b"RuntimeError" in blocked.stderr
    finally:
        lock.release()
    assert (
        subprocess.run(
            [sys.executable, "-c", script, url], capture_output=True, timeout=10
        ).returncode
        == 0
    )


def test_summary_freshness_and_modes(admin_client, app, clock):
    device = create_device(admin_client)
    assert admin_client.get("/api/summary").json()["devices"][0]["status"] == "stale"
    admin_client.post(f"/api/devices/{device['id']}/check", headers=mutation_headers(admin_client))
    assert admin_client.get("/api/summary").json()["devices"][0]["status"] == "reply"
    clock.advance(seconds=121)
    assert admin_client.get("/api/summary").json()["devices"][0]["status"] == "stale"
    app.state.settings.monitor_mode = "icmp"
    assert admin_client.get("/api/summary").json()["devices"][0]["latest"] is None


def test_demo_is_exclusive_and_sequence_deterministic(tmp_path):
    path = tmp_path / "demo.db"
    prepare_demo(path)
    before = path.read_bytes()
    with pytest.raises(FileExistsError):
        prepare_demo(path)
    assert path.read_bytes() == before

    async def run():
        provider = MockProbeProvider(demo=True)
        assert [(await provider.probe("192.0.2.2")).outcome for _ in range(7)] == [
            "reply",
            "no_reply",
            "no_reply",
            "no_reply",
            "no_reply",
            "reply",
            "reply",
        ]
        assert (await provider.probe("192.0.2.1")).outcome == "reply"
        assert (await provider.probe("192.0.2.3")).outcome == "error"

    asyncio.run(run())


def test_v2_upgrade_preserves_all_data(tmp_path, monkeypatch):
    from app.database import Database

    url = sqlite_url(tmp_path / "v2.db")
    migrate(url, monkeypatch, "20260911_0002")
    database = Database(url)
    with database.engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO devices (id,name,ip_address,is_active) VALUES (1,'old','192.0.2.1',1)"
            )
        )
        conn.execute(
            text(
                "INSERT INTO monitoring_results "
                "(device_id,target_ip,probe_mode,outcome) "
                "VALUES (1,'192.0.2.1','mock','reply')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO users (id,username,password_hash,role,is_active) "
                "VALUES (1,'olduser','preserve-this-hash','admin',1)"
            )
        )
        conn.execute(
            text(
                "INSERT INTO audit_logs (actor_user_id,source,action,outcome) "
                "VALUES (1,'cli','user.create','success')"
            )
        )
        before = {
            table: conn.execute(text(f"SELECT * FROM {table}")).all()
            for table in ("devices", "monitoring_results", "users", "audit_logs")
        }
    database.dispose()
    migrate(url, monkeypatch)
    with database.engine.connect() as conn:
        for table, rows in before.items():
            after = conn.execute(text(f"SELECT * FROM {table}")).all()
            assert [tuple(row[: len(rows[0])]) for row in after] == [tuple(r) for r in rows]
        assert not conn.execute(text("PRAGMA foreign_key_check")).all()
    database.dispose()


def test_short_interval_only_mock():
    assert Settings(monitor_mode="mock", monitor_interval_seconds=5).monitor_interval_seconds == 5
    with pytest.raises(ValueError):
        Settings(monitor_mode="icmp", monitor_interval_seconds=5)


def test_result_counter_and_alarm_roll_back_together(app, monkeypatch):
    import app.services.alarms as alarm_service

    device_id = seed(app)
    app.state.monitoring_service.provider = FixedProvider()
    for _ in range(2):
        asyncio.run(measure(app, device_id))

    def fail_audit(*args, **kwargs):
        raise RuntimeError("Audit write failed")

    monkeypatch.setattr(alarm_service, "audit_alarm", fail_audit)
    with pytest.raises(RuntimeError, match="Audit write failed"):
        asyncio.run(measure(app, device_id))
    with app.state.database.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(MonitoringResult)) == 2
        assert db.get(MonitorState, (device_id, "mock")).no_reply_count == 2
        assert db.scalar(select(func.count()).select_from(Alarm)) == 0


def test_stop_resets_streak_and_invalidates_inflight_manual(app):
    device_id = seed(app)
    app.state.monitoring_service.provider = FixedProvider()

    async def run():
        await measure(app, device_id)
        await measure(app, device_id)
        entered, release = asyncio.Event(), asyncio.Event()

        class Blocked:
            mode = "mock"

            async def probe(self, target):
                entered.set()
                await release.wait()
                return ProbeResult("no_reply")

        app.state.monitoring_service.provider = Blocked()
        task = asyncio.create_task(measure(app, device_id, "manual"))
        await entered.wait()
        scheduler = MonitoringScheduler(
            app.state.database, app.state.monitoring_service, app.state.settings, app.state.clock
        )
        await scheduler.stop()
        release.set()
        assert not (await task).is_current
        assert not alarms(app)
        with app.state.database.session_factory() as db:
            assert db.get(MonitorState, (device_id, "mock")).no_reply_count == 0

    asyncio.run(run())


def test_global_limit_is_shared_by_manual_and_scheduled_checks(app):
    from app.services.monitoring import MonitoringService

    ids = [seed(app, f"192.0.2.{i}") for i in range(1, 5)]

    async def run():
        entered, release = asyncio.Event(), asyncio.Event()
        active = 0
        maximum = 0

        class Blocked:
            mode = "mock"

            async def probe(self, target):
                nonlocal active, maximum
                active += 1
                maximum = max(maximum, active)
                if active == 2:
                    entered.set()
                await release.wait()
                active -= 1
                return ProbeResult("reply")

        app.state.monitoring_service = MonitoringService(Blocked(), 2)
        tasks = [
            asyncio.create_task(measure(app, i, "manual" if i % 2 else "scheduled")) for i in ids
        ]
        await entered.wait()
        assert maximum == 2
        release.set()
        await asyncio.gather(*tasks)
        assert maximum == 2

    asyncio.run(run())


def test_application_lifespan_rejects_second_process(client, app):
    script = (
        "import asyncio,sys; from app.config import Settings; "
        "from app.main import create_app; "
        "app=create_app(Settings(database_url=sys.argv[1])); "
        "asyncio.run(app.router.lifespan_context(app).__aenter__())"
    )
    result = subprocess.run(
        [sys.executable, "-c", script, app.state.settings.database_url],
        capture_output=True,
        timeout=15,
    )
    assert result.returncode != 0
    assert b"RuntimeError" in result.stderr
    assert client.get("/health").status_code == 200


def test_scheduled_no_reply_uses_system_actor_without_per_measurement_audit(app):
    device_id = seed(app)
    app.state.monitoring_service.provider = FixedProvider()
    scheduler = MonitoringScheduler(
        app.state.database, app.state.monitoring_service, app.state.settings, app.state.clock
    )
    for _ in range(4):
        asyncio.run(scheduler.scan_once())
    assert len(alarms(app)) == 1
    with app.state.database.session_factory() as db:
        logs = list(db.scalars(select(AuditLog).where(AuditLog.source == "scheduler")))
        assert len(logs) == 1
        assert logs[0].action == "alarm.opened"
        assert logs[0].actor_username == "system/scheduler"
        assert logs[0].actor_user_id is None
        assert (
            db.scalar(
                select(func.count())
                .select_from(MonitoringResult)
                .where(MonitoringResult.device_id == device_id)
            )
            == 4
        )


def test_cancelled_ping_is_cleaned_up_without_result(monkeypatch):
    from app.services.monitoring import PingAdapter

    async def run():
        entered = asyncio.Event()

        class Process:
            returncode = None
            killed = False

            async def communicate(self):
                entered.set()
                if not self.killed:
                    await asyncio.Event().wait()
                return b"", b""

            def kill(self):
                self.killed = True

        process = Process()

        async def spawn(*args, **kwargs):
            return process

        monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
        task = asyncio.create_task(PingAdapter("Windows").probe("127.0.0.1", 2))
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert process.killed

    asyncio.run(run())
