from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.models import AuditLog
from tests.conftest import ADMIN_PASSWORD, login_as, mutation_headers
from tests.test_api import create_device


def test_required_audit_events_are_recorded_without_secrets(
    admin_client: TestClient,
) -> None:
    device = create_device(admin_client)
    admin_client.post(
        f"/api/devices/{device['id']}/check",
        headers=mutation_headers(admin_client),
    )
    page = admin_client.get("/api/audit-logs?limit=100&offset=0")
    assert page.status_code == 200
    actions = {item["action"] for item in page.json()["items"]}
    assert {
        "auth.login",
        "user.create",
        "device.create",
        "device.check_requested",
        "device.check_result",
    } <= actions

    with admin_client.app.state.database.session_factory() as db:
        serialized = "\n".join(
            "|".join(
                filter(
                    None,
                    [
                        log.actor_username,
                        log.action,
                        log.target_type,
                        log.target_id,
                        log.metadata_json,
                    ],
                )
            )
            for log in db.scalars(select(AuditLog)).all()
        )
    lowered = serialized.casefold()
    assert ADMIN_PASSWORD.casefold() not in lowered
    assert "argon2id" not in lowered
    assert admin_client.cookies.get("avit_session") not in serialized
    assert admin_client.cookies.get("avit_csrf") not in serialized


def test_failed_login_audit_survives_and_logs_are_read_only(client: TestClient) -> None:
    password = "Denetimde görünmemesi gereken parola"
    assert login_as(client, "bilinmeyen", password).status_code == 401
    assert login_as(client, "admin", ADMIN_PASSWORD).status_code == 303
    logs = client.get("/api/audit-logs?limit=100").json()["items"]
    assert any(log["action"] == "auth.login" and log["outcome"] == "failure" for log in logs)
    assert password not in str(logs)
    assert client.delete(
        "/api/audit-logs/1", headers=mutation_headers(client)
    ).status_code in {404, 405}


def test_device_and_audit_are_one_transaction(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_audit(*args: object, **kwargs: object) -> None:
        raise RuntimeError("audit write failed")

    monkeypatch.setattr("app.api.devices.add_audit_log", fail_audit)
    with pytest.raises(RuntimeError, match="audit write failed"):
        admin_client.post(
            "/api/devices",
            json={"name": "Rollback cihazı", "ip_address": "127.0.0.44"},
            headers=mutation_headers(admin_client),
        )
    assert admin_client.get("/api/devices").json()["total"] == 0
