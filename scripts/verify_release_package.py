from __future__ import annotations

import argparse
import ast
import hashlib
import hmac
import json
import re
import stat
import subprocess
import sys
import zipfile
from pathlib import Path, PurePosixPath

try:
    from package_release import INCLUDES, files_for, validate_version
except ImportError:  # pragma: no cover - used when imported as scripts.verify_release_package
    from scripts.package_release import INCLUDES, files_for, validate_version


ROOT = Path(__file__).resolve().parents[1]
BRIDGE_TAG = "v1.4.3"
BRIDGE_TAG_OBJECT = "f08acb6c5216c987cb6581513de499c9360d9f1a"
BRIDGE_COMMIT = "e02ceb198264104fd8f2bc68eb8db7b24ac00dc8"
LEGACY_TAG = "v1.4.2"
LEGACY_TAG_OBJECT = "938af782a7a7aebfa8931995b39ff0ed45bb25c2"
LEGACY_COMMIT = "b3fad615c232b97e2354dfd72b0013b80e6ed6d0"
BRIDGE_RELEASE_PUBLISHED_AT = "2026-07-25T18:25:35Z"
BRIDGE_ARCHIVE_NAME = "watering-planner-1.4.3.zip"
BRIDGE_ARCHIVE_SIZE = 403_087
BRIDGE_ARCHIVE_FILE_COUNT = 26
BRIDGE_ARCHIVE_SHA256 = "78a38685d19c0952541bf96f9286b8eb11c5df0c2edc8de3a9bd285ed9f5ce7a"
BRIDGE_CHECKSUM_NAME = f"{BRIDGE_ARCHIVE_NAME}.sha256"
BRIDGE_CHECKSUM_SIZE = 93
BRIDGE_CHECKSUM_SHA256 = "32dc99123f053d5530292023bb5e951a83c44c068f745e138cb8728ec123f0bf"
HEX_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class ReleaseVerificationError(ValueError):
    pass


def _fail(message: str) -> None:
    raise ReleaseVerificationError(message)


def _git(root: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", *arguments],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode:
        _fail(result.stderr.strip() or f"Git-Pruefung fehlgeschlagen: {' '.join(arguments)}")
    return result.stdout.strip()


def _git_bytes(root: Path, *arguments: str) -> bytes:
    result = subprocess.run(
        ["git", *arguments],
        cwd=root,
        check=False,
        capture_output=True,
    )
    if result.returncode:
        _fail(
            result.stderr.decode("utf-8", errors="replace").strip()
            or f"Git-Pruefung fehlgeschlagen: {' '.join(arguments)}"
        )
    return result.stdout


def verify_tag(
    root: Path,
    tag: str,
    expected_version: str,
    expected_commit: str | None = None,
    require_head: bool = False,
) -> str:
    if tag != f"v{expected_version}":
        _fail(f"Tag {tag!r} passt nicht zu Version {expected_version!r}")
    commit = _git(root, "rev-parse", "--verify", f"refs/tags/{tag}^{{commit}}")
    tagged_version = _git(root, "show", f"{tag}:VERSION")
    if tagged_version != expected_version:
        _fail(f"{tag} enthaelt VERSION {tagged_version!r} statt {expected_version!r}")
    if expected_commit and commit != expected_commit:
        _fail(f"{tag} zeigt auf {commit} statt auf {expected_commit}")
    if require_head:
        head = _git(root, "rev-parse", "HEAD")
        if commit != head:
            _fail(f"Release-Tag {tag} zeigt nicht auf HEAD ({head})")
    return commit


def verify_bridge_tag(
    root: Path = ROOT,
    tag: str = BRIDGE_TAG,
    expected_tag_object: str = BRIDGE_TAG_OBJECT,
    expected_commit: str = BRIDGE_COMMIT,
) -> str:
    tag_object = _git(root, "rev-parse", f"refs/tags/{tag}")
    if tag_object != expected_tag_object:
        _fail(f"{tag} wurde veraendert: Tag-Objekt {tag_object} statt {expected_tag_object}")
    return verify_tag(root, tag, tag.removeprefix("v"), expected_commit)


def verify_bridge_history(
    root: Path,
    bridge_commit: str,
    release_commit: str = "HEAD",
) -> str:
    """Prove that the immutable updater bridge is part of the release history."""
    _git(root, "merge-base", "--is-ancestor", bridge_commit, release_commit)
    return _git(root, "rev-parse", release_commit)


def _managed_paths_at(root: Path, revision: str) -> tuple[str, ...]:
    source = _git_bytes(
        root,
        "show",
        f"{revision}:updater/updater.py",
    ).decode("utf-8")
    tree = ast.parse(source)
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(
            isinstance(target, ast.Name) and target.id == "MANAGED_PATHS"
            for target in node.targets
        ):
            continue
        value = ast.literal_eval(node.value)
        if (
            isinstance(value, tuple)
            and all(isinstance(item, str) for item in value)
        ):
            return value
    _fail(f"MANAGED_PATHS fehlt im Updater von {revision}")


def verify_bridge_update_path(
    root: Path,
    bridge_commit: str = BRIDGE_COMMIT,
) -> dict[str, object]:
    """Prove why 1.4.3 is required between the legacy and modular layouts."""
    legacy_tag_object = _git(root, "rev-parse", f"refs/tags/{LEGACY_TAG}")
    if legacy_tag_object != LEGACY_TAG_OBJECT:
        _fail(
            f"Tag-Objekt {LEGACY_TAG} ist {legacy_tag_object}, "
            f"erwartet wird {LEGACY_TAG_OBJECT}"
        )
    legacy_commit = verify_tag(
        root,
        LEGACY_TAG,
        LEGACY_TAG.removeprefix("v"),
        LEGACY_COMMIT,
    )
    _git(root, "merge-base", "--is-ancestor", legacy_commit, bridge_commit)
    legacy_paths = _managed_paths_at(root, legacy_commit)
    bridge_paths = _managed_paths_at(root, bridge_commit)
    modular_paths = {"watering_backend", "package.json"}
    if modular_paths.intersection(legacy_paths):
        _fail("Der v1.4.2-Updater darf die modularen Zielpfade noch nicht verwalten")
    if not modular_paths.issubset(bridge_paths):
        _fail("Der v1.4.3-Updater verwaltet die modularen Zielpfade nicht vollstaendig")
    return {
        "legacy_commit": legacy_commit,
        "legacy_managed_paths": legacy_paths,
        "bridge_managed_paths": bridge_paths,
    }


def verify_bridge_release_metadata(metadata_path: Path) -> dict[str, object]:
    """Verify authenticated GitHub metadata for the published bridge assets."""
    try:
        release = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ReleaseVerificationError("Release-Metadaten fuer v1.4.3 sind ungueltig") from exc
    if (
        release.get("tag_name") != BRIDGE_TAG
        or release.get("published_at") != BRIDGE_RELEASE_PUBLISHED_AT
        or release.get("draft")
        or release.get("prerelease")
    ):
        _fail("Release-Metadaten fuer v1.4.3 stimmen nicht mit der publizierten Bridge ueberein")
    release_assets = release.get("assets")
    if not isinstance(release_assets, list):
        _fail("Release-Metadaten fuer v1.4.3 enthalten keine Asset-Liste")
    assets = {
        str(asset.get("name")): asset
        for asset in release_assets
        if isinstance(asset, dict)
    }
    expected = {
        BRIDGE_ARCHIVE_NAME: (
            f"sha256:{BRIDGE_ARCHIVE_SHA256}",
            BRIDGE_ARCHIVE_SIZE,
        ),
        BRIDGE_CHECKSUM_NAME: (
            f"sha256:{BRIDGE_CHECKSUM_SHA256}",
            BRIDGE_CHECKSUM_SIZE,
        ),
    }
    for name, (digest, size) in expected.items():
        asset = assets.get(name)
        if not asset or asset.get("digest") != digest:
            _fail(f"Publiziertes Bridge-Asset {name} hat nicht den erwarteten SHA-256")
        if size is not None:
            try:
                actual_size = int(asset.get("size", -1))
            except (TypeError, ValueError):
                actual_size = -1
            if actual_size != size:
                _fail(f"Publiziertes Bridge-Asset {name} hat nicht die erwartete Groesse")
    return {
        "published_at": release["published_at"],
        "archive_sha256": BRIDGE_ARCHIVE_SHA256,
        "checksum_sha256": BRIDGE_CHECKSUM_SHA256,
    }


def _raw_central_directory_names(archive_path: Path) -> list[str]:
    data = archive_path.read_bytes()
    end_offset = data.rfind(b"PK\x05\x06")
    if end_offset < 0 or end_offset + 22 > len(data):
        _fail("ZIP-Endverzeichnis fehlt")
    entry_count = int.from_bytes(data[end_offset + 10:end_offset + 12], "little")
    directory_offset = int.from_bytes(data[end_offset + 16:end_offset + 20], "little")
    if entry_count == 0xFFFF or directory_offset == 0xFFFFFFFF:
        _fail("ZIP64-Archive sind fuer Releases nicht erlaubt")

    names = []
    cursor = directory_offset
    for _ in range(entry_count):
        if data[cursor:cursor + 4] != b"PK\x01\x02" or cursor + 46 > len(data):
            _fail("Ungueltiges ZIP-Zentralverzeichnis")
        flags = int.from_bytes(data[cursor + 8:cursor + 10], "little")
        name_length = int.from_bytes(data[cursor + 28:cursor + 30], "little")
        extra_length = int.from_bytes(data[cursor + 30:cursor + 32], "little")
        comment_length = int.from_bytes(data[cursor + 32:cursor + 34], "little")
        name_start = cursor + 46
        name_end = name_start + name_length
        try:
            names.append(data[name_start:name_end].decode("utf-8" if flags & 0x800 else "cp437"))
        except UnicodeError as exc:
            raise ReleaseVerificationError("Ungueltiger Dateiname im ZIP-Zentralverzeichnis") from exc
        cursor = name_end + extra_length + comment_length
    return names


def validate_archive_layout(archive_path: Path, expected_root: str) -> dict[str, zipfile.ZipInfo]:
    try:
        archive = zipfile.ZipFile(archive_path)
    except (OSError, zipfile.BadZipFile) as exc:
        raise ReleaseVerificationError(f"Ungueltiges ZIP: {archive_path.name}") from exc

    with archive:
        raw_names = _raw_central_directory_names(archive_path)
        infos = archive.infolist()
        if len(raw_names) != len(infos):
            _fail("ZIP-Dateiliste und Zentralverzeichnis widersprechen sich")
        files: dict[str, zipfile.ZipInfo] = {}
        roots: set[str] = set()
        seen_casefold: set[str] = set()
        for raw_name, info in zip(raw_names, infos):
            name = raw_name
            raw_parts = name.split("/")
            if (
                not name
                or "\\" in name
                or "\x00" in name
                or name.startswith("/")
                or re.match(r"^[A-Za-z]:", name)
                or any(part in {"", ".", ".."} for part in raw_parts[:-1])
                or any(part in {".", ".."} for part in raw_parts)
            ):
                _fail(f"Unsicherer ZIP-Pfad: {name!r}")
            path = PurePosixPath(name)
            if path.is_absolute() or len(path.parts) < 2:
                _fail(f"ZIP-Pfad liegt nicht unter einer Paketwurzel: {name!r}")
            roots.add(path.parts[0])
            normalized = name.casefold()
            if normalized in seen_casefold:
                _fail(f"Doppelter ZIP-Pfad: {name!r}")
            seen_casefold.add(normalized)
            unix_mode = info.external_attr >> 16
            if stat.S_ISLNK(unix_mode):
                _fail(f"Symbolischer Link im ZIP: {name!r}")
            if not info.is_dir():
                files[name] = info

        if roots != {expected_root}:
            _fail(f"ZIP-Wurzel ist {sorted(roots)!r}, erwartet wird {expected_root!r}")
        if not files:
            _fail("Release-ZIP enthaelt keine Dateien")
        return files


def expected_archive_sources(root: Path, version: str) -> dict[str, Path]:
    package_root = f"watering-planner-{version}"
    expected: dict[str, Path] = {}
    seen_casefold: set[str] = set()
    for entry in INCLUDES:
        source = root / entry
        if source.is_symlink() or not source.exists():
            _fail(f"Release-Quelle fehlt oder ist ein Symlink: {entry}")
        for file in files_for(source, root):
            name = f"{package_root}/{file.relative_to(root).as_posix()}"
            normalized = name.casefold()
            if normalized in seen_casefold:
                _fail(f"Doppelter Quellpfad fuer Release: {name}")
            seen_casefold.add(normalized)
            expected[name] = file
    return expected


def expected_archive_files(root: Path, version: str) -> set[str]:
    return set(expected_archive_sources(root, version))


def verify_checksum(archive_path: Path, checksum_path: Path) -> str:
    try:
        content = checksum_path.read_text(encoding="ascii")
    except (OSError, UnicodeError) as exc:
        raise ReleaseVerificationError(f"SHA-256-Datei fehlt oder ist unlesbar: {checksum_path}") from exc
    match = re.fullmatch(r"([0-9a-f]{64})  ([^\r\n]+)\n", content)
    if not match or not HEX_SHA256.fullmatch(match.group(1)):
        _fail("SHA-256-Datei hat nicht das erwartete strikte Format")
    expected_digest, expected_name = match.groups()
    if expected_name != archive_path.name:
        _fail(f"SHA-256-Datei verweist auf {expected_name!r} statt {archive_path.name!r}")
    actual_digest = hashlib.sha256(archive_path.read_bytes()).hexdigest()
    if not hmac.compare_digest(actual_digest, expected_digest):
        _fail("SHA-256-Pruefsumme stimmt nicht")
    return actual_digest


def verify_bridge_release_assets(
    archive_path: Path,
    checksum_path: Path,
    *,
    root: Path = ROOT,
    bridge_commit: str = BRIDGE_COMMIT,
) -> dict[str, object]:
    """Verify downloaded v1.4.3 assets against their pinned published hashes."""
    if archive_path.name != BRIDGE_ARCHIVE_NAME:
        _fail(f"Unerwarteter Bridge-Paketname: {archive_path.name}")
    if checksum_path.name != BRIDGE_CHECKSUM_NAME:
        _fail(f"Unerwarteter Bridge-Pruefsummenname: {checksum_path.name}")
    archive_digest = verify_checksum(archive_path, checksum_path)
    checksum_digest = hashlib.sha256(checksum_path.read_bytes()).hexdigest()
    if archive_digest != BRIDGE_ARCHIVE_SHA256:
        _fail("Das heruntergeladene v1.4.3-Paket stimmt nicht mit dem publizierten SHA-256 ueberein")
    if checksum_digest != BRIDGE_CHECKSUM_SHA256:
        _fail("Die heruntergeladene v1.4.3-Pruefsumme stimmt nicht mit dem publizierten SHA-256 ueberein")
    if archive_path.stat().st_size != BRIDGE_ARCHIVE_SIZE:
        _fail("Das heruntergeladene v1.4.3-Paket hat nicht die publizierte Groesse")
    package_root = f"watering-planner-{BRIDGE_TAG.removeprefix('v')}"
    compared_files = 0
    try:
        with zipfile.ZipFile(archive_path) as archive:
            for member in archive.infolist():
                if member.is_dir():
                    continue
                path = PurePosixPath(member.filename)
                if (
                    len(path.parts) < 2
                    or path.parts[0] != package_root
                    or path.is_absolute()
                    or ".." in path.parts
                ):
                    _fail("Das publizierte v1.4.3-Paket hat eine ungueltige Wurzel")
                relative = PurePosixPath(*path.parts[1:]).as_posix()
                tagged_source = _git_bytes(
                    root,
                    "show",
                    f"{bridge_commit}:{relative}",
                )
                if archive.read(member) != tagged_source:
                    _fail(
                        "Bridge-Asset stimmt nicht mit dem unveraenderlichen "
                        f"Tag-Inhalt ueberein: {relative}"
                    )
                compared_files += 1
    except zipfile.BadZipFile as exc:
        raise ReleaseVerificationError("Das publizierte v1.4.3-Paket ist kein gueltiges ZIP") from exc
    if compared_files != BRIDGE_ARCHIVE_FILE_COUNT:
        _fail(
            "Das publizierte v1.4.3-Paket enthaelt "
            f"{compared_files} statt {BRIDGE_ARCHIVE_FILE_COUNT} Dateien"
        )
    return {
        "archive_sha256": archive_digest,
        "checksum_sha256": checksum_digest,
        "archive_size": archive_path.stat().st_size,
        "source_file_count": compared_files,
    }


def _archive_text(archive: zipfile.ZipFile, name: str) -> str:
    try:
        return archive.read(name).decode("utf-8")
    except KeyError as exc:
        raise ReleaseVerificationError(f"Release-Datei fehlt: {name}") from exc
    except UnicodeError as exc:
        raise ReleaseVerificationError(f"Release-Datei ist kein UTF-8: {name}") from exc


def _verify_versioned_assets(archive: zipfile.ZipFile, package_root: str, version: str) -> None:
    def text(relative: str) -> str:
        return _archive_text(archive, f"{package_root}/{relative}")

    compose = text("docker-compose.yml")
    for image in ("watering-planner", "watering-planner-updater"):
        marker = f"image: {image}:{version}"
        if compose.count(marker) != 1:
            _fail(f"Compose-Imageversion fehlt oder ist nicht eindeutig: {marker}")

    changelog = text("CHANGELOG.md")
    if not re.search(rf"(?m)^## \[{re.escape(version)}\](?:\s+-\s+[^\n]+)?\s*$", changelog):
        _fail(f"CHANGELOG enthaelt keinen Eintrag fuer {version}")

    index = text("public/index.html")
    styles = text("public/styles.css")
    app = text("public/app.js")
    updater = text("public/js/updater.js")
    service_worker = text("public/sw.js")

    for source_name, source in (
        ("public/index.html", index),
        ("public/styles.css", styles),
        ("public/sw.js", service_worker),
    ):
        query_versions = re.findall(r"\?v=([0-9A-Za-z.-]+)", source)
        if not query_versions or any(candidate != version for candidate in query_versions):
            _fail(f"Versionsparameter in {source_name} passen nicht zu {version}")

    for marker in (f"/styles.css?v={version}", f"/app.js?v={version}"):
        if marker not in index:
            _fail(f"HTML-Assetreferenz fehlt: {marker}")
        if f'"{marker}"' not in service_worker:
            _fail(f"PWA-Assetreferenz fehlt: {marker}")
    if f'const CACHE_NAME = "watering-planner-{version}";' not in service_worker:
        _fail("PWA-Cacheversion passt nicht zur Release-Version")
    if f'state.version || "{version}"' not in app:
        _fail("Frontend-Versionsfallback passt nicht zur Release-Version")
    if f'state?.version || "{version}"' not in updater:
        _fail("Updater-Versionsfallback passt nicht zur Release-Version")

    shell_match = re.search(r"const APP_SHELL = \[(.*?)\];", service_worker, flags=re.DOTALL)
    if not shell_match:
        _fail("PWA APP_SHELL fehlt")
    shell_urls = set(re.findall(r'"(/[^"]*)"', shell_match.group(1)))
    shell_paths = {url.split("?", 1)[0].lstrip("/") for url in shell_urls}

    names = {info.filename for info in archive.infolist() if not info.is_dir()}
    public_files = {
        name.removeprefix(f"{package_root}/")
        for name in names
        if name.startswith(f"{package_root}/public/")
    }
    required_shell_files = {
        relative
        for relative in public_files
        if (
            relative in {"public/index.html", "public/styles.css", "public/app.js", "public/manifest.webmanifest"}
            or relative.startswith("public/css/")
            or relative.startswith("public/js/")
            or relative.startswith("public/icons/")
        )
    }
    cached_files = {f"public/{path}" for path in shell_paths if path}
    missing_cache_files = sorted(required_shell_files - cached_files)
    if missing_cache_files:
        _fail(f"PWA APP_SHELL ist unvollstaendig: {', '.join(missing_cache_files)}")

    for relative in sorted(path for path in public_files if path.startswith("public/css/")):
        css_name = relative.removeprefix("public/")
        if f'@import url("/{css_name}?v={version}")' not in styles:
            _fail(f"Stylesheet-Fallback bindet {css_name} nicht versioniert ein")

    for url in shell_urls:
        path = url.split("?", 1)[0]
        if path == "/":
            continue
        relative = f"public/{path.lstrip('/')}"
        if relative not in public_files:
            _fail(f"PWA APP_SHELL verweist auf fehlende Datei: {path}")


def verify_release_package(
    archive_path: Path,
    checksum_path: Path | None = None,
    *,
    root: Path = ROOT,
    expected_version: str | None = None,
    release_tag: str | None = None,
    bridge_tag: str = BRIDGE_TAG,
    bridge_tag_object: str = BRIDGE_TAG_OBJECT,
    bridge_commit: str = BRIDGE_COMMIT,
    bridge_release_metadata_path: Path | None = None,
    bridge_release_archive_path: Path | None = None,
    bridge_release_checksum_path: Path | None = None,
) -> dict[str, object]:
    root = root.resolve(strict=True)
    version = validate_version(
        expected_version or (root / "VERSION").read_text(encoding="utf-8").strip()
    )
    archive_path = archive_path.resolve(strict=True)
    checksum_path = checksum_path or archive_path.with_suffix(f"{archive_path.suffix}.sha256")
    package_root = f"watering-planner-{version}"
    if archive_path.name != f"{package_root}.zip":
        _fail(f"Paketname {archive_path.name!r} passt nicht zu Version {version}")

    digest = verify_checksum(archive_path, checksum_path)
    files = validate_archive_layout(archive_path, package_root)
    expected_sources = expected_archive_sources(root, version)
    expected_files = set(expected_sources)
    actual_files = set(files)
    missing = sorted(expected_files - actual_files)
    unexpected = sorted(actual_files - expected_files)
    if missing or unexpected:
        details = []
        if missing:
            details.append(f"fehlend: {', '.join(missing[:8])}")
        if unexpected:
            details.append(f"unerwartet: {', '.join(unexpected[:8])}")
        _fail("Release-Inhalt stimmt nicht mit den Quellen ueberein (" + "; ".join(details) + ")")

    with zipfile.ZipFile(archive_path) as archive:
        internal_version = _archive_text(archive, f"{package_root}/VERSION").strip()
        if internal_version != version:
            _fail(f"VERSION im Paket ist {internal_version!r} statt {version!r}")
        _verify_versioned_assets(archive, package_root, version)
        for name, source in expected_sources.items():
            if archive.read(name) != source.read_bytes():
                _fail(f"Release-Datei stimmt nicht mit der Quelle ueberein: {name}")

    bridge = verify_bridge_tag(root, bridge_tag, bridge_tag_object, bridge_commit)
    update_path = verify_bridge_update_path(root, bridge)
    release_commit = None
    if release_tag:
        release_commit = verify_tag(root, release_tag, version, require_head=True)
    history_commit = verify_bridge_history(
        root,
        bridge,
        release_commit or "HEAD",
    )
    bridge_release_metadata = (
        verify_bridge_release_metadata(bridge_release_metadata_path)
        if bridge_release_metadata_path
        else None
    )
    if bool(bridge_release_archive_path) != bool(bridge_release_checksum_path):
        _fail("Bridge-Paket und Bridge-Pruefsumme muessen gemeinsam angegeben werden")
    bridge_release_assets = (
        verify_bridge_release_assets(
            bridge_release_archive_path,
            bridge_release_checksum_path,
            root=root,
            bridge_commit=bridge,
        )
        if bridge_release_archive_path and bridge_release_checksum_path
        else None
    )

    return {
        "version": version,
        "archive": str(archive_path),
        "sha256": digest,
        "file_count": len(actual_files),
        "bridge_tag": bridge_tag,
        "bridge_tag_object": bridge_tag_object,
        "bridge_commit": bridge,
        "bridge_update_path": update_path,
        "release_tag": release_tag,
        "release_commit": release_commit,
        "bridge_history_commit": history_commit,
        "bridge_release_metadata": bridge_release_metadata,
        "bridge_release_assets": bridge_release_assets,
    }


def parse_args(arguments: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prueft ein Watering-Planner-Releasepaket.")
    parser.add_argument("--archive", type=Path)
    parser.add_argument("--checksum", type=Path)
    parser.add_argument("--expected-version")
    parser.add_argument("--tag", dest="release_tag")
    parser.add_argument("--bridge-tag", default=BRIDGE_TAG)
    parser.add_argument("--bridge-tag-object", default=BRIDGE_TAG_OBJECT)
    parser.add_argument("--bridge-commit", default=BRIDGE_COMMIT)
    parser.add_argument("--bridge-release-metadata", type=Path)
    parser.add_argument("--bridge-release-archive", type=Path)
    parser.add_argument("--bridge-release-checksum", type=Path)
    return parser.parse_args(arguments)


def main(arguments: list[str] | None = None) -> int:
    options = parse_args(arguments)
    version = options.expected_version or (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    archive = options.archive or ROOT / "dist" / f"watering-planner-{version}.zip"
    try:
        result = verify_release_package(
            archive,
            options.checksum,
            root=ROOT,
            expected_version=version,
            release_tag=options.release_tag,
            bridge_tag=options.bridge_tag,
            bridge_tag_object=options.bridge_tag_object,
            bridge_commit=options.bridge_commit,
            bridge_release_metadata_path=options.bridge_release_metadata,
            bridge_release_archive_path=options.bridge_release_archive,
            bridge_release_checksum_path=options.bridge_release_checksum,
        )
    except (OSError, ReleaseVerificationError, ValueError) as exc:
        print(f"Releasepruefung fehlgeschlagen: {exc}", file=sys.stderr)
        return 1
    print(
        f"Releasepaket {result['version']} geprueft: "
        f"{result['file_count']} Dateien, SHA-256 {result['sha256']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
