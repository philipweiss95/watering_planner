from __future__ import annotations

from datetime import datetime, time, timedelta
from http import HTTPStatus
from typing import Any

from watering_backend.api.responses import ApiResponse, error_response
from watering_backend.api.router import ApiContext, ApiRequest, Router


def shortcuts(
    context: ApiContext,
    request: ApiRequest,
    _params: dict[str, str],
) -> ApiResponse:
    host = request.headers.get("Host", "127.0.0.1:8080")
    base_url = request.first("base_url", f"http://{host}")
    return ApiResponse(context.shortcut_blueprint(base_url))


def homekit_check(
    context: ApiContext,
    request: ApiRequest,
    _params: dict[str, str],
) -> ApiResponse:
    try:
        weather = context.weather_from_query(request.query)
        return ApiResponse(context.evaluate_weather(weather, request.first("slot", "morning")))
    except ValueError as exc:
        return error_response(str(exc), HTTPStatus.BAD_GATEWAY)


def homekit_mark_run(
    context: ApiContext,
    request: ApiRequest,
    _params: dict[str, str],
) -> ApiResponse:
    payload: dict[str, Any] = request.json()
    weather = context.weather_from_payload(payload)
    slot = str(payload.get("slot", "morning"))
    result = context.evaluate_weather(weather, slot)
    booking = context.mark_run(
        delivered_ml=int(result["pump"]["delivered_per_cycle_ml"]),
        temperature_c=float(weather["temperature_c"]),
        rain_mm=float(weather["rain_mm"]),
        run_id=payload.get("run_id"),
        source=str(payload.get("source", "home_assistant")),
    )
    return ApiResponse(
        {
            **context.evaluate_weather(weather, slot),
            "booking": booking,
        }
    )


def manual_run(
    context: ApiContext,
    request: ApiRequest,
    _params: dict[str, str],
) -> ApiResponse:
    payload: dict[str, Any] = request.json()
    weather = context.weather_from_payload(payload)
    result = context.evaluate_weather(weather, str(payload.get("slot", "morning")))
    if not result["manual_run"]["available"]:
        return error_response(result["manual_run"]["reason"], HTTPStatus.CONFLICT)
    try:
        run_id = context.trigger_home_assistant_manual_run(result, payload.get("run_id"))
    except ValueError as exc:
        return error_response(str(exc), HTTPStatus.BAD_GATEWAY)
    return ApiResponse(
        {
            "accepted": True,
            "run_id": run_id,
            "message": "Manueller Pumpenlauf wurde an Home Assistant \u00fcbergeben.",
            "evaluation": result,
        },
        HTTPStatus.ACCEPTED,
    )


def manual_refill(
    context: ApiContext,
    request: ApiRequest,
    _params: dict[str, str],
) -> ApiResponse:
    payload: dict[str, Any] = request.json()
    weather = context.weather_from_payload(payload)
    result = context.evaluate_weather(weather, str(payload.get("slot", "morning")))
    if not result["manual_refill"]["available"]:
        return error_response(result["manual_refill"]["reason"], HTTPStatus.CONFLICT)
    try:
        run_id = context.trigger_home_assistant_manual_refill(result, payload.get("run_id"))
    except ValueError as exc:
        return error_response(str(exc), HTTPStatus.BAD_GATEWAY)
    return ApiResponse(
        {
            "accepted": True,
            "run_id": run_id,
            "message": "Manueller Nachf\u00fclllauf wurde an Home Assistant \u00fcbergeben.",
            "evaluation": result,
        },
        HTTPStatus.ACCEPTED,
    )


def pause_automation(
    context: ApiContext,
    request: ApiRequest,
    _params: dict[str, str],
) -> ApiResponse:
    payload: dict[str, Any] = request.json()
    timezone_name = context.get_state()["balcony"].get("timezone_name", "Europe/Berlin")
    now = context.local_now(str(timezone_name))
    if payload.get("until"):
        pause_until = datetime.fromisoformat(str(payload["until"]))
        if pause_until.tzinfo is None:
            pause_until = pause_until.replace(tzinfo=now.tzinfo)
    else:
        pause_until = datetime.combine(
            now.date() + timedelta(days=1),
            time(0, 0),
            tzinfo=now.tzinfo,
        )
    context.set_setting("automation_pause_until", pause_until.isoformat())
    return ApiResponse({"automation_pause_until": pause_until.isoformat()})


def resume_automation(
    context: ApiContext,
    _request: ApiRequest,
    _params: dict[str, str],
) -> ApiResponse:
    context.delete_setting("automation_pause_until")
    return ApiResponse({"automation_pause_until": ""})


def register(router: Router) -> None:
    router.get("/api/shortcuts", shortcuts)
    router.get("/api/homekit/check", homekit_check)
    router.post("/api/homekit/mark-run", homekit_mark_run)
    router.post("/api/manual-run", manual_run)
    router.post("/api/manual-refill", manual_refill)
    router.post("/api/automation/pause", pause_automation)
    router.post("/api/automation/resume", resume_automation)
