from __future__ import annotations

from collections.abc import Callable
from typing import Any

from watering_backend.database import Database
from watering_backend.repositories.hoses import HosesRepository
from watering_backend.repositories.plants import PlantsRepository
from watering_backend.repositories.settings import SettingsRepository
from watering_backend.repositories.tanks import TanksRepository


class StateService:
    """Assemble the stable browser-facing state document."""

    def __init__(
        self,
        *,
        database: Database,
        tanks: TanksRepository,
        hoses: HosesRepository,
        plants: PlantsRepository,
        settings: SettingsRepository,
        version: str,
        planner_config: Callable[[], dict[str, Any]],
        weather_diagnostics: Callable[[], dict[str, Any]],
        notification_status: Callable[[], dict[str, Any]],
        home_assistant_diagnostics: Callable[[], dict[str, Any]],
        calibration_status: Callable[[], dict[str, Any]],
        completed_cycles_today: Callable[[str], int],
    ):
        self.database = database
        self.tanks = tanks
        self.hoses = hoses
        self.plants = plants
        self.settings = settings
        self.version = version
        self.planner_config = planner_config
        self.weather_diagnostics = weather_diagnostics
        self.notification_status = notification_status
        self.home_assistant_diagnostics = home_assistant_diagnostics
        self.calibration_status = calibration_status
        self.completed_cycles_today = completed_cycles_today

    def get_state(self) -> dict[str, Any]:
        with self.database.connection() as conn:
            balcony = self.tanks.balcony(conn=conn)
            outlets = self.tanks.outlets(conn=conn)
            walls = self.tanks.walls(conn=conn)
            catalog = self.plants.catalog(conn)
            hoses = self.hoses.list(conn)
            plants = self.plants.list(conn, hoses=hoses)
        timezone_name = str(
            balcony.get("timezone_name", "Europe/Berlin")
        )
        return {
            "version": self.version,
            "balcony": balcony,
            "outlets": outlets,
            "hoses": hoses,
            "walls": walls,
            "catalog": catalog,
            "plants": plants,
            "settings": {
                "watering_amount_percent": round(
                    self.settings.watering_amount_percent(),
                    1,
                ),
                "main_pump_calibration_factor": round(
                    self.settings.main_pump_calibration_factor(),
                    4,
                ),
                "refill_automation_enabled": (
                    self.settings.refill_automation_enabled()
                ),
                "refill_schedule_times": (
                    self.settings.refill_schedule_times()
                ),
                "refill_cooldown_minutes_per_liter": (
                    self.settings.refill_cooldown_minutes_per_liter()
                ),
            },
            "planner_config": self.planner_config(),
            "weather_status": self.weather_diagnostics(),
            "notifications": self.notification_status(),
            "home_assistant": self.home_assistant_diagnostics(),
            "calibration": self.calibration_status(),
            "cycles_completed_today": self.completed_cycles_today(
                timezone_name
            ),
        }

