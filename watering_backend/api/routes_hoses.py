from __future__ import annotations

from watering_backend.api.responses import ApiResponse
from watering_backend.api.router import ApiContext, ApiRequest, Router


def save_hoses(
    context: ApiContext,
    request: ApiRequest,
    _params: dict[str, str],
) -> ApiResponse:
    context.save_hoses(request.json())
    return ApiResponse(context.get_state())


def register(router: Router) -> None:
    router.post("/api/hoses", save_hoses)
