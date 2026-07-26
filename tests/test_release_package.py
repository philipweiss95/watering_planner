from __future__ import annotations

import hashlib
import json
import shutil
import stat
import unittest
import uuid
import zipfile
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from scripts import package_release
from scripts import verify_release_package as verifier


ROOT = Path(__file__).resolve().parents[1]
VERSION = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
PACKAGE_ROOT = f"watering-planner-{VERSION}"
TEMP_ROOT = ROOT / ".test-tmp"
TEMP_ROOT.mkdir(exist_ok=True)


@contextmanager
def temporary_directory():
    directory = TEMP_ROOT / f"release-package-{uuid.uuid4().hex}"
    directory.mkdir()
    try:
        yield str(directory)
    finally:
        if directory.resolve().parent != TEMP_ROOT.resolve():
            raise RuntimeError(f"Unsicheres Testverzeichnis: {directory}")
        shutil.rmtree(directory, ignore_errors=True)


def write_checksum(archive: Path) -> Path:
    checksum = archive.with_suffix(f"{archive.suffix}.sha256")
    checksum.write_text(
        f"{hashlib.sha256(archive.read_bytes()).hexdigest()}  {archive.name}\n",
        encoding="ascii",
    )
    return checksum


class ReleasePackageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._temporary = temporary_directory()
        temporary_root = Path(cls._temporary.__enter__())
        cls.output_dir = temporary_root / "dist"
        cls.archive, cls.checksum = package_release.build_package(
            ROOT,
            version=VERSION,
            output_dir=cls.output_dir,
        )

    @classmethod
    def tearDownClass(cls):
        cls._temporary.__exit__(None, None, None)

    def test_regular_package_is_complete_and_verified(self):
        result = verifier.verify_release_package(
            self.archive,
            self.checksum,
            root=ROOT,
            expected_version=VERSION,
        )

        self.assertEqual(result["version"], VERSION)
        self.assertEqual(result["bridge_commit"], verifier.BRIDGE_COMMIT)
        self.assertEqual(result["bridge_tag_object"], verifier.BRIDGE_TAG_OBJECT)
        with zipfile.ZipFile(self.archive) as archive:
            names = {info.filename for info in archive.infolist()}
        self.assertIn(f"{PACKAGE_ROOT}/watering_backend/services/weather.py", names)
        self.assertIn(f"{PACKAGE_ROOT}/watering_backend/services/refill_runs.py", names)
        self.assertIn(f"{PACKAGE_ROOT}/watering_backend/repositories/refill_runs.py", names)
        self.assertIn(f"{PACKAGE_ROOT}/public/js/dashboard.js", names)
        self.assertIn(f"{PACKAGE_ROOT}/public/css/responsive.css", names)
        self.assertIn(f"{PACKAGE_ROOT}/home-assistant/configuration.yaml", names)
        self.assertIn(f"{PACKAGE_ROOT}/home-assistant/automations.yaml", names)
        self.assertIn(f"{PACKAGE_ROOT}/scripts/verify_release_runtime.py", names)
        for entry in package_release.INCLUDES:
            prefix = f"{PACKAGE_ROOT}/{entry}"
            self.assertTrue(any(name == prefix or name.startswith(f"{prefix}/") for name in names), entry)

    def test_bridge_tag_is_the_immutable_published_commit(self):
        self.assertEqual(
            verifier.verify_bridge_tag(ROOT),
            "e02ceb198264104fd8f2bc68eb8db7b24ac00dc8",
        )
        self.assertEqual(
            verifier._git(ROOT, "rev-parse", "refs/tags/v1.4.3"),
            "f08acb6c5216c987cb6581513de499c9360d9f1a",
        )
        self.assertEqual(
            verifier.verify_bridge_history(ROOT, verifier.BRIDGE_COMMIT),
            verifier._git(ROOT, "rev-parse", "HEAD"),
        )
        update_path = verifier.verify_bridge_update_path(
            ROOT,
            verifier.BRIDGE_COMMIT,
        )
        self.assertNotIn(
            "watering_backend",
            update_path["legacy_managed_paths"],
        )
        self.assertNotIn(
            "package.json",
            update_path["legacy_managed_paths"],
        )
        self.assertIn(
            "watering_backend",
            update_path["bridge_managed_paths"],
        )
        self.assertIn(
            "package.json",
            update_path["bridge_managed_paths"],
        )

    def test_release_history_rejects_commit_without_bridge_ancestor(self):
        bridge_parent = verifier._git(
            ROOT,
            "rev-parse",
            f"{verifier.BRIDGE_COMMIT}^",
        )
        with self.assertRaisesRegex(
            verifier.ReleaseVerificationError,
            "merge-base --is-ancestor",
        ):
            verifier.verify_bridge_history(
                ROOT,
                verifier.BRIDGE_COMMIT,
                bridge_parent,
            )

    def test_published_bridge_release_metadata_is_pinned_offline(self):
        metadata = {
            "tag_name": verifier.BRIDGE_TAG,
            "published_at": verifier.BRIDGE_RELEASE_PUBLISHED_AT,
            "draft": False,
            "prerelease": False,
            "assets": [
                {
                    "name": verifier.BRIDGE_ARCHIVE_NAME,
                    "size": verifier.BRIDGE_ARCHIVE_SIZE,
                    "digest": f"sha256:{verifier.BRIDGE_ARCHIVE_SHA256}",
                },
                {
                    "name": verifier.BRIDGE_CHECKSUM_NAME,
                    "size": verifier.BRIDGE_CHECKSUM_SIZE,
                    "digest": f"sha256:{verifier.BRIDGE_CHECKSUM_SHA256}",
                },
            ],
        }
        with temporary_directory() as directory:
            path = Path(directory) / "release.json"
            path.write_text(json.dumps(metadata), encoding="utf-8")
            verified = verifier.verify_bridge_release_metadata(path)
            self.assertEqual(
                verified["archive_sha256"],
                verifier.BRIDGE_ARCHIVE_SHA256,
            )

            metadata["assets"][0]["digest"] = f"sha256:{'0' * 64}"
            path.write_text(json.dumps(metadata), encoding="utf-8")
            with self.assertRaisesRegex(
                verifier.ReleaseVerificationError,
                "erwarteten SHA-256",
            ):
                verifier.verify_bridge_release_metadata(path)

    def test_checksum_must_match_and_use_strict_format(self):
        with temporary_directory() as directory:
            checksum = Path(directory) / self.checksum.name
            checksum.write_text(f"{'0' * 64}  {self.archive.name}\n", encoding="ascii")
            with self.assertRaisesRegex(verifier.ReleaseVerificationError, "stimmt nicht"):
                verifier.verify_checksum(self.archive, checksum)

            checksum.write_text(
                f"{hashlib.sha256(self.archive.read_bytes()).hexdigest()} *{self.archive.name}\n",
                encoding="ascii",
            )
            with self.assertRaisesRegex(verifier.ReleaseVerificationError, "strikte Format"):
                verifier.verify_checksum(self.archive, checksum)

    def test_package_name_and_internal_version_must_match(self):
        with temporary_directory() as directory:
            wrong_name = Path(directory) / "watering-planner-wrong.zip"
            shutil.copy2(self.archive, wrong_name)
            checksum = write_checksum(wrong_name)
            with self.assertRaisesRegex(verifier.ReleaseVerificationError, "Paketname"):
                verifier.verify_release_package(
                    wrong_name,
                    checksum,
                    root=ROOT,
                    expected_version=VERSION,
                )

        with temporary_directory() as directory:
            archive = Path(directory) / self.archive.name
            target = f"{PACKAGE_ROOT}/VERSION"
            with zipfile.ZipFile(self.archive) as source, zipfile.ZipFile(archive, "w") as output:
                for info in source.infolist():
                    content = b"1.5.9\n" if info.filename == target else source.read(info.filename)
                    output.writestr(info, content)
            checksum = write_checksum(archive)
            with self.assertRaisesRegex(verifier.ReleaseVerificationError, "VERSION im Paket"):
                verifier.verify_release_package(
                    archive,
                    checksum,
                    root=ROOT,
                    expected_version=VERSION,
                )

    def test_release_tag_must_match_version_and_head(self):
        commit = "a" * 40
        with patch.object(verifier, "_git", side_effect=(commit, VERSION, commit)):
            self.assertEqual(
                verifier.verify_tag(ROOT, f"v{VERSION}", VERSION, require_head=True),
                commit,
            )
        with self.assertRaisesRegex(verifier.ReleaseVerificationError, "passt nicht"):
            verifier.verify_tag(ROOT, "v1.5.9", VERSION)

    def test_corrupted_zip_is_rejected(self):
        with temporary_directory() as directory:
            archive = Path(directory) / f"{PACKAGE_ROOT}.zip"
            archive.write_bytes(b"not a zip")
            with self.assertRaisesRegex(verifier.ReleaseVerificationError, "Ungueltiges ZIP"):
                verifier.validate_archive_layout(archive, PACKAGE_ROOT)

    def test_unsafe_zip_paths_are_rejected(self):
        invalid_names = (
            f"{PACKAGE_ROOT}/../escape",
            f"{PACKAGE_ROOT}\\server.py",
            f"/{PACKAGE_ROOT}/server.py",
            f"C:/{PACKAGE_ROOT}/server.py",
        )
        for invalid_name in invalid_names:
            with self.subTest(invalid_name=invalid_name), temporary_directory() as directory:
                archive = Path(directory) / f"{PACKAGE_ROOT}.zip"
                with zipfile.ZipFile(archive, "w") as output:
                    output.writestr(invalid_name, b"")
                if "\\" in invalid_name:
                    normalized = invalid_name.replace("\\", "/").encode("utf-8")
                    archive.write_bytes(archive.read_bytes().replace(normalized, invalid_name.encode("utf-8")))
                with self.assertRaises(verifier.ReleaseVerificationError):
                    verifier.validate_archive_layout(archive, PACKAGE_ROOT)

    def test_wrong_or_multiple_zip_roots_are_rejected(self):
        with temporary_directory() as directory:
            archive = Path(directory) / f"{PACKAGE_ROOT}.zip"
            with zipfile.ZipFile(archive, "w") as output:
                output.writestr(f"{PACKAGE_ROOT}/server.py", b"")
                output.writestr("other-root/VERSION", VERSION)
            with self.assertRaisesRegex(verifier.ReleaseVerificationError, "ZIP-Wurzel"):
                verifier.validate_archive_layout(archive, PACKAGE_ROOT)

    def test_duplicate_zip_paths_are_rejected_case_insensitively(self):
        with temporary_directory() as directory:
            archive = Path(directory) / f"{PACKAGE_ROOT}.zip"
            with zipfile.ZipFile(archive, "w") as output:
                output.writestr(f"{PACKAGE_ROOT}/server.py", b"")
                output.writestr(f"{PACKAGE_ROOT}/SERVER.py", b"")
            with self.assertRaisesRegex(verifier.ReleaseVerificationError, "Doppelter ZIP-Pfad"):
                verifier.validate_archive_layout(archive, PACKAGE_ROOT)

    def test_zip_symbolic_links_are_rejected(self):
        with temporary_directory() as directory:
            archive = Path(directory) / f"{PACKAGE_ROOT}.zip"
            link = zipfile.ZipInfo(f"{PACKAGE_ROOT}/server.py")
            link.create_system = 3
            link.external_attr = (stat.S_IFLNK | 0o777) << 16
            with zipfile.ZipFile(archive, "w") as output:
                output.writestr(link, "../../outside")
            with self.assertRaisesRegex(verifier.ReleaseVerificationError, "Symbolischer Link"):
                verifier.validate_archive_layout(archive, PACKAGE_ROOT)

    def test_missing_backend_file_is_rejected(self):
        with temporary_directory() as directory:
            archive = Path(directory) / self.archive.name
            omitted = f"{PACKAGE_ROOT}/watering_backend/services/weather.py"
            with zipfile.ZipFile(self.archive) as source, zipfile.ZipFile(archive, "w") as output:
                for info in source.infolist():
                    if info.filename != omitted:
                        output.writestr(info, source.read(info.filename))
            checksum = write_checksum(archive)
            with self.assertRaisesRegex(verifier.ReleaseVerificationError, "fehlend"):
                verifier.verify_release_package(
                    archive,
                    checksum,
                    root=ROOT,
                    expected_version=VERSION,
                )

    def test_compose_and_pwa_version_mismatches_are_rejected(self):
        replacements = (
            (
                f"{PACKAGE_ROOT}/docker-compose.yml",
                b"image: watering-planner:1.5.0",
                b"image: watering-planner:1.5.9",
                "Compose-Imageversion",
            ),
            (
                f"{PACKAGE_ROOT}/public/sw.js",
                b'const CACHE_NAME = "watering-planner-1.5.0";',
                b'const CACHE_NAME = "watering-planner-1.5.9";',
                "PWA-Cacheversion",
            ),
        )
        for target, old, new, message in replacements:
            with self.subTest(target=target), temporary_directory() as directory:
                archive = Path(directory) / self.archive.name
                with zipfile.ZipFile(self.archive) as source, zipfile.ZipFile(archive, "w") as output:
                    for info in source.infolist():
                        content = source.read(info.filename)
                        if info.filename == target:
                            self.assertIn(old, content)
                            content = content.replace(old, new)
                        output.writestr(info, content)
                checksum = write_checksum(archive)
                with self.assertRaisesRegex(verifier.ReleaseVerificationError, message):
                    verifier.verify_release_package(
                        archive,
                        checksum,
                        root=ROOT,
                        expected_version=VERSION,
                    )

    def test_source_symbolic_links_are_rejected(self):
        with temporary_directory() as directory:
            root = Path(directory)
            link = root / "link.txt"
            link.write_text("placeholder", encoding="utf-8")
            with (
                patch.object(type(link), "is_symlink", return_value=True),
                self.assertRaisesRegex(package_release.ReleasePackagingError, "Symbolische Links"),
            ):
                list(package_release.files_for(link, root))


if __name__ == "__main__":
    unittest.main()
