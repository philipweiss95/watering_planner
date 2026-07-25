from __future__ import annotations

from http import HTTPStatus
from typing import Any

from watering_backend.api.responses import ApiResponse
from watering_backend.api.router import ApiContext, ApiRequest, Router


def health(
    context: ApiContext,
    _request: ApiRequest,
    _params: dict[str, str],
) -> ApiResponse:
    return ApiResponse({"ok": True, "version": context.app_version})


def state(
    context: ApiContext,
    _request: ApiRequest,
    _params: dict[str, str],
) -> ApiResponse:
    return ApiResponse(context.get_state())


def save_settings(
    context: ApiContext,
    request: ApiRequest,
    _params: dict[str, str],
) -> ApiResponse:
    config = context.save_planner_config(request.json())
    return ApiResponse({"planner_config": config})


def save_balcony(
    context: ApiContext,
    request: ApiRequest,
    _params: dict[str, str],
) -> ApiResponse:
    payload: dict[str, Any] = request.json()
    context.save_balcony(payload)
    return ApiResponse(context.get_state())


def evaluate(
    context: ApiContext,
    request: ApiRequest,
    _params: dict[str, str],
) -> ApiResponse:
    payload: dict[str, Any] = request.json()
    weather = context.weather_from_payload(payload)
    return ApiResponse(context.evaluate_weather(weather, str(payload.get("slot", "morning"))))


def register(router: Router) -> None:
    router.get("/api/health", health)
    router.get("/api/state", state)
    router.post("/api/settings", save_settings)
    router.post("/api/balcony", save_balcony)
    router.post("/api/evaluate", evaluate)
