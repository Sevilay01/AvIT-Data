"""OS-owned lock: released even after a crash; the lock file is never removed."""

import os
from pathlib import Path

from sqlalchemy.engine import make_url


class DatabaseProcessLock:
    def __init__(self, url):
        parsed = make_url(url)
        if parsed.get_backend_name() != "sqlite" or not parsed.database:
            raise ValueError("Bu teslim dosya tabanlı SQLite gerektirir.")
        if parsed.database == ":memory:":
            raise ValueError("Süreç kilidi için dosya tabanlı SQLite kullanın.")
        self.path = (
            Path(parsed.database).resolve().with_suffix(Path(parsed.database).suffix + ".lock")
        )
        self.handle = None

    def acquire(self):
        handle = self.path.open("a+b")
        try:
            handle.seek(0, 2)
            if handle.tell() == 0:
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            handle.close()
            raise RuntimeError("Aynı SQLite veritabanı başka bir uygulama sürecinde açık.") from exc
        self.handle = handle

    def release(self):
        if self.handle:
            self.handle.close()
            self.handle = None
