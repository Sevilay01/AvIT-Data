from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from tests.conftest import mutation_headers


def create_device(
    client: TestClient,
    *,
    name: str = "Merkez yönlendirici",
    ip_address: str = "127.0.0.1",
) -> dict:
    response = client.post(
        "/api/devices",
        json={
            "name": name,
            "ip_address": ip_address,
            "device_type": "Yönlendirici",
            "location": "Sunucu odası",
        },
        headers=mutation_headers(client),
    )
    assert response.status_code == 201
    return response.json()


def test_health_is_public_and_minimal(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert client.get("/static/app.js").status_code == 200


def test_dashboard_redirects_anonymous_and_is_available_to_viewer(
    client: TestClient, viewer_client: TestClient
) -> None:
    anonymous = TestClient(client.app, base_url="http://127.0.0.1:8000")
    response = anonymous.get("/", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"
    dashboard = viewer_client.get("/")
    assert dashboard.status_code == 200
    assert "Kurumsal Ağ İzleme" in dashboard.text
    assert "Yeni cihaz ekle" not in dashboard.text


def test_sqlite_foreign_keys_are_enabled(client: TestClient) -> None:
    from sqlalchemy import text

    with client.app.state.database.engine.connect() as connection:
        assert connection.scalar(text("PRAGMA foreign_keys")) == 1


def test_device_can_be_created_and_listed_with_normalized_ip(admin_client: TestClient) -> None:
    created = create_device(admin_client, ip_address="2001:0db8:0:0:0:0:0:1")
    assert created["ip_address"] == "2001:db8::1"
    assert created["created_at"].endswith("Z")
    page = admin_client.get("/api/devices?limit=10&offset=0")
    assert page.status_code == 200
    assert page.json()["items"][0]["id"] == created["id"]


@pytest.mark.parametrize(
    ("payload", "field"),
    [
        ({"name": "   ", "ip_address": "127.0.0.1"}, "name"),
        ({"name": "Cihaz", "ip_address": "sunucu.local"}, "ip_address"),
    ],
)
def test_invalid_device_is_rejected(
    admin_client: TestClient, payload: dict, field: str
) -> None:
    response = admin_client.post(
        "/api/devices", json=payload, headers=mutation_headers(admin_client)
    )
    assert response.status_code == 422
    assert field in str(response.json())


def test_normalized_duplicate_ip_is_rejected(admin_client: TestClient) -> None:
    create_device(admin_client, ip_address="2001:db8::5")
    response = admin_client.post(
        "/api/devices",
        json={"name": "İkinci", "ip_address": "2001:0db8:0:0:0:0:0:5"},
        headers=mutation_headers(admin_client),
    )
    assert response.status_code == 409


def test_missing_device_returns_404(admin_client: TestClient) -> None:
    headers = mutation_headers(admin_client)
    assert admin_client.get("/api/devices/404").status_code == 404
    missing_update = admin_client.patch(
        "/api/devices/404", json={"name": "Yok"}, headers=headers
    )
    assert missing_update.status_code == 404
    assert admin_client.post("/api/devices/404/check", headers=headers).status_code == 404
    assert admin_client.get("/api/devices/404/checks").status_code == 404


def test_device_can_be_updated_and_deactivated(admin_client: TestClient) -> None:
    device = create_device(admin_client)
    response = admin_client.patch(
        f"/api/devices/{device['id']}",
        json={"name": "Güncel cihaz", "is_active": False},
        headers=mutation_headers(admin_client),
    )
    assert response.status_code == 200
    assert response.json()["name"] == "Güncel cihaz"
    assert response.json()["is_active"] is False
    null_name = admin_client.patch(
        f"/api/devices/{device['id']}",
        json={"name": None},
        headers=mutation_headers(admin_client),
    )
    assert null_name.status_code == 422


def test_inactive_device_cannot_be_checked(admin_client: TestClient) -> None:
    device = create_device(admin_client)
    headers = mutation_headers(admin_client)
    admin_client.patch(f"/api/devices/{device['id']}", json={"is_active": False}, headers=headers)
    response = admin_client.post(f"/api/devices/{device['id']}/check", headers=headers)
    assert response.status_code == 409
    assert "Pasif" in response.json()["detail"]
    assert admin_client.get(f"/api/devices/{device['id']}/checks").json()["total"] == 0


def test_mock_check_is_stored_and_returned_in_history(admin_client: TestClient) -> None:
    device = create_device(admin_client, ip_address="127.0.0.1")
    response = admin_client.post(
        f"/api/devices/{device['id']}/check", headers=mutation_headers(admin_client)
    )
    assert response.status_code == 200
    result = response.json()
    assert result["probe_mode"] == "mock"
    assert result["outcome"] == "reply"
    history = admin_client.get(f"/api/devices/{device['id']}/checks").json()
    assert history["items"][0] == result


@pytest.mark.parametrize(
    ("target_ip", "expected_outcome"),
    [("127.0.0.2", "no_reply"), ("127.0.0.3", "error")],
)
def test_mock_scenarios_are_deterministic(
    admin_client: TestClient, target_ip: str, expected_outcome: str
) -> None:
    device = create_device(admin_client, ip_address=target_ip)
    headers = mutation_headers(admin_client)
    first = admin_client.post(f"/api/devices/{device['id']}/check", headers=headers).json()
    second = admin_client.post(f"/api/devices/{device['id']}/check", headers=headers).json()
    assert first["outcome"] == second["outcome"] == expected_outcome


def test_mock_mode_never_starts_ping_process(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def forbidden_process(*args: object, **kwargs: object) -> None:
        raise AssertionError("Mock modunda alt süreç başlatılmamalı")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", forbidden_process)
    device = create_device(admin_client)
    assert admin_client.post(
        f"/api/devices/{device['id']}/check", headers=mutation_headers(admin_client)
    ).status_code == 200


def test_old_check_keeps_target_ip_after_device_ip_change(admin_client: TestClient) -> None:
    device = create_device(admin_client, ip_address="127.0.0.1")
    headers = mutation_headers(admin_client)
    admin_client.post(f"/api/devices/{device['id']}/check", headers=headers)
    admin_client.patch(
        f"/api/devices/{device['id']}", json={"ip_address": "127.0.0.2"}, headers=headers
    )
    history = admin_client.get(f"/api/devices/{device['id']}/checks").json()
    assert history["items"][0]["target_ip"] == "127.0.0.1"


def test_pagination_is_bounded(admin_client: TestClient) -> None:
    assert admin_client.get("/api/devices?limit=101").status_code == 422
