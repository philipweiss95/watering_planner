from __future__ import annotations

import hashlib
import shutil
import sys
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VERSION = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
PACKAGE_ROOT = f"watering-planner-{VERSION}"
OUTPUT_DIR = ROOT / "dist"
ARCHIVE = OUTPUT_DIR / f"{PACKAGE_ROOT}.zip"
INCLUDES = (
    "server.py", "public", "updater", "home-assistant", "docs", "scripts", ".github",
    "Dockerfile", "docker-compose.yml", ".dockerignore", ".gitignore", ".env.synology.example",
    "README.md", "CHANGELOG.md", "VERSION",
)


def files_for(path: Path):
    if path.is_file():
        yield path
        return
    for candidate in sorted(path.rglob("*")):
        if candidate.is_file() and "__pycache__" not in candidate.parts and candidate.suffix not in {".pyc", ".sqlite3"}:
            yield candidate


def main() -> None:
    if not VERSION or any(character not in "0123456789.-abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ" for character in VERSION):
        raise SystemExit("Ungültige VERSION")
    shutil.rmtree(OUTPUT_DIR, ignore_errors=True)
    OUTPUT_DIR.mkdir()
    with zipfile.ZipFile(ARCHIVE, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for entry in INCLUDES:
            source = ROOT / entry
            if not source.exists():
                raise SystemExit(f"Release-Datei fehlt: {entry}")
            for file in files_for(source):
                relative = file.relative_to(ROOT)
                archive.write(file, Path(PACKAGE_ROOT) / relative)
    digest = hashlib.sha256(ARCHIVE.read_bytes()).hexdigest()
    checksum = ARCHIVE.with_suffix(f"{ARCHIVE.suffix}.sha256")
    checksum.write_text(f"{digest}  {ARCHIVE.name}\n", encoding="utf-8")
    print(ARCHIVE)
    print(checksum)


if __name__ == "__main__":
    sys.exit(main())
