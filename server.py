from __future__ import annotations

import json
import math
import os
import sqlite3
import ssl
from contextlib import contextmanager
from datetime import date, datetime, timezone
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Iterator
from urllib.error import URLError
from urllib.request import urlopen
from urllib.parse import parse_qs, urlencode, urlparse


ROOT = Path(__file__).parent
PUBLIC_DIR = ROOT / "public"
DATA_DIR = ROOT / "data"
DB_PATH = DATA_DIR / "watering.sqlite3"


PLANT_CATALOG = [
    ("olive", "Olivenbaum", "Mediterrane Gehölze", 22, 1.15, 0.8, "Mag es hell, eher trocken, aber nicht komplett austrocknen lassen."),
    ("citrus", "Zitrusbaum", "Mediterrane Gehölze", 30, 1.15, 0.95, "Hell und gleichmäßig feucht, bei Hitze und Fruchtansatz durstiger."),
    ("fig", "Feigenbaum", "Mediterrane Gehölze", 28, 1.1, 0.85, "Robust, im Kübel bei voller Sonne aber mit deutlichem Wasserbedarf."),
    ("oleander", "Oleander", "Mediterrane Gehölze", 36, 1.2, 0.95, "Verträgt viel Sonne und braucht im Sommer reichlich Wasser."),
    ("bay", "Lorbeer", "Mediterrane Gehölze", 24, 1.0, 0.8, "Mag gleichmäßige Feuchte, aber keine dauerhafte Staunässe."),
    ("loquat", "Mispel", "Obstgehölze", 28, 1.05, 0.9, "Gleichmäßige Feuchte, bei Hitze deutlich durstiger."),
    ("tomato", "Tomatenpflanze", "Gemüse", 48, 1.25, 1.2, "Sehr hoher Bedarf, regelmäßige Wassergaben vermeiden Fruchtplatzen."),
    ("cucumber", "Gurke", "Gemüse", 58, 1.15, 1.25, "Sehr durstig, besonders bei Fruchtbildung und Wind."),
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
    "tomato": {"crop_coefficient": 1.15, "canopy_m2_medium": 0.45, "recommended_pot_liters": 20, "moisture_preference": 1.12},
    "raspberry": {"crop_coefficient": 0.98, "canopy_m2_medium": 0.42, "recommended_pot_liters": 25, "moisture_preference": 1.08},
    "lavender": {"crop_coefficient": 0.45, "canopy_m2_medium": 0.2, "recommended_pot_liters": 10, "moisture_preference": 0.62},
    "basil": {"crop_coefficient": 0.96, "canopy_m2_medium": 0.16, "recommended_pot_liters": 7, "moisture_preference": 1.08},
    "rosemary": {"crop_coefficient": 0.5, "canopy_m2_medium": 0.22, "recommended_pot_liters": 12, "moisture_preference": 0.66},
    "hydrangea": {"crop_coefficient": 1.1, "canopy_m2_medium": 0.48, "recommended_pot_liters": 20, "moisture_preference": 1.25},
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

        if conn.execute("SELECT COUNT(*) FROM plants").fetchone()[0] == 0:
            conn.executemany(
                """
                INSERT INTO plants
                    (catalog_id, custom_name, size, pot_liters, pot_type, outlet_id, pos_x, pos_y, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    ("olive", "Olive", "medium", 28, "overflow", 2, 0.25, 0.7, now_iso()),
                    ("tomato", "Tomate", "medium", 18, "closed", 3, 0.7, 0.55, now_iso()),
                    ("lavender", "Lavendel", "small", 10, "overflow", 1, 0.45, 0.25, now_iso()),
                ],
            )


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
    daily_need_ml = max(0, irrigation_need_ml - rain_credit_ml)
    return {
        "daily_need_ml": daily_need_ml,
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
        option_sets = [plant_tube_options(plant, options, cycles) for plant in plants]
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

    grouped: dict[int, dict] = {}
    for assignment in best["assignments"]:
        for tube in assignment["tubes"]:
            delivered_by_tube = best["cycles"] * tube["ml_per_run"] * tube["count"]
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

    best["by_outlet"] = sorted(grouped.values(), key=lambda item: item["ml_per_run"])
    best["outlet_limits"] = outlet_limits
    best["summary"] = routing_summary(best)
    return best


def plant_tube_options(plant: dict, outlets: list[dict], cycles: int) -> list[dict]:
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
                option_score += max(0, tube_count - 2) * 22
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
                "position": {"x": plant["pos_x"], "y": plant["pos_y"]},
                "need_ml": round(scheduled_need),
                "daily_need_ml": round(daily_need),
                "water_model": {
                    "reference_et0_mm": reference_et0_mm,
                    "canopy_area_m2": water_need["canopy_area_m2"],
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

    routing_plan = optimize_routing(plant_results, outlets)
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
            INSERT INTO plants (catalog_id, custom_name, size, pot_liters, pot_type, outlet_id, pos_x, pos_y, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                now_iso(),
            ),
        )
        return int(cursor.lastrowid)


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
