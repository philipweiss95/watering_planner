import importlib.util
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch


UPDATER_PATH = Path(__file__).resolve().parents[1] / "updater" / "updater.py"
SPEC = importlib.util.spec_from_file_location("watering_updater", UPDATER_PATH)
updater = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(updater)
RELEASE_NOTES_PATH = Path(__file__).resolve().parents[1] / "scripts" / "release_notes.py"
RELEASE_NOTES_SPEC = importlib.util.spec_from_file_location("watering_release_notes", RELEASE_NOTES_PATH)
release_notes = importlib.util.module_from_spec(RELEASE_NOTES_SPEC)
RELEASE_NOTES_SPEC.loader.exec_module(release_notes)


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

    def test_compose_uses_project_label_from_synology_container(self):
        commands = []

        def fake_run(command, timeout=600):
            commands.append(command)
            if command[:3] == ["docker", "inspect", "--format"]:
                return "container-manager-project"
            return ""

        with patch.object(updater, "run", side_effect=fake_run):
            updater.compose(["up", "-d", "watering-planner"], Path("/data/update/runtime.yml"))

        compose_command = commands[-1]
        self.assertEqual(compose_command[compose_command.index("--project-name") + 1], "container-manager-project")

    def test_compose_project_falls_back_when_containers_have_no_project_label(self):
        with patch.object(updater, "run", return_value=""):
            self.assertEqual(updater.compose_project_name(), "watering-planner")

    def test_release_notes_use_only_requested_changelog_version(self):
        changelog = """# Changelog

## [1.0.2] - 2026-07-21

- Neue Info-Seite.

## [1.0.1] - 2026-07-21

- Vorherige Änderung.
"""
        notes = release_notes.notes_for_version(changelog, "1.0.2")

        self.assertIn("Änderungen in 1.0.2", notes)
        self.assertIn("Neue Info-Seite", notes)
        self.assertNotIn("Vorherige Änderung", notes)

    def test_updater_panel_is_rendered_on_info_page(self):
        html = (Path(__file__).resolve().parents[1] / "public" / "index.html").read_text(encoding="utf-8")

        self.assertGreater(html.index("<h2>Updater</h2>"), html.index('class="view info-view"'))

    def test_configuration_views_have_guided_headers(self):
        html = (Path(__file__).resolve().parents[1] / "public" / "index.html").read_text(encoding="utf-8")

        for view in ("settings", "hoses", "plants", "info"):
            marker = f'class="view {view}-view"'
            section = html[html.index(marker):]
            self.assertLess(section.index("view-hero"), section.index("</div>"))

    def test_mobile_navigation_uses_reachable_bottom_bar(self):
        css = (Path(__file__).resolve().parents[1] / "public" / "styles.css").read_text(encoding="utf-8")

        mobile = css[css.index("@media (max-width: 719px)"):]
        self.assertIn(".app-nav", mobile)
        self.assertIn("position: fixed", mobile)
        self.assertIn("safe-area-inset-bottom", mobile)

    def test_calibration_ui_uses_percentage_levels(self):
        root = Path(__file__).resolve().parents[1]
        html = (root / "public" / "index.html").read_text(encoding="utf-8")
        javascript = (root / "public" / "app.js").read_text(encoding="utf-8")

        self.assertIn('id="mainCalibrationLevel" type="number" min="0" max="100"', html)
        self.assertIn('id="refillCalibrationLevel" type="number" min="0" max="100"', html)
        self.assertIn("measured_level_percent", javascript)
        self.assertNotIn("JSON.stringify({ measured_level_ml:", javascript)


if __name__ == "__main__":
    unittest.main()
