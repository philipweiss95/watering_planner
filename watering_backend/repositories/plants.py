from __future__ import annotations

import sqlite3
from collections.abc import Callable

from watering_backend.database import Database
from watering_backend.repositories.hoses import HosesRepository, row_dict
from watering_backend.validation import (
    validate_plant_payload,
    validate_position_payload,
)


class PlantsRepository:
    def __init__(
        self,
        database: Database,
        hoses: HosesRepository,
        now_iso: Callable[[], str],
    ):
        self.database = database
        self.hoses = hoses
        self.now_iso = now_iso

    def catalog(self, conn: sqlite3.Connection | None = None) -> list[dict]:
        if conn is None:
            with self.database.connection() as active:
                return self.catalog(active)
        return [
            row_dict(row)
            for row in conn.execute(
                "SELECT * FROM plant_catalog ORDER BY category, name"
            )
        ]

    def list(
        self,
        conn: sqlite3.Connection | None = None,
        *,
        hoses: list[dict] | None = None,
    ) -> list[dict]:
        if conn is None:
            with self.database.connection() as active:
                active_hoses = self.hoses.list(active)
                return self.list(active, hoses=active_hoses)
        active_hoses = hoses if hoses is not None else self.hoses.list(conn)
        result = []
        for row in conn.execute(
            """
            SELECT
                plants.*,
                plant_catalog.name AS catalog_name,
                plant_catalog.category,
                plant_catalog.base_ml_per_l_day,
                plant_catalog.sun_factor,
                plant_catalog.drought_sensitivity,
                plant_catalog.crop_coefficient,
                plant_catalog.canopy_m2_medium,
                plant_catalog.recommended_pot_liters,
                plant_catalog.moisture_preference,
                plant_catalog.notes,
                pump_outlets.name AS outlet_name,
                pump_outlets.ml_per_run
            FROM plants
            JOIN plant_catalog ON plant_catalog.id = plants.catalog_id
            JOIN pump_outlets ON pump_outlets.id = plants.outlet_id
            ORDER BY plants.created_at, plants.id
            """
        ):
            plant = row_dict(row)
            assigned = [
                hose for hose in active_hoses if hose["plant_id"] == plant["id"]
            ]
            plant["hoses"] = assigned
            plant["hose_numbers"] = ", ".join(hose["number"] for hose in assigned)
            plant["hose_count"] = len(assigned)
            plant["configured_ml_per_cycle"] = sum(
                int(hose["ml_per_run"]) for hose in assigned
            )
            plant["outlet_summary"] = ", ".join(
                f"{hose['number']}: {hose['outlet_name']} "
                f"({hose['ml_per_run']} ml)"
                for hose in assigned
            )
            result.append(plant)
        return result

    def add(self, payload: dict) -> int:
        with self.database.connection() as conn:
            catalog_ids = [
                str(row["id"]) for row in conn.execute("SELECT id FROM plant_catalog")
            ]
            plant = validate_plant_payload(payload, catalog_ids)
            cursor = conn.execute(
                """
                INSERT INTO plants
                    (
                        catalog_id, custom_name, size, pot_liters, pot_type,
                        outlet_id, pos_x, pos_y, hose_numbers,
                        target_ml_per_cycle, created_at
                    )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    plant["catalog_id"],
                    plant["custom_name"],
                    plant["size"],
                    plant["pot_liters"],
                    plant["pot_type"],
                    self.hoses.default_outlet_id(conn),
                    plant["pos_x"],
                    plant["pos_y"],
                    plant["hose_numbers"],
                    None,
                    self.now_iso(),
                ),
            )
            plant_id = int(cursor.lastrowid)
            self.hoses.assign_to_plant(conn, plant_id, plant["hose_numbers"])
            return plant_id

    def update(self, plant_id: int, payload: dict) -> None:
        with self.database.connection() as conn:
            catalog_ids = [
                str(row["id"]) for row in conn.execute("SELECT id FROM plant_catalog")
            ]
            plant = validate_plant_payload(payload, catalog_ids)
            cursor = conn.execute(
                """
                UPDATE plants
                SET catalog_id = ?, custom_name = ?, size = ?,
                    pot_liters = ?, pot_type = ?
                WHERE id = ?
                """,
                (
                    plant["catalog_id"],
                    plant["custom_name"],
                    plant["size"],
                    plant["pot_liters"],
                    plant["pot_type"],
                    plant_id,
                ),
            )
            if cursor.rowcount == 0:
                raise ValueError("Pflanze nicht gefunden")
            self.hoses.assign_to_plant(conn, plant_id, plant["hose_numbers"])

    def update_position(self, plant_id: int, payload: dict) -> None:
        pos_x, pos_y = validate_position_payload(payload)
        with self.database.connection() as conn:
            cursor = conn.execute(
                "UPDATE plants SET pos_x = ?, pos_y = ? WHERE id = ?",
                (pos_x, pos_y, plant_id),
            )
            if cursor.rowcount == 0:
                raise ValueError("Pflanze nicht gefunden")

    def delete(self, plant_id: int) -> None:
        with self.database.connection() as conn:
            conn.execute(
                "UPDATE irrigation_hoses SET plant_id = NULL WHERE plant_id = ?",
                (plant_id,),
            )
            conn.execute("DELETE FROM plants WHERE id = ?", (plant_id,))
