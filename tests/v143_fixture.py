from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any


# v1.4.3 is the updater bridge release. Its application/database code is
# unchanged from v1.4.2, so this is the schema after that server has completed
# init_db() and is then carried by the immutable v1.4.3 tag.
V143_SCHEMA = """
PRAGMA user_version = 0;

CREATE TABLE plant_catalog (
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
CREATE TABLE balcony_settings (
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
CREATE TABLE terrace_walls (
    side TEXT PRIMARY KEY,
    height_m REAL NOT NULL
);
CREATE TABLE pump_outlets (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    ml_per_run INTEGER NOT NULL
);
CREATE TABLE plants (
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
CREATE TABLE watering_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ran_at TEXT NOT NULL,
    delivered_ml INTEGER NOT NULL,
    temperature_c REAL,
    rain_mm REAL,
    source TEXT NOT NULL,
    actual_consumed_ml INTEGER
);
CREATE TABLE pump_calibration_events (
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
CREATE TABLE refill_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ran_at TEXT NOT NULL,
    target_date TEXT NOT NULL,
    requested_ml INTEGER NOT NULL,
    transferred_ml INTEGER NOT NULL,
    duration_seconds INTEGER NOT NULL,
    window_label TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL
);
CREATE TABLE tank_fill_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ran_at TEXT NOT NULL,
    tank_name TEXT NOT NULL,
    previous_ml INTEGER NOT NULL,
    new_ml INTEGER NOT NULL,
    capacity_ml INTEGER NOT NULL,
    source TEXT NOT NULL
);
CREATE TABLE irrigation_hoses (
    number TEXT PRIMARY KEY,
    outlet_id INTEGER NOT NULL REFERENCES pump_outlets(id),
    plant_id INTEGER REFERENCES plants(id)
);
CREATE TABLE app_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


V143_WATER_MODEL_CALIBRATION = 0.20

V143_BALCONY = (
    1,
    "west",
    247.5,
    5.35,
    1.85,
    "Dachterrasse Bestand",
    48.1374,
    11.5755,
    "Europe/Berlin",
    1.35,
    45_000,
    17_321,
    55_000,
    41_007,
    1_350,
    "2026-05-30T18:20:00+00:00",
)

V143_CATALOG = (
    (
        "olive",
        "Olivenbaum",
        "Mediterrane Gehölze",
        22,
        1.15,
        0.8,
        0.62,
        0.42,
        35,
        0.72,
        "Mag es hell, eher trocken, aber nicht komplett austrocknen lassen.",
    ),
    (
        "tomato",
        "Tomatenpflanze",
        "Gemüse",
        48,
        1.25,
        1.2,
        1.15,
        0.45,
        20,
        1.12,
        "Sehr hoher Bedarf, regelmäßige Wassergaben vermeiden Fruchtplatzen.",
    ),
    (
        "lavender",
        "Lavendel",
        "Kräuter",
        16,
        1.25,
        0.65,
        0.45,
        0.2,
        10,
        0.62,
        "Trockenheitsliebend, Staunässe vermeiden.",
    ),
    (
        "citrus",
        "Zitrusbaum",
        "Mediterrane Gehölze",
        30,
        1.15,
        0.95,
        0.82,
        0.48,
        35,
        0.95,
        "Hell und gleichmäßig feucht, bei Hitze und Fruchtansatz durstiger.",
    ),
)

V143_OUTLETS = (
    (1, "Fein", 18),
    (2, "Mittel", 32),
    (3, "Stark", 67),
    (4, "Maximal", 95),
)

V143_PLANTS = (
    (
        11,
        "olive",
        "Olivia",
        "large",
        38.5,
        "overflow",
        2,
        0.12,
        0.84,
        "01, 02",
        64.0,
        "2025-04-12T08:00:00+00:00",
    ),
    (
        12,
        "tomato",
        "Roma links",
        "medium",
        19.0,
        "reservoir",
        3,
        0.68,
        0.53,
        "03",
        67.0,
        "2025-05-03T09:15:00+00:00",
    ),
    (
        13,
        "lavender",
        "Lavendel klein",
        "small",
        7.5,
        "closed",
        1,
        0.37,
        0.22,
        "04",
        18.0,
        "2025-03-22T10:30:00+00:00",
    ),
    (
        14,
        "citrus",
        "Zitrone am Gelander",
        "large",
        42.0,
        "reservoir_overflow",
        4,
        0.9,
        0.72,
        "05, 06",
        190.0,
        "2025-04-28T07:45:00+00:00",
    ),
    (
        15,
        "olive",
        "Olivenbaum Altbestand",
        "tree",
        82.0,
        "reservoir_overflow",
        3,
        0.18,
        0.76,
        "08",
        67.0,
        "2024-03-16T11:20:00+00:00",
    ),
)

V143_HOSES = (
    ("01", 2, 11),
    ("02", 2, 11),
    ("03", 3, 12),
    ("04", 1, 13),
    ("05", 4, 14),
    ("06", 4, 14),
    ("07", 1, None),
    ("08", 3, 15),
)

V143_WATERING_EVENTS = (
    (
        101,
        "2026-05-28T04:00:00+00:00",
        410,
        23.2,
        0.0,
        "home_assistant",
        455,
    ),
    (
        102,
        "2026-05-28T15:30:00+00:00",
        410,
        28.4,
        0.3,
        "automatic",
        448,
    ),
    (
        103,
        "2026-05-29T05:15:00+00:00",
        410,
        19.8,
        2.1,
        "manual",
        452,
    ),
)

V143_REFILL_EVENTS = (
    (
        51,
        "2026-05-27T03:30:00+00:00",
        "2026-05-27",
        12_000,
        11_750,
        523,
        "05:30",
        "home_assistant",
    ),
    (
        52,
        "2026-05-29T19:10:00+00:00",
        "2026-05-29",
        8_000,
        8_000,
        356,
        "21:00",
        "automatic",
    ),
)

V143_CALIBRATION_EVENTS = (
    (
        21,
        "2026-05-18T12:00:00+00:00",
        "main",
        18_100,
        "2026-05-17T12:00:00+00:00",
        20_400,
        5,
        2_050,
        2_300,
        1.121951,
    ),
    (
        22,
        "2026-05-25T12:00:00+00:00",
        "refill",
        31_000,
        "2026-05-24T12:00:00+00:00",
        37_750,
        1,
        7_000,
        6_750,
        0.964286,
    ),
)

V143_TANK_FILL_EVENTS = (
    (
        31,
        "2026-05-20T17:00:00+00:00",
        "main",
        4_200,
        38_000,
        45_000,
        "manual",
    ),
    (
        32,
        "2026-05-26T17:00:00+00:00",
        "refill",
        9_500,
        55_000,
        55_000,
        "manual",
    ),
)

V143_SETTINGS = {
    "automation_pause_until": "2026-06-02T07:30:00+02:00",
    "main_pump_calibration_factor": "1.121951",
    "refill_automation_enabled": "true",
    "refill_cooldown_minutes_per_liter": "24",
    "refill_schedule_times": '["05:30","13:15","21:00"]',
    "watering_amount_calibration_basis": "0.2",
    "watering_amount_percent": "137.5",
}

V143_WALLS = (
    ("north", 0.45),
    ("east", 1.1),
    ("south", 1.35),
    ("west", 0.25),
)

PRESERVED_SETTING_KEYS = tuple(
    key
    for key in V143_SETTINGS
    if key != "watering_amount_percent"
)


def create_v143_database(path: Path) -> dict[str, Any]:
    """Create a realistic database from the application schema in v1.4.3."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.executescript(V143_SCHEMA)
        conn.executemany(
            "INSERT INTO plant_catalog VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            V143_CATALOG,
        )
        conn.execute(
            "INSERT INTO balcony_settings VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            V143_BALCONY,
        )
        conn.executemany(
            "INSERT INTO terrace_walls (side, height_m) VALUES (?, ?)",
            V143_WALLS,
        )
        conn.executemany(
            "INSERT INTO pump_outlets (id, name, ml_per_run) VALUES (?, ?, ?)",
            V143_OUTLETS,
        )
        conn.executemany(
            """
            INSERT INTO plants
                (
                    id, catalog_id, custom_name, size, pot_liters, pot_type,
                    outlet_id, pos_x, pos_y, hose_numbers,
                    target_ml_per_cycle, created_at
                )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            V143_PLANTS,
        )
        conn.executemany(
            "INSERT INTO irrigation_hoses (number, outlet_id, plant_id) VALUES (?, ?, ?)",
            V143_HOSES,
        )
        conn.executemany(
            """
            INSERT INTO watering_events
                (
                    id, ran_at, delivered_ml, temperature_c, rain_mm,
                    source, actual_consumed_ml
                )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            V143_WATERING_EVENTS,
        )
        conn.executemany(
            """
            INSERT INTO refill_events
                (
                    id, ran_at, target_date, requested_ml, transferred_ml,
                    duration_seconds, window_label, source
                )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            V143_REFILL_EVENTS,
        )
        conn.executemany(
            """
            INSERT INTO pump_calibration_events
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            V143_CALIBRATION_EVENTS,
        )
        conn.executemany(
            "INSERT INTO tank_fill_events VALUES (?, ?, ?, ?, ?, ?, ?)",
            V143_TANK_FILL_EVENTS,
        )
        conn.executemany(
            "INSERT INTO app_settings (key, value) VALUES (?, ?)",
            sorted(V143_SETTINGS.items()),
        )
        conn.commit()
        return domain_snapshot(conn)


def domain_snapshot(conn: sqlite3.Connection) -> dict[str, Any]:
    """Return user-owned v1.4.3 values in a migration-stable representation."""

    def rows(query: str) -> list[list[Any]]:
        return [list(row) for row in conn.execute(query).fetchall()]

    settings = dict(
        conn.execute(
            (
                "SELECT key, value FROM app_settings "
                f"WHERE key IN ({','.join('?' for _ in PRESERVED_SETTING_KEYS)}) "
                "ORDER BY key"
            ),
            PRESERVED_SETTING_KEYS,
        ).fetchall()
    )
    amount_row = conn.execute(
        "SELECT value FROM app_settings WHERE key = 'watering_amount_percent'"
    ).fetchone()
    basis_row = conn.execute(
        "SELECT value FROM app_settings WHERE key = 'watering_amount_calibration_basis'"
    ).fetchone()
    watering_amount = float(amount_row[0]) if amount_row else 100.0
    calibration_basis = (
        float(basis_row[0])
        if basis_row
        else V143_WATER_MODEL_CALIBRATION
    )

    return {
        "balcony": rows(
            """
            SELECT
                id, orientation, orientation_deg, width_m, depth_m, location,
                latitude, longitude, timezone_name, wall_height_m,
                tank_capacity_ml, tank_current_ml, refill_tank_capacity_ml,
                refill_tank_current_ml, refill_pump_ml_per_min, updated_at
            FROM balcony_settings ORDER BY id
            """
        ),
        "walls": rows(
            "SELECT side, height_m FROM terrace_walls ORDER BY side"
        ),
        "outlets": rows(
            "SELECT id, name, ml_per_run FROM pump_outlets ORDER BY id"
        ),
        "plants": rows(
            """
            SELECT
                id, catalog_id, custom_name, size, pot_liters, pot_type,
                outlet_id, pos_x, pos_y, hose_numbers,
                target_ml_per_cycle, created_at
            FROM plants ORDER BY id
            """
        ),
        "hoses": rows(
            """
            SELECT number, outlet_id, plant_id
            FROM irrigation_hoses ORDER BY number
            """
        ),
        "watering_events": rows(
            """
            SELECT
                id, ran_at, delivered_ml, temperature_c, rain_mm,
                source, actual_consumed_ml
            FROM watering_events ORDER BY id
            """
        ),
        "refill_events": rows(
            """
            SELECT
                id, ran_at, target_date, requested_ml, transferred_ml,
                duration_seconds, window_label, source
            FROM refill_events ORDER BY id
            """
        ),
        "calibration_events": rows(
            """
            SELECT
                id, calibrated_at, pump_name, measured_level_ml, baseline_at,
                baseline_level_ml, cycles, nominal_ml, measured_ml, result_value
            FROM pump_calibration_events ORDER BY id
            """
        ),
        "tank_fill_events": rows(
            """
            SELECT
                id, ran_at, tank_name, previous_ml, new_ml,
                capacity_ml, source
            FROM tank_fill_events ORDER BY id
            """
        ),
        "settings": settings,
        # The already-started v1.4.3 application stored basis 0.20. Comparing
        # the product guards against an accidental second legacy conversion.
        "effective_watering_calibration": round(
            watering_amount * calibration_basis / 100.0,
            12,
        ),
    }


def snapshot_json(snapshot: dict[str, Any]) -> str:
    return json.dumps(
        snapshot,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
