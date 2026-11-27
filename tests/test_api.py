import pytest
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.fixture
def client(tmp_path):
    db_path = tmp_path / "frequency-test.db"
    app = create_app(str(db_path))
    with TestClient(app) as test_client:
        yield test_client


VALID_PAYLOAD = {
    "name": "测试场",
    "mics": [
        {"mic_id": "m1", "name": "主持", "main_frequency": 10, "backup_candidates": [20, 30]},
        {"mic_id": "m2", "name": "嘉宾", "main_frequency": 40, "backup_candidates": [50, 55]},
        {"mic_id": "m3", "name": "乐器", "main_frequency": 71, "backup_candidates": [80, 90]},
    ],
    "interferences": [
        {"interference_id": "i1", "name": "干扰一", "occupied_frequencies": [10, 20]},
        {"interference_id": "i2", "name": "干扰二", "occupied_frequencies": [40, 50]},
    ],
}


def test_health_and_empty_table(client):
    assert client.get("/health").json()["status"] == "ok"
    table = client.get("/api/table")
    assert table.status_code == 200
    assert table.json()["status"] == "not_configured"


def test_configure_table_is_one_consistent_snapshot(client):
    response = client.post("/api/configuration", json=VALID_PAYLOAD)
    assert response.status_code == 200, response.text
    body = response.json()
    version = body["effective_version"]
    assert body["status"] == "feasible"
    assert body["configuration"]["mics"][0]["main_frequency"] == 10
    assert len(body["switch_table"]) == 2

    fetched = client.get("/api/table").json()
    assert fetched["effective_version"] == version
    assert fetched["configuration"] == body["configuration"]
    assert fetched["switch_table"] == body["switch_table"]

    # Every switch row only switches mains that are occupied.
    occupied_sets = {
        row["interference_id"]: set(row["occupied_frequencies"])
        for row in fetched["switch_table"]
    }
    mains = {
        mic["mic_id"]: mic["main_frequency"]
        for mic in fetched["configuration"]["mics"]
    }
    for row in fetched["switch_table"]:
        switched_ids = {switch["mic_id"] for switch in row["switches"]}
        expected = {
            mic_id
            for mic_id, freq in mains.items()
            if freq in occupied_sets[row["interference_id"]]
        }
        assert switched_ids == expected


def test_no_solution_saves_new_settings_and_hides_old_table(client):
    ok = client.post("/api/configuration", json=VALID_PAYLOAD)
    assert ok.json()["status"] == "feasible"
    old_version = ok.json()["effective_version"]

    impossible = {
        "mics": [
            {"mic_id": "x1", "main_frequency": 10, "backup_candidates": [20]},
            {"mic_id": "x2", "main_frequency": 11, "backup_candidates": [21]},
        ],
        "interferences": [
            {"interference_id": "block", "occupied_frequencies": [10, 20]},
        ],
    }
    response = client.post("/api/configuration", json=impossible)
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "infeasible"
    assert body["switch_table"] is None
    assert body["no_solution"]

    table = client.get("/api/table").json()
    assert table["effective_version"] == old_version + 1
    assert table["status"] == "infeasible"
    assert table["switch_table"] is None
    assert table["configuration"]["mics"][0]["mic_id"] == "x1"
    assert table["configuration"]["interferences"][0]["interference_id"] == "block"


def test_registered_backup_is_reused_on_reconfiguration(client):
    payload = {
        "mics": [
            {"mic_id": "m1", "main_frequency": 10, "backup_candidates": [20, 30]},
            {"mic_id": "m2", "main_frequency": 40, "backup_candidates": [50]},
        ],
        "interferences": [],
    }
    client.post("/api/configuration", json=payload)

    registered = client.post(
        "/api/registrations",
        json={"mic_id": "m1", "backup_frequency": 20},
    )
    assert registered.status_code == 200, registered.text
    selected = {
        item["mic_id"]: item
        for item in registered.json()["selected_backups"]
    }
    assert selected["m1"]["backup_frequency"] == 20
    assert selected["m1"]["source"] == "registered"

    # Reverse candidate preference; an unregistered solver would now prefer 30.
    payload["mics"][0]["backup_candidates"] = [30, 20]
    payload["interferences"] = [
        {"interference_id": "new", "occupied_frequencies": [999]}
    ]
    reconfigured = client.post("/api/configuration", json=payload).json()
    selected = {
        item["mic_id"]: item
        for item in reconfigured["selected_backups"]
    }
    assert selected["m1"]["backup_frequency"] == 20
    assert reconfigured["configuration"]["mics"][0]["installed_backup"] == 20

    # Explicit null clears the inline/hard registration; omitted fields in a
    # normal reconfiguration preserve it.
    payload["mics"][0]["installed_backup"] = None
    cleared = client.post("/api/configuration", json=payload).json()
    assert cleared["configuration"]["mics"][0]["installed_backup"] is None
    assert cleared["selected_backups"][0]["backup_frequency"] == 30


def test_interference_update_accepts_raw_list_and_preserves_registration(client):
    client.post("/api/configuration", json=VALID_PAYLOAD)
    reg = client.post(
        "/api/backups/register",
        json={"mic_id": "m1", "backup_frequency": 30},
    )
    assert reg.status_code == 200

    response = client.put(
        "/api/interferences",
        json=[
            {"interference_id": "raw", "occupied_frequencies": [10, 20]},
            {"interference_id": "raw2", "occupied_frequencies": [40]},
        ],
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert [row["interference_id"] for row in body["switch_table"]] == [
        "raw",
        "raw2",
    ]
    m1 = body["configuration"]["mics"][0]
    assert m1["installed_backup"] == 30


def test_configuration_survives_new_app_instance_on_same_sqlite_file(tmp_path):
    db_path = tmp_path / "restart.db"
    first = TestClient(create_app(str(db_path)))
    response = first.post("/api/configuration", json=VALID_PAYLOAD)
    assert response.status_code == 200
    version = response.json()["effective_version"]

    reopened = TestClient(create_app(str(db_path)))
    table = reopened.get("/api/table").json()
    assert table["effective_version"] == version
    assert table["status"] == "feasible"
    assert len(table["configuration"]["mics"]) == 3


def test_limit_validation_rejected(client):
    too_many = {
        "mics": [
            {"mic_id": f"m{i}", "main_frequency": i, "backup_candidates": [100 + i]}
            for i in range(7)
        ]
    }
    response = client.post("/api/configuration", json=too_many)
    assert response.status_code == 422

    too_many_candidates = {
        "mics": [
            {"mic_id": "m1", "main_frequency": 1, "backup_candidates": [2, 3, 4, 5, 6]}
        ]
    }
    assert client.post("/api/configuration", json=too_many_candidates).status_code == 422
