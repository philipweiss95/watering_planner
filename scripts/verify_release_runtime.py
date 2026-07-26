from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class RuntimeVerificationError(RuntimeError):
    pass


EXPECTED_PLANTS = {
    11: ("olive", "Olivia", "large"),
    12: ("tomato", "Roma links", "medium"),
    13: ("lavender", "Lavendel klein", "small"),
    14: ("citrus", "Zitrone am Gelander", "large"),
    15: ("olive", "Olivenbaum Altbestand", "tree"),
}
EXPECTED_HOSE_NUMBERS = {"01", "02", "03", "04", "05", "06", "07", "08"}
EXPECTED_WATERING_EVENT_IDS = {101, 102, 103}


class ApiClient(Protocol):
    def request(self, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]: ...


@dataclass
class HttpJsonClient:
    base_url: str
    request_timeout: float = 10.0

    def request(self, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = Request(
            f"{self.base_url.rstrip('/')}{path}",
            data=body,
            headers={"Content-Type": "application/json"} if body is not None else {},
            method="POST" if body is not None else "GET",
        )
        try:
            with urlopen(request, timeout=self.request_timeout) as response:
                result = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            raise RuntimeVerificationError(f"{request.method} {path} antwortet mit HTTP {exc.code}") from exc
        except (URLError, TimeoutError) as exc:
            raise RuntimeVerificationError(f"{request.method} {path} ist nicht erreichbar") from exc
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise RuntimeVerificationError(f"{request.method} {path} liefert kein gueltiges JSON") from exc
        if not isinstance(result, dict):
            raise RuntimeVerificationError(f"{request.method} {path} liefert kein JSON-Objekt")
        return result


def require(condition: object, message: str) -> None:
    if not condition:
        raise RuntimeVerificationError(message)


def wait_for_health(client: ApiClient, timeout_seconds: float) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    last_error = "kein Healthcheck ausgefuehrt"
    while time.monotonic() < deadline:
        try:
            health = client.request("/api/health")
            if health.get("ok") is True:
                return health
            last_error = "/api/health meldet nicht ok"
        except RuntimeVerificationError as exc:
            last_error = str(exc)
        time.sleep(2)
    raise RuntimeVerificationError(f"Planner wurde nicht rechtzeitig gesund: {last_error}")


def _tank_levels(state: dict[str, Any]) -> tuple[int, int]:
    balcony = state.get("balcony")
    require(isinstance(balcony, dict), "/api/state enthaelt keinen Balkonstatus")
    try:
        return int(balcony["tank_current_ml"]), int(balcony["refill_tank_current_ml"])
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeVerificationError("/api/state enthaelt keine gueltigen Tankstaende") from exc


def _assert_no_secrets(payloads: list[dict[str, Any]], forbidden_values: list[str]) -> None:
    serialized = json.dumps(payloads, ensure_ascii=True, sort_keys=True)
    leaked = [value for value in forbidden_values if value and value in serialized]
    require(not leaked, "Eine geheime Testkonfiguration wurde ueber die Browser-API ausgegeben")


def verify_runtime(
    client: ApiClient,
    *,
    expected_version: str,
    run_prefix: str,
    forbidden_values: list[str] | None = None,
) -> dict[str, Any]:
    forbidden_values = forbidden_values or []
    health = client.request("/api/health")
    require(health.get("ok") is True, "/api/health meldet nicht ok")
    require(health.get("version") == expected_version, "Healthcheck meldet die falsche Version")

    initial_state = client.request("/api/state")
    require(initial_state.get("version") == expected_version, "/api/state meldet die falsche Version")
    plants = initial_state.get("plants")
    require(isinstance(plants, list) and plants, "Migrierte Pflanzen fehlen")
    require(
        all(isinstance(plant, dict) and isinstance(plant.get("catalog_id"), str) for plant in plants),
        "Eine migrierte catalog_id ist keine Zeichenkette",
    )
    migrated_plants = {
        int(plant["id"]): (
            plant["catalog_id"],
            plant["custom_name"],
            plant.get("size"),
        )
        for plant in plants
        if isinstance(plant, dict)
        and "id" in plant
        and "catalog_id" in plant
        and "custom_name" in plant
    }
    require(migrated_plants == EXPECTED_PLANTS, "Die Pflanzen der v1.4.3-Fixture wurden nicht vollstaendig migriert")
    hoses = initial_state.get("hoses")
    require(isinstance(hoses, list), "Migrierte Schlaeuche fehlen")
    hose_numbers = {
        str(hose.get("number"))
        for hose in hoses
        if isinstance(hose, dict)
    }
    require(hose_numbers == EXPECTED_HOSE_NUMBERS, "Die acht Schlaeuche der v1.4.3-Fixture fehlen")
    initial_main, initial_refill = _tank_levels(initial_state)
    balcony = initial_state["balcony"]
    require(int(balcony.get("tank_capacity_ml", 0)) == 45_000, "Haupttankkapazitaet wurde nicht erhalten")
    require(int(balcony.get("refill_tank_capacity_ml", 0)) == 55_000, "Vorratstankkapazitaet wurde nicht erhalten")
    require(initial_main == 17_321, "Haupttankstand der v1.4.3-Fixture wurde veraendert")
    require(initial_refill == 41_007, "Vorratstankstand der v1.4.3-Fixture wurde veraendert")
    watering_history = client.request("/api/watering-events?limit=12")
    old_events = watering_history.get("events")
    require(isinstance(old_events, list), "Bisheriger Bewaesserungsverlauf fehlt")
    old_event_ids = {
        int(event["id"])
        for event in old_events
        if isinstance(event, dict) and "id" in event
    }
    require(
        EXPECTED_WATERING_EVENT_IDS.issubset(old_event_ids),
        "Bewaesserungsverlauf der v1.4.3-Fixture wurde nicht erhalten",
    )

    simulation = {
        "temperature_c": 29,
        "rain_mm": 0.4,
        "wind_kmh": 12,
        "sunshine_hours": 8,
        "et0_mm": 5.1,
    }
    evaluation = client.request("/api/evaluate", simulation)
    weather = evaluation.get("weather")
    require(isinstance(weather, dict) and weather.get("simulation") is True, "Wettertest ist nicht als Simulation markiert")
    depletion = evaluation.get("depletion")
    require(isinstance(depletion, dict), "Reichweitenprognose fehlt")
    forecast_events = depletion.get("forecast_events")
    require(isinstance(forecast_events, list) and forecast_events, "Prognoseereignisse fehlen")
    require("last_supported_watering_at" in depletion, "Kompatibles Reichweitenfeld fehlt")

    watering_run_id = f"{run_prefix}-watering"
    watering_payload = {
        **simulation,
        "run_id": watering_run_id,
        "source": "release_runtime_check",
    }
    before_watering = client.request("/api/state")
    first_watering = client.request("/api/homekit/mark-run", watering_payload)
    second_watering = client.request("/api/homekit/mark-run", watering_payload)
    after_watering = client.request("/api/state")
    first_booking = first_watering.get("booking")
    second_booking = second_watering.get("booking")
    require(isinstance(first_booking, dict), "Erste Bewaesserungsbuchung fehlt")
    require(isinstance(second_booking, dict), "Wiederholte Bewaesserungsbuchung fehlt")
    require(first_booking.get("idempotent_replay") is False, "Erste Bewaesserung wurde als Wiederholung behandelt")
    require(second_booking.get("idempotent_replay") is True, "Doppelte Bewaesserung wurde erneut verbucht")
    require(first_booking.get("run_id") == watering_run_id, "Bewaesserungs-run_id wurde veraendert")
    require(second_booking.get("id") == first_booking.get("id"), "Doppelte Bewaesserung lieferte ein anderes Ereignis")
    before_main, before_refill = _tank_levels(before_watering)
    after_main, after_refill = _tank_levels(after_watering)
    actual_consumed = int(first_booking.get("actual_consumed_ml", 0))
    require(actual_consumed > 0, "Kalibrierter Tankverbrauch ist nicht positiv")
    require(before_main - after_main == actual_consumed, "Doppelte Bewaesserung hat den Haupttank mehr als einmal veraendert")
    require(before_refill == after_refill, "Bewaesserung hat den Vorratstank veraendert")

    refill_run_id = f"{run_prefix}-refill"
    before_refill_run = client.request("/api/state")
    refill_start = client.request(
        "/api/refill/start",
        {
            "run_id": refill_run_id,
            "run_type": "manual",
            "source": "release_runtime_check",
        },
    )
    refill_running = client.request(
        "/api/refill/running",
        {"run_id": refill_run_id},
    )
    refill_running_replay = client.request(
        "/api/refill/running",
        {"run_id": refill_run_id},
    )
    first_refill_booking = client.request(
        "/api/refill/complete",
        {"run_id": refill_run_id},
    )
    second_refill_booking = client.request(
        "/api/refill/complete",
        {"run_id": refill_run_id},
    )
    late_refill_start = client.request(
        "/api/refill/start",
        {
            "run_id": refill_run_id,
            "run_type": "manual",
            "source": "release_runtime_check",
        },
    )
    persisted_refill = client.request(
        f"/api/refill/runs/{refill_run_id}"
    )
    after_refill_run = client.request("/api/state")
    reserved_refill = refill_start.get("refill_run")
    running_refill = refill_running.get("refill_run")
    repeated_running_refill = refill_running_replay.get("refill_run")
    first_refill_event = first_refill_booking.get("refill_run")
    second_refill_event = second_refill_booking.get("refill_run")
    late_refill = late_refill_start.get("refill_run")
    persisted_refill_run = persisted_refill.get("refill_run")
    require(isinstance(reserved_refill, dict), "Nachfuellreservierung fehlt")
    require(isinstance(running_refill, dict), "Laufbestaetigung fehlt")
    require(
        isinstance(repeated_running_refill, dict),
        "Wiederholte Laufbestaetigung fehlt",
    )
    require(isinstance(first_refill_event, dict), "Erste Nachfuellbuchung fehlt")
    require(isinstance(second_refill_event, dict), "Wiederholte Nachfuellbuchung fehlt")
    require(isinstance(late_refill, dict), "Verspaeteter Nachfuellstart fehlt")
    require(isinstance(persisted_refill_run, dict), "Persistenter Nachfuelllauf fehlt")
    require(reserved_refill.get("status") == "reserved", "Nachfuelllauf wurde nicht reserviert")
    require(running_refill.get("status") == "running", "Nachfuelllauf wurde nicht als laufend bestaetigt")
    require(
        running_refill.get("pump_start_authorized") is True,
        "Erster Claim hat den Pumpenstart nicht freigegeben",
    )
    require(
        repeated_running_refill.get("pump_start_authorized") is False,
        "Wiederholter Claim hat den Pumpenstart erneut freigegeben",
    )
    require(
        int(repeated_running_refill.get("duration_seconds", 0)) == 0,
        "Wiederholter Claim lieferte eine nutzbare Laufdauer",
    )
    require(first_refill_event.get("idempotent_replay") is False, "Erste Nachfuellung wurde als Wiederholung behandelt")
    require(second_refill_event.get("idempotent_replay") is True, "Doppelte Nachfuellung wurde erneut verbucht")
    require(first_refill_event.get("run_id") == refill_run_id, "Nachfuell-run_id wurde veraendert")
    require(second_refill_event.get("run_id") == refill_run_id, "Wiederholte Nachfuellung lieferte eine andere run_id")
    require(persisted_refill_run.get("status") == "completed", "Nachfuelllauf wurde nicht persistent abgeschlossen")
    require(
        late_refill.get("pump_start_authorized") is False
        and int(late_refill.get("duration_seconds", 0)) == 0,
        "Abgeschlossener Lauf konnte erneut gestartet werden",
    )
    require(
        int(reserved_refill.get("planned_transfer_ml", 0))
        == int(first_refill_event.get("physical_transfer_ml", 0)),
        "Abschluss verwendet nicht die reservierte Nachfuellmenge",
    )
    transferred = int(first_refill_event.get("transferred_ml", 0))
    require(transferred > 0, "Nachfuellbuchung hat kein Wasser uebertragen")
    main_before_refill, reserve_before_refill = _tank_levels(before_refill_run)
    main_after_refill, reserve_after_refill = _tank_levels(after_refill_run)
    require(
        main_after_refill - main_before_refill == transferred,
        "Doppelte Nachfuellung hat den Haupttank mehr als einmal veraendert",
    )
    require(
        reserve_before_refill - reserve_after_refill == transferred,
        "Doppelte Nachfuellung hat den Vorratstank mehr als einmal veraendert",
    )

    notification_diagnostics = client.request("/api/diagnostics/notifications")
    home_assistant_diagnostics = client.request("/api/diagnostics/home-assistant")
    require(isinstance(notification_diagnostics.get("smtp"), dict), "SMTP-Diagnose fehlt")
    require("worker_running" in notification_diagnostics, "Worker-Diagnose fehlt")
    require("configured" in home_assistant_diagnostics, "Home-Assistant-Diagnose fehlt")
    _assert_no_secrets(
        [
            initial_state,
            watering_history,
            evaluation,
            first_watering,
            second_watering,
            first_refill_booking,
            second_refill_booking,
            persisted_refill,
            notification_diagnostics,
            home_assistant_diagnostics,
        ],
        forbidden_values,
    )

    return {
        "version": expected_version,
        "plant_count": len(plants),
        "hose_count": len(hoses),
        "preserved_watering_event_count": len(EXPECTED_WATERING_EVENT_IDS),
        "forecast_event_count": len(forecast_events),
        "watering_consumed_ml": actual_consumed,
        "refill_transferred_ml": transferred,
        "main_tank_before_ml": initial_main,
        "main_tank_after_ml": main_after_refill,
        "refill_tank_before_ml": initial_refill,
        "refill_tank_after_ml": reserve_after_refill,
    }


def parse_args(arguments: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prueft einen laufenden 1.5-Releasecontainer.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8080")
    parser.add_argument("--expected-version", default="1.5.0")
    parser.add_argument("--health-timeout", type=float, default=120)
    parser.add_argument("--request-timeout", type=float, default=10)
    parser.add_argument("--run-prefix")
    parser.add_argument("--forbidden-value", action="append", default=[])
    return parser.parse_args(arguments)


def main(arguments: list[str] | None = None) -> int:
    options = parse_args(arguments)
    client = HttpJsonClient(options.base_url, options.request_timeout)
    run_prefix = options.run_prefix or f"release-smoke-{uuid.uuid4().hex}"
    try:
        wait_for_health(client, options.health_timeout)
        result = verify_runtime(
            client,
            expected_version=options.expected_version,
            run_prefix=run_prefix,
            forbidden_values=options.forbidden_value,
        )
    except (RuntimeVerificationError, TypeError, ValueError) as exc:
        print(f"Container-Laufzeitpruefung fehlgeschlagen: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
