from __future__ import annotations

from typing import Callable


def global_assignments(
    plants: list[dict],
    option_sets: list[list[dict]],
    outlet_limits: dict[int, int],
    cycles: int,
    *,
    normalize: Callable[[dict], dict],
    overwater_tolerance: Callable[[dict], float],
    max_states: int = 6000,
) -> dict | None:
    """Bounded dynamic programming across every plant and outlet capacity."""
    ordered = sorted(
        zip(plants, option_sets),
        key=lambda item: (len(item[1]), -float(item[0]["need_ml"])),
    )
    outlet_ids = tuple(sorted(outlet_limits))
    states: dict[tuple[int, ...], tuple] = {
        tuple(0 for _ in outlet_ids): (cycles * 0.02, cycles * 0.02, 0, 0, [], [])
    }
    for plant, options in ordered:
        next_states: dict[tuple[int, ...], tuple] = {}
        need = float(plant["need_ml"])
        for usage, state in states.items():
            objective, raw_score, hard_under, severe_over, assignments, unassigned = state
            for option in options:
                candidate_usage = tuple(
                    usage[index] + int(option["connections"].get(outlet_id, 0))
                    for index, outlet_id in enumerate(outlet_ids)
                )
                if any(
                    candidate_usage[index] > int(outlet_limits[outlet_id])
                    for index, outlet_id in enumerate(outlet_ids)
                ):
                    continue
                option_hard = int(option["under_ml"] > max(25, round(need * 0.12)))
                option_severe = int(option["over_ml"] > round(overwater_tolerance(plant)))
                new_raw = raw_score + float(option["score"])
                new_hard = hard_under + option_hard
                new_severe = severe_over + option_severe
                new_objective = new_raw + new_hard * 2500 + new_severe * 900
                candidate = (
                    new_objective,
                    new_raw,
                    new_hard,
                    new_severe,
                    assignments + [normalize(option)],
                    list(unassigned),
                )
                previous = next_states.get(candidate_usage)
                if previous is None or candidate[0] < previous[0]:
                    next_states[candidate_usage] = candidate

            skip_score = need * 30 + 10_000
            skipped = (
                objective + skip_score + 2500,
                raw_score + skip_score,
                hard_under + 1,
                severe_over,
                list(assignments),
                unassigned + [plant["name"]],
            )
            previous = next_states.get(usage)
            if previous is None or skipped[0] < previous[0]:
                next_states[usage] = skipped

        if not next_states:
            return None
        if len(next_states) > max_states:
            next_states = dict(sorted(next_states.items(), key=lambda item: item[1][0])[:max_states])
        states = next_states

    best = min(states.values(), key=lambda item: item[0])
    return {
        "assignments": best[4],
        "score": best[1],
        "hard_underwatered": best[2],
        "severely_overwatered": best[3],
        "unassigned_plants": best[5],
        "optimization_method": "bounded_dynamic_programming",
    }
