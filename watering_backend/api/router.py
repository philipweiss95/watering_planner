from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from http import HTTPStatus
from typing import Any, Callable, Mapping, Protocol
from urllib.parse import parse_qs, urlparse

from watering_backend.api.responses import ApiResponse, error_response


class ApiContext(Protocol):
    """Application operations used by HTTP routes.

    Implementations compose repositories and services. Route modules depend on
    this protocol rather than importing the application orchestrator.
    """

    @property
    def app_version(self) -> str: ...

    def get_state(self) -> dict[str, Any]: ...

    def save_planner_config(self, value: object) -> dict[str, Any]: ...

    def save_balcony(self, payload: dict[str, Any]) -> None: ...

    def add_plant(self, payload: dict[str, Any]) -> int: ...

    def update_plant(self, plant_id: int, payload: dict[str, Any]) -> None: ...

    def update_plant_position(self, plant_id: int, payload: dict[str, Any]) -> None: ...

    def delete_plant(self, plant_id: int) -> None: ...

    def save_hoses(self, payload: dict[str, Any]) -> None: ...

    def fetch_weather(self, balcony: dict[str, Any], force: bool = False) -> dict[str, Any]: ...

    def weather_from_query(self, params: dict[str, list[str]]) -> dict[str, Any]: ...

    def weather_from_payload(self, payload: dict[str, Any]) -> dict[str, Any]: ...

    def evaluate_weather(self, weather: dict[str, Any], slot: str = "morning") -> dict[str, Any]: ...

    def watering_events(self, limit: int = 12) -> list[dict[str, Any]]: ...

    def shortcut_blueprint(self, base_url: str) -> dict[str, Any]: ...

    def calibrate_main_pump(self, measured_level_percent: object) -> dict[str, Any]: ...

    def calibrate_refill_pump(self, measured_level_percent: object) -> dict[str, Any]: ...

    def mark_run(
        self,
        delivered_ml: int,
        temperature_c: float,
        rain_mm: float,
        run_id: object = None,
        source: str = "home_assistant",
    ) -> dict[str, Any]: ...

    def mark_refill_run(
        self,
        source: str = "home_assistant",
        run_id: object = None,
    ) -> dict[str, Any]: ...

    def start_refill_run(
        self,
        *,
        run_type: str,
        run_id: object,
        source: str = "home_assistant",
    ) -> dict[str, Any]: ...

    def mark_refill_running(self, run_id: object) -> dict[str, Any]: ...

    def complete_refill_run(
        self,
        run_id: object,
        *,
        completion_reason: str = "pump_stopped",
    ) -> dict[str, Any]: ...

    def fail_refill_run(
        self,
        run_id: object,
        *,
        error: object = "",
        may_have_transferred: bool | None = None,
    ) -> dict[str, Any]: ...

    def get_refill_run(self, run_id: object) -> dict[str, Any]: ...

    def reconcile_refill_run(
        self,
        run_id: object,
        **payload: object,
    ) -> dict[str, Any]: ...

    def trigger_home_assistant_manual_run(
        self,
        result: dict[str, Any],
        run_id: object = None,
    ) -> str: ...

    def trigger_home_assistant_manual_refill(
        self,
        result: dict[str, Any],
        run_id: object = None,
    ) -> str: ...

    def fill_tank(self, tank_name: str) -> None: ...

    def notification_diagnostics(self) -> dict[str, Any]: ...

    def weather_diagnostics(self) -> dict[str, Any]: ...

    def notification_worker_running(self) -> bool: ...

    def send_test_notification(self) -> dict[str, Any]: ...

    def run_notification_check(self) -> list[dict[str, Any]]: ...

    def home_assistant_diagnostics(self) -> dict[str, Any]: ...

    def test_home_assistant_connection(self) -> dict[str, Any]: ...

    def updater_request(
        self,
        path: str,
        method: str = "GET",
        payload: dict[str, Any] | None = None,
        timeout: int = 30,
    ) -> dict[str, Any]: ...

    def local_now(self, timezone_name: str) -> datetime: ...

    def set_setting(self, key: str, value: str) -> None: ...

    def delete_setting(self, key: str) -> None: ...


JsonLoader = Callable[[], Any]
RouteHandler = Callable[[ApiContext, "ApiRequest", dict[str, str]], ApiResponse]
_UNREAD = object()
_PATH_PARAMETER = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")
_CLIENT_ERRORS = (ValueError, KeyError, sqlite3.IntegrityError, json.JSONDecodeError)


@dataclass
class ApiRequest:
    method: str
    target: str
    headers: Mapping[str, str] = field(default_factory=dict)
    json_loader: JsonLoader = field(default=lambda: {})
    path: str = field(init=False)
    query: dict[str, list[str]] = field(init=False)
    _json_value: Any = field(default=_UNREAD, init=False, repr=False)

    def __post_init__(self) -> None:
        parsed = urlparse(self.target)
        self.method = self.method.upper()
        self.path = parsed.path
        self.query = parse_qs(parsed.query)

    def json(self) -> Any:
        if self._json_value is _UNREAD:
            self._json_value = self.json_loader()
        return self._json_value

    def first(self, name: str, default: str) -> str:
        values = self.query.get(name)
        return values[0] if values else default


@dataclass(frozen=True)
class Route:
    method: str
    template: str
    handler: RouteHandler
    catch_client_errors: bool
    pattern: re.Pattern[str]

    def match(self, method: str, path: str) -> dict[str, str] | None:
        if method != self.method:
            return None
        matched = self.pattern.fullmatch(path)
        return matched.groupdict() if matched else None


def _compile_template(template: str) -> re.Pattern[str]:
    cursor = 0
    parts: list[str] = []
    for matched in _PATH_PARAMETER.finditer(template):
        parts.append(re.escape(template[cursor : matched.start()]))
        parts.append(f"(?P<{matched.group(1)}>[^/]+)")
        cursor = matched.end()
    parts.append(re.escape(template[cursor:]))
    return re.compile("".join(parts))


class Router:
    def __init__(self) -> None:
        self._routes: list[Route] = []

    @property
    def routes(self) -> tuple[Route, ...]:
        return tuple(self._routes)

    def add(
        self,
        method: str,
        template: str,
        handler: RouteHandler,
        *,
        catch_client_errors: bool | None = None,
    ) -> None:
        normalized_method = method.upper()
        if catch_client_errors is None:
            catch_client_errors = normalized_method in {"POST", "PUT"}
        self._routes.append(
            Route(
                method=normalized_method,
                template=template,
                handler=handler,
                catch_client_errors=catch_client_errors,
                pattern=_compile_template(template),
            )
        )

    def get(self, template: str, handler: RouteHandler) -> None:
        self.add("GET", template, handler)

    def post(self, template: str, handler: RouteHandler) -> None:
        self.add("POST", template, handler)

    def put(self, template: str, handler: RouteHandler) -> None:
        self.add("PUT", template, handler)

    def delete(self, template: str, handler: RouteHandler) -> None:
        self.add("DELETE", template, handler)

    def dispatch(self, request: ApiRequest, context: ApiContext) -> ApiResponse | None:
        for route in self._routes:
            params = route.match(request.method, request.path)
            if params is None:
                continue
            try:
                return route.handler(context, request, params)
            except _CLIENT_ERRORS as exc:
                if not route.catch_client_errors:
                    raise
                return error_response(str(exc), HTTPStatus.BAD_REQUEST)
        return None


def build_router() -> Router:
    from watering_backend.api import (
        routes_automation,
        routes_diagnostics,
        routes_hoses,
        routes_plants,
        routes_state,
        routes_tanks,
        routes_updates,
    )

    router = Router()
    for route_module in (
        routes_state,
        routes_plants,
        routes_hoses,
        routes_tanks,
        routes_automation,
        routes_diagnostics,
        routes_updates,
    ):
        route_module.register(router)
    return router
