"""Online SQLite backup, completion manifest, exclusive publication and quarantine restore."""

import hashlib
import json
import math
import os
import sqlite3
import tempfile
import time
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

from app.version import SCHEMA_HEAD, VERSION


class BackupError(ValueError):
    pass


def deadline_check(deadline):
    if time.monotonic() >= deadline:
        raise BackupError("Yedekleme/geri yükleme süre sınırı aşıldı.")


def sha256(path, deadline=None):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            if deadline is not None:
                deadline_check(deadline)
            digest.update(chunk)
    return digest.hexdigest()


def inspect_database(conn, deadline):
    conn.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
    deadline_check(deadline)
    if conn.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
        raise BackupError("SQLite bütünlük denetimi başarısız.")
    if conn.execute("PRAGMA foreign_key_check").fetchall():
        raise BackupError("SQLite foreign key denetimi başarısız.")
    rows = conn.execute("SELECT version_num FROM alembic_version").fetchall()
    if len(rows) != 1:
        raise BackupError("Tek migration sürümü bulunamadı.")
    counts = {}
    for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
        quoted = '"' + name.replace('"', '""') + '"'
        counts[name] = conn.execute(f"SELECT COUNT(*) FROM {quoted}").fetchone()[0]
    deadline_check(deadline)
    return rows[0][0], counts


def manifest_path(path):
    return Path(str(path) + ".manifest.json")


def _copy(source, target, timeout, *, restore=False):
    if not math.isfinite(timeout) or not 0 < timeout <= 3600:
        raise BackupError("Süre sınırı 0–3600 saniye aralığında olmalı.")
    started = time.monotonic()
    deadline = started + timeout
    source = Path(source).resolve(strict=True)
    target = Path(target).absolute()
    manifest = manifest_path(target)
    if target.exists() or target.is_symlink() or manifest.exists() or manifest.is_symlink():
        raise BackupError("Hedef veya manifest zaten var; üzerine yazılmadı.")
    if source == target.resolve():
        raise BackupError("Kaynak ve hedef farklı dosyalar olmalı.")
    if restore:
        try:
            saved = json.loads(manifest_path(source).read_text(encoding="utf-8"))
            if saved["status"] != "complete" or saved["sha256"] != sha256(source, deadline):
                raise BackupError("Yedek SHA-256/başarı manifesti doğrulanamadı.")
        except (OSError, KeyError, json.JSONDecodeError) as exc:
            raise BackupError("Tamamlanmış yedek manifesti gerekli.") from exc
    temp_files = []
    published = []
    try:
        fd, name = tempfile.mkstemp(prefix=".avit-backup-", suffix=".partial", dir=target.parent)
        os.close(fd)
        partial = Path(name)
        temp_files.append(partial)
        with (
            closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True, timeout=0.1)) as src,
            closing(sqlite3.connect(partial, timeout=0.1)) as dst,
        ):
            src.backup(dst, pages=128, sleep=0.01, progress=lambda *_: deadline_check(deadline))
            revision, counts = inspect_database(dst, deadline)
            if restore:
                if revision != SCHEMA_HEAD:
                    raise BackupError("Tatbikat için yedeğin migration sürümü güncel olmalı.")
                now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
                dst.execute(
                    "INSERT OR REPLACE INTO recovery_guard (id, restored_at) VALUES (1, ?)",
                    (now,),
                )
                dst.execute(
                    "UPDATE user_sessions SET revoked_at=? WHERE revoked_at IS NULL", (now,)
                )
                dst.commit()
                inspect_database(dst, deadline)
        data = {
            "status": "complete",
            "kind": "restore-drill" if restore else "backup",
            "application_version": VERSION,
            "migration": revision,
            "created_at_utc": datetime.now(UTC).isoformat(),
            "sha256": sha256(partial, deadline),
            "size_bytes": partial.stat().st_size,
            "source_snapshot_counts": counts,
            "integrity": "ok",
            "foreign_keys": "ok",
            "recovery_hold": restore,
            "elapsed_seconds": round(time.monotonic() - started, 6),
        }
        with partial.open("r+b") as stream:
            os.fsync(stream.fileno())
        fd, name = tempfile.mkstemp(prefix=".avit-manifest-", suffix=".partial", dir=target.parent)
        temp_manifest = Path(name)
        temp_files.append(temp_manifest)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        deadline_check(deadline)
        # Hard links publish atomically and fail if a destination appeared in the
        # meantime. Unlike replace/rename, they never overwrite another backup.
        os.link(partial, target)
        published.append(target)
        os.link(temp_manifest, manifest)
        published.append(manifest)
        deadline_check(deadline)
        return data
    except (sqlite3.Error, OSError) as exc:
        raise BackupError("Yedek işlemi başarısız (kilit, dosya veya SQLite hatası).") from exc
    finally:
        # Missing manifest means incomplete even after a hard kill. Only files
        # created by this invocation are candidates for normal failure cleanup.
        import sys

        if sys.exc_info()[0] is not None:
            for path in reversed(published):
                path.unlink(missing_ok=True)
        for path in temp_files:
            path.unlink(missing_ok=True)


def backup_database(source, target, *, timeout=30):
    return _copy(source, target, timeout)


def restore_drill(source, target, *, timeout=30):
    return _copy(source, target, timeout, restore=True)
