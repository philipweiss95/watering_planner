from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
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
from watering_backend.config import validate_planner_config
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


@dataclass(frozen=True)
class NormalizedConfiguration:
    balcony: dict[str, Any]
    outlets: list[dict[str, Any]]
    walls: list[dict[str, Any]]
    watering_amount_percent: float | None
    water_model_calibration_percent: float | None
    refill_automation_enabled: bool | None
    refill_schedule_times: list[str] | None
    refill_cooldown_minutes_per_liter: float | None
    main_pump_calibration_factor: float | None
    planner_config: dict[str, Any] | None


def _optional_bool(value: object, field: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in {0, 1}:
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "on", "yes"}:
            return True
        if normalized in {"0", "false", "off", "no"}:
            return False
    raise ValueError(f"{field} muss boolesch sein")


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

    def _normalize(
        self,
        payload: dict[str, Any],
        *,
        current: dict[str, Any],
        existing_outlet_ids: list[int],
    ) -> NormalizedConfiguration:
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

        amount_percent = (
            payload.get("watering_amount_percent")
            if "watering_amount_percent" in payload
            else None
        )
        calibration_percent = (
            payload.get("water_model_calibration_percent")
            if "water_model_calibration_percent" in payload
            else None
        )
        refill_enabled = (
            _optional_bool(
                payload["refill_automation_enabled"],
                "refill_automation_enabled",
            )
            if "refill_automation_enabled" in payload
            else None
        )
        refill_times = (
            self.settings.normalize_refill_schedule_times(
                payload["refill_schedule_times"]
            )
            if "refill_schedule_times" in payload
            else None
        )
        refill_cooldown = (
            finite_number(
                payload["refill_cooldown_minutes_per_liter"],
                "refill_cooldown_minutes_per_liter",
                minimum=1,
                maximum=self.settings.defaults.refill_max_cooldown_minutes,
            )
            if "refill_cooldown_minutes_per_liter" in payload
            else None
        )
        main_consumption_factor = (
            finite_number(
                payload["main_pump_calibration_factor"],
                "main_pump_calibration_factor",
                minimum=0.1,
                maximum=10,
            )
            if "main_pump_calibration_factor" in payload
            else None
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

        outlets = validate_outlets(
            payload["outlets"],
            existing_outlet_ids,
        )
        walls = validate_walls(payload["walls"])
        planner_config = (
            validate_planner_config(payload["planner_config"])
            if "planner_config" in payload
            else None
        )
        return NormalizedConfiguration(
            balcony={
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
            outlets=outlets,
            walls=walls,
            watering_amount_percent=amount_percent,
            water_model_calibration_percent=calibration_percent,
            refill_automation_enabled=refill_enabled,
            refill_schedule_times=refill_times,
            refill_cooldown_minutes_per_liter=refill_cooldown,
            main_pump_calibration_factor=main_consumption_factor,
            planner_config=planner_config,
        )

    def save_balcony(self, payload: dict[str, Any]) -> None:
        if not isinstance(payload, dict):
            raise ValueError("Einstellungen müssen ein Objekt sein")
        with self.database.connection(immediate=True) as conn:
            current = self.tanks.balcony(conn=conn)
            normalized = self._normalize(
                payload,
                current=current,
                existing_outlet_ids=[
                    int(outlet["id"])
                    for outlet in self.tanks.outlets(conn=conn)
                ],
            )
            self.tanks.update_balcony(
                conn,
                normalized.balcony,
                updated_at=self.now_iso(),
            )
            self.tanks.replace_outlets(conn, normalized.outlets)
            self.hoses.sync_all_legacy_connections(conn)
            self.tanks.replace_walls(conn, normalized.walls)

            if normalized.watering_amount_percent is not None:
                self.settings.save_watering_amount_percent(
                    normalized.watering_amount_percent,
                    conn=conn,
                )
            if normalized.water_model_calibration_percent is not None:
                self.settings.save_water_model_calibration_percent(
                    normalized.water_model_calibration_percent,
                    conn=conn,
                )
            if normalized.refill_automation_enabled is not None:
                self.settings.save_refill_automation_enabled(
                    normalized.refill_automation_enabled,
                    conn=conn,
                )
            if normalized.refill_schedule_times is not None:
                self.settings.save_refill_schedule_times(
                    normalized.refill_schedule_times,
                    conn=conn,
                )
            if normalized.refill_cooldown_minutes_per_liter is not None:
                self.settings.save_refill_cooldown_minutes_per_liter(
                    normalized.refill_cooldown_minutes_per_liter,
                    conn=conn,
                )
            if normalized.main_pump_calibration_factor is not None:
                self.settings.save_main_pump_calibration_factor(
                    normalized.main_pump_calibration_factor,
                    conn=conn,
                )
            if normalized.planner_config is not None:
                self.settings.save_planner_config(
                    normalized.planner_config,
                    conn=conn,
                )
