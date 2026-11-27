import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
import uvicorn

from app.main import create_app


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture
def live_client(tmp_path):
    db_path = tmp_path / "concurrent-frequency.db"
    app = create_app(str(db_path))
    port = _free_port()
    config = uvicorn.Config(
        app, host="127.0.0.1", port=port, log_level="error", workers=1
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, name="uvicorn-test", daemon=True)
    thread.start()

    base_url = f"http://127.0.0.1:{port}"
    deadline = time.time() + 15
    while time.time() < deadline:
        try:
            response = httpx.get(base_url + "/health", timeout=0.5)
            if response.status_code == 200:
                break
        except httpx.HTTPError:
            time.sleep(0.05)
    else:
        raise RuntimeError("test server did not start")

    with httpx.Client(base_url=base_url, timeout=10) as client:
        yield client

    server.should_exit = True
    thread.join(timeout=5)


def test_concurrent_registration_and_interference_update_both_persist(live_client):
    setup = {
        "mics": [
            {"mic_id": "m1", "main_frequency": 10, "backup_candidates": [20, 30]},
            {"mic_id": "m2", "main_frequency": 40, "backup_candidates": [50]},
        ],
        "interferences": [
            {"interference_id": "old", "occupied_frequencies": [999]},
        ],
    }
    response = live_client.post("/api/configuration", json=setup)
    assert response.status_code == 200, response.text

    # Repeat real simultaneous requests to make a lost-update race likely if
    # writes are not serialized through SQLite.
    for round_number in range(12):
        barrier = threading.Barrier(2)
        interference_payload = {
            "interferences": [
                {
                    "interference_id": "concurrent-case",
                    "name": f"round-{round_number}",
                    "occupied_frequencies": [10, 20],
                },
                {
                    "interference_id": "shared-second-case",
                    "occupied_frequencies": [10],
                },
            ]
        }
        registration_payload = {
            "mic_id": "m1",
            "backup_frequency": 30,
        }

        def register():
            barrier.wait()
            return live_client.post(
                "/api/registrations", json=registration_payload
            )

        def change_interferences():
            barrier.wait()
            return live_client.put(
                "/api/interferences", json=interference_payload
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            reg_result, interference_result = list(
                pool.map(lambda fn: fn(), [register, change_interferences])
            )

        assert reg_result.status_code == 200, reg_result.text
        assert interference_result.status_code == 200, interference_result.text

    table_response = live_client.get("/api/table")
    assert table_response.status_code == 200
    table = table_response.json()

    # Both logical updates are present in the final effective snapshot.
    m1 = next(mic for mic in table["configuration"]["mics"] if mic["mic_id"] == "m1")
    assert m1["installed_backup"] == 30

    interference_ids = [
        item["interference_id"]
        for item in table["configuration"]["interferences"]
    ]
    assert interference_ids == ["concurrent-case", "shared-second-case"]
    concurrent_case = table["configuration"]["interferences"][0]
    assert concurrent_case["name"] == "round-11"

    # The final registered choice makes both scenarios feasible and the same
    # backup 30 is shared by m1 whenever its main 10 is occupied.
    assert table["status"] == "feasible"
    assert {
        row["interference_id"]: row["switches"] for row in table["switch_table"]
    }["concurrent-case"][0]["to_frequency"] == 30
    assert {
        row["interference_id"]: row["switches"] for row in table["switch_table"]
    }["shared-second-case"][0]["to_frequency"] == 30

    # The table and its configuration are one stored version, not a mix.
    assert table["effective_version"] == table_response.json()["effective_version"]
    for row in table["switch_table"]:
        config_ids = [
            item["interference_id"]
            for item in table["configuration"]["interferences"]
        ]
        assert row["interference_id"] in config_ids


def test_concurrent_batch_registrations_all_persist(live_client):
    setup = {
        "mics": [
            {"mic_id": f"m{i}", "main_frequency": 100 + i * 10,
             "backup_candidates": [200 + i * 10, 300 + i * 10]}
            for i in range(4)
        ],
        "interferences": [],
    }
    live_client.post("/api/configuration", json=setup)

    barrier = threading.Barrier(4)

    def register_index(index):
        barrier.wait()
        return live_client.post(
            "/api/registrations",
            json={"mic_id": f"m{index}", "backup_frequency": 200 + index * 10},
        )

    with ThreadPoolExecutor(max_workers=4) as pool:
        responses = list(pool.map(register_index, range(4)))

    assert all(response.status_code == 200 for response in responses)
    table = live_client.get("/api/table").json()
    registered = {
        mic["mic_id"]: mic["installed_backup"]
        for mic in table["configuration"]["mics"]
    }
    assert registered == {
        "m0": 200,
        "m1": 210,
        "m2": 220,
        "m3": 230,
    }
