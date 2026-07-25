from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from v143_fixture import (
    V143_SETTINGS,
    create_v143_database,
    domain_snapshot,
    snapshot_json,
)
from watering_backend.app import Application, ApplicationPaths
from watering_backend.schema import SCHEMA_VERSION


class Migration143Tests(unittest.TestCase):
    def test_realistic_v143_database_migrates_twice_without_data_loss(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            data_dir = root / "data"
            database_path = data_dir / "watering.sqlite3"
            before = create_v143_database(database_path)
            before_json = snapshot_json(before)
            application = Application(
                ApplicationPaths(
                    root=root,
                    public_dir=root / "public",
                    data_dir=data_dir,
                    database_path=database_path,
                    version_path=root / "VERSION",
                    updater_token_file=data_dir / ".updater-token",
                ),
                environment={"NOTIFICATION_WORKER_DISABLED": "true"},
            )

            application.initialize()
            with application.database.connection() as conn:
                after_first = domain_snapshot(conn)
                first_schema = self._schema_snapshot(conn)
                self._assert_migrated_schema(conn)

            application.initialize()
            with application.database.connection() as conn:
                after_second = domain_snapshot(conn)
                second_schema = self._schema_snapshot(conn)
                self._assert_migrated_schema(conn)
                migrated_percentage = conn.execute(
                    """
                    SELECT value FROM app_settings
                    WHERE key = 'watering_amount_percent'
                    """
                ).fetchone()["value"]
                calibration_basis = conn.execute(
                    """
                    SELECT value FROM app_settings
                    WHERE key = 'watering_amount_calibration_basis'
                    """
                ).fetchone()["value"]

            self.assertEqual(snapshot_json(after_first), before_json)
            self.assertEqual(snapshot_json(after_second), before_json)
            self.assertEqual(second_schema, first_schema)
            self.assertEqual(float(migrated_percentage), 55.0)
            self.assertEqual(float(calibration_basis), 0.2)

            state = application.get_state()
            plants = {plant["id"]: plant for plant in state["plants"]}
            self.assertEqual(
                [plants[plant_id]["catalog_id"] for plant_id in sorted(plants)],
                ["olive", "tomato", "lavender", "citrus"],
            )
            self.assertEqual(plants[11]["custom_name"], "Olivia")
            self.assertEqual(plants[14]["pot_type"], "reservoir_overflow")
            self.assertEqual(
                state["balcony"]["tank_current_ml"],
                17_321,
            )
            self.assertEqual(
                state["balcony"]["refill_tank_current_ml"],
                41_007,
            )

    def test_fixture_contains_representative_bridge_settings(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            database_path = (
                Path(temporary_directory) / "data" / "watering.sqlite3"
            )
            create_v143_database(database_path)
            with closing(sqlite3.connect(database_path)) as conn:
                actual = dict(
                    conn.execute(
                        "SELECT key, value FROM app_settings ORDER BY key"
                    ).fetchall()
                )
                foreign_key_violations = conn.execute(
                    "PRAGMA foreign_key_check"
                ).fetchall()

            self.assertEqual(actual, V143_SETTINGS)
            self.assertEqual(foreign_key_violations, [])
            self.assertIn("home_assistant_last_success_at", actual)
            self.assertIn("updater_channel", actual)

    def _assert_migrated_schema(self, conn: sqlite3.Connection) -> None:
        self.assertEqual(
            conn.execute("PRAGMA user_version").fetchone()[0],
            SCHEMA_VERSION,
        )
        self.assertEqual(conn.execute("PRAGMA foreign_keys").fetchone()[0], 1)
        self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])

        table_names = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        self.assertTrue(
            {
                "notification_state",
                "notification_log",
                "refill_window_observations",
            }
            <= table_names
        )

        watering_columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(watering_events)")
        }
        refill_columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(refill_events)")
        }
        self.assertIn("run_id", watering_columns)
        self.assertIn("run_id", refill_columns)

        indexes = {
            row["name"]: row["sql"]
            for row in conn.execute(
                """
                SELECT name, sql FROM sqlite_master
                WHERE type = 'index' AND name LIKE 'ux_%_run_id'
                """
            )
        }
        self.assertEqual(
            set(indexes),
            {
                "ux_watering_events_run_id",
                "ux_refill_events_run_id",
            },
        )
        for sql in indexes.values():
            self.assertIn("CREATE UNIQUE INDEX", sql)
            self.assertIn("WHERE run_id IS NOT NULL", sql)

    @staticmethod
    def _schema_snapshot(conn: sqlite3.Connection) -> list[tuple[str, str, str]]:
        return [
            tuple(row)
            for row in conn.execute(
                """
                SELECT type, name, COALESCE(sql, '')
                FROM sqlite_master
                WHERE name NOT LIKE 'sqlite_%'
                ORDER BY type, name
                """
            ).fetchall()
        ]


if __name__ == "__main__":
    unittest.main()
