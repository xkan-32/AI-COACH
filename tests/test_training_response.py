from datetime import UTC, datetime, timedelta

from app.domain.models import Activity
from app.training_response import derive_training_response_signal

NOW = datetime(2026, 9, 14, 12, tzinfo=UTC)


def activity(
    activity_id: str, *, days_ago: int, intensity: str | None = None, status=None
) -> Activity:
    return Activity(
        id=activity_id,
        athlete_id="athlete-1",
        activity_type="Run",
        started_at=NOW - timedelta(days=days_ago),
        duration_seconds=30 * 60,
        distance_meters=5000,
        perceived_intensity=intensity,
        completion_status=status,
    )


def test_multiple_recent_hard_reports_reduce_next_week_intensity_limit() -> None:
    signal = derive_training_response_signal(
        [
            activity("activity-1", days_ago=1, intensity="hard"),
            activity("activity-2", days_ago=4, intensity="hard"),
            activity("activity-3", days_ago=8, intensity="easy"),
        ],
        NOW,
    )

    assert signal.recommended_maximum_moderate_days == 1
    assert signal.evidence_activity_ids == ["activity-3", "activity-2", "activity-1"]
    assert "multiple_recent_hard_rpe_reports" in signal.reason_codes


def test_skipped_and_old_activities_do_not_change_the_signal() -> None:
    signal = derive_training_response_signal(
        [
            activity("skipped", days_ago=1, intensity="hard", status="skipped"),
            activity("old", days_ago=15, intensity="hard"),
        ],
        NOW,
    )

    assert signal.completed_activity_count == 0
    assert signal.recommended_maximum_moderate_days is None
    assert signal.reason_codes == ["no_recent_response"]


def test_confirmed_plan_links_exclude_unplanned_activities_from_load_signal() -> None:
    signal = derive_training_response_signal(
        [
            activity("planned", days_ago=1, intensity="easy"),
            activity("unplanned-1", days_ago=2, intensity="hard"),
            activity("unplanned-2", days_ago=3, intensity="hard"),
        ],
        NOW,
        confirmed_planned_activity_ids={"planned"},
    )

    assert signal.evidence_source == "confirmed_planned_activities"
    assert signal.evidence_activity_ids == ["planned"]
    assert signal.recommended_maximum_moderate_days is None
