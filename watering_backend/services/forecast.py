from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime, timedelta
from typing import Any

from watering_backend.forecast import simulate_tanks
from watering_backend.repositories.events import EventsRepository


class ForecastService:
    """Chronological tank projection with explicit planning dependencies."""

    def __init__(
        self,
        *,
        events: EventsRepository,
        state_provider: Callable[[], dict[str, Any]],
        planner_config: Callable[[], dict[str, Any]],
        refill_enabled: Callable[[], bool],
        calibrated_consumption: Callable[[int | float], int],
        local_now: Callable[[str], datetime],
        window_datetime: Callable[[date, str, Any], datetime],
        distributed_windows: Callable[[date, int, Any], list[datetime]],
        calculate_plants: Callable[..., dict[str, Any]],
        configured_plan: Callable[[list[dict[str, Any]]], dict[str, Any]],
        apply_plan_to_weather: Callable[..., dict[str, Any]],
        weather_forecast_days: int = 16,
        tank_forecast_days: int = 45,
    ):
        self.events = events
        self.state_provider = state_provider
        self.planner_config = planner_config
        self.refill_enabled = refill_enabled
        self.calibrated_consumption = calibrated_consumption
        self.local_now = local_now
        self.window_datetime = window_datetime
        self.distributed_windows = distributed_windows
        self.calculate_plants = calculate_plants
        self.configured_plan = configured_plan
        self.apply_plan_to_weather = apply_plan_to_weather
        self.weather_forecast_days = weather_forecast_days
        self.tank_forecast_days = tank_forecast_days
        self.projected_consumption_provider: Callable[..., list[dict[str, Any]]] | None = None

    def depletion_forecast(
        self,
        result: dict[str, Any],
        weather: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        state = self.state_provider()
        balcony = state["balcony"]
        timezone_name = str(balcony.get("timezone_name", "Europe/Berlin"))
        now = self.local_now(timezone_name)
        delivered_per_cycle = int(result["pump"]["delivered_per_cycle_ml"])
        consumed_per_cycle = int(
            result["pump"].get(
                "consumed_per_cycle_ml",
                self.calibrated_consumption(delivered_per_cycle),
            )
        )
        main_ml = max(0, int(result["tank"]["current_ml"]))
        refill = result.get("refill", {})
        refill_tank = refill.get("refill_tank", {})
        refill_ml = max(
            0,
            int(
                refill_tank.get(
                    "current_ml",
                    balcony.get("refill_tank_current_ml", 0),
                )
            ),
        )
        weather_payload = weather or result.get("weather") or result.get("inputs", {})
        raw_forecast = (
            weather_payload.get("forecast")
            if isinstance(weather_payload, dict)
            else None
        )
        safe_forecast_days = len(raw_forecast) if isinstance(raw_forecast, list) else 0
        forecast_days = self.normalized_forecast_days(weather_payload, now.date())
        projector = (
            self.projected_consumption_provider
            or self.projected_consumption_days
        )
        projected_days = projector(
            state,
            forecast_days,
            delivered_per_cycle,
            consumed_per_cycle,
        )
        cycle_events = self.projected_cycle_events(
            projected_days,
            result,
            now,
            timezone_name,
        )
        next_cycle_at = cycle_events[0]["at"].isoformat() if cycle_events else ""

        if consumed_per_cycle <= 0:
            summary = "Noch kein Wasserverbrauch pro Zyklus berechenbar."
        elif not cycle_events:
            summary = "Für die Wetterprognose sind keine Gießläufe geplant."
        else:
            summary = (
                "Reichweite aus chronologischen Gieß- und "
                "Nachfüllereignissen berechnet."
            )

        simulation = self.chronological_depletion_simulation(
            result=result,
            state=state,
            projected_days=projected_days,
            cycle_events=cycle_events,
            now=now,
        )
        first_unserved_at = simulation["first_unserved_watering_at"]
        last_supported_at = simulation["last_supported_watering_at"]
        transferred_refill_ml = sum(
            int(event.get("transferred_ml", 0))
            for event in simulation["forecast_events"]
            if event.get("event_type") == "refill"
        )
        first_unserved_event = next(
            (
                event
                for event in simulation["forecast_events"]
                if event.get("event_type") == "watering"
                and event.get("status") == "unserved"
            ),
            None,
        )
        estimated_after_forecast = any(
            day.get("estimated") for day in projected_days
        )
        return {
            "main_empty_at": simulation["main_empty_at"],
            "all_empty_at": simulation["all_empty_at"],
            "first_unserved_at": first_unserved_at,
            "last_watering_at": last_supported_at,
            "last_supported_watering_at": last_supported_at,
            "first_unserved_watering_at": first_unserved_at,
            "forecast_events": simulation["forecast_events"],
            "main_tank_after_forecast_ml": simulation[
                "main_tank_after_forecast_ml"
            ],
            "refill_tank_after_forecast_ml": simulation[
                "refill_tank_after_forecast_ml"
            ],
            "next_cycle_at": next_cycle_at,
            "total_available_ml": main_ml + transferred_refill_ml,
            "main_available_ml": main_ml,
            "refill_available_ml": refill_ml,
            "usable_refill_ml": transferred_refill_ml,
            "consumption_per_cycle_ml": consumed_per_cycle,
            "estimated_after_forecast": estimated_after_forecast,
            "first_unserved_is_estimated": bool(
                first_unserved_event
                and first_unserved_event.get("estimated_weather")
            ),
            "projected_days": projected_days,
            "forecast_days": min(
                len(forecast_days),
                self.weather_forecast_days,
            ),
            "forecast_horizon_days": len(forecast_days),
            "safe_weather_forecast_days": min(
                safe_forecast_days,
                len(forecast_days),
            ),
            "summary": summary,
        }

    def chronological_depletion_simulation(
        self,
        *,
        result: dict[str, Any],
        state: dict[str, Any],
        projected_days: list[dict[str, Any]],
        cycle_events: list[dict[str, Any]],
        now: datetime,
    ) -> dict[str, Any]:
        balcony = state["balcony"]
        config = self.planner_config()
        refill_windows: list[tuple[datetime, datetime]] = []
        for day in projected_days:
            day_value = date.fromisoformat(day["date"])
            for window in config["refill_windows"]:
                refill_windows.append(
                    (
                        self.window_datetime(
                            day_value,
                            window["start"],
                            now.tzinfo,
                        ),
                        self.window_datetime(
                            day_value,
                            window["end"],
                            now.tzinfo,
                        ),
                    )
                )
        completed_refill_windows: set[str] = set()
        for row in self.events.refill_windows():
            try:
                completed_start = self.window_datetime(
                    date.fromisoformat(str(row["target_date"])),
                    str(row["window_label"]),
                    now.tzinfo,
                )
            except ValueError:
                continue
            completed_refill_windows.add(completed_start.isoformat())
        refill = result.get("refill", {})
        refill_tank = refill.get("refill_tank", {})
        latest_refill = self.events.latest_refill()
        return simulate_tanks(
            now=now,
            watering_events=cycle_events,
            refill_windows=refill_windows,
            main_current_ml=int(result["tank"]["current_ml"]),
            main_capacity_ml=int(result["tank"]["capacity_ml"]),
            refill_current_ml=int(
                refill_tank.get(
                    "current_ml",
                    balcony.get("refill_tank_current_ml", 0),
                )
            ),
            refill_capacity_ml=int(
                refill_tank.get(
                    "capacity_ml",
                    balcony.get("refill_tank_capacity_ml", 0),
                )
            ),
            refill_enabled=bool(
                refill.get("enabled", self.refill_enabled())
            ),
            refill_pump_ml_per_min=int(
                refill.get(
                    "pump_ml_per_min",
                    balcony.get("refill_pump_ml_per_min", 0),
                )
            ),
            refill_min_interval_minutes=int(
                config["refill_min_interval_minutes"]
            ),
            refill_strategy=str(config["refill_strategy"]),
            refill_fraction=float(config["refill_fraction"]),
            refill_target_ml=int(config["refill_target_ml"]),
            completed_refill_windows=completed_refill_windows,
            last_refill_at=(
                self.parse_event_datetime(latest_refill.get("ran_at"))
                if latest_refill
                else None
            ),
        )

    def normalized_forecast_days(
        self,
        weather: dict[str, Any],
        today: date,
    ) -> list[dict[str, Any]]:
        forecast = weather.get("forecast") if isinstance(weather, dict) else None
        if isinstance(forecast, list) and forecast:
            items = [dict(item) for item in forecast if isinstance(item, dict)]
            while items and len(items) < self.tank_forecast_days:
                previous = dict(items[-1])
                try:
                    previous_date = date.fromisoformat(str(previous["date"]))
                except (KeyError, ValueError):
                    previous_date = today + timedelta(days=len(items) - 1)
                previous["date"] = (previous_date + timedelta(days=1)).isoformat()
                previous["estimated_from_last_forecast_day"] = True
                items.append(previous)
            return items[: self.tank_forecast_days]
        return [
            {
                "date": (today + timedelta(days=index)).isoformat(),
                "temperature_c": self.forecast_number(
                    weather,
                    "temperature_c",
                    20,
                ),
                "rain_mm": self.forecast_number(weather, "rain_mm", 0),
                "wind_kmh": self.forecast_number(weather, "wind_kmh", 0),
                "sunshine_hours": self.forecast_number(
                    weather,
                    "sunshine_hours",
                    6,
                ),
                "et0_mm": self.forecast_number(weather, "et0_mm", 0),
            }
            for index in range(self.tank_forecast_days)
        ]

    @staticmethod
    def forecast_number(
        weather: dict[str, Any],
        key: str,
        default: float,
    ) -> float:
        if not isinstance(weather, dict):
            return default
        value = weather.get(key, default)
        if value is None:
            return default
        return float(value)

    def projected_consumption_days(
        self,
        state: dict[str, Any],
        forecast_days: list[dict[str, Any]],
        delivered_per_cycle: int,
        consumed_per_cycle: int | None = None,
    ) -> list[dict[str, Any]]:
        balcony = state["balcony"]
        walls = state["walls"]
        plants = state["plants"]
        outlets = state["outlets"]
        tank_consumption_per_cycle = (
            self.calibrated_consumption(delivered_per_cycle)
            if consumed_per_cycle is None
            else max(0, int(consumed_per_cycle))
        )
        projections: list[dict[str, Any]] = []
        for weather in forecast_days:
            try:
                target_date = date.fromisoformat(str(weather.get("date")))
            except ValueError:
                target_date = self.local_now(
                    str(balcony.get("timezone_name", "Europe/Berlin"))
                ).date()
            calculated = self.calculate_plants(
                balcony,
                walls,
                plants,
                temperature_c=float(weather.get("temperature_c", 20)),
                rain_mm=float(weather.get("rain_mm", 0)),
                wind_kmh=float(weather.get("wind_kmh", 0)),
                sunshine_hours=float(weather.get("sunshine_hours", 0)),
                et0_mm=float(weather.get("et0_mm", 0) or 0),
                target_date=target_date,
            )
            routing_plan = self.apply_plan_to_weather(
                self.configured_plan(plants),
                calculated["plants"],
                outlets,
                max_cycles=int(self.planner_config()["max_cycles_per_day"]),
            )
            cycles = int(routing_plan["cycles"])
            projections.append(
                {
                    "date": target_date.isoformat(),
                    "cycles": cycles,
                    "delivered_ml": cycles * delivered_per_cycle,
                    "delivered_per_cycle_ml": delivered_per_cycle,
                    "consumed_ml": cycles * tank_consumption_per_cycle,
                    "consumed_per_cycle_ml": tank_consumption_per_cycle,
                    "need_ml": round(calculated["total_need_ml"]),
                    "estimated": bool(
                        weather.get("estimated_from_last_forecast_day", False)
                    ),
                    "weather": {
                        "temperature_c": float(
                            weather.get("temperature_c", 20)
                        ),
                        "rain_mm": float(weather.get("rain_mm", 0)),
                    },
                }
            )
        return projections

    def projected_cycle_events(
        self,
        projected_days: list[dict[str, Any]],
        result: dict[str, Any],
        now: datetime,
        timezone_name: str,
    ) -> list[dict[str, Any]]:
        del timezone_name
        events: list[dict[str, Any]] = []
        today = now.date()
        completed_today = int(result.get("cycles_completed_today", 0))
        for day in projected_days:
            day_date = date.fromisoformat(day["date"])
            if day_date < today:
                continue
            cycles = int(day["cycles"])
            windows = self.distributed_windows(day_date, cycles, now.tzinfo)
            consumed_ml = int(
                day.get(
                    "consumed_per_cycle_ml",
                    day["delivered_per_cycle_ml"],
                )
            )
            if day_date == today:
                remaining_windows = windows[min(completed_today, cycles) :]
                overdue_windows = [
                    window for window in remaining_windows if window <= now
                ]
                if overdue_windows and result.get("automation", {}).get("run_now"):
                    events.append(
                        {
                            "at": now,
                            "delivered_ml": int(
                                day["delivered_per_cycle_ml"]
                            ),
                            "consumed_ml": consumed_ml,
                            "date": day["date"],
                            "estimated": bool(day.get("estimated", False)),
                        }
                    )
                windows = [window for window in remaining_windows if window > now]
            for window in windows:
                events.append(
                    {
                        "at": window,
                        "delivered_ml": int(day["delivered_per_cycle_ml"]),
                        "consumed_ml": consumed_ml,
                        "date": day["date"],
                        "estimated": bool(day.get("estimated", False)),
                    }
                )
        return sorted(events, key=lambda item: item["at"])

    @staticmethod
    def parse_event_datetime(value: object) -> datetime | None:
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(str(value))
        except ValueError:
            return None
        return parsed
