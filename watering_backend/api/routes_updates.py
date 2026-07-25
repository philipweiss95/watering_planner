from __future__ import annotations

from http import HTTPStatus
from typing import Any

from watering_backend.api.responses import ApiResponse, error_response
from watering_backend.api.router import ApiContext, ApiRequest, Router


def update_status(
    context: ApiContext,
    _request: ApiRequest,
    _params: dict[str, str],
) -> ApiResponse:
    try:
        return ApiResponse(context.updater_request("/api/status"))
    except ValueError as exc:
        return error_response(str(exc), HTTPStatus.SERVICE_UNAVAILABLE)


def update_setup(
    context: ApiContext,
    request: ApiRequest,
    _params: dict[str, str],
) -> ApiResponse:
    return ApiResponse(context.updater_request("/api/setup", "POST", request.json()))


def update_check(
    context: ApiContext,
    _request: ApiRequest,
    _params: dict[str, str],
) -> ApiResponse:
    return ApiResponse(
        context.updater_request(
            "/api/check",
            "POST",
            {"currentVersion": context.app_version},
        )
    )


def update_install(
    context: ApiContext,
    request: ApiRequest,
    _params: dict[str, str],
) -> ApiResponse:
    payload: dict[str, Any] = request.json()
    if payload.get("confirm") is not True:
        return error_response("update_install_confirmation_required", HTTPStatus.CONFLICT)
    return ApiResponse(
        context.updater_request(
            "/api/install",
            "POST",
            {"currentVersion": context.app_version},
        ),
        HTTPStatus.ACCEPTED,
    )


def register(router: Router) -> None:
    router.get("/api/update/status", update_status)
    router.post("/api/update/setup", update_setup)
    router.post("/api/update/check", update_check)
    router.post("/api/update/install", update_install)
