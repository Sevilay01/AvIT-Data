"""Standard-library process helpers; never inherit application or pip settings."""

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def configure_console():
    """Keep Turkish diagnostic output usable under redirected Windows consoles."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")


def safe_env(database=None):
    allowed = {
        "SYSTEMROOT",
        "WINDIR",
        "COMSPEC",
        "PATH",
        "PATHEXT",
        "TEMP",
        "TMP",
        "TMPDIR",
        "HOME",
        "USERPROFILE",
        "LOCALAPPDATA",
        "APPDATA",
        "LANG",
        "LC_ALL",
        "SYSTEMDRIVE",
    }
    env = {key: value for key, value in os.environ.items() if key.upper() in allowed}
    env.update(
        AVIT_NO_ENV_FILE="1",
        MONITOR_MODE="mock",
        NOTIFICATION_MODE="off",
        MOCK_DEMO="false",
        PYTHONUTF8="1",
        PYTHONNOUSERSITE="1",
        PYTEST_DISABLE_PLUGIN_AUTOLOAD="1",
        PIP_CONFIG_FILE=os.devnull,
    )
    if database is not None:
        env["DATABASE_URL"] = "sqlite:///" + Path(database).resolve().as_posix()
    else:
        env["DATABASE_URL"] = "sqlite:///:memory:"
    return env


def run(args, *, cwd=ROOT, env=None, timeout=300):
    print("Çalıştırılıyor: " + " ".join(str(a) for a in args), flush=True)
    completed = subprocess.run(
        [str(a) for a in args],
        cwd=cwd,
        env=env or safe_env(),
        check=False,
        timeout=timeout,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    print(completed.stdout, end="", flush=True)
    completed.check_returncode()


def venv_python(path):
    return Path(path) / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def require_python312():
    if sys.version_info[:2] != (3, 12):
        raise RuntimeError("Bu doğrulama CPython 3.12 gerektirir.")
