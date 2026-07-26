from __future__ import annotations

import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from watering_backend.app import Application, ApplicationPaths
from watering_backend.errors import ConflictError


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

    def _uncertain_run(self, run_id: str) -> dict:
        reserved = self.application.start_refill_run(
            run_type="manual",
            run_id=run_id,
        )
        claimed = self.application.mark_refill_running(run_id)
        self.assertTrue(claimed["pump_start_authorized"])
        failed = self.application.fail_refill_run(
            run_id,
            error="Pumpenzustand unbekannt",
        )
        self.assertTrue(failed["needs_manual_review"])
        return reserved

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
        claimed = self.application.mark_refill_running(
            "window-limited"
        )
        self.assertTrue(claimed["pump_start_authorized"])

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
        self.application.mark_refill_running("tank-drift")
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
        reviewed = self.application.reconcile_refill_run(
            "tank-drift",
            mode="cancelled_after_review",
        )
        self.assertEqual(reviewed["status"], "completed")
        self.assertFalse(reviewed["needs_manual_review"])
        self.assertEqual(reviewed["transferred_ml"], 700)

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
        self.application.mark_refill_running("parallel-complete")
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

        self.application.reconcile_refill_run(
            "failed-run",
            mode="no_transfer",
        )
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
        with self.assertRaisesRegex(ValueError, "ungeklärt"):
            self.application.start_refill_run(
                run_type="manual",
                run_id="blocked-replacement-run",
            )
        self.application.reconcile_refill_run(
            "expires-run",
            mode="no_transfer",
        )
        replacement = self.application.start_refill_run(
            run_type="manual",
            run_id="replacement-run",
        )

        expired = self.application.get_refill_run("expires-run")
        self.assertEqual(expired["status"], "cancelled")
        self.assertFalse(expired["needs_manual_review"])
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
        self.application.mark_refill_running("cooldown-first")
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

    def test_terminal_runs_never_reauthorize_pump_start(self) -> None:
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
            run_id="terminal-failed",
        )
        self.application.fail_refill_run(
            "terminal-failed",
            may_have_transferred=False,
        )
        failed = self.application.start_refill_run(
            run_type="manual",
            run_id="terminal-failed",
        )

        self._uncertain_run("terminal-cancelled")
        self.application.reconcile_refill_run(
            "terminal-cancelled",
            mode="cancelled_after_review",
        )
        cancelled = self.application.start_refill_run(
            run_type="manual",
            run_id="terminal-cancelled",
        )

        expired_reservation = self.application.start_refill_run(
            run_type="manual",
            run_id="terminal-expired",
        )
        self.clock.value = (
            datetime.fromisoformat(expired_reservation["expires_at"])
            .astimezone(ZoneInfo("Europe/Berlin"))
            + timedelta(seconds=1)
        )
        self.application.refill_runs.expire_stale()
        expired = self.application.start_refill_run(
            run_type="manual",
            run_id="terminal-expired",
        )
        self.application.reconcile_refill_run(
            "terminal-expired",
            mode="no_transfer",
        )

        self.application.start_refill_run(
            run_type="manual",
            run_id="terminal-completed",
        )
        self.application.mark_refill_running("terminal-completed")
        self.application.complete_refill_run("terminal-completed")
        completed = self.application.start_refill_run(
            run_type="manual",
            run_id="terminal-completed",
        )

        for result, status in (
            (failed, "failed"),
            (cancelled, "cancelled"),
            (expired, "expired"),
            (completed, "completed"),
        ):
            with self.subTest(status=status):
                self.assertEqual(result["status"], status)
                self.assertFalse(result["pump_start_authorized"])
                self.assertEqual(result["duration_seconds"], 0)
                self.assertTrue(result["idempotent_replay"])

    def test_parallel_claim_authorizes_exactly_one_pump_start(
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
        self.application.start_refill_run(
            run_type="manual",
            run_id="parallel-claim",
        )
        with ThreadPoolExecutor(max_workers=4) as executor:
            results = list(
                executor.map(
                    lambda _index: self.application.mark_refill_running(
                        "parallel-claim"
                    ),
                    range(4),
                )
            )
        authorized = [
            item for item in results if item["pump_start_authorized"]
        ]
        rejected = [
            item for item in results if not item["pump_start_authorized"]
        ]
        self.assertEqual(len(authorized), 1)
        self.assertEqual(len(rejected), 3)
        self.assertGreater(authorized[0]["duration_seconds"], 0)
        self.assertTrue(
            all(item["duration_seconds"] == 0 for item in rejected)
        )
        self.assertEqual(
            {item["status"] for item in results},
            {"running"},
        )

    def test_duplicate_and_late_webhooks_cannot_restart_pump(self) -> None:
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
            run_id="duplicate-webhook",
        )
        repeated_reservation = self.application.start_refill_run(
            run_type="manual",
            run_id="duplicate-webhook",
        )
        first_claim = self.application.mark_refill_running(
            "duplicate-webhook"
        )
        repeated_claim = self.application.mark_refill_running(
            "duplicate-webhook"
        )
        completed = self.application.complete_refill_run(
            "duplicate-webhook"
        )
        late_start = self.application.start_refill_run(
            run_type="manual",
            run_id="duplicate-webhook",
        )
        late_claim = self.application.mark_refill_running(
            "duplicate-webhook"
        )
        repeated_complete = self.application.complete_refill_run(
            "duplicate-webhook"
        )

        self.assertFalse(reserved["pump_start_authorized"])
        self.assertFalse(repeated_reservation["pump_start_authorized"])
        self.assertTrue(first_claim["pump_start_authorized"])
        self.assertFalse(repeated_claim["pump_start_authorized"])
        self.assertFalse(late_start["pump_start_authorized"])
        self.assertFalse(late_claim["pump_start_authorized"])
        self.assertEqual(late_start["duration_seconds"], 0)
        self.assertEqual(late_claim["duration_seconds"], 0)
        self.assertEqual(completed["status"], "completed")
        self.assertTrue(repeated_complete["idempotent_replay"])
        with self.application.database.connection() as conn:
            count = conn.execute(
                "SELECT COUNT(*) AS count FROM refill_events "
                "WHERE run_id = 'duplicate-webhook'"
            ).fetchone()
        self.assertEqual(int(count["count"]), 1)

    def test_uncertain_run_blocks_every_direct_start(self) -> None:
        self.clock.value = datetime(
            2026,
            6,
            3,
            10,
            0,
            tzinfo=ZoneInfo("Europe/Berlin"),
        )
        self._uncertain_run("uncertain-blocker")
        with self.assertRaisesRegex(ValueError, "ungeklärt"):
            self.application.start_refill_run(
                run_type="manual",
                run_id="must-not-start",
            )
        self.application.reconcile_refill_run(
            "uncertain-blocker",
            mode="no_transfer",
        )
        allowed = self.application.start_refill_run(
            run_type="manual",
            run_id="allowed-after-review",
        )
        self.assertEqual(allowed["status"], "reserved")

    def test_all_manual_reconciliation_modes_are_atomic(self) -> None:
        self.clock.value = datetime(
            2026,
            6,
            3,
            10,
            0,
            tzinfo=ZoneInfo("Europe/Berlin"),
        )
        before = self.application.tanks.balcony()

        self._uncertain_run("reconcile-none")
        none_result = self.application.reconcile_refill_run(
            "reconcile-none",
            mode="no_transfer",
            note="Pumpe blieb aus",
        )
        none_repeated = self.application.reconcile_refill_run(
            "reconcile-none",
            mode="no_transfer",
            note="Pumpe blieb aus",
        )
        self.assertEqual(none_result["status"], "cancelled")
        self.assertEqual(
            none_result["reconciliation_mode"],
            "no_transfer",
        )
        self.assertFalse(none_result["needs_manual_review"])
        self.assertTrue(none_repeated["idempotent_replay"])
        self.assertEqual(before, self.application.tanks.balcony())

        self._uncertain_run("reconcile-cancelled")
        cancelled = self.application.reconcile_refill_run(
            "reconcile-cancelled",
            mode="cancelled_after_review",
        )
        self.assertEqual(cancelled["status"], "cancelled")

        self._uncertain_run("reconcile-measured")
        measured_before = self.application.tanks.balcony()
        measured = self.application.reconcile_refill_run(
            "reconcile-measured",
            mode="measured_transfer",
            measured_transfer_ml=321,
        )
        measured_after = self.application.tanks.balcony()
        self.assertEqual(measured["status"], "completed")
        self.assertEqual(measured["transferred_ml"], 321)
        self.assertEqual(
            measured_after["tank_current_ml"]
            - measured_before["tank_current_ml"],
            321,
        )

        self.clock.value += timedelta(hours=4)
        self._uncertain_run("reconcile-full")
        full = self.application.reconcile_refill_run(
            "reconcile-full",
            mode="full_transfer",
        )
        self.assertEqual(full["status"], "completed")
        self.assertEqual(
            full["transferred_ml"],
            full["planned_transfer_ml"],
        )

        self.clock.value += timedelta(hours=4)
        self._set_tanks(main_ml=8000, refill_ml=12000)
        self._uncertain_run("reconcile-levels")
        corrected = self.application.reconcile_refill_run(
            "reconcile-levels",
            mode="tank_levels_corrected",
            main_tank_current_ml=7250,
            refill_tank_current_ml=11100,
        )
        corrected_levels = self.application.tanks.balcony()
        self.assertEqual(corrected["status"], "cancelled")
        self.assertEqual(
            corrected["reconciliation_mode"],
            "tank_levels_corrected",
        )
        self.assertEqual(corrected_levels["tank_current_ml"], 7250)
        self.assertEqual(
            corrected_levels["refill_tank_current_ml"],
            11100,
        )
        with self.application.database.connection() as conn:
            event_count = conn.execute(
                """
                SELECT COUNT(*) AS count
                FROM refill_events
                WHERE source LIKE 'reconcile:%'
                """
            ).fetchone()
        self.assertEqual(int(event_count["count"]), 2)

    def test_reconciliation_replay_must_match_persisted_request(
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
        self._uncertain_run("reconcile-signature-measured")
        first = self.application.reconcile_refill_run(
            "reconcile-signature-measured",
            mode="measured_transfer",
            measured_transfer_ml=321,
            note="vor Ort gemessen",
        )
        after_first = self.application.tanks.balcony()
        repeated = self.application.reconcile_refill_run(
            "reconcile-signature-measured",
            mode="measured_transfer",
            measured_transfer_ml="321",
            note="vor Ort gemessen",
        )
        self.assertTrue(repeated["idempotent_replay"])
        self.assertEqual(first["transferred_ml"], 321)
        self.assertEqual(after_first, self.application.tanks.balcony())

        with self.assertRaises(ConflictError):
            self.application.reconcile_refill_run(
                "reconcile-signature-measured",
                mode="measured_transfer",
                measured_transfer_ml=322,
                note="vor Ort gemessen",
            )
        with self.assertRaises(ConflictError):
            self.application.reconcile_refill_run(
                "reconcile-signature-measured",
                mode="tank_levels_corrected",
                main_tank_current_ml=7000,
                refill_tank_current_ml=11000,
                note="vor Ort gemessen",
            )
        self.assertEqual(after_first, self.application.tanks.balcony())

        self.clock.value += timedelta(hours=4)
        self._uncertain_run("reconcile-signature-levels")
        corrected = self.application.reconcile_refill_run(
            "reconcile-signature-levels",
            mode="tank_levels_corrected",
            main_tank_current_ml=7250,
            refill_tank_current_ml=11100,
            note="Tankstände abgelesen",
        )
        corrected_levels = self.application.tanks.balcony()
        repeated_levels = self.application.reconcile_refill_run(
            "reconcile-signature-levels",
            mode="tank_levels_corrected",
            main_tank_current_ml="7250",
            refill_tank_current_ml="11100",
            note="Tankstände abgelesen",
        )
        self.assertTrue(repeated_levels["idempotent_replay"])
        self.assertEqual(corrected["status"], repeated_levels["status"])
        with self.assertRaises(ConflictError):
            self.application.reconcile_refill_run(
                "reconcile-signature-levels",
                mode="tank_levels_corrected",
                main_tank_current_ml=0,
                refill_tank_current_ml=0,
                note="Tankstände abgelesen",
            )
        self.assertEqual(
            corrected_levels,
            self.application.tanks.balcony(),
        )

    def test_empty_corrected_tank_levels_never_become_zero(
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
        self._uncertain_run("reconcile-empty-levels")
        before = self.application.tanks.balcony()
        for main_ml, refill_ml in (
            (None, 12000),
            ("", 12000),
            ("7000", None),
            ("7000", " "),
        ):
            with self.subTest(
                main_ml=main_ml,
                refill_ml=refill_ml,
            ):
                with self.assertRaisesRegex(
                    ValueError,
                    "darf nicht leer sein",
                ):
                    self.application.reconcile_refill_run(
                        "reconcile-empty-levels",
                        mode="tank_levels_corrected",
                        main_tank_current_ml=main_ml,
                        refill_tank_current_ml=refill_ml,
                    )
                self.assertEqual(
                    before,
                    self.application.tanks.balcony(),
                )

    def test_expired_old_run_cannot_complete_after_replacement(
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
        old = self.application.start_refill_run(
            run_type="manual",
            run_id="expired-old",
        )
        self.application.mark_refill_running("expired-old")
        self.clock.value = (
            datetime.fromisoformat(old["expires_at"])
            .astimezone(ZoneInfo("Europe/Berlin"))
            + timedelta(seconds=1)
        )
        self.application.refill_runs.expire_stale()
        with self.application.database.connection(immediate=True) as conn:
            conn.execute(
                """
                UPDATE refill_runs
                SET needs_manual_review = 0
                WHERE run_id = 'expired-old'
                """
            )
        replacement = self.application.start_refill_run(
            run_type="manual",
            run_id="newer-replacement",
        )
        before = self.application.tanks.balcony()
        with self.assertRaisesRegex(ValueError, "neueren Lauf"):
            self.application.complete_refill_run("expired-old")
        self.assertEqual(before, self.application.tanks.balcony())
        self.assertEqual(replacement["status"], "reserved")

    def test_reconciliation_failure_rolls_back_tanks_and_event(
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
        self._uncertain_run("reconcile-rollback")
        before = self.application.tanks.balcony()
        with patch.object(
            self.application.refill_run_repository,
            "mark_reconciled",
            side_effect=RuntimeError("simulated reconciliation crash"),
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "simulated reconciliation crash",
            ):
                self.application.reconcile_refill_run(
                    "reconcile-rollback",
                    mode="full_transfer",
                )
        self.assertEqual(before, self.application.tanks.balcony())
        run = self.application.get_refill_run("reconcile-rollback")
        self.assertTrue(run["needs_manual_review"])
        with self.application.database.connection() as conn:
            event = self.application.events.refill_by_run_id(
                "reconcile-rollback",
                conn=conn,
            )
        self.assertIsNone(event)


if __name__ == "__main__":
    unittest.main()
