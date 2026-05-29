from __future__ import annotations

import json
import math
import os
import re
import sqlite3
import ssl
from copy import deepcopy
from hashlib import sha256
from contextlib import contextmanager
from datetime import date, datetime, timezone
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Iterator
from urllib.error import URLError
from urllib.request import urlopen
from urllib.parse import parse_qs, urlencode, urlparse
from zipfile import ZipFile
from xml.etree import ElementTree as ET


ROOT = Path(__file__).parent
PUBLIC_DIR = ROOT / "public"
DATA_DIR = Path(os.environ.get("DATA_DIR", ROOT / "data"))
DB_PATH = Path(os.environ.get("DB_PATH", DATA_DIR / "watering.sqlite3"))


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

TABLE_PLANT_ALIASES = {
    "basilikum": "basil",
    "lavendel": "lavender",
    "zucchini": "zucchini",
    "nelke": "carnation",
    "olive": "olive",
    "tomate": "tomato",
    "himbeere": "raspberry",
    "paprika": "chili",
    "chili": "chili",
    "kiefer": "pine",
    "mispel": "loquat",
    "erdbeere": "strawberry",
    "rosmarin": "rosemary",
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
# terrace drip setup needs a much smaller calibrated fraction per day.
WATER_MODEL_CALIBRATION = 0.03

SEASONAL_WATER_CURVES = {
    "warm_annual": [(1, 0.22), (80, 0.25), (130, 0.42), (172, 0.78), (220, 1.0), (280, 0.62), (335, 0.28), (366, 0.22)],
    "annual": [(1, 0.28), (80, 0.32), (130, 0.5), (172, 0.82), (220, 1.0), (280, 0.68), (335, 0.32), (366, 0.28)],
    "woody": [(1, 0.38), (80, 0.45), (130, 0.62), (172, 0.88), (220, 1.0), (280, 0.72), (335, 0.42), (366, 0.38)],
    "evergreen": [(1, 0.45), (80, 0.5), (130, 0.65), (172, 0.88), (220, 1.0), (280, 0.78), (335, 0.5), (366, 0.45)],
    "succulent": [(1, 0.55), (80, 0.58), (130, 0.68), (172, 0.82), (220, 0.9), (280, 0.72), (335, 0.58), (366, 0.55)],
}


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
            """
        )
        ensure_column(conn, "balcony_settings", "latitude", "REAL NOT NULL DEFAULT 52.52")
        ensure_column(conn, "balcony_settings", "longitude", "REAL NOT NULL DEFAULT 13.405")
        ensure_column(conn, "balcony_settings", "timezone_name", "TEXT NOT NULL DEFAULT 'Europe/Berlin'")
        ensure_column(conn, "balcony_settings", "orientation_deg", "REAL NOT NULL DEFAULT 180")
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

        if conn.execute("SELECT COUNT(*) FROM balcony_settings").fetchone()[0] == 0:
            conn.execute(
                """
                INSERT INTO balcony_settings
                    (id, orientation, orientation_deg, width_m, depth_m, location, latitude, longitude, timezone_name, wall_height_m,
                     tank_capacity_ml, tank_current_ml, updated_at)
                VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
        elif (DATA_DIR / "table.xlsx").exists() and has_unimported_plants(conn):
            conn.execute("DELETE FROM plants")
            seed_plants(conn)


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
    table_plants = plants_from_table_xlsx(DATA_DIR / "table.xlsx", conn)
    if table_plants:
        conn.executemany(
            """
            INSERT INTO plants
                (catalog_id, custom_name, size, pot_liters, pot_type, outlet_id, pos_x, pos_y,
                 hose_numbers, target_ml_per_cycle, created_at)
            VALUES
                (:catalog_id, :custom_name, :size, :pot_liters, :pot_type, :outlet_id, :pos_x, :pos_y,
                 :hose_numbers, :target_ml_per_cycle, :created_at)
            """,
            table_plants,
        )
        return

    conn.executemany(
        """
        INSERT INTO plants
            (catalog_id, custom_name, size, pot_liters, pot_type, outlet_id, pos_x, pos_y,
             hose_numbers, target_ml_per_cycle, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            ("olive", "Olive", "medium", 28, "overflow", 2, 0.25, 0.7, "", None, now_iso()),
            ("tomato", "Tomate", "medium", 18, "closed", 3, 0.7, 0.55, "", None, now_iso()),
            ("lavender", "Lavendel", "small", 10, "overflow", 1, 0.45, 0.25, "", None, now_iso()),
        ],
    )


def has_unimported_plants(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        """
        SELECT
            COUNT(*) AS total,
            SUM(CASE WHEN COALESCE(hose_numbers, '') = '' AND target_ml_per_cycle IS NULL THEN 1 ELSE 0 END) AS unimported
        FROM plants
        """
    ).fetchone()
    return int(row["total"]) > 0 and int(row["total"]) == int(row["unimported"] or 0)


def plants_from_table_xlsx(path: Path, conn: sqlite3.Connection) -> list[dict]:
    if not path.exists():
        return []

    rows = xlsx_rows(path)
    header_index = next(
        (
            index
            for index, row in enumerate(rows)
            if "name" in [normalize_table_header(cell) for cell in row]
            and any("groesse" in normalize_table_header(cell) for cell in row)
        ),
        None,
    )
    if header_index is None:
        return []

    headers = [normalize_table_header(cell) for cell in rows[header_index]]
    created_at = now_iso()
    plants = []
    for index, row in enumerate(rows[header_index + 1 :], start=1):
        values = dict(zip(headers, row))
        name = values.get("name", "").strip()
        if not name:
            continue
        catalog_id = catalog_id_for_table_name(name)
        size = table_size(values.get("groesse", ""))
        target_ml = parse_target_ml(values.get("ml-menge pro pumpzyklus", ""))
        hose_numbers = values.get("nummer schlauch", "").strip()
        plants.append(
            {
                "catalog_id": catalog_id,
                "custom_name": name,
                "size": size,
                "pot_liters": estimated_pot_liters(conn, catalog_id, size),
                "pot_type": table_pot_type(name),
                "outlet_id": outlet_id_for_target_ml(conn, target_ml),
                "pos_x": table_position(name, hose_numbers, "x"),
                "pos_y": table_position(name, hose_numbers, "y"),
                "hose_numbers": hose_numbers,
                "target_ml_per_cycle": target_ml,
                "created_at": created_at,
            }
        )
    return plants


def normalize_table_header(value: str) -> str:
    normalized = value.strip().casefold().replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss")
    return re.sub(r"\s+", " ", normalized)


def xlsx_rows(path: Path) -> list[list[str]]:
    ns = {"a": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    with ZipFile(path) as archive:
        shared_strings = []
        if "xl/sharedStrings.xml" in archive.namelist():
            shared_root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
            for item in shared_root.findall("a:si", ns):
                shared_strings.append("".join(text.text or "" for text in item.findall(".//a:t", ns)))

        sheet_root = ET.fromstring(archive.read("xl/worksheets/sheet1.xml"))
        parsed_rows = []
        for row in sheet_root.findall(".//a:sheetData/a:row", ns):
            parsed = []
            for cell in row.findall("a:c", ns):
                column_index = xlsx_column_index(cell.attrib.get("r", "A1"))
                while len(parsed) <= column_index:
                    parsed.append("")
                parsed[column_index] = xlsx_cell_value(cell, shared_strings, ns)
            parsed_rows.append(parsed)
        return parsed_rows


def xlsx_column_index(reference: str) -> int:
    letters = re.match(r"[A-Z]+", reference.upper())
    if not letters:
        return 0
    index = 0
    for char in letters.group(0):
        index = index * 26 + (ord(char) - ord("A") + 1)
    return index - 1


def xlsx_cell_value(cell: ET.Element, shared_strings: list[str], ns: dict[str, str]) -> str:
    if cell.attrib.get("t") == "inlineStr":
        return "".join(text.text or "" for text in cell.findall(".//a:t", ns)).strip()
    value = cell.find("a:v", ns)
    if value is None or value.text is None:
        return ""
    text = value.text.strip()
    if cell.attrib.get("t") == "s" and text:
        return shared_strings[int(text)].strip()
    return text


def catalog_id_for_table_name(name: str) -> str:
    normalized = normalize_plant_name(name)
    return TABLE_PLANT_ALIASES.get(normalized) or TABLE_PLANT_ALIASES.get(normalized.split(" ", 1)[0]) or normalized


def normalize_plant_name(name: str) -> str:
    first_part = name.split(",", 1)[0]
    normalized = first_part.casefold().replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss")
    normalized = re.sub(r"[^a-z0-9]+", " ", normalized).strip()
    return normalized


def table_size(value: str) -> str:
    normalized = value.casefold().replace("ß", "ss")
    if "baum" in normalized or "strauch" in normalized:
        return "tree"
    if "gross" in normalized or "groß" in normalized:
        return "large"
    if "mittel" in normalized:
        return "medium"
    return "small"


def parse_target_ml(value: str) -> int | None:
    if not value:
        return None
    tail = value.split("=", 1)[1] if "=" in value else value
    numbers = [int(match) for match in re.findall(r"\d+", tail)]
    if not numbers:
        return None
    if "=" in value:
        return numbers[0]
    return sum(numbers)


def table_pot_type(name: str) -> str:
    if "lechuza" in name.casefold():
        return "reservoir_overflow"
    return "overflow"


def estimated_pot_liters(conn: sqlite3.Connection, catalog_id: str, size: str) -> float:
    row = conn.execute(
        "SELECT recommended_pot_liters FROM plant_catalog WHERE id = ?",
        (catalog_id,),
    ).fetchone()
    recommended = float(row["recommended_pot_liters"]) if row else 10.0
    multiplier = {"small": 0.75, "medium": 1.0, "large": 1.35, "tree": 1.8}.get(size, 1.0)
    return round(recommended * multiplier)


def outlet_id_for_target_ml(conn: sqlite3.Connection, target_ml: int | None) -> int:
    if target_ml is None:
        return default_outlet_id_for_conn(conn)
    row = conn.execute(
        """
        SELECT id
        FROM pump_outlets
        ORDER BY ABS(ml_per_run - ?), ml_per_run
        LIMIT 1
        """,
        (target_ml,),
    ).fetchone()
    return int(row["id"]) if row else default_outlet_id_for_conn(conn)


def default_outlet_id_for_conn(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT id FROM pump_outlets ORDER BY ml_per_run LIMIT 1").fetchone()
    return int(row["id"])


def table_position(name: str, hose_numbers: str, axis: str) -> float:
    digest = sha256(f"{name}|{hose_numbers}|{axis}".encode("utf-8")).digest()
    return round(0.12 + int.from_bytes(digest[:2], "big") / 65535 * 0.76, 3)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def row_to_dict(row: sqlite3.Row) -> dict:
    return {key: row[key] for key in row.keys()}


def get_state() -> dict:
    with connect() as conn:
        balcony = row_to_dict(conn.execute("SELECT * FROM balcony_settings WHERE id = 1").fetchone())
        outlets = [row_to_dict(row) for row in conn.execute("SELECT * FROM pump_outlets ORDER BY ml_per_run")]
        walls = [row_to_dict(row) for row in conn.execute("SELECT * FROM terrace_walls ORDER BY side")]
        catalog = [row_to_dict(row) for row in conn.execute("SELECT * FROM plant_catalog ORDER BY category, name")]
        plants = [
            row_to_dict(row)
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
        "balcony": balcony,
        "outlets": outlets,
        "walls": walls,
        "catalog": catalog,
        "plants": plants,
        "cycles_completed_today": completed_cycles_today(),
    }


def completed_cycles_today() -> int:
    with connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS count FROM watering_events WHERE substr(ran_at, 1, 10) = ?",
            (datetime.now(timezone.utc).date().isoformat(),),
        ).fetchone()
        return int(row["count"])


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
) -> dict:
    canopy_area = plant_canopy_area_m2(plant)
    season = seasonal_water_factor(plant)
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
    daily_need_ml = raw_daily_need_ml * WATER_MODEL_CALIBRATION * season["factor"]
    return {
        "daily_need_ml": daily_need_ml,
        "raw_daily_need_ml": raw_daily_need_ml,
        "calibration_factor": WATER_MODEL_CALIBRATION,
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
        "daily": "precipitation_sum,temperature_2m_max,sunshine_duration,et0_fao_evapotranspiration",
        "forecast_days": 1,
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
    return {
        "source": "open-meteo",
        "latitude": latitude,
        "longitude": longitude,
        "timezone": timezone_name,
        "temperature_c": float(current.get("temperature_2m", first_value(daily, "temperature_2m_max", 20))),
        "rain_mm": float(first_value(daily, "precipitation_sum", current.get("rain", current.get("precipitation", 0)))),
        "current_rain_mm": float(current.get("rain", current.get("precipitation", 0))),
        "wind_kmh": float(current.get("wind_speed_10m", 0)),
        "sunshine_hours": round(float(sunshine_seconds) / 3600, 1),
        "et0_mm": float(first_value(daily, "et0_fao_evapotranspiration", 0)),
        "tls_verified": tls_verified,
        "fetched_at": now_iso(),
    }


def first_value(payload: dict, key: str, default: float) -> float:
    value = payload.get(key, default)
    if isinstance(value, list):
        return value[0] if value else default
    return value


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
    return result


def shortcut_blueprint(base_url: str) -> dict:
    check_url = f"{base_url.rstrip('/')}/api/homekit/check?auto=true&slot=morning"
    mark_url = f"{base_url.rstrip('/')}/api/homekit/mark-run"
    return {
        "name": "Terrassenbewässerung prüfen",
        "base_url_placeholder": base_url,
        "steps": [
            {"action": "URL", "value": check_url},
            {"action": "Inhalte von URL abrufen", "method": "GET", "headers": {"Accept": "application/json"}},
            {"action": "Wert für Schlüssel abrufen", "key": "should_run"},
            {"action": "Wenn", "condition": "should_run ist wahr"},
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
) -> dict:
    slot_multiplier = {"morning": 1.0, "midday": 0.72, "evening": 0.92}.get(slot, 1.0)
    orientation_deg = orientation_degrees(balcony)
    terrace_sun = estimate_sun_hours(balcony, walls)
    reference_et0_mm = et0_mm if et0_mm > 0 else estimate_reference_et0_mm(temperature_c, sunshine_hours, wind_kmh)
    wind_factor = wind_exposure_factor(wind_kmh, walls)
    orientation_multiplier = orientation_exposure_factor(orientation_deg)
    rain_credit_mm = rain_credit_factor(rain_mm, orientation_deg, walls)

    plant_results = []
    total_need_ml = 0
    for plant in plants:
        plant_sun = estimate_sun_hours(
            balcony,
            walls,
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
                "current_target_ml_per_cycle": plant["target_ml_per_cycle"],
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
    routing_plan = apply_fixed_connection_to_weather(connection_plan, plant_results, outlets)
    recommended_cycles = routing_plan["cycles"]
    assignment_by_plant = {assignment["plant_id"]: assignment for assignment in routing_plan["assignments"]}
    for plant in plant_results:
        assignment = assignment_by_plant.get(plant["id"])
        if assignment:
            plant["suggested_outlet"] = assignment["outlet_name"]
            plant["suggested_ml_per_run"] = assignment["ml_per_run"]
            plant["suggested_tubes"] = assignment["tubes"]
            plant["suggested_tube_label"] = assignment["tube_label"]
            plant["delivered_ml"] = assignment["delivered_ml"]
            plant["difference_ml"] = assignment["difference_ml"]
            plant["connection_status"] = assignment.get("connection_status")
            plant["connection_severity"] = assignment.get("connection_severity")
            plant["connection_action_title"] = assignment.get("connection_action_title")
            plant["connection_note"] = assignment.get("connection_note")

    total_delivered_per_run = sum(
        tube["ml_per_run"] * tube["count"]
        for assignment in routing_plan["assignments"]
        for tube in assignment["tubes"]
    )

    cycles_completed = completed_cycles_today()
    remaining_cycles = max(0, recommended_cycles - cycles_completed)
    delivered_if_remaining = remaining_cycles * total_delivered_per_run
    tank_after = max(0, balcony["tank_current_ml"] - delivered_if_remaining)

    hottest_sensitive_need = max((p["need_ml"] for p in plant_results), default=0)
    rain_threshold = round(clamp(2.2 + (temperature_c - 22) * 0.18 + hottest_sensitive_need / 1200, 0.8, 8.0), 1)
    temp_threshold = 16 if rain_mm < 1 else 22
    should_run = (
        bool(plants)
        and remaining_cycles > 0
        and rain_mm < rain_threshold
        and temperature_c >= temp_threshold
    )

    if plants and balcony["tank_current_ml"] < total_delivered_per_run:
        should_run = False
        reason = "Wassertank reicht nicht für einen vollständigen Pumpenlauf"
    elif not plants:
        reason = "Noch keine Pflanzen angelegt"
    elif recommended_cycles == 0:
        reason = "Heute ist mit dem festen Anschlussplan kein Pumpenlauf nötig"
    elif remaining_cycles == 0 and recommended_cycles > 0:
        reason = "Alle empfohlenen Zyklen für heute sind bereits verbucht"
    elif should_run:
        reason = f"Wasserbedarf {round(total_need_ml)} ml, Regen unter Schwelle"
    elif rain_mm >= rain_threshold:
        reason = f"Erwarteter Regen {rain_mm:g} mm deckt genug Bedarf"
    else:
        reason = f"Temperatur {temperature_c:g} C unter Schwelle {temp_threshold:g} C"

    return {
        "should_run": should_run,
        "reason": reason,
        "recommended_cycles_today": recommended_cycles,
        "cycles_completed_today": cycles_completed,
        "remaining_cycles_today": remaining_cycles,
        "thresholds": {
            "temperature_c": temp_threshold,
            "rain_mm": rain_threshold,
        },
        "pump": {
            "duration_seconds": 60,
            "delivered_per_cycle_ml": total_delivered_per_run,
            "delivered_if_remaining_ml": delivered_if_remaining,
        },
        "tank": {
            "current_ml": balcony["tank_current_ml"],
            "after_recommended_ml": tank_after,
            "capacity_ml": balcony["tank_capacity_ml"],
        },
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
        "calculated_at": now_iso(),
    }


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
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


class AppHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(PUBLIC_DIR), **kwargs)

    def log_message(self, format: str, *args) -> None:
        print(f"[{self.log_date_time_string()}] {format % args}")

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/state":
            send_json(self, get_state())
            return
        if parsed.path == "/api/weather":
            try:
                send_json(self, fetch_weather(get_state()["balcony"]))
            except ValueError as exc:
                send_json(self, {"error": str(exc)}, HTTPStatus.BAD_GATEWAY)
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
        "tank_current_ml",
        "outlets",
        "walls",
    ]
    for key in required:
        if key not in payload:
            raise KeyError(f"{key} fehlt")

    orientation_deg = float(payload["orientation_deg"]) % 360
    with connect() as conn:
        conn.execute(
            """
            UPDATE balcony_settings
            SET orientation = ?, orientation_deg = ?, width_m = ?, depth_m = ?, location = ?, latitude = ?,
                longitude = ?, timezone_name = ?, wall_height_m = ?,
                tank_capacity_ml = ?, tank_current_ml = ?, updated_at = ?
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
                int(payload["tank_current_ml"]),
                now_iso(),
            ),
        )
        for outlet in payload["outlets"]:
            conn.execute(
                "UPDATE pump_outlets SET name = ?, ml_per_run = ? WHERE id = ?",
                (outlet["name"], int(outlet["ml_per_run"]), int(outlet["id"])),
            )
        for wall in payload["walls"]:
            conn.execute(
                """
                INSERT INTO terrace_walls (side, height_m)
                VALUES (?, ?)
                ON CONFLICT(side) DO UPDATE SET height_m = excluded.height_m
                """,
                (wall["side"], float(wall["height_m"])),
            )


def add_plant(payload: dict) -> int:
    required = ["catalog_id", "custom_name", "size", "pot_liters", "pot_type"]
    for key in required:
        if key not in payload:
            raise KeyError(f"{key} fehlt")
    outlet_id = int(payload.get("outlet_id") or default_outlet_id())
    with connect() as conn:
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
                str(payload.get("hose_numbers", "")).strip(),
                int(payload["target_ml_per_cycle"]) if payload.get("target_ml_per_cycle") else None,
                now_iso(),
            ),
        )
        return int(cursor.lastrowid)


def update_plant(plant_id: int, payload: dict) -> None:
    required = ["catalog_id", "custom_name", "size", "pot_liters", "pot_type"]
    for key in required:
        if key not in payload:
            raise KeyError(f"{key} fehlt")

    target_ml_per_cycle = payload.get("target_ml_per_cycle")
    outlet_id = int(payload.get("outlet_id") or default_outlet_id())
    with connect() as conn:
        cursor = conn.execute(
            """
            UPDATE plants
            SET catalog_id = ?,
                custom_name = ?,
                size = ?,
                pot_liters = ?,
                pot_type = ?,
                outlet_id = ?,
                hose_numbers = ?,
                target_ml_per_cycle = ?
            WHERE id = ?
            """,
            (
                payload["catalog_id"],
                payload["custom_name"].strip() or "Pflanze",
                payload["size"],
                float(payload["pot_liters"]),
                payload["pot_type"],
                outlet_id,
                str(payload.get("hose_numbers", "")).strip(),
                int(target_ml_per_cycle) if target_ml_per_cycle not in (None, "") else None,
                plant_id,
            ),
        )
        if cursor.rowcount == 0:
            raise ValueError("Pflanze nicht gefunden")


def default_outlet_id() -> int:
    with connect() as conn:
        row = conn.execute("SELECT id FROM pump_outlets ORDER BY ml_per_run LIMIT 1").fetchone()
        return int(row["id"])


def update_plant_position(plant_id: int, payload: dict) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE plants SET pos_x = ?, pos_y = ? WHERE id = ?",
            (clamp(float(payload["pos_x"]), 0, 1), clamp(float(payload["pos_y"]), 0, 1), plant_id),
        )


def mark_run(delivered_ml: int, temperature_c: float, rain_mm: float) -> None:
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO watering_events (ran_at, delivered_ml, temperature_c, rain_mm, source)
            VALUES (?, ?, ?, ?, ?)
            """,
            (now_iso(), delivered_ml, temperature_c, rain_mm, "homekit"),
        )
        conn.execute(
            """
            UPDATE balcony_settings
            SET tank_current_ml = MAX(0, tank_current_ml - ?), updated_at = ?
            WHERE id = 1
            """,
            (delivered_ml, now_iso()),
        )


def main() -> None:
    init_db()
    port = int(os.environ.get("PORT", "8080"))
    host = os.environ.get("HOST", "127.0.0.1")
    server = ThreadingHTTPServer((host, port), AppHandler)
    print(f"Bewässerungsplaner läuft auf http://{host}:{port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
