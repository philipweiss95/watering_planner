"""Legacy-compatible facade over the instance-scoped application.

New code should import repositories, services, ``Application`` or the API
router directly. This module intentionally stays small because ``server.py``
aliases it for older integrations that patch module attributes such as
``DATA_DIR``, ``local_now`` or ``urlopen``.
"""

from __future__ import annotations

import os
import sqlite3
from datetime import date, datetime, timezone
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler
from pathlib import Path
from typing import Any
from urllib.request import urlopen

from watering_backend.api import ApiRequest, build_router
from watering_backend.api.responses import read_json, send_json
from watering_backend.app import (
    Application,
    ApplicationPaths,
    create_application,
    utc_now_iso,
)
from watering_backend.catalog import (
    AUTOMATION_DAY_END,
    AUTOMATION_DAY_START,
    AUTOMATION_RUN_COOLDOWN_MINUTES,
    AUTOMATION_TRIGGER_TOLERANCE_MINUTES,
    CONNECTION_DESIGN,
    DEFAULT_BALCONY,
    DEFAULT_WALLS,
    LEGACY_WATER_MODEL_CALIBRATION,
    LEGACY_WATER_MODEL_CALIBRATIONS,
    MAX_WATERING_AMOUNT_PERCENT,
    MAX_WATER_MODEL_CALIBRATION_PERCENT,
    MIN_WATERING_AMOUNT_PERCENT,
    MIN_WATER_MODEL_CALIBRATION_PERCENT,
    PLANT_CATALOG,
    PLANT_WATER_PROFILES,
    PREVIOUS_WATER_MODEL_CALIBRATION,
    REFILL_COOLDOWN_MINUTES_PER_LITER,
    REFILL_MAX_COOLDOWN_MINUTES,
    REFILL_MIN_COOLDOWN_MINUTES,
    REFILL_MIN_INTERVAL_MINUTES,
    REFILL_RUN_TIMES,
    REFILL_TRANSFER_FRACTION,
    REFILL_TRIGGER_TOLERANCE_MINUTES,
    SEASONAL_WATER_CURVES,
    TANK_FORECAST_DAYS,
    TANK_LOW_PERCENT,
    WATER_MODEL_CALIBRATION,
    WEATHER_FORECAST_DAYS,
)
from watering_backend.config import (
    local_day_utc_bounds,
    validate_planner_config,
)
from watering_backend.connections import global_assignments
from watering_backend.forecast import simulate_tanks
from watering_backend.notifications import (
    NotificationService,
    NotificationWorker,
    SMTPConfig,
)
from watering_backend.services.configuration import (
    orientation_name_from_degrees,
)
from watering_backend.services.evaluation import (
    angular_distance,
    calculate_plant_results as pure_calculate_plant_results,
    canopy_size_factor,
    clamp,
    current_tube_count,
    estimate_reference_et0_mm,
    estimate_sun_hours,
    orientation_degrees,
    orientation_exposure_factor,
    orientation_factor,
    plant_canopy_area_m2,
    plant_water_need_ml,
    pot_factor,
    pot_irrigation_efficiency_factor,
    pot_surface_area_m2,
    rain_credit_factor,
    seasonal_curve_value,
    seasonal_profile_key,
    seasonal_water_factor,
    side_center_degrees,
    size_factor,
    solar_position,
    temp_factor,
    wall_distance_m,
    wall_shadow_block,
    wind_exposure_factor,
)
from watering_backend.services.history import (
    format_liters_for_text,
    refill_event_detail,
    tank_label,
)
from watering_backend.services.routing import (
    add_connection_comparison,
    apply_fixed_connection_to_weather,
    choose_tube_assignments,
    configured_connection_plan,
    fallback_single_tube_plan,
    finalize_routing_plan,
    fixed_cycle_score,
    normalize_assignment,
    optimize_routing,
    outlet_delivery_options,
    overwater_tolerance_ml,
    plant_tube_options,
    routing_summary,
    static_connection_summary,
    weather_fixed_plan_summary,
)
from watering_backend.services.scheduling import (
    parse_hhmm,
    parse_pause_until as pure_parse_pause_until,
    window_datetime,
)
from watering_backend.services.weather import (
    daily_forecast_items,
    first,
    first_value,
    indexed_number,
    indexed_value,
    number_or_default,
    truthy,
)
from watering_backend.services.watering import normalize_run_id
from watering_backend.validation import (
    finite_integer,
    finite_number,
    normalize_hose_numbers as validated_hose_numbers,
    validate_outlets,
    validate_plant_payload,
    validate_position_payload,
    validate_walls,
)


ROOT = Path(__file__).parent.parent
PUBLIC_DIR = ROOT / "public"
DATA_DIR = Path(os.environ.get("DATA_DIR", ROOT / "data"))
DB_PATH = Path(os.environ.get("DB_PATH", DATA_DIR / "watering.sqlite3"))
VERSION_PATH = ROOT / "VERSION"
APP_VERSION = (
    VERSION_PATH.read_text(encoding="utf-8").strip()
    if VERSION_PATH.exists()
    else "0.0.0"
)
UPDATER_URL = os.environ.get(
    "UPDATER_URL",
    "http://updater:3188",
).rstrip("/")
UPDATER_TOKEN_FILE = Path(
    os.environ.get(
        "UPDATER_TOKEN_FILE",
        DATA_DIR / ".updater-token",
    )
)

_default_application: Application | None = None
_default_application_key: tuple[str, str, str] | None = None


def local_now(timezone_name: str) -> datetime:
    from watering_backend.services.scheduling import local_now as clock

    return clock(timezone_name)


def now_iso() -> str:
    return utc_now_iso()


def _new_application() -> Application:
    paths = ApplicationPaths(
        root=ROOT,
        public_dir=PUBLIC_DIR,
        data_dir=Path(DATA_DIR),
        database_path=Path(DB_PATH),
        version_path=VERSION_PATH,
        updater_token_file=UPDATER_TOKEN_FILE,
    )
    return create_application(
        paths=paths,
        environment=os.environ,
        local_clock=local_now,
        now_iso=now_iso,
        opener=urlopen,
    )


def _application() -> Application:
    global _default_application, _default_application_key
    key = (str(DATA_DIR), str(DB_PATH), str(UPDATER_TOKEN_FILE))
    if _default_application is None or _default_application_key != key:
        if _default_application is not None:
            _default_application.stop_notification_worker()
        _default_application = _new_application()
        _default_application_key = key
    notification_provider = (
        (lambda: _default_application.notification_transport)
        if notification_service is _DEFAULT_NOTIFICATION_SERVICE
        else notification_service
    )
    weather_provider = (
        (lambda balcony: _default_application.weather.fetch_weather(balcony))
        if fetch_weather is _DEFAULT_FETCH_WEATHER
        else fetch_weather
    )
    projection_provider = (
        None
        if projected_consumption_days is _DEFAULT_PROJECTED_CONSUMPTION_DAYS
        else projected_consumption_days
    )
    _default_application.set_runtime_hooks(
        local_clock=local_now,
        now_iso=now_iso,
        opener=urlopen,
        notification_service=notification_provider,
        notification_weather_fetch=weather_provider,
        projected_consumption=projection_provider,
    )
    return _default_application


def connect():
    return _application().connect()


def ensure_data_dir_writable() -> None:
    _application().ensure_writable()


def init_db() -> None:
    _application().initialize()


def get_setting(key: str, default: str = "") -> str:
    return _application().settings.get(key, default)


def set_setting(key: str, value: str) -> None:
    _application().settings.set(key, value)


def delete_setting(key: str) -> None:
    _application().settings.delete(key)


def planner_config() -> dict[str, Any]:
    return _application().settings.planner_config()


def save_planner_config(value: object) -> dict[str, Any]:
    return _application().settings.save_planner_config(value)


def refill_automation_enabled() -> bool:
    return _application().settings.refill_automation_enabled()


def save_refill_automation_enabled(value: object) -> None:
    _application().settings.save_refill_automation_enabled(value)


def main_pump_calibration_factor() -> float:
    return _application().settings.main_pump_calibration_factor()


def save_main_pump_calibration_factor(value: object) -> None:
    _application().settings.save_main_pump_calibration_factor(value)


def calibrated_consumption_ml(nominal_ml: int | float) -> int:
    return _application().settings.calibrated_consumption_ml(nominal_ml)


def normalize_refill_schedule_times(value: object) -> list[str]:
    return _application().settings.normalize_refill_schedule_times(value)


def refill_schedule_times() -> list[str]:
    return _application().settings.refill_schedule_times()


def save_refill_schedule_times(value: object) -> None:
    _application().settings.save_refill_schedule_times(value)


def refill_cooldown_minutes_per_liter() -> float:
    return _application().settings.refill_cooldown_minutes_per_liter()


def save_refill_cooldown_minutes_per_liter(value: object) -> None:
    _application().settings.save_refill_cooldown_minutes_per_liter(value)


def water_model_calibration() -> float:
    return _application().settings.water_model_calibration()


def save_water_model_calibration_percent(value: object) -> None:
    _application().settings.save_water_model_calibration_percent(value)


def saved_watering_amount_percent() -> float | None:
    return _application().settings.saved_watering_amount_percent()


def watering_amount_percent() -> float:
    return _application().settings.watering_amount_percent()


def save_watering_amount_percent(value: object) -> None:
    _application().settings.save_watering_amount_percent(value)


def parse_hose_numbers(value: object) -> list[str]:
    return validated_hose_numbers(value)


def normalize_hose_numbers(value: object) -> str:
    return ", ".join(validated_hose_numbers(value))


def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any]:
    return dict(row) if row is not None else {}


def irrigation_hoses(
    conn: sqlite3.Connection | None = None,
) -> list[dict[str, Any]]:
    return _application().hoses.list(conn)


def default_outlet_id_for_conn(conn: sqlite3.Connection) -> int:
    return _application().hoses.default_outlet_id(conn)


def default_outlet_id() -> int:
    return _application().hoses.default_outlet_id()


def assign_hoses_to_plant(
    conn: sqlite3.Connection,
    plant_id: int,
    hose_numbers: object,
) -> None:
    _application().hoses.assign_to_plant(conn, plant_id, hose_numbers)


def sync_legacy_plant_connection(
    conn: sqlite3.Connection,
    plant_id: int,
) -> None:
    _application().hoses.sync_legacy_connection(conn, plant_id)


def save_hoses(payload: dict[str, Any]) -> None:
    _application().hoses.save(payload)


def latest_calibration(pump_name: str) -> dict[str, Any] | None:
    return _application().calibration.latest(pump_name)


def calibration_status() -> dict[str, Any]:
    return _application().calibration.status()


def measured_level_ml_from_percent(
    measured_level_percent: int | float,
    capacity_ml: int,
    tank_description: str,
) -> int:
    return _application().calibration.measured_level_ml_from_percent(
        measured_level_percent,
        capacity_ml,
        tank_description,
    )


def calibrate_main_pump(value: object) -> dict[str, Any]:
    return _application().calibration.calibrate_main(value)


def calibrate_refill_pump(value: object) -> dict[str, Any]:
    return _application().calibration.calibrate_refill(value)


def weather_diagnostics() -> dict[str, Any]:
    return _application().diagnostics.weather()


def public_notification_status() -> dict[str, Any]:
    return _application().diagnostics.notification_public_status()


def home_assistant_diagnostics() -> dict[str, Any]:
    return _application().home_assistant.diagnostics()


def test_home_assistant_connection() -> dict[str, Any]:
    return _application().home_assistant.test_connection()


def get_state() -> dict[str, Any]:
    return _application().get_state()


def watering_events(limit: int = 12) -> list[dict[str, Any]]:
    return _application().watering_events(limit)


def completed_cycles_today(
    timezone_name: str = "Europe/Berlin",
) -> int:
    return _application().completed_cycles_today(timezone_name)


def delivered_ml_for_local_date(
    day: date,
    timezone_name: str = "Europe/Berlin",
) -> int:
    return _application().delivered_ml_for_local_date(day, timezone_name)


def refill_event_for_target_date(
    target_date: date,
    window_label: str = "",
) -> dict[str, Any] | None:
    return _application().events.refill_for_target(
        target_date,
        window_label,
    )


def latest_watering_run_at() -> str:
    return _application().events.latest_watering_at()


def calculate_plant_results(*args, **kwargs) -> dict[str, Any]:
    return _application().calculate_plant_results(*args, **kwargs)


def optimize_static_connection_plan(
    balcony: dict[str, Any],
    walls: list[dict[str, Any]],
    plants: list[dict[str, Any]],
    outlets: list[dict[str, Any]],
) -> dict[str, Any]:
    app = _application()
    timezone_name = str(
        balcony.get("timezone_name", "Europe/Berlin")
    )
    from watering_backend.services.routing import (
        optimize_static_connection_plan as optimize,
    )

    return optimize(
        balcony,
        walls,
        plants,
        outlets,
        target_date=app.local_now(timezone_name).date(),
        calibration_factor=app.settings.water_model_calibration(),
    )


def fetch_weather(
    balcony: dict[str, Any],
    force: bool = False,
) -> dict[str, Any]:
    return _application().weather.fetch_weather(balcony, force=force)


def current_weather_or_params(
    params: dict[str, list[str]] | dict[str, Any],
    balcony: dict[str, Any],
) -> dict[str, Any]:
    return _application().weather.current_weather_or_params(
        params,
        balcony,
    )


def weather_from_query(
    params: dict[str, list[str]],
) -> dict[str, Any]:
    return _application().weather.weather_from_query(params)


def weather_from_payload(
    payload: dict[str, Any],
) -> dict[str, Any]:
    return _application().weather.weather_from_payload(payload)


def evaluate_weather(
    weather: dict[str, Any],
    slot: str = "morning",
) -> dict[str, Any]:
    return _application().weather.evaluate_weather(weather, slot)


def shortcut_blueprint(base_url: str) -> dict[str, Any]:
    return _application().shortcut_blueprint(base_url)


def distributed_automation_windows(
    today: date,
    total_cycles: int,
    tzinfo,
) -> list[datetime]:
    return _application().scheduling.distributed_automation_windows(
        today,
        total_cycles,
        tzinfo,
    )


def parse_pause_until(
    value: str,
    timezone_name: str,
) -> datetime | None:
    return pure_parse_pause_until(
        value,
        timezone_name,
        timezone_reference=local_now(timezone_name),
    )


def automation_status(*args, **kwargs) -> dict[str, Any]:
    return _application().scheduling.automation_status(*args, **kwargs)


def refill_window_datetimes(
    today: date,
    tzinfo,
    schedule_times: list[str] | None = None,
) -> list[datetime]:
    return _application().scheduling.refill_window_datetimes(
        today,
        tzinfo,
        schedule_times,
    )


def refill_cooldown_minutes(transferred_ml: int | float) -> int:
    return _application().refill.cooldown_minutes(transferred_ml)


def latest_refill_event() -> dict[str, Any] | None:
    return _application().refill.latest_event()


def parse_event_datetime(value: object) -> datetime | None:
    from watering_backend.services.refill import parse_event_datetime as parse

    return parse(value)


def refill_status(
    balcony: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return _application().refill.status(balcony)


def manual_refill_plan(result: dict[str, Any]) -> dict[str, Any]:
    return _application().refill.manual_plan(result)


def save_pending_refill_request(plan: dict[str, Any]) -> None:
    _application().refill.save_pending_request(plan)


def pending_refill_request() -> dict[str, Any] | None:
    return _application().refill.pending_request()


def normalized_forecast_days(
    weather: dict[str, Any],
    today: date,
) -> list[dict[str, Any]]:
    return _application().forecast.normalized_forecast_days(weather, today)


def forecast_number(
    weather: dict[str, Any],
    key: str,
    default: float,
) -> float:
    return _application().forecast.forecast_number(weather, key, default)


def projected_consumption_days(*args, **kwargs) -> list[dict[str, Any]]:
    return _application().forecast.projected_consumption_days(
        *args,
        **kwargs,
    )


def projected_cycle_events(*args, **kwargs) -> list[dict[str, Any]]:
    return _application().forecast.projected_cycle_events(*args, **kwargs)


def chronological_depletion_simulation(
    *args,
    **kwargs,
) -> dict[str, Any]:
    return _application().forecast.chronological_depletion_simulation(
        *args,
        **kwargs,
    )


def depletion_forecast(
    result: dict[str, Any],
    weather: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return _application().forecast.depletion_forecast(result, weather)


def home_assistant_webhook_url() -> str:
    return _application().home_assistant.watering_webhook_url()


def home_assistant_refill_webhook_url() -> str:
    return _application().home_assistant.refill_webhook_url()


def manual_run_status(result: dict[str, Any]) -> dict[str, Any]:
    return _application().home_assistant.manual_run_status(result)


def manual_refill_status(result: dict[str, Any]) -> dict[str, Any]:
    return _application().home_assistant.manual_refill_status(result)


def trigger_home_assistant_manual_run(
    result: dict[str, Any],
    run_id: object = None,
) -> str:
    return _application().home_assistant.trigger_manual_run(result, run_id)


def trigger_home_assistant_manual_refill(
    result: dict[str, Any],
    run_id: object = None,
) -> str:
    return _application().trigger_home_assistant_manual_refill(
        result,
        run_id,
    )


def evaluate(*args, **kwargs) -> dict[str, Any]:
    return _application().evaluation.evaluate(*args, **kwargs)


def notification_service() -> NotificationService:
    return _application().notification_transport


def notification_condition(
    alert_key: str,
    active: bool,
    severity: str,
    subject: str,
    message: str,
) -> dict[str, Any]:
    return _application().notification_conditions.notification_condition(
        alert_key,
        active,
        severity,
        subject,
        message,
    )


def record_home_assistant_health(
    reachable: bool,
    detail: str,
) -> None:
    _application().record_home_assistant_health(reachable, detail)


def notification_conditions() -> list[dict[str, Any]]:
    return _application().notification_conditions.notification_conditions()


def run_notification_check() -> list[dict[str, Any]]:
    return _application().notification_conditions.run_notification_check()


def updater_token() -> str:
    return _application().updater.updater_token()


def updater_request(*args, **kwargs) -> dict[str, Any]:
    return _application().updater.updater_request(*args, **kwargs)


def save_balcony(payload: dict[str, Any]) -> None:
    _application().configuration.save_balcony(payload)


def add_plant(payload: dict[str, Any]) -> int:
    return _application().plants.add(payload)


def update_plant(
    plant_id: int,
    payload: dict[str, Any],
) -> None:
    _application().plants.update(plant_id, payload)


def update_plant_position(
    plant_id: int,
    payload: dict[str, Any],
) -> None:
    _application().plants.update_position(plant_id, payload)


def delete_plant(plant_id: int) -> None:
    _application().plants.delete(plant_id)


def mark_refill_run(
    source: str = "home_assistant",
    run_id: object = None,
) -> dict[str, Any]:
    return _application().mark_refill_run(
        source=source,
        run_id=run_id,
    )


def fill_tank(tank_name: str) -> None:
    _application().watering.fill_tank(tank_name)


def mark_run(
    delivered_ml: int,
    temperature_c: float,
    rain_mm: float,
    run_id: object = None,
    source: str = "homekit",
) -> dict[str, Any]:
    return _application().watering.mark_run(
        delivered_ml,
        temperature_c,
        rain_mm,
        run_id,
        source,
    )


_DEFAULT_NOTIFICATION_SERVICE = notification_service
_DEFAULT_FETCH_WEATHER = fetch_weather
_DEFAULT_PROJECTED_CONSUMPTION_DAYS = projected_consumption_days
_ROUTER = build_router()


class AppHandler(SimpleHTTPRequestHandler):
    """Compatibility handler backed by the registered API router."""

    def __init__(self, *args, **kwargs):
        kwargs.pop("directory", None)
        super().__init__(*args, directory=str(PUBLIC_DIR), **kwargs)

    def log_message(self, format: str, *args) -> None:
        print(f"[{self.log_date_time_string()}] {format % args}")

    def end_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "same-origin")
        self.send_header("Permissions-Policy", "geolocation=(self)")
        super().end_headers()

    @staticmethod
    def _dispatch(handler, method: str) -> bool:
        request = ApiRequest(
            method=method,
            target=handler.path,
            headers=handler.headers,
            json_loader=lambda: read_json(handler),
        )
        response = _ROUTER.dispatch(request, _application())
        if response is None:
            return False
        if response.status == HTTPStatus.OK:
            send_json(handler, response.payload)
        else:
            send_json(handler, response.payload, response.status)
        return True

    def do_GET(self) -> None:
        if not AppHandler._dispatch(self, "GET"):
            super().do_GET()

    def do_POST(self) -> None:
        if not AppHandler._dispatch(self, "POST"):
            send_json(self, {"error": "Not found"}, HTTPStatus.NOT_FOUND)

    def do_PUT(self) -> None:
        if not AppHandler._dispatch(self, "PUT"):
            send_json(self, {"error": "Not found"}, HTTPStatus.NOT_FOUND)

    def do_DELETE(self) -> None:
        if not AppHandler._dispatch(self, "DELETE"):
            send_json(self, {"error": "Not found"}, HTTPStatus.NOT_FOUND)


def start_notification_worker() -> NotificationWorker | None:
    return _application().start_notification_worker()


def stop_notification_worker() -> None:
    global _default_application
    if _default_application is not None:
        _default_application.stop_notification_worker()


def main() -> None:
    app = _application()
    app.ensure_writable()
    app.initialize()
    from watering_backend.http_api import serve

    serve(
        os.environ.get("HOST", "127.0.0.1"),
        int(os.environ.get("PORT", "8080")),
        AppHandler,
        on_start=app.start_notification_worker,
        on_stop=app.stop_notification_worker,
    )


__all__ = [
    # Lifecycle and paths
    "APP_VERSION",
    "AppHandler",
    "Application",
    "ApplicationPaths",
    "DATA_DIR",
    "DB_PATH",
    "PUBLIC_DIR",
    "ROOT",
    "create_application",
    "connect",
    "ensure_data_dir_writable",
    "init_db",
    "main",
    "start_notification_worker",
    "stop_notification_worker",
    # Stable domain constants
    "CONNECTION_DESIGN",
    "DEFAULT_BALCONY",
    "DEFAULT_WALLS",
    "PLANT_CATALOG",
    "PLANT_WATER_PROFILES",
    "PREVIOUS_WATER_MODEL_CALIBRATION",
    "SEASONAL_WATER_CURVES",
    "TANK_LOW_PERCENT",
    "WATER_MODEL_CALIBRATION",
    "WEATHER_FORECAST_DAYS",
    # Configuration and persistence facade
    "calibrated_consumption_ml",
    "delete_setting",
    "get_setting",
    "main_pump_calibration_factor",
    "planner_config",
    "refill_automation_enabled",
    "refill_cooldown_minutes_per_liter",
    "refill_schedule_times",
    "save_main_pump_calibration_factor",
    "save_planner_config",
    "save_refill_automation_enabled",
    "save_refill_cooldown_minutes_per_liter",
    "save_refill_schedule_times",
    "save_watering_amount_percent",
    "set_setting",
    "water_model_calibration",
    "watering_amount_percent",
    # State and CRUD
    "add_plant",
    "delete_plant",
    "get_state",
    "save_balcony",
    "save_hoses",
    "update_plant",
    "update_plant_position",
    "watering_events",
    # Evaluation, routing and scheduling
    "automation_status",
    "calculate_plant_results",
    "choose_tube_assignments",
    "completed_cycles_today",
    "configured_connection_plan",
    "distributed_automation_windows",
    "estimate_sun_hours",
    "evaluate",
    "local_day_utc_bounds",
    "local_now",
    "normalize_assignment",
    "optimize_routing",
    "optimize_static_connection_plan",
    "plant_tube_options",
    # Weather and forecast
    "daily_forecast_items",
    "depletion_forecast",
    "evaluate_weather",
    "fetch_weather",
    "normalized_forecast_days",
    "projected_consumption_days",
    "weather_diagnostics",
    "weather_from_payload",
    "weather_from_query",
    # Tanks, refill and calibration
    "calibrate_main_pump",
    "calibrate_refill_pump",
    "calibration_status",
    "fill_tank",
    "manual_refill_plan",
    "mark_refill_run",
    "mark_run",
    "refill_status",
    # Integrations and notifications
    "home_assistant_diagnostics",
    "manual_run_status",
    "notification_conditions",
    "notification_service",
    "run_notification_check",
    "shortcut_blueprint",
    "test_home_assistant_connection",
    "trigger_home_assistant_manual_refill",
    "trigger_home_assistant_manual_run",
    "updater_request",
    "updater_token",
    # Validation and compatibility helpers
    "normalize_run_id",
    "now_iso",
    "validate_outlets",
    "validate_plant_payload",
    "validate_walls",
]


if __name__ == "__main__":
    main()
