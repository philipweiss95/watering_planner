from __future__ import annotations

import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import server
from backend_support import TemporaryBackend, fixed_backend_time, selected
from watering_backend.forecast import simulate_tanks


FIXED_NOW = datetime(2026, 6, 3, 6, 30, tzinfo=ZoneInfo("Europe/Berlin"))


def project_state(state: dict) -> dict:
    balcony_keys = (
        "orientation",
        "orientation_deg",
        "width_m",
        "depth_m",
        "location",
        "latitude",
        "longitude",
        "timezone_name",
        "wall_height_m",
        "tank_capacity_ml",
        "tank_current_ml",
        "refill_tank_capacity_ml",
        "refill_tank_current_ml",
        "refill_pump_ml_per_min",
    )
    plant_keys = (
        "id",
        "catalog_id",
        "custom_name",
        "size",
        "pot_liters",
        "pot_type",
        "pos_x",
        "pos_y",
        "hose_numbers",
        "configured_ml_per_cycle",
    )
    return {
        "balcony": selected(state["balcony"], *balcony_keys),
        "walls": [
            selected(item, "side", "height_m")
            for item in sorted(state["walls"], key=lambda item: item["side"])
        ],
        "outlets": [
            selected(item, "id", "name", "ml_per_run")
            for item in sorted(state["outlets"], key=lambda item: item["id"])
        ],
        "plants": [
            selected(item, *plant_keys)
            for item in sorted(state["plants"], key=lambda item: item["id"])
        ],
        "hoses": [
            selected(item, "number", "outlet_id", "plant_id")
            for item in sorted(state["hoses"], key=lambda item: item["number"])
        ],
        "planner_config": state["planner_config"],
    }


STATE_SNAPSHOT = {
    "balcony": {
        "orientation": "south",
        "orientation_deg": 180.0,
        "width_m": 3.0,
        "depth_m": 1.4,
        "location": "Berlin",
        "latitude": 52.52,
        "longitude": 13.405,
        "timezone_name": "Europe/Berlin",
        "wall_height_m": 1.05,
        "tank_capacity_ml": 10000,
        "tank_current_ml": 8000,
        "refill_tank_capacity_ml": 30000,
        "refill_tank_current_ml": 30000,
        "refill_pump_ml_per_min": 1000,
    },
    "walls": [
        {"side": "east", "height_m": 0.0},
        {"side": "north", "height_m": 0.0},
        {"side": "south", "height_m": 1.05},
        {"side": "west", "height_m": 0.0},
    ],
    "outlets": [
        {"id": 1, "name": "S", "ml_per_run": 15},
        {"id": 2, "name": "M", "ml_per_run": 30},
        {"id": 3, "name": "L", "ml_per_run": 60},
    ],
    "plants": [
        {
            "id": 1,
            "catalog_id": "olive",
            "custom_name": "Olive",
            "size": "medium",
            "pot_liters": 28.0,
            "pot_type": "overflow",
            "pos_x": 0.25,
            "pos_y": 0.7,
            "hose_numbers": "1",
            "configured_ml_per_cycle": 30,
        },
        {
            "id": 2,
            "catalog_id": "tomato",
            "custom_name": "Tomate",
            "size": "medium",
            "pot_liters": 18.0,
            "pot_type": "closed",
            "pos_x": 0.7,
            "pos_y": 0.55,
            "hose_numbers": "2",
            "configured_ml_per_cycle": 60,
        },
        {
            "id": 3,
            "catalog_id": "lavender",
            "custom_name": "Lavendel",
            "size": "small",
            "pot_liters": 10.0,
            "pot_type": "overflow",
            "pos_x": 0.45,
            "pos_y": 0.25,
            "hose_numbers": "3",
            "configured_ml_per_cycle": 15,
        },
    ],
    "hoses": [
        {"number": "1", "outlet_id": 2, "plant_id": 1},
        {"number": "2", "outlet_id": 3, "plant_id": 2},
        {"number": "3", "outlet_id": 1, "plant_id": 3},
    ],
    "planner_config": {
        "watering_window_start": "07:00",
        "watering_window_end": "19:00",
        "max_cycles_per_day": 16,
        "watering_min_interval_minutes": 30,
        "refill_windows": [
            {"start": "01:00", "end": "02:00"},
            {"start": "06:00", "end": "07:00"},
        ],
        "refill_min_interval_minutes": 180,
        "refill_strategy": "fraction",
        "refill_fraction": 0.5,
        "refill_target_ml": 0,
        "weather_stale_after_minutes": 180,
        "weather_cache_minutes": 20,
        "missed_watering_tolerance_minutes": 30,
        "notification_cooldown_minutes": 360,
        "notification_retry_minutes": 5,
        "notification_resolved_enabled": True,
        "supply_warning_days": 3,
        "notification_worker_interval_seconds": 60,
    },
}


def project_evaluation(result: dict) -> dict:
    return {
        "decision": selected(
            result,
            "should_run",
            "run_now",
            "recommended_cycles_today",
            "cycles_completed_today",
            "remaining_cycles_today",
        ),
        "pump": result["pump"],
        "tank": selected(
            result["tank"],
            "current_ml",
            "after_recommended_ml",
            "capacity_ml",
            "percent",
            "after_recommended_percent",
            "low",
            "empty_soon",
        ),
        "automation": selected(
            result["automation"],
            "run_now",
            "next_window",
            "windows",
            "due_cycles",
            "catch_up",
            "shortfall_prevention",
            "paused",
            "cooldown_active",
        ),
        "refill": selected(
            result["refill"],
            "status",
            "blocked",
            "blocked_reason",
            "severity",
            "planned_transfer_ml",
            "duration_seconds",
        ),
        "plants": [
            {
                **selected(
                    item,
                    "id",
                    "name",
                    "need_ml",
                    "daily_need_ml",
                    "delivered_ml",
                    "difference_ml",
                ),
                "water_model": selected(
                    item["water_model"],
                    "reference_et0_mm",
                    "raw_daily_need_ml",
                    "calibrated_daily_need_ml",
                    "seasonal_factor",
                    "seasonal_profile",
                    "seasonal_day_of_year",
                    "transpiration_ml",
                    "substrate_evaporation_ml",
                    "rain_credit_ml",
                    "wind_factor",
                ),
            }
            for item in result["plants"]
        ],
    }


EVALUATION_SNAPSHOT = {
    "decision": {
        "should_run": True,
        "run_now": False,
        "recommended_cycles_today": 7,
        "cycles_completed_today": 0,
        "remaining_cycles_today": 7,
    },
    "pump": {
        "duration_seconds": 120,
        "delivered_per_cycle_ml": 105,
        "delivered_if_remaining_ml": 735,
        "consumption_factor": 1.0,
        "consumed_per_cycle_ml": 105,
        "consumed_if_remaining_ml": 735,
    },
    "tank": {
        "current_ml": 8000,
        "after_recommended_ml": 7265,
        "capacity_ml": 10000,
        "percent": 80,
        "after_recommended_percent": 73,
        "low": False,
        "empty_soon": False,
    },
    "automation": {
        "run_now": False,
        "next_window": "07:00",
        "windows": ["07:00", "09:00", "11:00", "13:00", "15:00", "17:00", "19:00"],
        "due_cycles": 0,
        "catch_up": False,
        "shortfall_prevention": False,
        "paused": False,
        "cooldown_active": False,
    },
    "refill": {
        "status": "ready",
        "blocked": False,
        "blocked_reason": "",
        "severity": "info",
        "planned_transfer_ml": 1000,
        "duration_seconds": 60,
    },
    "plants": [
        {
            "id": 1,
            "name": "Olive",
            "need_ml": 207,
            "daily_need_ml": 207,
            "delivered_ml": 210,
            "difference_ml": 3,
            "water_model": {
                "reference_et0_mm": 4.2,
                "raw_daily_need_ml": 1326,
                "calibrated_daily_need_ml": 207,
                "seasonal_factor": 0.781,
                "seasonal_profile": "evergreen",
                "seasonal_day_of_year": 154,
                "transpiration_ml": 1075,
                "substrate_evaporation_ml": 294,
                "rain_credit_ml": 42,
                "wind_factor": 1.041,
            },
        },
        {
            "id": 2,
            "name": "Tomate",
            "need_ml": 383,
            "daily_need_ml": 383,
            "delivered_ml": 420,
            "difference_ml": 37,
            "water_model": {
                "reference_et0_mm": 4.2,
                "raw_daily_need_ml": 3401,
                "calibrated_daily_need_ml": 383,
                "seasonal_factor": 0.563,
                "seasonal_profile": "warm_annual",
                "seasonal_day_of_year": 154,
                "transpiration_ml": 4139,
                "substrate_evaporation_ml": 270,
                "rain_credit_ml": 38,
                "wind_factor": 1.041,
            },
        },
        {
            "id": 3,
            "name": "Lavendel",
            "need_ml": 47,
            "daily_need_ml": 47,
            "delivered_ml": 105,
            "difference_ml": 58,
            "water_model": {
                "reference_et0_mm": 4.2,
                "raw_daily_need_ml": 438,
                "calibrated_daily_need_ml": 47,
                "seasonal_factor": 0.533,
                "seasonal_profile": "annual",
                "seasonal_day_of_year": 154,
                "transpiration_ml": 247,
                "substrate_evaporation_ml": 210,
                "rain_credit_ml": 19,
                "wind_factor": 1.041,
            },
        },
    ],
}


def project_forecast(result: dict) -> dict:
    events = []
    for event in result["forecast_events"]:
        projected = selected(
            event,
            "at",
            "event_type",
            "planned_water_ml",
            "main_tank_before_ml",
            "main_tank_after_ml",
            "refill_tank_before_ml",
            "refill_tank_after_ml",
            "status",
        )
        if event["event_type"] == "watering":
            projected["estimated_weather"] = event["estimated_weather"]
        else:
            projected.update(
                selected(
                    event,
                    "started_at",
                    "completed_at",
                    "duration_seconds",
                    "transferred_ml",
                    "limited_by_window",
                )
            )
        events.append(projected)
    return {
        **selected(
            result,
            "last_supported_watering_at",
            "first_unserved_watering_at",
            "main_empty_at",
            "all_empty_at",
            "main_tank_after_forecast_ml",
            "refill_tank_after_forecast_ml",
        ),
        "events": events,
    }


FORECAST_SNAPSHOT = {
    "last_supported_watering_at": "2026-06-03T06:30:00+02:00",
    "first_unserved_watering_at": "2026-06-03T07:15:00+02:00",
    "main_empty_at": "2026-06-03T07:15:00+02:00",
    "all_empty_at": "",
    "main_tank_after_forecast_ml": 700,
    "refill_tank_after_forecast_ml": 1800,
    "events": [
        {
            "at": "2026-06-03T06:30:00+02:00",
            "event_type": "watering",
            "planned_water_ml": 1000,
            "main_tank_before_ml": 1100,
            "main_tank_after_ml": 100,
            "refill_tank_before_ml": 2400,
            "refill_tank_after_ml": 2400,
            "status": "successful",
            "estimated_weather": False,
        },
        {
            "at": "2026-06-03T07:00:00+02:00",
            "event_type": "refill",
            "planned_water_ml": 1200,
            "main_tank_before_ml": 500,
            "main_tank_after_ml": 700,
            "refill_tank_before_ml": 3000,
            "refill_tank_after_ml": 1800,
            "status": "estimated",
            "started_at": "2026-06-03T06:00:00+02:00",
            "completed_at": "2026-06-03T07:00:00+02:00",
            "duration_seconds": 3600,
            "transferred_ml": 1200,
            "limited_by_window": True,
        },
        {
            "at": "2026-06-03T07:15:00+02:00",
            "event_type": "watering",
            "planned_water_ml": 1000,
            "main_tank_before_ml": 700,
            "main_tank_after_ml": 700,
            "refill_tank_before_ml": 1800,
            "refill_tank_after_ml": 1800,
            "status": "unserved",
            "estimated_weather": False,
        },
        {
            "at": "2026-06-03T08:00:00+02:00",
            "event_type": "watering",
            "planned_water_ml": 1000,
            "main_tank_before_ml": 700,
            "main_tank_after_ml": 700,
            "refill_tank_before_ml": 1800,
            "refill_tank_after_ml": 1800,
            "status": "unserved",
            "estimated_weather": True,
        },
    ],
}


def project_routing(result: dict) -> dict:
    return {
        **selected(
            result,
            "cycles",
            "score",
            "hard_underwatered",
            "severely_overwatered",
            "optimization_method",
            "outlet_limits",
        ),
        "assignments": [
            selected(
                item,
                "plant_id",
                "tube_label",
                "ml_per_cycle",
                "need_ml",
                "delivered_ml",
                "difference_ml",
                "under_ml",
                "over_ml",
            )
            for item in sorted(result["assignments"], key=lambda item: item["plant_id"])
        ],
    }


ROUTING_SNAPSHOT = {
    "cycles": 7,
    "score": 87.45,
    "hard_underwatered": 0,
    "severely_overwatered": 0,
    "optimization_method": "bounded_dynamic_programming",
    "outlet_limits": {1: 12, 2: 12, 3: 12},
    "assignments": [
        {
            "plant_id": 1,
            "tube_label": "1x 15 ml + 1x 30 ml",
            "ml_per_cycle": 45,
            "need_ml": 315,
            "delivered_ml": 315,
            "difference_ml": 0,
            "under_ml": 0,
            "over_ml": 0,
        },
        {
            "plant_id": 2,
            "tube_label": "1x 30 ml + 1x 60 ml",
            "ml_per_cycle": 90,
            "need_ml": 570,
            "delivered_ml": 630,
            "difference_ml": 60,
            "under_ml": 0,
            "over_ml": 60,
        },
    ],
}


class RegressionSnapshotTests(unittest.TestCase):
    def test_state_snapshot_preserves_default_persisted_contract(self):
        with TemporaryBackend(), fixed_backend_time(FIXED_NOW):
            self.assertEqual(project_state(server.get_state()), STATE_SNAPSHOT)

    def test_evaluation_snapshot_preserves_planning_and_water_model(self):
        with TemporaryBackend(), fixed_backend_time(FIXED_NOW):
            result = server.evaluate(
                temperature_c=26,
                rain_mm=0.5,
                wind_kmh=8,
                sunshine_hours=7,
                et0_mm=4.2,
            )
            self.assertEqual(project_evaluation(result), EVALUATION_SNAPSHOT)

    def test_forecast_snapshot_preserves_chronological_transfer_semantics(self):
        start = datetime(2026, 6, 3, 6, 0, tzinfo=ZoneInfo("Europe/Berlin"))
        result = simulate_tanks(
            now=start,
            watering_events=[
                {"at": start + timedelta(minutes=30), "consumed_ml": 1000, "estimated": False},
                {"at": start + timedelta(minutes=75), "consumed_ml": 1000, "estimated": False},
                {"at": start + timedelta(minutes=120), "consumed_ml": 1000, "estimated": True},
            ],
            refill_windows=[(start, start + timedelta(minutes=60))],
            main_current_ml=500,
            main_capacity_ml=3000,
            refill_current_ml=3000,
            refill_capacity_ml=3000,
            refill_enabled=True,
            refill_pump_ml_per_min=20,
            refill_min_interval_minutes=180,
            refill_strategy="target",
            refill_fraction=0.5,
            refill_target_ml=3000,
        )
        self.assertEqual(project_forecast(result), FORECAST_SNAPSHOT)

    def test_routing_snapshot_preserves_global_assignment(self):
        plants = [
            {
                "id": 1,
                "name": "Olive",
                "catalog_name": "Olive",
                "need_ml": 315,
                "pot_liters": 20,
                "pot_type": "overflow",
                "drought_sensitivity": 0.8,
            },
            {
                "id": 2,
                "name": "Tomate",
                "catalog_name": "Tomate",
                "need_ml": 570,
                "pot_liters": 18,
                "pot_type": "closed",
                "drought_sensitivity": 1.3,
            },
        ]
        outlets = [
            {"id": 1, "name": "S", "ml_per_run": 15},
            {"id": 2, "name": "M", "ml_per_run": 30},
            {"id": 3, "name": "L", "ml_per_run": 60},
        ]
        result = server.optimize_routing(plants, outlets, max_cycles=8)
        self.assertEqual(project_routing(result), ROUTING_SNAPSHOT)


if __name__ == "__main__":
    unittest.main()
