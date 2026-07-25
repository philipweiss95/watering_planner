from __future__ import annotations

import sqlite3


SCHEMA_VERSION = 2


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})")}


def _add_column(conn: sqlite3.Connection, table: str, name: str, definition: str) -> None:
    if name not in _columns(conn, table):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")


def migrate(conn: sqlite3.Connection) -> None:
    """Apply additive migrations so databases from every published version remain usable."""
    _add_column(conn, "watering_events", "run_id", "TEXT")
    _add_column(conn, "refill_events", "run_id", "TEXT")
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS ux_watering_events_run_id
        ON watering_events(run_id)
        WHERE run_id IS NOT NULL AND run_id <> ''
        """
    )
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS ux_refill_events_run_id
        ON refill_events(run_id)
        WHERE run_id IS NOT NULL AND run_id <> ''
        """
    )
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS notification_state (
            alert_key TEXT PRIMARY KEY,
            severity TEXT NOT NULL,
            active INTEGER NOT NULL DEFAULT 0,
            last_changed_at TEXT NOT NULL,
            last_notified_at TEXT,
            message TEXT NOT NULL DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS notification_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            alert_key TEXT NOT NULL,
            severity TEXT NOT NULL,
            event_state TEXT NOT NULL,
            subject TEXT NOT NULL,
            message TEXT NOT NULL,
            created_at TEXT NOT NULL,
            sent_at TEXT,
            status TEXT NOT NULL,
            error TEXT NOT NULL DEFAULT ''
        );
        """
    )
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
