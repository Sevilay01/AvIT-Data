import asyncio
import logging
from contextlib import suppress

from sqlalchemy import select

from app.models import Device
from app.services.alarms import reset_streaks
from app.services.monitoring import CheckInProgressError, TargetNotAllowedError

logger = logging.getLogger(__name__)
SYSTEM_ACTOR = {"source": "scheduler", "actor_username": "system/scheduler"}


class MonitoringScheduler:
    """One fixed-delay sweep; no backlog, catch-up, or shared job store."""

    def __init__(self, database, monitor, settings, clock):
        self.database = database
        self.monitor = monitor
        self.settings = settings
        self.clock = clock
        self.task = None
        self.last_scan_at = None
        self.last_error = None
        self._control = asyncio.Lock()
        self._sweep = asyncio.Lock()

    @property
    def running(self):
        return self.task is not None and not self.task.done()

    def reset(self):
        self.monitor.epoch += 1
        with self.database.session_factory() as db:
            reset_streaks(db)
            db.commit()

    async def start(self):
        async with self._control:
            if self.running:
                return False
            self.task = asyncio.create_task(self._run())
            return True

    async def stop(self):
        async with self._control:
            changed = self.running
            if self.task:
                self.task.cancel()
                with suppress(asyncio.CancelledError):
                    await self.task
                self.task = None
            self.reset()
            return changed

    async def _run(self):
        while True:
            try:
                await self.scan_once()
            except Exception:
                self.last_error = "Tarama tamamlanamadı; sunucu günlüğünü inceleyin."
                logger.exception("Scheduled sweep failed")
            await asyncio.sleep(self.settings.monitor_interval_seconds)

    async def scan_once(self):
        if self._sweep.locked():
            return
        async with self._sweep:
            self.last_error = None
            with self.database.session_factory() as db:
                ids = list(db.scalars(select(Device.id).where(Device.is_active.is_(True))))
            # Fixed-size batches bound both pending tasks and concurrent probes.
            width = self.settings.max_concurrent_checks
            for offset in range(0, len(ids), width):
                await asyncio.gather(*(self._check(i) for i in ids[offset : offset + width]))
            self.last_scan_at = self.clock()

    async def _check(self, device_id):
        try:
            await self.monitor.check_and_record(
                self.database,
                device_id,
                source="scheduled",
                actor=SYSTEM_ACTOR,
                clock=self.clock,
                threshold=self.settings.alarm_threshold,
            )
        except CheckInProgressError:
            pass
        except TargetNotAllowedError:
            self.last_error = "Bir hedef izin listesi dışında; kontrol atlandı."
        except Exception:
            self.last_error = "Bir cihaz kontrol edilemedi; sunucu günlüğünü inceleyin."
            logger.exception("Scheduled device check failed: %s", device_id)
