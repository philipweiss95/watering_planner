from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, time, timedelta, timezone
import math
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


MIN_NOTIFICATION_WORKER_INTERVAL_SECONDS = 10
MAX_NOTIFICATION_WORKER_INTERVAL_SECONDS = 3600
LEGACY_MAX_NOTIFICATION_WORKER_INTERVAL_SECONDS = 86400


DEFAULT_PLANNER_CONFIG: dict[str, Any] = {
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
}


def _minutes(value: object, field: str) -> int:
    if not isinstance(value, str):
        raise ValueError(f"{field} muss im Format HH:MM angegeben werden")
    try:
        parsed = datetime.strptime(value, "%H:%M")
    except ValueError as exc:
        raise ValueError(f"{field} muss im Format HH:MM angegeben werden") from exc
    return parsed.hour * 60 + parsed.minute


def _bounded_int(value: object, field: str, minimum: int, maximum: int) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} muss eine ganze Zahl sein") from exc
    if not minimum <= result <= maximum:
        raise ValueError(f"{field} muss zwischen {minimum} und {maximum} liegen")
    return result


def _bounded_float(value: object, field: str, minimum: float, maximum: float) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} muss eine Zahl sein") from exc
    if not math.isfinite(result) or not minimum <= result <= maximum:
        raise ValueError(f"{field} muss zwischen {minimum:g} und {maximum:g} liegen")
    return result


def validate_planner_config(value: object) -> dict[str, Any]:
    if value is None:
        value = {}
    if not isinstance(value, dict):
        raise ValueError("planner_config muss ein Objekt sein")
    unknown = set(value) - set(DEFAULT_PLANNER_CONFIG)
    if unknown:
        raise ValueError(f"Unbekannte Planer-Einstellung: {sorted(unknown)[0]}")

    result = deepcopy(DEFAULT_PLANNER_CONFIG)
    result.update(value)
    start = _minutes(result["watering_window_start"], "watering_window_start")
    end = _minutes(result["watering_window_end"], "watering_window_end")
    if end <= start:
        raise ValueError("Das Bewaesserungszeitfenster muss am selben Tag enden und nach dem Beginn liegen")

    max_cycles = _bounded_int(result["max_cycles_per_day"], "max_cycles_per_day", 1, 96)
    spacing = _bounded_int(
        result["watering_min_interval_minutes"],
        "watering_min_interval_minutes",
        1,
        1440,
    )
    if max_cycles > 1 and (max_cycles - 1) * spacing > end - start:
        raise ValueError("Im Bewaesserungszeitfenster ist der Mindestabstand fuer die maximale Zykluszahl nicht einhaltbar")
    result["max_cycles_per_day"] = max_cycles
    result["watering_min_interval_minutes"] = spacing

    windows = result["refill_windows"]
    if not isinstance(windows, list):
        raise ValueError("refill_windows muss eine Liste sein")
    normalized_windows: list[dict[str, str]] = []
    occupied: list[tuple[int, int]] = []
    for index, window in enumerate(windows):
        if not isinstance(window, dict):
            raise ValueError("Jedes Nachfuellzeitfenster muss start und end enthalten")
        window_start = _minutes(window.get("start"), f"refill_windows[{index}].start")
        window_end = _minutes(window.get("end"), f"refill_windows[{index}].end")
        if window_end <= window_start:
            raise ValueError("Nachfuellzeitfenster muessen am selben Tag enden und nach dem Beginn liegen")
        if any(window_start < other_end and other_start < window_end for other_start, other_end in occupied):
            raise ValueError("Nachfuellzeitfenster duerfen sich nicht ueberschneiden")
        occupied.append((window_start, window_end))
        normalized_windows.append(
            {"start": str(window["start"]), "end": str(window["end"])}
        )
    result["refill_windows"] = sorted(normalized_windows, key=lambda item: item["start"])
    result["refill_min_interval_minutes"] = _bounded_int(
        result["refill_min_interval_minutes"],
        "refill_min_interval_minutes",
        0,
        10080,
    )
    if result["refill_strategy"] not in {"fraction", "target"}:
        raise ValueError("refill_strategy muss fraction oder target sein")
    result["refill_fraction"] = _bounded_float(result["refill_fraction"], "refill_fraction", 0.01, 1)
    result["refill_target_ml"] = _bounded_int(result["refill_target_ml"], "refill_target_ml", 0, 1_000_000)
    if result["refill_strategy"] == "target" and result["refill_target_ml"] <= 0:
        raise ValueError("Bei refill_strategy=target muss refill_target_ml groesser als 0 sein")
    result["weather_stale_after_minutes"] = _bounded_int(
        result["weather_stale_after_minutes"],
        "weather_stale_after_minutes",
        1,
        10080,
    )
    result["weather_cache_minutes"] = _bounded_int(
        result["weather_cache_minutes"],
        "weather_cache_minutes",
        1,
        1440,
    )
    result["missed_watering_tolerance_minutes"] = _bounded_int(
        result["missed_watering_tolerance_minutes"],
        "missed_watering_tolerance_minutes",
        1,
        1440,
    )
    result["notification_cooldown_minutes"] = _bounded_int(
        result["notification_cooldown_minutes"],
        "notification_cooldown_minutes",
        0,
        43200,
    )
    result["notification_retry_minutes"] = _bounded_int(
        result["notification_retry_minutes"],
        "notification_retry_minutes",
        1,
        1440,
    )
    result["notification_resolved_enabled"] = bool(result["notification_resolved_enabled"])
    result["supply_warning_days"] = _bounded_int(result["supply_warning_days"], "supply_warning_days", 1, 365)
    result["notification_worker_interval_seconds"] = _bounded_int(
        result["notification_worker_interval_seconds"],
        "notification_worker_interval_seconds",
        MIN_NOTIFICATION_WORKER_INTERVAL_SECONDS,
        MAX_NOTIFICATION_WORKER_INTERVAL_SECONDS,
    )
    return result


def local_timezone(name: str):
    try:
        return ZoneInfo(name or "Europe/Berlin")
    except ZoneInfoNotFoundError:
        return ZoneInfo("Europe/Berlin")


def parse_hhmm(value: str) -> time:
    try:
        return datetime.strptime(value, "%H:%M").time()
    except (TypeError, ValueError) as exc:
        raise ValueError("Zeit muss im Format HH:MM angegeben werden") from exc


def local_now(timezone_name: str) -> datetime:
    return datetime.now(timezone.utc).astimezone(local_timezone(timezone_name))


def window_datetime(day: date, value: str, tzinfo) -> datetime:
    return datetime.combine(day, parse_hhmm(value), tzinfo=tzinfo)


def local_day_utc_bounds(day: date, tzinfo) -> tuple[datetime, datetime]:
    start = datetime.combine(day, time.min, tzinfo=tzinfo)
    end = datetime.combine(day + timedelta(days=1), time.min, tzinfo=tzinfo)
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


def distributed_windows(day, total_cycles: int, timezone_name: str, config: dict[str, Any]):
    if total_cycles <= 0:
        return []
    if total_cycles > int(config["max_cycles_per_day"]):
        total_cycles = int(config["max_cycles_per_day"])
    tzinfo = local_timezone(timezone_name)
    start_time = datetime.strptime(config["watering_window_start"], "%H:%M").time()
    end_time = datetime.strptime(config["watering_window_end"], "%H:%M").time()
    start = datetime.combine(day, start_time, tzinfo=tzinfo)
    end = datetime.combine(day, end_time, tzinfo=tzinfo)
    if total_cycles == 1:
        return [start]
    natural_spacing = (end - start) / (total_cycles - 1)
    minimum_spacing = timedelta(minutes=int(config["watering_min_interval_minutes"]))
    if natural_spacing < minimum_spacing:
        raise ValueError("Die geplanten Bewaesserungszyklen unterschreiten den Mindestabstand")
    return [start + natural_spacing * index for index in range(total_cycles)]
