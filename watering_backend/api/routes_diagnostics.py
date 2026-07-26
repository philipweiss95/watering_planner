from __future__ import annotations

from http import HTTPStatus

from watering_backend.api.responses import ApiResponse, error_response
from watering_backend.api.router import ApiContext, ApiRequest, Router


def _truthy(value: str) -> bool:
    return value.lower() in {"1", "true", "yes", "ja", "on"}


def notification_diagnostics(
    context: ApiContext,
    _request: ApiRequest,
    _params: dict[str, str],
) -> ApiResponse:
    return ApiResponse(
        {
            **context.notification_diagnostics(),
            "weather": context.weather_diagnostics(),
            "worker_running": context.notification_worker_running(),
        }
    )


def home_assistant_diagnostics(
    context: ApiContext,
    _request: ApiRequest,
    _params: dict[str, str],
) -> ApiResponse:
    return ApiResponse(context.home_assistant_diagnostics())


def weather(
    context: ApiContext,
    request: ApiRequest,
    _params: dict[str, str],
) -> ApiResponse:
    try:
        balcony = context.get_state()["balcony"]
        force = _truthy(request.first("force", "false"))
        weather_payload = context.fetch_weather(balcony, force=force)
        if _truthy(request.first("evaluate", "false")):
            evaluation = context.evaluate_weather(
                weather_payload,
                request.first("slot", "morning"),
            )
            return ApiResponse(
                {
                    "weather": weather_payload,
                    "evaluation": evaluation,
                }
            )
        return ApiResponse(weather_payload)
    except ValueError as exc:
        return error_response(str(exc), HTTPStatus.BAD_GATEWAY)


def test_notification(
    context: ApiContext,
    _request: ApiRequest,
    _params: dict[str, str],
) -> ApiResponse:
    return ApiResponse(context.send_test_notification())


def check_notifications(
    context: ApiContext,
    _request: ApiRequest,
    _params: dict[str, str],
) -> ApiResponse:
    return ApiResponse(
        {
            "checks": context.run_notification_check(),
            **context.notification_diagnostics(),
        }
    )


def test_home_assistant(
    context: ApiContext,
    _request: ApiRequest,
    _params: dict[str, str],
) -> ApiResponse:
    result = context.test_home_assistant_connection()
    status = HTTPStatus.OK if result.get("reachable") else HTTPStatus.SERVICE_UNAVAILABLE
    return ApiResponse(result, status)


def register(router: Router) -> None:
    router.get("/api/diagnostics/notifications", notification_diagnostics)
    router.get("/api/diagnostics/home-assistant", home_assistant_diagnostics)
    router.get("/api/weather", weather)
    router.post("/api/notifications/test", test_notification)
    router.post("/api/diagnostics/notifications/check", check_notifications)
    router.post("/api/diagnostics/home-assistant/test", test_home_assistant)
