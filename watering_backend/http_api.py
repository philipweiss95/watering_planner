from __future__ import annotations

from http.server import ThreadingHTTPServer
from typing import Callable


def serve(
    host: str,
    port: int,
    handler,
    *,
    on_start: Callable[[], object] | None = None,
    on_stop: Callable[[], object] | None = None,
) -> None:
    httpd = ThreadingHTTPServer((host, port), handler)
    if on_start:
        on_start()
    try:
        print(f"Bewaesserungsplaner laeuft auf http://{host}:{port}")
        httpd.serve_forever()
    finally:
        if on_stop:
            on_stop()
        httpd.server_close()
