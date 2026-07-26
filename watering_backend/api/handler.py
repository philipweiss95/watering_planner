from __future__ import annotations

from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler
from pathlib import Path
from typing import TypeAlias

from watering_backend.api.responses import read_json, send_json
from watering_backend.api.router import ApiContext, ApiRequest, Router, build_router


class ApiHandler(SimpleHTTPRequestHandler):
    api_context: ApiContext | None = None
    api_router: Router | None = None
    public_directory: str | Path = "."

    def __init__(self, *args, **kwargs):
        kwargs.pop("directory", None)
        super().__init__(*args, directory=str(type(self).public_directory), **kwargs)

    def log_message(self, format: str, *args) -> None:
        print(f"[{self.log_date_time_string()}] {format % args}")

    def end_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "same-origin")
        self.send_header("Permissions-Policy", "geolocation=(self)")
        super().end_headers()

    def _dispatch_api(self, method: str) -> bool:
        router = type(self).api_router
        context = type(self).api_context
        if router is None or context is None:
            raise RuntimeError("API handler is not configured")
        request = ApiRequest(
            method=method,
            target=self.path,
            headers=self.headers,
            json_loader=lambda: read_json(self),
        )
        response = router.dispatch(request, context)
        if response is None:
            return False
        send_json(self, response.payload, response.status)
        return True

    def do_GET(self) -> None:
        if not self._dispatch_api("GET"):
            super().do_GET()

    def do_POST(self) -> None:
        if not self._dispatch_api("POST"):
            send_json(self, {"error": "Not found"}, HTTPStatus.NOT_FOUND)

    def do_PUT(self) -> None:
        if not self._dispatch_api("PUT"):
            send_json(self, {"error": "Not found"}, HTTPStatus.NOT_FOUND)

    def do_DELETE(self) -> None:
        if not self._dispatch_api("DELETE"):
            send_json(self, {"error": "Not found"}, HTTPStatus.NOT_FOUND)


HandlerType: TypeAlias = type[ApiHandler]


def create_handler(
    context: ApiContext,
    public_directory: str | Path,
    router: Router | None = None,
) -> HandlerType:
    class ConfiguredApiHandler(ApiHandler):
        pass

    ConfiguredApiHandler.api_context = context
    ConfiguredApiHandler.api_router = router or build_router()
    ConfiguredApiHandler.public_directory = public_directory
    ConfiguredApiHandler.__name__ = "AppHandler"
    ConfiguredApiHandler.__qualname__ = "AppHandler"
    return ConfiguredApiHandler
