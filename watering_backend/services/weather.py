from __future__ import annotations

import json
import ssl
import threading
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Protocol
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import urlopen

from watering_backend.config import local_timezone
from watering_backend.weather import (
    manual_simulation,
    normalize_daily_forecast,
    number_or_default as normalized_number_or_default,
)


DEFAULT_LATITUDE = 52.52
DEFAULT_LONGITUDE = 13.405
DEFAULT_TIMEZONE = "Europe/Berlin"
DEFAULT_FORECAST_DAYS = 16
DEFAULT_FAILURE_RETRY_MINUTES = 5

GetSetting = Callable[[str, str], str]
SetSetting = Callable[[str, str], None]
PlannerConfigProvider = Callable[[], Mapping[str, Any]]
BalconyProvider = Callable[[], dict[str, Any]]
Clock = Callable[[], datetime]
Evaluator = Callable[..., dict[str, Any]]
DepletionForecaster = Callable[[dict[str, Any], dict[str, Any] | None], dict[str, Any]]
ManualRefillEvaluator = Callable[[dict[str, Any]], dict[str, Any]]
WeatherFetcher = Callable[[dict[str, Any]], dict[str, Any]]


class Lock(Protocol):
    def __enter__(self) -> Any: ...

    def __exit__(self, exc_type, exc_value, traceback) -> None: ...


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _weather_cache_key(
    latitude: float,
    longitude: float,
    timezone_name: str,
) -> dict[str, Any]:
    return {
        "latitude": round(latitude, 6),
        "longitude": round(longitude, 6),
        "timezone": timezone_name,
    }


def weather_age_minutes(
    weather: Mapping[str, Any],
    *,
    now: datetime | None = None,
) -> float | None:
    try:
        fetched = datetime.fromisoformat(str(weather.get("fetched_at", "")))
        if fetched.tzinfo is None:
            fetched = fetched.replace(tzinfo=timezone.utc)
        current = _as_utc(now or utc_now())
        return max(
            0,
            (current - fetched.astimezone(timezone.utc)).total_seconds() / 60,
        )
    except (TypeError, ValueError):
        return None


def daily_forecast_items(
    daily: dict[str, Any],
    current: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    del current
    return normalize_daily_forecast(daily)


def indexed_value(
    payload: Mapping[str, Any],
    key: str,
    index: int,
    default: float,
) -> Any:
    value = payload.get(key, default)
    if isinstance(value, list):
        if index < len(value) and value[index] is not None:
            return value[index]
        return default
    return default if value is None else value


def number_or_default(value: Any, default: float) -> float:
    return normalized_number_or_default(value, default)


def indexed_number(
    payload: Mapping[str, Any],
    key: str,
    index: int,
    default: float,
) -> float:
    return number_or_default(indexed_value(payload, key, index, default), default)


def first_value(payload: Mapping[str, Any], key: str, default: float) -> float:
    value = payload.get(key, default)
    if isinstance(value, list):
        return value[0] if value and value[0] is not None else default
    return default if value is None else value


def first(
    params: Mapping[str, list[str]],
    name: str,
    default: str,
) -> str:
    values = params.get(name)
    return values[0] if values else default


def truthy(value: str) -> bool:
    return value.lower() in {"1", "true", "yes", "ja", "on"}


def current_weather_or_params(
    params: dict[str, list[str]] | dict[str, Any],
    balcony: dict[str, Any],
    fetcher: WeatherFetcher,
) -> dict[str, Any]:
    auto = truthy(first(params, "auto", "false")) if isinstance(params, dict) else False
    has_manual = any(name in params for name in ["temperature_c", "rain_mm", "wind_kmh"])
    if auto or not has_manual:
        return fetcher(balcony)
    return {
        "source": "manual",
        "mode": "simulation",
        "simulation": True,
        "temperature_c": float(first(params, "temperature_c", "20")),
        "rain_mm": float(first(params, "rain_mm", "0")),
        "wind_kmh": float(first(params, "wind_kmh", "0")),
        "sunshine_hours": float(first(params, "sunshine_hours", "0")),
        "et0_mm": float(first(params, "et0_mm", "0")),
    }


def weather_from_query(
    params: dict[str, list[str]],
    balcony: dict[str, Any],
    fetcher: WeatherFetcher,
) -> dict[str, Any]:
    return current_weather_or_params(params, balcony, fetcher)


def weather_from_payload(
    payload: dict[str, Any],
    balcony: dict[str, Any],
    fetcher: WeatherFetcher,
) -> dict[str, Any]:
    auto = bool(payload.get("auto_weather")) or not {
        "temperature_c",
        "rain_mm",
    }.intersection(payload)
    if auto:
        return fetcher(balcony)
    return manual_simulation(payload)


def evaluate_weather(
    weather: dict[str, Any],
    evaluator: Evaluator,
    depletion_forecaster: DepletionForecaster,
    manual_refill_evaluator: ManualRefillEvaluator,
    slot: str = "morning",
) -> dict[str, Any]:
    planning_weather = (
        weather.get("planning_day")
        if weather.get("source") == "open-meteo"
        else weather
    )
    if not isinstance(planning_weather, dict):
        planning_weather = weather
    result = evaluator(
        temperature_c=float(planning_weather["temperature_c"]),
        rain_mm=float(planning_weather["rain_mm"]),
        wind_kmh=float(planning_weather.get("wind_kmh", 0)),
        slot=slot,
        sunshine_hours=(
            float(planning_weather["sunshine_hours"])
            if planning_weather.get("sunshine_hours") is not None
            else None
        ),
        weather_source=str(weather.get("source", "manual")),
        et0_mm=float(planning_weather.get("et0_mm", 0) or 0),
    )
    result["weather"] = weather
    result["weather"]["planning_values_used"] = dict(planning_weather)
    result["depletion"] = depletion_forecaster(result, weather)
    result["manual_refill"] = manual_refill_evaluator(result)
    return result


@dataclass
class WeatherService:
    get_setting: GetSetting
    set_setting: SetSetting
    planner_config_provider: PlannerConfigProvider
    balcony_provider: BalconyProvider
    evaluator: Evaluator | None = None
    depletion_forecaster: DepletionForecaster | None = None
    manual_refill_evaluator: ManualRefillEvaluator | None = None
    clock: Clock = utc_now
    opener: Callable[..., Any] = urlopen
    fetch_lock: Lock = field(default_factory=threading.Lock)
    forecast_days: int = DEFAULT_FORECAST_DAYS
    failure_retry_minutes: int = DEFAULT_FAILURE_RETRY_MINUTES
    request_timeout_seconds: int = 8
    default_latitude: float = DEFAULT_LATITUDE
    default_longitude: float = DEFAULT_LONGITUDE

    def now_utc(self) -> datetime:
        return _as_utc(self.clock())

    def now_iso(self) -> str:
        return self.now_utc().isoformat()

    def planner_config(self) -> dict[str, Any]:
        return dict(self.planner_config_provider())

    def _read_weather_cache(
        self,
        cache_key: dict[str, Any],
    ) -> dict[str, Any] | None:
        raw = self.get_setting("weather_cache", "")
        if not raw:
            return None
        try:
            cached = json.loads(raw)
        except json.JSONDecodeError:
            return None
        if not isinstance(cached, dict) or cached.get("cache_key") != cache_key:
            return None
        result = cached.get("weather")
        return deepcopy(result) if isinstance(result, dict) else None

    def _weather_age_minutes(self, weather: Mapping[str, Any]) -> float | None:
        return weather_age_minutes(weather, now=self.now_utc())

    def _cached_weather(
        self,
        cache_key: dict[str, Any],
        max_age_minutes: int,
    ) -> dict[str, Any] | None:
        cached = self._read_weather_cache(cache_key)
        age = self._weather_age_minutes(cached or {})
        if cached is None or age is None or age > max_age_minutes:
            return None
        cached["cache_hit"] = True
        cached["cache_fallback"] = False
        cached["data_age_minutes"] = round(age, 1)
        return cached

    def _failed_fetch_fallback(
        self,
        cache_key: dict[str, Any],
        stale_after_minutes: int,
    ) -> dict[str, Any] | None:
        error = self.get_setting("last_weather_fetch_error", "")
        if not error:
            return None
        cached = self._cached_weather(
            cache_key,
            max(stale_after_minutes, 24 * 60),
        )
        if not cached:
            return None
        cached["cache_fallback"] = True
        cached["weather_error"] = error
        cached["stale"] = bool(
            float(cached.get("data_age_minutes", 0))
            > stale_after_minutes
        )
        return cached

    def _recent_fetch_failed(self) -> bool:
        if not self.get_setting("last_weather_fetch_error", ""):
            return False
        try:
            attempted = datetime.fromisoformat(
                self.get_setting("last_weather_fetch_attempt_at", "")
            )
        except (TypeError, ValueError):
            return False
        return (
            self.now_utc() - _as_utc(attempted)
        ).total_seconds() < max(1, self.failure_retry_minutes) * 60

    def fetch_weather(
        self,
        balcony: dict[str, Any],
        force: bool = False,
    ) -> dict[str, Any]:
        latitude = float(balcony.get("latitude", self.default_latitude))
        longitude = float(balcony.get("longitude", self.default_longitude))
        timezone_name = str(balcony.get("timezone_name") or DEFAULT_TIMEZONE)
        config = self.planner_config()
        cache_key = _weather_cache_key(latitude, longitude, timezone_name)
        cache_minutes = int(config["weather_cache_minutes"])
        if not force:
            cached = self._cached_weather(cache_key, cache_minutes)
            if cached:
                return cached
            if self._recent_fetch_failed():
                fallback = self._failed_fetch_fallback(
                    cache_key,
                    int(config["weather_stale_after_minutes"]),
                )
                if fallback:
                    return fallback
        with self.fetch_lock:
            if not force:
                cached = self._cached_weather(cache_key, cache_minutes)
                if cached:
                    return cached
                if self._recent_fetch_failed():
                    fallback = self._failed_fetch_fallback(
                        cache_key,
                        int(config["weather_stale_after_minutes"]),
                    )
                    if fallback:
                        return fallback
            return self._fetch_weather_uncached(
                latitude,
                longitude,
                timezone_name,
                cache_key,
                int(config["weather_stale_after_minutes"]),
            )

    def _fetch_weather_uncached(
        self,
        latitude: float,
        longitude: float,
        timezone_name: str,
        cache_key: dict[str, Any],
        stale_after_minutes: int,
    ) -> dict[str, Any]:
        params = {
            "latitude": latitude,
            "longitude": longitude,
            "timezone": timezone_name,
            "current": "temperature_2m,precipitation,rain,wind_speed_10m",
            "hourly": "temperature_2m,precipitation,rain,wind_speed_10m",
            "daily": (
                "precipitation_sum,temperature_2m_max,sunshine_duration,"
                "et0_fao_evapotranspiration,wind_speed_10m_max"
            ),
            "forecast_days": self.forecast_days,
        }
        url = "https://api.open-meteo.com/v1/forecast?" + urlencode(params)
        attempted_at = self.now_iso()
        self.set_setting("last_weather_fetch_attempt_at", attempted_at)
        try:
            with self.opener(url, timeout=self.request_timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (OSError, URLError, TimeoutError, json.JSONDecodeError) as exc:
            certificate_error = isinstance(exc, ssl.SSLCertVerificationError) or (
                isinstance(exc, URLError)
                and isinstance(exc.reason, ssl.SSLCertVerificationError)
            )
            error = (
                "TLS-Zertifikat des Wetterdienstes konnte nicht bestaetigt werden."
                if certificate_error
                else "Wetterdienst ist voruebergehend nicht erreichbar."
            )
            self.set_setting("last_weather_fetch_error", error)
            cached = self._failed_fetch_fallback(
                cache_key,
                stale_after_minutes,
            )
            if cached:
                return cached
            raise ValueError(error) from exc

        current = payload.get("current", {})
        hourly = payload.get("hourly", {})
        daily = payload.get("daily", {})
        forecast = daily_forecast_items(daily)
        planning_day = (
            forecast[0]
            if forecast
            else {
                "date": self.now_utc()
                .astimezone(local_timezone(timezone_name))
                .date()
                .isoformat(),
                "temperature_c": 20.0,
                "rain_mm": 0.0,
                "wind_kmh": 0.0,
                "sunshine_hours": 0.0,
                "et0_mm": 0.0,
            }
        )
        fetched_at = self.now_iso()
        result = {
            "source": "open-meteo",
            "mode": "forecast",
            "simulation": False,
            "latitude": latitude,
            "longitude": longitude,
            "timezone": timezone_name,
            "temperature_c": planning_day["temperature_c"],
            "rain_mm": planning_day["rain_mm"],
            "wind_kmh": planning_day["wind_kmh"],
            "sunshine_hours": planning_day["sunshine_hours"],
            "et0_mm": planning_day["et0_mm"],
            "current_rain_mm": number_or_default(
                current.get("rain", current.get("precipitation")),
                0,
            ),
            "current": {
                "time": current.get("time"),
                "temperature_c": number_or_default(current.get("temperature_2m"), 20),
                "rain_mm": number_or_default(
                    current.get("rain", current.get("precipitation")),
                    0,
                ),
                "precipitation_mm": number_or_default(current.get("precipitation"), 0),
                "wind_kmh": number_or_default(current.get("wind_speed_10m"), 0),
            },
            "hourly": {
                "time": hourly.get("time", []),
                "temperature_c": hourly.get("temperature_2m", []),
                "precipitation_mm": hourly.get("precipitation", []),
                "rain_mm": hourly.get("rain", []),
                "wind_kmh": hourly.get("wind_speed_10m", []),
            },
            "planning_day": planning_day,
            "forecast": forecast,
            "tls_verified": True,
            "fetched_at": fetched_at,
            "cache_hit": False,
            "cache_fallback": False,
            "data_age_minutes": 0,
            "stale": False,
        }
        self.set_setting("last_successful_weather_fetch_at", fetched_at)
        self.set_setting("last_weather_fetch_error", "")
        self.set_setting(
            "weather_cache",
            json.dumps(
                {"cache_key": cache_key, "weather": result},
                ensure_ascii=False,
            ),
        )
        return result

    def current_weather_or_params(
        self,
        params: dict[str, list[str]] | dict[str, Any],
        balcony: dict[str, Any],
    ) -> dict[str, Any]:
        return current_weather_or_params(params, balcony, self.fetch_weather)

    def weather_from_query(
        self,
        params: dict[str, list[str]],
    ) -> dict[str, Any]:
        balcony = self.balcony_provider()
        return weather_from_query(params, balcony, self.fetch_weather)

    def weather_from_payload(
        self,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        balcony = self.balcony_provider()
        return weather_from_payload(payload, balcony, self.fetch_weather)

    def evaluate_weather(
        self,
        weather: dict[str, Any],
        slot: str = "morning",
    ) -> dict[str, Any]:
        if (
            self.evaluator is None
            or self.depletion_forecaster is None
            or self.manual_refill_evaluator is None
        ):
            raise RuntimeError("Weather evaluation dependencies are not configured")
        return evaluate_weather(
            weather,
            self.evaluator,
            self.depletion_forecaster,
            self.manual_refill_evaluator,
            slot,
        )
