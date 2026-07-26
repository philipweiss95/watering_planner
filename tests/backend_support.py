from __future__ import annotations

import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import server


class TemporaryBackend:
    """Isolate tests that still exercise the legacy module-level application."""

    def __init__(self, *, initialize: bool = True):
        self.initialize = initialize
        self._temporary_directory: tempfile.TemporaryDirectory[str] | None = None
        self._original_data_dir: Path | None = None
        self._original_db_path: Path | None = None
        self.data_dir: Path
        self.db_path: Path

    def __enter__(self) -> "TemporaryBackend":
        server.stop_notification_worker()
        self._temporary_directory = tempfile.TemporaryDirectory()
        self._original_data_dir = server.DATA_DIR
        self._original_db_path = server.DB_PATH
        self.data_dir = Path(self._temporary_directory.name)
        self.db_path = self.data_dir / "watering.sqlite3"
        server.DATA_DIR = self.data_dir
        server.DB_PATH = self.db_path
        if self.initialize:
            server.init_db()
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        server.stop_notification_worker()
        if self._original_data_dir is not None:
            server.DATA_DIR = self._original_data_dir
        if self._original_db_path is not None:
            server.DB_PATH = self._original_db_path
        if self._temporary_directory is not None:
            self._temporary_directory.cleanup()


@contextmanager
def fixed_backend_time(value: datetime):
    """Freeze the two clocks exposed by the current compatibility module."""

    if value.tzinfo is None:
        raise ValueError("fixed_backend_time requires an aware datetime")
    utc_value = value.astimezone(timezone.utc)
    with (
        patch.object(server, "local_now", side_effect=lambda _timezone_name: value),
        patch.object(server, "now_iso", return_value=utc_value.isoformat()),
    ):
        yield


def selected(source: dict, *keys: str) -> dict:
    return {key: source[key] for key in keys}


def rounded(value: object, digits: int = 6) -> float:
    return round(float(value), digits)
