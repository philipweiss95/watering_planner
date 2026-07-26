from __future__ import annotations

from typing import Any


def number_or_default(value: Any, default: float) -> float:
    if value is None:
        return float(default)
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def indexed_number(payload: dict, key: str, index: int, default: float) -> float:
    value = payload.get(key, default)
    if isinstance(value, list):
        value = value[index] if index < len(value) and value[index] is not None else default
    return number_or_default(value, default)


def normalize_daily_forecast(daily: dict) -> list[dict]:
    """Normalize daily fields without borrowing values from current conditions."""
    dates = daily.get("time", [])
    if not isinstance(dates, list):
        return []
    result = []
    for index, day_value in enumerate(dates):
        result.append(
            {
                "date": str(day_value),
                "temperature_c": indexed_number(daily, "temperature_2m_max", index, 20),
                "rain_mm": indexed_number(daily, "precipitation_sum", index, 0),
                "wind_kmh": indexed_number(daily, "wind_speed_10m_max", index, 0),
                "sunshine_hours": round(indexed_number(daily, "sunshine_duration", index, 0) / 3600, 1),
                "et0_mm": indexed_number(daily, "et0_fao_evapotranspiration", index, 0),
            }
        )
    return result


def manual_simulation(payload: dict) -> dict:
    return {
        "source": "manual",
        "mode": "simulation",
        "simulation": True,
        "temperature_c": float(payload.get("temperature_c", 20)),
        "rain_mm": float(payload.get("rain_mm", 0)),
        "wind_kmh": float(payload.get("wind_kmh", 0)),
        "sunshine_hours": float(payload.get("sunshine_hours", 0)),
        "et0_mm": float(payload.get("et0_mm", 0)),
    }
