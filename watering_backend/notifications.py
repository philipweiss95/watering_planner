from __future__ import annotations

import os
import smtplib
import ssl
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from typing import Callable, Iterable


SEVERITIES = {"info", "warning", "critical"}


def _truthy(value: object) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on", "ja"}


@dataclass(frozen=True)
class SMTPConfig:
    enabled: bool
    host: str
    port: int
    username: str
    password: str
    sender: str
    recipients: tuple[str, ...]
    security: str

    @classmethod
    def from_env(cls) -> "SMTPConfig":
        security = os.environ.get("SMTP_SECURITY", "starttls").strip().lower()
        if security not in {"ssl", "starttls", "none"}:
            raise ValueError("SMTP_SECURITY muss ssl, starttls oder none sein")
        default_port = 465 if security == "ssl" else 587 if security == "starttls" else 25
        try:
            port = int(os.environ.get("SMTP_PORT", str(default_port)))
        except ValueError as exc:
            raise ValueError("SMTP_PORT muss eine ganze Zahl sein") from exc
        if not 1 <= port <= 65535:
            raise ValueError("SMTP_PORT muss zwischen 1 und 65535 liegen")
        recipients = tuple(
            item.strip()
            for item in os.environ.get("SMTP_TO", "").replace(";", ",").split(",")
            if item.strip()
        )
        return cls(
            enabled=_truthy(os.environ.get("NOTIFICATIONS_ENABLED")),
            host=os.environ.get("SMTP_HOST", "").strip(),
            port=port,
            username=os.environ.get("SMTP_USERNAME", "").strip(),
            password=os.environ.get("SMTP_PASSWORD", ""),
            sender=os.environ.get("SMTP_FROM", "").strip(),
            recipients=recipients,
            security=security,
        )

    def validate_for_send(self) -> None:
        if not self.enabled:
            raise ValueError("Benachrichtigungen sind deaktiviert")
        if not self.host or not self.sender or not self.recipients:
            raise ValueError("SMTP_HOST, SMTP_FROM und SMTP_TO muessen konfiguriert sein")

    def public_status(self) -> dict:
        return {
            "enabled": self.enabled,
            "configured": bool(self.host and self.sender and self.recipients),
            "host": self.host,
            "port": self.port,
            "from": self.sender,
            "to": list(self.recipients),
            "security": self.security,
            "username_configured": bool(self.username),
        }


def send_email(config: SMTPConfig, subject: str, body: str) -> None:
    config.validate_for_send()
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = config.sender
    message["To"] = ", ".join(config.recipients)
    message.set_content(body)

    client_class = smtplib.SMTP_SSL if config.security == "ssl" else smtplib.SMTP
    kwargs = {"context": ssl.create_default_context()} if config.security == "ssl" else {}
    with client_class(config.host, config.port, timeout=10, **kwargs) as client:
        if config.security == "starttls":
            client.starttls(context=ssl.create_default_context())
        if config.username:
            client.login(config.username, config.password)
        client.send_message(message)


class NotificationService:
    def __init__(self, connect: Callable, now: Callable[[], datetime] | None = None):
        self._connect = connect
        self._now = now or (lambda: datetime.now(timezone.utc))

    def send_test(self) -> dict:
        config = SMTPConfig.from_env()
        created_at = self._now().astimezone(timezone.utc).isoformat()
        try:
            send_email(config, "Watering Planner: Test", "Die SMTP-Konfiguration funktioniert.")
        except Exception as exc:
            self._log("smtp_test", "info", "test", "Watering Planner: Test", str(exc), created_at, "failed", str(exc))
            raise ValueError(f"Test-E-Mail konnte nicht gesendet werden: {exc}") from exc
        self._log(
            "smtp_test",
            "info",
            "test",
            "Watering Planner: Test",
            "Die SMTP-Konfiguration funktioniert.",
            created_at,
            "sent",
            "",
        )
        return {"sent": True, "sent_at": created_at}

    def update_condition(
        self,
        alert_key: str,
        active: bool,
        severity: str,
        subject: str,
        message: str,
        *,
        cooldown_minutes: int,
        send_resolved: bool,
        retry_minutes: int = 5,
    ) -> dict:
        if severity not in SEVERITIES:
            raise ValueError("Unbekannter Schweregrad")
        now = self._now().astimezone(timezone.utc)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM notification_state WHERE alert_key = ?",
                (alert_key,),
            ).fetchone()
            was_active = bool(row["active"]) if row else False
            last_notified = (
                datetime.fromisoformat(str(row["last_notified_at"]))
                if row and row["last_notified_at"]
                else None
            )
            last_attempt = (
                datetime.fromisoformat(str(row["last_attempt_at"]))
                if row and row["last_attempt_at"]
                else None
            )
            consecutive_failures = int(row["consecutive_failures"] or 0) if row else 0
            previous_error = str(row["last_error"] or "") if row else ""
            retry_due = (
                consecutive_failures > 0
                and (
                    last_attempt is None
                    or now - last_attempt.astimezone(timezone.utc)
                    >= timedelta(minutes=max(0, retry_minutes))
                )
            )
            should_send = active and (
                not was_active
                or last_notified is None
                or now - last_notified.astimezone(timezone.utc) >= timedelta(minutes=max(0, cooldown_minutes))
            ) and (consecutive_failures == 0 or retry_due)
            event_state = "active"
            send_subject = subject
            send_message_text = message
            if not active and was_active and send_resolved:
                should_send = consecutive_failures == 0 or retry_due
                event_state = "resolved"
                send_subject = f"Entwarnung: {subject}"
                send_message_text = f"Der zuvor gemeldete Zustand ist behoben.\n\n{message}"
            sent_at = None
            attempted_at = None
            status = "deduplicated" if active else "inactive"
            error = previous_error if consecutive_failures else ""
            if should_send:
                attempted_at = now.isoformat()
                try:
                    send_email(SMTPConfig.from_env(), send_subject, send_message_text)
                    sent_at = now.isoformat()
                    status = "sent"
                    consecutive_failures = 0
                except Exception as exc:
                    status = "failed"
                    error = str(exc)
                    consecutive_failures += 1
                self._log(
                    alert_key,
                    severity,
                    event_state,
                    send_subject,
                    send_message_text,
                    now.isoformat(),
                    status,
                    error,
                    conn=conn,
                    sent_at=sent_at,
                )
            persisted_active = bool(
                active
                or (
                    event_state == "resolved"
                    and (status == "failed" or consecutive_failures > 0)
                )
            )
            conn.execute(
                """
                INSERT INTO notification_state
                    (
                        alert_key, severity, active, last_changed_at, last_notified_at,
                        last_attempt_at, last_error, consecutive_failures, message
                    )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(alert_key) DO UPDATE SET
                    severity = excluded.severity,
                    active = excluded.active,
                    last_changed_at = CASE
                        WHEN notification_state.active <> excluded.active THEN excluded.last_changed_at
                        ELSE notification_state.last_changed_at
                    END,
                    last_notified_at = COALESCE(excluded.last_notified_at, notification_state.last_notified_at),
                    last_attempt_at = COALESCE(excluded.last_attempt_at, notification_state.last_attempt_at),
                    last_error = excluded.last_error,
                    consecutive_failures = excluded.consecutive_failures,
                    message = excluded.message
                """,
                (
                    alert_key,
                    severity,
                    int(persisted_active),
                    now.isoformat(),
                    sent_at,
                    attempted_at,
                    error,
                    consecutive_failures,
                    message,
                ),
            )
        return {"alert_key": alert_key, "active": active, "status": status, "error": error}

    def diagnostics(self, limit: int = 50) -> dict:
        with self._connect() as conn:
            active = [dict(row) for row in conn.execute(
                "SELECT * FROM notification_state WHERE active = 1 ORDER BY severity DESC, last_changed_at DESC"
            )]
            log = [dict(row) for row in conn.execute(
                "SELECT * FROM notification_log ORDER BY created_at DESC, id DESC LIMIT ?",
                (max(1, min(int(limit), 200)),),
            )]
        try:
            smtp_status = SMTPConfig.from_env().public_status()
        except ValueError as exc:
            smtp_status = {"enabled": False, "configured": False, "configuration_error": str(exc)}
        return {
            "smtp": smtp_status,
            "active_alerts": active,
            "notification_log": log,
        }

    def _log(
        self,
        alert_key: str,
        severity: str,
        event_state: str,
        subject: str,
        message: str,
        created_at: str,
        status: str,
        error: str,
        *,
        conn=None,
        sent_at: str | None = None,
    ) -> None:
        def insert(active_conn) -> None:
            active_conn.execute(
                """
                INSERT INTO notification_log
                    (alert_key, severity, event_state, subject, message, created_at, sent_at, status, error)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (alert_key, severity, event_state, subject, message, created_at, sent_at, status, error),
            )

        if conn is not None:
            insert(conn)
        else:
            with self._connect() as active_conn:
                insert(active_conn)


class NotificationWorker:
    def __init__(
        self,
        checker: Callable[[], Iterable[dict]],
        service: NotificationService,
        interval_seconds: int,
    ):
        self._checker = checker
        self._service = service
        self._interval_seconds = max(10, int(interval_seconds))
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def start(self) -> None:
        if self.running:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="watering-notifications", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=timeout)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                for condition in self._checker():
                    self._service.update_condition(**condition)
            except Exception:
                # A monitoring worker must survive one broken poll. Details from
                # individual SMTP failures are persisted by NotificationService.
                pass
            self._stop.wait(self._interval_seconds)
