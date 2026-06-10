import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import server


class WateringPlannerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.original_data_dir = server.DATA_DIR
        self.original_db_path = server.DB_PATH
        server.DATA_DIR = Path(self.tmp.name)
        server.DB_PATH = Path(self.tmp.name) / "watering.sqlite3"
        server.init_db()

    def tearDown(self):
        server.DATA_DIR = self.original_data_dir
        server.DB_PATH = self.original_db_path
        self.tmp.cleanup()

    def test_homekit_evaluation_contains_remaining_cycles(self):
        result = server.evaluate(temperature_c=26, rain_mm=0.5, wind_kmh=8, slot="morning", sunshine_hours=7)

        self.assertTrue(result["should_run"])
        self.assertGreater(result["recommended_cycles_today"], 0)
        self.assertEqual(result["cycles_completed_today"], 0)
        self.assertEqual(result["remaining_cycles_today"], result["recommended_cycles_today"])
        self.assertGreater(result["pump"]["delivered_per_cycle_ml"], 0)
        self.assertIn("sun_hours", result["sun"])
        self.assertIn("routing_plan", result)
        self.assertEqual(len(result["routing_plan"]["assignments"]), len(result["plants"]))
        self.assertIn("automation", result)
        self.assertIn("run_now", result)
        self.assertIn("next_window", result["automation"])
        self.assertIn("water_model", result["plants"][0])
        self.assertGreater(result["plants"][0]["water_model"]["canopy_area_m2"], 0)
        self.assertEqual(result["connection_plan"]["design_basis"]["cycles"], 4)
        self.assertEqual(result["plants"][0]["water_model"]["calibration_factor"], server.WATER_MODEL_CALIBRATION)
        self.assertIn("seasonal_factor", result["plants"][0]["water_model"])
        self.assertLessEqual(result["plants"][0]["water_model"]["seasonal_factor"], 1.05)
        self.assertLess(
            result["plants"][0]["water_model"]["calibrated_daily_need_ml"],
            result["plants"][0]["water_model"]["raw_daily_need_ml"],
        )
        self.assertLessEqual(result["recommended_cycles_today"], 10)

    def test_weather_adjusted_remaining_cycles_are_not_blocked_by_rain_threshold(self):
        with patch(
            "server.local_now",
            return_value=datetime(2026, 5, 31, 19, 0, tzinfo=ZoneInfo("Europe/Berlin")),
        ):
            result = server.evaluate(temperature_c=26, rain_mm=3.5, wind_kmh=8, sunshine_hours=7)

        self.assertGreater(result["remaining_cycles_today"], 0)
        self.assertGreaterEqual(result["inputs"]["rain_mm"], result["thresholds"]["rain_mm"])
        self.assertTrue(result["should_run"])
        self.assertTrue(result["run_now"])
        self.assertIn("nach Wetteranrechnung", result["reason"])

    def test_mark_run_counts_cycle_and_reduces_tank(self):
        before = server.evaluate(temperature_c=26, rain_mm=0.5)
        server.mark_run(
            delivered_ml=before["pump"]["delivered_per_cycle_ml"],
            temperature_c=26,
            rain_mm=0.5,
        )

        after = server.evaluate(temperature_c=26, rain_mm=0.5)

        self.assertEqual(after["cycles_completed_today"], 1)
        self.assertEqual(after["remaining_cycles_today"], before["recommended_cycles_today"] - 1)
        self.assertEqual(
            after["tank"]["current_ml"],
            before["tank"]["current_ml"] - before["pump"]["delivered_per_cycle_ml"],
        )

    def test_completed_cycles_today_uses_balcony_timezone(self):
        now = datetime(2026, 6, 3, 0, 30, tzinfo=ZoneInfo("Europe/Berlin"))
        with server.connect() as conn:
            conn.execute(
                """
                INSERT INTO watering_events (ran_at, delivered_ml, temperature_c, rain_mm, source)
                VALUES (?, ?, ?, ?, ?), (?, ?, ?, ?, ?)
                """,
                (
                    "2026-06-02T21:59:00+00:00",
                    100,
                    20,
                    0,
                    "test",
                    "2026-06-02T22:10:00+00:00",
                    100,
                    20,
                    0,
                    "test",
                ),
            )

        with patch("server.local_now", return_value=now):
            completed = server.completed_cycles_today("Europe/Berlin")

        self.assertEqual(completed, 1)

    def test_water_model_calibration_defaults_to_eight_percent(self):
        state = server.get_state()
        result = server.evaluate(temperature_c=26, rain_mm=0, wind_kmh=8, sunshine_hours=7)

        self.assertEqual(state["settings"]["watering_amount_percent"], 100)
        self.assertEqual(result["plants"][0]["water_model"]["calibration_factor"], 0.08)

    def test_new_standard_increases_sunny_day_need_by_a_third(self):
        server.save_watering_amount_percent(100 * server.LEGACY_WATER_MODEL_CALIBRATION / server.WATER_MODEL_CALIBRATION)
        legacy = server.evaluate(temperature_c=25, rain_mm=0, wind_kmh=0, sunshine_hours=7)
        server.delete_setting("watering_amount_percent")
        adjusted = server.evaluate(temperature_c=25, rain_mm=0, wind_kmh=0, sunshine_hours=7)

        legacy_need = sum(plant["daily_need_ml"] for plant in legacy["plants"])
        adjusted_need = sum(plant["daily_need_ml"] for plant in adjusted["plants"])
        self.assertAlmostEqual(adjusted_need / legacy_need, 4 / 3, delta=0.05)

    def test_legacy_saved_default_moves_to_new_standard(self):
        server.set_setting("water_model_calibration", "0.04")

        self.assertEqual(server.water_model_calibration(), 0.08)
        self.assertEqual(server.watering_amount_percent(), 100)

    def test_legacy_high_value_becomes_new_standard_without_changing_amount(self):
        server.set_setting("water_model_calibration", "0.06")

        self.assertEqual(server.water_model_calibration(), 0.08)
        self.assertEqual(server.watering_amount_percent(), 100)

    def test_balcony_save_persists_water_model_calibration(self):
        state = server.get_state()
        payload = {
            **state["balcony"],
            "outlets": state["outlets"],
            "walls": state["walls"],
            "watering_amount_percent": 125,
        }
        before = server.evaluate(temperature_c=26, rain_mm=0, wind_kmh=8, sunshine_hours=7)

        server.save_balcony(payload)

        after = server.evaluate(temperature_c=26, rain_mm=0, wind_kmh=8, sunshine_hours=7)
        self.assertEqual(server.get_state()["settings"]["watering_amount_percent"], 125)
        self.assertEqual(after["plants"][0]["water_model"]["calibration_factor"], 0.1)
        self.assertGreater(
            sum(plant["need_ml"] for plant in after["plants"]),
            sum(plant["need_ml"] for plant in before["plants"]),
        )

    def test_watering_amount_rejects_extreme_values(self):
        with self.assertRaises(ValueError):
            server.save_watering_amount_percent(510)

    def test_watering_events_are_listed_newest_first(self):
        server.mark_run(delivered_ml=120, temperature_c=24, rain_mm=0.2)
        server.mark_run(delivered_ml=90, temperature_c=25, rain_mm=0)

        events = server.watering_events()

        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]["delivered_ml"], 90)
        self.assertEqual(events[0]["source"], "homekit")
        self.assertIn("ran_at", events[0])

    def test_manual_weather_payload_does_not_fetch_network(self):
        weather = server.weather_from_payload(
            {"temperature_c": 24, "rain_mm": 1.2, "wind_kmh": 5, "sunshine_hours": 6}
        )
        result = server.evaluate_weather(weather)

        self.assertEqual(result["weather"]["source"], "manual")
        self.assertEqual(result["inputs"]["weather_source"], "manual")

    def test_shortcut_blueprint_contains_check_and_mark_urls(self):
        blueprint = server.shortcut_blueprint("https://example.test")

        self.assertIn("/api/homekit/check?auto=true", blueprint["check_url"])
        self.assertEqual(blueprint["mark_run_url"], "https://example.test/api/homekit/mark-run")
        self.assertEqual(blueprint["manual_run_url"], "https://example.test/api/manual-run")

    def test_manual_run_is_disabled_without_home_assistant_webhook(self):
        with patch.dict("os.environ", {"HOME_ASSISTANT_WEBHOOK_URL": ""}):
            result = server.evaluate(temperature_c=26, rain_mm=0, wind_kmh=8, sunshine_hours=7)

        self.assertFalse(result["manual_run"]["available"])
        self.assertIn("Webhook", result["manual_run"]["reason"])

    def test_manual_run_posts_configured_home_assistant_webhook(self):
        webhook_url = "http://home-assistant.test:8123/api/webhook/watering_planner_manual_run"
        response = MagicMock()
        response.__enter__.return_value = response
        with patch.dict("os.environ", {"HOME_ASSISTANT_WEBHOOK_URL": webhook_url}):
            result = server.evaluate(temperature_c=26, rain_mm=0, wind_kmh=8, sunshine_hours=7)
            with patch("server.urlopen", return_value=response) as mock_urlopen:
                server.trigger_home_assistant_manual_run(result)

        request = mock_urlopen.call_args.args[0]
        self.assertTrue(result["manual_run"]["available"])
        self.assertEqual(request.full_url, webhook_url)
        self.assertEqual(request.method, "POST")
        response.read.assert_called_once()

    def test_manual_run_ignores_automation_cooldown(self):
        webhook_url = "http://home-assistant.test:8123/api/webhook/watering_planner_manual_run"
        now = datetime(2026, 6, 2, 10, 0, tzinfo=ZoneInfo("Europe/Berlin"))
        with patch.dict("os.environ", {"HOME_ASSISTANT_WEBHOOK_URL": webhook_url}):
            with patch("server.local_now", return_value=now):
                server.mark_run(delivered_ml=120, temperature_c=26, rain_mm=0)
                result = server.evaluate(temperature_c=26, rain_mm=0, wind_kmh=8, sunshine_hours=7)

        self.assertTrue(result["automation"]["cooldown_active"])
        self.assertTrue(result["manual_run"]["available"])

    def test_add_plant_does_not_require_manual_outlet(self):
        plant_id = server.add_plant(
            {
                "catalog_id": "basil",
                "custom_name": "Basilikum",
                "size": "small",
                "pot_liters": 8,
                "pot_type": "overflow",
            }
        )
        state = server.get_state()
        plant = next(item for item in state["plants"] if item["id"] == plant_id)

        self.assertEqual(plant["outlet_name"], "S")
        self.assertEqual(plant["configured_ml_per_cycle"], 0)

    def test_add_plant_calculates_water_amount_from_hoses_and_outlet(self):
        server.save_hoses(
            {
                "hoses": [
                    {"number": "6", "outlet_id": 3},
                    {"number": "7", "outlet_id": 3},
                ]
            }
        )
        plant_id = server.add_plant(
            {
                "catalog_id": "olive",
                "custom_name": "Olive zwei Schläuche",
                "size": "large",
                "pot_liters": 45,
                "pot_type": "overflow",
                "hose_numbers": "6, 7, 7",
                "target_ml_per_cycle": 999,
            }
        )

        plant = next(item for item in server.get_state()["plants"] if item["id"] == plant_id)

        self.assertEqual(plant["hose_numbers"], "6, 7")
        self.assertEqual(plant["hose_count"], 2)
        self.assertEqual(plant["target_ml_per_cycle"], 120)
        self.assertEqual(plant["configured_ml_per_cycle"], 120)

    def test_update_plant_edits_inventory_fields(self):
        server.save_hoses({"hoses": [{"number": "14", "outlet_id": 2}]})
        plant_id = server.add_plant(
            {
                "catalog_id": "basil",
                "custom_name": "Basilikum",
                "size": "small",
                "pot_liters": 8,
                "pot_type": "overflow",
            }
        )

        server.update_plant(
            plant_id,
            {
                "catalog_id": "rosemary",
                "custom_name": "Rosmarin rechts",
                "size": "medium",
                "pot_liters": 12,
                "pot_type": "reservoir_overflow",
                "hose_numbers": "14",
                "target_ml_per_cycle": 999,
            },
        )

        plant = next(item for item in server.get_state()["plants"] if item["id"] == plant_id)

        self.assertEqual(plant["catalog_id"], "rosemary")
        self.assertEqual(plant["custom_name"], "Rosmarin rechts")
        self.assertEqual(plant["hose_numbers"], "14")
        self.assertEqual(plant["target_ml_per_cycle"], 30)
        self.assertEqual(plant["configured_ml_per_cycle"], 30)
        self.assertEqual(plant["outlet_id"], 2)

    def test_balcony_save_recalculates_water_amount_after_outlet_change(self):
        server.save_hoses(
            {
                "hoses": [
                    {"number": "21", "outlet_id": 1},
                    {"number": "22", "outlet_id": 1},
                ]
            }
        )
        plant_id = server.add_plant(
            {
                "catalog_id": "basil",
                "custom_name": "Basilikum Test",
                "size": "small",
                "pot_liters": 8,
                "pot_type": "overflow",
                "hose_numbers": "21, 22",
            }
        )
        state = server.get_state()
        payload = {
            **state["balcony"],
            "outlets": [
                {**outlet, "ml_per_run": 20 if outlet["id"] == 1 else outlet["ml_per_run"]}
                for outlet in state["outlets"]
            ],
            "walls": state["walls"],
        }

        server.save_balcony(payload)

        plant = next(item for item in server.get_state()["plants"] if item["id"] == plant_id)
        self.assertEqual(plant["target_ml_per_cycle"], 40)
        self.assertEqual(plant["configured_ml_per_cycle"], 40)

    def test_plant_water_amount_combines_hoses_from_different_outputs(self):
        server.save_hoses(
            {
                "hoses": [
                    {"number": "31", "outlet_id": 1},
                    {"number": "32", "outlet_id": 3},
                ]
            }
        )
        plant_id = server.add_plant(
            {
                "catalog_id": "olive",
                "custom_name": "Olive gemischt",
                "size": "large",
                "pot_liters": 45,
                "pot_type": "overflow",
                "hose_numbers": ["31", "32"],
            }
        )

        plant = next(item for item in server.get_state()["plants"] if item["id"] == plant_id)

        self.assertEqual(plant["hose_numbers"], "31, 32")
        self.assertEqual(plant["configured_ml_per_cycle"], 75)
        self.assertIn("31: S (15 ml)", plant["outlet_summary"])
        self.assertIn("32: L (60 ml)", plant["outlet_summary"])

    def test_init_db_migrates_legacy_plant_hoses_once(self):
        with server.connect() as conn:
            plant_id = int(conn.execute("SELECT id FROM plants ORDER BY id LIMIT 1").fetchone()["id"])
            conn.execute("UPDATE plants SET outlet_id = 3, hose_numbers = '41, 42' WHERE id = ?", (plant_id,))
            conn.execute("DELETE FROM irrigation_hoses")

        server.init_db()

        hoses = server.get_state()["hoses"]
        migrated = [hose for hose in hoses if hose["number"] in {"41", "42"}]
        self.assertEqual([hose["number"] for hose in migrated], ["41", "42"])
        self.assertTrue(all(hose["outlet_name"] == "L" for hose in migrated))

    def test_evaluation_uses_configured_hoses_for_pump_delivery(self):
        server.save_hoses(
            {
                "hoses": [
                    {"number": "51", "outlet_id": 1},
                    {"number": "52", "outlet_id": 3},
                ]
            }
        )
        server.add_plant(
            {
                "catalog_id": "olive",
                "custom_name": "Olive reale Menge",
                "size": "large",
                "pot_liters": 45,
                "pot_type": "overflow",
                "hose_numbers": ["51", "52"],
            }
        )

        result = server.evaluate(temperature_c=26, rain_mm=0, wind_kmh=8, sunshine_hours=7)

        self.assertEqual(result["pump"]["delivered_per_cycle_ml"], 75)

    def test_evaluation_without_configured_hoses_does_not_run(self):
        server.save_hoses({"hoses": []})

        result = server.evaluate(temperature_c=26, rain_mm=0, wind_kmh=8, sunshine_hours=7)

        self.assertFalse(result["should_run"])
        self.assertFalse(result["run_now"])
        self.assertEqual(result["pump"]["delivered_per_cycle_ml"], 0)
        self.assertIn("keine nutzbare Verschlauchung", result["reason"])

    def test_automation_waits_after_completed_run(self):
        now = datetime(2026, 6, 2, 10, 0, tzinfo=ZoneInfo("Europe/Berlin"))
        with patch("server.local_now", return_value=now):
            status = server.automation_status(
                should_run=True,
                remaining_cycles=3,
                timezone_name="Europe/Berlin",
                latest_run_value="2026-06-02T07:45:00+00:00",
            )

        self.assertFalse(status["run_now"])
        self.assertTrue(status["cooldown_active"])
        self.assertEqual(status["cooldown_until"], "2026-06-02T10:15:00+02:00")
        self.assertEqual(status["summary"], "Pause nach letztem Lauf bis 10:15.")

    def test_automation_distributes_four_cycles_across_the_day(self):
        checks = [
            (6, 45, 0, False),
            (7, 0, 0, True),
            (10, 0, 1, False),
            (11, 0, 1, True),
            (14, 0, 2, False),
            (15, 0, 2, True),
            (18, 0, 3, False),
            (19, 0, 3, True),
        ]

        for hour, minute, completed, expected_run_now in checks:
            with self.subTest(hour=hour, minute=minute, completed=completed):
                now = datetime(2026, 6, 2, hour, minute, tzinfo=ZoneInfo("Europe/Berlin"))
                with patch("server.local_now", return_value=now):
                    status = server.automation_status(
                        should_run=True,
                        remaining_cycles=4 - completed,
                        timezone_name="Europe/Berlin",
                        recommended_cycles=4,
                        completed_cycles=completed,
                    )

                self.assertEqual(status["windows"], ["07:00", "11:00", "15:00", "19:00"])
                self.assertEqual(status["run_now"], expected_run_now)

    def test_automation_catches_up_a_missed_distributed_cycle(self):
        now = datetime(2026, 6, 2, 12, 0, tzinfo=ZoneInfo("Europe/Berlin"))
        with patch("server.local_now", return_value=now):
            status = server.automation_status(
                should_run=True,
                remaining_cycles=3,
                timezone_name="Europe/Berlin",
                recommended_cycles=4,
                completed_cycles=1,
            )

        self.assertTrue(status["run_now"])
        self.assertTrue(status["catch_up"])
        self.assertEqual(status["next_window"], "15:00")

    def test_catalog_contains_common_balcony_plants(self):
        catalog_ids = {plant["id"] for plant in server.get_state()["catalog"]}

        self.assertIn("petunia", catalog_ids)
        self.assertIn("cucumber", catalog_ids)
        self.assertIn("citrus", catalog_ids)
        self.assertIn("thyme", catalog_ids)
        self.assertIn("zucchini", catalog_ids)
        self.assertIn("carnation", catalog_ids)
        self.assertIn("pine", catalog_ids)

    def test_table_xlsx_is_ignored_when_seeding_inventory(self):
        source = server.ROOT / "data" / "table.xlsx"
        if not source.exists():
            self.skipTest("data/table.xlsx ist nicht vorhanden")

        (server.DATA_DIR / "table.xlsx").write_bytes(source.read_bytes())
        with server.connect() as conn:
            conn.execute("DELETE FROM plants")
        server.init_db()

        state = server.get_state()
        names = {plant["custom_name"] for plant in state["plants"]}

        self.assertEqual(names, {"Olive", "Tomate", "Lavendel"})

    def test_wind_increases_water_need(self):
        calm = server.evaluate(temperature_c=26, rain_mm=0, wind_kmh=2, sunshine_hours=7)
        windy = server.evaluate(temperature_c=26, rain_mm=0, wind_kmh=35, sunshine_hours=7)

        self.assertGreater(windy["inputs"]["wind_factor"], calm["inputs"]["wind_factor"])
        self.assertGreater(
            sum(plant["need_ml"] for plant in windy["plants"]),
            sum(plant["need_ml"] for plant in calm["plants"]),
        )

    def test_weather_changes_cycles_not_fixed_connections(self):
        mild = server.evaluate(temperature_c=20, rain_mm=0, wind_kmh=2, sunshine_hours=4)
        hot = server.evaluate(temperature_c=32, rain_mm=0, wind_kmh=25, sunshine_hours=10)

        mild_connections = {
            assignment["plant_id"]: assignment["tube_label"]
            for assignment in mild["routing_plan"]["assignments"]
        }
        hot_connections = {
            assignment["plant_id"]: assignment["tube_label"]
            for assignment in hot["routing_plan"]["assignments"]
        }

        self.assertEqual(mild_connections, hot_connections)
        self.assertGreaterEqual(hot["recommended_cycles_today"], mild["recommended_cycles_today"])

    def test_connection_plan_flags_wrong_current_outlet_amount(self):
        server.save_hoses({"hoses": [{"number": "101", "outlet_id": 1}]})
        plant_id = server.add_plant(
            {
                "catalog_id": "tomato",
                "custom_name": "Tomate Test",
                "size": "large",
                "pot_liters": 27,
                "pot_type": "overflow",
                "hose_numbers": "101",
            }
        )

        result = server.evaluate(temperature_c=26, rain_mm=0, wind_kmh=8, sunshine_hours=7)
        assignment = next(item for item in result["connection_plan"]["assignments"] if item["plant_id"] == plant_id)

        self.assertIn(assignment["connection_status"], {"change", "urgent"})
        self.assertTrue("Besser" in assignment["connection_note"] or "Unerlässlich" in assignment["connection_note"])

    def test_connection_plan_marks_urgent_under_supply(self):
        server.save_hoses({"hoses": [{"number": "102", "outlet_id": 1}]})
        plant_id = server.add_plant(
            {
                "catalog_id": "tomato",
                "custom_name": "Tomate trocken",
                "size": "large",
                "pot_liters": 27,
                "pot_type": "overflow",
                "hose_numbers": "102",
            }
        )

        result = server.evaluate(temperature_c=26, rain_mm=0, wind_kmh=8, sunshine_hours=7)
        assignment = next(item for item in result["connection_plan"]["assignments"] if item["plant_id"] == plant_id)

        self.assertEqual(assignment["connection_status"], "urgent")
        self.assertIn("Unerlässlich", assignment["connection_note"])

    def test_static_connection_plan_never_adds_more_tubes_than_existing(self):
        result = server.evaluate(temperature_c=26, rain_mm=0, wind_kmh=8, sunshine_hours=7)

        for assignment in result["connection_plan"]["assignments"]:
            plant = next(item for item in result["plants"] if item["id"] == assignment["plant_id"])
            recommended_tubes = sum(tube["count"] for tube in assignment["tubes"])

            self.assertLessEqual(recommended_tubes, plant["current_tube_count"])

    def test_tube_optimizer_can_combine_30_and_15_ml(self):
        outlets = [
            {"id": 1, "name": "S", "ml_per_run": 15},
            {"id": 2, "name": "M", "ml_per_run": 30},
            {"id": 3, "name": "L", "ml_per_run": 60},
        ]
        plants = [
            {
                "id": 100,
                "name": "Testpflanze",
                "catalog_name": "Test",
                "need_ml": 450,
                "pot_liters": 10,
                "pot_type": "overflow",
                "drought_sensitivity": 1,
            }
        ]

        options = server.plant_tube_options(plants[0], outlets, cycles=10)
        assignment = next(option for option in options if option["ml_per_cycle"] == 45)
        assignment = server.normalize_assignment(assignment)

        self.assertEqual(assignment["ml_per_cycle"], 45)
        self.assertEqual(assignment["tube_label"], "1x 15 ml + 1x 30 ml")

    def test_tube_optimizer_respects_12_connections_per_outlet(self):
        outlets = [
            {"id": 1, "name": "S", "ml_per_run": 15},
            {"id": 2, "name": "M", "ml_per_run": 30},
            {"id": 3, "name": "L", "ml_per_run": 60},
        ]
        plants = [
            {
                "id": index,
                "name": f"Pflanze {index}",
                "catalog_name": "Test",
                "need_ml": 450,
                "pot_liters": 10,
                "pot_type": "overflow",
                "drought_sensitivity": 1,
            }
            for index in range(20)
        ]

        plan = server.optimize_routing(plants, outlets, max_cycles=10)

        for outlet in plan["by_outlet"]:
            self.assertLessEqual(outlet["connections_used"], 12)

    def test_position_changes_plant_sun_factor(self):
        north = server.estimate_sun_hours(
            {"latitude": 52.52, "longitude": 13.405, "width_m": 3, "depth_m": 2, "orientation_deg": 180},
            [{"side": "south", "height_m": 2.0}],
            position=(0.5, 0.1),
        )
        south = server.estimate_sun_hours(
            {"latitude": 52.52, "longitude": 13.405, "width_m": 3, "depth_m": 2, "orientation_deg": 180},
            [{"side": "south", "height_m": 2.0}],
            position=(0.5, 0.95),
        )

        self.assertGreaterEqual(north["sun_hours"], south["sun_hours"])


if __name__ == "__main__":
    unittest.main()
