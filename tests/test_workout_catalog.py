from app.workout_catalog import compatible_templates, prescribe


def test_compatible_templates_are_limited_to_selected_environment_and_choices() -> None:
    templates = compatible_templates(
        [{"name": "屋外ランニング"}, {"name": "インドアバイク"}],
        ["run-wave-v1", "bike-cadence-v1"],
    )

    assert [item.id for item in templates] == ["run-wave-v1", "bike-cadence-v1"]


def test_running_catalog_includes_interval_and_lsd_candidates() -> None:
    templates = compatible_templates([{"name": "屋外ランニング"}])
    by_id = {item.id: item for item in templates}

    assert by_id["run-interval-400-v1"].intensity == "moderate"
    assert "400m" in by_id["run-interval-400-v1"].description
    assert "60秒" in by_id["run-interval-400-v1"].description
    assert by_id["run-lsd-v1"].minimum_minutes == 60


def test_prescribes_running_only_for_outdoor_running_environment() -> None:
    result = prescribe(
        {
            "environment_ids": ["park"],
            "max_workout_minutes": 40,
            "outdoors_allowed": True,
        },
        {"park": "屋外ランニング"},
        [
            {
                "sport": "running",
                "pace_seconds_per_km": {"easy": {"lower": 360, "upper": 420}},
            }
        ],
    )

    assert result is not None
    assert result["template_id"] == "run-easy-v1"
    assert "360〜420秒/km" in result["rationale"]


def test_prescribes_indoor_bike_without_claiming_ftp() -> None:
    result = prescribe(
        {
            "environment_ids": ["bike"],
            "max_workout_minutes": 30,
            "outdoors_allowed": False,
        },
        {"bike": "インドアバイク"},
        [{"sport": "cycling", "heartrate_bpm": {"easy": {"lower": 125, "upper": 140}}}],
    )

    assert result is not None
    assert result["template_id"] == "bike-endurance-v1"
    assert "125〜140bpm" in result["rationale"]
    assert "FTP" not in result["rationale"]


def test_prescribes_bodyweight_for_home_environment() -> None:
    result = prescribe(
        {
            "environment_ids": ["home"],
            "max_workout_minutes": 25,
            "outdoors_allowed": False,
        },
        {"home": "自宅トレーニング（自重）"},
        [],
    )

    assert result is not None
    assert result["template_id"] == "bodyweight-full-v1"
    assert "スクワット8回" in result["rationale"]
