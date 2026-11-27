#!/usr/bin/env python3
"""Black-box check for the dropout review endpoint on a running API.

Usage:
    python scripts/review_smoke.py [base_url]

Default base URL is http://127.0.0.1:8000, matching Docker Compose.  The
script posts a small configuration so a real effective_version exists, then
exercises the two records requested for the /docs trial:

  1. a single dropout that is explainable (co-channel + intermod candidates);
  2. a two-event session that has no offset assignment.

Only the Python standard library is required.
"""

from __future__ import annotations

import json
import sys
from urllib.error import HTTPError
from urllib.request import Request, urlopen

BASE_URL = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000").rstrip("/")


def request(method: str, path: str, payload=None):
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"} if data is not None else {}
    req = Request(BASE_URL + path, data=data, headers=headers, method=method)
    try:
        with urlopen(req, timeout=10) as response:
            body = response.read().decode("utf-8")
            return response.status, json.loads(body) if body else {}
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise AssertionError(f"{method} {path} -> {exc.code}: {body}") from exc


def main() -> int:
    status, health = request("GET", "/health")
    assert status == 200 and health["status"] == "ok"

    configuration = {
        "name": "review-smoke",
        "mics": [
            {"mic_id": "m1", "main_frequency": 480, "backup_candidates": [490]},
            {"mic_id": "m2", "main_frequency": 520, "backup_candidates": [528]},
            {"mic_id": "m3", "main_frequency": 561, "backup_candidates": [570]},
        ],
    }
    status, configured = request("POST", "/api/configuration", configuration)
    assert status == 200, configured
    version = configured["effective_version"]

    single = {
        "effective_version": version,
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
    status, explained = request("POST", "/api/review", single)
    assert status == 200, explained
    assert explained["feasible"] is True, explained
    row = explained["events"][0]
    assert row["cochannel_mic_ids"] == ["m2"], row["cochannel_mic_ids"]
    assert row["intermod_mic_pairs"] == [["m3", "m2"]], row["intermod_mic_pairs"]
    assert set(explained["mics_to_check"]) == {"m2", "m3"}
    assert explained["clock_offsets"]["m2"] == 0

    impossible = {
        "effective_version": version,
        "target_mic_id": "m1",
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
    status, no_solution = request("POST", "/api/review", impossible)
    assert status == 200, no_solution
    assert no_solution["feasible"] is False
    assert no_solution["no_solution"] is not None

    # Structural limit must be a clean 422.
    bad = dict(impossible)
    bad["mics"] = [
        {"mic_id": f"x{i}", "clock_offset_range": [0, 1], "segments": []}
        for i in range(3)
    ]
    data = json.dumps(bad).encode("utf-8")
    req = Request(
        BASE_URL + "/api/review",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        urlopen(req, timeout=10)
    except HTTPError as exc:
        assert exc.code == 422, exc.code
    else:
        raise AssertionError("three offset ranges must be rejected with 422")

    print("REVIEW SMOKE PASSED")
    print(f"base_url={BASE_URL}")
    print(f"effective_version={version}")
    print("single dropout: cochannel=m2 intermod=(m3 a, m2 b)")
    print("two-event session: no_solution as expected")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
