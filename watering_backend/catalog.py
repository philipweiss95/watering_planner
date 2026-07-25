"""Plant catalogue, model profiles, and domain defaults."""

from __future__ import annotations


PLANT_CATALOG: list[tuple[str, str, str, int, float, float, str]] = [
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


PLANT_WATER_PROFILES: dict[str, dict[str, float]] = {
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


DEFAULT_BALCONY: dict[str, object] = {
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

DEFAULT_WALLS: list[tuple[str, float]] = [
    ("north", 0.0),
    ("east", 0.0),
    ("south", 1.05),
    ("west", 0.0),
]

CONNECTION_DESIGN: dict[str, int] = {
    "temperature_c": 26,
    "rain_mm": 0,
    "wind_kmh": 8,
    "sunshine_hours": 7,
    "cycles": 4,
}

# The raw ET0/canopy estimate models open-surface evapotranspiration. The
# terrace drip setup needs a calibrated fraction per day.
WATER_MODEL_CALIBRATION = 0.20
PREVIOUS_WATER_MODEL_CALIBRATION = 0.08
LEGACY_WATER_MODEL_CALIBRATIONS = (0.04, 0.06, PREVIOUS_WATER_MODEL_CALIBRATION)
LEGACY_WATER_MODEL_CALIBRATION = 0.06
MIN_WATER_MODEL_CALIBRATION_PERCENT = 0.5
MAX_WATER_MODEL_CALIBRATION_PERCENT = 40.0
MIN_WATERING_AMOUNT_PERCENT = 40.0
MAX_WATERING_AMOUNT_PERCENT = 500.0
TANK_LOW_PERCENT = 20

SEASONAL_WATER_CURVES: dict[str, list[tuple[int, float]]] = {
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
REFILL_RUN_TIMES = ("01:00", "06:00")
REFILL_TRIGGER_TOLERANCE_MINUTES = 60
REFILL_MIN_INTERVAL_MINUTES = 3 * 60
REFILL_TRANSFER_FRACTION = 0.5
REFILL_COOLDOWN_MINUTES_PER_LITER = 30
REFILL_MIN_COOLDOWN_MINUTES = 15
REFILL_MAX_COOLDOWN_MINUTES = 12 * 60
WEATHER_FORECAST_DAYS = 16
TANK_FORECAST_DAYS = 45
