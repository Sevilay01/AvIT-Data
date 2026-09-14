"""Real localhost HTTP acceptance using only stdlib + runtime dependencies."""

import argparse
import csv
import importlib.metadata
import importlib.util
import io
import json
import platform
import secrets
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from contextlib import closing, contextmanager
from datetime import timedelta
from http.cookiejar import CookieJar
from pathlib import Path

from scripts.common import ROOT, safe_env


class HTTP:
    def __init__(self, origin):
        self.origin, self.jar = origin, CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}), urllib.request.HTTPCookieProcessor(self.jar)
        )

    def cookie(self, name):
        return next(c.value for c in self.jar if c.name == name)

    def request(self, path, *, body=None, form=None, expected=200):
        headers = {"Origin": self.origin}
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers.update(
                {"Content-Type": "application/json", "X-CSRF-Token": self.cookie("avit_csrf")}
            )
        if form is not None:
            data = urllib.parse.urlencode(form).encode()
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        req = urllib.request.Request(self.origin + path, data=data, headers=headers)
        try:
            response = self.opener.open(req, timeout=3)
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            content = response.read()
            if response.status != expected:
                raise RuntimeError(f"HTTP {path}: beklenen {expected}, alınan {response.status}")
            return content, response.headers

    def json(self, path, **kwargs):
        return json.loads(self.request(path, **kwargs)[0])

    def login(self, password):
        self.request("/login")
        self.request(
            "/login",
            form={
                "username": "verification-admin",
                "password": password,
                "csrf_token": self.cookie("avit_csrf"),
            },
        )
        assert self.json("/api/auth/me")["role"] == "admin"


@contextmanager
def server(database, directory, *, notification_mode="mock"):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    origin = f"http://127.0.0.1:{port}"
    env = safe_env(database)
    env.update(
        APP_BASE_URL=origin,
        NOTIFICATION_MODE=notification_mode,
        MONITOR_INTERVAL_SECONDS="5",
        NOTIFICATION_POLL_SECONDS="0.1",
        MOCK_DEMO="true",
    )
    # A module launched from the extracted source; absolute templates/static paths
    # come from that source. No reference to the original checkout is provided.
    with (directory / (database.stem + "-server.log")).open("w", encoding="utf-8") as log:
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "app.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--workers",
                "1",
                "--no-access-log",
            ],
            cwd=ROOT,
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        client = HTTP(origin)
        try:
            deadline = time.monotonic() + 20
            while True:
                if process.poll() is not None:
                    raise RuntimeError("Doğrulamanın başlattığı sunucu açılamadı.")
                try:
                    if client.json("/ready")["status"] == "ready":
                        break
                except (OSError, RuntimeError):
                    if time.monotonic() >= deadline:
                        raise RuntimeError("Localhost açılış süre sınırı aşıldı.") from None
                time.sleep(0.1)
            yield client
        finally:
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


def until(fn):
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        value = fn()
        if value:
            return value
        time.sleep(0.1)
    raise RuntimeError("Mock HTTP senaryosunun süre sınırı aşıldı.")


def table_rows(path):
    with closing(sqlite3.connect(path)) as conn:
        names = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        return {
            name: conn.execute('SELECT * FROM "' + name.replace('"', '""') + '"').fetchall()
            for name in names
        }


def smoke(directory, *, runtime_only=False):
    if runtime_only:
        for name in ("pytest", "ruff", "httpx", "httpx2"):
            if importlib.util.find_spec(name) is not None:
                raise RuntimeError("Çalışma ortamına geliştirme bağımlılığı karışmış: " + name)
    from alembic.config import Config

    from alembic import command
    from app.database import Database
    from app.models import utc_now
    from app.services.backup import backup_database, restore_drill
    from app.services.users import create_user
    from app.version import VERSION

    database_path = directory / "runtime.db"
    database_path.touch(exist_ok=False)
    url = "sqlite:///" + database_path.as_posix()
    config = Config(str(ROOT / "alembic.ini"))
    config.attributes["database_url"] = url
    command.upgrade(config, "head")
    database = Database(url)
    password = secrets.token_urlsafe(32)
    try:
        with database.session_factory() as db:
            create_user(
                db, username="verification-admin", password=password, role="admin", now=utc_now()
            )
    finally:
        database.dispose()
    with server(database_path, directory) as client:
        assert client.json("/health") == {"status": "ok"}
        client.request("/api/summary", expected=401)
        client.login(password)
        assert b"report-form" in client.request("/")[0]
        for asset in ("app.js", "reports.js", "operations.js", "vendor/chartjs-4.5.1/chart.umd.js"):
            client.request("/static/" + asset)
        assert not client.json("/api/summary")["running"]
        device = client.json(
            "/api/devices",
            body={"name": "Paket doğrulama", "ip_address": "192.0.2.2"},
            expected=201,
        )
        did = device["id"]
        period = {
            "device_id": did,
            "starts_at": utc_now().isoformat(),
            "ends_at": (utc_now() + timedelta(minutes=5)).isoformat(),
            "reason": "Geçici mock kabul",
        }
        mid = client.json("/api/maintenance", body=period, expected=201)["id"]
        # MOCK_DEMO's existing deterministic pattern opens and resolves alarms.
        outcomes = []
        for _ in range(4):
            outcomes.append(client.json(f"/api/devices/{did}/check", body={})["outcome"])
        alarm = client.json("/api/alarms")["items"][0]
        assert alarm["status"] == "open" and alarm["in_maintenance"]
        aid = alarm["id"]
        sid = client.json(f"/api/alarms/{aid}/silences", body=period, expected=201)["id"]
        client.json(f"/api/maintenance/{mid}/cancel", body={})
        assert client.json(f"/api/alarms/{aid}")["silenced"]
        client.json(f"/api/silences/{sid}/cancel", body={})
        client.json(f"/api/devices/{did}/check", body={})
        # Depending on mock sequence the next result resolves or sustains it;
        # both transitions must remain visible and eventually mock-delivered.
        for _ in range(4):
            client.json(f"/api/devices/{did}/check", body={})
        completed = until(
            lambda: [
                r for r in client.json("/api/notifications")["items"] if r["status"] == "mock_sent"
            ]
        )
        assert client.json(f"/api/alarms/{aid}")["status"] == "resolved"
        assert all(r["delivery_mode"] in ("mock", "off") for r in completed)
        filters = urllib.parse.urlencode(
            {
                "start": (utc_now() - timedelta(hours=1)).isoformat(),
                "end": (utc_now() + timedelta(seconds=1)).isoformat(),
                "probe_mode": "mock",
            }
        )
        metrics = client.json(f"/api/devices/{did}/metrics?{filters}")
        assert metrics["summary"]["total"] >= 8
        assert metrics["data_scope"]["basis"] == "retained_measurements_only"
        content, headers = client.request(f"/api/devices/{did}/measurements.csv?{filters}")
        assert "retained-measurements-only" in headers["X-Data-Scope"]
        rows = list(csv.DictReader(io.StringIO(content.decode("utf-8-sig")), delimiter=";"))
        assert len(rows) == metrics["summary"]["total"]
        assert all(r["data_scope"] == "retained_only_period_coverage_unknown" for r in rows)
        summary = client.json("/api/summary")
        assert summary["operations"]["heartbeat"]["last_check_completed_at"]
        # Back up while the actual server is live, after its mock queue settles.
        until(lambda: client.json("/api/summary")["operations"]["pending_notifications"] == 0)
        backup = directory / "online-backup.db"
        start_backup = time.monotonic()
        backup_info = backup_database(database_path, backup)
        backup_wall = time.monotonic() - start_backup
    restored = directory / "restored.db"
    start_restore = time.monotonic()
    restore_info = restore_drill(backup, restored)
    restore_wall = time.monotonic() - start_restore
    original, copy = table_rows(backup), table_rows(restored)
    compared = [n for n in original if n not in {"user_sessions", "recovery_guard"}]
    assert all(original[n] == copy[n] for n in compared)
    assert len(original["user_sessions"]) == len(copy["user_sessions"])
    before_outbox = copy["notification_outbox"]
    with server(restored, directory) as client:
        client.login(password)
        summary = client.json("/api/summary")
        assert not summary["running"] and summary["probe_mode"] == "mock"
        assert summary["operations"]["recovery_hold"]
        assert summary["operations"]["notification_mode"] == "off"
        client.json(f"/api/devices/{did}/metrics")
        assert table_rows(restored)["notification_outbox"] == before_outbox
    return {
        "version": VERSION,
        "status": "passed",
        "runtime_dependencies_only": runtime_only,
        "python": sys.version,
        "python_base": sys.base_prefix,
        "sqlite": sqlite3.sqlite_version,
        "platform": platform.platform(),
        "pip": importlib.metadata.version("pip"),
        "transport": "real localhost HTTP, stdlib client, one uvicorn worker",
        "migration_auth_device_alarm_notification_maintenance_silence_health_report_csv": "passed",
        "restored_http": "passed",
        "compared_tables": compared,
        "session_policy": "all restored sessions revoked before startup",
        "backup": backup_info,
        "restore": restore_info,
        "backup_wall_seconds": round(backup_wall, 6),
        "restore_wall_seconds": round(restore_wall, 6),
        "browser": "not_run",
        "excel": "not_run",
        "company_network": "not_run",
        "real_smtp": "not_run",
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime-only", action="store_true")
    parser.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="avit-http-") as name:
        result = smoke(Path(name), runtime_only=args.runtime_only)
    args.evidence.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print("Localhost HTTP ve geri yükleme doğrulaması geçti.")


if __name__ == "__main__":
    main()
