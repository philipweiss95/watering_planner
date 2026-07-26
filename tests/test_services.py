from __future__ import annotations

import unittest
from datetime import date, datetime, time, timedelta, timezone
from unittest.mock import Mock
from zoneinfo import ZoneInfo

from watering_backend.config import DEFAULT_PLANNER_CONFIG
from watering_backend.services.evaluation import calculate_plant_results
from watering_backend.services.forecast import ForecastService
from watering_backend.services.notifications import (
    NotificationConditionsService,
)
from watering_backend.services.scheduling import SchedulingService


class EmptyEvents:
    def refill_windows(self) -> list[dict]:
        return []

    def latest_refill(self) -> None:
        return None


class CountingNotificationUpdater:
    def __init__(self):
        self.active_keys: set[str] = set()
        self.sent_keys: list[str] = []

    def update_condition(self, **condition) -> dict:
        alert_key = str(condition["alert_key"])
        active = bool(condition["active"])
        if active and alert_key not in self.active_keys:
            self.active_keys.add(alert_key)
            self.sent_keys.append(alert_key)
            status = "sent"
        else:
            if not active:
                self.active_keys.discard(alert_key)
            status = "deduplicated" if active else "inactive"
        return {
            "alert_key": alert_key,
            "active": active,
            "status": status,
            "error": "",
        }


class ServiceTests(unittest.TestCase):
    def test_pure_plant_calculation_has_no_database_or_http_dependency(self):
        balcony = {
            "orientation": "south",
            "orientation_deg": 180,
            "width_m": 3.0,
            "depth_m": 1.4,
            "latitude": 52.52,
            "longitude": 13.405,
            "timezone_name": "Europe/Berlin",
        }
        walls = [
            {"side": "north", "height_m": 0.0},
            {"side": "east", "height_m": 0.0},
            {"side": "south", "height_m": 1.05},
            {"side": "west", "height_m": 0.0},
        ]
        plant = {
            "id": 1,
            "catalog_id": "olive",
            "catalog_name": "Olivenbaum",
            "category": "Mediterrane Geh\u00f6lze",
            "custom_name": "Olive",
            "size": "medium",
            "pot_liters": 28.0,
            "pot_type": "overflow",
            "pos_x": 0.25,
            "pos_y": 0.7,
            "sun_factor": 1.15,
            "drought_sensitivity": 0.8,
            "crop_coefficient": 0.62,
            "canopy_m2_medium": 0.42,
            "recommended_pot_liters": 35.0,
            "moisture_preference": 0.72,
            "outlet_name": "M",
            "ml_per_run": 30,
            "hose_numbers": "1",
            "hoses": [],
            "configured_ml_per_cycle": 30,
        }

        result = calculate_plant_results(
            balcony,
            walls,
            [plant],
            temperature_c=26,
            rain_mm=0.5,
            wind_kmh=8,
            sunshine_hours=7,
            et0_mm=4.2,
            target_date=date(2026, 6, 3),
            calibration_factor=0.2,
        )

        self.assertEqual(result["reference_et0_mm"], 4.2)
        self.assertEqual(result["orientation_deg"], 180.0)
        self.assertEqual(len(result["plants"]), 1)
        calculated = result["plants"][0]
        self.assertEqual(
            {
                "id": calculated["id"],
                "need_ml": calculated["need_ml"],
                "daily_need_ml": calculated["daily_need_ml"],
                "seasonal_factor": calculated["water_model"]["seasonal_factor"],
                "seasonal_profile": calculated["water_model"]["seasonal_profile"],
                "raw_daily_need_ml": calculated["water_model"]["raw_daily_need_ml"],
                "calibrated_daily_need_ml": calculated["water_model"][
                    "calibrated_daily_need_ml"
                ],
            },
            {
                "id": 1,
                "need_ml": 207,
                "daily_need_ml": 207,
                "seasonal_factor": 0.781,
                "seasonal_profile": "evergreen",
                "raw_daily_need_ml": 1326,
                "calibrated_daily_need_ml": 207,
            },
        )

    def test_scheduling_service_preserves_summer_and_winter_dst_offsets(self):
        tzinfo = ZoneInfo("Europe/Berlin")
        service = SchedulingService(
            planner_config_provider=lambda: DEFAULT_PLANNER_CONFIG,
        )

        spring = service.refill_window_datetimes(
            date(2026, 3, 29),
            tzinfo,
            ["01:00", "03:00"],
        )
        winter = service.refill_window_datetimes(
            date(2026, 10, 25),
            tzinfo,
            ["01:00", "03:00"],
        )

        self.assertEqual(spring[0].utcoffset(), timedelta(hours=1))
        self.assertEqual(spring[1].utcoffset(), timedelta(hours=2))
        self.assertEqual(
            spring[1].astimezone(timezone.utc)
            - spring[0].astimezone(timezone.utc),
            timedelta(hours=1),
        )
        self.assertEqual(winter[0].utcoffset(), timedelta(hours=2))
        self.assertEqual(winter[1].utcoffset(), timedelta(hours=1))
        self.assertEqual(
            winter[1].astimezone(timezone.utc)
            - winter[0].astimezone(timezone.utc),
            timedelta(hours=3),
        )

    def test_forecast_service_uses_fixed_injected_planning_callables(self):
        tzinfo = ZoneInfo("Europe/Berlin")
        now = datetime(2026, 6, 3, 6, 0, tzinfo=tzinfo)
        state = {
            "balcony": {
                "timezone_name": "Europe/Berlin",
                "tank_current_ml": 100,
                "tank_capacity_ml": 500,
                "refill_tank_current_ml": 300,
                "refill_tank_capacity_ml": 300,
                "refill_pump_ml_per_min": 100,
            },
            "walls": [],
            "plants": [{"id": 1}],
            "outlets": [{"id": 1}],
        }
        config = {
            "max_cycles_per_day": 4,
            "refill_windows": [],
            "refill_min_interval_minutes": 180,
            "refill_strategy": "fraction",
            "refill_fraction": 0.5,
            "refill_target_ml": 0,
        }
        calculate_plants = Mock(
            side_effect=lambda *_args, **_kwargs: {
                "plants": [{"id": 1, "need_ml": 100}],
                "total_need_ml": 100,
            }
        )
        configured_plan = Mock(return_value={"configured": True})
        apply_plan = Mock(return_value={"cycles": 1})
        distributed_windows = Mock(
            side_effect=lambda day_value, cycles, zone: (
                [
                    datetime.combine(
                        day_value,
                        time(8, 0),
                        tzinfo=zone,
                    )
                ]
                if cycles
                else []
            )
        )
        window_datetime = Mock(
            side_effect=lambda day_value, value, zone: datetime.combine(
                day_value,
                time.fromisoformat(value),
                tzinfo=zone,
            )
        )
        service = ForecastService(
            events=EmptyEvents(),
            state_provider=lambda: state,
            planner_config=lambda: config,
            refill_enabled=lambda: False,
            calibrated_consumption=lambda delivered: round(float(delivered) * 1.2),
            local_now=lambda _timezone_name: now,
            window_datetime=window_datetime,
            distributed_windows=distributed_windows,
            calculate_plants=calculate_plants,
            configured_plan=configured_plan,
            apply_plan_to_weather=apply_plan,
            weather_forecast_days=2,
            tank_forecast_days=2,
        )

        result = service.depletion_forecast(
            {
                "pump": {
                    "delivered_per_cycle_ml": 100,
                    "consumed_per_cycle_ml": 120,
                },
                "tank": {"current_ml": 100, "capacity_ml": 500},
                "refill": {
                    "enabled": False,
                    "pump_ml_per_min": 100,
                    "refill_tank": {
                        "current_ml": 300,
                        "capacity_ml": 300,
                    },
                },
                "cycles_completed_today": 0,
                "automation": {"run_now": False},
            },
            {
                "forecast": [
                    {
                        "date": "2026-06-03",
                        "temperature_c": 25,
                        "rain_mm": 0,
                        "wind_kmh": 5,
                        "sunshine_hours": 8,
                        "et0_mm": 4,
                    }
                ]
            },
        )

        self.assertEqual(result["last_supported_watering_at"], "")
        self.assertEqual(
            result["first_unserved_watering_at"],
            "2026-06-03T08:00:00+02:00",
        )
        self.assertEqual(result["main_tank_after_forecast_ml"], 100)
        self.assertEqual(result["refill_tank_after_forecast_ml"], 300)
        self.assertEqual(result["safe_weather_forecast_days"], 1)
        self.assertTrue(result["estimated_after_forecast"])
        self.assertFalse(result["first_unserved_is_estimated"])
        self.assertEqual(
            [
                (event["at"], event["status"], event["estimated_weather"])
                for event in result["forecast_events"]
                if event["event_type"] == "watering"
            ],
            [
                ("2026-06-03T08:00:00+02:00", "unserved", False),
                ("2026-06-04T08:00:00+02:00", "unserved", True),
            ],
        )
        self.assertEqual(calculate_plants.call_count, 2)
        self.assertEqual(configured_plan.call_count, 2)
        self.assertEqual(apply_plan.call_count, 2)
        self.assertEqual(distributed_windows.call_count, 2)
        window_datetime.assert_not_called()

    def test_backend_forecast_keeps_45_day_supply_horizon(self):
        service = ForecastService(
            events=EmptyEvents(),
            state_provider=Mock(),
            planner_config=Mock(),
            refill_enabled=Mock(),
            calibrated_consumption=Mock(),
            local_now=Mock(),
            window_datetime=Mock(),
            distributed_windows=Mock(),
            calculate_plants=Mock(),
            configured_plan=Mock(),
            apply_plan_to_weather=Mock(),
            weather_forecast_days=16,
            tank_forecast_days=45,
        )

        days = service.normalized_forecast_days(
            {
                "forecast": [
                    {
                        "date": "2026-07-25",
                        "temperature_c": 25,
                        "rain_mm": 0,
                        "wind_kmh": 5,
                        "sunshine_hours": 8,
                        "et0_mm": 4,
                    }
                ]
            },
            date(2026, 7, 25),
        )

        self.assertEqual(len(days), 45)
        self.assertNotIn("estimated_from_last_forecast_day", days[0])
        self.assertTrue(days[16]["estimated_from_last_forecast_day"])
        self.assertEqual(days[-1]["date"], "2026-09-07")

    def _notification_conditions(
        self,
        refill: dict,
        *,
        refill_current_ml: int = 10000,
    ) -> tuple[
        NotificationConditionsService,
        CountingNotificationUpdater,
    ]:
        updater = CountingNotificationUpdater()
        state = {
            "balcony": {
                "tank_current_ml": 10000,
                "refill_tank_current_ml": refill_current_ml,
                "refill_tank_capacity_ml": 10000,
                "timezone_name": "Europe/Berlin",
            },
            "hoses": [],
        }
        config = {
            "notification_cooldown_minutes": 60,
            "notification_retry_minutes": 5,
            "notification_resolved_enabled": True,
            "supply_warning_days": 3,
        }

        def unavailable_weather(_balcony):
            raise ValueError("weather unavailable")

        service = NotificationConditionsService(
            state=lambda: state,
            calibrated_consumption=lambda _nominal: 0,
            refill_status=lambda _balcony: refill,
            fetch_weather=unavailable_weather,
            evaluate_weather=lambda _weather: {},
            weather_diagnostics=lambda: {
                "stale": False,
                "last_error": "",
                "last_successful_fetch_at": (
                    "2026-07-25T08:00:00+00:00"
                ),
                "stale_after_minutes": 180,
            },
            planner_config=lambda: config,
            local_now=lambda _timezone_name: datetime(
                2026,
                7,
                25,
                10,
                0,
                tzinfo=ZoneInfo("Europe/Berlin"),
            ),
            notification_service=lambda: updater,
            previous_missed_keys=lambda: [],
        )
        return service, updater

    def test_refill_notification_conditions_prefer_specific_root_cause(
        self,
    ):
        scenarios = [
            (
                "empty reserve",
                {
                    "status": "refill_tank_empty",
                    "blocked": True,
                    "blocked_reason": "refill_tank_empty",
                    "summary": "Vorratstank ist leer.",
                    "target_date": "2026-07-25",
                    "missed_windows": [],
                },
                0,
                "refill_tank_low",
            ),
            (
                "missed window",
                {
                    "status": "window_missed",
                    "blocked": True,
                    "blocked_reason": "window_missed",
                    "summary": "Nachfuellfenster verpasst.",
                    "target_date": "2026-07-25",
                    "missed_windows": ["01:00", "01:00"],
                },
                10000,
                "refill_run_missed:2026-07-25:01:00",
            ),
            (
                "missed previous-day window",
                {
                    "status": "window_missed",
                    "blocked": True,
                    "blocked_reason": "window_missed",
                    "summary": "Nachfuellfenster verpasst.",
                    "target_date": "2026-07-26",
                    "missed_windows": [],
                    "missed_window_details": [
                        {
                            "target_date": "2026-07-25",
                            "window_label": "23:30",
                            "window_key": (
                                "2026-07-25|"
                                "2026-07-25T21:30:00+00:00|"
                                "2026-07-25T22:00:00+00:00"
                            ),
                        }
                    ],
                },
                10000,
                "refill_run_missed:2026-07-25:23:30",
            ),
            (
                "independent blocker",
                {
                    "status": "pump_flow_missing",
                    "blocked": True,
                    "blocked_reason": "pump_flow_missing",
                    "summary": "Pumpendurchsatz fehlt.",
                    "target_date": "2026-07-25",
                    "missed_windows": [],
                },
                10000,
                "automatic_refill_blocked",
            ),
        ]
        for name, refill, refill_current_ml, expected_key in scenarios:
            with self.subTest(name=name):
                service, updater = self._notification_conditions(
                    refill,
                    refill_current_ml=refill_current_ml,
                )
                conditions = service.notification_conditions()
                active_refill_keys = [
                    item["alert_key"]
                    for item in conditions
                    if item["active"]
                    and (
                        item["alert_key"] == "refill_tank_low"
                        or item["alert_key"]
                        == "automatic_refill_blocked"
                        or item["alert_key"].startswith(
                            "refill_run_missed:"
                        )
                    )
                ]
                self.assertEqual(active_refill_keys, [expected_key])

                service.run_notification_check()
                service.run_notification_check()
                self.assertEqual(updater.sent_keys, [expected_key])


if __name__ == "__main__":
    unittest.main()
