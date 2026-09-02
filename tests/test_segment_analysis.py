import base64
from datetime import UTC, datetime

from app.domain.models import Activity, ActivityStreamPoint
from app.segment_analysis import (
    RouteFingerprintHasher,
    compare_route_segments,
    compute_segment_metrics,
)


def activity(activity_id: str = "activity-1") -> Activity:
    return Activity(
        id=activity_id,
        athlete_id="athlete-1",
        activity_type="Run",
        started_at=datetime(2026, 9, 2, tzinfo=UTC),
        duration_seconds=600,
        distance_meters=1000,
    )


def stream_points(activity_id: str = "activity-1") -> list[ActivityStreamPoint]:
    return [
        ActivityStreamPoint(
            activity_id=activity_id,
            athlete_id="athlete-1",
            sample_index=index,
            time_seconds=index * 30,
            distance_meters=index * 50,
            altitude_meters=100 + index * (2 if index < 10 else 0.2),
            velocity_mps=2 if 5 <= index < 10 else 3.5,
            heartrate_bpm=155 if 5 <= index < 10 else 135,
            cadence_rpm=78 if 5 <= index < 10 else 86,
            moving=True,
            grade_percent=4 if 5 <= index < 10 else 0,
        )
        for index in range(21)
    ]


def test_segments_are_binned_and_high_load_has_reasons() -> None:
    segments = compute_segment_metrics(activity(), stream_points())

    assert len(segments) == 4
    assert segments[0].start_distance_meters == 0
    assert segments[0].end_distance_meters == 250
    assert segments[-1].end_distance_meters == 1000
    high = [segment for segment in segments if segment.high_load_reasons]
    assert high
    assert "heart_rate_above_session" in high[0].high_load_reasons
    assert "sustained_climb" in high[0].high_load_reasons
    assert high[0].relative_load_rank_percentile >= 75


def test_missing_sensors_are_explicitly_partial() -> None:
    points = [
        point.model_copy(update={"heartrate_bpm": None, "cadence_rpm": None})
        for point in stream_points()
    ]

    segments = compute_segment_metrics(activity(), points)

    assert all(segment.metric_quality == "partial" for segment in segments)
    assert all("heartrate_missing" in segment.quality_reasons for segment in segments)


def test_route_hash_is_stable_after_trimming_and_quantization() -> None:
    hasher = RouteFingerprintHasher(base64.b64encode(b"r" * 32).decode("ascii"))
    route = [
        (
            float(distance),
            35.0002 + distance / 100_000,
            139.0002 + distance / 100_000,
        )
        for distance in range(0, 2251, 50)
    ]
    noisy = [
        (distance, latitude + 0.00001, longitude - 0.00001)
        for distance, latitude, longitude in route
    ]

    first = hasher.create(activity(), route)
    second = hasher.create(activity("activity-2"), noisy)

    assert first is not None
    assert second is not None
    assert first.route_hash == second.route_hash
    assert "35." not in first.route_hash
    assert first.sampled_point_count > 2


def test_short_route_has_no_fingerprint() -> None:
    hasher = RouteFingerprintHasher(base64.b64encode(b"r" * 32).decode("ascii"))

    assert hasher.create(activity(), [(0, 35, 139), (900, 35.1, 139.1)]) is None


def test_route_comparison_requires_two_baselines() -> None:
    current = compute_segment_metrics(activity(), stream_points())
    previous_1 = compute_segment_metrics(
        activity("previous-1"), stream_points("previous-1")
    )
    previous_2 = compute_segment_metrics(
        activity("previous-2"), stream_points("previous-2")
    )

    assert compare_route_segments(activity(), "hash", current, [previous_1]) is None
    comparison = compare_route_segments(
        activity(), "hash", current, [previous_1, previous_2]
    )

    assert comparison is not None
    assert comparison.baseline_activity_count == 2
    assert comparison.pace_delta_percent == 0
