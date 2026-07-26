from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterable, Mapping
from datetime import datetime, timezone

from watering_backend.validation import normalize_hose_numbers


SCHEMA_VERSION = 7

BASE_SCHEMA = """
CREATE TABLE IF NOT EXISTS plant_catalog (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    category TEXT NOT NULL,
    base_ml_per_l_day REAL NOT NULL,
    sun_factor REAL NOT NULL,
    drought_sensitivity REAL NOT NULL,
    crop_coefficient REAL NOT NULL DEFAULT 1.0,
    canopy_m2_medium REAL NOT NULL DEFAULT 0.2,
    recommended_pot_liters REAL NOT NULL DEFAULT 10,
    moisture_preference REAL NOT NULL DEFAULT 1.0,
    notes TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS balcony_settings (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    orientation TEXT NOT NULL,
    orientation_deg REAL NOT NULL DEFAULT 180,
    width_m REAL NOT NULL,
    depth_m REAL NOT NULL,
    location TEXT NOT NULL,
    latitude REAL NOT NULL DEFAULT 52.52,
    longitude REAL NOT NULL DEFAULT 13.405,
    timezone_name TEXT NOT NULL DEFAULT 'Europe/Berlin',
    wall_height_m REAL NOT NULL,
    tank_capacity_ml INTEGER NOT NULL,
    tank_current_ml INTEGER NOT NULL,
    refill_tank_capacity_ml INTEGER NOT NULL DEFAULT 30000,
    refill_tank_current_ml INTEGER NOT NULL DEFAULT 30000,
    refill_pump_ml_per_min INTEGER NOT NULL DEFAULT 1000,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS terrace_walls (
    side TEXT PRIMARY KEY,
    height_m REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS pump_outlets (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    ml_per_run INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS plants (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    catalog_id TEXT NOT NULL REFERENCES plant_catalog(id),
    custom_name TEXT NOT NULL,
    size TEXT NOT NULL,
    pot_liters REAL NOT NULL,
    pot_type TEXT NOT NULL,
    outlet_id INTEGER NOT NULL REFERENCES pump_outlets(id),
    pos_x REAL NOT NULL DEFAULT 0.5,
    pos_y REAL NOT NULL DEFAULT 0.5,
    hose_numbers TEXT NOT NULL DEFAULT '',
    target_ml_per_cycle REAL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS watering_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ran_at TEXT NOT NULL,
    delivered_ml INTEGER NOT NULL,
    temperature_c REAL,
    rain_mm REAL,
    source TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pump_calibration_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    calibrated_at TEXT NOT NULL,
    pump_name TEXT NOT NULL,
    measured_level_ml INTEGER NOT NULL,
    baseline_at TEXT NOT NULL,
    baseline_level_ml INTEGER NOT NULL,
    cycles INTEGER NOT NULL,
    nominal_ml INTEGER NOT NULL,
    measured_ml INTEGER NOT NULL,
    result_value REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS refill_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ran_at TEXT NOT NULL,
    target_date TEXT NOT NULL,
    requested_ml INTEGER NOT NULL,
    transferred_ml INTEGER NOT NULL,
    duration_seconds INTEGER NOT NULL,
    window_label TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tank_fill_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ran_at TEXT NOT NULL,
    tank_name TEXT NOT NULL,
    previous_ml INTEGER NOT NULL,
    new_ml INTEGER NOT NULL,
    capacity_ml INTEGER NOT NULL,
    source TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS irrigation_hoses (
    number TEXT PRIMARY KEY,
    outlet_id INTEGER NOT NULL REFERENCES pump_outlets(id),
    plant_id INTEGER REFERENCES plants(id)
);

CREATE TABLE IF NOT EXISTS app_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})")}


def _add_column(conn: sqlite3.Connection, table: str, name: str, definition: str) -> None:
    if name not in _columns(conn, table):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")


def _window_key(target_date: str, window_start: str, window_end: str) -> str:
    def canonical(value: str) -> str:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).isoformat()

    try:
        start = canonical(window_start)
        end = canonical(window_end)
    except ValueError:
        start, end = window_start, window_end
    return f"{target_date}|{start}|{end}"


def _migrate_refill_window_observations(conn: sqlite3.Connection) -> None:
    """Preserve observations written by early 1.5 builds in the durable plan table."""
    rows = conn.execute(
        """
        SELECT target_date, window_label, window_start, window_end,
               need_detected, eligible, blocking_reason, observed_at
        FROM refill_window_observations
        """
    ).fetchall()
    for row in rows:
        (
            target_date,
            window_label,
            window_start,
            window_end,
            need_detected,
            eligible,
            blocking_reason,
            observed_at,
        ) = tuple(row)
        conn.execute(
            """
            INSERT INTO refill_window_plans(
                window_key, target_date, window_label,
                window_start, window_end, need_detected, executable,
                expected_transfer_ml, blocking_reason, observed_in_window,
                created_at, last_checked_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
            ON CONFLICT(window_key) DO NOTHING
            """,
            (
                _window_key(
                    str(target_date),
                    str(window_start),
                    str(window_end),
                ),
                target_date,
                window_label,
                window_start,
                window_end,
                int(need_detected),
                int(eligible),
                1 if eligible else 0,
                blocking_reason,
                observed_at,
                observed_at,
            ),
        )


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
    _add_column(conn, "notification_state", "last_attempt_at", "TEXT")
    _add_column(conn, "notification_state", "last_error", "TEXT NOT NULL DEFAULT ''")
    _add_column(conn, "notification_state", "consecutive_failures", "INTEGER NOT NULL DEFAULT 0")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS refill_window_observations (
            target_date TEXT NOT NULL,
            window_label TEXT NOT NULL,
            window_start TEXT NOT NULL,
            window_end TEXT NOT NULL,
            need_detected INTEGER NOT NULL DEFAULT 0,
            eligible INTEGER NOT NULL DEFAULT 0,
            blocking_reason TEXT NOT NULL DEFAULT '',
            observed_at TEXT NOT NULL,
            PRIMARY KEY (target_date, window_label)
        )
        """
    )
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS refill_window_plans (
            window_key TEXT PRIMARY KEY,
            target_date TEXT NOT NULL,
            window_label TEXT NOT NULL,
            window_start TEXT NOT NULL,
            window_end TEXT NOT NULL,
            need_detected INTEGER NOT NULL DEFAULT 0,
            executable INTEGER NOT NULL DEFAULT 0,
            expected_transfer_ml INTEGER NOT NULL DEFAULT 0,
            blocking_reason TEXT NOT NULL DEFAULT '',
            observed_in_window INTEGER NOT NULL DEFAULT 0,
            fulfilled_at TEXT,
            cancelled_at TEXT,
            created_at TEXT NOT NULL,
            last_checked_at TEXT NOT NULL
        );

        CREATE UNIQUE INDEX IF NOT EXISTS ux_refill_window_plans_bounds
        ON refill_window_plans(target_date, window_start, window_end);

        CREATE INDEX IF NOT EXISTS ix_refill_window_plans_due
        ON refill_window_plans(target_date, window_end, cancelled_at);

        CREATE TABLE IF NOT EXISTS refill_runs (
            run_id TEXT PRIMARY KEY,
            run_type TEXT NOT NULL
                CHECK (run_type IN ('automatic', 'manual')),
            status TEXT NOT NULL
                CHECK (
                    status IN (
                        'reserved', 'running', 'completed', 'failed',
                        'expired', 'cancelled'
                    )
                ),
            created_at TEXT NOT NULL,
            authorized_at TEXT NOT NULL,
            started_at TEXT,
            completed_at TEXT,
            target_date TEXT NOT NULL,
            window_key TEXT NOT NULL DEFAULT '',
            window_label TEXT NOT NULL DEFAULT '',
            window_start TEXT NOT NULL DEFAULT '',
            window_end TEXT NOT NULL DEFAULT '',
            requested_ml INTEGER NOT NULL,
            planned_transfer_ml INTEGER NOT NULL,
            planned_duration_seconds INTEGER NOT NULL,
            main_tank_start_ml INTEGER NOT NULL,
            refill_tank_start_ml INTEGER NOT NULL,
            pump_ml_per_min INTEGER NOT NULL,
            expected_complete_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            limit_reason TEXT NOT NULL DEFAULT '',
            source TEXT NOT NULL,
            accounted_transfer_ml INTEGER,
            physical_transfer_ml INTEGER,
            main_accounted_ml INTEGER,
            main_before_complete_ml INTEGER,
            main_after_complete_ml INTEGER,
            refill_before_complete_ml INTEGER,
            refill_after_complete_ml INTEGER,
            consistency_delta_ml INTEGER NOT NULL DEFAULT 0,
            consistency_note TEXT NOT NULL DEFAULT '',
            needs_manual_review INTEGER NOT NULL DEFAULT 0,
            error_text TEXT NOT NULL DEFAULT '',
            completion_reason TEXT NOT NULL DEFAULT '',
            reconciled_at TEXT,
            reconciliation_mode TEXT NOT NULL DEFAULT '',
            reconciliation_note TEXT NOT NULL DEFAULT '',
            reconciliation_payload TEXT NOT NULL DEFAULT '',
            active_slot INTEGER,
            updated_at TEXT NOT NULL
        );

        CREATE UNIQUE INDEX IF NOT EXISTS ux_refill_runs_active_slot
        ON refill_runs(active_slot)
        WHERE active_slot IS NOT NULL;

        CREATE INDEX IF NOT EXISTS ix_refill_runs_status_updated
        ON refill_runs(status, updated_at DESC);
        """
    )
    _add_column(
        conn,
        "refill_runs",
        "physical_transfer_ml",
        "INTEGER",
    )
    _add_column(
        conn,
        "refill_runs",
        "main_accounted_ml",
        "INTEGER",
    )
    _add_column(
        conn,
        "refill_runs",
        "reconciled_at",
        "TEXT",
    )
    _add_column(
        conn,
        "refill_runs",
        "reconciliation_mode",
        "TEXT NOT NULL DEFAULT ''",
    )
    _add_column(
        conn,
        "refill_runs",
        "reconciliation_note",
        "TEXT NOT NULL DEFAULT ''",
    )
    _add_column(
        conn,
        "refill_runs",
        "reconciliation_payload",
        "TEXT NOT NULL DEFAULT ''",
    )
    _migrate_refill_window_observations(conn)
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")


def _ensure_legacy_columns(conn: sqlite3.Connection) -> None:
    additions = (
        ("balcony_settings", "latitude", "REAL NOT NULL DEFAULT 52.52"),
        ("balcony_settings", "longitude", "REAL NOT NULL DEFAULT 13.405"),
        ("balcony_settings", "timezone_name", "TEXT NOT NULL DEFAULT 'Europe/Berlin'"),
        ("balcony_settings", "orientation_deg", "REAL NOT NULL DEFAULT 180"),
        ("balcony_settings", "refill_tank_capacity_ml", "INTEGER NOT NULL DEFAULT 30000"),
        ("balcony_settings", "refill_tank_current_ml", "INTEGER NOT NULL DEFAULT 30000"),
        ("balcony_settings", "refill_pump_ml_per_min", "INTEGER NOT NULL DEFAULT 1000"),
        ("refill_events", "window_label", "TEXT NOT NULL DEFAULT ''"),
        ("watering_events", "actual_consumed_ml", "INTEGER"),
        ("plants", "pos_x", "REAL NOT NULL DEFAULT 0.5"),
        ("plants", "pos_y", "REAL NOT NULL DEFAULT 0.5"),
        ("plants", "hose_numbers", "TEXT NOT NULL DEFAULT ''"),
        ("plants", "target_ml_per_cycle", "REAL"),
        ("plant_catalog", "crop_coefficient", "REAL NOT NULL DEFAULT 1.0"),
        ("plant_catalog", "canopy_m2_medium", "REAL NOT NULL DEFAULT 0.2"),
        ("plant_catalog", "recommended_pot_liters", "REAL NOT NULL DEFAULT 10"),
        ("plant_catalog", "moisture_preference", "REAL NOT NULL DEFAULT 1.0"),
    )
    for table, name, definition in additions:
        _add_column(conn, table, name, definition)


def _upsert_catalog(
    conn: sqlite3.Connection,
    catalog: Iterable[tuple],
    profiles: Mapping[str, Mapping[str, float]],
) -> None:
    conn.executemany(
        """
        INSERT INTO plant_catalog
            (id, name, category, base_ml_per_l_day, sun_factor, drought_sensitivity, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            name = excluded.name,
            category = excluded.category,
            base_ml_per_l_day = excluded.base_ml_per_l_day,
            sun_factor = excluded.sun_factor,
            drought_sensitivity = excluded.drought_sensitivity,
            notes = excluded.notes
        """,
        catalog,
    )
    for catalog_id, profile in profiles.items():
        conn.execute(
            """
            UPDATE plant_catalog
            SET crop_coefficient = ?, canopy_m2_medium = ?,
                recommended_pot_liters = ?, moisture_preference = ?
            WHERE id = ?
            """,
            (
                profile["crop_coefficient"],
                profile["canopy_m2_medium"],
                profile["recommended_pot_liters"],
                profile["moisture_preference"],
                catalog_id,
            ),
        )


def _migrate_watering_amount_standard(
    conn: sqlite3.Connection,
    *,
    previous_calibration: float,
    current_calibration: float,
    minimum_percent: float,
    maximum_percent: float,
) -> None:
    row = conn.execute(
        "SELECT value FROM app_settings WHERE key = 'watering_amount_percent'"
    ).fetchone()
    basis = conn.execute(
        "SELECT value FROM app_settings WHERE key = 'watering_amount_calibration_basis'"
    ).fetchone()
    if row and not basis:
        try:
            amount_percent = float(row["value"])
        except ValueError:
            amount_percent = 100
        migrated_percent = max(
            minimum_percent,
            min(
                maximum_percent,
                amount_percent * previous_calibration / current_calibration,
            ),
        )
        conn.execute(
            """
            INSERT INTO app_settings (key, value)
            VALUES ('watering_amount_percent', ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (str(migrated_percent),),
        )
    conn.execute(
        """
        INSERT INTO app_settings (key, value)
        VALUES ('watering_amount_calibration_basis', ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        (str(current_calibration),),
    )


def _seed_defaults(
    conn: sqlite3.Connection,
    *,
    balcony: Mapping[str, object],
    walls: Iterable[tuple[str, float]],
    now_iso: Callable[[], str],
) -> None:
    if conn.execute("SELECT COUNT(*) FROM balcony_settings").fetchone()[0] == 0:
        conn.execute(
            """
            INSERT INTO balcony_settings
                (
                    id, orientation, orientation_deg, width_m, depth_m, location,
                    latitude, longitude, timezone_name, wall_height_m,
                    tank_capacity_ml, tank_current_ml, refill_tank_capacity_ml,
                    refill_tank_current_ml, refill_pump_ml_per_min, updated_at
                )
            VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                balcony["orientation"],
                balcony["orientation_deg"],
                balcony["width_m"],
                balcony["depth_m"],
                balcony["location"],
                balcony["latitude"],
                balcony["longitude"],
                balcony["timezone_name"],
                balcony["wall_height_m"],
                balcony["tank_capacity_ml"],
                balcony["tank_current_ml"],
                balcony["refill_tank_capacity_ml"],
                balcony["refill_tank_current_ml"],
                balcony["refill_pump_ml_per_min"],
                now_iso(),
            ),
        )
    if conn.execute("SELECT COUNT(*) FROM pump_outlets").fetchone()[0] == 0:
        conn.executemany(
            "INSERT INTO pump_outlets (id, name, ml_per_run) VALUES (?, ?, ?)",
            [(1, "S", 15), (2, "M", 30), (3, "L", 60)],
        )
    if conn.execute("SELECT COUNT(*) FROM terrace_walls").fetchone()[0] == 0:
        conn.executemany(
            "INSERT INTO terrace_walls (side, height_m) VALUES (?, ?)",
            list(walls),
        )
    if conn.execute("SELECT COUNT(*) FROM plants").fetchone()[0] == 0:
        conn.executemany(
            """
            INSERT INTO plants
                (
                    catalog_id, custom_name, size, pot_liters, pot_type,
                    outlet_id, pos_x, pos_y, hose_numbers,
                    target_ml_per_cycle, created_at
                )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                ("olive", "Olive", "medium", 28, "overflow", 2, 0.25, 0.7, "1", 30, now_iso()),
                ("tomato", "Tomate", "medium", 18, "closed", 3, 0.7, 0.55, "2", 60, now_iso()),
                ("lavender", "Lavendel", "small", 10, "overflow", 1, 0.45, 0.25, "3", 15, now_iso()),
            ],
        )
    for plant in conn.execute("SELECT id, outlet_id, hose_numbers FROM plants"):
        for number in normalize_hose_numbers(plant["hose_numbers"]):
            conn.execute(
                """
                INSERT OR IGNORE INTO irrigation_hoses (number, outlet_id, plant_id)
                VALUES (?, ?, ?)
                """,
                (number, int(plant["outlet_id"]), int(plant["id"])),
            )


def initialize(
    conn: sqlite3.Connection,
    *,
    catalog: Iterable[tuple],
    profiles: Mapping[str, Mapping[str, float]],
    default_balcony: Mapping[str, object],
    default_walls: Iterable[tuple[str, float]],
    now_iso: Callable[[], str],
    previous_calibration: float,
    current_calibration: float,
    minimum_percent: float,
    maximum_percent: float,
) -> None:
    """Create the base schema, apply additive migrations, and seed defaults."""
    conn.executescript(BASE_SCHEMA)
    _ensure_legacy_columns(conn)
    migrate(conn)
    _upsert_catalog(conn, catalog, profiles)
    _migrate_watering_amount_standard(
        conn,
        previous_calibration=previous_calibration,
        current_calibration=current_calibration,
        minimum_percent=minimum_percent,
        maximum_percent=maximum_percent,
    )
    _seed_defaults(
        conn,
        balcony=default_balcony,
        walls=default_walls,
        now_iso=now_iso,
    )
