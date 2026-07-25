from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any


def _event(
    at: datetime,
    event_type: str,
    planned_ml: int,
    main_before: int,
    main_after: int,
    refill_before: int,
    refill_after: int,
    status: str,
    **extra: Any,
) -> dict[str, Any]:
    return {
        "at": at.isoformat(),
        "date": at.date().isoformat(),
        "event_type": event_type,
        "planned_water_ml": int(planned_ml),
        "main_tank_before_ml": int(main_before),
        "main_tank_after_ml": int(main_after),
        "refill_tank_before_ml": int(refill_before),
        "refill_tank_after_ml": int(refill_after),
        "status": status,
        **extra,
    }


def simulate_tanks(
    *,
    now: datetime,
    watering_events: list[dict[str, Any]],
    refill_windows: list[tuple[datetime, datetime]],
    main_current_ml: int,
    main_capacity_ml: int,
    refill_current_ml: int,
    refill_capacity_ml: int,
    refill_enabled: bool,
    refill_pump_ml_per_min: int,
    refill_min_interval_minutes: int,
    refill_strategy: str,
    refill_fraction: float,
    refill_target_ml: int,
    completed_refill_windows: set[str] | None = None,
    last_refill_at: datetime | None = None,
) -> dict[str, Any]:
    """Simulate every transfer in time order; reserve water is never directly drinkable."""
    completed_refill_windows = completed_refill_windows or set()
    main = max(0, min(int(main_current_ml), int(main_capacity_ml)))
    reserve = max(0, min(int(refill_current_ml), int(refill_capacity_ml)))
    timeline: list[tuple[datetime, int, str, Any]] = []
    for item in watering_events:
        at = item["at"]
        if isinstance(at, str):
            at = datetime.fromisoformat(at)
        if at >= now:
            timeline.append((at, 1, "watering", item))
    for start, end in refill_windows:
        window_key = start.isoformat()
        if window_key in completed_refill_windows:
            continue
        if end < now:
            if end.date() == now.date():
                timeline.append((end, 0, "refill", (start, end)))
            continue
        if end >= now:
            run_at = max(start, now) if start <= now <= end else start
            if run_at >= now:
                timeline.append((run_at, 0, "refill", (start, end)))
    timeline.sort(key=lambda item: (item[0], item[1]))

    events: list[dict[str, Any]] = []
    if last_refill_at and last_refill_at.tzinfo is None:
        last_refill_at = last_refill_at.replace(tzinfo=now.tzinfo)
    last_supported = ""
    first_unserved = ""
    for at, _, kind, payload in timeline:
        main_before = main
        reserve_before = reserve
        if kind == "watering":
            consumed = max(0, int(payload.get("consumed_ml", payload.get("planned_water_ml", 0))))
            delivered = max(0, int(payload.get("delivered_ml", consumed)))
            if consumed > 0 and main >= consumed:
                main -= consumed
                status = "successful"
                last_supported = at.isoformat()
            else:
                status = "unserved"
                if not first_unserved:
                    first_unserved = at.isoformat()
            events.append(
                _event(
                    at,
                    "watering",
                    consumed,
                    main_before,
                    main,
                    reserve_before,
                    reserve,
                    status,
                    delivered_to_plants_ml=delivered,
                    consumed_from_main_tank_ml=consumed,
                )
            )
            continue

        start, end = payload
        missed_window = end < now
        missing = max(0, int(main_capacity_ml) - main)
        if refill_strategy == "target":
            requested = min(missing, max(0, int(refill_target_ml)))
        else:
            requested = min(missing, max(0, round(missing * float(refill_fraction))))
        max_by_window = max(
            0,
            int((end - max(start, at)).total_seconds() / 60 * max(0, int(refill_pump_ml_per_min))),
        )
        interval_ok = (
            last_refill_at is None
            or at - last_refill_at >= timedelta(minutes=max(0, int(refill_min_interval_minutes)))
        )
        transfer = (
            min(requested, reserve, max_by_window)
            if refill_enabled and refill_pump_ml_per_min > 0 and interval_ok and not missed_window
            else 0
        )
        if transfer > 0:
            main += transfer
            reserve -= transfer
            last_refill_at = at
        blocked_reason = ""
        if missed_window:
            blocked_reason = "missed_window"
        elif not refill_enabled:
            blocked_reason = "disabled"
        elif refill_pump_ml_per_min <= 0:
            blocked_reason = "pump_flow_missing"
        elif not interval_ok:
            blocked_reason = "minimum_interval"
        elif reserve <= 0:
            blocked_reason = "refill_tank_empty"
        elif missing <= 0:
            blocked_reason = "main_tank_full"
        elif max_by_window <= 0:
            blocked_reason = "missed_window"
        events.append(
            _event(
                at,
                "refill",
                requested,
                main_before,
                main,
                reserve_before,
                reserve,
                "estimated" if transfer > 0 else "unserved",
                transferred_ml=transfer,
                window_start=start.isoformat(),
                window_end=end.isoformat(),
                blocked_reason=blocked_reason,
            )
        )

    return {
        "forecast_events": events,
        "last_supported_watering_at": last_supported,
        "first_unserved_watering_at": first_unserved,
        "main_tank_after_forecast_ml": main,
        "refill_tank_after_forecast_ml": reserve,
    }
