"""Network-free 0.5.0 demo, exclusive new DB, simulated time, no reusable credentials."""

import argparse
import asyncio
import json
import secrets
from datetime import timedelta
from pathlib import Path

from sqlalchemy import select

from app.config import IsolatedSettings
from app.database import Database
from app.demo import prepare_demo
from app.models import Alarm, MaintenanceWindow, NotificationOutbox, utc_now
from app.services.monitoring import MonitoringService, ProbeResult
from app.services.notifications import NotificationWorker, delivery_identity
from app.services.users import create_user
from app.version import VERSION


class DemoClock:
    def __init__(self):
        self.now = utc_now() - timedelta(seconds=80)

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += timedelta(seconds=seconds)


class DemoProbe:
    mode = "mock"
    outcome = "no_reply"

    async def probe(self, target_ip):
        return ProbeResult(self.outcome, 1.0 if self.outcome == "reply" else None)


async def run_demo(path):
    url = prepare_demo(path)
    # Both values are explicit, regardless of a user's environment or .env.
    settings = IsolatedSettings(
        _env_file=None,
        database_url=url,
        monitor_mode="mock",
        notification_mode="mock",
        mock_demo=False,
    )
    database = Database(url)
    clock, provider = DemoClock(), DemoProbe()
    monitor = MonitoringService(provider, 1, settings)
    worker = NotificationWorker(database, settings, clock)
    try:
        with database.session_factory() as db:
            author = create_user(
                db,
                username="demo-author",
                password=secrets.token_urlsafe(32),
                role="admin",
                now=clock(),
            )
            # Attribution only. There is no default account usable for login.
            author.is_active = False
            db.commit()

        def maintenance(seconds):
            mode, target = delivery_identity(settings)
            with database.session_factory() as db:
                db.add(
                    MaintenanceWindow(
                        device_id=2,
                        starts_at=clock(),
                        ends_at=clock() + timedelta(seconds=seconds),
                        reason="Ağsız demo bakımı",
                        created_at=clock(),
                        created_by=author.id,
                        delivery_mode=mode,
                        target_key=target,
                    )
                )
                db.commit()

        async def measure():
            return await monitor.check_and_record(
                database,
                2,
                source="manual",
                clock=clock,
                threshold=1,
                actor={
                    "source": "cli",
                    "actor_username": "demo-author",
                    "actor_user_id": author.id,
                },
            )

        maintenance(60)
        await measure()
        await worker.tick()
        clock.advance(60)
        await worker.tick()  # No post-maintenance measurement: no assumed outage.
        await measure()
        await worker.tick()  # One current-status mock notification.
        maintenance(20)
        clock.advance(10)
        provider.outcome = "reply"
        await measure()
        await worker.tick()
        clock.advance(10)
        await worker.tick()  # One resolution summary, no historical burst.
        with database.session_factory() as db:
            alarm = db.scalar(select(Alarm))
            events = list(db.scalars(select(NotificationOutbox).order_by(NotificationOutbox.id)))
            delivered = [row.event_type for row in events if row.status == "mock_sent"]
            assert delivered == ["current_status", "resolved_summary"]
            assert alarm.status == "resolved"
            return {
                "version": VERSION,
                "database": str(path.resolve()),
                "network_used": False,
                "notification_mode": "mock",
                "time": "simulated UTC, no sleeps",
                "alarm_status": alarm.status,
                "completed_mock_events": delivered,
                "outbox": [
                    {
                        "event_id": e.event_id,
                        "type": e.event_type,
                        "status": e.status,
                        "attempts": e.attempts,
                        "suppression_reason": e.suppression_reason,
                    }
                    for e in events
                ],
            }
    finally:
        database.dispose()


def main():
    parser = argparse.ArgumentParser(description="Yeni dosyada tamamen mock kurumsal paket demosu")
    parser.add_argument("--path", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(asyncio.run(run_demo(args.path)), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
