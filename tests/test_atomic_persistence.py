from __future__ import annotations

import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from watering_backend.app import Application, ApplicationPaths


ROOT = Path(__file__).parent.parent


class AtomicPersistenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        data_dir = Path(self.temporary_directory.name)
        self.application = Application(
            ApplicationPaths(
                root=ROOT,
                public_dir=ROOT / "public",
                data_dir=data_dir,
                database_path=data_dir / "watering.sqlite3",
                version_path=ROOT / "VERSION",
                updater_token_file=data_dir / ".updater-token",
            ),
            environment={"NOTIFICATION_WORKER_DISABLED": "true"},
        )
        self.application.initialize()

    def tearDown(self) -> None:
        self.application.stop_notification_worker()
        self.temporary_directory.cleanup()

    def _database_snapshot(
        self,
        *,
        ignore_timestamps: bool = False,
    ) -> str:
        tables = (
            "balcony_settings",
            "pump_outlets",
            "terrace_walls",
            "plants",
            "irrigation_hoses",
            "app_settings",
        )
        with self.application.database.connection() as conn:
            snapshot = {}
            for table in tables:
                columns = [
                    row["name"]
                    for row in conn.execute(
                        f"PRAGMA table_info({table})"
                    )
                ]
                ordering = ", ".join(columns)
                snapshot[table] = []
                for row in conn.execute(
                    f"SELECT * FROM {table} ORDER BY {ordering}"
                ):
                    item = dict(row)
                    if ignore_timestamps:
                        for key in tuple(item):
                            if key.endswith("_at"):
                                item.pop(key)
                    snapshot[table].append(item)
        return json.dumps(
            snapshot,
            sort_keys=True,
            separators=(",", ":"),
        )

    def _plant_ids(self) -> tuple[int, int]:
        plants = self.application.get_state()["plants"]
        return int(plants[0]["id"]), int(plants[1]["id"])

    def _settings_payload(self) -> dict:
        state = self.application.get_state()
        balcony = state["balcony"]
        return {
            "orientation_deg": balcony["orientation_deg"],
            "width_m": balcony["width_m"],
            "depth_m": balcony["depth_m"],
            "latitude": balcony["latitude"],
            "longitude": balcony["longitude"],
            "timezone_name": balcony["timezone_name"],
            "tank_capacity_ml": balcony["tank_capacity_ml"],
            "refill_tank_capacity_ml": (
                balcony["refill_tank_capacity_ml"]
            ),
            "refill_pump_ml_per_min": (
                balcony["refill_pump_ml_per_min"]
            ),
            "outlets": [
                {
                    "id": outlet["id"],
                    "name": outlet["name"],
                    "ml_per_run": outlet["ml_per_run"],
                }
                for outlet in state["outlets"]
            ],
            "walls": [
                {
                    "side": wall["side"],
                    "height_m": wall["height_m"],
                }
                for wall in state["walls"]
            ],
            "watering_amount_percent": (
                state["settings"]["watering_amount_percent"]
            ),
            "refill_automation_enabled": (
                state["settings"]["refill_automation_enabled"]
            ),
            "main_pump_calibration_factor": (
                state["settings"]["main_pump_calibration_factor"]
            ),
            "planner_config": deepcopy(state["planner_config"]),
        }

    def _assert_hose_failure_rolls_back(
        self,
        payload: dict,
        expected_message: str,
    ) -> None:
        before = self._database_snapshot()
        with self.assertRaisesRegex(
            (ValueError, KeyError),
            expected_message,
        ):
            self.application.save_hoses(payload)
        self.assertEqual(self._database_snapshot(), before)

    def _assert_settings_failure_rolls_back(
        self,
        payload: dict,
        expected_message: str,
    ) -> None:
        before = self._database_snapshot()
        with self.assertRaisesRegex(
            (ValueError, KeyError),
            expected_message,
        ):
            self.application.save_balcony(payload)
        self.assertEqual(self._database_snapshot(), before)

    def test_hose_snapshot_adds_moves_removes_and_syncs_legacy_fields(
        self,
    ) -> None:
        first_plant, second_plant = self._plant_ids()
        payload = {
            "hoses": [
                {
                    "number": "701",
                    "outlet_id": 1,
                    "plant_id": first_plant,
                },
                {
                    "number": "702",
                    "outlet_id": 2,
                    "plant_id": second_plant,
                },
            ]
        }

        self.application.save_hoses(payload)
        first_snapshot = self._database_snapshot()
        self.application.save_hoses(payload)
        self.assertEqual(self._database_snapshot(), first_snapshot)

        state = self.application.get_state()
        plants = {int(plant["id"]): plant for plant in state["plants"]}
        hoses = {hose["number"]: hose for hose in state["hoses"]}
        self.assertEqual(set(hoses), {"701", "702"})
        self.assertEqual(hoses["701"]["plant_id"], first_plant)
        self.assertEqual(hoses["702"]["plant_id"], second_plant)
        self.assertEqual(plants[first_plant]["hose_numbers"], "701")
        self.assertEqual(plants[second_plant]["hose_numbers"], "702")
        self.assertEqual(
            plants[first_plant]["target_ml_per_cycle"],
            hoses["701"]["ml_per_run"],
        )

        changed = {
            "hoses": [
                {
                    "number": "702",
                    "outlet_id": 3,
                    "plant_id": first_plant,
                },
                {
                    "number": "703",
                    "outlet_id": 1,
                    "plant_id": second_plant,
                },
            ]
        }
        self.application.save_hoses(changed)
        changed_state = self.application.get_state()
        changed_plants = {
            int(plant["id"]): plant
            for plant in changed_state["plants"]
        }
        self.assertEqual(
            {hose["number"] for hose in changed_state["hoses"]},
            {"702", "703"},
        )
        self.assertEqual(
            changed_plants[first_plant]["hose_numbers"],
            "702",
        )
        self.assertEqual(
            changed_plants[second_plant]["hose_numbers"],
            "703",
        )

    def test_hose_validation_failures_leave_complete_snapshot_unchanged(
        self,
    ) -> None:
        first_plant, _second_plant = self._plant_ids()
        valid = {
            "hoses": [
                {
                    "number": "711",
                    "outlet_id": 1,
                    "plant_id": first_plant,
                }
            ]
        }
        self.application.save_hoses(valid)
        self._assert_hose_failure_rolls_back(
            {
                "hoses": [
                    {
                        "number": "711",
                        "outlet_id": 1,
                        "plant_id": 999999,
                    }
                ]
            },
            "Pflanzen-ID",
        )
        self._assert_hose_failure_rolls_back(
            {
                "hoses": [
                    {
                        "number": "711",
                        "outlet_id": 999999,
                        "plant_id": first_plant,
                    }
                ]
            },
            "Ausgang",
        )
        self._assert_hose_failure_rolls_back(
            {
                "hoses": [
                    {"number": "711", "outlet_id": 1},
                    {"number": "711", "outlet_id": 1},
                ]
            },
            "doppelt",
        )
        self._assert_hose_failure_rolls_back(
            {
                "hoses": [
                    {
                        "number": str(800 + index),
                        "outlet_id": 1,
                        "plant_id": None,
                    }
                    for index in range(13)
                ]
            },
            "Limit",
        )

    def test_complete_settings_payload_is_atomic_and_idempotent(
        self,
    ) -> None:
        payload = self._settings_payload()
        payload.update(
            {
                "orientation_deg": 137,
                "width_m": 6.5,
                "depth_m": 2.1,
                "tank_capacity_ml": 8_000,
                "refill_tank_capacity_ml": 42_000,
                "refill_pump_ml_per_min": 1_250,
                "watering_amount_percent": 118,
                "water_model_calibration_percent": 13.5,
                "refill_automation_enabled": False,
                "main_pump_calibration_factor": 1.2,
            }
        )
        payload["outlets"][0]["name"] = "Klein angepasst"
        payload["walls"][0]["height_m"] = 1.75
        payload["planner_config"]["weather_cache_minutes"] = 20

        self.application.save_balcony(payload)
        first_snapshot = self._database_snapshot(
            ignore_timestamps=True
        )
        self.application.save_balcony(payload)
        self.assertEqual(
            self._database_snapshot(ignore_timestamps=True),
            first_snapshot,
        )

        state = self.application.get_state()
        self.assertEqual(state["balcony"]["orientation_deg"], 137)
        self.assertEqual(state["balcony"]["tank_capacity_ml"], 8_000)
        self.assertLessEqual(state["balcony"]["tank_current_ml"], 8_000)
        self.assertEqual(
            state["balcony"]["refill_tank_capacity_ml"],
            42_000,
        )
        self.assertEqual(state["outlets"][0]["name"], "Klein angepasst")
        self.assertEqual(state["walls"][0]["height_m"], 1.75)
        self.assertEqual(
            state["planner_config"]["weather_cache_minutes"],
            20,
        )
        with self.application.database.connection() as conn:
            stored = {
                row["key"]: row["value"]
                for row in conn.execute(
                    """
                    SELECT key, value
                    FROM app_settings
                    WHERE key IN (
                        'watering_amount_percent',
                        'water_model_calibration'
                    )
                    """
                )
            }
        self.assertEqual(float(stored["watering_amount_percent"]), 118)
        self.assertAlmostEqual(
            float(stored["water_model_calibration"]),
            0.135,
        )

    def test_all_settings_validation_failures_roll_back_every_table(
        self,
    ) -> None:
        cases: list[tuple[str, object, str]] = [
            (
                "planner_config",
                {"weather_cache_minutes": -1},
                "weather_cache_minutes",
            ),
            ("timezone_name", "Mars/Olympus", "Zeitzone"),
            (
                "outlets",
                [{"id": 999999, "name": "Falsch", "ml_per_run": 10}],
                "Ausgang",
            ),
            (
                "walls",
                [
                    {"side": "north", "height_m": 1},
                    {"side": "north", "height_m": 2},
                ],
                "doppelt",
            ),
        ]
        for field, value, message in cases:
            with self.subTest(field=field):
                payload = self._settings_payload()
                if field == "planner_config":
                    payload[field].update(value)
                else:
                    payload[field] = value
                self._assert_settings_failure_rolls_back(
                    payload,
                    message,
                )

    def test_setting_write_failure_rolls_back_balcony_outlets_and_walls(
        self,
    ) -> None:
        payload = self._settings_payload()
        payload["width_m"] = 9.9
        payload["outlets"][0]["name"] = "Darf nicht bleiben"
        payload["walls"][0]["height_m"] = 9.0
        before = self._database_snapshot()

        with patch.object(
            self.application.settings,
            "save_planner_config",
            side_effect=RuntimeError("simulierter Setting-Fehler"),
        ):
            with self.assertRaisesRegex(RuntimeError, "Setting-Fehler"):
                self.application.save_balcony(payload)

        self.assertEqual(self._database_snapshot(), before)


if __name__ == "__main__":
    unittest.main()
