from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

from watering_backend.notifications import SMTPConfig
from watering_backend.repositories.settings import SettingsRepository


class SMTPConfigurationService:
    """Resolve environment defaults and write-only browser overrides."""

    SETTING_KEYS = {
        "enabled": "secret_smtp_enabled",
        "host": "secret_smtp_host",
        "port": "secret_smtp_port",
        "username": "secret_smtp_username",
        "password": "secret_smtp_password",
        "sender": "secret_smtp_sender",
        "recipients": "secret_smtp_recipients",
        "security": "secret_smtp_security",
    }
    ENVIRONMENT_KEYS = {
        "enabled": "NOTIFICATIONS_ENABLED",
        "host": "SMTP_HOST",
        "port": "SMTP_PORT",
        "username": "SMTP_USERNAME",
        "password": "SMTP_PASSWORD",
        "sender": "SMTP_FROM",
        "recipients": "SMTP_TO",
        "security": "SMTP_SECURITY",
    }
    ALLOWED_PAYLOAD_KEYS = frozenset(
        {
            *SETTING_KEYS,
            "clear_credentials",
        }
    )

    def __init__(
        self,
        settings: SettingsRepository,
        environment: Mapping[str, str],
    ):
        self.settings = settings
        self.environment = environment

    def _stored(
        self,
        field: str,
        *,
        conn=None,
    ) -> str | None:
        return self.settings.find(
            self.SETTING_KEYS[field],
            conn=conn,
        )

    def _values(self, *, conn=None) -> dict[str, str]:
        values: dict[str, str] = {}
        for field, environment_key in self.ENVIRONMENT_KEYS.items():
            stored = self._stored(field, conn=conn)
            values[environment_key] = (
                stored
                if stored is not None
                else str(self.environment.get(environment_key, ""))
            )
        return values

    def load(self, *, conn=None) -> SMTPConfig:
        return SMTPConfig.from_mapping(self._values(conn=conn))

    def public_status(self) -> dict[str, Any]:
        try:
            status = self.load().public_status()
        except ValueError as exc:
            status = {
                "enabled": False,
                "configured": False,
                "configuration_error": str(exc),
            }
        status["write_only"] = True
        status["web_configured"] = any(
            self._stored(field) is not None
            for field in self.SETTING_KEYS
        )
        return status

    @staticmethod
    def _text(
        value: object,
        label: str,
        *,
        maximum: int,
        allow_empty: bool = False,
    ) -> str:
        if not isinstance(value, str):
            raise ValueError(f"{label} muss Text sein")
        normalized = value.strip()
        if not normalized and not allow_empty:
            raise ValueError(f"{label} darf nicht leer sein")
        if len(normalized) > maximum:
            raise ValueError(
                f"{label} darf höchstens {maximum} Zeichen lang sein"
            )
        return normalized

    def _normalize(self, payload: object) -> dict[str, str]:
        if not isinstance(payload, dict):
            raise ValueError("SMTP-Konfiguration muss ein Objekt sein")
        unknown = set(payload) - self.ALLOWED_PAYLOAD_KEYS
        if unknown:
            raise ValueError(
                "Unbekannte SMTP-Felder: "
                + ", ".join(sorted(str(item) for item in unknown))
            )

        normalized: dict[str, str] = {}
        if "enabled" in payload:
            if not isinstance(payload["enabled"], bool):
                raise ValueError("E-Mail-Benachrichtigungen müssen an oder aus sein")
            normalized["enabled"] = (
                "true" if payload["enabled"] else "false"
            )
        if "host" in payload and payload["host"] != "":
            normalized["host"] = self._text(
                payload["host"],
                "SMTP-Server",
                maximum=255,
            )
        if "port" in payload and payload["port"] not in {"", None}:
            value = payload["port"]
            if isinstance(value, bool):
                raise ValueError("SMTP-Port muss eine ganze Zahl sein")
            try:
                port = float(value)
            except (TypeError, ValueError) as exc:
                raise ValueError("SMTP-Port muss eine ganze Zahl sein") from exc
            if (
                not math.isfinite(port)
                or not port.is_integer()
                or not 1 <= port <= 65535
            ):
                raise ValueError(
                    "SMTP-Port muss zwischen 1 und 65535 liegen"
                )
            normalized["port"] = str(int(port))
        if "username" in payload and payload["username"] != "":
            normalized["username"] = self._text(
                payload["username"],
                "SMTP-Benutzername",
                maximum=255,
            )
        if "password" in payload and payload["password"] != "":
            password = payload["password"]
            if not isinstance(password, str):
                raise ValueError("SMTP-Passwort muss Text sein")
            if len(password) > 1024:
                raise ValueError(
                    "SMTP-Passwort darf höchstens 1024 Zeichen lang sein"
                )
            normalized["password"] = password
        if "sender" in payload and payload["sender"] != "":
            normalized["sender"] = self._text(
                payload["sender"],
                "Absender",
                maximum=320,
            )
        if "recipients" in payload and payload["recipients"] != "":
            normalized["recipients"] = self._text(
                payload["recipients"],
                "Empfänger",
                maximum=2000,
            )
        if "security" in payload and payload["security"] != "":
            security = self._text(
                payload["security"],
                "SMTP-Sicherheit",
                maximum=20,
            ).lower()
            if security not in {"ssl", "starttls", "none"}:
                raise ValueError(
                    "SMTP-Sicherheit muss ssl, starttls oder none sein"
                )
            normalized["security"] = security
        if payload.get("clear_credentials") is True:
            normalized["username"] = ""
            normalized["password"] = ""
        elif (
            "clear_credentials" in payload
            and payload["clear_credentials"] is not False
        ):
            raise ValueError(
                "Anmeldedaten löschen muss an oder aus sein"
            )
        return normalized

    def save(self, payload: object) -> dict[str, Any]:
        normalized = self._normalize(payload)
        if not normalized:
            raise ValueError("Keine SMTP-Änderung angegeben")
        with self.settings.database.connection(immediate=True) as conn:
            for field, value in normalized.items():
                self.settings.set(
                    self.SETTING_KEYS[field],
                    value,
                    conn=conn,
                )
            config = self.load(conn=conn)
            if config.enabled:
                config.validate_for_send()
                if config.username and not config.password:
                    raise ValueError(
                        "Für den SMTP-Benutzernamen fehlt das Passwort"
                    )
        return self.public_status()
