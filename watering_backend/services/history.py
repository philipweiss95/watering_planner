from __future__ import annotations

from typing import Any

from watering_backend.repositories.events import EventsRepository


def format_liters_for_text(ml: int | float) -> str:
    return f"{round(float(ml) / 1000, 1):g} l"


def tank_label(tank_name: str) -> str:
    return {
        "main": "Haupttank",
        "refill": "Vorratstank",
    }.get(str(tank_name), str(tank_name))


def refill_event_detail(row: dict[str, Any]) -> str:
    source = str(row["source"])
    window = str(row["window_label"] or "").strip()
    parts = [
        f"{int(row['transferred_ml'])} ml nachgefüllt",
        f"{int(row['duration_seconds'])} s",
    ]
    if source == "manual":
        parts.append("manuell")
    elif window:
        parts.append(window)
    return " · ".join(parts)


class HistoryService:
    def __init__(
        self,
        events: EventsRepository,
        calibrated_consumption,
    ):
        self.events = events
        self.calibrated_consumption = calibrated_consumption

    def watering_events(self, limit: int = 12) -> list[dict[str, Any]]:
        bounded = max(1, min(int(limit), 50))
        events: list[dict[str, Any]] = [
            {
                **row,
                "event_type": "watering",
                "title": "Bewässerung",
                "amount_ml": int(row["delivered_ml"]),
                "duration_seconds": 120,
                "detail": (
                    f"{int(row['delivered_ml'])} ml an Pflanzen · "
                    f"{int(row['actual_consumed_ml'] if row['actual_consumed_ml'] is not None else self.calibrated_consumption(row['delivered_ml']))} ml Tankverbrauch"
                ),
            }
            for row in self.events.watering_events()
        ]
        events.extend(
            {
                **row,
                "event_type": "refill",
                "title": "Nachfüllung",
                "amount_ml": int(row["transferred_ml"]),
                "temperature_c": None,
                "rain_mm": None,
                "detail": refill_event_detail(row),
            }
            for row in self.events.refill_events()
        )
        events.extend(
            {
                **row,
                "event_type": "tank_fill",
                "title": "Tank voll markiert",
                "amount_ml": max(
                    0,
                    int(row["new_ml"]) - int(row["previous_ml"]),
                ),
                "duration_seconds": 0,
                "temperature_c": None,
                "rain_mm": None,
                "detail": (
                    f"{tank_label(row['tank_name'])}: "
                    f"{format_liters_for_text(row['previous_ml'])} -> "
                    f"{format_liters_for_text(row['new_ml'])}"
                ),
            }
            for row in self.events.tank_fill_events()
        )
        return sorted(
            events,
            key=lambda item: (item["ran_at"], int(item["id"])),
            reverse=True,
        )[:bounded]

