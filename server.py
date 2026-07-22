from __future__ import annotations

import json
import math
import os
import re
import sqlite3
import ssl
from copy import deepcopy
from contextlib import contextmanager
from datetime import date, datetime, time, timedelta, timezone
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Iterator
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from urllib.parse import parse_qs, urlencode, urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


ROOT = Path(__file__).parent
PUBLIC_DIR = ROOT / "public"
DATA_DIR = Path(os.environ.get("DATA_DIR", ROOT / "data"))
DB_PATH = Path(os.environ.get("DB_PATH", DATA_DIR / "watering.sqlite3"))
VERSION_PATH = ROOT / "VERSION"
APP_VERSION = VERSION_PATH.read_text(encoding="utf-8").strip() if VERSION_PATH.exists() else "0.0.0"
UPDATER_URL = os.environ.get("UPDATER_URL", "http://updater:3188").rstrip("/")
UPDATER_TOKEN_FILE = Path(os.environ.get("UPDATER_TOKEN_FILE", DATA_DIR / ".updater-token"))


PLANT_CATALOG = [
    ("olive", "Olivenbaum", "Mediterrane Gehölze", 22, 1.15, 0.8, "Mag es hell, eher trocken, aber nicht komplett austrocknen lassen."),
    ("citrus", "Zitrusbaum", "Mediterrane Gehölze", 30, 1.15, 0.95, "Hell und gleichmäßig feucht, bei Hitze und Fruchtansatz durstiger."),
    ("fig", "Feigenbaum", "Mediterrane Gehölze", 28, 1.1, 0.85, "Robust, im Kübel bei voller Sonne aber mit deutlichem Wasserbedarf."),
    ("oleander", "Oleander", "Mediterrane Gehölze", 36, 1.2, 0.95, "Verträgt viel Sonne und braucht im Sommer reichlich Wasser."),
    ("bay", "Lorbeer", "Mediterrane Gehölze", 24, 1.0, 0.8, "Mag gleichmäßige Feuchte, aber keine dauerhafte Staunässe."),
    ("loquat", "Mispel", "Obstgehölze", 28, 1.05, 0.9, "Gleichmäßige Feuchte, bei Hitze deutlich durstiger."),
    ("pine", "Kiefer", "Nadelgehölze", 18, 1.05, 0.72, "Sparsam, aber Kübelpflanzen sollten nicht komplett austrocknen."),
    ("tomato", "Tomatenpflanze", "Gemüse", 48, 1.25, 1.2, "Sehr hoher Bedarf, regelmäßige Wassergaben vermeiden Fruchtplatzen."),
    ("cucumber", "Gurke", "Gemüse", 58, 1.15, 1.25, "Sehr durstig, besonders bei Fruchtbildung und Wind."),
    ("zucchini", "Zucchini", "Gemüse", 60, 1.12, 1.25, "Große Blätter und Früchte machen sie im Topf sehr durstig."),
    ("eggplant", "Aubergine", "Gemüse", 42, 1.15, 1.05, "Wärmeliebend, gleichmäßige Wasserversorgung fördert Fruchtansatz."),
    ("lettuce", "Pflücksalat", "Gemüse", 34, 0.9, 1.15, "Flach wurzelnd, trocknet im Topf schnell aus."),
    ("arugula", "Rucola", "Gemüse", 30, 0.9, 1.0, "Gleichmäßig feucht halten, Hitze fördert Schossen."),
    ("raspberry", "Himbeere", "Beerenobst", 38, 1.1, 1.15, "Feuchte Erde, besonders bei Fruchtbildung."),
    ("blueberry", "Heidelbeere", "Beerenobst", 42, 0.95, 1.2, "Saurer Boden, gleichmäßige Feuchte, keine Trockenphasen."),
    ("currant", "Johannisbeere", "Beerenobst", 34, 1.0, 1.0, "Im Kübel gleichmäßig feucht halten."),
    ("lavender", "Lavendel", "Kräuter", 16, 1.25, 0.65, "Trockenheitsliebend, Staunässe vermeiden."),
    ("basil", "Basilikum", "Kräuter", 42, 0.95, 1.05, "Hoher Wasserbedarf, aber empfindlich gegen nasse Blätter."),
    ("rosemary", "Rosmarin", "Kräuter", 18, 1.2, 0.7, "Mediterran und sparsam, lieber seltener kräftig."),
    ("thyme", "Thymian", "Kräuter", 14, 1.2, 0.62, "Sehr trockenheitsverträglich, Staunässe vermeiden."),
    ("oregano", "Oregano", "Kräuter", 18, 1.1, 0.72, "Eher trocken halten, verträgt Sonne gut."),
    ("sage", "Salbei", "Kräuter", 20, 1.1, 0.75, "Mäßig gießen, trockenheitsverträglich."),
    ("parsley", "Petersilie", "Kräuter", 36, 0.9, 1.08, "Gleichmäßig feucht halten, nicht austrocknen lassen."),
    ("chives", "Schnittlauch", "Kräuter", 34, 0.85, 1.0, "Mag gleichmäßige Feuchte und etwas weniger pralle Sonne."),
    ("cilantro", "Koriander", "Kräuter", 32, 0.85, 1.0, "Gleichmäßige Feuchte, Hitze und Trockenheit fördern Schossen."),
    ("hydrangea", "Hortensie", "Blühpflanzen", 55, 0.85, 1.25, "Sehr hoher Bedarf, Schatten reduziert Stress."),
    ("carnation", "Nelke", "Blühpflanzen", 24, 1.0, 0.78, "Eher mäßiger Bedarf, im Topf bei Hitze gleichmäßig feucht halten."),
    ("geranium", "Geranie", "Blühpflanzen", 34, 1.05, 0.95, "Solider Bedarf, blüht stabil bei gleichmäßiger Feuchte."),
    ("petunia", "Petunie", "Blühpflanzen", 40, 1.1, 1.0, "Blühstark und durstig, bei Sonne regelmäßig versorgen."),
    ("calibrachoa", "Zauberglöckchen", "Blühpflanzen", 38, 1.1, 1.0, "Kleine Töpfe trocknen schnell, gleichmäßige Feuchte wichtig."),
    ("fuchsia", "Fuchsie", "Blühpflanzen", 36, 0.75, 1.12, "Mag halbschattige, gleichmäßig feuchte Standorte."),
    ("begonia", "Begonie", "Blühpflanzen", 28, 0.7, 0.92, "Mäßig feucht, eher geschützt und nicht zu nass."),
    ("dahlia", "Dahlie", "Blühpflanzen", 44, 1.05, 1.05, "Große Blattmasse, im Kübel hoher Bedarf."),
    ("marguerite", "Margerite", "Blühpflanzen", 38, 1.05, 1.0, "Regelmäßig gießen, volle Sonne erhöht Bedarf."),
    ("marigold", "Tagetes", "Blühpflanzen", 28, 1.0, 0.85, "Robust, mäßiger Wasserbedarf."),
    ("nasturtium", "Kapuzinerkresse", "Blühpflanzen", 34, 1.0, 0.9, "Robust, bei Hitze und Sonne durstiger."),
    ("strawberry", "Erdbeere", "Beerenobst", 32, 1.0, 1.0, "Während Blüte und Fruchtbildung nicht austrocknen lassen."),
    ("mint", "Minze", "Kräuter", 46, 0.9, 1.1, "Durstig und robust, verträgt mehr Feuchte."),
    ("chili", "Chili/Paprika", "Gemüse", 36, 1.15, 1.0, "Gleichmäßig gießen, bei Hitze steigt der Bedarf schnell."),
    ("clematis", "Clematis", "Kletterpflanzen", 34, 0.95, 1.0, "Wurzeln kühl und feucht, Triebe sonniger."),
    ("jasmine", "Jasmin", "Kletterpflanzen", 32, 1.0, 0.95, "Gleichmäßig feucht, in kleinen Töpfen empfindlicher."),
    ("passionflower", "Passionsblume", "Kletterpflanzen", 44, 1.1, 1.1, "Starke Blattmasse, bei Sonne und Wind durstig."),
    ("succulent", "Sukkulenten/Sedum", "Sukkulenten", 8, 1.2, 0.45, "Sehr sparsam gießen, Staunässe unbedingt vermeiden."),
    ("aloe", "Aloe Vera", "Sukkulenten", 10, 1.1, 0.5, "Trockenheitsverträglich, nur mäßig gießen."),
]

PLANT_WATER_PROFILES = {
    "olive": {"crop_coefficient": 0.62, "canopy_m2_medium": 0.42, "recommended_pot_liters": 35, "moisture_preference": 0.72},
    "loquat": {"crop_coefficient": 0.82, "canopy_m2_medium": 0.5, "recommended_pot_liters": 35, "moisture_preference": 0.9},
    "pine": {"crop_coefficient": 0.48, "canopy_m2_medium": 0.32, "recommended_pot_liters": 25, "moisture_preference": 0.68},
    "tomato": {"crop_coefficient": 1.15, "canopy_m2_medium": 0.45, "recommended_pot_liters": 20, "moisture_preference": 1.12},
    "zucchini": {"crop_coefficient": 1.18, "canopy_m2_medium": 0.58, "recommended_pot_liters": 25, "moisture_preference": 1.22},
    "raspberry": {"crop_coefficient": 0.98, "canopy_m2_medium": 0.42, "recommended_pot_liters": 25, "moisture_preference": 1.08},
    "lavender": {"crop_coefficient": 0.45, "canopy_m2_medium": 0.2, "recommended_pot_liters": 10, "moisture_preference": 0.62},
    "basil": {"crop_coefficient": 0.96, "canopy_m2_medium": 0.16, "recommended_pot_liters": 7, "moisture_preference": 1.08},
    "rosemary": {"crop_coefficient": 0.5, "canopy_m2_medium": 0.22, "recommended_pot_liters": 12, "moisture_preference": 0.66},
    "hydrangea": {"crop_coefficient": 1.1, "canopy_m2_medium": 0.48, "recommended_pot_liters": 20, "moisture_preference": 1.25},
    "carnation": {"crop_coefficient": 0.62, "canopy_m2_medium": 0.16, "recommended_pot_liters": 8, "moisture_preference": 0.82},
    "geranium": {"crop_coefficient": 0.78, "canopy_m2_medium": 0.18, "recommended_pot_liters": 8, "moisture_preference": 0.92},
    "strawberry": {"crop_coefficient": 0.85, "canopy_m2_medium": 0.12, "recommended_pot_liters": 6, "moisture_preference": 1.0},
    "mint": {"crop_coefficient": 1.02, "canopy_m2_medium": 0.18, "recommended_pot_liters": 8, "moisture_preference": 1.12},
    "chili": {"crop_coefficient": 1.0, "canopy_m2_medium": 0.32, "recommended_pot_liters": 14, "moisture_preference": 1.0},
    "citrus": {"crop_coefficient": 0.82, "canopy_m2_medium": 0.48, "recommended_pot_liters": 35, "moisture_preference": 0.95},
    "fig": {"crop_coefficient": 0.72, "canopy_m2_medium": 0.55, "recommended_pot_liters": 40, "moisture_preference": 0.82},
    "oleander": {"crop_coefficient": 0.95, "canopy_m2_medium": 0.5, "recommended_pot_liters": 35, "moisture_preference": 1.0},
    "bay": {"crop_coefficient": 0.65, "canopy_m2_medium": 0.34, "recommended_pot_liters": 25, "moisture_preference": 0.82},
    "cucumber": {"crop_coefficient": 1.18, "canopy_m2_medium": 0.55, "recommended_pot_liters": 25, "moisture_preference": 1.22},
    "eggplant": {"crop_coefficient": 1.05, "canopy_m2_medium": 0.42, "recommended_pot_liters": 18, "moisture_preference": 1.05},
    "lettuce": {"crop_coefficient": 0.95, "canopy_m2_medium": 0.16, "recommended_pot_liters": 6, "moisture_preference": 1.16},
    "arugula": {"crop_coefficient": 0.86, "canopy_m2_medium": 0.13, "recommended_pot_liters": 5, "moisture_preference": 1.02},
    "blueberry": {"crop_coefficient": 0.95, "canopy_m2_medium": 0.36, "recommended_pot_liters": 25, "moisture_preference": 1.18},
    "currant": {"crop_coefficient": 0.82, "canopy_m2_medium": 0.38, "recommended_pot_liters": 25, "moisture_preference": 1.0},
    "thyme": {"crop_coefficient": 0.38, "canopy_m2_medium": 0.12, "recommended_pot_liters": 6, "moisture_preference": 0.58},
    "oregano": {"crop_coefficient": 0.48, "canopy_m2_medium": 0.14, "recommended_pot_liters": 7, "moisture_preference": 0.68},
    "sage": {"crop_coefficient": 0.52, "canopy_m2_medium": 0.18, "recommended_pot_liters": 10, "moisture_preference": 0.72},
    "parsley": {"crop_coefficient": 0.9, "canopy_m2_medium": 0.13, "recommended_pot_liters": 7, "moisture_preference": 1.06},
    "chives": {"crop_coefficient": 0.82, "canopy_m2_medium": 0.12, "recommended_pot_liters": 6, "moisture_preference": 1.0},
    "cilantro": {"crop_coefficient": 0.82, "canopy_m2_medium": 0.13, "recommended_pot_liters": 6, "moisture_preference": 1.0},
    "petunia": {"crop_coefficient": 0.95, "canopy_m2_medium": 0.2, "recommended_pot_liters": 8, "moisture_preference": 1.0},
    "calibrachoa": {"crop_coefficient": 0.9, "canopy_m2_medium": 0.16, "recommended_pot_liters": 7, "moisture_preference": 1.0},
    "fuchsia": {"crop_coefficient": 0.85, "canopy_m2_medium": 0.2, "recommended_pot_liters": 10, "moisture_preference": 1.12},
    "begonia": {"crop_coefficient": 0.72, "canopy_m2_medium": 0.16, "recommended_pot_liters": 8, "moisture_preference": 0.9},
    "dahlia": {"crop_coefficient": 1.02, "canopy_m2_medium": 0.34, "recommended_pot_liters": 18, "moisture_preference": 1.05},
    "marguerite": {"crop_coefficient": 0.86, "canopy_m2_medium": 0.24, "recommended_pot_liters": 12, "moisture_preference": 0.98},
    "marigold": {"crop_coefficient": 0.72, "canopy_m2_medium": 0.16, "recommended_pot_liters": 8, "moisture_preference": 0.84},
    "nasturtium": {"crop_coefficient": 0.8, "canopy_m2_medium": 0.22, "recommended_pot_liters": 10, "moisture_preference": 0.9},
    "clematis": {"crop_coefficient": 0.86, "canopy_m2_medium": 0.34, "recommended_pot_liters": 20, "moisture_preference": 1.0},
    "jasmine": {"crop_coefficient": 0.82, "canopy_m2_medium": 0.3, "recommended_pot_liters": 18, "moisture_preference": 0.94},
    "passionflower": {"crop_coefficient": 1.05, "canopy_m2_medium": 0.45, "recommended_pot_liters": 25, "moisture_preference": 1.08},
    "succulent": {"crop_coefficient": 0.24, "canopy_m2_medium": 0.1, "recommended_pot_liters": 5, "moisture_preference": 0.42},
    "aloe": {"crop_coefficient": 0.28, "canopy_m2_medium": 0.14, "recommended_pot_liters": 8, "moisture_preference": 0.48},
}

DEFAULT_BALCONY = {
    "orientation": "south",
    "orientation_deg": 180,
    "width_m": 3.0,
    "depth_m": 1.4,
    "location": "Berlin",
    "latitude": 52.52,
    "longitude": 13.405,
    "timezone_name": "Europe/Berlin",
    "wall_height_m": 1.05,
    "tank_capacity_ml": 10000,
    "tank_current_ml": 8000,
    "refill_tank_capacity_ml": 30000,
    "refill_tank_current_ml": 30000,
    "refill_pump_ml_per_min": 1000,
    "outlets": [
        {"name": "S", "ml_per_run": 15},
        {"name": "M", "ml_per_run": 30},
        {"name": "L", "ml_per_run": 60},
    ],
}

DEFAULT_WALLS = [
    ("north", 0.0),
    ("east", 0.0),
    ("south", 1.05),
    ("west", 0.0),
]

CONNECTION_DESIGN = {
    "temperature_c": 26,
    "rain_mm": 0,
    "wind_kmh": 8,
    "sunshine_hours": 7,
    "cycles": 4,
}

# The raw ET0/canopy estimate models open-surface evapotranspiration. The real
# terrace drip setup needs a calibrated fraction per day. The current default
# reflects the formerly common 250-260% UI setting, so 100% is a useful start.
WATER_MODEL_CALIBRATION = 0.20
PREVIOUS_WATER_MODEL_CALIBRATION = 0.08
LEGACY_WATER_MODEL_CALIBRATIONS = (0.04, 0.06, PREVIOUS_WATER_MODEL_CALIBRATION)
LEGACY_WATER_MODEL_CALIBRATION = 0.06
MIN_WATER_MODEL_CALIBRATION_PERCENT = 0.5
MAX_WATER_MODEL_CALIBRATION_PERCENT = 40.0
MIN_WATERING_AMOUNT_PERCENT = 40.0
MAX_WATERING_AMOUNT_PERCENT = 500.0
TANK_LOW_PERCENT = 20

SEASONAL_WATER_CURVES = {
    "warm_annual": [(1, 0.22), (80, 0.25), (130, 0.42), (172, 0.78), (220, 1.0), (280, 0.62), (335, 0.28), (366, 0.22)],
    "annual": [(1, 0.28), (80, 0.32), (130, 0.5), (172, 0.82), (220, 1.0), (280, 0.68), (335, 0.32), (366, 0.28)],
    "woody": [(1, 0.38), (80, 0.45), (130, 0.62), (172, 0.88), (220, 1.0), (280, 0.72), (335, 0.42), (366, 0.38)],
    "evergreen": [(1, 0.45), (80, 0.5), (130, 0.65), (172, 0.88), (220, 1.0), (280, 0.78), (335, 0.5), (366, 0.45)],
    "succulent": [(1, 0.55), (80, 0.58), (130, 0.68), (172, 0.82), (220, 0.9), (280, 0.72), (335, 0.58), (366, 0.55)],
}

AUTOMATION_DAY_START = "07:00"
AUTOMATION_DAY_END = "19:00"
AUTOMATION_TRIGGER_TOLERANCE_MINUTES = 20
AUTOMATION_RUN_COOLDOWN_MINUTES = 30
REFILL_RUN_TIMES = ("01:00",)
REFILL_TRIGGER_TOLERANCE_MINUTES = 60
REFILL_TRANSFER_FRACTION = 0.5
REFILL_COOLDOWN_MINUTES_PER_LITER = 30
REFILL_MIN_COOLDOWN_MINUTES = 15
REFILL_MAX_COOLDOWN_MINUTES = 12 * 60
WEATHER_FORECAST_DAYS = 16


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    DATA_DIR.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def ensure_data_dir_writable() -> None:
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        probe = DATA_DIR / ".write-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        if DB_PATH.exists():
            with sqlite3.connect(DB_PATH) as conn:
                conn.execute("PRAGMA user_version")
                conn.execute("CREATE TABLE IF NOT EXISTS __write_probe (id INTEGER PRIMARY KEY)")
                conn.execute("DROP TABLE __write_probe")
    except OSError as exc:
        raise RuntimeError(
            f"DATA_DIR ist nicht beschreibbar: {DATA_DIR}. "
            "Prüfe im Synology Container Manager das Volume ./data:/app/data und deaktiviere Read-only."
        ) from exc
    except sqlite3.OperationalError as exc:
        raise RuntimeError(
            f"SQLite-Datenbank ist nicht beschreibbar: {DB_PATH}. "
            "Prüfe Besitzer/Rechte von data/watering.sqlite3 und ob das Volume im Container Manager schreibbar gemountet ist."
        ) from exc


def init_db() -> None:
    with connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS plant_catalog (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                category TEXT NOT NULL,
                base_ml_per_l_day REAL NOT NULL,
                sun_factor REAL NOT NULL,
                drought_sensitivity REAL NOT NULL,
                crop_coefficient REAL NOT NULL DEFAULT 1.0,
                canopy_m2_medium REAL NOT NULL DEFAULT 0.2,
                recommended_pot_liters REAL NOT NULL DEFAULT 10,
                moisture_preference REAL NOT NULL DEFAULT 1.0,
                notes TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS balcony_settings (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                orientation TEXT NOT NULL,
                orientation_deg REAL NOT NULL DEFAULT 180,
                width_m REAL NOT NULL,
                depth_m REAL NOT NULL,
                location TEXT NOT NULL,
                latitude REAL NOT NULL DEFAULT 52.52,
                longitude REAL NOT NULL DEFAULT 13.405,
                timezone_name TEXT NOT NULL DEFAULT 'Europe/Berlin',
                wall_height_m REAL NOT NULL,
                tank_capacity_ml INTEGER NOT NULL,
                tank_current_ml INTEGER NOT NULL,
                refill_tank_capacity_ml INTEGER NOT NULL DEFAULT 30000,
                refill_tank_current_ml INTEGER NOT NULL DEFAULT 30000,
                refill_pump_ml_per_min INTEGER NOT NULL DEFAULT 1000,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS terrace_walls (
                side TEXT PRIMARY KEY,
                height_m REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS pump_outlets (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                ml_per_run INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS plants (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                catalog_id TEXT NOT NULL REFERENCES plant_catalog(id),
                custom_name TEXT NOT NULL,
                size TEXT NOT NULL,
                pot_liters REAL NOT NULL,
                pot_type TEXT NOT NULL,
                outlet_id INTEGER NOT NULL REFERENCES pump_outlets(id),
                pos_x REAL NOT NULL DEFAULT 0.5,
                pos_y REAL NOT NULL DEFAULT 0.5,
                hose_numbers TEXT NOT NULL DEFAULT '',
                target_ml_per_cycle REAL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS watering_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ran_at TEXT NOT NULL,
                delivered_ml INTEGER NOT NULL,
                temperature_c REAL,
                rain_mm REAL,
                source TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS pump_calibration_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                calibrated_at TEXT NOT NULL,
                pump_name TEXT NOT NULL,
                measured_level_ml INTEGER NOT NULL,
                baseline_at TEXT NOT NULL,
                baseline_level_ml INTEGER NOT NULL,
                cycles INTEGER NOT NULL,
                nominal_ml INTEGER NOT NULL,
                measured_ml INTEGER NOT NULL,
                result_value REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS refill_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ran_at TEXT NOT NULL,
                target_date TEXT NOT NULL,
                requested_ml INTEGER NOT NULL,
                transferred_ml INTEGER NOT NULL,
                duration_seconds INTEGER NOT NULL,
                window_label TEXT NOT NULL DEFAULT '',
                source TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS tank_fill_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ran_at TEXT NOT NULL,
                tank_name TEXT NOT NULL,
                previous_ml INTEGER NOT NULL,
                new_ml INTEGER NOT NULL,
                capacity_ml INTEGER NOT NULL,
                source TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS irrigation_hoses (
                number TEXT PRIMARY KEY,
                outlet_id INTEGER NOT NULL REFERENCES pump_outlets(id),
                plant_id INTEGER REFERENCES plants(id)
            );

            CREATE TABLE IF NOT EXISTS app_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            """
        )
        ensure_column(conn, "balcony_settings", "latitude", "REAL NOT NULL DEFAULT 52.52")
        ensure_column(conn, "balcony_settings", "longitude", "REAL NOT NULL DEFAULT 13.405")
        ensure_column(conn, "balcony_settings", "timezone_name", "TEXT NOT NULL DEFAULT 'Europe/Berlin'")
        ensure_column(conn, "balcony_settings", "orientation_deg", "REAL NOT NULL DEFAULT 180")
        ensure_column(conn, "balcony_settings", "refill_tank_capacity_ml", "INTEGER NOT NULL DEFAULT 30000")
        ensure_column(conn, "balcony_settings", "refill_tank_current_ml", "INTEGER NOT NULL DEFAULT 30000")
        ensure_column(conn, "balcony_settings", "refill_pump_ml_per_min", "INTEGER NOT NULL DEFAULT 1000")
        ensure_column(conn, "refill_events", "window_label", "TEXT NOT NULL DEFAULT ''")
        ensure_column(conn, "watering_events", "actual_consumed_ml", "INTEGER")
        ensure_column(conn, "plants", "pos_x", "REAL NOT NULL DEFAULT 0.5")
        ensure_column(conn, "plants", "pos_y", "REAL NOT NULL DEFAULT 0.5")
        ensure_column(conn, "plants", "hose_numbers", "TEXT NOT NULL DEFAULT ''")
        ensure_column(conn, "plants", "target_ml_per_cycle", "REAL")
        ensure_column(conn, "plant_catalog", "crop_coefficient", "REAL NOT NULL DEFAULT 1.0")
        ensure_column(conn, "plant_catalog", "canopy_m2_medium", "REAL NOT NULL DEFAULT 0.2")
        ensure_column(conn, "plant_catalog", "recommended_pot_liters", "REAL NOT NULL DEFAULT 10")
        ensure_column(conn, "plant_catalog", "moisture_preference", "REAL NOT NULL DEFAULT 1.0")

        upsert_plant_catalog(conn)
        upsert_plant_water_profiles(conn)
        migrate_watering_amount_standard(conn)

        if conn.execute("SELECT COUNT(*) FROM balcony_settings").fetchone()[0] == 0:
            conn.execute(
                """
                INSERT INTO balcony_settings
                    (id, orientation, orientation_deg, width_m, depth_m, location, latitude, longitude, timezone_name, wall_height_m,
                    tank_capacity_ml, tank_current_ml, refill_tank_capacity_ml, refill_tank_current_ml,
                     refill_pump_ml_per_min, updated_at)
                VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    DEFAULT_BALCONY["orientation"],
                    DEFAULT_BALCONY["orientation_deg"],
                    DEFAULT_BALCONY["width_m"],
                    DEFAULT_BALCONY["depth_m"],
                    DEFAULT_BALCONY["location"],
                    DEFAULT_BALCONY["latitude"],
                    DEFAULT_BALCONY["longitude"],
                    DEFAULT_BALCONY["timezone_name"],
                    DEFAULT_BALCONY["wall_height_m"],
                    DEFAULT_BALCONY["tank_capacity_ml"],
                    DEFAULT_BALCONY["tank_current_ml"],
                    DEFAULT_BALCONY["refill_tank_capacity_ml"],
                    DEFAULT_BALCONY["refill_tank_current_ml"],
                    DEFAULT_BALCONY["refill_pump_ml_per_min"],
                    now_iso(),
                ),
            )

        if conn.execute("SELECT COUNT(*) FROM pump_outlets").fetchone()[0] == 0:
            conn.executemany(
                "INSERT INTO pump_outlets (id, name, ml_per_run) VALUES (?, ?, ?)",
                [(1, "S", 15), (2, "M", 30), (3, "L", 60)],
            )

        if conn.execute("SELECT COUNT(*) FROM terrace_walls").fetchone()[0] == 0:
            conn.executemany("INSERT INTO terrace_walls (side, height_m) VALUES (?, ?)", DEFAULT_WALLS)

        plant_count = conn.execute("SELECT COUNT(*) FROM plants").fetchone()[0]
        if plant_count == 0:
            seed_plants(conn)
        migrate_legacy_hoses(conn)


def get_setting(key: str, default: str = "") -> str:
    with connect() as conn:
        try:
            row = conn.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
        except sqlite3.OperationalError:
            return default
        return str(row["value"]) if row else default


def set_setting(key: str, value: str) -> None:
    with connect() as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS app_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        conn.execute(
            """
            INSERT INTO app_settings (key, value)
            VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (key, value),
        )


def delete_setting(key: str) -> None:
    with connect() as conn:
        try:
            conn.execute("DELETE FROM app_settings WHERE key = ?", (key,))
        except sqlite3.OperationalError:
            pass


def migrate_watering_amount_standard(conn: sqlite3.Connection) -> None:
    row = conn.execute("SELECT value FROM app_settings WHERE key = 'watering_amount_percent'").fetchone()
    basis = conn.execute("SELECT value FROM app_settings WHERE key = 'watering_amount_calibration_basis'").fetchone()
    if row and not basis:
        try:
            amount_percent = float(row["value"])
        except ValueError:
            amount_percent = 100
        migrated_percent = clamp(amount_percent * PREVIOUS_WATER_MODEL_CALIBRATION / WATER_MODEL_CALIBRATION, MIN_WATERING_AMOUNT_PERCENT, MAX_WATERING_AMOUNT_PERCENT)
        conn.execute(
            """
            INSERT INTO app_settings (key, value)
            VALUES ('watering_amount_percent', ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (str(migrated_percent),),
        )
    conn.execute(
        """
        INSERT INTO app_settings (key, value)
        VALUES ('watering_amount_calibration_basis', ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        (str(WATER_MODEL_CALIBRATION),),
    )


def refill_automation_enabled() -> bool:
    return get_setting("refill_automation_enabled", "true").lower() not in {"0", "false", "off", "no"}


def save_refill_automation_enabled(value: object) -> None:
    enabled = str(value).lower() in {"1", "true", "on", "yes"}
    set_setting("refill_automation_enabled", "true" if enabled else "false")


def main_pump_calibration_factor() -> float:
    try:
        factor = float(get_setting("main_pump_calibration_factor", "1"))
    except ValueError:
        return 1.0
    return factor if 0.1 <= factor <= 10 else 1.0


def save_main_pump_calibration_factor(value: object) -> None:
    factor = float(value)
    if not 0.1 <= factor <= 10:
        raise ValueError("Der Verbrauchsfaktor muss zwischen 0,1 und 10 liegen")
    set_setting("main_pump_calibration_factor", f"{factor:.6g}")


def calibrated_consumption_ml(nominal_ml: int | float) -> int:
    return max(0, round(float(nominal_ml) * main_pump_calibration_factor()))


def normalize_refill_schedule_times(value: object) -> list[str]:
    if isinstance(value, str):
        raw_items = [item.strip() for item in value.replace(";", ",").split(",")]
    elif isinstance(value, (list, tuple)):
        raw_items = [str(item).strip() for item in value]
    else:
        raw_items = []
    times = []
    seen = set()
    for item in raw_items:
        if not item:
            continue
        parsed = parse_hhmm(item)
        label = f"{parsed.hour:02d}:{parsed.minute:02d}"
        if label not in seen:
            seen.add(label)
            times.append(label)
    if not times:
        raise ValueError("Mindestens eine Nachfüllzeit muss angegeben werden")
    return sorted(times, key=lambda item: parse_hhmm(item))


def refill_schedule_times() -> list[str]:
    return list(REFILL_RUN_TIMES)


def save_refill_schedule_times(value: object) -> None:
    # Das einzelne Nachtfenster ist fest definiert und wird nicht nachgeholt.
    set_setting("refill_schedule_times", json.dumps(list(REFILL_RUN_TIMES)))


def refill_cooldown_minutes_per_liter() -> float:
    try:
        value = float(get_setting("refill_cooldown_minutes_per_liter", str(REFILL_COOLDOWN_MINUTES_PER_LITER)))
    except ValueError:
        return float(REFILL_COOLDOWN_MINUTES_PER_LITER)
    if value <= 0:
        return float(REFILL_COOLDOWN_MINUTES_PER_LITER)
    return value


def save_refill_cooldown_minutes_per_liter(value: object) -> None:
    minutes = float(value)
    if minutes <= 0 or minutes > REFILL_MAX_COOLDOWN_MINUTES:
        raise ValueError(f"Nachfüllsperre pro Liter muss zwischen 1 und {REFILL_MAX_COOLDOWN_MINUTES} Minuten liegen")
    set_setting("refill_cooldown_minutes_per_liter", f"{minutes:g}")


def water_model_calibration() -> float:
    amount_percent = saved_watering_amount_percent()
    if amount_percent is not None:
        return WATER_MODEL_CALIBRATION * amount_percent / 100
    try:
        calibration = float(get_setting("water_model_calibration", str(WATER_MODEL_CALIBRATION)))
    except ValueError:
        return WATER_MODEL_CALIBRATION
    if any(math.isclose(calibration, legacy) for legacy in LEGACY_WATER_MODEL_CALIBRATIONS):
        return WATER_MODEL_CALIBRATION
    if not MIN_WATER_MODEL_CALIBRATION_PERCENT / 100 <= calibration <= MAX_WATER_MODEL_CALIBRATION_PERCENT / 100:
        return WATER_MODEL_CALIBRATION
    return calibration


def save_water_model_calibration_percent(value: object) -> None:
    calibration_percent = float(value)
    if not MIN_WATER_MODEL_CALIBRATION_PERCENT <= calibration_percent <= MAX_WATER_MODEL_CALIBRATION_PERCENT:
        raise ValueError(
            f"Wasser-Skalierung muss zwischen {MIN_WATER_MODEL_CALIBRATION_PERCENT:g} und "
            f"{MAX_WATER_MODEL_CALIBRATION_PERCENT:g} Prozent liegen"
        )
    delete_setting("watering_amount_percent")
    set_setting("water_model_calibration", str(calibration_percent / 100))


def saved_watering_amount_percent() -> float | None:
    try:
        amount_percent = float(get_setting("watering_amount_percent"))
    except ValueError:
        return None
    if not MIN_WATERING_AMOUNT_PERCENT <= amount_percent <= MAX_WATERING_AMOUNT_PERCENT:
        return None
    return amount_percent


def watering_amount_percent() -> float:
    amount_percent = saved_watering_amount_percent()
    if amount_percent is not None:
        return amount_percent
    return water_model_calibration() / WATER_MODEL_CALIBRATION * 100


def save_watering_amount_percent(value: object) -> None:
    amount_percent = float(value)
    if not MIN_WATERING_AMOUNT_PERCENT <= amount_percent <= MAX_WATERING_AMOUNT_PERCENT:
        raise ValueError(
            f"Gießmenge muss zwischen {MIN_WATERING_AMOUNT_PERCENT:g} und "
            f"{MAX_WATERING_AMOUNT_PERCENT:g} Prozent liegen"
        )
    delete_setting("water_model_calibration")
    set_setting("watering_amount_percent", str(amount_percent))


def ensure_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    columns = [row["name"] for row in conn.execute(f"PRAGMA table_info({table})")]
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def upsert_plant_catalog(conn: sqlite3.Connection) -> None:
    conn.executemany(
        """
        INSERT INTO plant_catalog
            (id, name, category, base_ml_per_l_day, sun_factor, drought_sensitivity, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            name = excluded.name,
            category = excluded.category,
            base_ml_per_l_day = excluded.base_ml_per_l_day,
            sun_factor = excluded.sun_factor,
            drought_sensitivity = excluded.drought_sensitivity,
            notes = excluded.notes
        """,
        PLANT_CATALOG,
    )


def upsert_plant_water_profiles(conn: sqlite3.Connection) -> None:
    for catalog_id, profile in PLANT_WATER_PROFILES.items():
        conn.execute(
            """
            UPDATE plant_catalog
            SET crop_coefficient = ?,
                canopy_m2_medium = ?,
                recommended_pot_liters = ?,
                moisture_preference = ?
            WHERE id = ?
            """,
            (
                profile["crop_coefficient"],
                profile["canopy_m2_medium"],
                profile["recommended_pot_liters"],
                profile["moisture_preference"],
                catalog_id,
            ),
        )


def seed_plants(conn: sqlite3.Connection) -> None:
    conn.executemany(
        """
        INSERT INTO plants
            (catalog_id, custom_name, size, pot_liters, pot_type, outlet_id, pos_x, pos_y,
             hose_numbers, target_ml_per_cycle, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            ("olive", "Olive", "medium", 28, "overflow", 2, 0.25, 0.7, "1", 30, now_iso()),
            ("tomato", "Tomate", "medium", 18, "closed", 3, 0.7, 0.55, "2", 60, now_iso()),
            ("lavender", "Lavendel", "small", 10, "overflow", 1, 0.45, 0.25, "3", 15, now_iso()),
        ],
    )


def parse_hose_numbers(value: object) -> list[str]:
    hose_numbers = []
    for hose_number in re.findall(r"\d+", str(value or "")):
        if hose_number not in hose_numbers:
            hose_numbers.append(hose_number)
    return hose_numbers


def normalize_hose_numbers(value: object) -> str:
    return ", ".join(parse_hose_numbers(value))


def irrigation_hoses(conn: sqlite3.Connection) -> list[dict]:
    return [
        row_to_dict(row)
        for row in conn.execute(
            """
            SELECT
                irrigation_hoses.number,
                irrigation_hoses.outlet_id,
                irrigation_hoses.plant_id,
                pump_outlets.name AS outlet_name,
                pump_outlets.ml_per_run,
                plants.custom_name AS plant_name
            FROM irrigation_hoses
            JOIN pump_outlets ON pump_outlets.id = irrigation_hoses.outlet_id
            LEFT JOIN plants ON plants.id = irrigation_hoses.plant_id
            ORDER BY CAST(irrigation_hoses.number AS INTEGER), irrigation_hoses.number
            """
        )
    ]


def migrate_legacy_hoses(conn: sqlite3.Connection) -> None:
    if conn.execute("SELECT COUNT(*) FROM irrigation_hoses").fetchone()[0] > 0:
        return
    for plant in conn.execute("SELECT id, outlet_id, hose_numbers FROM plants"):
        for number in parse_hose_numbers(plant["hose_numbers"]):
            conn.execute(
                """
                INSERT OR IGNORE INTO irrigation_hoses (number, outlet_id, plant_id)
                VALUES (?, ?, ?)
                """,
                (number, int(plant["outlet_id"]), int(plant["id"])),
            )


def enrich_plant_connection(plant: dict, hoses: list[dict]) -> dict:
    assigned_hoses = [hose for hose in hoses if hose["plant_id"] == plant["id"]]
    hose_count = len(assigned_hoses)
    plant["hoses"] = assigned_hoses
    plant["hose_numbers"] = ", ".join(hose["number"] for hose in assigned_hoses)
    plant["hose_count"] = hose_count
    plant["configured_ml_per_cycle"] = sum(int(hose["ml_per_run"]) for hose in assigned_hoses)
    plant["outlet_summary"] = ", ".join(
        f"{hose['number']}: {hose['outlet_name']} ({hose['ml_per_run']} ml)"
        for hose in assigned_hoses
    )
    return plant


def sync_legacy_plant_connection(conn: sqlite3.Connection, plant_id: int) -> None:
    hoses = [
        row_to_dict(row)
        for row in conn.execute(
            """
            SELECT irrigation_hoses.number, irrigation_hoses.outlet_id, pump_outlets.ml_per_run
            FROM irrigation_hoses
            JOIN pump_outlets ON pump_outlets.id = irrigation_hoses.outlet_id
            WHERE irrigation_hoses.plant_id = ?
            ORDER BY CAST(irrigation_hoses.number AS INTEGER), irrigation_hoses.number
            """,
            (plant_id,),
        )
    ]
    fallback_outlet_id = default_outlet_id_for_conn(conn)
    conn.execute(
        """
        UPDATE plants
        SET outlet_id = ?, hose_numbers = ?, target_ml_per_cycle = ?
        WHERE id = ?
        """,
        (
            int(hoses[0]["outlet_id"]) if hoses else fallback_outlet_id,
            ", ".join(hose["number"] for hose in hoses),
            sum(int(hose["ml_per_run"]) for hose in hoses) or None,
            plant_id,
        ),
    )


def assign_hoses_to_plant(conn: sqlite3.Connection, plant_id: int, hose_numbers: object) -> None:
    selected = parse_hose_numbers(hose_numbers)
    existing = {
        str(row["number"])
        for row in conn.execute("SELECT number FROM irrigation_hoses")
    }
    missing = [number for number in selected if number not in existing]
    if missing:
        raise ValueError(f"Unbekannte Schläuche: {', '.join(missing)}")
    affected = {
        int(row["plant_id"])
        for row in conn.execute(
            "SELECT DISTINCT plant_id FROM irrigation_hoses WHERE plant_id IS NOT NULL AND (plant_id = ? OR number IN ({placeholders}))".format(
                placeholders=", ".join("?" for _ in selected) or "NULL"
            ),
            (plant_id, *selected),
        )
    }
    conn.execute("UPDATE irrigation_hoses SET plant_id = NULL WHERE plant_id = ?", (plant_id,))
    if selected:
        conn.execute(
            "UPDATE irrigation_hoses SET plant_id = ? WHERE number IN ({})".format(", ".join("?" for _ in selected)),
            (plant_id, *selected),
        )
    affected.add(plant_id)
    for affected_plant_id in affected:
        sync_legacy_plant_connection(conn, affected_plant_id)


def save_hoses(payload: dict) -> None:
    hoses = payload.get("hoses")
    if not isinstance(hoses, list):
        raise ValueError("hoses fehlt")
    normalized = []
    seen = set()
    for hose in hoses:
        number = normalize_hose_numbers(hose.get("number", ""))
        if not number or "," in number:
            raise ValueError("Jeder Schlauch braucht genau eine Nummer")
        if number in seen:
            raise ValueError(f"Schlauch {number} ist doppelt vorhanden")
        seen.add(number)
        normalized.append({"number": number, "outlet_id": int(hose["outlet_id"])})
    with connect() as conn:
        if normalized:
            conn.execute(
                "DELETE FROM irrigation_hoses WHERE number NOT IN ({})".format(", ".join("?" for _ in normalized)),
                tuple(hose["number"] for hose in normalized),
            )
        else:
            conn.execute("DELETE FROM irrigation_hoses")
        for hose in normalized:
            conn.execute(
                """
                INSERT INTO irrigation_hoses (number, outlet_id)
                VALUES (?, ?)
                ON CONFLICT(number) DO UPDATE SET outlet_id = excluded.outlet_id
                """,
                (hose["number"], hose["outlet_id"]),
            )
        for plant in conn.execute("SELECT id FROM plants"):
            sync_legacy_plant_connection(conn, int(plant["id"]))


def default_outlet_id_for_conn(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT id FROM pump_outlets ORDER BY ml_per_run LIMIT 1").fetchone()
    return int(row["id"])


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def row_to_dict(row: sqlite3.Row) -> dict:
    return {key: row[key] for key in row.keys()}


def calibration_baseline(conn: sqlite3.Connection, pump_name: str) -> dict | None:
    tank_name = "main" if pump_name == "main" else "refill"
    calibration = conn.execute(
        """
        SELECT calibrated_at AS baseline_at, measured_level_ml AS baseline_level_ml
        FROM pump_calibration_events
        WHERE pump_name = ?
        ORDER BY calibrated_at DESC, id DESC
        LIMIT 1
        """,
        (pump_name,),
    ).fetchone()
    tank_fill = conn.execute(
        """
        SELECT ran_at AS baseline_at, new_ml AS baseline_level_ml
        FROM tank_fill_events
        WHERE tank_name = ?
        ORDER BY ran_at DESC, id DESC
        LIMIT 1
        """,
        (tank_name,),
    ).fetchone()
    candidates = [row_to_dict(row) for row in (calibration, tank_fill) if row]
    return max(candidates, key=lambda item: str(item["baseline_at"])) if candidates else None


def latest_calibration(pump_name: str) -> dict | None:
    with connect() as conn:
        row = conn.execute(
            """
            SELECT calibrated_at, measured_level_ml, baseline_at, baseline_level_ml,
                   cycles, nominal_ml, measured_ml, result_value
            FROM pump_calibration_events
            WHERE pump_name = ?
            ORDER BY calibrated_at DESC, id DESC
            LIMIT 1
            """,
            (pump_name,),
        ).fetchone()
        return row_to_dict(row) if row else None


def calibration_status() -> dict:
    with connect() as conn:
        main_baseline = calibration_baseline(conn, "main")
        refill_baseline = calibration_baseline(conn, "refill")
    return {
        "main": {
            "factor": round(main_pump_calibration_factor(), 4),
            "baseline": main_baseline or {},
            "latest": latest_calibration("main") or {},
        },
        "refill": {
            "baseline": refill_baseline or {},
            "latest": latest_calibration("refill") or {},
        },
    }


def measured_level_ml_from_percent(measured_level_percent: int | float, capacity_ml: int, tank_label: str) -> int:
    percent = float(measured_level_percent)
    if not math.isfinite(percent) or not 0 <= percent <= 100:
        raise ValueError(f"Der Füllstand des {tank_label} muss zwischen 0 und 100 Prozent liegen")
    return int(round(capacity_ml * percent / 100))


def calibrate_main_pump(measured_level_percent: int | float) -> dict:
    calibrated_at = now_iso()
    with connect() as conn:
        balcony = conn.execute("SELECT tank_capacity_ml FROM balcony_settings WHERE id = 1").fetchone()
        if not balcony:
            raise ValueError("Der Haupttank ist nicht konfiguriert")
        measured_level = measured_level_ml_from_percent(
            measured_level_percent,
            int(balcony["tank_capacity_ml"]),
            "Haupttanks",
        )
        baseline = calibration_baseline(conn, "main")
        if not baseline:
            raise ValueError("Vor der ersten Kalibrierung den Haupttank einmal als voll markieren")
        watering = conn.execute(
            """
            SELECT COUNT(*) AS cycles, COALESCE(SUM(delivered_ml), 0) AS nominal_ml
            FROM watering_events WHERE ran_at > ? AND ran_at <= ?
            """,
            (baseline["baseline_at"], calibrated_at),
        ).fetchone()
        refilled = conn.execute(
            "SELECT COALESCE(SUM(transferred_ml), 0) AS amount_ml FROM refill_events WHERE ran_at > ? AND ran_at <= ?",
            (baseline["baseline_at"], calibrated_at),
        ).fetchone()
        cycles = int(watering["cycles"])
        nominal_ml = int(watering["nominal_ml"])
        measured_ml = int(baseline["baseline_level_ml"]) + int(refilled["amount_ml"]) - measured_level
        if cycles <= 0 or nominal_ml <= 0:
            raise ValueError("Seit dem letzten Vollstand oder der letzten Kalibrierung wurde kein Bewässerungszyklus verbucht")
        if measured_ml <= 0:
            raise ValueError("Der gemessene Stand ergibt keinen positiven Wasserverbrauch")
        factor = measured_ml / nominal_ml
        if not 0.1 <= factor <= 10:
            raise ValueError("Die Messung ergibt einen unplausiblen Verbrauchsfaktor außerhalb 0,1 bis 10")
        conn.execute(
            "UPDATE balcony_settings SET tank_current_ml = ?, updated_at = ? WHERE id = 1",
            (measured_level, calibrated_at),
        )
        conn.execute(
            """
            INSERT INTO pump_calibration_events
                (calibrated_at, pump_name, measured_level_ml, baseline_at, baseline_level_ml,
                 cycles, nominal_ml, measured_ml, result_value)
            VALUES (?, 'main', ?, ?, ?, ?, ?, ?, ?)
            """,
            (calibrated_at, measured_level, baseline["baseline_at"], baseline["baseline_level_ml"], cycles, nominal_ml, measured_ml, factor),
        )
    save_main_pump_calibration_factor(factor)
    return latest_calibration("main") or {}


def calibrate_refill_pump(measured_level_percent: int | float) -> dict:
    calibrated_at = now_iso()
    with connect() as conn:
        balcony = conn.execute("SELECT refill_tank_capacity_ml FROM balcony_settings WHERE id = 1").fetchone()
        if not balcony:
            raise ValueError("Der Vorratstank ist nicht konfiguriert")
        measured_level = measured_level_ml_from_percent(
            measured_level_percent,
            int(balcony["refill_tank_capacity_ml"]),
            "Vorratstanks",
        )
        baseline = calibration_baseline(conn, "refill")
        if not baseline:
            raise ValueError("Vor der ersten Kalibrierung den Vorratstank einmal als voll markieren")
        refills = conn.execute(
            """
            SELECT COUNT(*) AS cycles, COALESCE(SUM(transferred_ml), 0) AS nominal_ml,
                   COALESCE(SUM(duration_seconds), 0) AS duration_seconds
            FROM refill_events WHERE ran_at > ? AND ran_at <= ?
            """,
            (baseline["baseline_at"], calibrated_at),
        ).fetchone()
        cycles = int(refills["cycles"])
        duration_seconds = int(refills["duration_seconds"])
        measured_ml = int(baseline["baseline_level_ml"]) - measured_level
        if cycles <= 0 or duration_seconds <= 0:
            raise ValueError("Seit dem letzten Vollstand oder der letzten Kalibrierung wurde kein Nachfüllzyklus verbucht")
        if measured_ml <= 0:
            raise ValueError("Der gemessene Stand ergibt keine positive Fördermenge")
        ml_per_min = measured_ml / duration_seconds * 60
        if not 1 <= ml_per_min <= 100000:
            raise ValueError("Die Messung ergibt einen unplausiblen Pumpendurchsatz")
        conn.execute(
            """
            UPDATE balcony_settings
            SET refill_tank_current_ml = ?, refill_pump_ml_per_min = ?, updated_at = ?
            WHERE id = 1
            """,
            (measured_level, round(ml_per_min), calibrated_at),
        )
        conn.execute(
            """
            INSERT INTO pump_calibration_events
                (calibrated_at, pump_name, measured_level_ml, baseline_at, baseline_level_ml,
                 cycles, nominal_ml, measured_ml, result_value)
            VALUES (?, 'refill', ?, ?, ?, ?, ?, ?, ?)
            """,
            (calibrated_at, measured_level, baseline["baseline_at"], baseline["baseline_level_ml"], cycles, int(refills["nominal_ml"]), measured_ml, ml_per_min),
        )
    return latest_calibration("refill") or {}


def get_state() -> dict:
    with connect() as conn:
        balcony = row_to_dict(conn.execute("SELECT * FROM balcony_settings WHERE id = 1").fetchone())
        outlets = [row_to_dict(row) for row in conn.execute("SELECT * FROM pump_outlets ORDER BY ml_per_run")]
        walls = [row_to_dict(row) for row in conn.execute("SELECT * FROM terrace_walls ORDER BY side")]
        catalog = [row_to_dict(row) for row in conn.execute("SELECT * FROM plant_catalog ORDER BY category, name")]
        hoses = irrigation_hoses(conn)
        plants = [
            enrich_plant_connection(row_to_dict(row), hoses)
            for row in conn.execute(
                """
                SELECT
                    plants.*,
                    plant_catalog.name AS catalog_name,
                    plant_catalog.category,
                    plant_catalog.base_ml_per_l_day,
                    plant_catalog.sun_factor,
                    plant_catalog.drought_sensitivity,
                    plant_catalog.crop_coefficient,
                    plant_catalog.canopy_m2_medium,
                    plant_catalog.recommended_pot_liters,
                    plant_catalog.moisture_preference,
                    plant_catalog.notes,
                    pump_outlets.name AS outlet_name,
                    pump_outlets.ml_per_run
                FROM plants
                JOIN plant_catalog ON plant_catalog.id = plants.catalog_id
                JOIN pump_outlets ON pump_outlets.id = plants.outlet_id
                ORDER BY plants.created_at, plants.id
                """
            )
        ]
    return {
        "version": APP_VERSION,
        "balcony": balcony,
        "outlets": outlets,
        "hoses": hoses,
        "walls": walls,
        "catalog": catalog,
        "plants": plants,
        "settings": {
            "watering_amount_percent": round(watering_amount_percent(), 1),
            "main_pump_calibration_factor": round(main_pump_calibration_factor(), 4),
            "refill_automation_enabled": refill_automation_enabled(),
            "refill_schedule_times": refill_schedule_times(),
            "refill_cooldown_minutes_per_liter": refill_cooldown_minutes_per_liter(),
        },
        "calibration": calibration_status(),
        "cycles_completed_today": completed_cycles_today(str(balcony.get("timezone_name", "Europe/Berlin"))),
    }


def watering_events(limit: int = 12) -> list[dict]:
    limit = int(clamp(limit, 1, 50))
    with connect() as conn:
        events = [
            {
                **row_to_dict(row),
                "event_type": "watering",
                "title": "Bewässerung",
                "amount_ml": int(row["delivered_ml"]),
                "duration_seconds": 120,
                "detail": (
                    f"{int(row['delivered_ml'])} ml an Pflanzen · "
                    f"{int(row['actual_consumed_ml'] if row['actual_consumed_ml'] is not None else calibrated_consumption_ml(row['delivered_ml']))} ml Tankverbrauch"
                ),
            }
            for row in conn.execute(
                """
                SELECT id, ran_at, delivered_ml, actual_consumed_ml, temperature_c, rain_mm, source
                FROM watering_events
                """
            )
        ]
        events.extend(
            {
                **row_to_dict(row),
                "event_type": "refill",
                "title": "Nachfüllung",
                "amount_ml": int(row["transferred_ml"]),
                "temperature_c": None,
                "rain_mm": None,
                "detail": refill_event_detail(row),
            }
            for row in conn.execute(
                """
                SELECT id, ran_at, target_date, requested_ml, transferred_ml, duration_seconds, window_label, source
                FROM refill_events
                """
            )
        )
        events.extend(
            {
                **row_to_dict(row),
                "event_type": "tank_fill",
                "title": "Tank voll markiert",
                "amount_ml": max(0, int(row["new_ml"]) - int(row["previous_ml"])),
                "duration_seconds": 0,
                "temperature_c": None,
                "rain_mm": None,
                "detail": f"{tank_label(row['tank_name'])}: {format_liters_for_text(row['previous_ml'])} -> {format_liters_for_text(row['new_ml'])}",
            }
            for row in conn.execute(
                """
                SELECT id, ran_at, tank_name, previous_ml, new_ml, capacity_ml, source
                FROM tank_fill_events
                """
            )
        )
    return sorted(events, key=lambda item: (item["ran_at"], int(item["id"])), reverse=True)[:limit]


def refill_event_detail(row: sqlite3.Row) -> str:
    source = str(row["source"])
    window = str(row["window_label"] or "").strip()
    parts = [
        f"{int(row['transferred_ml'])} ml nachgefüllt",
        f"{int(row['duration_seconds'])} s",
    ]
    if source == "manual":
        parts.append("manuell")
    elif window:
        parts.append(window)
    return " · ".join(parts)


def tank_label(tank_name: str) -> str:
    return {"main": "Haupttank", "refill": "Vorratstank"}.get(str(tank_name), str(tank_name))


def completed_cycles_today(timezone_name: str = "Europe/Berlin") -> int:
    now = local_now(timezone_name)
    start, end = local_day_utc_bounds(now.date(), now.tzinfo)
    with connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS count FROM watering_events WHERE ran_at >= ? AND ran_at < ?",
            (start.isoformat(), end.isoformat()),
        ).fetchone()
        return int(row["count"])


def local_day_utc_bounds(day: date, tzinfo) -> tuple[datetime, datetime]:
    start_local = datetime.combine(day, time(0, 0), tzinfo=tzinfo)
    end_local = start_local + timedelta(days=1)
    return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)


def delivered_ml_for_local_date(day: date, timezone_name: str = "Europe/Berlin") -> int:
    tzinfo = local_now(timezone_name).tzinfo
    start, end = local_day_utc_bounds(day, tzinfo)
    with connect() as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(delivered_ml), 0) AS delivered_ml FROM watering_events WHERE ran_at >= ? AND ran_at < ?",
            (start.isoformat(), end.isoformat()),
        ).fetchone()
        return int(row["delivered_ml"])


def refill_event_for_target_date(target_date: date, window_label: str = "") -> dict | None:
    with connect() as conn:
        where = "target_date = ?"
        params: tuple = (target_date.isoformat(),)
        if window_label:
            where += " AND window_label = ?"
            params = (target_date.isoformat(), window_label)
        row = conn.execute(
            f"""
            SELECT id, ran_at, target_date, requested_ml, transferred_ml, duration_seconds, window_label, source
            FROM refill_events
            WHERE {where}
            ORDER BY ran_at DESC, id DESC
            LIMIT 1
            """,
            params,
        ).fetchone()
        return row_to_dict(row) if row else None


def latest_watering_run_at() -> str:
    with connect() as conn:
        row = conn.execute(
            "SELECT ran_at FROM watering_events ORDER BY ran_at DESC, id DESC LIMIT 1"
        ).fetchone()
        return str(row["ran_at"]) if row else ""


def clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


def orientation_factor(orientation: str) -> float:
    return {
        "north": 0.78,
        "east": 0.96,
        "south": 1.22,
        "west": 1.1,
        "southwest": 1.28,
        "southeast": 1.16,
    }.get(orientation, 1.0)


def orientation_degrees(balcony: dict) -> float:
    value = balcony.get("orientation_deg")
    if value is not None:
        return float(value) % 360
    return {
        "north": 0,
        "east": 90,
        "south": 180,
        "west": 270,
        "southeast": 135,
        "southwest": 225,
    }.get(balcony.get("orientation", "south"), 180)


def orientation_exposure_factor(degrees: float) -> float:
    south_distance = angular_distance(degrees, 180)
    west_bonus = 0.08 if 190 <= degrees <= 290 else 0
    return clamp(1.28 - south_distance / 260 + west_bonus, 0.72, 1.35)


def temp_factor(temperature_c: float) -> float:
    if temperature_c < 12:
        return 0.45
    if temperature_c < 18:
        return 0.7
    if temperature_c < 24:
        return 1.0
    if temperature_c < 29:
        return 1.24
    if temperature_c < 34:
        return 1.55
    return 1.85


def size_factor(size: str) -> float:
    return {"small": 0.72, "medium": 1.0, "large": 1.34, "tree": 1.65}.get(size, 1.0)


def canopy_size_factor(size: str) -> float:
    return {"small": 0.55, "medium": 1.0, "large": 1.55, "tree": 2.25}.get(size, 1.0)


def pot_factor(pot_type: str) -> float:
    return {
        "reservoir": 0.72,
        "overflow": 0.9,
        "reservoir_overflow": 0.66,
        "closed": 0.82,
    }.get(pot_type, 1.0)


def pot_irrigation_efficiency_factor(pot_type: str) -> float:
    return {
        "reservoir": 0.84,
        "overflow": 1.0,
        "reservoir_overflow": 0.8,
        "closed": 0.78,
    }.get(pot_type, 1.0)


def pot_surface_area_m2(pot_liters: float) -> float:
    return clamp(0.018 * max(pot_liters, 1) ** 0.55, 0.025, 0.32)


def estimate_reference_et0_mm(temperature_c: float, sunshine_hours: float | None, wind_kmh: float) -> float:
    sunshine = sunshine_hours if sunshine_hours is not None else 5.0
    et0 = 0.95 + max(0, temperature_c - 7) * 0.12 + clamp(sunshine, 0, 15) * 0.23 + clamp(wind_kmh, 0, 45) * 0.035
    if temperature_c < 10:
        et0 *= 0.65
    return round(clamp(et0, 0.4, 9.5), 2)


def wind_exposure_factor(wind_kmh: float, walls: list[dict]) -> float:
    wall_average = sum(float(wall["height_m"]) for wall in walls) / max(len(walls), 1)
    shelter = clamp(wall_average / 1.8, 0, 0.65)
    wind = clamp(wind_kmh, 0, 65)
    if wind <= 8:
        wind_lift = wind * 0.006
    elif wind <= 25:
        wind_lift = 0.048 + (wind - 8) * 0.012
    else:
        wind_lift = 0.252 + (wind - 25) * 0.018
    return round(1 + wind_lift * (1 - shelter), 3)


def plant_canopy_area_m2(plant: dict) -> float:
    pot_ratio = max(float(plant["pot_liters"]), 1) / max(float(plant["recommended_pot_liters"]), 1)
    pot_limit_factor = clamp(pot_ratio ** 0.38, 0.45, 1.35)
    return round(
        float(plant["canopy_m2_medium"]) * canopy_size_factor(plant["size"]) * pot_limit_factor,
        3,
    )


def seasonal_curve_value(points: list[tuple[int, float]], day_of_year: int) -> float:
    day = max(1, min(366, day_of_year))
    for index in range(1, len(points)):
        prev_day, prev_value = points[index - 1]
        next_day, next_value = points[index]
        if day <= next_day:
            span = max(1, next_day - prev_day)
            progress = (day - prev_day) / span
            return prev_value + (next_value - prev_value) * progress
    return points[-1][1]


def seasonal_profile_key(plant: dict) -> str:
    catalog_id = plant.get("catalog_id")
    category = plant.get("category", "")
    if catalog_id in {"tomato", "zucchini", "cucumber", "eggplant", "chili", "basil"}:
        return "warm_annual"
    if category in {"Gemüse", "Kräuter", "Blühpflanzen", "Kletterpflanzen"}:
        return "annual"
    if category in {"Mediterrane Gehölze", "Obstgehölze", "Beerenobst", "Nadelgehölze"}:
        return "evergreen"
    if category == "Sukkulenten":
        return "succulent"
    return "woody"


def seasonal_water_factor(plant: dict, when: date | None = None) -> dict:
    current_day = (when or date.today()).timetuple().tm_yday
    profile = seasonal_profile_key(plant)
    factor = seasonal_curve_value(SEASONAL_WATER_CURVES[profile], current_day)
    if plant.get("size") == "small" and current_day < 190 and profile in {"warm_annual", "annual"}:
        factor *= 0.78
    elif plant.get("size") == "medium" and current_day < 170 and profile in {"warm_annual", "annual"}:
        factor *= 0.9
    return {
        "factor": round(clamp(factor, 0.18, 1.05), 3),
        "profile": profile,
        "day_of_year": current_day,
    }


def plant_water_need_ml(
    plant: dict,
    plant_sun: dict,
    terrace_sun: dict,
    et0_mm: float,
    rain_mm_effective: float,
    temperature_c: float,
    wind_factor: float,
    calibration_factor: float | None = None,
    target_date: date | None = None,
) -> dict:
    canopy_area = plant_canopy_area_m2(plant)
    season = seasonal_water_factor(plant, target_date)
    sun_ratio = clamp(plant_sun["sun_hours"] / max(terrace_sun["theoretical_sun_hours"], 1), 0.15, 1.0)
    exposure_multiplier = clamp(0.55 + sun_ratio * 0.75, 0.55, 1.32) * plant_sun["wall_shade_factor"]
    growth_temperature = 0.55 if temperature_c < 12 else 0.82 if temperature_c < 16 else 1.0
    if temperature_c > 32:
        growth_temperature *= 1.08

    transpiration_ml = (
        et0_mm
        * float(plant["crop_coefficient"])
        * canopy_area
        * 1000
        * exposure_multiplier
        * growth_temperature
        * float(plant["moisture_preference"])
        * wind_factor
    )
    pot_wind_factor = 1 + (wind_factor - 1) * clamp(1.15 - max(float(plant["pot_liters"]), 1) / 45, 0.25, 1.05)
    substrate_evaporation_ml = (
        et0_mm
        * pot_surface_area_m2(float(plant["pot_liters"]))
        * 1000
        * clamp(0.2 + sun_ratio * 0.34, 0.2, 0.55)
        * pot_irrigation_efficiency_factor(plant["pot_type"])
        * pot_wind_factor
    )
    irrigation_need_ml = (transpiration_ml + substrate_evaporation_ml) * pot_irrigation_efficiency_factor(
        plant["pot_type"]
    )
    rain_capture_area = pot_surface_area_m2(float(plant["pot_liters"])) + canopy_area * 0.18
    rain_credit_ml = rain_mm_effective * rain_capture_area * 1000
    raw_daily_need_ml = max(0, irrigation_need_ml - rain_credit_ml)
    calibration_factor = calibration_factor if calibration_factor is not None else water_model_calibration()
    daily_need_ml = raw_daily_need_ml * calibration_factor * season["factor"]
    return {
        "daily_need_ml": daily_need_ml,
        "raw_daily_need_ml": raw_daily_need_ml,
        "calibration_factor": calibration_factor,
        "seasonal_factor": season["factor"],
        "seasonal_profile": season["profile"],
        "seasonal_day_of_year": season["day_of_year"],
        "canopy_area_m2": canopy_area,
        "transpiration_ml": transpiration_ml,
        "substrate_evaporation_ml": substrate_evaporation_ml,
        "rain_credit_ml": rain_credit_ml,
    }


def side_center_degrees(side: str) -> int:
    return {"north": 0, "east": 90, "south": 180, "west": 270}.get(side, 180)


def angular_distance(a: float, b: float) -> float:
    return abs((a - b + 180) % 360 - 180)


def solar_position(latitude: float, longitude: float, when: datetime) -> tuple[float, float]:
    day = when.timetuple().tm_yday
    hour = when.hour + when.minute / 60
    declination = math.radians(23.44 * math.sin(math.radians((360 / 365) * (day - 81))))
    latitude_rad = math.radians(latitude)
    solar_time = hour + longitude / 15
    hour_angle = math.radians(15 * (solar_time - 12))
    altitude = math.asin(
        math.sin(latitude_rad) * math.sin(declination)
        + math.cos(latitude_rad) * math.cos(declination) * math.cos(hour_angle)
    )
    azimuth = math.degrees(
        math.atan2(
            math.sin(hour_angle),
            math.cos(hour_angle) * math.sin(latitude_rad) - math.tan(declination) * math.cos(latitude_rad),
        )
    )
    return math.degrees(altitude), (azimuth + 180) % 360


def wall_distance_m(side: str, x: float, y: float, width_m: float, depth_m: float) -> float:
    return {
        "north": y * depth_m,
        "south": (1 - y) * depth_m,
        "west": x * width_m,
        "east": (1 - x) * width_m,
    }.get(side, depth_m)


def wall_shadow_block(
    side: str,
    height_m: float,
    x: float,
    y: float,
    width_m: float,
    depth_m: float,
    altitude: float,
    azimuth: float,
) -> float:
    if height_m <= 0 or altitude <= 0:
        return 0.0
    if angular_distance(azimuth, side_center_degrees(side)) > 65:
        return 0.0
    shadow_length = height_m / max(math.tan(math.radians(altitude)), 0.05)
    distance = wall_distance_m(side, x, y, width_m, depth_m)
    if shadow_length <= distance:
        return 0.0
    return clamp((shadow_length - distance) / max(shadow_length, 0.1), 0.2, 0.95)


def estimate_sun_hours(
    balcony: dict,
    walls: list[dict],
    target_date: date | None = None,
    position: tuple[float, float] = (0.5, 0.5),
) -> dict:
    target_date = target_date or datetime.now(timezone.utc).date()
    latitude = float(balcony.get("latitude", DEFAULT_BALCONY["latitude"]))
    longitude = float(balcony.get("longitude", DEFAULT_BALCONY["longitude"]))
    wall_map = {wall["side"]: float(wall["height_m"]) for wall in walls}
    terrace_side = orientation_degrees(balcony)
    x, y = position
    width_m = max(float(balcony["width_m"]), 0.4)
    depth_m = max(float(balcony["depth_m"]), 0.4)
    open_hours = 0.0
    theoretical_hours = 0.0
    shade_load = 0.0

    for hour in range(5, 22):
        when = datetime(target_date.year, target_date.month, target_date.day, hour, tzinfo=timezone.utc)
        altitude, azimuth = solar_position(latitude, longitude, when)
        if altitude <= 0:
            continue
        theoretical_hours += 1
        if angular_distance(azimuth, terrace_side) > 105:
            continue

        blocked = 0.0
        for side, height in wall_map.items():
            blocked = max(blocked, wall_shadow_block(side, height, x, y, width_m, depth_m, altitude, azimuth))
        open_hours += 1 - blocked
        shade_load += blocked

    exposure_ratio = 0 if theoretical_hours == 0 else clamp(open_hours / theoretical_hours, 0, 1.25)
    return {
        "date": target_date.isoformat(),
        "sun_hours": round(open_hours, 1),
        "theoretical_sun_hours": round(theoretical_hours, 1),
        "exposure_factor": round(0.75 + exposure_ratio * 0.65, 2),
        "wall_shade_factor": round(1 - clamp(shade_load / max(theoretical_hours, 1), 0, 0.65) * 0.28, 2),
    }


def fetch_weather(balcony: dict) -> dict:
    latitude = float(balcony.get("latitude", DEFAULT_BALCONY["latitude"]))
    longitude = float(balcony.get("longitude", DEFAULT_BALCONY["longitude"]))
    timezone_name = balcony.get("timezone_name") or "Europe/Berlin"
    params = {
        "latitude": latitude,
        "longitude": longitude,
        "timezone": timezone_name,
        "current": "temperature_2m,precipitation,rain,wind_speed_10m",
        "daily": "precipitation_sum,temperature_2m_max,sunshine_duration,et0_fao_evapotranspiration,wind_speed_10m_max",
        "forecast_days": WEATHER_FORECAST_DAYS,
    }
    url = "https://api.open-meteo.com/v1/forecast?" + urlencode(params)
    tls_verified = True
    try:
        with urlopen(url, timeout=8) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except ssl.SSLCertVerificationError:
        tls_verified = False
        with urlopen(url, timeout=8, context=ssl._create_unverified_context()) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except URLError as exc:
        if isinstance(exc.reason, ssl.SSLCertVerificationError):
            tls_verified = False
            with urlopen(url, timeout=8, context=ssl._create_unverified_context()) as response:
                payload = json.loads(response.read().decode("utf-8"))
        else:
            raise ValueError(f"Wetterdaten konnten nicht abgerufen werden: {exc}") from exc
    except (OSError, URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise ValueError(f"Wetterdaten konnten nicht abgerufen werden: {exc}") from exc

    current = payload.get("current", {})
    daily = payload.get("daily", {})
    sunshine_seconds = first_value(daily, "sunshine_duration", 0)
    forecast = daily_forecast_items(daily, current)
    fallback_temperature = number_or_default(first_value(daily, "temperature_2m_max", 20), 20)
    fallback_rain = number_or_default(first_value(daily, "precipitation_sum", 0), 0)
    fallback_et0 = number_or_default(first_value(daily, "et0_fao_evapotranspiration", 0), 0)
    return {
        "source": "open-meteo",
        "latitude": latitude,
        "longitude": longitude,
        "timezone": timezone_name,
        "temperature_c": number_or_default(current.get("temperature_2m"), fallback_temperature),
        "rain_mm": fallback_rain,
        "current_rain_mm": number_or_default(current.get("rain", current.get("precipitation")), 0),
        "wind_kmh": number_or_default(current.get("wind_speed_10m"), 0),
        "sunshine_hours": round(float(sunshine_seconds) / 3600, 1),
        "et0_mm": fallback_et0,
        "forecast": forecast,
        "tls_verified": tls_verified,
        "fetched_at": now_iso(),
    }


def daily_forecast_items(daily: dict, current: dict | None = None) -> list[dict]:
    current = current or {}
    dates = daily.get("time", [])
    if not isinstance(dates, list):
        return []
    fallback_temperature = number_or_default(current.get("temperature_2m"), 20)
    fallback_rain = number_or_default(current.get("rain", current.get("precipitation")), 0)
    fallback_wind = number_or_default(current.get("wind_speed_10m"), 0)
    items = []
    for index, day_value in enumerate(dates):
        sunshine_seconds = indexed_number(daily, "sunshine_duration", index, 0)
        items.append(
            {
                "date": str(day_value),
                "temperature_c": indexed_number(daily, "temperature_2m_max", index, fallback_temperature),
                "rain_mm": indexed_number(daily, "precipitation_sum", index, fallback_rain),
                "wind_kmh": indexed_number(daily, "wind_speed_10m_max", index, fallback_wind),
                "sunshine_hours": round(sunshine_seconds / 3600, 1),
                "et0_mm": indexed_number(daily, "et0_fao_evapotranspiration", index, 0),
            }
        )
    return items


def indexed_number(payload: dict, key: str, index: int, default: float) -> float:
    return number_or_default(indexed_value(payload, key, index, default), default)


def indexed_value(payload: dict, key: str, index: int, default: float) -> float:
    value = payload.get(key, default)
    if isinstance(value, list):
        if index < len(value) and value[index] is not None:
            return value[index]
        return default
    return default if value is None else value


def number_or_default(value, default: float) -> float:
    if value is None:
        return float(default)
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def first_value(payload: dict, key: str, default: float) -> float:
    value = payload.get(key, default)
    if isinstance(value, list):
        return value[0] if value and value[0] is not None else default
    return default if value is None else value


def current_weather_or_params(params: dict[str, list[str]] | dict, balcony: dict) -> dict:
    auto = truthy(first(params, "auto", "false")) if isinstance(params, dict) else False
    has_manual = any(name in params for name in ["temperature_c", "rain_mm", "wind_kmh"])
    if auto or not has_manual:
        return fetch_weather(balcony)
    return {
        "source": "manual",
        "temperature_c": float(first(params, "temperature_c", "20")),
        "rain_mm": float(first(params, "rain_mm", "0")),
        "wind_kmh": float(first(params, "wind_kmh", "0")),
        "sunshine_hours": float(first(params, "sunshine_hours", "0")),
        "et0_mm": float(first(params, "et0_mm", "0")),
    }


def truthy(value: str) -> bool:
    return value.lower() in {"1", "true", "yes", "ja", "on"}


def weather_from_query(params: dict[str, list[str]]) -> dict:
    balcony = get_state()["balcony"]
    auto = truthy(first(params, "auto", "false"))
    has_manual = any(name in params for name in ["temperature_c", "rain_mm", "wind_kmh"])
    if auto or not has_manual:
        return fetch_weather(balcony)
    return {
        "source": "manual",
        "temperature_c": float(first(params, "temperature_c", "20")),
        "rain_mm": float(first(params, "rain_mm", "0")),
        "wind_kmh": float(first(params, "wind_kmh", "0")),
        "sunshine_hours": float(first(params, "sunshine_hours", "0")),
        "et0_mm": float(first(params, "et0_mm", "0")),
    }


def weather_from_payload(payload: dict) -> dict:
    balcony = get_state()["balcony"]
    auto = bool(payload.get("auto_weather")) or not {"temperature_c", "rain_mm"}.intersection(payload)
    if auto:
        return fetch_weather(balcony)
    return {
        "source": "manual",
        "temperature_c": float(payload.get("temperature_c", 20)),
        "rain_mm": float(payload.get("rain_mm", 0)),
        "wind_kmh": float(payload.get("wind_kmh", 0)),
        "sunshine_hours": float(payload.get("sunshine_hours", 0)),
        "et0_mm": float(payload.get("et0_mm", 0)),
    }


def evaluate_weather(weather: dict, slot: str = "morning") -> dict:
    result = evaluate(
        temperature_c=float(weather["temperature_c"]),
        rain_mm=float(weather["rain_mm"]),
        wind_kmh=float(weather.get("wind_kmh", 0)),
        slot=slot,
        sunshine_hours=float(weather["sunshine_hours"]) if weather.get("sunshine_hours") is not None else None,
        weather_source=str(weather.get("source", "manual")),
        et0_mm=float(weather.get("et0_mm", 0) or 0),
    )
    result["weather"] = weather
    result["depletion"] = depletion_forecast(result, weather)
    result["manual_refill"] = manual_refill_status(result)
    return result


def shortcut_blueprint(base_url: str) -> dict:
    check_url = f"{base_url.rstrip('/')}/api/homekit/check?auto=true&slot=morning"
    mark_url = f"{base_url.rstrip('/')}/api/homekit/mark-run"
    manual_run_url = f"{base_url.rstrip('/')}/api/manual-run"
    return {
        "name": "Terrassenbewässerung prüfen",
        "base_url_placeholder": base_url,
        "steps": [
            {"action": "URL", "value": check_url},
            {"action": "Inhalte von URL abrufen", "method": "GET", "headers": {"Accept": "application/json"}},
            {"action": "Wert für Schlüssel abrufen", "key": "run_now"},
            {"action": "Wenn", "condition": "run_now ist wahr"},
            {"action": "HomeKit", "value": "Pumpe einschalten"},
            {"action": "Warten", "seconds": 65},
            {"action": "HomeKit", "value": "Pumpe ausschalten"},
            {
                "action": "Inhalte von URL abrufen",
                "method": "POST",
                "url": mark_url,
                "headers": {"Content-Type": "application/json"},
                "body": {"auto_weather": True, "slot": "morning"},
            },
            {"action": "Ende Wenn"},
        ],
        "check_url": check_url,
        "mark_run_url": mark_url,
        "manual_run_url": manual_run_url,
        "manual_run_steps": [
            {"action": "URL", "value": manual_run_url},
            {
                "action": "Inhalte von URL abrufen",
                "method": "POST",
                "url": manual_run_url,
                "headers": {"Content-Type": "application/json"},
                "body": {"auto_weather": True},
            },
        ],
    }


def rain_credit_factor(rain_mm: float, orientation_deg: float, walls: list[dict]) -> float:
    exposure = clamp(0.62 - angular_distance(orientation_deg, 225) / 520, 0.32, 0.68)
    wall_block = clamp(sum(float(wall["height_m"]) for wall in walls) / 8, 0, 0.55)
    return max(0.08, rain_mm * exposure * (1 - wall_block))


def outlet_delivery_options(outlets: list[dict]) -> list[dict]:
    return sorted(outlets, key=lambda outlet: outlet["ml_per_run"])


def overwater_tolerance_ml(plant: dict) -> float:
    base = float(plant["pot_liters"]) * {
        "reservoir_overflow": 55,
        "reservoir": 45,
        "overflow": 34,
        "closed": 18,
    }.get(plant["pot_type"], 25)
    return base / max(float(plant["drought_sensitivity"]), 0.5)


def optimize_routing(plants: list[dict], outlets: list[dict], max_cycles: int = 96) -> dict:
    if not plants:
        return {"cycles": 0, "score": 0, "assignments": [], "by_outlet": [], "outlet_limits": {}}

    options = outlet_delivery_options(outlets)
    outlet_limits = {outlet["id"]: 12 for outlet in options}
    best: dict | None = None
    for cycles in range(1, max_cycles + 1):
        option_sets = [
            plant_tube_options(plant, options, cycles, max_total_tubes=current_tube_count(plant))
            for plant in plants
        ]
        candidate = choose_tube_assignments(plants, option_sets, outlet_limits, cycles)
        if candidate is None:
            continue
        assignments = candidate["assignments"]
        score = cycles * 0.35 + candidate["score"]
        hard_under = candidate["hard_underwatered"]
        severe_over = candidate["severely_overwatered"]
        score += hard_under * 2500 + severe_over * 900
        candidate = {
            "cycles": cycles,
            "score": round(score, 2),
            "assignments": assignments,
            "hard_underwatered": hard_under,
            "severely_overwatered": severe_over,
        }
        if best is None or candidate["score"] < best["score"]:
            best = candidate

    if best is None:
        best = fallback_single_tube_plan(plants, options, max_cycles)

    finalize_routing_plan(best, outlets, best["cycles"])
    best["summary"] = routing_summary(best)
    return best


def finalize_routing_plan(plan: dict, outlets: list[dict], cycles: int) -> dict:
    outlet_limits = {outlet["id"]: 12 for outlet in outlets}
    grouped: dict[int, dict] = {}
    for assignment in plan["assignments"]:
        for tube in assignment["tubes"]:
            delivered_by_tube = cycles * tube["ml_per_run"] * tube["count"]
            outlet = grouped.setdefault(
                tube["outlet_id"],
                {
                    "outlet_id": tube["outlet_id"],
                    "name": tube["outlet_name"],
                    "ml_per_run": tube["ml_per_run"],
                    "plants": [],
                    "need_ml": 0,
                    "delivered_ml": 0,
                    "connections_used": 0,
                    "connections_limit": outlet_limits.get(tube["outlet_id"], 12),
                },
            )
            outlet["plants"].append(f"{assignment['plant_name']} ({tube['count']}x)")
            outlet["connections_used"] += tube["count"]
            outlet["need_ml"] += assignment["need_ml"]
            outlet["delivered_ml"] += delivered_by_tube

    plan["cycles"] = cycles
    plan["by_outlet"] = sorted(grouped.values(), key=lambda item: item["ml_per_run"])
    plan["outlet_limits"] = outlet_limits
    return plan


def plant_tube_options(
    plant: dict,
    outlets: list[dict],
    cycles: int,
    max_total_tubes: int = 9,
    tube_penalty: float = 22,
) -> list[dict]:
    need = float(plant["need_ml"])
    tolerance = overwater_tolerance_ml(plant)
    per_cycle_target = 0 if cycles == 0 else need / cycles
    outlet_by_ml = {int(outlet["ml_per_run"]): outlet for outlet in outlets}
    options = []
    for count_15 in range(0, 5):
        for count_30 in range(0, 4):
            for count_60 in range(0, 3):
                if count_15 + count_30 + count_60 == 0:
                    continue
                if count_15 + count_30 + count_60 > max_total_tubes:
                    continue
                ml_per_cycle = count_15 * 15 + count_30 * 30 + count_60 * 60
                if ml_per_cycle > max(per_cycle_target * 2.4, per_cycle_target + 90):
                    continue
                delivered = cycles * ml_per_cycle
                under = max(0, need - delivered)
                over = max(0, delivered - need)
                tube_count = count_15 + count_30 + count_60
                option_score = under * 7.5 + max(0, under - need * 0.08) * 14
                option_score += over * 1.35 + max(0, over - tolerance) * 5.5
                option_score += abs(ml_per_cycle - per_cycle_target) * 0.45
                option_score += max(0, tube_count - 2) * tube_penalty
                tubes = []
                connections = {}
                for ml_per_run, count in [(15, count_15), (30, count_30), (60, count_60)]:
                    if count == 0 or ml_per_run not in outlet_by_ml:
                        continue
                    outlet = outlet_by_ml[ml_per_run]
                    tubes.append(
                        {
                            "outlet_id": outlet["id"],
                            "outlet_name": outlet["name"],
                            "ml_per_run": outlet["ml_per_run"],
                            "count": count,
                        }
                    )
                    connections[outlet["id"]] = count
                if not tubes:
                    continue
                options.append(
                    {
                        "plant_id": plant["id"],
                        "plant_name": plant["name"],
                        "catalog_name": plant["catalog_name"],
                        "tubes": tubes,
                        "connections": connections,
                        "ml_per_cycle": ml_per_cycle,
                        "need_ml": round(need),
                        "delivered_ml": round(delivered),
                        "difference_ml": round(delivered - need),
                        "under_ml": round(under),
                        "over_ml": round(over),
                        "score": option_score,
                    }
                )
    return sorted(options, key=lambda item: item["score"])[:24]


def current_tube_count(plant: dict) -> int:
    hose_numbers = str(plant.get("current_hose_numbers") or plant.get("hose_numbers") or "").strip()
    if hose_numbers:
        return max(1, len(re.findall(r"\d+", hose_numbers)))
    target_ml = plant.get("current_target_ml_per_cycle", plant.get("target_ml_per_cycle"))
    if target_ml not in (None, ""):
        smallest_ml = 15
        return max(1, math.ceil(float(target_ml) / smallest_ml))
    return 1


def choose_tube_assignments(
    plants: list[dict], option_sets: list[list[dict]], outlet_limits: dict[int, int], cycles: int
) -> dict | None:
    ordered = sorted(
        zip(plants, option_sets),
        key=lambda item: (len(item[1]), -float(item[0]["need_ml"])),
    )
    used: dict[int, int] = {}
    assignments = []
    score = cycles * 0.02
    hard_under = 0
    severe_over = 0

    for plant, options in ordered:
        chosen = None
        for option in options:
            if all(
                used.get(outlet_id, 0) + count <= outlet_limits.get(outlet_id, 12)
                for outlet_id, count in option["connections"].items()
            ):
                chosen = option
                break
        if chosen is None:
            return None
        for outlet_id, count in chosen["connections"].items():
            used[outlet_id] = used.get(outlet_id, 0) + count
        need = float(plant["need_ml"])
        if chosen["under_ml"] > max(25, round(need * 0.12)):
            hard_under += 1
        if chosen["over_ml"] > round(overwater_tolerance_ml(plant)):
            severe_over += 1
        score += chosen["score"]
        assignments.append(normalize_assignment(chosen))

    return {
        "assignments": assignments,
        "score": score,
        "hard_underwatered": hard_under,
        "severely_overwatered": severe_over,
    }


def normalize_assignment(assignment: dict) -> dict:
    primary_tube = max(assignment["tubes"], key=lambda tube: tube["ml_per_run"])
    tube_label = " + ".join(
        f"{tube['count']}x {tube['ml_per_run']} ml" for tube in sorted(assignment["tubes"], key=lambda item: item["ml_per_run"])
    )
    return {
        **assignment,
        "outlet_id": primary_tube["outlet_id"],
        "outlet_name": primary_tube["outlet_name"],
        "ml_per_run": assignment["ml_per_cycle"],
        "tube_label": tube_label,
    }


def fallback_single_tube_plan(plants: list[dict], outlets: list[dict], max_cycles: int) -> dict:
    cycles = max_cycles
    assignments = []
    hard_under = 0
    severe_over = 0
    score = 0.0
    outlet_usage: dict[int, int] = {}
    sorted_outlets = sorted(outlets, key=lambda outlet: outlet["ml_per_run"], reverse=True)
    unassigned_plants = []
    for plant in plants:
        chosen_outlet = next(
            (outlet for outlet in sorted_outlets if outlet_usage.get(outlet["id"], 0) < 12),
            None,
        )
        if chosen_outlet is None:
            hard_under += 1
            score += 5000
            unassigned_plants.append(plant["name"])
            continue
        outlet_usage[chosen_outlet["id"]] = outlet_usage.get(chosen_outlet["id"], 0) + 1
        need = float(plant["need_ml"])
        delivered = cycles * chosen_outlet["ml_per_run"]
        assignment = normalize_assignment(
            {
                "plant_id": plant["id"],
                "plant_name": plant["name"],
                "catalog_name": plant["catalog_name"],
                "tubes": [
                    {
                        "outlet_id": chosen_outlet["id"],
                        "outlet_name": chosen_outlet["name"],
                        "ml_per_run": chosen_outlet["ml_per_run"],
                        "count": 1,
                    }
                ],
                "connections": {chosen_outlet["id"]: 1},
                "ml_per_cycle": chosen_outlet["ml_per_run"],
                "need_ml": round(need),
                "delivered_ml": round(delivered),
                "difference_ml": round(delivered - need),
                "under_ml": round(max(0, need - delivered)),
                "over_ml": round(max(0, delivered - need)),
                "score": abs(delivered - need),
            }
        )
        if assignment["under_ml"] > max(25, round(need * 0.12)):
            hard_under += 1
        if assignment["over_ml"] > round(overwater_tolerance_ml(plant)):
            severe_over += 1
        score += assignment["score"]
        assignments.append(assignment)
    return {
        "cycles": cycles,
        "score": round(score, 2),
        "assignments": assignments,
        "hard_underwatered": hard_under,
        "severely_overwatered": severe_over,
        "unassigned_plants": unassigned_plants,
    }


def routing_summary(plan: dict) -> str:
    if plan.get("unassigned_plants"):
        return "Warnung: Für mindestens eine Pflanze ist kein freier Anschluss mehr verfügbar."
    if plan["hard_underwatered"]:
        return "Warnung: mindestens eine Pflanze bekäme trotz Vorschlag zu wenig Wasser."
    if plan["severely_overwatered"]:
        return "Warnung: mindestens eine Pflanze bekäme deutlich mehr Wasser als empfohlen."
    return "Ausgewogenste Kombination aus Zyklen und 15/30/60-ml-Ausgängen."


def calculate_plant_results(
    balcony: dict,
    walls: list[dict],
    plants: list[dict],
    temperature_c: float,
    rain_mm: float,
    wind_kmh: float,
    sunshine_hours: float | None,
    slot: str = "morning",
    et0_mm: float = 0,
    target_date: date | None = None,
) -> dict:
    slot_multiplier = {"morning": 1.0, "midday": 0.72, "evening": 0.92}.get(slot, 1.0)
    orientation_deg = orientation_degrees(balcony)
    target_date = target_date or local_now(str(balcony.get("timezone_name", "Europe/Berlin"))).date()
    terrace_sun = estimate_sun_hours(balcony, walls, target_date=target_date)
    reference_et0_mm = et0_mm if et0_mm > 0 else estimate_reference_et0_mm(temperature_c, sunshine_hours, wind_kmh)
    wind_factor = wind_exposure_factor(wind_kmh, walls)
    orientation_multiplier = orientation_exposure_factor(orientation_deg)
    rain_credit_mm = rain_credit_factor(rain_mm, orientation_deg, walls)
    calibration_factor = water_model_calibration()

    plant_results = []
    total_need_ml = 0
    for plant in plants:
        plant_sun = estimate_sun_hours(
            balcony,
            walls,
            target_date=target_date,
            position=(float(plant["pos_x"]), float(plant["pos_y"])),
        )
        water_need = plant_water_need_ml(
            plant,
            plant_sun,
            terrace_sun,
            reference_et0_mm * orientation_multiplier * plant["sun_factor"],
            rain_credit_mm,
            temperature_c,
            wind_factor,
            calibration_factor,
            target_date,
        )
        daily_need = water_need["daily_need_ml"]
        scheduled_need = daily_need * slot_multiplier
        total_need_ml += scheduled_need

        plant_results.append(
            {
                "id": plant["id"],
                "name": plant["custom_name"],
                "catalog_name": plant["catalog_name"],
                "pot_liters": plant["pot_liters"],
                "pot_type": plant["pot_type"],
                "drought_sensitivity": plant["drought_sensitivity"],
                "current_outlet": plant["outlet_name"],
                "current_ml_per_run": plant["ml_per_run"],
                "current_hose_numbers": plant["hose_numbers"],
                "current_hoses": plant["hoses"],
                "current_target_ml_per_cycle": plant["configured_ml_per_cycle"],
                "current_tube_count": current_tube_count(plant),
                "position": {"x": plant["pos_x"], "y": plant["pos_y"]},
                "need_ml": round(scheduled_need),
                "daily_need_ml": round(daily_need),
                "water_model": {
                    "reference_et0_mm": reference_et0_mm,
                    "canopy_area_m2": water_need["canopy_area_m2"],
                    "raw_daily_need_ml": round(water_need["raw_daily_need_ml"]),
                    "calibrated_daily_need_ml": round(daily_need),
                    "calibration_factor": water_need["calibration_factor"],
                    "seasonal_factor": water_need["seasonal_factor"],
                    "seasonal_profile": water_need["seasonal_profile"],
                    "seasonal_day_of_year": water_need["seasonal_day_of_year"],
                    "transpiration_ml": round(water_need["transpiration_ml"]),
                    "substrate_evaporation_ml": round(water_need["substrate_evaporation_ml"]),
                    "rain_credit_ml": round(water_need["rain_credit_ml"]),
                    "crop_coefficient": plant["crop_coefficient"],
                    "recommended_pot_liters": plant["recommended_pot_liters"],
                    "wind_factor": wind_factor,
                },
                "sun": plant_sun,
            }
        )

    return {
        "plants": plant_results,
        "total_need_ml": total_need_ml,
        "terrace_sun": terrace_sun,
        "reference_et0_mm": reference_et0_mm,
        "wind_factor": wind_factor,
        "orientation_deg": orientation_deg,
    }


def optimize_static_connection_plan(
    balcony: dict,
    walls: list[dict],
    plants: list[dict],
    outlets: list[dict],
) -> dict:
    if not plants:
        return {"cycles": 0, "score": 0, "assignments": [], "by_outlet": [], "outlet_limits": {}, "summary": "Noch keine Pflanzen angelegt."}

    design = calculate_plant_results(
        balcony,
        walls,
        plants,
        temperature_c=CONNECTION_DESIGN["temperature_c"],
        rain_mm=CONNECTION_DESIGN["rain_mm"],
        wind_kmh=CONNECTION_DESIGN["wind_kmh"],
        sunshine_hours=CONNECTION_DESIGN["sunshine_hours"],
        slot="morning",
    )
    design_plants = design["plants"]
    options = outlet_delivery_options(outlets)
    outlet_limits = {outlet["id"]: 12 for outlet in options}
    option_sets = [
        plant_tube_options(
            plant,
            options,
            CONNECTION_DESIGN["cycles"],
            max_total_tubes=current_tube_count(plant),
            tube_penalty=80,
        )
        for plant in design_plants
    ]
    candidate = choose_tube_assignments(design_plants, option_sets, outlet_limits, CONNECTION_DESIGN["cycles"])
    if candidate is None:
        candidate = fallback_single_tube_plan(design_plants, options, CONNECTION_DESIGN["cycles"])
    else:
        candidate = {
            "cycles": CONNECTION_DESIGN["cycles"],
            "score": round(candidate["score"], 2),
            "assignments": candidate["assignments"],
            "hard_underwatered": candidate["hard_underwatered"],
            "severely_overwatered": candidate["severely_overwatered"],
        }

    finalize_routing_plan(candidate, outlets, CONNECTION_DESIGN["cycles"])
    add_connection_comparison(candidate, design_plants)
    candidate["summary"] = static_connection_summary(candidate)
    candidate["design_basis"] = {
        "temperature_c": CONNECTION_DESIGN["temperature_c"],
        "rain_mm": CONNECTION_DESIGN["rain_mm"],
        "wind_kmh": CONNECTION_DESIGN["wind_kmh"],
        "sunshine_hours": CONNECTION_DESIGN["sunshine_hours"],
        "cycles": CONNECTION_DESIGN["cycles"],
    }
    return candidate


def add_connection_comparison(plan: dict, plants: list[dict]) -> None:
    plant_by_id = {plant["id"]: plant for plant in plants}
    for assignment in plan["assignments"]:
        plant = plant_by_id.get(assignment["plant_id"], {})
        current_target = plant.get("current_target_ml_per_cycle")
        current_ml = float(current_target) if current_target not in (None, "") else float(plant.get("current_ml_per_run", 0) or 0)
        recommended_ml = float(assignment["ml_per_cycle"])
        tolerance = max(7, recommended_ml * 0.2)
        current_delivered = current_ml * int(plan.get("cycles", CONNECTION_DESIGN["cycles"]))
        need_ml = float(assignment["need_ml"])
        current_under = max(0, need_ml - current_delivered)
        current_over = max(0, current_delivered - need_ml)
        over_tolerance = overwater_tolerance_ml(plant)
        assignment["current_ml_per_cycle"] = round(current_ml)
        assignment["current_delivered_ml"] = round(current_delivered)
        assignment["current_hose_numbers"] = plant.get("current_hose_numbers", "")
        assignment["connection_status"] = "ok"
        assignment["connection_severity"] = "ok"
        assignment["connection_action_title"] = "Passt"
        assignment["connection_note"] = "Aktueller Anschluss passt zum festen Plan."
        connection_differs = abs(current_ml - recommended_ml) > tolerance
        if connection_differs:
            direction = "mehr" if current_ml < recommended_ml else "weniger"
            assignment["connection_status"] = "change"
            assignment["connection_severity"] = "change"
            assignment["connection_action_title"] = "Anschluss ändern"
            assignment["connection_note"] = (
                f"Besser {assignment['tube_label']} ({round(recommended_ml)} ml) statt aktuell "
                f"{round(current_ml)} ml: diese Pflanze braucht dauerhaft {direction} Wasser pro gemeinsamem Zyklus."
            )
        if connection_differs and current_under > max(50, need_ml * 0.25):
            assignment["connection_status"] = "urgent"
            assignment["connection_severity"] = "urgent"
            assignment["connection_action_title"] = "Unerlässlich verlegen"
            assignment["connection_note"] = (
                f"Unerlässlich: aktueller Anschluss liefert im Auslegungsfall ca. {round(current_under)} ml zu wenig. "
                f"Auf {assignment['tube_label']} umstecken."
            )
        elif connection_differs and current_over > over_tolerance:
            assignment["connection_status"] = "urgent"
            assignment["connection_severity"] = "urgent"
            assignment["connection_action_title"] = "Unerlässlich reduzieren"
            assignment["connection_note"] = (
                f"Unerlässlich: aktueller Anschluss liefert im Auslegungsfall ca. {round(current_over)} ml zu viel. "
                f"Auf {assignment['tube_label']} reduzieren."
            )


def static_connection_summary(plan: dict) -> str:
    changes = sum(1 for assignment in plan["assignments"] if assignment.get("connection_status") == "change")
    urgent = sum(1 for assignment in plan["assignments"] if assignment.get("connection_status") == "urgent")
    if plan.get("unassigned_plants"):
        return "Warnung: Für mindestens eine Pflanze ist kein freier Anschluss mehr verfügbar."
    if urgent:
        return f"Dringend: {urgent} Pflanze(n) müssen umgesteckt werden, sonst droht deutliche Fehlversorgung."
    if changes:
        return f"Fester Anschlussplan: {changes} Pflanze(n) sollten anders verschlaucht werden."
    return "Fester Anschlussplan passt. Wetter verändert nur die Anzahl gemeinsamer Pumpzyklen."


def fixed_cycle_score(plant: dict, delivered_ml: float) -> tuple[float, bool, bool]:
    need = float(plant["need_ml"])
    under = max(0, need - delivered_ml)
    over = max(0, delivered_ml - need)
    tolerance = overwater_tolerance_ml(plant)
    under_limit = max(20, need * 0.14)
    score = under * 8 + over * 1.8
    score += max(0, under - under_limit) * 24
    score += max(0, over - tolerance) * 10
    return score, under > under_limit, over > tolerance


def configured_connection_plan(plants: list[dict]) -> dict:
    assignments = []
    unassigned_plants = []
    for plant in plants:
        grouped = {}
        for hose in plant.get("hoses", []):
            tube = grouped.setdefault(
                int(hose["outlet_id"]),
                {
                    "outlet_id": int(hose["outlet_id"]),
                    "outlet_name": hose["outlet_name"],
                    "ml_per_run": int(hose["ml_per_run"]),
                    "count": 0,
                },
            )
            tube["count"] += 1
        tubes = list(grouped.values())
        if not tubes:
            unassigned_plants.append(plant["custom_name"])
            continue
        ml_per_cycle = sum(tube["ml_per_run"] * tube["count"] for tube in tubes)
        assignments.append(
            normalize_assignment(
                {
                    "plant_id": plant["id"],
                    "plant_name": plant["custom_name"],
                    "catalog_name": plant["catalog_name"],
                    "tubes": tubes,
                    "connections": {tube["outlet_id"]: tube["count"] for tube in tubes},
                    "ml_per_cycle": ml_per_cycle,
                    "need_ml": 0,
                    "delivered_ml": 0,
                    "difference_ml": 0,
                    "under_ml": 0,
                    "over_ml": 0,
                    "score": 0,
                }
            )
        )
    return {
        "cycles": 0,
        "score": 0,
        "assignments": assignments,
        "unassigned_plants": unassigned_plants,
    }


def apply_fixed_connection_to_weather(
    connection_plan: dict,
    plant_results: list[dict],
    outlets: list[dict],
    max_cycles: int = 96,
) -> dict:
    if not plant_results or not connection_plan.get("assignments"):
        plan = deepcopy(connection_plan)
        finalize_routing_plan(plan, outlets, 0)
        plan["summary"] = "Noch keine Verschlauchung berechenbar."
        return plan

    plant_by_id = {plant["id"]: plant for plant in plant_results}
    best = None
    for cycles in range(0, max_cycles + 1):
        score = cycles * 0.45
        hard_under = 0
        severe_over = 0
        for assignment in connection_plan["assignments"]:
            plant = plant_by_id[assignment["plant_id"]]
            delivered = cycles * assignment["ml_per_cycle"]
            plant_score, is_under, is_over = fixed_cycle_score(plant, delivered)
            score += plant_score
            hard_under += 1 if is_under else 0
            severe_over += 1 if is_over else 0
        candidate = {
            "cycles": cycles,
            "score": round(score, 2),
            "hard_underwatered": hard_under,
            "severely_overwatered": severe_over,
        }
        if best is None or candidate["score"] < best["score"]:
            best = candidate

    plan = deepcopy(connection_plan)
    plan.update(best or {"cycles": 0, "score": 0, "hard_underwatered": 0, "severely_overwatered": 0})
    for assignment in plan["assignments"]:
        plant = plant_by_id[assignment["plant_id"]]
        delivered = plan["cycles"] * assignment["ml_per_cycle"]
        assignment["need_ml"] = round(float(plant["need_ml"]))
        assignment["delivered_ml"] = round(delivered)
        assignment["difference_ml"] = round(delivered - float(plant["need_ml"]))
        assignment["under_ml"] = round(max(0, float(plant["need_ml"]) - delivered))
        assignment["over_ml"] = round(max(0, delivered - float(plant["need_ml"])))
    finalize_routing_plan(plan, outlets, plan["cycles"])
    plan["summary"] = weather_fixed_plan_summary(plan)
    return plan


def weather_fixed_plan_summary(plan: dict) -> str:
    if plan.get("hard_underwatered"):
        return "Mit dem festen Anschlussplan bleibt mindestens eine Pflanze bei dieser Zykluszahl zu trocken."
    if plan.get("severely_overwatered"):
        return "Mit dem festen Anschlussplan bekommt mindestens eine Pflanze bei dieser Zykluszahl deutlich zu viel Wasser."
    return "Beste Zykluszahl für den festen Anschlussplan."


def local_now(timezone_name: str) -> datetime:
    try:
        tzinfo = ZoneInfo(timezone_name or "Europe/Berlin")
    except ZoneInfoNotFoundError:
        tzinfo = ZoneInfo("Europe/Berlin")
    return datetime.now(tzinfo)


def parse_hhmm(value: str) -> time:
    hour, minute = value.split(":", 1)
    return time(int(hour), int(minute))


def window_datetime(today: date, value: str, tzinfo) -> datetime:
    return datetime.combine(today, parse_hhmm(value), tzinfo=tzinfo)


def distributed_automation_windows(today: date, total_cycles: int, tzinfo) -> list[datetime]:
    start = window_datetime(today, AUTOMATION_DAY_START, tzinfo)
    end = window_datetime(today, AUTOMATION_DAY_END, tzinfo)
    if total_cycles <= 0:
        return []
    if total_cycles == 1:
        return [start]
    spacing = (end - start) / (total_cycles - 1)
    return [start + spacing * index for index in range(total_cycles)]


def parse_pause_until(value: str, timezone_name: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    timezone = local_now(timezone_name).tzinfo
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone)
    return parsed.astimezone(timezone)


def automation_status(
    should_run: bool,
    remaining_cycles: int,
    timezone_name: str,
    pause_until_value: str = "",
    latest_run_value: str = "",
    recommended_cycles: int | None = None,
    completed_cycles: int | None = None,
) -> dict:
    now = local_now(timezone_name)
    tolerance = timedelta(minutes=AUTOMATION_TRIGGER_TOLERANCE_MINUTES)
    total_cycles = max(0, int(recommended_cycles if recommended_cycles is not None else remaining_cycles))
    completed = max(0, int(completed_cycles if completed_cycles is not None else total_cycles - remaining_cycles))
    windows = distributed_automation_windows(now.date(), total_cycles, now.tzinfo)
    day_start = window_datetime(now.date(), AUTOMATION_DAY_START, now.tzinfo)
    day_end = window_datetime(now.date(), AUTOMATION_DAY_END, now.tzinfo)
    pause_until = parse_pause_until(pause_until_value, timezone_name)
    paused = bool(pause_until and pause_until > now)
    latest_run = parse_pause_until(latest_run_value, timezone_name)
    cooldown_until = latest_run + timedelta(minutes=AUTOMATION_RUN_COOLDOWN_MINUTES) if latest_run else None
    cooldown_active = bool(cooldown_until and cooldown_until > now)
    due_windows = [item for item in windows if item <= now]
    future_windows = [item for item in windows if item > now]
    next_window = future_windows[0] if future_windows else None
    behind_schedule = completed < len(due_windows)
    catch_up = bool(behind_schedule and due_windows and now > due_windows[min(completed, len(due_windows) - 1)] + tolerance)
    distribution_active = day_start <= now <= day_end + tolerance
    run_now = bool(
        should_run
        and remaining_cycles > 0
        and distribution_active
        and behind_schedule
        and not paused
        and not cooldown_active
    )
    if paused:
        summary = f"Automatik pausiert bis {pause_until.strftime('%d.%m. %H:%M')}."
    elif cooldown_active:
        summary = f"Pause nach letztem Lauf bis {cooldown_until.strftime('%H:%M')}."
    elif not should_run or remaining_cycles <= 0:
        summary = "Kein automatischer Lauf nötig."
    elif run_now:
        summary = "Geplanter Zeitpunkt erreicht: Home Assistant darf jetzt einen Zyklus starten."
    elif next_window:
        summary = f"Nächster geplanter Lauf um {next_window.strftime('%H:%M')}."
    else:
        summary = "Heute kein geplanter Lauf mehr."
    return {
        "run_now": run_now,
        "paused": paused,
        "pause_until": pause_until.isoformat() if pause_until else "",
        "cooldown_active": cooldown_active,
        "cooldown_until": cooldown_until.isoformat() if cooldown_until else "",
        "cooldown_minutes": AUTOMATION_RUN_COOLDOWN_MINUTES,
        "windows": [item.strftime("%H:%M") for item in windows],
        "distribution_start": AUTOMATION_DAY_START,
        "distribution_end": AUTOMATION_DAY_END,
        "active_window": due_windows[-1].strftime("%H:%M") if behind_schedule and distribution_active else "",
        "next_window": next_window.strftime("%H:%M") if next_window else "",
        "regular_slots_remaining": len(future_windows),
        "due_cycles": len(due_windows),
        "catch_up": catch_up,
        "shortfall_prevention": catch_up,
        "summary": summary,
    }


def refill_window_datetimes(today: date, tzinfo, schedule_times: list[str] | None = None) -> list[datetime]:
    return [window_datetime(today, item, tzinfo) for item in (schedule_times or refill_schedule_times())]


def refill_cooldown_minutes(transferred_ml: int | float) -> int:
    if transferred_ml <= 0:
        return 0
    minutes = math.ceil(float(transferred_ml) / 1000 * refill_cooldown_minutes_per_liter())
    return int(clamp(minutes, REFILL_MIN_COOLDOWN_MINUTES, REFILL_MAX_COOLDOWN_MINUTES))


def latest_refill_event() -> dict | None:
    with connect() as conn:
        row = conn.execute(
            """
            SELECT id, ran_at, target_date, requested_ml, transferred_ml, duration_seconds, window_label, source
            FROM refill_events
            ORDER BY ran_at DESC, id DESC
            LIMIT 1
            """
        ).fetchone()
        return row_to_dict(row) if row else None


def parse_event_datetime(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def refill_status(balcony: dict) -> dict:
    timezone_name = str(balcony.get("timezone_name", "Europe/Berlin"))
    now = local_now(timezone_name)
    target_date = now.date()
    enabled = refill_automation_enabled()
    schedule_times = refill_schedule_times()
    main_capacity = max(int(balcony.get("tank_capacity_ml", 0)), 0)
    main_current = max(int(balcony.get("tank_current_ml", 0)), 0)
    refill_capacity = max(int(balcony.get("refill_tank_capacity_ml", 30000)), 1)
    refill_current = max(int(balcony.get("refill_tank_current_ml", 0)), 0)
    pump_ml_per_min = max(int(balcony.get("refill_pump_ml_per_min", 0)), 0)
    main_missing_ml = max(0, main_capacity - main_current)
    requested_ml = main_missing_ml
    target_transfer_ml = math.ceil(requested_ml * REFILL_TRANSFER_FRACTION)
    transferable_ml = min(target_transfer_ml, main_missing_ml, refill_current)
    duration_seconds = math.ceil(transferable_ml / pump_ml_per_min * 60) if pump_ml_per_min and transferable_ml else 0
    refill_windows = refill_window_datetimes(now.date(), now.tzinfo, schedule_times)
    elapsed_windows = [item for item in refill_windows if item <= now]
    eligible_windows = [
        item for item in elapsed_windows
        if now <= item + timedelta(minutes=REFILL_TRIGGER_TOLERANCE_MINUTES)
    ]
    pending_windows = [
        item for item in eligible_windows
        if not refill_event_for_target_date(target_date, item.strftime("%H:%M"))
    ]
    completed_windows = [
        item for item in elapsed_windows
        if refill_event_for_target_date(target_date, item.strftime("%H:%M"))
    ]
    missed_windows = [
        item for item in elapsed_windows
        if item not in eligible_windows
        and not refill_event_for_target_date(target_date, item.strftime("%H:%M"))
    ]
    active_window = pending_windows[0] if pending_windows else None
    active_window_label = active_window.strftime("%H:%M") if active_window else ""
    next_window = next((item for item in refill_windows if item > now), None)
    if not next_window:
        next_window = refill_window_datetimes(now.date() + timedelta(days=1), now.tzinfo, schedule_times)[0]
    last_event = latest_refill_event()
    schedule_due = bool(active_window)
    need_exists = bool(transferable_ml > 0 and pump_ml_per_min > 0)
    run_now = bool(enabled and schedule_due and need_exists)
    catch_up = False

    if not enabled:
        summary = "Automatisches Nachfüllen ist deaktiviert."
    elif pump_ml_per_min <= 0:
        summary = "Durchsatz der Nachfüllpumpe fehlt."
    elif main_missing_ml <= 0:
        summary = "Haupttank ist voll."
    elif refill_current <= 0:
        summary = "Vorratstank ist leer."
    elif transferable_ml < target_transfer_ml:
        summary = f"Nachfüllung auf {format_liters_for_text(transferable_ml)} begrenzt."
    elif run_now:
        summary = "Nachfüllbedarf besteht, Nachfüllpumpe darf laufen."
    elif missed_windows:
        summary = f"Nachtfenster für heute verpasst. Nächste Nachfüllung um {next_window.strftime('%H:%M')}."
    elif now < next_window:
        summary = f"Nächste Nachfüllung um {next_window.strftime('%H:%M')}."
    else:
        summary = "Nachfüllung wartet auf den nächsten geplanten Zeitpunkt."

    refill_percent = round(refill_current / refill_capacity * 100)
    return {
        "run_now": run_now,
        "enabled": enabled,
        "target_date": target_date.isoformat(),
        "scheduled_time": (active_window or next_window).strftime("%H:%M"),
        "scheduled_times": schedule_times,
        "active_window": active_window_label,
        "window_label": active_window_label if run_now else "",
        "last_window": elapsed_windows[-1].strftime("%H:%M") if elapsed_windows else "",
        "next_window": next_window.strftime("%H:%M") if next_window else "",
        "requested_ml": requested_ml,
        "target_transfer_ml": target_transfer_ml,
        "transfer_fraction": REFILL_TRANSFER_FRACTION,
        "planned_transfer_ml": transferable_ml,
        "duration_seconds": duration_seconds,
        "pump_ml_per_min": pump_ml_per_min,
        "main_missing_ml": main_missing_ml,
        "blocked_by_empty_refill_tank": bool(refill_current <= 0),
        "limited_by_refill_tank": bool(target_transfer_ml > 0 and transferable_ml < min(target_transfer_ml, main_missing_ml)),
        "already_done": bool(completed_windows),
        "missed_today": bool(missed_windows),
        "cooldown_active": False,
        "cooldown_minutes": 0,
        "cooldown_until": "",
        "need_exists": need_exists,
        "schedule_due": schedule_due,
        "catch_up": catch_up,
        "last_event": last_event or {},
        "refill_tank": {
            "current_ml": refill_current,
            "capacity_ml": refill_capacity,
            "percent": refill_percent,
            "low": refill_percent <= TANK_LOW_PERCENT,
            "empty": refill_current <= 0,
        },
        "summary": summary,
    }


def format_liters_for_text(ml: int | float) -> str:
    return f"{round(float(ml) / 1000, 1):g} l"


def depletion_forecast(result: dict, weather: dict | None = None) -> dict:
    state = get_state()
    balcony = state["balcony"]
    timezone_name = str(balcony.get("timezone_name", "Europe/Berlin"))
    now = local_now(timezone_name)
    delivered_per_cycle = int(result["pump"]["delivered_per_cycle_ml"])
    main_ml = max(0, int(result["tank"]["current_ml"]))
    refill = result.get("refill", {})
    refill_tank = refill.get("refill_tank", {})
    refill_ml = max(0, int(refill_tank.get("current_ml", balcony.get("refill_tank_current_ml", 0))))
    total_ml = main_ml + refill_ml
    forecast_days = normalized_forecast_days(weather or result.get("weather") or result.get("inputs", {}), now.date())
    projected_days = projected_consumption_days(state, forecast_days, delivered_per_cycle)
    cycle_events = projected_cycle_events(projected_days, result, now, timezone_name)
    main_remaining = main_ml
    total_remaining = total_ml
    main_empty_at = ""
    all_empty_at = ""
    next_cycle_at = cycle_events[0]["at"].isoformat() if cycle_events else ""

    if delivered_per_cycle <= 0:
        summary = "Noch kein Wasserverbrauch pro Zyklus berechenbar."
    else:
        summary = "Reichweite anhand der Wetterprognose berechnet."
        for event in cycle_events:
            amount = int(event["delivered_ml"])
            if not main_empty_at and main_remaining <= amount:
                main_empty_at = event["at"].isoformat()
            if not all_empty_at and total_remaining <= amount:
                all_empty_at = event["at"].isoformat()
                break
            main_remaining = max(0, main_remaining - amount)
            total_remaining = max(0, total_remaining - amount)
        if delivered_per_cycle > 0 and not all_empty_at:
            all_empty_at = estimate_depletion_after_forecast(projected_days, cycle_events, total_remaining, delivered_per_cycle, now, timezone_name)
        if not main_empty_at and main_remaining <= 0:
            main_empty_at = cycle_events[-1]["at"].isoformat() if cycle_events else ""

    return {
        "main_empty_at": main_empty_at,
        "all_empty_at": all_empty_at,
        "next_cycle_at": next_cycle_at,
        "total_available_ml": total_ml,
        "main_available_ml": main_ml,
        "refill_available_ml": refill_ml,
        "projected_days": projected_days,
        "forecast_days": len(forecast_days),
        "summary": summary,
    }


def normalized_forecast_days(weather: dict, today: date) -> list[dict]:
    forecast = weather.get("forecast") if isinstance(weather, dict) else None
    if isinstance(forecast, list) and forecast:
        return [item for item in forecast if isinstance(item, dict)]
    return [
        {
            "date": (today + timedelta(days=index)).isoformat(),
            "temperature_c": forecast_number(weather, "temperature_c", 20),
            "rain_mm": forecast_number(weather, "rain_mm", 0),
            "wind_kmh": forecast_number(weather, "wind_kmh", 0),
            "sunshine_hours": forecast_number(weather, "sunshine_hours", 6),
            "et0_mm": forecast_number(weather, "et0_mm", 0),
        }
        for index in range(WEATHER_FORECAST_DAYS)
    ]


def forecast_number(weather: dict, key: str, default: float) -> float:
    if not isinstance(weather, dict):
        return default
    value = weather.get(key, default)
    if value is None:
        return default
    return float(value)


def projected_consumption_days(state: dict, forecast_days: list[dict], delivered_per_cycle: int) -> list[dict]:
    balcony = state["balcony"]
    walls = state["walls"]
    plants = state["plants"]
    outlets = state["outlets"]
    projections = []
    for weather in forecast_days:
        try:
            target_date = date.fromisoformat(str(weather.get("date")))
        except ValueError:
            target_date = local_now(str(balcony.get("timezone_name", "Europe/Berlin"))).date()
        calculated = calculate_plant_results(
            balcony,
            walls,
            plants,
            temperature_c=float(weather.get("temperature_c", 20)),
            rain_mm=float(weather.get("rain_mm", 0)),
            wind_kmh=float(weather.get("wind_kmh", 0)),
            sunshine_hours=float(weather.get("sunshine_hours", 0)),
            et0_mm=float(weather.get("et0_mm", 0) or 0),
            target_date=target_date,
        )
        routing_plan = apply_fixed_connection_to_weather(configured_connection_plan(plants), calculated["plants"], outlets)
        cycles = int(routing_plan["cycles"])
        projections.append(
            {
                "date": target_date.isoformat(),
                "cycles": cycles,
                "delivered_ml": cycles * delivered_per_cycle,
                "delivered_per_cycle_ml": delivered_per_cycle,
                "need_ml": round(calculated["total_need_ml"]),
                "weather": {
                    "temperature_c": float(weather.get("temperature_c", 20)),
                    "rain_mm": float(weather.get("rain_mm", 0)),
                },
            }
        )
    return projections


def projected_cycle_events(projected_days: list[dict], result: dict, now: datetime, timezone_name: str) -> list[dict]:
    events = []
    today = now.date()
    completed_today = int(result.get("cycles_completed_today", 0))
    for day in projected_days:
        day_date = date.fromisoformat(day["date"])
        cycles = int(day["cycles"])
        windows = distributed_automation_windows(day_date, cycles, now.tzinfo)
        for index, window in enumerate(windows):
            if day_date == today and (window <= now or index < completed_today):
                continue
            events.append({"at": window, "delivered_ml": int(day["delivered_per_cycle_ml"]), "date": day["date"]})
    return sorted(events, key=lambda item: item["at"])


def estimate_depletion_after_forecast(
    projected_days: list[dict],
    cycle_events: list[dict],
    remaining_ml: int,
    delivered_per_cycle: int,
    now: datetime,
    timezone_name: str,
) -> str:
    daily_totals = [int(day["delivered_ml"]) for day in projected_days if int(day.get("delivered_ml", 0)) > 0]
    if not daily_totals:
        return ""
    average_daily_ml = max(1, round(sum(daily_totals) / len(daily_totals)))
    days_after_forecast = math.ceil(max(0, remaining_ml) / average_daily_ml)
    last_date = date.fromisoformat(projected_days[-1]["date"]) if projected_days else now.date()
    estimated_date = last_date + timedelta(days=max(1, days_after_forecast))
    average_cycles = max(1, round(average_daily_ml / max(delivered_per_cycle, 1)))
    windows = distributed_automation_windows(estimated_date, average_cycles, now.tzinfo)
    return (windows[-1] if windows else datetime.combine(estimated_date, parse_hhmm(AUTOMATION_DAY_END), tzinfo=now.tzinfo)).isoformat()


def home_assistant_webhook_url() -> str:
    value = os.environ.get("HOME_ASSISTANT_WEBHOOK_URL", "").strip()
    if not value or "HOME-ASSISTANT-IP" in value:
        return ""
    parsed = urlparse(value)
    return value if parsed.scheme in {"http", "https"} and parsed.netloc else ""


def home_assistant_refill_webhook_url() -> str:
    value = os.environ.get("HOME_ASSISTANT_REFILL_WEBHOOK_URL", "").strip()
    if not value or "HOME-ASSISTANT-IP" in value:
        return ""
    parsed = urlparse(value)
    return value if parsed.scheme in {"http", "https"} and parsed.netloc else ""


def manual_run_status(result: dict) -> dict:
    delivered_per_cycle = int(result["pump"]["delivered_per_cycle_ml"])
    if not result["plants"]:
        reason = "Noch keine Pflanzen angelegt."
    elif delivered_per_cycle <= 0:
        reason = "Noch keine nutzbare Verschlauchung vorhanden."
    elif int(result["tank"]["current_ml"]) < delivered_per_cycle:
        reason = "Der Wassertank reicht nicht für einen vollständigen Pumpenlauf."
    elif not home_assistant_webhook_url():
        reason = "Home-Assistant-Webhook für manuelle Läufe ist noch nicht konfiguriert."
    else:
        return {
            "available": True,
            "reason": "Ein manueller Zyklus kann sofort über Home Assistant gestartet werden.",
            "endpoint": "/api/manual-run",
        }
    return {
        "available": False,
        "reason": reason,
        "endpoint": "/api/manual-run",
    }


def manual_refill_status(result: dict) -> dict:
    manual_plan = manual_refill_plan(result)
    if int(manual_plan.get("planned_transfer_ml", 0)) <= 0:
        reason = manual_plan.get("summary") or "Keine Nachfüllung nötig."
    elif not home_assistant_refill_webhook_url():
        reason = "Home-Assistant-Webhook für manuelle Nachfüllläufe ist noch nicht konfiguriert."
    else:
        return {
            "available": True,
            "reason": "Ein Nachfülllauf kann über Home Assistant gestartet werden.",
            "endpoint": "/api/manual-refill",
            **manual_plan,
        }
    return {
        "available": False,
        "reason": reason,
        "endpoint": "/api/manual-refill",
        **manual_plan,
    }


def manual_refill_plan(result: dict) -> dict:
    tank = result.get("tank", {})
    refill = result.get("refill", {})
    refill_tank = refill.get("refill_tank", {})
    main_missing_ml = max(0, int(tank.get("capacity_ml", 0)) - int(tank.get("current_ml", 0)))
    refill_current = max(0, int(refill_tank.get("current_ml", 0)))
    pump_ml_per_min = max(0, int(refill.get("pump_ml_per_min", 0)))
    target_transfer_ml = math.ceil(main_missing_ml * REFILL_TRANSFER_FRACTION)
    planned_transfer_ml = min(target_transfer_ml, refill_current)
    duration_seconds = math.ceil(planned_transfer_ml / pump_ml_per_min * 60) if pump_ml_per_min and planned_transfer_ml else 0
    if pump_ml_per_min <= 0:
        summary = "Durchsatz der Nachfüllpumpe fehlt."
    elif main_missing_ml <= 0:
        summary = "Haupttank ist voll."
    elif refill_current <= 0:
        summary = "Vorratstank ist leer."
    elif planned_transfer_ml < target_transfer_ml:
        summary = f"Manuelle Nachfüllung auf {format_liters_for_text(planned_transfer_ml)} begrenzt."
    else:
        summary = f"Manuelle Nachfüllung: {format_liters_for_text(planned_transfer_ml)}."
    return {
        "target_transfer_ml": target_transfer_ml,
        "planned_transfer_ml": planned_transfer_ml,
        "duration_seconds": duration_seconds,
        "main_missing_ml": main_missing_ml,
        "summary": summary,
    }


def save_pending_refill_request(plan: dict) -> None:
    set_setting(
        "pending_refill_request",
        json.dumps(
            {
                "created_at": now_iso(),
                "source": "manual",
                "target_date": local_now(get_state()["balcony"].get("timezone_name", "Europe/Berlin")).date().isoformat(),
                "requested_ml": int(plan.get("main_missing_ml", 0)),
                "transferred_ml": int(plan.get("planned_transfer_ml", 0)),
                "duration_seconds": int(plan.get("duration_seconds", 0)),
                "window_label": "manual",
            }
        ),
    )


def pending_refill_request() -> dict | None:
    raw = get_setting("pending_refill_request", "")
    if not raw:
        return None
    try:
        payload = json.loads(raw)
        created_at = datetime.fromisoformat(str(payload.get("created_at", "")))
    except (ValueError, TypeError, json.JSONDecodeError):
        delete_setting("pending_refill_request")
        return None
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    if datetime.now(timezone.utc) - created_at.astimezone(timezone.utc) > timedelta(hours=2):
        delete_setting("pending_refill_request")
        return None
    return payload


def trigger_home_assistant_manual_run(result: dict) -> None:
    status = manual_run_status(result)
    if not status["available"]:
        raise ValueError(status["reason"])
    payload = json.dumps(
        {
            "source": "watering-planner",
            "requested_at": now_iso(),
            "delivered_per_cycle_ml": result["pump"]["delivered_per_cycle_ml"],
        }
    ).encode("utf-8")
    request = Request(
        home_assistant_webhook_url(),
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=5) as response:
            response.read()
    except (OSError, URLError, TimeoutError) as exc:
        raise ValueError(f"Home Assistant konnte nicht erreicht werden: {exc}") from exc


def trigger_home_assistant_manual_refill(result: dict) -> None:
    status = manual_refill_status(result)
    if not status["available"]:
        raise ValueError(status["reason"])
    save_pending_refill_request(status)
    payload = json.dumps(
        {
            "source": "watering-planner",
            "requested_at": now_iso(),
            "planned_transfer_ml": status.get("planned_transfer_ml", 0),
            "duration_seconds": status.get("duration_seconds", 0),
        }
    ).encode("utf-8")
    request = Request(
        home_assistant_refill_webhook_url(),
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=5) as response:
            response.read()
    except (OSError, URLError, TimeoutError) as exc:
        delete_setting("pending_refill_request")
        raise ValueError(f"Home Assistant konnte nicht erreicht werden: {exc}") from exc


def evaluate(
    temperature_c: float,
    rain_mm: float,
    wind_kmh: float = 0,
    slot: str = "morning",
    sunshine_hours: float | None = None,
    weather_source: str = "manual",
    et0_mm: float = 0,
) -> dict:
    state = get_state()
    balcony = state["balcony"]
    walls = state["walls"]
    plants = state["plants"]
    outlets = state["outlets"]
    calculated = calculate_plant_results(
        balcony,
        walls,
        plants,
        temperature_c=temperature_c,
        rain_mm=rain_mm,
        wind_kmh=wind_kmh,
        sunshine_hours=sunshine_hours,
        slot=slot,
        et0_mm=et0_mm,
    )
    plant_results = calculated["plants"]
    total_need_ml = calculated["total_need_ml"]
    terrace_sun = calculated["terrace_sun"]
    reference_et0_mm = calculated["reference_et0_mm"]
    wind_factor = calculated["wind_factor"]
    orientation_deg = calculated["orientation_deg"]

    connection_plan = optimize_static_connection_plan(balcony, walls, plants, outlets)
    routing_plan = apply_fixed_connection_to_weather(configured_connection_plan(plants), plant_results, outlets)
    recommended_cycles = routing_plan["cycles"]
    actual_assignment_by_plant = {assignment["plant_id"]: assignment for assignment in routing_plan["assignments"]}
    suggested_assignment_by_plant = {assignment["plant_id"]: assignment for assignment in connection_plan["assignments"]}
    for plant in plant_results:
        suggested = suggested_assignment_by_plant.get(plant["id"])
        actual = actual_assignment_by_plant.get(plant["id"])
        if suggested:
            plant["suggested_outlet"] = suggested["outlet_name"]
            plant["suggested_ml_per_run"] = suggested["ml_per_run"]
            plant["suggested_tubes"] = suggested["tubes"]
            plant["suggested_tube_label"] = suggested["tube_label"]
            plant["connection_status"] = suggested.get("connection_status")
            plant["connection_severity"] = suggested.get("connection_severity")
            plant["connection_action_title"] = suggested.get("connection_action_title")
            plant["connection_note"] = suggested.get("connection_note")
        if actual:
            plant["delivered_ml"] = actual["delivered_ml"]
            plant["difference_ml"] = actual["difference_ml"]

    total_delivered_per_run = sum(
        tube["ml_per_run"] * tube["count"]
        for assignment in routing_plan["assignments"]
        for tube in assignment["tubes"]
    )

    cycles_completed = completed_cycles_today(str(balcony.get("timezone_name", "Europe/Berlin")))
    remaining_cycles = max(0, recommended_cycles - cycles_completed)
    delivered_if_remaining = remaining_cycles * total_delivered_per_run
    consumed_per_run = calibrated_consumption_ml(total_delivered_per_run)
    consumed_if_remaining = remaining_cycles * consumed_per_run
    tank_after = max(0, balcony["tank_current_ml"] - consumed_if_remaining)
    tank_capacity = max(int(balcony["tank_capacity_ml"]), 1)
    tank_percent = round(int(balcony["tank_current_ml"]) / tank_capacity * 100)
    tank_after_percent = round(tank_after / tank_capacity * 100)
    tank_empty_soon = bool(plants and consumed_per_run > 0 and int(balcony["tank_current_ml"]) < consumed_per_run)
    tank_low = tank_percent <= TANK_LOW_PERCENT or tank_empty_soon

    hottest_sensitive_need = max((p["need_ml"] for p in plant_results), default=0)
    rain_threshold = round(clamp(2.2 + (temperature_c - 22) * 0.18 + hottest_sensitive_need / 1200, 0.8, 8.0), 1)
    temp_threshold = 16 if rain_mm < 1 else 22
    # Plant demand already includes the weather credit. A remaining cycle must
    # not be vetoed again by coarse rain or temperature thresholds.
    should_run = bool(plants) and remaining_cycles > 0

    if plants and total_delivered_per_run <= 0:
        should_run = False
        reason = "Noch keine nutzbare Verschlauchung vorhanden"
    elif plants and balcony["tank_current_ml"] < consumed_per_run:
        should_run = False
        reason = "Wassertank reicht nicht für einen vollständigen Pumpenlauf"
    elif not plants:
        reason = "Noch keine Pflanzen angelegt"
    elif recommended_cycles == 0:
        reason = "Heute ist mit dem festen Anschlussplan kein Pumpenlauf nötig"
    elif remaining_cycles == 0 and recommended_cycles > 0:
        reason = "Alle empfohlenen Zyklen für heute sind bereits verbucht"
    elif should_run:
        reason = f"Wasserbedarf {round(total_need_ml)} ml nach Wetteranrechnung"
    else:
        reason = "Heute ist kein automatischer Lauf nötig"

    automation = automation_status(
        should_run,
        remaining_cycles,
        str(balcony.get("timezone_name", "Europe/Berlin")),
        get_setting("automation_pause_until", ""),
        latest_watering_run_at(),
        recommended_cycles,
        cycles_completed,
    )
    refill = refill_status(balcony)

    result = {
        "should_run": should_run,
        "run_now": automation["run_now"],
        "reason": reason,
        "recommended_cycles_today": recommended_cycles,
        "cycles_completed_today": cycles_completed,
        "remaining_cycles_today": remaining_cycles,
        "thresholds": {
            "temperature_c": temp_threshold,
            "rain_mm": rain_threshold,
        },
        "pump": {
            "duration_seconds": 120,
            "delivered_per_cycle_ml": total_delivered_per_run,
            "delivered_if_remaining_ml": delivered_if_remaining,
            "consumption_factor": main_pump_calibration_factor(),
            "consumed_per_cycle_ml": consumed_per_run,
            "consumed_if_remaining_ml": consumed_if_remaining,
        },
        "tank": {
            "current_ml": balcony["tank_current_ml"],
            "after_recommended_ml": tank_after,
            "capacity_ml": balcony["tank_capacity_ml"],
            "percent": tank_percent,
            "after_recommended_percent": tank_after_percent,
            "low_percent_threshold": TANK_LOW_PERCENT,
            "low": tank_low,
            "empty_soon": tank_empty_soon,
            "warning": (
                "Tank reicht nicht mehr für einen vollständigen Pumpenlauf."
                if tank_empty_soon
                else f"Tank unter {TANK_LOW_PERCENT} Prozent."
                if tank_low
                else ""
            ),
        },
        "refill": refill,
        "routing": routing_plan["by_outlet"],
        "routing_plan": routing_plan,
        "connection_plan": connection_plan,
        "plants": plant_results,
        "inputs": {
            "temperature_c": temperature_c,
            "rain_mm": rain_mm,
            "wind_kmh": wind_kmh,
            "slot": slot,
            "sunshine_hours": sunshine_hours,
            "weather_source": weather_source,
            "orientation_deg": orientation_deg,
            "et0_mm": reference_et0_mm,
            "wind_factor": wind_factor,
        },
        "sun": terrace_sun,
        "automation": automation,
        "calculated_at": now_iso(),
    }
    result["manual_run"] = manual_run_status(result)
    result["depletion"] = depletion_forecast(result, result["inputs"])
    result["manual_refill"] = manual_refill_status(result)
    return result


def read_json(handler: SimpleHTTPRequestHandler) -> dict:
    length = int(handler.headers.get("Content-Length", "0"))
    if length == 0:
        return {}
    raw = handler.rfile.read(length).decode("utf-8")
    return json.loads(raw)


def send_json(handler: SimpleHTTPRequestHandler, payload: dict, status: HTTPStatus = HTTPStatus.OK) -> None:
    body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def updater_token() -> str:
    try:
        return UPDATER_TOKEN_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def updater_request(path: str, method: str = "GET", payload: dict | None = None, timeout: int = 30) -> dict:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Accept": "application/json"}
    token = updater_token()
    if data is not None:
        headers["Content-Type"] = "application/json"
    if token:
        headers["X-Watering-Planner-Updater-Token"] = token
    request = Request(f"{UPDATER_URL}{path}", data=data, headers=headers, method=method)
    try:
        with urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        try:
            reason = json.loads(exc.read().decode("utf-8")).get("reason")
        except (ValueError, AttributeError):
            reason = None
        raise ValueError(reason or f"updater_http_{exc.code}") from exc
    except (URLError, TimeoutError) as exc:
        raise ValueError("updater_unavailable") from exc


class AppHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(PUBLIC_DIR), **kwargs)

    def log_message(self, format: str, *args) -> None:
        print(f"[{self.log_date_time_string()}] {format % args}")

    def end_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "same-origin")
        self.send_header("Permissions-Policy", "geolocation=(self)")
        super().end_headers()

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/health":
            send_json(self, {"ok": True, "version": APP_VERSION})
            return
        if parsed.path == "/api/state":
            send_json(self, get_state())
            return
        if parsed.path == "/api/update/status":
            try:
                send_json(self, updater_request("/api/status"))
            except ValueError as exc:
                send_json(self, {"error": str(exc)}, HTTPStatus.SERVICE_UNAVAILABLE)
            return
        if parsed.path == "/api/weather":
            try:
                send_json(self, fetch_weather(get_state()["balcony"]))
            except ValueError as exc:
                send_json(self, {"error": str(exc)}, HTTPStatus.BAD_GATEWAY)
            return
        if parsed.path == "/api/watering-events":
            params = parse_qs(parsed.query)
            send_json(self, {"events": watering_events(int(first(params, "limit", "12")))})
            return
        if parsed.path == "/api/shortcuts":
            params = parse_qs(parsed.query)
            base_url = first(params, "base_url", f"http://{self.headers.get('Host', '127.0.0.1:8080')}")
            send_json(self, shortcut_blueprint(base_url))
            return
        if parsed.path == "/api/homekit/check":
            params = parse_qs(parsed.query)
            try:
                weather = weather_from_query(params)
                send_json(self, evaluate_weather(weather, first(params, "slot", "morning")))
            except ValueError as exc:
                send_json(self, {"error": str(exc)}, HTTPStatus.BAD_GATEWAY)
            return
        return super().do_GET()

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/api/balcony":
                payload = read_json(self)
                save_balcony(payload)
                send_json(self, get_state())
                return
            if parsed.path == "/api/plants":
                payload = read_json(self)
                plant_id = add_plant(payload)
                send_json(self, {"id": plant_id, **get_state()}, HTTPStatus.CREATED)
                return
            if parsed.path == "/api/hoses":
                save_hoses(read_json(self))
                send_json(self, get_state())
                return
            if parsed.path.startswith("/api/plants/") and parsed.path.endswith("/position"):
                plant_id = int(parsed.path.split("/")[3])
                update_plant_position(plant_id, read_json(self))
                send_json(self, get_state())
                return
            if parsed.path == "/api/evaluate":
                payload = read_json(self)
                weather = weather_from_payload(payload)
                send_json(self, evaluate_weather(weather, str(payload.get("slot", "morning"))))
                return
            if parsed.path == "/api/calibration/main":
                payload = read_json(self)
                result = calibrate_main_pump(payload["measured_level_percent"])
                send_json(self, {"calibration": result, **get_state()})
                return
            if parsed.path == "/api/calibration/refill":
                payload = read_json(self)
                result = calibrate_refill_pump(payload["measured_level_percent"])
                send_json(self, {"calibration": result, **get_state()})
                return
            if parsed.path == "/api/update/setup":
                send_json(self, updater_request("/api/setup", "POST", read_json(self)))
                return
            if parsed.path == "/api/update/check":
                send_json(self, updater_request("/api/check", "POST", {"currentVersion": APP_VERSION}))
                return
            if parsed.path == "/api/update/install":
                payload = read_json(self)
                if payload.get("confirm") is not True:
                    send_json(self, {"error": "update_install_confirmation_required"}, HTTPStatus.CONFLICT)
                    return
                send_json(
                    self,
                    updater_request("/api/install", "POST", {"currentVersion": APP_VERSION}),
                    HTTPStatus.ACCEPTED,
                )
                return
            if parsed.path == "/api/homekit/mark-run":
                payload = read_json(self)
                weather = weather_from_payload(payload)
                result = evaluate_weather(weather, str(payload.get("slot", "morning")))
                mark_run(
                    delivered_ml=int(result["pump"]["delivered_per_cycle_ml"]),
                    temperature_c=float(weather["temperature_c"]),
                    rain_mm=float(weather["rain_mm"]),
                )
                send_json(self, evaluate_weather(weather, str(payload.get("slot", "morning"))))
                return
            if parsed.path == "/api/manual-run":
                payload = read_json(self)
                weather = weather_from_payload(payload)
                result = evaluate_weather(weather, str(payload.get("slot", "morning")))
                if not result["manual_run"]["available"]:
                    send_json(self, {"error": result["manual_run"]["reason"]}, HTTPStatus.CONFLICT)
                    return
                try:
                    trigger_home_assistant_manual_run(result)
                except ValueError as exc:
                    send_json(self, {"error": str(exc)}, HTTPStatus.BAD_GATEWAY)
                    return
                send_json(
                    self,
                    {
                        "accepted": True,
                        "message": "Manueller Pumpenlauf wurde an Home Assistant übergeben.",
                        "evaluation": result,
                    },
                    HTTPStatus.ACCEPTED,
                )
                return
            if parsed.path == "/api/manual-refill":
                payload = read_json(self)
                weather = weather_from_payload(payload)
                result = evaluate_weather(weather, str(payload.get("slot", "morning")))
                if not result["manual_refill"]["available"]:
                    send_json(self, {"error": result["manual_refill"]["reason"]}, HTTPStatus.CONFLICT)
                    return
                try:
                    trigger_home_assistant_manual_refill(result)
                except ValueError as exc:
                    send_json(self, {"error": str(exc)}, HTTPStatus.BAD_GATEWAY)
                    return
                send_json(
                    self,
                    {
                        "accepted": True,
                        "message": "Manueller Nachfülllauf wurde an Home Assistant übergeben.",
                        "evaluation": result,
                    },
                    HTTPStatus.ACCEPTED,
                )
                return
            if parsed.path == "/api/refill/mark-run":
                refill = mark_refill_run()
                send_json(
                    self,
                    {
                        "accepted": True,
                        "message": "Nachfülllauf wurde verbucht.",
                        "refill": refill,
                        **get_state(),
                    },
                )
                return
            if parsed.path == "/api/tanks/main/fill":
                fill_tank("main")
                send_json(self, get_state())
                return
            if parsed.path == "/api/tanks/refill/fill":
                fill_tank("refill")
                send_json(self, get_state())
                return
            if parsed.path == "/api/automation/pause":
                payload = read_json(self)
                timezone_name = get_state()["balcony"].get("timezone_name", "Europe/Berlin")
                now = local_now(str(timezone_name))
                if payload.get("until"):
                    pause_until = datetime.fromisoformat(str(payload["until"]))
                    if pause_until.tzinfo is None:
                        pause_until = pause_until.replace(tzinfo=now.tzinfo)
                else:
                    pause_until = datetime.combine(now.date() + timedelta(days=1), time(0, 0), tzinfo=now.tzinfo)
                set_setting("automation_pause_until", pause_until.isoformat())
                send_json(self, {"automation_pause_until": pause_until.isoformat()})
                return
            if parsed.path == "/api/automation/resume":
                delete_setting("automation_pause_until")
                send_json(self, {"automation_pause_until": ""})
                return
        except (ValueError, KeyError, sqlite3.IntegrityError, json.JSONDecodeError) as exc:
            send_json(self, {"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return
        send_json(self, {"error": "Not found"}, HTTPStatus.NOT_FOUND)

    def do_PUT(self) -> None:
        parsed = urlparse(self.path)
        try:
            if parsed.path.startswith("/api/plants/"):
                plant_id = int(parsed.path.rsplit("/", 1)[1])
                update_plant(plant_id, read_json(self))
                send_json(self, get_state())
                return
        except (ValueError, KeyError, sqlite3.IntegrityError, json.JSONDecodeError) as exc:
            send_json(self, {"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return
        send_json(self, {"error": "Not found"}, HTTPStatus.NOT_FOUND)

    def do_DELETE(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/plants/"):
            plant_id = int(parsed.path.rsplit("/", 1)[1])
            with connect() as conn:
                conn.execute("UPDATE irrigation_hoses SET plant_id = NULL WHERE plant_id = ?", (plant_id,))
                conn.execute("DELETE FROM plants WHERE id = ?", (plant_id,))
            send_json(self, get_state())
            return
        send_json(self, {"error": "Not found"}, HTTPStatus.NOT_FOUND)


def first(params: dict[str, list[str]], name: str, default: str) -> str:
    values = params.get(name)
    return values[0] if values else default


def orientation_name_from_degrees(degrees: float) -> str:
    names = [
        (0, "north"),
        (45, "northeast"),
        (90, "east"),
        (135, "southeast"),
        (180, "south"),
        (225, "southwest"),
        (270, "west"),
        (315, "northwest"),
    ]
    nearest = min(names, key=lambda item: angular_distance(degrees, item[0]))
    return nearest[1]


def save_balcony(payload: dict) -> None:
    required = [
        "orientation_deg",
        "width_m",
        "depth_m",
        "latitude",
        "longitude",
        "tank_capacity_ml",
        "refill_pump_ml_per_min",
        "outlets",
        "walls",
    ]
    for key in required:
        if key not in payload:
            raise KeyError(f"{key} fehlt")

    orientation_deg = float(payload["orientation_deg"]) % 360
    amount_percent = payload.get("watering_amount_percent")
    calibration_percent = payload.get("water_model_calibration_percent")
    refill_enabled = payload.get("refill_automation_enabled")
    refill_times = payload.get("refill_schedule_times")
    refill_cooldown = payload.get("refill_cooldown_minutes_per_liter")
    main_consumption_factor = payload.get("main_pump_calibration_factor")
    if amount_percent is not None:
        amount_percent = float(amount_percent)
        if not MIN_WATERING_AMOUNT_PERCENT <= amount_percent <= MAX_WATERING_AMOUNT_PERCENT:
            raise ValueError(
                f"Gießmenge muss zwischen {MIN_WATERING_AMOUNT_PERCENT:g} und "
                f"{MAX_WATERING_AMOUNT_PERCENT:g} Prozent liegen"
            )
    if calibration_percent is not None:
        calibration_percent = float(calibration_percent)
        if not MIN_WATER_MODEL_CALIBRATION_PERCENT <= calibration_percent <= MAX_WATER_MODEL_CALIBRATION_PERCENT:
            raise ValueError(
                f"Wasser-Skalierung muss zwischen {MIN_WATER_MODEL_CALIBRATION_PERCENT:g} und "
                f"{MAX_WATER_MODEL_CALIBRATION_PERCENT:g} Prozent liegen"
            )
    with connect() as conn:
        conn.execute(
            """
            UPDATE balcony_settings
            SET orientation = ?, orientation_deg = ?, width_m = ?, depth_m = ?, location = ?, latitude = ?,
                longitude = ?, timezone_name = ?, wall_height_m = ?,
                tank_capacity_ml = ?, tank_current_ml = MIN(tank_current_ml, ?), refill_tank_capacity_ml = ?,
                refill_tank_current_ml = MIN(refill_tank_current_ml, ?), refill_pump_ml_per_min = ?, updated_at = ?
            WHERE id = 1
            """,
            (
                orientation_name_from_degrees(orientation_deg),
                orientation_deg,
                float(payload["width_m"]),
                float(payload["depth_m"]),
                "",
                float(payload["latitude"]),
                float(payload["longitude"]),
                payload.get("timezone_name") or "Europe/Berlin",
                max(float(wall["height_m"]) for wall in payload["walls"]),
                int(payload["tank_capacity_ml"]),
                int(payload["tank_capacity_ml"]),
                int(payload.get("refill_tank_capacity_ml", DEFAULT_BALCONY["refill_tank_capacity_ml"])),
                int(payload.get("refill_tank_capacity_ml", DEFAULT_BALCONY["refill_tank_capacity_ml"])),
                int(payload["refill_pump_ml_per_min"]),
                now_iso(),
            ),
        )
        for outlet in payload["outlets"]:
            conn.execute(
                "UPDATE pump_outlets SET name = ?, ml_per_run = ? WHERE id = ?",
                (outlet["name"], int(outlet["ml_per_run"]), int(outlet["id"])),
            )
        for plant in conn.execute("SELECT id FROM plants"):
            sync_legacy_plant_connection(conn, int(plant["id"]))
        for wall in payload["walls"]:
            conn.execute(
                """
                INSERT INTO terrace_walls (side, height_m)
                VALUES (?, ?)
                ON CONFLICT(side) DO UPDATE SET height_m = excluded.height_m
                """,
                (wall["side"], float(wall["height_m"])),
            )
    if amount_percent is not None:
        save_watering_amount_percent(amount_percent)
    elif calibration_percent is not None:
        save_water_model_calibration_percent(calibration_percent)
    if refill_enabled is not None:
        save_refill_automation_enabled(refill_enabled)
    if refill_times is not None:
        save_refill_schedule_times(refill_times)
    if refill_cooldown is not None:
        save_refill_cooldown_minutes_per_liter(refill_cooldown)
    if main_consumption_factor is not None:
        save_main_pump_calibration_factor(main_consumption_factor)


def add_plant(payload: dict) -> int:
    required = ["catalog_id", "custom_name", "size", "pot_liters", "pot_type"]
    for key in required:
        if key not in payload:
            raise KeyError(f"{key} fehlt")
    hose_numbers = normalize_hose_numbers(payload.get("hose_numbers", ""))
    with connect() as conn:
        outlet_id = default_outlet_id_for_conn(conn)
        cursor = conn.execute(
            """
            INSERT INTO plants
                (catalog_id, custom_name, size, pot_liters, pot_type, outlet_id, pos_x, pos_y,
                 hose_numbers, target_ml_per_cycle, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                payload["catalog_id"],
                payload["custom_name"].strip() or "Pflanze",
                payload["size"],
                float(payload["pot_liters"]),
                payload["pot_type"],
                outlet_id,
                float(payload.get("pos_x", 0.5)),
                float(payload.get("pos_y", 0.5)),
                hose_numbers,
                None,
                now_iso(),
            ),
        )
        plant_id = int(cursor.lastrowid)
        assign_hoses_to_plant(conn, plant_id, hose_numbers)
        return plant_id


def update_plant(plant_id: int, payload: dict) -> None:
    required = ["catalog_id", "custom_name", "size", "pot_liters", "pot_type"]
    for key in required:
        if key not in payload:
            raise KeyError(f"{key} fehlt")

    hose_numbers = normalize_hose_numbers(payload.get("hose_numbers", ""))
    with connect() as conn:
        cursor = conn.execute(
            """
            UPDATE plants
            SET catalog_id = ?,
                custom_name = ?,
                size = ?,
                pot_liters = ?,
                pot_type = ?
            WHERE id = ?
            """,
            (
                payload["catalog_id"],
                payload["custom_name"].strip() or "Pflanze",
                payload["size"],
                float(payload["pot_liters"]),
                payload["pot_type"],
                plant_id,
            ),
        )
        if cursor.rowcount == 0:
            raise ValueError("Pflanze nicht gefunden")
        assign_hoses_to_plant(conn, plant_id, hose_numbers)


def default_outlet_id() -> int:
    with connect() as conn:
        return default_outlet_id_for_conn(conn)


def update_plant_position(plant_id: int, payload: dict) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE plants SET pos_x = ?, pos_y = ? WHERE id = ?",
            (clamp(float(payload["pos_x"]), 0, 1), clamp(float(payload["pos_y"]), 0, 1), plant_id),
        )


def mark_refill_run(source: str = "home_assistant") -> dict:
    state = get_state()
    pending = pending_refill_request()
    refill = refill_status(state["balcony"])
    if pending:
        refill = {
            **refill,
            "target_date": pending.get("target_date", refill.get("target_date", "")),
            "requested_ml": int(pending.get("requested_ml", 0)),
            "planned_transfer_ml": int(pending.get("transferred_ml", 0)),
            "duration_seconds": int(pending.get("duration_seconds", 0)),
            "window_label": str(pending.get("window_label", "manual")),
        }
        source = str(pending.get("source", "manual"))
    elif not refill["enabled"]:
        raise ValueError("Automatisches Nachfüllen ist deaktiviert")
    elif not refill["schedule_due"]:
        raise ValueError("Für dieses Nachfüllzeitfenster wurde bereits ein Lauf verbucht oder es ist noch nicht erreicht")
    transferred_ml = int(refill["planned_transfer_ml"])
    if transferred_ml <= 0:
        raise ValueError(refill["summary"] or "Keine Nachfüllung nötig")
    now = local_now(str(state["balcony"].get("timezone_name", "Europe/Berlin"))).astimezone(timezone.utc).isoformat()
    with connect() as conn:
        conn.execute(
            """
            UPDATE balcony_settings
            SET tank_current_ml = MIN(tank_capacity_ml, tank_current_ml + ?),
                refill_tank_current_ml = MAX(0, refill_tank_current_ml - ?),
                updated_at = ?
            WHERE id = 1
            """,
            (transferred_ml, transferred_ml, now),
        )
        conn.execute(
            """
            INSERT INTO refill_events (ran_at, target_date, requested_ml, transferred_ml, duration_seconds, window_label, source)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                now,
                refill["target_date"],
                int(refill["requested_ml"]),
                transferred_ml,
                int(refill["duration_seconds"]),
                refill.get("window_label") or refill.get("active_window") or refill.get("last_window") or "",
                source,
            ),
        )
    if pending:
        delete_setting("pending_refill_request")
    return refill


def fill_tank(tank_name: str) -> None:
    if tank_name == "main":
        column = "tank_current_ml = tank_capacity_ml"
        current_column = "tank_current_ml"
        capacity_column = "tank_capacity_ml"
    elif tank_name == "refill":
        column = "refill_tank_current_ml = refill_tank_capacity_ml"
        current_column = "refill_tank_current_ml"
        capacity_column = "refill_tank_capacity_ml"
    else:
        raise ValueError("Unbekannter Tank")
    now = now_iso()
    with connect() as conn:
        row = conn.execute(
            f"SELECT {current_column} AS current_ml, {capacity_column} AS capacity_ml FROM balcony_settings WHERE id = 1"
        ).fetchone()
        if row is None:
            raise ValueError("Tankdaten nicht gefunden")
        conn.execute(
            f"UPDATE balcony_settings SET {column}, updated_at = ? WHERE id = 1",
            (now,),
        )
        conn.execute(
            """
            INSERT INTO tank_fill_events (ran_at, tank_name, previous_ml, new_ml, capacity_ml, source)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                now,
                tank_name,
                int(row["current_ml"]),
                int(row["capacity_ml"]),
                int(row["capacity_ml"]),
                "ui",
            ),
        )


def mark_run(delivered_ml: int, temperature_c: float, rain_mm: float) -> None:
    actual_consumed_ml = calibrated_consumption_ml(delivered_ml)
    timestamp = now_iso()
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO watering_events (ran_at, delivered_ml, actual_consumed_ml, temperature_c, rain_mm, source)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (timestamp, delivered_ml, actual_consumed_ml, temperature_c, rain_mm, "homekit"),
        )
        conn.execute(
            """
            UPDATE balcony_settings
            SET tank_current_ml = MAX(0, tank_current_ml - ?), updated_at = ?
            WHERE id = 1
            """,
            (actual_consumed_ml, timestamp),
        )


def main() -> None:
    ensure_data_dir_writable()
    init_db()
    port = int(os.environ.get("PORT", "8080"))
    host = os.environ.get("HOST", "127.0.0.1")
    server = ThreadingHTTPServer((host, port), AppHandler)
    print(f"Bewässerungsplaner läuft auf http://{host}:{port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
