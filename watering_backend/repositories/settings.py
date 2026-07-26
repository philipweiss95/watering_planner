from __future__ import annotations

import json
import math
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from watering_backend.config import (
    LEGACY_MAX_NOTIFICATION_WORKER_INTERVAL_SECONDS,
    MAX_NOTIFICATION_WORKER_INTERVAL_SECONDS,
    MIN_NOTIFICATION_WORKER_INTERVAL_SECONDS,
    parse_hhmm,
    validate_planner_config,
)
from watering_backend.database import Database


@dataclass(frozen=True)
class SettingsDefaults:
    refill_run_times: tuple[str, ...] = ("01:00", "06:00")
    refill_trigger_tolerance_minutes: int = 60
    refill_min_interval_minutes: int = 180
    refill_cooldown_minutes_per_liter: float = 30
    refill_max_cooldown_minutes: int = 720
    water_model_calibration: float = 0.20
    legacy_water_model_calibrations: tuple[float, ...] = (0.04, 0.06, 0.08)
    minimum_water_model_percent: float = 0.5
    maximum_water_model_percent: float = 40.0
    minimum_watering_amount_percent: float = 40.0
    maximum_watering_amount_percent: float = 500.0


class SettingsRepository:
    def __init__(self, database: Database, defaults: SettingsDefaults | None = None):
        self.database = database
        self.defaults = defaults or SettingsDefaults()

    def get(self, key: str, default: str = "") -> str:
        with self.database.connection() as conn:
            try:
                row = conn.execute(
                    "SELECT value FROM app_settings WHERE key = ?",
                    (key,),
                ).fetchone()
            except sqlite3.OperationalError:
                return default
            return str(row["value"]) if row else default

    def set(self, key: str, value: str) -> None:
        with self.database.connection() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS app_settings "
                "(key TEXT PRIMARY KEY, value TEXT NOT NULL)"
            )
            conn.execute(
                """
                INSERT INTO app_settings (key, value)
                VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (key, value),
            )

    def delete(self, key: str) -> None:
        with self.database.connection() as conn:
            try:
                conn.execute("DELETE FROM app_settings WHERE key = ?", (key,))
            except sqlite3.OperationalError:
                pass

    def planner_config(self) -> dict:
        raw = self.get("planner_config", "")
        saved: dict = {}
        normalized_legacy_interval = False
        if raw:
            try:
                value = json.loads(raw)
                if isinstance(value, dict):
                    saved = value
            except json.JSONDecodeError:
                saved = {}
        legacy_interval = saved.get(
            "notification_worker_interval_seconds"
        )
        try:
            parsed_legacy_interval = int(legacy_interval)
        except (TypeError, ValueError):
            parsed_legacy_interval = None
        if (
            parsed_legacy_interval is not None
            and 1
            <= parsed_legacy_interval
            <= LEGACY_MAX_NOTIFICATION_WORKER_INTERVAL_SECONDS
            and not (
                MIN_NOTIFICATION_WORKER_INTERVAL_SECONDS
                <= parsed_legacy_interval
                <= MAX_NOTIFICATION_WORKER_INTERVAL_SECONDS
            )
        ):
            # Releases before 1.5 accepted worker intervals up to one day.
            # Keep those databases bootable, but persist the nearest safe
            # value so short refill windows are observed going forward.
            saved["notification_worker_interval_seconds"] = min(
                MAX_NOTIFICATION_WORKER_INTERVAL_SECONDS,
                max(
                    MIN_NOTIFICATION_WORKER_INTERVAL_SECONDS,
                    parsed_legacy_interval,
                ),
            )
            normalized_legacy_interval = True
        if "refill_windows" not in saved:
            legacy_raw = self.get(
                "refill_schedule_times",
                json.dumps(list(self.defaults.refill_run_times)),
            )
            try:
                legacy_value = json.loads(legacy_raw)
            except json.JSONDecodeError:
                legacy_value = legacy_raw
            legacy_times = self.normalize_refill_schedule_times(legacy_value)
            saved["refill_windows"] = [
                {
                    "start": item,
                    "end": (
                        datetime.combine(date.today(), parse_hhmm(item))
                        + timedelta(
                            minutes=self.defaults.refill_trigger_tolerance_minutes
                        )
                    ).strftime("%H:%M"),
                }
                for item in legacy_times
                if item < "23:00"
            ]
        if "refill_min_interval_minutes" not in saved:
            saved["refill_min_interval_minutes"] = (
                self.defaults.refill_min_interval_minutes
            )
        config = validate_planner_config(saved)
        if normalized_legacy_interval:
            self.set(
                "planner_config",
                json.dumps(
                    config,
                    separators=(",", ":"),
                    sort_keys=True,
                ),
            )
        return config

    def save_planner_config(self, value: object) -> dict:
        config = validate_planner_config(value)
        self.set(
            "planner_config",
            json.dumps(config, separators=(",", ":"), sort_keys=True),
        )
        self.set(
            "refill_schedule_times",
            json.dumps([item["start"] for item in config["refill_windows"]]),
        )
        return config

    def refill_automation_enabled(self) -> bool:
        return self.get("refill_automation_enabled", "true").lower() not in {
            "0",
            "false",
            "off",
            "no",
        }

    def save_refill_automation_enabled(self, value: object) -> None:
        enabled = str(value).lower() in {"1", "true", "on", "yes"}
        self.set("refill_automation_enabled", "true" if enabled else "false")

    def main_pump_calibration_factor(self) -> float:
        try:
            factor = float(self.get("main_pump_calibration_factor", "1"))
        except ValueError:
            return 1.0
        return factor if 0.1 <= factor <= 10 else 1.0

    def save_main_pump_calibration_factor(self, value: object) -> None:
        factor = float(value)
        if not math.isfinite(factor) or not 0.1 <= factor <= 10:
            raise ValueError("Der Verbrauchsfaktor muss zwischen 0,1 und 10 liegen")
        self.set("main_pump_calibration_factor", f"{factor:.6g}")

    def calibrated_consumption_ml(self, nominal_ml: int | float) -> int:
        return max(
            0,
            round(float(nominal_ml) * self.main_pump_calibration_factor()),
        )

    def normalize_refill_schedule_times(self, value: object) -> list[str]:
        if isinstance(value, str):
            raw_items = [
                item.strip() for item in value.replace(";", ",").split(",")
            ]
        elif isinstance(value, (list, tuple)):
            raw_items = [str(item).strip() for item in value]
        else:
            raw_items = []
        times: list[str] = []
        seen: set[str] = set()
        for item in raw_items:
            if not item:
                continue
            parsed = parse_hhmm(item)
            label = f"{parsed.hour:02d}:{parsed.minute:02d}"
            if label not in seen:
                seen.add(label)
                times.append(label)
        if not times:
            raise ValueError("Mindestens eine Nachfüllzeit muss angegeben werden")
        return sorted(times, key=parse_hhmm)

    def refill_schedule_times(self) -> list[str]:
        raw = self.get("planner_config", "")
        if raw:
            try:
                value = json.loads(raw)
                windows = value.get("refill_windows", []) if isinstance(value, dict) else []
                if windows:
                    return [str(item["start"]) for item in windows]
            except (json.JSONDecodeError, KeyError, TypeError):
                pass
        return list(self.defaults.refill_run_times)

    def save_refill_schedule_times(self, value: object) -> None:
        times = self.normalize_refill_schedule_times(value)
        config = self.planner_config()
        config["refill_windows"] = []
        for item in times:
            start = datetime.combine(date.today(), parse_hhmm(item))
            end = start + timedelta(
                minutes=self.defaults.refill_trigger_tolerance_minutes
            )
            if end.date() != start.date():
                raise ValueError(
                    "Nachfuellzeitfenster duerfen nicht ueber Mitternacht reichen"
                )
            config["refill_windows"].append(
                {"start": item, "end": end.strftime("%H:%M")}
            )
        self.save_planner_config(config)

    def refill_cooldown_minutes_per_liter(self) -> float:
        fallback = self.defaults.refill_cooldown_minutes_per_liter
        try:
            value = float(
                self.get("refill_cooldown_minutes_per_liter", str(fallback))
            )
        except ValueError:
            return float(fallback)
        if not math.isfinite(value) or value <= 0:
            return float(fallback)
        return value

    def save_refill_cooldown_minutes_per_liter(self, value: object) -> None:
        minutes = float(value)
        if (
            not math.isfinite(minutes)
            or minutes <= 0
            or minutes > self.defaults.refill_max_cooldown_minutes
        ):
            raise ValueError(
                "Nachfüllsperre pro Liter muss zwischen 1 und "
                f"{self.defaults.refill_max_cooldown_minutes} Minuten liegen"
            )
        self.set("refill_cooldown_minutes_per_liter", f"{minutes:g}")

    def saved_watering_amount_percent(self) -> float | None:
        try:
            amount = float(self.get("watering_amount_percent"))
        except ValueError:
            return None
        if not (
            math.isfinite(amount)
            and self.defaults.minimum_watering_amount_percent
            <= amount
            <= self.defaults.maximum_watering_amount_percent
        ):
            return None
        return amount

    def water_model_calibration(self) -> float:
        amount = self.saved_watering_amount_percent()
        if amount is not None:
            return self.defaults.water_model_calibration * amount / 100
        try:
            calibration = float(
                self.get(
                    "water_model_calibration",
                    str(self.defaults.water_model_calibration),
                )
            )
        except ValueError:
            return self.defaults.water_model_calibration
        if any(
            math.isclose(calibration, legacy)
            for legacy in self.defaults.legacy_water_model_calibrations
        ):
            return self.defaults.water_model_calibration
        if not (
            math.isfinite(calibration)
            and self.defaults.minimum_water_model_percent / 100
            <= calibration
            <= self.defaults.maximum_water_model_percent / 100
        ):
            return self.defaults.water_model_calibration
        return calibration

    def save_water_model_calibration_percent(self, value: object) -> None:
        percent = float(value)
        if not (
            math.isfinite(percent)
            and self.defaults.minimum_water_model_percent
            <= percent
            <= self.defaults.maximum_water_model_percent
        ):
            raise ValueError(
                "Wasser-Skalierung muss zwischen "
                f"{self.defaults.minimum_water_model_percent:g} und "
                f"{self.defaults.maximum_water_model_percent:g} Prozent liegen"
            )
        self.delete("watering_amount_percent")
        self.set("water_model_calibration", str(percent / 100))

    def watering_amount_percent(self) -> float:
        amount = self.saved_watering_amount_percent()
        if amount is not None:
            return amount
        return (
            self.water_model_calibration()
            / self.defaults.water_model_calibration
            * 100
        )

    def save_watering_amount_percent(self, value: object) -> None:
        amount = float(value)
        if not (
            math.isfinite(amount)
            and self.defaults.minimum_watering_amount_percent
            <= amount
            <= self.defaults.maximum_watering_amount_percent
        ):
            raise ValueError(
                "Gießmenge muss zwischen "
                f"{self.defaults.minimum_watering_amount_percent:g} und "
                f"{self.defaults.maximum_watering_amount_percent:g} Prozent liegen"
            )
        self.delete("water_model_calibration")
        self.set("watering_amount_percent", str(amount))
