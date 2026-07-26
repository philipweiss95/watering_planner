from __future__ import annotations

import json
from urllib.error import URLError
from urllib.request import Request


def post_webhook(url: str, payload: dict, opener, timeout: int = 5) -> None:
    request = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with opener(request, timeout=timeout) as response:
            response.read()
    except (OSError, URLError, TimeoutError) as exc:
        raise ValueError(f"Home Assistant konnte nicht erreicht werden: {exc}") from exc
