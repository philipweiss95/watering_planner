"""Pure hose assignment and connection-plan optimization."""

from __future__ import annotations

from copy import deepcopy
from datetime import date

from watering_backend.catalog import CONNECTION_DESIGN, WATER_MODEL_CALIBRATION
from watering_backend.connections import global_assignments
from watering_backend.services.evaluation import (
    calculate_plant_results,
    current_tube_count,
)


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


def optimize_routing(
    plants: list[dict],
    outlets: list[dict],
    max_cycles: int = 96,
) -> dict:
    if not plants:
        return {
            "cycles": 0,
            "score": 0,
            "assignments": [],
            "by_outlet": [],
            "outlet_limits": {},
        }

    options = outlet_delivery_options(outlets)
    outlet_limits = {outlet["id"]: 12 for outlet in options}
    best: dict | None = None
    for cycles in range(1, max_cycles + 1):
        option_sets = [
            plant_tube_options(plant, options, cycles, max_total_tubes=6)
            for plant in plants
        ]
        candidate = choose_tube_assignments(
            plants,
            option_sets,
            outlet_limits,
            cycles,
        )
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
            "unassigned_plants": candidate.get("unassigned_plants", []),
            "optimization_method": candidate.get(
                "optimization_method",
                "bounded_dynamic_programming",
            ),
        }
        if best is None or candidate["score"] < best["score"]:
            best = candidate

    if best is None:
        best = fallback_single_tube_plan(plants, options, max_cycles)

    finalize_routing_plan(best, outlets, best["cycles"])
    best["summary"] = routing_summary(best)
    return best


def finalize_routing_plan(
    plan: dict,
    outlets: list[dict],
    cycles: int,
) -> dict:
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
                    "connections_limit": outlet_limits.get(
                        tube["outlet_id"],
                        12,
                    ),
                },
            )
            outlet["plants"].append(
                f"{assignment['plant_name']} ({tube['count']}x)"
            )
            outlet["connections_used"] += tube["count"]
            outlet["need_ml"] += assignment["need_ml"]
            outlet["delivered_ml"] += delivered_by_tube

    plan["cycles"] = cycles
    plan["by_outlet"] = sorted(
        grouped.values(),
        key=lambda item: item["ml_per_run"],
    )
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
    outlet_by_ml = {
        int(outlet["ml_per_run"]): outlet
        for outlet in outlets
    }
    options = []
    for count_15 in range(0, 5):
        for count_30 in range(0, 4):
            for count_60 in range(0, 3):
                if count_15 + count_30 + count_60 == 0:
                    continue
                if count_15 + count_30 + count_60 > max_total_tubes:
                    continue
                ml_per_cycle = (
                    count_15 * 15
                    + count_30 * 30
                    + count_60 * 60
                )
                if ml_per_cycle > max(
                    per_cycle_target * 2.4,
                    per_cycle_target + 90,
                ):
                    continue
                delivered = cycles * ml_per_cycle
                under = max(0, need - delivered)
                over = max(0, delivered - need)
                tube_count = count_15 + count_30 + count_60
                option_score = (
                    under * 7.5
                    + max(0, under - need * 0.08) * 14
                )
                option_score += (
                    over * 1.35
                    + max(0, over - tolerance) * 5.5
                )
                option_score += abs(ml_per_cycle - per_cycle_target) * 0.45
                option_score += max(0, tube_count - 2) * tube_penalty
                tubes = []
                connections = {}
                for ml_per_run, count in [
                    (15, count_15),
                    (30, count_30),
                    (60, count_60),
                ]:
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
    plants: list[dict],
    option_sets: list[list[dict]],
    outlet_limits: dict[int, int],
    cycles: int,
) -> dict | None:
    """Globally optimize all plants with a bounded DP over outlet occupancy."""
    return global_assignments(
        plants,
        option_sets,
        outlet_limits,
        cycles,
        normalize=normalize_assignment,
        overwater_tolerance=overwater_tolerance_ml,
    )


def normalize_assignment(assignment: dict) -> dict:
    primary_tube = max(
        assignment["tubes"],
        key=lambda tube: tube["ml_per_run"],
    )
    tube_label = " + ".join(
        f"{tube['count']}x {tube['ml_per_run']} ml"
        for tube in sorted(
            assignment["tubes"],
            key=lambda item: item["ml_per_run"],
        )
    )
    return {
        **assignment,
        "outlet_id": primary_tube["outlet_id"],
        "outlet_name": primary_tube["outlet_name"],
        "ml_per_run": assignment["ml_per_cycle"],
        "tube_label": tube_label,
    }


def fallback_single_tube_plan(
    plants: list[dict],
    outlets: list[dict],
    max_cycles: int,
) -> dict:
    option_sets = []
    for plant in plants:
        need = float(plant["need_ml"])
        options = []
        for outlet in outlets:
            delivered = max_cycles * int(outlet["ml_per_run"])
            under = max(0, need - delivered)
            over = max(0, delivered - need)
            options.append(
                {
                    "plant_id": plant["id"],
                    "plant_name": plant["name"],
                    "catalog_name": plant["catalog_name"],
                    "tubes": [
                        {
                            "outlet_id": outlet["id"],
                            "outlet_name": outlet["name"],
                            "ml_per_run": outlet["ml_per_run"],
                            "count": 1,
                        }
                    ],
                    "connections": {outlet["id"]: 1},
                    "ml_per_cycle": outlet["ml_per_run"],
                    "need_ml": round(need),
                    "delivered_ml": round(delivered),
                    "difference_ml": round(delivered - need),
                    "under_ml": round(under),
                    "over_ml": round(over),
                    "score": under * 12 + over * 1.8,
                }
            )
        option_sets.append(
            sorted(options, key=lambda item: item["score"])
        )
    candidate = global_assignments(
        plants,
        option_sets,
        {int(outlet["id"]): 12 for outlet in outlets},
        max_cycles,
        normalize=normalize_assignment,
        overwater_tolerance=overwater_tolerance_ml,
    )
    if candidate is not None:
        return {
            "cycles": max_cycles,
            **candidate,
            "fallback": True,
        }

    cycles = max_cycles
    assignments = []
    hard_under = 0
    severe_over = 0
    score = 0.0
    outlet_usage: dict[int, int] = {}
    sorted_outlets = sorted(
        outlets,
        key=lambda outlet: outlet["ml_per_run"],
        reverse=True,
    )
    unassigned_plants = []
    for plant in plants:
        chosen_outlet = next(
            (
                outlet
                for outlet in sorted_outlets
                if outlet_usage.get(outlet["id"], 0) < 12
            ),
            None,
        )
        if chosen_outlet is None:
            hard_under += 1
            score += 5000
            unassigned_plants.append(plant["name"])
            continue
        outlet_usage[chosen_outlet["id"]] = (
            outlet_usage.get(chosen_outlet["id"], 0) + 1
        )
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
        return (
            "Warnung: Für mindestens eine Pflanze ist kein freier "
            "Anschluss mehr verfügbar."
        )
    if plan["hard_underwatered"]:
        return (
            "Warnung: mindestens eine Pflanze bekäme trotz Vorschlag "
            "zu wenig Wasser."
        )
    if plan["severely_overwatered"]:
        return (
            "Warnung: mindestens eine Pflanze bekäme deutlich mehr "
            "Wasser als empfohlen."
        )
    return (
        "Ausgewogenste Kombination aus Zyklen und "
        "15/30/60-ml-Ausgängen."
    )


def optimize_static_connection_plan(
    balcony: dict,
    walls: list[dict],
    plants: list[dict],
    outlets: list[dict],
    *,
    target_date: date | None = None,
    calibration_factor: float = WATER_MODEL_CALIBRATION,
) -> dict:
    if not plants:
        return {
            "cycles": 0,
            "score": 0,
            "assignments": [],
            "by_outlet": [],
            "outlet_limits": {},
            "summary": "Noch keine Pflanzen angelegt.",
        }

    design = calculate_plant_results(
        balcony,
        walls,
        plants,
        temperature_c=CONNECTION_DESIGN["temperature_c"],
        rain_mm=CONNECTION_DESIGN["rain_mm"],
        wind_kmh=CONNECTION_DESIGN["wind_kmh"],
        sunshine_hours=CONNECTION_DESIGN["sunshine_hours"],
        slot="morning",
        target_date=target_date,
        calibration_factor=calibration_factor,
    )
    design_plants = design["plants"]
    options = outlet_delivery_options(outlets)
    outlet_limits = {outlet["id"]: 12 for outlet in options}
    option_sets = [
        plant_tube_options(
            plant,
            options,
            CONNECTION_DESIGN["cycles"],
            max_total_tubes=6,
            tube_penalty=80,
        )
        for plant in design_plants
    ]
    candidate = choose_tube_assignments(
        design_plants,
        option_sets,
        outlet_limits,
        CONNECTION_DESIGN["cycles"],
    )
    if candidate is None:
        candidate = fallback_single_tube_plan(
            design_plants,
            options,
            CONNECTION_DESIGN["cycles"],
        )
    else:
        candidate = {
            "cycles": CONNECTION_DESIGN["cycles"],
            "score": round(candidate["score"], 2),
            "assignments": candidate["assignments"],
            "hard_underwatered": candidate["hard_underwatered"],
            "severely_overwatered": candidate["severely_overwatered"],
            "unassigned_plants": candidate.get("unassigned_plants", []),
            "optimization_method": candidate.get(
                "optimization_method",
                "bounded_dynamic_programming",
            ),
        }

    finalize_routing_plan(
        candidate,
        outlets,
        CONNECTION_DESIGN["cycles"],
    )
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
        current_ml = (
            float(current_target)
            if current_target not in (None, "")
            else float(plant.get("current_ml_per_run", 0) or 0)
        )
        recommended_ml = float(assignment["ml_per_cycle"])
        tolerance = max(7, recommended_ml * 0.2)
        current_delivered = current_ml * int(
            plan.get("cycles", CONNECTION_DESIGN["cycles"])
        )
        need_ml = float(assignment["need_ml"])
        current_under = max(0, need_ml - current_delivered)
        current_over = max(0, current_delivered - need_ml)
        over_tolerance = overwater_tolerance_ml(plant)
        assignment["current_ml_per_cycle"] = round(current_ml)
        assignment["current_delivered_ml"] = round(current_delivered)
        assignment["current_hose_numbers"] = plant.get(
            "current_hose_numbers",
            "",
        )
        current_count = int(
            plant.get("current_tube_count", 0) or 0
        )
        recommended_count = sum(
            int(tube["count"])
            for tube in assignment["tubes"]
        )
        assignment["current_tube_count"] = current_count
        assignment["recommended_tube_count"] = recommended_count
        assignment["connection_action"] = (
            "add_hose"
            if recommended_count > current_count
            else "rewire"
            if abs(current_ml - recommended_ml) > tolerance
            else "none"
        )
        assignment["reason_codes"] = (
            [
                (
                    "under_supply"
                    if current_ml < recommended_ml
                    else "over_supply"
                ),
                (
                    "additional_hose_needed"
                    if recommended_count > current_count
                    else "different_hose_combination"
                ),
            ]
            if abs(current_ml - recommended_ml) > tolerance
            else ["within_tolerance"]
        )
        assignment["connection_status"] = "ok"
        assignment["connection_severity"] = "ok"
        assignment["connection_action_title"] = "Passt"
        assignment["connection_note"] = (
            "Aktueller Anschluss passt zum festen Plan."
        )
        connection_differs = abs(current_ml - recommended_ml) > tolerance
        if connection_differs:
            direction = "mehr" if current_ml < recommended_ml else "weniger"
            assignment["connection_status"] = "change"
            assignment["connection_severity"] = "change"
            assignment["connection_action_title"] = "Anschluss ändern"
            assignment["connection_note"] = (
                f"Besser {assignment['tube_label']} "
                f"({round(recommended_ml)} ml) statt aktuell "
                f"{round(current_ml)} ml: diese Pflanze braucht dauerhaft "
                f"{direction} Wasser pro gemeinsamem Zyklus."
            )
        if connection_differs and current_under > max(50, need_ml * 0.25):
            assignment["connection_status"] = "urgent"
            assignment["connection_severity"] = "urgent"
            assignment["connection_action_title"] = "Unerlässlich verlegen"
            assignment["connection_note"] = (
                "Unerlässlich: aktueller Anschluss liefert im "
                f"Auslegungsfall ca. {round(current_under)} ml zu wenig. "
                f"Auf {assignment['tube_label']} umstecken."
            )
        elif connection_differs and current_over > over_tolerance:
            assignment["connection_status"] = "urgent"
            assignment["connection_severity"] = "urgent"
            assignment["connection_action_title"] = "Unerlässlich reduzieren"
            assignment["connection_note"] = (
                "Unerlässlich: aktueller Anschluss liefert im "
                f"Auslegungsfall ca. {round(current_over)} ml zu viel. "
                f"Auf {assignment['tube_label']} reduzieren."
            )


def static_connection_summary(plan: dict) -> str:
    changes = sum(
        1
        for assignment in plan["assignments"]
        if assignment.get("connection_status") == "change"
    )
    urgent = sum(
        1
        for assignment in plan["assignments"]
        if assignment.get("connection_status") == "urgent"
    )
    if plan.get("unassigned_plants"):
        return (
            "Warnung: Für mindestens eine Pflanze ist kein freier "
            "Anschluss mehr verfügbar."
        )
    if urgent:
        return (
            f"Dringend: {urgent} Pflanze(n) müssen umgesteckt werden, "
            "sonst droht deutliche Fehlversorgung."
        )
    if changes:
        return (
            f"Fester Anschlussplan: {changes} Pflanze(n) sollten anders "
            "verschlaucht werden."
        )
    return (
        "Fester Anschlussplan passt. Wetter verändert nur die Anzahl "
        "gemeinsamer Pumpzyklen."
    )


def fixed_cycle_score(
    plant: dict,
    delivered_ml: float,
) -> tuple[float, bool, bool]:
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
        ml_per_cycle = sum(
            tube["ml_per_run"] * tube["count"]
            for tube in tubes
        )
        assignments.append(
            normalize_assignment(
                {
                    "plant_id": plant["id"],
                    "plant_name": plant["custom_name"],
                    "catalog_name": plant["catalog_name"],
                    "tubes": tubes,
                    "connections": {
                        tube["outlet_id"]: tube["count"]
                        for tube in tubes
                    },
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

    plant_by_id = {
        plant["id"]: plant
        for plant in plant_results
    }
    best = None
    for cycles in range(0, max_cycles + 1):
        score = cycles * 0.45
        hard_under = 0
        severe_over = 0
        for assignment in connection_plan["assignments"]:
            plant = plant_by_id[assignment["plant_id"]]
            delivered = cycles * assignment["ml_per_cycle"]
            plant_score, is_under, is_over = fixed_cycle_score(
                plant,
                delivered,
            )
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
    plan.update(
        best
        or {
            "cycles": 0,
            "score": 0,
            "hard_underwatered": 0,
            "severely_overwatered": 0,
        }
    )
    for assignment in plan["assignments"]:
        plant = plant_by_id[assignment["plant_id"]]
        delivered = plan["cycles"] * assignment["ml_per_cycle"]
        assignment["need_ml"] = round(float(plant["need_ml"]))
        assignment["delivered_ml"] = round(delivered)
        assignment["difference_ml"] = round(
            delivered - float(plant["need_ml"])
        )
        assignment["under_ml"] = round(
            max(0, float(plant["need_ml"]) - delivered)
        )
        assignment["over_ml"] = round(
            max(0, delivered - float(plant["need_ml"]))
        )
    finalize_routing_plan(plan, outlets, plan["cycles"])
    plan["summary"] = weather_fixed_plan_summary(plan)
    return plan


def weather_fixed_plan_summary(plan: dict) -> str:
    if plan.get("hard_underwatered"):
        return (
            "Mit dem festen Anschlussplan bleibt mindestens eine Pflanze "
            "bei dieser Zykluszahl zu trocken."
        )
    if plan.get("severely_overwatered"):
        return (
            "Mit dem festen Anschlussplan bekommt mindestens eine Pflanze "
            "bei dieser Zykluszahl deutlich zu viel Wasser."
        )
    return "Beste Zykluszahl für den festen Anschlussplan."
