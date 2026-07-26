from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any, Callable, Mapping
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from watering_backend.config import DEFAULT_PLANNER_CONFIG, distributed_windows


PlannerConfigProvider = Callable[[], Mapping[str, Any]]
LocalClock = Callable[[str], datetime]


def local_now(timezone_name: str) -> datetime:
    try:
        tzinfo = ZoneInfo(timezone_name or "Europe/Berlin")
    except ZoneInfoNotFoundError:
        tzinfo = ZoneInfo("Europe/Berlin")
    return datetime.now(tzinfo)


def parse_hhmm(value: str) -> time:
    hour, minute = value.split(":", 1)
    return time(int(hour), int(minute))


def window_datetime(today: date, value: str, tzinfo) -> datetime:
    return datetime.combine(today, parse_hhmm(value), tzinfo=tzinfo)


def distributed_automation_windows(
    today: date,
    total_cycles: int,
    tzinfo,
    planner_config: Mapping[str, Any],
) -> list[datetime]:
    return distributed_windows(
        today,
        total_cycles,
        getattr(tzinfo, "key", str(tzinfo)),
        dict(planner_config),
    )


def parse_pause_until(
    value: str,
    timezone_name: str,
    *,
    timezone_reference: datetime | None = None,
) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    reference = timezone_reference or local_now(timezone_name)
    timezone = reference.tzinfo
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone)
    return parsed.astimezone(timezone)


def automation_status(
    should_run: bool,
    remaining_cycles: int,
    timezone_name: str,
    pause_until_value: str = "",
    latest_run_value: str = "",
    recommended_cycles: int | None = None,
    completed_cycles: int | None = None,
    *,
    planner_config: Mapping[str, Any],
    now: datetime,
) -> dict[str, Any]:
    config = planner_config
    tolerance = timedelta(minutes=int(config["missed_watering_tolerance_minutes"]))
    total_cycles = max(
        0,
        int(recommended_cycles if recommended_cycles is not None else remaining_cycles),
    )
    completed = max(
        0,
        int(completed_cycles if completed_cycles is not None else total_cycles - remaining_cycles),
    )
    windows = distributed_automation_windows(now.date(), total_cycles, now.tzinfo, config)
    day_start = window_datetime(now.date(), str(config["watering_window_start"]), now.tzinfo)
    day_end = window_datetime(now.date(), str(config["watering_window_end"]), now.tzinfo)
    pause_until = parse_pause_until(
        pause_until_value,
        timezone_name,
        timezone_reference=now,
    )
    paused = bool(pause_until and pause_until > now)
    latest_run = parse_pause_until(
        latest_run_value,
        timezone_name,
        timezone_reference=now,
    )
    cooldown_minutes = int(config["watering_min_interval_minutes"])
    cooldown_until = latest_run + timedelta(minutes=cooldown_minutes) if latest_run else None
    cooldown_active = bool(cooldown_until and cooldown_until > now)
    due_windows = [item for item in windows if item <= now]
    future_windows = [item for item in windows if item > now]
    next_window = future_windows[0] if future_windows else None
    behind_schedule = completed < len(due_windows)
    catch_up = bool(
        behind_schedule
        and due_windows
        and now > due_windows[min(completed, len(due_windows) - 1)] + tolerance
    )
    distribution_active = day_start <= now <= day_end + tolerance
    run_now = bool(
        should_run
        and remaining_cycles > 0
        and distribution_active
        and behind_schedule
        and not paused
        and not cooldown_active
    )
    if paused:
        summary = f"Automatik pausiert bis {pause_until.strftime('%d.%m. %H:%M')}."
    elif cooldown_active:
        summary = f"Pause nach letztem Lauf bis {cooldown_until.strftime('%H:%M')}."
    elif not should_run or remaining_cycles <= 0:
        summary = "Kein automatischer Lauf n\u00f6tig."
    elif run_now:
        summary = "Geplanter Zeitpunkt erreicht: Home Assistant darf jetzt einen Zyklus starten."
    elif next_window:
        summary = f"N\u00e4chster geplanter Lauf um {next_window.strftime('%H:%M')}."
    else:
        summary = "Heute kein geplanter Lauf mehr."
    return {
        "run_now": run_now,
        "paused": paused,
        "pause_until": pause_until.isoformat() if pause_until else "",
        "cooldown_active": cooldown_active,
        "cooldown_until": cooldown_until.isoformat() if cooldown_until else "",
        "cooldown_minutes": cooldown_minutes,
        "windows": [item.strftime("%H:%M") for item in windows],
        "distribution_start": config["watering_window_start"],
        "distribution_end": config["watering_window_end"],
        "active_window": (
            due_windows[-1].strftime("%H:%M")
            if behind_schedule and distribution_active
            else ""
        ),
        "next_window": next_window.strftime("%H:%M") if next_window else "",
        "regular_slots_remaining": len(future_windows),
        "due_cycles": len(due_windows),
        "catch_up": catch_up,
        "shortfall_prevention": catch_up,
        "summary": summary,
    }


def refill_window_datetimes(
    today: date,
    tzinfo,
    schedule_times: list[str] | None = None,
    *,
    planner_config: Mapping[str, Any] | None = None,
) -> list[datetime]:
    if schedule_times:
        times = schedule_times
    else:
        config = planner_config or DEFAULT_PLANNER_CONFIG
        times = [
            str(item["start"])
            for item in config.get("refill_windows", [])
            if isinstance(item, Mapping) and item.get("start")
        ]
        if not times:
            times = [
                str(item["start"])
                for item in DEFAULT_PLANNER_CONFIG["refill_windows"]
            ]
    return [window_datetime(today, item, tzinfo) for item in times]


@dataclass(frozen=True)
class SchedulingService:
    planner_config_provider: PlannerConfigProvider
    clock: LocalClock = local_now

    def planner_config(self) -> dict[str, Any]:
        return dict(self.planner_config_provider())

    def local_now(self, timezone_name: str) -> datetime:
        return self.clock(timezone_name)

    def distributed_automation_windows(
        self,
        today: date,
        total_cycles: int,
        tzinfo,
    ) -> list[datetime]:
        return distributed_automation_windows(
            today,
            total_cycles,
            tzinfo,
            self.planner_config(),
        )

    def parse_pause_until(self, value: str, timezone_name: str) -> datetime | None:
        return parse_pause_until(
            value,
            timezone_name,
            timezone_reference=self.local_now(timezone_name),
        )

    def automation_status(
        self,
        should_run: bool,
        remaining_cycles: int,
        timezone_name: str,
        pause_until_value: str = "",
        latest_run_value: str = "",
        recommended_cycles: int | None = None,
        completed_cycles: int | None = None,
    ) -> dict[str, Any]:
        return automation_status(
            should_run,
            remaining_cycles,
            timezone_name,
            pause_until_value,
            latest_run_value,
            recommended_cycles,
            completed_cycles,
            planner_config=self.planner_config(),
            now=self.local_now(timezone_name),
        )

    def refill_window_datetimes(
        self,
        today: date,
        tzinfo,
        schedule_times: list[str] | None = None,
    ) -> list[datetime]:
        return refill_window_datetimes(
            today,
            tzinfo,
            schedule_times,
            planner_config=self.planner_config(),
        )
