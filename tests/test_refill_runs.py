from __future__ import annotations

import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from watering_backend.app import Application, ApplicationPaths


ROOT = Path(__file__).parent.parent


class MutableClock:
    def __init__(self, value: datetime):
        self.value = value

    def local(self, timezone_name: str) -> datetime:
        return self.value.astimezone(ZoneInfo(timezone_name))

    def iso(self) -> str:
        return self.value.astimezone(timezone.utc).isoformat()


class RefillRunLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        data_dir = Path(self.temporary_directory.name)
        self.paths = ApplicationPaths(
            root=ROOT,
            public_dir=ROOT / "public",
            data_dir=data_dir,
            database_path=data_dir / "watering.sqlite3",
            version_path=ROOT / "VERSION",
            updater_token_file=data_dir / ".updater-token",
        )
        self.clock = MutableClock(
            datetime(
                2026,
                6,
                3,
                1,
                59,
                tzinfo=ZoneInfo("Europe/Berlin"),
            )
        )
        self.application = self._application()
        self.application.initialize()

    def tearDown(self) -> None:
        self.application.stop_notification_worker()
        self.temporary_directory.cleanup()

    def _application(self) -> Application:
        return Application(
            self.paths,
            environment={
                "NOTIFICATION_WORKER_DISABLED": "true",
                "HOME_ASSISTANT_REFILL_WEBHOOK_URL": (
                    "http://home-assistant.test/refill"
                ),
            },
            local_clock=self.clock.local,
            now_iso=self.clock.iso,
        )

    def _set_tanks(
        self,
        *,
        main_ml: int | None = None,
        refill_ml: int | None = None,
    ) -> None:
        with self.application.database.connection(
            immediate=True
        ) as conn:
            current = self.application.tanks.balcony(conn=conn)
            conn.execute(
                """
                UPDATE balcony_settings
                SET tank_current_ml = ?,
                    refill_tank_current_ml = ?
                WHERE id = 1
                """,
                (
                    (
                        int(current["tank_current_ml"])
                        if main_ml is None
                        else main_ml
                    ),
                    (
                        int(current["refill_tank_current_ml"])
                        if refill_ml is None
                        else refill_ml
                    ),
                ),
            )

    def test_window_limited_run_completes_after_window_with_reserved_values(
        self,
    ) -> None:
        reserved = self.application.start_refill_run(
            run_type="automatic",
            run_id="window-limited",
        )

        self.assertEqual(reserved["status"], "reserved")
        self.assertEqual(reserved["planned_transfer_ml"], 833)
        self.assertEqual(reserved["duration_seconds"], 50)
        self.assertIn("window_remaining", reserved["limit_reasons"])
        self.assertLessEqual(
            datetime.fromisoformat(reserved["expected_complete_at"]),
            datetime.fromisoformat(reserved["window"]["end"])
            - timedelta(seconds=10),
        )

        config = self.application.settings.planner_config()
        config["refill_fraction"] = 0.1
        self.application.settings.save_planner_config(config)
        self.clock.value = datetime(
            2026,
            6,
            3,
            2,
            0,
            5,
            tzinfo=ZoneInfo("Europe/Berlin"),
        )
        completed = self.application.complete_refill_run(
            "window-limited"
        )

        self.assertEqual(completed["status"], "completed")
        self.assertEqual(completed["authorized_transfer_ml"], 833)
        self.assertEqual(completed["transferred_ml"], 833)
        with self.application.database.connection() as conn:
            event = conn.execute(
                "SELECT * FROM refill_events WHERE run_id = ?",
                ("window-limited",),
            ).fetchone()
            fulfilled = conn.execute(
                """
                SELECT COUNT(*) AS count
                FROM refill_window_plans
                WHERE window_key = ? AND fulfilled_at IS NOT NULL
                """,
                (reserved["window"]["key"],),
            ).fetchone()
            other_fulfilled = conn.execute(
                """
                SELECT COUNT(*) AS count
                FROM refill_window_plans
                WHERE window_key <> ? AND fulfilled_at IS NOT NULL
                """,
                (reserved["window"]["key"],),
            ).fetchone()
        self.assertEqual(int(event["transferred_ml"]), 833)
        self.assertEqual(int(fulfilled["count"]), 1)
        self.assertEqual(int(other_fulfilled["count"]), 0)

    def test_changed_tanks_create_visible_consistency_difference_once(
        self,
    ) -> None:
        self.clock.value = datetime(
            2026,
            6,
            3,
            10,
            0,
            tzinfo=ZoneInfo("Europe/Berlin"),
        )
        reserved = self.application.start_refill_run(
            run_type="manual",
            run_id="tank-drift",
        )
        self.assertEqual(reserved["planned_transfer_ml"], 1000)
        self._set_tanks(main_ml=9500, refill_ml=700)

        first = self.application.complete_refill_run("tank-drift")
        levels_after_first = self.application.tanks.balcony()
        second = self.application.complete_refill_run("tank-drift")
        levels_after_second = self.application.tanks.balcony()

        self.assertEqual(first["transferred_ml"], 700)
        self.assertEqual(first["main_accounted_ml"], 500)
        self.assertEqual(first["consistency_delta_ml"], 500)
        self.assertTrue(first["needs_manual_review"])
        self.assertIn("Tankstände manuell prüfen", first["consistency_note"])
        self.assertTrue(second["idempotent_replay"])
        self.assertEqual(levels_after_first, levels_after_second)
        self.assertEqual(levels_after_second["tank_current_ml"], 10000)
        self.assertEqual(
            levels_after_second["refill_tank_current_ml"],
            0,
        )
        with self.application.database.connection() as conn:
            count = conn.execute(
                "SELECT COUNT(*) AS count FROM refill_events "
                "WHERE run_id = 'tank-drift'"
            ).fetchone()
        self.assertEqual(int(count["count"]), 1)

    def test_parallel_same_start_is_idempotent(self) -> None:
        self.clock.value = datetime(
            2026,
            6,
            3,
            10,
            0,
            tzinfo=ZoneInfo("Europe/Berlin"),
        )
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(
                executor.map(
                    lambda _index: self.application.start_refill_run(
                        run_type="manual",
                        run_id="parallel-same",
                    ),
                    range(2),
                )
            )
        self.assertEqual(
            {item["run_id"] for item in results},
            {"parallel-same"},
        )
        self.assertEqual(
            sorted(item["idempotent_replay"] for item in results),
            [False, True],
        )
        with self.application.database.connection() as conn:
            count = conn.execute(
                "SELECT COUNT(*) AS count FROM refill_runs"
            ).fetchone()
        self.assertEqual(int(count["count"]), 1)

    def test_parallel_different_starts_allow_only_one_active_run(self) -> None:
        self.clock.value = datetime(
            2026,
            6,
            3,
            10,
            0,
            tzinfo=ZoneInfo("Europe/Berlin"),
        )

        def start(run_id: str):
            try:
                return self.application.start_refill_run(
                    run_type="manual",
                    run_id=run_id,
                )
            except ValueError as exc:
                return exc

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(
                executor.map(start, ("parallel-a", "parallel-b"))
            )
        self.assertEqual(
            sum(isinstance(item, dict) for item in results),
            1,
        )
        self.assertEqual(
            sum(isinstance(item, ValueError) for item in results),
            1,
        )

    def test_parallel_completion_books_tanks_and_event_once(self) -> None:
        self.clock.value = datetime(
            2026,
            6,
            3,
            10,
            0,
            tzinfo=ZoneInfo("Europe/Berlin"),
        )
        self.application.start_refill_run(
            run_type="manual",
            run_id="parallel-complete",
        )
        before = self.application.tanks.balcony()
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(
                executor.map(
                    lambda _index: self.application.complete_refill_run(
                        "parallel-complete"
                    ),
                    range(2),
                )
            )
        after = self.application.tanks.balcony()
        self.assertEqual(
            sorted(item["idempotent_replay"] for item in results),
            [False, True],
        )
        self.assertEqual(
            after["tank_current_ml"] - before["tank_current_ml"],
            1000,
        )
        with self.application.database.connection() as conn:
            count = conn.execute(
                "SELECT COUNT(*) AS count FROM refill_events "
                "WHERE run_id = 'parallel-complete'"
            ).fetchone()
        self.assertEqual(int(count["count"]), 1)

    def test_restart_and_delayed_feedback_preserve_open_run(self) -> None:
        reserved = self.application.start_refill_run(
            run_type="automatic",
            run_id="restart-run",
        )
        self.application.mark_refill_running("restart-run")
        self.clock.value = datetime(
            2026,
            6,
            3,
            2,
            3,
            tzinfo=ZoneInfo("Europe/Berlin"),
        )
        restarted = self._application()
        restarted.initialize()
        try:
            visible = restarted.get_refill_run("restart-run")
            completed = restarted.complete_refill_run("restart-run")
        finally:
            restarted.stop_notification_worker()

        self.assertEqual(visible["status"], "running")
        self.assertEqual(
            visible["planned_transfer_ml"],
            reserved["planned_transfer_ml"],
        )
        self.assertEqual(completed["status"], "completed")

    def test_failed_and_expired_runs_are_diagnostic_and_release_lock(
        self,
    ) -> None:
        self.clock.value = datetime(
            2026,
            6,
            3,
            10,
            0,
            tzinfo=ZoneInfo("Europe/Berlin"),
        )
        before = self.application.tanks.balcony()
        self.application.start_refill_run(
            run_type="manual",
            run_id="failed-run",
        )
        self.application.mark_refill_running("failed-run")
        failed = self.application.fail_refill_run(
            "failed-run",
            error="Pumpe meldet Fehler",
        )
        self.assertEqual(failed["status"], "failed")
        self.assertTrue(failed["needs_manual_review"])
        self.assertEqual(before, self.application.tanks.balcony())

        reserved = self.application.start_refill_run(
            run_type="manual",
            run_id="expires-run",
        )
        self.clock.value = (
            datetime.fromisoformat(reserved["expires_at"])
            .astimezone(ZoneInfo("Europe/Berlin"))
            + timedelta(seconds=1)
        )
        diagnostics = self.application.refill_runs.diagnostics()
        replacement = self.application.start_refill_run(
            run_type="manual",
            run_id="replacement-run",
        )

        expired = self.application.get_refill_run("expires-run")
        self.assertEqual(expired["status"], "expired")
        self.assertTrue(diagnostics["manual_review_required"])
        self.assertEqual(replacement["status"], "reserved")

    def test_reserve_and_main_capacity_limit_authorized_amount(self) -> None:
        self.clock.value = datetime(
            2026,
            6,
            3,
            10,
            0,
            tzinfo=ZoneInfo("Europe/Berlin"),
        )
        self._set_tanks(main_ml=8000, refill_ml=300)
        reserve_limited = self.application.start_refill_run(
            run_type="manual",
            run_id="reserve-limit",
        )
        self.assertEqual(reserve_limited["planned_transfer_ml"], 300)
        self.assertIn("refill_tank", reserve_limited["limit_reasons"])
        self.application.fail_refill_run(
            "reserve-limit",
            may_have_transferred=False,
        )

        self._set_tanks(main_ml=9700, refill_ml=30000)
        config = self.application.settings.planner_config()
        config["refill_strategy"] = "target"
        config["refill_target_ml"] = 1000
        self.application.settings.save_planner_config(config)
        capacity_limited = self.application.start_refill_run(
            run_type="manual",
            run_id="capacity-limit",
        )
        self.assertEqual(capacity_limited["planned_transfer_ml"], 300)
        self.assertIn(
            "main_tank_capacity",
            capacity_limited["limit_reasons"],
        )
        self.assertLessEqual(
            capacity_limited["planned_transfer_ml"],
            300,
        )

    def test_completed_run_activates_cooldown_for_second_run(self) -> None:
        self.clock.value = datetime(
            2026,
            6,
            3,
            10,
            0,
            tzinfo=ZoneInfo("Europe/Berlin"),
        )
        self.application.start_refill_run(
            run_type="manual",
            run_id="cooldown-first",
        )
        self.application.complete_refill_run("cooldown-first")
        with self.assertRaisesRegex(ValueError, "Cooldown"):
            self.application.start_refill_run(
                run_type="manual",
                run_id="cooldown-second",
            )

    def test_ambiguous_manual_webhook_failure_requires_review(
        self,
    ) -> None:
        self.clock.value = datetime(
            2026,
            6,
            3,
            10,
            0,
            tzinfo=ZoneInfo("Europe/Berlin"),
        )
        result = self.application.evaluate(
            temperature_c=22,
            rain_mm=0,
        )
        with patch.object(
            self.application.home_assistant,
            "trigger_reserved_refill",
            side_effect=ValueError("Zeitüberschreitung"),
        ):
            with self.assertRaisesRegex(ValueError, "Zeitüberschreitung"):
                self.application.trigger_home_assistant_manual_refill(
                    result,
                    "ambiguous-webhook",
                )

        failed = self.application.get_refill_run(
            "ambiguous-webhook"
        )
        self.assertEqual(failed["status"], "failed")
        self.assertTrue(failed["needs_manual_review"])
        self.assertEqual(
            failed["completion_reason"],
            "pump_state_uncertain",
        )


if __name__ == "__main__":
    unittest.main()
