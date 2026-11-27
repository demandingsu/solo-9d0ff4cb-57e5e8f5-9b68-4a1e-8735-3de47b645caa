from app.solver import find_frequency_violations, solve_configuration


def test_duplicate_and_reflection_rules_are_detected():
    violations = find_frequency_violations(
        ["m1", "m2", "m3"], [10, 20, 30], occupied_frequencies=[20]
    )
    codes = {item["code"] for item in violations}
    assert "occupied_frequency" in codes
    assert "reflection_hits_third_frequency" in codes

    duplicate = find_frequency_violations(["m1", "m2"], [7, 7])
    assert any(item["code"] == "duplicate_frequency" for item in duplicate)


def test_multiple_interference_scenarios_share_one_backup():
    config = {
        "mics": [
            {
                "mic_id": "m1",
                "main_frequency": 10,
                "backup_candidates": [20, 30],
                "installed_backup": None,
            },
            {
                "mic_id": "m2",
                "main_frequency": 40,
                "backup_candidates": [50],
                "installed_backup": None,
            },
        ],
        "interferences": [
            {
                "interference_id": "blocks-first-backup",
                "occupied_frequencies": [10, 20],
            },
            {
                "interference_id": "only-main",
                "occupied_frequencies": [10],
            },
        ],
    }

    result = solve_configuration(config)

    assert result["status"] == "feasible"
    selected = {
        item["mic_id"]: item["backup_frequency"]
        for item in result["selected_backups"]
    }
    assert selected["m1"] == 30

    rows = {row["interference_id"]: row for row in result["switch_table"]}
    for scenario in ("blocks-first-backup", "only-main"):
        switches = rows[scenario]["switches"]
        assert len(switches) == 1
        assert switches[0]["mic_id"] == "m1"
        assert switches[0]["from_frequency"] == 10
        assert switches[0]["to_frequency"] == 30
        assert rows[scenario]["valid"] is True


def test_baseline_reflection_is_immediately_infeasible():
    config = {
        "mics": [
            {"mic_id": "m1", "main_frequency": 10, "backup_candidates": [101]},
            {"mic_id": "m2", "main_frequency": 20, "backup_candidates": [102]},
            {"mic_id": "m3", "main_frequency": 30, "backup_candidates": [103]},
        ],
        "interferences": [],
    }
    result = solve_configuration(config)
    assert result["status"] == "infeasible"
    assert result["switch_table"] is None
    assert result["no_solution"]["stage"] == "normal"


def test_blocked_backup_can_make_configuration_infeasible():
    config = {
        "mics": [
            {"mic_id": "m1", "main_frequency": 10, "backup_candidates": [20]},
        ],
        "interferences": [
            {"interference_id": "all-blocked", "occupied_frequencies": [10, 20]},
        ],
    }
    result = solve_configuration(config)
    assert result["status"] == "infeasible"
    assert result["switch_table"] is None
