from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


@dataclass(frozen=True)
class Database:
    """Instance-scoped SQLite connection factory.

    Keeping paths on this value object allows tests and application instances to
    use independent databases without mutating module globals.
    """

    data_dir: Path
    db_path: Path
    timeout_seconds: int = 30

    @contextmanager
    def connection(self, *, immediate: bool = False) -> Iterator[sqlite3.Connection]:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path, timeout=self.timeout_seconds)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 30000")
        try:
            if immediate:
                conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def ensure_writable(self) -> None:
        try:
            self.data_dir.mkdir(parents=True, exist_ok=True)
            probe = self.data_dir / ".write-test"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink(missing_ok=True)
            if self.db_path.exists():
                with self.connection() as conn:
                    conn.execute("PRAGMA user_version")
                    conn.execute("CREATE TABLE IF NOT EXISTS __write_probe (id INTEGER PRIMARY KEY)")
                    conn.execute("DROP TABLE __write_probe")
        except OSError as exc:
            raise RuntimeError(
                f"DATA_DIR ist nicht beschreibbar: {self.data_dir}. "
                "Prüfe im Synology Container Manager das Volume ./data:/app/data "
                "und deaktiviere Read-only."
            ) from exc
        except sqlite3.OperationalError as exc:
            raise RuntimeError(
                f"SQLite-Datenbank ist nicht beschreibbar: {self.db_path}. "
                "Prüfe Besitzer/Rechte von data/watering.sqlite3 und ob das "
                "Volume im Container Manager beschreibbar gemountet ist."
            ) from exc


@contextmanager
def connection(data_dir: Path, db_path: Path) -> Iterator[sqlite3.Connection]:
    """Compatibility helper for legacy callers."""
    with Database(data_dir, db_path).connection() as conn:
        yield conn
