from __future__ import annotations

import sqlite3
import unittest

import server
from backend_support import TemporaryBackend
from watering_backend.schema import SCHEMA_VERSION


# Frozen from the tables created by the published v1.4.2 server. Keeping the
# fixture as SQL makes schema drift reviewable without requiring an old image.
V142_SCHEMA = """
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


V142_DATA = """
INSERT INTO plant_catalog VALUES
    ('olive', 'Olive', 'Mediterran', 34, 1.08, 0.72, 0.65, 0.28, 25, 0.75, 'Altbestand');
INSERT INTO balcony_settings VALUES
    (1, 'south', 180, 4.2, 1.6, 'Altbalkon', 52.5, 13.4,
     'Europe/Berlin', 1.1, 30000, 12345, 42000, 23456, 850,
     '2026-06-01T10:00:00+00:00');
INSERT INTO terrace_walls VALUES ('left', 1.1), ('back', 0.8);
INSERT INTO pump_outlets VALUES (1, 'S', 15), (2, 'M', 30), (3, 'L', 60);
INSERT INTO plants
    (id, catalog_id, custom_name, size, pot_liters, pot_type, outlet_id,
     pos_x, pos_y, hose_numbers, target_ml_per_cycle, created_at)
VALUES
    (42, 'olive', 'Olive Bestand', 'large', 31, 'overflow', 2,
     0.25, 0.75, '9, 10', 60, '2026-05-01T08:00:00+00:00');
INSERT INTO irrigation_hoses VALUES ('9', 2, 42), ('10', 2, 42);
INSERT INTO watering_events
    (id, ran_at, delivered_ml, temperature_c, rain_mm, source, actual_consumed_ml)
VALUES
    (7, '2026-06-02T06:00:00+00:00', 60, 24, 0, 'home_assistant', 72);
INSERT INTO pump_calibration_events VALUES
    (3, '2026-05-20T10:00:00+00:00', 'main', 20000,
     '2026-05-19T10:00:00+00:00', 25000, 2, 120, 5000, 1.15);
INSERT INTO refill_events VALUES
    (5, '2026-06-02T04:00:00+00:00', '2026-06-02',
     5000, 5000, 353, '06:00', 'home_assistant');
INSERT INTO tank_fill_events VALUES
    (2, '2026-05-30T10:00:00+00:00', 'refill', 10000, 42000, 42000, 'manual');
INSERT INTO app_settings VALUES
    ('legacy_preserved_key', 'bleibt-erhalten'),
    ('refill_schedule_times', '["01:00","06:00"]'),
    ('watering_amount_percent', '125');
"""


class Migration142Tests(unittest.TestCase):
    def test_realistic_v142_database_migrates_twice_without_data_loss(self):
        with TemporaryBackend(initialize=False) as backend:
            conn = sqlite3.connect(backend.db_path)
            try:
                conn.executescript(V142_SCHEMA)
                conn.executescript(V142_DATA)
                conn.commit()
            finally:
                conn.close()

            server.init_db()
            first_state = server.get_state()
            server.init_db()
            second_state = server.get_state()

            with server.connect() as conn:
                version = conn.execute("PRAGMA user_version").fetchone()[0]
                watering_columns = {
                    row["name"] for row in conn.execute("PRAGMA table_info(watering_events)")
                }
                refill_columns = {
                    row["name"] for row in conn.execute("PRAGMA table_info(refill_events)")
                }
                notification_columns = {
                    row["name"] for row in conn.execute("PRAGMA table_info(notification_state)")
                }
                preserved_setting = conn.execute(
                    "SELECT value FROM app_settings WHERE key = 'legacy_preserved_key'"
                ).fetchone()["value"]
                counts = {
                    table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                    for table in (
                        "plants",
                        "irrigation_hoses",
                        "watering_events",
                        "pump_calibration_events",
                        "refill_events",
                        "tank_fill_events",
                    )
                }
                old_watering = dict(
                    conn.execute("SELECT * FROM watering_events WHERE id = 7").fetchone()
                )
                old_refill = dict(
                    conn.execute("SELECT * FROM refill_events WHERE id = 5").fetchone()
                )
                foreign_keys = conn.execute("PRAGMA foreign_keys").fetchone()[0]

            self.assertEqual(version, SCHEMA_VERSION)
            self.assertIn("run_id", watering_columns)
            self.assertIn("run_id", refill_columns)
            self.assertTrue(
                {"last_attempt_at", "last_error", "consecutive_failures"}
                <= notification_columns
            )
            self.assertEqual(preserved_setting, "bleibt-erhalten")
            self.assertEqual(
                counts,
                {
                    "plants": 1,
                    "irrigation_hoses": 2,
                    "watering_events": 1,
                    "pump_calibration_events": 1,
                    "refill_events": 1,
                    "tank_fill_events": 1,
                },
            )
            self.assertEqual(old_watering["actual_consumed_ml"], 72)
            self.assertIsNone(old_watering["run_id"])
            self.assertEqual(old_refill["transferred_ml"], 5000)
            self.assertIsNone(old_refill["run_id"])
            self.assertEqual(foreign_keys, 1)

            first_plant = next(item for item in first_state["plants"] if item["id"] == 42)
            second_plant = next(item for item in second_state["plants"] if item["id"] == 42)
            self.assertEqual(first_plant["catalog_id"], "olive")
            self.assertEqual(first_plant["custom_name"], "Olive Bestand")
            self.assertEqual(first_plant["hose_numbers"], "9, 10")
            self.assertEqual(second_plant, first_plant)
            self.assertEqual(first_state["balcony"]["tank_current_ml"], 12345)
            self.assertEqual(second_state["balcony"]["refill_tank_current_ml"], 23456)


if __name__ == "__main__":
    unittest.main()
