from __future__ import annotations

import sqlite3

from watering_backend.database import Database
from watering_backend.validation import (
    finite_integer,
    normalize_hose_numbers,
)


MAX_CONNECTIONS_PER_OUTLET = 12


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

    def _normalize_snapshot(
        self,
        payload: dict,
    ) -> list[dict[str, int | str | bool | None]]:
        hoses = payload.get("hoses")
        if not isinstance(hoses, list):
            raise ValueError("hoses fehlt")
        normalized: list[dict[str, int | str | bool | None]] = []
        seen: set[str] = set()
        for index, hose in enumerate(hoses):
            if not isinstance(hose, dict):
                raise ValueError(
                    f"hoses[{index}] muss ein Objekt sein"
                )
            unknown = set(hose) - {
                "number",
                "outlet_id",
                "plant_id",
            }
            if unknown:
                raise ValueError(
                    "Unbekanntes Schlauchfeld: "
                    f"{sorted(unknown)[0]}"
                )
            if "outlet_id" not in hose:
                raise KeyError(f"hoses[{index}].outlet_id fehlt")
            numbers = normalize_hose_numbers(hose.get("number", ""))
            if len(numbers) != 1:
                raise ValueError("Jeder Schlauch braucht genau eine Nummer")
            number = numbers[0]
            if number in seen:
                raise ValueError(f"Schlauch {number} ist doppelt vorhanden")
            seen.add(number)
            plant_value = hose.get("plant_id")
            plant_id_provided = "plant_id" in hose
            plant_id = (
                None
                if plant_value is None or plant_value == ""
                else finite_integer(
                    plant_value,
                    f"hoses[{index}].plant_id",
                    minimum=1,
                    maximum=2_147_483_647,
                )
            )
            normalized.append(
                {
                    "number": number,
                    "outlet_id": finite_integer(
                        hose["outlet_id"],
                        f"hoses[{index}].outlet_id",
                        minimum=1,
                        maximum=1_000_000,
                    ),
                    "plant_id": plant_id,
                    "plant_id_provided": plant_id_provided,
                }
            )
        return normalized

    def save(self, payload: dict) -> None:
        normalized = self._normalize_snapshot(payload)
        with self.database.connection(immediate=True) as conn:
            outlet_ids = {
                int(row["id"])
                for row in conn.execute("SELECT id FROM pump_outlets")
            }
            plant_ids = {
                int(row["id"])
                for row in conn.execute("SELECT id FROM plants")
            }
            outlet_counts: dict[int, int] = {}
            for hose in normalized:
                outlet_id = int(hose["outlet_id"])
                if outlet_id not in outlet_ids:
                    raise ValueError(
                        f"Unbekannter Ausgang: {outlet_id}"
                    )
                plant_id = hose["plant_id"]
                if (
                    plant_id is not None
                    and int(plant_id) not in plant_ids
                ):
                    raise ValueError(
                        f"Unbekannte Pflanzen-ID: {plant_id}"
                    )
                outlet_counts[outlet_id] = (
                    outlet_counts.get(outlet_id, 0) + 1
                )
            exceeded = next(
                (
                    outlet_id
                    for outlet_id, count in outlet_counts.items()
                    if count > MAX_CONNECTIONS_PER_OUTLET
                ),
                None,
            )
            if exceeded is not None:
                raise ValueError(
                    f"Ausgang {exceeded} überschreitet das Limit von "
                    f"{MAX_CONNECTIONS_PER_OUTLET} Anschlüssen"
                )

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
                    INSERT INTO irrigation_hoses (
                        number, outlet_id, plant_id
                    )
                    VALUES (?, ?, ?)
                    ON CONFLICT(number) DO UPDATE
                    SET outlet_id = excluded.outlet_id,
                        plant_id = CASE
                            WHEN ? THEN excluded.plant_id
                            ELSE irrigation_hoses.plant_id
                        END
                    """,
                    (
                        hose["number"],
                        hose["outlet_id"],
                        hose["plant_id"],
                        int(bool(hose["plant_id_provided"])),
                    ),
                )
            self.sync_all_legacy_connections(conn)

    def sync_all_legacy_connections(self, conn: sqlite3.Connection) -> None:
        for row in conn.execute("SELECT id FROM plants"):
            self.sync_legacy_connection(conn, int(row["id"]))
