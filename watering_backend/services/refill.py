from __future__ import annotations

import json
import math
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Protocol

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


def refill_window_key(
    target_date: date | str,
    start: datetime,
    end: datetime,
) -> str:
    """Identify a local schedule window by its unambiguous UTC bounds."""
    target = (
        target_date.isoformat()
        if isinstance(target_date, date)
        else str(target_date)
    )
    return (
        f"{target}|{aware_utc(start).isoformat()}|"
        f"{aware_utc(end).isoformat()}"
    )


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
        window_safety_seconds: int = 10,
        run_diagnostics: Callable[[], dict[str, Any]] | None = None,
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
        self.window_safety_seconds = max(
            0,
            min(int(window_safety_seconds), 60),
        )
        self.run_diagnostics = run_diagnostics or (lambda: {})

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
        now_utc = self._utc_now()
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
        high_reserve_threshold_percent = int(
            config["refill_high_reserve_threshold_percent"]
        )
        high_reserve_minimum_transfer_ml = int(
            config["refill_high_reserve_minimum_transfer_ml"]
        )
        minimum_transfer_blocked = bool(
            high_reserve_threshold_percent > 0
            and high_reserve_minimum_transfer_ml > 0
            and refill_current * 100
            > refill_capacity * high_reserve_threshold_percent
            and target_transfer_ml < high_reserve_minimum_transfer_ml
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

        scheduled_pairs = self._scheduler(now.date(), timezone_name, config)
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
            if aware_utc(end) <= now_utc
        ]
        eligible_windows = [
            start
            for start, end, _label in refill_window_pairs
            if aware_utc(start) <= now_utc < aware_utc(end)
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
        active_window_end = next(
            (
                end
                for start, end, _label in refill_window_pairs
                if active_window and start == active_window
            ),
            None,
        )
        next_window = next(
            (item for item in refill_windows if aware_utc(item) > now_utc),
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
        cooldown_until_utc = (
            aware_utc(cooldown_until) if cooldown_until else None
        )
        cooldown_active = bool(
            cooldown_until_utc and now_utc < cooldown_until_utc
        )
        schedule_due = bool(active_window)
        remaining_window_seconds = (
            max(
                0,
                math.floor(
                    (
                        aware_utc(active_window_end) - now_utc
                    ).total_seconds()
                )
                - self.window_safety_seconds,
            )
            if active_window_end
            else 0
        )
        window_transfer_limit_ml = (
            math.floor(
                remaining_window_seconds * pump_ml_per_min / 60
            )
            if schedule_due and pump_ml_per_min > 0
            else 0
        )
        authorized_transfer_ml = (
            min(transferable_ml, window_transfer_limit_ml)
            if schedule_due
            else transferable_ml
        )
        authorized_duration_seconds = (
            math.ceil(
                authorized_transfer_ml / pump_ml_per_min * 60
            )
            if pump_ml_per_min and authorized_transfer_ml
            else 0
        )
        need_exists = bool(
            authorized_transfer_ml > 0 and pump_ml_per_min > 0
        )
        run_now = bool(
            enabled
            and schedule_due
            and need_exists
            and not cooldown_active
            and not minimum_transfer_blocked
        )
        catch_up = False

        # Persist today's and tomorrow's absolute opportunities before they
        # open. A restart or worker outage during a window can then still
        # distinguish a missed executable run from an impossible one.
        checked_at = now_utc.isoformat()
        for plan_day in (target_date, target_date + timedelta(days=1)):
            day_pairs = self._scheduler(
                plan_day,
                timezone_name,
                config,
            )
            configured_keys: set[str] = set()
            for (start, end), window in zip(
                day_pairs,
                config["refill_windows"],
            ):
                start_utc = aware_utc(start)
                end_utc = aware_utc(end)
                key = refill_window_key(plan_day, start, end)
                configured_keys.add(key)
                # Refill windows are half-open intervals. At the exact end no
                # transfer can start, and the last executable snapshot must
                # remain intact for missed-window detection on the next poll.
                if end_utc <= now_utc:
                    continue

                observed_in_window = (
                    start_utc <= now_utc < end_utc
                )
                available_from = start_utc
                if observed_in_window:
                    available_from = now_utc
                if (
                    cooldown_until_utc
                    and cooldown_until_utc > available_from
                ):
                    available_from = cooldown_until_utc
                available_minutes = max(
                    0.0,
                    (end_utc - available_from).total_seconds() / 60,
                )
                window_transfer_limit = math.floor(
                    available_minutes * pump_ml_per_min
                )
                expected_transfer_ml = min(
                    transferable_ml,
                    max(0, window_transfer_limit),
                )
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
                    if cooldown_until_utc
                    and cooldown_until_utc >= end_utc
                    else "invalid_window"
                    if end_utc <= start_utc
                    else "window_too_short"
                    if expected_transfer_ml <= 0
                    else "minimum_transfer_not_reached"
                    if minimum_transfer_blocked
                    else ""
                )
                executable = bool(
                    enabled
                    and main_missing_ml > 0
                    and refill_current > 0
                    and pump_ml_per_min > 0
                    and expected_transfer_ml > 0
                    and end_utc > start_utc
                    and not minimum_transfer_blocked
                )
                self.events.upsert_refill_window_plan(
                    window_key=key,
                    target_date=plan_day.isoformat(),
                    window_label=str(window["start"]),
                    window_start=start_utc.isoformat(),
                    window_end=end_utc.isoformat(),
                    need_detected=main_missing_ml > 0,
                    executable=executable,
                    expected_transfer_ml=expected_transfer_ml,
                    blocking_reason=blocking_reason,
                    observed_in_window=observed_in_window,
                    checked_at=checked_at,
                    cooldown_minutes=int(
                        config["refill_min_interval_minutes"]
                    ),
                )
            self.events.cancel_obsolete_refill_window_plans(
                target_date=plan_day.isoformat(),
                active_window_keys=configured_keys,
                after_at=checked_at,
                cancelled_at=checked_at,
            )

        recent_dates = [
            (target_date - timedelta(days=1)).isoformat(),
            target_date.isoformat(),
        ]
        missed_window_details: list[dict[str, Any]] = []
        completed_plan_keys: set[str] = set()
        for plan in self.events.refill_window_plans(
            target_dates=recent_dates,
        ):
            if plan.get("cancelled_at"):
                continue
            event = self.events.refill_for_target(
                str(plan["target_date"]),
                str(plan["window_label"]),
            )
            if event and int(event.get("transferred_ml", 0)) > 0:
                fulfilled_at = str(event.get("ran_at", checked_at))
                self.events.mark_refill_window_fulfilled(
                    str(plan["window_key"]),
                    fulfilled_at,
                    checked_at,
                )
                plan["fulfilled_at"] = fulfilled_at
            if plan.get("fulfilled_at"):
                completed_plan_keys.add(str(plan["window_key"]))
                continue
            window_end = parse_event_datetime(plan.get("window_end"))
            if (
                window_end
                and aware_utc(window_end) < now_utc
                and bool(plan.get("need_detected"))
                and bool(plan.get("executable"))
                and int(plan.get("expected_transfer_ml", 0)) > 0
            ):
                missed_window_details.append(
                    {
                        "window_key": str(plan["window_key"]),
                        "target_date": str(plan["target_date"]),
                        "window_label": str(plan["window_label"]),
                        "window_start": str(plan["window_start"]),
                        "window_end": str(plan["window_end"]),
                        "expected_transfer_ml": int(
                            plan["expected_transfer_ml"]
                        ),
                    }
                )
        missed_windows = [
            item["window_label"]
            for item in missed_window_details
            if item["target_date"] == target_date.isoformat()
        ]
        if completed_plan_keys:
            completed_windows = [
                start
                for start, end, label in refill_window_pairs
                if refill_window_key(target_date, start, end)
                in completed_plan_keys
            ]

        run_state = self.run_diagnostics()
        active_run = run_state.get("active_run")
        uncertain_runs = run_state.get("uncertain_runs", [])
        if active_run:
            status = (
                "running"
                if active_run.get("status") == "running"
                else "running"
            )
            severity, blocked, blocked_reason = (
                "warning",
                True,
                "refill_run_active",
            )
            run_now = False
            summary = (
                "Nachfülllauf ist aktiv; Abschlussmeldung wird erwartet."
            )
        elif uncertain_runs:
            status, severity, blocked, blocked_reason = (
                "run_unconfirmed",
                "critical",
                True,
                "refill_run_unconfirmed",
            )
            run_now = False
            summary = (
                "Nachfülllauf ist unbestätigt. Tankstände manuell prüfen."
            )
        elif missed_window_details:
            status, severity, blocked, blocked_reason = (
                "window_missed",
                "critical",
                True,
                "window_missed",
            )
            summary = "Nachf\u00fcllfenster verpasst."
            if next_window:
                summary += (
                    " N\u00e4chste Nachf\u00fcllung um "
                    f"{next_window.strftime('%H:%M')}."
                )
        elif not enabled:
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
        elif minimum_transfer_blocked:
            status, severity, blocked, blocked_reason = (
                "minimum_transfer_pending",
                "info",
                False,
                "",
            )
            summary = (
                "Automatische Nachfüllung wartet auf mindestens "
                f"{format_liters_for_text(high_reserve_minimum_transfer_ml)} "
                "Nachfüllbedarf bei hohem Vorratstankstand."
            )
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
            "high_reserve_threshold_percent": high_reserve_threshold_percent,
            "high_reserve_minimum_transfer_ml": high_reserve_minimum_transfer_ml,
            "minimum_transfer_blocked": minimum_transfer_blocked,
            "planned_transfer_ml": (
                authorized_transfer_ml
                if schedule_due
                else transferable_ml
            ),
            "duration_seconds": (
                authorized_duration_seconds
                if schedule_due
                else duration_seconds
            ),
            "window_remaining_seconds": remaining_window_seconds,
            "window_transfer_limit_ml": window_transfer_limit_ml,
            "window_safety_seconds": self.window_safety_seconds,
            "limited_by_window": bool(
                schedule_due
                and authorized_transfer_ml < transferable_ml
            ),
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
            "missed": bool(missed_window_details),
            "missed_windows": missed_windows,
            "missed_window_details": missed_window_details,
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
            **run_state,
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
