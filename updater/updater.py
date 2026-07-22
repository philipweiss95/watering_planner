from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import zipfile
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePosixPath
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


PORT = int(os.environ.get("UPDATER_PORT", "3188"))
DATA_DIR = Path(os.environ.get("UPDATER_DATA_DIR", "/data/update"))
PROJECT_DIR = Path(os.environ.get("UPDATER_PROJECT_DIR", "/project"))
UPDATER_CONTAINER_NAME = os.environ.get("UPDATER_CONTAINER_NAME", "watering-planner-updater")
PLANNER_CONTAINER_NAME = os.environ.get("PLANNER_CONTAINER_NAME", "watering-planner")
DEFAULT_COMPOSE_PROJECT = os.environ.get("COMPOSE_PROJECT_NAME", "watering-planner")
CONFIG_PATH = DATA_DIR / "config.json"
STATE_PATH = DATA_DIR / "status.json"
SHARED_TOKEN_PATH = Path("/data/.updater-token")
COMPOSE_FILE = PROJECT_DIR / "docker-compose.yml"
ASSET_PREFIX = "watering-planner"
MANAGED_PATHS = (
    "server.py", "public", "updater", "home-assistant", "docs", "scripts", ".github",
    "Dockerfile", "docker-compose.yml", ".dockerignore", ".gitignore", ".env.synology.example",
    "README.md", "CHANGELOG.md", "VERSION",
)
INSTALL_RUNNING = threading.Event()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_json_file(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def write_json_file(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.chmod(0o600)
    temporary.replace(path)


def read_token() -> str:
    try:
        return SHARED_TOKEN_PATH.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def authorized(headers) -> bool:
    expected = read_token()
    received = str(headers.get("X-Watering-Planner-Updater-Token", ""))
    return bool(expected and received and hmac.compare_digest(expected, received))


def normalize_version(value: object) -> str:
    version = str(value or "").strip().removeprefix("v")
    return version if re.fullmatch(r"\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?", version) else ""


def version_key(value: str) -> tuple:
    base, _, prerelease = normalize_version(value).partition("-")
    parts = tuple(int(item) for item in base.split(".")) if base else (0, 0, 0)
    return (*parts, 1 if not prerelease else 0, prerelease)


def github_request(url: str, token: str, accept: str = "application/vnd.github+json") -> bytes:
    request = Request(
        url,
        headers={
            "Accept": accept,
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "watering-planner-updater",
        },
    )
    try:
        with urlopen(request, timeout=120) as response:
            return response.read()
    except HTTPError as exc:
        if exc.code == 401:
            raise ValueError("github_token_invalid") from exc
        if exc.code == 403:
            raise ValueError("github_access_forbidden") from exc
        if exc.code == 404:
            raise ValueError("github_stable_release_missing_or_access_denied") from exc
        raise ValueError(f"github_request_failed_{exc.code}") from exc
    except URLError as exc:
        raise ValueError("github_unavailable") from exc


def github_json(url: str, token: str) -> dict:
    return json.loads(github_request(url, token).decode("utf-8"))


def latest_release() -> dict:
    config = read_json_file(CONFIG_PATH, {})
    repository = config.get("repository", "")
    token = config.get("githubToken", "")
    if not repository or not token:
        raise ValueError("updater_not_configured")
    release = github_json(f"https://api.github.com/repos/{repository}/releases/latest", token)
    if release.get("draft") or release.get("prerelease"):
        raise ValueError("latest_release_is_not_stable")
    version = normalize_version(release.get("tag_name"))
    if not version:
        raise ValueError("invalid_release_version")
    archive_name = f"{ASSET_PREFIX}-{version}.zip"
    checksum_name = f"{archive_name}.sha256"
    assets = {item.get("name"): item for item in release.get("assets", [])}
    if archive_name not in assets or checksum_name not in assets:
        raise ValueError("release_assets_missing")
    return {
        "version": version,
        "tag": release.get("tag_name"),
        "name": release.get("name") or f"v{version}",
        "publishedAt": release.get("published_at"),
        "notes": release.get("body") or "",
        "archive": assets[archive_name],
        "checksum": assets[checksum_name],
    }


def public_release(release: dict) -> dict:
    return {key: release[key] for key in ("version", "tag", "name", "publishedAt", "notes")}


def update_state(**values) -> dict:
    state = read_json_file(STATE_PATH, {})
    state.update(values, updatedAt=now_iso())
    write_json_file(STATE_PATH, state)
    return state


def run(command: list[str], timeout: int = 600) -> str:
    result = subprocess.run(command, check=False, capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError(f"{' '.join(command)}: {result.stderr.strip()}")
    return result.stdout.strip()


def updater_container_reference() -> str:
    """Prefer this container's own Docker ID over a configured display name."""
    candidates = [os.environ.get("HOSTNAME", "").strip(), UPDATER_CONTAINER_NAME]
    for candidate in candidates:
        if not candidate:
            continue
        try:
            container_id = run(["docker", "inspect", "--format", "{{.Id}}", candidate]).strip()
        except RuntimeError:
            continue
        if re.fullmatch(r"[a-f0-9]{12,64}", container_id):
            return container_id
    raise RuntimeError("updater_container_not_found")


def host_project_dir() -> str:
    raw = run(["docker", "inspect", "--format", "{{json .Mounts}}", updater_container_reference()])
    mounts = json.loads(raw)
    for mount in mounts:
        if mount.get("Type") == "bind" and mount.get("Destination") == "/project":
            return str(mount["Source"])
    raise RuntimeError("updater_host_project_mount_missing")


def compose_project_name() -> str:
    """Use the Compose project that owns the running Synology containers."""
    label_format = '{{ index .Config.Labels "com.docker.compose.project" }}'
    try:
        updater_reference = updater_container_reference()
    except RuntimeError:
        updater_reference = UPDATER_CONTAINER_NAME
    for container_name in (updater_reference, PLANNER_CONTAINER_NAME):
        try:
            project_name = run(["docker", "inspect", "--format", label_format, container_name]).strip()
        except RuntimeError:
            continue
        if re.fullmatch(r"[a-z0-9][a-z0-9_-]*", project_name):
            return project_name
    return DEFAULT_COMPOSE_PROJECT


def runtime_override(host_dir: str, path: Path) -> None:
    content = (
        "services:\n"
        "  watering-planner:\n"
        "    volumes:\n"
        f"      - {json.dumps(host_dir + '/data:/app/data')}\n"
        "  updater:\n"
        "    volumes:\n"
        f"      - {json.dumps(host_dir + ':/project')}\n"
        f"      - {json.dumps(host_dir + '/data:/data')}\n"
        "      - /var/run/docker.sock:/var/run/docker.sock\n"
    )
    path.write_text(content, encoding="utf-8")


def compose(arguments: list[str], runtime_file: Path, timeout: int = 900) -> str:
    return run([
        "docker", "compose", "--project-name", compose_project_name(), "--project-directory", str(PROJECT_DIR),
        "-f", str(COMPOSE_FILE), "-f", str(runtime_file), *arguments,
    ], timeout=timeout)


def cleanup_stopped_updater_containers(project_name: str, current_container_id: str) -> list[str]:
    """Remove interrupted Compose replacements, scoped to this project and service."""
    removed = []
    for status in ("created", "exited", "dead"):
        output = run([
            "docker", "ps", "-aq",
            "--filter", f"label=com.docker.compose.project={project_name}",
            "--filter", "label=com.docker.compose.service=updater",
            "--filter", f"status={status}",
        ])
        for container_id in output.splitlines():
            container_id = container_id.strip()
            if not container_id or current_container_id.startswith(container_id) or container_id.startswith(current_container_id):
                continue
            run(["docker", "rm", container_id])
            removed.append(container_id)
    return removed


def same_container(left: str, right: str) -> bool:
    return bool(left and right and (left.startswith(right) or right.startswith(left)))


def updater_container_ids(project_name: str) -> list[str]:
    output = run([
        "docker", "ps", "-aq",
        "--filter", f"label=com.docker.compose.project={project_name}",
        "--filter", "label=com.docker.compose.service=updater",
    ])
    return list(dict.fromkeys(item.strip() for item in output.splitlines() if item.strip()))


def cleanup_other_updater_containers(project_name: str, keep_container_id: str) -> list[str]:
    """Remove every updater from this Compose project except the verified replacement."""
    removed = []
    for container_id in updater_container_ids(project_name):
        if same_container(container_id, keep_container_id):
            continue
        run(["docker", "rm", "-f", container_id])
        removed.append(container_id)
    return removed


def helper_compose_command(project_name: str, runtime_file: Path, arguments: list[str]) -> list[str]:
    return [
        "docker", "compose", "--project-name", project_name, "--project-directory", str(PROJECT_DIR),
        "-f", str(COMPOSE_FILE), "-f", str(runtime_file), *arguments,
    ]


def wait_for_verified_updater(container_id: str, expected_image_id: str, timeout: int = 120) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        actual_image_id = run(["docker", "inspect", "--format", "{{.Image}}", container_id])
        if actual_image_id != expected_image_id:
            raise RuntimeError("updater_replacement_uses_unexpected_image")
        status = run([
            "docker", "inspect", "--format",
            "{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}", container_id,
        ])
        if status in {"healthy", "running"}:
            return
        if status in {"unhealthy", "exited", "dead"}:
            raise RuntimeError(f"updater_replacement_{status}")
        time.sleep(2)
    raise RuntimeError("updater_replacement_health_timeout")


def perform_updater_handoff(
    runtime_file: Path,
    previous_container_id: str,
    project_name: str,
    expected_image_id: str,
    target_version: str,
) -> str:
    """Recreate, verify and reconcile the updater from an independent helper container."""
    time.sleep(5)
    last_error = None
    try:
        for attempt in range(1, 4):
            try:
                run(helper_compose_command(
                    project_name,
                    runtime_file,
                    ["up", "-d", "--no-deps", "--force-recreate", "updater"],
                ), timeout=900)
                candidates = [
                    container_id
                    for container_id in updater_container_ids(project_name)
                    if not same_container(container_id, previous_container_id)
                    and run(["docker", "inspect", "--format", "{{.Image}}", container_id]) == expected_image_id
                ]
                if not candidates:
                    raise RuntimeError("updater_replacement_not_created")
                new_container_id = ""
                candidate_errors = []
                for candidate_id in candidates:
                    try:
                        wait_for_verified_updater(candidate_id, expected_image_id)
                        new_container_id = candidate_id
                        break
                    except Exception as exc:
                        candidate_errors.append(str(exc))
                if not new_container_id:
                    raise RuntimeError(f"updater_replacement_not_healthy: {'; '.join(candidate_errors)}")
                cleanup_other_updater_containers(project_name, new_container_id)
                remaining = updater_container_ids(project_name)
                if len(remaining) != 1 or not same_container(remaining[0], new_container_id):
                    raise RuntimeError("updater_reconciliation_failed")
                update_state(
                    status="ok",
                    phase="complete",
                    step=8,
                    targetVersion=target_version,
                    message=f"Update auf {target_version} erfolgreich installiert; der neue Updater ist geprüft und aktiv.",
                    finishedAt=now_iso(),
                )
                return new_container_id
            except Exception as exc:
                last_error = exc
                if attempt < 3:
                    time.sleep(3)
        raise RuntimeError(f"updater_handoff_failed_after_retries: {last_error}")
    except Exception as exc:
        update_state(status="error", phase="error", message=str(exc), finishedAt=now_iso())
        raise
    finally:
        runtime_file.unlink(missing_ok=True)


def schedule_updater_handoff(runtime_file: Path, target_version: str) -> str:
    """Start the verified handoff in an independent helper based on the new image."""
    current_container_id = updater_container_reference()
    project_name = compose_project_name()
    host_dir = host_project_dir()
    cleanup_stopped_updater_containers(project_name, current_container_id)
    image_reference = f"watering-planner-updater:{target_version}"
    image_id = run(["docker", "image", "inspect", "--format", "{{.Id}}", image_reference])
    if not re.fullmatch(r"sha256:[a-f0-9]{64}", image_id):
        raise RuntimeError("updater_image_not_found_after_build")

    helper_name = f"watering-planner-updater-handoff-{current_container_id[:12]}-{int(time.time())}"
    return run([
        "docker", "run", "-d", "--rm", "--name", helper_name,
        "-v", "/var/run/docker.sock:/var/run/docker.sock",
        "-v", f"{host_dir}:/project",
        "-v", f"{host_dir}/data:/data",
        "--entrypoint", "python",
        image_id,
        "/app/updater.py", "handoff", runtime_file.name, current_container_id,
        project_name, image_id, target_version,
    ])


def reconcile_updater_on_startup(project_version: str | None = None) -> None:
    """Let the expected current image recover interrupted replacements and the canonical name."""
    time.sleep(2)
    try:
        current_container_id = updater_container_reference()
        project_name = compose_project_name()
        version = project_version or (PROJECT_DIR / "VERSION").read_text(encoding="utf-8").strip()
        expected_image_id = run([
            "docker", "image", "inspect", "--format", "{{.Id}}",
            f"watering-planner-updater:{version}",
        ])
        current_image_id = run(["docker", "inspect", "--format", "{{.Image}}", current_container_id])
        if current_image_id != expected_image_id:
            print("Updater startup reconciliation skipped: running image is not the project version.")
            return
        cleanup_other_updater_containers(project_name, current_container_id)
        current_name = run(["docker", "inspect", "--format", "{{.Name}}", current_container_id]).lstrip("/")
        if current_name != UPDATER_CONTAINER_NAME:
            run(["docker", "rename", current_container_id, UPDATER_CONTAINER_NAME])
        remaining = updater_container_ids(project_name)
        if len(remaining) != 1 or not same_container(remaining[0], current_container_id):
            raise RuntimeError("updater_startup_reconciliation_failed")
        print(f"Updater startup reconciliation complete: {UPDATER_CONTAINER_NAME} uses {version}.")
    except Exception as exc:
        print(f"Updater startup reconciliation warning: {exc}")


def safe_zip_members(archive: zipfile.ZipFile, expected_root: str) -> list[zipfile.ZipInfo]:
    members = archive.infolist()
    prefix = f"{expected_root}/"
    if not members:
        raise ValueError("invalid_release_archive_layout")
    for member in members:
        path = PurePosixPath(member.filename)
        if member.filename.startswith("/") or ".." in path.parts or not member.filename.startswith(prefix):
            raise ValueError("invalid_release_archive_layout")
    names = {member.filename.rstrip("/") for member in members}
    for required in ("server.py", "public/index.html", "updater/Dockerfile", "docker-compose.yml", "CHANGELOG.md", "VERSION"):
        if f"{expected_root}/{required}" not in names:
            raise ValueError(f"release_archive_missing_{required}")
    return members


def download_asset(asset: dict, token: str, destination: Path) -> None:
    destination.write_bytes(github_request(asset["url"], token, "application/octet-stream"))


def install_update(current_version: str) -> None:
    try:
        backup_path = None
        runtime_file = None
        handoff_scheduled = False
        try:
            update_state(type="install", status="running", phase="release", step=1, totalSteps=8, currentVersion=current_version, message="Suche nach dem neuesten stabilen Release.")
            release = latest_release()
            update_state(
                targetVersion=release["version"],
                releaseName=release["name"],
                releasePublishedAt=release["publishedAt"],
                releaseNotes=release["notes"],
            )
            if version_key(release["version"]) <= version_key(current_version):
                update_state(status="ok", phase="complete", step=8, targetVersion=release["version"], message="Die installierte Version ist bereits aktuell.", finishedAt=now_iso())
                return
            config = read_json_file(CONFIG_PATH, {})
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix="watering-update-", dir=DATA_DIR) as temporary:
                work = Path(temporary)
                archive_path = work / release["archive"]["name"]
                checksum_path = work / release["checksum"]["name"]
                update_state(phase="download", step=2, targetVersion=release["version"], message=f"Lade Release {release['version']} herunter.")
                download_asset(release["archive"], config["githubToken"], archive_path)
                download_asset(release["checksum"], config["githubToken"], checksum_path)
                expected = re.search(r"[a-fA-F0-9]{64}", checksum_path.read_text(encoding="utf-8"))
                actual = hashlib.sha256(archive_path.read_bytes()).hexdigest()
                if not expected or not hmac.compare_digest(expected.group(0).lower(), actual):
                    raise ValueError("release_checksum_mismatch")
                update_state(phase="verify", step=3, message="Prüfsumme und Paketinhalt sind gültig.")
                expected_root = f"{ASSET_PREFIX}-{release['version']}"
                extract_dir = work / "extract"
                with zipfile.ZipFile(archive_path) as archive:
                    safe_zip_members(archive, expected_root)
                    archive.extractall(extract_dir)
                source = extract_dir / expected_root
                if (source / "VERSION").read_text(encoding="utf-8").strip() != release["version"]:
                    raise ValueError("release_version_mismatch")
                backups = DATA_DIR / "backups"
                backups.mkdir(exist_ok=True)
                backup_path = backups / f"watering-planner-{current_version or 'unknown'}-{int(time.time())}.tar.gz"
                update_state(phase="backup", step=4, message="Sichere die bisherige Programmversion.")
                with tarfile.open(backup_path, "w:gz") as tar:
                    for entry in MANAGED_PATHS:
                        candidate = PROJECT_DIR / entry
                        if candidate.exists():
                            tar.add(candidate, arcname=entry)
                update_state(phase="files", step=5, message="Übernehme neue Programmdateien; Daten und Einstellungen bleiben erhalten.")
                for entry in MANAGED_PATHS:
                    target = PROJECT_DIR / entry
                    incoming = source / entry
                    if target.is_dir():
                        shutil.rmtree(target)
                    elif target.exists():
                        target.unlink()
                    if incoming.exists():
                        shutil.copytree(incoming, target) if incoming.is_dir() else shutil.copy2(incoming, target)
                runtime_file = DATA_DIR / f"runtime-{int(time.time())}.yml"
                runtime_override(host_project_dir(), runtime_file)
                update_state(phase="build", step=6, message="Baue das neue Container-Image.")
                compose(["build", "--no-cache", "watering-planner", "updater"], runtime_file)
                update_state(phase="restart", step=7, message="Starte den Planner neu und prüfe seinen Zustand.")
                compose(["up", "-d", "--no-deps", "--force-recreate", "watering-planner"], runtime_file)
                container_id = compose(["ps", "-q", "watering-planner"], runtime_file)
                deadline = time.time() + 120
                while time.time() < deadline:
                    status = run(["docker", "inspect", "--format", "{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}", container_id])
                    if status in {"healthy", "running"}:
                        break
                    if status in {"unhealthy", "exited", "dead"}:
                        raise RuntimeError(f"watering_planner_container_{status}")
                    time.sleep(2)
                else:
                    raise RuntimeError("watering_planner_health_timeout")
                update_state(phase="handoff", step=8, message="Aktiviere den neuen Updater ohne den laufenden Installationsprozess zu unterbrechen.")
                schedule_updater_handoff(runtime_file, release["version"])
                handoff_scheduled = True
        except Exception as exc:
            if backup_path and backup_path.exists():
                try:
                    for entry in MANAGED_PATHS:
                        target = PROJECT_DIR / entry
                        if target.is_dir():
                            shutil.rmtree(target)
                        elif target.exists():
                            target.unlink()
                    with tarfile.open(backup_path, "r:gz") as tar:
                        tar.extractall(PROJECT_DIR, filter="data")
                    if runtime_file and runtime_file.exists():
                        compose(["build", "--no-cache", "watering-planner"], runtime_file)
                        compose(["up", "-d", "--no-deps", "--force-recreate", "watering-planner"], runtime_file)
                except Exception as rollback_error:
                    exc = RuntimeError(f"{exc}; rollback_failed: {rollback_error}")
            update_state(status="error", phase="error", message=str(exc), finishedAt=now_iso())
        finally:
            if runtime_file and not handoff_scheduled:
                runtime_file.unlink(missing_ok=True)
    finally:
        INSTALL_RUNNING.clear()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args) -> None:
        print(f"[{self.log_date_time_string()}] {format % args}")

    def send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def payload(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        return json.loads(self.rfile.read(length).decode("utf-8")) if length else {}

    def do_GET(self) -> None:
        if self.path == "/health":
            self.send_json(HTTPStatus.OK, {"ok": True})
            return
        if self.path == "/api/status":
            config = read_json_file(CONFIG_PATH, {})
            state = read_json_file(STATE_PATH, None)
            token_stored = bool(config.get("githubToken"))
            self.send_json(HTTPStatus.OK, {"ok": True, "configured": bool(config.get("repository") and token_stored), "tokenStored": token_stored, "repository": config.get("repository"), "channel": "stable", "lastOperation": state})
            return
        self.send_json(HTTPStatus.NOT_FOUND, {"reason": "not_found"})

    def do_POST(self) -> None:
        try:
            if self.path == "/api/setup":
                existing_token = read_token()
                if existing_token and not authorized(self.headers):
                    self.send_json(HTTPStatus.UNAUTHORIZED, {"reason": "updater_auth_required"})
                    return
                payload = self.payload()
                repository = str(payload.get("repository", "")).strip()
                github_token = str(payload.get("githubToken", "")).strip()
                if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
                    raise ValueError("invalid_repository")
                if len(github_token) < 20:
                    raise ValueError("invalid_github_token")
                github_json(f"https://api.github.com/repos/{repository}", github_token)
                write_json_file(CONFIG_PATH, {"repository": repository, "githubToken": github_token, "channel": "stable"})
                if not existing_token:
                    SHARED_TOKEN_PATH.write_text(os.urandom(32).hex() + "\n", encoding="utf-8")
                    SHARED_TOKEN_PATH.chmod(0o600)
                update_state(type="setup", status="ok", message="GitHub-Zugang eingerichtet.", finishedAt=now_iso())
                self.send_json(HTTPStatus.OK, {"ok": True, "configured": True, "repository": repository})
                return
            if not authorized(self.headers):
                self.send_json(HTTPStatus.UNAUTHORIZED, {"reason": "updater_auth_required"})
                return
            payload = self.payload()
            current = normalize_version(payload.get("currentVersion"))
            if self.path == "/api/check":
                release = latest_release()
                self.send_json(HTTPStatus.OK, {"ok": True, "currentVersion": current, "updateAvailable": version_key(release["version"]) > version_key(current), "release": public_release(release)})
                return
            if self.path == "/api/install":
                if INSTALL_RUNNING.is_set():
                    raise ValueError("update_already_running")
                INSTALL_RUNNING.set()
                threading.Thread(target=install_update, args=(current,), daemon=True).start()
                self.send_json(HTTPStatus.ACCEPTED, {"ok": True, "accepted": True})
                return
            self.send_json(HTTPStatus.NOT_FOUND, {"reason": "not_found"})
        except (ValueError, KeyError, json.JSONDecodeError) as exc:
            self.send_json(HTTPStatus.BAD_REQUEST, {"reason": str(exc)})
        except Exception as exc:
            self.send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"reason": str(exc)})


def main() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    threading.Thread(target=reconcile_updater_on_startup, daemon=True).start()
    print(f"Watering Planner updater listening on {PORT}")
    server.serve_forever()


if __name__ == "__main__":
    if len(sys.argv) == 7 and sys.argv[1] == "handoff":
        runtime_name, previous_container_id, project_name, expected_image_id, target_version = sys.argv[2:]
        if Path(runtime_name).name != runtime_name or not re.fullmatch(r"runtime-[0-9]+\.yml", runtime_name):
            raise SystemExit("invalid_handoff_runtime_file")
        perform_updater_handoff(
            DATA_DIR / runtime_name,
            previous_container_id,
            project_name,
            expected_image_id,
            target_version,
        )
    else:
        main()
