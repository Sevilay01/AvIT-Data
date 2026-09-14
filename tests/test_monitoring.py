from __future__ import annotations

import asyncio

import pytest

from app.config import Settings
from app.services.monitoring import (
    CheckInProgressError,
    IcmpProbeProvider,
    MonitoringService,
    PingAdapter,
    ProbeResult,
    TargetNotAllowedError,
)


class FakeProcess:
    def __init__(
        self,
        stdout: bytes = b"",
        stderr: bytes = b"",
        returncode: int = 0,
        delay: float = 0,
    ) -> None:
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode
        self.delay = delay
        self.killed = False

    async def communicate(self) -> tuple[bytes, bytes]:
        if self.delay and not self.killed:
            await asyncio.sleep(self.delay)
        return self.stdout, self.stderr

    def kill(self) -> None:
        self.killed = True


def install_fake_process(
    monkeypatch: pytest.MonkeyPatch,
    process: FakeProcess,
    captured: list[tuple[object, ...]] | None = None,
) -> None:
    async def create(*args: object, **kwargs: object) -> FakeProcess:
        if captured is not None:
            captured.append(args)
        assert kwargs["stdout"] == asyncio.subprocess.PIPE
        assert kwargs["stderr"] == asyncio.subprocess.PIPE
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create)


def test_windows_ping_reply_is_parsed_without_shell(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[tuple[object, ...]] = []
    process = FakeProcess(stdout=b"Reply from 127.0.0.1: bytes=32 time<1ms TTL=128")
    install_fake_process(monkeypatch, process, captured)

    result = asyncio.run(PingAdapter("Windows").probe("127.0.0.1", 1))

    assert result.outcome == "reply"
    assert result.latency_ms is None
    assert captured == [("ping", "-n", "1", "-w", "1000", "127.0.0.1")]


@pytest.mark.parametrize("value, expected", [("<1", None), ("=1,25", 1.25), ("=0", 0.0)])
def test_turkish_oem_ping_reply(monkeypatch, value, expected):
    monkeypatch.setattr(PingAdapter, "output_encoding", staticmethod(lambda: "cp857"))
    output = f"127.0.0.1 cevabı: bayt=32 süre{value}ms TTL=128".encode("cp857")
    install_fake_process(monkeypatch, FakeProcess(stdout=output))
    result = asyncio.run(PingAdapter("Windows").probe("127.0.0.1", 1))
    assert result.outcome == "reply"
    assert result.latency_ms == expected


@pytest.mark.parametrize("exception", [FileNotFoundError, PermissionError])
def test_missing_or_denied_ping_is_error(monkeypatch, exception):
    async def fail(*args, **kwargs):
        raise exception()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fail)
    result = asyncio.run(PingAdapter("Windows").probe("127.0.0.1", 1))
    assert result.outcome == "error"
    assert result.latency_ms is None


def test_linux_ping_no_reply_is_distinct_from_error(monkeypatch: pytest.MonkeyPatch) -> None:
    process = FakeProcess(
        stdout=b"1 packets transmitted, 0 received, 100% packet loss",
        returncode=1,
    )
    install_fake_process(monkeypatch, process)
    result = asyncio.run(PingAdapter("Linux").probe("127.0.0.1", 1))
    assert result.outcome == "no_reply"
    assert result.error_message is None


def test_unparseable_ping_output_is_a_mechanism_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_fake_process(monkeypatch, FakeProcess(stdout=b"unexpected output", returncode=0))
    result = asyncio.run(PingAdapter("Linux").probe("127.0.0.1", 1))
    assert result.outcome == "error"
    assert "yorumlanamadı" in (result.error_message or "")


def test_ping_process_timeout_is_cleaned_up(monkeypatch: pytest.MonkeyPatch) -> None:
    process = FakeProcess(delay=1)
    install_fake_process(monkeypatch, process)
    result = asyncio.run(PingAdapter("Linux").probe("127.0.0.1", 0.001))
    assert result.outcome == "error"
    assert "süre sınırında" in result.error_message
    assert process.killed is True


class SpyAdapter:
    def __init__(self) -> None:
        self.called = False

    async def probe(self, target_ip: str, timeout_seconds: float):
        self.called = True
        raise AssertionError("İzin verilmeyen hedefte ağ işlemi başlamamalı")


def test_disallowed_icmp_target_is_rejected_before_network() -> None:
    settings = Settings(
        monitor_mode="icmp",
        allowed_target_cidrs="127.0.0.1/32,::1/128",
        _env_file=None,
    )
    adapter = SpyAdapter()
    provider = IcmpProbeProvider(settings, adapter=adapter)  # type: ignore[arg-type]

    with pytest.raises(TargetNotAllowedError):
        asyncio.run(provider.probe("192.0.2.1"))
    assert adapter.called is False


def test_same_device_cannot_start_overlapping_checks() -> None:
    class SlowProvider:
        mode = "mock"

        def __init__(self) -> None:
            self.started = asyncio.Event()
            self.release = asyncio.Event()

        async def probe(self, target_ip: str) -> ProbeResult:
            self.started.set()
            await self.release.wait()
            return ProbeResult("reply", latency_ms=1)

    async def scenario() -> None:
        provider = SlowProvider()
        service = MonitoringService(provider, max_concurrent_checks=2)
        first = asyncio.create_task(service.check(1, "127.0.0.1"))
        await provider.started.wait()
        with pytest.raises(CheckInProgressError):
            await service.check(1, "127.0.0.1")
        provider.release.set()
        assert (await first).outcome == "reply"

    asyncio.run(scenario())
