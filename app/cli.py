from __future__ import annotations

import argparse
import getpass

from app.config import get_settings
from app.database import Database
from app.models import utc_now
from app.services.security import validate_password
from app.services.users import (
    UserManagementError,
    create_user,
    deactivate_user,
    reset_user_password,
)


def confirmed_password() -> str:
    password = getpass.getpass("Parola: ")
    confirmation = getpass.getpass("Parola tekrar: ")
    if password != confirmation:
        raise UserManagementError("Parolalar eşleşmiyor.")
    try:
        return validate_password(password)
    except ValueError as exc:
        raise UserManagementError(str(exc)) from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="AvITData yerel kullanıcı yönetimi")
    subparsers = parser.add_subparsers(dest="command", required=True)

    create_parser = subparsers.add_parser("create-user", help="Admin veya viewer oluştur")
    create_parser.add_argument("--username", required=True)
    create_parser.add_argument("--role", required=True, choices=("admin", "viewer"))

    reset_parser = subparsers.add_parser("reset-password", help="Kullanıcı parolasını sıfırla")
    reset_parser.add_argument("--username", required=True)

    deactivate_parser = subparsers.add_parser("deactivate-user", help="Kullanıcıyı pasife al")
    deactivate_parser.add_argument("--username", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    database = Database(get_settings().database_url)
    try:
        with database.session_factory() as db:
            if args.command == "create-user":
                user = create_user(
                    db,
                    username=args.username,
                    password=confirmed_password(),
                    role=args.role,
                    now=utc_now(),
                )
                print(f"Kullanıcı oluşturuldu: {user.username} ({user.role})")
            elif args.command == "reset-password":
                user = reset_user_password(
                    db,
                    username=args.username,
                    password=confirmed_password(),
                    now=utc_now(),
                )
                print(f"Parola sıfırlandı ve oturumlar iptal edildi: {user.username}")
            else:
                user = deactivate_user(db, username=args.username, now=utc_now())
                print(f"Kullanıcı pasife alındı ve oturumları iptal edildi: {user.username}")
    except ValueError as exc:
        print(f"Hata: {exc}")
        return 1
    finally:
        database.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
