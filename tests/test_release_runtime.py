from __future__ import annotations

import copy
import unittest

from scripts.verify_release_runtime import RuntimeVerificationError, verify_runtime


class FakeRuntimeClient:
    def __init__(self, mutate_duplicate: bool = False):
        self.main_tank = 17_321
        self.refill_tank = 41_007
        self.mutate_duplicate = mutate_duplicate
        self.watering_events = {}
        self.refill_events = {}

    def state(self):
        return {
            "version": "1.5.0",
            "balcony": {
                "tank_capacity_ml": 45_000,
                "tank_current_ml": self.main_tank,
                "refill_tank_capacity_ml": 55_000,
                "refill_tank_current_ml": self.refill_tank,
            },
            "plants": [
                {"id": 11, "catalog_id": "olive", "custom_name": "Olivia", "size": "large"},
                {"id": 12, "catalog_id": "tomato", "custom_name": "Roma links", "size": "medium"},
                {"id": 13, "catalog_id": "lavender", "custom_name": "Lavendel klein", "size": "small"},
                {"id": 14, "catalog_id": "citrus", "custom_name": "Zitrone am Gelander", "size": "large"},
                {"id": 15, "catalog_id": "olive", "custom_name": "Olivenbaum Altbestand", "size": "tree"},
            ],
            "hoses": [{"number": f"{number:02d}"} for number in range(1, 9)],
        }

    def request(self, path, payload=None):
        if path == "/api/health":
            return {"ok": True, "version": "1.5.0"}
        if path == "/api/state":
            return copy.deepcopy(self.state())
        if path == "/api/evaluate":
            return {
                "weather": {"simulation": True},
                "depletion": {
                    "forecast_events": [{"event_type": "watering"}],
                    "last_supported_watering_at": "2026-07-25T15:00:00+02:00",
                },
            }
        if path == "/api/watering-events?limit=12":
            return {"events": [{"id": event_id} for event_id in (103, 102, 101)]}
        if path == "/api/homekit/mark-run":
            run_id = payload["run_id"]
            replay = run_id in self.watering_events
            if not replay:
                self.watering_events[run_id] = {
                    "id": 201,
                    "run_id": run_id,
                    "actual_consumed_ml": 500,
                }
                self.main_tank -= 500
            elif self.mutate_duplicate:
                self.main_tank -= 500
            return {
                "booking": {
                    **self.watering_events[run_id],
                    "idempotent_replay": replay,
                }
            }
        if path == "/api/refill/mark-run":
            run_id = payload["run_id"]
            replay = run_id in self.refill_events
            if not replay:
                self.refill_events[run_id] = {
                    "id": 301,
                    "run_id": run_id,
                    "transferred_ml": 2_000,
                }
                self.main_tank += 2_000
                self.refill_tank -= 2_000
            return {
                "refill": {
                    **self.refill_events[run_id],
                    "idempotent_replay": replay,
                }
            }
        if path == "/api/diagnostics/notifications":
            return {"smtp": {"enabled": False}, "worker_running": False}
        if path == "/api/diagnostics/home-assistant":
            return {"configured": False}
        raise AssertionError(f"unexpected request: {path}")


class ReleaseRuntimeScriptTests(unittest.TestCase):
    def test_runtime_verifier_checks_complete_api_and_idempotency(self):
        result = verify_runtime(
            FakeRuntimeClient(),
            expected_version="1.5.0",
            run_prefix="unit-runtime",
            forbidden_values=["must-not-leak"],
        )

        self.assertEqual(result["plant_count"], 5)
        self.assertEqual(result["hose_count"], 8)
        self.assertEqual(result["watering_consumed_ml"], 500)
        self.assertEqual(result["refill_transferred_ml"], 2_000)

    def test_runtime_verifier_detects_duplicate_tank_mutation(self):
        with self.assertRaisesRegex(RuntimeVerificationError, "mehr als einmal"):
            verify_runtime(
                FakeRuntimeClient(mutate_duplicate=True),
                expected_version="1.5.0",
                run_prefix="bad-runtime",
            )

    def test_runtime_verifier_rejects_secret_leaks(self):
        client = FakeRuntimeClient()
        original = client.request

        def leaking_request(path, payload=None):
            result = original(path, payload)
            if path == "/api/diagnostics/home-assistant":
                result["secret"] = "must-not-leak"
            return result

        client.request = leaking_request
        with self.assertRaisesRegex(RuntimeVerificationError, "geheime"):
            verify_runtime(
                client,
                expected_version="1.5.0",
                run_prefix="secret-runtime",
                forbidden_values=["must-not-leak"],
            )


if __name__ == "__main__":
    unittest.main()
