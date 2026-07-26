from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from watering_backend.home_assistant import post_webhook
from watering_backend.repositories.settings import SettingsRepository
from watering_backend.services.watering import normalize_run_id


def shortcut_blueprint(base_url: str) -> dict[str, Any]:
    check_url = (
        f"{base_url.rstrip('/')}/api/homekit/check?auto=true&slot=morning"
    )
    mark_url = f"{base_url.rstrip('/')}/api/homekit/mark-run"
    manual_run_url = f"{base_url.rstrip('/')}/api/manual-run"
    return {
        "name": "Terrassenbewässerung prüfen",
        "base_url_placeholder": base_url,
        "steps": [
            {"action": "URL", "value": check_url},
            {
                "action": "Inhalte von URL abrufen",
                "method": "GET",
                "headers": {"Accept": "application/json"},
            },
            {"action": "Wert für Schlüssel abrufen", "key": "run_now"},
            {"action": "Wenn", "condition": "run_now ist wahr"},
            {"action": "UUID abrufen", "variable": "run_id"},
            {"action": "HomeKit", "value": "Pumpe einschalten"},
            {"action": "Warten", "seconds": 65},
            {"action": "HomeKit", "value": "Pumpe ausschalten"},
            {
                "action": "Inhalte von URL abrufen",
                "method": "POST",
                "url": mark_url,
                "headers": {"Content-Type": "application/json"},
                "body": {
                    "auto_weather": True,
                    "slot": "morning",
                    "run_id": "{{run_id}}",
                },
            },
            {"action": "Ende Wenn"},
        ],
        "check_url": check_url,
        "mark_run_url": mark_url,
        "manual_run_url": manual_run_url,
        "manual_run_steps": [
            {"action": "URL", "value": manual_run_url},
            {
                "action": "Inhalte von URL abrufen",
                "method": "POST",
                "url": manual_run_url,
                "headers": {"Content-Type": "application/json"},
                "body": {
                    "auto_weather": True,
                    "run_id": "{{run_id}}",
                },
            },
        ],
    }


class HomeAssistantService:
    """Webhook integration with injectable transport and secret-free status."""

    def __init__(
        self,
        *,
        settings: SettingsRepository,
        now_iso: Callable[[], str],
        manual_refill_plan: Callable[
            [dict[str, Any]],
            dict[str, Any],
        ],
        health_notifier: Callable[[bool, str], None] | None = None,
        environment: Mapping[str, str] | None = None,
        opener: Callable[..., Any] = urlopen,
    ):
        self.settings = settings
        self.now_iso = now_iso
        self.manual_refill_plan = manual_refill_plan
        self.health_notifier = health_notifier or (lambda _ok, _detail: None)
        self.environment = environment if environment is not None else os.environ
        self.opener = opener

    def _webhook_url(self, name: str) -> str:
        value = self.environment.get(name, "").strip()
        if not value or "HOME-ASSISTANT-IP" in value:
            return ""
        parsed = urlparse(value)
        if parsed.scheme in {"http", "https"} and parsed.netloc:
            return value
        return ""

    def watering_webhook_url(self) -> str:
        return self._webhook_url("HOME_ASSISTANT_WEBHOOK_URL")

    def refill_webhook_url(self) -> str:
        return self._webhook_url("HOME_ASSISTANT_REFILL_WEBHOOK_URL")

    def diagnostics(self) -> dict[str, Any]:
        return {
            "configured": bool(
                self.watering_webhook_url() or self.refill_webhook_url()
            ),
            "last_successful_contact_at": self.settings.get(
                "home_assistant_last_success_at",
                "",
            ),
            "last_error": self.settings.get(
                "home_assistant_last_error",
                "",
            ),
        }

    def record_health(self, reachable: bool, detail: str) -> None:
        try:
            self.health_notifier(reachable, detail)
        except Exception:
            pass

    def test_connection(self) -> dict[str, Any]:
        webhook = self.watering_webhook_url() or self.refill_webhook_url()
        if not webhook:
            return {
                **self.diagnostics(),
                "reachable": False,
                "error": "Home Assistant ist nicht konfiguriert.",
            }
        parsed = urlparse(webhook)
        probe_url = f"{parsed.scheme}://{parsed.netloc}/api/"
        request = Request(probe_url, method="GET")
        try:
            with self.opener(request, timeout=5) as response:
                response.read(1)
        except HTTPError as exc:
            if exc.code not in {401, 403, 404, 405}:
                message = (
                    f"Home Assistant antwortet mit HTTP {exc.code}."
                )
                self.settings.set("home_assistant_last_error", message)
                self.record_health(False, message)
                return {
                    **self.diagnostics(),
                    "reachable": False,
                    "error": message,
                }
        except (OSError, URLError, TimeoutError) as exc:
            message = f"Home Assistant ist nicht erreichbar: {exc}"
            self.settings.set("home_assistant_last_error", message)
            self.record_health(False, message)
            return {
                **self.diagnostics(),
                "reachable": False,
                "error": message,
            }
        contacted_at = self.now_iso()
        self.settings.set("home_assistant_last_success_at", contacted_at)
        self.settings.delete("home_assistant_last_error")
        self.record_health(True, "Home Assistant ist erreichbar.")
        return {
            **self.diagnostics(),
            "reachable": True,
            "last_successful_contact_at": contacted_at,
            "error": "",
        }

    def manual_run_status(self, result: dict[str, Any]) -> dict[str, Any]:
        delivered_per_cycle = int(
            result["pump"]["delivered_per_cycle_ml"]
        )
        consumed_per_cycle = int(
            result["pump"].get(
                "consumed_per_cycle_ml",
                delivered_per_cycle,
            )
        )
        if not result["plants"]:
            reason = "Noch keine Pflanzen angelegt."
        elif delivered_per_cycle <= 0:
            reason = "Noch keine nutzbare Verschlauchung vorhanden."
        elif int(result["tank"]["current_ml"]) < consumed_per_cycle:
            reason = (
                "Der Wassertank reicht nicht für einen vollständigen "
                "Pumpenlauf."
            )
        elif not self.watering_webhook_url():
            reason = (
                "Home-Assistant-Webhook für manuelle Läufe ist noch "
                "nicht konfiguriert."
            )
        else:
            return {
                "available": True,
                "reason": (
                    "Ein manueller Zyklus kann sofort über Home Assistant "
                    "gestartet werden."
                ),
                "endpoint": "/api/manual-run",
            }
        return {
            "available": False,
            "reason": reason,
            "endpoint": "/api/manual-run",
        }

    def manual_refill_status(
        self,
        result: dict[str, Any],
    ) -> dict[str, Any]:
        manual_plan = self.manual_refill_plan(result)
        if int(manual_plan.get("planned_transfer_ml", 0)) <= 0:
            reason = manual_plan.get("summary") or "Keine Nachfüllung nötig."
        elif result.get("refill", {}).get("active_run"):
            reason = "Ein Nachfülllauf ist bereits reserviert oder aktiv."
        elif result.get("refill", {}).get("manual_review_required"):
            reason = (
                "Ein früherer Nachfülllauf ist unbestätigt. "
                "Tankstände zuerst manuell prüfen."
            )
        elif result.get("refill", {}).get("cooldown_active"):
            reason = (
                result["refill"].get("summary")
                or "Zwischen Nachfüllvorgängen müssen mindestens drei "
                "Stunden liegen."
            )
        elif not self.refill_webhook_url():
            reason = (
                "Home-Assistant-Webhook für manuelle Nachfüllläufe ist "
                "noch nicht konfiguriert."
            )
        else:
            return {
                "available": True,
                "reason": (
                    "Ein Nachfülllauf kann über Home Assistant gestartet "
                    "werden."
                ),
                "endpoint": "/api/manual-refill",
                **manual_plan,
            }
        return {
            "available": False,
            "reason": reason,
            "endpoint": "/api/manual-refill",
            **manual_plan,
        }

    def trigger_manual_run(
        self,
        result: dict[str, Any],
        run_id: object = None,
    ) -> str:
        status = self.manual_run_status(result)
        if not status["available"]:
            raise ValueError(status["reason"])
        normalized_run_id, _ = normalize_run_id(run_id, "watering")
        try:
            post_webhook(
                self.watering_webhook_url(),
                {
                    "source": "watering-planner",
                    "requested_at": self.now_iso(),
                    "run_id": normalized_run_id,
                    "delivered_per_cycle_ml": result["pump"][
                        "delivered_per_cycle_ml"
                    ],
                    "consumed_per_cycle_ml": result["pump"][
                        "consumed_per_cycle_ml"
                    ],
                },
                self.opener,
            )
        except ValueError as exc:
            self.record_health(False, str(exc))
            raise
        self.record_health(True, "Webhook ist wieder erreichbar.")
        return normalized_run_id

    def trigger_reserved_refill(
        self,
        reservation: dict[str, Any],
    ) -> None:
        """Notify Home Assistant about an already persisted manual run."""
        run_id = str(reservation.get("run_id", "")).strip()
        duration_seconds = int(
            reservation.get("duration_seconds", 0)
        )
        planned_transfer_ml = int(
            reservation.get("planned_transfer_ml", 0)
        )
        if not run_id or duration_seconds <= 0 or planned_transfer_ml <= 0:
            raise ValueError("Ungültige Nachfüllreservierung")
        try:
            post_webhook(
                self.refill_webhook_url(),
                {
                    "source": "watering-planner",
                    "requested_at": self.now_iso(),
                    "run_id": run_id,
                    "run_type": str(
                        reservation.get("run_type", "manual")
                    ),
                    "planned_transfer_ml": planned_transfer_ml,
                    "duration_seconds": duration_seconds,
                    "expected_complete_at": reservation.get(
                        "expected_complete_at",
                        "",
                    ),
                },
                self.opener,
            )
        except ValueError as exc:
            self.record_health(False, str(exc))
            raise
        self.record_health(True, "Webhook ist wieder erreichbar.")
