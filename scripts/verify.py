"""One local/CI entry point; each failed child gives this script a failing exit."""

import argparse
import hashlib
import json
import platform
import shutil
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
import zipfile
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

from scripts.common import ROOT, configure_console, require_python312, run, safe_env, venv_python


def local(evidence):
    require_python312()
    with tempfile.TemporaryDirectory(prefix="avit-check-") as name:
        env = safe_env(Path(name) / "schema.db")
        junit = Path(name) / "tests.xml"
        run(
            [
                sys.executable,
                "-m",
                "pytest",
                "--junitxml",
                junit,
                "--basetemp",
                Path(name) / "pytest",
            ],
            env=env,
        )
        suite = ET.parse(junit).getroot().find("testsuite")
        test_results = dict(suite.attrib)
        run([sys.executable, "-m", "ruff", "check", "app", "tests", "alembic", "scripts"], env=env)
        run([sys.executable, "-m", "pip", "--isolated", "check"], env=env)
        run([sys.executable, "-m", "scripts.lock_dependencies", "--check"], env=env)
        run([sys.executable, "-m", "alembic", "upgrade", "head"], env=env)
        run([sys.executable, "-m", "alembic", "check"], env=env)
        node = shutil.which("node")
        if not node:
            raise RuntimeError("JavaScript sözdizimi kontrolü için Node.js gerekli.")
        for file in sorted((ROOT / "app/static").glob("*.js")):
            run([node, "--check", file], env=env)
    evidence.write_text(
        json.dumps(
            {
                "status": "passed",
                "scope": "local quality suite",
                "python": sys.version,
                "platform": platform.platform(),
                "utc": datetime.now(UTC).isoformat(),
                "tests": test_results,
            }
        ),
        encoding="utf-8",
    )


def extract(archive_path, destination):
    with zipfile.ZipFile(archive_path) as archive:
        entries = archive.infolist()
        if len({i.filename for i in entries}) != len(entries):
            raise ValueError("Yinelenmiş ZIP girdisi.")
        if sum(i.file_size for i in entries) > 50 * 1024 * 1024:
            raise ValueError("Kaynak ZIP boyut sınırı aşıldı.")
        manifest = json.loads(archive.read("SOURCE-MANIFEST.json"))
        if set(archive.namelist()) != {*manifest, "SOURCE-MANIFEST.json"}:
            raise ValueError("ZIP girdileri manifest ile uyuşmuyor.")
        for entry in entries:
            name = entry.filename
            path = PurePosixPath(name)
            if (
                path.is_absolute()
                or ".." in path.parts
                or "\\" in name
                or ":" in name
                or entry.is_dir()
                or entry.external_attr >> 28 == 0xA
            ):
                raise ValueError("Güvensiz ZIP yolu.")
            content = archive.read(entry)
            if name in manifest and hashlib.sha256(content).hexdigest() != manifest[name]:
                raise ValueError("ZIP SHA-256 denetimi başarısız.")
            target = destination / name
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("xb") as stream:
                stream.write(content)
    from scripts.package import allowed_names

    if set(allowed_names(destination)) != set(manifest):
        raise ValueError("ZIP izin listesi manifest ile uyuşmuyor.")
    return manifest


def delivery(archive, work_dir, python, evidence):
    archive = archive.resolve(strict=True)
    work_dir = work_dir.resolve()
    work_dir.mkdir()  # Never merge into or remove an existing user's directory.
    started = time.monotonic()
    extracted = work_dir / "source"
    extracted.mkdir()
    manifest = extract(archive, extracted)
    env = safe_env()
    run([python, "-c", "import sys; assert sys.version_info[:2] == (3, 12)"], cwd=work_dir, env=env)
    runtime = work_dir / "runtime-venv"
    run([python, "-m", "venv", runtime], cwd=work_dir, env=env)
    runtime_python = venv_python(runtime)
    run(
        [
            runtime_python,
            "-m",
            "pip",
            "--isolated",
            "install",
            "--disable-pip-version-check",
            "--only-binary=:all:",
            "-r",
            extracted / "requirements.txt",
        ],
        cwd=extracted,
        env=env,
        timeout=600,
    )
    run([runtime_python, "-m", "pip", "--isolated", "check"], cwd=extracted, env=env)
    runtime_evidence = work_dir / "runtime-evidence.json"
    run(
        [
            runtime_python,
            "-m",
            "scripts.runtime_smoke",
            "--runtime-only",
            "--evidence",
            runtime_evidence,
        ],
        cwd=extracted,
        env=env,
        timeout=120,
    )
    development = work_dir / "development-venv"
    run([python, "-m", "venv", development], cwd=work_dir, env=env)
    dev_python = venv_python(development)
    run(
        [
            dev_python,
            "-m",
            "pip",
            "--isolated",
            "install",
            "--disable-pip-version-check",
            "--only-binary=:all:",
            "-r",
            extracted / "requirements-dev.txt",
        ],
        cwd=extracted,
        env=env,
        timeout=600,
    )
    quality_evidence = work_dir / "quality-evidence.json"
    run(
        [dev_python, "-m", "scripts.verify", "local", "--evidence", quality_evidence],
        cwd=extracted,
        env=env,
        timeout=600,
    )
    for name, digest in manifest.items():
        if hashlib.sha256((extracted / name).read_bytes()).hexdigest() != digest:
            raise RuntimeError("Doğrulama kaynak ZIP dosyalarını değiştirdi.")
    result = {
        "status": "passed",
        "archive": str(archive),
        "archive_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
        "work_dir": str(work_dir),
        "source_manifest_unchanged": True,
        "runtime": json.loads(runtime_evidence.read_text(encoding="utf-8")),
        "quality": json.loads(quality_evidence.read_text(encoding="utf-8")),
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "remote_ci": "not_run",
    }
    evidence.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")


def main():
    configure_console()
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    quality = commands.add_parser("local")
    quality.add_argument("--evidence", type=Path, default=ROOT / "build/local-quality.json")
    release = commands.add_parser("delivery")
    release.add_argument("--archive", type=Path, required=True)
    release.add_argument("--work-dir", type=Path, required=True)
    release.add_argument("--python", type=Path, default=Path(sys.executable))
    release.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args()
    args.evidence = args.evidence.resolve()
    args.evidence.parent.mkdir(parents=True, exist_ok=True)
    if args.evidence.exists():
        parser.error("Kanıt dosyası zaten var; yeni bir ad seçin.")
    try:
        if args.command == "local":
            local(args.evidence)
        else:
            delivery(args.archive, args.work_dir, args.python, args.evidence)
    except (subprocess.SubprocessError, OSError, RuntimeError, ValueError) as exc:
        # No command carries passwords, tokens or SMTP settings.
        print(f"Doğrulama başarısız: {type(exc).__name__}", file=sys.stderr)
        return 1
    print("Doğrulama geçti: " + str(args.evidence))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
