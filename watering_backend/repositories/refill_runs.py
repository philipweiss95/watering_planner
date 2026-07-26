from __future__ import annotations

import sqlite3
from typing import Any

from watering_backend.database import Database


def _row(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


class RefillRunsRepository:
    """SQLite boundary for the two-phase refill-run lifecycle."""

    OPEN_STATUSES = ("reserved", "running")

    def __init__(self, database: Database):
        self.database = database

    def get(
        self,
        run_id: str,
        *,
        conn: sqlite3.Connection | None = None,
    ) -> dict[str, Any] | None:
        if conn is None:
            with self.database.connection() as active:
                return self.get(run_id, conn=active)
        return _row(
            conn.execute(
                "SELECT * FROM refill_runs WHERE run_id = ?",
                (run_id,),
            ).fetchone()
        )

    def active(
        self,
        *,
        conn: sqlite3.Connection | None = None,
    ) -> dict[str, Any] | None:
        if conn is None:
            with self.database.connection() as active:
                return self.active(conn=active)
        return _row(
            conn.execute(
                """
                SELECT *
                FROM refill_runs
                WHERE active_slot = 1
                ORDER BY created_at
                LIMIT 1
                """
            ).fetchone()
        )

    def insert_reserved(
        self,
        conn: sqlite3.Connection,
        values: dict[str, Any],
    ) -> None:
        conn.execute(
            """
            INSERT INTO refill_runs(
                run_id, run_type, status, created_at, authorized_at,
                target_date, window_key, window_label, window_start,
                window_end, requested_ml, planned_transfer_ml,
                planned_duration_seconds, main_tank_start_ml,
                refill_tank_start_ml, pump_ml_per_min,
                expected_complete_at, expires_at, limit_reason, source,
                active_slot, updated_at
            )
            VALUES (
                ?, ?, 'reserved', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, 1, ?
            )
            """,
            (
                values["run_id"],
                values["run_type"],
                values["created_at"],
                values["authorized_at"],
                values["target_date"],
                values.get("window_key", ""),
                values.get("window_label", ""),
                values.get("window_start", ""),
                values.get("window_end", ""),
                values["requested_ml"],
                values["planned_transfer_ml"],
                values["planned_duration_seconds"],
                values["main_tank_start_ml"],
                values["refill_tank_start_ml"],
                values["pump_ml_per_min"],
                values["expected_complete_at"],
                values["expires_at"],
                values.get("limit_reason", ""),
                values["source"],
                values["updated_at"],
            ),
        )

    def mark_running(
        self,
        conn: sqlite3.Connection,
        run_id: str,
        *,
        started_at: str,
    ) -> bool:
        cursor = conn.execute(
            """
            UPDATE refill_runs
            SET status = 'running',
                started_at = COALESCE(started_at, ?),
                updated_at = ?
            WHERE run_id = ? AND status = 'reserved'
            """,
            (started_at, started_at, run_id),
        )
        return cursor.rowcount == 1

    def newer_than(
        self,
        conn: sqlite3.Connection,
        *,
        run_id: str,
    ) -> dict[str, Any] | None:
        return _row(
            conn.execute(
                """
                SELECT *
                FROM refill_runs
                WHERE run_id <> ?
                  AND rowid > (
                      SELECT rowid FROM refill_runs WHERE run_id = ?
                  )
                ORDER BY rowid DESC
                LIMIT 1
                """,
                (run_id, run_id),
            ).fetchone()
        )

    def mark_completed(
        self,
        conn: sqlite3.Connection,
        run_id: str,
        *,
        completed_at: str,
        accounted_transfer_ml: int,
        physical_transfer_ml: int,
        main_accounted_ml: int,
        tank_values: dict[str, int],
        consistency_delta_ml: int,
        consistency_note: str,
        needs_manual_review: bool,
        completion_reason: str,
    ) -> None:
        conn.execute(
            """
            UPDATE refill_runs
            SET status = 'completed',
                completed_at = ?,
                accounted_transfer_ml = ?,
                physical_transfer_ml = ?,
                main_accounted_ml = ?,
                main_before_complete_ml = ?,
                main_after_complete_ml = ?,
                refill_before_complete_ml = ?,
                refill_after_complete_ml = ?,
                consistency_delta_ml = ?,
                consistency_note = ?,
                needs_manual_review = ?,
                completion_reason = ?,
                active_slot = NULL,
                updated_at = ?
            WHERE run_id = ?
              AND status IN ('reserved', 'running', 'expired')
            """,
            (
                completed_at,
                accounted_transfer_ml,
                physical_transfer_ml,
                main_accounted_ml,
                tank_values["main_before_ml"],
                tank_values["main_after_ml"],
                tank_values["refill_before_ml"],
                tank_values["refill_after_ml"],
                consistency_delta_ml,
                consistency_note,
                int(needs_manual_review),
                completion_reason,
                completed_at,
                run_id,
            ),
        )

    def mark_failed(
        self,
        conn: sqlite3.Connection,
        run_id: str,
        *,
        failed_at: str,
        error_text: str,
        needs_manual_review: bool,
        completion_reason: str,
    ) -> None:
        conn.execute(
            """
            UPDATE refill_runs
            SET status = 'failed',
                completed_at = ?,
                error_text = ?,
                needs_manual_review = ?,
                completion_reason = ?,
                active_slot = NULL,
                updated_at = ?
            WHERE run_id = ? AND status IN ('reserved', 'running')
            """,
            (
                failed_at,
                error_text,
                int(needs_manual_review),
                completion_reason,
                failed_at,
                run_id,
            ),
        )

    def mark_reconciled(
        self,
        conn: sqlite3.Connection,
        run_id: str,
        *,
        status: str,
        reconciled_at: str,
        reconciliation_mode: str,
        reconciliation_note: str,
        completion_reason: str,
        accounted_transfer_ml: int | None = None,
        physical_transfer_ml: int | None = None,
        main_accounted_ml: int | None = None,
        tank_values: dict[str, int] | None = None,
        consistency_delta_ml: int = 0,
        consistency_note: str = "",
    ) -> bool:
        if status not in {"completed", "cancelled"}:
            raise ValueError("Ungültiger Abgleichstatus")
        values = tank_values or {}
        cursor = conn.execute(
            """
            UPDATE refill_runs
            SET status = ?,
                completed_at = COALESCE(completed_at, ?),
                accounted_transfer_ml = COALESCE(
                    ?, accounted_transfer_ml
                ),
                physical_transfer_ml = COALESCE(
                    ?, physical_transfer_ml
                ),
                main_accounted_ml = COALESCE(
                    ?, main_accounted_ml
                ),
                main_before_complete_ml = COALESCE(
                    ?, main_before_complete_ml
                ),
                main_after_complete_ml = COALESCE(
                    ?, main_after_complete_ml
                ),
                refill_before_complete_ml = COALESCE(
                    ?, refill_before_complete_ml
                ),
                refill_after_complete_ml = COALESCE(
                    ?, refill_after_complete_ml
                ),
                consistency_delta_ml = ?,
                consistency_note = ?,
                needs_manual_review = 0,
                error_text = '',
                completion_reason = ?,
                reconciled_at = ?,
                reconciliation_mode = ?,
                reconciliation_note = ?,
                active_slot = NULL,
                updated_at = ?
            WHERE run_id = ?
              AND needs_manual_review = 1
            """,
            (
                status,
                reconciled_at,
                accounted_transfer_ml,
                physical_transfer_ml,
                main_accounted_ml,
                values.get("main_before_ml"),
                values.get("main_after_ml"),
                values.get("refill_before_ml"),
                values.get("refill_after_ml"),
                int(consistency_delta_ml),
                consistency_note,
                completion_reason,
                reconciled_at,
                reconciliation_mode,
                reconciliation_note,
                reconciled_at,
                run_id,
            ),
        )
        return cursor.rowcount == 1

    def expire_stale(
        self,
        conn: sqlite3.Connection,
        *,
        now_at: str,
    ) -> int:
        cursor = conn.execute(
            """
            UPDATE refill_runs
            SET status = 'expired',
                active_slot = NULL,
                needs_manual_review = 1,
                error_text = CASE
                    WHEN error_text = ''
                        THEN 'Abschlussmeldung ist ausgeblieben.'
                    ELSE error_text
                END,
                completion_reason = 'completion_timeout',
                updated_at = ?
            WHERE active_slot = 1
              AND status IN ('reserved', 'running')
              AND julianday(expires_at) < julianday(?)
            """,
            (now_at, now_at),
        )
        return int(cursor.rowcount)

    def recent_uncertain(
        self,
        *,
        limit: int = 10,
        conn: sqlite3.Connection | None = None,
    ) -> list[dict[str, Any]]:
        if conn is None:
            with self.database.connection() as active:
                return self.recent_uncertain(limit=limit, conn=active)
        return [
            dict(row)
            for row in conn.execute(
                """
                SELECT *
                FROM refill_runs
                WHERE needs_manual_review = 1
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (max(1, min(int(limit), 100)),),
            )
        ]

    def latest_with_statuses(
        self,
        statuses: tuple[str, ...],
        *,
        conn: sqlite3.Connection | None = None,
    ) -> dict[str, Any] | None:
        if conn is None:
            with self.database.connection() as active:
                return self.latest_with_statuses(statuses, conn=active)
        placeholders = ", ".join("?" for _ in statuses)
        return _row(
            conn.execute(
                f"""
                SELECT *
                FROM refill_runs
                WHERE status IN ({placeholders})
                ORDER BY updated_at DESC
                LIMIT 1
                """,
                statuses,
            ).fetchone()
        )
