from __future__ import annotations

import unittest
from typing import get_args

import server
from backend_support import TemporaryBackend
from watering_backend.models import PlantSize
from watering_backend.services.evaluation import canopy_size_factor, size_factor
from watering_backend.validation import PLANT_SIZES, validate_plant_payload


class PlantSizeCompatibilityTests(unittest.TestCase):
    def test_tree_is_a_shared_valid_plant_size(self):
        self.assertIn("tree", get_args(PlantSize))
        self.assertIn("tree", PLANT_SIZES)
        plant = validate_plant_payload(
            {
                "catalog_id": "olive",
                "custom_name": "Alter Olivenbaum",
                "size": "tree",
                "pot_liters": 80,
                "pot_type": "overflow",
            },
            {"olive"},
        )
        self.assertEqual(plant["size"], "tree")
        self.assertEqual(size_factor("tree"), 1.65)
        self.assertEqual(canopy_size_factor("tree"), 2.25)

    def test_tree_survives_create_edit_and_hose_configuration_payload(self):
        with TemporaryBackend():
            plant_id = server.add_plant(
                {
                    "catalog_id": "olive",
                    "custom_name": "Olivenbaum",
                    "size": "tree",
                    "pot_liters": 75,
                    "pot_type": "reservoir_overflow",
                }
            )
            created = next(
                plant
                for plant in server.get_state()["plants"]
                if plant["id"] == plant_id
            )
            self.assertEqual(created["size"], "tree")

            server.save_hoses(
                {
                    "hoses": [
                        {
                            "number": hose["number"],
                            "outlet_id": hose["outlet_id"],
                        }
                        for hose in server.get_state()["hoses"]
                    ]
                    + [{"number": "90", "outlet_id": 2}]
                }
            )
            server.update_plant(
                plant_id,
                {
                    "catalog_id": "olive",
                    "custom_name": "Olivenbaum bearbeitet",
                    "size": "tree",
                    "pot_liters": 80,
                    "pot_type": "overflow",
                    "hose_numbers": "90",
                },
            )
            updated_state = server.get_state()
            updated = next(
                plant
                for plant in updated_state["plants"]
                if plant["id"] == plant_id
            )
            configured_hose = next(
                hose for hose in updated_state["hoses"] if hose["number"] == "90"
            )
            self.assertEqual(updated["custom_name"], "Olivenbaum bearbeitet")
            self.assertEqual(updated["size"], "tree")
            self.assertEqual(updated["hose_numbers"], "90")
            self.assertEqual(configured_hose["plant_id"], plant_id)


if __name__ == "__main__":
    unittest.main()
