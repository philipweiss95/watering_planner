from __future__ import annotations

import json
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.request import Request, urlopen
from unittest.mock import MagicMock

from watering_backend.app import ApplicationPaths, create_application


class HttpIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        data_dir = Path(self.temporary_directory.name)
        root = Path(__file__).parent.parent
        self.application = create_application(
            paths=ApplicationPaths(
                root=root,
                public_dir=root / "public",
                data_dir=data_dir,
                database_path=data_dir / "watering.sqlite3",
                version_path=root / "VERSION",
                updater_token_file=data_dir / ".updater-token",
            )
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
        self.base_url = (
            f"http://127.0.0.1:{self.server.server_address[1]}"
        )

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
            method="POST" if data is not None else "GET",
        )
        with urlopen(request, timeout=10) as response:
            return json.loads(response.read().decode("utf-8"))

    def test_health_state_simulation_and_idempotent_booking(self) -> None:
        self.assertTrue(self.request("/api/health")["ok"])
        before = self.request("/api/state")
        simulation = {
            "temperature_c": 26,
            "rain_mm": 0,
            "wind_kmh": 8,
            "sunshine_hours": 7,
        }
        evaluation = self.request("/api/evaluate", simulation)
        payload = {**simulation, "run_id": "http-idempotent-test"}
        first = self.request("/api/homekit/mark-run", payload)
        second = self.request("/api/homekit/mark-run", payload)
        after = self.request("/api/state")

        self.assertTrue(evaluation["weather"]["simulation"])
        self.assertEqual(
            first["booking"]["id"],
            second["booking"]["id"],
        )
        self.assertFalse(first["booking"]["idempotent_replay"])
        self.assertTrue(second["booking"]["idempotent_replay"])
        self.assertEqual(
            before["balcony"]["tank_current_ml"]
            - after["balcony"]["tank_current_ml"],
            first["booking"]["actual_consumed_ml"],
        )

    def test_forced_cache_fallback_is_evaluated_without_second_fetch(
        self,
    ) -> None:
        balcony = self.application.get_state()["balcony"]
        cache_key = {
            "latitude": round(float(balcony["latitude"]), 6),
            "longitude": round(float(balcony["longitude"]), 6),
            "timezone": balcony["timezone_name"],
        }
        fetched_at = (
            datetime.now(timezone.utc) - timedelta(minutes=30)
        ).isoformat()
        cached_weather = {
            "source": "open-meteo",
            "mode": "forecast",
            "simulation": False,
            "temperature_c": 24,
            "rain_mm": 0,
            "wind_kmh": 6,
            "sunshine_hours": 7,
            "et0_mm": 3.5,
            "planning_day": {
                "date": datetime.now(timezone.utc).date().isoformat(),
                "temperature_c": 24,
                "rain_mm": 0,
                "wind_kmh": 6,
                "sunshine_hours": 7,
                "et0_mm": 3.5,
            },
            "forecast": [],
            "fetched_at": fetched_at,
        }
        self.application.settings.set(
            "weather_cache",
            json.dumps(
                {"cache_key": cache_key, "weather": cached_weather}
            ),
        )
        opener = MagicMock(side_effect=OSError("offline"))
        self.application.weather.opener = opener

        result = self.request(
            "/api/weather?force=true&evaluate=true&slot=morning"
        )

        self.assertEqual(opener.call_count, 1)
        self.assertTrue(result["weather"]["cache_fallback"])
        self.assertTrue(
            result["evaluation"]["weather"]["cache_fallback"]
        )
        self.assertEqual(
            result["evaluation"]["weather"]["fetched_at"],
            fetched_at,
        )

    def test_refill_run_http_lifecycle_is_persistent_and_idempotent(
        self,
    ) -> None:
        before = self.request("/api/state")["balcony"]
        started = self.request(
            "/api/refill/start",
            {
                "run_type": "manual",
                "run_id": "http-refill-lifecycle",
                "source": "integration_test",
            },
        )["refill_run"]
        running = self.request(
            "/api/refill/running",
            {"run_id": "http-refill-lifecycle"},
        )["refill_run"]
        completed = self.request(
            "/api/refill/complete",
            {"run_id": "http-refill-lifecycle"},
        )
        repeated = self.request(
            "/api/refill/complete",
            {"run_id": "http-refill-lifecycle"},
        )
        fetched = self.request(
            "/api/refill/runs/http-refill-lifecycle"
        )["refill_run"]

        self.assertEqual(started["status"], "reserved")
        self.assertEqual(running["status"], "running")
        self.assertEqual(
            completed["refill_run"]["status"],
            "completed",
        )
        self.assertFalse(
            completed["refill_run"]["idempotent_replay"]
        )
        self.assertTrue(
            repeated["refill_run"]["idempotent_replay"]
        )
        self.assertEqual(fetched["status"], "completed")
        self.assertEqual(
            fetched["planned_transfer_ml"],
            started["planned_transfer_ml"],
        )
        self.assertEqual(
            before["tank_current_ml"]
            + started["planned_transfer_ml"],
            completed["balcony"]["tank_current_ml"],
        )
        self.assertEqual(
            completed["balcony"]["tank_current_ml"],
            repeated["balcony"]["tank_current_ml"],
        )
        with self.application.database.connection() as conn:
            event_count = conn.execute(
                """
                SELECT COUNT(*) AS count
                FROM refill_events
                WHERE run_id = ?
                """,
                ("http-refill-lifecycle",),
            ).fetchone()["count"]
        self.assertEqual(event_count, 1)

    def test_uncertain_refill_can_be_reconciled_through_api(
        self,
    ) -> None:
        run_id = "http-refill-reconcile"
        self.request(
            "/api/refill/start",
            {
                "run_type": "manual",
                "run_id": run_id,
                "source": "integration_test",
            },
        )
        claimed = self.request(
            "/api/refill/running",
            {"run_id": run_id},
        )["refill_run"]
        self.assertTrue(claimed["pump_start_authorized"])
        self.request(
            "/api/refill/fail",
            {
                "run_id": run_id,
                "error": "Rückmeldung fehlt",
                "may_have_transferred": True,
            },
        )
        reconciled = self.request(
            f"/api/refill/runs/{run_id}/reconcile",
            {
                "mode": "no_transfer",
                "note": "Pumpe vor Ort als aus geprüft",
            },
        )["refill_run"]
        repeated = self.request(
            f"/api/refill/runs/{run_id}/reconcile",
            {"mode": "no_transfer"},
        )["refill_run"]

        self.assertEqual(reconciled["status"], "cancelled")
        self.assertEqual(
            reconciled["reconciliation_mode"],
            "no_transfer",
        )
        self.assertFalse(reconciled["needs_manual_review"])
        self.assertTrue(repeated["idempotent_replay"])


if __name__ == "__main__":
    unittest.main()
