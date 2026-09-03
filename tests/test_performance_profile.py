from datetime import UTC, datetime, timedelta

from app.domain.models import Activity
from app.performance_profile import derive_performance_profiles

NOW = datetime(2026, 9, 3, tzinfo=UTC)


def activity(
    identifier: str,
    activity_type: str,
    *,
    days_ago: int,
    duration_seconds: int = 1_800,
    distance_meters: float = 5_000,
    average_heartrate_bpm: float | None = 145,
    elapsed_seconds: int | None = None,
    elevation_meters: float | None = 30,
) -> Activity:
    return Activity(
        id=identifier,
        athlete_id="athlete-1",
        activity_type=activity_type,
        started_at=NOW - timedelta(days=days_ago),
        duration_seconds=duration_seconds,
        distance_meters=distance_meters,
        elapsed_seconds=elapsed_seconds,
        average_heartrate_bpm=average_heartrate_bpm,
        total_elevation_gain_meters=elevation_meters,
    )


def test_running_profile_uses_valid_recent_runs_and_excludes_outliers() -> None:
    profiles = derive_performance_profiles(
        [
            activity("run-1", "Run", days_ago=3, duration_seconds=1_800),
            activity("run-2", "Run", days_ago=10, duration_seconds=1_850),
            activity("run-3", "VirtualRun", days_ago=20, duration_seconds=1_900),
            activity("run-stop", "Run", days_ago=4, elapsed_seconds=4_000),
            activity("run-old", "Run", days_ago=100),
        ],
        NOW,
    )

    running = next(item for item in profiles if item.sport == "running")
    assert running.confidence == "low"
    assert running.evidence_activity_ids == ["run-1", "run-2", "run-3"]
    assert running.pace_seconds_per_km["easy"].lower > 380
    assert running.pace_seconds_per_km["quality"].upper < 375
    assert running.heartrate_bpm["easy"].lower == 130
    assert "outliers_or_low_quality_activities_excluded" in running.quality_reasons


def test_profile_with_insufficient_or_missing_heart_rate_does_not_invent_ranges() -> (
    None
):
    profiles = derive_performance_profiles(
        [
            activity("run-1", "Run", days_ago=3, average_heartrate_bpm=None),
            activity("run-2", "Run", days_ago=10, average_heartrate_bpm=None),
        ],
        NOW,
    )

    running = next(item for item in profiles if item.sport == "running")
    cycling = next(item for item in profiles if item.sport == "cycling")
    assert running.confidence == "insufficient"
    assert running.pace_seconds_per_km == {}
    assert running.heartrate_bpm == {}
    assert cycling.confidence == "insufficient"
    assert "no_recent_activity" in cycling.quality_reasons


def test_cycling_profile_is_heart_rate_and_duration_based_without_ftp_claim() -> None:
    profiles = derive_performance_profiles(
        [
            activity("bike-1", "VirtualRide", days_ago=2, duration_seconds=2_400),
            activity("bike-2", "VirtualRide", days_ago=9, duration_seconds=2_700),
            activity("bike-3", "Ride", days_ago=16, duration_seconds=3_000),
        ],
        NOW,
    )

    cycling = next(item for item in profiles if item.sport == "cycling")
    assert cycling.confidence == "low"
    assert cycling.pace_seconds_per_km == {}
    assert cycling.heartrate_bpm["steady"].lower == 145
    assert cycling.duration_minutes["endurance"].lower >= 36
    assert all("ftp" not in key for key in cycling.model_dump())
