"""Shared structural types for repositories, services, and API responses."""

from __future__ import annotations

from typing import Literal, TypedDict


Severity = Literal["info", "warning", "critical"]
ConnectionSeverity = Literal["ok", "change", "urgent"]
ForecastEventStatus = Literal["successful", "estimated", "unserved"]
PlantSize = Literal["small", "medium", "large", "tree"]
RefillAutomationStatus = Literal[
    "ready",
    "disabled",
    "main_tank_full",
    "refill_tank_empty",
    "pump_flow_missing",
    "cooldown",
    "window_pending",
    "window_missed",
    "running",
    "run_unconfirmed",
    "completed",
]
RefillRunType = Literal["automatic", "manual"]
RefillRunStatus = Literal[
    "reserved",
    "running",
    "completed",
    "failed",
    "expired",
    "cancelled",
]


class RefillWindow(TypedDict):
    start: str
    end: str


class PlannerConfig(TypedDict):
    watering_window_start: str
    watering_window_end: str
    max_cycles_per_day: int
    watering_min_interval_minutes: int
    refill_windows: list[RefillWindow]
    refill_min_interval_minutes: int
    refill_strategy: Literal["fraction", "target"]
    refill_fraction: float
    refill_target_ml: int
    weather_stale_after_minutes: int
    weather_cache_minutes: int
    missed_watering_tolerance_minutes: int
    notification_cooldown_minutes: int
    notification_retry_minutes: int
    notification_resolved_enabled: bool
    supply_warning_days: int
    notification_worker_interval_seconds: int


class DailyWeather(TypedDict, total=False):
    date: str
    temperature_c: float
    rain_mm: float
    wind_kmh: float
    sunshine_hours: float
    et0_mm: float
    estimated: bool


class WeatherData(TypedDict, total=False):
    source: str
    mode: str
    simulation: bool
    fetched_at: str
    last_successful_fetch_at: str
    cache_hit: bool
    cache_fallback: bool
    stale: bool
    temperature_c: float
    rain_mm: float
    wind_kmh: float
    sunshine_hours: float
    et0_mm: float
    current: dict[str, object]
    hourly: dict[str, object]
    forecast: list[DailyWeather]


class TankForecastEvent(TypedDict, total=False):
    at: str
    date: str
    event_type: Literal["watering", "refill"]
    planned_water_ml: int
    main_tank_before_ml: int
    main_tank_after_ml: int
    refill_tank_before_ml: int
    refill_tank_after_ml: int
    status: ForecastEventStatus
    delivered_to_plants_ml: int
    consumed_from_main_tank_ml: int
    transferred_ml: int
    estimated_weather: bool
    started_at: str
    completed_at: str
    duration_seconds: int
    window_start: str
    window_end: str
    blocked_reason: str
    limited_by_window: bool


class TankEvent(TypedDict, total=False):
    id: int
    ran_at: str
    tank_name: Literal["main", "refill"]
    previous_ml: int
    new_ml: int
    capacity_ml: int
    source: str


class WateringEvent(TypedDict, total=False):
    id: int
    run_id: str
    ran_at: str
    delivered_ml: int
    actual_consumed_ml: int
    temperature_c: float
    rain_mm: float
    source: str
    idempotent_replay: bool
    legacy_generated_run_id: bool


class RefillEvent(TypedDict, total=False):
    id: int
    run_id: str
    ran_at: str
    target_date: str
    requested_ml: int
    transferred_ml: int
    duration_seconds: int
    window_label: str
    source: str
    idempotent_replay: bool
    legacy_generated_run_id: bool


class RefillRun(TypedDict, total=False):
    run_id: str
    run_type: RefillRunType
    status: RefillRunStatus
    created_at: str
    authorized_at: str
    started_at: str
    completed_at: str
    target_date: str
    window_key: str
    window_label: str
    window_start: str
    window_end: str
    requested_ml: int
    planned_transfer_ml: int
    planned_duration_seconds: int
    main_tank_start_ml: int
    refill_tank_start_ml: int
    pump_ml_per_min: int
    expected_complete_at: str
    expires_at: str
    limit_reason: str
    source: str
    accounted_transfer_ml: int
    physical_transfer_ml: int
    main_accounted_ml: int
    main_before_complete_ml: int
    main_after_complete_ml: int
    refill_before_complete_ml: int
    refill_after_complete_ml: int
    consistency_delta_ml: int
    consistency_note: str
    needs_manual_review: bool
    error_text: str
    completion_reason: str
    idempotent_replay: bool


class PlantCalculation(TypedDict, total=False):
    id: int
    catalog_id: str
    name: str
    custom_name: str
    size: PlantSize
    need_ml: int
    delivered_ml: int
    difference_ml: int
    sun_hours: float
    shade_factor: float
    rain_credit_ml: float
    et0_mm: float
    seasonal_factor: float
    connection_status: str
    connection_severity: ConnectionSeverity
    connection_action_title: str
    connection_note: str


class DiagnosticState(TypedDict, total=False):
    status: str
    configured: bool
    reachable: bool
    blocked: bool
    blocked_reason: str
    severity: Severity
    last_successful_contact_at: str
    last_successful_fetch_at: str
    last_attempt_at: str
    last_error: str
    data_age_minutes: float | None
    stale: bool
    summary: str


class NotificationCondition(TypedDict):
    alert_key: str
    active: bool
    severity: Severity
    subject: str
    message: str
    cooldown_minutes: int
    retry_minutes: int
    send_resolved: bool
