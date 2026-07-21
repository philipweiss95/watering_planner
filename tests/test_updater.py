import importlib.util
import tempfile
import unittest
import zipfile
from pathlib import Path


UPDATER_PATH = Path(__file__).resolve().parents[1] / "updater" / "updater.py"
SPEC = importlib.util.spec_from_file_location("watering_updater", UPDATER_PATH)
updater = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(updater)


class UpdaterTests(unittest.TestCase):
    def test_versions_compare_stable_releases(self):
        self.assertEqual(updater.normalize_version("v1.0.0"), "1.0.0")
        self.assertGreater(updater.version_key("1.1.0"), updater.version_key("1.0.9"))
        self.assertGreater(updater.version_key("1.0.0"), updater.version_key("1.0.0-rc.1"))

    def test_archive_validation_rejects_path_traversal(self):
        with tempfile.TemporaryDirectory() as directory:
            archive_path = Path(directory) / "release.zip"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("watering-planner-1.0.0/server.py", "")
                archive.writestr("watering-planner-1.0.0/../../escape", "")
            with zipfile.ZipFile(archive_path) as archive:
                with self.assertRaisesRegex(ValueError, "invalid_release_archive_layout"):
                    updater.safe_zip_members(archive, "watering-planner-1.0.0")

    def test_runtime_override_uses_real_host_mounts(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "runtime.yml"
            updater.runtime_override("/volume1/docker/watering-planner", output)
            content = output.read_text(encoding="utf-8")

        self.assertIn("/volume1/docker/watering-planner/data:/app/data", content)
        self.assertIn("/volume1/docker/watering-planner:/project", content)
        self.assertIn("/var/run/docker.sock:/var/run/docker.sock", content)


if __name__ == "__main__":
    unittest.main()
