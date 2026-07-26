from __future__ import annotations

import hashlib
import re
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
    "server.py", "watering_backend", "public", "updater", "home-assistant", "docs", "scripts", ".github",
    "Dockerfile", "docker-compose.yml", ".dockerignore", ".gitignore", ".env.synology.example",
    "README.md", "CHANGELOG.md", "VERSION", "package.json",
)
VERSION_PATTERN = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
EXCLUDED_SUFFIXES = {".pyc", ".sqlite3"}
EXCLUDED_PARTS = {"__pycache__"}


class ReleasePackagingError(ValueError):
    pass


def validate_version(version: str) -> str:
    if not VERSION_PATTERN.fullmatch(version):
        raise ReleasePackagingError(f"Ungueltige Release-Version: {version!r}")
    return version


def _assert_safe_source(path: Path, root: Path) -> None:
    if path.is_symlink():
        raise ReleasePackagingError(f"Symbolische Links sind im Release nicht erlaubt: {path.relative_to(root)}")
    try:
        path.resolve(strict=True).relative_to(root.resolve(strict=True))
    except (FileNotFoundError, ValueError) as exc:
        raise ReleasePackagingError(f"Release-Pfad liegt ausserhalb des Projekts: {path}") from exc


def files_for(path: Path, root: Path = ROOT):
    _assert_safe_source(path, root)
    if path.is_file():
        yield path
        return
    for candidate in sorted(path.rglob("*")):
        _assert_safe_source(candidate, root)
        if (
            candidate.is_file()
            and not EXCLUDED_PARTS.intersection(candidate.parts)
            and candidate.suffix not in EXCLUDED_SUFFIXES
        ):
            yield candidate


def build_package(
    root: Path = ROOT,
    version: str = VERSION,
    output_dir: Path = OUTPUT_DIR,
    archive_path: Path | None = None,
) -> tuple[Path, Path]:
    root = root.resolve(strict=True)
    version = validate_version(version)
    package_root = f"watering-planner-{version}"
    archive_path = archive_path or output_dir / f"{package_root}.zip"
    seen_names: set[str] = set()

    shutil.rmtree(output_dir, ignore_errors=True)
    output_dir.mkdir(parents=True)
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for entry in INCLUDES:
            source = root / entry
            if source.is_symlink():
                raise ReleasePackagingError(f"Symbolische Links sind im Release nicht erlaubt: {entry}")
            if not source.exists():
                raise ReleasePackagingError(f"Release-Datei fehlt: {entry}")
            for file in files_for(source, root):
                relative = file.relative_to(root)
                archive_name = f"{package_root}/{relative.as_posix()}"
                normalized_name = archive_name.casefold()
                if normalized_name in seen_names:
                    raise ReleasePackagingError(f"Doppelter Release-Pfad: {archive_name}")
                seen_names.add(normalized_name)
                archive.write(file, archive_name)

    digest = hashlib.sha256(archive_path.read_bytes()).hexdigest()
    checksum = archive_path.with_suffix(f"{archive_path.suffix}.sha256")
    checksum.write_text(f"{digest}  {archive_path.name}\n", encoding="utf-8")
    return archive_path, checksum


def main() -> None:
    try:
        archive, checksum = build_package(
            ROOT,
            version=VERSION,
            output_dir=OUTPUT_DIR,
            archive_path=ARCHIVE,
        )
    except ReleasePackagingError as exc:
        raise SystemExit(str(exc)) from exc
    print(archive)
    print(checksum)


if __name__ == "__main__":
    sys.exit(main())
