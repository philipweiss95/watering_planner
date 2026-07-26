from __future__ import annotations

import sqlite3
from typing import Any

from watering_backend.database import Database


class TanksRepository:
    """Persistence boundary for balcony, tank, outlet and wall state."""

    def __init__(self, database: Database):
        self.database = database

    def balcony(self, *, conn: sqlite3.Connection | None = None) -> dict[str, Any]:
        if conn is not None:
            row = conn.execute("SELECT * FROM balcony_settings WHERE id = 1").fetchone()
            if row is None:
                raise RuntimeError("Balcony settings have not been initialized")
            return dict(row)
        with self.database.connection() as active:
            return self.balcony(conn=active)

    def outlets(self, *, conn: sqlite3.Connection | None = None) -> list[dict[str, Any]]:
        if conn is not None:
            return [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT id, name, ml_per_run
                    FROM pump_outlets
                    ORDER BY ml_per_run
                    """
                ).fetchall()
            ]
        with self.database.connection() as active:
            return self.outlets(conn=active)

    def walls(self, *, conn: sqlite3.Connection | None = None) -> list[dict[str, Any]]:
        if conn is not None:
            return [
                dict(row)
                for row in conn.execute(
                    "SELECT side, height_m FROM terrace_walls ORDER BY side"
                ).fetchall()
            ]
        with self.database.connection() as active:
            return self.walls(conn=active)

    def update_balcony(
        self,
        conn: sqlite3.Connection,
        values: dict[str, Any],
        *,
        updated_at: str,
    ) -> None:
        allowed = {
            "orientation",
            "orientation_deg",
            "width_m",
            "depth_m",
            "location",
            "latitude",
            "longitude",
            "timezone_name",
            "wall_height_m",
            "tank_capacity_ml",
            "tank_current_ml",
            "refill_tank_capacity_ml",
            "refill_tank_current_ml",
            "refill_pump_ml_per_min",
        }
        assignments = [f"{key} = ?" for key in values if key in allowed]
        if not assignments:
            return
        params = [values[key] for key in values if key in allowed]
        params.extend([updated_at, 1])
        conn.execute(
            f"UPDATE balcony_settings SET {', '.join(assignments)}, updated_at = ? WHERE id = ?",
            params,
        )

    def replace_outlets(
        self,
        conn: sqlite3.Connection,
        outlets: list[dict[str, Any]],
    ) -> None:
        current_ids = {
            int(row["id"])
            for row in conn.execute("SELECT id FROM pump_outlets").fetchall()
        }
        requested_ids = {int(outlet["id"]) for outlet in outlets}
        for outlet in outlets:
            conn.execute(
                """
                INSERT INTO pump_outlets(id, name, ml_per_run)
                VALUES (?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    name = excluded.name,
                    ml_per_run = excluded.ml_per_run
                """,
                (
                    int(outlet["id"]),
                    str(outlet["name"]),
                    int(outlet["ml_per_run"]),
                ),
            )
        removable = current_ids - requested_ids
        if removable:
            placeholders = ",".join("?" for _ in removable)
            references = conn.execute(
                f"""
                SELECT COUNT(*) AS count
                FROM plants
                WHERE outlet_id IN ({placeholders})
                """,
                tuple(removable),
            ).fetchone()
            hose_references = conn.execute(
                f"""
                SELECT COUNT(*) AS count
                FROM irrigation_hoses
                WHERE outlet_id IN ({placeholders})
                """,
                tuple(removable),
            ).fetchone()
            if int(references["count"]) or int(hose_references["count"]):
                raise ValueError("Ausgang wird noch von Pflanzen oder Schläuchen verwendet.")
            conn.execute(
                f"DELETE FROM pump_outlets WHERE id IN ({placeholders})",
                tuple(removable),
            )

    def replace_walls(
        self,
        conn: sqlite3.Connection,
        walls: list[dict[str, Any]],
    ) -> None:
        conn.execute("DELETE FROM terrace_walls")
        conn.executemany(
            "INSERT INTO terrace_walls(side, height_m) VALUES (?, ?)",
            [(str(wall["side"]), float(wall["height_m"])) for wall in walls],
        )

    def save_walls(
        self,
        conn: sqlite3.Connection,
        walls: list[dict[str, Any]],
    ) -> None:
        conn.executemany(
            """
            INSERT INTO terrace_walls(side, height_m)
            VALUES (?, ?)
            ON CONFLICT(side) DO UPDATE SET height_m = excluded.height_m
            """,
            [(str(wall["side"]), float(wall["height_m"])) for wall in walls],
        )

    def set_tank_level(
        self,
        conn: sqlite3.Connection,
        tank_name: str,
        level_ml: int,
        *,
        updated_at: str,
    ) -> None:
        column = {
            "main": "tank_current_ml",
            "refill": "refill_tank_current_ml",
        }.get(tank_name)
        if column is None:
            raise ValueError("Unbekannter Tank.")
        conn.execute(
            f"UPDATE balcony_settings SET {column} = ?, updated_at = ? WHERE id = 1",
            (int(level_ml), updated_at),
        )

    def adjust_tanks(
        self,
        conn: sqlite3.Connection,
        *,
        main_delta_ml: int = 0,
        refill_delta_ml: int = 0,
        updated_at: str,
    ) -> dict[str, int]:
        state = self.balcony(conn=conn)
        main_after = min(
            int(state["tank_capacity_ml"]),
            max(0, int(state["tank_current_ml"]) + int(main_delta_ml)),
        )
        refill_after = min(
            int(state["refill_tank_capacity_ml"]),
            max(0, int(state["refill_tank_current_ml"]) + int(refill_delta_ml)),
        )
        conn.execute(
            """
            UPDATE balcony_settings
            SET tank_current_ml = ?,
                refill_tank_current_ml = ?,
                updated_at = ?
            WHERE id = 1
            """,
            (main_after, refill_after, updated_at),
        )
        return {
            "main_before_ml": int(state["tank_current_ml"]),
            "main_after_ml": main_after,
            "refill_before_ml": int(state["refill_tank_current_ml"]),
            "refill_after_ml": refill_after,
        }

    def state(self) -> dict[str, Any]:
        with self.database.connection() as conn:
            return {
                "balcony": self.balcony(conn=conn),
                "walls": self.walls(conn=conn),
                "outlets": self.outlets(conn=conn),
            }
