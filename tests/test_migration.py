from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text

from tests.conftest import migrate, sqlite_url


def test_fresh_database_migrates_to_auth_schema(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_url = sqlite_url(tmp_path / "fresh.db")
    migrate(database_url, monkeypatch)
    engine = create_engine(database_url)
    assert set(inspect(engine).get_table_names()) == {
        "alembic_version",
        "alarms",
        "monitor_states",
        "audit_logs",
        "devices",
        "monitoring_results",
        "user_sessions",
        "users",
        "notification_outbox",
        "maintenance_windows",
        "alarm_silences",
        "monitoring_heartbeat",
        "recovery_guard",
    }
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "20260914_0005"
    engine.dispose()


def test_v1_data_is_preserved_during_upgrade(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_url = sqlite_url(tmp_path / "upgrade.db")
    migrate(database_url, monkeypatch, "20260911_0001")
    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO devices "
                "(id, name, ip_address, is_active) VALUES (7, 'Eski cihaz', '127.0.0.7', 1)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO monitoring_results "
                "(id, device_id, target_ip, probe_mode, outcome) "
                "VALUES (9, 7, '127.0.0.7', 'mock', 'reply')"
            )
        )
    engine.dispose()

    migrate(database_url, monkeypatch)
    engine = create_engine(database_url)
    with engine.connect() as connection:
        device = connection.execute(text("SELECT name, ip_address FROM devices WHERE id=7")).one()
        result = connection.execute(
            text("SELECT device_id, target_ip, outcome FROM monitoring_results WHERE id=9")
        ).one()
        assert device == ("Eski cihaz", "127.0.0.7")
        assert result == (7, "127.0.0.7", "reply")
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "20260914_0005"
    engine.dispose()
