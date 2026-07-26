from __future__ import annotations

import json
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from v143_fixture import (
    create_v143_database,
    domain_snapshot,
    snapshot_json,
)
from watering_backend.app import ApplicationPaths, create_application


ROOT = Path(__file__).resolve().parents[1]
FIXED_LOCAL_NOW = datetime(
    2026,
    7,
    25,
    14,
    0,
    tzinfo=ZoneInfo("Europe/Berlin"),
)


class UpgradeRuntime143Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.temporary_directory.name) / "data"
        self.database_path = self.data_dir / "watering.sqlite3"
        self.before = create_v143_database(self.database_path)
        environment = {
            "NOTIFICATION_WORKER_DISABLED": "true",
            "NOTIFICATIONS_ENABLED": "false",
            "HOME_ASSISTANT_WEBHOOK_URL": (
                "http://home-assistant.local:8123/api/webhook/private-watering"
            ),
            "HOME_ASSISTANT_REFILL_WEBHOOK_URL": (
                "http://home-assistant.local:8123/api/webhook/private-refill"
            ),
        }
        self.application = create_application(
            paths=ApplicationPaths(
                root=ROOT,
                public_dir=ROOT / "public",
                data_dir=self.data_dir,
                database_path=self.database_path,
                version_path=ROOT / "VERSION",
                updater_token_file=self.data_dir / ".updater-token",
            ),
            environment=environment,
            local_clock=lambda _timezone_name: FIXED_LOCAL_NOW,
            now_iso=lambda: FIXED_LOCAL_NOW.astimezone(timezone.utc).isoformat(),
        )
        self.application.initialize()
        self.server = ThreadingHTTPServer(
            ("127.0.0.1", 0),
            self.application.handler_class(),
        )
        self.thread = threading.Thread(
            target=self.server.serve_forever,
            daemon=True,
        )
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.application.stop_notification_worker()
        self.temporary_directory.cleanup()

    def request(
        self,
        path: str,
        payload: dict | None = None,
        *,
        method: str | None = None,
    ) -> dict:
        data = (
            json.dumps(payload).encode("utf-8")
            if payload is not None
            else None
        )
        request = Request(
            self.base_url + path,
            data=data,
            headers=(
                {"Content-Type": "application/json"}
                if data is not None
                else {}
            ),
            method=method or ("POST" if data is not None else "GET"),
        )
        with urlopen(request, timeout=10) as response:
            return json.loads(response.read().decode("utf-8"))

    def test_migrated_bridge_installation_supports_complete_api_smoke(self) -> None:
        with self.application.database.connection() as conn:
            self.assertEqual(
                snapshot_json(domain_snapshot(conn)),
                snapshot_json(self.before),
            )

        health = self.request("/api/health")
        state = self.request("/api/state")
        self.assertTrue(health["ok"])
        self.assertEqual(health["version"], "1.5.0")
        self.assertEqual(state["version"], "1.5.0")
        self.assertEqual(
            [
                plant["catalog_id"]
                for plant in sorted(
                    state["plants"],
                    key=lambda plant: plant["id"],
                )
            ],
            ["olive", "tomato", "lavender", "citrus", "olive"],
        )
        legacy_tree = next(
            plant for plant in state["plants"] if plant["id"] == 15
        )
        self.assertEqual(legacy_tree["size"], "tree")
        self.assertEqual(state["balcony"]["tank_capacity_ml"], 45_000)
        self.assertEqual(state["balcony"]["refill_tank_capacity_ml"], 55_000)
        self.assertTrue(state["home_assistant"]["configured"])
        self.assertNotIn("private-watering", json.dumps(state))
        self.assertNotIn("private-refill", json.dumps(state))

        simulation = {
            "temperature_c": 29,
            "rain_mm": 0.4,
            "wind_kmh": 12,
            "sunshine_hours": 8,
            "et0_mm": 5.1,
        }
        evaluation = self.request("/api/evaluate", simulation)
        self.assertTrue(evaluation["weather"]["simulation"])
        self.assertGreaterEqual(
            len(evaluation["depletion"]["forecast_events"]),
            1,
        )
        self.assertIn(
            "last_supported_watering_at",
            evaluation["depletion"],
        )

        hoses = [
            {
                "number": hose["number"],
                "outlet_id": (
                    2 if hose["number"] == "07" else hose["outlet_id"]
                ),
            }
            for hose in state["hoses"]
        ]
        hose_state = self.request("/api/hoses", {"hoses": hoses})
        reserve = next(
            hose
            for hose in hose_state["hoses"]
            if hose["number"] == "07"
        )
        self.assertEqual(reserve["outlet_id"], 2)

        plant_payload = {
            "catalog_id": "olive",
            "custom_name": "Olive nach Update",
            "size": "medium",
            "pot_liters": 24,
            "pot_type": "overflow",
            "hose_numbers": "07",
            "pos_x": 0.44,
            "pos_y": 0.66,
        }
        created = self.request("/api/plants", plant_payload)
        plant_id = int(created["id"])
        created_plant = next(
            plant for plant in created["plants"] if plant["id"] == plant_id
        )
        self.assertEqual(created_plant["catalog_id"], "olive")

        updated = self.request(
            f"/api/plants/{plant_id}",
            {
                **plant_payload,
                "catalog_id": "lavender",
                "custom_name": "Lavendel nach Update",
            },
            method="PUT",
        )
        updated_plant = next(
            plant for plant in updated["plants"] if plant["id"] == plant_id
        )
        self.assertEqual(updated_plant["catalog_id"], "lavender")
        deleted = self.request(
            f"/api/plants/{plant_id}",
            method="DELETE",
        )
        self.assertNotIn(plant_id, {plant["id"] for plant in deleted["plants"]})

        before_watering = self.request("/api/state")["balcony"]
        watering_payload = {
            **simulation,
            "run_id": "upgrade-143-watering",
            "source": "upgrade_test",
        }
        first_watering = self.request(
            "/api/homekit/mark-run",
            watering_payload,
        )
        second_watering = self.request(
            "/api/homekit/mark-run",
            watering_payload,
        )
        after_watering = self.request("/api/state")["balcony"]
        self.assertFalse(first_watering["booking"]["idempotent_replay"])
        self.assertTrue(second_watering["booking"]["idempotent_replay"])
        self.assertEqual(
            before_watering["tank_current_ml"]
            - after_watering["tank_current_ml"],
            first_watering["booking"]["actual_consumed_ml"],
        )

        self.application.settings.set(
            "pending_refill_request",
            json.dumps(
                {
                    "created_at": FIXED_LOCAL_NOW.astimezone(
                        timezone.utc
                    ).isoformat(),
                    "run_id": "upgrade-143-refill",
                    "source": "upgrade_test",
                    "target_date": FIXED_LOCAL_NOW.date().isoformat(),
                    "requested_ml": 2_000,
                    "transferred_ml": 2_000,
                    "duration_seconds": 90,
                    "window_label": "manual",
                }
            ),
        )
        before_refill = self.request("/api/state")["balcony"]
        first_refill = self.request(
            "/api/refill/mark-run",
            {"run_id": "upgrade-143-refill", "source": "upgrade_test"},
        )
        second_refill = self.request(
            "/api/refill/mark-run",
            {"run_id": "upgrade-143-refill", "source": "upgrade_test"},
        )
        after_refill = self.request("/api/state")["balcony"]
        transferred = first_refill["refill"]["transferred_ml"]
        self.assertFalse(first_refill["refill"]["idempotent_replay"])
        self.assertTrue(second_refill["refill"]["idempotent_replay"])
        self.assertEqual(
            after_refill["tank_current_ml"]
            - before_refill["tank_current_ml"],
            transferred,
        )
        self.assertEqual(
            before_refill["refill_tank_current_ml"]
            - after_refill["refill_tank_current_ml"],
            transferred,
        )

        notification_status = self.request(
            "/api/diagnostics/notifications"
        )
        home_assistant_status = self.request(
            "/api/diagnostics/home-assistant"
        )
        self.assertFalse(notification_status["smtp"]["enabled"])
        self.assertFalse(notification_status["smtp"]["configured"])
        self.assertFalse(notification_status["worker_running"])
        self.assertTrue(home_assistant_status["configured"])
        self.assertNotIn(
            "private-watering",
            json.dumps(home_assistant_status),
        )


if __name__ == "__main__":
    unittest.main()
