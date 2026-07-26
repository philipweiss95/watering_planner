from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


Opener = Callable[..., Any]


@dataclass(frozen=True)
class UpdaterService:
    updater_url: str
    token_file: Path
    opener: Opener = urlopen

    def __post_init__(self) -> None:
        object.__setattr__(self, "updater_url", str(self.updater_url).rstrip("/"))
        object.__setattr__(self, "token_file", Path(self.token_file))

    def updater_token(self) -> str:
        try:
            return self.token_file.read_text(encoding="utf-8").strip()
        except OSError:
            return ""

    def updater_request(
        self,
        path: str,
        method: str = "GET",
        payload: dict[str, Any] | None = None,
        timeout: int = 30,
    ) -> dict[str, Any]:
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        headers = {"Accept": "application/json"}
        token = self.updater_token()
        if data is not None:
            headers["Content-Type"] = "application/json"
        if token:
            headers["X-Watering-Planner-Updater-Token"] = token
        request = Request(
            f"{self.updater_url}{path}",
            data=data,
            headers=headers,
            method=method,
        )
        try:
            with self.opener(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            try:
                reason = json.loads(exc.read().decode("utf-8")).get("reason")
            except (ValueError, AttributeError):
                reason = None
            raise ValueError(reason or f"updater_http_{exc.code}") from exc
        except (URLError, TimeoutError) as exc:
            raise ValueError("updater_unavailable") from exc

    # Short aliases keep composition code concise while the legacy method names
    # remain available to compatibility adapters.
    def token(self) -> str:
        return self.updater_token()

    def request(
        self,
        path: str,
        method: str = "GET",
        payload: dict[str, Any] | None = None,
        timeout: int = 30,
    ) -> dict[str, Any]:
        return self.updater_request(path, method, payload, timeout)
