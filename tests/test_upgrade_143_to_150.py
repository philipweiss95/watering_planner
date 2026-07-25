from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import shutil
import subprocess
import tarfile
import threading
import unittest
import uuid
import zipfile
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from v143_fixture import create_v143_database


ROOT = Path(__file__).resolve().parents[1]
BRIDGE_TAG = "v1.4.3"
BRIDGE_COMMIT = "e02ceb198264104fd8f2bc68eb8db7b24ac00dc8"
TARGET_VERSION = "1.5.0"


def load_module(path: Path, prefix: str):
    name = f"{prefix}_{uuid.uuid4().hex}"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot_load_module:{path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


current_updater = load_module(ROOT / "updater" / "updater.py", "upgrade_current_updater")
package_release = load_module(
    ROOT / "scripts" / "package_release.py",
    "upgrade_package_release",
)


def extract_tar_bytes(payload: bytes, destination: Path) -> None:
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:") as archive:
        archive.extractall(destination, filter="data")


class WritableTemporaryDirectory:
    """Temporary directory that also works with restricted Windows ACLs."""

    def __init__(self, prefix: str = "tmp-", dir: Path | str | None = None):
        parent = Path(dir) if dir is not None else ROOT
        self.path = parent / f"{prefix}{uuid.uuid4().hex}"
        self.path.mkdir(parents=True)

    def __enter__(self) -> str:
        return str(self.path)

    def __exit__(self, exc_type, exc, traceback) -> None:
        shutil.rmtree(self.path, ignore_errors=True)


def tree_snapshot(root: Path, entries) -> dict[str, str]:
    result: dict[str, str] = {}
    for entry in entries:
        candidate = root / entry
        if candidate.is_symlink():
            result[entry] = f"symlink:{candidate.readlink()}"
        elif candidate.is_file():
            result[entry] = hashlib.sha256(candidate.read_bytes()).hexdigest()
        elif candidate.is_dir():
            for file in sorted(candidate.rglob("*")):
                relative = file.relative_to(root).as_posix()
                if file.is_symlink():
                    result[relative] = f"symlink:{file.readlink()}"
                elif file.is_file():
                    result[relative] = hashlib.sha256(file.read_bytes()).hexdigest()
    return result


class Upgrade143To150Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.class_root = (
            ROOT / f".upgrade-assets-{uuid.uuid4().hex}"
        )
        cls.class_root.mkdir()
        cls.assets_dir = cls.class_root / "assets"
        cls.archive_path = (
            cls.assets_dir
            / f"watering-planner-{package_release.VERSION}.zip"
        )
        with (
            patch.object(package_release, "OUTPUT_DIR", cls.assets_dir),
            patch.object(package_release, "ARCHIVE", cls.archive_path),
        ):
            package_release.main()
        cls.checksum_path = cls.archive_path.with_suffix(".zip.sha256")

        try:
            cls.bridge_commit = subprocess.check_output(
                ["git", "rev-parse", f"{BRIDGE_TAG}^{{commit}}"],
                cwd=ROOT,
                text=True,
            ).strip()
            cls.bridge_archive = subprocess.check_output(
                ["git", "archive", "--format=tar", BRIDGE_TAG],
                cwd=ROOT,
            )
        except (OSError, subprocess.CalledProcessError) as exc:
            raise RuntimeError(
                f"{BRIDGE_TAG} must be available for upgrade tests"
            ) from exc
        if cls.bridge_commit != BRIDGE_COMMIT:
            raise RuntimeError(
                f"{BRIDGE_TAG} points to {cls.bridge_commit}, "
                f"expected immutable bridge commit {BRIDGE_COMMIT}"
            )

        cls.expected_dir = cls.class_root / "expected"
        with zipfile.ZipFile(cls.archive_path) as archive:
            archive.extractall(cls.expected_dir)
        cls.expected_root = (
            cls.expected_dir / f"watering-planner-{package_release.VERSION}"
        )
        cls.expected_program = tree_snapshot(
            cls.expected_root,
            current_updater.MANAGED_PATHS,
        )

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.class_root, ignore_errors=True)

    def setUp(self) -> None:
        self.test_root = (
            ROOT / f".upgrade-case-{uuid.uuid4().hex}"
        )
        self.test_root.mkdir()

    def tearDown(self) -> None:
        current_updater.INSTALL_RUNNING.clear()
        shutil.rmtree(self.test_root, ignore_errors=True)

    def create_bridge_install(self, name: str = "install") -> dict:
        project = self.test_root / name
        project.mkdir()
        extract_tar_bytes(self.bridge_archive, project)

        environment_path = project / ".env.synology"
        environment_path.write_text(
            "HOME_ASSISTANT_WEBHOOK_URL=https://ha.invalid/private\n"
            "SMTP_PASSWORD=must-stay-secret\n",
            encoding="utf-8",
        )
        database_path = project / "data" / "watering.sqlite3"
        create_v143_database(database_path)
        update_dir = project / "data" / "update"
        update_dir.mkdir(parents=True)
        config_path = update_dir / "config.json"
        config = {
            "repository": "philipweiss95/watering_planner",
            "githubToken": "fixture-token-that-is-long-enough",
            "channel": "stable",
            "customPreservedValue": "keep-me",
        }
        config_path.write_text(
            json.dumps(config, sort_keys=True),
            encoding="utf-8",
        )

        bridge_updater = load_module(
            project / "updater" / "updater.py",
            "upgrade_bridge_updater",
        )
        return {
            "project": project,
            "database_path": database_path,
            "database_bytes": database_path.read_bytes(),
            "environment_path": environment_path,
            "environment_bytes": environment_path.read_bytes(),
            "update_dir": update_dir,
            "config_path": config_path,
            "config": config,
            "state_path": update_dir / "status.json",
            "updater": bridge_updater,
            "before": tree_snapshot(project, bridge_updater.MANAGED_PATHS),
        }

    @staticmethod
    def release_for(archive_path: Path, checksum_path: Path) -> dict:
        return {
            "version": TARGET_VERSION,
            "tag": f"v{TARGET_VERSION}",
            "name": f"v{TARGET_VERSION}",
            "publishedAt": "2026-07-25T12:00:00Z",
            "notes": "Upgrade path test",
            "archive": {
                "name": f"watering-planner-{TARGET_VERSION}.zip",
                "url": "local://archive",
            },
            "checksum": {
                "name": f"watering-planner-{TARGET_VERSION}.zip.sha256",
                "url": "local://checksum",
            },
            "_archive_path": archive_path,
            "_checksum_path": checksum_path,
        }

    def run_bridge_install(
        self,
        installation: dict,
        *,
        archive_path: Path | None = None,
        checksum_path: Path | None = None,
        compose_side_effect=None,
        run_side_effect=None,
        schedule_side_effect=None,
        copytree_side_effect=None,
    ) -> dict:
        updater = installation["updater"]
        archive_path = archive_path or self.archive_path
        checksum_path = checksum_path or self.checksum_path
        release = self.release_for(archive_path, checksum_path)
        compose_calls: list[list[str]] = []

        def download(asset, token, destination):
            self.assertEqual(token, installation["config"]["githubToken"])
            source = (
                release["_archive_path"]
                if asset["url"] == "local://archive"
                else release["_checksum_path"]
            )
            shutil.copy2(source, destination)

        def compose(arguments, runtime_file, timeout=900):
            compose_calls.append(list(arguments))
            if compose_side_effect is not None:
                return compose_side_effect(arguments, runtime_file, timeout)
            if arguments == ["ps", "-q", "watering-planner"]:
                return "planner-container-id"
            return ""

        def run(command, timeout=600):
            if run_side_effect is not None:
                return run_side_effect(command, timeout)
            if command[:3] == ["docker", "inspect", "--format"]:
                return "healthy"
            return ""

        def schedule(runtime_file, version):
            if schedule_side_effect is not None:
                return schedule_side_effect(runtime_file, version)
            return "handoff-helper-id"

        with ExitStack() as stack:
            stack.enter_context(
                patch.multiple(
                    updater,
                    PROJECT_DIR=installation["project"],
                    DATA_DIR=installation["update_dir"],
                    CONFIG_PATH=installation["config_path"],
                    STATE_PATH=installation["state_path"],
                    SHARED_TOKEN_PATH=(
                        installation["project"] / "data" / ".updater-token"
                    ),
                    COMPOSE_FILE=installation["project"] / "docker-compose.yml",
                )
            )
            stack.enter_context(patch.object(updater, "latest_release", return_value=release))
            stack.enter_context(patch.object(updater, "download_asset", side_effect=download))
            stack.enter_context(
                patch.object(
                    updater,
                    "host_project_dir",
                    return_value=installation["project"].as_posix(),
                )
            )
            stack.enter_context(patch.object(updater, "compose", side_effect=compose))
            stack.enter_context(patch.object(updater, "run", side_effect=run))
            stack.enter_context(
                patch.object(
                    updater.tempfile,
                    "TemporaryDirectory",
                    WritableTemporaryDirectory,
                )
            )
            stack.enter_context(
                patch.object(
                    updater,
                    "schedule_updater_handoff",
                    side_effect=schedule,
                )
            )
            if copytree_side_effect is not None:
                stack.enter_context(
                    patch.object(
                        updater.shutil,
                        "copytree",
                        side_effect=copytree_side_effect,
                    )
                )
            updater.install_update("1.4.3")

        return {
            "state": json.loads(
                installation["state_path"].read_text(encoding="utf-8")
            ),
            "compose_calls": compose_calls,
        }

    def assert_private_data_unchanged(self, installation: dict) -> None:
        self.assertEqual(
            installation["database_path"].read_bytes(),
            installation["database_bytes"],
        )
        self.assertEqual(
            installation["environment_path"].read_bytes(),
            installation["environment_bytes"],
        )
        self.assertEqual(
            json.loads(
                installation["config_path"].read_text(encoding="utf-8")
            ),
            installation["config"],
        )

    def archive_variant(
        self,
        name: str,
        *,
        rewrite_name=None,
        rewrite_data=None,
        include=None,
        extra: list[tuple[str, bytes]] | None = None,
    ) -> tuple[Path, Path]:
        destination = self.test_root / f"{name}.zip"
        with (
            zipfile.ZipFile(self.archive_path) as source,
            zipfile.ZipFile(
                destination,
                "w",
                compression=zipfile.ZIP_DEFLATED,
            ) as target,
        ):
            for item in source.infolist():
                filename = (
                    rewrite_name(item.filename)
                    if rewrite_name is not None
                    else item.filename
                )
                if include is not None and not include(filename):
                    continue
                data = source.read(item.filename)
                if rewrite_data is not None:
                    data = rewrite_data(filename, data)
                target.writestr(filename, data)
            for filename, data in extra or []:
                target.writestr(filename, data)
        checksum = destination.with_suffix(".zip.sha256")
        checksum.write_text(
            f"{hashlib.sha256(destination.read_bytes()).hexdigest()}  "
            f"{destination.name}\n",
            encoding="utf-8",
        )
        return destination, checksum

    def test_bridge_installs_complete_package_and_file_rollback_is_exact(self):
        installation = self.create_bridge_install()
        result = self.run_bridge_install(installation)

        self.assertEqual(result["state"]["phase"], "handoff")
        self.assertEqual(
            tree_snapshot(
                installation["project"],
                current_updater.MANAGED_PATHS,
            ),
            self.expected_program,
        )
        self.assertTrue((installation["project"] / "watering_backend").is_dir())
        self.assertTrue((installation["project"] / "package.json").is_file())
        self.assertIn(
            ["build", "--no-cache", "watering-planner", "updater"],
            result["compose_calls"],
        )
        self.assert_private_data_unchanged(installation)

        backups = list((installation["update_dir"] / "backups").glob("*.tar.gz"))
        self.assertEqual(len(backups), 1)
        backup_extract = self.test_root / "backup-extract"
        backup_extract.mkdir()
        with tarfile.open(backups[0], "r:gz") as archive:
            archive.extractall(backup_extract, filter="data")
        self.assertEqual(
            tree_snapshot(backup_extract, current_updater.MANAGED_PATHS),
            installation["before"],
        )

        current_updater.restore_managed_backup(
            backups[0],
            installation["project"],
        )
        self.assertEqual(
            tree_snapshot(
                installation["project"],
                current_updater.MANAGED_PATHS,
            ),
            installation["before"],
        )
        self.assertFalse(
            (installation["project"] / "watering_backend").exists()
        )
        self.assertFalse((installation["project"] / "package.json").exists())
        self.assert_private_data_unchanged(installation)

    def test_pre_backup_archive_failures_leave_bridge_untouched(self):
        root = f"watering-planner-{TARGET_VERSION}"
        wrong_root = self.archive_variant(
            "wrong-root",
            rewrite_name=lambda name: name.replace(
                f"{root}/",
                "unexpected-root/",
                1,
            ),
        )
        traversal = self.archive_variant(
            "traversal",
            extra=[(f"{root}/../../outside.txt", b"escape")],
        )
        missing_version = self.archive_variant(
            "missing-version",
            include=lambda name: name != f"{root}/VERSION",
        )
        mismatched_version = self.archive_variant(
            "mismatched-version",
            rewrite_data=lambda name, data: (
                b"9.9.9\n" if name == f"{root}/VERSION" else data
            ),
        )
        missing_backend = self.archive_variant(
            "missing-backend",
            include=lambda name: not name.startswith(
                f"{root}/watering_backend/"
            ),
        )
        corrupt_archive = self.test_root / "corrupt.zip"
        corrupt_archive.write_bytes(b"this is not a zip archive")
        corrupt_checksum = corrupt_archive.with_suffix(".zip.sha256")
        corrupt_checksum.write_text(
            f"{hashlib.sha256(corrupt_archive.read_bytes()).hexdigest()}  "
            "corrupt.zip\n",
            encoding="utf-8",
        )
        bad_checksum = self.test_root / "bad-checksum.sha256"
        bad_checksum.write_text(
            f"{'0' * 64}  {self.archive_path.name}\n",
            encoding="utf-8",
        )

        scenarios = {
            "corrupt_zip": (
                corrupt_archive,
                corrupt_checksum,
                "File is not a zip file",
            ),
            "wrong_checksum": (
                self.archive_path,
                bad_checksum,
                "release_checksum_mismatch",
            ),
            "wrong_root": (*wrong_root, "invalid_release_archive_layout"),
            "path_traversal": (*traversal, "invalid_release_archive_layout"),
            "missing_version": (
                *missing_version,
                "release_archive_missing_VERSION",
            ),
            "version_mismatch": (
                *mismatched_version,
                "release_version_mismatch",
            ),
            "missing_backend": (
                *missing_backend,
                "release_archive_missing_watering_backend/__init__.py",
            ),
        }
        for scenario, (archive_path, checksum_path, expected_error) in scenarios.items():
            with self.subTest(scenario=scenario):
                installation = self.create_bridge_install(scenario)
                result = self.run_bridge_install(
                    installation,
                    archive_path=archive_path,
                    checksum_path=checksum_path,
                )
                self.assertEqual(result["state"]["status"], "error")
                self.assertIn(expected_error, result["state"]["message"])
                self.assertEqual(
                    tree_snapshot(
                        installation["project"],
                        installation["updater"].MANAGED_PATHS,
                    ),
                    installation["before"],
                )
                self.assertFalse(
                    (installation["update_dir"] / "backups").exists()
                )
                self.assert_private_data_unchanged(installation)

    def test_post_backup_failures_restore_every_bridge_file(self):
        root = f"watering-planner-{TARGET_VERSION}"
        incomplete_backend = self.archive_variant(
            "incomplete-backend",
            include=lambda name: (
                not name.startswith(f"{root}/watering_backend/")
                or name == f"{root}/watering_backend/__init__.py"
            ),
        )
        broken_python = self.archive_variant(
            "broken-python",
            rewrite_data=lambda name, data: (
                b"def broken(:\n"
                if name == f"{root}/server.py"
                else data
            ),
        )

        def build_failure(message):
            failed = False

            def compose(arguments, runtime_file, timeout):
                nonlocal failed
                if (
                    not failed
                    and arguments
                    == ["build", "--no-cache", "watering-planner", "updater"]
                ):
                    failed = True
                    raise RuntimeError(message)
                if arguments == ["ps", "-q", "watering-planner"]:
                    return "restored-planner"
                return ""

            return compose

        scenarios = {
            "incomplete_backend": (
                incomplete_backend,
                build_failure("watering_backend_incomplete"),
                None,
            ),
            "invalid_python": (
                broken_python,
                build_failure("python_compile_failed"),
                None,
            ),
            "planner_build": (
                (self.archive_path, self.checksum_path),
                build_failure("planner_build_failed"),
                None,
            ),
            "updater_build": (
                (self.archive_path, self.checksum_path),
                build_failure("updater_build_failed"),
                None,
            ),
            "planner_health": (
                (self.archive_path, self.checksum_path),
                None,
                lambda command, timeout: (
                    "unhealthy"
                    if command[:3] == ["docker", "inspect", "--format"]
                    else ""
                ),
            ),
        }
        for scenario, (assets, compose_failure, run_failure) in scenarios.items():
            with self.subTest(scenario=scenario):
                installation = self.create_bridge_install(scenario)
                result = self.run_bridge_install(
                    installation,
                    archive_path=assets[0],
                    checksum_path=assets[1],
                    compose_side_effect=compose_failure,
                    run_side_effect=run_failure,
                )
                self.assertEqual(result["state"]["status"], "error")
                self.assertEqual(
                    tree_snapshot(
                        installation["project"],
                        installation["updater"].MANAGED_PATHS,
                    ),
                    installation["before"],
                )
                self.assertEqual(
                    len(
                        list(
                            (
                                installation["update_dir"] / "backups"
                            ).glob("*.tar.gz")
                        )
                    ),
                    1,
                )
                self.assert_private_data_unchanged(installation)

    def test_interrupted_copy_restores_bridge_before_runtime_creation(self):
        installation = self.create_bridge_install()
        original_copytree = shutil.copytree
        interrupted = False

        def interrupt_backend_copy(source, destination, *args, **kwargs):
            nonlocal interrupted
            if not interrupted and Path(source).name == "watering_backend":
                interrupted = True
                raise OSError("simulated_copy_interruption")
            return original_copytree(source, destination, *args, **kwargs)

        result = self.run_bridge_install(
            installation,
            copytree_side_effect=interrupt_backend_copy,
        )

        self.assertTrue(interrupted)
        self.assertEqual(result["state"]["status"], "error")
        self.assertIn("simulated_copy_interruption", result["state"]["message"])
        self.assertEqual(
            tree_snapshot(
                installation["project"],
                installation["updater"].MANAGED_PATHS,
            ),
            installation["before"],
        )
        self.assert_private_data_unchanged(installation)

    def test_incomplete_previous_attempt_is_replaced_by_complete_package(self):
        installation = self.create_bridge_install()
        stale_backend = installation["project"] / "watering_backend"
        stale_backend.mkdir()
        (stale_backend / "partial.py").write_text(
            "incomplete = True\n",
            encoding="utf-8",
        )
        (installation["project"] / "package.json").write_text(
            '{"incomplete":true}\n',
            encoding="utf-8",
        )

        result = self.run_bridge_install(installation)

        self.assertEqual(result["state"]["phase"], "handoff")
        self.assertFalse((stale_backend / "partial.py").exists())
        self.assertEqual(
            tree_snapshot(
                installation["project"],
                current_updater.MANAGED_PATHS,
            ),
            self.expected_program,
        )
        self.assert_private_data_unchanged(installation)

    def test_install_claim_allows_only_one_parallel_update(self):
        current_updater.INSTALL_RUNNING.clear()
        barrier = threading.Barrier(16)
        results: list[bool] = []
        lock = threading.Lock()

        def claim():
            barrier.wait()
            result = current_updater.claim_install()
            with lock:
                results.append(result)

        threads = [threading.Thread(target=claim) for _ in range(16)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(results.count(True), 1)
        self.assertEqual(results.count(False), 15)

    def test_new_updater_health_failure_rolls_back_and_leaves_one_updater(self):
        installation = self.create_bridge_install()
        success = self.run_bridge_install(installation)
        self.assertEqual(success["state"]["phase"], "handoff")
        runtime_file = installation["update_dir"] / "runtime-handoff.yml"
        runtime_file.write_text("services: {}\n", encoding="utf-8")

        expected_new_image = "sha256:" + "a" * 64
        restored_image = "sha256:" + "b" * 64
        previous_id = "1" * 64
        restored_id = "2" * 64
        stale_id = "3" * 64
        attempts = 0
        rollback_started = False
        updater_ids = {restored_id, stale_id}
        commands: list[list[str]] = []

        def fake_run(command, timeout=600):
            nonlocal attempts, rollback_started
            commands.append(list(command))
            if command[-4:] == [
                "-d",
                "--no-deps",
                "--force-recreate",
                "updater",
            ]:
                attempts += 1
                return ""
            if command[-5:] == [
                "-d",
                "--no-deps",
                "--force-recreate",
                "watering-planner",
                "updater",
            ]:
                rollback_started = True
                return ""
            if command[-3:] == ["ps", "-q", "watering-planner"]:
                return "planner-restored"
            if command[-3:] == ["ps", "-q", "updater"]:
                return restored_id
            if command[:3] == ["docker", "ps", "-aq"]:
                if rollback_started:
                    return "\n".join(sorted(updater_ids))
                return f"candidate-{attempts}"
            if command[:4] == [
                "docker",
                "inspect",
                "--format",
                "{{.Image}}",
            ]:
                container_id = command[-1]
                return (
                    restored_image
                    if container_id == restored_id
                    else expected_new_image
                )
            if command[:3] == ["docker", "image", "inspect"]:
                return restored_image
            if command[:3] == ["docker", "inspect", "--format"]:
                container_id = command[-1]
                if container_id.startswith("candidate-"):
                    return "unhealthy"
                return "healthy"
            if command[:3] == ["docker", "rm", "-f"]:
                updater_ids.discard(command[-1])
                return command[-1]
            return ""

        with (
            patch.multiple(
                current_updater,
                PROJECT_DIR=installation["project"],
                DATA_DIR=installation["update_dir"],
                CONFIG_PATH=installation["config_path"],
                STATE_PATH=installation["state_path"],
                COMPOSE_FILE=installation["project"] / "docker-compose.yml",
            ),
            patch.object(current_updater, "run", side_effect=fake_run),
            patch.object(current_updater.time, "sleep"),
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "Programmdateien und Container wurden auf 1.4.3",
            ):
                current_updater.perform_updater_handoff(
                    runtime_file,
                    previous_id,
                    "watering-planner",
                    expected_new_image,
                    TARGET_VERSION,
                )

        self.assertEqual(attempts, 3)
        self.assertEqual(updater_ids, {restored_id})
        self.assertFalse(runtime_file.exists())
        self.assertEqual(
            tree_snapshot(
                installation["project"],
                current_updater.MANAGED_PATHS,
            ),
            installation["before"],
        )
        state = json.loads(
            installation["state_path"].read_text(encoding="utf-8")
        )
        self.assertEqual(state["phase"], "rolled_back")
        self.assert_private_data_unchanged(installation)
        self.assertIn(
            ["docker", "rm", "-f", stale_id],
            commands,
        )


if __name__ == "__main__":
    unittest.main()
