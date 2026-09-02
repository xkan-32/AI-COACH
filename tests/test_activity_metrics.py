from datetime import UTC, datetime

import pytest

from app.activity_metrics import compute_activity_metrics
from app.domain.models import Activity, ActivityLap, ActivityStreamPoint


def activity() -> Activity:
    return Activity(
        id="activity-1",
        athlete_id="athlete-1",
        activity_type="Run",
        started_at=datetime(2026, 9, 2, tzinfo=UTC),
        duration_seconds=600,
        distance_meters=2000,
        suffer_score=42,
    )


def test_computes_reproducible_running_metrics() -> None:
    laps = [
        ActivityLap(
            activity_id="activity-1",
            athlete_id="athlete-1",
            lap_index=index,
            elapsed_seconds=300,
            moving_seconds=300,
            distance_meters=distance,
        )
        for index, distance in enumerate((1000, 900))
    ]
    points = [
        ActivityStreamPoint(
            activity_id="activity-1",
            athlete_id="athlete-1",
            sample_index=index,
            time_seconds=index * 10,
            distance_meters=index * 30,
            altitude_meters=altitude,
            velocity_mps=velocity,
            heartrate_bpm=heartrate,
            cadence_rpm=85 + index,
            moving=True,
            grade_percent=grade,
        )
        for index, (altitude, velocity, heartrate, grade) in enumerate(
            (
                (100, 3.0, 130, 4),
                (102, 3.2, 132, 0),
                (101, 3.1, 140, -4),
                (103, 3.3, 144, 0),
            )
        )
    ]

    metrics = compute_activity_metrics(activity(), laps, points)

    assert metrics.average_pace_seconds_per_km == 300
    assert metrics.ascent_meters == 4
    assert metrics.descent_meters == 1
    assert metrics.uphill_seconds == 10
    assert metrics.flat_seconds == 10
    assert metrics.downhill_seconds == 10
    assert metrics.uphill_meters == 30
    assert metrics.flat_meters == 30
    assert metrics.downhill_meters == 30
    assert metrics.average_heartrate_bpm == 136.5
    assert metrics.heartrate_drift_percent == pytest.approx(8.3969, rel=1e-3)
    assert metrics.average_cadence_per_minute == 173
    assert metrics.metric_quality == "full"
    assert metrics.suffer_score == 42


def test_missing_streams_are_reported_without_inventing_values() -> None:
    metrics = compute_activity_metrics(activity(), [], [])

    assert metrics.metric_quality == "summary_only"
    assert "streams_missing" in metrics.quality_reasons
    assert "heartrate_missing" in metrics.quality_reasons
    assert metrics.average_heartrate_bpm is None
    assert metrics.ascent_meters is None
    assert metrics.heartrate_drift_percent is None


def test_partial_streams_identify_missing_sensor_data() -> None:
    points = [
        ActivityStreamPoint(
            activity_id="activity-1",
            athlete_id="athlete-1",
            sample_index=index,
            time_seconds=index * 10,
            distance_meters=index * 30,
            altitude_meters=100 + index,
            velocity_mps=3,
            moving=True,
            grade_percent=0,
        )
        for index in range(2)
    ]

    metrics = compute_activity_metrics(activity(), [], points)

    assert metrics.metric_quality == "partial_streams"
    assert "heartrate_missing" in metrics.quality_reasons
    assert "cadence_missing" in metrics.quality_reasons
    assert metrics.average_heartrate_bpm is None


def test_strava_summary_elevation_avoids_raw_altitude_noise() -> None:
    value = activity().model_copy(update={"total_elevation_gain_meters": 55})
    points = [
        ActivityStreamPoint(
            activity_id="activity-1",
            athlete_id="athlete-1",
            sample_index=index,
            altitude_meters=altitude,
        )
        for index, altitude in enumerate((100, 110, 105, 120, 100))
    ]

    metrics = compute_activity_metrics(value, [], points)

    assert metrics.ascent_meters == 55
    assert metrics.descent_meters == 55


def test_distance_less_activity_has_no_pace() -> None:
    value = activity().model_copy(
        update={"activity_type": "WeightTraining", "distance_meters": 0}
    )

    assert compute_activity_metrics(value, [], []).average_pace_seconds_per_km is None


def test_zero_distance_stopped_interval_is_not_counted_as_grade_time() -> None:
    points = [
        ActivityStreamPoint(
            activity_id="activity-1",
            athlete_id="athlete-1",
            sample_index=index,
            time_seconds=time,
            distance_meters=distance,
            altitude_meters=100 + index,
            velocity_mps=3,
            moving=moving,
            grade_percent=4,
        )
        for index, (time, distance, moving) in enumerate(
            ((0, 0, True), (10, 30, False), (310, 30, True), (320, 60, True))
        )
    ]

    metrics = compute_activity_metrics(activity(), [], points)

    assert metrics.uphill_seconds == 20
    assert metrics.uphill_meters == 60
