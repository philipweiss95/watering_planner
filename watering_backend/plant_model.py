from __future__ import annotations

from typing import Any


POT_RETENTION_FACTORS = {
    "reservoir": 0.72,
    "overflow": 0.9,
    "reservoir_overflow": 0.66,
    "closed": 0.82,
}

POT_IRRIGATION_EFFICIENCY = {
    "reservoir": 0.84,
    "overflow": 1.0,
    "reservoir_overflow": 0.8,
    "closed": 0.78,
}


def pot_factor(pot_type: str) -> float:
    return POT_RETENTION_FACTORS.get(pot_type, 1.0)


def pot_irrigation_efficiency_factor(pot_type: str) -> float:
    return POT_IRRIGATION_EFFICIENCY.get(pot_type, 1.0)


def finish_daily_need(
    *,
    transpiration_ml: float,
    substrate_evaporation_ml: float,
    rain_credit_ml: float,
    pot_type: str,
    calibration_factor: float,
    seasonal_factor: float,
) -> dict[str, Any]:
    """Apply pot efficiency once to gross irrigation demand.

    The physical ET0 components describe water leaving plant and substrate.
    The pot construction changes how much irrigation must replace that loss, so
    its efficiency belongs around the combined loss, not inside either component.
    """
    gross_irrigation_ml = (
        transpiration_ml + substrate_evaporation_ml
    ) * pot_irrigation_efficiency_factor(pot_type)
    raw_daily_need_ml = max(0.0, gross_irrigation_ml - rain_credit_ml)
    return {
        "gross_irrigation_ml": gross_irrigation_ml,
        "raw_daily_need_ml": raw_daily_need_ml,
        "daily_need_ml": raw_daily_need_ml * calibration_factor * seasonal_factor,
    }
