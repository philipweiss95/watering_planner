from __future__ import annotations

import json
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.request import Request, urlopen

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


if __name__ == "__main__":
    unittest.main()
