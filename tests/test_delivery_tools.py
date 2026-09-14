import hashlib
import json
import subprocess
import sys
import zipfile

import pytest

from scripts.common import run, safe_env
from scripts.package import allowed_names
from scripts.verify import extract


def test_child_failure_is_propagated_and_only_its_process_runs(tmp_path):
    sentinel = tmp_path / "keep.txt"
    sentinel.write_text("untouched")
    with pytest.raises(subprocess.CalledProcessError) as error:
        run([sys.executable, "-c", "raise SystemExit(17)"], cwd=tmp_path)
    assert error.value.returncode == 17
    assert sentinel.read_text() == "untouched"


def test_verification_environment_cannot_inherit_secrets_or_python_injection(monkeypatch, tmp_path):
    for key in (
        "SMTP_PASSWORD",
        "NOTIFICATION_MODE",
        "MONITOR_MODE",
        "DATABASE_URL",
        "PYTHONPATH",
        "PYTHONHOME",
        "PIP_INDEX_URL",
        "PYTEST_ADDOPTS",
        "HTTP_PROXY",
    ):
        monkeypatch.setenv(key, "dangerous-sentinel")
    env = safe_env(tmp_path / "safe.db")
    assert "dangerous-sentinel" not in env.values()
    assert env["DATABASE_URL"].endswith("safe.db")
    assert env["MONITOR_MODE"] == "mock" and env["NOTIFICATION_MODE"] == "off"
    assert env["AVIT_NO_ENV_FILE"] == "1"


@pytest.mark.parametrize("name", ["../escape.py", "C:/escape.py", "dir\\escape.py"])
def test_zip_extractor_rejects_escape_paths(tmp_path, name):
    archive = tmp_path / "malicious.zip"
    content = b"pass"
    with zipfile.ZipFile(archive, "x") as out:
        out.writestr(name, content)
        out.writestr(
            "SOURCE-MANIFEST.json", json.dumps({name: hashlib.sha256(content).hexdigest()})
        )
    with pytest.raises(ValueError, match="ZIP"):
        extract(archive, tmp_path / "destination")
    assert not (tmp_path / "escape.py").exists()


def test_allowlist_never_adds_runtime_files_by_directory_scanning(tmp_path):
    (tmp_path / "release-files.txt").write_text("release-files.txt\napp.py\n")
    (tmp_path / "app.py").write_text("pass\n")
    for name in (".env", "real.db", "backup.manifest.json", "old.zip", "password.txt"):
        (tmp_path / name).write_text("must not package")
    assert allowed_names(tmp_path) == ["release-files.txt", "app.py"]
    (tmp_path / "release-files.txt").write_text(".env\n")
    with pytest.raises(ValueError):
        allowed_names(tmp_path)


def test_cli_failure_returns_nonzero_and_does_not_read_ambient_database(tmp_path):
    from scripts.common import ROOT

    protected = tmp_path / "real.db"
    protected.write_bytes(b"untouched")
    env = safe_env(protected)
    env.update(NOTIFICATION_MODE="smtp", SMTP_PASSWORD="never-log-this")
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "app.cli",
            "backup",
            "--source",
            str(tmp_path / "missing.db"),
            "--target",
            str(tmp_path / "backup.db"),
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert result.returncode == 1
    assert "never-log-this" not in result.stdout + result.stderr
    assert protected.read_bytes() == b"untouched"
    assert not (tmp_path / "backup.db").exists()
