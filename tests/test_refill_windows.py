from __future__ import annotations

import json
import shutil
import threading
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from watering_backend.app import Application, ApplicationPaths
from watering_backend.config import validate_planner_config
from watering_backend.notifications import NotificationWorker


ROOT = Path(__file__).resolve().parents[1]
BERLIN = ZoneInfo("Europe/Berlin")


class MutableClock:
    def __init__(self, value: datetime):
        self.value = value

    def set(self, value: datetime) -> None:
        self.value = value

    def local(self, timezone_name: str) -> datetime:
        return self.value.astimezone(ZoneInfo(timezone_name))

    def now_iso(self) -> str:
        return self.value.astimezone(timezone.utc).isoformat()


class RefillWindowPersistenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = ROOT / f".refill-window-test-{uuid.uuid4().hex}"
        self.root.mkdir()
        self.clock = MutableClock(
            datetime(2026, 7, 25, 0, 15, tzinfo=BERLIN)
        )
        self.application = self.new_application()
        self.application.initialize()

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def new_application(self) -> Application:
        data_dir = self.root / "data"
        paths = ApplicationPaths(
            root=self.root,
            public_dir=self.root / "public",
            data_dir=data_dir,
            database_path=data_dir / "watering.sqlite3",
            version_path=self.root / "VERSION",
            updater_token_file=data_dir / ".updater-token",
        )
        return Application(
            paths,
            environment={"NOTIFICATION_WORKER_DISABLED": "true"},
            local_clock=self.clock.local,
            now_iso=self.clock.now_iso,
        )

    def configure(
        self,
        *,
        windows: list[dict[str, str]] | None = None,
        main_current_ml: int = 8_000,
        refill_current_ml: int = 30_000,
        pump_ml_per_min: int = 1_000,
        enabled: bool = True,
        minimum_interval_minutes: int = 0,
    ) -> dict:
        config = self.application.settings.planner_config()
        config["refill_windows"] = windows or [
            {"start": "01:00", "end": "02:00"}
        ]
        config["refill_min_interval_minutes"] = minimum_interval_minutes
        config["refill_strategy"] = "target"
        config["refill_target_ml"] = 2_000
        self.application.settings.save_planner_config(config)
        self.application.settings.save_refill_automation_enabled(enabled)
        with self.application.database.connection() as conn:
            conn.execute(
                """
                UPDATE balcony_settings
                SET tank_capacity_ml = 10000,
                    tank_current_ml = ?,
                    refill_tank_capacity_ml = 30000,
                    refill_tank_current_ml = ?,
                    refill_pump_ml_per_min = ?,
                    timezone_name = 'Europe/Berlin'
                WHERE id = 1
                """,
                (
                    main_current_ml,
                    refill_current_ml,
                    pump_ml_per_min,
                ),
            )
        return self.application.refill.status()

    def active_plans(
        self,
        target_date: date | str,
        *,
        application: Application | None = None,
    ) -> list[dict]:
        active = application or self.application
        value = (
            target_date.isoformat()
            if isinstance(target_date, date)
            else target_date
        )
        return [
            item
            for item in active.events.refill_window_plans(
                target_dates=[value]
            )
            if not item["cancelled_at"]
        ]

    def restart_at(self, value: datetime) -> tuple[Application, dict]:
        self.clock.set(value)
        restarted = self.new_application()
        restarted.initialize()
        return restarted, restarted.refill.status()

    def test_executable_window_is_missed_without_an_in_window_poll(self):
        initial = self.configure()
        plan = self.active_plans(date(2026, 7, 25))[0]
        self.assertEqual(initial["status"], "window_pending")
        self.assertEqual(plan["need_detected"], 1)
        self.assertEqual(plan["executable"], 1)
        self.assertEqual(plan["expected_transfer_ml"], 2_000)
        self.assertEqual(plan["observed_in_window"], 0)

        restarted, status = self.restart_at(
            datetime(2026, 7, 25, 2, 5, tzinfo=BERLIN)
        )

        self.assertEqual(status["status"], "window_missed")
        self.assertEqual(status["missed_windows"], ["01:00"])
        self.assertTrue(status["missed_today"])
        self.assertEqual(
            status["missed_window_details"][0]["window_key"],
            plan["window_key"],
        )
        persisted = self.active_plans(
            date(2026, 7, 25),
            application=restarted,
        )[0]
        self.assertEqual(persisted["created_at"], plan["created_at"])

    def test_exact_window_end_preserves_last_executable_snapshot(self):
        self.configure()
        initial = self.active_plans(date(2026, 7, 25))[0]
        self.assertEqual(initial["executable"], 1)
        self.assertEqual(initial["expected_transfer_ml"], 2_000)

        self.clock.set(
            datetime(2026, 7, 25, 2, 0, tzinfo=BERLIN)
        )
        at_end = self.application.refill.status()
        persisted_at_end = self.active_plans(date(2026, 7, 25))[0]

        self.assertFalse(at_end["schedule_due"])
        self.assertFalse(at_end["missed"])
        self.assertEqual(persisted_at_end["executable"], 1)
        self.assertEqual(
            persisted_at_end["expected_transfer_ml"],
            2_000,
        )
        self.assertEqual(
            persisted_at_end["last_checked_at"],
            initial["last_checked_at"],
        )

        self.clock.set(
            datetime(2026, 7, 25, 2, 0, 1, tzinfo=BERLIN)
        )
        after_end = self.application.refill.status()

        self.assertEqual(after_end["status"], "window_missed")
        self.assertEqual(after_end["missed_windows"], ["01:00"])

    def test_partial_transfer_does_not_hide_missed_status(self):
        self.configure(refill_current_ml=350)
        plan = self.active_plans(date(2026, 7, 25))[0]
        self.assertEqual(plan["expected_transfer_ml"], 350)
        self.assertEqual(plan["executable"], 1)

        _restarted, status = self.restart_at(
            datetime(2026, 7, 25, 2, 5, tzinfo=BERLIN)
        )

        self.assertEqual(status["status"], "window_missed")
        self.assertEqual(status["severity"], "critical")
        self.assertEqual(
            status["missed_window_details"][0][
                "expected_transfer_ml"
            ],
            350,
        )

    def test_first_missed_window_has_priority_while_second_can_run(self):
        self.configure(
            windows=[
                {"start": "01:00", "end": "01:30"},
                {"start": "02:00", "end": "03:00"},
            ]
        )
        self.clock.set(
            datetime(2026, 7, 25, 2, 10, tzinfo=BERLIN)
        )

        status = self.application.refill.status()

        self.assertEqual(status["status"], "window_missed")
        self.assertEqual(status["missed_windows"], ["01:00"])
        self.assertTrue(status["run_now"])
        self.assertEqual(status["active_window"], "02:00")

    def test_impossible_windows_are_never_classified_as_missed(self):
        scenarios = {
            "empty": {
                "refill_current_ml": 0,
                "expected_status": "refill_tank_empty",
            },
            "disabled": {
                "enabled": False,
                "expected_status": "disabled",
            },
            "missing_flow": {
                "pump_ml_per_min": 0,
                "expected_status": "pump_flow_missing",
            },
        }
        for name, values in scenarios.items():
            with self.subTest(name=name):
                case_root = (
                    ROOT / f".refill-impossible-{uuid.uuid4().hex}"
                )
                case_root.mkdir()
                previous_root = self.root
                previous_application = self.application
                try:
                    self.root = case_root
                    self.application = self.new_application()
                    self.application.initialize()
                    self.configure(
                        refill_current_ml=values.get(
                            "refill_current_ml",
                            30_000,
                        ),
                        pump_ml_per_min=values.get(
                            "pump_ml_per_min",
                            1_000,
                        ),
                        enabled=values.get("enabled", True),
                    )
                    self.clock.set(
                        datetime(
                            2026,
                            7,
                            25,
                            2,
                            5,
                            tzinfo=BERLIN,
                        )
                    )
                    status = self.application.refill.status()
                    self.assertEqual(
                        status["status"],
                        values["expected_status"],
                    )
                    self.assertFalse(status["missed_today"])
                    self.assertEqual(status["missed_window_details"], [])
                finally:
                    self.application = previous_application
                    self.root = previous_root
                    shutil.rmtree(case_root, ignore_errors=True)
                    self.clock.set(
                        datetime(
                            2026,
                            7,
                            25,
                            0,
                            15,
                            tzinfo=BERLIN,
                        )
                    )

    def test_cooldown_covering_window_is_not_missed(self):
        with self.application.database.connection() as conn:
            self.application.events.insert_refill(
                conn,
                ran_at=datetime(
                    2026,
                    7,
                    25,
                    0,
                    30,
                    tzinfo=BERLIN,
                ).astimezone(timezone.utc).isoformat(),
                target_date="2026-07-25",
                requested_ml=500,
                transferred_ml=500,
                duration_seconds=30,
                window_label="previous",
                source="test",
                run_id="previous-refill",
            )
        self.configure(minimum_interval_minutes=180)
        plan = self.active_plans(date(2026, 7, 25))[0]
        self.assertEqual(plan["executable"], 0)
        self.assertEqual(plan["blocking_reason"], "cooldown")

        _restarted, status = self.restart_at(
            datetime(2026, 7, 25, 2, 5, tzinfo=BERLIN)
        )

        self.assertFalse(status["missed_today"])
        self.assertNotEqual(status["status"], "window_missed")

    def test_successful_event_fulfils_plan_across_restart(self):
        self.configure()
        plan = self.active_plans(date(2026, 7, 25))[0]
        with self.application.database.connection() as conn:
            self.application.events.insert_refill(
                conn,
                ran_at=datetime(
                    2026,
                    7,
                    25,
                    1,
                    15,
                    tzinfo=BERLIN,
                ).astimezone(timezone.utc).isoformat(),
                target_date="2026-07-25",
                requested_ml=2_000,
                transferred_ml=2_000,
                duration_seconds=120,
                window_label="01:00",
                source="test",
                run_id="successful-refill",
            )

        restarted, status = self.restart_at(
            datetime(2026, 7, 25, 2, 5, tzinfo=BERLIN)
        )

        self.assertFalse(status["missed_today"])
        self.assertNotEqual(status["status"], "window_missed")
        persisted = {
            item["window_key"]: item
            for item in self.active_plans(
                date(2026, 7, 25),
                application=restarted,
            )
        }[plan["window_key"]]
        self.assertTrue(persisted["fulfilled_at"])

    def test_refill_transaction_survives_crash_before_plan_refresh(self):
        self.configure(
            windows=[
                {"start": "01:00", "end": "01:30"},
                {"start": "02:00", "end": "02:30"},
            ],
            minimum_interval_minutes=180,
        )
        before = self.active_plans(date(2026, 7, 25))
        self.assertEqual([item["executable"] for item in before], [1, 1])

        self.clock.set(
            datetime(2026, 7, 25, 1, 10, tzinfo=BERLIN)
        )

        original_reconcile = (
            self.application.events.reconcile_refill_plans_after_event
        )

        def simulate_transaction_failure(*_args, **_kwargs) -> None:
            raise RuntimeError("simulated crash during transaction")

        self.application.events.reconcile_refill_plans_after_event = (
            simulate_transaction_failure
        )
        tank_before = self.application.tanks.balcony()
        with self.assertRaisesRegex(RuntimeError, "simulated crash"):
            self.application.mark_refill_run(
                source="test",
                run_id="refill-before-crash",
            )
        self.application.events.reconcile_refill_plans_after_event = (
            original_reconcile
        )
        with self.application.database.connection() as conn:
            self.assertIsNone(
                self.application.events.refill_by_run_id(
                    "refill-before-crash",
                    conn=conn,
                )
            )
            self.assertEqual(
                self.application.tanks.balcony(conn=conn),
                tank_before,
            )
        self.assertEqual(
            self.application.get_refill_run(
                "refill-before-crash"
            )["status"],
            "running",
        )

        self.application.complete_refill_run("refill-before-crash")

        # A status calculation may have started before completion committed.
        # Its newer timestamp must not restore the stale executable snapshot.
        stale_second = before[1]
        self.application.events.upsert_refill_window_plan(
            window_key=str(stale_second["window_key"]),
            target_date=str(stale_second["target_date"]),
            window_label=str(stale_second["window_label"]),
            window_start=str(stale_second["window_start"]),
            window_end=str(stale_second["window_end"]),
            need_detected=True,
            executable=True,
            expected_transfer_ml=2_000,
            blocking_reason="",
            observed_in_window=False,
            checked_at=datetime(
                2026,
                7,
                25,
                1,
                10,
                1,
                tzinfo=BERLIN,
            ).astimezone(timezone.utc).isoformat(),
            cooldown_minutes=180,
        )

        persisted_after_commit = self.active_plans(date(2026, 7, 25))
        self.assertTrue(persisted_after_commit[0]["fulfilled_at"])
        self.assertEqual(persisted_after_commit[1]["executable"], 0)
        self.assertEqual(
            persisted_after_commit[1]["blocking_reason"],
            "cooldown",
        )
        self.assertEqual(
            persisted_after_commit[1]["expected_transfer_ml"],
            0,
        )
        with self.application.database.connection() as conn:
            event_count = conn.execute(
                """
                SELECT COUNT(*) AS count
                FROM refill_events
                WHERE run_id = 'refill-before-crash'
                """
            ).fetchone()["count"]
            tank = self.application.tanks.balcony(conn=conn)
        self.assertEqual(event_count, 1)
        self.assertEqual(tank["tank_current_ml"], 10_000)
        self.assertEqual(tank["refill_tank_current_ml"], 28_000)

        restarted, status = self.restart_at(
            datetime(2026, 7, 25, 2, 35, tzinfo=BERLIN)
        )

        self.assertFalse(status["missed"])
        self.assertNotEqual(status["status"], "window_missed")
        persisted_after_restart = self.active_plans(
            date(2026, 7, 25),
            application=restarted,
        )
        self.assertTrue(persisted_after_restart[0]["fulfilled_at"])
        self.assertEqual(persisted_after_restart[1]["executable"], 0)
        self.assertEqual(
            persisted_after_restart[1]["blocking_reason"],
            "cooldown",
        )

    def test_restart_inside_window_updates_same_plan_idempotently(self):
        self.configure()
        before = self.active_plans(date(2026, 7, 25))[0]

        restarted, status = self.restart_at(
            datetime(2026, 7, 25, 1, 15, tzinfo=BERLIN)
        )
        restarted.initialize()
        after = self.active_plans(
            date(2026, 7, 25),
            application=restarted,
        )

        self.assertEqual(status["status"], "ready")
        self.assertEqual(len(after), 1)
        self.assertEqual(after[0]["window_key"], before["window_key"])
        self.assertEqual(after[0]["created_at"], before["created_at"])
        self.assertEqual(after[0]["observed_in_window"], 1)

    def test_main_tank_fill_before_window_removes_persisted_need(self):
        self.configure(main_current_ml=8_000)
        before = self.active_plans(date(2026, 7, 25))[0]
        self.assertEqual(before["need_detected"], 1)
        self.assertEqual(before["executable"], 1)

        self.clock.set(
            datetime(2026, 7, 25, 0, 30, tzinfo=BERLIN)
        )
        self.application.fill_tank("main")
        after_fill = self.active_plans(date(2026, 7, 25))[0]
        self.assertEqual(after_fill["window_key"], before["window_key"])
        self.assertEqual(after_fill["need_detected"], 0)
        self.assertEqual(after_fill["executable"], 0)
        self.assertEqual(after_fill["expected_transfer_ml"], 0)
        self.assertEqual(after_fill["blocking_reason"], "main_tank_full")

        _restarted, status = self.restart_at(
            datetime(2026, 7, 25, 2, 5, tzinfo=BERLIN)
        )

        self.assertEqual(status["status"], "main_tank_full")
        self.assertFalse(status["missed"])
        self.assertEqual(status["missed_window_details"], [])

    def test_newer_plan_snapshot_wins_parallel_and_late_stale_updates(self):
        key = (
            "2026-07-27|2026-07-26T23:00:00+00:00|"
            "2026-07-27T00:00:00+00:00"
        )
        common = {
            "window_key": key,
            "target_date": "2026-07-27",
            "window_label": "01:00",
            "window_start": "2026-07-26T23:00:00+00:00",
            "window_end": "2026-07-27T00:00:00+00:00",
            "observed_in_window": False,
        }
        snapshots = [
            {
                **common,
                "need_detected": True,
                "executable": True,
                "expected_transfer_ml": 2_000,
                "blocking_reason": "",
                "checked_at": "2026-07-26T20:00:00+00:00",
            },
            {
                **common,
                "need_detected": False,
                "executable": False,
                "expected_transfer_ml": 0,
                "blocking_reason": "main_tank_full",
                "checked_at": "2026-07-26T20:01:00+00:00",
            },
        ]
        barrier = threading.Barrier(2)

        def persist(snapshot: dict) -> None:
            barrier.wait()
            self.application.events.upsert_refill_window_plan(**snapshot)

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(persist, item) for item in snapshots]
            for future in futures:
                future.result()

        # A stale calculation can also arrive after the newer transaction.
        self.application.events.upsert_refill_window_plan(**snapshots[0])
        self.application.events.cancel_obsolete_refill_window_plans(
            target_date="2026-07-27",
            active_window_keys=set(),
            after_at=snapshots[0]["checked_at"],
            cancelled_at=snapshots[0]["checked_at"],
        )
        persisted = {
            item["window_key"]: item
            for item in self.application.events.refill_window_plans(
                target_dates=["2026-07-27"]
            )
        }[key]
        self.assertEqual(persisted["last_checked_at"], snapshots[1]["checked_at"])
        self.assertEqual(persisted["need_detected"], 0)
        self.assertEqual(persisted["executable"], 0)
        self.assertEqual(persisted["expected_transfer_ml"], 0)
        self.assertEqual(persisted["blocking_reason"], "main_tank_full")
        self.assertIsNone(persisted["cancelled_at"])

    def test_history_survives_config_change_and_future_plan_is_cancelled(self):
        self.configure(
            windows=[{"start": "01:00", "end": "02:00"}]
        )
        historical = self.active_plans(date(2026, 7, 25))[0]
        future_old = self.active_plans(date(2026, 7, 26))[0]

        self.clock.set(
            datetime(2026, 7, 25, 2, 5, tzinfo=BERLIN)
        )
        config = self.application.settings.planner_config()
        config["refill_windows"] = [
            {"start": "04:00", "end": "05:00"}
        ]
        self.application.settings.save_planner_config(config)
        status = self.application.refill.status()

        self.assertEqual(status["status"], "window_missed")
        all_today = self.application.events.refill_window_plans(
            target_dates=["2026-07-25"]
        )
        persisted_history = {
            item["window_key"]: item for item in all_today
        }[historical["window_key"]]
        self.assertIsNone(persisted_history["cancelled_at"])
        self.assertEqual(
            persisted_history["window_start"],
            historical["window_start"],
        )

        tomorrow = self.application.events.refill_window_plans(
            target_dates=["2026-07-26"]
        )
        by_key = {item["window_key"]: item for item in tomorrow}
        self.assertTrue(by_key[future_old["window_key"]]["cancelled_at"])
        active = [item for item in tomorrow if not item["cancelled_at"]]
        self.assertEqual([item["window_label"] for item in active], ["04:00"])

    def test_window_keys_and_durations_are_dst_safe_in_both_directions(self):
        scenarios = [
            (
                datetime(2026, 3, 29, 0, 15, tzinfo=BERLIN),
                timedelta(hours=1),
            ),
            (
                datetime(2026, 10, 25, 0, 15, tzinfo=BERLIN),
                timedelta(hours=3),
            ),
        ]
        for start, expected_duration in scenarios:
            with self.subTest(day=start.date()):
                case_root = ROOT / f".refill-dst-{uuid.uuid4().hex}"
                case_root.mkdir()
                previous_root = self.root
                previous_application = self.application
                try:
                    self.root = case_root
                    self.clock.set(start)
                    self.application = self.new_application()
                    self.application.initialize()
                    self.configure(
                        windows=[
                            {"start": "01:00", "end": "03:00"}
                        ]
                    )
                    before = self.active_plans(start.date())[0]
                    duration = (
                        datetime.fromisoformat(before["window_end"])
                        - datetime.fromisoformat(before["window_start"])
                    )
                    self.assertEqual(duration, expected_duration)

                    restarted = self.new_application()
                    restarted.initialize()
                    after = self.active_plans(
                        start.date(),
                        application=restarted,
                    )
                    self.assertEqual(len(after), 1)
                    self.assertEqual(
                        after[0]["window_key"],
                        before["window_key"],
                    )
                finally:
                    self.application = previous_application
                    self.root = previous_root
                    shutil.rmtree(case_root, ignore_errors=True)
        self.clock.set(
            datetime(2026, 7, 25, 0, 15, tzinfo=BERLIN)
        )

    def test_worker_interval_is_bounded_in_config_and_worker(self):
        for accepted in (10, 60, 3_600):
            with self.subTest(accepted=accepted):
                config = validate_planner_config(
                    {"notification_worker_interval_seconds": accepted}
                )
                self.assertEqual(
                    config["notification_worker_interval_seconds"],
                    accepted,
                )
        for rejected in (0, 9, 3_601, 86_400):
            with self.subTest(rejected=rejected):
                with self.assertRaises(ValueError):
                    validate_planner_config(
                        {
                            "notification_worker_interval_seconds": (
                                rejected
                            )
                        }
                    )
                with self.assertRaises(ValueError):
                    NotificationWorker(
                        lambda: [],
                        object(),
                        rejected,
                    )

    def test_legacy_worker_interval_is_normalized_during_startup(self):
        legacy = self.application.settings.planner_config()
        legacy["notification_worker_interval_seconds"] = 7_200
        self.application.settings.set(
            "planner_config",
            json.dumps(legacy),
        )

        restarted = self.new_application()
        restarted.initialize()

        config = restarted.settings.planner_config()
        persisted = json.loads(
            restarted.settings.get("planner_config")
        )
        self.assertEqual(
            config["notification_worker_interval_seconds"],
            3_600,
        )
        self.assertEqual(
            persisted["notification_worker_interval_seconds"],
            3_600,
        )

    def test_legacy_observation_migrates_additively_and_idempotently(self):
        with self.application.database.connection() as conn:
            conn.execute(
                """
                INSERT INTO refill_window_observations(
                    target_date, window_label, window_start, window_end,
                    need_detected, eligible, blocking_reason, observed_at
                )
                VALUES (?, ?, ?, ?, 1, 1, '', ?)
                """,
                (
                    "2026-07-24",
                    "legacy",
                    "2026-07-24T01:00:00+02:00",
                    "2026-07-24T02:00:00+02:00",
                    "2026-07-24T01:15:00+02:00",
                ),
            )

        self.application.initialize()
        self.application.initialize()
        migrated = [
            item
            for item in self.application.events.refill_window_plans(
                target_dates=["2026-07-24"]
            )
            if item["window_label"] == "legacy"
        ]

        self.assertEqual(len(migrated), 1)
        self.assertEqual(migrated[0]["need_detected"], 1)
        self.assertEqual(migrated[0]["executable"], 1)
        self.assertEqual(migrated[0]["expected_transfer_ml"], 1)
        self.assertEqual(migrated[0]["observed_in_window"], 1)


if __name__ == "__main__":
    unittest.main()
