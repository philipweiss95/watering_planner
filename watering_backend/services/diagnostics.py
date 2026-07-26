from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from watering_backend.notifications import SMTPConfig
from watering_backend.repositories.settings import SettingsRepository


class DiagnosticsService:
    """Build secret-free status values from persisted operational metadata."""

    def __init__(
        self,
        *,
        settings: SettingsRepository,
        planner_config: Callable[[], dict[str, Any]],
        smtp_config: Callable[[], SMTPConfig] = SMTPConfig.from_env,
        now: Callable[[], datetime] | None = None,
    ):
        self.settings = settings
        self.planner_config = planner_config
        self.smtp_config = smtp_config
        self.now = now or (lambda: datetime.now(timezone.utc))

    def weather(self) -> dict[str, Any]:
        config = self.planner_config()
        fetched_at = self.settings.get(
            "last_successful_weather_fetch_at",
            "",
        )
        attempted_at = self.settings.get(
            "last_weather_fetch_attempt_at",
            "",
        )
        last_error = self.settings.get("last_weather_fetch_error", "")
        stale = True
        age_minutes: float | None = None
        if fetched_at:
            try:
                fetched = datetime.fromisoformat(fetched_at)
                if fetched.tzinfo is None:
                    fetched = fetched.replace(tzinfo=timezone.utc)
                current = self.now()
                if current.tzinfo is None:
                    current = current.replace(tzinfo=timezone.utc)
                age_minutes = max(
                    0,
                    round(
                        (
                            current.astimezone(timezone.utc)
                            - fetched.astimezone(timezone.utc)
                        ).total_seconds()
                        / 60,
                        1,
                    ),
                )
                stale = age_minutes > int(
                    config["weather_stale_after_minutes"]
                )
            except ValueError:
                stale = True
        return {
            "last_successful_fetch_at": fetched_at,
            "last_attempt_at": attempted_at,
            "last_error": last_error,
            "data_age_minutes": age_minutes,
            "cache_minutes": int(config["weather_cache_minutes"]),
            "stale_after_minutes": int(
                config["weather_stale_after_minutes"]
            ),
            "stale": stale,
        }

    def notification_public_status(self) -> dict[str, Any]:
        try:
            return self.smtp_config().public_status()
        except ValueError as exc:
            return {
                "enabled": False,
                "configured": False,
                "configuration_error": str(exc),
            }

