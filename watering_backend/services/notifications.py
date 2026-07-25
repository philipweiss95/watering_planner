"""Build and dispatch notification conditions without transport or SQL coupling."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable, Iterable, Protocol

from watering_backend.catalog import TANK_LOW_PERCENT, WEATHER_FORECAST_DAYS
from watering_backend.models import NotificationCondition, Severity


class NotificationUpdater(Protocol):
    def update_condition(
        self,
        alert_key: str,
        active: bool,
        severity: str,
        subject: str,
        message: str,
        *,
        cooldown_minutes: int,
        send_resolved: bool,
        retry_minutes: int = 5,
    ) -> dict:
        ...


def _parse_event_datetime(value: object) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


class NotificationConditionsService:
    """Coordinates condition checks using explicitly supplied application ports."""

    def __init__(
        self,
        *,
        state: Callable[[], dict],
        calibrated_consumption: Callable[[int | float], int],
        refill_status: Callable[[dict], dict],
        fetch_weather: Callable[[dict], dict],
        evaluate_weather: Callable[[dict], dict],
        weather_diagnostics: Callable[[], dict],
        planner_config: Callable[[], dict],
        local_now: Callable[[str], datetime],
        notification_service: Callable[[], NotificationUpdater],
        previous_missed_keys: Callable[[], Iterable[str]],
    ) -> None:
        self._state = state
        self._calibrated_consumption = calibrated_consumption
        self._refill_status = refill_status
        self._fetch_weather = fetch_weather
        self._evaluate_weather = evaluate_weather
        self._weather_diagnostics = weather_diagnostics
        self._planner_config = planner_config
        self._local_now = local_now
        self._notification_service = notification_service
        self._previous_missed_keys = previous_missed_keys

    def notification_condition(
        self,
        alert_key: str,
        active: bool,
        severity: Severity,
        subject: str,
        message: str,
    ) -> NotificationCondition:
        config = self._planner_config()
        return {
            "alert_key": alert_key,
            "active": active,
            "severity": severity,
            "subject": subject,
            "message": message,
            "cooldown_minutes": int(config["notification_cooldown_minutes"]),
            "retry_minutes": int(config["notification_retry_minutes"]),
            "send_resolved": bool(config["notification_resolved_enabled"]),
        }

    def notification_conditions(self) -> list[NotificationCondition]:
        state = self._state()
        balcony = state["balcony"]
        nominal_cycle_ml = sum(
            int(hose["ml_per_run"])
            for hose in state["hoses"]
            if hose.get("plant_id") is not None
        )
        consumed_cycle_ml = self._calibrated_consumption(nominal_cycle_ml)
        main_current = int(balcony["tank_current_ml"])
        refill_current = int(balcony["refill_tank_current_ml"])
        refill_capacity = max(1, int(balcony["refill_tank_capacity_ml"]))
        conditions = [
            self.notification_condition(
                "main_tank_cycle",
                consumed_cycle_ml > 0 and main_current < consumed_cycle_ml,
                "critical",
                "Haupttank reicht nicht fuer einen Zyklus",
                (
                    f"Haupttank: {main_current} ml, kalibrierter "
                    f"Zyklusverbrauch: {consumed_cycle_ml} ml."
                ),
            ),
            self.notification_condition(
                "refill_tank_low",
                (
                    refill_current <= 0
                    or refill_current / refill_capacity
                    <= TANK_LOW_PERCENT / 100
                ),
                "warning" if refill_current > 0 else "critical",
                "Vorratstank niedrig",
                (
                    f"Vorratstank: {refill_current} von "
                    f"{refill_capacity} ml."
                ),
            ),
        ]
        refill = self._refill_status(balcony)
        refill_blocked = bool(refill.get("blocked"))
        conditions.append(
            self.notification_condition(
                "automatic_refill_blocked",
                refill_blocked,
                "critical",
                "Automatische Nachfuellung blockiert",
                str(
                    refill.get(
                        "summary",
                        "Automatische Nachfuellung konnte nicht laufen.",
                    )
                ),
            )
        )
        active_missed_keys: set[str] = set()
        for window_label in refill.get("missed_windows", []):
            alert_key = (
                f"refill_run_missed:{refill['target_date']}:{window_label}"
            )
            active_missed_keys.add(alert_key)
            conditions.append(
                self.notification_condition(
                    alert_key,
                    True,
                    "critical",
                    "Nachfuelllauf verpasst",
                    (
                        f"Im Nachfuellfenster {window_label} bestand Bedarf, "
                        "aber es wurde kein Lauf verbucht."
                    ),
                )
            )
        previous_missed_keys = {
            str(alert_key)
            for alert_key in self._previous_missed_keys()
        }
        for alert_key in sorted(previous_missed_keys - active_missed_keys):
            conditions.append(
                self.notification_condition(
                    alert_key,
                    False,
                    "critical",
                    "Nachfuelllauf verpasst",
                    "Das betroffene Nachfuellfenster ist nicht mehr offen.",
                )
            )
        try:
            weather = self._fetch_weather(balcony)
            evaluation = self._evaluate_weather(weather)
            first_unserved = _parse_event_datetime(
                evaluation["depletion"].get(
                    "first_unserved_watering_at",
                    "",
                )
            )
            timezone_name = str(balcony["timezone_name"])
            supply_days = (
                max(
                    0,
                    (
                        first_unserved.astimezone(
                            self._local_now(timezone_name).tzinfo
                        ).date()
                        - self._local_now(timezone_name).date()
                    ).days,
                )
                if first_unserved
                else WEATHER_FORECAST_DAYS
            )
            threshold = int(self._planner_config()["supply_warning_days"])
            conditions.append(
                self.notification_condition(
                    "supply_range",
                    supply_days < threshold,
                    "warning",
                    "Prognostizierte Wasserversorgung zu kurz",
                    (
                        f"Geschaetzte Reichweite: {supply_days} Tage, "
                        f"Warnschwelle: {threshold} Tage."
                    ),
                )
            )
            automation = evaluation.get("automation", {})
            missed = bool(
                automation.get("catch_up")
                and evaluation.get("remaining_cycles_today", 0) > 0
                and not automation.get("cooldown_active")
            )
            conditions.append(
                self.notification_condition(
                    "watering_run_missed",
                    missed,
                    "critical",
                    "Faelliger Bewaesserungslauf nicht verbucht",
                    str(
                        automation.get(
                            "summary",
                            "Ein geplanter Lauf ist ueberfaellig.",
                        )
                    ),
                )
            )
        except ValueError:
            pass
        weather_status = self._weather_diagnostics()
        conditions.append(
            self.notification_condition(
                "weather_stale",
                bool(
                    weather_status["stale"]
                    or weather_status["last_error"]
                ),
                "warning",
                "Wetterdaten fehlen oder sind veraltet",
                (
                    "Letzter erfolgreicher Abruf: "
                    f"{weather_status['last_successful_fetch_at'] or 'nie'}. "
                    f"Grenze: {weather_status['stale_after_minutes']} Minuten."
                ),
            )
        )
        return conditions

    def run_notification_check(self) -> list[dict]:
        service = self._notification_service()
        return [
            service.update_condition(**condition)
            for condition in self.notification_conditions()
        ]
