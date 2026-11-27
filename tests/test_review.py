"""Tests for the rehearsal dropout review endpoint and solver."""

from __future__ import annotations

import itertools
import json
import random
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.review import ReviewInputError, review_incident

REPO_ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = REPO_ROOT / "examples"


# ---------------------------------------------------------------------------
# Pure solver: hand-built scenarios
# ---------------------------------------------------------------------------


def test_fixed_cochannel_explains_dropout():
    result = review_incident(
        {
            "target_mic_id": "t",
            "target_frequency": 480,
            "events": [{"time": 1000, "status": "dropout"}],
            "mics": [
                {
                    "mic_id": "m2",
                    "segments": [{"start": 900, "end": 1100, "frequency": 480}],
                }
            ],
        }
    )
    assert result["feasible"] is True
    row = result["events"][0]
    assert row["cochannel_mic_ids"] == ["m2"]
    assert row["intermod_mic_pairs"] == []
    assert row["at_selected_offsets"]["conflict_detected"] is True


def test_intermod_pair_2a_minus_b_hits_target():
    # 2 * 500 - 520 = 480
    result = review_incident(
        {
            "target_frequency": 480,
            "events": [{"time": 0, "status": "dropout"}],
            "mics": [
                {"mic_id": "a", "segments": [{"start": 0, "end": 1, "frequency": 500}]},
                {"mic_id": "b", "segments": [{"start": 0, "end": 1, "frequency": 520}]},
            ],
        }
    )
    assert result["feasible"] is True
    pairs = result["events"][0]["intermod_pairs"]
    assert [(p["a_mic_id"], p["b_mic_id"]) for p in pairs] == [("a", "b")]
    assert pairs[0]["a_frequency"] == 500
    assert pairs[0]["b_frequency"] == 520
    assert pairs[0]["forbidden_value"] == 480
    assert result["events"][0]["intermod_mic_pairs"] == [["a", "b"]]


def test_half_open_interval_end_is_excluded():
    base = {
        "target_frequency": 5,
        "mics": [
            {"mic_id": "m", "segments": [{"start": 100, "end": 200, "frequency": 5}]}
        ],
    }
    inside = review_incident(
        {**base, "events": [{"time": 199, "status": "dropout"}]}
    )
    assert inside["feasible"] is True

    at_end = review_incident(
        {**base, "events": [{"time": 200, "status": "dropout"}]}
    )
    assert at_end["feasible"] is False

    at_start = review_incident(
        {**base, "events": [{"time": 100, "status": "dropout"}]}
    )
    assert at_start["feasible"] is True


def test_offset_is_added_to_log_time():
    # Segment [105,106) only lines up with log t=100 when offset is +5.
    result = review_incident(
        {
            "target_frequency": 9,
            "events": [{"time": 100, "status": "dropout"}],
            "mics": [
                {
                    "mic_id": "m",
                    "clock_offset_range": [0, 10],
                    "segments": [{"start": 105, "end": 106, "frequency": 9}],
                }
            ],
        }
    )
    assert result["feasible"] is True
    assert result["clock_offsets"]["m"] == 5


def test_normal_event_forbids_conflict():
    result = review_incident(
        {
            "target_frequency": 480,
            "events": [{"time": 1000, "status": "normal"}],
            "mics": [
                {"mic_id": "m2", "segments": [{"start": 1000, "end": 1001, "frequency": 480}]}
            ],
        }
    )
    assert result["feasible"] is False
    assert result["status"] == "infeasible"
    assert result["no_solution"] is not None


def test_single_event_lists_cochannel_and_intermod_across_feasible_offsets():
    result = review_incident(
        {
            "target_mic_id": "m1",
            "target_frequency": 480,
            "events": [{"time": 1000, "status": "dropout"}],
            "mics": [
                {
                    "mic_id": "m2",
                    "clock_offset_range": [0, 50],
                    "segments": [
                        {"start": 1000, "end": 1001, "frequency": 480},
                        {"start": 1010, "end": 1021, "frequency": 520},
                    ],
                },
                {
                    "mic_id": "m3",
                    "segments": [{"start": 1000, "end": 1001, "frequency": 500}],
                },
            ],
        }
    )
    assert result["feasible"] is True
    # Smallest feasible witness is the co-channel offset 0.
    assert result["clock_offsets"] == {"m2": 0, "m3": 0}

    row = result["events"][0]
    assert row["cochannel_mic_ids"] == ["m2"]
    # Offset 10..20 is another feasible timing and implicates the pair.
    assert row["intermod_mic_pairs"] == [["m3", "m2"]]


def test_candidate_only_conflicting_under_globally_infeasible_offset_is_hidden():
    # See module docstring-style construction: offset 0 globally works via
    # co-channel; offset 5 creates an intermod at the dropout but also violates
    # the normal event, so that pair must NOT appear in global candidates.
    payload = {
        "target_frequency": 480,
        "events": [
            {"time": 100, "status": "dropout"},
            {"time": 200, "status": "normal"},
        ],
        "mics": [
            {
                "mic_id": "m1",
                "clock_offset_range": [0, 10],
                "segments": [
                    {"start": 100, "end": 101, "frequency": 480},
                    {"start": 105, "end": 106, "frequency": 520},
                    {"start": 205, "end": 206, "frequency": 520},
                ],
            },
            {
                "mic_id": "m2",
                "segments": [
                    {"start": 100, "end": 101, "frequency": 500},
                    {"start": 200, "end": 201, "frequency": 500},
                ],
            },
        ],
    }
    result = review_incident(payload)
    assert result["feasible"] is True
    assert result["clock_offsets"]["m1"] == 0

    dropout_row = result["events"][0]
    assert dropout_row["cochannel_mic_ids"] == ["m1"]
    assert dropout_row["intermod_mic_pairs"] == []

    # The decoy pair still exists somewhere inside the raw offset ranges.
    domain_pairs = {
        tuple(p["mic_ids"])
        for p in dropout_row["domain_possible_causes"]["intermod_pairs"]
    }
    assert ("m2", "m1") in domain_pairs


def test_two_variable_mics_require_coordinated_offsets():
    result = review_incident(
        {
            "target_frequency": 100,
            "events": [{"time": 0, "status": "dropout"}],
            "mics": [
                {
                    "mic_id": "a",
                    "clock_offset_range": [-10, 10],
                    "segments": [{"start": 3, "end": 4, "frequency": 60}],
                },
                {
                    "mic_id": "b",
                    "clock_offset_range": [-10, 10],
                    "segments": [{"start": 7, "end": 8, "frequency": 20}],
                },
            ],
        }
    )
    assert result["feasible"] is True
    assert result["clock_offsets"]["a"] == 3
    assert result["clock_offsets"]["b"] == 7
    assert result["events"][0]["intermod_mic_pairs"] == [["a", "b"]]


def test_whole_session_contradiction_is_no_solution():
    result = review_incident(
        {
            "target_frequency": 480,
            "events": [
                {"time": 100, "status": "dropout"},
                {"time": 200, "status": "normal"},
            ],
            "mics": [
                {
                    "mic_id": "m2",
                    "clock_offset_range": [0, 50],
                    "segments": [
                        {"start": 100, "end": 101, "frequency": 480},
                        {"start": 200, "end": 201, "frequency": 480},
                    ],
                }
            ],
        }
    )
    assert result["feasible"] is False
    assert result["has_solution"] is False
    assert result["solution"] is None
    assert "不存在" in result["no_solution"]["message"]


def test_computation_does_not_grow_with_millisecond_span():
    # The fixed mic makes the normal event unavoidable, so the session stays
    # infeasible regardless of how wide the variable mic's range becomes.
    payload = {
        "target_frequency": 480,
        "events": [
            {"time": 100, "status": "dropout"},
            {"time": 500, "status": "normal"},
        ],
        "mics": [
            {
                "mic_id": "fixed-bad",
                "segments": [
                    {"start": 500, "end": 501, "frequency": 480},
                ],
            },
            {
                "mic_id": "wide",
                "clock_offset_range": [-10**12, 10**12],
                "segments": [
                    {"start": 100, "end": 101, "frequency": 480},
                    {"start": 200, "end": 201, "frequency": 520},
                    {"start": 900, "end": 901, "frequency": 500},
                ],
            },
        ],
    }
    start = time.perf_counter()
    result = review_incident(payload)
    elapsed = time.perf_counter() - start

    assert result["feasible"] is False
    solver = result["solver"]
    assert solver["iterates_over_millisecond_span"] is False
    # Only a handful of endpoint cells despite a 2e12+1 wide range.
    assert solver["assignments_checked"] <= 16
    assert elapsed < 2.0


def test_rejects_overlapping_segments_and_bad_ranges():
    with pytest.raises(ReviewInputError):
        review_incident(
            {
                "target_frequency": 1,
                "events": [],
                "mics": [
                    {
                        "mic_id": "m",
                        "segments": [
                            {"start": 0, "end": 10, "frequency": 1},
                            {"start": 9, "end": 20, "frequency": 1},
                        ],
                    }
                ],
            }
        )

    with pytest.raises(ReviewInputError):
        review_incident(
            {
                "target_frequency": 1,
                "events": [],
                "mics": [
                    {"mic_id": "m", "clock_offset_range": [5, 1]},
                ],
            }
        )

    with pytest.raises(ReviewInputError):
        review_incident(
            {
                "target_frequency": 1,
                "events": [],
                "mics": [{"mic_id": "m", "segments": [{"start": 2, "end": 2}]}],
            }
        )


def test_rejects_more_than_two_offset_mics_in_core():
    with pytest.raises(ReviewInputError):
        review_incident(
            {
                "target_frequency": 1,
                "events": [],
                "mics": [
                    {"mic_id": f"m{i}", "clock_offset_range": [0, 1]}
                    for i in range(3)
                ],
            }
        )


# ---------------------------------------------------------------------------
# Differential check against exhaustive offset enumeration
# ---------------------------------------------------------------------------


def _active_frequency(mic, event_time, offset):
    shifted = event_time + offset
    for segment in mic["segments"]:
        if segment["start"] <= shifted < segment["end"]:
            return segment["frequency"]
    return None


def _brute_force(payload):
    target = payload["target_frequency"]
    events = payload["events"]
    mics = payload["mics"]

    def domain(mic):
        spec = mic.get("clock_offset_range")
        if spec is None:
            return [0]
        if isinstance(spec, int):
            return [spec]
        return range(spec[0], spec[1] + 1)

    domains = [domain(mic) for mic in mics]
    candidates = [{"co": set(), "pair": set()} for _ in events]
    any_feasible = False

    for combo in itertools.product(*domains):
        offsets = {mic["mic_id"]: value for mic, value in zip(mics, combo)}

        assignment_ok = True
        event_causes = []
        for event in events:
            active = {}
            for mic in mics:
                freq = _active_frequency(mic, event["time"], offsets[mic["mic_id"]])
                if freq is not None:
                    active[mic["mic_id"]] = freq
            co = {mid for mid, freq in active.items() if freq == target}
            pairs = {
                (i, j)
                for i in active
                for j in active
                if i != j and 2 * active[i] - active[j] == target
            }
            conflict = bool(co or pairs)
            ok = conflict if event["status"] == "dropout" else not conflict
            assignment_ok = assignment_ok and ok
            event_causes.append((co, pairs))

        if assignment_ok:
            any_feasible = True
            for e, (co, pairs) in enumerate(event_causes):
                candidates[e]["co"] |= co
                candidates[e]["pair"] |= pairs

    return any_feasible, candidates


def test_differential_against_exhaustive_enumeration():
    rng = random.Random(20261127)
    freq_choices = [1, 2, 3, 5, 7, 10, 20, 30]

    for trial in range(600):
        target = rng.choice(freq_choices)
        mics = []
        variable_count = 0
        for mi in range(rng.randint(1, 4)):
            segments = []
            cursor = rng.randint(-6, 6)
            for _ in range(rng.randint(1, 4)):
                length = rng.randint(1, 3)
                segments.append(
                    {
                        "start": cursor,
                        "end": cursor + length,
                        "frequency": rng.choice(freq_choices),
                    }
                )
                cursor += length + rng.randint(0, 3)
            mic = {"mic_id": f"m{mi}", "segments": segments}
            choice = rng.random()
            if variable_count < 2 and choice < 0.45:
                lo = rng.randint(-3, 1)
                mic["clock_offset_range"] = [lo, lo + rng.randint(0, 4)]
                variable_count += 1
            elif choice < 0.7:
                mic["clock_offset_range"] = rng.randint(-3, 3)
            mics.append(mic)

        events = [
            {
                "time": rng.randint(-6, 8),
                "status": rng.choice(["dropout", "normal"]),
            }
            for _ in range(rng.randint(1, 6))
        ]
        payload = {
            "target_mic_id": "target",
            "target_frequency": target,
            "events": events,
            "mics": mics,
        }

        result = review_incident(payload)
        expected_feasible, expected_candidates = _brute_force(payload)

        assert result["feasible"] is expected_feasible, (trial, payload)

        if expected_feasible:
            # The returned witness must really explain the whole session.
            offsets = result["clock_offsets"]
            for event in events:
                active = {}
                for mic in mics:
                    freq = _active_frequency(
                        mic, event["time"], offsets[mic["mic_id"]]
                    )
                    if freq is not None:
                        active[mic["mic_id"]] = freq
                co = {m for m, f in active.items() if f == target}
                pairs = {
                    (i, j)
                    for i in active
                    for j in active
                    if i != j and 2 * active[i] - active[j] == target
                }
                conflict = bool(co or pairs)
                if event["status"] == "dropout":
                    assert conflict, ("bad witness dropout", trial)
                else:
                    assert not conflict, ("bad witness normal", trial)

            for e in range(len(events)):
                got_co = set(result["events"][e]["cochannel_mic_ids"])
                got_pairs = {
                    tuple(pair)
                    for pair in result["events"][e]["intermod_mic_pairs"]
                }
                assert got_co == expected_candidates[e]["co"], (trial, e)
                assert got_pairs == expected_candidates[e]["pair"], (trial, e)


# ---------------------------------------------------------------------------
# HTTP API
# ---------------------------------------------------------------------------


@pytest.fixture
def client(tmp_path):
    app = create_app(str(tmp_path / "review-test.db"))
    with TestClient(app) as test_client:
        yield test_client


def _configure_example(client):
    configuration = json.loads((EXAMPLES / "configuration.json").read_text())
    response = client.post("/api/configuration", json=configuration)
    assert response.status_code == 200
    return response.json()["effective_version"]


def test_review_endpoint_self_contained_without_version(client):
    response = client.post(
        "/api/review",
        json={
            "target_mic_id": "x",
            "target_frequency": 10,
            "events": [{"time": 0, "status": "dropout"}],
            "mics": [
                {"mic_id": "a", "segments": [{"start": 0, "end": 1, "frequency": 10}]}
            ],
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["feasible"] is True
    assert body["effective_version"] is None
    assert body["version_source"] == "none"


def test_review_uses_selected_version_for_names_and_default_frequency(client):
    version = _configure_example(client)
    response = client.post(
        "/api/review",
        json={
            "effective_version": version,
            "target_mic_id": "m1",
            # target_frequency intentionally omitted -> plan main 480
            "events": [{"time": 480, "status": "normal"}],
            "mics": [],
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["target_frequency"] == 480
    assert body["target_mic"]["name"] == "主持"
    assert body["feasible"] is True


def test_example_files_match_expected_outcomes(client):
    _configure_example(client)

    single = json.loads((EXAMPLES / "review_single_explainable.json").read_text())
    response = client.post("/api/review", json=single)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["feasible"] is True
    row = body["events"][0]
    assert row["cochannel_mic_ids"] == ["m2"]
    assert row["intermod_mic_pairs"] == [["m3", "m2"]]

    no_solution = json.loads((EXAMPLES / "review_no_solution.json").read_text())
    response = client.post("/api/review", json=no_solution)
    assert response.status_code == 200
    body = response.json()
    assert body["feasible"] is False
    assert body["no_solution"]["code"] == "no_offset_assignment_explains_session"

    full = json.loads((EXAMPLES / "review_full_session.json").read_text())
    response = client.post("/api/review", json=full)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["feasible"] is True
    assert [e["status"] for e in body["events"]] == [
        "normal",
        "dropout",
        "normal",
        "dropout",
    ]
    assert body["events"][1]["cochannel_mic_ids"] == ["m2"]
    assert body["events"][3]["intermod_mic_pairs"] == [["m2", "m3"]]


def test_review_alias_and_version_scoped_routes(client):
    version = _configure_example(client)
    payload = json.loads((EXAMPLES / "review_single_explainable.json").read_text())

    alias = client.post("/api/dropout-review", json=payload)
    assert alias.status_code == 200
    assert alias.json()["feasible"] is True

    scoped = client.post(f"/api/versions/{version}/review", json=payload)
    assert scoped.status_code == 200
    assert scoped.json()["effective_version"] == version

    mic_scoped = client.post("/api/mics/m1/review?version=1", json=payload)
    assert mic_scoped.status_code == 200
    assert mic_scoped.json()["target_mic_id"] == "m1"

    deep = client.post("/api/versions/1/mics/m1/review", json=payload)
    assert deep.status_code == 200
    assert deep.json()["effective_version"] == 1

    assert client.post("/api/dropouts", json=payload).status_code == 200
    assert client.post("/api/forensics", json=payload).status_code == 200


def test_unknown_explicit_version_is_404(client):
    response = client.post(
        "/api/review",
        json={"effective_version": 4242, "target_mic_id": "m1", "events": [], "mics": []},
    )
    assert response.status_code == 404
    assert response.json()["error"] == "version_not_found"


def test_unknown_target_in_explicit_version_is_404(client):
    _configure_example(client)
    response = client.post(
        "/api/review",
        json={
            "effective_version": 1,
            "target_mic_id": "nope",
            "target_frequency": 1,
            "events": [],
            "mics": [],
        },
    )
    assert response.status_code == 404
    assert response.json()["error"] == "target_mic_not_found"


def test_schema_limits_return_422(client):
    too_many_events = {
        "target_frequency": 1,
        "events": [{"time": i, "status": "normal"} for i in range(9)],
        "mics": [],
    }
    assert client.post("/api/review", json=too_many_events).status_code == 422

    too_many_segments = {
        "target_frequency": 1,
        "events": [],
        "mics": [
            {
                "mic_id": "m",
                "segments": [
                    {"start": i * 10, "end": i * 10 + 1, "frequency": 1}
                    for i in range(9)
                ],
            }
        ],
    }
    assert client.post("/api/review", json=too_many_segments).status_code == 422

    too_many_ranges = {
        "target_mic_id": "t",
        "target_frequency": 1,
        "events": [],
        "mics": [
            {"mic_id": f"m{i}", "clock_offset_range": [0, 1], "segments": []}
            for i in range(3)
        ],
    }
    cap_response = client.post("/api/review", json=too_many_ranges)
    assert cap_response.status_code == 422
    assert cap_response.json()["error"] == "invalid_review_input"

    # target_mic_id is optional when the fixed frequency is supplied directly.
    no_target_id = {
        "target_frequency": 9,
        "events": [{"time": 0, "status": "normal"}],
        "mics": [],
    }
    assert client.post("/api/review", json=no_target_id).status_code == 200

    overlap = {
        "target_frequency": 1,
        "events": [],
        "mics": [
            {
                "mic_id": "m",
                "segments": [
                    {"start": 0, "end": 10, "frequency": 1},
                    {"start": 5, "end": 15, "frequency": 1},
                ],
            }
        ],
    }
    assert client.post("/api/review", json=overlap).status_code == 422


def test_scalar_offsets_are_fixed_and_do_not_consume_range_cap():
    # Three scalar offsets + one real range must be accepted; scalars shift
    # log time by a known constant.
    result = review_incident(
        {
            "target_frequency": 11,
            "events": [{"time": 100, "status": "dropout"}],
            "mics": [
                {"mic_id": "scalar-1", "clock_offset_range": 4, "segments": []},
                {"mic_id": "scalar-2", "clock_offset_range": -2, "segments": []},
                {"mic_id": "scalar-3", "clock_offset_range": 0, "segments": []},
                {
                    "mic_id": "ranged",
                    "clock_offset_range": [0, 10],
                    "segments": [
                        {"start": 103, "end": 104, "frequency": 11}
                    ],
                },
            ],
        }
    )
    assert result["feasible"] is True
    offsets = result["clock_offsets"]
    assert offsets["scalar-1"] == 4
    assert offsets["scalar-2"] == -2
    assert offsets["ranged"] == 3
    sources = {item["mic_id"]: item["source"] for item in result["offset_assignment"]}
    assert sources["scalar-1"] == "fixed_offset"
    assert sources["scalar-3"] == "fixed_offset"
    assert sources["ranged"] == "range_solver"


def test_mics_to_check_summary_lists_every_candidate_once():
    result = review_incident(
        {
            "target_frequency": 480,
            "events": [{"time": 0, "status": "dropout"}],
            "mics": [
                {"mic_id": "m2", "segments": [{"start": 0, "end": 1, "frequency": 480}]},
                {"mic_id": "m3", "segments": [{"start": 0, "end": 1, "frequency": 500}]},
                {"mic_id": "m4", "segments": [{"start": 0, "end": 1, "frequency": 520}]},
            ],
        }
    )
    assert result["feasible"] is True
    # m2 co-channel and (m3,m4) intermod both implicated in one event.
    assert set(result["mics_to_check"]) == {"m2", "m3", "m4"}
    by_id = {s["mic_id"]: s for s in result["suspects"]}
    assert "cochannel" in by_id["m2"]["reasons"]
    assert "intermodulation_2a_minus_b" in by_id["m3"]["reasons"]


def test_nested_target_object_is_supported(client):
    response = client.post(
        "/api/review",
        json={
            "target": {"mic_id": "m1", "fixed_frequency": 42},
            "events": [{"time": 0, "status": "normal"}],
            "mics": [],
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["target_mic_id"] == "m1"
    assert body["target_frequency"] == 42


def test_openapi_lists_review_endpoint_and_examples(client):
    spec = client.get("/openapi.json").json()
    assert "/api/review" in spec["paths"]
    request_body = spec["paths"]["/api/review"]["post"]["requestBody"]
    examples = request_body["content"]["application/json"]["examples"]
    assert "single_event_explainable" in examples
    assert "whole_session_no_solution" in examples
    assert client.get("/docs").status_code == 200
