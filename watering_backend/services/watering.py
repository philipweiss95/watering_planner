from __future__ import annotations

import re
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from watering_backend.database import Database
from watering_backend.repositories.events import EventsRepository
from watering_backend.repositories.settings import SettingsRepository
from watering_backend.repositories.tanks import TanksRepository


def normalize_run_id(run_id: object, event_type: str) -> tuple[str, bool]:
    value = str(run_id or "").strip()
    generated = not value
    if generated:
        value = f"legacy-{event_type}-{uuid.uuid4()}"
    if len(value) > 128 or not re.fullmatch(r"[A-Za-z0-9._:-]+", value):
        raise ValueError(
            "run_id darf maximal 128 Zeichen aus A-Z, a-z, 0-9, Punkt, "
            "Doppelpunkt, Unterstrich und Minus enthalten"
        )
    return value, generated


class WateringService:
    """Transactional watering/refill accounting and direct tank fills."""

    def __init__(
        self,
        *,
        database: Database,
        events: EventsRepository,
        tanks: TanksRepository,
        settings: SettingsRepository,
        now_iso: Callable[[], str],
        local_now: Callable[[str], datetime],
        state_provider: Callable[[], dict[str, Any]],
        refill_status: Callable[[dict[str, Any]], dict[str, Any]],
        pending_refill_request: Callable[[], dict[str, Any] | None],
        clear_pending_refill_request: Callable[[], None],
    ):
        self.database = database
        self.events = events
        self.tanks = tanks
        self.settings = settings
        self.now_iso = now_iso
        self.local_now = local_now
        self.state_provider = state_provider
        self.refill_status = refill_status
        self.pending_refill_request = pending_refill_request
        self.clear_pending_refill_request = clear_pending_refill_request

    def mark_run(
        self,
        delivered_ml: int,
        temperature_c: float,
        rain_mm: float,
        run_id: object = None,
        source: str = "homekit",
    ) -> dict[str, Any]:
        normalized_run_id, legacy_generated = normalize_run_id(
            run_id,
            "watering",
        )
        actual_consumed_ml = self.settings.calibrated_consumption_ml(delivered_ml)
        timestamp = self.now_iso()
        with self.database.connection(immediate=True) as conn:
            existing = self.events.watering_by_run_id(
                normalized_run_id,
                conn=conn,
            )
            if existing:
                return {
                    **existing,
                    "idempotent_replay": True,
                    "legacy_generated_run_id": False,
                }
            tank = self.tanks.balcony(conn=conn)
            if int(tank["tank_current_ml"]) < actual_consumed_ml:
                raise ValueError(
                    "Der Haupttank reicht nicht fuer den kalibrierten "
                    "Verbrauch eines vollstaendigen Zyklus"
                )
            event_id = self.events.insert_watering(
                conn,
                ran_at=timestamp,
                delivered_ml=int(delivered_ml),
                actual_consumed_ml=actual_consumed_ml,
                temperature_c=float(temperature_c),
                rain_mm=float(rain_mm),
                source=source,
                run_id=normalized_run_id,
            )
            self.tanks.adjust_tanks(
                conn,
                main_delta_ml=-actual_consumed_ml,
                updated_at=timestamp,
            )
        return {
            "id": event_id,
            "run_id": normalized_run_id,
            "ran_at": timestamp,
            "delivered_ml": int(delivered_ml),
            "actual_consumed_ml": actual_consumed_ml,
            "temperature_c": float(temperature_c),
            "rain_mm": float(rain_mm),
            "source": source,
            "idempotent_replay": False,
            "legacy_generated_run_id": legacy_generated,
        }

    def mark_refill_run(
        self,
        source: str = "home_assistant",
        run_id: object = None,
    ) -> dict[str, Any]:
        normalized_run_id, legacy_generated = normalize_run_id(run_id, "refill")
        existing = self.events.refill_by_run_id(normalized_run_id)
        if existing:
            return {
                **existing,
                "planned_transfer_ml": int(existing["transferred_ml"]),
                "idempotent_replay": True,
                "legacy_generated_run_id": False,
            }

        state = self.state_provider()
        pending = self.pending_refill_request()
        refill = self.refill_status(state["balcony"])
        if pending:
            if refill["cooldown_active"]:
                raise ValueError(
                    "Zwischen zwei Nachfüllvorgängen müssen mindestens "
                    "drei Stunden liegen"
                )
            refill = {
                **refill,
                "target_date": pending.get(
                    "target_date",
                    refill.get("target_date", ""),
                ),
                "requested_ml": int(pending.get("requested_ml", 0)),
                "planned_transfer_ml": int(pending.get("transferred_ml", 0)),
                "duration_seconds": int(pending.get("duration_seconds", 0)),
                "window_label": str(
                    pending.get("window_label", "manual")
                ),
            }
            source = str(pending.get("source", "manual"))
        elif not refill["enabled"]:
            raise ValueError("Automatisches Nachfüllen ist deaktiviert")
        elif not refill["schedule_due"]:
            raise ValueError(
                "Für dieses Nachfüllzeitfenster wurde bereits ein Lauf "
                "verbucht oder es ist noch nicht erreicht"
            )
        elif refill["cooldown_active"]:
            raise ValueError(
                "Zwischen zwei Nachfüllvorgängen müssen mindestens "
                "drei Stunden liegen"
            )

        planned_transfer_ml = int(refill["planned_transfer_ml"])
        if planned_transfer_ml <= 0:
            raise ValueError(refill["summary"] or "Keine Nachfüllung nötig")
        now = self.local_now(
            str(state["balcony"].get("timezone_name", "Europe/Berlin"))
        ).astimezone(timezone.utc).isoformat()

        with self.database.connection(immediate=True) as conn:
            existing = self.events.refill_by_run_id(
                normalized_run_id,
                conn=conn,
            )
            if existing:
                return {
                    **existing,
                    "planned_transfer_ml": int(existing["transferred_ml"]),
                    "idempotent_replay": True,
                    "legacy_generated_run_id": False,
                }
            levels = self.tanks.balcony(conn=conn)
            transferred_ml = min(
                planned_transfer_ml,
                max(
                    0,
                    int(levels["tank_capacity_ml"])
                    - int(levels["tank_current_ml"]),
                ),
                max(0, int(levels["refill_tank_current_ml"])),
            )
            if transferred_ml <= 0:
                raise ValueError(
                    "Zum Buchungszeitpunkt ist keine Nachfuellmenge "
                    "mehr verfuegbar"
                )
            self.tanks.adjust_tanks(
                conn,
                main_delta_ml=transferred_ml,
                refill_delta_ml=-transferred_ml,
                updated_at=now,
            )
            self.events.insert_refill(
                conn,
                ran_at=now,
                target_date=str(refill["target_date"]),
                requested_ml=int(refill["requested_ml"]),
                transferred_ml=transferred_ml,
                duration_seconds=int(refill["duration_seconds"]),
                window_label=str(
                    refill.get("window_label")
                    or refill.get("active_window")
                    or refill.get("last_window")
                    or ""
                ),
                source=source,
                run_id=normalized_run_id,
            )
        if pending:
            self.clear_pending_refill_request()
        return {
            **refill,
            "run_id": normalized_run_id,
            "planned_transfer_ml": transferred_ml,
            "transferred_ml": transferred_ml,
            "idempotent_replay": False,
            "legacy_generated_run_id": legacy_generated,
        }

    def fill_tank(self, tank_name: str) -> None:
        if tank_name not in {"main", "refill"}:
            raise ValueError("Unbekannter Tank")
        now = self.now_iso()
        with self.database.connection(immediate=True) as conn:
            state = self.tanks.balcony(conn=conn)
            if tank_name == "main":
                current = int(state["tank_current_ml"])
                capacity = int(state["tank_capacity_ml"])
            else:
                current = int(state["refill_tank_current_ml"])
                capacity = int(state["refill_tank_capacity_ml"])
            self.tanks.set_tank_level(
                conn,
                tank_name,
                capacity,
                updated_at=now,
            )
            self.events.insert_tank_fill(
                conn,
                ran_at=now,
                tank_name=tank_name,
                previous_ml=current,
                new_ml=capacity,
                capacity_ml=capacity,
                source="ui",
            )

