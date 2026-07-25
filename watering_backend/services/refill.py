from __future__ import annotations

import json
import math
from datetime import date, datetime, timedelta, timezone
from typing import Any, Protocol

from watering_backend.config import local_timezone
from watering_backend.repositories.events import EventsRepository
from watering_backend.repositories.settings import SettingsRepository
from watering_backend.repositories.tanks import TanksRepository
from watering_backend.scheduling import refill_times


class Clock(Protocol):
    def __call__(self) -> datetime: ...


class RefillScheduler(Protocol):
    def __call__(
        self,
        day: date,
        timezone_name: str,
        config: dict[str, Any],
    ) -> list[tuple[datetime, datetime]]: ...


def system_now() -> datetime:
    return datetime.now(timezone.utc)


def aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def parse_event_datetime(value: object) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def format_liters_for_text(ml: int | float) -> str:
    return f"{round(float(ml) / 1000, 1):g} l"


class RefillService:
    """Plan refill windows and persist their observations without HTTP state."""

    def __init__(
        self,
        events: EventsRepository,
        settings: SettingsRepository,
        tanks: TanksRepository,
        *,
        now: Clock = system_now,
        scheduler: RefillScheduler = refill_times,
        tank_low_percent: int = 20,
        minimum_cooldown_minutes: int = 15,
        maximum_cooldown_minutes: int = 12 * 60,
        pending_request_max_age: timedelta = timedelta(hours=2),
    ):
        self.events = events
        self.settings = settings
        self.tanks = tanks
        self._now = now
        self._scheduler = scheduler
        self.tank_low_percent = int(tank_low_percent)
        self.minimum_cooldown_minutes = int(minimum_cooldown_minutes)
        self.maximum_cooldown_minutes = int(maximum_cooldown_minutes)
        self.pending_request_max_age = pending_request_max_age

    def _utc_now(self) -> datetime:
        return aware_utc(self._now())

    def _local_now(self, timezone_name: str) -> datetime:
        return self._utc_now().astimezone(local_timezone(timezone_name))

    def cooldown_minutes(self, transferred_ml: int | float) -> int:
        if transferred_ml <= 0:
            return 0
        minutes = math.ceil(
            float(transferred_ml)
            / 1000
            * self.settings.refill_cooldown_minutes_per_liter()
        )
        return int(
            max(
                self.minimum_cooldown_minutes,
                min(minutes, self.maximum_cooldown_minutes),
            )
        )

    def latest_event(self) -> dict[str, Any] | None:
        return self.events.latest_refill()

    def status(self, balcony: dict[str, Any] | None = None) -> dict[str, Any]:
        balcony = balcony or self.tanks.balcony()
        timezone_name = str(
            balcony.get("timezone_name", "Europe/Berlin")
        )
        now = self._local_now(timezone_name)
        config = self.settings.planner_config()
        target_date = now.date()
        enabled = self.settings.refill_automation_enabled()
        schedule_times = [
            str(item["start"]) for item in config["refill_windows"]
        ]
        main_capacity = max(int(balcony.get("tank_capacity_ml", 0)), 0)
        main_current = max(int(balcony.get("tank_current_ml", 0)), 0)
        refill_capacity = max(
            int(balcony.get("refill_tank_capacity_ml", 30000)),
            1,
        )
        refill_current = max(
            int(balcony.get("refill_tank_current_ml", 0)),
            0,
        )
        pump_ml_per_min = max(
            int(balcony.get("refill_pump_ml_per_min", 0)),
            0,
        )
        main_missing_ml = max(0, main_capacity - main_current)
        requested_ml = main_missing_ml
        target_transfer_ml = (
            min(requested_ml, int(config["refill_target_ml"]))
            if config["refill_strategy"] == "target"
            else math.ceil(
                requested_ml * float(config["refill_fraction"])
            )
        )
        transferable_ml = min(
            target_transfer_ml,
            main_missing_ml,
            refill_current,
        )
        duration_seconds = (
            math.ceil(transferable_ml / pump_ml_per_min * 60)
            if pump_ml_per_min and transferable_ml
            else 0
        )

        scheduled_pairs = self._scheduler(
            now.date(),
            timezone_name,
            config,
        )
        refill_window_pairs = [
            (start, end, str(window["start"]))
            for (start, end), window in zip(
                scheduled_pairs,
                config["refill_windows"],
            )
        ]
        refill_windows = [item[0] for item in refill_window_pairs]
        elapsed_windows = [
            start
            for start, end, _label in refill_window_pairs
            if end < now
        ]
        eligible_windows = [
            start
            for start, end, _label in refill_window_pairs
            if start <= now <= end
        ]
        pending_windows = [
            item
            for item in eligible_windows
            if not self.events.refill_for_target(
                target_date,
                item.strftime("%H:%M"),
            )
        ]
        completed_windows = [
            item
            for item in elapsed_windows
            if self.events.refill_for_target(
                target_date,
                item.strftime("%H:%M"),
            )
        ]
        active_window = pending_windows[0] if pending_windows else None
        active_window_label = (
            active_window.strftime("%H:%M") if active_window else ""
        )
        next_window = next(
            (item for item in refill_windows if item > now),
            None,
        )
        if not next_window and config["refill_windows"]:
            next_window = self._scheduler(
                now.date() + timedelta(days=1),
                timezone_name,
                config,
            )[0][0]

        last_event = self.latest_event()
        last_event_at = (
            parse_event_datetime(last_event.get("ran_at"))
            if last_event
            else None
        )
        cooldown_until = (
            last_event_at.astimezone(now.tzinfo)
            + timedelta(
                minutes=int(config["refill_min_interval_minutes"])
            )
            if last_event_at
            else None
        )
        cooldown_active = bool(cooldown_until and now < cooldown_until)
        schedule_due = bool(active_window)
        need_exists = bool(
            transferable_ml > 0 and pump_ml_per_min > 0
        )
        run_now = bool(
            enabled
            and schedule_due
            and need_exists
            and not cooldown_active
        )
        catch_up = False
        eligible_now = bool(
            enabled
            and main_missing_ml > 0
            and refill_current > 0
            and pump_ml_per_min > 0
            and not cooldown_active
        )

        if active_window:
            active_pair = next(
                (
                    item
                    for item in refill_window_pairs
                    if item[0] == active_window
                ),
                None,
            )
            if active_pair:
                blocking_reason = (
                    "disabled"
                    if not enabled
                    else "main_tank_full"
                    if main_missing_ml <= 0
                    else "refill_tank_empty"
                    if refill_current <= 0
                    else "pump_flow_missing"
                    if pump_ml_per_min <= 0
                    else "cooldown"
                    if cooldown_active
                    else ""
                )
                self.events.record_refill_window_observation(
                    target_date=target_date.isoformat(),
                    window_label=active_pair[2],
                    window_start=active_pair[0].isoformat(),
                    window_end=active_pair[1].isoformat(),
                    need_detected=main_missing_ml > 0,
                    eligible=eligible_now,
                    blocking_reason=blocking_reason,
                    observed_at=now.isoformat(),
                )

        observed = {
            str(item["window_label"]): item
            for item in self.events.refill_window_observations(
                target_date=target_date.isoformat()
            )
        }
        missed_windows = [
            start
            for start, end, label in refill_window_pairs
            if end < now
            and not self.events.refill_for_target(target_date, label)
            and bool(observed.get(label, {}).get("need_detected"))
            and bool(observed.get(label, {}).get("eligible"))
        ]

        if not enabled:
            status, severity, blocked, blocked_reason = (
                "disabled",
                "info",
                False,
                "disabled",
            )
            summary = "Automatisches Nachf\u00fcllen ist deaktiviert."
        elif pump_ml_per_min <= 0:
            status, severity, blocked, blocked_reason = (
                "pump_flow_missing",
                "critical",
                main_missing_ml > 0,
                "pump_flow_missing",
            )
            summary = "Durchsatz der Nachf\u00fcllpumpe fehlt."
        elif refill_current <= 0:
            status, severity, blocked, blocked_reason = (
                "refill_tank_empty",
                "critical",
                main_missing_ml > 0,
                "refill_tank_empty",
            )
            summary = "Vorratstank ist leer."
        elif main_missing_ml <= 0:
            status, severity, blocked, blocked_reason = (
                "main_tank_full",
                "info",
                False,
                "",
            )
            summary = "Haupttank ist voll."
        elif cooldown_active:
            status, severity, blocked, blocked_reason = (
                "cooldown",
                "warning",
                schedule_due,
                "cooldown" if schedule_due else "",
            )
            summary = (
                "Nachf\u00fcllpause aktiv. Fr\u00fchester n\u00e4chster "
                f"Lauf um {cooldown_until.strftime('%H:%M')}."
            )
        elif transferable_ml < target_transfer_ml:
            status = "ready" if run_now else "window_pending"
            severity, blocked, blocked_reason = "warning", False, ""
            summary = (
                "Nachf\u00fcllung auf "
                f"{format_liters_for_text(transferable_ml)} begrenzt."
            )
        elif run_now:
            status, severity, blocked, blocked_reason = (
                "ready",
                "info",
                False,
                "",
            )
            summary = (
                "Nachf\u00fcllbedarf besteht, "
                "Nachf\u00fcllpumpe darf laufen."
            )
        elif missed_windows:
            status, severity, blocked, blocked_reason = (
                "window_missed",
                "critical",
                True,
                "window_missed",
            )
            summary = (
                "Nachf\u00fcllfenster verpasst. N\u00e4chste "
                f"Nachf\u00fcllung um {next_window.strftime('%H:%M')}."
            )
        elif completed_windows:
            status, severity, blocked, blocked_reason = (
                "completed",
                "info",
                False,
                "",
            )
            summary = (
                "Nachf\u00fcllung im heutigen Zeitfenster abgeschlossen."
            )
        elif next_window and now < next_window:
            status, severity, blocked, blocked_reason = (
                "window_pending",
                "info",
                False,
                "",
            )
            summary = (
                f"N\u00e4chste Nachf\u00fcllung um "
                f"{next_window.strftime('%H:%M')}."
            )
        else:
            status, severity, blocked, blocked_reason = (
                "window_pending",
                "info",
                False,
                "",
            )
            summary = (
                "Nachf\u00fcllung wartet auf den n\u00e4chsten "
                "geplanten Zeitpunkt."
            )

        refill_percent = round(refill_current / refill_capacity * 100)
        return {
            "run_now": run_now,
            "status": status,
            "severity": severity,
            "blocked": blocked,
            "blocked_reason": blocked_reason,
            "enabled": enabled,
            "target_date": target_date.isoformat(),
            "scheduled_time": (
                (active_window or next_window).strftime("%H:%M")
                if (active_window or next_window)
                else ""
            ),
            "scheduled_times": schedule_times,
            "active_window": active_window_label,
            "window_label": active_window_label if run_now else "",
            "last_window": (
                elapsed_windows[-1].strftime("%H:%M")
                if elapsed_windows
                else ""
            ),
            "next_window": (
                next_window.strftime("%H:%M") if next_window else ""
            ),
            "requested_ml": requested_ml,
            "target_transfer_ml": target_transfer_ml,
            "transfer_fraction": float(config["refill_fraction"]),
            "refill_strategy": config["refill_strategy"],
            "refill_target_ml": int(config["refill_target_ml"]),
            "planned_transfer_ml": transferable_ml,
            "duration_seconds": duration_seconds,
            "pump_ml_per_min": pump_ml_per_min,
            "main_missing_ml": main_missing_ml,
            "blocked_by_empty_refill_tank": bool(refill_current <= 0),
            "limited_by_refill_tank": bool(
                target_transfer_ml > 0
                and transferable_ml
                < min(target_transfer_ml, main_missing_ml)
            ),
            "already_done": bool(completed_windows),
            "missed_today": bool(missed_windows),
            "missed_windows": [
                item.strftime("%H:%M") for item in missed_windows
            ],
            "cooldown_active": cooldown_active,
            "cooldown_minutes": int(
                config["refill_min_interval_minutes"]
            ),
            "cooldown_until": (
                cooldown_until.isoformat() if cooldown_until else ""
            ),
            "need_exists": need_exists,
            "schedule_due": schedule_due,
            "catch_up": catch_up,
            "last_event": last_event or {},
            "refill_tank": {
                "current_ml": refill_current,
                "capacity_ml": refill_capacity,
                "percent": refill_percent,
                "low": refill_percent <= self.tank_low_percent,
                "empty": refill_current <= 0,
            },
            "summary": summary,
        }

    def manual_plan(self, result: dict[str, Any]) -> dict[str, Any]:
        config = self.settings.planner_config()
        tank = result.get("tank", {})
        refill = result.get("refill", {})
        refill_tank = refill.get("refill_tank", {})
        main_missing_ml = max(
            0,
            int(tank.get("capacity_ml", 0))
            - int(tank.get("current_ml", 0)),
        )
        refill_current = max(
            0,
            int(refill_tank.get("current_ml", 0)),
        )
        pump_ml_per_min = max(
            0,
            int(refill.get("pump_ml_per_min", 0)),
        )
        target_transfer_ml = (
            min(main_missing_ml, int(config["refill_target_ml"]))
            if config["refill_strategy"] == "target"
            else math.ceil(
                main_missing_ml * float(config["refill_fraction"])
            )
        )
        planned_transfer_ml = min(
            target_transfer_ml,
            refill_current,
        )
        duration_seconds = (
            math.ceil(planned_transfer_ml / pump_ml_per_min * 60)
            if pump_ml_per_min and planned_transfer_ml
            else 0
        )
        if pump_ml_per_min <= 0:
            summary = "Durchsatz der Nachf\u00fcllpumpe fehlt."
        elif main_missing_ml <= 0:
            summary = "Haupttank ist voll."
        elif refill_current <= 0:
            summary = "Vorratstank ist leer."
        elif planned_transfer_ml < target_transfer_ml:
            summary = (
                "Manuelle Nachf\u00fcllung auf "
                f"{format_liters_for_text(planned_transfer_ml)} begrenzt."
            )
        else:
            summary = (
                "Manuelle Nachf\u00fcllung: "
                f"{format_liters_for_text(planned_transfer_ml)}."
            )
        return {
            "target_transfer_ml": target_transfer_ml,
            "planned_transfer_ml": planned_transfer_ml,
            "duration_seconds": duration_seconds,
            "main_missing_ml": main_missing_ml,
            "summary": summary,
        }

    def save_pending_request(self, plan: dict[str, Any]) -> None:
        balcony = self.tanks.balcony()
        timezone_name = str(
            balcony.get("timezone_name", "Europe/Berlin")
        )
        created_at = self._utc_now()
        self.settings.set(
            "pending_refill_request",
            json.dumps(
                {
                    "created_at": created_at.isoformat(),
                    "run_id": str(plan.get("run_id", "")),
                    "source": "manual",
                    "target_date": created_at.astimezone(
                        local_timezone(timezone_name)
                    ).date().isoformat(),
                    "requested_ml": int(
                        plan.get("main_missing_ml", 0)
                    ),
                    "transferred_ml": int(
                        plan.get("planned_transfer_ml", 0)
                    ),
                    "duration_seconds": int(
                        plan.get("duration_seconds", 0)
                    ),
                    "window_label": "manual",
                }
            ),
        )

    def pending_request(self) -> dict[str, Any] | None:
        raw = self.settings.get("pending_refill_request", "")
        if not raw:
            return None
        try:
            payload = json.loads(raw)
            created_at = datetime.fromisoformat(
                str(payload.get("created_at", ""))
            )
        except (ValueError, TypeError, json.JSONDecodeError):
            self.settings.delete("pending_refill_request")
            return None
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        if (
            self._utc_now() - created_at.astimezone(timezone.utc)
            > self.pending_request_max_age
        ):
            self.settings.delete("pending_refill_request")
            return None
        return payload
