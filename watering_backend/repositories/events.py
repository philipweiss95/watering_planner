from __future__ import annotations

import sqlite3
from datetime import date, datetime
from typing import Any

from watering_backend.database import Database


def _row_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


class EventsRepository:
    """Persistence boundary for watering, refill, fill and calibration events."""

    def __init__(self, database: Database):
        self.database = database

    def watering_by_run_id(
        self,
        run_id: str,
        *,
        conn: sqlite3.Connection | None = None,
    ) -> dict[str, Any] | None:
        if not run_id:
            return None
        if conn is not None:
            return _row_dict(
                conn.execute(
                    "SELECT * FROM watering_events WHERE run_id = ?",
                    (run_id,),
                ).fetchone()
            )
        with self.database.connection() as active:
            return self.watering_by_run_id(run_id, conn=active)

    def refill_by_run_id(
        self,
        run_id: str,
        *,
        conn: sqlite3.Connection | None = None,
    ) -> dict[str, Any] | None:
        if not run_id:
            return None
        if conn is not None:
            return _row_dict(
                conn.execute(
                    "SELECT * FROM refill_events WHERE run_id = ?",
                    (run_id,),
                ).fetchone()
            )
        with self.database.connection() as active:
            return self.refill_by_run_id(run_id, conn=active)

    def insert_watering(
        self,
        conn: sqlite3.Connection,
        *,
        ran_at: str,
        delivered_ml: int,
        actual_consumed_ml: int,
        temperature_c: float | None,
        rain_mm: float | None,
        source: str,
        run_id: str,
    ) -> int:
        cursor = conn.execute(
            """
            INSERT INTO watering_events(
                ran_at, delivered_ml, actual_consumed_ml,
                temperature_c, rain_mm, source, run_id
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                ran_at,
                delivered_ml,
                actual_consumed_ml,
                temperature_c,
                rain_mm,
                source,
                run_id,
            ),
        )
        return int(cursor.lastrowid)

    def insert_refill(
        self,
        conn: sqlite3.Connection,
        *,
        ran_at: str,
        target_date: str,
        requested_ml: int,
        transferred_ml: int,
        duration_seconds: int,
        window_label: str,
        source: str,
        run_id: str,
    ) -> int:
        cursor = conn.execute(
            """
            INSERT INTO refill_events(
                ran_at, target_date, requested_ml, transferred_ml,
                duration_seconds, window_label, source, run_id
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                ran_at,
                target_date,
                requested_ml,
                transferred_ml,
                duration_seconds,
                window_label,
                source,
                run_id,
            ),
        )
        return int(cursor.lastrowid)

    def insert_tank_fill(
        self,
        conn: sqlite3.Connection,
        *,
        ran_at: str,
        tank_name: str,
        previous_ml: int,
        new_ml: int,
        capacity_ml: int,
        source: str,
    ) -> int:
        cursor = conn.execute(
            """
            INSERT INTO tank_fill_events(
                ran_at, tank_name, previous_ml, new_ml, capacity_ml, source
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (ran_at, tank_name, previous_ml, new_ml, capacity_ml, source),
        )
        return int(cursor.lastrowid)

    def insert_calibration(
        self,
        conn: sqlite3.Connection,
        *,
        calibrated_at: str,
        pump_name: str,
        measured_level_ml: int,
        baseline_at: str,
        baseline_level_ml: int,
        cycles: int,
        nominal_ml: int,
        measured_ml: int,
        result_value: float,
    ) -> int:
        cursor = conn.execute(
            """
            INSERT INTO pump_calibration_events(
                calibrated_at, pump_name, measured_level_ml, baseline_at,
                baseline_level_ml, cycles, nominal_ml, measured_ml, result_value
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                calibrated_at,
                pump_name,
                measured_level_ml,
                baseline_at,
                baseline_level_ml,
                cycles,
                nominal_ml,
                measured_ml,
                result_value,
            ),
        )
        return int(cursor.lastrowid)

    def latest_calibration(self, pump_name: str) -> dict[str, Any] | None:
        with self.database.connection() as conn:
            return self.latest_calibration_in(conn, pump_name)

    def latest_calibration_in(
        self,
        conn: sqlite3.Connection,
        pump_name: str,
    ) -> dict[str, Any] | None:
        return _row_dict(
            conn.execute(
                """
                SELECT calibrated_at, measured_level_ml,
                       baseline_at, baseline_level_ml,
                       cycles, nominal_ml, measured_ml, result_value
                FROM pump_calibration_events
                WHERE pump_name = ?
                ORDER BY calibrated_at DESC, id DESC
                LIMIT 1
                """,
                (pump_name,),
            ).fetchone()
        )

    def calibration_baseline(
        self,
        conn: sqlite3.Connection,
        pump_name: str,
    ) -> dict[str, Any] | None:
        tank_name = "main" if pump_name == "main" else "refill"
        calibration = _row_dict(
            conn.execute(
                """
                SELECT calibrated_at AS baseline_at,
                       measured_level_ml AS baseline_level_ml
                FROM pump_calibration_events
                WHERE pump_name = ?
                ORDER BY calibrated_at DESC, id DESC
                LIMIT 1
                """,
                (pump_name,),
            ).fetchone()
        )
        tank_fill = _row_dict(
            conn.execute(
                """
                SELECT ran_at AS baseline_at,
                       new_ml AS baseline_level_ml
                FROM tank_fill_events
                WHERE tank_name = ?
                ORDER BY ran_at DESC, id DESC
                LIMIT 1
                """,
                (tank_name,),
            ).fetchone()
        )
        candidates = [
            item for item in (calibration, tank_fill) if item is not None
        ]
        return (
            max(candidates, key=lambda item: str(item["baseline_at"]))
            if candidates
            else None
        )

    def watering_aggregate(
        self,
        conn: sqlite3.Connection,
        start_exclusive: str,
        end_inclusive: str,
    ) -> dict[str, int]:
        row = conn.execute(
            """
            SELECT COUNT(*) AS cycles,
                   COALESCE(SUM(delivered_ml), 0) AS nominal_ml
            FROM watering_events
            WHERE ran_at > ? AND ran_at <= ?
            """,
            (start_exclusive, end_inclusive),
        ).fetchone()
        return {
            "cycles": int(row["cycles"]),
            "nominal_ml": int(row["nominal_ml"]),
        }

    def transferred_between(
        self,
        conn: sqlite3.Connection,
        start_exclusive: str,
        end_inclusive: str,
    ) -> int:
        row = conn.execute(
            """
            SELECT COALESCE(SUM(transferred_ml), 0) AS amount_ml
            FROM refill_events
            WHERE ran_at > ? AND ran_at <= ?
            """,
            (start_exclusive, end_inclusive),
        ).fetchone()
        return int(row["amount_ml"])

    def refill_aggregate(
        self,
        conn: sqlite3.Connection,
        start_exclusive: str,
        end_inclusive: str,
    ) -> dict[str, int]:
        row = conn.execute(
            """
            SELECT COUNT(*) AS cycles,
                   COALESCE(SUM(transferred_ml), 0) AS nominal_ml,
                   COALESCE(SUM(duration_seconds), 0) AS duration_seconds
            FROM refill_events
            WHERE ran_at > ? AND ran_at <= ?
            """,
            (start_exclusive, end_inclusive),
        ).fetchone()
        return {
            "cycles": int(row["cycles"]),
            "nominal_ml": int(row["nominal_ml"]),
            "duration_seconds": int(row["duration_seconds"]),
        }

    def latest_refill(self) -> dict[str, Any] | None:
        with self.database.connection() as conn:
            return _row_dict(
                conn.execute(
                    "SELECT * FROM refill_events ORDER BY ran_at DESC, id DESC LIMIT 1"
                ).fetchone()
            )

    def refill_windows(self) -> list[dict[str, Any]]:
        with self.database.connection() as conn:
            return [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT target_date, window_label, ran_at, transferred_ml
                    FROM refill_events
                    ORDER BY ran_at
                    """
                ).fetchall()
            ]

    def latest_watering_at(self) -> str:
        with self.database.connection() as conn:
            row = conn.execute(
                "SELECT ran_at FROM watering_events ORDER BY ran_at DESC, id DESC LIMIT 1"
            ).fetchone()
        return str(row["ran_at"]) if row else ""

    def watering_count_between(self, start_utc: str, end_utc: str) -> int:
        with self.database.connection() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) AS count
                FROM watering_events
                WHERE ran_at >= ? AND ran_at < ?
                """,
                (start_utc, end_utc),
            ).fetchone()
        return int(row["count"] if row else 0)

    def delivered_between(self, start_utc: str, end_utc: str) -> int:
        with self.database.connection() as conn:
            row = conn.execute(
                """
                SELECT COALESCE(SUM(delivered_ml), 0) AS delivered_ml
                FROM watering_events
                WHERE ran_at >= ? AND ran_at < ?
                """,
                (start_utc, end_utc),
            ).fetchone()
        return int(row["delivered_ml"] if row else 0)

    def refill_for_target(
        self,
        target_date: date | str,
        window_label: str = "",
    ) -> dict[str, Any] | None:
        target = target_date.isoformat() if isinstance(target_date, date) else target_date
        query = "SELECT * FROM refill_events WHERE target_date = ?"
        params: list[Any] = [target]
        if window_label:
            query += " AND window_label = ?"
            params.append(window_label)
        query += " ORDER BY ran_at DESC, id DESC LIMIT 1"
        with self.database.connection() as conn:
            return _row_dict(conn.execute(query, params).fetchone())

    def history(self, limit: int = 12) -> list[dict[str, Any]]:
        bounded = max(1, min(int(limit), 200))
        with self.database.connection() as conn:
            rows = conn.execute(
                """
                SELECT id, ran_at, 'watering' AS event_type,
                       delivered_ml AS amount_ml, source, run_id,
                       temperature_c, rain_mm, actual_consumed_ml,
                       '' AS tank_name, '' AS window_label
                FROM watering_events
                UNION ALL
                SELECT id, ran_at, 'refill' AS event_type,
                       transferred_ml AS amount_ml, source, run_id,
                       NULL, NULL, NULL,
                       '' AS tank_name, window_label
                FROM refill_events
                UNION ALL
                SELECT id, ran_at, 'tank_fill' AS event_type,
                       new_ml - previous_ml AS amount_ml, source, NULL,
                       NULL, NULL, NULL,
                       tank_name, ''
                FROM tank_fill_events
                ORDER BY ran_at DESC, id DESC
                LIMIT ?
                """,
                (bounded,),
            ).fetchall()
        return [dict(row) for row in rows]

    def watering_events(self) -> list[dict[str, Any]]:
        with self.database.connection() as conn:
            return [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT id, ran_at, run_id, delivered_ml,
                           actual_consumed_ml, temperature_c, rain_mm, source
                    FROM watering_events
                    """
                ).fetchall()
            ]

    def refill_events(self) -> list[dict[str, Any]]:
        with self.database.connection() as conn:
            return [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT id, ran_at, run_id, target_date, requested_ml,
                           transferred_ml, duration_seconds, window_label, source
                    FROM refill_events
                    """
                ).fetchall()
            ]

    def tank_fill_events(self) -> list[dict[str, Any]]:
        with self.database.connection() as conn:
            return [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT id, ran_at, tank_name, previous_ml, new_ml,
                           capacity_ml, source
                    FROM tank_fill_events
                    """
                ).fetchall()
            ]

    def record_refill_window_observation(
        self,
        *,
        target_date: str,
        window_label: str,
        window_start: str,
        window_end: str,
        need_detected: bool,
        eligible: bool,
        blocking_reason: str,
        observed_at: str,
    ) -> None:
        with self.database.connection() as conn:
            conn.execute(
                """
                INSERT INTO refill_window_observations(
                    target_date, window_label, window_start, window_end,
                    need_detected, eligible, blocking_reason, observed_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(target_date, window_label) DO UPDATE SET
                    window_start = excluded.window_start,
                    window_end = excluded.window_end,
                    need_detected = MAX(
                        refill_window_observations.need_detected,
                        excluded.need_detected
                    ),
                    eligible = MAX(
                        refill_window_observations.eligible,
                        excluded.eligible
                    ),
                    blocking_reason = excluded.blocking_reason,
                    observed_at = excluded.observed_at
                """,
                (
                    target_date,
                    window_label,
                    window_start,
                    window_end,
                    int(need_detected),
                    int(eligible),
                    blocking_reason,
                    observed_at,
                ),
            )

    def refill_window_observations(
        self,
        *,
        target_date: str | None = None,
    ) -> list[dict[str, Any]]:
        query = "SELECT * FROM refill_window_observations"
        params: tuple[Any, ...] = ()
        if target_date:
            query += " WHERE target_date = ?"
            params = (target_date,)
        query += " ORDER BY window_start DESC"
        with self.database.connection() as conn:
            return [dict(row) for row in conn.execute(query, params).fetchall()]
