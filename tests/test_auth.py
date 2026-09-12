from __future__ import annotations

from contextlib import closing

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import select

from app.config import Settings
from app.models import AuditLog, User, UserSession
from app.services.security import DUMMY_PASSWORD_HASH
from app.services.users import deactivate_user, reset_user_password
from tests.conftest import (
    ADMIN_PASSWORD,
    ORIGIN,
    VIEWER_PASSWORD,
    MutableClock,
    csrf_token,
    login_as,
    mutation_headers,
)

INVALID_MESSAGE = "Kullanıcı adı veya parola hatalı."


def test_successful_login_rotates_session_and_exposes_only_safe_user_fields(
    client: TestClient,
) -> None:
    client.get("/login")
    preauth_id = client.cookies.get("avit_session")
    response = client.post(
        "/login",
        data={
            "username": " ADMIN ",
            "password": ADMIN_PASSWORD,
            "csrf_token": csrf_token(client),
        },
        headers={"Origin": ORIGIN},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert client.cookies.get("avit_session") != preauth_id
    cookie_headers = "\n".join(response.headers.get_list("set-cookie")).casefold()
    assert "httponly" in cookie_headers
    assert "samesite=lax" in cookie_headers
    assert "path=/" in cookie_headers
    assert "secure" not in cookie_headers
    me = client.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json() == {"id": 1, "username": "admin", "role": "admin"}
    assert "password" not in me.text.casefold()

    with client.app.state.database.session_factory() as db:
        user = db.scalar(select(User).where(User.username == "admin"))
        assert user is not None
        assert user.password_hash.startswith("$argon2id$")
        assert user.password_hash != ADMIN_PASSWORD
        stored_hashes = db.scalars(select(UserSession.token_hash)).all()
        assert preauth_id not in stored_hashes
        assert all(len(stored_hash) == 64 for stored_hash in stored_hashes)


def test_wrong_unknown_and_inactive_users_share_generic_error(client: TestClient) -> None:
    wrong = login_as(client, "admin", "Yanlış parola ama 15 karakter")
    assert wrong.status_code == 401
    assert INVALID_MESSAGE in wrong.text

    unknown = login_as(client, "bilinmeyen", "Yanlış parola ama 15 karakter")
    assert unknown.status_code == 401
    assert INVALID_MESSAGE in unknown.text

    with client.app.state.database.session_factory() as db:
        viewer = db.scalar(select(User).where(User.username == "viewer"))
        assert viewer is not None
        viewer.is_active = False
        db.commit()
    inactive = login_as(client, "viewer", VIEWER_PASSWORD)
    assert inactive.status_code == 401
    assert INVALID_MESSAGE in inactive.text


def test_unknown_user_runs_dummy_hash_verification(client: TestClient, monkeypatch) -> None:
    observed: list[str] = []

    def spy(password: str, password_hash: str) -> bool:
        observed.append(password_hash)
        return False

    monkeypatch.setattr("app.api.auth.verify_password", spy)
    response = login_as(client, "olmayan", "Yeterince uzun yanlış parola")
    assert response.status_code == 401
    assert observed == [DUMMY_PASSWORD_HASH]


def test_idle_and_absolute_session_expiry_use_controlled_clock(
    client: TestClient, clock: MutableClock
) -> None:
    assert login_as(client, "admin", ADMIN_PASSWORD).status_code == 303
    clock.advance(minutes=31)
    assert client.get("/api/auth/me").status_code == 401

    assert login_as(client, "admin", ADMIN_PASSWORD).status_code == 303
    for _ in range(16):
        clock.advance(minutes=29)
        assert client.get("/api/auth/me").status_code == 200
    clock.advance(minutes=17)
    assert client.get("/api/auth/me").status_code == 401


def test_logout_revokes_server_session_and_clears_cookies(client: TestClient) -> None:
    assert login_as(client, "admin", ADMIN_PASSWORD).status_code == 303
    old_session = client.cookies.get("avit_session")
    old_csrf = csrf_token(client)
    response = client.post(
        "/logout",
        data={"csrf_token": old_csrf},
        headers={"Origin": ORIGIN},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert client.cookies.get("avit_session") is None
    client.cookies.set("avit_session", old_session)
    client.cookies.set("avit_csrf", old_csrf)
    assert client.get("/api/auth/me").status_code == 401
    with client.app.state.database.session_factory() as db:
        assert db.scalar(select(AuditLog).where(AuditLog.action == "auth.logout")) is not None


def test_password_reset_and_deactivation_revoke_existing_sessions(
    client: TestClient, clock: MutableClock
) -> None:
    assert login_as(client, "viewer", VIEWER_PASSWORD).status_code == 303
    with client.app.state.database.session_factory() as db:
        reset_user_password(
            db,
            username="viewer",
            password="Yeni Görüntüleyici Parolası 2026",
            now=clock(),
        )
    assert client.get("/api/auth/me").status_code == 401

    assert login_as(client, "viewer", "Yeni Görüntüleyici Parolası 2026").status_code == 303
    with client.app.state.database.session_factory() as db:
        deactivate_user(db, username="viewer", now=clock())
    assert client.get("/api/auth/me").status_code == 401


def test_authorization_matrix(app: FastAPI) -> None:
    with TestClient(app, base_url=ORIGIN, client=("127.0.0.1", 50100)) as anonymous:
        assert anonymous.get("/api/devices").status_code == 401
        assert anonymous.get("/api/audit-logs").status_code == 401
        assert anonymous.get("/docs").status_code == 401
        assert anonymous.get("/openapi.json").status_code == 401

    with TestClient(app, base_url=ORIGIN, client=("127.0.0.2", 50101)) as viewer:
        assert login_as(viewer, "viewer", VIEWER_PASSWORD).status_code == 303
        assert viewer.get("/api/devices").status_code == 200
        headers = mutation_headers(viewer)
        assert (
            viewer.post(
                "/api/devices",
                json={"name": "Yasak", "ip_address": "127.0.0.8"},
                headers=headers,
            ).status_code
            == 403
        )
        assert viewer.post("/api/devices/1/check", headers=headers).status_code == 403
        assert viewer.get("/api/audit-logs").status_code == 403
        assert viewer.get("/docs").status_code == 403

    with TestClient(app, base_url=ORIGIN, client=("127.0.0.3", 50102)) as admin:
        assert login_as(admin, "admin", ADMIN_PASSWORD).status_code == 303
        assert admin.get("/api/audit-logs").status_code == 200
        assert admin.get("/docs").status_code == 200
        schema = admin.get("/openapi.json")
        assert schema.status_code == 200
        parameters = schema.json()["paths"]["/api/devices"]["post"]["parameters"]
        assert any(item["name"] == "X-CSRF-Token" for item in parameters)
        assert (
            admin.post(
                "/api/devices",
                json={"name": "Yetkili", "ip_address": "127.0.0.8"},
                headers=mutation_headers(admin),
            ).status_code
            == 201
        )


def test_login_and_api_mutations_require_valid_session_bound_csrf(
    client: TestClient,
) -> None:
    client.get("/login")
    missing_login_csrf = client.post(
        "/login",
        data={"username": "admin", "password": ADMIN_PASSWORD},
        headers={"Origin": ORIGIN},
    )
    assert missing_login_csrf.status_code == 403
    wrong_login_csrf = client.post(
        "/login",
        data={"username": "admin", "password": ADMIN_PASSWORD, "csrf_token": "wrong"},
        headers={"Origin": ORIGIN},
    )
    assert wrong_login_csrf.status_code == 403

    assert login_as(client, "admin", ADMIN_PASSWORD).status_code == 303
    payload = {"name": "CSRF cihazı", "ip_address": "127.0.0.9"}
    assert client.post("/api/devices", json=payload, headers={"Origin": ORIGIN}).status_code == 403
    assert (
        client.post(
            "/api/devices",
            json=payload,
            headers={"Origin": ORIGIN, "X-CSRF-Token": "wrong"},
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/api/devices",
            json=payload,
            headers={"Origin": "http://evil.invalid", "X-CSRF-Token": csrf_token(client)},
        ).status_code
        == 403
    )


def test_csrf_token_from_another_session_is_rejected(app: FastAPI) -> None:
    with (
        TestClient(app, base_url=ORIGIN, client=("127.0.0.4", 50103)) as first,
        closing(TestClient(app, base_url=ORIGIN, client=("127.0.0.5", 50104))) as second,
    ):
        assert login_as(first, "admin", ADMIN_PASSWORD).status_code == 303
        assert login_as(second, "admin", ADMIN_PASSWORD).status_code == 303
        response = first.post(
            "/api/devices",
            json={"name": "Çapraz", "ip_address": "127.0.0.10"},
            headers=mutation_headers(first, csrf_token(second)),
        )
        assert response.status_code == 403


def test_login_rate_limit_expires_without_real_waiting(
    client: TestClient, clock: MutableClock
) -> None:
    for _ in range(3):
        assert login_as(client, "admin", "Yanlış parola ama 15 karakter").status_code == 401
    limited = login_as(client, "admin", ADMIN_PASSWORD)
    assert limited.status_code == 429
    assert limited.headers["Retry-After"] == "60"
    clock.advance(seconds=61)
    assert login_as(client, "admin", ADMIN_PASSWORD).status_code == 303


def test_proxy_headers_do_not_bypass_source_rate_limit(client: TestClient) -> None:
    for index in range(3):
        client.get("/login")
        response = client.post(
            "/login",
            data={
                "username": f"olmayan-{index}",
                "password": "Yanlış parola ama 15 karakter",
                "csrf_token": csrf_token(client),
            },
            headers={"Origin": ORIGIN, "X-Forwarded-For": f"192.0.2.{index}"},
        )
        assert response.status_code == 401
    limited = login_as(client, "baska-hesap", "Yanlış parola ama 15 karakter")
    assert limited.status_code == 429


def test_http_cookie_exception_is_local_only_and_https_forces_secure() -> None:
    assert Settings(app_base_url="http://localhost:8000", _env_file=None).cookie_secure is False
    assert Settings(app_base_url="https://monitor.example", _env_file=None).cookie_secure is True
    with pytest.raises(ValidationError, match="HTTP yalnızca yerel"):
        Settings(app_base_url="http://monitor.example", _env_file=None)
