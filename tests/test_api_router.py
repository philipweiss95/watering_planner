from __future__ import annotations

import copy
import unittest
from http import HTTPStatus

from watering_backend.api.router import ApiRequest, build_router


class FakeApiContext:
    app_version = "9.9.9-test"

    def __init__(self):
        self.state = {
            "balcony": {"timezone_name": "Europe/Berlin"},
            "plants": [],
        }
        self.calls: list[tuple] = []
        self.fail_add = False

    def get_state(self) -> dict:
        self.calls.append(("get_state",))
        return copy.deepcopy(self.state)

    def add_plant(self, payload: dict) -> int:
        self.calls.append(("add_plant", copy.deepcopy(payload)))
        if self.fail_add:
            raise ValueError("catalog_id unbekannt")
        plant_id = 7
        self.state["plants"].append({"id": plant_id, **payload})
        return plant_id

    def update_plant(self, plant_id: int, payload: dict) -> None:
        self.calls.append(("update_plant", plant_id, copy.deepcopy(payload)))
        plant = next(item for item in self.state["plants"] if item["id"] == plant_id)
        plant.update(payload)

    def update_plant_position(self, plant_id: int, payload: dict) -> None:
        self.calls.append(
            ("update_plant_position", plant_id, copy.deepcopy(payload))
        )
        self.update_plant(plant_id, payload)

    def delete_plant(self, plant_id: int) -> None:
        self.calls.append(("delete_plant", plant_id))
        self.state["plants"] = [
            item for item in self.state["plants"] if item["id"] != plant_id
        ]


class ApiRouterTests(unittest.TestCase):
    def setUp(self):
        self.router = build_router()
        self.context = FakeApiContext()

    def test_health_and_state_routes_use_context_without_reading_json(self):
        reads = 0

        def load_json():
            nonlocal reads
            reads += 1
            raise AssertionError("GET route must not read a JSON body")

        health = self.router.dispatch(
            ApiRequest("GET", "/api/health", json_loader=load_json),
            self.context,
        )
        state = self.router.dispatch(
            ApiRequest("GET", "/api/state", json_loader=load_json),
            self.context,
        )

        self.assertEqual(
            health.payload,
            {"ok": True, "version": "9.9.9-test"},
        )
        self.assertEqual(health.status, HTTPStatus.OK)
        self.assertEqual(state.payload, self.context.state)
        self.assertEqual(state.status, HTTPStatus.OK)
        self.assertEqual(reads, 0)

    def test_plant_create_update_position_and_delete_routes(self):
        create_payload = {
            "catalog_id": "olive",
            "custom_name": "Terrassenolive",
        }
        created = self.router.dispatch(
            ApiRequest(
                "POST",
                "/api/plants",
                json_loader=lambda: create_payload,
            ),
            self.context,
        )
        updated = self.router.dispatch(
            ApiRequest(
                "PUT",
                "/api/plants/7",
                json_loader=lambda: {
                    "catalog_id": "olive",
                    "custom_name": "Olive bearbeitet",
                },
            ),
            self.context,
        )
        positioned = self.router.dispatch(
            ApiRequest(
                "POST",
                "/api/plants/7/position",
                json_loader=lambda: {"pos_x": 0.25, "pos_y": 0.75},
            ),
            self.context,
        )
        deleted = self.router.dispatch(
            ApiRequest(
                "DELETE",
                "/api/plants/7",
                json_loader=lambda: self.fail("DELETE read a body"),
            ),
            self.context,
        )

        self.assertEqual(created.status, HTTPStatus.CREATED)
        self.assertEqual(created.payload["id"], 7)
        self.assertEqual(updated.status, HTTPStatus.OK)
        self.assertEqual(
            positioned.payload["plants"][0],
            {
                "id": 7,
                "catalog_id": "olive",
                "custom_name": "Olive bearbeitet",
                "pos_x": 0.25,
                "pos_y": 0.75,
            },
        )
        self.assertEqual(deleted.status, HTTPStatus.OK)
        self.assertEqual(deleted.payload["plants"], [])
        self.assertIn(("delete_plant", 7), self.context.calls)

    def test_request_json_is_loaded_lazily_and_only_once(self):
        reads = 0
        payload = {"catalog_id": "olive"}

        def load_json():
            nonlocal reads
            reads += 1
            return payload

        request = ApiRequest(
            "POST",
            "/api/plants",
            json_loader=load_json,
        )
        self.assertEqual(reads, 0)
        self.assertIs(request.json(), payload)
        self.assertIs(request.json(), payload)
        self.assertEqual(reads, 1)

        response = self.router.dispatch(request, self.context)
        self.assertEqual(response.status, HTTPStatus.CREATED)
        self.assertEqual(reads, 1)

    def test_unknown_route_returns_none_without_reading_body(self):
        response = self.router.dispatch(
            ApiRequest(
                "POST",
                "/api/not-a-route",
                json_loader=lambda: self.fail("Unknown route read a body"),
            ),
            self.context,
        )
        self.assertIsNone(response)

    def test_client_error_is_returned_with_bad_request_status(self):
        self.context.fail_add = True
        response = self.router.dispatch(
            ApiRequest(
                "POST",
                "/api/plants",
                json_loader=lambda: {"catalog_id": "missing"},
            ),
            self.context,
        )

        self.assertEqual(response.status, HTTPStatus.BAD_REQUEST)
        self.assertEqual(response.payload, {"error": "catalog_id unbekannt"})


if __name__ == "__main__":
    unittest.main()
