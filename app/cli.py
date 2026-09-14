from __future__ import annotations

import argparse
import getpass
import json
import sys
from pathlib import Path

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
    parser = argparse.ArgumentParser(description="AvITData yerel işletim araçları")
    subparsers = parser.add_subparsers(dest="command", required=True)

    create_parser = subparsers.add_parser("create-user", help="Admin veya viewer oluştur")
    create_parser.add_argument("--username", required=True)
    create_parser.add_argument("--role", required=True, choices=("admin", "viewer"))

    reset_parser = subparsers.add_parser("reset-password", help="Kullanıcı parolasını sıfırla")
    reset_parser.add_argument("--username", required=True)

    deactivate_parser = subparsers.add_parser("deactivate-user", help="Kullanıcıyı pasife al")
    deactivate_parser.add_argument("--username", required=True)

    clean = subparsers.add_parser("cleanup", help="Varsayılan salt okunur saklama önizlemesi")
    clean.add_argument("--database", type=Path, required=True)
    clean.add_argument("--measurements-before", help="Saat dilimli ISO-8601 kesim zamanı")
    clean.add_argument("--sessions-before", help="Oturum bitişi için ISO-8601 kesim zamanı")
    clean.add_argument("--apply", action="store_true")
    clean.add_argument("--batch-size", type=int, default=500)
    clean.add_argument("--max-batches", type=int, default=20)
    for name, help_text in (
        ("backup", "Tutarlı SQLite yedeği"),
        ("restore-drill", "Yeni hedefte karantinalı geri yükleme"),
    ):
        backup = subparsers.add_parser(name, help=help_text)
        backup.add_argument("--source", type=Path, required=True)
        backup.add_argument("--target", type=Path, required=True)
        backup.add_argument("--timeout", type=float, default=30)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command in {"cleanup", "backup", "restore-drill"}:
        # Explicit file paths: these operations never read DATABASE_URL or .env.
        try:
            if args.command == "cleanup":
                from app.services.reporting import parse_timestamp
                from app.services.retention import cleanup

                result = cleanup(
                    args.database,
                    measurements_before=(
                        parse_timestamp(args.measurements_before)
                        if args.measurements_before
                        else None
                    ),
                    sessions_before=(
                        parse_timestamp(args.sessions_before) if args.sessions_before else None
                    ),
                    apply=args.apply,
                    batch_size=args.batch_size,
                    max_batches=args.max_batches,
                    on_batch=lambda name, count: print(
                        json.dumps({"committed_batch": name, "deleted": count}),
                        file=sys.stderr,
                        flush=True,
                    ),
                )
            else:
                from app.services.backup import backup_database, restore_drill

                operation = backup_database if args.command == "backup" else restore_drill
                result = operation(args.source, args.target, timeout=args.timeout)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        except (ValueError, OSError) as exc:
            print(f"İşlem başarısız: {exc}")
            return 1
        except KeyboardInterrupt:
            print("İşlem kesildi; commit edilmiş partiler kalır. Aynı kesimle yeniden çalıştırın.")
            return 130
        except Exception:
            print("İşlem başarısız: veritabanı/şema denetlenmeli; tamamlanan partiler korunur.")
            return 1
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
