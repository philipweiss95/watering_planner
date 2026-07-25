"""Pure plant-demand, exposure, and sunlight calculations."""

from __future__ import annotations

import math
import re
from collections.abc import Callable
from datetime import date, datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from watering_backend.catalog import (
    DEFAULT_BALCONY,
    SEASONAL_WATER_CURVES,
    WATER_MODEL_CALIBRATION,
)
from watering_backend.plant_model import (
    finish_daily_need,
    pot_factor as model_pot_factor,
    pot_irrigation_efficiency_factor as model_pot_irrigation_efficiency_factor,
)


def clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


def orientation_factor(orientation: str) -> float:
    return {
        "north": 0.78,
        "east": 0.96,
        "south": 1.22,
        "west": 1.1,
        "southwest": 1.28,
        "southeast": 1.16,
    }.get(orientation, 1.0)


def orientation_degrees(balcony: dict) -> float:
    value = balcony.get("orientation_deg")
    if value is not None:
        return float(value) % 360
    return {
        "north": 0,
        "east": 90,
        "south": 180,
        "west": 270,
        "southeast": 135,
        "southwest": 225,
    }.get(balcony.get("orientation", "south"), 180)


def orientation_exposure_factor(degrees: float) -> float:
    south_distance = angular_distance(degrees, 180)
    west_bonus = 0.08 if 190 <= degrees <= 290 else 0
    return clamp(1.28 - south_distance / 260 + west_bonus, 0.72, 1.35)


def temp_factor(temperature_c: float) -> float:
    if temperature_c < 12:
        return 0.45
    if temperature_c < 18:
        return 0.7
    if temperature_c < 24:
        return 1.0
    if temperature_c < 29:
        return 1.24
    if temperature_c < 34:
        return 1.55
    return 1.85


def size_factor(size: str) -> float:
    return {"small": 0.72, "medium": 1.0, "large": 1.34, "tree": 1.65}.get(size, 1.0)


def canopy_size_factor(size: str) -> float:
    return {"small": 0.55, "medium": 1.0, "large": 1.55, "tree": 2.25}.get(size, 1.0)


def pot_factor(pot_type: str) -> float:
    return model_pot_factor(pot_type)


def pot_irrigation_efficiency_factor(pot_type: str) -> float:
    return model_pot_irrigation_efficiency_factor(pot_type)


def pot_surface_area_m2(pot_liters: float) -> float:
    return clamp(0.018 * max(pot_liters, 1) ** 0.55, 0.025, 0.32)


def estimate_reference_et0_mm(
    temperature_c: float,
    sunshine_hours: float | None,
    wind_kmh: float,
) -> float:
    sunshine = sunshine_hours if sunshine_hours is not None else 5.0
    et0 = (
        0.95
        + max(0, temperature_c - 7) * 0.12
        + clamp(sunshine, 0, 15) * 0.23
        + clamp(wind_kmh, 0, 45) * 0.035
    )
    if temperature_c < 10:
        et0 *= 0.65
    return round(clamp(et0, 0.4, 9.5), 2)


def wind_exposure_factor(wind_kmh: float, walls: list[dict]) -> float:
    wall_average = sum(float(wall["height_m"]) for wall in walls) / max(len(walls), 1)
    shelter = clamp(wall_average / 1.8, 0, 0.65)
    wind = clamp(wind_kmh, 0, 65)
    if wind <= 8:
        wind_lift = wind * 0.006
    elif wind <= 25:
        wind_lift = 0.048 + (wind - 8) * 0.012
    else:
        wind_lift = 0.252 + (wind - 25) * 0.018
    return round(1 + wind_lift * (1 - shelter), 3)


def plant_canopy_area_m2(plant: dict) -> float:
    pot_ratio = max(float(plant["pot_liters"]), 1) / max(
        float(plant["recommended_pot_liters"]),
        1,
    )
    pot_limit_factor = clamp(pot_ratio**0.38, 0.45, 1.35)
    return round(
        float(plant["canopy_m2_medium"])
        * canopy_size_factor(plant["size"])
        * pot_limit_factor,
        3,
    )


def seasonal_curve_value(points: list[tuple[int, float]], day_of_year: int) -> float:
    day = max(1, min(366, day_of_year))
    for index in range(1, len(points)):
        prev_day, prev_value = points[index - 1]
        next_day, next_value = points[index]
        if day <= next_day:
            span = max(1, next_day - prev_day)
            progress = (day - prev_day) / span
            return prev_value + (next_value - prev_value) * progress
    return points[-1][1]


def seasonal_profile_key(plant: dict) -> str:
    catalog_id = plant.get("catalog_id")
    category = plant.get("category", "")
    if catalog_id in {"tomato", "zucchini", "cucumber", "eggplant", "chili", "basil"}:
        return "warm_annual"
    if category in {"Gemüse", "Kräuter", "Blühpflanzen", "Kletterpflanzen"}:
        return "annual"
    if category in {"Mediterrane Gehölze", "Obstgehölze", "Beerenobst", "Nadelgehölze"}:
        return "evergreen"
    if category == "Sukkulenten":
        return "succulent"
    return "woody"


def seasonal_water_factor(plant: dict, when: date | None = None) -> dict:
    current_day = (when or date.today()).timetuple().tm_yday
    profile = seasonal_profile_key(plant)
    factor = seasonal_curve_value(SEASONAL_WATER_CURVES[profile], current_day)
    if (
        plant.get("size") == "small"
        and current_day < 190
        and profile in {"warm_annual", "annual"}
    ):
        factor *= 0.78
    elif (
        plant.get("size") == "medium"
        and current_day < 170
        and profile in {"warm_annual", "annual"}
    ):
        factor *= 0.9
    return {
        "factor": round(clamp(factor, 0.18, 1.05), 3),
        "profile": profile,
        "day_of_year": current_day,
    }


def plant_water_need_ml(
    plant: dict,
    plant_sun: dict,
    terrace_sun: dict,
    et0_mm: float,
    rain_mm_effective: float,
    temperature_c: float,
    wind_factor: float,
    calibration_factor: float | None = None,
    target_date: date | None = None,
) -> dict:
    canopy_area = plant_canopy_area_m2(plant)
    season = seasonal_water_factor(plant, target_date)
    sun_ratio = clamp(
        plant_sun["sun_hours"] / max(terrace_sun["theoretical_sun_hours"], 1),
        0.15,
        1.0,
    )
    exposure_multiplier = (
        clamp(0.55 + sun_ratio * 0.75, 0.55, 1.32)
        * plant_sun["wall_shade_factor"]
    )
    growth_temperature = (
        0.55 if temperature_c < 12 else 0.82 if temperature_c < 16 else 1.0
    )
    if temperature_c > 32:
        growth_temperature *= 1.08

    transpiration_ml = (
        et0_mm
        * float(plant["crop_coefficient"])
        * canopy_area
        * 1000
        * exposure_multiplier
        * growth_temperature
        * float(plant["moisture_preference"])
        * wind_factor
    )
    pot_wind_factor = 1 + (wind_factor - 1) * clamp(
        1.15 - max(float(plant["pot_liters"]), 1) / 45,
        0.25,
        1.05,
    )
    substrate_evaporation_ml = (
        et0_mm
        * pot_surface_area_m2(float(plant["pot_liters"]))
        * 1000
        * clamp(0.2 + sun_ratio * 0.34, 0.2, 0.55)
        * pot_wind_factor
    )
    rain_capture_area = (
        pot_surface_area_m2(float(plant["pot_liters"])) + canopy_area * 0.18
    )
    rain_credit_ml = rain_mm_effective * rain_capture_area * 1000
    effective_calibration = (
        WATER_MODEL_CALIBRATION
        if calibration_factor is None
        else float(calibration_factor)
    )
    finished_need = finish_daily_need(
        transpiration_ml=transpiration_ml,
        substrate_evaporation_ml=substrate_evaporation_ml,
        rain_credit_ml=rain_credit_ml,
        pot_type=plant["pot_type"],
        calibration_factor=effective_calibration,
        seasonal_factor=season["factor"],
    )
    raw_daily_need_ml = finished_need["raw_daily_need_ml"]
    daily_need_ml = finished_need["daily_need_ml"]
    return {
        "daily_need_ml": daily_need_ml,
        "raw_daily_need_ml": raw_daily_need_ml,
        "calibration_factor": effective_calibration,
        "seasonal_factor": season["factor"],
        "seasonal_profile": season["profile"],
        "seasonal_day_of_year": season["day_of_year"],
        "canopy_area_m2": canopy_area,
        "transpiration_ml": transpiration_ml,
        "substrate_evaporation_ml": substrate_evaporation_ml,
        "rain_credit_ml": rain_credit_ml,
    }


def side_center_degrees(side: str) -> int:
    return {"north": 0, "east": 90, "south": 180, "west": 270}.get(side, 180)


def angular_distance(a: float, b: float) -> float:
    return abs((a - b + 180) % 360 - 180)


def solar_position(
    latitude: float,
    longitude: float,
    when: datetime,
) -> tuple[float, float]:
    day = when.timetuple().tm_yday
    hour = when.hour + when.minute / 60
    declination = math.radians(
        23.44 * math.sin(math.radians((360 / 365) * (day - 81)))
    )
    latitude_rad = math.radians(latitude)
    solar_time = hour + longitude / 15
    hour_angle = math.radians(15 * (solar_time - 12))
    altitude = math.asin(
        math.sin(latitude_rad) * math.sin(declination)
        + math.cos(latitude_rad) * math.cos(declination) * math.cos(hour_angle)
    )
    azimuth = math.degrees(
        math.atan2(
            math.sin(hour_angle),
            math.cos(hour_angle) * math.sin(latitude_rad)
            - math.tan(declination) * math.cos(latitude_rad),
        )
    )
    return math.degrees(altitude), (azimuth + 180) % 360


def wall_distance_m(
    side: str,
    x: float,
    y: float,
    width_m: float,
    depth_m: float,
) -> float:
    return {
        "north": y * depth_m,
        "south": (1 - y) * depth_m,
        "west": x * width_m,
        "east": (1 - x) * width_m,
    }.get(side, depth_m)


def wall_shadow_block(
    side: str,
    height_m: float,
    x: float,
    y: float,
    width_m: float,
    depth_m: float,
    altitude: float,
    azimuth: float,
) -> float:
    if height_m <= 0 or altitude <= 0:
        return 0.0
    if angular_distance(azimuth, side_center_degrees(side)) > 65:
        return 0.0
    shadow_length = height_m / max(math.tan(math.radians(altitude)), 0.05)
    distance = wall_distance_m(side, x, y, width_m, depth_m)
    if shadow_length <= distance:
        return 0.0
    return clamp(
        (shadow_length - distance) / max(shadow_length, 0.1),
        0.2,
        0.95,
    )


def estimate_sun_hours(
    balcony: dict,
    walls: list[dict],
    target_date: date | None = None,
    position: tuple[float, float] = (0.5, 0.5),
) -> dict:
    target_date = target_date or datetime.now(timezone.utc).date()
    latitude = float(balcony.get("latitude", DEFAULT_BALCONY["latitude"]))
    longitude = float(balcony.get("longitude", DEFAULT_BALCONY["longitude"]))
    wall_map = {wall["side"]: float(wall["height_m"]) for wall in walls}
    terrace_side = orientation_degrees(balcony)
    x, y = position
    width_m = max(float(balcony["width_m"]), 0.4)
    depth_m = max(float(balcony["depth_m"]), 0.4)
    open_hours = 0.0
    theoretical_hours = 0.0
    shade_load = 0.0

    for hour in range(5, 22):
        when = datetime(
            target_date.year,
            target_date.month,
            target_date.day,
            hour,
            tzinfo=timezone.utc,
        )
        altitude, azimuth = solar_position(latitude, longitude, when)
        if altitude <= 0:
            continue
        theoretical_hours += 1
        if angular_distance(azimuth, terrace_side) > 105:
            continue

        blocked = 0.0
        for side, height in wall_map.items():
            blocked = max(
                blocked,
                wall_shadow_block(
                    side,
                    height,
                    x,
                    y,
                    width_m,
                    depth_m,
                    altitude,
                    azimuth,
                ),
            )
        open_hours += 1 - blocked
        shade_load += blocked

    exposure_ratio = (
        0
        if theoretical_hours == 0
        else clamp(open_hours / theoretical_hours, 0, 1.25)
    )
    return {
        "date": target_date.isoformat(),
        "sun_hours": round(open_hours, 1),
        "theoretical_sun_hours": round(theoretical_hours, 1),
        "exposure_factor": round(0.75 + exposure_ratio * 0.65, 2),
        "wall_shade_factor": round(
            1 - clamp(shade_load / max(theoretical_hours, 1), 0, 0.65) * 0.28,
            2,
        ),
    }


def rain_credit_factor(
    rain_mm: float,
    orientation_deg: float,
    walls: list[dict],
) -> float:
    exposure = clamp(
        0.62 - angular_distance(orientation_deg, 225) / 520,
        0.32,
        0.68,
    )
    wall_block = clamp(
        sum(float(wall["height_m"]) for wall in walls) / 8,
        0,
        0.55,
    )
    return max(0.08, rain_mm * exposure * (1 - wall_block))


def current_tube_count(plant: dict) -> int:
    hose_numbers = str(
        plant.get("current_hose_numbers") or plant.get("hose_numbers") or ""
    ).strip()
    if hose_numbers:
        return max(1, len(re.findall(r"\d+", hose_numbers)))
    target_ml = plant.get(
        "current_target_ml_per_cycle",
        plant.get("target_ml_per_cycle"),
    )
    if target_ml not in (None, ""):
        return max(1, math.ceil(float(target_ml) / 15))
    return 1


def _date_in_timezone(timezone_name: str) -> date:
    try:
        tzinfo = ZoneInfo(timezone_name or "Europe/Berlin")
    except ZoneInfoNotFoundError:
        tzinfo = ZoneInfo("Europe/Berlin")
    return datetime.now(tzinfo).date()


def calculate_plant_results(
    balcony: dict,
    walls: list[dict],
    plants: list[dict],
    temperature_c: float,
    rain_mm: float,
    wind_kmh: float,
    sunshine_hours: float | None,
    slot: str = "morning",
    et0_mm: float = 0,
    target_date: date | None = None,
    calibration_factor: float = WATER_MODEL_CALIBRATION,
) -> dict:
    slot_multiplier = {
        "morning": 1.0,
        "midday": 0.72,
        "evening": 0.92,
    }.get(slot, 1.0)
    orientation_deg = orientation_degrees(balcony)
    target_date = target_date or _date_in_timezone(
        str(balcony.get("timezone_name", "Europe/Berlin"))
    )
    terrace_sun = estimate_sun_hours(balcony, walls, target_date=target_date)
    reference_et0_mm = (
        et0_mm
        if et0_mm > 0
        else estimate_reference_et0_mm(
            temperature_c,
            sunshine_hours,
            wind_kmh,
        )
    )
    wind_factor = wind_exposure_factor(wind_kmh, walls)
    orientation_multiplier = orientation_exposure_factor(orientation_deg)
    rain_credit_mm = rain_credit_factor(rain_mm, orientation_deg, walls)

    plant_results = []
    total_need_ml = 0
    for plant in plants:
        plant_sun = estimate_sun_hours(
            balcony,
            walls,
            target_date=target_date,
            position=(float(plant["pos_x"]), float(plant["pos_y"])),
        )
        water_need = plant_water_need_ml(
            plant,
            plant_sun,
            terrace_sun,
            reference_et0_mm * orientation_multiplier * plant["sun_factor"],
            rain_credit_mm,
            temperature_c,
            wind_factor,
            calibration_factor,
            target_date,
        )
        daily_need = water_need["daily_need_ml"]
        scheduled_need = daily_need * slot_multiplier
        total_need_ml += scheduled_need

        plant_results.append(
            {
                "id": plant["id"],
                "name": plant["custom_name"],
                "catalog_name": plant["catalog_name"],
                "pot_liters": plant["pot_liters"],
                "pot_type": plant["pot_type"],
                "drought_sensitivity": plant["drought_sensitivity"],
                "current_outlet": plant["outlet_name"],
                "current_ml_per_run": plant["ml_per_run"],
                "current_hose_numbers": plant["hose_numbers"],
                "current_hoses": plant["hoses"],
                "current_target_ml_per_cycle": plant["configured_ml_per_cycle"],
                "current_tube_count": current_tube_count(plant),
                "position": {"x": plant["pos_x"], "y": plant["pos_y"]},
                "need_ml": round(scheduled_need),
                "daily_need_ml": round(daily_need),
                "water_model": {
                    "reference_et0_mm": reference_et0_mm,
                    "canopy_area_m2": water_need["canopy_area_m2"],
                    "raw_daily_need_ml": round(water_need["raw_daily_need_ml"]),
                    "calibrated_daily_need_ml": round(daily_need),
                    "calibration_factor": water_need["calibration_factor"],
                    "seasonal_factor": water_need["seasonal_factor"],
                    "seasonal_profile": water_need["seasonal_profile"],
                    "seasonal_day_of_year": water_need["seasonal_day_of_year"],
                    "transpiration_ml": round(water_need["transpiration_ml"]),
                    "substrate_evaporation_ml": round(
                        water_need["substrate_evaporation_ml"]
                    ),
                    "rain_credit_ml": round(water_need["rain_credit_ml"]),
                    "crop_coefficient": plant["crop_coefficient"],
                    "recommended_pot_liters": plant["recommended_pot_liters"],
                    "wind_factor": wind_factor,
                },
                "sun": plant_sun,
            }
        )

    return {
        "plants": plant_results,
        "total_need_ml": total_need_ml,
        "terrace_sun": terrace_sun,
        "reference_et0_mm": reference_et0_mm,
        "wind_factor": wind_factor,
        "orientation_deg": orientation_deg,
    }


class EvaluationService:
    """Compose pure demand/routing functions into the daily watering result."""

    def __init__(
        self,
        *,
        state_provider: Callable[[], dict[str, Any]],
        planner_config: Callable[[], dict[str, Any]],
        water_calibration: Callable[[], float],
        main_pump_factor: Callable[[], float],
        calibrated_consumption: Callable[[int | float], int],
        completed_cycles_today: Callable[[str], int],
        latest_watering_at: Callable[[], str],
        local_now: Callable[[str], datetime],
        automation_status: Callable[..., dict[str, Any]],
        refill_status: Callable[[dict[str, Any]], dict[str, Any]],
        connection_optimizer: Callable[..., dict[str, Any]],
        configured_plan: Callable[[list[dict[str, Any]]], dict[str, Any]],
        weather_plan: Callable[..., dict[str, Any]],
        manual_run_status: Callable[[dict[str, Any]], dict[str, Any]],
        manual_refill_status: Callable[[dict[str, Any]], dict[str, Any]],
        depletion_forecast: Callable[
            [dict[str, Any], dict[str, Any] | None],
            dict[str, Any],
        ],
        pause_until: Callable[[], str],
        now_iso: Callable[[], str],
        tank_low_percent: int = 20,
    ):
        self.state_provider = state_provider
        self.planner_config = planner_config
        self.water_calibration = water_calibration
        self.main_pump_factor = main_pump_factor
        self.calibrated_consumption = calibrated_consumption
        self.completed_cycles_today = completed_cycles_today
        self.latest_watering_at = latest_watering_at
        self.local_now = local_now
        self.automation_status = automation_status
        self.refill_status = refill_status
        self.connection_optimizer = connection_optimizer
        self.configured_plan = configured_plan
        self.weather_plan = weather_plan
        self.manual_run_status = manual_run_status
        self.manual_refill_status = manual_refill_status
        self.depletion_forecast = depletion_forecast
        self.pause_until = pause_until
        self.now_iso = now_iso
        self.tank_low_percent = tank_low_percent

    def evaluate(
        self,
        temperature_c: float,
        rain_mm: float,
        wind_kmh: float = 0,
        slot: str = "morning",
        sunshine_hours: float | None = None,
        weather_source: str = "manual",
        et0_mm: float = 0,
    ) -> dict[str, Any]:
        state = self.state_provider()
        balcony = state["balcony"]
        walls = state["walls"]
        plants = state["plants"]
        outlets = state["outlets"]
        timezone_name = str(
            balcony.get("timezone_name", "Europe/Berlin")
        )
        target_date = self.local_now(timezone_name).date()
        calibration_factor = self.water_calibration()
        calculated = calculate_plant_results(
            balcony,
            walls,
            plants,
            temperature_c=temperature_c,
            rain_mm=rain_mm,
            wind_kmh=wind_kmh,
            sunshine_hours=sunshine_hours,
            slot=slot,
            et0_mm=et0_mm,
            target_date=target_date,
            calibration_factor=calibration_factor,
        )
        plant_results = calculated["plants"]
        total_need_ml = calculated["total_need_ml"]
        terrace_sun = calculated["terrace_sun"]
        reference_et0_mm = calculated["reference_et0_mm"]
        wind_factor = calculated["wind_factor"]
        orientation_deg = calculated["orientation_deg"]

        connection_plan = self.connection_optimizer(
            balcony,
            walls,
            plants,
            outlets,
            target_date=target_date,
            calibration_factor=calibration_factor,
        )
        routing_plan = self.weather_plan(
            self.configured_plan(plants),
            plant_results,
            outlets,
            max_cycles=int(self.planner_config()["max_cycles_per_day"]),
        )
        recommended_cycles = int(routing_plan["cycles"])
        actual_assignment_by_plant = {
            assignment["plant_id"]: assignment
            for assignment in routing_plan["assignments"]
        }
        suggested_assignment_by_plant = {
            assignment["plant_id"]: assignment
            for assignment in connection_plan["assignments"]
        }
        for plant in plant_results:
            suggested = suggested_assignment_by_plant.get(plant["id"])
            actual = actual_assignment_by_plant.get(plant["id"])
            if suggested:
                plant["suggested_outlet"] = suggested["outlet_name"]
                plant["suggested_ml_per_run"] = suggested["ml_per_run"]
                plant["suggested_tubes"] = suggested["tubes"]
                plant["suggested_tube_label"] = suggested["tube_label"]
                plant["connection_status"] = suggested.get(
                    "connection_status"
                )
                plant["connection_severity"] = suggested.get(
                    "connection_severity"
                )
                plant["connection_action_title"] = suggested.get(
                    "connection_action_title"
                )
                plant["connection_note"] = suggested.get("connection_note")
            if actual:
                plant["delivered_ml"] = actual["delivered_ml"]
                plant["difference_ml"] = actual["difference_ml"]

        total_delivered_per_run = sum(
            tube["ml_per_run"] * tube["count"]
            for assignment in routing_plan["assignments"]
            for tube in assignment["tubes"]
        )
        cycles_completed = self.completed_cycles_today(timezone_name)
        remaining_cycles = max(0, recommended_cycles - cycles_completed)
        delivered_if_remaining = remaining_cycles * total_delivered_per_run
        consumed_per_run = self.calibrated_consumption(
            total_delivered_per_run
        )
        consumed_if_remaining = remaining_cycles * consumed_per_run
        tank_after = max(
            0,
            int(balcony["tank_current_ml"]) - consumed_if_remaining,
        )
        tank_capacity = max(int(balcony["tank_capacity_ml"]), 1)
        tank_percent = round(
            int(balcony["tank_current_ml"]) / tank_capacity * 100
        )
        tank_after_percent = round(tank_after / tank_capacity * 100)
        tank_empty_soon = bool(
            plants
            and consumed_per_run > 0
            and int(balcony["tank_current_ml"]) < consumed_per_run
        )
        tank_low = (
            tank_percent <= self.tank_low_percent or tank_empty_soon
        )

        hottest_sensitive_need = max(
            (plant["need_ml"] for plant in plant_results),
            default=0,
        )
        rain_threshold = round(
            clamp(
                2.2
                + (temperature_c - 22) * 0.18
                + hottest_sensitive_need / 1200,
                0.8,
                8.0,
            ),
            1,
        )
        temp_threshold = 16 if rain_mm < 1 else 22
        should_run = bool(plants) and remaining_cycles > 0

        if plants and total_delivered_per_run <= 0:
            should_run = False
            reason = "Noch keine nutzbare Verschlauchung vorhanden"
        elif plants and int(balcony["tank_current_ml"]) < consumed_per_run:
            should_run = False
            reason = (
                "Wassertank reicht nicht für einen vollständigen Pumpenlauf"
            )
        elif not plants:
            reason = "Noch keine Pflanzen angelegt"
        elif recommended_cycles == 0:
            reason = (
                "Heute ist mit dem festen Anschlussplan kein Pumpenlauf nötig"
            )
        elif remaining_cycles == 0 and recommended_cycles > 0:
            reason = (
                "Alle empfohlenen Zyklen für heute sind bereits verbucht"
            )
        elif should_run:
            reason = (
                f"Wasserbedarf {round(total_need_ml)} ml "
                "nach Wetteranrechnung"
            )
        else:
            reason = "Heute ist kein automatischer Lauf nötig"

        automation = self.automation_status(
            should_run,
            remaining_cycles,
            timezone_name,
            self.pause_until(),
            self.latest_watering_at(),
            recommended_cycles,
            cycles_completed,
        )
        refill = self.refill_status(balcony)
        result: dict[str, Any] = {
            "should_run": should_run,
            "run_now": automation["run_now"],
            "reason": reason,
            "recommended_cycles_today": recommended_cycles,
            "cycles_completed_today": cycles_completed,
            "remaining_cycles_today": remaining_cycles,
            "thresholds": {
                "temperature_c": temp_threshold,
                "rain_mm": rain_threshold,
            },
            "pump": {
                "duration_seconds": 120,
                "delivered_per_cycle_ml": total_delivered_per_run,
                "delivered_if_remaining_ml": delivered_if_remaining,
                "consumption_factor": self.main_pump_factor(),
                "consumed_per_cycle_ml": consumed_per_run,
                "consumed_if_remaining_ml": consumed_if_remaining,
            },
            "tank": {
                "current_ml": balcony["tank_current_ml"],
                "after_recommended_ml": tank_after,
                "capacity_ml": balcony["tank_capacity_ml"],
                "percent": tank_percent,
                "after_recommended_percent": tank_after_percent,
                "low_percent_threshold": self.tank_low_percent,
                "low": tank_low,
                "empty_soon": tank_empty_soon,
                "warning": (
                    "Tank reicht nicht mehr für einen vollständigen "
                    "Pumpenlauf."
                    if tank_empty_soon
                    else f"Tank unter {self.tank_low_percent} Prozent."
                    if tank_low
                    else ""
                ),
            },
            "refill": refill,
            "routing": routing_plan["by_outlet"],
            "routing_plan": routing_plan,
            "connection_plan": connection_plan,
            "plants": plant_results,
            "inputs": {
                "temperature_c": temperature_c,
                "rain_mm": rain_mm,
                "wind_kmh": wind_kmh,
                "slot": slot,
                "sunshine_hours": sunshine_hours,
                "weather_source": weather_source,
                "orientation_deg": orientation_deg,
                "et0_mm": reference_et0_mm,
                "wind_factor": wind_factor,
            },
            "sun": terrace_sun,
            "automation": automation,
            "calculated_at": self.now_iso(),
        }
        result["manual_run"] = self.manual_run_status(result)
        result["depletion"] = self.depletion_forecast(
            result,
            result["inputs"],
        )
        result["manual_refill"] = self.manual_refill_status(result)
        return result
