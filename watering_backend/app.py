from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from urllib.request import urlopen

from watering_backend.api import create_handler
from watering_backend.catalog import (
    DEFAULT_BALCONY,
    DEFAULT_WALLS,
    MAX_WATERING_AMOUNT_PERCENT,
    MIN_WATERING_AMOUNT_PERCENT,
    PLANT_CATALOG,
    PLANT_WATER_PROFILES,
    PREVIOUS_WATER_MODEL_CALIBRATION,
    TANK_FORECAST_DAYS,
    TANK_LOW_PERCENT,
    WATER_MODEL_CALIBRATION,
    WEATHER_FORECAST_DAYS,
)
from watering_backend.config import local_day_utc_bounds
from watering_backend.database import Database
from watering_backend.http_api import serve
from watering_backend.notifications import (
    NotificationService,
    NotificationWorker,
    SMTPConfig,
)
from watering_backend.repositories import (
    EventsRepository,
    HosesRepository,
    NotificationsRepository,
    PlantsRepository,
    SettingsRepository,
    TanksRepository,
)
from watering_backend.schema import initialize as initialize_schema
from watering_backend.services.calibration import CalibrationService
from watering_backend.services.configuration import ConfigurationService
from watering_backend.services.diagnostics import DiagnosticsService
from watering_backend.services.evaluation import (
    EvaluationService,
    calculate_plant_results,
)
from watering_backend.services.forecast import ForecastService
from watering_backend.services.history import HistoryService
from watering_backend.services.home_assistant import (
    HomeAssistantService,
    shortcut_blueprint,
)
from watering_backend.services.notifications import (
    NotificationConditionsService,
)
from watering_backend.services.refill import RefillService
from watering_backend.services.routing import (
    apply_fixed_connection_to_weather,
    configured_connection_plan,
    optimize_static_connection_plan,
)
from watering_backend.services.scheduling import (
    SchedulingService,
    local_now as system_local_now,
    window_datetime,
)
from watering_backend.services.state import StateService
from watering_backend.services.updater import UpdaterService
from watering_backend.services.watering import WateringService
from watering_backend.services.weather import WeatherService


LocalClock = Callable[[str], datetime]
NowIso = Callable[[], str]
Opener = Callable[..., Any]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class ApplicationPaths:
    root: Path
    public_dir: Path
    data_dir: Path
    database_path: Path
    version_path: Path
    updater_token_file: Path

    @classmethod
    def from_environment(
        cls,
        *,
        root: Path | None = None,
        environment: Mapping[str, str] | None = None,
    ) -> "ApplicationPaths":
        env = environment if environment is not None else os.environ
        app_root = root or Path(__file__).parent.parent
        data_dir = Path(env.get("DATA_DIR", app_root / "data"))
        database_path = Path(
            env.get("DB_PATH", data_dir / "watering.sqlite3")
        )
        return cls(
            root=app_root,
            public_dir=app_root / "public",
            data_dir=data_dir,
            database_path=database_path,
            version_path=app_root / "VERSION",
            updater_token_file=Path(
                env.get(
                    "UPDATER_TOKEN_FILE",
                    data_dir / ".updater-token",
                )
            ),
        )


class Application:
    """Composition root for repositories, domain services and adapters."""

    def __init__(
        self,
        paths: ApplicationPaths,
        *,
        environment: Mapping[str, str] | None = None,
        local_clock: LocalClock = system_local_now,
        now_iso: NowIso = utc_now_iso,
        opener: Opener = urlopen,
        updater_url: str | None = None,
    ):
        self.paths = paths
        self.environment = environment if environment is not None else os.environ
        self._local_clock = local_clock
        self._now_iso = now_iso
        self.opener = opener
        self._notification_worker: NotificationWorker | None = None

        self.database = Database(paths.data_dir, paths.database_path)
        self.settings = SettingsRepository(self.database)
        self.hoses = HosesRepository(self.database)
        self.plants = PlantsRepository(
            self.database,
            self.hoses,
            lambda: self.now_iso(),
        )
        self.events = EventsRepository(self.database)
        self.tanks = TanksRepository(self.database)
        self.notification_repository = NotificationsRepository(self.database)

        self.scheduling = SchedulingService(
            self.settings.planner_config,
            clock=lambda timezone_name: self.local_now(timezone_name),
        )
        self.refill = RefillService(
            self.events,
            self.settings,
            self.tanks,
            # Refill windows have always followed the installation-local
            # clock. Keeping this dependency explicit also makes DST and
            # legacy clock overrides deterministic.
            now=lambda: self.local_now("Europe/Berlin").astimezone(
                timezone.utc
            ),
            tank_low_percent=TANK_LOW_PERCENT,
        )
        self.calibration = CalibrationService(
            self.database,
            self.events,
            self.settings,
            self.tanks,
            now=lambda: self.now_datetime(),
        )
        self.diagnostics = DiagnosticsService(
            settings=self.settings,
            planner_config=self.settings.planner_config,
            now=lambda: self.now_datetime(),
        )
        self.notification_transport = NotificationService(
            repository=self.notification_repository,
            now=lambda: self.now_datetime(),
        )
        self.notification_service_provider: Callable[
            [],
            NotificationService,
        ] = lambda: self.notification_transport

        self.home_assistant = HomeAssistantService(
            settings=self.settings,
            now_iso=lambda: self.now_iso(),
            manual_refill_plan=self.refill.manual_plan,
            save_pending_refill_request=self.refill.save_pending_request,
            clear_pending_refill_request=lambda: self.settings.delete(
                "pending_refill_request"
            ),
            health_notifier=self.record_home_assistant_health,
            environment=self.environment,
            opener=self.opener,
        )
        self.state = StateService(
            database=self.database,
            tanks=self.tanks,
            hoses=self.hoses,
            plants=self.plants,
            settings=self.settings,
            version=self.app_version,
            planner_config=self.settings.planner_config,
            weather_diagnostics=self.diagnostics.weather,
            notification_status=self.diagnostics.notification_public_status,
            home_assistant_diagnostics=self.home_assistant.diagnostics,
            calibration_status=self.calibration.status,
            completed_cycles_today=self.completed_cycles_today,
        )
        self.forecast = ForecastService(
            events=self.events,
            state_provider=self.get_state,
            planner_config=self.settings.planner_config,
            refill_enabled=self.settings.refill_automation_enabled,
            calibrated_consumption=self.settings.calibrated_consumption_ml,
            local_now=self.local_now,
            window_datetime=window_datetime,
            distributed_windows=self.scheduling.distributed_automation_windows,
            calculate_plants=self.calculate_plant_results,
            configured_plan=configured_connection_plan,
            apply_plan_to_weather=apply_fixed_connection_to_weather,
            weather_forecast_days=WEATHER_FORECAST_DAYS,
            tank_forecast_days=TANK_FORECAST_DAYS,
        )
        self.evaluation = EvaluationService(
            state_provider=self.get_state,
            planner_config=self.settings.planner_config,
            water_calibration=self.settings.water_model_calibration,
            main_pump_factor=self.settings.main_pump_calibration_factor,
            calibrated_consumption=self.settings.calibrated_consumption_ml,
            completed_cycles_today=self.completed_cycles_today,
            latest_watering_at=self.events.latest_watering_at,
            local_now=self.local_now,
            automation_status=self.scheduling.automation_status,
            refill_status=self.refill.status,
            connection_optimizer=optimize_static_connection_plan,
            configured_plan=configured_connection_plan,
            weather_plan=apply_fixed_connection_to_weather,
            manual_run_status=self.home_assistant.manual_run_status,
            manual_refill_status=self.home_assistant.manual_refill_status,
            depletion_forecast=self.forecast.depletion_forecast,
            pause_until=lambda: self.settings.get(
                "automation_pause_until",
                "",
            ),
            now_iso=self.now_iso,
            tank_low_percent=TANK_LOW_PERCENT,
        )
        self.weather = WeatherService(
            get_setting=self.settings.get,
            set_setting=self.settings.set,
            planner_config_provider=self.settings.planner_config,
            balcony_provider=lambda: self.tanks.balcony(),
            evaluator=self.evaluation.evaluate,
            depletion_forecaster=self.forecast.depletion_forecast,
            manual_refill_evaluator=self.refill.manual_plan,
            clock=lambda: self.now_datetime(),
            opener=self.opener,
            forecast_days=WEATHER_FORECAST_DAYS,
        )
        self.notification_weather_fetch: Callable[
            [dict[str, Any]],
            dict[str, Any],
        ] = lambda balcony: self.weather.fetch_weather(balcony)
        self.notification_conditions = NotificationConditionsService(
            state=self.get_state,
            calibrated_consumption=self.settings.calibrated_consumption_ml,
            refill_status=self.refill.status,
            fetch_weather=lambda balcony: self.notification_weather_fetch(
                balcony
            ),
            evaluate_weather=self.weather.evaluate_weather,
            weather_diagnostics=self.diagnostics.weather,
            planner_config=self.settings.planner_config,
            local_now=self.local_now,
            notification_service=lambda: self.notification_service_provider(),
            previous_missed_keys=lambda: (
                self.notification_repository.keys_with_prefix(
                    "refill_run_missed:"
                )
            ),
        )
        self.history = HistoryService(
            self.events,
            self.settings.calibrated_consumption_ml,
        )
        self.configuration = ConfigurationService(
            database=self.database,
            tanks=self.tanks,
            hoses=self.hoses,
            settings=self.settings,
            now_iso=self.now_iso,
        )
        self.watering = WateringService(
            database=self.database,
            events=self.events,
            tanks=self.tanks,
            settings=self.settings,
            now_iso=self.now_iso,
            local_now=self.local_now,
            state_provider=self.get_state,
            refill_status=self.refill.status,
            pending_refill_request=self.refill.pending_request,
            clear_pending_refill_request=lambda: self.settings.delete(
                "pending_refill_request"
            ),
        )
        self.updater = UpdaterService(
            updater_url
            if updater_url is not None
            else self.environment.get(
                "UPDATER_URL",
                "http://updater:3188",
            ),
            paths.updater_token_file,
            opener=self.opener,
        )

    @property
    def app_version(self) -> str:
        try:
            return self.paths.version_path.read_text(
                encoding="utf-8"
            ).strip()
        except OSError:
            return "0.0.0"

    def set_runtime_hooks(
        self,
        *,
        local_clock: LocalClock | None = None,
        now_iso: NowIso | None = None,
        opener: Opener | None = None,
        notification_service: Callable[[], NotificationService] | None = None,
        notification_weather_fetch: Callable[
            [dict[str, Any]],
            dict[str, Any],
        ]
        | None = None,
        projected_consumption: Callable[..., list[dict[str, Any]]] | None = None,
    ) -> None:
        if local_clock is not None:
            self._local_clock = local_clock
        if now_iso is not None:
            self._now_iso = now_iso
        if opener is not None:
            self.opener = opener
            self.weather.opener = opener
            self.home_assistant.opener = opener
            object.__setattr__(self.updater, "opener", opener)
        if notification_service is not None:
            self.notification_service_provider = notification_service
        if notification_weather_fetch is not None:
            self.notification_weather_fetch = notification_weather_fetch
        self.forecast.projected_consumption_provider = projected_consumption

    def local_now(self, timezone_name: str) -> datetime:
        return self._local_clock(timezone_name)

    def now_iso(self) -> str:
        return self._now_iso()

    def now_datetime(self) -> datetime:
        value = datetime.fromisoformat(self.now_iso())
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    def ensure_writable(self) -> None:
        self.database.ensure_writable()

    def initialize(self) -> None:
        with self.database.connection() as conn:
            initialize_schema(
                conn,
                catalog=PLANT_CATALOG,
                profiles=PLANT_WATER_PROFILES,
                default_balcony=DEFAULT_BALCONY,
                default_walls=DEFAULT_WALLS,
                now_iso=self.now_iso,
                previous_calibration=PREVIOUS_WATER_MODEL_CALIBRATION,
                current_calibration=WATER_MODEL_CALIBRATION,
                minimum_percent=MIN_WATERING_AMOUNT_PERCENT,
                maximum_percent=MAX_WATERING_AMOUNT_PERCENT,
            )
        # Plan absolute refill opportunities at startup as well as during
        # normal status polls. This keeps missed-window detection independent
        # of the notification worker being alive while a window is open.
        self.refill.status(self.tanks.balcony())

    def connect(self):
        return self.database.connection()

    def get_state(self) -> dict[str, Any]:
        return self.state.get_state()

    def calculate_plant_results(self, *args, **kwargs) -> dict[str, Any]:
        kwargs.setdefault(
            "calibration_factor",
            self.settings.water_model_calibration(),
        )
        if kwargs.get("target_date") is None and args:
            balcony = args[0]
            timezone_name = str(
                balcony.get("timezone_name", "Europe/Berlin")
            )
            kwargs["target_date"] = self.local_now(timezone_name).date()
        return calculate_plant_results(*args, **kwargs)

    def completed_cycles_today(
        self,
        timezone_name: str = "Europe/Berlin",
    ) -> int:
        now = self.local_now(timezone_name)
        start, end = local_day_utc_bounds(now.date(), now.tzinfo)
        return self.events.watering_count_between(
            start.isoformat(),
            end.isoformat(),
        )

    def delivered_ml_for_local_date(
        self,
        day: date,
        timezone_name: str = "Europe/Berlin",
    ) -> int:
        tzinfo = self.local_now(timezone_name).tzinfo
        start, end = local_day_utc_bounds(day, tzinfo)
        return self.events.delivered_between(
            start.isoformat(),
            end.isoformat(),
        )

    def save_balcony(self, payload: dict[str, Any]) -> None:
        self.configuration.save_balcony(payload)

    def add_plant(self, payload: dict[str, Any]) -> int:
        return self.plants.add(payload)

    def update_plant(
        self,
        plant_id: int,
        payload: dict[str, Any],
    ) -> None:
        self.plants.update(plant_id, payload)

    def update_plant_position(
        self,
        plant_id: int,
        payload: dict[str, Any],
    ) -> None:
        self.plants.update_position(plant_id, payload)

    def delete_plant(self, plant_id: int) -> None:
        self.plants.delete(plant_id)

    def save_hoses(self, payload: dict[str, Any]) -> None:
        self.hoses.save(payload)

    def save_planner_config(self, value: object) -> dict[str, Any]:
        return self.settings.save_planner_config(value)

    def fetch_weather(
        self,
        balcony: dict[str, Any],
        force: bool = False,
    ) -> dict[str, Any]:
        return self.weather.fetch_weather(balcony, force=force)

    def weather_from_query(
        self,
        params: dict[str, list[str]],
    ) -> dict[str, Any]:
        return self.weather.weather_from_query(params)

    def weather_from_payload(
        self,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        return self.weather.weather_from_payload(payload)

    def evaluate_weather(
        self,
        weather: dict[str, Any],
        slot: str = "morning",
    ) -> dict[str, Any]:
        return self.weather.evaluate_weather(weather, slot)

    def evaluate(self, *args, **kwargs) -> dict[str, Any]:
        return self.evaluation.evaluate(*args, **kwargs)

    def watering_events(self, limit: int = 12) -> list[dict[str, Any]]:
        return self.history.watering_events(limit)

    def shortcut_blueprint(self, base_url: str) -> dict[str, Any]:
        return shortcut_blueprint(base_url)

    def calibrate_main_pump(self, value: object) -> dict[str, Any]:
        return self.calibration.calibrate_main(value)

    def calibrate_refill_pump(self, value: object) -> dict[str, Any]:
        return self.calibration.calibrate_refill(value)

    def mark_run(self, *args, **kwargs) -> dict[str, Any]:
        return self.watering.mark_run(*args, **kwargs)

    def mark_refill_run(self, *args, **kwargs) -> dict[str, Any]:
        return self.watering.mark_refill_run(*args, **kwargs)

    def fill_tank(self, tank_name: str) -> None:
        self.watering.fill_tank(tank_name)

    def trigger_home_assistant_manual_run(
        self,
        result: dict[str, Any],
        run_id: object = None,
    ) -> str:
        return self.home_assistant.trigger_manual_run(result, run_id)

    def trigger_home_assistant_manual_refill(
        self,
        result: dict[str, Any],
        run_id: object = None,
    ) -> str:
        return self.home_assistant.trigger_manual_refill(result, run_id)

    def weather_diagnostics(self) -> dict[str, Any]:
        return self.diagnostics.weather()

    def home_assistant_diagnostics(self) -> dict[str, Any]:
        return self.home_assistant.diagnostics()

    def test_home_assistant_connection(self) -> dict[str, Any]:
        return self.home_assistant.test_connection()

    def notification_service(self) -> NotificationService:
        return self.notification_service_provider()

    def notification_diagnostics(self) -> dict[str, Any]:
        return self.notification_service().diagnostics()

    def send_test_notification(self) -> dict[str, Any]:
        return self.notification_service().send_test()

    def run_notification_check(self) -> list[dict[str, Any]]:
        return self.notification_conditions.run_notification_check()

    def record_home_assistant_health(
        self,
        reachable: bool,
        detail: str,
    ) -> None:
        try:
            condition = self.notification_conditions.notification_condition(
                "home_assistant_unreachable",
                not reachable,
                "critical",
                "Home-Assistant-Webhook nicht erreichbar",
                detail,
            )
            self.notification_service().update_condition(**condition)
        except Exception:
            pass

    def updater_request(
        self,
        path: str,
        method: str = "GET",
        payload: dict[str, Any] | None = None,
        timeout: int = 30,
    ) -> dict[str, Any]:
        return self.updater.updater_request(
            path,
            method,
            payload,
            timeout,
        )

    def set_setting(self, key: str, value: str) -> None:
        self.settings.set(key, value)

    def delete_setting(self, key: str) -> None:
        self.settings.delete(key)

    def notification_worker_running(self) -> bool:
        return bool(
            self._notification_worker
            and self._notification_worker.running
        )

    def start_notification_worker(self) -> NotificationWorker | None:
        disabled = str(
            self.environment.get("NOTIFICATION_WORKER_DISABLED", "")
        ).lower() in {"1", "true", "yes", "on"}
        try:
            smtp_config = SMTPConfig.from_env()
        except ValueError:
            return None
        if disabled or not smtp_config.enabled:
            return None
        config = self.settings.planner_config()
        self._notification_worker = NotificationWorker(
            self.notification_conditions.notification_conditions,
            self.notification_service(),
            int(config["notification_worker_interval_seconds"]),
        )
        self._notification_worker.start()
        return self._notification_worker

    def stop_notification_worker(self) -> None:
        if self._notification_worker:
            self._notification_worker.stop()
            self._notification_worker = None

    def handler_class(self):
        return create_handler(self, self.paths.public_dir)

    def run_server(
        self,
        *,
        host: str | None = None,
        port: int | None = None,
    ) -> None:
        self.ensure_writable()
        self.initialize()
        active_host = host or self.environment.get("HOST", "127.0.0.1")
        active_port = (
            int(port)
            if port is not None
            else int(self.environment.get("PORT", "8080"))
        )
        serve(
            active_host,
            active_port,
            self.handler_class(),
            on_start=self.start_notification_worker,
            on_stop=self.stop_notification_worker,
        )


def create_application(
    *,
    paths: ApplicationPaths | None = None,
    environment: Mapping[str, str] | None = None,
    local_clock: LocalClock = system_local_now,
    now_iso: NowIso = utc_now_iso,
    opener: Opener = urlopen,
) -> Application:
    return Application(
        paths
        or ApplicationPaths.from_environment(environment=environment),
        environment=environment,
        local_clock=local_clock,
        now_iso=now_iso,
        opener=opener,
    )
