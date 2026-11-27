#!/usr/bin/env python3
"""Black-box smoke test for a running API, using only the Python stdlib.

Usage:
    python scripts/smoke_test.py [base_url]

Default base URL is http://127.0.0.1:8000, matching Docker Compose.
The script intentionally exercises:
  * several interference scenarios sharing one selected backup;
  * simultaneous backup registration and interference-list modification;
  * the current table's one-version snapshot.
"""

from __future__ import annotations

import json
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
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

    setup = {
        "name": "smoke-test",
        "mics": [
            {
                "mic_id": "m1",
                "main_frequency": 10,
                "backup_candidates": [20, 30],
            },
            {
                "mic_id": "m2",
                "main_frequency": 40,
                "backup_candidates": [50],
            },
        ],
        "interferences": [
            {"interference_id": "blocks-20", "occupied_frequencies": [10, 20]},
            {"interference_id": "only-main", "occupied_frequencies": [10]},
        ],
    }
    status, configured = request("POST", "/api/configuration", setup)
    assert status == 200
    assert configured["status"] == "feasible", configured
    selected = {
        item["mic_id"]: item["backup_frequency"]
        for item in configured["selected_backups"]
    }
    assert selected["m1"] == 30, selected

    rows = {row["interference_id"]: row for row in configured["switch_table"]}
    for scenario in ("blocks-20", "only-main"):
        switches = rows[scenario]["switches"]
        assert len(switches) == 1
        assert switches[0]["mic_id"] == "m1"
        assert switches[0]["to_frequency"] == 30

    # Repeat simultaneous writes so a read-modify-write lost update is likely
    # if transactions are not serialized correctly.
    for round_number in range(8):
        barrier = threading.Barrier(2)

        def register():
            barrier.wait()
            return request(
                "POST",
                "/api/registrations",
                {"mic_id": "m1", "backup_frequency": 30},
            )

        def update_interferences():
            barrier.wait()
            return request(
                "PUT",
                "/api/interferences",
                {
                    "interferences": [
                        {
                            "interference_id": "concurrent-a",
                            "name": f"round-{round_number}",
                            "occupied_frequencies": [10, 20],
                        },
                        {
                            "interference_id": "concurrent-b",
                            "occupied_frequencies": [10],
                        },
                    ]
                },
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            register_result, interference_result = list(
                pool.map(lambda fn: fn(), [register, update_interferences])
            )
        assert register_result[0] == 200
        assert interference_result[0] == 200

    status, table = request("GET", "/api/table")
    assert status == 200
    assert table["status"] == "feasible", table
    assert table["effective_version"] is not None

    m1 = next(mic for mic in table["configuration"]["mics"] if mic["mic_id"] == "m1")
    assert m1["installed_backup"] == 30, m1

    interference_ids = [
        item["interference_id"]
        for item in table["configuration"]["interferences"]
    ]
    assert interference_ids == ["concurrent-a", "concurrent-b"], interference_ids
    assert table["configuration"]["interferences"][0]["name"] == "round-7"

    for row in table["switch_table"]:
        if row["interference_id"].startswith("concurrent-"):
            assert row["switches"][0]["to_frequency"] == 30

    print("SMOKE TEST PASSED")
    print(f"base_url={BASE_URL}")
    print(f"effective_version={table['effective_version']}")
    print("shared backup: m1 -> 30 in both concurrent-a and concurrent-b")
    print("concurrent registration + interference update: both persisted")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
