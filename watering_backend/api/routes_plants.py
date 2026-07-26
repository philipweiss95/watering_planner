from __future__ import annotations

from http import HTTPStatus
from typing import Any

from watering_backend.api.responses import ApiResponse
from watering_backend.api.router import ApiContext, ApiRequest, Router


def add_plant(
    context: ApiContext,
    request: ApiRequest,
    _params: dict[str, str],
) -> ApiResponse:
    payload: dict[str, Any] = request.json()
    plant_id = context.add_plant(payload)
    return ApiResponse({"id": plant_id, **context.get_state()}, HTTPStatus.CREATED)


def update_plant(
    context: ApiContext,
    request: ApiRequest,
    params: dict[str, str],
) -> ApiResponse:
    context.update_plant(int(params["plant_id"]), request.json())
    return ApiResponse(context.get_state())


def update_position(
    context: ApiContext,
    request: ApiRequest,
    params: dict[str, str],
) -> ApiResponse:
    context.update_plant_position(int(params["plant_id"]), request.json())
    return ApiResponse(context.get_state())


def delete_plant(
    context: ApiContext,
    _request: ApiRequest,
    params: dict[str, str],
) -> ApiResponse:
    context.delete_plant(int(params["plant_id"]))
    return ApiResponse(context.get_state())


def register(router: Router) -> None:
    router.post("/api/plants", add_plant)
    router.post("/api/plants/{plant_id}/position", update_position)
    router.put("/api/plants/{plant_id}", update_plant)
    router.delete("/api/plants/{plant_id}", delete_plant)
