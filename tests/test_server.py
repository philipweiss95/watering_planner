import tempfile
import unittest
from pathlib import Path

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
        self.assertIn("water_model", result["plants"][0])
        self.assertGreater(result["plants"][0]["water_model"]["canopy_area_m2"], 0)

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

    def test_catalog_contains_common_balcony_plants(self):
        catalog_ids = {plant["id"] for plant in server.get_state()["catalog"]}

        self.assertIn("petunia", catalog_ids)
        self.assertIn("cucumber", catalog_ids)
        self.assertIn("citrus", catalog_ids)
        self.assertIn("thyme", catalog_ids)

    def test_wind_increases_water_need(self):
        calm = server.evaluate(temperature_c=26, rain_mm=0, wind_kmh=2, sunshine_hours=7)
        windy = server.evaluate(temperature_c=26, rain_mm=0, wind_kmh=35, sunshine_hours=7)

        self.assertGreater(windy["inputs"]["wind_factor"], calm["inputs"]["wind_factor"])
        self.assertGreater(
            sum(plant["need_ml"] for plant in windy["plants"]),
            sum(plant["need_ml"] for plant in calm["plants"]),
        )

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
