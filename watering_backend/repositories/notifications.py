from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Callable, ContextManager, Iterator, Protocol


ConnectionFactory = Callable[[], ContextManager[sqlite3.Connection]]


class DatabaseLike(Protocol):
    def connection(self) -> ContextManager[sqlite3.Connection]: ...


def _connection_factory(
    source: ConnectionFactory | DatabaseLike,
) -> ConnectionFactory:
    if callable(source):
        return source
    connection = getattr(source, "connection", None)
    if callable(connection):
        return connection
    raise TypeError("NotificationsRepository requires a connection factory or Database")


@dataclass(frozen=True)
class NotificationTransaction:
    """Notification persistence operations bound to one SQLite transaction."""

    connection: sqlite3.Connection

    def state(self, alert_key: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT * FROM notification_state WHERE alert_key = ?",
            (alert_key,),
        ).fetchone()
        return dict(row) if row is not None else None

    def append_log(
        self,
        *,
        alert_key: str,
        severity: str,
        event_state: str,
        subject: str,
        message: str,
        created_at: str,
        sent_at: str | None,
        status: str,
        error: str,
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO notification_log
                (
                    alert_key, severity, event_state, subject, message,
                    created_at, sent_at, status, error
                )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                alert_key,
                severity,
                event_state,
                subject,
                message,
                created_at,
                sent_at,
                status,
                error,
            ),
        )

    def upsert_state(
        self,
        *,
        alert_key: str,
        severity: str,
        active: bool,
        changed_at: str,
        notified_at: str | None,
        attempted_at: str | None,
        error: str,
        consecutive_failures: int,
        message: str,
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO notification_state
                (
                    alert_key, severity, active, last_changed_at,
                    last_notified_at, last_attempt_at, last_error,
                    consecutive_failures, message
                )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(alert_key) DO UPDATE SET
                severity = excluded.severity,
                active = excluded.active,
                last_changed_at = CASE
                    WHEN notification_state.active <> excluded.active
                        THEN excluded.last_changed_at
                    ELSE notification_state.last_changed_at
                END,
                last_notified_at = COALESCE(
                    excluded.last_notified_at,
                    notification_state.last_notified_at
                ),
                last_attempt_at = COALESCE(
                    excluded.last_attempt_at,
                    notification_state.last_attempt_at
                ),
                last_error = excluded.last_error,
                consecutive_failures = excluded.consecutive_failures,
                message = excluded.message
            """,
            (
                alert_key,
                severity,
                int(active),
                changed_at,
                notified_at,
                attempted_at,
                error,
                consecutive_failures,
                message,
            ),
        )


class NotificationsRepository:
    """SQLite persistence for notification state and delivery history."""

    def __init__(self, source: ConnectionFactory | DatabaseLike):
        self._connect = _connection_factory(source)

    @contextmanager
    def transaction(self) -> Iterator[NotificationTransaction]:
        with self._connect() as connection:
            yield NotificationTransaction(connection)

    def append_log(
        self,
        *,
        alert_key: str,
        severity: str,
        event_state: str,
        subject: str,
        message: str,
        created_at: str,
        sent_at: str | None,
        status: str,
        error: str,
    ) -> None:
        with self.transaction() as transaction:
            transaction.append_log(
                alert_key=alert_key,
                severity=severity,
                event_state=event_state,
                subject=subject,
                message=message,
                created_at=created_at,
                sent_at=sent_at,
                status=status,
                error=error,
            )

    def diagnostics(
        self,
        limit: int = 50,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        bounded_limit = max(1, min(int(limit), 200))
        with self._connect() as connection:
            active = [
                dict(row)
                for row in connection.execute(
                    """
                    SELECT *
                    FROM notification_state
                    WHERE active = 1
                    ORDER BY severity DESC, last_changed_at DESC
                    """
                )
            ]
            log = [
                dict(row)
                for row in connection.execute(
                    """
                    SELECT *
                    FROM notification_log
                    ORDER BY created_at DESC, id DESC
                    LIMIT ?
                    """,
                    (bounded_limit,),
                )
            ]
        return active, log

    def keys_with_prefix(self, prefix: str) -> set[str]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT alert_key
                FROM notification_state
                WHERE alert_key LIKE ?
                """,
                (f"{prefix}%",),
            ).fetchall()
        return {str(row["alert_key"]) for row in rows}
