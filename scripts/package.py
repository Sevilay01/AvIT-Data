"""Exclusive, source-only ZIP creation from an explicit reviewed file list."""

import argparse
import hashlib
import json
import secrets
import zipfile
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

from scripts.common import ROOT


def allowed_names(root):
    names = (root / "release-files.txt").read_text(encoding="utf-8").splitlines()
    if len(names) != len(set(names)) or not names:
        raise ValueError("Paket izin listesi boş veya yinelenmiş kayıt içeriyor.")
    for name in names:
        parts = PurePosixPath(name).parts
        if (
            "\\" in name
            or ":" in name
            or not parts
            or ".." in parts
            or name.startswith("/")
            or any(p.startswith(".env") and p != ".env.example" for p in parts)
        ):
            raise ValueError("Paket izin listesinde güvensiz yol.")
        if Path(name).suffix in {".db", ".sqlite", ".sqlite3", ".zip", ".log", ".partial"}:
            raise ValueError("Paket izin listesinde çalışma verisi.")
        path = root / name
        if not path.resolve().is_relative_to(root.resolve()) or any(
            parent.is_symlink() for parent in (path, *path.parents) if parent != root.parent
        ):
            raise ValueError("Paket dosyası kaynak kökünün dışında veya symlink.")
        if not path.is_file():
            raise ValueError("Paket izin listesindeki dosya bulunamadı: " + name)
    return names


def package(destination=None):
    from app.version import VERSION

    names = allowed_names(ROOT)
    manifest = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in names}
    if destination is None:
        (ROOT / "dist").mkdir(exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        destination = (
            ROOT / "dist" / f"AvITData-{VERSION}-source-{stamp}-{secrets.token_hex(2)}.zip"
        )
    destination = Path(destination).resolve()
    with zipfile.ZipFile(destination, "x", compression=zipfile.ZIP_DEFLATED) as output:
        for name in names:
            output.write(ROOT / name, name)
        output.writestr("SOURCE-MANIFEST.json", json.dumps(manifest, indent=2))
    with zipfile.ZipFile(destination) as archive:
        if archive.testzip() is not None:
            raise RuntimeError("ZIP CRC doğrulaması başarısız.")
        for name, digest in manifest.items():
            if hashlib.sha256(archive.read(name)).hexdigest() != digest:
                raise RuntimeError("Paketleme sırasında kaynak değişti.")
    return {
        "archive": str(destination),
        "sha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
        "file_count": len(names),
        "crc": "passed",
        "manifest": manifest,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = package(args.output)
    evidence = Path(result["archive"]).with_suffix(".verification.json")
    with evidence.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, indent=2)
    print(result["archive"])


if __name__ == "__main__":
    main()
