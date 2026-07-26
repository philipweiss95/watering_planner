from __future__ import annotations

import math
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from watering_backend.config import local_timezone
from watering_backend.database import Database
from watering_backend.repositories.events import EventsRepository
from watering_backend.repositories.refill_runs import RefillRunsRepository
from watering_backend.repositories.settings import SettingsRepository
from watering_backend.repositories.tanks import TanksRepository
from watering_backend.scheduling import refill_times
from watering_backend.services.refill import (
    aware_utc,
    parse_event_datetime,
    refill_window_key,
)
from watering_backend.services.watering import normalize_run_id


REFILL_WINDOW_SAFETY_SECONDS = 10
REFILL_COMPLETION_GRACE = timedelta(minutes=15)


class RefillRunService:
    """Reserve, acknowledge and account physical refill-pump runs."""

    def __init__(
        self,
        *,
        database: Database,
        runs: RefillRunsRepository,
        events: EventsRepository,
        settings: SettingsRepository,
        tanks: TanksRepository,
        now: Callable[[], datetime],
        safety_seconds: int = REFILL_WINDOW_SAFETY_SECONDS,
        completion_grace: timedelta = REFILL_COMPLETION_GRACE,
    ):
        self.database = database
        self.runs = runs
        self.events = events
        self.settings = settings
        self.tanks = tanks
        self._now = now
        self.safety_seconds = max(0, min(int(safety_seconds), 60))
        self.completion_grace = completion_grace

    def _utc_now(self) -> datetime:
        return aware_utc(self._now())

    @staticmethod
    def _limit_reasons(
        *,
        target_ml: int,
        main_room_ml: int,
        refill_available_ml: int,
        window_limit_ml: int | None,
        planned_ml: int,
    ) -> list[str]:
        reasons: list[str] = []
        if main_room_ml < target_ml and planned_ml <= main_room_ml:
            reasons.append("main_tank_capacity")
        if (
            refill_available_ml < min(target_ml, main_room_ml)
            and planned_ml <= refill_available_ml
        ):
            reasons.append("refill_tank")
        if (
            window_limit_ml is not None
            and window_limit_ml < min(
                target_ml,
                main_room_ml,
                refill_available_ml,
            )
        ):
            reasons.append("window_remaining")
        return reasons

    def _plan(
        self,
        conn: sqlite3.Connection,
        *,
        run_type: str,
        now_utc: datetime,
    ) -> dict[str, Any]:
        if run_type not in {"automatic", "manual"}:
            raise ValueError("run_type muss automatic oder manual sein")
        balcony = self.tanks.balcony(conn=conn)
        config = self.settings.planner_config(conn=conn)
        timezone_name = str(
            balcony.get("timezone_name", "Europe/Berlin")
        )
        local_now = now_utc.astimezone(local_timezone(timezone_name))
        main_capacity = max(0, int(balcony["tank_capacity_ml"]))
        main_current = max(0, int(balcony["tank_current_ml"]))
        refill_current = max(
            0,
            int(balcony["refill_tank_current_ml"]),
        )
        pump_ml_per_min = max(
            0,
            int(balcony["refill_pump_ml_per_min"]),
        )
        main_room = max(0, main_capacity - main_current)
        requested_ml = main_room
        target_ml = (
            int(config["refill_target_ml"])
            if config["refill_strategy"] == "target"
            else math.ceil(main_room * float(config["refill_fraction"]))
        )
        if main_room <= 0:
            raise ValueError("Haupttank ist bereits voll")
        if refill_current <= 0:
            raise ValueError("Vorratstank ist leer")
        if pump_ml_per_min <= 0:
            raise ValueError("Durchsatz der Nachfüllpumpe fehlt")

        last_event = self.events.latest_refill(conn=conn)
        last_at = (
            parse_event_datetime(last_event.get("ran_at"))
            if last_event
            else None
        )
        cooldown_until = (
            aware_utc(last_at)
            + timedelta(
                minutes=int(config["refill_min_interval_minutes"])
            )
            if last_at
            else None
        )
        if cooldown_until and now_utc < cooldown_until:
            raise ValueError(
                "Zwischen zwei Nachfüllvorgängen müssen mindestens drei "
                "Stunden liegen. Cooldown aktiv bis "
                f"{cooldown_until.isoformat()}."
            )

        window_key = ""
        window_label = "manual" if run_type == "manual" else ""
        window_start = ""
        window_end = ""
        window_limit_ml: int | None = None
        if run_type == "automatic":
            if not self.settings.refill_automation_enabled(conn=conn):
                raise ValueError("Automatisches Nachfüllen ist deaktiviert")
            pairs = refill_times(local_now.date(), timezone_name, config)
            active = next(
                (
                    (start, end, str(window["start"]))
                    for (start, end), window in zip(
                        pairs,
                        config["refill_windows"],
                    )
                    if aware_utc(start) <= now_utc < aware_utc(end)
                ),
                None,
            )
            if active is None:
                raise ValueError(
                    "Kein automatisches Nachfüllfenster ist geöffnet"
                )
            start, end, window_label = active
            if self.events.refill_for_target(
                local_now.date(),
                window_label,
                conn=conn,
            ):
                raise ValueError(
                    "Dieses Nachfüllfenster wurde bereits abgeschlossen"
                )
            remaining_seconds = math.floor(
                (
                    aware_utc(end)
                    - now_utc
                ).total_seconds()
            ) - self.safety_seconds
            if remaining_seconds <= 0:
                raise ValueError(
                    "Im Nachfüllfenster bleibt keine sichere Pumpenlaufzeit"
                )
            window_limit_ml = math.floor(
                remaining_seconds * pump_ml_per_min / 60
            )
            window_key = refill_window_key(local_now.date(), start, end)
            window_start = aware_utc(start).isoformat()
            window_end = aware_utc(end).isoformat()

        limits = [target_ml, main_room, refill_current]
        if window_limit_ml is not None:
            limits.append(window_limit_ml)
        planned_ml = max(0, min(limits))
        if planned_ml <= 0:
            raise ValueError(
                "Es kann keine sinnvolle Nachfüllmenge freigegeben werden"
            )
        duration_seconds = math.ceil(
            planned_ml / pump_ml_per_min * 60
        )
        expected_complete_at = now_utc + timedelta(
            seconds=duration_seconds
        )
        if (
            window_end
            and expected_complete_at
            > datetime.fromisoformat(window_end)
            - timedelta(seconds=self.safety_seconds)
        ):
            raise ValueError(
                "Die freigegebene Laufzeit überschreitet das Nachfüllfenster"
            )
        limit_reasons = self._limit_reasons(
            target_ml=target_ml,
            main_room_ml=main_room,
            refill_available_ml=refill_current,
            window_limit_ml=window_limit_ml,
            planned_ml=planned_ml,
        )
        return {
            "target_date": local_now.date().isoformat(),
            "window_key": window_key,
            "window_label": window_label,
            "window_start": window_start,
            "window_end": window_end,
            "requested_ml": requested_ml,
            "target_transfer_ml": target_ml,
            "planned_transfer_ml": planned_ml,
            "planned_duration_seconds": duration_seconds,
            "main_tank_start_ml": main_current,
            "refill_tank_start_ml": refill_current,
            "pump_ml_per_min": pump_ml_per_min,
            "expected_complete_at": expected_complete_at.isoformat(),
            "expires_at": (
                expected_complete_at + self.completion_grace
            ).isoformat(),
            "limit_reason": ",".join(limit_reasons),
            "limit_reasons": limit_reasons,
        }

    def start(
        self,
        *,
        run_type: str,
        run_id: object,
        source: str = "home_assistant",
    ) -> dict[str, Any]:
        normalized_run_id, legacy_generated = normalize_run_id(
            run_id,
            "refill",
        )
        now_utc = self._utc_now()
        now_at = now_utc.isoformat()
        with self.database.connection(immediate=True) as conn:
            self.runs.expire_stale(conn, now_at=now_at)
            existing = self.runs.get(normalized_run_id, conn=conn)
            if existing:
                return self._public(
                    existing,
                    idempotent_replay=True,
                    legacy_generated_run_id=legacy_generated,
                    now_utc=now_utc,
                )
            active = self.runs.active(conn=conn)
            if active:
                raise ValueError(
                    "Ein anderer Nachfülllauf ist bereits reserviert "
                    f"oder aktiv ({active['run_id']})"
                )
            plan = self._plan(
                conn,
                run_type=run_type,
                now_utc=now_utc,
            )
            values = {
                **plan,
                "run_id": normalized_run_id,
                "run_type": run_type,
                "created_at": now_at,
                "authorized_at": now_at,
                "source": str(source)[:80],
                "updated_at": now_at,
            }
            try:
                self.runs.insert_reserved(conn, values)
            except sqlite3.IntegrityError as exc:
                repeated = self.runs.get(
                    normalized_run_id,
                    conn=conn,
                )
                if repeated:
                    return self._public(
                        repeated,
                        idempotent_replay=True,
                        legacy_generated_run_id=legacy_generated,
                        now_utc=now_utc,
                    )
                raise ValueError(
                    "Ein anderer Nachfülllauf ist bereits aktiv"
                ) from exc
            reserved = self.runs.get(normalized_run_id, conn=conn)
        return self._public(
            reserved or values,
            idempotent_replay=False,
            legacy_generated_run_id=legacy_generated,
            now_utc=now_utc,
        )

    def mark_running(self, run_id: object) -> dict[str, Any]:
        normalized_run_id, _legacy = normalize_run_id(
            run_id,
            "refill",
        )
        now_utc = self._utc_now()
        now_at = now_utc.isoformat()
        with self.database.connection(immediate=True) as conn:
            run = self.runs.get(normalized_run_id, conn=conn)
            if not run:
                raise ValueError("Unbekannte Nachfüll-run_id")
            replay = run["status"] != "reserved"
            if run["status"] == "reserved":
                self.runs.mark_running(
                    conn,
                    normalized_run_id,
                    started_at=now_at,
                )
                run = self.runs.get(normalized_run_id, conn=conn) or run
        return self._public(
            run,
            idempotent_replay=replay,
            now_utc=now_utc,
        )

    def complete(
        self,
        run_id: object,
        *,
        completion_reason: str = "pump_stopped",
    ) -> dict[str, Any]:
        normalized_run_id, _legacy = normalize_run_id(
            run_id,
            "refill",
        )
        now_utc = self._utc_now()
        now_at = now_utc.isoformat()
        with self.database.connection(immediate=True) as conn:
            run = self.runs.get(normalized_run_id, conn=conn)
            if not run:
                raise ValueError(
                    "Unbekannte Nachfüll-run_id; zuerst /api/refill/start aufrufen"
                )
            if run["status"] == "completed":
                return self._public(
                    run,
                    idempotent_replay=True,
                    now_utc=now_utc,
                )
            if run["status"] in {"failed", "cancelled"}:
                raise ValueError(
                    f"Nachfülllauf ist bereits {run['status']}"
                )

            planned_ml = int(run["planned_transfer_ml"])
            levels = self.tanks.balcony(conn=conn)
            main_room = max(
                0,
                int(levels["tank_capacity_ml"])
                - int(levels["tank_current_ml"]),
            )
            refill_available = max(
                0,
                int(levels["refill_tank_current_ml"]),
            )
            physical_transfer_ml = min(
                planned_ml,
                refill_available,
            )
            main_accounted_ml = min(
                physical_transfer_ml,
                main_room,
            )
            tank_values = self.tanks.adjust_tanks(
                conn,
                main_delta_ml=main_accounted_ml,
                refill_delta_ml=-physical_transfer_ml,
                updated_at=now_at,
            )
            difference_ml = max(
                0,
                planned_ml - main_accounted_ml,
            )
            consistency_note = ""
            if difference_ml:
                consistency_note = (
                    f"Physisch freigegeben waren {planned_ml} ml; "
                    f"aus dem Vorrat wurden rechnerisch "
                    f"{physical_transfer_ml} ml entnommen und im "
                    f"Haupttank konnten {main_accounted_ml} ml "
                    "verbucht werden. Tankstände manuell prüfen."
                )
            self.events.insert_refill(
                conn,
                ran_at=now_at,
                target_date=str(run["target_date"]),
                requested_ml=int(run["requested_ml"]),
                transferred_ml=physical_transfer_ml,
                duration_seconds=int(
                    run["planned_duration_seconds"]
                ),
                window_label=str(run["window_label"]),
                source=str(run["source"]),
                run_id=normalized_run_id,
            )
            config = self.settings.planner_config(conn=conn)
            cooldown_until = (
                now_utc
                + timedelta(
                    minutes=int(
                        config["refill_min_interval_minutes"]
                    )
                )
            ).isoformat()
            self.events.reconcile_refill_plans_after_event(
                conn,
                target_date=str(run["target_date"]),
                window_label=str(run["window_label"]),
                window_key=str(run["window_key"]),
                ran_at=now_at,
                cooldown_until=cooldown_until,
            )
            self.runs.mark_completed(
                conn,
                normalized_run_id,
                completed_at=now_at,
                accounted_transfer_ml=main_accounted_ml,
                physical_transfer_ml=physical_transfer_ml,
                main_accounted_ml=main_accounted_ml,
                tank_values=tank_values,
                consistency_delta_ml=difference_ml,
                consistency_note=consistency_note,
                needs_manual_review=bool(difference_ml),
                completion_reason=(
                    "completed_with_accounting_difference"
                    if difference_ml
                    else str(completion_reason)[:120]
                ),
            )
            completed = self.runs.get(
                normalized_run_id,
                conn=conn,
            ) or run
        return self._public(
            completed,
            idempotent_replay=False,
            now_utc=now_utc,
        )

    def fail(
        self,
        run_id: object,
        *,
        error: object = "",
        may_have_transferred: bool | None = None,
    ) -> dict[str, Any]:
        normalized_run_id, _legacy = normalize_run_id(
            run_id,
            "refill",
        )
        now_utc = self._utc_now()
        now_at = now_utc.isoformat()
        with self.database.connection(immediate=True) as conn:
            run = self.runs.get(normalized_run_id, conn=conn)
            if not run:
                raise ValueError("Unbekannte Nachfüll-run_id")
            if run["status"] in {
                "completed",
                "failed",
                "expired",
                "cancelled",
            }:
                return self._public(
                    run,
                    idempotent_replay=True,
                    now_utc=now_utc,
                )
            uncertain = (
                run["status"] == "running"
                if may_have_transferred is None
                else bool(may_have_transferred)
            )
            self.runs.mark_failed(
                conn,
                normalized_run_id,
                failed_at=now_at,
                error_text=(
                    str(error).strip()[:500]
                    or "Nachfüllpumpe konnte nicht sicher ausgeführt werden."
                ),
                needs_manual_review=uncertain,
                completion_reason=(
                    "pump_state_uncertain"
                    if uncertain
                    else "pump_not_started"
                ),
            )
            failed = self.runs.get(
                normalized_run_id,
                conn=conn,
            ) or run
        return self._public(
            failed,
            idempotent_replay=False,
            now_utc=now_utc,
        )

    def get(self, run_id: object) -> dict[str, Any]:
        normalized_run_id, _legacy = normalize_run_id(
            run_id,
            "refill",
        )
        run = self.runs.get(normalized_run_id)
        if not run:
            raise ValueError("Unbekannte Nachfüll-run_id")
        return self._public(run, now_utc=self._utc_now())

    def expire_stale(self) -> int:
        now_at = self._utc_now().isoformat()
        with self.database.connection(immediate=True) as conn:
            return self.runs.expire_stale(conn, now_at=now_at)

    def diagnostics(self) -> dict[str, Any]:
        now_utc = self._utc_now()
        self.expire_stale()
        active = self.runs.active()
        uncertain = self.runs.recent_uncertain()
        completed = self.runs.latest_with_statuses(("completed",))
        failed = self.runs.latest_with_statuses(
            ("failed", "expired", "cancelled")
        )
        return {
            "active_run": (
                self._public(active, now_utc=now_utc)
                if active
                else None
            ),
            "uncertain_runs": [
                self._public(item, now_utc=now_utc)
                for item in uncertain
            ],
            "last_successful_run": (
                self._public(completed, now_utc=now_utc)
                if completed
                else None
            ),
            "last_failed_run": (
                self._public(failed, now_utc=now_utc)
                if failed
                else None
            ),
            "manual_review_required": bool(uncertain),
        }

    def complete_legacy(
        self,
        *,
        run_id: object,
        source: str,
        run_type: str = "automatic",
    ) -> dict[str, Any]:
        normalized_run_id, legacy_generated = normalize_run_id(
            run_id,
            "refill",
        )
        existing = self.runs.get(normalized_run_id)
        if not existing:
            self.start(
                run_type=run_type,
                run_id=normalized_run_id,
                source=("manual" if run_type == "manual" else source),
            )
        result = self.complete(normalized_run_id)
        return {
            **result,
            "legacy_generated_run_id": legacy_generated,
        }

    @staticmethod
    def _public(
        run: dict[str, Any],
        *,
        idempotent_replay: bool = False,
        legacy_generated_run_id: bool = False,
        now_utc: datetime,
    ) -> dict[str, Any]:
        authorized = parse_event_datetime(run.get("authorized_at"))
        elapsed_seconds = (
            max(
                0,
                math.floor(
                    (
                        now_utc
                        - aware_utc(authorized)
                    ).total_seconds()
                ),
            )
            if authorized
            else 0
        )
        limit_reason = str(run.get("limit_reason", ""))
        public_run = {
            key: value
            for key, value in dict(run).items()
            if key != "active_slot"
        }
        completed = run.get("status") == "completed"
        return {
            **public_run,
            "duration_seconds": int(
                run.get("planned_duration_seconds", 0)
            ),
            "authorized_transfer_ml": int(
                run.get("planned_transfer_ml", 0)
            ),
            "transferred_ml": int(
                run.get("physical_transfer_ml")
                if completed
                and run.get("physical_transfer_ml") is not None
                else 0
            ),
            "main_accounted_ml": int(
                run.get("main_accounted_ml")
                if run.get("main_accounted_ml") is not None
                else run.get("accounted_transfer_ml") or 0
            ),
            "limit_reasons": [
                item for item in limit_reason.split(",") if item
            ],
            "limited": bool(limit_reason),
            "elapsed_seconds": elapsed_seconds,
            "expected_latest_completion_at": str(
                run.get("expires_at", "")
            ),
            "needs_manual_review": bool(
                run.get("needs_manual_review")
            ),
            "idempotent_replay": idempotent_replay,
            "legacy_generated_run_id": legacy_generated_run_id,
            "window": {
                "key": str(run.get("window_key", "")),
                "label": str(run.get("window_label", "")),
                "start": str(run.get("window_start", "")),
                "end": str(run.get("window_end", "")),
            },
        }
