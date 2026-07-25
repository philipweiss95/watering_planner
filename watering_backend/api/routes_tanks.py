from __future__ import annotations

from typing import Any

from watering_backend.api.responses import ApiResponse
from watering_backend.api.router import ApiContext, ApiRequest, Router


def watering_events(
    context: ApiContext,
    request: ApiRequest,
    _params: dict[str, str],
) -> ApiResponse:
    limit = int(request.first("limit", "12"))
    return ApiResponse({"events": context.watering_events(limit)})


def calibrate_main(
    context: ApiContext,
    request: ApiRequest,
    _params: dict[str, str],
) -> ApiResponse:
    payload: dict[str, Any] = request.json()
    result = context.calibrate_main_pump(payload["measured_level_percent"])
    return ApiResponse({"calibration": result, **context.get_state()})


def calibrate_refill(
    context: ApiContext,
    request: ApiRequest,
    _params: dict[str, str],
) -> ApiResponse:
    payload: dict[str, Any] = request.json()
    result = context.calibrate_refill_pump(payload["measured_level_percent"])
    return ApiResponse({"calibration": result, **context.get_state()})


def mark_refill_run(
    context: ApiContext,
    request: ApiRequest,
    _params: dict[str, str],
) -> ApiResponse:
    payload: dict[str, Any] = request.json()
    refill = context.mark_refill_run(
        source=str(payload.get("source", "home_assistant")),
        run_id=payload.get("run_id"),
    )
    return ApiResponse(
        {
            "accepted": True,
            "message": "Nachf\u00fclllauf wurde verbucht.",
            "refill": refill,
            **context.get_state(),
        }
    )


def fill_main(
    context: ApiContext,
    _request: ApiRequest,
    _params: dict[str, str],
) -> ApiResponse:
    context.fill_tank("main")
    return ApiResponse(context.get_state())


def fill_refill(
    context: ApiContext,
    _request: ApiRequest,
    _params: dict[str, str],
) -> ApiResponse:
    context.fill_tank("refill")
    return ApiResponse(context.get_state())


def register(router: Router) -> None:
    router.get("/api/watering-events", watering_events)
    router.post("/api/calibration/main", calibrate_main)
    router.post("/api/calibration/refill", calibrate_refill)
    router.post("/api/refill/mark-run", mark_refill_run)
    router.post("/api/tanks/main/fill", fill_main)
    router.post("/api/tanks/refill/fill", fill_refill)
