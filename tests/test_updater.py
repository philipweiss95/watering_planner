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
        image_id = "sha256:" + "b" * 64

        def fake_run(command, timeout=600):
            commands.append(command)
            if command[:4] == ["docker", "inspect", "--format", "{{.Id}}"]:
                return current_id
            if command[:4] == ["docker", "inspect", "--format", '{{ index .Config.Labels "com.docker.compose.project" }}']:
                return "container-manager-project"
            if command[:4] == ["docker", "inspect", "--format", "{{json .Mounts}}"]:
                return '[{"Type":"bind","Destination":"/project","Source":"/volume1/docker/watering-planner"}]'
            if command[:3] == ["docker", "image", "inspect"]:
                return image_id
            if command[:3] == ["docker", "ps", "-aq"]:
                return ""
            if command[:2] == ["docker", "run"]:
                return "handoff-container-id"
            return ""

        with patch.object(updater, "run", side_effect=fake_run), patch.object(updater.time, "time", return_value=1234):
            helper_id = updater.schedule_updater_handoff(Path("/data/update/runtime-1234.yml"), "1.3.1")

        self.assertEqual(helper_id, "handoff-container-id")
        docker_run = next(command for command in commands if command[:2] == ["docker", "run"])
        self.assertIn("--rm", docker_run)
        self.assertIn("/volume1/docker/watering-planner:/project", docker_run)
        self.assertIn("/volume1/docker/watering-planner/data:/data", docker_run)
        self.assertEqual(docker_run[docker_run.index("--entrypoint") + 1], "python")
        self.assertIn(image_id, docker_run)
        self.assertEqual(docker_run[-6:], [
            "handoff", "runtime-1234.yml", current_id,
            "container-manager-project", image_id, "1.3.1",
        ])

    def test_handoff_verifies_new_image_health_and_removes_all_duplicates(self):
        commands = []
        removed = set()
        previous_id = "a" * 64
        new_id = "b" * 64
        old_duplicate_id = "c" * 64
        image_id = "sha256:" + "d" * 64

        def fake_run(command, timeout=600):
            commands.append(command)
            if command[-3:] == ["ps", "-q", "updater"]:
                return new_id
            if command[:4] == ["docker", "inspect", "--format", "{{.Image}}"]:
                return image_id
            if command[:3] == ["docker", "inspect", "--format"]:
                return "healthy"
            if command[:3] == ["docker", "ps", "-aq"]:
                ids = [new_id]
                if old_duplicate_id not in removed:
                    ids.append(old_duplicate_id)
                return "\n".join(ids)
            if command[:3] == ["docker", "rm", "-f"]:
                removed.add(command[-1])
                return command[-1]
            return ""

        with tempfile.TemporaryDirectory() as directory:
            runtime_file = Path(directory) / "runtime-1234.yml"
            runtime_file.write_text("services: {}", encoding="utf-8")
            with (
                patch.object(updater, "run", side_effect=fake_run),
                patch.object(updater, "update_state") as update_state,
                patch.object(updater.time, "sleep"),
            ):
                result = updater.perform_updater_handoff(
                    runtime_file, previous_id, "synology-project", image_id, "1.3.1"
                )

            self.assertEqual(result, new_id)
            self.assertFalse(runtime_file.exists())
            self.assertIn(old_duplicate_id, removed)
            self.assertIn(["docker", "rm", "-f", old_duplicate_id], commands)
            update_state.assert_called_once()
            self.assertEqual(update_state.call_args.kwargs["status"], "ok")
            self.assertEqual(update_state.call_args.kwargs["targetVersion"], "1.3.1")

    def test_startup_reconciliation_promotes_current_image_and_removes_old_updater(self):
        commands = []
        current_id = "a" * 64
        old_id = "b" * 64
        image_id = "sha256:" + "c" * 64
        removed = set()

        def fake_run(command, timeout=600):
            commands.append(command)
            if command[:4] == ["docker", "inspect", "--format", "{{.Id}}"]:
                return current_id
            if command[:4] == ["docker", "inspect", "--format", '{{ index .Config.Labels "com.docker.compose.project" }}']:
                return "synology-project"
            if command[:3] == ["docker", "image", "inspect"]:
                return image_id
            if command[:4] == ["docker", "inspect", "--format", "{{.Image}}"]:
                return image_id
            if command[:4] == ["docker", "inspect", "--format", "{{.Name}}"]:
                return "/7ac37_current-updater"
            if command[:3] == ["docker", "ps", "-aq"]:
                ids = [current_id]
                if old_id not in removed:
                    ids.append(old_id)
                return "\n".join(ids)
            if command[:3] == ["docker", "rm", "-f"]:
                removed.add(command[-1])
                return command[-1]
            return ""

        with (
            patch.object(updater, "run", side_effect=fake_run),
            patch.object(updater.time, "sleep"),
        ):
            updater.reconcile_updater_on_startup("1.3.1")

        self.assertIn(["docker", "rm", "-f", old_id], commands)
        self.assertIn(["docker", "rename", current_id, "watering-planner-updater"], commands)

    def test_handoff_retries_three_times_and_persists_failure(self):
        compose_attempts = []

        def fake_run(command, timeout=600):
            if command[-3:] == ["--no-deps", "--force-recreate", "updater"]:
                compose_attempts.append(command)
                raise RuntimeError("compose_failed")
            return ""

        with tempfile.TemporaryDirectory() as directory:
            runtime_file = Path(directory) / "runtime-1234.yml"
            runtime_file.write_text("services: {}", encoding="utf-8")
            with (
                patch.object(updater, "run", side_effect=fake_run),
                patch.object(updater, "update_state") as update_state,
                patch.object(updater.time, "sleep"),
            ):
                with self.assertRaisesRegex(RuntimeError, "updater_handoff_failed_after_retries"):
                    updater.perform_updater_handoff(
                        runtime_file,
                        "a" * 64,
                        "synology-project",
                        "sha256:" + "b" * 64,
                        "1.3.1",
                    )

            self.assertEqual(len(compose_attempts), 3)
            self.assertFalse(runtime_file.exists())
            update_state.assert_called_once()
            self.assertEqual(update_state.call_args.kwargs["status"], "error")

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
