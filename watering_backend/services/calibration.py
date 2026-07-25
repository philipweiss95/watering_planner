from __future__ import annotations

import math
import sqlite3
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from watering_backend.database import Database
from watering_backend.repositories.events import EventsRepository
from watering_backend.repositories.settings import SettingsRepository
from watering_backend.repositories.tanks import TanksRepository


NowCallback = Callable[[], datetime]


def _system_now() -> datetime:
    return datetime.now(timezone.utc)


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


class CalibrationService:
    """Calibrate pump consumption and throughput without HTTP dependencies."""

    def __init__(
        self,
        database: Database,
        events: EventsRepository,
        settings: SettingsRepository,
        tanks: TanksRepository,
        *,
        now: NowCallback = _system_now,
    ):
        self.database = database
        self.events = events
        self.settings = settings
        self.tanks = tanks
        self._now = now

    def _now_iso(self) -> str:
        return _aware_utc(self._now()).isoformat()

    def calibration_baseline(
        self,
        conn: sqlite3.Connection,
        pump_name: str,
    ) -> dict[str, Any] | None:
        return self.events.calibration_baseline(conn, pump_name)

    def latest(self, pump_name: str) -> dict[str, Any] | None:
        return self.events.latest_calibration(pump_name)

    def status(self) -> dict[str, Any]:
        with self.database.connection() as conn:
            main_baseline = self.calibration_baseline(conn, "main")
            refill_baseline = self.calibration_baseline(conn, "refill")
        return {
            "main": {
                "factor": round(
                    self.settings.main_pump_calibration_factor(),
                    4,
                ),
                "baseline": main_baseline or {},
                "latest": self.latest("main") or {},
            },
            "refill": {
                "baseline": refill_baseline or {},
                "latest": self.latest("refill") or {},
            },
        }

    @staticmethod
    def measured_level_ml_from_percent(
        measured_level_percent: int | float,
        capacity_ml: int,
        tank_label: str,
    ) -> int:
        percent = float(measured_level_percent)
        if not math.isfinite(percent) or not 0 <= percent <= 100:
            raise ValueError(
                f"Der F\u00fcllstand des {tank_label} muss zwischen "
                "0 und 100 Prozent liegen"
            )
        return int(round(capacity_ml * percent / 100))

    def calibrate_main(self, measured_level_percent: int | float) -> dict[str, Any]:
        calibrated_at = self._now_iso()
        with self.database.connection() as conn:
            try:
                balcony = self.tanks.balcony(conn=conn)
            except RuntimeError:
                raise ValueError("Der Haupttank ist nicht konfiguriert")
            if not balcony:
                raise ValueError("Der Haupttank ist nicht konfiguriert")
            measured_level = self.measured_level_ml_from_percent(
                measured_level_percent,
                int(balcony["tank_capacity_ml"]),
                "Haupttanks",
            )
            baseline = self.calibration_baseline(conn, "main")
            if not baseline:
                raise ValueError(
                    "Vor der ersten Kalibrierung den Haupttank einmal "
                    "als voll markieren"
                )
            watering = self.events.watering_aggregate(
                conn,
                str(baseline["baseline_at"]),
                calibrated_at,
            )
            refilled_ml = self.events.transferred_between(
                conn,
                str(baseline["baseline_at"]),
                calibrated_at,
            )
            cycles = int(watering["cycles"])
            nominal_ml = int(watering["nominal_ml"])
            measured_ml = (
                int(baseline["baseline_level_ml"])
                + refilled_ml
                - measured_level
            )
            if cycles <= 0 or nominal_ml <= 0:
                raise ValueError(
                    "Seit dem letzten Vollstand oder der letzten Kalibrierung "
                    "wurde kein Bew\u00e4sserungszyklus verbucht"
                )
            if measured_ml <= 0:
                raise ValueError(
                    "Der gemessene Stand ergibt keinen positiven Wasserverbrauch"
                )
            factor = measured_ml / nominal_ml
            if not 0.1 <= factor <= 10:
                raise ValueError(
                    "Die Messung ergibt einen unplausiblen Verbrauchsfaktor "
                    "au\u00dferhalb 0,1 bis 10"
                )
            self.tanks.set_tank_level(
                conn,
                "main",
                measured_level,
                updated_at=calibrated_at,
            )
            self.events.insert_calibration(
                conn,
                calibrated_at=calibrated_at,
                pump_name="main",
                measured_level_ml=measured_level,
                baseline_at=str(baseline["baseline_at"]),
                baseline_level_ml=int(baseline["baseline_level_ml"]),
                cycles=cycles,
                nominal_ml=nominal_ml,
                measured_ml=measured_ml,
                result_value=factor,
            )
        self.settings.save_main_pump_calibration_factor(factor)
        return self.latest("main") or {}

    def calibrate_refill(
        self,
        measured_level_percent: int | float,
    ) -> dict[str, Any]:
        calibrated_at = self._now_iso()
        with self.database.connection() as conn:
            try:
                balcony = self.tanks.balcony(conn=conn)
            except RuntimeError:
                raise ValueError("Der Vorratstank ist nicht konfiguriert")
            if not balcony:
                raise ValueError("Der Vorratstank ist nicht konfiguriert")
            measured_level = self.measured_level_ml_from_percent(
                measured_level_percent,
                int(balcony["refill_tank_capacity_ml"]),
                "Vorratstanks",
            )
            baseline = self.calibration_baseline(conn, "refill")
            if not baseline:
                raise ValueError(
                    "Vor der ersten Kalibrierung den Vorratstank einmal "
                    "als voll markieren"
                )
            refills = self.events.refill_aggregate(
                conn,
                str(baseline["baseline_at"]),
                calibrated_at,
            )
            cycles = int(refills["cycles"])
            duration_seconds = int(refills["duration_seconds"])
            measured_ml = int(baseline["baseline_level_ml"]) - measured_level
            if cycles <= 0 or duration_seconds <= 0:
                raise ValueError(
                    "Seit dem letzten Vollstand oder der letzten Kalibrierung "
                    "wurde kein Nachf\u00fcllzyklus verbucht"
                )
            if measured_ml <= 0:
                raise ValueError(
                    "Der gemessene Stand ergibt keine positive F\u00f6rdermenge"
                )
            ml_per_min = measured_ml / duration_seconds * 60
            if not 1 <= ml_per_min <= 100000:
                raise ValueError(
                    "Die Messung ergibt einen unplausiblen Pumpendurchsatz"
                )
            self.tanks.update_balcony(
                conn,
                {
                    "refill_tank_current_ml": measured_level,
                    "refill_pump_ml_per_min": round(ml_per_min),
                },
                updated_at=calibrated_at,
            )
            self.events.insert_calibration(
                conn,
                calibrated_at=calibrated_at,
                pump_name="refill",
                measured_level_ml=measured_level,
                baseline_at=str(baseline["baseline_at"]),
                baseline_level_ml=int(baseline["baseline_level_ml"]),
                cycles=cycles,
                nominal_ml=int(refills["nominal_ml"]),
                measured_ml=measured_ml,
                result_value=ml_per_min,
            )
        return self.latest("refill") or {}
