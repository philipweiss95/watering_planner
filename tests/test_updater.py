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

    def test_cleanup_removes_only_stopped_duplicate_updaters(self):
        commands = []

        def fake_run(command, timeout=600):
            commands.append(command)
            if command[:3] == ["docker", "ps", "-aq"] and "status=exited" in command:
                return "old-updater-id"
            return ""

        with patch.object(updater, "run", side_effect=fake_run):
            removed = updater.cleanup_stopped_updater_containers("synology-project", "current-updater-id")

        self.assertEqual(removed, ["old-updater-id"])
        self.assertIn(["docker", "rm", "old-updater-id"], commands)
        filters = [item for command in commands for item in command if item.startswith("label=")]
        self.assertIn("label=com.docker.compose.project=synology-project", filters)
        self.assertIn("label=com.docker.compose.service=updater", filters)

    def test_updater_handoff_runs_in_independent_helper_container(self):
        commands = []
        current_id = "a" * 64

        def fake_run(command, timeout=600):
            commands.append(command)
            if command[:4] == ["docker", "inspect", "--format", "{{.Id}}"]:
                return current_id
            if command[:4] == ["docker", "inspect", "--format", '{{ index .Config.Labels "com.docker.compose.project" }}']:
                return "container-manager-project"
            if command[:4] == ["docker", "inspect", "--format", "{{json .Mounts}}"]:
                return '[{"Type":"bind","Destination":"/project","Source":"/volume1/docker/watering-planner"}]'
            if "images" in command and "-q" in command:
                return "sha256:new-updater-image"
            if command[:3] == ["docker", "ps", "-aq"]:
                return ""
            if command[:2] == ["docker", "run"]:
                return "handoff-container-id"
            return ""

        with patch.object(updater, "run", side_effect=fake_run), patch.object(updater.time, "time", return_value=1234):
            helper_id = updater.schedule_updater_handoff(Path("/data/update/runtime-1234.yml"))

        self.assertEqual(helper_id, "handoff-container-id")
        docker_run = next(command for command in commands if command[:2] == ["docker", "run"])
        self.assertIn("--rm", docker_run)
        self.assertIn("/volume1/docker/watering-planner:/project", docker_run)
        self.assertIn("/volume1/docker/watering-planner/data:/data", docker_run)
        script = docker_run[-1]
        self.assertIn("sleep 5", script)
        self.assertIn("--project-name container-manager-project", script)
        self.assertIn("up -d --no-deps --force-recreate updater", script)
        self.assertIn("rm -f /data/update/runtime-1234.yml", script)

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

    def test_iphone_web_app_metadata_and_icons_are_present(self):
        root = Path(__file__).resolve().parents[1]
        html = (root / "public" / "index.html").read_text(encoding="utf-8")
        manifest = (root / "public" / "manifest.webmanifest").read_text(encoding="utf-8")
        javascript = (root / "public" / "app.js").read_text(encoding="utf-8")

        self.assertIn('name="apple-mobile-web-app-capable" content="yes"', html)
        self.assertIn('name="apple-mobile-web-app-title" content="Gießplaner"', html)
        self.assertIn('rel="apple-touch-icon"', html)
        self.assertIn('"display": "standalone"', manifest)
        self.assertNotIn('id="plants"', html)
        self.assertIn("window.scrollTo(0, 0)", javascript)
        for size in (180, 192, 512):
            self.assertTrue((root / "public" / "icons" / f"app-icon-{size}.png").is_file())


if __name__ == "__main__":
    unittest.main()
