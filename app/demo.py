"""Create a separate, new mock demo database without credentials."""

import argparse
from pathlib import Path

from alembic.config import Config

from alembic import command
from app.database import Database
from app.models import Device


def prepare_demo(path: Path):
    path = path.resolve()
    if path.suffix not in {".db", ".sqlite", ".sqlite3"}:
        raise ValueError("Demo dosyası .db, .sqlite veya .sqlite3 uzantılı olmalı.")
    # Exclusive creation protects existing data, including the normal application database.
    with path.open("xb"):
        pass
    url = f"sqlite:///{path.as_posix()}"
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    config.attributes["database_url"] = url
    command.upgrade(config, "head")
    database = Database(url)
    try:
        with database.session_factory() as db:
            db.add_all(
                [
                    Device(name="Demo · Sürekli yanıt", ip_address="192.0.2.1"),
                    Device(name="Demo · Alarm yaşam döngüsü", ip_address="192.0.2.2"),
                    Device(name="Demo · Kontrol mekanizması hatası", ip_address="192.0.2.3"),
                ]
            )
            db.commit()
    finally:
        database.dispose()
    return url


def main():
    parser = argparse.ArgumentParser(description="Yeni ve ayrı mock demo veritabanı hazırla")
    parser.add_argument("--path", type=Path, required=True)
    args = parser.parse_args()
    try:
        url = prepare_demo(args.path)
    except (OSError, ValueError) as exc:
        parser.exit(1, f"Demo oluşturulamadı (var olan dosya korunur): {exc}\n")
    print(f"Demo hazır: {url}")
    print(
        "DATABASE_URL değerini bu dosyaya ayarlayın; MONITOR_MODE=mock ve MOCK_DEMO=true kullanın."
    )
    print("Hesabı python -m app.cli create-user ile kendi terminalinizde oluşturun.")


if __name__ == "__main__":
    main()
