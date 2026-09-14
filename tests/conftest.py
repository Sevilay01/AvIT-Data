from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic.config import Config
from fastapi import FastAPI
from fastapi.testclient import TestClient

from alembic import command
from app.config import Settings, get_settings
from app.main import create_app
from app.services.users import create_user

ORIGIN = "http://127.0.0.1:8000"
ADMIN_PASSWORD = "Yönetici Parolası 🔐 2026"
VIEWER_PASSWORD = "Görüntüleyici Parolası 2026"


class MutableClock:
    def __init__(self) -> None:
        self.current = datetime(2026, 9, 11, 7, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.current

    def advance(self, **kwargs: int) -> None:
        self.current += timedelta(**kwargs)


def sqlite_url(path: Path) -> str:
    return f"sqlite:///{path.as_posix()}"


def migrate(
    database_url: str,
    monkeypatch: pytest.MonkeyPatch,
    revision: str = "head",
) -> None:
    monkeypatch.setenv("DATABASE_URL", database_url)
    get_settings.cache_clear()
    config = Config("alembic.ini")
    command.upgrade(config, revision)
    get_settings.cache_clear()


@pytest.fixture
def clock() -> MutableClock:
    return MutableClock()


@pytest.fixture
def app(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    clock: MutableClock,
) -> Iterator[FastAPI]:
    database_url = sqlite_url(tmp_path / "test.db")
    migrate(database_url, monkeypatch)
    settings = Settings(
        database_url=database_url,
        monitor_mode="mock",
        notification_mode="off",
        allowed_target_cidrs="127.0.0.1/32,::1/128",
        app_base_url=ORIGIN,
        login_max_attempts=3,
        login_window_seconds=60,
        login_lock_seconds=60,
        _env_file=None,
    )
    application = create_app(settings, clock=clock)
    with application.state.database.session_factory() as db:
        create_user(
            db,
            username="admin",
            password=ADMIN_PASSWORD,
            role="admin",
            now=clock(),
        )
        create_user(
            db,
            username="viewer",
            password=VIEWER_PASSWORD,
            role="viewer",
            now=clock(),
        )
    yield application
    application.state.database.dispose()


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app, base_url=ORIGIN, client=("127.0.0.1", 50000)) as test_client:
        yield test_client


def csrf_token(client: TestClient) -> str:
    token = client.cookies.get("avit_csrf")
    assert token
    return token


def mutation_headers(client: TestClient, csrf: str | None = None) -> dict[str, str]:
    return {
        "Origin": ORIGIN,
        "X-CSRF-Token": csrf or csrf_token(client),
    }


def login_as(
    client: TestClient,
    username: str,
    password: str,
    *,
    follow_redirects: bool = False,
) -> object:
    page = client.get("/login")
    assert page.status_code == 200
    response = client.post(
        "/login",
        data={
            "username": username,
            "password": password,
            "csrf_token": csrf_token(client),
        },
        headers={"Origin": ORIGIN},
        follow_redirects=follow_redirects,
    )
    return response


@pytest.fixture
def admin_client(client: TestClient) -> TestClient:
    response = login_as(client, "admin", ADMIN_PASSWORD)
    assert response.status_code == 303
    return client


@pytest.fixture
def viewer_client(client: TestClient) -> TestClient:
    response = login_as(client, "viewer", VIEWER_PASSWORD)
    assert response.status_code == 303
    return client
