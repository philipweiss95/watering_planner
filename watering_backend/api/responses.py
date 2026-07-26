from __future__ import annotations

import json
from dataclasses import dataclass
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler
from typing import Any


@dataclass(frozen=True)
class ApiResponse:
    payload: Any
    status: HTTPStatus | int = HTTPStatus.OK


def read_json(handler: SimpleHTTPRequestHandler) -> Any:
    length = int(handler.headers.get("Content-Length", "0"))
    if length == 0:
        return {}
    raw = handler.rfile.read(length).decode("utf-8")
    return json.loads(raw)


def send_json(
    handler: SimpleHTTPRequestHandler,
    payload: Any,
    status: HTTPStatus | int = HTTPStatus.OK,
) -> None:
    body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def error_response(message: str, status: HTTPStatus | int) -> ApiResponse:
    return ApiResponse({"error": message}, status)
