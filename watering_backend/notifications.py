from __future__ import annotations

import os
import smtplib
import ssl
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from typing import Callable, Iterable

from watering_backend.config import (
    MAX_NOTIFICATION_WORKER_INTERVAL_SECONDS,
    MIN_NOTIFICATION_WORKER_INTERVAL_SECONDS,
)
from watering_backend.repositories.notifications import NotificationsRepository


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
    def __init__(
        self,
        connect: Callable | None = None,
        now: Callable[[], datetime] | None = None,
        *,
        repository: NotificationsRepository | None = None,
    ):
        if repository is None:
            if connect is None:
                raise TypeError("NotificationService requires connect or repository")
            repository = NotificationsRepository(connect)
        self._connect = connect
        self._repository = repository
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
        with self._repository.transaction() as transaction:
            row = transaction.state(alert_key)
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
                transaction.append_log(
                    alert_key=alert_key,
                    severity=severity,
                    event_state=event_state,
                    subject=send_subject,
                    message=send_message_text,
                    created_at=now.isoformat(),
                    status=status,
                    error=error,
                    sent_at=sent_at,
                )
            persisted_active = bool(
                active
                or (
                    event_state == "resolved"
                    and (status == "failed" or consecutive_failures > 0)
                )
            )
            transaction.upsert_state(
                alert_key=alert_key,
                severity=severity,
                active=persisted_active,
                changed_at=now.isoformat(),
                notified_at=sent_at,
                attempted_at=attempted_at,
                error=error,
                consecutive_failures=consecutive_failures,
                message=message,
            )
        return {"alert_key": alert_key, "active": active, "status": status, "error": error}

    def diagnostics(self, limit: int = 50) -> dict:
        active, log = self._repository.diagnostics(limit)
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
        sent_at: str | None = None,
    ) -> None:
        self._repository.append_log(
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


class NotificationWorker:
    def __init__(
        self,
        checker: Callable[[], Iterable[dict]],
        service: NotificationService,
        interval_seconds: int,
    ):
        self._checker = checker
        self._service = service
        try:
            checked_interval = int(interval_seconds)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "notification worker interval must be an integer"
            ) from exc
        if not (
            MIN_NOTIFICATION_WORKER_INTERVAL_SECONDS
            <= checked_interval
            <= MAX_NOTIFICATION_WORKER_INTERVAL_SECONDS
        ):
            raise ValueError(
                "notification worker interval must be between "
                f"{MIN_NOTIFICATION_WORKER_INTERVAL_SECONDS} and "
                f"{MAX_NOTIFICATION_WORKER_INTERVAL_SECONDS} seconds"
            )
        self._interval_seconds = checked_interval
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
