from __future__ import annotations

import asyncio
import ctypes
import math
import platform
import re
from dataclasses import dataclass
from ipaddress import IPv4Address, IPv6Address, ip_address
from typing import Protocol

from sqlalchemy import text

from app.config import Settings
from app.models import Device, MonitoringResult
from app.services.alarms import evaluate_result
from app.services.audit import add_audit_log


@dataclass(frozen=True, slots=True)
class ProbeResult:
    outcome: str
    latency_ms: float | None = None
    error_message: str | None = None


class ProbeProvider(Protocol):
    mode: str

    async def probe(self, target_ip: str) -> ProbeResult: ...


class TargetNotAllowedError(ValueError):
    pass


class CheckInProgressError(RuntimeError):
    pass


class MockProbeProvider:
    """Deterministic, network-free provider for demos and tests."""

    mode = "mock"

    def __init__(self, demo=False):
        self.demo = demo
        self.positions = {}

    async def probe(self, target_ip: str) -> ProbeResult:
        if self.demo and target_ip == "192.0.2.2":
            sequence = ("reply", "no_reply", "no_reply", "no_reply", "no_reply", "reply")
            position = self.positions.get(target_ip, 0)
            self.positions[target_ip] = position + 1
            outcome = sequence[min(position, len(sequence) - 1)]
            return ProbeResult(outcome, latency_ms=1.0 if outcome == "reply" else None)
        numeric_ip = int(ip_address(target_ip))
        scenario = (numeric_ip & 0xFF) % 3
        if scenario == 1:
            return ProbeResult("reply", latency_ms=round(0.5 + numeric_ip % 20 / 10, 2))
        if scenario == 2:
            return ProbeResult("no_reply")
        return ProbeResult("error", error_message="Mock kontrol hatası simüle edildi.")


class PingAdapter:
    _RTT_PATTERN = re.compile(
        r"(?:time|süre)\s*(?P<operator>[=<])\s*(?P<latency>\d+(?:[.,]\d+)?)\s*ms",
        re.IGNORECASE,
    )
    _NO_REPLY_MARKERS = (
        "request timed out",
        "destination host unreachable",
        "destination net unreachable",
        "100% packet loss",
        "0 received",
        "0 packets received",
        "istek zaman aşımına uğradı",
        "hedef ana bilgisayara ulaşılamıyor",
    )

    def __init__(self, system_name: str | None = None) -> None:
        self.system_name = system_name or platform.system()

    @staticmethod
    def output_encoding() -> str:
        # Windows console programs use the OEM code page, not necessarily UTF-8.
        if platform.system() == "Windows":
            return f"cp{ctypes.windll.kernel32.GetOEMCP()}"
        return "utf-8"

    def command(self, target_ip: str, timeout_seconds: float) -> list[str]:
        if self.system_name == "Windows":
            return ["ping", "-n", "1", "-w", str(math.ceil(timeout_seconds * 1000)), target_ip]
        if self.system_name == "Linux":
            return ["ping", "-c", "1", "-W", str(math.ceil(timeout_seconds)), target_ip]
        raise OSError(f"Desteklenmeyen işletim sistemi: {self.system_name}")

    async def probe(self, target_ip: str, timeout_seconds: float) -> ProbeResult:
        try:
            command = self.command(target_ip, timeout_seconds)
            process = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError:
            return ProbeResult("error", error_message="Ping komutu bulunamadı.")
        except PermissionError:
            return ProbeResult("error", error_message="Ping komutunu çalıştırma izni yok.")
        except OSError as exc:
            return ProbeResult("error", error_message=str(exc)[:240])

        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(), timeout=timeout_seconds + 1.0
            )
        except asyncio.CancelledError:
            if process.returncode is None:
                process.kill()
            await process.communicate()
            raise
        except TimeoutError:
            process.kill()
            await process.communicate()
            return ProbeResult("no_reply")

        output = b"\n".join((stdout, stderr)).decode(self.output_encoding(), errors="replace")
        rtt_match = self._RTT_PATTERN.search(output)
        if rtt_match:
            latency = float(rtt_match.group("latency").replace(",", "."))
            if rtt_match.group("operator") == "<":
                # A bound confirms a reply but does not provide an exact RTT sample.
                latency = None
            return ProbeResult("reply", latency_ms=latency)

        normalized_output = output.casefold()
        if any(marker in normalized_output for marker in self._NO_REPLY_MARKERS):
            return ProbeResult("no_reply")
        if process.returncode not in (0, 1):
            return ProbeResult(
                "error",
                error_message=f"Ping komutu çalıştırılamadı (çıkış kodu {process.returncode}).",
            )
        return ProbeResult("error", error_message="Ping çıktısı güvenilir biçimde yorumlanamadı.")


class IcmpProbeProvider:
    mode = "icmp"

    def __init__(self, settings: Settings, adapter: PingAdapter | None = None) -> None:
        self.timeout_seconds = settings.ping_timeout_seconds
        self.allowed_networks = settings.allowed_networks
        self.adapter = adapter or PingAdapter()

    def is_allowed(self, target: IPv4Address | IPv6Address) -> bool:
        return any(
            target.version == network.version and target in network
            for network in self.allowed_networks
        )

    async def probe(self, target_ip: str) -> ProbeResult:
        target = ip_address(target_ip)
        if not self.is_allowed(target):
            raise TargetNotAllowedError(
                "Hedef IP, sunucu tarafındaki ALLOWED_TARGET_CIDRS izin listesinde değil."
            )
        return await self.adapter.probe(str(target), self.timeout_seconds)


class MonitoringService:
    def __init__(self, provider: ProbeProvider, max_concurrent_checks: int) -> None:
        self.provider = provider
        self.epoch = 0
        self._semaphore = asyncio.Semaphore(max_concurrent_checks)
        self._active_devices: set[int] = set()
        self._active_lock = asyncio.Lock()

    async def check(self, device_id: int, target_ip: str, *, save=None) -> ProbeResult:
        async with self._active_lock:
            if device_id in self._active_devices:
                raise CheckInProgressError("Bu cihaz için zaten bir kontrol çalışıyor.")
            self._active_devices.add(device_id)

        try:
            async with self._semaphore:
                try:
                    result = await self.provider.probe(target_ip)
                except TargetNotAllowedError:
                    raise
                except Exception:
                    result = ProbeResult(
                        "error", error_message="Kontrol sağlayıcısı başarısız oldu."
                    )
                return save(result) if save else result
        finally:
            async with self._active_lock:
                self._active_devices.discard(device_id)

    async def check_and_record(self, database, device_id, *, source, actor, clock, threshold):
        with database.session_factory() as db:
            device = db.get(Device, device_id)
            if device is None or not device.is_active:
                raise CheckInProgressError("Cihaz bulunamadı veya pasif.")
            target_ip, version = device.ip_address, device.target_version
        epoch = self.epoch
        mode = self.provider.mode

        def save(probe):
            # No transaction survives the network await. Serialize read-modify-write on SQLite.
            with database.session_factory() as db:
                db.execute(text("BEGIN IMMEDIATE"))
                result = MonitoringResult(
                    device_id=device_id,
                    target_ip=target_ip,
                    target_version=version,
                    probe_mode=mode,
                    source=source,
                    outcome=probe.outcome,
                    latency_ms=probe.latency_ms,
                    error_message=probe.error_message,
                    checked_at=clock(),
                    is_current=epoch == self.epoch,
                )
                db.add(result)
                db.flush()
                evaluate_result(db, result, threshold, actor)
                if source == "manual":
                    add_audit_log(
                        db,
                        now=clock(),
                        action="device.check_result",
                        outcome="success",
                        target_type="monitoring_result",
                        target_id=result.id,
                        metadata={"probe_mode": mode, "probe_outcome": result.outcome},
                        **actor,
                    )
                db.commit()
                return result

        return await self.check(device_id, target_ip, save=save)


def build_monitoring_service(settings: Settings) -> MonitoringService:
    provider: ProbeProvider
    if settings.monitor_mode == "icmp":
        provider = IcmpProbeProvider(settings)
    else:
        provider = MockProbeProvider(settings.mock_demo)
    return MonitoringService(provider, settings.max_concurrent_checks)
