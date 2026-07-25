from __future__ import annotations

from datetime import date, datetime

from watering_backend.config import distributed_windows, local_timezone


def watering_times(day: date, total_cycles: int, timezone_name: str, config: dict) -> list[datetime]:
    return distributed_windows(day, total_cycles, timezone_name, config)


def refill_times(day: date, timezone_name: str, config: dict) -> list[tuple[datetime, datetime]]:
    tzinfo = local_timezone(timezone_name)
    return [
        (
            datetime.combine(day, datetime.strptime(item["start"], "%H:%M").time(), tzinfo=tzinfo),
            datetime.combine(day, datetime.strptime(item["end"], "%H:%M").time(), tzinfo=tzinfo),
        )
        for item in config["refill_windows"]
    ]
