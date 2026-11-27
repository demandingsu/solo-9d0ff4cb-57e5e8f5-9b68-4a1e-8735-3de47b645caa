"""Frequency compatibility rules.

The rules are intentionally independent from FastAPI and SQLite so they can be
unit-tested and reused by scripts.
"""

from __future__ import annotations

from itertools import product
from typing import Any, Iterable, Mapping, Sequence

# Re-exported so callers can reach the review solver from either
# ``app.review`` or the established rules module ``app.solver``.
from .review import (  # noqa: F401
    analyze_dropouts,
    review_incident,
    solve_review,
)

Mic = Mapping[str, Any]
Interference = Mapping[str, Any]


def find_frequency_violations(
    mic_ids: Sequence[str],
    frequencies: Sequence[int],
    occupied_frequencies: Iterable[int] | None = None,
) -> list[dict[str, Any]]:
    """Return all relevant duplicate, occupied, and reflection violations.

    For distinct microphones i, j and k, the forbidden relation is
    ``2*f_i - f_j == f_k``.  Both orders of the selected pair are checked
    because "任取 a、b" does not assign a permanent role to either frequency.
    """

    violations: list[dict[str, Any]] = []
    occupied = set(occupied_frequencies or [])

    seen: dict[int, str] = {}
    for mic_id, freq in zip(mic_ids, frequencies):
        if freq in occupied:
            violations.append(
                {
                    "code": "occupied_frequency",
                    "mic_id": mic_id,
                    "frequency": freq,
                }
            )

        previous = seen.get(freq)
        if previous is not None:
            violations.append(
                {
                    "code": "duplicate_frequency",
                    "frequency": freq,
                    "mic_ids": [previous, mic_id],
                }
            )
        else:
            seen[freq] = mic_id

    count = len(frequencies)
    for i in range(count):
        for j in range(count):
            if i == j:
                continue
            reflected = 2 * frequencies[i] - frequencies[j]
            for k in range(count):
                if k == i or k == j:
                    continue
                if frequencies[k] == reflected:
                    violations.append(
                        {
                            "code": "reflection_hits_third_frequency",
                            "mic_ids": [mic_ids[i], mic_ids[j], mic_ids[k]],
                            "a": frequencies[i],
                            "b": frequencies[j],
                            "third_frequency": frequencies[k],
                            "forbidden_value": reflected,
                            "equation": "2a-b=third",
                        }
                    )
                    break

    return violations


def _unique_int_values(values: Iterable[int]) -> list[int]:
    result: list[int] = []
    seen: set[int] = set()
    for value in values:
        value = int(value)
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _active_plan_for_interference(
    mics: Sequence[Mic],
    selected_backups: Mapping[str, int],
    interference: Interference,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    occupied = _unique_int_values(interference.get("occupied_frequencies", []))
    occupied_set = set(occupied)

    active_frequencies: list[dict[str, Any]] = []
    switches: list[dict[str, Any]] = []
    mic_ids: list[str] = []
    freq_values: list[int] = []

    for mic in mics:
        mic_id = str(mic["mic_id"])
        main_frequency = int(mic["main_frequency"])
        is_occupied = main_frequency in occupied_set

        if is_occupied:
            frequency = int(selected_backups[mic_id])
            switches.append(
                {
                    "mic_id": mic_id,
                    "mic_name": mic.get("name"),
                    "from_frequency": main_frequency,
                    "to_frequency": frequency,
                    "reason": "main_frequency_occupied",
                }
            )
        else:
            frequency = main_frequency

        mic_ids.append(mic_id)
        freq_values.append(frequency)
        active_frequencies.append(
            {
                "mic_id": mic_id,
                "mic_name": mic.get("name"),
                "frequency": frequency,
                "source": "backup" if is_occupied else "main",
            }
        )

    violations = find_frequency_violations(mic_ids, freq_values, occupied_set)
    return switches, active_frequencies, violations


def solve_configuration(configuration: Mapping[str, Any]) -> dict[str, Any]:
    """Choose one installed/pre-stored backup for every microphone.

    The choice is global because a physical microphone stores only one backup.
    A choice is feasible when the normal state and every listed interference
    state obey the frequency rules.
    """

    mics = list(configuration.get("mics", []))
    interferences = list(configuration.get("interferences", []))

    mic_ids = [str(mic["mic_id"]) for mic in mics]
    main_frequencies = [int(mic["main_frequency"]) for mic in mics]
    baseline_violations = find_frequency_violations(mic_ids, main_frequencies)

    if baseline_violations:
        return {
            "status": "infeasible",
            "feasible": False,
            "selected_backups": None,
            "switch_table": None,
            "no_solution": {
                "message": "平时主频已存在重频或 2a-b 落在第三支麦频点上，无法靠备频消除。",
                "stage": "normal",
                "violations": baseline_violations,
            },
            "message": "当前设置无解：平时主频不满足频点规则。",
        }

    choice_lists: list[list[int]] = []
    choice_sources: list[dict[int, str]] = []
    for mic in mics:
        installed = mic.get("installed_backup")
        if installed is not None:
            choices = [int(installed)]
            choice_lists.append(choices)
            choice_sources.append({choices[0]: "registered"})
        else:
            choices = _unique_int_values(mic.get("backup_candidates", []))
            choice_lists.append(choices)
            choice_sources.append({value: "candidate" for value in choices})

    attempted = 0
    last_failure: dict[str, Any] | None = None

    for combination in product(*choice_lists):
        attempted += 1
        selected_backups = {
            mic_id: int(freq) for mic_id, freq in zip(mic_ids, combination)
        }

        table: list[dict[str, Any]] = []
        first_violations: list[dict[str, Any]] = []
        failed_interference: str | None = None

        for interference in interferences:
            switches, active, violations = _active_plan_for_interference(
                mics, selected_backups, interference
            )
            interference_id = str(interference["interference_id"])
            row = {
                "interference_id": interference_id,
                "name": interference.get("name"),
                "occupied_frequencies": _unique_int_values(
                    interference.get("occupied_frequencies", [])
                ),
                "switches": switches,
                "active_frequencies": active,
                "valid": not violations,
                "violations": violations,
            }
            table.append(row)
            if violations and not first_violations:
                first_violations = violations
                failed_interference = interference_id

        if not first_violations:
            selected_details = []
            for index, mic in enumerate(mics):
                mic_id = str(mic["mic_id"])
                value = selected_backups[mic_id]
                installed = mic.get("installed_backup")
                if installed is not None and value == int(installed):
                    source = "registered"
                else:
                    source = "candidate"
                candidates = _unique_int_values(mic.get("backup_candidates", []))
                selected_details.append(
                    {
                        "mic_id": mic_id,
                        "mic_name": mic.get("name"),
                        "backup_frequency": value,
                        "source": source,
                        "candidate_index": (
                            candidates.index(value) if value in candidates else None
                        ),
                        "in_candidates": value in candidates,
                    }
                )

            # Violation arrays are only for failed rows; remove from successful
            # response rows to keep the table compact.
            for row in table:
                row.pop("violations", None)

            return {
                "status": "feasible",
                "feasible": True,
                "selected_backups": selected_details,
                "switch_table": table,
                "no_solution": None,
                "solver": {
                    "algorithm": "lexicographic_constraint_enumeration",
                    "combinations_checked": attempted,
                    "combinations_total": attempted,
                },
                "message": "已找到满足平时和全部干扰情形的备频方案。",
            }

        last_failure = {
            "interference_id": failed_interference,
            "selected_backups": dict(selected_backups),
            "violations": first_violations,
        }

    registered_count = sum(1 for mic in mics if mic.get("installed_backup") is not None)
    return {
        "status": "infeasible",
        "feasible": False,
        "selected_backups": None,
        "switch_table": None,
        "no_solution": {
            "message": (
                "所有备频组合均无法同时避开占用频点、重频和 2a-b 第三频点约束。"
            ),
            "stage": "interference",
            "combinations_attempted": attempted,
            "registered_microphone_count": registered_count,
            "last_failure": last_failure,
        },
        "message": "当前设置无解：没有可同时应对全部干扰情形的备频组合。",
    }
