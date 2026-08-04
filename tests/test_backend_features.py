import json
import os
import sqlite3
import ssl
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import server
from watering_backend.forecast import simulate_tanks
from watering_backend.notifications import NotificationService
from watering_backend.plant_model import (
    POT_IRRIGATION_EFFICIENCY,
    finish_daily_need,
)
from watering_backend.schema import SCHEMA_VERSION, migrate


class BackendFeatureTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.original_data_dir = server.DATA_DIR
        self.original_db_path = server.DB_PATH
        server.DATA_DIR = Path(self.tmp.name)
        server.DB_PATH = Path(self.tmp.name) / "watering.sqlite3"
        server.init_db()

    def tearDown(self):
        server.stop_notification_worker()
        server.DATA_DIR = self.original_data_dir
        server.DB_PATH = self.original_db_path
        self.tmp.cleanup()

    def _weather_payload(self):
        return {
            "current": {
                "time": "2026-07-25T07:00",
                "temperature_2m": 12,
                "rain": 0,
                "precipitation": 0,
                "wind_speed_10m": 2,
            },
            "hourly": {
                "time": ["2026-07-25T07:00"],
                "temperature_2m": [12],
                "precipitation": [0],
                "rain": [0],
                "wind_speed_10m": [2],
            },
            "daily": {
                "time": ["2026-07-25"],
                "temperature_2m_max": [31],
                "precipitation_sum": [1.5],
                "wind_speed_10m_max": [18],
                "sunshine_duration": [28800],
                "et0_fao_evapotranspiration": [5.2],
            },
        }

    def _weather_response(self):
        response = MagicMock()
        response.read.return_value = json.dumps(self._weather_payload()).encode()
        response.__enter__.return_value = response
        return response

    def test_open_meteo_planning_uses_daily_not_current_values(self):
        response = self._weather_response()
        with patch("server.urlopen", return_value=response):
            weather = server.fetch_weather(server.get_state()["balcony"])

        self.assertEqual(weather["temperature_c"], 31)
        self.assertEqual(weather["wind_kmh"], 18)
        self.assertEqual(weather["current"]["temperature_c"], 12)
        self.assertEqual(weather["planning_day"]["et0_mm"], 5.2)
        self.assertFalse(weather["simulation"])
        self.assertTrue(server.weather_diagnostics()["last_successful_fetch_at"])

    def test_weather_cache_hit_force_parallel_and_error_fallback(self):
        balcony = server.get_state()["balcony"]
        opener = MagicMock(return_value=self._weather_response())
        with patch("server.urlopen", opener):
            first = server.fetch_weather(balcony)
            cached = server.fetch_weather(balcony)
            forced = server.fetch_weather(balcony, force=True)
        self.assertEqual(opener.call_count, 2)
        self.assertFalse(first["cache_hit"])
        self.assertTrue(cached["cache_hit"])
        self.assertFalse(forced["cache_hit"])

        server.delete_setting("weather_cache")
        slow_opener = MagicMock()

        def delayed_response(*_args, **_kwargs):
            time.sleep(0.05)
            return self._weather_response()

        slow_opener.side_effect = delayed_response
        with patch("server.urlopen", slow_opener):
            with ThreadPoolExecutor(max_workers=5) as pool:
                results = list(pool.map(lambda _index: server.fetch_weather(balcony), range(5)))
        self.assertEqual(slow_opener.call_count, 1)
        self.assertEqual(len(results), 5)

        cache = json.loads(server.get_setting("weather_cache"))
        cache["weather"]["fetched_at"] = (
            datetime.now(timezone.utc) - timedelta(minutes=30)
        ).isoformat()
        server.set_setting("weather_cache", json.dumps(cache))
        cache_only_opener = MagicMock(
            side_effect=AssertionError("cache-only must not use network")
        )
        with patch("server.urlopen", cache_only_opener):
            stored = server.cached_weather(balcony)
        cache_only_opener.assert_not_called()
        self.assertTrue(stored["cache_hit"])
        self.assertEqual(stored["fetched_at"], cache["weather"]["fetched_at"])

        offline = MagicMock(side_effect=OSError("offline"))
        with patch("server.urlopen", offline):
            fallback = server.fetch_weather(balcony, force=True)
            throttled = server.fetch_weather(balcony)
            self.assertEqual(offline.call_count, 1)
            server.set_setting(
                "last_weather_fetch_attempt_at",
                (
                    datetime.now(timezone.utc) - timedelta(minutes=6)
                ).isoformat(),
            )
            retried = server.fetch_weather(balcony)
        self.assertEqual(offline.call_count, 2)
        self.assertTrue(fallback["cache_fallback"])
        self.assertTrue(throttled["cache_fallback"])
        self.assertTrue(retried["cache_fallback"])
        self.assertIn("voruebergehend", fallback["weather_error"])
        self.assertTrue(server.weather_diagnostics()["last_error"])

    def test_tls_certificate_error_never_retries_unverified(self):
        opener = MagicMock(side_effect=ssl.SSLCertVerificationError(1, "certificate failed"))
        with patch("server.urlopen", opener):
            with self.assertRaisesRegex(ValueError, "TLS-Zertifikat"):
                server.fetch_weather(server.get_state()["balcony"], force=True)
        self.assertEqual(opener.call_count, 1)
        self.assertNotIn("certificate failed", server.weather_diagnostics()["last_error"])

    def test_manual_weather_is_explicit_simulation(self):
        weather = server.weather_from_payload({"temperature_c": 7, "rain_mm": 0})
        self.assertEqual(weather["mode"], "simulation")
        self.assertTrue(weather["simulation"])

    def test_weather_staleness_uses_persistent_configured_threshold(self):
        config = server.planner_config()
        config["weather_stale_after_minutes"] = 5
        server.save_planner_config(config)
        server.set_setting(
            "last_successful_weather_fetch_at",
            (datetime.now(timezone.utc) - timedelta(minutes=6)).isoformat(),
        )
        self.assertTrue(server.weather_diagnostics()["stale"])
        server.set_setting("last_successful_weather_fetch_at", datetime.now(timezone.utc).isoformat())
        self.assertFalse(server.weather_diagnostics()["stale"])

    def test_local_day_bounds_cover_summer_and_winter_clock_changes(self):
        tzinfo = ZoneInfo("Europe/Berlin")
        summer_start, summer_end = server.local_day_utc_bounds(date(2026, 3, 29), tzinfo)
        winter_start, winter_end = server.local_day_utc_bounds(date(2026, 10, 25), tzinfo)
        self.assertEqual(summer_end - summer_start, timedelta(hours=23))
        self.assertEqual(winter_end - winter_start, timedelta(hours=25))

    def _simulate_refill(self, *, watering_at=None, **overrides):
        tzinfo = ZoneInfo("Europe/Berlin")
        values = {
            "now": datetime(2026, 7, 25, 0, 0, tzinfo=tzinfo),
            "watering_events": (
                [{"at": watering_at, "consumed_ml": 600}]
                if watering_at
                else []
            ),
            "refill_windows": [
                (
                    datetime(2026, 7, 25, 1, 0, tzinfo=tzinfo),
                    datetime(2026, 7, 25, 1, 30, tzinfo=tzinfo),
                )
            ],
            "main_current_ml": 0,
            "main_capacity_ml": 2000,
            "refill_current_ml": 5000,
            "refill_capacity_ml": 5000,
            "refill_enabled": True,
            "refill_pump_ml_per_min": 100,
            "refill_min_interval_minutes": 0,
            "refill_strategy": "target",
            "refill_fraction": 0.5,
            "refill_target_ml": 1000,
        }
        values.update(overrides)
        return simulate_tanks(**values)

    def test_watering_before_refill_start_is_unserved(self):
        result = self._simulate_refill(
            watering_at=datetime(2026, 7, 25, 0, 30, tzinfo=ZoneInfo("Europe/Berlin"))
        )
        watering = next(item for item in result["forecast_events"] if item["event_type"] == "watering")
        self.assertEqual(watering["status"], "unserved")
        self.assertEqual(watering["main_tank_before_ml"], 0)

    def test_watering_during_refill_only_sees_transferred_share(self):
        result = self._simulate_refill(
            watering_at=datetime(2026, 7, 25, 1, 5, tzinfo=ZoneInfo("Europe/Berlin"))
        )
        watering = next(item for item in result["forecast_events"] if item["event_type"] == "watering")
        refill = next(item for item in result["forecast_events"] if item["event_type"] == "refill")
        self.assertEqual(watering["main_tank_before_ml"], 500)
        self.assertEqual(watering["status"], "unserved")
        self.assertEqual(refill["transferred_ml"], 1000)

    def test_watering_at_refill_completion_sees_full_transfer(self):
        result = self._simulate_refill(
            watering_at=datetime(2026, 7, 25, 1, 10, tzinfo=ZoneInfo("Europe/Berlin"))
        )
        watering = next(item for item in result["forecast_events"] if item["event_type"] == "watering")
        self.assertEqual(watering["main_tank_before_ml"], 1000)
        self.assertEqual(watering["status"], "successful")

    def test_refill_is_limited_by_window_end(self):
        tzinfo = ZoneInfo("Europe/Berlin")
        result = self._simulate_refill(
            refill_target_ml=2000,
            refill_windows=[
                (
                    datetime(2026, 7, 25, 1, 0, tzinfo=tzinfo),
                    datetime(2026, 7, 25, 1, 10, tzinfo=tzinfo),
                )
            ],
        )
        refill = next(item for item in result["forecast_events"] if item["event_type"] == "refill")
        self.assertEqual(refill["transferred_ml"], 1000)
        self.assertTrue(refill["limited_by_window"])

    def test_refill_is_limited_by_main_tank_capacity(self):
        result = self._simulate_refill(main_current_ml=1500, refill_target_ml=2000)
        refill = next(item for item in result["forecast_events"] if item["event_type"] == "refill")
        self.assertEqual(refill["transferred_ml"], 500)
        self.assertEqual(result["main_tank_after_forecast_ml"], 2000)

    def test_refill_is_limited_by_reserve_tank(self):
        result = self._simulate_refill(refill_current_ml=300)
        refill = next(item for item in result["forecast_events"] if item["event_type"] == "refill")
        self.assertEqual(refill["transferred_ml"], 300)
        self.assertEqual(result["refill_tank_after_forecast_ml"], 0)

    def test_refill_cooldown_blocks_second_window(self):
        tzinfo = ZoneInfo("Europe/Berlin")
        result = self._simulate_refill(
            refill_windows=[
                (
                    datetime(2026, 7, 25, 1, 0, tzinfo=tzinfo),
                    datetime(2026, 7, 25, 1, 15, tzinfo=tzinfo),
                ),
                (
                    datetime(2026, 7, 25, 1, 20, tzinfo=tzinfo),
                    datetime(2026, 7, 25, 1, 40, tzinfo=tzinfo),
                ),
            ],
            refill_min_interval_minutes=60,
            refill_target_ml=500,
        )
        refills = [item for item in result["forecast_events"] if item["event_type"] == "refill"]
        self.assertEqual(refills[0]["transferred_ml"], 500)
        self.assertEqual(refills[1]["blocked_reason"], "minimum_interval")

    def test_disabled_refill_automation_transfers_nothing(self):
        result = self._simulate_refill(refill_enabled=False)
        refill = next(item for item in result["forecast_events"] if item["event_type"] == "refill")
        self.assertEqual(refill["transferred_ml"], 0)
        self.assertEqual(refill["blocked_reason"], "disabled")

    def test_refill_status_open_missed_and_persistent_notification_key(self):
        with server.connect() as conn:
            conn.execute(
                """
                UPDATE balcony_settings
                SET tank_current_ml = tank_capacity_ml - 2000,
                    refill_tank_current_ml = refill_tank_capacity_ml,
                    refill_pump_ml_per_min = 1000
                WHERE id = 1
                """
            )
        open_at = datetime(2026, 7, 25, 1, 30, tzinfo=ZoneInfo("Europe/Berlin"))
        after_end = datetime(2026, 7, 25, 2, 1, tzinfo=ZoneInfo("Europe/Berlin"))
        with patch("server.local_now", return_value=open_at):
            opened = server.refill_status(server.get_state()["balcony"])
        self.assertFalse(opened["missed_today"])
        self.assertEqual(opened["status"], "ready")

        server.init_db()
        manual_weather = {
            "source": "manual",
            "temperature_c": 22,
            "rain_mm": 0,
            "wind_kmh": 5,
            "sunshine_hours": 6,
            "et0_mm": 3,
        }
        with patch("server.local_now", return_value=after_end), patch(
            "server.fetch_weather", return_value=manual_weather
        ):
            missed = server.refill_status(server.get_state()["balcony"])
            conditions = server.notification_conditions()
        self.assertEqual(missed["status"], "window_missed")
        self.assertTrue(missed["blocked"])
        self.assertEqual(missed["severity"], "critical")
        self.assertIn(
            "refill_run_missed:2026-07-25:01:00",
            {item["alert_key"] for item in conditions},
        )

    def test_refill_status_does_not_mark_impossible_or_completed_window_missed(self):
        with server.connect() as conn:
            conn.execute(
                """
                UPDATE balcony_settings
                SET tank_current_ml = tank_capacity_ml - 2000,
                    refill_tank_current_ml = 0
                WHERE id = 1
                """
            )
        open_at = datetime(2026, 7, 25, 1, 30, tzinfo=ZoneInfo("Europe/Berlin"))
        after_end = datetime(2026, 7, 25, 2, 1, tzinfo=ZoneInfo("Europe/Berlin"))
        with patch("server.local_now", return_value=open_at):
            blocked = server.refill_status(server.get_state()["balcony"])
        self.assertEqual(blocked["status"], "refill_tank_empty")
        self.assertTrue(blocked["blocked"])
        with patch("server.local_now", return_value=after_end):
            after = server.refill_status(server.get_state()["balcony"])
        self.assertFalse(after["missed_today"])

        server.save_refill_automation_enabled(False)
        with patch("server.local_now", return_value=open_at):
            disabled = server.refill_status(server.get_state()["balcony"])
        self.assertEqual(disabled["status"], "disabled")
        self.assertFalse(disabled["blocked"])

    def test_tank_simulation_uses_multiple_refill_windows_chronologically(self):
        tzinfo = ZoneInfo("Europe/Berlin")
        now = datetime(2026, 7, 25, 0, 0, tzinfo=tzinfo)
        result = simulate_tanks(
            now=now,
            watering_events=[
                {"at": datetime(2026, 7, 25, 2, 0, tzinfo=tzinfo), "consumed_ml": 900, "delivered_ml": 800},
                {"at": datetime(2026, 7, 25, 8, 0, tzinfo=tzinfo), "consumed_ml": 900, "delivered_ml": 800},
            ],
            refill_windows=[
                (datetime(2026, 7, 25, 1, 0, tzinfo=tzinfo), datetime(2026, 7, 25, 1, 30, tzinfo=tzinfo)),
                (datetime(2026, 7, 25, 6, 0, tzinfo=tzinfo), datetime(2026, 7, 25, 6, 30, tzinfo=tzinfo)),
            ],
            main_current_ml=500,
            main_capacity_ml=2000,
            refill_current_ml=3000,
            refill_capacity_ml=3000,
            refill_enabled=True,
            refill_pump_ml_per_min=100,
            refill_min_interval_minutes=180,
            refill_strategy="fraction",
            refill_fraction=0.5,
            refill_target_ml=0,
        )
        self.assertEqual([item["event_type"] for item in result["forecast_events"]], ["refill", "watering", "refill", "watering"])
        self.assertTrue(all(item["status"] != "unserved" for item in result["forecast_events"]))
        self.assertEqual(result["refill_tank_after_forecast_ml"], 1425)

    def test_full_reserve_cannot_supply_watering_before_refill(self):
        tzinfo = ZoneInfo("Europe/Berlin")
        now = datetime(2026, 7, 25, 7, 0, tzinfo=tzinfo)
        result = simulate_tanks(
            now=now,
            watering_events=[
                {"at": datetime(2026, 7, 25, 8, 0, tzinfo=tzinfo), "consumed_ml": 1000},
                {"at": datetime(2026, 7, 25, 10, 0, tzinfo=tzinfo), "consumed_ml": 1000},
            ],
            refill_windows=[
                (datetime(2026, 7, 25, 9, 0, tzinfo=tzinfo), datetime(2026, 7, 25, 9, 30, tzinfo=tzinfo))
            ],
            main_current_ml=0,
            main_capacity_ml=2000,
            refill_current_ml=5000,
            refill_capacity_ml=5000,
            refill_enabled=True,
            refill_pump_ml_per_min=100,
            refill_min_interval_minutes=0,
            refill_strategy="target",
            refill_fraction=0.5,
            refill_target_ml=2000,
        )
        watering = [item for item in result["forecast_events"] if item["event_type"] == "watering"]
        self.assertEqual(watering[0]["status"], "unserved")
        self.assertEqual(watering[1]["status"], "successful")
        self.assertEqual(result["first_unserved_watering_at"], watering[0]["at"])
        self.assertEqual(result["last_supported_watering_at"], "")

    def test_manual_and_recorded_run_require_calibrated_consumption(self):
        server.save_main_pump_calibration_factor(2)
        with server.connect() as conn:
            conn.execute("UPDATE balcony_settings SET tank_current_ml = 150 WHERE id = 1")
        result = {
            "plants": [{"id": 1}],
            "pump": {"delivered_per_cycle_ml": 100, "consumed_per_cycle_ml": 200},
            "tank": {"current_ml": 150},
        }
        self.assertFalse(server.manual_run_status(result)["available"])
        with self.assertRaisesRegex(ValueError, "kalibrierten Verbrauch"):
            server.mark_run(100, 20, 0, run_id="calibrated-run")

    def test_watering_run_id_is_idempotent(self):
        before = server.get_state()["balcony"]["tank_current_ml"]
        first = server.mark_run(100, 20, 0, run_id="watering-123")
        second = server.mark_run(100, 20, 0, run_id="watering-123")
        after = server.get_state()["balcony"]["tank_current_ml"]
        self.assertFalse(first["idempotent_replay"])
        self.assertTrue(second["idempotent_replay"])
        self.assertEqual(after, before - 100)

    def test_parallel_duplicate_run_id_changes_tank_once(self):
        before = server.get_state()["balcony"]["tank_current_ml"]

        def book():
            return server.mark_run(120, 20, 0, run_id="parallel-123")

        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: book(), range(4)))
        after = server.get_state()["balcony"]["tank_current_ml"]
        self.assertEqual(after, before - 120)
        self.assertEqual(sum(not item["idempotent_replay"] for item in results), 1)

    def test_refill_run_id_is_idempotent(self):
        now = datetime(2026, 7, 25, 1, 5, tzinfo=ZoneInfo("Europe/Berlin"))
        with server.connect() as conn:
            conn.execute(
                "UPDATE balcony_settings SET tank_current_ml = tank_capacity_ml - 2000 WHERE id = 1"
            )
        before = server.get_state()["balcony"]
        with patch("server.local_now", return_value=now):
            first = server.mark_refill_run(run_id="refill-123")
            second = server.mark_refill_run(run_id="refill-123")
        after = server.get_state()["balcony"]
        self.assertFalse(first["idempotent_replay"])
        self.assertTrue(second["idempotent_replay"])
        self.assertEqual(after["tank_current_ml"], before["tank_current_ml"] + 1000)
        self.assertEqual(after["refill_tank_current_ml"], before["refill_tank_current_ml"] - 1000)

    def test_global_optimizer_reserves_scarce_outlet_for_constrained_plant(self):
        plants = [
            {"id": 1, "name": "flex", "need_ml": 100, "pot_liters": 10, "pot_type": "overflow", "drought_sensitivity": 1},
            {"id": 2, "name": "fixed", "need_ml": 100, "pot_liters": 10, "pot_type": "overflow", "drought_sensitivity": 1},
        ]

        def option(plant_id, name, outlet_id, score):
            return {
                "plant_id": plant_id,
                "plant_name": name,
                "catalog_name": name,
                "tubes": [{"outlet_id": outlet_id, "outlet_name": str(outlet_id), "ml_per_run": 100, "count": 1}],
                "connections": {outlet_id: 1},
                "ml_per_cycle": 100,
                "need_ml": 100,
                "delivered_ml": 100,
                "difference_ml": 0,
                "under_ml": 0,
                "over_ml": 0,
                "score": score,
            }

        result = server.choose_tube_assignments(
            plants,
            [[option(1, "flex", 1, 1), option(1, "flex", 2, 2)], [option(2, "fixed", 1, 1)]],
            {1: 1, 2: 1},
            1,
        )
        by_plant = {item["plant_id"]: item["outlet_id"] for item in result["assignments"]}
        self.assertEqual(by_plant, {1: 2, 2: 1})
        self.assertEqual(result["optimization_method"], "bounded_dynamic_programming")

    def test_pot_efficiency_is_applied_once_for_every_pot_type(self):
        for pot_type, efficiency in POT_IRRIGATION_EFFICIENCY.items():
            with self.subTest(pot_type=pot_type):
                result = finish_daily_need(
                    transpiration_ml=800,
                    substrate_evaporation_ml=200,
                    rain_credit_ml=100,
                    pot_type=pot_type,
                    calibration_factor=1,
                    seasonal_factor=1,
                )
                self.assertAlmostEqual(result["raw_daily_need_ml"], 1000 * efficiency - 100)

    def test_flexible_windows_persist_and_overlap_is_rejected(self):
        config = server.planner_config()
        config.update(
            {
                "watering_window_start": "06:30",
                "watering_window_end": "21:00",
                "max_cycles_per_day": 8,
                "watering_min_interval_minutes": 45,
                "refill_windows": [
                    {"start": "01:15", "end": "02:00"},
                    {"start": "04:30", "end": "05:45"},
                ],
            }
        )
        saved = server.save_planner_config(config)
        self.assertEqual(server.get_state()["planner_config"], saved)
        config["refill_windows"] = [
            {"start": "01:00", "end": "03:00"},
            {"start": "02:00", "end": "04:00"},
        ]
        with self.assertRaisesRegex(ValueError, "ueberschneiden"):
            server.save_planner_config(config)

    def test_smtp_success_deduplication_and_resolution(self):
        env = {
            "NOTIFICATIONS_ENABLED": "true",
            "SMTP_HOST": "smtp.example.test",
            "SMTP_PORT": "25",
            "SMTP_FROM": "planner@example.test",
            "SMTP_TO": "owner@example.test",
            "SMTP_SECURITY": "none",
        }
        client = MagicMock()
        smtp = MagicMock()
        smtp.return_value.__enter__.return_value = client
        service = NotificationService(server.connect)
        with patch.dict(os.environ, env, clear=False), patch("watering_backend.notifications.smtplib.SMTP", smtp):
            first = service.update_condition("tank", True, "critical", "Tank leer", "Leer", cooldown_minutes=60, send_resolved=True)
            duplicate = service.update_condition("tank", True, "critical", "Tank leer", "Leer", cooldown_minutes=60, send_resolved=True)
            resolved = service.update_condition("tank", False, "critical", "Tank leer", "Behoben", cooldown_minutes=60, send_resolved=True)
        self.assertEqual(first["status"], "sent")
        self.assertEqual(duplicate["status"], "deduplicated")
        self.assertEqual(resolved["status"], "sent")
        self.assertEqual(client.send_message.call_count, 2)

    def test_smtp_failure_is_persisted_and_secret_is_not_exposed(self):
        env = {
            "NOTIFICATIONS_ENABLED": "true",
            "SMTP_HOST": "smtp.example.test",
            "SMTP_PORT": "25",
            "SMTP_USERNAME": "planner",
            "SMTP_PASSWORD": "super-secret",
            "SMTP_FROM": "planner@example.test",
            "SMTP_TO": "owner@example.test",
            "SMTP_SECURITY": "none",
        }
        smtp = MagicMock(side_effect=OSError("offline"))
        service = NotificationService(server.connect)
        with patch.dict(os.environ, env, clear=False), patch("watering_backend.notifications.smtplib.SMTP", smtp):
            result = service.update_condition("smtp", True, "warning", "SMTP", "offline", cooldown_minutes=60, send_resolved=False)
            state = server.get_state()
            diagnostics = service.diagnostics()
        self.assertEqual(result["status"], "failed")
        self.assertEqual(diagnostics["notification_log"][0]["status"], "failed")
        self.assertNotIn("password", json.dumps(state).lower())
        self.assertNotIn("super-secret", json.dumps(state))

    def test_smtp_failure_uses_short_retry_and_success_cooldown_survives_restart(self):
        current = [datetime(2026, 7, 25, 8, 0, tzinfo=timezone.utc)]
        service = NotificationService(server.connect, now=lambda: current[0])
        with patch("watering_backend.notifications.send_email", side_effect=OSError("offline")):
            failed = service.update_condition(
                "retry", True, "warning", "SMTP", "offline",
                cooldown_minutes=60, retry_minutes=5, send_resolved=True,
            )
            immediate = service.update_condition(
                "retry", True, "warning", "SMTP", "offline",
                cooldown_minutes=60, retry_minutes=5, send_resolved=True,
            )
        with server.connect() as conn:
            row = conn.execute(
                "SELECT * FROM notification_state WHERE alert_key = 'retry'"
            ).fetchone()
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(immediate["status"], "deduplicated")
        self.assertIsNone(row["last_notified_at"])
        self.assertEqual(row["consecutive_failures"], 1)

        current[0] += timedelta(minutes=5)
        restarted = NotificationService(server.connect, now=lambda: current[0])
        with patch("watering_backend.notifications.send_email") as sender:
            retried = restarted.update_condition(
                "retry", True, "warning", "SMTP", "online",
                cooldown_minutes=60, retry_minutes=5, send_resolved=True,
            )
            quiet = restarted.update_condition(
                "retry", True, "warning", "SMTP", "online",
                cooldown_minutes=60, retry_minutes=5, send_resolved=True,
            )
        self.assertEqual(retried["status"], "sent")
        self.assertEqual(quiet["status"], "deduplicated")
        self.assertEqual(sender.call_count, 1)

        with patch("watering_backend.notifications.send_email") as sender:
            resolved = restarted.update_condition(
                "retry", False, "warning", "SMTP", "behoben",
                cooldown_minutes=60, retry_minutes=5, send_resolved=True,
            )
        self.assertEqual(resolved["status"], "sent")
        sender.assert_called_once()

        with patch("watering_backend.notifications.send_email"):
            restarted.update_condition(
                "resolution-retry", True, "critical", "Tank", "leer",
                cooldown_minutes=60, retry_minutes=5, send_resolved=True,
            )
        with patch("watering_backend.notifications.send_email", side_effect=OSError("offline")):
            failed_resolution = restarted.update_condition(
                "resolution-retry", False, "critical", "Tank", "behoben",
                cooldown_minutes=60, retry_minutes=5, send_resolved=True,
            )
            waiting_resolution = restarted.update_condition(
                "resolution-retry", False, "critical", "Tank", "behoben",
                cooldown_minutes=60, retry_minutes=5, send_resolved=True,
            )
        self.assertEqual(failed_resolution["status"], "failed")
        self.assertEqual(waiting_resolution["status"], "inactive")
        current[0] += timedelta(minutes=5)
        with patch("watering_backend.notifications.send_email"):
            recovered_resolution = restarted.update_condition(
                "resolution-retry", False, "critical", "Tank", "behoben",
                cooldown_minutes=60, retry_minutes=5, send_resolved=True,
            )
        self.assertEqual(recovered_resolution["status"], "sent")

    def test_plant_string_id_validation_and_foreign_keys(self):
        plant_id = server.add_plant(
            {
                "catalog_id": "olive",
                "custom_name": "Olive",
                "size": "medium",
                "pot_liters": 30,
                "pot_type": "overflow",
            }
        )
        server.update_plant(
            plant_id,
            {
                "catalog_id": "olive",
                "custom_name": "Olive bearbeitet",
                "size": "large",
                "pot_liters": 35,
                "pot_type": "reservoir",
            },
        )
        plant = next(item for item in server.get_state()["plants"] if item["id"] == plant_id)
        self.assertEqual(plant["catalog_id"], "olive")

        base = {
            "catalog_id": "olive",
            "custom_name": "Test",
            "size": "medium",
            "pot_liters": 10,
            "pot_type": "overflow",
        }
        for field, value in [
            ("catalog_id", "unknown"),
            ("size", "giant"),
            ("pot_type", "bucket"),
            ("pot_liters", float("nan")),
            ("pot_liters", float("inf")),
            ("pot_liters", -1),
        ]:
            with self.subTest(field=field, value=value):
                with self.assertRaises(ValueError):
                    server.add_plant({**base, field: value})
        with self.assertRaises(ValueError):
            server.update_plant_position(plant_id, {"pos_x": float("nan"), "pos_y": 0.5})
        with self.assertRaisesRegex(ValueError, "doppelt"):
            server.validate_outlets(
                [
                    {"id": 1, "name": "A", "ml_per_run": 10},
                    {"id": 1, "name": "B", "ml_per_run": 20},
                ],
                [1],
            )
        with self.assertRaisesRegex(ValueError, "doppelt"):
            server.validate_walls(
                [{"side": "north", "height_m": 1}, {"side": "north", "height_m": 2}]
            )
        config = server.planner_config()
        config["refill_fraction"] = float("nan")
        with self.assertRaises(ValueError):
            server.save_planner_config(config)
        with server.connect() as conn:
            self.assertEqual(conn.execute("PRAGMA foreign_keys").fetchone()[0], 1)
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    """
                    INSERT INTO plants
                        (catalog_id, custom_name, size, pot_liters, pot_type, outlet_id, created_at)
                    VALUES ('missing', 'X', 'medium', 10, 'overflow', 1, 'now')
                    """
                )

    def test_schema_migrates_legacy_event_tables_and_enforces_run_ids(self):
        path = Path(self.tmp.name) / "legacy.sqlite3"
        conn = sqlite3.connect(path)
        try:
            conn.executescript(
                """
                CREATE TABLE watering_events (
                    id INTEGER PRIMARY KEY, ran_at TEXT, delivered_ml INTEGER,
                    temperature_c REAL, rain_mm REAL, source TEXT
                );
                CREATE TABLE refill_events (
                    id INTEGER PRIMARY KEY, ran_at TEXT, target_date TEXT,
                    requested_ml INTEGER, transferred_ml INTEGER,
                    duration_seconds INTEGER, window_label TEXT, source TEXT
                );
                CREATE TABLE notification_state (
                    alert_key TEXT PRIMARY KEY, severity TEXT NOT NULL,
                    active INTEGER NOT NULL DEFAULT 0, last_changed_at TEXT NOT NULL,
                    last_notified_at TEXT, message TEXT NOT NULL DEFAULT ''
                );
                """
            )
            migrate(conn)
            migrate(conn)
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            watering_columns = {row[1] for row in conn.execute("PRAGMA table_info(watering_events)")}
            notification_columns = {
                row[1] for row in conn.execute("PRAGMA table_info(notification_state)")
            }
            conn.execute(
                "INSERT INTO watering_events (ran_at, delivered_ml, source, run_id) VALUES ('x', 1, 'test', 'same')"
            )
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO watering_events (ran_at, delivered_ml, source, run_id) VALUES ('y', 1, 'test', 'same')"
                )
        finally:
            conn.close()
        self.assertEqual(version, SCHEMA_VERSION)
        self.assertIn("run_id", watering_columns)
        self.assertTrue(
            {"last_attempt_at", "last_error", "consecutive_failures"}
            <= notification_columns
        )

    def test_worker_can_be_disabled_for_tests(self):
        with patch.dict(
            os.environ,
            {"NOTIFICATIONS_ENABLED": "true", "NOTIFICATION_WORKER_DISABLED": "true"},
            clear=False,
        ):
            self.assertIsNone(server.start_notification_worker())

    def test_home_assistant_probe_uses_webhook_without_posting(self):
        response = MagicMock()
        response.read.return_value = b"{"
        response.__enter__.return_value = response
        with patch.dict(
            os.environ,
            {"HOME_ASSISTANT_WEBHOOK_URL": "http://ha.local:8123/api/webhook/private-id"},
            clear=False,
        ), patch("server.urlopen", return_value=response) as opener:
            result = server.test_home_assistant_connection()
        request = opener.call_args.args[0]
        self.assertEqual(
            request.full_url,
            "http://ha.local:8123/api/webhook/private-id",
        )
        self.assertEqual(request.method, "GET")
        self.assertNotIn("private-id", json.dumps(result))
        self.assertTrue(result["reachable"])

    def test_notification_test_api_returns_service_result(self):
        service = MagicMock()
        expected = {
            "sent": True,
            "sent_at": "2026-07-25T12:00:00+00:00",
        }
        service.send_test.return_value = expected
        handler = SimpleNamespace(
            path="/api/notifications/test",
            headers={"Content-Length": "2"},
            rfile=BytesIO(b"{}"),
        )
        with (
            patch.object(server, "notification_service", return_value=service),
            patch.object(server, "send_json") as send_json,
        ):
            server.AppHandler.do_POST(handler)
        send_json.assert_called_once_with(handler, expected)
        service.send_test.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
