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

    def reconcile_refill_plans_after_event(
        self,
        conn: sqlite3.Connection,
        *,
        target_date: str,
        window_label: str,
        window_key: str = "",
        ran_at: str,
        cooldown_until: str,
    ) -> None:
        """Apply a committed refill to its plans in the same transaction.

        A process may stop immediately after the event is written. Keeping the
        matching fulfilment and fully covered cooldown windows in the event
        transaction prevents an executable pre-window snapshot from becoming a
        false missed run after restart.
        """
        if window_key:
            conn.execute(
                """
                UPDATE refill_window_plans
                SET fulfilled_at = COALESCE(fulfilled_at, ?),
                    last_checked_at = CASE
                        WHEN julianday(?) >= julianday(last_checked_at)
                            THEN ?
                        ELSE last_checked_at
                    END
                WHERE window_key = ?
                  AND cancelled_at IS NULL
                """,
                (ran_at, ran_at, ran_at, window_key),
            )
        elif window_label:
            conn.execute(
                """
                UPDATE refill_window_plans
                SET fulfilled_at = COALESCE(fulfilled_at, ?),
                    last_checked_at = CASE
                        WHEN julianday(?) >= julianday(last_checked_at)
                            THEN ?
                        ELSE last_checked_at
                    END
                WHERE target_date = ?
                  AND window_label = ?
                  AND cancelled_at IS NULL
                  AND julianday(window_start) <= julianday(?)
                  AND julianday(window_end) > julianday(?)
                """,
                (
                    ran_at,
                    ran_at,
                    ran_at,
                    target_date,
                    window_label,
                    ran_at,
                    ran_at,
                ),
            )

        conn.execute(
            """
            UPDATE refill_window_plans
            SET executable = 0,
                expected_transfer_ml = 0,
                blocking_reason = 'cooldown',
                last_checked_at = CASE
                    WHEN julianday(?) >= julianday(last_checked_at)
                        THEN ?
                    ELSE last_checked_at
                END
            WHERE cancelled_at IS NULL
              AND fulfilled_at IS NULL
              AND executable = 1
              AND julianday(window_start) >= julianday(?)
              AND julianday(window_end) <= julianday(?)
            """,
            (
                ran_at,
                ran_at,
                ran_at,
                cooldown_until,
            ),
        )

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

    def latest_refill(
        self,
        *,
        conn: sqlite3.Connection | None = None,
    ) -> dict[str, Any] | None:
        if conn is None:
            with self.database.connection() as active:
                return self.latest_refill(conn=active)
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
        *,
        conn: sqlite3.Connection | None = None,
    ) -> dict[str, Any] | None:
        target = target_date.isoformat() if isinstance(target_date, date) else target_date
        query = "SELECT * FROM refill_events WHERE target_date = ?"
        params: list[Any] = [target]
        if window_label:
            query += " AND window_label = ?"
            params.append(window_label)
        query += " ORDER BY ran_at DESC, id DESC LIMIT 1"
        if conn is None:
            with self.database.connection() as active:
                return self.refill_for_target(
                    target_date,
                    window_label,
                    conn=active,
                )
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

    def upsert_refill_window_plan(
        self,
        *,
        window_key: str,
        target_date: str,
        window_label: str,
        window_start: str,
        window_end: str,
        need_detected: bool,
        executable: bool,
        expected_transfer_ml: int,
        blocking_reason: str,
        observed_in_window: bool,
        checked_at: str,
        cooldown_minutes: int = 0,
    ) -> None:
        """Persist the newest snapshot of one absolute refill opportunity.

        ``last_checked_at`` is generated in canonical UTC by the service.
        The conflict guard is part of the SQL statement so a slower status
        calculation cannot overwrite a newer snapshot after waiting for the
        database transaction. The event lookup inside the write transaction
        also reconstructs a full-window cooldown after a concurrent event,
        even when the status calculation started before that event committed.
        """
        with self.database.connection(immediate=True) as conn:
            normalized_executable = bool(executable)
            normalized_expected_ml = max(0, int(expected_transfer_ml))
            normalized_blocking_reason = blocking_reason
            if normalized_executable and int(cooldown_minutes) > 0:
                covering_event = conn.execute(
                    """
                    SELECT 1
                    FROM refill_events
                    WHERE transferred_ml > 0
                      AND julianday(ran_at) <= julianday(?)
                      AND julianday(ran_at)
                            + (? / 1440.0) >= julianday(?)
                    LIMIT 1
                    """,
                    (
                        window_start,
                        int(cooldown_minutes),
                        window_end,
                    ),
                ).fetchone()
                if covering_event:
                    normalized_executable = False
                    normalized_expected_ml = 0
                    normalized_blocking_reason = "cooldown"
            conn.execute(
                """
                INSERT INTO refill_window_plans(
                    window_key, target_date, window_label,
                    window_start, window_end, need_detected, executable,
                    expected_transfer_ml, blocking_reason,
                    observed_in_window, created_at, last_checked_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(window_key) DO UPDATE SET
                    window_label = excluded.window_label,
                    need_detected = excluded.need_detected,
                    executable = excluded.executable,
                    expected_transfer_ml = excluded.expected_transfer_ml,
                    blocking_reason = excluded.blocking_reason,
                    observed_in_window = MAX(
                        refill_window_plans.observed_in_window,
                        excluded.observed_in_window
                    ),
                    cancelled_at = NULL,
                    last_checked_at = excluded.last_checked_at
                WHERE julianday(excluded.last_checked_at) >=
                      julianday(refill_window_plans.last_checked_at)
                """,
                (
                    window_key,
                    target_date,
                    window_label,
                    window_start,
                    window_end,
                    int(need_detected),
                    int(normalized_executable),
                    normalized_expected_ml,
                    normalized_blocking_reason,
                    int(observed_in_window),
                    checked_at,
                    checked_at,
                ),
            )

    def cancel_obsolete_refill_window_plans(
        self,
        *,
        target_date: str,
        active_window_keys: set[str],
        after_at: str,
        cancelled_at: str,
    ) -> None:
        """Cancel only future plans; elapsed observations remain immutable."""
        query = """
            UPDATE refill_window_plans
            SET cancelled_at = ?, last_checked_at = ?
            WHERE target_date = ?
              AND cancelled_at IS NULL
              AND fulfilled_at IS NULL
              AND window_start > ?
              AND julianday(last_checked_at) <= julianday(?)
        """
        params: list[Any] = [
            cancelled_at,
            cancelled_at,
            target_date,
            after_at,
            cancelled_at,
        ]
        if active_window_keys:
            placeholders = ",".join("?" for _ in active_window_keys)
            query += f" AND window_key NOT IN ({placeholders})"
            params.extend(sorted(active_window_keys))
        with self.database.connection(immediate=True) as conn:
            conn.execute(query, params)

    def mark_refill_window_fulfilled(
        self,
        window_key: str,
        fulfilled_at: str,
        checked_at: str,
    ) -> None:
        with self.database.connection(immediate=True) as conn:
            conn.execute(
                """
                UPDATE refill_window_plans
                SET fulfilled_at = COALESCE(fulfilled_at, ?),
                    last_checked_at = CASE
                        WHEN julianday(?) >= julianday(last_checked_at)
                            THEN ?
                        ELSE last_checked_at
                    END
                WHERE window_key = ?
                """,
                (
                    fulfilled_at,
                    checked_at,
                    checked_at,
                    window_key,
                ),
            )

    def refill_window_plans(
        self,
        *,
        target_dates: list[str] | tuple[str, ...] | None = None,
    ) -> list[dict[str, Any]]:
        query = "SELECT * FROM refill_window_plans"
        params: list[Any] = []
        if target_dates:
            placeholders = ",".join("?" for _ in target_dates)
            query += f" WHERE target_date IN ({placeholders})"
            params.extend(target_dates)
        query += " ORDER BY window_start, window_key"
        with self.database.connection() as conn:
            return [
                dict(row)
                for row in conn.execute(query, params).fetchall()
            ]
