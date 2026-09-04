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


def test_catalog_exposes_structured_examples_and_ai_bounds() -> None:
    templates = compatible_templates(
        [{"name": "屋外ランニング"}, {"name": "インドアバイク"}]
    )
    by_id = {item.id: item for item in templates}

    pace = by_id["run-pace-v1"].structure
    interval = by_id["run-interval-400-v1"].structure
    bike = by_id["bike-tempo-v1"].structure

    assert pace is not None
    assert pace["maximum_distance_km"] == 10
    assert pace["fastest_pace_seconds_per_km"] == 350
    assert by_id["run-pace-v1"].allowed_intensities == ["easy", "moderate"]
    assert any(step["name"] == "ペース走" for step in pace["steps"])
    assert interval is not None
    assert any(step.get("duration_seconds") == 60 for step in interval["steps"])
    assert bike is not None
    assert bike["maximum_duration_minutes"] == 60


def test_custom_running_candidate_keeps_structured_bounds() -> None:
    templates = compatible_templates(
        [{"name": "屋外ランニング"}],
        custom_running_candidates=[
            {
                "id": "custom-1",
                "title": "カスタムビルドアップ",
                "description": "徐々に上げる",
                "intensity": "easy",
                "minimum_minutes": 30,
                "maximum_distance_km": 9,
                "fastest_pace_seconds_per_km": 340,
                "example_structure": "2kmアップ、1kmごとに上げる、1kmダウン",
            }
        ],
    )
    custom = next(item for item in templates if item.id == "custom-1")

    assert custom.structure is not None
    assert custom.structure["maximum_distance_km"] == 9
    assert custom.structure["fastest_pace_seconds_per_km"] == 340
    assert "1kmごと" in custom.structure["freeform_example"]


def test_standard_candidate_can_be_overridden_with_specific_environment() -> None:
    templates = compatible_templates(
        [{"name": "河川敷"}],
        custom_running_candidates=[
            {
                "id": "run-pace-v1",
                "title": "ペース走",
                "description": "河川敷で行うペース走",
                "minimum_minutes": 35,
                "required_environment_keywords": ["河川敷"],
                "structure": {
                    "sport": "running",
                    "maximum_distance_km": 7,
                    "fastest_pace_seconds_per_km": 365,
                    "steps": [],
                },
            }
        ],
    )

    pace = next(item for item in templates if item.id == "run-pace-v1")
    assert pace.title == "ペース走"
    assert pace.required_environment_keywords == ["河川敷"]
    assert pace.structure is not None
    assert pace.structure["maximum_distance_km"] == 7


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
