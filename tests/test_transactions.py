from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from watering_backend.api import ApiRequest, build_router
from watering_backend.app import ApplicationPaths, create_application


class TransactionTests(unittest.TestCase):
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

    def tearDown(self) -> None:
        self.application.stop_notification_worker()
        self.temporary_directory.cleanup()

    def test_failure_after_event_insert_rolls_back_the_whole_run(self) -> None:
        before = self.application.get_state()["balcony"]["tank_current_ml"]
        result = self.application.evaluate(26, 0, 8, sunshine_hours=7)
        delivered_ml = result["pump"]["delivered_per_cycle_ml"]

        with patch.object(
            self.application.tanks,
            "adjust_tanks",
            side_effect=RuntimeError("injected transaction failure"),
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "injected transaction failure",
            ):
                self.application.mark_run(
                    delivered_ml,
                    26,
                    0,
                    run_id="rollback-test",
                )

        self.assertEqual(
            self.application.get_state()["balcony"]["tank_current_ml"],
            before,
        )
        self.assertIsNone(
            self.application.events.watering_by_run_id("rollback-test")
        )

    def test_registered_routes_integrate_with_real_application(self) -> None:
        expected_version = (
            Path(__file__).parent.parent / "VERSION"
        ).read_text(encoding="utf-8").strip()
        router = build_router()
        health = router.dispatch(
            ApiRequest("GET", "/api/health"),
            self.application,
        )
        state = router.dispatch(
            ApiRequest("GET", "/api/state"),
            self.application,
        )

        self.assertEqual(
            health.payload,
            {"ok": True, "version": expected_version},
        )
        self.assertEqual(state.payload["version"], expected_version)
        self.assertEqual(len(state.payload["plants"]), 3)


if __name__ == "__main__":
    unittest.main()

