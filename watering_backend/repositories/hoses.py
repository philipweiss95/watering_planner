from __future__ import annotations

import sqlite3

from watering_backend.database import Database
from watering_backend.validation import normalize_hose_numbers


def row_dict(row: sqlite3.Row) -> dict:
    return {key: row[key] for key in row.keys()}


class HosesRepository:
    def __init__(self, database: Database):
        self.database = database

    def list(self, conn: sqlite3.Connection | None = None) -> list[dict]:
        if conn is None:
            with self.database.connection() as active:
                return self.list(active)
        return [
            row_dict(row)
            for row in conn.execute(
                """
                SELECT
                    irrigation_hoses.number,
                    irrigation_hoses.outlet_id,
                    irrigation_hoses.plant_id,
                    pump_outlets.name AS outlet_name,
                    pump_outlets.ml_per_run,
                    plants.custom_name AS plant_name
                FROM irrigation_hoses
                JOIN pump_outlets
                    ON pump_outlets.id = irrigation_hoses.outlet_id
                LEFT JOIN plants ON plants.id = irrigation_hoses.plant_id
                ORDER BY
                    CAST(irrigation_hoses.number AS INTEGER),
                    irrigation_hoses.number
                """
            )
        ]

    def default_outlet_id(
        self,
        conn: sqlite3.Connection | None = None,
    ) -> int:
        if conn is None:
            with self.database.connection() as active:
                return self.default_outlet_id(active)
        row = conn.execute(
            "SELECT id FROM pump_outlets ORDER BY ml_per_run LIMIT 1"
        ).fetchone()
        return int(row["id"])

    def sync_legacy_connection(
        self,
        conn: sqlite3.Connection,
        plant_id: int,
    ) -> None:
        hoses = [
            row_dict(row)
            for row in conn.execute(
                """
                SELECT
                    irrigation_hoses.number,
                    irrigation_hoses.outlet_id,
                    pump_outlets.ml_per_run
                FROM irrigation_hoses
                JOIN pump_outlets
                    ON pump_outlets.id = irrigation_hoses.outlet_id
                WHERE irrigation_hoses.plant_id = ?
                ORDER BY
                    CAST(irrigation_hoses.number AS INTEGER),
                    irrigation_hoses.number
                """,
                (plant_id,),
            )
        ]
        conn.execute(
            """
            UPDATE plants
            SET outlet_id = ?, hose_numbers = ?, target_ml_per_cycle = ?
            WHERE id = ?
            """,
            (
                int(hoses[0]["outlet_id"])
                if hoses
                else self.default_outlet_id(conn),
                ", ".join(hose["number"] for hose in hoses),
                sum(int(hose["ml_per_run"]) for hose in hoses) or None,
                plant_id,
            ),
        )

    def assign_to_plant(
        self,
        conn: sqlite3.Connection,
        plant_id: int,
        hose_numbers: object,
    ) -> None:
        selected = normalize_hose_numbers(hose_numbers)
        existing = {
            str(row["number"])
            for row in conn.execute("SELECT number FROM irrigation_hoses")
        }
        missing = [number for number in selected if number not in existing]
        if missing:
            raise ValueError(f"Unbekannte Schläuche: {', '.join(missing)}")
        placeholders = ", ".join("?" for _ in selected) or "NULL"
        affected = {
            int(row["plant_id"])
            for row in conn.execute(
                "SELECT DISTINCT plant_id FROM irrigation_hoses "
                "WHERE plant_id IS NOT NULL "
                f"AND (plant_id = ? OR number IN ({placeholders}))",
                (plant_id, *selected),
            )
        }
        conn.execute(
            "UPDATE irrigation_hoses SET plant_id = NULL WHERE plant_id = ?",
            (plant_id,),
        )
        if selected:
            conn.execute(
                "UPDATE irrigation_hoses SET plant_id = ? "
                f"WHERE number IN ({', '.join('?' for _ in selected)})",
                (plant_id, *selected),
            )
        affected.add(plant_id)
        for affected_id in affected:
            self.sync_legacy_connection(conn, affected_id)

    def save(self, payload: dict) -> None:
        hoses = payload.get("hoses")
        if not isinstance(hoses, list):
            raise ValueError("hoses fehlt")
        normalized: list[dict] = []
        seen: set[str] = set()
        for hose in hoses:
            numbers = normalize_hose_numbers(hose.get("number", ""))
            if len(numbers) != 1:
                raise ValueError("Jeder Schlauch braucht genau eine Nummer")
            number = numbers[0]
            if number in seen:
                raise ValueError(f"Schlauch {number} ist doppelt vorhanden")
            seen.add(number)
            normalized.append(
                {"number": number, "outlet_id": int(hose["outlet_id"])}
            )
        with self.database.connection() as conn:
            if normalized:
                conn.execute(
                    "DELETE FROM irrigation_hoses "
                    f"WHERE number NOT IN ({', '.join('?' for _ in normalized)})",
                    tuple(hose["number"] for hose in normalized),
                )
            else:
                conn.execute("DELETE FROM irrigation_hoses")
            for hose in normalized:
                conn.execute(
                    """
                    INSERT INTO irrigation_hoses (number, outlet_id)
                    VALUES (?, ?)
                    ON CONFLICT(number) DO UPDATE
                    SET outlet_id = excluded.outlet_id
                    """,
                    (hose["number"], hose["outlet_id"]),
                )
            for plant in conn.execute("SELECT id FROM plants"):
                self.sync_legacy_connection(conn, int(plant["id"]))

    def sync_all_legacy_connections(self, conn: sqlite3.Connection) -> None:
        for row in conn.execute("SELECT id FROM plants"):
            self.sync_legacy_connection(conn, int(row["id"]))
