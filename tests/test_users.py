from __future__ import annotations

import pytest
from sqlalchemy import select

from app.cli import build_parser, main
from app.models import AuditLog, User
from app.services.security import normalize_username, verify_password
from app.services.users import UserManagementError, create_user, deactivate_user
from tests.conftest import MutableClock


def test_username_normalization_and_argon2_password_storage(app, clock: MutableClock) -> None:
    with app.state.database.session_factory() as db:
        user = create_user(
            db,
            username="  YÖNETİCİ_2  ",
            password="Unicode parola 🔐 güvenlidir",
            role="admin",
            now=clock(),
        )
        assert user.username == normalize_username("YÖNETİCİ_2")
        assert user.password_hash.startswith("$argon2id$")
        assert verify_password("Unicode parola 🔐 güvenlidir", user.password_hash)


@pytest.mark.parametrize("password", ["kısa", "x" * 129])
def test_password_length_limits_are_enforced(app, clock: MutableClock, password: str) -> None:
    with app.state.database.session_factory() as db, pytest.raises(ValueError, match="15-128"):
        create_user(
            db,
            username="sınır-kullanıcısı",
            password=password,
            role="viewer",
            now=clock(),
        )


def test_last_active_admin_cannot_be_deactivated(app, clock: MutableClock) -> None:
    with app.state.database.session_factory() as db:
        with pytest.raises(UserManagementError, match="Son aktif yönetici"):
            deactivate_user(db, username="admin", now=clock())
        admin = db.scalar(select(User).where(User.username == "admin"))
        assert admin is not None and admin.is_active
        denied = db.scalar(
            select(AuditLog).where(
                AuditLog.action == "user.deactivate", AuditLog.outcome == "denied"
            )
        )
        assert denied is not None


def test_admin_can_be_deactivated_when_another_active_admin_exists(
    app, clock: MutableClock
) -> None:
    with app.state.database.session_factory() as db:
        create_user(
            db,
            username="yedek-admin",
            password="Yedek yönetici parolası 2026",
            role="admin",
            now=clock(),
        )
        user = deactivate_user(db, username="admin", now=clock())
        assert user.is_active is False


def test_cli_has_no_password_argument() -> None:
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(
            ["create-user", "--username", "admin2", "--role", "admin", "--password", "secret"]
        )


def test_cli_create_reset_and_deactivate_use_hidden_confirmed_passwords(
    app, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    passwords = iter(
        [
            "İlk CLI görüntüleyici parolası",
            "İlk CLI görüntüleyici parolası",
            "Yeni CLI görüntüleyici parolası",
            "Yeni CLI görüntüleyici parolası",
        ]
    )
    monkeypatch.setattr("app.cli.get_settings", lambda: app.state.settings)
    monkeypatch.setattr("app.cli.getpass.getpass", lambda prompt: next(passwords))

    assert main(["create-user", "--username", "cli-viewer", "--role", "viewer"]) == 0
    assert main(["reset-password", "--username", "cli-viewer"]) == 0
    assert main(["deactivate-user", "--username", "cli-viewer"]) == 0

    output = capsys.readouterr().out
    assert "parolası" not in output.casefold()
    with app.state.database.session_factory() as db:
        user = db.scalar(select(User).where(User.username == "cli-viewer"))
        assert user is not None and not user.is_active
        actions = set(
            db.scalars(
                select(AuditLog.action).where(AuditLog.target_id == str(user.id))
            ).all()
        )
        assert {"user.create", "user.password_reset", "user.deactivate"} <= actions
