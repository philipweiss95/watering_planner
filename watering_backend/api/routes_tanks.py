from __future__ import annotations

from http import HTTPStatus
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


def start_refill_run(
    context: ApiContext,
    request: ApiRequest,
    _params: dict[str, str],
) -> ApiResponse:
    payload: dict[str, Any] = request.json()
    refill = context.start_refill_run(
        run_type=str(payload.get("run_type", "automatic")),
        run_id=payload.get("run_id"),
        source=str(payload.get("source", "home_assistant")),
    )
    return ApiResponse(
        {"accepted": True, "refill_run": refill},
        (
            HTTPStatus.OK
            if refill.get("idempotent_replay")
            else HTTPStatus.CREATED
        ),
    )


def mark_refill_running(
    context: ApiContext,
    request: ApiRequest,
    _params: dict[str, str],
) -> ApiResponse:
    payload: dict[str, Any] = request.json()
    return ApiResponse(
        {
            "accepted": True,
            "refill_run": context.mark_refill_running(
                payload.get("run_id")
            ),
        }
    )


def complete_refill_run(
    context: ApiContext,
    request: ApiRequest,
    _params: dict[str, str],
) -> ApiResponse:
    payload: dict[str, Any] = request.json()
    refill = context.complete_refill_run(
        payload.get("run_id"),
        completion_reason=str(
            payload.get("completion_reason", "pump_stopped")
        ),
    )
    return ApiResponse(
        {
            "accepted": True,
            "message": "Nachfülllauf wurde abgeschlossen.",
            "refill_run": refill,
            **context.get_state(),
        }
    )


def fail_refill_run(
    context: ApiContext,
    request: ApiRequest,
    _params: dict[str, str],
) -> ApiResponse:
    payload: dict[str, Any] = request.json()
    may_have_transferred = payload.get("may_have_transferred")
    if (
        may_have_transferred is not None
        and not isinstance(may_have_transferred, bool)
    ):
        raise ValueError("may_have_transferred muss boolesch sein")
    return ApiResponse(
        {
            "accepted": True,
            "refill_run": context.fail_refill_run(
                payload.get("run_id"),
                error=payload.get("error", ""),
                may_have_transferred=may_have_transferred,
            ),
        }
    )


def get_refill_run(
    context: ApiContext,
    _request: ApiRequest,
    params: dict[str, str],
) -> ApiResponse:
    return ApiResponse(
        {"refill_run": context.get_refill_run(params["run_id"])}
    )


def reconcile_refill_run(
    context: ApiContext,
    request: ApiRequest,
    params: dict[str, str],
) -> ApiResponse:
    payload: dict[str, Any] = request.json()
    refill = context.reconcile_refill_run(
        params["run_id"],
        mode=payload.get("mode"),
        measured_transfer_ml=payload.get("measured_transfer_ml"),
        main_tank_current_ml=payload.get("main_tank_current_ml"),
        refill_tank_current_ml=payload.get(
            "refill_tank_current_ml"
        ),
        note=payload.get("note", ""),
    )
    return ApiResponse(
        {
            "accepted": True,
            "message": "Nachfülllauf wurde manuell abgeglichen.",
            "refill_run": refill,
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
    router.post("/api/refill/start", start_refill_run)
    router.post("/api/refill/running", mark_refill_running)
    router.post("/api/refill/complete", complete_refill_run)
    router.post("/api/refill/fail", fail_refill_run)
    router.get("/api/refill/runs/{run_id}", get_refill_run)
    router.post(
        "/api/refill/runs/{run_id}/reconcile",
        reconcile_refill_run,
    )
    router.post("/api/refill/mark-run", mark_refill_run)
    router.post("/api/tanks/main/fill", fill_main)
    router.post("/api/tanks/refill/fill", fill_refill)
