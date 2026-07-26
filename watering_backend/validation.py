from __future__ import annotations

import math
import re
from typing import Any, Iterable, get_args

from watering_backend.models import PlantSize


PLANT_SIZES = frozenset(get_args(PlantSize))
POT_TYPES = frozenset({"overflow", "reservoir", "reservoir_overflow", "closed"})
WALL_SIDES = frozenset({"north", "east", "south", "west"})


def finite_number(
    value: object,
    field: str,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} muss eine Zahl sein") from exc
    if not math.isfinite(result):
        raise ValueError(f"{field} muss endlich sein")
    if minimum is not None and result < minimum:
        raise ValueError(f"{field} muss mindestens {minimum:g} sein")
    if maximum is not None and result > maximum:
        raise ValueError(f"{field} darf hoechstens {maximum:g} sein")
    return result


def finite_integer(
    value: object,
    field: str,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    number = finite_number(value, field, minimum=minimum, maximum=maximum)
    if not number.is_integer():
        raise ValueError(f"{field} muss eine ganze Zahl sein")
    return int(number)


def bounded_string(
    value: object,
    field: str,
    *,
    maximum: int,
    allow_empty: bool = False,
) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} muss Text sein")
    result = value.strip()
    if not allow_empty and not result:
        raise ValueError(f"{field} darf nicht leer sein")
    if len(result) > maximum:
        raise ValueError(f"{field} darf hoechstens {maximum} Zeichen lang sein")
    return result


def normalize_hose_numbers(value: object) -> list[str]:
    if value is None or value == "":
        return []
    if isinstance(value, (list, tuple)):
        raw = ", ".join(str(item) for item in value)
    elif isinstance(value, (str, int)):
        raw = str(value)
    else:
        raise ValueError("Schlauchnummern muessen als Text oder Liste angegeben werden")
    if len(raw) > 512:
        raise ValueError("Schlauchnummern sind zu lang")
    if not re.fullmatch(r"[\d\s,;]*", raw):
        raise ValueError("Schlauchnummern duerfen nur Ziffern und Trennzeichen enthalten")
    result: list[str] = []
    for number in re.findall(r"\d+", raw):
        if len(number) > 16:
            raise ValueError("Eine Schlauchnummer darf hoechstens 16 Stellen haben")
        if number not in result:
            result.append(number)
    if len(result) > 128:
        raise ValueError("Es duerfen hoechstens 128 Schlaeuche zugeordnet werden")
    return result


def _validate_keys(payload: object, allowed: set[str], required: set[str], label: str) -> dict:
    if not isinstance(payload, dict):
        raise ValueError(f"{label} muss ein Objekt sein")
    missing = required - set(payload)
    if missing:
        raise KeyError(f"{sorted(missing)[0]} fehlt")
    unknown = set(payload) - allowed
    if unknown:
        raise ValueError(f"Unbekanntes Feld in {label}: {sorted(unknown)[0]}")
    return payload


def validate_plant_payload(payload: object, catalog_ids: Iterable[str]) -> dict[str, Any]:
    allowed = {
        "catalog_id",
        "custom_name",
        "size",
        "pot_liters",
        "pot_type",
        "hose_numbers",
        "pos_x",
        "pos_y",
        # Legacy callers may still send this derived value. It is accepted for
        # compatibility but never trusted or persisted.
        "target_ml_per_cycle",
    }
    required = {"catalog_id", "custom_name", "size", "pot_liters", "pot_type"}
    data = _validate_keys(payload, allowed, required, "Pflanze")
    catalog_id = bounded_string(data["catalog_id"], "catalog_id", maximum=80)
    if catalog_id not in set(catalog_ids):
        raise ValueError("Unbekannte Pflanzenart")
    custom_name = bounded_string(data["custom_name"], "custom_name", maximum=80, allow_empty=True) or "Pflanze"
    size = bounded_string(data["size"], "size", maximum=20)
    if size not in PLANT_SIZES:
        raise ValueError("Unbekannte Pflanzengroesse")
    pot_type = bounded_string(data["pot_type"], "pot_type", maximum=40)
    if pot_type not in POT_TYPES:
        raise ValueError("Unbekannte Topfart")
    return {
        "catalog_id": catalog_id,
        "custom_name": custom_name,
        "size": size,
        "pot_liters": finite_number(data["pot_liters"], "pot_liters", minimum=0.1, maximum=1000),
        "pot_type": pot_type,
        "hose_numbers": ", ".join(normalize_hose_numbers(data.get("hose_numbers", ""))),
        "pos_x": finite_number(data.get("pos_x", 0.5), "pos_x", minimum=0, maximum=1),
        "pos_y": finite_number(data.get("pos_y", 0.5), "pos_y", minimum=0, maximum=1),
    }


def validate_position_payload(payload: object) -> tuple[float, float]:
    data = _validate_keys(payload, {"pos_x", "pos_y"}, {"pos_x", "pos_y"}, "Position")
    return (
        finite_number(data["pos_x"], "pos_x", minimum=0, maximum=1),
        finite_number(data["pos_y"], "pos_y", minimum=0, maximum=1),
    )


def validate_outlets(value: object, existing_ids: Iterable[int]) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value:
        raise ValueError("outlets muss eine nicht leere Liste sein")
    known = {int(item) for item in existing_ids}
    normalized = []
    seen: set[int] = set()
    for item in value:
        data = _validate_keys(
            item,
            {"id", "name", "ml_per_run", "max_connections"},
            {"id", "name", "ml_per_run"},
            "Ausgang",
        )
        outlet_id = finite_integer(data["id"], "Ausgangs-ID", minimum=1, maximum=1_000_000)
        if outlet_id not in known:
            raise ValueError(f"Unbekannte Ausgangs-ID: {outlet_id}")
        if outlet_id in seen:
            raise ValueError(f"Ausgangs-ID {outlet_id} ist doppelt vorhanden")
        seen.add(outlet_id)
        normalized.append(
            {
                "id": outlet_id,
                "name": bounded_string(data["name"], "Ausgangsname", maximum=80),
                "ml_per_run": finite_integer(
                    data["ml_per_run"],
                    "ml_per_run",
                    minimum=1,
                    maximum=1_000_000,
                ),
            }
        )
    if seen != known:
        raise ValueError("Alle vorhandenen Ausgaenge muessen angegeben werden")
    return normalized


def validate_walls(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value:
        raise ValueError("walls muss eine nicht leere Liste sein")
    normalized = []
    seen: set[str] = set()
    for item in value:
        data = _validate_keys(item, {"side", "height_m"}, {"side", "height_m"}, "Wand")
        side = bounded_string(data["side"], "Wandseite", maximum=10)
        if side not in WALL_SIDES:
            raise ValueError("Unbekannte Wandseite")
        if side in seen:
            raise ValueError(f"Wandseite {side} ist doppelt vorhanden")
        seen.add(side)
        normalized.append(
            {
                "side": side,
                "height_m": finite_number(data["height_m"], "Wandhoehe", minimum=0, maximum=20),
            }
        )
    return normalized
