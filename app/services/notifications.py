"""Single-process transactional outbox. The caller owns enqueue's transaction.

SMTP acceptance is at-least-once: a crash after DATA acceptance and before the
completion commit can duplicate a message. Stable Message-ID assists correlation,
but does not promise receiver deduplication or inbox delivery.
"""

import asyncio
import base64
import hashlib
import ssl
from contextlib import suppress
from datetime import UTC, timedelta
from email.message import EmailMessage
from email.policy import SMTP
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import func, or_, select, text
from sqlalchemy.dialects.sqlite import insert

from app.models import Alarm, Device, MaintenanceWindow, NotificationOutbox
from app.services.event_logging import log_event
from app.services.maintenance import (
    current_measurement,
    effective_end,
    fresh,
    intervals_for,
    suppression_reason,
)

ACTIVE = ("pending", "retry", "sending")
SUCCESS = ("accepted", "mock_sent")


def delivery_identity(settings, probe_mode=None):
    mode = settings.notification_mode
    # Simulated observations can never address a real recipient.
    if mode == "smtp" and probe_mode == "mock":
        mode = "mock"
    if mode != "smtp":
        return mode, mode
    # No address, credential or connection string is persisted. Changing routing
    # invalidates the old queue, rather than redirecting it to new recipients.
    routing = "\0".join(
        (
            settings.smtp_host.lower(),
            str(settings.smtp_port),
            settings.smtp_tls,
            settings.smtp_username,
            settings.smtp_from.strip(),
            ",".join(sorted(a.strip() for a in settings.smtp_to.split(","))),
        )
    )
    return mode, hashlib.sha256(routing.encode()).hexdigest()


def enqueue(db, alarm, event_type, event_key, now, settings, *, identity=None, reason=None):
    mode, target = identity or delivery_identity(settings, alarm.probe_mode)
    if alarm.probe_mode == "mock" and mode == "smtp":
        mode, target = "mock", "mock"
    reason = reason or suppression_reason(db, alarm, now)
    event_id = str(
        uuid5(
            NAMESPACE_URL,
            f"avit/alarm/{alarm.id}/{alarm.opened_at.astimezone(UTC).isoformat()}/{event_key}",
        )
    )
    status = "disabled" if mode == "off" else "suppressed" if reason else "pending"
    db.execute(
        insert(NotificationOutbox)
        .values(
            event_id=event_id,
            alarm_id=alarm.id,
            event_type=event_type,
            channel="email",
            target_key=target,
            delivery_mode=mode,
            status=status,
            attempts=0,
            created_at=now,
            next_attempt_at=now if status == "pending" else None,
            suppression_reason=reason,
            reconciled_at=now if mode == "off" else None,
        )
        .on_conflict_do_nothing(index_elements=["event_id", "channel", "target_key"])
    )
    return event_id


def capture_maintenance(db, settings, now):
    windows = db.scalars(
        select(MaintenanceWindow)
        .where(
            MaintenanceWindow.starts_at <= now,
            MaintenanceWindow.activated_at.is_(None),
        )
        .order_by(MaintenanceWindow.id)
    )
    for window in windows:
        end = effective_end(window)
        if end > window.starts_at:
            alarms = db.scalars(
                select(Alarm).where(
                    Alarm.device_id == window.device_id,
                    Alarm.opened_at < end,
                    or_(Alarm.ended_at.is_(None), Alarm.ended_at >= window.starts_at),
                )
            )
            for alarm in alarms:
                enqueue(
                    db,
                    alarm,
                    "current_status",
                    f"maintenance:{window.id}",
                    now,
                    settings,
                    identity=(window.delivery_mode, window.target_key),
                    reason=f"Bakım #{window.id}",
                )
                # Also covers a process that was down for the whole maintenance.
                for row in db.scalars(
                    select(NotificationOutbox).where(
                        NotificationOutbox.alarm_id == alarm.id,
                        NotificationOutbox.status.in_(("pending", "retry")),
                        NotificationOutbox.created_at < end,
                    )
                ):
                    row.status, row.suppression_reason = "suppressed", f"Bakım #{window.id}"
        window.activated_at = now
    db.flush()


def reconcile(db, settings, now):
    capture_maintenance(db, settings, now)
    ids = list(
        db.scalars(
            select(NotificationOutbox.alarm_id)
            .where(
                NotificationOutbox.status == "suppressed",
                NotificationOutbox.reconciled_at.is_(None),
            )
            .distinct()
        )
    )
    for alarm_id in ids:
        alarm = db.get(Alarm, alarm_id)
        if suppression_reason(db, alarm, now):
            continue
        rows = list(
            db.scalars(
                select(NotificationOutbox)
                .where(
                    NotificationOutbox.alarm_id == alarm_id,
                    NotificationOutbox.status == "suppressed",
                    NotificationOutbox.reconciled_at.is_(None),
                )
                .order_by(NotificationOutbox.id)
            )
        )
        compatible = []
        for row in rows:
            if (row.delivery_mode, row.target_key) != delivery_identity(settings, alarm.probe_mode):
                row.status, row.reconciled_at = "discarded", now
                row.safe_error = "Bildirim ayarı veya hedefi değişti; geçmiş olay gönderilmedi."
            else:
                compatible.append(row)
        if not compatible:
            continue
        if alarm.status == "closed":
            for row in compatible:
                row.reconciled_at = now
            continue
        previous_open = db.scalar(
            select(NotificationOutbox.id)
            .where(
                NotificationOutbox.alarm_id == alarm_id,
                NotificationOutbox.status.in_(SUCCESS),
                NotificationOutbox.event_type.in_(("opened", "current_status")),
                NotificationOutbox.target_key == compatible[-1].target_key,
                NotificationOutbox.delivery_mode == compatible[-1].delivery_mode,
            )
            .limit(1)
        )
        # Entirely suppressed lifecycle: no old messages are replayed.
        if alarm.status == "resolved" and not previous_open:
            for row in compatible:
                row.reconciled_at = now
            continue
        result = current_measurement(db, alarm)
        intervals = intervals_for(db, alarm, now)
        boundary = max((effective_end(i) for i in intervals), default=compatible[-1].created_at)
        replacement_types = (
            ("opened", "current_status")
            if alarm.status == "open"
            else ("resolved", "resolved_summary")
        )
        replacement = db.scalar(
            select(NotificationOutbox.id)
            .where(
                NotificationOutbox.alarm_id == alarm_id,
                NotificationOutbox.event_type.in_(replacement_types),
                NotificationOutbox.created_at >= boundary,
                NotificationOutbox.status.in_((*ACTIVE, *SUCCESS)),
                NotificationOutbox.delivery_mode == compatible[-1].delivery_mode,
                NotificationOutbox.target_key == compatible[-1].target_key,
            )
            .limit(1)
        )
        if replacement:
            for row in compatible:
                row.reconciled_at = now
            continue
        if not fresh(result, now, settings) or result.outcome == "error":
            continue
        if alarm.status == "open" and (
            result.outcome != "no_reply" or result.checked_at < boundary
        ):
            continue
        kind = "current_status" if alarm.status == "open" else "resolved_summary"
        # Include the last suppressed row in the deterministic reconciliation key.
        enqueue(db, alarm, kind, f"reconcile:{compatible[-1].id}", now, settings)
        for row in compatible:
            row.reconciled_at = now


class MockSender:
    async def send(self, message, settings):
        # Intentionally no sockets, no credentials and no recipient side effects.
        return None


class SMTPFailure(Exception):
    pass


class SMTPSender:
    """Small async SMTP adapter: verified TLS only; cancellation closes transport.

    Supports STARTTLS or implicit TLS and optional AUTH PLAIN. No fallback to
    cleartext and no server response text is exposed or logged.
    """

    async def send(self, message, settings):
        context = ssl.create_default_context()
        reader, writer = await asyncio.open_connection(
            settings.smtp_host,
            settings.smtp_port,
            ssl=context if settings.smtp_tls == "implicit" else None,
            **({"server_hostname": settings.smtp_host} if settings.smtp_tls == "implicit" else {}),
            limit=8192,
        )
        try:

            async def response(expected):
                lines = []
                for _ in range(100):
                    line = await reader.readline()
                    if len(line) < 4 or not line[:3].isdigit():
                        raise SMTPFailure()
                    code = int(line[:3])
                    lines.append(line[4:].decode("ascii", errors="replace").upper())
                    if line[3:4] == b" ":
                        if code not in expected:
                            raise SMTPFailure()
                        return " ".join(lines)
                    if line[3:4] != b"-":
                        raise SMTPFailure()
                raise SMTPFailure()

            async def command(value, expected=(250,)):
                writer.write(value.encode("ascii") + b"\r\n")
                await writer.drain()
                return await response(expected)

            await response((220,))
            features = await command("EHLO avit-monitor.local")
            if settings.smtp_tls == "starttls":
                if "STARTTLS" not in features:
                    raise SMTPFailure()
                await command("STARTTLS", (220,))
                await writer.start_tls(context, server_hostname=settings.smtp_host)
                features = await command("EHLO avit-monitor.local")
            if settings.smtp_username:
                if "AUTH" not in features or "PLAIN" not in features:
                    raise SMTPFailure()
                auth = f"\0{settings.smtp_username}\0{settings.smtp_password.get_secret_value()}"
                await command("AUTH PLAIN " + base64.b64encode(auth.encode()).decode(), (235,))
            await command(f"MAIL FROM:<{settings.smtp_from.strip()}>")
            for address in settings.smtp_to.split(","):
                await command(f"RCPT TO:<{address.strip()}>", (250, 251))
            await command("DATA", (354,))
            body = message.as_bytes(policy=SMTP)
            # SMTP dot transparency; EmailMessage with SMTP policy uses CRLF.
            body = b"\r\n".join(
                b"." + line if line.startswith(b".") else line for line in body.split(b"\r\n")
            )
            writer.write(body.rstrip(b"\r\n") + b"\r\n.\r\n")
            await writer.drain()
            await response((250,))
            # DATA acceptance is the success boundary. QUIT failure cannot undo it.
        finally:
            writer.close()


def build_message(row, alarm, settings):
    message = EmailMessage()
    message["Message-ID"] = f"<{row.event_id}.{row.target_key[:12]}@avit-monitor.local>"
    message["From"] = settings.smtp_from if row.delivery_mode == "smtp" else "mock@avit.invalid"
    message["To"] = settings.smtp_to if row.delivery_mode == "smtp" else "mock@avit.invalid"
    labels = {
        "opened": "Alarm açıldı",
        "resolved": "Alarm çözüldü",
        "current_status": "Bakım/susturma sonrası alarm sürüyor",
        "resolved_summary": "Bakım/susturma sonrası çözülme özeti",
    }
    message["Subject"] = f"AvITData · {labels[row.event_type]} · Alarm #{alarm.id}"
    message.set_content(
        f"Olay: {row.event_id}\nAlarm: {alarm.id}\nHedef: {alarm.target_ip}\n"
        f"Ölçüm modu: {alarm.probe_mode}\nTür: {labels[row.event_type]}\n"
        f"Olay zamanı (UTC): {row.created_at.isoformat()}\n"
        "Bu mesaj SMTP kabulünü gösterebilir; gelen kutusuna teslim garantisi değildir.\n",
        cte="quoted-printable",
    )
    return message


class NotificationWorker:
    def __init__(self, database, settings, clock, *, smtp_sender=None, mock_sender=None,
                 recovery_hold=False):
        self.database, self.settings, self.clock = database, settings, clock
        self.smtp_sender = smtp_sender or SMTPSender()
        self.mock_sender = mock_sender or MockSender()
        self.task = None
        self.last_tick_at = None
        self.last_error = None
        self._tick_lock = asyncio.Lock()
        self._stopping = asyncio.Event()
        self.recovery_hold = recovery_hold

    @property
    def running(self):
        return self.task is not None and not self.task.done()

    def recover(self):
        # Called only after acquiring the existing exclusive database process lock.
        with self.database.session_factory() as db:
            db.execute(text("BEGIN IMMEDIATE"))
            for row in db.scalars(
                select(NotificationOutbox).where(
                    NotificationOutbox.status == "sending",
                )
            ):
                row.status = (
                    "failed" if row.attempts >= self.settings.notification_max_attempts else "retry"
                )
                row.next_attempt_at = self.clock() + self.backoff(row.attempts)
                if row.status == "failed":
                    row.next_attempt_at = None
                row.safe_error = "Gönderim yarıda kaldı; önceki SMTP kabulü bilinmiyor."
                row.claimed_at = None
            db.commit()

    def start(self):
        if not self.recovery_hold:
            self.recover()
        self._stopping.clear()
        self.task = asyncio.create_task(self._run())

    async def stop(self):
        if self.task:
            # Drain bounded sends and DB work before releasing the process lock.
            self._stopping.set()
            await self.task
            self.task = None

    def backoff(self, attempts):
        return timedelta(
            seconds=min(3600, self.settings.notification_retry_seconds * 2 ** max(0, attempts - 1))
        )

    async def _run(self):
        while not self._stopping.is_set():
            try:
                await self.tick()
                self.last_error = None
            except Exception:
                self.last_error = "Bildirim kuyruğu işlenemedi; işletim kontrolü gerekli."
                log_event("notification.worker", "worker", status="error")
            with suppress(TimeoutError):
                await asyncio.wait_for(
                    self._stopping.wait(), self.settings.notification_poll_seconds
                )

    def claim(self):
        now = self.clock()
        with self.database.session_factory() as db:
            db.execute(text("BEGIN IMMEDIATE"))
            reconcile(db, self.settings, now)
            db.flush()
            first_ids = (
                select(func.min(NotificationOutbox.id))
                .where(
                    NotificationOutbox.status.in_(ACTIVE),
                )
                .group_by(NotificationOutbox.alarm_id)
            )
            rows = list(
                db.scalars(
                    select(NotificationOutbox)
                    .where(
                        NotificationOutbox.id.in_(first_ids),
                        NotificationOutbox.status.in_(("pending", "retry")),
                        NotificationOutbox.next_attempt_at <= now,
                    )
                    .order_by(NotificationOutbox.id)
                    .limit(self.settings.notification_concurrency)
                )
            )
            for row in rows:
                row.status, row.claimed_at = "sending", now
            db.commit()
            return [row.id for row in rows]

    def preflight(self, row_id):
        with self.database.session_factory() as db:
            db.execute(text("BEGIN IMMEDIATE"))
            row = db.get(NotificationOutbox, row_id)
            if row.status != "sending":
                return None
            alarm = db.get(Alarm, row.alarm_id)
            device = db.get(Device, alarm.device_id)
            reason = suppression_reason(db, alarm, self.clock())
            if (row.delivery_mode, row.target_key) != delivery_identity(
                self.settings, alarm.probe_mode
            ):
                row.status = "discarded"
                row.safe_error = "Bildirim ayarı veya hedefi değişti; geçmiş olay gönderilmedi."
            elif (
                alarm.status == "closed"
                or not device.is_active
                or device.ip_address != alarm.target_ip
            ):
                row.status = "discarded"
                row.safe_error = "Cihaz hedefi veya idari durum değişti; olay gönderilmedi."
            elif reason:
                row.status, row.suppression_reason = "suppressed", reason
                row.reconciled_at = None
            elif row.event_type == "current_status" and (
                alarm.status != "open"
                or not fresh(current_measurement(db, alarm), self.clock(), self.settings)
                or current_measurement(db, alarm).outcome != "no_reply"
            ):
                row.status, row.suppression_reason = "suppressed", "Güncel arıza ölçümü bekleniyor."
                row.reconciled_at = None
            elif row.attempts >= self.settings.notification_max_attempts:
                row.status, row.safe_error = "failed", "En fazla gönderim denemesine ulaşıldı."
            else:
                row.attempts += 1
            db.commit()
            if row.status != "sending":
                row.claimed_at, row.next_attempt_at = None, None
                db.commit()
                return None
            return row, alarm

    def complete(self, row_id, error):
        with self.database.session_factory() as db:
            db.execute(text("BEGIN IMMEDIATE"))
            row = db.get(NotificationOutbox, row_id)
            if error:
                row.status = (
                    "failed" if row.attempts >= self.settings.notification_max_attempts else "retry"
                )
                row.safe_error = error
                row.next_attempt_at = self.clock() + self.backoff(row.attempts)
                if row.status == "failed":
                    row.completed_at, row.next_attempt_at = self.clock(), None
            else:
                row.status = "accepted" if row.delivery_mode == "smtp" else "mock_sent"
                row.completed_at, row.next_attempt_at, row.safe_error = self.clock(), None, None
            row.claimed_at = None
            db.commit()
            log_event(
                "notification.send",
                row.event_id,
                status=row.status,
                alarm_id=row.alarm_id,
                attempts=row.attempts,
            )

    async def dispatch(self, row_id):
        # Re-check immediately before the external call, after the claim commit.
        item = await asyncio.to_thread(self.preflight, row_id)
        if item is None:
            return
        row, alarm = item
        sender = self.smtp_sender if row.delivery_mode == "smtp" else self.mock_sender
        error = None
        try:
            async with asyncio.timeout(self.settings.notification_timeout_seconds):
                await sender.send(build_message(row, alarm, self.settings), self.settings)
        except TimeoutError:
            error = "Gönderim zaman aşımına uğradı; SMTP kabulü bilinmiyor."
        except Exception:
            error = "Bildirim gönderilemedi; SMTP/TLS ve hesap ayarlarını kontrol edin."
        await asyncio.to_thread(self.complete, row_id, error)

    async def tick(self):
        if self.recovery_hold:
            self.last_tick_at = self.clock()
            return
        async with self._tick_lock:
            # Previous tick's tasks have all settled. Recover a claim whose result
            # could not be persisted (e.g. a transient DB failure) without restart.
            await asyncio.to_thread(self.recover)
            ids = await asyncio.to_thread(self.claim)
            results = await asyncio.gather(
                *(self.dispatch(row_id) for row_id in ids), return_exceptions=True
            )
            if any(isinstance(result, BaseException) for result in results):
                raise RuntimeError("Bildirim işi tamamlanamadı; sonraki turda uzlaştırılacak.")
            self.last_tick_at = self.clock()
