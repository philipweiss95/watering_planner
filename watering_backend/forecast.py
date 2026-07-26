from __future__ import annotations

import math
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
    """Simulate watering and gradual refill transfers in one chronological timeline."""
    completed_refill_windows = completed_refill_windows or set()
    main = max(0, min(int(main_current_ml), int(main_capacity_ml)))
    reserve = max(0, min(int(refill_current_ml), int(refill_capacity_ml)))
    timeline: list[tuple[datetime, int, str, Any]] = []
    for item in watering_events:
        at = item["at"]
        if isinstance(at, str):
            at = datetime.fromisoformat(at)
        if at >= now:
            timeline.append((at, 2, "watering", item))
    for start, end in refill_windows:
        window_key = start.isoformat()
        if window_key in completed_refill_windows:
            continue
        if end < now:
            if end.date() == now.date():
                timeline.append((end, 0, "refill_missed", (start, end)))
            continue
        run_at = max(start, now) if start <= now <= end else start
        if run_at >= now:
            timeline.append((run_at, 1, "refill_start", (start, end)))
    timeline.sort(key=lambda item: (item[0], item[1]))

    events: list[dict[str, Any]] = []
    if last_refill_at and last_refill_at.tzinfo is None:
        last_refill_at = last_refill_at.replace(tzinfo=now.tzinfo)
    last_supported = ""
    first_unserved = ""
    main_empty_at = ""
    all_empty_at = ""
    active_refills: list[dict[str, Any]] = []

    def advance_refills(at: datetime) -> None:
        nonlocal main, reserve
        for transfer in active_refills:
            elapsed_seconds = max(0.0, (at - transfer["start_at"]).total_seconds())
            target_transferred = min(
                transfer["planned_ml"],
                int(elapsed_seconds * transfer["pump_ml_per_min"] / 60),
            )
            if at >= transfer["completed_at"]:
                target_transferred = transfer["planned_ml"]
            increment = max(0, target_transferred - transfer["transferred_ml"])
            increment = min(increment, reserve, max(0, int(main_capacity_ml) - main))
            if increment:
                main += increment
                reserve -= increment
                transfer["transferred_ml"] += increment

    while timeline:
        timeline.sort(key=lambda item: (item[0], item[1]))
        at, _, kind, payload = timeline.pop(0)
        advance_refills(at)
        main_before = main
        reserve_before = reserve
        if kind == "watering":
            consumed = max(0, int(payload.get("consumed_ml", payload.get("planned_water_ml", 0))))
            delivered = max(0, int(payload.get("delivered_ml", consumed)))
            if consumed > 0 and main >= consumed:
                main -= consumed
                status = "successful"
                if not first_unserved:
                    last_supported = at.isoformat()
            else:
                status = "unserved"
                if not first_unserved:
                    first_unserved = at.isoformat()
                    main_empty_at = at.isoformat()
                    if reserve <= 0:
                        all_empty_at = at.isoformat()
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
                    estimated_weather=bool(payload.get("estimated", False)),
                )
            )
            if not all_empty_at and main <= 0 and reserve <= 0:
                all_empty_at = at.isoformat()
            continue

        if kind == "refill_complete":
            transfer = payload
            if transfer in active_refills:
                active_refills.remove(transfer)
            transferred = int(transfer["transferred_ml"])
            last_refill_at = at if transferred > 0 else last_refill_at
            events.append(
                _event(
                    at,
                    "refill",
                    transfer["planned_ml"],
                    transfer["main_before_ml"],
                    main,
                    transfer["reserve_before_ml"],
                    reserve,
                    "estimated" if transferred > 0 else "unserved",
                    transferred_ml=transferred,
                    started_at=transfer["start_at"].isoformat(),
                    completed_at=at.isoformat(),
                    duration_seconds=transfer["duration_seconds"],
                    window_start=transfer["window_start"].isoformat(),
                    window_end=transfer["window_end"].isoformat(),
                    blocked_reason="" if transferred > 0 else "capacity_or_reserve_changed",
                    limited_by_window=transfer["limited_by_window"],
                )
            )
            if not all_empty_at and main <= 0 and reserve <= 0:
                all_empty_at = at.isoformat()
            continue

        start, end = payload
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
        blocked_reason = ""
        if kind == "refill_missed":
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
        planned_transfer = (
            min(requested, reserve, max_by_window)
            if not blocked_reason
            else 0
        )
        if planned_transfer <= 0:
            events.append(
                _event(
                    at,
                    "refill",
                    requested,
                    main_before,
                    main,
                    reserve_before,
                    reserve,
                    "successful" if blocked_reason == "main_tank_full" else "unserved",
                    transferred_ml=0,
                    started_at=at.isoformat(),
                    completed_at=at.isoformat(),
                    duration_seconds=0,
                    window_start=start.isoformat(),
                    window_end=end.isoformat(),
                    blocked_reason=blocked_reason,
                    limited_by_window=False,
                )
            )
            continue

        duration_seconds = math.ceil(planned_transfer / int(refill_pump_ml_per_min) * 60)
        completed_at = min(end, at + timedelta(seconds=duration_seconds))
        transfer = {
            "start_at": at,
            "completed_at": completed_at,
            "window_start": start,
            "window_end": end,
            "pump_ml_per_min": int(refill_pump_ml_per_min),
            "planned_ml": int(planned_transfer),
            "transferred_ml": 0,
            "duration_seconds": int((completed_at - at).total_seconds()),
            "main_before_ml": main_before,
            "reserve_before_ml": reserve_before,
            "limited_by_window": planned_transfer < requested and max_by_window <= planned_transfer,
        }
        active_refills.append(transfer)
        timeline.append((completed_at, 0, "refill_complete", transfer))

    return {
        "forecast_events": events,
        "last_supported_watering_at": last_supported,
        "first_unserved_watering_at": first_unserved,
        "main_empty_at": main_empty_at,
        "all_empty_at": all_empty_at,
        "main_tank_after_forecast_ml": main,
        "refill_tank_after_forecast_ml": reserve,
    }
