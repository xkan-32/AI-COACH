from app.workout_catalog import prescribe


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
