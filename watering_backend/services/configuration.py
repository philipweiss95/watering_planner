from __future__ import annotations

from collections.abc import Callable
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from watering_backend.catalog import (
    DEFAULT_BALCONY,
    MAX_WATERING_AMOUNT_PERCENT,
    MAX_WATER_MODEL_CALIBRATION_PERCENT,
    MIN_WATERING_AMOUNT_PERCENT,
    MIN_WATER_MODEL_CALIBRATION_PERCENT,
)
from watering_backend.database import Database
from watering_backend.repositories.hoses import HosesRepository
from watering_backend.repositories.settings import SettingsRepository
from watering_backend.repositories.tanks import TanksRepository
from watering_backend.services.evaluation import angular_distance
from watering_backend.validation import (
    finite_integer,
    finite_number,
    validate_outlets,
    validate_walls,
)


def orientation_name_from_degrees(degrees: float) -> str:
    names = [
        (0, "north"),
        (45, "northeast"),
        (90, "east"),
        (135, "southeast"),
        (180, "south"),
        (225, "southwest"),
        (270, "west"),
        (315, "northwest"),
    ]
    nearest = min(
        names,
        key=lambda item: angular_distance(degrees, item[0]),
    )
    return nearest[1]


class ConfigurationService:
    """Validate and persist balcony, pump and planner configuration."""

    def __init__(
        self,
        *,
        database: Database,
        tanks: TanksRepository,
        hoses: HosesRepository,
        settings: SettingsRepository,
        now_iso: Callable[[], str],
    ):
        self.database = database
        self.tanks = tanks
        self.hoses = hoses
        self.settings = settings
        self.now_iso = now_iso

    def save_balcony(self, payload: dict[str, Any]) -> None:
        required = [
            "orientation_deg",
            "width_m",
            "depth_m",
            "latitude",
            "longitude",
            "tank_capacity_ml",
            "refill_pump_ml_per_min",
            "outlets",
            "walls",
        ]
        for key in required:
            if key not in payload:
                raise KeyError(f"{key} fehlt")

        orientation_deg = (
            finite_number(payload["orientation_deg"], "orientation_deg")
            % 360
        )
        width_m = finite_number(
            payload["width_m"],
            "width_m",
            minimum=0.1,
            maximum=100,
        )
        depth_m = finite_number(
            payload["depth_m"],
            "depth_m",
            minimum=0.1,
            maximum=100,
        )
        latitude = finite_number(
            payload["latitude"],
            "latitude",
            minimum=-90,
            maximum=90,
        )
        longitude = finite_number(
            payload["longitude"],
            "longitude",
            minimum=-180,
            maximum=180,
        )
        main_capacity = finite_integer(
            payload["tank_capacity_ml"],
            "tank_capacity_ml",
            minimum=100,
            maximum=1_000_000,
        )
        refill_capacity = finite_integer(
            payload.get(
                "refill_tank_capacity_ml",
                DEFAULT_BALCONY["refill_tank_capacity_ml"],
            ),
            "refill_tank_capacity_ml",
            minimum=100,
            maximum=1_000_000,
        )
        refill_flow = finite_integer(
            payload["refill_pump_ml_per_min"],
            "refill_pump_ml_per_min",
            minimum=0,
            maximum=1_000_000,
        )
        timezone_name = str(
            payload.get("timezone_name") or "Europe/Berlin"
        )
        try:
            ZoneInfo(timezone_name)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("Unbekannte Zeitzone") from exc

        amount_percent = payload.get("watering_amount_percent")
        calibration_percent = payload.get(
            "water_model_calibration_percent"
        )
        refill_enabled = payload.get("refill_automation_enabled")
        refill_times = payload.get("refill_schedule_times")
        refill_cooldown = payload.get(
            "refill_cooldown_minutes_per_liter"
        )
        main_consumption_factor = payload.get(
            "main_pump_calibration_factor"
        )
        if amount_percent is not None:
            amount_percent = finite_number(
                amount_percent,
                "watering_amount_percent",
            )
            if not (
                MIN_WATERING_AMOUNT_PERCENT
                <= amount_percent
                <= MAX_WATERING_AMOUNT_PERCENT
            ):
                raise ValueError(
                    "Gießmenge muss zwischen "
                    f"{MIN_WATERING_AMOUNT_PERCENT:g} und "
                    f"{MAX_WATERING_AMOUNT_PERCENT:g} Prozent liegen"
                )
        if calibration_percent is not None:
            calibration_percent = finite_number(
                calibration_percent,
                "water_model_calibration_percent",
            )
            if not (
                MIN_WATER_MODEL_CALIBRATION_PERCENT
                <= calibration_percent
                <= MAX_WATER_MODEL_CALIBRATION_PERCENT
            ):
                raise ValueError(
                    "Wasser-Skalierung muss zwischen "
                    f"{MIN_WATER_MODEL_CALIBRATION_PERCENT:g} und "
                    f"{MAX_WATER_MODEL_CALIBRATION_PERCENT:g} Prozent liegen"
                )

        with self.database.connection(immediate=True) as conn:
            current = self.tanks.balcony(conn=conn)
            outlets = validate_outlets(
                payload["outlets"],
                [outlet["id"] for outlet in self.tanks.outlets(conn=conn)],
            )
            walls = validate_walls(payload["walls"])
            self.tanks.update_balcony(
                conn,
                {
                    "orientation": orientation_name_from_degrees(
                        orientation_deg
                    ),
                    "orientation_deg": orientation_deg,
                    "width_m": width_m,
                    "depth_m": depth_m,
                    "location": "",
                    "latitude": latitude,
                    "longitude": longitude,
                    "timezone_name": timezone_name,
                    "wall_height_m": max(
                        wall["height_m"] for wall in walls
                    ),
                    "tank_capacity_ml": main_capacity,
                    "tank_current_ml": min(
                        int(current["tank_current_ml"]),
                        main_capacity,
                    ),
                    "refill_tank_capacity_ml": refill_capacity,
                    "refill_tank_current_ml": min(
                        int(current["refill_tank_current_ml"]),
                        refill_capacity,
                    ),
                    "refill_pump_ml_per_min": refill_flow,
                },
                updated_at=self.now_iso(),
            )
            self.tanks.replace_outlets(conn, outlets)
            self.hoses.sync_all_legacy_connections(conn)
            self.tanks.save_walls(conn, walls)

        if amount_percent is not None:
            self.settings.save_watering_amount_percent(amount_percent)
        elif calibration_percent is not None:
            self.settings.save_water_model_calibration_percent(
                calibration_percent
            )
        if refill_enabled is not None:
            self.settings.save_refill_automation_enabled(refill_enabled)
        if refill_times is not None:
            self.settings.save_refill_schedule_times(refill_times)
        if refill_cooldown is not None:
            self.settings.save_refill_cooldown_minutes_per_liter(
                refill_cooldown
            )
        if main_consumption_factor is not None:
            self.settings.save_main_pump_calibration_factor(
                main_consumption_factor
            )
        if "planner_config" in payload:
            self.settings.save_planner_config(payload["planner_config"])

